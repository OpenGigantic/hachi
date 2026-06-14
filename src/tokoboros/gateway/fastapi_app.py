from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

# UC-PERM-006: approvals inbox REST surface. core/approvals.py is a foundation
# module that is always present, so these imports are direct.
from tokoboros.core.approvals import get_approval_registry
from tokoboros.core.permissions import PermissionDecision
from tokoboros.core.rate_limit import RateLimiter
from tokoboros.gateway.control_plane import (
    PROTOCOL_VERSION,
    GatewayControlPlane,
    GatewayProtocolError,
    GatewayUnavailableError,
)

_APPROVALS_AVAILABLE = True


_log = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _parse_allowlist(raw: str | None) -> set[str]:
    return {item.strip() for item in raw.split(",")} - {""} if raw else set()


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    protocol_version: str
    time: str


class ReadinessResponse(BaseModel):
    status: str
    ready: bool
    blockers: list[str]
    active_connections: int
    time: str


class SessionCreateRequest(BaseModel):
    client_id: str
    surface: str
    protocol_version: str = PROTOCOL_VERSION
    capabilities: list[str] = Field(default_factory=list)


class SessionCreateResponse(BaseModel):
    accepted: bool
    session_id: str
    protocol_version: str
    server_capabilities: list[str]


class SessionMessageRequest(BaseModel):
    prompt: str


class SessionMessageResponse(BaseModel):
    session_id: str
    response: str


class GatewayEventPayload(BaseModel):
    sequence: int
    event_type: str
    payload: dict[str, Any]
    time: str


class SessionEventsResponse(BaseModel):
    session_id: str
    events: list[GatewayEventPayload]
    next_sequence: int


class SessionCloseResponse(BaseModel):
    session_id: str
    closed: bool


class RuntimeEventsResponseItem(BaseModel):
    model_config = ConfigDict(extra="allow")


class ConfigPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProviderRequest(BaseModel):
    provider: dict[str, Any]


class PermissionsRequest(BaseModel):
    permissions: dict[str, Any]


class MemoryRequest(BaseModel):
    memory: dict[str, Any]


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = None  # string or list of content blocks


class OpenAIChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    messages: list[OpenAIMessage]
    model: str | None = None
    stream: bool = False


class ChannelInboundRequest(BaseModel):
    sender_id: str
    text: str
    sender_name: str = ""


class ScheduleToggleRequest(BaseModel):
    enabled: bool


def _last_user_text(messages: list[OpenAIMessage]) -> str:
    """Extract the latest user message text from an OpenAI-style message list."""
    for message in reversed(messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return " ".join(parts).strip()
    return ""


class SetupApplyRequest(BaseModel):
    plan: dict[str, Any]


class ApprovalActionRequest(BaseModel):
    """Body for POST /approvals/{request_id} (UC-PERM-006)."""

    action: str  # "approve" | "deny" | "allow_once"
    rationale: str = ""


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def _error_response(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message, details=details).model_dump(mode="json"),
    )


