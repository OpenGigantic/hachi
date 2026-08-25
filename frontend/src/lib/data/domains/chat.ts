// ============================================================
// Hachi Web App — chat / sessions / streaming domain
// Wires: POST /sessions (lazy, once), POST /sessions/{id}/messages,
//        GET /sessions/{id}/stream (SSE), GET /sessions?limit=10
//
// Circular-import strategy:
//   store.ts ⇄ chat.ts — BOTH directions are static imports today, one hop
//   longer since T-63 split store.ts into typed slices under store/slices/:
//     store.ts imports store/slices/chat.ts (plus slices/liveRun.ts and
//     slices/sessions.ts, for the same reason), which import the *Live
//     functions from this module; this module imports useStore from
//     ../../store/store (below) — closing the loop back to store.ts directly.
//   There is a second cycle via approvals.ts: store.ts → slices/chat.ts →
//   chat.ts → approvals.ts → store.ts (chat.ts imports ./approvals;
//   approvals.ts imports useStore from ../../store/store).
//   Both cycles resolve only because every cross-module reference happens
//   inside a function body, never at module top level. Keep it that way: a
//   top-level use of an imported binding on either side becomes a TDZ
//   ("temporal dead zone") error at runtime that `tsc --noEmit` will NOT
//   catch — only the production build or a real page load will surface it.
//   Pinned by src/lib/module-graph.test.ts.
//
//   History (superseded, kept for context — T-64 / CF-16): this comment used
//   to claim "no cycle because store.ts never statically imports chat.ts"
//   and described a store.ts → (dynamic import) → chat.ts → useStore
//   strategy. That was real once; static imports were added to store.ts
//   later (the six *Live functions above) without updating this note, so the
//   claim went checkably false. Corrected once the drift was found.
// ============================================================

import { apiGet, apiSend, USE_MOCKS, isNetworkError, ApiError } from "../client";
import { streamSSE } from "../sse";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import { approveByRequestId, declineByRequestId, loadApprovals } from "./approvals";
import { loadDailySpend } from "./spend";
import { ensureNotificationPermission, notifyApprovalPending } from "../../notifications";
import type { Session, ActionCard } from "../types";
import { costMeter } from "./chat/costMeter";
import { sessionRef } from "./chat/sessionRef";
import { streamBuffers } from "./chat/streamBuffers";

// ============================================================
// 1. Module-level kernel state (T-65 / CF-16)
//    The live session id + SSE cursor (sessionRef), the streaming delta
//    buffers + rAF flush handles (streamBuffers) and the per-turn cost
//    baselines (costMeter) live in frontend/src/lib/data/domains/chat/ —
//    three cohesive, store-free objects, each with its own reset(), plus
//    one resetLiveSession() below that all four session-boundary reset
//    sites call. See docs/bug-bank.md.
// ============================================================

// ── Per-page-load nonce (BB-12) ───────────────────────────────────────────────
// store.ts's _id counter resets to 100 on every page load. The gateway session
// UUID is preserved across refreshes (sessionStorage), so `turn-102` from a
// prior load would match the idempotency cache and replay the old response
// instantly. A nonce generated at module-load time breaks that collision while
// preserving retry behaviour (retryLastMessage reuses the same hachiMsgId and
// therefore the same nonce+id pair → same key → cache hit, as intended).
const _PAGE_NONCE: string =
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID().slice(0, 8)
    : Math.random().toString(36).slice(2, 10);

/** Build the per-turn idempotency key. Exported for unit testing only. */
export function buildTurnKey(nonce: string, hachiMsgId: number): string {
  return `turn-${nonce}-${hachiMsgId}`;
}

// ── Run-slice text coalescing ─────────────────────────────────────────────────
// We coalesce consecutive text.stream.delta events into a single trailing line
// rather than pushing one line per token.  streamBuffers.textLineActive is true
// while the last runLines entry is an in-progress text delta; when a non-text
// event arrives we clear the flag so the next text event opens a fresh line.

// ── Run-lines flush batching ──────────────────────────────────────────────────
// During conversation streaming (streamBuffers.deltaTargetId is set),
// text.stream.delta events ALSO drive driveRunSlice which calls
// store.hydrate({ runLines }) synchronously on every token — bypassing the
// 48ms flush throttle and causing redundant re-renders in any subscriber to
// runLines.  We buffer the pending runLines text delta here and flush it on
// the same cadence as flushDeltaBuffer. Non-text run events (tool.call.*,
// thinking.step, etc.) still write synchronously since they are infrequent
// and carry structural information.

function flushRunLineBuffer(): void {
  streamBuffers.runLineRafHandle = null;
  streamBuffers.runLineLastFlushTs = nowMs();
  if (!streamBuffers.pendingRunLineText) return;
  const text = streamBuffers.pendingRunLineText;
  streamBuffers.pendingRunLineText = "";
  const store = useStore.getState();
  if (streamBuffers.textLineActive && store.runLines.length > 0) {
    const lines = [...store.runLines];
    const last = lines[lines.length - 1];
    lines[lines.length - 1] = { ...last, text: last.text + text };
    store.hydrate({ runLines: lines.length > RUN_LINES_MAX ? lines.slice(-RUN_LINES_MAX) : lines });
  } else {
    streamBuffers.textLineActive = true;
    const next = [...store.runLines, { text }];
    store.hydrate({ runLines: next.length > RUN_LINES_MAX ? next.slice(-RUN_LINES_MAX) : next });
  }
}

function scheduleRunLineFlush(): void {
  if (streamBuffers.runLineRafHandle != null) return;
  const elapsed = nowMs() - streamBuffers.runLineLastFlushTs;
  if (elapsed >= FLUSH_MIN_INTERVAL_MS && typeof requestAnimationFrame !== "undefined") {
    streamBuffers.runLineRafHandle = requestAnimationFrame(flushRunLineBuffer);
  } else {
    const wait = Math.max(0, FLUSH_MIN_INTERVAL_MS - elapsed);
    streamBuffers.runLineRafHandle = setTimeout(flushRunLineBuffer, wait) as unknown as number;
  }
}

function flushRunLineBufferSync(): void {
  if (streamBuffers.runLineRafHandle != null) {
    if (typeof cancelAnimationFrame !== "undefined") {
      cancelAnimationFrame(streamBuffers.runLineRafHandle);
    } else {
      clearTimeout(streamBuffers.runLineRafHandle as unknown as ReturnType<typeof setTimeout>);
    }
    streamBuffers.runLineRafHandle = null;
  }
  flushRunLineBuffer();
}

// ── Run-buffer hard cap ───────────────────────────────────────────────────────
// Defence-in-depth: pathological runs can't grow runLines without bound.
const RUN_LINES_MAX = 400;

// ── Text-delta flush batching ─────────────────────────────────────────────────
// Instead of writing to the store (and triggering a re-render) on every token,
// we accumulate deltas in a module-level buffer and flush on a throttled cadence.
//
// IMPORTANT (perf): re-rendering the streaming message re-lays-out its ENTIRE
// growing text on every flush, so streaming cost is ~O(size) per flush and
// O(size²) over the whole response. Flushing once per animation frame (~60/s)
// makes that quadratic term saturate the main thread for long responses
// (RESULT_CODE_HUNG). We therefore throttle flushes to FLUSH_MIN_INTERVAL_MS
// (~20/s), which cuts the re-layout count ~3× and guarantees the thread yields
// between flushes. (The conversation view further caps cost by rendering the
// in-progress message outside the virtualizer — see conversation.tsx.)
const FLUSH_MIN_INTERVAL_MS = 48;

