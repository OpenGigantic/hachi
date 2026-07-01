// ============================================================
// GovernanceBar — persistent status strip shown on every route.
// Mounted in __root.tsx below the <Outlet />.
//
// Shows:
//   • Safety / governance posture (guarded / standard / open)
//   • Live cost readout: spent vs budget (wired to the corrected
//     `spent` field — double-count fixed in chat.ts)
//   • Connection / reconnecting status
// ============================================================

import React from "react";
import { useStore } from "../../lib/store/store";
import type { SafetyLevel } from "../../lib/data/types";

// ── Safety posture display metadata ────────────────────────────────────────

interface PostureMeta {
  label: string;
  dot: string;
  fg: string;
}

function postureMeta(level: SafetyLevel): PostureMeta {
  const map: Record<SafetyLevel, PostureMeta> = {
    guarded: {
      label: "guarded",
      dot: "var(--hachi-success)",
      fg: "var(--hachi-success)",
    },
    standard: {
      label: "standard",
      dot: "var(--hachi-trust)",
      fg: "var(--hachi-trust)",
    },
    open: {
      label: "open",
      dot: "var(--hachi-working)",
      fg: "var(--hachi-working)",
    },
  };
  return map[level] ?? map.standard;
}

// ── Component ───────────────────────────────────────────────────────────────

export function GovernanceBar() {
  const safety = useStore((s) => s.safety);
  const spent = useStore((s) => s.spent);
  const budget = useStore((s) => s.budget);
  const reconnecting = useStore((s) => s.reconnecting);
  const operator = useStore((s) => s.operator);

  const pm = postureMeta(safety);
  const pct = budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
  const overBudget = budget > 0 && spent > budget;

  return (
    <div
      className="hachi-governance-bar"
      style={{
        flexShrink: 0,
        height: "28px",
        background: "var(--hachi-canvas)",
        borderTop: "0.5px solid var(--hachi-line)",
        display: "flex",
        alignItems: "center",
        gap: "16px",
        padding: "0 16px",
        fontFamily: "var(--font-mono)",
        fontSize: "12px",
        color: "var(--hachi-muted)",
        userSelect: "none",
      }}
    >
      {/* Governance posture */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "5px",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            background: pm.dot,
            flexShrink: 0,
          }}
        />
        <span style={{ color: pm.fg, fontWeight: 500 }}>{pm.label}</span>
      </span>

      {/* Separator */}
      <span
        style={{
          width: "1px",
          height: "12px",
          background: "var(--hachi-line)",
          flexShrink: 0,
        }}
      />

      {/* Live cost readout */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          flexShrink: 0,
        }}
      >
        <span style={{ color: "var(--hachi-muted)" }}>spent</span>
        <span
          style={{
            color: overBudget ? "var(--hachi-danger)" : "var(--hachi-ink)",
            fontWeight: overBudget ? 600 : 400,
            userSelect: "text",
          }}
        >
          ${spent.toFixed(2)}
        </span>
        {budget > 0 && (
          <>
            <span style={{ color: "var(--hachi-muted)" }}>
              / ${budget.toFixed(2)}
            </span>
            {/* Inline mini progress bar */}
            <span
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(pct)}
              aria-label={`Budget: ${pct.toFixed(0)}% used`}
              style={{
                display: "inline-block",
                width: "48px",
                height: "3px",
                borderRadius: "999px",
                background: "var(--hachi-line)",
                overflow: "hidden",
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  display: "block",
                  width: `${pct}%`,
                  height: "100%",
                  background: overBudget
                    ? "var(--hachi-danger)"
                    : "var(--hachi-brand)",
                  borderRadius: "999px",
                  transition: "width 0.3s ease",
                }}
              />
            </span>
          </>
        )}
      </span>

      {/* Operator mode indicator */}
      {operator && (
        <>
          <span
            style={{
              width: "1px",
              height: "12px",
              background: "var(--hachi-line)",
              flexShrink: 0,
            }}
          />
          <span style={{ color: "var(--hachi-muted)", flexShrink: 0 }}>
            operator
          </span>
        </>
      )}

      {/* Spacer */}
      <span style={{ flex: 1 }} />

      {/* Connection status — shown on the right */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "5px",
          flexShrink: 0,
        }}
      >
        {reconnecting ? (
          <>
            <span
              style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                background: "var(--hachi-working)",
                flexShrink: 0,
                // Pulse animation via inline keyframe — uses CSS animation
                animation: "hachi-pulse 1.2s ease-in-out infinite",
              }}
            />
            <span style={{ color: "var(--hachi-working)" }}>reconnecting</span>
          </>
        ) : (
          <>
            <span
              style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                background: "var(--hachi-success)",
                flexShrink: 0,
              }}
            />
            <span>connected</span>
          </>
        )}
      </span>

      {/* Keyframe for the reconnecting pulse — injected once */}
      <style>{`
        @keyframes hachi-pulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.35; }
        }
      `}</style>
    </div>
  );
}
