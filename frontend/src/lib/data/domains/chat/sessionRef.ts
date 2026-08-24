// ============================================================
// Hachi Web App — chat kernel: which session are we on, and where in its
// event log.
//
// Extracted from chat.ts (T-65 / CF-16): _liveSessionId, _sseAbort,
// _streamSessionId, _cursor and _eventsUnavailable were five separate
// module-level mutables (one a `let`-bound Set) whose reset was hand-copied
// into four functions. See docs/bug-bank.md.
//
// Deliberately store-free (no useStore import) — chat.ts remains the only
// module in the store.ts <-> chat.ts <-> approvals.ts import cycle that
// references useStore at top level. See T-65 spec §12 / §10.
// ============================================================

const SESSION_STORAGE_KEY = "hachi.liveSessionId";

function persistSessionId(id: string | null): void {
  try {
    if (id) {
      sessionStorage.setItem(SESSION_STORAGE_KEY, id);
    } else {
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch {
    // private browsing / storage disabled — ignore
  }
}

function readPersistedSessionId(): string | null {
  try {
    return sessionStorage.getItem(SESSION_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export const sessionRef = {
  // Rehydrate from sessionStorage on module load so a same-tab refresh
  // reconnects to the existing session instead of always creating a new one.
  liveSessionId: readPersistedSessionId() as string | null,

  abortController: null as AbortController | null,

  // The session id that the *currently open* SSE stream belongs to.
  // Set right before opening a new stream (chat.ts's startTaskLive); NOT
  // cleared by reset() — see the long comment on the guard in chat.ts's
  // handleSseMessage for why leaving it "stuck" at the previous stream's
  // session id is the correct, protective behaviour (clearing it to null
  // would make the stale-stream guard's `!== null` check pass trivially and
  // let a race-condition straggler event from an old stream through).
  // Guards against a stale/previous stream writing into the active session's
  // store (the "Aditya Dhanraj" cross-session leak scenario).
  streamSessionId: null as string | null,

  // Highest event sequence consumed so far. Persists ACROSS turns: the session is
  // reused, and the gateway stream replays every event after this cursor, so each
  // new turn must resume from the last consumed sequence — otherwise turn 2 would
  // replay turn 1's text deltas into the fresh (empty) message.
  cursor: 0,

  // HACHI-020: session ids whose events endpoint returned 4xx (e.g. 400
  // "Unknown session_id"). We record them here and skip re-fetching on
  // subsequent calls to prevent the ~8 repeated 400s observed in the audit.
  // The Set is module-level so it persists for the page lifetime — this is
  // intentionally NOT cleared by reset(): it is a page-lifetime negative
  // cache, not per-session state (gateway session ids are UUIDs, so a 4xx'd
  // id being reused by a later session is not a realistic scenario this
  // needs to guard against).
  eventsUnavailable: new Set<string>(),

  /** Set the live session id and persist it to sessionStorage (or clear it). */
  setSession(id: string | null): void {
    this.liveSessionId = id;
    persistSessionId(id);
  },

  /**
   * Reset the in-memory session pointer and cursor. Does NOT persist —
   * callers that need the new id persisted (or cleared) call setSession()
   * right after, matching the pre-extraction sites which always paired a
   * `_liveSessionId = X` write with `persistSessionId(X)`. Does NOT touch
   * streamSessionId or eventsUnavailable — see the field comments above.
   */
  reset(): void {
    this.liveSessionId = null;
    this.cursor = 0;
  },
};