function nowMs(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

/** Flush the pending delta buffer to the store in one update. */
function flushDeltaBuffer(): void {
  streamBuffers.rafHandle = null;
  streamBuffers.lastFlushTs = nowMs();
  if (!streamBuffers.deltaBuffer || streamBuffers.deltaTargetId == null) return;
  const text = streamBuffers.deltaBuffer;
  const targetId = streamBuffers.deltaTargetId;
  streamBuffers.deltaBuffer = "";
  // Perform the actual store update (same logic as the old per-token appendTextDelta)
  const store = useStore.getState();
  const msgs = store.messages;
  const msg = msgs.find((m) => m.id === targetId);
  if (!msg) return;
  store.hydrate({
    messages: msgs.map((m) =>
      m.id === targetId ? { ...m, text: m.text + text } : m
    ),
  });
}

/**
 * Schedule a throttled flush. If the last flush was longer ago than
 * FLUSH_MIN_INTERVAL_MS, flush on the next animation frame; otherwise defer with
 * a timeout so we never exceed ~20 flushes/second during fast streaming.
 */
function scheduleFlush(): void {
  if (streamBuffers.rafHandle != null) return;
  const elapsed = nowMs() - streamBuffers.lastFlushTs;
  if (elapsed >= FLUSH_MIN_INTERVAL_MS && typeof requestAnimationFrame !== "undefined") {
    streamBuffers.rafHandle = requestAnimationFrame(flushDeltaBuffer);
  } else {
    const wait = Math.max(0, FLUSH_MIN_INTERVAL_MS - elapsed);
    streamBuffers.rafHandle = setTimeout(flushDeltaBuffer, wait) as unknown as number;
  }
}

/** Cancel any pending RAF and synchronously flush remaining buffered text. */
function flushDeltaBufferSync(): void {
  if (streamBuffers.rafHandle != null) {
    if (typeof cancelAnimationFrame !== "undefined") {
      cancelAnimationFrame(streamBuffers.rafHandle);
    } else {
      clearTimeout(streamBuffers.rafHandle as unknown as ReturnType<typeof setTimeout>);
    }
    streamBuffers.rafHandle = null;
  }
  flushDeltaBuffer();
  // Also flush any pending run-line text so the Work view is up to date.
  flushRunLineBufferSync();
}

/**
 * Reset the delta buffer between turns. Narrower than streamBuffers.reset():
 * does NOT touch hachiMsgId/textLineActive — see streamBuffers.ts's
 * resetBuffers() doc comment. Kept as its own chat.ts function (rather than
 * inlining streamBuffers.resetBuffers() at each call site) so
 * startTaskLive/stopStreamingLive/abortSseLive's existing call sites are
 * untouched.
 */
function resetDeltaBuffer(): void {
  streamBuffers.resetBuffers();
}

// ============================================================
// 2. Raw gateway shapes
// ============================================================

interface GatewaySessionCreateResponse {
  accepted: boolean;
  session_id: string;
  protocol_version: string;
  server_capabilities: string[];
}

// GET /sessions returns an array directly (per control_plane.recent_sessions)
// which returns list[dict] from session_store.get_recent_sessions:
//   { id, started_at, ended_at, model, total_turns, title,
//     total_tokens, total_cost_usd }
interface GatewaySession {
  id?: string;
  started_at?: string;
  ended_at?: string | null;
  model?: string | null;
  total_turns?: number | null;
  title?: string | null;
  total_tokens?: number | null;
  total_cost_usd?: number | null;
}

// SSE event envelope — what the gateway puts in the "data:" field of each frame.
// Shape: { sequence, event_type, payload, time }
interface GatewayEventEnvelope {
  sequence?: number;
  event_type?: string;
  payload?: Record<string, unknown>;
  time?: string;
}

// ============================================================
// 3. Adapters
// ============================================================

function adaptSession(raw: GatewaySession, idx: number): Session {
  // "active" = ended_at is null/missing (open session)
  const active = !raw.ended_at;
  // Format date from ISO string
  let date = "unknown";
  if (raw.started_at) {
    try {
      date = new Date(raw.started_at).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      });
    } catch {
      date = raw.started_at;
    }
  }
  return {
    // Use the string id directly — Session.id is typed as string
    id: raw.id ?? String(idx),
    title: raw.title ?? "Unnamed session",
    turns: raw.total_turns ?? 0,
    model: raw.model ?? "unknown",
    date,
    active,
  };
}

// ============================================================
// 4. Session lifecycle helpers
// ============================================================

/** Create a new gateway session and cache the id. Throws on failure. */
async function ensureSession(): Promise<string> {
  if (sessionRef.liveSessionId) return sessionRef.liveSessionId;
  const res = await apiSend<GatewaySessionCreateResponse>("POST", "/sessions", {
    client_id: "web",
    surface: "web",
    protocol_version: "1.0",
    capabilities: [],
  });
  sessionRef.setSession(res.session_id); // FIX 2: persist for same-tab refresh
  sessionRef.cursor = 0; // fresh session → fresh event log
  // Return the id we just set directly rather than re-reading
  // sessionRef.liveSessionId: it is a mutable object property, so TS can't
  // narrow it to non-null through the setSession() call the way it could
  // narrow a plain `let` — the value is identical either way.
  return res.session_id;
}

/** Cancel the current SSE stream (e.g. on stopStreaming). */
function abortSse(): void {
  if (sessionRef.abortController) {
    sessionRef.abortController.abort();
    sessionRef.abortController = null;
  }
}

/**
 * Reset the full live-session kernel — sessionRef, streamBuffers and
 * costMeter — and point sessionRef at `nextSessionId` (persisting it, or
 * clearing sessionStorage for null). This is the single reset the four
 * former hand-duplicated blocks (loadConversationHistory's "no sessions
 * left" guard, switchSessionLive, deleteSessionLive, newSessionLive) all
 * call now (T-65 / CF-16). Callers keep their own `useStore.getState().
 * hydrate({ messages: [], ... })` afterwards — that is UI state, not kernel
 * state, and switchSessionLive deliberately does not blank messages here
 * (see its call site).
 */
export function resetLiveSession(nextSessionId: string | null): void {
  abortSse();
  streamBuffers.reset();
  costMeter.reset();
  sessionRef.reset();
  sessionRef.setSession(nextSessionId);
}

/**
 * Read-only snapshot of the chat kernel (sessionRef + streamBuffers +
 * costMeter) for debugging/tests. Deliberately does not expose the mutable
 * references themselves (e.g. sessionRef.abortController, .eventsUnavailable)
 * — this is a frozen plain object, not a view onto live state.
 */
