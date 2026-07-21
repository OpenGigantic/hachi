"""``/config`` -- provider, permissions, memory, limits, and routing admin routes.

Lifted out of ``create_gateway_app``'s closure (T-47). Handler bodies are
transcribed verbatim; the only change is replacing the closed-over
``control_plane`` with the ``ControlPlaneDep`` FastAPI dependency.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import (
    _ERROR_RESPONSES,
    AllowedCommandRequest,
    AllowedFolderRequest,
    LimitsRequest,
    MemoryRequest,
    PermissionsRequest,
    ProviderRequest,
    ProviderUpsertRequest,
    RoutingUpdateRequest,
)

router = APIRouter(
    prefix="/config",
    tags=["config"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("")
async def get_config(cp: ControlPlaneDep) -> Any:
    return cp.get_config()


@router.put("/provider")
async def put_provider(payload: ProviderRequest, cp: ControlPlaneDep) -> Any:
    return cp.update_provider(payload.model_dump(mode="python"))


@router.post("/provider/validate")
async def post_provider_validate(payload: ProviderRequest, cp: ControlPlaneDep) -> Any:
    return cp.validate_provider(payload.model_dump(mode="python"))


@router.put("/permissions")
async def put_permissions(payload: PermissionsRequest, cp: ControlPlaneDep) -> Any:
    return cp.update_permissions(payload.model_dump(mode="python"))


@router.put("/memory")
async def put_memory(payload: MemoryRequest, cp: ControlPlaneDep) -> Any:
    return cp.update_memory(payload.model_dump(mode="python"))


# G1-T3 — PUT /config/limits (spend caps)
@router.put("/limits")
async def put_limits(payload: LimitsRequest, cp: ControlPlaneDep) -> Any:
    """Persist per-task and daily spend caps.

    Body: ``{"per_task_cap_usd": 1.0, "daily_cap_usd": 5.0}``
    """
    return cp.update_limits(payload.model_dump(mode="python"))


# G1-T3 — POST /config/permissions/folders (add allowed folder)
@router.post("/permissions/folders")
async def post_allowed_folder(payload: AllowedFolderRequest, cp: ControlPlaneDep) -> Any:
    """Append a folder path to the allowed_paths list and persist to device-permissions.og."""
    return cp.add_allowed_folder(payload.path)


# G1-T3 — POST /config/permissions/commands (add allowed command)
@router.post("/permissions/commands")
async def post_allowed_command(payload: AllowedCommandRequest, cp: ControlPlaneDep) -> Any:
    """Append a command to the allowed_commands list and persist to device-permissions.og."""
    return cp.add_allowed_command(payload.command)


# T2b — DELETE /config/permissions/folders (remove allowed folder)
@router.delete("/permissions/folders")
async def delete_allowed_folder(payload: AllowedFolderRequest, cp: ControlPlaneDep) -> Any:
    """Remove a folder path from the allowed_paths list and persist to device-permissions.og."""
    return await anyio.to_thread.run_sync(lambda: cp.remove_allowed_folder(payload.path))


# T2b — DELETE /config/permissions/commands (remove allowed command)
@router.delete("/permissions/commands")
async def delete_allowed_command(payload: AllowedCommandRequest, cp: ControlPlaneDep) -> Any:
    """Remove a command from the allowed_commands list and persist to device-permissions.og."""
    return await anyio.to_thread.run_sync(lambda: cp.remove_allowed_command(payload.command))


# ---- Multi-provider + routing admin routes ----


@router.get("/providers")
async def get_providers(cp: ControlPlaneDep) -> Any:
    """Return all configured providers (secrets masked)."""
    return await anyio.to_thread.run_sync(lambda: cp.list_providers())


@router.post("/providers")
async def post_provider(payload: ProviderUpsertRequest, cp: ControlPlaneDep) -> Any:
    """Add a new named provider."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    import re as _re

    if not _re.match(r"^[A-Za-z0-9_-]+$", name):
        raise HTTPException(status_code=422, detail="invalid provider name")
    spec = payload.model_dump(mode="python", exclude={"name"}, exclude_none=True)
    return await anyio.to_thread.run_sync(lambda: cp.add_or_update_provider(name, spec))


