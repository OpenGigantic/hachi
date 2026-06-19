// ============================================================
// Hachi Web App — chat / sessions / streaming domain
// Wires: POST /sessions (lazy, once), POST /sessions/{id}/messages,
//        GET /sessions/{id}/stream (SSE), GET /sessions?limit=10
//
// Circular-import strategy:
//   store.ts → (dynamic import) → chat.ts → useStore
//   store.ts live-mode branches call chat.ts functions via dynamic import().
//   chat.ts imports useStore directly for reading/writing state (no cycle
//   because store.ts never statically imports chat.ts).
// ============================================================

import { apiGet, apiSend, USE_MOCKS } from "../client";
import { streamSSE } from "../sse";
import { registerLoader } from "../live";
import { useStore } from "../../store/store";
import type { Session, ActionCard } from "../types";

// ============================================================
// 1. Module-level session id (NOT persisted to store)
//    Reuse a single live session across the page lifetime.
// ============================================================

let _liveSessionId: string | null = null;
let _sseAbort: AbortController | null = null;
// Highest event sequence consumed so far. Persists ACROSS turns: the session is
// reused, and the gateway stream replays every event after this cursor, so each
// new turn must resume from the last consumed sequence — otherwise turn 2 would
// replay turn 1's text deltas into the fresh (empty) message.
let _cursor = 0;

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
  if (_liveSessionId) return _liveSessionId;
  const res = await apiSend<GatewaySessionCreateResponse>("POST", "/sessions", {
    client_id: "web",
    surface: "web",
    protocol_version: "1.0",
    capabilities: [],
  });
  _liveSessionId = res.session_id;
  _cursor = 0; // fresh session → fresh event log
  return _liveSessionId;
}

/** Cancel the current SSE stream (e.g. on stopStreaming). */
function abortSse(): void {
  if (_sseAbort) {
    _sseAbort.abort();
    _sseAbort = null;
  }
}

// ============================================================
// 5. Internal store mutation helpers
//    (used by the live functions below — avoids reaching into
//    private store internals by calling public actions or
//    hydrate-compatible patches)
// ============================================================

let _hachiMsgId: number | null = null;

/** Append a text delta to the current hachi message. */
function appendTextDelta(delta: string): void {
  const store = useStore.getState();
  const msgs = store.messages;
  const id = _hachiMsgId;
  if (id == null) return;
  const msg = msgs.find((m) => m.id === id);
  if (!msg) return;
  store.hydrate({
    messages: msgs.map((m) =>
      m.id === id ? { ...m, text: m.text + delta } : m
    ),
  });
}

