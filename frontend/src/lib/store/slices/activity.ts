// ============================================================
// T-63 — store slice: activity / audit (concern #12 of 22).
// No dedicated actions -- all three fields are hydrate-only, populated by
// domain loaders (events/toolCalls from the activity feed, auditLog from
// GET /permissions/audit -- server truth, see BB-87/BB-97). Moved verbatim
// from frontend/src/lib/store/store.ts.
// ============================================================
import type { StateCreator } from "zustand";
import type { ActivityEvent, AuditEntry, ToolCall } from "../../data/types";
import type { Store } from "../store";

export interface ActivityState {
  events: ActivityEvent[];
  toolCalls: ToolCall[];
  auditLog: AuditEntry[];
}

export const createActivitySlice: StateCreator<Store, [], [], ActivityState> = () => ({
  events: [],
  toolCalls: [],
  auditLog: [],
});
