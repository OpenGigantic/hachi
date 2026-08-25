// ============================================================
// T-63 — store slice: authoring (concern #10 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper call becomes `get().showToast(...)` since the toasts
// slice now owns that action (see the toasts slice for the identical
// implementation `showToast` forwards to).
// ============================================================
import type { StateCreator } from "zustand";
import type { AuthTarget, DiffLine } from "../../data/types";
import type { Store } from "../store";

export interface AuthoringState {
  authTargets: AuthTarget[];
  authSource: DiffLine[];
  authSel: string;
  authMode: "diff" | "source";
  authAppliedTarget: string | null;
}

export interface AuthoringActions {
  selectAuthTarget: (name: string) => void;
  setAuthMode: (m: "diff" | "source") => void;
  applyPatch: (reindexed_ms?: number) => void;
  revertPatch: () => void;
  rejectPatch: () => void;
}

export const createAuthoringSlice: StateCreator<
  Store,
  [],
  [],
  AuthoringState & AuthoringActions
> = (set, get) => ({
  authTargets: [],
  authSource: [],
  authSel: "deep_research.og",
  authMode: "diff",
  authAppliedTarget: null,

  selectAuthTarget: (name) => set({ authSel: name }),
  setAuthMode: (m) => set({ authMode: m }),
  applyPatch: (reindexed_ms?: number) => {
    const sel = get().authSel;
    set((s) => ({
      authAppliedTarget: sel,
      authTargets: s.authTargets.map((t) =>
        t.name === sel ? { ...t, dirty: false } : t
      ),
    }));
    const reindexStr =
      reindexed_ms !== undefined
        ? `re-indexed in ${(reindexed_ms / 1000).toFixed(1)}s`
        : "re-indexed";
    get().showToast(`Patch applied — ${reindexStr}`, "success");
  },
  revertPatch: () => {
    const sel = get().authSel;
    set((s) => ({
      authAppliedTarget: null,
      authTargets: s.authTargets.map((t) =>
        t.name === sel ? { ...t, dirty: true } : t
      ),
    }));
  },
  rejectPatch: () => {
    get().showToast("Rejected — nothing was written", "success");
  },
});
