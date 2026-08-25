// ============================================================
// T-63 — store slice: review (concern #7 of 22).
// State only -- no dedicated actions in this file. `approveProposed` /
// `rejectProposed` live in the memory slice (matching the original file's
// "// memory" banner grouping, which included them) and write `proposed`
// via the Store-typed `set`. Moved verbatim from
// frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { ProposedChange } from "../../data/types";
import type { Store } from "../store";

export interface ReviewState {
  proposed: ProposedChange | null;
}

export const createReviewSlice: StateCreator<Store, [], [], ReviewState> = () => ({
  proposed: null,
});
