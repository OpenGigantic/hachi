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

import hashlib
import secrets as _secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request

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


def require_auth(request: Request) -> str | None:
    """Enforce bearer-token authentication on protected routes.

    Transcribed verbatim (behaviour, not implementation detail) from the
    ``_require_auth`` closure that used to live inside
    ``create_gateway_app`` (see that function's docstring, "Auth posture").
    Reads its configuration from ``request.app.state`` -- populated
    unconditionally near the top of ``create_gateway_app`` -- instead of
    closing over ``auth_token`` / ``_auth_enabled``.

    Preserves all four documented modes:

    1. Token configured, valid bearer credential supplied -> returns the
       sha256 hex-digest of the caller's raw token (used for session-
       ownership checks, C1, without keeping the raw token in memory).
    2. Token configured, credential missing or wrong -> raises
       :class:`AuthRequired` (-> HTTP 401 via the registered handler).
    3. ``HACHI_GATEWAY_NO_AUTH=1`` (``app.state.auth_enabled`` is False) ->
       returns ``None``, no enforcement.
    4. No token configured at all (the ``bind_host=None`` unit-test /
       ``hachi gateway-openapi`` export path -- the CLI always generates a
       token for a real bind) -> returns ``None``, no enforcement.

    Loopback binding does NOT exempt this check (MR-2 / UC-AUTH-001 /
    ADV-014) -- the startup guards in ``create_gateway_app`` (E8/T8.2)
    guarantee ``auth_token`` is truthy whenever the process actually binds to
    a socket, so mode 4 only fires on the guard-skipping ``bind_host=None``
    path.

    Returns:
        sha256 hex-digest of the caller's bearer token when auth is enforced
        and the credential is valid; ``None`` in no-auth mode or when running
        as a test helper without a configured token.
    """
    if not request.app.state.auth_enabled:
        return None
    auth_token = request.app.state.auth_token
    if not auth_token:
        # No token configured AND bind_host was None (unit-test / export path) ->
        # no enforcement. When bind_host was provided, the startup guard
        # (CRIT-001) already raised ValueError before the server started, so
        # reaching here with bind_host set and no token is structurally
        # impossible in production.
        return None
    expected = f"Bearer {auth_token}"
    received = request.headers.get("Authorization", "")
    if not _secrets.compare_digest(received.strip(), expected):
        raise AuthRequired()
    raw_token = received.strip()[len("Bearer ") :].strip()
    # C1: return the sha256 digest so routes can compare ownership without
    # storing or comparing the raw bearer value.
    return hashlib.sha256(raw_token.encode()).hexdigest()


def check_session_ownership(
    control_plane: GatewayControlPlane, session_id: str, caller_hash: str | None
) -> None:
    """Enforce C1 session-ownership policy.

    Transcribed from the ``_check_session_ownership`` closure that used to
    live inside ``create_gateway_app``. If the session has an owner set, the
    caller's token-hash must match. Legacy sessions (NULL owner) are
    accessible by any authenticated caller for backward compatibility. In
    no-auth mode (``caller_hash`` is ``None``) all sessions are accessible.

    Args:
        control_plane: The control plane to read the session's owner from.
        session_id: The session being accessed.
        caller_hash: sha256 hex-digest returned by :func:`require_auth`, or
            ``None`` in no-auth / test-helper mode.

    Raises:
        HTTPException: 403 if the caller does not own the session.
    """
    if caller_hash is None:
        # No-auth mode or unconfigured token: skip ownership check.
        return
    owner = control_plane.get_session_owner(session_id)
    if owner is not None and not _secrets.compare_digest(owner, caller_hash):
        raise HTTPException(
            status_code=403,
            detail="access denied: session is owned by another caller",
        )


# Reusable annotated dependency types for router handler signatures (T-47 Sec 5).
CallerHash = Annotated[str | None, Depends(require_auth)]
ControlPlaneDep = Annotated[GatewayControlPlane, Depends(get_control_plane)]
