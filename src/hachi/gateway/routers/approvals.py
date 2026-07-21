"""``/approvals/*`` -- pending-permission inbox REST surface (UC-PERM-006, T-47).

Exposes the pending-permission queue so the web companion and other clients
can approve/deny agent actions. Both routes are protected by the same auth
dependency as all other routes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from hachi.gateway import fastapi_app as _fastapi_app
from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, ApprovalActionRequest

router = APIRouter(
    prefix="/approvals",
    tags=["approvals"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("")
async def get_approvals(cp: ControlPlaneDep) -> Any:
    """List all pending permission requests.

    Returns:
        A JSON object ``{"pending": [...]}`` with every unresolved request
        from the approval registry, exactly as
        ``PendingApprovalRegistry.list_pending()`` returns it (T-16):
        ``request_id``, ``operation``, ``tool``, ``target``, ``reason``,
        ``policy_summary``, ``created_at``, ``expires_at``. ``created_at``/
        ``expires_at`` are epoch milliseconds; ``expires_at`` is ``None``
        when the entry was parked without an enforced timeout. No field on
        this response is fabricated by the gateway (T-15).
    """
    # T-47: module-qualified access -- tests monkeypatch
    # hachi.gateway.fastapi_app._APPROVALS_AVAILABLE and rely on that patch
    # being visible here at handler-call time, not baked in at import time.
    if not _fastapi_app._APPROVALS_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Approvals module not yet available — Agent A module pending.",
        )
    return {"pending": cp.pending_approvals()}


@router.post("/{request_id}")
async def post_approval_decision(
    request_id: str,
    payload: ApprovalActionRequest,
    cp: ControlPlaneDep,
) -> Any:
    """Resolve a pending permission request.

    Args:
        request_id: The opaque ID from the pending-request list.
        payload: ``{"action": "approve"|"deny"|"allow_once", "rationale": "..."}``

    Returns:
        ``{"resolved": true}`` on success.

    Raises:
        404: When ``request_id`` is not found in the registry.
        422: When ``action`` is not one of ``approve``/``deny``/``allow_once``.
    """
    if not _fastapi_app._APPROVALS_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Approvals module not yet available — Agent A module pending.",
        )
    valid_actions = {"approve", "deny", "allow_once"}
    if payload.action not in valid_actions:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of: {', '.join(sorted(valid_actions))}",
        )
    resolved = cp.resolve_approval(request_id, payload.action, payload.rationale)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Unknown request_id: {request_id}")
    return {"resolved": True}
