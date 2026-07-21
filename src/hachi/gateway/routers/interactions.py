"""``/interactions/*`` -- eval-loop interaction read/rating routes (E1, T-47)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from hachi.gateway import fastapi_app as _fastapi_app
from hachi.gateway.deps import require_auth
from hachi.gateway.fastapi_app import (
    _ERROR_RESPONSES,
    InteractionListResponse,
    InteractionRecord,
    RatingRequest,
    RatingResponse,
)

router = APIRouter(
    prefix="/interactions",
    tags=["interactions"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("", response_model=InteractionListResponse)
async def get_interactions(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> InteractionListResponse:
    """Return interaction_log rows newest-first, optionally scoped to one session.

    ADR-0006 (T-61): there is no per-caller ownership filter — every
    authenticated caller sees every row, exactly as legacy (ownerless) rows
    always did.

    Args:
        session_id: Optional session filter.
        limit: Maximum rows to return (1–500; default 100).
    """
    # T-47: qualified module access (not `from ... import _resolve_session_store`) --
    # several tests patch `hachi.gateway.fastapi_app._resolve_session_store` and rely
    # on that patch being visible to whoever calls it at request time.
    store = _fastapi_app._resolve_session_store()
    if store is None:
        raise HTTPException(status_code=503, detail="session store unavailable")

    rows = store.get_interactions(session_id=session_id, limit=limit)

    records = [InteractionRecord(**row) for row in rows]
    return InteractionListResponse(interactions=records)


@router.put("/{interaction_id}/rating", response_model=RatingResponse)
async def put_interaction_rating(
    interaction_id: str,
    payload: RatingRequest,
) -> RatingResponse:
    """Set the thumbs-up / thumbs-down rating (and optional notes) for a turn.

    Returns 404 if ``interaction_id`` does not exist.
    """
    store = _fastapi_app._resolve_session_store()
    if store is None:
        raise HTTPException(status_code=503, detail="session store unavailable")

    updated = store.set_interaction_rating(
        interaction_id, rating=payload.rating, notes=payload.notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Interaction {interaction_id!r} not found")
    return RatingResponse(ok=True)
