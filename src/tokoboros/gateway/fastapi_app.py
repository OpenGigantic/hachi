from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tokoboros.gateway.control_plane import (
    PROTOCOL_VERSION,
    GatewayControlPlane,
    GatewayProtocolError,
    GatewayUnavailableError,
)


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


class SetupApplyRequest(BaseModel):
    plan: dict[str, Any]


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
    app = FastAPI(title="Tokoboros Gateway", version="1.0.0", lifespan=_lifespan)

    _raw_origins = os.environ.get("TOKOBOROS_CORS_ORIGINS", "")
    _cors_origins: list[str] = (
        [o.strip() for o in _raw_origins.split(",") if o.strip()]
        if _raw_origins.strip()
        else ["http://localhost:5173", "http://localhost:4173"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    def _require_auth(request: Request) -> None:
        if not auth_token:
            return
        expected = f"Bearer {auth_token}"
        received = request.headers.get("Authorization", "")
        if not secrets.compare_digest(received.strip(), expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid bearer token",
            )

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

    protected_dependencies = [Depends(_require_auth)] if auth_token else []

    system = APIRouter(tags=["system"])
    sessions = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=protected_dependencies)
    runtime_ops = APIRouter(tags=["runtime"], dependencies=protected_dependencies)
    config = APIRouter(prefix="/config", tags=["config"], dependencies=protected_dependencies)
    setup = APIRouter(prefix="/setup", tags=["setup"], dependencies=protected_dependencies)

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
        return control_plane.create_session(
            client_id=payload.client_id,
            surface=payload.surface,
            capabilities=payload.capabilities,
            protocol_version=payload.protocol_version,
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

    app.include_router(system)
    app.include_router(sessions)
    app.include_router(runtime_ops)
    app.include_router(config)
    app.include_router(setup)
    return app
