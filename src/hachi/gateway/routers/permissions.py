"""``GET /permissions/audit`` -- one of the 7 routers the former ``runtime_ops``
splits into (T-47 Sec 3.3).

No ``prefix=`` -- ``runtime_ops`` carried none, so this (and its 6 siblings)
must not gain one either, or every route under it silently re-paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES

router = APIRouter(
    tags=["runtime"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("/permissions/audit")
async def get_permission_audit(
    cp: ControlPlaneDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    return cp.permission_audit(limit)