export function getKernelSnapshot(): Readonly<{
  liveSessionId: string | null;
  streamSessionId: string | null;
  cursor: number;
  hachiMsgId: number | null;
  textLineActive: boolean;
  deltaBuffer: string;
  deltaTargetId: number | null;
  pendingRunLineText: string;
  costCumAtTurnStart: number;
  spentAtTurnStart: number;
  lastKnownCumCost: number;
}> {
  return Object.freeze({
    liveSessionId: sessionRef.liveSessionId,
    streamSessionId: sessionRef.streamSessionId,
    cursor: sessionRef.cursor,
    hachiMsgId: streamBuffers.hachiMsgId,
    textLineActive: streamBuffers.textLineActive,
    deltaBuffer: streamBuffers.deltaBuffer,
    deltaTargetId: streamBuffers.deltaTargetId,
    pendingRunLineText: streamBuffers.pendingRunLineText,
    costCumAtTurnStart: costMeter.costCumAtTurnStart,
    spentAtTurnStart: costMeter.spentAtTurnStart,
    lastKnownCumCost: costMeter.lastKnownCumCost,
  });
}

// ============================================================
// 5. Internal store mutation helpers
//    (used by the live functions below — avoids reaching into
//    private store internals by calling public actions or
//    hydrate-compatible patches)
// ============================================================

/**
 * Enqueue a text delta for the current hachi message.
 * The actual store update is batched and happens on the next animation frame
 * (or at most ~40 ms later), not on every token.
 */
function appendTextDelta(delta: string): void {
  const id = streamBuffers.hachiMsgId;
  if (id == null) return;
  // If the target changed mid-stream (shouldn't happen, but guard anyway),
  // flush the old buffer first so it lands in the right message.
  if (streamBuffers.deltaTargetId !== null && streamBuffers.deltaTargetId !== id) {
    flushDeltaBufferSync();
  }
  streamBuffers.deltaTargetId = id;
  streamBuffers.deltaBuffer += delta;
  scheduleFlush();
}

/** Upsert an action card on the current hachi message. */
function upsertCard(card: ActionCard): void {
  const store = useStore.getState();
  const msgs = store.messages;
  const id = streamBuffers.hachiMsgId;
  if (id == null) return;
  store.hydrate({
    messages: msgs.map((m) => {
      if (m.id !== id) return m;
      const exists = m.cards.some((c) => c.key === card.key);
      return {
        ...m,
        cards: exists
          ? m.cards.map((c) => (c.key === card.key ? { ...c, ...card } : c))
          : [...m.cards, card],
      };
    }),
  });
}

// ============================================================
// 6. SSE event handler — run-slice driver
// ============================================================

/**
 * Update the run slice from a single SSE event.
 * Only called in live mode (guarded by caller).
 */
function driveRunSlice(eventType: string, payload: Record<string, unknown>): void {
  const store = useStore.getState();

  switch (eventType) {
    case "text.stream.delta": {
      const text = typeof payload.text === "string" ? payload.text : "";
      if (!text) break;
      // When a conversation delta target is active, the text is already being
      // rendered via the throttled appendTextDelta/flushDeltaBuffer path.
      // Coalesce the runLines text update onto the same ~48ms cadence so we
      // don't call store.hydrate({ runLines }) synchronously on every token
      // (which would bypass the flush throttle and re-render all runLines
      // subscribers at full token rate — ~20-50/s).
      if (streamBuffers.deltaTargetId != null) {
        streamBuffers.pendingRunLineText += text;
        scheduleRunLineFlush();
      } else {
        // No active conversation stream (run-only view): write synchronously
        // so the Work/Run view sees text immediately without a delta target.
        if (streamBuffers.textLineActive && store.runLines.length > 0) {
          const lines = [...store.runLines];
          const last = lines[lines.length - 1];
          lines[lines.length - 1] = { ...last, text: last.text + text };
          store.hydrate({ runLines: lines.length > RUN_LINES_MAX ? lines.slice(-RUN_LINES_MAX) : lines });
        } else {
          streamBuffers.textLineActive = true;
          const next = [...store.runLines, { text }];
          store.hydrate({ runLines: next.length > RUN_LINES_MAX ? next.slice(-RUN_LINES_MAX) : next });
        }
      }
      break;
    }

    case "thinking.step": {
      streamBuffers.textLineActive = false;
      const prev = store.runLines;
      if (prev.length > 0 && prev[prev.length - 1].text === "· thinking…") break;
      const next = [...prev, { text: "· thinking…", tone: "muted" }];
      store.hydrate({ runLines: next.length > RUN_LINES_MAX ? next.slice(-RUN_LINES_MAX) : next });
      break;
    }

    case "tool.call.started": {
      streamBuffers.textLineActive = false;
      const name = typeof payload.name === "string" ? payload.name : "tool";
      const next = [...store.runLines, { text: "▸ " + name, tone: "muted" }];
      store.hydrate({ runLines: next.length > RUN_LINES_MAX ? next.slice(-RUN_LINES_MAX) : next });
      break;
    }

    case "tool.call.completed": {
      streamBuffers.textLineActive = false;
      const name = typeof payload.name === "string" ? payload.name : "tool";
      const next = [...store.runLines, { text: "✓ " + name, tone: "success" }];
      store.hydrate({ runLines: next.length > RUN_LINES_MAX ? next.slice(-RUN_LINES_MAX) : next });
      break;
    }

    case "subagent.started": {
      streamBuffers.textLineActive = false;
      const role = typeof payload.role === "string" ? payload.role : "agent";
      const task = typeof payload.task === "string" ? payload.task : "";
      const memberId = role + ":" + task.slice(0, 24);
      // Deduplicate by id
      const existing = store.runPack.find((p) => p.id === memberId);
      const newPack = existing
        ? store.runPack
        : [
            ...store.runPack,
            {
              id: memberId,
              name: role,
              role,
              task,
              status: "working" as const,
              tokens: 0,
            },
          ];
      const nextLines = [
        ...store.runLines,
        { text: "→ delegated to " + role + ": " + task, tone: "muted" },
      ];
      store.hydrate({
        runPack: newPack,
        runLines: nextLines.length > RUN_LINES_MAX ? nextLines.slice(-RUN_LINES_MAX) : nextLines,
      });
      break;
    }

    case "subagent.completed": {
      streamBuffers.textLineActive = false;
      const role = typeof payload.role === "string" ? payload.role : "agent";
      const status = typeof payload.status === "string" ? payload.status : "done";
      const tone = status === "error" || status === "failed" ? "danger" : "success";
      const updatedPack = store.runPack.map((p) =>
        p.role === role || p.id.startsWith(role + ":")
          ? { ...p, status: "done" as const }
          : p
      );
      const nextLines = [
        ...store.runLines,
        { text: "✓ " + role + " " + status, tone },
      ];
      store.hydrate({
        runPack: updatedPack,
        runLines: nextLines.length > RUN_LINES_MAX ? nextLines.slice(-RUN_LINES_MAX) : nextLines,
      });
      break;
    }

    case "session.chat.completed": {
      streamBuffers.textLineActive = false;
      // Flush any pending buffered run-line text so it appears before "✓ done"
      flushRunLineBufferSync();
      const doneLines = [...store.runLines, { text: "✓ done", tone: "success" }];
      const donePack = store.runPack.map((p) => ({ ...p, status: "done" as const }));
      store.hydrate({
        runDone: true,
        runStreaming: false,
        runLines: doneLines.length > RUN_LINES_MAX ? doneLines.slice(-RUN_LINES_MAX) : doneLines,
        runPack: donePack,
      });
      break;
    }

    default:
      break;
  }
}

