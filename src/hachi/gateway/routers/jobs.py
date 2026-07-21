"""``/jobs/*`` -- durable job-queue read models. One of the 7 routers the
former ``runtime_ops`` splits into (T-47 Sec 3.3).

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


@router.get("/jobs/active")
async def get_active_jobs(cp: ControlPlaneDep) -> Any:
    return cp.active_jobs()


@router.get("/jobs/failed")
async def get_failed_jobs(cp: ControlPlaneDep, limit: int = Query(default=20, ge=1, le=200)) -> Any:
    return cp.failed_jobs(limit)
