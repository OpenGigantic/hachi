"""Regression tests for T-62 / BB-108 — superseded by ADR-0006 (T-61).

T-62 gave ``GET /sessions/{session_id}/messages`` the same per-session
ownership check its sibling session routes performed, closing an
inconsistency where two routes skipped the check nine others ran. ADR-0006
(T-61) subsequently deleted the ownership layer entirely — the check T-62
added is gone along with every other one, coherently. See
``docs/architecture/adr/0006-single-tenant-authz.md``.

Per T-62's own file (Sec 1, "If T-61 has already landed with the delete
option, this task is superseded — do not apply it; note that in the file and
move on") and T-61 Sec 6 step 5 ("If you chose (a), T-62 is superseded; say
so in its file and in your commit message"): **T-62 is superseded.**

``test_session_messages_denies_a_foreign_owner`` is removed — it staged a
foreign owner via ``set_session_owner``, a method that no longer exists, so
there is nothing left for it to exercise (not merely a now-false assertion,
a call that no longer resolves). ``test_session_messages_still_serves_the_owner``
is unchanged: it never depended on the deleted ownership API, only on the
normal single-principal request path, which ADR-0006 keeps working exactly
as before. One test is added: the route's real remaining access control
(bearer authentication) still holds without a valid token.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app
from tests.conftest import control_plane as build_control_plane

TOKEN = "t62-test-token"  # noqa: S105 - test fixture, not a real credential

_CONNECT = {
    "client_id": "t",
    "surface": "test",
    "protocol_version": "1.0",
    "capabilities": [],
}


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _build_app() -> tuple[GatewayControlPlane, TestClient]:
    """A gateway app+control-plane pair with auth enforced like production."""
    control_plane = build_control_plane(
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


def test_session_messages_still_serves_the_owner() -> None:
    """The single-principal path must keep working: create, then read back."""
    _control_plane, client = _build_app()
    session_id = _create_session(client)

    resp = client.get(f"/sessions/{session_id}/messages", headers=_bearer())
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["messages"]


def test_session_messages_requires_a_bearer_token() -> None:
    """ADR-0006: the surviving access control on this route is authentication,
    not ownership. A request with no (or a wrong) bearer token must still be
    rejected, same as every other route on this router."""
    _control_plane, client = _build_app()
    session_id = _create_session(client)

    no_auth = client.get(f"/sessions/{session_id}/messages")
    assert no_auth.status_code == 401

    wrong_token = client.get(
        f"/sessions/{session_id}/messages", headers={"Authorization": "Bearer wrong-token"}
    )
    assert wrong_token.status_code == 401
