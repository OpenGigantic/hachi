// ============================================================
// T-63 — store slice: budget / spend (concern #3 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts. `setTaskCap`/
// `setDailyCap` were textually under a separate "settings helpers" banner
// in the original Actions interface, but write taskCap/dailyCap (this
// slice's own fields), so they land here by state ownership. The rest of
// this slice's fields (spent/taskSpent/spendTurns/spendTidies/spendModels)
// have no dedicated setters -- they are written by other actions'
// Store-typed `set()` calls (e.g. icAllow, dryRunRoutine, live-run
// archiveRun) and by the spend domain loader via hydrate().
// ============================================================
import type { StateCreator } from "zustand";
import type { SpendModel, SpendTidy, SpendTurn } from "../../data/types";
import type { Store } from "../store";

export interface BudgetState {
  spent: number;
  budget: number;
  taskSpent: number;
  spendTurns: SpendTurn[];
  spendTidies: SpendTidy[];
  spendModels: SpendModel[];
  // spend caps (editable)
  taskCap: number;
  dailyCap: number;
}

export interface BudgetActions {
  setTaskCap: (v: number) => void;
  setDailyCap: (v: number) => void;
}

export const createBudgetSlice: StateCreator<Store, [], [], BudgetState & BudgetActions> = (
  set
) => ({
  spent: 0,
  budget: 5.0,
  taskSpent: 0,
  spendTurns: [],
  spendTidies: [],
  spendModels: [],
  taskCap: 1.0,
  dailyCap: 5.0,

  setTaskCap: (v) => set({ taskCap: v }),
  setDailyCap: (v) => {
    set({ dailyCap: v, budget: v });
  },
});
