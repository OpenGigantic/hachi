"""``/interactions/*`` -- eval-loop interaction read/rating routes (E1, T-47)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query

from hachi.gateway import fastapi_app as _fastapi_app
from hachi.gateway.deps import CallerHash, ControlPlaneDep, check_session_ownership, require_auth
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
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> InteractionListResponse:
    """Return interaction_log rows newest-first, optionally scoped to one session.

    Ownership rules (mirrors ``list_sessions``):
    - If ``session_id`` is supplied: ownership-check that session before querying.
    - If not supplied: return rows across all sessions owned by the caller
      (legacy NULL-owner sessions included for back-compat); no-auth returns all.

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

    if session_id is not None:
        # Ownership-check before querying to avoid leaking row existence.
        check_session_ownership(cp, session_id, caller_hash)
        rows = store.get_interactions(session_id=session_id, limit=limit)
    else:
        # Pull all rows then filter to owned sessions.
        rows = store.get_interactions(session_id=None, limit=limit)
        if caller_hash is not None:

            def _owned_session(sid: str) -> bool:
                owner = cp.get_session_owner(sid)
                return owner is None or secrets.compare_digest(owner, caller_hash)

            rows = [r for r in rows if _owned_session(r.get("session_id") or "")]

    records = [InteractionRecord(**row) for row in rows]
    return InteractionListResponse(interactions=records)


@router.put("/{interaction_id}/rating", response_model=RatingResponse)
async def put_interaction_rating(
    interaction_id: str,
    payload: RatingRequest,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
) -> RatingResponse:
    """Set the thumbs-up / thumbs-down rating (and optional notes) for a turn.

    Ownership check: looks up the row's ``session_id`` first, then verifies the
    caller owns that session before allowing the write.

    Returns 404 if ``interaction_id`` does not exist.
    """
    store = _fastapi_app._resolve_session_store()
    if store is None:
        raise HTTPException(status_code=503, detail="session store unavailable")

    # Look up the row to discover its session_id for the ownership check.
    existing = store.get_interactions(session_id=None, limit=10_000)
    row = next((r for r in existing if r.get("id") == interaction_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Interaction {interaction_id!r} not found")

    # Ownership-check the owning session before mutating.
    row_session_id: str = row.get("session_id") or ""
    if row_session_id:
        check_session_ownership(cp, row_session_id, caller_hash)

    updated = store.set_interaction_rating(
        interaction_id, rating=payload.rating, notes=payload.notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Interaction {interaction_id!r} not found")
    return RatingResponse(ok=True)
