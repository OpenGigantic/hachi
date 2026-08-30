"""Integration tests for session access under ADR-0006's single-principal model.

ADR-0006 (T-61) deleted the per-session "ownership" layer ("C1": sha256 token
digests, an owner map, 403 branches, owner-filtered lists) this file used to
exercise. It could never discriminate between callers — exactly one bearer
token exists per process, so every authenticated caller hashed to the same
digest and every ownership comparison compared a value to itself (CF-15). See
``docs/architecture/adr/0006-single-tenant-authz.md`` for the full decision.

This file is rewritten, not deleted, per CLAUDE.md's regression law: a test
asserting deleted behaviour is a contract change to record, not a weakening
to silently drop. What survives:

- The owner/legacy-session/single-token/no-auth positive-path tests below
  never depended on the deleted ``set_session_owner``/``get_session_owner``
  API in the first place -- they created a session and accessed it with the
  same token, which is exactly what single-principal access still is. They
  are unchanged.
- ``TestControlPlaneOwnership`` (direct unit tests of the deleted
  ``set_session_owner``/``get_session_owner`` methods), ``TestCrossCallerDenied``
  (simulated a foreign owner via ``set_session_owner`` to assert a 403 that no
  longer exists) and ``TestListSessionsScoped`` (asserted owner-based
  filtering that no longer exists) are removed -- their setup called methods
  that no longer exist, not merely asserted a now-false outcome. Their route
  coverage (``/events``, ``/stream``, ``GET /sessions`` returning every
  session) is folded into ``TestOwnerAllowed``/the new
  ``test_list_sessions_returns_every_session`` below, asserting the new
  contract instead.

``tests/test_session_authz_model.py`` is the canonical ADR-0006 regression
test (structural: no ownership symbol survives anywhere under
``hachi.gateway``); this file stays as HTTP-level integration coverage of the
session routes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app
from tests.conftest import control_plane

_TOKEN_A = "token-owner-aaa"

_CONNECT = {
    "client_id": "c",
    "surface": "tui",
    "protocol_version": "1.0",
    "capabilities": [],
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# The authenticated caller can access every route on a session it created
# ---------------------------------------------------------------------------


class TestOwnerAllowed:
    """The token that created the session retains full access — there is no
    second principal to deny, so this is the entire access-control surface."""

    def test_owner_can_get_session(self) -> None:
        cp = control_plane(get_session_state=lambda sid: {"session_id": sid, "ok": True})
        app = create_gateway_app(cp, auth_token=_TOKEN_A)
        client = TestClient(app)
        resp = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN_A))
        sid = resp.json()["session_id"]

        r = client.get(f"/sessions/{sid}", headers=_auth(_TOKEN_A))
        assert r.status_code == 200

    def test_owner_can_post_message(self) -> None:
        cp = control_plane(on_chat=lambda _sid, p: f"echo:{p}")
        app = create_gateway_app(cp, auth_token=_TOKEN_A)
        client = TestClient(app)
        resp = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN_A))
        sid = resp.json()["session_id"]

        r = client.post(
            f"/sessions/{sid}/messages", json={"prompt": "hello"}, headers=_auth(_TOKEN_A)
        )
        assert r.status_code == 200
        assert r.json()["response"] == "echo:hello"

    def test_owner_can_delete_session(self) -> None:
        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=_TOKEN_A)
        client = TestClient(app)
        resp = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN_A))
        sid = resp.json()["session_id"]

        r = client.delete(f"/sessions/{sid}", headers=_auth(_TOKEN_A))
        assert r.status_code == 200
        assert r.json()["closed"] is True

    def test_owner_can_read_events(self) -> None:
        """Folded in from the deleted TestCrossCallerDenied.test_events_forbidden --
        that test simulated a foreign owner to assert 403; there is no
        ownership gate left to deny, so the same route now just works."""
        cp = control_plane(read_events=lambda _sid, _after, _limit: [])
        app = create_gateway_app(cp, auth_token=_TOKEN_A)
        client = TestClient(app)
        resp = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN_A))
        sid = resp.json()["session_id"]

        r = client.get(f"/sessions/{sid}/events", headers=_auth(_TOKEN_A))
        assert r.status_code == 200

    def test_owner_can_stream(self) -> None:
        """Folded in from the deleted TestCrossCallerDenied.test_stream_forbidden."""
        cp = control_plane(read_events=lambda _sid, _after, _limit: [])
        app = create_gateway_app(cp, auth_token=_TOKEN_A)
        client = TestClient(app)
        resp = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN_A))
        sid = resp.json()["session_id"]

        with client.stream(
            "GET",
            f"/sessions/{sid}/stream",
            params={"poll_interval": 0, "max_idle_cycles": 1},
            headers=_auth(_TOKEN_A),
        ) as r:
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /sessions returns every session — there is no owner filter left
# ---------------------------------------------------------------------------


def test_list_sessions_returns_every_session() -> None:
    """Folded in from the deleted TestListSessionsScoped -- that test stamped
    a second session with a different owner digest via ``set_session_owner``
    and asserted it was filtered out of the list. That mechanism no longer
    exists; every session the store holds is now returned, unfiltered."""
    session_records: list[dict] = []

    def _recent(limit: int) -> list[dict]:
        return session_records[:limit]

    cp = control_plane(get_recent_sessions=_recent)
    app = create_gateway_app(cp, auth_token=_TOKEN_A)
    client = TestClient(app)

    r1 = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN_A))
    assert r1.status_code == 200
    sid_a = r1.json()["session_id"]
    session_records.append({"session_id": sid_a, "title": "A"})

    s2 = cp.create_session(client_id="b", surface="tui", protocol_version="1.0", capabilities=[])
    sid_b = s2["session_id"]
    session_records.append({"session_id": sid_b, "title": "B"})

    r_list = client.get("/sessions", headers=_auth(_TOKEN_A))
    assert r_list.status_code == 200
    body = r_list.json()
    assert isinstance(body, list)
    ids = [s.get("session_id") or s.get("id") or "" for s in body]
    assert sid_a in ids
    assert sid_b in ids


# ---------------------------------------------------------------------------
# Single-token deployment: same token = same caller, no breakage
# ---------------------------------------------------------------------------


class TestSingleTokenDeployment:
    """When all callers use the same token, access never blocks."""

    def test_same_token_can_access_own_session(self) -> None:
        _TOKEN = "shared-single-token"
        cp = control_plane(on_chat=lambda _sid, p: f"pong:{p}")
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.post("/sessions", json=_CONNECT, headers=_auth(_TOKEN))
        sid = resp.json()["session_id"]

        r1 = client.post(f"/sessions/{sid}/messages", json={"prompt": "a"}, headers=_auth(_TOKEN))
        r2 = client.post(f"/sessions/{sid}/messages", json={"prompt": "b"}, headers=_auth(_TOKEN))
        assert r1.status_code == 200
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# No-auth mode: all sessions accessible (unchanged by ADR-0006)
# ---------------------------------------------------------------------------


class TestNoAuthMode:
    """In no-auth mode (auth_token=None), every session is accessible."""

    def test_no_auth_can_access_any_session(self) -> None:
        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=None)
        client = TestClient(app)

        resp = client.post("/sessions", json=_CONNECT)
        assert resp.status_code == 200
        sid = resp.json()["session_id"]

        r = client.delete(f"/sessions/{sid}")
        assert r.status_code == 200