// ============================================================
// 7. SSE event handler — conversation + run slice
// ============================================================

// Exported for testing only (approvals.test.ts) — no behaviour change. This is
// the seam the "permission.requested" field-mapping regression test exercises
// directly; the file already exports several other internals for the same reason
// (buildTurnKey, reconstructMessagesFromEvents, extractInjectedAttachments).
export function handleSseMessage(eventType: string, payload: Record<string, unknown>): void {
  // Guard: drop events from a stale SSE stream (e.g. user switched sessions while
  // a turn was in-flight).  sessionRef.streamSessionId is the gateway UUID this
  // stream was opened for; sessionRef.liveSessionId is the currently active
  // gateway UUID.  If they diverge the old stream's events must not pollute the
  // new session's view.
  //
  // NOTE: do NOT filter on payload.session_id — the backend stamps events with the
  // *SQLite* session ID (AgentSession._session_id from store.start_session()), which
  // is a different ID from the gateway UUID stored in sessionRef.liveSessionId.
  // Comparing them would always mismatch and silently drop every streaming event.
  if (sessionRef.streamSessionId !== null && sessionRef.streamSessionId !== sessionRef.liveSessionId) {
    return;
  }

  const store = useStore.getState();
  const isLive = store.dataMode === "live";

  switch (eventType) {
    case "text.stream.delta": {
      const text = typeof payload.text === "string" ? payload.text : "";
      if (text) appendTextDelta(text);
      break;
    }

    case "tool.call.started": {
      const name = typeof payload.name === "string" ? payload.name : "tool";
      const key = "tool-" + name;
      upsertCard({
        key,
        icon: "play",
        title: name,
        status: "working",
        cost: 0,
        tool: name,
        detail: "",
        expanded: false,
      });
      break;
    }

    case "tool.call.completed": {
      const name = typeof payload.name === "string" ? payload.name : "tool";
      const key = "tool-" + name;
      const preview =
        typeof payload.result_preview === "string" ? payload.result_preview : "";
      upsertCard({
        key,
        icon: "circle-check",
        title: name,
        status: "done",
        cost: 0,
        tool: name,
        detail: preview,
        expanded: false,
      });
      break;
    }

    case "permission.requested": {
      // The gateway's real wire fields (core/approvals.py, core/permissions.py —
      // all three emitters agree): request_id / operation / tool / target / reason /
      // policy_summary. There is no "request" / "action" / "why" key on the wire —
      // reading those (as this used to) meant every field silently fell back to its
      // default, and request_id was discarded outright. See BB-105.
      const requestId = typeof payload.request_id === "string" ? payload.request_id : "";
      const operation = typeof payload.operation === "string" ? payload.operation : "";
      const tool = typeof payload.tool === "string" ? payload.tool : "";
      const target = typeof payload.target === "string" ? payload.target : "";
      const reason = typeof payload.reason === "string" ? payload.reason : "";
      const request = tool || "Permission requested";
      // Composed the same way approvals.ts's adaptApproval() composes `action`
      // (`${tool || operation} → ${target}`) so the in-context leash and the
      // /approvals list agree on what they show for the same event.
      const action = `${tool || operation} → ${target}`;
      const why = reason;
      const msgId = streamBuffers.hachiMsgId ?? 0;
      store.hydrate({
        inContext: { msgId, requestId, request, action, why },
      });
      // M10: notify the user if they've switched to another tab
      notifyApprovalPending(request);
      // Also refresh the formal approvals list from the authoritative registry
      // (GET /approvals) so the Leash screen shows this request in real time with
      // its REAL request_id — making it approvable there, not just inline. The
      // backend registers the pending request as it blocks on the approval
      // handler, so by the time this SSE event reaches the browser the registry
      // entry exists.
      void loadApprovals();
      if (isLive) {
        // Also surface on the run view
        store.hydrate({ runLeash: { requestId, request, action, why }, runStreaming: false });
        streamBuffers.textLineActive = false;
      }
      break;
    }

    case "permission.decided": {
      // A permission was approved/denied. The backend emits "permission.decided"
      // (there is no "permission.resolved" event). Refresh the approvals list so
      // the now-resolved request drops off the Leash screen.
      void loadApprovals();
      break;
    }

    case "usage.updated": {
      const totalTokens =
        typeof payload.total_tokens === "number" ? payload.total_tokens : null;
      // estimated_cost_usd is CUMULATIVE (session running total, not a delta).
      // Confirmed: orchestrator.py lines 720-756 comment "cumulative counters"
      // and CostTracker.snapshot() in core/cost.py accumulates via +=.
      // We convert to a per-turn delta using baselines captured at stream start.
      const cumCost =
        typeof payload.estimated_cost_usd === "number"
          ? payload.estimated_cost_usd
          : null;
      if (cumCost != null) {
        costMeter.lastKnownCumCost = cumCost;
        const turnDelta = Math.max(0, cumCost - costMeter.costCumAtTurnStart);
        store.hydrate({
          taskSpent: Math.round(turnDelta * 1000) / 1000,
          spent:     Math.round((costMeter.spentAtTurnStart + turnDelta) * 1000) / 1000,
          ...(isLive ? { runSpent: Math.round(turnDelta * 1000) / 1000 } : {}),
        });
      }
      // FIX 4: also consume model from usage.updated as a fallback
      // (contract C-MODEL — backend may send it here instead of chat.completed)
      const usageModel =
        typeof payload.model === "string" && payload.model ? payload.model : null;
      if (usageModel) {
        store.hydrate({ lastUsedModel: usageModel });
      }
      // totalTokens surfaced but no dedicated UI field currently
      void totalTokens;
      break;
    }

    case "session.chat.completed": {
      // Flush any remaining buffered text BEFORE ending streaming state, so
      // the final message is complete when the UI transitions out of streaming.
      flushDeltaBufferSync();
      // FIX 4: capture the model id from this event (contract C-MODEL).
      // The field may be absent; default to null if missing (defensive).
      const completedModel =
        typeof payload.model === "string" && payload.model
          ? payload.model
          : null;
      if (completedModel) {
        store.hydrate({ lastUsedModel: completedModel });
      }
      // Turn is done — end streaming state
      store.hydrate({ streaming: false });
      // Reconcile today's spend with the durable backend ledger (GET /spend/daily)
      // so the live SSE-accumulated value can never drift from backend truth.
      void loadDailySpend();
      // FIX 3: refresh the session list so the auto-generated title shows up without a
      // manual navigation. The titler runs asynchronously AFTER the turn completes, so
      // re-fetch once now and again shortly after to catch the title once it lands.
      void loadSessions();
      setTimeout(() => {
        void loadSessions();
      }, 2500);
      streamBuffers.hachiMsgId = null;
      // Clear the run buffers after a short delay (so the user sees "✓ done")
      // but only if a newer run hasn't started in the meantime.
      const clearedHachiMsgId = streamBuffers.hachiMsgId; // already null after line above
      void clearedHachiMsgId; // suppress unused-var lint
      const sentinelTurnId = costMeter.lastKnownCumCost; // use cost as a stable sentinel
      setTimeout(() => {
        // If the cost baseline hasn't changed, no new turn started — safe to clear.
        if (costMeter.lastKnownCumCost === sentinelTurnId) {
          useStore.getState().hydrate({ runLines: [], runPack: [] });
        }
      }, 2000);
      break;
    }

    default:
      // Unknown event types are silently ignored
      break;
  }

  // Drive the run slice from every event in live mode
  if (isLive) {
    driveRunSlice(eventType, payload);
  }
}

