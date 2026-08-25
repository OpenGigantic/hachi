// ============================================================
// T-63 — store slice: home UI (concern #20 of 22).
// State only -- no dedicated actions in this file. `setHomeInput` lives in
// the chat/composer slice and `openWork`/`closeWork`/`setWorkFilter` live in
// the work-queue slice (both write these fields via the Store-typed `set`,
// matching the original file's banner grouping -- see the slice map in this
// task's report). Moved verbatim from frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { Store } from "../store";

export interface HomeUiState {
  homeInput: string;
  selectedWork: unknown | null;
  workFilter: string;
}

export const createHomeUiSlice: StateCreator<Store, [], [], HomeUiState> = () => ({
  homeInput: "",
  selectedWork: null,
  workFilter: "active",
});
