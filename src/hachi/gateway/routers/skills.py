"""``/skills/*`` -- .og skill inventory + kennel install (T-47)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, SkillAddRequest

router = APIRouter(
    tags=["skills"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("/skills")
async def get_skills(cp: ControlPlaneDep) -> Any:
    """Return the .og skill inventory list."""
    return cp.skills()


# G1-T6 — GET /skills/kennel (available skills to install)
@router.get("/skills/kennel")
async def get_kennel(cp: ControlPlaneDep) -> Any:
    """Return the available skills catalogue (kennel) for browsing and installation."""
    return cp.kennel()


# G1-T6 — POST /skills/add (install a skill)
@router.post("/skills/add")
async def post_skill_add(payload: SkillAddRequest, cp: ControlPlaneDep) -> Any:
    """Install a skill from the kennel by id.

    Body: ``{"kennel_id": "..."}``
    """
    return cp.add_skill(payload.kennel_id)