// ============================================================
// 7. Live action: start a task (creates/reuses session, opens SSE,
//    fires POST message)
// ============================================================

/**
 * Live-mode implementation of startTask / sendMessage.
 *
 * The caller (store.ts) has already:
 *   - appended the "you" message
 *   - appended an empty "hachi" message
 *   - set streaming = true
 *
 * We receive the hachiMsgId so we can append text deltas to it.
 */
export async function startTaskLive(
  text: string,
  hachiMsgId: number,
  attachmentIds: string[] = []
): Promise<void> {
  // Cancel any in-flight stream first
  abortSse();

  // Reset the delta buffer for this new turn so stale text from a prior
  // turn can't bleed into the new hachi message.
  resetDeltaBuffer();

  // M10: lazily request notification permission on first task (fire-and-forget)
  void ensureNotificationPermission();

  streamBuffers.hachiMsgId = hachiMsgId;

  // Initialize the run slice for this turn (live mode only).
  // Also clears runLines/runPack so the previous turn's entries don't persist.
  {
    const store = useStore.getState();
    if (store.dataMode === "live") {
      streamBuffers.textLineActive = false;
      store.hydrate({
        runView: {
          id: Date.now(),
          title: text.slice(0, 60) || "Live run",
          status: "working",
          progress: 0,
          spent: 0,
          budget: store.budget ?? 1,
        },
        runLines: [{ text: "hachi ▸ working on your task", tone: "muted" }],
        runPack: [],
        runSteps: [],
        runDone: false,
        runLeash: null,
        runStreaming: true,
        runSpent: 0,
      });
    }
  }

  let sessionId: string;
  try {
    sessionId = await ensureSession();
  } catch {
    useStore.getState().setReconnecting(true);
    useStore.getState().hydrate({ streaming: false });
    return;
  }

  // FIX 2: bind this SSE stream to its session so handleSseMessage can drop events
  // that arrive after the user switched sessions (this is what actually prevents the
  // cross-session/cross-section leak — the guard at the top of handleSseMessage reads it).
  sessionRef.streamSessionId = sessionId;

  // Resume from the sessionRef cursor so a reused session only streams the
  // NEW turn's events (see sessionRef.ts's cursor docs), not the whole session history.
  const startAfter = sessionRef.cursor;

  // Snapshot cost baselines so the usage.updated handler can convert the
  // gateway's cumulative estimated_cost_usd into a per-turn delta.
  costMeter.costCumAtTurnStart = costMeter.lastKnownCumCost;
  costMeter.spentAtTurnStart = useStore.getState().spent;

  // Open SSE stream first, THEN fire the message POST in the background.
  // This ensures we don't miss early events from the turn.
  sessionRef.abortController = new AbortController();
  const signal = sessionRef.abortController.signal;

  // Fire the POST in the background — we don't await the full response
  // because the turn streams via SSE. The POST response is the blocking
  // fallback; we rely on SSE events for incremental updates.
  const postPromise = apiSend<{ session_id: string; response: string }>(
    "POST",
    `/sessions/${sessionId}/messages`,
    // Ingress idempotency (AUDIT P0-3): key the turn on the (stable) assistant-bubble id.
    // retryLastMessage reuses the same hachiMsgId, so a retry or double-submit replays the
    // prior turn server-side instead of double-spending; distinct turns get distinct keys.
    { prompt: text, attachment_ids: attachmentIds, idempotency_key: buildTurnKey(_PAGE_NONCE, hachiMsgId) },
    signal
  ).then((res) => {
    // If SSE didn't deliver a completed event (e.g. gateway closed stream early),
    // use the blocking response as the final text.
    const store = useStore.getState();
    if (store.streaming && streamBuffers.hachiMsgId === hachiMsgId) {
      // SSE stream is still open but we already have the full reply —
      // fill it in and end streaming.
      const msgs = store.messages;
      const msg = msgs.find((m) => m.id === hachiMsgId);
      if (msg && msg.text === "" && res.response) {
        store.hydrate({
          messages: msgs.map((m) =>
            m.id === hachiMsgId ? { ...m, text: res.response } : m
          ),
          streaming: false,
        });
        streamBuffers.hachiMsgId = null;
      }
    }
  }).catch(async (err: unknown) => {
    // POST failed — surface the error to the user and clean up.

    // FIX 2: Handle session expiry / unknown session robustly.
    // "session_expired" → create a new session and show a friendly notice.
    // Any "Unknown session" / corrupted-id case → silently reset and create new.
    const isSessionExpired =
      err instanceof ApiError &&
      (err.code === "session_expired" || err.message === "session_expired");
    const isUnknownSession =
      err instanceof ApiError &&
      (err.code === "unknown_session" ||
        err.message?.toLowerCase().includes("unknown session"));

    if (isSessionExpired || isUnknownSession) {
      // Drop stale session id so next ensureSession() mints a fresh one
      sessionRef.setSession(null);
      sessionRef.cursor = 0;

      // Update sessions list so the stale active marker is cleared
      const store = useStore.getState();
      store.hydrate({
        sessions: store.sessions.map((s) => ({ ...s, active: false })),
      });

      if (isSessionExpired) {
        // Bug 5: auto-retry — create a fresh session and resend the prompt so
        // the user doesn't have to re-type their message after a gateway restart.
        // A new idempotency key is used (the old key was scoped to the dead session).
        // The daily cost cap still applies server-side (reads from the durable ledger,
        // not the per-session tracker), so budget-stopped users cannot bypass it.
        store.showToast("Session refreshed — resending your message…", "info");
        try {
          const newSessionId = await ensureSession();
          const retryKey = buildTurnKey(_PAGE_NONCE + "-r", hachiMsgId);
          await apiSend<{ session_id: string; response: string }>(
            "POST",
            `/sessions/${newSessionId}/messages`,
            { prompt: text, attachment_ids: attachmentIds, idempotency_key: retryKey },
          );
        } catch {
          store.hydrate({ streaming: false });
          store.showToast("Session expired — please send your message again.", "blocked");
        }
      } else {
        // Gateway restarted and lost the in-memory session — user must resend.
        store.showToast("Connection reset — please send your message again.", "info");
      }
      return;
    }

    const store = useStore.getState();
    if (store.streaming && streamBuffers.hachiMsgId === hachiMsgId) {
      // HACHI-010: abort the SSE reader so it doesn't linger until idle timeout.
      abortSse();

      // HACHI-002: set the error sentinel on the assistant bubble so the Retry
      // button in conversation.tsx renders. The store's outer .catch is dead code
      // (startTaskLive resolves even on POST failure), so we must set it here.
      const msgs = store.messages;
      store.hydrate({
        streaming: false,
        messages: msgs.map((m) =>
          m.id === hachiMsgId && m.text === ""
            ? { ...m, text: "Something went wrong — please try again." }
            : m
        ),
      });
      streamBuffers.hachiMsgId = null;

      // HACHI-003: only flag reconnecting for genuine connectivity loss
      // (fetch rejected / proxy-down 502-504), not ordinary gateway 5xx.
      if (isNetworkError(err)) {
        store.setReconnecting(true);
      } else {
        // HACHI-012: surface the gateway error message via toast so the user
        // gets actionable feedback instead of a silent failure.
        const msg =
          err instanceof ApiError && err.message
            ? err.message
            : "Send failed — please try again";
        store.showToast(msg, "blocked");
      }
    }
  });

  // Open the SSE stream concurrently
  try {
    await streamSSE(
      `/sessions/${sessionId}/stream`,
      {
        onMessage: (msg) => {
          // Advance the persistent cursor so the NEXT turn resumes past here.
          if (msg.id) sessionRef.cursor = Math.max(sessionRef.cursor, parseInt(msg.id, 10) || sessionRef.cursor);

          // Parse the envelope from the data field
          let envelope: GatewayEventEnvelope = {};
          try {
            envelope = JSON.parse(msg.data) as GatewayEventEnvelope;
          } catch {
            return;
          }

          const eventType = envelope.event_type ?? msg.event;
          const payload = envelope.payload ?? {};
          handleSseMessage(eventType, payload);
        },
        onError: (err) => {
          // Only treat genuine network failures as "reconnecting".
          // HTTP-level errors (already decoded as ApiError with status>0) are
          // surfaced as an error state, not a reconnect spinner.
          if (isNetworkError(err)) {
            useStore.getState().setReconnecting(true);
          }
          // Flush any remaining buffered delta on stream error
          flushDeltaBufferSync();
        },
        onClose: () => {
          // Flush remaining buffered text before marking streaming done.
          flushDeltaBufferSync();
          // Stream closed — if still "streaming", end it
          const store = useStore.getState();
          if (store.streaming && streamBuffers.hachiMsgId === hachiMsgId) {
            store.hydrate({ streaming: false });
            streamBuffers.hachiMsgId = null;
          }
        },
      },
      { params: { after: startAfter }, signal }
    );
  } catch {
    // streamSSE never throws, but guard anyway
    useStore.getState().setReconnecting(true);
  }

  // Wait for POST to finish (it may already be resolved)
  await postPromise.catch(() => void 0);
}

