"""``/runtime/secrets`` -- credential manager (BB-9). One of the 7 routers the
former ``runtime_ops`` splits into (T-47 Sec 3.3).

Reserved namespace: ``cred:<name>`` (system keys are never listed/deleted).
No ``prefix=`` -- ``runtime_ops`` carried none, so this (and its 6 siblings)
must not gain one either, or every route under it silently re-paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, SecretStoreRequest

router = APIRouter(
    tags=["runtime"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.post("/runtime/secrets")
async def post_secret(payload: SecretStoreRequest, cp: ControlPlaneDep) -> Any:
    """Store a user-managed credential as ``cred:<name>`` in the secret store."""
    return cp.store_secret(payload.name, payload.value)


@router.get("/runtime/secrets")
async def get_secrets(cp: ControlPlaneDep) -> Any:
    """List user-managed credential names (``cred:`` prefix stripped, values never returned)."""
    return cp.list_secret_names()


@router.delete("/runtime/secrets/{name}")
async def delete_secret(name: str, cp: ControlPlaneDep) -> Any:
    """Delete ``cred:<name>`` (idempotent; system keys are silently ignored)."""
    return cp.delete_secret(name)
