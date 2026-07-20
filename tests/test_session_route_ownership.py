"""Regression tests for T-62 / BB-108.

``GET /sessions/{session_id}/messages`` must apply the same per-session
ownership check its sibling session routes already perform. Before the fix,
the handler's signature was ``(session_id, limit)`` with no ``request:
Request`` parameter, so it could not call ``_require_auth(request)`` and
therefore could never obtain the caller digest that
``_check_session_ownership`` needs -- the transcript was returned
unconditionally to any bearer-authenticated caller.

Design note (mirrors ``tests/test_session_ownership.py``): a gateway instance
enforces exactly one bearer token, so there is no way to *authenticate* as a
second caller. A second principal is honestly simulated by stamping a
*different* owner digest onto a live session directly through the control
plane, then requesting that session with the one real token -- a faithful
stand-in for the future two-token world CF-15 warns about.

Trap this file deliberately guards against (docs/audit/2026-07-19/tasks/
CORRECTIONS.md C23; T-62 spec Sec 7/Sec 12): ``set_session_owner`` is a
**silent no-op** when the session is not present in
``control_plane._connections`` (i.e. not "live"), and a ``None`` owner means
*allow*. If the foreign-owner stamp silently failed to land, the route would
return 200 for a reason that has nothing to do with the fix under test --
the ownership map would simply be empty, same as after a restart. Every test
below asserts the stamp landed (``get_session_owner() ==
FOREIGN_OWNER_DIGEST``) before asserting on the HTTP response, so a broken
setup fails loudly as a setup assertion instead of quietly certifying the
permissive path.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app

TOKEN = "t62-test-token"
FOREIGN_OWNER_DIGEST = "0" * 64  # sha256 hex-digest never equals sha256(TOKEN)

_CONNECT = {
    "client_id": "t",
    "surface": "test",
    "protocol_version": "1.0",
    "capabilities": [],
}


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _build_app() -> tuple[GatewayControlPlane, TestClient]:
    """A gateway app+control-plane pair with auth enforced like production.

    Both ``auth_token`` and ``bind_host`` matter here: without ``auth_token``,
    ``_require_auth`` returns ``None`` unconditionally (no-auth/unit-test
    mode) and ``_check_session_ownership`` skips itself for the wrong reason
    -- the test would pass even against the pre-fix handler had it taken a
    ``request`` param at all. ``get_session_messages`` is wired so the
    transcript path is deterministic (always a hit) once ownership allows it.
    """
    control_plane = GatewayControlPlane(
        get_session_messages=lambda _sid, _limit: [
            {
                "id": 1,
                "role": "user",
                "content": "hi",
                "tool_name": None,
                "timestamp": "2026-07-19T00:00:00+00:00",
            }
        ],
    )
    app = create_gateway_app(control_plane, auth_token=TOKEN, bind_host="127.0.0.1")
    return control_plane, TestClient(app)


def _create_session(client: TestClient) -> str:
    resp = client.post("/sessions", json=_CONNECT, headers=_bearer())
    assert resp.status_code == 200
    session_id: str = resp.json()["session_id"]
    return session_id


def test_session_messages_denies_a_foreign_owner() -> None:
    """GET /sessions/{id}/messages must apply the ownership check its siblings do."""
    control_plane, client = _build_app()
    session_id = _create_session(client)

    # Simulate the future two-principal world: this session belongs to someone else.
    control_plane.set_session_owner(session_id, FOREIGN_OWNER_DIGEST)
    # Guard against the documented silent no-op: if the stamp didn't land (e.g.
    # because the session were no longer "live"), get_session_owner() would
    # return None, which is the *allow* policy -- and the assertion below would
    # pass trivially regardless of whether the route checks ownership at all.
    assert control_plane.get_session_owner(session_id) == FOREIGN_OWNER_DIGEST, (
        "test setup broken: the foreign-owner stamp did not land (set_session_owner "
        "silently no-ops for a non-live session -- see CORRECTIONS.md C23). "
        "This must fail here, not be papered over, or the 403 assertion below would "
        "be certifying the permissive (ownerless) path instead of the ownership check."
    )

    resp = client.get(f"/sessions/{session_id}/messages", headers=_bearer())
    assert resp.status_code == 403, (
        f"expected 403, got {resp.status_code}. GET /sessions/{{id}}/messages returned "
        "the transcript without checking session ownership."
    )


def test_session_messages_still_serves_the_owner() -> None:
    """The fix must not break the normal single-principal path."""
    _control_plane, client = _build_app()
    session_id = _create_session(client)

    resp = client.get(f"/sessions/{session_id}/messages", headers=_bearer())
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["messages"]
