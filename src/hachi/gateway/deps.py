"""FastAPI dependency providers for the gateway (T-47).

``create_gateway_app`` (``fastapi_app.py``) used to declare all 19 routers and
100+ routes *inside* its own body, so every handler closed over locals like
``control_plane`` and ``auth_token``. That made the routers impossible to
import, test, or review in isolation.

This module replaces that closure capture with explicit dependency
injection: ``create_gateway_app`` stores its collaborators on ``app.state``
(per-app, never a module global -- two ``FastAPI`` apps built in the same
process, which the test suite does routinely, must never share state), and
the providers below read them back per-request. Routers under
``hachi.gateway.routers`` can then be declared at module scope and depend on
these providers instead of a closure.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Annotated

from fastapi import Depends, Request

from hachi.gateway.control_plane import GatewayControlPlane


class AuthRequired(Exception):
    """Raised by :func:`require_auth` when a protected route's bearer token
    is missing or invalid.

    ``create_gateway_app`` registers ``@app.exception_handler(AuthRequired)``,
    which turns this into the standard 401 ``ErrorResponse`` envelope. This
    class used to be a nested class inside ``create_gateway_app`` (so only
    that closure's own ``_require_auth`` could raise it); it is now a plain
    module-level exception so a module-scope router's dependency can raise it
    too.
    """


def get_control_plane(request: Request) -> GatewayControlPlane:
    """Return the :class:`GatewayControlPlane` backing this app instance.

    Reads ``request.app.state.control_plane`` -- per-app state set once by
    ``create_gateway_app`` -- rather than a module-level global, so multiple
    apps in one process never share a control plane (T-47 Sec 5 decision 1).
    """
    return request.app.state.control_plane  # type: ignore[no-any-return]


def require_auth(request: Request) -> bool:
    """Enforce bearer-token authentication on protected routes.

    Transcribed verbatim (behaviour, not implementation detail) from the
    ``_require_auth`` closure that used to live inside
    ``create_gateway_app`` (see that function's docstring, "Auth posture").
    Reads its configuration from ``request.app.state`` -- populated
    unconditionally near the top of ``create_gateway_app`` -- instead of
    closing over ``auth_token`` / ``_auth_enabled``.

    Preserves all four documented modes:

    1. Token configured, valid bearer credential supplied -> returns
       ``True``.
    2. Token configured, credential missing or wrong -> raises
       :class:`AuthRequired` (-> HTTP 401 via the registered handler).
    3. ``HACHI_GATEWAY_NO_AUTH=1`` (``app.state.auth_enabled`` is False) ->
       returns ``False``, no enforcement.
    4. No token configured at all (the ``bind_host=None`` unit-test /
       ``hachi gateway-openapi`` export path -- the CLI always generates a
       token for a real bind) -> returns ``False``, no enforcement.

    Loopback binding does NOT exempt this check (MR-2 / UC-AUTH-001 /
    ADV-014) -- the startup guards in ``create_gateway_app`` (E8/T8.2)
    guarantee ``auth_token`` is truthy whenever the process actually binds to
    a socket, so mode 4 only fires on the guard-skipping ``bind_host=None``
    path.

    ADR-0006 (T-61): this used to return the sha256 hex-digest of the
    caller's raw bearer token, so routes could compare per-session
    "ownership" without keeping the raw token in memory. Hachi is
    single-principal -- exactly one bearer token exists per process, so
    every authenticated caller produced the *same* digest and every such
    comparison compared a value to itself. That ownership layer (and its
    only consumer of the digest) has been deleted; this dependency now only
    answers "was this request authenticated," which is all any caller
    legitimately needs from it.

    Returns:
        ``True`` when auth is enforced and the credential is valid;
        ``False`` in no-auth mode or when running as a test helper without a
        configured token.
    """
    if not request.app.state.auth_enabled:
        return False
    auth_token = request.app.state.auth_token
    if not auth_token:
        # No token configured AND bind_host was None (unit-test / export path) ->
        # no enforcement. When bind_host was provided, the startup guard
        # (CRIT-001) already raised ValueError before the server started, so
        # reaching here with bind_host set and no token is structurally
        # impossible in production.
        return False
    expected = f"Bearer {auth_token}"
    received = request.headers.get("Authorization", "")
    if not _secrets.compare_digest(received.strip(), expected):
        raise AuthRequired()
    return True


# Reusable annotated dependency types for router handler signatures (T-47 Sec 5).
# ADR-0006 (T-61): renamed from ``CallerHash`` -- it stopped carrying a caller
# hash the moment the digest-returning C1 ownership layer was deleted; a type
# still named "caller hash" once the hash was gone would itself have been the
# kind of misleading artifact this decision exists to remove.
Authenticated = Annotated[bool, Depends(require_auth)]
ControlPlaneDep = Annotated[GatewayControlPlane, Depends(get_control_plane)]
