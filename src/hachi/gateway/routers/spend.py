"""``/spend*`` -- per-turn token/cost rows and the daily spend ledger (T-47)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES

router = APIRouter(
    tags=["spend"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("/spend")
async def get_spend(cp: ControlPlaneDep, session_id: str | None = Query(default=None)) -> Any:
    """Return per-turn token/cost rows, compaction events, and model totals."""
    return cp.spend(session_id=session_id)


@router.get("/spend/daily")
async def get_spend_daily(cp: ControlPlaneDep) -> Any:
    """Return today's accumulated spend from the daily ledger plus configured caps."""
    return cp.spend_daily()
