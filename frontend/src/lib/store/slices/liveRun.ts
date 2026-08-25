// ============================================================
// T-63 — store slice: live-run view (concern #21 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. 13 of the 14 `dataMode === "live"` forks in
// the whole store live in this file (see store.test.ts's "live run —
// scripted demo queue must never fire (BB-36 gap-2 investigation)" describe
// block, which pins that the mock queue below never fires in live mode --
// those tests must keep passing untouched, they are the best signal this
// slice moved correctly).
//
// This file imports allowLeashLive/denyLeashLive/stopStreamingLive directly
// from the chat domain (rather than the module-scope import store.ts used
// to carry) -- that import already participates in a tolerated
// store<->chat module cycle (chat.ts imports useStore). Verified with
// `npm run build` (tsc --noEmit does not see TDZ issues from module-init
// order; Rollup does) -- see this task's report.
// ============================================================
import type { StateCreator } from "zustand";
import { allowLeashLive, denyLeashLive, stopStreamingLive } from "../../data/domains/chat";
import { apiSend } from "../../data/client";
import type { FinishedTask, Run, RunPackMember } from "../../data/types";
import type { Store } from "../store";

// ---- run-queue item type (internal) ----
interface RunQueueItem {
  line?: { text: string; tone?: string };
  steps?: { i: number; state: string }[];
  pack?: { id: string; status: string; task?: string };
  spend?: number;
  leash?: { requestId: string; request: string; action: string; why: string };
}

export interface LiveRunState {
  runView: Run | null;
  runLines: { text: string; tone?: string }[];
  runStreaming: boolean;
  runDone: boolean;
  runLeash: { requestId: string; request: string; action: string; why: string } | null;
  runSpent: number;
  runPack: RunPackMember[];
  runSteps: { label: string; state: string }[];
}

export interface LiveRunActions {
  // live run
  openRun: (runId?: number, from?: string) => void;
  watchLive: (runId: number) => void;
  closeRun: () => void;
  runAllowLeash: () => void;
  runAlwaysAllow: () => void;
  runDenyLeash: () => void;
  stopRun: () => void;
}

// module-level timers/queue, private to this slice (moved verbatim from store.ts)
let _runIv: ReturnType<typeof setInterval> | null = null;
let _runQueue: RunQueueItem[] = [];
let _runFrom = "home";
let _runStart = 0;

function clearRunIv() { if (_runIv) { clearInterval(_runIv); _runIv = null; } }

const slug = (t: string) =>
  String(t || "task")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 28) || "task";

