"""``GET /integrations/oauth/callback`` -- OAuth2 popup callback (T-47).

Deliberately a SEPARATE router from ``routers/integrations.py``: this route
does NOT carry the auth dependency (the provider redirects the user's browser
here directly and cannot supply a Bearer token) or ``_ERROR_RESPONSES``
(every branch returns an ``HTMLResponse``, never the JSON envelope). CSRF
protection is the ``state`` parameter, not auth -- keeping it a separate
router object makes that omission structural, not something a future edit to
the "real" integrations router could accidentally undo.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from hachi.gateway.deps import ControlPlaneDep

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/oauth/callback")
async def get_oauth_callback(
    cp: ControlPlaneDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Any:
    """OAuth2 callback — provider redirects the user's browser popup here.

    Validates the CSRF state, exchanges the code for tokens, stores them,
    then returns a small HTML page that:
    - postMessages ``{type: "hachi_oauth_done", id: "<id>", ok: true/false}``
      to the opener (the Hachi frontend).
    - Closes the popup window.

    This route is intentionally NOT protected by the Bearer auth dependency
    because the provider redirects the user's browser here directly.
    CSRF protection is provided by the ``state`` parameter.
    """
    from hachi.integrations.oauth_flow import (
        _callback_html_error,
        _callback_html_ok,
        oauth_callback,
    )

    if error:
        # Provider signalled an error (e.g. user denied consent).
        safe_error = str(error)[:200]
        html = _callback_html_error(
            "",
            f"The provider returned an error: {safe_error}. Please try again.",
        )
        return HTMLResponse(content=html, status_code=400)

    if not code or not state:
        html = _callback_html_error(
            "",
            "Missing code or state parameter. Please start the OAuth flow again.",
        )
        return HTMLResponse(content=html, status_code=400)

    connector_id, ok, err_msg = await anyio.to_thread.run_sync(
        lambda: oauth_callback(str(code), str(state))
    )
    if ok:
        # BB-11: auto-enable the capability so tools are registered on next boot.
        # Silently swallow errors (handler absent in tests; unknown cap id is non-fatal).
        try:
            cp.set_capability_enabled(connector_id, True)
        except Exception:
            pass
        html = _callback_html_ok(connector_id)
        return HTMLResponse(content=html, status_code=200)
    else:
        html = _callback_html_error(connector_id, err_msg)
        return HTMLResponse(content=html, status_code=400)
