// ============================================================
// T-63 — store slice: approvals (concern #4 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. `resolveApproval` cross-writes `history`
// (owned by the work-queue slice) via the Store-typed `set`, exactly as it
// did before the split.
// ============================================================
import type { StateCreator } from "zustand";
import type { Approval, HistoryItem } from "../../data/types";
import type { Store } from "../store";

export interface ApprovalsState {
  approvals: Approval[];
}

export interface ApprovalsActions {
  allowApproval: (id: string, scope?: string) => void;
  alwaysApproval: (id: string) => void;
  declineApproval: (id: string, reason?: string) => void;
  declineAll: () => void;
  // auto-tick (called by interval)
  tickApprovals: () => void;
}

export const createApprovalsSlice: StateCreator<
  Store,
  [],
  [],
  ApprovalsState & ApprovalsActions
> = (set, get) => {
  const resolveApproval = (
    id: string,
    decision: string,
    color: string
  ) => {
    const a = get().approvals.find((x) => x.id === id);
    const historyEntry: HistoryItem | undefined = a
      ? {
          id,
          icon:
            color === "var(--hachi-danger)" ? "x" : "circle-check",
          request: a.request,
          decision,
          when: "just now",
          ts: Date.now(),
          color,
        }
      : undefined;
    // The audit log is server truth, hydrated from GET /permissions/audit
    // (approvals.ts:loadApprovals). Never unshift a client-invented row into it —
    // gateway rows carry no timestamp and render "—", so a fabricated row with a
    // real clock reading looks MORE authoritative than the real ones. See BB-87.
    set((s) => ({
      approvals: s.approvals.filter((x) => x.id !== id),
      history: historyEntry
        ? [historyEntry, ...s.history]
        : s.history,
    }));
  };

  return {
    approvals: [],

    allowApproval: (id) => {
      resolveApproval(id, "allowed once", "var(--hachi-success)");
      get().showToast("On it — Hachi is working", "working");
    },
    alwaysApproval: (id) => {
      resolveApproval(id, "always allowed", "var(--hachi-success)");
      get().showToast("Saved — Hachi won't ask again for this", "success");
    },
    declineApproval: (id) => {
      resolveApproval(id, "declined", "var(--hachi-danger)");
      get().showToast("Stayed put — Hachi is resting", "success");
    },
    declineAll: () => {
      const ids = get().approvals.map((a) => a.id);
      ids.forEach((id) =>
        resolveApproval(id, "declined", "var(--hachi-danger)")
      );
      get().showToast("Stayed put — Hachi is resting", "success");
    },
    tickApprovals: () => {
      // Live approvals have no server-sent expiry (expiresAt=0). Only mock
      // data uses countdowns, so skip the expiry pass entirely in live mode.
      if (get().dataMode === "live") return;
      const now = Date.now();
      // expiresAt=0 is the sentinel for "no expiry" — never treat it as expired.
      const expired = get().approvals.filter(
        (a) => a.expiresAt > 0 && a.expiresAt <= now
      );
      if (expired.length) {
        set((s) => ({
          approvals: s.approvals.filter(
            (a) => a.expiresAt <= 0 || a.expiresAt > now
          ),
          history: [
            ...expired.map((a) => ({
              id: a.id,
              icon: "clock",
              request: a.request,
              decision: "timed out — Hachi stayed put",
              when: "just now",
              ts: Date.now(),
              color: "var(--hachi-muted)",
            })),
            ...s.history,
          ],
        }));
      }
    },
  };
};
