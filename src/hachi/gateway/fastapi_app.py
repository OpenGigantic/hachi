from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import anyio
from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from hachi.core.rate_limit import RateLimiter
from hachi.gateway.control_plane import (
    PROTOCOL_VERSION,
    GatewayControlPlane,
    GatewayProtocolError,
    GatewayUnavailableError,
    SessionExpiredError,
)
from hachi.gateway.deps import AuthRequired

_APPROVALS_AVAILABLE = True


_log = logging.getLogger(__name__)


def _is_loopback_host(host: str) -> bool:
    """Return *True* when ``host`` is a loopback address (127.0.0.1 / ::1 / localhost)."""
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


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
    checks: dict[str, str] = Field(default_factory=dict)
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
    attachment_ids: list[str] = []
    # Optional ingress idempotency token (AUDIT P0-3). When supplied — preferably via the
    # ``Idempotency-Key`` HTTP header, with this body field as a fallback — a duplicate
    # POST with the same key replays the prior turn's response instead of re-running the
    # turn (no double spend / duplicate side effects). The header takes precedence.
    idempotency_key: str | None = None


class SessionMessageResponse(BaseModel):
    response: str
    # Optional: the normal chat path returns the resolved session_id, but
    # synthetic slash-command replies (e.g. /model, /help) omit it — the client
    # already knows which session it posted to, so echoing it back (and risking a
    # literal "None") is noise. Excluded from the payload when None via the
    # route's response_model_exclude_none.
    session_id: str | None = None


class GatewayEventPayload(BaseModel):
    sequence: int
    event_type: str
    payload: dict[str, Any]
    time: str


class SessionEventsResponse(BaseModel):
    session_id: str
    events: list[GatewayEventPayload]
    next_sequence: int
    # T-30: oldest sequence still retained by the live event ring (0 when the
    # buffer is empty, or on the history-replay branch which has no ring). A gap
    # occurred iff base_sequence > after + 1, where `after` is the cursor the
    # client sent — this lets a reconnecting client detect it read past the
    # 2000-entry trim boundary instead of silently rendering a truncated stream.
    base_sequence: int


class SessionCloseResponse(BaseModel):
    session_id: str
    closed: bool


class SessionInterruptResponse(BaseModel):
    """Response for POST /sessions/{session_id}/interrupt (P1-4)."""

    interrupted: bool


class RuntimeEventsResponseItem(BaseModel):
    model_config = ConfigDict(extra="allow")


class ConfigPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProviderRequest(BaseModel):
    provider: dict[str, Any]


class ProviderUpsertRequest(BaseModel):
    """Body for POST /config/providers (add) and PUT /config/providers/{name} (update)."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None  # present for POST; absent for PUT (name in path)
    kind: str = "anthropic"
    model: str = ""
    base_url: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    api_key: str | None = None


class RoutingUpdateRequest(BaseModel):
    """Body for PUT /config/routing."""

    model_config = ConfigDict(extra="ignore")

    default_model: str
    capabilities: dict[str, list[str]] = Field(default_factory=dict)


class VoiceSpeakBody(BaseModel):
    """Body for POST /voice/speak."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(max_length=8000)
    voice: str | None = None
    model: str | None = None
    format: str = "mp3"
    provider: Literal["openai", "elevenlabs"] | None = None


class PermissionsRequest(BaseModel):
    permissions: dict[str, Any]


class MemoryRequest(BaseModel):
    memory: dict[str, Any]


class MemoryFactAddRequest(BaseModel):
    """Body for POST /memory/facts."""

    group: str  # "core_memory" | "user_profile" | "agent_profile"
    text: str


class MemoryFactDeleteResponse(BaseModel):
    """Response for DELETE /memory/facts/{fact_id}."""

    ok: bool


