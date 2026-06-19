// ============================================================
// Live run — /run
// Prototype markup: Hachi Web App.dc.html lines 1239–1310
// Logic: lines 1927–2012, 2452–2471 (openRun, runTick, runAllowLeash,
//         runDenyLeash, stopRun, closeRun, _finishRun, renderVals run block)
//
// T4.5 decision: Option B (honest, no fabricated simulation).
// The gateway has no dedicated run/jobs streaming endpoint — only
// GET /sessions/{id}/stream which serves conversation turns. Driving the
// run screen from that would misrepresent conversation events as sub-agent
// orchestration output. Instead, in live mode we show an honest redirect
// card: the user is told that live run streaming is available in the
// conversation view and is offered a button to go there.
// Mock mode preserves the full simulation so offline dev/demos still work.
// ============================================================

import React, { useEffect, useRef } from "react";
import type { CSSProperties } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useStore } from "../lib/store/store";
import { Icon } from "../components/icons/Icon";
import { Terminal } from "../components/ui/Terminal";
import { LeashPrompt } from "../components/brand/LeashPrompt";

// ---- helpers ----

function fmtMoney(n: number): string {
  return "$" + Number(n).toFixed(2);
}

// Mirror prototype statusMeta — returns pill colors for each run phase.
function statusMeta(phase: "working" | "done" | "blocked") {
  const map = {
    working: {
      bg: "var(--hachi-working-tint)",
      fg: "#A8651A",
      dot: "var(--hachi-working)",
      text: "working",
    },
    done: {
      bg: "var(--hachi-success-tint)",
      fg: "#2C7A50",
      dot: "var(--hachi-success)",
      text: "done",
    },
    blocked: {
      bg: "var(--hachi-danger-tint)",
      fg: "#9E2D21",
      dot: "var(--hachi-danger)",
      text: "blocked",
    },
  };
  return map[phase] ?? map.working;
}

// Mirror prototype stepMeta — icon + color per step state.
function stepMeta(state: string): { icon: string; color: string } {
  const map: Record<string, { icon: string; color: string }> = {
    done: { icon: "circle-check", color: "var(--hachi-success)" },
    working: { icon: "play", color: "var(--hachi-working)" },
    failed: { icon: "circle-alert", color: "var(--hachi-danger)" },
    pending: { icon: "minus", color: "var(--hachi-muted)" },
  };
  return map[state] ?? map.pending;
}

// Mirror prototype packMeta — dot/text/fg per pack member status.
function packMeta(status: string): { dot: string; text: string; fg: string } {
  if (status === "done") {
    return { dot: "var(--hachi-success)", text: "done", fg: "#2C7A50" };
  }
  if (status === "working") {
    return { dot: "var(--hachi-working)", text: "on it", fg: "#A8651A" };
  }
  return { dot: "var(--hachi-line)", text: "queued", fg: "var(--hachi-muted)" };
}

// ============================================================
// Honest live-mode placeholder
// ============================================================

