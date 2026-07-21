"""``/v1/*`` -- OpenAI-compatible facade (T-47).

Lets users point Open WebUI / LibreChat / any OpenAI client at Hachi. Maps
the latest user message to an auto-managed agent session (the agent keeps
its own conversation memory).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from hachi.core.rate_limit import RateLimiter
from hachi.gateway import fastapi_app as _fastapi_app
from hachi.gateway.deps import CallerHash, ControlPlaneDep, check_session_ownership, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, OpenAIChatRequest, _last_user_text

router = APIRouter(
    tags=["openai"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


def _get_openai_limiter(request: Request) -> RateLimiter:
    """Read the per-app rate limiter created by ``create_gateway_app``.

    T-47: ``openai_limiter`` used to be a closure local, freshly constructed
    on every ``create_gateway_app()`` call -- kept identical by living on
    ``app.state`` (populated in ``create_gateway_app`` using this module's own
    ``RateLimiter``/``_int_env``, so a test that monkeypatches
    ``hachi.gateway.fastapi_app.RateLimiter`` before calling
    ``create_gateway_app`` still observes the patched class).
    """
    return request.app.state.openai_limiter  # type: ignore[no-any-return]


OpenAiLimiterDep = Annotated[RateLimiter, Depends(_get_openai_limiter)]


@router.post("/v1/chat/completions")
async def openai_chat_completions(
    payload: OpenAIChatRequest,
    request: Request,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
    openai_limiter: OpenAiLimiterDep,
) -> Any:
    # H6: resolve the caller's identity so a facade session created below is
    # stamped with an owner, same as the normal POST /sessions route.
    prompt = _last_user_text(payload.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="no user message content")
    # BB-58: key the rate limiter per-principal (the same caller_hash H6 uses
    # for session ownership) instead of one shared global bucket — otherwise
    # one caller exhausting the limit locks out every other caller too.
    # ``caller_hash`` is None in no-auth mode; fall back to a fixed key there
    # since there is no notion of distinct principals without auth.
    rate_key = caller_hash or "anonymous"
    if not openai_limiter.allow(rate_key):
        raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")

    # BB-58: validate (not silently ignore) the requested model. When the
    # control plane has allowlist data (list_all_models — the same data that
    # powers GET /config/models/all), reject unrecognized model ids instead
    # of silently substituting a default. When no allowlist data is wired
    # (handler not attached), fail open — nothing to validate against.
    requested_model = (payload.model or "").strip()
    if requested_model:
        try:
            all_models = await anyio.to_thread.run_sync(cp.list_all_models)
        except Exception:
            all_models = {}
        known_models: set[str] = set()
        if isinstance(all_models, dict):
            for ids in all_models.values():
                if isinstance(ids, list):
                    known_models.update(str(i) for i in ids)
        if known_models and requested_model not in known_models:
            raise HTTPException(
                status_code=400,
                detail=f"model '{requested_model}' is not recognized or allowed",
            )

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())
    model_name = payload.model or "hachi"

    if not payload.stream:
        # T-32: never share one server-side session across callers. Namespace
        # by principal (the caller's own token hash, or "anon" in no-auth
        # mode) so ids cannot collide across bearer tokens; use the client's
        # OpenAI `user` field for continuity when supplied, otherwise mint a
        # fresh per-request session — matching what the streaming path
        # already does below.
        principal = caller_hash or "anon"
        conversation = (payload.user or "").strip()
        session_key = f"openai:{principal}:{conversation or f'req-{uuid.uuid4().hex}'}"

        # Full agent turn is synchronous — run in a thread so the event loop stays free.
        session_id = await anyio.to_thread.run_sync(
            lambda: cp.get_or_create_session(session_key, owner_token_hash=caller_hash)
        )
        # The ownership check the other 10 session-scoped routes perform.
        # Must run BEFORE the turn: complete_chat used to run the turn and
        # only return the session id afterwards, which made a 403 here
        # meaningless — the unauthorized turn would already have executed,
        # already mutated memory, and already cost money.
        check_session_ownership(cp, session_id, caller_hash)
        try:
            result = await anyio.to_thread.run_sync(
                lambda: cp.send_message(session_id=session_id, prompt=prompt)
            )
        finally:
            # T-33: a session minted for THIS request (no client-supplied
            # `user`) must be released, or the 100-session cap fills
            # permanently. A `user`-keyed session is meant to persist for
            # continuity, so only the ephemeral fallback is closed here.
            # Never let a teardown failure mask the real turn result/error.
            if not conversation:
                try:
                    await anyio.to_thread.run_sync(lambda: cp.close_session(session_id))
                except Exception:
                    _fastapi_app._log.warning(
                        "OpenAI facade: failed to close session %s",
                        session_id,
                        exc_info=True,
                    )
        reply = result.get("response", "")
        return {
            "id": chat_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    # Streaming path: forward text.stream.delta events as OpenAI SSE chunks.
    # Each chunk carries one text fragment so clients can render incrementally.
    async def _openai_event_stream() -> AsyncIterator[str]:
        # Create/get the session before starting the turn so we can read its events.
        session_key = f"openai-{chat_id}"
        session_id: str = await anyio.to_thread.run_sync(
            lambda: cp.get_or_create_session(session_key, owner_token_hash=caller_hash)
        )
        # T-32: the same ownership check the non-streaming path performs. A
        # no-op today (session_key is a fresh per-request UUID, so the
        # session is always freshly owned by this caller) — kept so "the
        # facade checks ownership" is true without exceptions.
        check_session_ownership(cp, session_id, caller_hash)

        # Snapshot the event cursor so only events from THIS turn are forwarded.
        initial = cp.read_session_events(session_id=session_id, after=0, limit=10000)
        cursor = max((e["sequence"] for e in initial.get("events", [])), default=0)

        # OpenAI streaming: role chunk first, then content deltas.
        yield (
            "data: "
            + json.dumps(
                {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            + "\n\n"
        )

        # Run the full agent turn in a background task.
        turn_done: asyncio.Event = asyncio.Event()

        async def _run_turn() -> None:
            try:
                await anyio.to_thread.run_sync(
                    lambda: cp.send_message(session_id=session_id, prompt=prompt)
                )
            finally:
                turn_done.set()

        turn_task: asyncio.Task[None] = asyncio.create_task(_run_turn())
        # MED-002: event-driven wake instead of fixed 0.05 s busy-poll.
        _sse_wake = asyncio.Event()
        _sse_wake.set()  # fire immediately on first iteration

        async def _sse_trigger() -> None:
            while True:
                await asyncio.sleep(0.05)
                _sse_wake.set()

        _sse_trigger_task: asyncio.Task[None] = asyncio.create_task(_sse_trigger())
        try:
            while True:
                await _sse_wake.wait()
                _sse_wake.clear()

                if await request.is_disconnected():
                    turn_task.cancel()
                    # P1-4: also cancel the worker thread, not just the coroutine.
                    cp.interrupt(session_id)
                    return

                batch = cp.read_session_events(session_id=session_id, after=cursor, limit=200)
                events = batch.get("events", [])

                for event in events:
                    cursor = event["sequence"]
                    if event.get("event_type") == "text.stream.delta":
                        text = (event.get("payload") or {}).get("text", "")
                        if text:
                            yield (
                                "data: "
                                + json.dumps(
                                    {
                                        "id": chat_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_ts,
                                        "model": model_name,
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {"content": text},
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                )
                                + "\n\n"
                            )

                if turn_done.is_set() and not events:
                    break
        finally:
            _sse_trigger_task.cancel()
            try:
                await _sse_trigger_task
            except asyncio.CancelledError:
                pass
            if not turn_task.done():
                turn_task.cancel()
                try:
                    await turn_task
                except asyncio.CancelledError:
                    pass
            # T-33: this session was minted for THIS request (session_key is
            # a fresh UUID) — release it, or the 100-session cap fills
            # permanently. close_session raises on an already-closed id; a
            # leaked session is far worse than a swallowed teardown error,
            # so never let this propagate into the response stream.
            try:
                await anyio.to_thread.run_sync(lambda: cp.close_session(session_id))
            except Exception:
                _fastapi_app._log.warning(
                    "OpenAI facade: failed to close session %s",
                    session_id,
                    exc_info=True,
                )

        # Finish chunk signals end of stream.
        yield (
            "data: "
            + json.dumps(
                {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _openai_event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/v1/models")
async def openai_models(cp: ControlPlaneDep) -> Any:
    data: list[dict[str, Any]] = []
    try:
        models = cp.models()
        for item in models if isinstance(models, list) else []:
            model_id = item.get("id") if isinstance(item, dict) else str(item)
            if model_id:
                data.append({"id": model_id, "object": "model", "owned_by": "hachi"})
    except Exception:
        pass
    if not data:
        data = [{"id": "hachi", "object": "model", "owned_by": "hachi"}]
    return {"object": "list", "data": data}
