// ============================================================
// T-63 — store slice: OG explorer (concern #11 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { OgNode } from "../../data/types";
import type { Store } from "../store";

export interface OgState {
  ogNodes: OgNode[];
  ogSel: string;
  ogView: "graph" | "tree";
}

export interface OgActions {
  setOgSel: (id: string) => void;
  setOgView: (v: "graph" | "tree") => void;
  setOgHover: (id: string | null) => void;
}

export const createOgSlice: StateCreator<Store, [], [], OgState & OgActions> = (set) => ({
  ogNodes: [],
  ogSel: "brain",
  ogView: "graph",

  setOgSel: (id) => set({ ogSel: id }),
  setOgView: (v) => set({ ogView: v }),
  setOgHover: (_id) => {
    // local-only hover not stored in global state per contract guidance
    // screens should use local useState for hover; this is a no-op shim
  },
});