function LiveRunHonest() {
  const navigate = useNavigate();

  const containerStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    padding: 32,
    gap: 0,
  };

  const cardStyle: CSSProperties = {
    background: "var(--hachi-surface)",
    border: "0.5px solid var(--hachi-line)",
    borderRadius: 16,
    padding: "36px 40px",
    maxWidth: 520,
    width: "100%",
    display: "flex",
    flexDirection: "column",
    gap: 20,
  };

  const iconRowStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
  };

  const titleStyle: CSSProperties = {
    margin: 0,
    fontFamily: "var(--font-display)",
    fontWeight: 500,
    fontSize: 20,
    color: "var(--hachi-ink)",
  };

  const bodyStyle: CSSProperties = {
    fontSize: 14,
    color: "var(--hachi-muted)",
    lineHeight: "22px",
    margin: 0,
  };

  const chatBtnStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    height: 40,
    padding: "0 18px",
    borderRadius: 10,
    background: "var(--hachi-trust)",
    color: "#fff",
    font: "500 14px var(--font-body)",
    border: "none",
    cursor: "pointer",
    alignSelf: "flex-start",
    transition: "opacity var(--dur-fast) var(--ease)",
  };

  const backBtnStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    height: 40,
    padding: "0 18px",
    borderRadius: 10,
    background: "transparent",
    color: "var(--hachi-muted)",
    font: "500 14px var(--font-body)",
    border: "0.5px solid var(--hachi-line)",
    cursor: "pointer",
    alignSelf: "flex-start",
    transition: "background var(--dur-fast) var(--ease)",
  };

  const btnRowStyle: CSSProperties = {
    display: "flex",
    gap: 10,
    flexWrap: "wrap",
  };

  return (
    <div style={containerStyle}>
      <div style={cardStyle}>
        <div style={iconRowStyle}>
          <span style={{ color: "var(--hachi-working)", display: "inline-flex" }}>
            <Icon name="terminal" size={22} />
          </span>
          <h1 style={titleStyle}>Live run streaming</h1>
        </div>

        <p style={bodyStyle}>
          Live run streaming — with real sub-agent output, pack progress, and
          step-by-step planning — is available in the conversation view.
        </p>
        <p style={bodyStyle}>
          Start a task in the chat and Hachi will show you exactly what it's
          doing as it happens. The live output panel here is only available
          when a backend run-streaming endpoint is connected.
        </p>

        <div style={btnRowStyle}>
          <button
            style={chatBtnStyle}
            onClick={() => void navigate({ to: "/conversation" })}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = "0.85";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = "1";
            }}
          >
            <Icon name="message-square" size={15} />
            Open conversation
          </button>
          <button
            style={backBtnStyle}
            onClick={() => void navigate({ to: "/" })}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                "var(--hachi-cream)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                "transparent";
            }}
          >
            <Icon name="chevron-left" size={15} />
            Back
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Mock simulation component (mock mode only) ----

