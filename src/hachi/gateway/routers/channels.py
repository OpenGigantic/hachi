"""``POST /channels/{channel}/inbound`` -- generic inbound webhook (T-47).

Any platform (Slack Events, Discord, Twilio SMS, a custom form) can POST a
message and reach the agent, with per-sender session continuity. Bespoke
adapters are optional sugar on top of this.
"""

from __future__ import annotations

from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request

from hachi.core.rate_limit import RateLimiter
from hachi.core.untrusted_content import guard_untrusted
from hachi.gateway.deps import CallerHash, ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, ChannelInboundRequest

router = APIRouter(
    tags=["channels"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


def _get_channel_limiter(request: Request) -> RateLimiter:
    """Read the per-app rate limiter created by ``create_gateway_app`` (T-47)."""
    return request.app.state.channel_limiter  # type: ignore[no-any-return]


def _get_channel_allowlist(request: Request) -> set[str]:
    """Read the per-app sender allowlist created by ``create_gateway_app`` (T-47)."""
    return request.app.state.channel_allowlist  # type: ignore[no-any-return]


ChannelLimiterDep = Annotated[RateLimiter, Depends(_get_channel_limiter)]
ChannelAllowlistDep = Annotated[set[str], Depends(_get_channel_allowlist)]


@router.post("/channels/{channel}/inbound")
async def channel_inbound(
    channel: str,
    payload: ChannelInboundRequest,
    request: Request,
    caller_hash: CallerHash,
    cp: ControlPlaneDep,
    channel_limiter: ChannelLimiterDep,
    channel_allowlist: ChannelAllowlistDep,
) -> Any:
    # H6: resolve the caller's identity (the webhook's bearer token, not the
    # attacker-controlled sender_id) so the facade session for this
    # channel:sender key is stamped with a real owner.
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    sender = payload.sender_id
    # Optional allowlist accepts either a bare sender id or "channel:sender".
    if channel_allowlist and not (
        sender in channel_allowlist or f"{channel}:{sender}" in channel_allowlist
    ):
        raise HTTPException(status_code=403, detail="sender is not allowed")
    session_key = f"{channel}:{sender}"
    # MED-003: key the rate limiter on client IP (not user-supplied sender_id) so
    # an attacker cannot bypass limits by cycling through sender values.  We keep
    # session_key for session continuity but limit by IP+channel.
    client_ip = (request.client.host if request.client else "unknown") or "unknown"
    rate_key = f"{channel}:{client_ip}"
    if not channel_limiter.allow(rate_key):
        raise HTTPException(status_code=429, detail="rate limit exceeded; slow down")
    # H10: wrap inbound webhook text in the untrusted-content data envelope,
    # matching the treatment applied by the polling ChannelRouter (channels/router.py).
    # guard_untrusted is unconditional per SEC-002 — it always frames the content
    # as data even when no injection is detected.
    source_label = f"channel:{channel} sender:{sender}"
    guarded_text, _flagged = guard_untrusted(payload.text, source=source_label)
    # Full agent turn is synchronous — run in a thread so the event loop stays free.
    result = await anyio.to_thread.run_sync(
        lambda: cp.complete_chat(
            guarded_text, session_key=session_key, owner_token_hash=caller_hash
        )
    )
    return {
        "channel": channel,
        "recipient_id": sender,
        "reply": result.get("response", ""),
    }
