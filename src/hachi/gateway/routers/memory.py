"""``/memory/*`` -- facts, recall, and MemoryGardener review (T-47)."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends, Query

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import (
    _ERROR_RESPONSES,
    MemoryFactAddRequest,
    MemoryGardenerDecisionRequest,
    MemoryGardenerDecisionResponse,
    MemoryGardenerDiffsResponse,
)

router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("")
async def get_memory(cp: ControlPlaneDep) -> Any:
    """Return grouped memory facts, blocked items, and proposed changes."""
    return cp.memory()


@router.get("/recall")
async def get_memory_recall(
    cp: ControlPlaneDep,
    query: str = Query(..., min_length=1),
    top_k: int = Query(default=8, ge=1, le=50),
) -> Any:
    """Return semantic memory recall hits for the given query."""
    return cp.memory_recall(query=query, top_k=top_k)


@router.post("/facts")
async def post_memory_fact(payload: MemoryFactAddRequest, cp: ControlPlaneDep) -> Any:
    """Add a fact to a vault collection.

    Body: ``{"group": "user_profile", "text": "..."}``
    Returns: ``{"id": "<fact_id>"}``
    """
    return cp.add_memory_fact(group=payload.group, text=payload.text)


@router.delete("/facts/{fact_id}")
async def delete_memory_fact(fact_id: str, cp: ControlPlaneDep) -> Any:
    """Delete a fact from the vault by its content-hash id.

    Returns: ``{"ok": true}``
    """
    return cp.delete_memory_fact(fact_id=fact_id)


# T-08 A2 — GET /memory/diffs: MemoryGardener pending_review proposals.
@router.get("/diffs", response_model=MemoryGardenerDiffsResponse)
async def get_memory_diffs(cp: ControlPlaneDep) -> Any:
    """Return pending MemoryGardener review proposals from the last gardener run."""
    return cp.memory_gardener_diffs()


# T-08 A3 — POST /memory/diffs/{proposal_id}: approve/reject write-back.
@router.post("/diffs/{proposal_id}", response_model=MemoryGardenerDecisionResponse)
async def post_memory_diff_decision(
    proposal_id: str, payload: MemoryGardenerDecisionRequest, cp: ControlPlaneDep
) -> Any:
    """Approve or reject a pending MemoryGardener proposal.

    Body: ``{"action": "approve"|"reject"}``. Approve applies the proposal via
    an append-only ``::changelog`` note (never overwrites node content — the
    gardener's governance rule); reject drops it without touching any ``.og``
    file. Both persist the updated ``pending_diffs.json``. Raises 404 when
    ``proposal_id`` is not currently pending.
    """
    return await anyio.to_thread.run_sync(
        lambda: cp.decide_memory_gardener_diff(proposal_id, payload.action)
    )
