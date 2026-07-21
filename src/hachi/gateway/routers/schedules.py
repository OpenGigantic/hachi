"""``/schedules/*`` -- schedule listing + lifecycle. One of the 7 routers the
former ``runtime_ops`` splits into (T-47 Sec 3.3).

No ``prefix=`` -- ``runtime_ops`` carried none, so this (and its 6 siblings)
must not gain one either, or every route under it silently re-paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, ScheduleToggleRequest

router = APIRouter(
    tags=["runtime"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("/schedules")
async def get_schedules(cp: ControlPlaneDep) -> Any:
    return {"schedules": cp.list_schedules()}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, cp: ControlPlaneDep) -> Any:
    return cp.cancel_schedule(schedule_id)


@router.put("/schedules/{schedule_id}/enabled")
async def put_schedule_enabled(
    schedule_id: str, payload: ScheduleToggleRequest, cp: ControlPlaneDep
) -> Any:
    return cp.toggle_schedule(schedule_id, payload.enabled)


@router.post("/schedules/{schedule_id}/run-now")
async def post_schedule_run_now(schedule_id: str, cp: ControlPlaneDep) -> Any:
    """Force-trigger a schedule immediately, regardless of its next-run time.

    Dispatches in the background (does not block on the agent turn); returns
    ``{"ok": true, "schedule_id": ...}`` once dispatch is confirmed, or
    ``{"ok": false, ...}`` if the schedule id is unknown or scheduling is
    unavailable (no active session).
    """
    return cp.run_schedule_now(schedule_id)
