// ============================================================
// Hachi Web App — chat kernel: everything that accumulates during a stream.
//
// Extracted from chat.ts (T-65 / CF-16): nine module-level mutables
// (_textLineActive, _pendingRunLineText, _runLineRafHandle,
// _runLineLastFlushTs, _deltaBuffer, _deltaTargetId, _rafHandle,
// _lastFlushTs, _hachiMsgId) whose reset was hand-copied into four
// functions. See docs/bug-bank.md.
//
// ⚠️ This state is LIVE DURING STREAMING — mutated from inside the SSE event
// handler on every delta (chat.ts's handleSseMessage / driveRunSlice /
// appendTextDelta, which stay in chat.ts; only the state moved here). Plain
// object property access only: no getters that allocate or rebuild state.
// See T-65 spec §12.
//
// Deliberately store-free (no useStore import) — chat.ts remains the only
// module in the cycle with useStore + ../client/../sse at top level.
// ============================================================

export const streamBuffers = {
  // ── Run-slice text coalescing ─────────────────────────────────────────
  // True while the last runLines entry is an in-progress text delta; a
  // non-text event clears it so the next text event opens a fresh line.
  textLineActive: false,

  // ── Run-lines flush batching ────────────────────────────────────────────
  // Pending runLines text delta, flushed on the same throttled cadence as
  // the conversation delta buffer below.
  pendingRunLineText: "",
  runLineRafHandle: null as number | null,
  runLineLastFlushTs: 0,

  // ── Text-delta flush batching ───────────────────────────────────────────
  // Accumulates streamed text between throttled flushes to the store.
  deltaBuffer: "",
  deltaTargetId: null as number | null, // hachiMsgId the buffered text belongs to
  rafHandle: null as number | null,
  lastFlushTs: 0,

  // The hachi message id the current turn is streaming into.
  hachiMsgId: null as number | null,

  /**
   * Cancel any pending rAF/timeout flush handles and clear the pending text
   * buffers. Does NOT touch hachiMsgId, textLineActive, or the flush-throttle
   * timestamps (lastFlushTs / runLineLastFlushTs) — those persist across a
   * buffer reset by design (an immediate re-throttle after a fresh reset
   * would otherwise let a burst of frames straight through). This is the
   * exact behaviour of the pre-extraction resetDeltaBuffer()/
   * resetRunLineBuffer() pair, which chat.ts's (narrower) resetDeltaBuffer()
   * still calls via this method — it is reused by startTaskLive /
   * stopStreamingLive / abortSseLive as well as by reset() below.
   */
  resetBuffers(): void {
    if (this.rafHandle != null) {
      if (typeof cancelAnimationFrame !== "undefined") {
        cancelAnimationFrame(this.rafHandle);
      } else {
        clearTimeout(this.rafHandle as unknown as ReturnType<typeof setTimeout>);
      }
      this.rafHandle = null;
    }
    this.deltaBuffer = "";
    this.deltaTargetId = null;

    if (this.runLineRafHandle != null) {
      if (typeof cancelAnimationFrame !== "undefined") {
        cancelAnimationFrame(this.runLineRafHandle);
      } else {
        clearTimeout(this.runLineRafHandle as unknown as ReturnType<typeof setTimeout>);
      }
      this.runLineRafHandle = null;
    }
    this.pendingRunLineText = "";
  },

  /**
   * Full session-boundary reset — used by chat.ts's resetLiveSession(). A
   * pending rAF callback that fires after this would otherwise write
   * buffered text into whatever message is current, i.e. the new session's
   * — so the handles are cancelled here (via resetBuffers()), not merely
   * nulled.
   */
  reset(): void {
    this.resetBuffers();
    this.hachiMsgId = null;
    this.textLineActive = false;
  },
};