@router.put("/providers/{name}")
async def put_provider_named(name: str, payload: ProviderUpsertRequest, cp: ControlPlaneDep) -> Any:
    """Update (or create) a named provider."""
    import re as _re

    if not _re.match(r"^[A-Za-z0-9_-]+$", name):
        raise HTTPException(status_code=422, detail="invalid provider name")
    spec = payload.model_dump(mode="python", exclude={"name"}, exclude_none=True)
    return await anyio.to_thread.run_sync(lambda: cp.add_or_update_provider(name, spec))


@router.delete("/providers/{name}")
async def delete_provider(name: str, cp: ControlPlaneDep) -> Any:
    """Remove a named provider."""
    import re as _re

    if not _re.match(r"^[A-Za-z0-9_-]+$", name):
        raise HTTPException(status_code=422, detail="invalid provider name")
    return await anyio.to_thread.run_sync(lambda: cp.remove_provider(name))


@router.post("/providers/{name}/test-connection")
async def post_provider_test(name: str, cp: ControlPlaneDep) -> Any:
    """Best-effort connectivity test for a provider."""
    import re as _re

    if not _re.match(r"^[A-Za-z0-9_-]+$", name):
        raise HTTPException(status_code=422, detail="invalid provider name")
    return await anyio.to_thread.run_sync(lambda: cp.test_provider_connection(name))


@router.post("/providers/probe")
async def post_provider_probe(payload: ProviderUpsertRequest, cp: ControlPlaneDep) -> Any:
    """Probe an unsaved provider spec: connectivity + available models.

    Returns ``{ok, detail, models}`` so the add/edit dialog can verify a key
    and populate a model picker before the provider is saved.
    """
    spec = payload.model_dump(mode="python", exclude_none=True)
    return await anyio.to_thread.run_sync(lambda: cp.probe_provider(spec))


@router.get("/routing")
async def get_routing(cp: ControlPlaneDep) -> Any:
    """Return the capability routing matrix (synthesized when absent)."""
    return await anyio.to_thread.run_sync(lambda: cp.get_routing())


@router.put("/routing")
async def put_routing(payload: RoutingUpdateRequest, cp: ControlPlaneDep) -> Any:
    """Persist the capability routing matrix (hot-reload — no restart)."""
    return await anyio.to_thread.run_sync(
        lambda: cp.set_routing(payload.default_model, payload.capabilities)
    )


@router.post("/routing/validate")
async def post_routing_validate(payload: RoutingUpdateRequest, cp: ControlPlaneDep) -> Any:
    """Validate a routing config without persisting it.

    Returns ``{"ok": true, "issues": [], "warnings": []}`` on success or
    ``{"ok": false, "issues": [...], "warnings": [...]}`` on failure.
    Always returns HTTP 200 — the ``ok`` field signals validity.
    """
    return await anyio.to_thread.run_sync(
        lambda: cp.validate_routing(payload.default_model, payload.capabilities)
    )


@router.post("/routing/rollback")
async def post_routing_rollback(cp: ControlPlaneDep) -> Any:
    """Restore the last-known-good routing config."""
    return await anyio.to_thread.run_sync(lambda: cp.rollback_routing())


@router.get("/routing/audit")
async def get_routing_audit(cp: ControlPlaneDep, limit: int = 50) -> Any:
    """Return recent routing-change audit records (newest first)."""
    return await anyio.to_thread.run_sync(lambda: cp.get_routing_audit(limit=limit))


@router.get("/models/all")
async def get_models_all(cp: ControlPlaneDep) -> Any:
    """Return model ids per configured provider."""
    return await anyio.to_thread.run_sync(lambda: cp.list_all_models())
