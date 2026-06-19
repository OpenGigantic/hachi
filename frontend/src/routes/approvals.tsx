// ============================================================
// The leash — /approvals
// Pending approvals queue with countdown + history + operator audit log.
// ============================================================

import React, { useState, useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useStore } from "../lib/store/store";
import { useCountdown } from "../lib/hooks/useCountdown";
import { Icon } from "../components/icons/Icon";
import { HachiMark } from "../components/brand/HachiMark";
import type { Approval, HistoryItem, AuditEntry } from "../lib/data/types";

// localStorage key for dismissing the "What is the leash?" info band
const LEASH_INFO_KEY = "hachi.leash-info-dismissed";
import {
  loadApprovals,
  approve,
  allowAlways,
  decline,
  declineAll as declineAllMutation,
} from "../lib/data/domains/approvals";

// ============================================================
// LeashInfoBand — "What is the leash?" dismissible info band
// Shown once on first visit; suppressed after dismiss via localStorage.
// ============================================================
function LeashInfoBand() {
  const [visible, setVisible] = useState<boolean>(
    () => localStorage.getItem(LEASH_INFO_KEY) !== "1"
  );

  const dismiss = () => {
    try {
      localStorage.setItem(LEASH_INFO_KEY, "1");
    } catch {
      // Storage unavailable (e.g. private browsing quota) — still hide the band.
    }
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div
      role="status"
      aria-label="What is the leash?"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "13px 16px",
        background: "var(--hachi-trust-tint)",
        border: "0.5px solid var(--hachi-line)",
        borderRadius: 12,
        marginBottom: 4,
      }}
    >
      <span
        style={{
          color: "var(--hachi-trust)",
          display: "inline-flex",
          flexShrink: 0,
          marginTop: 2,
        }}
      >
        <Icon name="shield" size={16} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: "var(--font-body)",
            fontWeight: 500,
            fontSize: 13,
            color: "var(--hachi-ink)",
            marginBottom: 3,
          }}
        >
          What is the leash?
        </div>
        <div style={{ fontSize: 13, color: "var(--hachi-muted)", lineHeight: "20px" }}>
          The leash is how you stay in control — before Hachi runs anything risky or
          irreversible, it pauses here and asks. You decide: allow once, always allow,
          or decline. Hachi never acts without your say-so on gated actions.
        </div>
      </div>
      <button
        type="button"
        aria-label="Dismiss: What is the leash?"
        onClick={dismiss}
        style={{
          flexShrink: 0,
          border: "none",
          background: "transparent",
          color: "var(--hachi-muted)",
          cursor: "pointer",
          padding: "2px 4px",
          display: "inline-flex",
          alignItems: "center",
          borderRadius: 6,
          font: "16px/1 var(--font-body)",
        }}
      >
        ×
      </button>
    </div>
  );
}

// ---- risk metadata (mirrors dc.html riskMeta) ----
function riskMeta(r: string): {
  label: string;
  fg: string;
  bg: string;
  cd: string;
} {
  const map: Record<string, { label: string; fg: string; bg: string; cd: string }> = {
    low: {
      label: "low risk",
      fg: "#2C7A50",
      bg: "var(--hachi-success-tint)",
      cd: "var(--hachi-success)",
    },
    medium: {
      label: "needs care",
      fg: "#A8651A",
      bg: "var(--hachi-working-tint)",
      cd: "var(--hachi-working)",
    },
    high: {
      label: "high risk",
      fg: "#9E2D21",
      bg: "var(--hachi-danger-tint)",
      cd: "var(--hachi-danger)",
    },
  };
  return map[r] ?? map.medium;
}

// ---- audit decision color map ----
const auditDColor: Record<string, string> = {
  allowed: "var(--hachi-success)",
  "allowed once": "var(--hachi-trust)",
  declined: "var(--hachi-danger)",
  automatic: "var(--hachi-muted)",
};

// ---- unchecked actions list (prototype literal) ----
const UNCHECKED_ACTIONS = [
  "Reading files inside your project folder",
  "Searching the web to answer a question",
  "Reading your calendar and inbox (not sending)",
];

// ============================================================
// ApprovalCard — renders one pending approval with live countdown
// ============================================================
interface ApprovalCardProps {
  approval: Approval;
  onAllowOnce: () => void;
  onAlwaysAllow: () => void;
  onDecline: () => void;
}

