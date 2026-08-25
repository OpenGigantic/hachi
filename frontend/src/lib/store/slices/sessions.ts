// ============================================================
// T-63 — store slice: sessions (concern #13 of 22).
// Moved verbatim from frontend/src/lib/store/store.ts. Note: this slice
// imports switchSessionLive/newSessionLive from the chat domain directly
// (rather than the module-scope import store.ts used to carry) -- that
// import already participates in a tolerated store<->chat module cycle
// (chat.ts imports useStore). See BB-4xx / this task's report for the
// npm run build verification this specifically requires (tsc --noEmit does
// not see TDZ issues from module-init order; Rollup does).
// ============================================================
import type { StateCreator } from "zustand";
import { newSessionLive, switchSessionLive } from "../../data/domains/chat";
import type { Session } from "../../data/types";
import type { Store } from "../store";

export interface SessionsState {
  sessions: Session[];
}

export interface SessionsActions {
  // session switching (M8 MED-020)
  switchSession: (sessionId: string) => void;
  newSession: () => void;
}

export const createSessionsSlice: StateCreator<
  Store,
  [],
  [],
  SessionsState & SessionsActions
> = (set, get) => ({
  sessions: [],

  switchSession: (sessionId) => {
    if (get().dataMode === "live") {
      void switchSessionLive(sessionId);
      return;
    }
    // Mock mode: just flip the active flag
    set((s) => ({
      messages: [],
      sessions: s.sessions.map((sess) =>
        sess.id === sessionId
          ? { ...sess, active: true }
          : { ...sess, active: false }
      ),
    }));
  },

  newSession: () => {
    if (get().dataMode === "live") {
      newSessionLive();
      return;
    }
    // Mock mode: clear messages and deactivate all sessions
    set((s) => ({
      messages: [],
      sessions: s.sessions.map((sess) => ({ ...sess, active: false })),
    }));
  },
});