class MemoryGardenerProposalModel(BaseModel):
    """One MemoryGardener ``pending_review`` proposal (T-08 A2).

    Field set mirrors ``hachi.core.memory.gardener.GardeningProposal`` exactly —
    the dataclass that ``AgentSession.snapshot_gardener_diffs()`` serialises via
    ``dataclasses.asdict``.
    """

    id: str
    kind: str
    name_a: str
    name_b: str = ""
    reason: str = ""
    confidence: float = 1.0
    file_path_a: str = ""
    file_path_b: str = ""
    auto_applied: bool = False


class MemoryGardenerDiffsResponse(BaseModel):
    """Response for GET /memory/diffs (T-08 A2)."""

    pending_review: list[MemoryGardenerProposalModel] = Field(default_factory=list)


class MemoryGardenerDecisionRequest(BaseModel):
    """Body for POST /memory/diffs/{proposal_id} (T-08 A3)."""

    action: Literal["approve", "reject"]


class MemoryGardenerDecisionResponse(BaseModel):
    """Response for POST /memory/diffs/{proposal_id} (T-08 A3)."""

    ok: bool
    id: str
    action: str
    proposal: MemoryGardenerProposalModel


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = None  # string or list of content blocks


class OpenAIChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    messages: list[OpenAIMessage]
    model: str | None = None
    stream: bool = False
    user: str | None = None  # OpenAI's stable end-user id; keys the Hachi session (T-32)


class ChannelInboundRequest(BaseModel):
    sender_id: str
    text: str
    sender_name: str = ""


class ScheduleToggleRequest(BaseModel):
    enabled: bool


class CapabilityToggleRequest(BaseModel):
    """Body for PUT /capabilities/{id}/enabled."""

    enabled: bool


# G1 — new request models


class LimitsRequest(BaseModel):
    """Body for PUT /config/limits (G1-T3)."""

    per_task_cap_usd: float | None = None
    daily_cap_usd: float | None = None


class AllowedFolderRequest(BaseModel):
    """Body for POST /config/permissions/folders (G1-T3)."""

    path: str


class AllowedCommandRequest(BaseModel):
    """Body for POST /config/permissions/commands (G1-T3)."""

    command: str


class AlwaysAllowRequest(BaseModel):
    """Body for POST /sessions/{id}/permissions/always-allow (G1-T4)."""

    request: str
    action: str


class SecretStoreRequest(BaseModel):
    """Body for POST /runtime/secrets."""

    name: str = Field(..., pattern=r"^[\w.\-]+$")
    value: str = Field(..., min_length=1)


class McpRegisterRequest(BaseModel):
    """Body for POST /runtime/mcp/register (G1-T5 / CONTRACT-MCP).

    Supports three transport kinds:
      - ``stdio`` (default): requires ``command`` + optional ``args`` / ``env_vars``.
      - ``sse``:             requires ``url`` + optional ``headers`` / ``env_vars``.
      - ``http``:            requires ``url`` + optional ``headers`` / ``env_vars``.
    """

    name: str
    transport: str = "stdio"
    # stdio fields
    command: str = ""
    args: str = ""
    env_vars: dict[str, str] = Field(default_factory=dict)
    # sse / http fields
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class SkillAddRequest(BaseModel):
    """Body for POST /skills/add (G1-T6)."""

    kennel_id: str


class OgNodeModel(BaseModel):
    """One ObjectGraph node with per-pass token estimates (GET /og/graph).

    Field set derived from ``og_graph_from_engine`` (``gateway/orchestrator.py``),
    the function that actually builds this payload -- not from the frontend's
    hand-written ``GatewayOgNode`` mirror, which T-38 found already drifted.
    """

    id: str
    name: str
    kind: str
    index: int
    dense: int
    full: int
    deps: list[str] = []


class OgGraphResponse(BaseModel):
    """Response for ``GET /og/graph`` (T-38 / BB-115).

    ``count`` defaults to 0 because ``GatewayControlPlane.og_graph()``
    (``gateway/control_plane.py``) falls back to ``{"nodes": []}`` -- no ``count``
    key -- when no ``get_og_graph`` handler is wired. The real payload built by
    ``og_graph_from_engine`` always includes ``count``; the default only tolerates
    the no-handler fallback shape without misdeclaring the field's type.
    """

    nodes: list[OgNodeModel]
    count: int = 0