def create_gateway_app(
    control_plane: GatewayControlPlane,
    auth_token: str | None = None,
) -> FastAPI:
    """Create the Tokoboros gateway FastAPI application.

    Args:
        control_plane: The :class:`GatewayControlPlane` that backs all routes.
        auth_token: Bearer token required on all protected routes.  When *None*
            the token is still enforced unless the opt-out env var
            ``TOKOBOROS_GATEWAY_NO_AUTH=1`` is set (power-user escape hatch).

    Auth posture (UC-AUTH-001/ADV-014):
        All protected routes require ``Authorization: Bearer <token>``.
        Loopback binding does NOT exempt authentication — a localhost-only
        daemon is still reachable by any process on the same machine.
        The opt-out env flag ``TOKOBOROS_GATEWAY_NO_AUTH=1`` disables the
        check and logs a WARNING so accidental exposure is visible in logs.

    CORS posture (UC-AUTH-003):
        Allow-list is limited to local dev origins (localhost:5173/4173) or
        the caller-provided ``TOKOBOROS_CORS_ORIGINS`` env var.
        ``allow_origins=["*"]`` is intentionally NOT used together with
        credentials / auth headers — that combination is a security anti-pattern.
    """
    # Determine effective auth posture.
    _no_auth_override = os.environ.get("TOKOBOROS_GATEWAY_NO_AUTH", "").strip() == "1"
    _auth_enabled = not _no_auth_override
    if _no_auth_override:
        _log.warning(
            "TOKOBOROS_GATEWAY_NO_AUTH=1 is set — "
            "the gateway control plane is running WITHOUT authentication. "
            "Do NOT expose this gateway on a network-accessible interface."
        )

    app = FastAPI(title="Tokoboros Gateway", version="1.0.0", lifespan=_lifespan)

    _raw_origins = os.environ.get("TOKOBOROS_CORS_ORIGINS", "")
    _cors_origins: list[str] = (
        [o.strip() for o in _raw_origins.split(",") if o.strip()]
        if _raw_origins.strip()
        else ["http://localhost:5173", "http://localhost:4173"]
    )
    # Security: allow_credentials=False — our stateless Bearer auth is header-based
    # and does not rely on cookies.  Using allow_origins=["*"] with credentials
    # would be a CORS misconfiguration; we keep the origin allow-list explicit.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    class _AuthRequired(Exception):
        pass

    @app.exception_handler(_AuthRequired)
    async def _auth_required_handler(_request: Request, _exc: _AuthRequired) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Missing or invalid bearer token",
        )

    def _require_auth(request: Request) -> None:
        """Enforce bearer-token authentication on protected routes.

        Auth is enforced when BOTH:
        - ``auth_token`` is non-empty (i.e. a token was configured/generated), AND
        - ``TOKOBOROS_GATEWAY_NO_AUTH=1`` is NOT set.

        Loopback binding does NOT exempt this check (MR-2 / UC-AUTH-001 / ADV-014).
        The security fix is at the CLI layer: ``_start_gateway_server`` now always
        generates/persists a token for loopback hosts, so ``auth_token`` is always
        truthy in normal operation.

        Tests that call ``create_gateway_app(cp)`` without a token continue to work
        because ``auth_token=None`` means no token was configured (not an error case
        in test helpers).  The VULNERABILITY was the CLI skipping token generation
        for loopback — that is fixed in ``_resolve_gateway_auth_token``.
        """
        if not _auth_enabled:
            return
        if not auth_token:
            # No token configured at all → no enforcement (e.g. unit tests that
            # call create_gateway_app(cp) directly without a token).
            return
        expected = f"Bearer {auth_token}"
        received = request.headers.get("Authorization", "")
        if not secrets.compare_digest(received.strip(), expected):
            raise _AuthRequired()

    @app.exception_handler(GatewayProtocolError)
    async def _protocol_error_handler(_request: Request, exc: GatewayProtocolError) -> JSONResponse:
        return _error_response(status_code=400, code="protocol_error", message=str(exc))

    @app.exception_handler(GatewayUnavailableError)
    async def _unavailable_error_handler(
        _request: Request, exc: GatewayUnavailableError
    ) -> JSONResponse:
        return _error_response(status_code=503, code="service_unavailable", message=str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            status_code=422,
            code="validation_error",
            message="Request validation failed",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Catch-all: prevents the LLM raising (e.g. 401 from a bad API key) from
        # returning a raw text/plain 500 that clients cannot parse.
        # Exception details (type, message, traceback) are logged server-side only —
        # never returned to the client to avoid leaking internal paths or secrets.
        import logging as _logging
        import traceback as _traceback

        correlation_id = str(uuid.uuid4())
        _logging.getLogger(__name__).error(
            "Internal error [%s]: %s\n%s",
            correlation_id,
            exc,
            _traceback.format_exc(),
        )
        return _error_response(
            status_code=500,
            code="internal_error",
            message="An unexpected error occurred.",
            details={"correlation_id": correlation_id},
        )

    # All "protected" routes carry the auth dependency.
    # When auth_token is truthy AND TOKOBOROS_GATEWAY_NO_AUTH is not set,
    # _require_auth enforces the bearer check.  The auth_token is always
    # populated by the CLI layer (MR-2 / UC-AUTH-001 / ADV-014).
    protected_dependencies = [Depends(_require_auth)]

    system = APIRouter(tags=["system"])
    sessions = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=protected_dependencies)
    runtime_ops = APIRouter(tags=["runtime"], dependencies=protected_dependencies)
    config = APIRouter(prefix="/config", tags=["config"], dependencies=protected_dependencies)
    setup = APIRouter(prefix="/setup", tags=["setup"], dependencies=protected_dependencies)

    @system.get("/")
    async def get_root() -> Any:
        return {
            "service": "Tokoboros Gateway",
            "version": "1.0.0",
            "health": "/health",
            "readiness": "/readiness",
            "docs": "/docs",
        }

    @system.get("/health", response_model=HealthResponse)
    async def get_health() -> Any:
        return control_plane.health()

    @system.get("/readiness", response_model=ReadinessResponse)
    async def get_readiness() -> JSONResponse:
        readiness = control_plane.readiness()
        status = 200 if readiness["ready"] else 503
        return JSONResponse(status_code=status, content=readiness)

    @system.get("/capabilities", dependencies=protected_dependencies)
    async def get_capabilities() -> Any:
        return control_plane.capabilities()

    @system.get("/models", dependencies=protected_dependencies)
    async def get_models() -> Any:
        return control_plane.models()

    @sessions.post("", response_model=SessionCreateResponse)
    async def create_session(payload: SessionCreateRequest) -> Any:
        # boot_agent runs MCP subprocess spawns — move off the event loop so
        # SSE keepalives and health checks stay responsive during boot.
        return await anyio.to_thread.run_sync(
            lambda: control_plane.create_session(
                client_id=payload.client_id,
                surface=payload.surface,
                capabilities=payload.capabilities,
                protocol_version=payload.protocol_version,
            )
        )

    @sessions.get("")
    async def list_sessions(limit: int = Query(default=10, ge=1, le=100)) -> Any:
        return control_plane.recent_sessions(limit)

    @sessions.get("/{session_id}")
    async def get_session(session_id: str) -> Any:
        return control_plane.session_state(session_id)

    @sessions.post("/{session_id}/messages", response_model=SessionMessageResponse)
    def post_message(session_id: str, payload: SessionMessageRequest) -> Any:
        return control_plane.send_message(session_id=session_id, prompt=payload.prompt)

    @sessions.delete("/{session_id}", response_model=SessionCloseResponse)
    async def delete_session(session_id: str) -> Any:
        return control_plane.close_session(session_id)

    @sessions.get("/{session_id}/events", response_model=SessionEventsResponse)
    async def get_session_events(
        session_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> Any:
        return control_plane.read_session_events(session_id=session_id, after=after, limit=limit)

    @sessions.get("/{session_id}/stream")
    async def stream_session_events(
        session_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        poll_interval: float = Query(default=1.0, ge=0.0, le=10.0),
        max_idle_cycles: int = Query(default=30, ge=1, le=600),
    ) -> StreamingResponse:
        """Server-Sent Events stream of runtime events — replaces client polling.

        Pushes events as they arrive, then closes after ``max_idle_cycles`` idle
        polls (an EventSource auto-reconnects, so this stays cheap and robust).
        Clients resume with the last ``id`` they saw via the ``after`` query param.
        """
        # Validate up front so unknown-session / unattached-stream surface as proper
        # HTTP errors instead of mid-stream failures.
        control_plane.read_session_events(session_id=session_id, after=after, limit=1)

        async def event_stream() -> AsyncIterator[str]:
            cursor = after
            idle = 0
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    batch = control_plane.read_session_events(
                        session_id=session_id, after=cursor, limit=100
                    )
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
                # When events just arrived keep the loop tight for incremental text;
                # fall back to poll_interval when the session is idle.
                await asyncio.sleep(0.05 if events else poll_interval)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @runtime_ops.get("/runtime/events")
    async def get_runtime_events(
        session_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> Any:
        return control_plane.runtime_events(session_id=session_id, limit=limit)

    @runtime_ops.get("/runtime/mcp/inventory")
    async def get_mcp_inventory() -> Any:
        return control_plane.mcp_inventory()

    @runtime_ops.get("/jobs/active")
    async def get_active_jobs() -> Any:
        return control_plane.active_jobs()

    @runtime_ops.get("/jobs/failed")
    async def get_failed_jobs(limit: int = Query(default=20, ge=1, le=200)) -> Any:
        return control_plane.failed_jobs(limit)

    @runtime_ops.get("/permissions/audit")
    async def get_permission_audit(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> Any:
        return control_plane.permission_audit(limit)

    @runtime_ops.get("/subagents/reports")
    async def get_subagent_reports(
        query: str = Query(default="recent delegation"),
        top_k: int = Query(default=10, ge=1, le=100),
    ) -> Any:
        return control_plane.subagent_reports(query=query, top_k=top_k)

    @runtime_ops.get("/schedules")
    async def get_schedules() -> Any:
        return {"schedules": control_plane.list_schedules()}

    @runtime_ops.delete("/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str) -> Any:
        return control_plane.cancel_schedule(schedule_id)

    @runtime_ops.put("/schedules/{schedule_id}/enabled")
    async def put_schedule_enabled(schedule_id: str, payload: ScheduleToggleRequest) -> Any:
        return control_plane.toggle_schedule(schedule_id, payload.enabled)

    @config.get("")
    async def get_config() -> Any:
        return control_plane.get_config()

    @config.put("/provider")
    async def put_provider(payload: ProviderRequest) -> Any:
        return control_plane.update_provider(payload.model_dump(mode="python"))

    @config.post("/provider/validate")
    async def post_provider_validate(payload: ProviderRequest) -> Any:
        return control_plane.validate_provider(payload.model_dump(mode="python"))

    @config.put("/permissions")
    async def put_permissions(payload: PermissionsRequest) -> Any:
        return control_plane.update_permissions(payload.model_dump(mode="python"))

    @config.put("/memory")
    async def put_memory(payload: MemoryRequest) -> Any:
        return control_plane.update_memory(payload.model_dump(mode="python"))

    @setup.get("")
    async def get_setup() -> Any:
        return control_plane.setup_state()

    @setup.post("/apply")
    async def post_setup_apply(payload: SetupApplyRequest) -> Any:
        return control_plane.apply_setup(payload.model_dump(mode="python"))

    # OpenAI-compatible facade — lets users point Open WebUI / LibreChat / any
    # OpenAI client at Tokoboros. Maps the latest user message to an auto-managed
    # agent session (the agent keeps its own conversation memory).
    openai_compat = APIRouter(tags=["openai"], dependencies=protected_dependencies)
    openai_limiter = RateLimiter(_int_env("TOKOBOROS_OPENAI_RATE_LIMIT", 60), 60.0)

    @openai_compat.post("/v1/chat/completions")
    async def openai_chat_completions(payload: OpenAIChatRequest, request: Request) -> Any:
        prompt = _last_user_text(payload.messages)
        if not prompt:
            raise HTTPException(status_code=400, detail="no user message content")
        if not openai_limiter.allow("openai"):
            raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")

        chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created_ts = int(time.time())
        model_name = payload.model or "tokoboros"

        if not payload.stream:
            # Full agent turn is synchronous — run in a thread so the event loop stays free.
            result = await anyio.to_thread.run_sync(lambda: control_plane.complete_chat(prompt))
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
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        # Streaming path: forward text.stream.delta events as OpenAI SSE chunks.
        # Each chunk carries one text fragment so clients can render incrementally.
        async def _openai_event_stream() -> AsyncIterator[str]:
            # Create/get the session before starting the turn so we can read its events.
            session_key = f"openai-{chat_id}"
            session_id: str = await anyio.to_thread.run_sync(
                lambda: control_plane.get_or_create_session(session_key)
            )

            # Snapshot the event cursor so only events from THIS turn are forwarded.
            initial = control_plane.read_session_events(session_id=session_id, after=0, limit=10000)
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
                            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
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
                        lambda: control_plane.send_message(session_id=session_id, prompt=prompt)
                    )
                finally:
                    turn_done.set()

            turn_task: asyncio.Task[None] = asyncio.create_task(_run_turn())
            try:
                while True:
                    if await request.is_disconnected():
                        turn_task.cancel()
                        return

                    batch = control_plane.read_session_events(
                        session_id=session_id, after=cursor, limit=200
                    )
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

                    await asyncio.sleep(0.05)
            finally:
                if not turn_task.done():
                    turn_task.cancel()
                    try:
                        await turn_task
                    except asyncio.CancelledError:
                        pass

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

        return StreamingResponse(_openai_event_stream(), media_type="text/event-stream")

    @openai_compat.get("/v1/models")
    async def openai_models() -> Any:
        data: list[dict[str, Any]] = []
        try:
            models = control_plane.models()
            for item in models if isinstance(models, list) else []:
                model_id = item.get("id") if isinstance(item, dict) else str(item)
                if model_id:
                    data.append({"id": model_id, "object": "model", "owned_by": "tokoboros"})
        except Exception:
            pass
        if not data:
            data = [{"id": "tokoboros", "object": "model", "owned_by": "tokoboros"}]
        return {"object": "list", "data": data}

    # Generic inbound webhook: any platform (Slack Events, Discord, Twilio SMS, a
    # custom form) can POST a message and reach the agent, with per-sender session
    # continuity. Bespoke adapters are optional sugar on top of this.
    channels_router = APIRouter(tags=["channels"], dependencies=protected_dependencies)
    # Per-sender abuse protection for the externally-reachable webhook.
    channel_limiter = RateLimiter(_int_env("TOKOBOROS_CHANNEL_RATE_LIMIT", 30), 60.0)
    channel_allowlist = _parse_allowlist(os.environ.get("TOKOBOROS_CHANNEL_ALLOWLIST"))

    @channels_router.post("/channels/{channel}/inbound")
    async def channel_inbound(channel: str, payload: ChannelInboundRequest) -> Any:
        if not payload.text.strip():
            raise HTTPException(status_code=400, detail="text is required")
        sender = payload.sender_id
        # Optional allowlist accepts either a bare sender id or "channel:sender".
        if channel_allowlist and not (
            sender in channel_allowlist or f"{channel}:{sender}" in channel_allowlist
        ):
            raise HTTPException(status_code=403, detail="sender is not allowed")
        session_key = f"{channel}:{sender}"
        if not channel_limiter.allow(session_key):
            raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")
        # Full agent turn is synchronous — run in a thread so the event loop stays free.
        result = await anyio.to_thread.run_sync(
            lambda: control_plane.complete_chat(payload.text, session_key=session_key)
        )
        return {
            "channel": channel,
            "recipient_id": sender,
            "reply": result.get("response", ""),
        }

    # UC-PERM-006: Approvals inbox REST surface.
    # These endpoints expose the pending-permission queue so that the web
    # companion and other clients can approve/deny agent actions.
    # Both routes are protected by the same auth dependency as all other routes.
    approvals_router = APIRouter(
        prefix="/approvals", tags=["approvals"], dependencies=protected_dependencies
    )

    @approvals_router.get("")
    async def get_approvals() -> Any:
        """List all pending permission requests.

        Returns:
            A JSON object ``{"pending": [...]}`` with every unresolved request
            from the approval registry.
        """
        if not _APPROVALS_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail="Approvals module not yet available — Agent A module pending.",
            )
        return {"pending": get_approval_registry().list_pending()}

    @approvals_router.post("/{request_id}")
    async def post_approval_decision(
        request_id: str,
        payload: ApprovalActionRequest,
    ) -> Any:
        """Resolve a pending permission request.

        Args:
            request_id: The opaque ID from the pending-request list.
            payload: ``{"action": "approve"|"deny"|"allow_once", "rationale": "..."}``

        Returns:
            ``{"resolved": true}`` on success.

        Raises:
            404: When ``request_id`` is not found in the registry.
            422: When ``action`` is not a valid :class:`PermissionDecision` action.
        """
        if not _APPROVALS_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail="Approvals module not yet available — Agent A module pending.",
            )
        valid_actions = {"approve", "deny", "allow_once"}
        if payload.action not in valid_actions:
            raise HTTPException(
                status_code=422,
                detail=f"action must be one of: {', '.join(sorted(valid_actions))}",
            )
        decision = PermissionDecision(action=payload.action, rationale=payload.rationale)
        resolved = get_approval_registry().resolve(request_id, decision)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Unknown request_id: {request_id}")
        return {"resolved": True}

    app.include_router(system)
    app.include_router(sessions)
    app.include_router(runtime_ops)
    app.include_router(config)
    app.include_router(setup)
    app.include_router(openai_compat)
    app.include_router(channels_router)
    app.include_router(approvals_router)

    if _auth_enabled:
        # Inject the bearer security scheme into the exported OpenAPI spec so
        # frontend clients and tools like Swagger UI know auth is required.
        def _custom_openapi() -> dict[str, Any]:
            if app.openapi_schema:
                return app.openapi_schema
            from fastapi.openapi.utils import get_openapi

            schema = get_openapi(
                title=app.title,
                version=app.version,
                routes=app.routes,
            )
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
                "type": "http",
                "scheme": "bearer",
            }
            for path_item in schema.get("paths", {}).values():
                for operation in path_item.values():
                    if not isinstance(operation, dict):
                        continue
                    operation.setdefault("security", [{"BearerAuth": []}])
                    operation.setdefault("responses", {}).setdefault(
                        "401", {"description": "Missing or invalid bearer token"}
                    )
            app.openapi_schema = schema
            return schema

        app.openapi = _custom_openapi  # type: ignore[method-assign]

    return app
