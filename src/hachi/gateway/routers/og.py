"""``GET /og/graph`` -- the ObjectGraph node graph with per-pass token estimates (T-47)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, OgGraphResponse

router = APIRouter(
    prefix="/og",
    tags=["og"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


# response_model_exclude_unset=True: the no-handler fallback
# (control_plane.og_graph() -> {"nodes": []}, gateway/control_plane.py) has no
# "count" key at all. Without exclude_unset, FastAPI would fill the declared
# default (count=0) into that response and change its wire shape. With it, a
# key is only emitted when the underlying dict actually set it -- so the real
# og_graph_from_engine payload (which always sets "count" explicitly) keeps
# reporting it, and the fallback stays exactly {"nodes": []}.
@router.get("/graph", response_model=OgGraphResponse, response_model_exclude_unset=True)
async def get_og_graph(cp: ControlPlaneDep) -> Any:
    """Return the ObjectGraph node graph with per-pass token estimates."""
    return cp.og_graph()