class AuthoringGenerateRequest(BaseModel):
    """Body for POST /authoring/targets/{name}/generate (G1-T2 / CRIT-002)."""

    prompt: str


class AuthoringSourceSaveRequest(BaseModel):
    """Body for POST /authoring/targets/{name}/source (CONTRACT-AUTHORING-SAVE)."""

    content: str


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


# ---------------------------------------------------------------------------
# Eval loop — interaction read/rating models (E1)
# ---------------------------------------------------------------------------


class InteractionRecord(BaseModel):
    """One row from ``interaction_log`` — the per-turn eval ground truth.

    ``tool_calls`` and ``memory_hits`` are decoded JSON arrays (lists of strings).
    Numeric fields are optional because they may be NULL for older rows.
    """

    id: str
    session_id: str
    turn_id: str | None = None
    ts: str
    user_input: str | None = None
    agent_output: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
    memory_hits: list[str] = Field(default_factory=list)
    model_used: str | None = None
    token_cost: float | None = None
    tokens: int | None = None
    rating: str | None = None
    notes: str | None = None


class InteractionListResponse(BaseModel):
    """Response for ``GET /interactions``."""

    interactions: list[InteractionRecord]


class RatingRequest(BaseModel):
    """Body for ``PUT /interactions/{interaction_id}/rating``."""

    rating: Literal["up", "down"]
    notes: str | None = None


class RatingResponse(BaseModel):
    """Response for ``PUT /interactions/{interaction_id}/rating``."""

    ok: bool


_GATEWAY_THREAD_LIMITER_ENV = "HACHI_GATEWAY_THREAD_LIMITER"
_DEFAULT_THREAD_LIMITER = 64


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # B6: raise the AnyIO default threadpool limit (40) to accommodate concurrent
    # blocking REST reads (session list, message history, event history) plus
    # in-flight per-turn send_message threads without queuing. Configurable via
    # HACHI_GATEWAY_THREAD_LIMITER; intentionally separate from the session-object
    # cap (HACHI_GATEWAY_MAX_SESSIONS) which governs agent lifetime, not I/O threads.
    _thread_cap = int(os.environ.get(_GATEWAY_THREAD_LIMITER_ENV, str(_DEFAULT_THREAD_LIMITER)))
    anyio.to_thread.current_default_thread_limiter().total_tokens = _thread_cap
    yield


def _error_response(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message, details=details).model_dump(mode="json"),
    )


def _resolve_session_store() -> Any:
    """Return the process-wide ``SessionStore`` if one has been booted, else ``None``.

    Mirrors the ``ProcessSharedServices`` access pattern used elsewhere in this module so
    durable ingress idempotency (AUDIT P0-3) reads/writes the same database the rest of the
    gateway uses. Patched in tests to point at an isolated DB.

    T-10 / BB-42 recurrence: this used to fall back to a brand-new ``SessionStore()``
    whenever ``ProcessSharedServices.session_store`` was unset — which, since the
    attribute was never declared (and nothing ever assigned it), was EVERY call on
    this per-message hot path. That fallback ran full schema init/migrations and was
    never closed, leaking a sqlite3/WAL connection per message for the life of the
    process. All three current callers (``post_message`` idempotency check,
    ``get_interactions``, ``put_interaction_rating``) already treat ``None`` as
    "durable idempotency/interactions unavailable for this request" — so once a real
    shared store exists (attached by ``boot_agent``) that already-handled ``None``
    path is preferable to silently opening-and-abandoning a private one here.
    """
    try:
        from hachi.gateway.shared_services import ProcessSharedServices

        shared = ProcessSharedServices.instance()
        store = shared.session_store if shared is not None else None
        if store is not None:
            return store
        return None
    except Exception:
        # T-10: never break the turn — but never fail silently either. A caller
        # degrading to "no durable idempotency for this request" should be visible.
        _log.warning(
            "_resolve_session_store: could not resolve a SessionStore; durable ingress "
            "idempotency is disabled for this request",
            exc_info=True,
        )
        return None