/** Upsert an action card on the current hachi message. */
function upsertCard(card: ActionCard): void {
  const store = useStore.getState();
  const msgs = store.messages;
  const id = _hachiMsgId;
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
// 6. SSE event handler
// ============================================================

function handleSseMessage(eventType: string, payload: Record<string, unknown>): void {
  const store = useStore.getState();

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
      const request = typeof payload.request === "string" ? payload.request : "Permission requested";
      const action = typeof payload.action === "string" ? payload.action : "";
      const why = typeof payload.why === "string" ? payload.why : "";
      const msgId = _hachiMsgId ?? 0;
      store.hydrate({
        inContext: { msgId, request, action, why },
      });
      break;
    }

    case "usage.updated": {
      const totalTokens =
        typeof payload.total_tokens === "number" ? payload.total_tokens : null;
      const costUsd =
        typeof payload.estimated_cost_usd === "number"
          ? payload.estimated_cost_usd
          : null;
      if (costUsd != null) {
        const s = useStore.getState();
        store.hydrate({
          taskSpent: Math.round((s.taskSpent + costUsd) * 1000) / 1000,
          spent: Math.round((s.spent + costUsd) * 1000) / 1000,
        });
      }
      // totalTokens surfaced but no dedicated UI field currently
      void totalTokens;
      break;
    }

    case "session.chat.completed": {
      // Turn is done — end streaming state
      store.hydrate({ streaming: false });
      _hachiMsgId = null;
      break;
    }

    default:
      // Unknown event types are silently ignored
      break;
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
export async function startTaskLive(text: string, hachiMsgId: number): Promise<void> {
  // Cancel any in-flight stream first
  abortSse();

  _hachiMsgId = hachiMsgId;

  let sessionId: string;
  try {
    sessionId = await ensureSession();
  } catch {
    useStore.getState().setReconnecting(true);
    useStore.getState().hydrate({ streaming: false });
    return;
  }

  // Resume from the module-level cursor so a reused session only streams the
  // NEW turn's events (see _cursor docs above), not the whole session history.
  const startAfter = _cursor;

  // Open SSE stream first, THEN fire the message POST in the background.
  // This ensures we don't miss early events from the turn.
  _sseAbort = new AbortController();
  const signal = _sseAbort.signal;

  // Fire the POST in the background — we don't await the full response
  // because the turn streams via SSE. The POST response is the blocking
  // fallback; we rely on SSE events for incremental updates.
  const postPromise = apiSend<{ session_id: string; response: string }>(
    "POST",
    `/sessions/${sessionId}/messages`,
    { prompt: text },
    signal
  ).then((res) => {
    // If SSE didn't deliver a completed event (e.g. gateway closed stream early),
    // use the blocking response as the final text.
    const store = useStore.getState();
    if (store.streaming && _hachiMsgId === hachiMsgId) {
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
        _hachiMsgId = null;
      }
    }
  }).catch(() => {
    // POST failed — end streaming gracefully
    const store = useStore.getState();
    if (store.streaming && _hachiMsgId === hachiMsgId) {
      store.hydrate({ streaming: false });
      _hachiMsgId = null;
      store.setReconnecting(true);
    }
  });

  // Open the SSE stream concurrently
  try {
    await streamSSE(
      `/sessions/${sessionId}/stream`,
      {
        onMessage: (msg) => {
          // Advance the persistent cursor so the NEXT turn resumes past here.
          if (msg.id) _cursor = Math.max(_cursor, parseInt(msg.id, 10) || _cursor);

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
        onError: () => {
          useStore.getState().setReconnecting(true);
        },
        onClose: () => {
          // Stream closed — if still "streaming", end it
          const store = useStore.getState();
          if (store.streaming && _hachiMsgId === hachiMsgId) {
            store.hydrate({ streaming: false });
            _hachiMsgId = null;
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
  abortSse();
  _hachiMsgId = null;
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
  // Clear the leash immediately (optimistic)
  store.hydrate({ inContext: null });
  // In a full implementation, POST to an approval endpoint here.
  // The SSE stream continues delivering events after this.
  // For now: the gateway's permission flow uses the approvals registry;
  // the in-context leash is informational in the current gateway schema.
  // The stream will naturally continue after the in-context is cleared.
}

export async function denyLeashLive(): Promise<void> {
  const store = useStore.getState();
  store.hydrate({ inContext: null });
  // Stop streaming — the user declined the permission.
  stopStreamingLive();
  store.hydrate({ streaming: false });
}

// ============================================================
// 10. Loader: GET /sessions?limit=10 → UI Session[]
// ============================================================

export async function loadSessions(): Promise<void> {
  if (USE_MOCKS) return; // keep seeded fixtures
  try {
    const raw = await apiGet<GatewaySession[]>("/sessions", { limit: 10 });
    const adapted = Array.isArray(raw) ? raw.map(adaptSession) : [];
    useStore.getState().hydrate({ sessions: adapted });
    useStore.getState().setReconnecting(false);
  } catch {
    // Leave fixtures in place; flag offline
    useStore.getState().setReconnecting(true);
  }
}

// Self-register so bootstrapLiveData() picks this up on app mount.
registerLoader("chat", loadSessions);
