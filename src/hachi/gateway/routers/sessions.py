"""``/sessions/*`` -- session lifecycle, messaging, and SSE event streaming (T-47).

The highest-traffic router and the one with the most direct
``require_auth(request)`` / ownership-check call sites (T-47 Sec 3.4).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from hachi.gateway import fastapi_app as _fastapi_app
from hachi.gateway.control_plane import (
    GatewayControlPlane,
    GatewayProtocolError,
    GatewayUnavailableError,
)
from hachi.gateway.deps import CallerHash, ControlPlaneDep, check_session_ownership, require_auth
from hachi.gateway.fastapi_app import (
    _ERROR_RESPONSES,
    AlwaysAllowRequest,
    SessionCloseResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionEventsResponse,
    SessionInterruptResponse,
    SessionMessageRequest,
    SessionMessageResponse,
)

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


# Step 7 — Minimal slash-command handler.
# Intercepts messages whose text starts with '/' BEFORE the LLM sees them
# so that simple meta-commands work even when no agent is booted yet.
_SLASH_HELP_TEXT = (
    "Available slash commands:\n"
    "  /model — Show the current model and how to change it\n"
    "  /memory <query>  — Search long-term memory\n"
    "  /remember <fact> — Save a fact to long-term memory\n"
    "  /help  — Show this help text"
)


def _build_augmented_prompt(prompt: str, attachment_ids: list[str]) -> str:
    """Append file-attachment context blocks to *prompt* (graceful: errors skipped)."""
    if not attachment_ids:
        return prompt
    try:
        from hachi.core.files.store import FileMetaStore, LocalStorageProvider
        from hachi.core.paths import get_hachi_paths

        paths = get_hachi_paths()
        meta_store = FileMetaStore(paths.files_db)
        storage = LocalStorageProvider(paths.blobs_dir)

        parts: list[str] = []
        for file_id in attachment_ids[:5]:  # cap at 5 attachments per turn
            try:
                meta = meta_store.get(file_id)
                if meta is None:
                    continue
                if meta.media_type.startswith("image/"):
                    blob_path = str(paths.blobs_dir / file_id)
                    parts.append(
                        f"[Attached image: {meta.name}]\n"
                        f"(Use the view_image tool with path: {blob_path})"
                    )
                elif meta.extracted_text_ref:
                    try:
                        with storage.open(meta.extracted_text_ref) as f:
                            text = f.read(4096).decode("utf-8", errors="replace")
                        parts.append(f"[Attached file: {meta.name}]\n{text}")
                    except Exception:
                        parts.append(f"[Attached file: {meta.name} — extraction pending]")
                else:
                    parts.append(
                        f"[Attached file: {meta.name} ({meta.media_type})] — no extracted text yet"
                    )
            except Exception:
                continue
        if not parts:
            return prompt
        return prompt.rstrip() + "\n\n" + "\n\n".join(parts)
    except Exception:
        return prompt


def _handle_slash_command(
    session_id: str, prompt: str, cp: GatewayControlPlane
) -> dict[str, Any] | None:
    """Return a synthetic assistant response for leading-slash commands, or None.

    Returns None when ``prompt`` is NOT a slash command so the caller can
    fall through to the normal LLM path unaffected.
    """
    stripped = prompt.strip()
    if not stripped.startswith("/"):
        return None
    # Extract the command word (everything up to first whitespace) and the
    # remainder of the line as the command's argument text.
    rest = stripped[1:]
    parts = rest.split(maxsplit=1)
    word = parts[0].lower() if rest.strip() else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if word == "model":
        try:
            from hachi.core.config_loader import load_effective_config as _load_cfg

            cfg = _load_cfg()
            model_name = cfg.provider.model
            provider_kind = cfg.provider.kind
        except Exception:
            model_name = "unknown"
            provider_kind = "unknown"
        reply = (
            f"Current model: **{model_name}** (provider: {provider_kind})\n\n"
            "To change the model, go to **Settings → Provider** in the web UI, "
            "or run `hachi setup --tier lightning` and select a different model."
        )
    elif word == "memory":
        # H4: /memory <query> — real semantic recall, wired to GET /memory/recall's
        # backing handler so this behaves identically to that endpoint.
        if not arg:
            reply = "Usage: `/memory <query>` — searches long-term memory for a query."
        else:
            try:
                hits = cp.memory_recall(query=arg, top_k=5)
            except Exception:
                hits = []
            if not hits:
                reply = f"No memory found for “{arg}”."
            else:
                lines = [f"Memory results for “{arg}”:"]
                for hit in hits:
                    content = (
                        hit.get("content") or hit.get("raw_content") or ""
                        if isinstance(hit, dict)
                        else str(hit)
                    )
                    if content:
                        lines.append(f"- {content}")
                reply = "\n".join(lines)
    elif word == "remember":
        # H4: /remember <fact> — real write, wired to POST /memory/facts' backing
        # handler (stored in the user_profile collection, like the UI's memory tool).
        if not arg:
            reply = "Usage: `/remember <fact>` — saves a fact to long-term memory."
        else:
            try:
                cp.add_memory_fact(group="user_profile", text=arg)
                reply = f"Got it — I'll remember: “{arg}”."
            except Exception as exc:
                reply = f"Couldn't save that fact: {exc}"
    elif word == "help":
        reply = _SLASH_HELP_TEXT
    else:
        reply = f"Unknown command `/{word}`. Try /help to see available commands."

    return {"response": reply}


@router.post("", response_model=SessionCreateResponse)
async def create_session(
    payload: SessionCreateRequest, caller_hash: CallerHash, cp: ControlPlaneDep
) -> Any:
    # boot_agent runs MCP subprocess spawns — move off the event loop so
    # SSE keepalives and health checks stay responsive during boot.
    result = await anyio.to_thread.run_sync(
        lambda: cp.create_session(
            client_id=payload.client_id,
            surface=payload.surface,
            capabilities=payload.capabilities,
            protocol_version=payload.protocol_version,
        )
    )
    # C1: stamp ownership immediately after creation so subsequent accesses
    # are gated.  When auth is disabled caller_hash is None — skip stamping.
    if caller_hash is not None:
        cp.set_session_owner(result["session_id"], caller_hash)
    return result


@router.get("")
async def list_sessions(
    caller_hash: CallerHash, cp: ControlPlaneDep, limit: int = Query(default=10, ge=1, le=100)
) -> Any:
    # C1: filter sessions so a caller only sees sessions they own.
    # Legacy sessions (NULL owner) are always returned for backward compat.
    # B5+B7a: offload the SQLite read (now 2 queries instead of 2N) off the
    # event loop; owner filtering runs inline after (in-memory O(1) dict lookups).
    all_sessions = await anyio.to_thread.run_sync(lambda: cp.recent_sessions(limit))
    if caller_hash is None:
        # No-auth mode: return everything.
        return all_sessions

    # Filter: include the session if the owner is NULL (legacy) or matches caller.
    def _owned(s: dict[str, Any]) -> bool:
        sid = s.get("session_id") or s.get("id") or ""
        if not sid:
            return True  # can't tell — include it
        owner = cp.get_session_owner(sid)
        import secrets as _secrets

        return owner is None or _secrets.compare_digest(owner, caller_hash)

    if isinstance(all_sessions, list):
        return [s for s in all_sessions if _owned(s)]
    return all_sessions


@router.get("/{session_id}")
async def get_session(session_id: str, caller_hash: CallerHash, cp: ControlPlaneDep) -> Any:
    check_session_ownership(cp, session_id, caller_hash)
    return cp.session_state(session_id)


@router.post(
    "/{session_id}/messages",
    response_model=SessionMessageResponse,
    response_model_exclude_none=True,
)
def post_message(
    session_id: str,
    payload: SessionMessageRequest,
    request: Request,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
) -> Any:
    check_session_ownership(cp, session_id, caller_hash)
    # Ingress idempotency (AUDIT P0-3 / Theme A): a duplicate POST — client retry,
    # reconnect, or double-click — carrying the same key replays the prior turn's
    # response instead of re-running the whole turn (no double spend, no duplicate
    # side effects). The header wins over the body field; an absent key disables it.
    idem_key = (request.headers.get("Idempotency-Key") or payload.idempotency_key or "").strip()
    # T-47: qualified module access (not `from ... import _resolve_session_store`) --
    # several tests patch `hachi.gateway.fastapi_app._resolve_session_store` and rely
    # on that patch being visible to whoever calls it at request time.
    store = _fastapi_app._resolve_session_store() if idem_key else None
    if store is not None:
        prior = store.get_idempotent_response(session_id, idem_key)
        if prior is not None:
            return prior
    # Step 7: intercept slash commands before the LLM.
    slash_result = _handle_slash_command(session_id, payload.prompt, cp)
    if slash_result is not None:
        return slash_result
    augmented = _build_augmented_prompt(payload.prompt, payload.attachment_ids)
    result = cp.send_message(session_id=session_id, prompt=augmented)
    # Persist only on success (send_message raises on failure → left retryable).
    if store is not None:
        store.save_idempotent_response(session_id, idem_key, result)
    return result


@router.delete("/{session_id}", response_model=SessionCloseResponse)
async def delete_session(session_id: str, caller_hash: CallerHash, cp: ControlPlaneDep) -> Any:
    check_session_ownership(cp, session_id, caller_hash)
    return cp.close_session(session_id)


# P1-4 — POST /sessions/{session_id}/interrupt
# Signals the live execution loop to stop at its next iteration boundary so that
# a browser Stop button or SSE disconnect actually cancels the server-side turn
# instead of only closing the coroutine while the worker thread keeps running.
@router.post("/{session_id}/interrupt", response_model=SessionInterruptResponse)
async def interrupt_session(session_id: str, caller_hash: CallerHash, cp: ControlPlaneDep) -> Any:
    """Cancel the in-progress turn for a live session.

    Delivers a cancellation signal to the session's ``ExecutionLoop`` via
    ``ExecutionLoop.cancel()`` (a thread-safe ``threading.Event``). The current
    LLM chunk and any in-progress tool call finish first; the loop exits cleanly
    at the top of the following iteration, preserving message-history integrity.

    Returns ``{"interrupted": true}`` when the signal was delivered to a live
    session, ``{"interrupted": false}`` when the session is not live or no
    in-process runtime is attached (safe — never raises a 5xx).
    """
    check_session_ownership(cp, session_id, caller_hash)
    return cp.interrupt(session_id)


# Issue 12 — POST /sessions/cleanup-empty
# Deletes session rows that have no messages (ghost sessions accumulated before
# the lazy-row-creation fix landed).  Requires auth; returns {"removed": <int>}.
@router.post("/cleanup-empty")
async def cleanup_empty_sessions(_caller_hash: CallerHash) -> Any:
    """Delete all empty sessions (no messages) from the SQLite store.

    Safe to call at any time.  Sessions that are currently booting (in-memory
    but with no messages yet) are not affected — they have no DB row yet.

    Returns:
        ``{"removed": <count>}`` — the number of rows deleted.
    """
    # T-62 (BB-108): no session_id here, so there is nothing to owner-check —
    # this operation is intentionally global (deletes empty rows across all
    # owners). `_caller_hash` still enforces require_auth explicitly, matching
    # every other handler on this router, rather than relying solely on the
    # router-level dependency whose return value FastAPI discards.
    removed = 0
    try:
        from hachi.gateway.shared_services import ProcessSharedServices

        _shared = ProcessSharedServices.instance()
        if _shared is not None and _shared.session_store is not None:
            removed = _shared.session_store.delete_empty_sessions()
        else:
            # Fallback: instantiate a fresh store (same DB file). T-10 / BB-42
            # recurrence: this one is NOT the process-shared instance, so it
            # must be shut down here — otherwise its sqlite3/WAL connection
            # leaks for the rest of the process lifetime (mirrors the correct
            # fallback shape already in core/cost.py).
            from hachi.core.session_store import SessionStore

            _store = SessionStore()
            try:
                removed = _store.delete_empty_sessions()
            finally:
                _store.shutdown()
    except Exception:
        _log.warning("cleanup_empty_sessions: failed to delete empty sessions", exc_info=True)
    return {"removed": removed}


# TASK-B — GET /sessions/{session_id}/messages
# Returns the durable transcript for a past (or live) session.  Sourced directly
# from the SQLite SessionStore so it works even when the session is no longer active.
# HTTP 404 when the session_id is unknown or has no persisted messages.
@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
    limit: int = Query(default=200, ge=1, le=1000),
) -> Any:
    """Return the durable message transcript for *session_id*.

    Sourced from the SQLite ``messages`` table so it works for historical
    sessions that are no longer active.  Messages are returned in chronological
    order (oldest first).

    Returns::

        {
          "session_id": "<id>",
          "messages": [
            {"id": <int>, "role": "user"|"assistant"|"tool",
             "content": "...", "tool_name": null|"...", "timestamp": "<iso>"},
            ...
          ]
        }

    HTTP 404 when *session_id* is unknown or has no persisted messages.
    """
    # T-62 (BB-108): this is a transcript endpoint — check ownership before
    # touching the store, so a 403 never costs a SQLite round-trip.
    check_session_ownership(cp, session_id, caller_hash)
    # B5: session_transcript hits SQLite — offload to thread so the event loop
    # stays free for SSE keepalives during a long message-history fetch.
    result = await anyio.to_thread.run_sync(
        lambda: cp.session_transcript(session_id=session_id, limit=limit)
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    return result


# G1-T4 — POST /sessions/{session_id}/permissions/always-allow
# The frontend posts to /sessions/current/permissions/always-allow — "current" is a
# sentinel that the server resolves to the most-recently-booted session (or ignores
# ownership when running no-auth).  When the literal session_id exists in the connection
# map the ownership check applies; for the "current" sentinel we skip it (no session to
# own) and delegate resolution to the handler.
@router.post("/{session_id}/permissions/always-allow")
async def post_always_allow(
    session_id: str, payload: AlwaysAllowRequest, caller_hash: CallerHash, cp: ControlPlaneDep
) -> Any:
    """Persist a standing always-allow rule for the given request+action pair.

    The frontend sends ``session_id = "current"`` as a sentinel; the backend
    resolves it to the active session or persists the rule globally.

    Body: ``{"request": "<tool/path>", "action": "<operation>"}``
    """
    # Only enforce ownership for real (non-sentinel) session ids.
    if session_id != "current":
        check_session_ownership(cp, session_id, caller_hash)
    return cp.add_always_allow(session_id, payload.request, payload.action)


@router.get("/{session_id}/events", response_model=SessionEventsResponse)
async def get_session_events(
    session_id: str,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> Any:
    check_session_ownership(cp, session_id, caller_hash)
    # B5: thread the whole call — historical sessions hit SQLite via
    # _synthesize_history_events; live sessions use an in-memory slice (minor
    # thread-hop overhead, acceptable since live callers use SSE not this endpoint).
    return await anyio.to_thread.run_sync(
        lambda: cp.read_session_events(session_id=session_id, after=after, limit=limit)
    )


@router.get("/{session_id}/stream")
async def stream_session_events(
    session_id: str,
    request: Request,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
    after: int = Query(default=0, ge=0),
    poll_interval: float = Query(default=1.0, ge=0.0, le=10.0),
    max_idle_cycles: int = Query(default=30, ge=1, le=600),
) -> StreamingResponse:
    """Server-Sent Events stream of runtime events — replaces client polling.

    Pushes events as they arrive, then closes after ``max_idle_cycles`` idle
    polls. The frontend consumes this with ``fetch`` + ``ReadableStream``
    (``frontend/src/lib/data/sse.ts``), not the native ``EventSource`` API
    (which cannot set an ``Authorization`` header) — it does **not**
    auto-reconnect; callers own their own retry/backoff policy. Clients that
    do reconnect resume with the last ``id`` they saw via the ``after`` query
    param; compare the resumed response's ``base_sequence`` against that
    cursor to detect whether events were dropped by the ring buffer's trim
    while disconnected (T-30).
    """
    # C1: ownership check before any streaming starts.
    check_session_ownership(cp, session_id, caller_hash)
    # Validate up front so unknown-session / unattached-stream surface as proper
    # HTTP errors instead of mid-stream failures.
    cp.read_session_events(session_id=session_id, after=after, limit=1)

    async def event_stream() -> AsyncIterator[str]:
        cursor = after
        idle = 0
        # MED-002: use asyncio.Event to avoid busy-polling.  The event is set
        # by a short-interval background task that acts as an adaptive timer
        # rather than a fixed sleep.  When the session just produced events we
        # wait only 50 ms (tight loop for incremental text); when idle we wait
        # up to poll_interval seconds, waking early if new events arrive.
        _wake = asyncio.Event()
        _wake.set()  # fire immediately on first iteration

        async def _poll_trigger() -> None:
            """Signal _wake whenever the session has events or at poll_interval."""
            while True:
                await asyncio.sleep(0.05)
                _wake.set()

        _trigger_task: asyncio.Task[None] = asyncio.create_task(_poll_trigger())

        yield ": connected\n\n"
        try:
            while True:
                # Wait for wake signal (set either on first tick or by trigger task).
                await _wake.wait()
                _wake.clear()

                if await request.is_disconnected():
                    # T-29: a disconnect is CONNECTION-scoped; the turn is SESSION-scoped.
                    # Stop this stream only. Cancelling the turn is an explicit user action
                    # via POST /sessions/{session_id}/interrupt (ownership-checked).
                    break
                try:
                    batch = cp.read_session_events(session_id=session_id, after=cursor, limit=100)
                except (GatewayProtocolError, GatewayUnavailableError):
                    # Session was closed (or the runtime detached) mid-stream. The
                    # response has already started, so end gracefully instead of
                    # letting the exception abort the connection.
                    yield "event: closed\ndata: {}\n\n"
                    break
                events = batch.get("events", [])
                if events:
                    idle = 0
                    for event in events:
                        cursor = event["sequence"]
                        yield (
                            f"id: {event['sequence']}\n"
                            f"event: {event['event_type']}\n"
                            f"data: {json.dumps(event)}\n\n"
                        )
                else:
                    idle += 1
                    if idle >= max_idle_cycles:
                        break
                    yield ": keepalive\n\n"
                    # Slow down the trigger task when idle to save CPU.
                    await asyncio.sleep(max(0.0, poll_interval - 0.05))
        finally:
            _trigger_task.cancel()
            try:
                await _trigger_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
