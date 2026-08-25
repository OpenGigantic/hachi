// ============================================================
// T-63 — store slices: shared, stateless module-scope helpers.
//
// These hold no zustand state of their own and are not owned by any single
// concern, so they are not "cross-slice coupling" in the sense the slice
// split is trying to remove (see docs/audit/2026-07-19/tasks/
// T-63-frontend-store-slices.md sec 9) -- they're a plain utility module,
// same as importing from a third-party lib. Moved verbatim from
// frontend/src/lib/store/store.ts.
// ============================================================
import type { ChatMessage } from "../../data/types";

// ---- internal id counter (module-level, not in state) ----
// A SINGLE shared counter, deliberately not per-slice: toast ids, fact ids,
// routine ids, run ids, etc. all draw from the same sequence today, so
// splitting it per-slice would risk id collisions across concerns -- a
// behaviour change the slice split must not introduce.
let _id = 100;
export function nextId(): number {
  return ++_id;
}

// ---- chat message cap (used by store.ts's hydrate() and the chat slice) ----
export const CHAT_MESSAGES_MAX = 400;

export function capMessages(messages: ChatMessage[]): ChatMessage[] {
  if (messages.length <= CHAT_MESSAGES_MAX) return messages;
  return messages.slice(-CHAT_MESSAGES_MAX);
}