# T-37 (BB-114): the gateway's error envelope (_error_response -> ErrorResponse) is
# returned by 7 exception handlers (8 call sites -- one handler emits either a
# provider-passthrough status or 503) and 21 in-route returns:
# 400/401/403/404/409/410/429/500/503. 422 is deliberately excluded -- FastAPI
# already documents it as HTTPValidationError via the RequestValidationError
# handler, and some in-route 422s use the envelope while others are plain
# validation failures, so declaring 422 here would collide with FastAPI's own
# auto-generated schema for that code (see T-37 task notes). FastAPI only records
# a model in components.schemas when some route DECLARES it -- this shared mapping
# is what makes the envelope part of the published contract. Declared once per
# router (not per route) so the declaration can't drift route-by-route.
#
# T-47: hoisted to module scope (was a local inside create_gateway_app) so the
# module-scope routers in hachi.gateway.routers can reference it in their own
# router constructors (``responses=_ERROR_RESPONSES``) at import time -- a
# router module executes before create_gateway_app is ever called.
_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Invalid request"},
    401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"},
    403: {
        "model": ErrorResponse,
        "description": "Caller is not permitted to perform this action",
    },
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {
        "model": ErrorResponse,
        "description": "Conflict with the resource's current state",
    },
    410: {
        "model": ErrorResponse,
        "description": "The session existed but is no longer active",
    },
    429: {"model": ErrorResponse, "description": "Rate limited by the upstream AI provider"},
    500: {"model": ErrorResponse, "description": "Unhandled server error"},
    503: {"model": ErrorResponse, "description": "Runtime or upstream provider unavailable"},
}
# `system` is the only router carrying fully-public routes (/, /health,
# /readiness) -- its other three routes opt into auth per-route rather than via
# router-level `dependencies=`. Declaring 401 here would wrongly claim /health can
# return an auth error, which it structurally cannot.
_SYSTEM_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: response for code, response in _ERROR_RESPONSES.items() if code != 401
}