function MockRunSimulation() {
  const navigate = useNavigate();

  // Store slices
  const runView = useStore((s) => s.runView);
  const runLines = useStore((s) => s.runLines);
  const runDone = useStore((s) => s.runDone);
  const runLeash = useStore((s) => s.runLeash);
  const runSpent = useStore((s) => s.runSpent);
  const runPack = useStore((s) => s.runPack);
  const runSteps = useStore((s) => s.runSteps);

  // Store actions
  const closeRun = useStore((s) => s.closeRun);
  const stopRun = useStore((s) => s.stopRun);
  const runAllowLeash = useStore((s) => s.runAllowLeash);
  const runDenyLeash = useStore((s) => s.runDenyLeash);
  const openRun = useStore((s) => s.openRun);

  // Auto-scroll ref for the terminal output wrapper
  const outputRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = outputRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [runLines]);

  // If there is no active run, boot one from the first available run in the
  // store (mirrors prototype behaviour when navigating to /run directly).
  useEffect(() => {
    if (!runView) {
      openRun(undefined, "run");
    }
  }, []); // run once on mount only

  // Navigate back on closeRun (runView becomes null after close).
  // We do NOT fire this on initial mount (before the useEffect above kicks in).
  const didMountRef = useRef(false);
  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true;
      return;
    }
    // runView cleared by closeRun → go back
    if (!runView) {
      navigate({ to: "/" });
    }
  }, [runView]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- derived renderVals (mirror dc.html lines 2452–2471) ----
  const runPhase: "working" | "done" | "blocked" = runDone
    ? "done"
    : runLeash
    ? "blocked"
    : "working";

  const rsm = statusMeta(runPhase);

  const statusText = runDone
    ? "Hachi is resting"
    : runLeash
    ? "Hachi is blocked — needs you"
    : "Hachi is working";

  const spentStr = fmtMoney(runSpent);
  const budgetStr = fmtMoney(runView?.budget ?? 1.0);

  const canStop = !runDone && !runLeash;

  const onit = runPack.filter((p) => p.status === "working").length;
  const packLabel = runDone
    ? runPack.length + " rested"
    : onit > 0
    ? onit + " on it"
    : "spinning up";

  const title = runView?.title ?? "Live run";

  const steps = runSteps.map((stp) => {
    const mm = stepMeta(stp.state);
    return {
      label: stp.label,
      icon: mm.icon,
      color: mm.color,
      textColor:
        stp.state === "pending"
          ? "var(--hachi-muted)"
          : "var(--hachi-ink)",
    };
  });

  const pack = runPack.map((p) => {
    const m = packMeta(p.status);
    return {
      id: p.id,
      name: p.name,
      task: p.task,
      dot: m.dot,
      statusText: m.text,
      statusFg: m.fg,
      tokens: (p.tokens ?? 0).toLocaleString(),
      hasTokens: (p.tokens ?? 0) > 0,
    };
  });

  const hasLeash = !!runLeash;

  // ---- handlers ----
  const handleBack = () => {
    closeRun();
    // Navigation is handled by the runView→null effect above.
  };

  // ---- styles ----
  const headerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 14,
    padding: "0 32px",
    height: 68,
    flexShrink: 0,
    borderBottom: "0.5px solid var(--hachi-line)",
  };

  const backBtnStyle: CSSProperties = {
    border: "none",
    background: "transparent",
    color: "var(--hachi-muted)",
    cursor: "pointer",
    display: "inline-flex",
    padding: 6,
    borderRadius: 8,
    marginLeft: -6,
    transition: "background var(--dur-fast) var(--ease)",
  };

  const pillStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    padding: "5px 11px",
    borderRadius: 999,
    background: rsm.bg,
    color: rsm.fg,
    fontSize: 12,
    fontWeight: 500,
  };

  const stopBtnStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    height: 36,
    padding: "0 14px",
    borderRadius: 10,
    border: "1px solid var(--hachi-trust)",
    background: "transparent",
    color: "var(--hachi-trust)",
    font: "500 13px var(--font-body)",
    cursor: "pointer",
    transition: "background var(--dur-fast) var(--ease)",
  };

  const leftPanelStyle: CSSProperties = {
    width: 312,
    flexShrink: 0,
    borderRight: "0.5px solid var(--hachi-line)",
    overflow: "auto",
    padding: "22px 22px 32px",
    display: "flex",
    flexDirection: "column",
    gap: 26,
  };

  const sectionLabelStyle: CSSProperties = {
    fontSize: 12,
    color: "var(--hachi-muted)",
    marginBottom: 10,
  };

  const packCardStyle: CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    background: "var(--hachi-surface)",
    border: "0.5px solid var(--hachi-line)",
    borderRadius: 12,
    padding: "11px 13px",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* ---- header ---- */}
      <div style={headerStyle}>
        {/* back button */}
        <button
          onClick={handleBack}
          style={backBtnStyle}
          aria-label="Back"
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "var(--hachi-cream)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              "transparent";
          }}
        >
          <Icon name="chevron-left" size={18} />
        </button>

        {/* title */}
        <h1
          style={{
            margin: 0,
            fontFamily: "var(--font-display)",
            fontWeight: 500,
            fontSize: 20,
            lineHeight: "24px",
            color: "var(--hachi-ink)",
          }}
        >
          {title}
        </h1>

        {/* status pill */}
        <span style={pillStyle}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: rsm.dot,
            }}
          />
          {rsm.text}
        </span>

        {/* status text */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            color: "var(--hachi-muted)",
          }}
        >
          <span>{statusText}</span>
        </div>

        <div style={{ flex: 1 }} />

        {/* budget */}
        <span
          style={{
            fontSize: 13,
            color: "var(--hachi-muted)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          spent {spentStr} of {budgetStr}
        </span>

        {/* stop button */}
        {canStop && (
          <button
            onClick={stopRun}
            style={stopBtnStyle}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                "var(--hachi-trust-tint)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background =
                "transparent";
            }}
          >
            <Icon name="stop" size={15} />
            Stop
          </button>
        )}
      </div>

      {/* ---- body ---- */}
      <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
        {/* ==== left panel: plan + the pack ==== */}
        <div className="hachi-scroll" style={leftPanelStyle}>
          {/* Plan section */}
          <div>
            <div style={sectionLabelStyle}>Plan</div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {steps.map((stp, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 11,
                    padding: "9px 0",
                    borderBottom: "0.5px solid var(--hachi-line)",
                  }}
                >
                  <span
                    style={{
                      color: stp.color,
                      display: "inline-flex",
                    }}
                  >
                    <Icon name={stp.icon} size={16} />
                  </span>
                  <span
                    style={{
                      fontSize: 14,
                      color: stp.textColor,
                    }}
                  >
                    {stp.label}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* The pack section */}
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                marginBottom: 11,
              }}
            >
              <span
                style={{
                  color: "var(--hachi-trust)",
                  display: "inline-flex",
                }}
              >
                <Icon name="paw" size={15} />
              </span>
              <span style={{ fontSize: 12, color: "var(--hachi-muted)" }}>
                The pack · {packLabel}
              </span>
            </div>

            <div
              style={{ display: "flex", flexDirection: "column", gap: 8 }}
            >
              {pack.map((p) => (
                <div key={p.id} style={packCardStyle}>
                  {/* status dot */}
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: p.dot,
                      marginTop: 5,
                      flexShrink: 0,
                    }}
                  />
                  {/* name + task */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                      }}
                    >
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 13,
                          color: "var(--hachi-ink)",
                        }}
                      >
                        {p.name}
                      </span>
                      {p.hasTokens && (
                        <span
                          style={{
                            fontSize: 11,
                            color: "var(--hachi-muted)",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {p.tokens} tok
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--hachi-muted)",
                        marginTop: 2,
                      }}
                    >
                      {p.task}
                    </div>
                  </div>
                  {/* status label */}
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 500,
                      color: p.statusFg,
                    }}
                  >
                    {p.statusText}
                  </span>
                </div>
              ))}
            </div>

            {/* sub-agent disclaimer */}
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 7,
                marginTop: 13,
                fontSize: 12,
                color: "var(--hachi-muted)",
                lineHeight: "18px",
              }}
            >
              <Icon name="shield" size={13} />
              <span>
                Sub-agents share your leash — they ask before anything risky
                too.
              </span>
            </div>
          </div>
        </div>

        {/* ==== right panel: live output ==== */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            position: "relative",
          }}
        >
          {/* output header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "16px 24px 12px",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                color: "var(--hachi-muted)",
                display: "inline-flex",
              }}
            >
              <Icon name="terminal" size={15} />
            </span>
            <span style={{ fontSize: 13, color: "var(--hachi-muted)" }}>
              Live output
            </span>
          </div>

          {/* scrollable terminal wrapper */}
          <div
            ref={outputRef}
            className="hachi-run-output hachi-scroll"
            style={{
              flex: 1,
              minHeight: 0,
              overflow: "auto",
              padding: "0 24px 24px",
            }}
          >
            <Terminal
              lines={runLines}
              prompt=""
              style={{ display: "block", minHeight: "100%" }}
            />
          </div>

          {/* leash overlay (inline, mid-run) */}
          {hasLeash && runLeash && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                background: "rgba(28,26,32,0.22)",
                display: "grid",
                placeItems: "center",
                padding: 24,
                zIndex: 10,
              }}
            >
              <LeashPrompt
                request={runLeash.request}
                action={runLeash.action}
                why={runLeash.why}
                onAllowOnce={runAllowLeash}
                onAlwaysAllow={runAllowLeash}
                onDecline={runDenyLeash}
                style={{ width: 440, maxWidth: "100%" }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Top-level route component ----
// Delegates to the honest placeholder in live mode;
// keeps the mock simulation in mock mode.

function LiveRun() {
  const dataMode = useStore((s) => s.dataMode);
  if (dataMode === "live") {
    return <LiveRunHonest />;
  }
  return <MockRunSimulation />;
}

export const Route = createFileRoute("/run")({
  component: LiveRun,
});