function ApprovalCard({
  approval,
  onAllowOnce,
  onAlwaysAllow,
  onDecline,
}: ApprovalCardProps) {
  const { ms, label: countdownLabel } = useCountdown(approval.expiresAt);
  const rm = riskMeta(approval.risk);
  const cdPct = Math.max(0, Math.min(100, (ms / approval.totalMs) * 100));

  // Hover states for the three buttons
  const [declineHover, setDeclineHover] = useState(false);
  const [alwaysHover, setAlwaysHover] = useState(false);
  const [allowHover, setAllowHover] = useState(false);

  return (
    <div
      style={{
        background: "var(--hachi-surface)",
        border: "0.5px solid var(--hachi-line)",
        borderRadius: 16,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {/* Header row: icon + title + risk pill */}
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div
          style={{
            flexShrink: 0,
            width: 48,
            height: 48,
            borderRadius: 10,
            background: "var(--hachi-cream)",
            display: "grid",
            placeItems: "center",
          }}
        >
          <Icon name={approval.icon} size={24} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 500,
                fontSize: 18,
              }}
            >
              {approval.request}
            </span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "3px 10px",
                borderRadius: 999,
                background: rm.bg,
                color: rm.fg,
                fontSize: 12,
                fontWeight: 500,
              }}
            >
              {rm.label}
            </span>
          </div>
          <div
            style={{
              fontSize: 14,
              color: "var(--hachi-muted)",
              marginTop: 4,
            }}
          >
            why: {approval.why}
          </div>
        </div>
      </div>

      {/* Action shell */}
      <div
        style={{
          background: "var(--hachi-cream)",
          border: "0.5px solid var(--hachi-line)",
          borderRadius: 10,
          padding: "12px 14px",
          fontFamily: "var(--font-mono)",
          fontSize: 13,
          lineHeight: "22px",
          wordBreak: "break-word",
        }}
      >
        <span style={{ color: "var(--hachi-brand)" }}>hachi ▸ </span>
        {approval.action}
      </div>

      {/* Op + policy */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 10,
          fontSize: 12,
          color: "var(--hachi-muted)",
        }}
      >
        <span style={{ color: "var(--hachi-ink)" }}>{approval.op}</span>
        <span>·</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <Icon name="shield" size={13} />
          {approval.policy}
        </span>
      </div>

      {/* What this touches — file chips */}
      <div>
        <div
          style={{
            fontSize: 12,
            color: "var(--hachi-muted)",
            marginBottom: 8,
          }}
        >
          What this touches
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
          {(approval.touches ?? []).map((f, i) => (
            <span
              key={i}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 10px",
                borderRadius: 8,
                background: "var(--hachi-canvas)",
                border: "0.5px solid var(--hachi-line)",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--hachi-ink)",
              }}
            >
              <span
                style={{
                  color: "var(--hachi-muted)",
                  display: "inline-flex",
                }}
              >
                <Icon name="file" size={12} />
              </span>
              {f}
            </span>
          ))}
        </div>
      </div>

      {/* Countdown bar — only shown when the gateway sends a real expiry (totalMs > 0).
          The current gateway does not send an expiry field (PermissionRequest has none).
          When totalMs === 0 we show a plain "Hachi stays put" note instead of a fake bar. */}
      {approval.totalMs > 0 ? (
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: 12,
              color: "var(--hachi-muted)",
              marginBottom: 6,
            }}
          >
            <span
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <Icon name="clock" size={13} />
              {" "}auto-declines in {countdownLabel}
            </span>
            <span>requests expire — Hachi stays put</span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(cdPct)}
            aria-label={`Time remaining: ${countdownLabel}`}
            style={{
              height: 3,
              borderRadius: 999,
              background: "var(--hachi-line)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: cdPct + "%",
                height: "100%",
                background: rm.cd,
                borderRadius: 999,
                transition: "width 1s linear",
              }}
            />
          </div>
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12,
            color: "var(--hachi-muted)",
          }}
        >
          <Icon name="clock" size={13} />
          no auto-decline — Hachi waits until you decide
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 9 }}>
        <button
          type="button"
          onClick={onDecline}
          onMouseEnter={() => setDeclineHover(true)}
          onMouseLeave={() => setDeclineHover(false)}
          style={{
            flex: 1,
            height: 40,
            borderRadius: 10,
            border: "1px solid var(--hachi-trust)",
            background: declineHover ? "var(--hachi-trust-tint)" : "transparent",
            color: "var(--hachi-trust)",
            font: "500 14px var(--font-body)",
            cursor: "pointer",
            transition: "background var(--dur-fast) var(--ease)",
          }}
        >
          Decline
        </button>
        <button
          type="button"
          onClick={onAllowOnce}
          onMouseEnter={() => setAllowHover(true)}
          onMouseLeave={() => setAllowHover(false)}
          style={{
            flex: 1,
            height: 40,
            borderRadius: 10,
            border: "0.5px solid transparent",
            background: allowHover
              ? "var(--hachi-brand-hover)"
              : "var(--hachi-brand)",
            color: "var(--hachi-canvas)",
            font: "500 14px var(--font-body)",
            cursor: "pointer",
            transition: "background var(--dur-fast) var(--ease)",
          }}
        >
          Allow once
        </button>
        <button
          type="button"
          onClick={onAlwaysAllow}
          onMouseEnter={() => setAlwaysHover(true)}
          onMouseLeave={() => setAlwaysHover(false)}
          style={{
            flex: 1,
            height: 40,
            borderRadius: 10,
            border: "0.5px solid transparent",
            background: alwaysHover ? "var(--hachi-cream)" : "transparent",
            color: "var(--hachi-muted)",
            font: "500 14px var(--font-body)",
            cursor: "pointer",
            transition: "background var(--dur-fast) var(--ease)",
          }}
        >
          Always allow {approval.scope}
        </button>
      </div>
    </div>
  );
}