// ============================================================
// 8. Live action: stop streaming
// ============================================================

/** Live-mode implementation of stopStreaming. */
export function stopStreamingLive(): void {
  // Flush any pending delta before aborting so the partial text is preserved
  flushDeltaBufferSync();
  // P1-4: fire-and-forget interrupt so the server-side worker thread also stops,
  // not just the SSE coroutine. abortSse() closes the stream first so the server
  // can see is_disconnected(); the explicit interrupt guarantees the worker stops
  // even on HTTP/1.1 where disconnect detection may lag.
  if (sessionRef.liveSessionId) {
    apiSend<{ interrupted: boolean }>("POST", `/sessions/${sessionRef.liveSessionId}/interrupt`).catch(
      () => void 0 // fire-and-forget — never block the UI
    );
  }
  abortSse();
  streamBuffers.hachiMsgId = null;
  resetDeltaBuffer();
}

/**
 * Expose the SSE abort so the conversation component can call it on unmount.
 * This is the same as stopStreamingLive but does not reset streaming state
 * (the component is unmounting, so state updates are moot).
 */
export function abortSseLive(): void {
  flushDeltaBufferSync();
  abortSse();
  resetDeltaBuffer();
}

// ============================================================
// 9. Live action: icAllow / icDeny
//    For live mode, these simply clear the in-context leash
//    and signal the gateway (fire-and-forget approval).
// ============================================================

export async function allowLeashLive(): Promise<void> {
  const store = useStore.getState();
  const ic = store.inContext;
  if (!ic) return;
  // Clear the leash immediately (optimistic UI update)
  store.hydrate({ inContext: null });
  // Resolve by the gateway's own request_id (see approvals.ts). Best-effort:
  // errors — including an unmapped/unknown request_id — are soft here, the
  // SSE stream continues regardless. approveByRequestId itself never throws
  // for an unknown id (it returns false); the try/catch below only guards
  // against a genuine network/POST failure.
  try {
    await approveByRequestId(ic.requestId);
  } catch {
    // Gateway POST failed — SSE stream continues, decision is best-effort
  }
}

export async function denyLeashLive(): Promise<void> {
  const store = useStore.getState();
  const ic = store.inContext;
  store.hydrate({ inContext: null });
  // Resolve by the gateway's own request_id (see approvals.ts).
  try {
    await declineByRequestId(ic?.requestId ?? "");
  } catch {
    // Gateway POST failed — proceed with local stream abort
  }
  // Stop streaming — the user declined the permission.
  stopStreamingLive();
  store.hydrate({ streaming: false });
}

// ============================================================
// 10. Live action: switch to an existing session (M8 MED-020)
//     + history replay on page refresh (Step 5)
// ============================================================

// Shape of each event returned by GET /sessions/{id}/events
interface GatewayEventRecord {
  sequence?: number;
  event_type?: string;
  payload?: Record<string, unknown>;
  time?: string;
  // Some backends wrap user input as role/content instead
  role?: "user" | "assistant";
  content?: string;
}

interface SessionEventsResponse {
  events?: GatewayEventRecord[];
  next_sequence?: number;
}

const HISTORY_EVENTS_PAGE_SIZE = 500;
const HISTORY_EVENTS_MAX = 5000;
const HISTORY_PAGE_FETCH_MAX = 40;

function getHighestSequence(events: GatewayEventRecord[]): number {
  let highest = 0;
  for (const event of events) {
    const seq = event.sequence ?? 0;
    if (seq > highest) highest = seq;
  }
  return highest;
}

function normalizeSessionEventsResponse(
  raw: SessionEventsResponse | GatewayEventRecord[]
): { events: GatewayEventRecord[]; nextSequence: number } {
  if (Array.isArray(raw)) {
    const highest = getHighestSequence(raw);
    return { events: raw, nextSequence: highest + 1 };
  }

  const events = Array.isArray(raw.events) ? raw.events : [];
  const highest = getHighestSequence(events);
  const nextSequence =
    typeof raw.next_sequence === "number" && Number.isFinite(raw.next_sequence)
      ? raw.next_sequence
      : highest + 1;
  return { events, nextSequence };
}

async function loadSessionEventsWindow(
  sessionId: string
): Promise<{ events: GatewayEventRecord[]; lastSeq: number }> {
  // HACHI-020: skip sessions whose events endpoint previously returned 4xx
  // to prevent repeated 400 spam observed in the audit (~8 calls per switch).
  if (sessionRef.eventsUnavailable.has(sessionId)) {
    throw new Error("events-unavailable");
  }

  let after = 0;
  let pageCount = 0;
  let lastSeq = 0;
  let boundedEvents: GatewayEventRecord[] = [];

  while (pageCount < HISTORY_PAGE_FETCH_MAX) {
    let raw: SessionEventsResponse | GatewayEventRecord[];
    try {
      raw = await apiGet<SessionEventsResponse | GatewayEventRecord[]>(
        `/sessions/${sessionId}/events`,
        {
          after,
          limit: HISTORY_EVENTS_PAGE_SIZE,
        }
      );
    } catch (err) {
      // HACHI-020: record 4xx failures so we don't re-fetch the same session.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        sessionRef.eventsUnavailable.add(sessionId);
      }
      throw err;
    }
    const { events, nextSequence } = normalizeSessionEventsResponse(raw);
    if (events.length === 0) break;

    const pageHighest = getHighestSequence(events);
    if (pageHighest > lastSeq) lastSeq = pageHighest;

    boundedEvents = [...boundedEvents, ...events];
    if (boundedEvents.length > HISTORY_EVENTS_MAX) {
      boundedEvents = boundedEvents.slice(-HISTORY_EVENTS_MAX);
    }

    pageCount += 1;
    if (nextSequence <= after) break;
    after = nextSequence;
    if (events.length < HISTORY_EVENTS_PAGE_SIZE) break;
  }

  return { events: boundedEvents, lastSeq };
}

