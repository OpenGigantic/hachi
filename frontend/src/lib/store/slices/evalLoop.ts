// ============================================================
// T-63 — store slice: eval loop (concern #22 of 22).
// No dedicated actions -- both fields are hydrate-only (interactions is
// populated by the eval-loop domain loader; lastUsedModel is set from a
// usage/turn-complete SSE event via hydrate). Moved verbatim from
// frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { AgentInteraction } from "../../data/types";
import type { Store } from "../store";

export interface EvalLoopState {
  // FIX 4: last model id seen in a usage/turn-complete SSE event
  lastUsedModel: string | null;
  // eval loop
  interactions: AgentInteraction[];
}

export const createEvalLoopSlice: StateCreator<Store, [], [], EvalLoopState> = () => ({
  lastUsedModel: null,
  interactions: [],
});