// ============================================================
// HistoryRow
// ============================================================
function HistoryRow({ item }: { item: HistoryItem }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "13px 16px",
        borderBottom: "0.5px solid var(--hachi-line)",
      }}
    >
      <span style={{ color: item.color, display: "inline-flex" }}>
        <Icon name={item.icon} size={16} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14 }}>{item.request}</div>
        <div
          style={{
            fontSize: 12,
            color: "var(--hachi-muted)",
            marginTop: 2,
          }}
        >
          {item.decision} · {item.when}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// AuditRow (operator-only)
// ============================================================
function AuditRow({ entry }: { entry: AuditEntry }) {
  const dColor = auditDColor[entry.decision] ?? "var(--hachi-muted)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 13,
        padding: "11px 16px",
        borderBottom: "0.5px solid var(--hachi-line)",
        fontSize: 13,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--hachi-muted)",
          flexShrink: 0,
        }}
      >
        {entry.ts}
      </span>
      <span
        style={{
          color: "var(--hachi-ink)",
          minWidth: 110,
          flexShrink: 0,
        }}
      >
        {entry.op}
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--hachi-muted)",
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {entry.target}
      </span>
      <span
        style={{
          fontSize: 12,
          color: "var(--hachi-muted)",
          flexShrink: 0,
        }}
      >
        {entry.policy}
      </span>
      <span
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: dColor,
          flexShrink: 0,
          minWidth: 78,
          textAlign: "right",
        }}
      >
        {entry.decision}
      </span>
    </div>
  );
}