/**
 * Replay an ordered list of gateway event records into a ChatMessage array.
 *
/**
 * Strip tool-protocol injection blocks from a user message and return the
 * attachments they described.
 *
 * Pattern (multiline, global):
 *   \n?[Attached image|file: <name>]\n(Use the <tool> tool with path: <path>)
 *
 * Applied only to `role === 'you'` history messages — never to the live send path.
 */
export function extractInjectedAttachments(text: string): {
  text: string;
  attachments: { name: string }[];
} {
  const attachments: { name: string }[] = [];
  const pattern = /\n?\[Attached (?:image|file): ([^\]]+)\]\n\(Use the \w+ tool with path:[^)]*\)/g;
  const cleaned = text.replace(pattern, (_match, name: string) => {
    attachments.push({ name });
    return "";
  });
  return { text: cleaned.trimEnd(), attachments };
}

/**
 * The gateway's GET /sessions/{id}/events endpoint returns the same event
 * envelopes that the SSE stream emits in real-time (confirmed by inspecting
 * runtime_events.py). We walk them in order:
 *   - `session.turn.started` opens a new user + hachi message pair.
 *   - `text.stream.delta` appends to the current hachi message text.
 *   - `tool.call.*` upserts action cards on the current hachi message.
 *   - `session.chat.completed` closes the current turn.
 *
 * Returns { messages, lastSeq } where lastSeq is the highest sequence number
 * seen (used to advance the SSE cursor so the next live turn only streams new
 * events).
 */
export function reconstructMessagesFromEvents(events: GatewayEventRecord[]): {
  messages: import("../types").ChatMessage[];
  lastSeq: number;
} {
  const reconstructed: import("../types").ChatMessage[] = [];
  let currentHachiIndex: number | null = null;
  let lastSeq = 0;

  for (const ev of events) {
    const seq = ev.sequence ?? 0;
    if (seq > lastSeq) lastSeq = seq;

    const eventType = ev.event_type ?? "";
    const payload = ev.payload ?? {};

    switch (eventType) {
      case "session.turn.started": {
        // Open a new user + hachi message pair
        const rawUserText =
          typeof payload.user_text === "string" && payload.user_text
            ? payload.user_text
            : "(message)";
        const { text: userText, attachments: injectedAttachments } =
          extractInjectedAttachments(rawUserText);
        const youId = Date.now() + reconstructed.length;
        const hachiId = youId + 1;
        reconstructed.push(
          {
            id: youId, role: "you" as const, text: userText,
            cards: [], routine: null,
            ...(injectedAttachments.length > 0
              ? { attachments: injectedAttachments.map((a) => ({ name: a.name, mediaType: "", size: 0 })) }
              : {}),
          },
          { id: hachiId, role: "hachi" as const, text: "", cards: [], routine: null }
        );
        currentHachiIndex = reconstructed.length - 1;
        break;
      }

      case "text.stream.delta": {
        if (currentHachiIndex == null) break;
        const text = typeof payload.text === "string" ? payload.text : "";
        const message = reconstructed[currentHachiIndex];
        reconstructed[currentHachiIndex] = { ...message, text: message.text + text };
        break;
      }

      case "tool.call.started": {
        if (currentHachiIndex == null) break;
        const name = typeof payload.name === "string" ? payload.name : "tool";
        const key = "tool-" + name;
        const msg = reconstructed[currentHachiIndex];
        const hasCard = msg.cards.some((c) => c.key === key);
        if (!hasCard) {
          reconstructed[currentHachiIndex] = {
            ...msg,
            cards: [...msg.cards, { key, icon: "play", title: name, status: "working" as const, cost: 0, tool: name, detail: "", expanded: false }],
          };
        }
        break;
      }

      case "tool.call.completed": {
        if (currentHachiIndex == null) break;
        const name = typeof payload.name === "string" ? payload.name : "tool";
        const key = "tool-" + name;
        const preview = typeof payload.result_preview === "string" ? payload.result_preview : "";
        const msg = reconstructed[currentHachiIndex];
        const exists = msg.cards.some((c) => c.key === key);
        reconstructed[currentHachiIndex] = {
          ...msg,
          cards: exists
            ? msg.cards.map((c) => c.key === key ? { ...c, status: "done" as const, icon: "circle-check", detail: preview } : c)
            : [...msg.cards, { key, icon: "circle-check", title: name, status: "done" as const, cost: 0, tool: name, detail: preview, expanded: false }],
        };
        break;
      }

      case "session.chat.completed": {
        currentHachiIndex = null;
        break;
      }

      default:
        break;
    }
  }

  return { messages: reconstructed, lastSeq };
}

/**
 * Load (or reload) conversation history for the active session and hydrate
 * `messages` in the store.
 *
 * Safe to call on mount and on active-session change:
 *  - Skips in mock mode (fixtures are already seeded).
 *  - Guards against overwriting an in-flight streaming turn.
 *  - If no active session id is available, does nothing.
 */
export async function loadConversationHistory(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures

  // Don't overwrite a live in-flight stream
  const store = useStore.getState();
  if (store.streaming) return;

  // No sessions in the store — nothing to display. Reset to a clean slate.
  // This fires after all sessions are deleted: the activeSessionId effect changes
  // from a SQLite ID to null, re-running this function, and without this guard the
  // sessionRef.liveSessionId fallback (a gateway UUID, a different ID namespace)
  // would fetch a now-deleted session, get a 4xx, and the catch clause would leave
  // stale messages on screen ("history unavailable — leave messages intact").
  if (store.sessions.length === 0) {
    resetLiveSession(null);
    useStore.getState().hydrate({ messages: [], streaming: false, inContext: null });
    return;
  }

  // Find the active session id — prefer the flagged active session, then sessionRef.liveSessionId.
  const activeSess = store.sessions.find((s) => s.active);
  const sessionId = activeSess?.id ?? sessionRef.liveSessionId;
  if (!sessionId) return;

  let history: { events: GatewayEventRecord[]; lastSeq: number };
  try {
    history = await loadSessionEventsWindow(sessionId);
  } catch {
    // History unavailable — leave existing messages intact (don't blank the screen)
    return;
  }

  // Guard again: a stream may have started while we were fetching
  if (useStore.getState().streaming) return;

  const { messages: reconstructed, lastSeq: reconstructedLastSeq } =
    reconstructMessagesFromEvents(history.events);
  const lastSeq = Math.max(reconstructedLastSeq, history.lastSeq);

  // Advance cursor so the next live turn only streams new events
  if (lastSeq > sessionRef.cursor) sessionRef.cursor = lastSeq;
  // Also sync the live session id so the SSE stream uses the right session
  if (!sessionRef.liveSessionId) {
    sessionRef.setSession(sessionId); // FIX 2: persist rehydrated id
  }

  if (reconstructed.length === 0) {
    // Nothing to restore — leave the store as-is (welcome screen is correct)
    return;
  }

  useStore.getState().hydrate({ messages: reconstructed });
}

