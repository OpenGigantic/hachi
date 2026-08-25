// ============================================================
// T-63 — store slice: work queue (concern #5 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. `openWork`/`closeWork`/`setWorkFilter` write
// `selectedWork`/`workFilter` (owned by the home-UI slice) via the
// Store-typed `set` -- they were textually under this file's original
// "// routines / work" banner, so they stay grouped here.
// ============================================================
import type { StateCreator } from "zustand";
import type { BlockedItem, FailedTask, FinishedTask, HistoryItem, Routine, Run } from "../../data/types";
import type { Store } from "../store";
import { nextId } from "./shared";

export interface WorkQueueState {
  history: HistoryItem[];
  runs: Run[];
  routines: Routine[];
  finished: FinishedTask[];
  failed: FailedTask[];
  blocked: BlockedItem[];
}

export interface WorkQueueActions {
  pauseRoutine: (id: number) => void;
  runRoutineNow: (id?: number) => void;
  cancelRun: (id: number) => void;
  retryFailed: (id: number) => void;
  duplicateRoutine: (id: number) => void;
  openWork: (item: unknown) => void;
  closeWork: () => void;
  setWorkFilter: (f: string) => void;
}

export const createWorkQueueSlice: StateCreator<
  Store,
  [],
  [],
  WorkQueueState & WorkQueueActions
> = (set, get) => ({
  history: [],
  runs: [],
  routines: [],
  finished: [],
  failed: [],
  blocked: [],

  pauseRoutine: (id) =>
    set((s) => ({
      routines: s.routines.map((r) =>
        r.id === id ? { ...r, paused: !r.paused } : r
      ),
    })),
  runRoutineNow: () => {
    set({ selectedWork: null });
    get().showToast("Running now — Hachi is working", "working");
  },
  cancelRun: (id) =>
    set((s) => ({ runs: s.runs.filter((r) => r.id !== id) })),
  retryFailed: (id) => {
    const f = get().failed.find((x) => x.id === id);
    set((s) => ({
      failed: s.failed.filter((x) => x.id !== id),
      runs: f
        ? [
            {
              id: nextId(),
              title: f.title,
              status: "working" as const,
              progress: 0.12,
              spent: 0.0,
              budget: 1.0,
              meta: "Retrying",
              owner: f.owner,
              when: "retrying now",
              steps: (f.steps || []).map((stp) => ({
                label: stp.label,
                state: "done" as const,
              })),
            },
            ...s.runs,
          ]
        : s.runs,
      selectedWork: null,
      workFilter: "active",
    }));
    get().showToast("Retrying — Hachi is working", "working");
  },
  duplicateRoutine: (id) => {
    const r = get().routines.find((x) => x.id === id);
    if (!r) return;
    set((s) => ({
      routines: [
        { ...r, id: nextId(), title: r.title + " (copy)" },
        ...s.routines,
      ],
    }));
  },
  openWork: (item) => set({ selectedWork: item }),
  closeWork: () => set({ selectedWork: null }),
  setWorkFilter: (f) => set({ workFilter: f }),
});
