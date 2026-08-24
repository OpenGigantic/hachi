// ============================================================
// Hachi Web App — chat kernel: per-turn cost baseline tracking
//
// Extracted from chat.ts (T-65 / CF-16): was three module-level `let`s
// (_costCumAtTurnStart, _spentAtTurnStart, _lastKnownCumCost) reset by hand
// in four separate functions (loadConversationHistory, switchSessionLive,
// deleteSessionLive, newSessionLive). See docs/bug-bank.md.
//
// The gateway's `usage.updated` payload carries a CUMULATIVE `estimated_cost_usd`
// (running total since session boot — confirmed in orchestrator.py:720-756 and
// CostTracker.snapshot() in core/cost.py which accumulates via +=).
//
// To derive the cost delta for *this turn*, chat.ts snapshots two baselines at
// stream start:
//   costCumAtTurnStart  — the last cumulative value seen before this turn's
//                         first `usage.updated` event. Starts at 0 for the
//                         first turn; carries forward across turns.
//   spentAtTurnStart    — the `spent` field in the store at the moment we
//                         open the SSE stream, so we can reconstruct the
//                         absolute `spent` as turns accumulate.
//
// On each `usage.updated` tick (still handled in chat.ts):
//   turnDelta = cumulative - costCumAtTurnStart
//   taskSpent = turnDelta           (cost of THIS turn only)
//   spent     = spentAtTurnStart + turnDelta  (all-time total)
//
// Example: gateway sends 0.01, 0.02, 0.03 over 3 ticks of a turn where
// costCumAtTurnStart = 0 and spentAtTurnStart = 0.05 (prior turns).
//   tick1: taskSpent=0.01, spent=0.06
//   tick2: taskSpent=0.02, spent=0.07
//   tick3: taskSpent=0.03, spent=0.08  ← correct; no double-count.
//
// Deliberately store-free (no useStore import) — chat.ts remains the only
// module in the cycle with useStore + ../client/../sse at top level. Plain
// property access only: no getters, so touching this object on every
// streamed token (usage.updated can fire many times per turn) never
// allocates. See T-65 spec §12.
// ============================================================

export const costMeter = {
  costCumAtTurnStart: 0,
  spentAtTurnStart: 0,
  // Updated on every usage.updated tick so we always know the last cumulative
  // value; used to seed costCumAtTurnStart when the next turn opens.
  lastKnownCumCost: 0,

  /** Reset all three baselines to 0 (session/turn boundary). */
  reset(): void {
    this.costCumAtTurnStart = 0;
    this.spentAtTurnStart = 0;
    this.lastKnownCumCost = 0;
  },
};