/**
 * Switch the live session to `sessionId`:
 *  1. Abort the current SSE stream.
 *  2. Update module-level session id + reset cursor.
 *  3. Fetch the session's event history via reconstructMessagesFromEvents.
 *  4. Mark that session active in the sessions slice.
 */
export async function switchSessionLive(sessionId: string): Promise<void> {
  // 1 + 2: abort the in-flight stream and switch the whole kernel — session
  // id, cursor, delta/run-line buffers, rAF handles, cost baselines — to
  // sessionId in one call (T-65 / CF-16).
  resetLiveSession(sessionId);

  // Optimistically mark this session active in the store before the fetch
  const store = useStore.getState();
  store.hydrate({
    messages: [],
    streaming: false,
    inContext: null,
    sessions: store.sessions.map((s) =>
      s.id === sessionId ? { ...s, active: true } : { ...s, active: false }
    ),
  });

  // 3. Fetch history
  let history: { events: GatewayEventRecord[]; lastSeq: number };
  try {
    history = await loadSessionEventsWindow(sessionId);
  } catch {
    // History unavailable — show an honest "resumed" placeholder
    const resumed: import("../types").ChatMessage = {
      id: Date.now(),
      role: "hachi" as const,
      text: "(session resumed — history unavailable)",
      cards: [],
      routine: null,
    };
    useStore.getState().hydrate({ messages: [resumed] });
    return;
  }

  // 4. Replay events using the shared helper
  const { messages: reconstructed, lastSeq: reconstructedLastSeq } =
    reconstructMessagesFromEvents(history.events);
  const lastSeq = Math.max(reconstructedLastSeq, history.lastSeq);

  // Advance cursor so the next live turn only sees new events
  sessionRef.cursor = lastSeq;

  // FIX 3: if no messages were reconstructed, treat it as an empty/fresh
  // conversation silently (don't show a misleading "no history found" note).
  useStore.getState().hydrate({ messages: reconstructed });
}

/**
 * Delete a session via DELETE /sessions/{id} and refresh recent sessions.
 * If the deleted session is currently active, clear the in-memory live pointer.
 */
export async function deleteSessionLive(sessionId: string): Promise<boolean> {
  if (USE_MOCKS) return false;

  const encoded = encodeURIComponent(sessionId);
  try {
    await apiSend<unknown>("DELETE", `/sessions/${encoded}`);
  } catch {
    return false;
  }

  if (sessionRef.liveSessionId === sessionId) {
    resetLiveSession(null);
    useStore.getState().hydrate({
      messages: [],
      streaming: false,
      inContext: null,
    });
  }

  await loadSessions();
  return true;
}

/**
 * Start a brand-new session (clears in-flight state and defers session
 * creation until the next startTaskLive call).
 */
export function newSessionLive(): void {
  resetLiveSession(null);
  useStore.getState().hydrate({
    messages: [],
    streaming: false,
    inContext: null,
    sessions: useStore.getState().sessions.map((s) => ({ ...s, active: false })),
  });
}

// ============================================================
// 11a. Cleanup: POST /sessions/cleanup-empty (FIX 3)
//     Asks the backend to remove 0-message sessions, then refreshes the list.
// ============================================================

export async function cleanupEmptySessionsLive(): Promise<void> {
  if (USE_MOCKS) return;
  try {
    await apiSend<unknown>("POST", "/sessions/cleanup-empty");
  } catch {
    // Best-effort — even if the route doesn't exist yet, refresh the list.
  }
  await loadSessions();
}

// ============================================================
// 11. Loader: GET /sessions?limit=10 → UI Session[]
// ============================================================

export async function loadSessions(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewaySession[]>("/sessions", { limit: 10 });
    const list = Array.isArray(raw) ? raw : [];
    // FIX 4: mark the live session active so `activeSessionId` resolves (otherwise the
    // picker stays empty and loadConversationHistory never fires for the current chat).
    const adapted = list
      .map(adaptSession)
      .map((s) => (s.id === sessionRef.liveSessionId ? { ...s, active: true } : s));
    // NOTE: today's spend (`spent`) is the SINGLE source of truth from the
    // durable backend ledger, hydrated via loadDailySpend() (GET /spend/daily).
    // We deliberately do NOT also sum session costs here — that produced a
    // second, diverging value for the same number (the inconsistency users saw).
    useStore.getState().hydrate({ sessions: adapted });
    useStore.getState().setReconnecting(false);
  } catch {
    // FIX 4: do NOT wipe the list on a transient error — a stale list beats an empty
    // one. Wiping to [] on any blip is what made the previous-sessions list flicker
    // between visible and empty. Just flag reconnecting and keep what we have.
    useStore.getState().setReconnecting(true);
  }
}

// Self-register so bootstrapLiveData() picks this up on app mount.
registerLoader("chat", loadSessions);

// ============================================================
// 12. Load a past session's transcript from GET /sessions/{id}/messages
//     Returns the reconstructed ChatMessage[] for display-only mode.
//     HTTP 404 resolves to null (unknown session id).
// ============================================================

interface GatewaySessionMessagesResponse {
  session_id?: string;
  messages?: Array<{ role: string; content: string; [key: string]: unknown }>;
}

/**
 * Fetch a past session's transcript from `GET /sessions/{id}/messages`
 * and adapt it into the same ChatMessage[] shape that loadConversationHistory
 * produces via reconstructMessagesFromEvents.
 *
 * Returns null on 404 (unknown session) so the caller can fall back gracefully.
 * Other errors propagate (the caller should handle them).
 */
export async function loadSessionMessages(
  sessionId: string
): Promise<import("../types").ChatMessage[] | null> {
  if (USE_MOCKS) return null;

  let raw: GatewaySessionMessagesResponse;
  try {
    raw = await apiGet<GatewaySessionMessagesResponse>(`/sessions/${encodeURIComponent(sessionId)}/messages`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }

  const msgs = Array.isArray(raw.messages) ? raw.messages : [];
  if (msgs.length === 0) return [];

  // Build a minimal ChatMessage[] from role/content pairs.
  // Each user message is paired with the following assistant message.
  const result: import("../types").ChatMessage[] = [];
  let baseId = Date.now();
  for (const m of msgs) {
    const role = m.role === "user" ? "you" as const : "hachi" as const;
    const rawText = typeof m.content === "string" ? m.content : "";
    if (role === "you") {
      const { text, attachments: injected } = extractInjectedAttachments(rawText);
      result.push({
        id: baseId++, role, text, cards: [], routine: null,
        ...(injected.length > 0
          ? { attachments: injected.map((a) => ({ name: a.name, mediaType: "", size: 0 })) }
          : {}),
      });
    } else {
      result.push({ id: baseId++, role, text: rawText, cards: [], routine: null });
    }
  }
  return result;
}
