"""``/``, ``/health``, ``/readiness``, ``/capabilities``, ``/models`` -- system
routes (T-47).

The only router carrying fully-public routes (``/``, ``/health``,
``/readiness``) -- its other three routes opt into auth per-route rather than
via router-level ``dependencies=``, so this router itself carries none.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from hachi.gateway.deps import CallerHash, ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import (
    _SYSTEM_ERROR_RESPONSES,
    CapabilityToggleRequest,
    HealthResponse,
    ReadinessResponse,
)

router = APIRouter(tags=["system"], responses=_SYSTEM_ERROR_RESPONSES)


@router.get("/")
async def get_root() -> Any:
    return {
        "service": "Hachi Gateway",
        "version": "1.0.0",
        "health": "/health",
        "readiness": "/readiness",
        "docs": "/docs",
    }


@router.get("/health", response_model=HealthResponse)
async def get_health(cp: ControlPlaneDep) -> Any:
    return cp.health()


@router.get("/readiness", response_model=ReadinessResponse)
async def get_readiness(cp: ControlPlaneDep) -> JSONResponse:
    readiness = cp.readiness()
    status = 200 if readiness["ready"] else 503
    return JSONResponse(status_code=status, content=readiness)


@router.get("/capabilities", dependencies=[Depends(require_auth)])
async def get_capabilities(cp: ControlPlaneDep) -> Any:
    return cp.capabilities()


@router.get("/models", dependencies=[Depends(require_auth)])
async def get_models(cp: ControlPlaneDep) -> Any:
    return cp.models()


@router.put("/capabilities/{cap_id}/enabled")
async def put_capability_enabled(
    cap_id: str,
    payload: CapabilityToggleRequest,
    cp: ControlPlaneDep,
    _auth: CallerHash,
) -> Any:
    """Toggle a capability on/off and persist to global config."""
    return cp.set_capability_enabled(cap_id, payload.enabled)