// ============================================================
// Approvals screen component
// ============================================================
function Approvals() {
  const navigate = useNavigate();
  const approvals = useStore((s) => s.approvals);
  const history = useStore((s) => s.history);
  const auditLog = useStore((s) => s.auditLog);
  const operator = useStore((s) => s.operator);

  // Refresh gateway data on navigate-to this screen
  useEffect(() => { void loadApprovals(); }, []);

  // Hover state for "Decline all" button
  const [declineAllHover, setDeclineAllHover] = useState(false);

  const pendingCount = approvals.length;
  const hasPending = pendingCount > 0;
  const noPending = pendingCount === 0;
  const manyPending = pendingCount > 1;
  const pendingCountStr =
    pendingCount === 1 ? "1 thing" : pendingCount + " things";

  const goSettings = () => navigate({ to: "/settings" });

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "0 32px",
          height: 68,
          flexShrink: 0,
          borderBottom: "0.5px solid var(--hachi-line)",
        }}
      >
        <h1
          style={{
            margin: 0,
            fontFamily: "var(--font-display)",
            fontWeight: 500,
            fontSize: 22,
            lineHeight: "26px",
          }}
        >
          The leash
        </h1>
        <span style={{ fontSize: 13, color: "var(--hachi-muted)" }}>
          you hold it · {pendingCountStr} waiting
        </span>
        <div style={{ flex: 1 }} />
        {manyPending && (
          <button
            type="button"
            onClick={() => { void declineAllMutation(); }}
            onMouseEnter={() => setDeclineAllHover(true)}
            onMouseLeave={() => setDeclineAllHover(false)}
            style={{
              height: 36,
              padding: "0 14px",
              borderRadius: 10,
              border: "1px solid var(--hachi-trust)",
              background: declineAllHover
                ? "var(--hachi-trust-tint)"
                : "transparent",
              color: "var(--hachi-trust)",
              font: "500 13px var(--font-body)",
              cursor: "pointer",
              transition: "background var(--dur-fast) var(--ease)",
            }}
          >
            Decline all
          </button>
        )}
      </div>

      {/* Scrollable content */}
      <div
        className="hachi-scroll"
        style={{ flex: 1, overflow: "auto", padding: "28px 32px 64px" }}
      >
        <div
          style={{
            maxWidth: 720,
            margin: "0 auto",
            display: "flex",
            flexDirection: "column",
            gap: 28,
          }}
        >
          {/* What is the leash? — first-encounter info band */}
          <LeashInfoBand />

          {/* Pending approvals */}
          {hasPending && (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {approvals.map((a) => (
                <ApprovalCard
                  key={a.id}
                  approval={a}
                  onAllowOnce={() => { void approve(a.id); }}
                  onAlwaysAllow={() => { void allowAlways(a.id); }}
                  onDecline={() => { void decline(a.id); }}
                />
              ))}
            </div>
          )}

          {/* Empty state */}
          {noPending && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                textAlign: "center",
                gap: 14,
                padding: "40px 0",
                background: "var(--hachi-surface)",
                border: "0.5px solid var(--hachi-line)",
                borderRadius: 16,
              }}
            >
              <HachiMark size={48} />
              <div
                style={{
                  fontFamily: "var(--font-display)",
                  fontWeight: 500,
                  fontSize: 18,
                }}
              >
                Nothing pending
              </div>
              <div style={{ fontSize: 14, color: "var(--hachi-muted)" }}>
                Hachi is on watch. It will ask here before doing anything risky.
              </div>
            </div>
          )}

          {/* Runs without a leash check */}
          <div>
            <h2
              style={{
                margin: "0 0 12px",
                fontFamily: "var(--font-display)",
                fontWeight: 500,
                fontSize: 16,
              }}
            >
              Runs without a leash check
            </h2>
            <div
              style={{
                background: "var(--hachi-surface)",
                border: "0.5px solid var(--hachi-line)",
                borderRadius: 16,
                padding: 18,
                fontSize: 13,
                color: "var(--hachi-muted)",
              }}
            >
              <div
                style={{
                  marginBottom: 10,
                  color: "var(--hachi-ink)",
                }}
              >
                Honest about it: these run without asking, on your current
                safety level.
              </div>
              {UNCHECKED_ACTIONS.map((u, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 9,
                    padding: "6px 0",
                  }}
                >
                  <span
                    style={{
                      color: "var(--hachi-success)",
                      display: "inline-flex",
                    }}
                  >
                    <Icon name="check" size={15} />
                  </span>
                  {u}
                </div>
              ))}
              <GoSettingsLink onClick={goSettings} />
            </div>
          </div>

          {/* Past decisions — only rendered when history is non-empty */}
          {history.length > 0 && (
            <div>
              <h2
                style={{
                  margin: "0 0 12px",
                  fontFamily: "var(--font-display)",
                  fontWeight: 500,
                  fontSize: 16,
                }}
              >
                Past decisions
              </h2>
              <div
                style={{
                  background: "var(--hachi-surface)",
                  border: "0.5px solid var(--hachi-line)",
                  borderRadius: 16,
                  overflow: "hidden",
                }}
              >
                {history.map((h) => (
                  <HistoryRow key={h.id} item={h} />
                ))}
              </div>
            </div>
          )}

          {/* Permission audit — operator only; only rendered when auditLog is non-empty */}
          {operator && auditLog.length > 0 && (
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 9,
                  marginBottom: 12,
                }}
              >
                <h2
                  style={{
                    margin: 0,
                    fontFamily: "var(--font-display)",
                    fontWeight: 500,
                    fontSize: 16,
                  }}
                >
                  Permission audit
                </h2>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 500,
                    color: "var(--hachi-trust)",
                    background: "var(--hachi-trust-tint)",
                    padding: "3px 9px",
                    borderRadius: 999,
                  }}
                >
                  operator
                </span>
              </div>
              <div
                style={{
                  background: "var(--hachi-surface)",
                  border: "0.5px solid var(--hachi-line)",
                  borderRadius: 16,
                  overflow: "hidden",
                }}
              >
                {auditLog.map((a, i) => (
                  <AuditRow key={i} entry={a} />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- small inline link (avoids re-creating on each parent render) ----
function GoSettingsLink({ onClick }: { onClick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        marginTop: 8,
        border: "none",
        background: "transparent",
        color: hover ? "var(--hachi-trust)" : "var(--hachi-trust)",
        font: "500 13px var(--font-body)",
        cursor: "pointer",
        padding: 0,
        opacity: hover ? 0.8 : 1,
        transition: "opacity var(--dur-fast) var(--ease)",
      }}
    >
      Tighten the leash in settings →
    </button>
  );
}

// ============================================================
// Route registration
// ============================================================
export const Route = createFileRoute("/approvals")({
  component: Approvals,
});