def create_gateway_app(
    control_plane: GatewayControlPlane,
    auth_token: str | None = None,
    *,
    bind_host: str | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    """Create the Hachi gateway FastAPI application.

    Args:
        control_plane: The :class:`GatewayControlPlane` that backs all routes.
        auth_token: Bearer token required on all protected routes.  When *None*
            the token is still enforced unless the opt-out env var
            ``HACHI_GATEWAY_NO_AUTH=1`` is set (power-user escape hatch).
        bind_host: The host address the server will bind to.  When supplied the
            function enforces the loopback-only constraint for NO_AUTH mode and
            the hard-error for non-loopback binds without a token.  When *None*
            (default, used by unit-test helpers and the OpenAPI export command)
            these startup guards are skipped.
        frontend_dist: Path to the pre-built frontend ``dist`` directory.  When
            provided, the directory is mounted as a static files handler at the
            root ``/`` route after all API routes (CRIT-008 / ``hachi serve``).
            When *None* (default) no static files are mounted.

    Auth posture (UC-AUTH-001/ADV-014):
        All protected routes require ``Authorization: Bearer <token>``.
        Loopback binding does NOT exempt authentication — a localhost-only
        daemon is still reachable by any process on the same machine.
        The opt-out env flag ``HACHI_GATEWAY_NO_AUTH=1`` disables the
        check **only when the bind address is a loopback** (E8/T8.2).  Using
        NO_AUTH on a non-loopback host is a startup error.

    CORS posture (UC-AUTH-003):
        Allow-list is limited to local dev origins (localhost:5173/4173) or
        the caller-provided ``HACHI_CORS_ORIGINS`` env var.
        ``allow_origins=["*"]`` is intentionally NOT used together with
        credentials / auth headers — that combination is a security anti-pattern.
    """
    # Determine effective auth posture.
    _no_auth_override = os.environ.get("HACHI_GATEWAY_NO_AUTH", "").strip() == "1"

    # E8/T8.2 — Guard 1: NO_AUTH is only allowed on loopback.
    if _no_auth_override and bind_host is not None and not _is_loopback_host(bind_host):
        raise ValueError(
            f"HACHI_GATEWAY_NO_AUTH=1 is not permitted when binding to a non-loopback "
            f"host ('{bind_host}'). Remove the env var or bind to 127.0.0.1/::1/localhost."
        )

    # E8/T8.2 — Guard 2: non-loopback bind without a token is a hard startup error.
    if bind_host is not None and not _is_loopback_host(bind_host) and not auth_token:
        raise ValueError(
            f"Gateway cannot start on non-loopback host '{bind_host}' without an auth token. "
            "Set HACHI_GATEWAY_API_TOKEN (or HACHI_GATEWAY_API_TOKEN_FILE) before starting."
        )

    # CRIT-001 — Guard 3: fail-closed when a bind_host is known but no token was
    # configured AND no-auth override is absent.  Unit tests that call
    # create_gateway_app(cp) without a bind_host keep working (bind_host=None skips
    # this check); the gateway-openapi export path passes bind_host=None explicitly.
    if (
        bind_host is not None
        and _is_loopback_host(bind_host)
        and not auth_token
        and not _no_auth_override
    ):
        raise ValueError(
            "Gateway cannot start on loopback without an auth token and no "
            "HACHI_GATEWAY_NO_AUTH=1 override. "
            "The CLI always generates a token; this error indicates a misconfiguration."
        )

    _auth_enabled = not _no_auth_override
    if _no_auth_override:
        _log.warning(
            "HACHI_GATEWAY_NO_AUTH=1 is set — "
            "the gateway control plane is running WITHOUT authentication. "
            "Do NOT expose this gateway on a network-accessible interface."
        )

    app = FastAPI(title="Hachi Gateway", version="1.0.0", lifespan=_lifespan)

    # T-47: collaborators live on `app.state` (per-app), not a module global or a
    # closure variable, so routers can be declared at module scope (hachi.gateway.routers)
    # and read them back per-request via hachi.gateway.deps' Depends providers. Two apps
    # built in the same process (routine in the test suite) never share these.
    app.state.control_plane = control_plane
    app.state.auth_token = auth_token
    app.state.auth_enabled = _auth_enabled
    # T-47: these used to be closure locals declared right before the router
    # that reads them (openai_compat / channels_router), freshly constructed
    # on every create_gateway_app() call -- kept identical by living on
    # app.state instead: still rebuilt on every call, still per-app, and
    # `RateLimiter` is still resolved from *this* module's globals at call
    # time so a test that monkeypatches `hachi.gateway.fastapi_app.RateLimiter`
    # before calling create_gateway_app is still honoured.
    app.state.openai_limiter = RateLimiter(_int_env("HACHI_OPENAI_RATE_LIMIT", 60), 60.0)
    app.state.channel_limiter = RateLimiter(_int_env("HACHI_CHANNEL_RATE_LIMIT", 30), 60.0)
    app.state.channel_allowlist = _parse_allowlist(os.environ.get("HACHI_CHANNEL_ALLOWLIST"))

    _raw_origins = os.environ.get("HACHI_CORS_ORIGINS", "")
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
    # Phase 4: request-tracing middleware (pure ASGI, after CORS, before auth).
    # No-op when tracing is inactive; adds `traceparent` response header only —
    # zero OpenAPI schema change (verified: run `hachi gateway-openapi`).
    from hachi.gateway.middleware.trace import TraceMiddleware

    app.add_middleware(TraceMiddleware)

    # T-47: _AuthRequired / _require_auth used to be nested here, closing over
    # `auth_token` / `_auth_enabled`. Both now live in hachi.gateway.deps as
    # AuthRequired / require_auth, reading the equivalent state from `app.state`
    # (populated above) instead of a closure -- see deps.py for the transcribed,
    # behaviour-preserving auth logic (all four documented modes).
    @app.exception_handler(AuthRequired)
    async def _auth_required_handler(_request: Request, _exc: AuthRequired) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthorized",
            message="Missing or invalid bearer token",
        )

    # Issue 1: SessionExpiredError must be registered BEFORE GatewayProtocolError
    # (it is a subclass).  HTTP 410 Gone tells the frontend the session existed but
    # is no longer active, and the client should create a new session.
    @app.exception_handler(SessionExpiredError)
    async def _session_expired_handler(_request: Request, exc: SessionExpiredError) -> JSONResponse:
        return _error_response(
            status_code=410,
            code=exc.code,
            message=str(exc),
            details={"session_id": exc.session_id},
        )

    @app.exception_handler(GatewayProtocolError)
    async def _protocol_error_handler(_request: Request, exc: GatewayProtocolError) -> JSONResponse:
        return _error_response(
            status_code=400,
            code=getattr(exc, "code", "protocol_error"),
            message=str(exc),
        )

    @app.exception_handler(GatewayUnavailableError)
    async def _unavailable_error_handler(
        _request: Request, exc: GatewayUnavailableError
    ) -> JSONResponse:
        return _error_response(status_code=503, code="service_unavailable", message=str(exc))

    # Provider (LLM) errors — surface the upstream provider's actionable message
    # (e.g. "credit balance too low", auth failure, rate limit) instead of masking
    # it as an opaque 500 internal_error. Only the provider's user-facing message
    # is exposed — never the stack trace or API key. Registered against
    # anthropic.APIStatusError; its subclasses (BadRequestError / AuthenticationError
    # / RateLimitError / ...) are matched via the exception MRO. Guarded import so
    # the gateway still starts if the SDK is absent (e.g. non-runtime mode).
    _anthropic: Any = None
    try:
        import anthropic

        _anthropic = anthropic
    except Exception:  # pragma: no cover - anthropic is present in runtime mode
        pass

    if _anthropic is not None:

        @app.exception_handler(_anthropic.APIStatusError)
        async def _provider_error_handler(_request: Request, exc: Any) -> JSONResponse:
            status = getattr(exc, "status_code", None) or 400
            message: str | None = None
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict) and isinstance(err.get("message"), str):
                    message = err["message"]
            if not message:
                message = getattr(exc, "message", None) or "AI provider request failed."
            # Upstream 5xx / overloaded → transient: report as unavailable so clients
            # may retry. Upstream 4xx (billing / auth / validation / rate-limit) →
            # surface the actionable message under a stable provider_error code so the
            # UI can show it (and the user can fix billing / key / model).
            if status in (500, 502, 503, 504, 529):
                return _error_response(
                    status_code=503,
                    code="provider_unavailable",
                    message=f"AI provider temporarily unavailable: {message}",
                )
            client_status = status if status in (400, 401, 403, 404, 409, 422, 429) else 400
            return _error_response(
                status_code=client_status,
                code="provider_error",
                message=message,
            )

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

    # T-47: routers lifted to module scope (hachi.gateway.routers) are assembled
    # here; local import avoids a module-level import cycle (fastapi_app <-> routers
    # <-> fastapi_app) since routers import Pydantic models and _ERROR_RESPONSES back
    # from this module -- deferring the import to call time means this module has
    # always finished initializing by the time it runs.
    from hachi.gateway.routers import ALL_ROUTERS

    for _extracted_router in ALL_ROUTERS:
        app.include_router(_extracted_router)

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

    # CRIT-008 — Mount the pre-built frontend as a static SPA AFTER all API routes
    # so that API paths always win.  The catch-all ``html=True`` makes
    # ``/anything/deep`` return ``index.html`` for client-side routing.
    if frontend_dist is not None:
        _dist = Path(frontend_dist)
        if _dist.is_dir():
            from starlette.staticfiles import StaticFiles

            app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
            _log.info("Frontend mounted from %s", _dist)
        else:
            _log.warning("frontend_dist '%s' is not a directory — skipping static mount", _dist)

    return app