export const createLiveRunSlice: StateCreator<Store, [], [], LiveRunState & LiveRunActions> = (
  set,
  get
) => {
  // ---- run helpers ----
  const applyRunSteps = (arr: { i: number; state: string }[]) => {
    set((s) => ({
      runSteps: s.runSteps.map((st, i) => {
        const u = arr.find((a) => a.i === i);
        return u ? { ...st, state: u.state } : st;
      }),
    }));
  };

  const archiveRun = (): Partial<Store> => {
    const s = get();
    const rv = s.runView;
    if (!rv) return {};
    const finItem: FinishedTask = {
      id: rv.id,
      title: rv.title,
      note: "Hachi is resting",
      when: "just now",
      cost: s.runSpent,
      result: "Summarised 6 PRs — 2 need review. Posted to #eng.",
      steps: s.runSteps.map((st) => ({
        label: st.label,
        state: (st.state === "working" ? "done" : st.state) as "done" | "working" | "pending" | "failed",
      })),
    };
    return {
      runs: s.runs.filter((r) => r.id !== rv.id),
      finished: [finItem, ...s.finished],
      spent:
        Math.round(
          (s.spent + Math.max(0, s.runSpent - (rv.spent || 0))) * 100
        ) / 100,
    };
  };

  const finishRun = () => {
    clearRunIv();
    const s = get();
    if (s.runDone || !s.runView) return;
    const elapsed = _runStart
      ? Math.ceil((Date.now() - _runStart) / 1000)
      : 0;
    const elStr = elapsed ? " · " + (elapsed < 60 ? elapsed + "s" : Math.floor(elapsed / 60) + "m " + (elapsed % 60) + "s") : "";
    set({
      ...archiveRun(),
      runStreaming: false,
      runDone: true,
      runLines: [
        ...s.runLines,
        { text: "done — Hachi is resting" + elStr, tone: "success" },
      ],
      runSteps: s.runSteps.map((st) => ({ ...st, state: "done" })),
      runPack: s.runPack.map((p) => ({ ...p, status: "done" as const })),
    });
    get().showToast("Task complete — Hachi is resting", "success");
  };

  const runTick = () => {
    const s = get();
    if (s.runDone || !s.runView) { clearRunIv(); return; }
    if (!_runQueue || _runQueue.length === 0) { finishRun(); return; }
    const item = _runQueue.shift()!;
    if (item.steps) applyRunSteps(item.steps);
    if (item.leash) {
      clearRunIv();
      set({ runLeash: item.leash, runStreaming: false });
      return;
    }
    set((prev) => {
      let pack = prev.runPack;
      if (item.pack) {
        pack = pack.map((p) =>
          p.id === item.pack!.id ? { ...p, ...item.pack } : p
        ) as RunPackMember[];
      }
      return {
        runLines: item.line ? [...prev.runLines, item.line] : prev.runLines,
        runPack: pack,
        runSpent: item.spend
          ? Math.round((prev.runSpent + item.spend) * 100) / 100
          : prev.runSpent,
      };
    });
  };

  return {
    runView: null,
    runLines: [],
    runStreaming: false,
    runDone: false,
    runLeash: null,
    runSpent: 0,
    runPack: [],
    runSteps: [],

    // live run
    openRun: (runId, from = "home") => {
      _runFrom = from;

      // Live mode: don't seed mock data or start the mock interval.
      if (get().dataMode === "live") {
        const rv = get().runView;
        if (!rv) {
          get().showToast("Nothing live right now — start a task in the conversation", "success");
        }
        // If runView exists it was already seeded by startTaskLive — leave it.
        return;
      }

      // ── Mock mode: existing simulation ──────────────────────────────────
      clearRunIv();
      const s = get();
      const r =
        (runId != null && s.runs.find((x) => x.id === runId)) ||
        s.runs[0];
      if (!r) {
        get().showToast("Nothing live right now — Hachi is on watch", "success");
        return;
      }
      set({
        runView: r,
        runLines: [
          { text: "hachi ▸ run " + slug(r.title) + " --watch" },
          { text: "→ starting on your machine", tone: "muted" },
        ],
        runStreaming: true,
        runDone: false,
        runLeash: null,
        runSpent: r.spent || 0,
        runPack: [
          { id: "p1", name: "reader-1", role: "reader", task: "reading PR #482 · auth refactor", status: "working", tokens: 6240 },
          { id: "p2", name: "reader-2", role: "reader", task: "reading PR #485 · vitest migration", status: "working", tokens: 5180 },
          { id: "p3", name: "writer", role: "writer", task: "waiting for the readers", status: "pending", tokens: 0 },
        ],
        runSteps: (
          r.steps && r.steps.length
            ? r.steps
            : [
                { label: "Read the source", state: "working" },
                { label: "Do the work", state: "pending" },
                { label: "Report back to you", state: "pending" },
              ]
        ).map((step) => ({ ...step })),
      });
      _runQueue = [
        { line: { text: "✓ cloned repo index · 1,204 files", tone: "success" }, steps: [{ i: 0, state: "done" }] },
        { line: { text: "→ spawning the pack · 3 sub-agents", tone: "muted" }, steps: [{ i: 1, state: "working" }] },
        { line: { text: "· reader-1 → PR #482 auth refactor", tone: "muted" } },
        { line: { text: "· reader-2 → PR #485 vitest migration", tone: "muted" } },
        { line: { text: "✓ reader-1 done — 2 risks noted", tone: "success" }, pack: { id: "p1", status: "done", task: "PR #482 · 2 risks noted" }, spend: 0.01 },
        { line: { text: "✓ reader-2 done — looks clean", tone: "success" }, pack: { id: "p2", status: "done", task: "PR #485 · looks clean" }, spend: 0.01 },
        { line: { text: "→ writer drafting the summary", tone: "muted" }, pack: { id: "p3", status: "working", task: "drafting the summary" }, steps: [{ i: 1, state: "done" }, { i: 2, state: "working" }] },
        { line: { text: "✓ summary ready — 6 PRs · 2 need review", tone: "success" }, pack: { id: "p3", status: "done", task: "summary drafted" }, steps: [{ i: 2, state: "done" }], spend: 0.01 },
        { leash: { requestId: "", request: "Hachi wants to post the summary", action: "slack.post #eng · \"This week: 6 PRs, 2 need review\"", why: "share the weekly PR summary with your team" }, steps: [{ i: 3, state: "working" }] },
        { line: { text: "✓ posted to #eng", tone: "success" } },
      ];
      _runStart = Date.now();
      _runIv = setInterval(() => runTick(), 1100);
    },

    watchLive: (runId) => get().openRun(runId, "work"),

    closeRun: () => {
      clearRunIv();
      _runQueue = [];
      set({ runView: null, runLeash: null });
    },

    runAllowLeash: () => {
      // Live mode: route approval through the gateway
      if (get().dataMode === "live") {
        set((s) => ({
          runLeash: null,
          runStreaming: true,
          runLines: [...s.runLines, { text: "✓ allowed — continuing", tone: "success" }],
        }));
        void allowLeashLive();
        get().showToast("On it — Hachi is working", "working");
        return;
      }

      // ── Mock mode ─────────────────────────────────────────────────────
      set((s) => ({
        runLeash: null,
        runStreaming: true,
        runLines: [...s.runLines, { text: "✓ allowed — posting now", tone: "success" }],
      }));
      clearRunIv();
      _runIv = setInterval(() => runTick(), 1100);
      get().showToast("On it — Hachi is working", "working");
    },

    runAlwaysAllow: () => {
      const rl = get().runLeash;
      if (get().dataMode === "live") {
        if (rl) {
          // Persist the always-allow rule; surface failure honestly
          void (async () => {
            try {
              await apiSend("POST", `/sessions/current/permissions/always-allow`, {
                request: rl.request,
                action: rl.action,
              });
              set((s) => ({
                runLeash: null,
                runStreaming: true,
                runLines: [...s.runLines, { text: "✓ always allowed — continuing", tone: "success" }],
              }));
                void allowLeashLive();
              get().showToast("Saved — Hachi won't ask again for this", "success");
            } catch {
              // Endpoint returned an error — tell the user
              get().showToast("Could not save always-allow rule — please try again", "blocked");
            }
          })();
        } else {
          // No leash context — just allow
          set((s) => ({
            runLeash: null,
            runStreaming: true,
            runLines: [...s.runLines, { text: "✓ always allowed — continuing", tone: "success" }],
          }));
          void allowLeashLive();
          get().showToast("Saved — Hachi won't ask again for this", "success");
        }
        return;
      }

      // ── Mock mode ─────────────────────────────────────────────────────
      // Then proceed exactly like runAllowLeash
      set((s) => ({
        runLeash: null,
        runStreaming: true,
        runLines: [...s.runLines, { text: "✓ always allowed — posting now", tone: "success" }],
      }));
      clearRunIv();
      _runIv = setInterval(() => runTick(), 1100);
      get().showToast("Saved — Hachi won't ask again for this", "success");
    },

    runDenyLeash: () => {
      // Live mode: abort the stream and mark done
      if (get().dataMode === "live") {
        const s = get();
        set({
          runLeash: null,
          runStreaming: false,
          runDone: true,
          runLines: [
            ...s.runLines,
            { text: "✗ denied — Hachi is resting", tone: "danger" },
          ],
          runPack: s.runPack.map((p) => ({
            ...p,
            status: p.status === "working" ? "done" as const : p.status,
          })),
        });
          void denyLeashLive();
        get().showToast("Stayed put — Hachi is resting", "success");
        return;
      }

      // ── Mock mode ─────────────────────────────────────────────────────
      clearRunIv();
      _runQueue = [];
      const s = get();
      set({
        ...archiveRun(),
        runLeash: null,
        runStreaming: false,
        runDone: true,
        runLines: [
          ...s.runLines,
          { text: "✗ denied — Hachi is resting", tone: "danger" },
        ],
        runPack: s.runPack.map((p) => ({
          ...p,
          status: p.status === "working" ? "done" as const : p.status,
        })),
      });
      get().showToast("Stayed put — Hachi is resting", "success");
    },

    stopRun: () => {
      // Live mode: abort the SSE stream and mark done
      if (get().dataMode === "live") {
        const s = get();
        set({
          runStreaming: false,
          runDone: true,
          runLeash: null,
          runLines: [
            ...s.runLines,
            { text: "■ stopped — Hachi is resting", tone: "muted" },
          ],
          runPack: s.runPack.map((p) => ({
            ...p,
            status: p.status === "working" ? "done" as const : p.status,
          })),
        });
          stopStreamingLive();
        get().showToast("Stopped — Hachi is resting", "success");
        return;
      }

      // ── Mock mode ─────────────────────────────────────────────────────
      clearRunIv();
      _runQueue = [];
      const s = get();
      set({
        ...archiveRun(),
        runStreaming: false,
        runDone: true,
        runLeash: null,
        runLines: [
          ...s.runLines,
          { text: "■ stopped — Hachi is resting", tone: "muted" },
        ],
        runPack: s.runPack.map((p) => ({
          ...p,
          status: p.status === "working" ? "done" as const : p.status,
        })),
      });
      get().showToast("Stopped — Hachi is resting", "success");
    },
  };
};
