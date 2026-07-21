"""``GET /runtime/events`` -- one of the 7 routers the former ``runtime_ops``
splits into (T-47 Sec 3.3: its 17 routes spanned five unrelated concerns).

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


@router.get("/runtime/events")
async def get_runtime_events(
    cp: ControlPlaneDep,
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> Any:
    return cp.runtime_events(session_id=session_id, limit=limit)
