"""ADR-0006 (T-61) regression test — the authz-model decision.

CF-15 found that Hachi's per-session "ownership" layer ("C1": sha256 token
digests, an owner map, 403 branches, owner-filtered list endpoints) could
never discriminate between callers, because exactly one bearer token exists
per process — every authenticated caller hashes to the same digest, so every
ownership comparison compared a value to itself. It was also incomplete: the
owner map lived only on ``GatewayControlPlane._connections`` (wiped on every
restart) and a missing owner was documented as *allow*.

The product owner chose **option (a) — delete** (see
``docs/architecture/adr/0006-single-tenant-authz.md``): Hachi is honestly
single-principal, the bearer token IS the identity, and there is no
per-session owner layer on top of authentication.

This test is unusual in the same way the task itself is: under a single
bearer token, "ownership exists but always allows because every caller
hashes to the same digest" and "ownership does not exist at all" are
HTTP-indistinguishable — that indistinguishability *is* CF-15's finding, and
section 4 of the task spec explicitly says not to invent a behavioural
failure that isn't there. So this test combines two things a pure
``TestClient`` probe cannot supply on its own:

  1. Structural assertions that the ownership machinery — the control-plane
     methods, the ``owner_token_hash`` key, the ``_check_session_ownership``/
     ``_check_run_ownership`` check functions — no longer exists anywhere
     under ``hachi.gateway``. T-47 moved every route handler out of
     ``fastapi_app.py`` into ``routers/*.py`` (plus ``deps.py`` and
     ``control_plane.py``), so a single-file grep against ``fastapi_app.py``
     (as an earlier sketch of this test assumed) would prove nothing — it
     never held any of this code to begin with. This test greps every ``.py``
     file under the ``hachi.gateway`` package instead.

     It also greps ``hachi/core/session_store.py`` explicitly: the task spec's
     own §3.2/§4 asserted ``grep -rn "owner" src/hachi/core/session_store.py``
     "returns nothing today," verified against ``main @ bd24afe``. That claim
     was wrong even against that exact commit (`git show bd24afe:...`
     confirms it) — ``SessionStore.set_session_owner``/``.get_session_owner``
     and a persisted ``owner_token_hash`` column have existed there,
     completely unreferenced by anything, since 2026-06-20. The gateway's
     ownership was always in-memory-only exactly as CF-15 diagnosed; this was
     a second, independently orphaned "finish it" attempt nothing ever wired
     up. Deleted alongside the gateway-side machinery so the ADR's claim that
     no ownership symbol survives is actually true, not just true of the file
     the (stale) task spec happened to check.
  2. A real ``TestClient`` driving the actual routes (including
     ``GET /sessions/{id}/messages``, the one T-62 had just finished wiring a
     now-superseded ownership check onto) with the one real bearer token,
     proving the single authenticated principal reaches every session route
     with no owner gate anywhere in the way.

If you are reintroducing per-caller ownership, supersede ADR-0006 first — do
not just delete this test.
"""

from __future__ import annotations

from pathlib import Path

from conftest import control_plane as build_control_plane
from fastapi.testclient import TestClient

from hachi.core.session_store import SessionStore
from hachi.gateway import control_plane as cp_mod
from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app

TOKEN = "t61-test-token"  # noqa: S105 - test fixture, not a real credential

_CONNECT = {
    "client_id": "t",
    "surface": "test",
    "protocol_version": "1.0",
    "capabilities": [],
}

# The exact symbols CF-15 named as the ownership machinery. None of these may
# appear anywhere under hachi.gateway once ADR-0006's delete option lands.
_FORBIDDEN_SYMBOLS = (
    "owner_token_hash",
    "_check_session_ownership",
    "_check_run_ownership",
    "set_session_owner",
    "get_session_owner",
)


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _gateway_source_files() -> list[Path]:
    """Every ``.py`` file under ``hachi.gateway``, plus ``core/session_store.py``.

    T-47 spread what used to be a single ``fastapi_app.py`` across
    ``routers/*.py``, ``deps.py``, and ``control_plane.py`` — a check against
    ``fastapi_app.py`` alone would silently pass while the real machinery
    lived on happily in a router module. ``core/session_store.py`` is included
    explicitly because it independently carried a second, orphaned copy of
    this machinery the task spec's own reproduction steps missed (see module
    docstring) — a package-scoped ``hachi.gateway`` glob alone would miss it.
    """
    import hachi.core.session_store as session_store_mod
    import hachi.gateway as gateway_pkg

    root = Path(gateway_pkg.__file__).parent
    return sorted([*root.rglob("*.py"), Path(session_store_mod.__file__)])


def _build_app() -> tuple[GatewayControlPlane, TestClient]:
    control_plane = build_control_plane(
        on_chat=lambda _sid, p: f"echo:{p}",
        get_session_state=lambda sid: {"session_id": sid, "ok": True},
        get_recent_sessions=lambda _limit: [],
        get_session_messages=lambda _sid, _limit: [
            {
                "id": 1,
                "role": "user",
                "content": "hi",
                "tool_name": None,
                "timestamp": "2026-07-21T00:00:00+00:00",
            }
        ],
    )
    app = create_gateway_app(control_plane, auth_token=TOKEN, bind_host="127.0.0.1")
    return control_plane, TestClient(app)


def test_gateway_is_single_principal_by_decision() -> None:
    """ADR-0006: the bearer token IS the identity; there is no per-session owner."""
    # -- Structural: the ownership machinery must not exist anywhere. --
    assert not hasattr(cp_mod.GatewayControlPlane, "set_session_owner"), (
        "GatewayControlPlane.set_session_owner still exists — ADR-0006 chose to "
        "delete the C1 ownership machinery, not keep it."
    )
    assert not hasattr(cp_mod.GatewayControlPlane, "get_session_owner"), (
        "GatewayControlPlane.get_session_owner still exists — ADR-0006 chose to "
        "delete the C1 ownership machinery, not keep it."
    )
    assert not hasattr(SessionStore, "set_session_owner"), (
        "SessionStore.set_session_owner still exists — a second, orphaned copy of "
        "the ownership machinery that the (stale) task spec's own repro steps missed."
    )
    assert not hasattr(SessionStore, "get_session_owner"), (
        "SessionStore.get_session_owner still exists — a second, orphaned copy of "
        "the ownership machinery that the (stale) task spec's own repro steps missed."
    )

    hits: list[str] = []
    for path in _gateway_source_files():
        text = path.read_text(encoding="utf-8")
        for symbol in _FORBIDDEN_SYMBOLS:
            if symbol in text:
                hits.append(f"{path.name}: {symbol!r}")
    assert hits == [], (
        "ownership machinery still referenced under hachi.gateway (ADR-0006 chose "
        f"delete, not finish): {hits}"
    )

    # -- Behavioural: the single authenticated principal reaches every session --
    # -- route with no owner gate anywhere in the way, including the transcript --
    # -- endpoint T-62 had just finished wiring a (now superseded) check onto. --
    _control_plane, client = _build_app()

    created = client.post("/sessions", json=_CONNECT, headers=_bearer())
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    assert client.get(f"/sessions/{session_id}", headers=_bearer()).status_code == 200
    assert (
        client.post(
            f"/sessions/{session_id}/messages",
            json={"prompt": "hi"},
            headers=_bearer(),
        ).status_code
        == 200
    )
    assert client.get(f"/sessions/{session_id}/messages", headers=_bearer()).status_code == 200
    assert client.get("/sessions", headers=_bearer()).status_code == 200
    assert client.delete(f"/sessions/{session_id}", headers=_bearer()).status_code == 200
