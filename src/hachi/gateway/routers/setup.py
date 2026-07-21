"""``/setup`` -- onboarding/setup-wizard state and apply (T-47)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, SetupApplyRequest

router = APIRouter(
    prefix="/setup",
    tags=["setup"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("")
async def get_setup(cp: ControlPlaneDep) -> Any:
    return cp.setup_state()


@router.post("/apply")
async def post_setup_apply(payload: SetupApplyRequest, cp: ControlPlaneDep) -> Any:
    return cp.apply_setup(payload.model_dump(mode="python"))
