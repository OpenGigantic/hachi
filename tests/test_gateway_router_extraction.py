"""T-47 — characterization tests for lifting the gateway's routers out of the
``create_gateway_app`` closure (see
``docs/audit/2026-07-19/tasks/T-47-lift-routers-out-of-closure.md``).

This is a pure structural refactor: there is no bug, so these tests pin the
exact HTTP contract *before* the move so the relocation is provably
behaviour-neutral (CLAUDE.md Rule 2, "the regression law", applied to a
refactor).

- ``test_openapi_spec_is_unchanged`` PASSES before the extraction and must
  still pass after every step -- that is what characterization means.
- ``test_protected_routes_reject_a_missing_token`` is THE auth-bypass guard
  (T-47 Sec 3.4): a router lifted to module scope that silently loses its
  ``dependencies=[Depends(_require_auth)]`` serves protected data to anyone,
  and no route-shape test would notice -- this one does.
- ``test_auth_parity_across_all_four_modes`` fails with an ``ImportError``
  until ``hachi.gateway.deps`` exists, and then pins ``deps.require_auth``'s
  behaviour against the four documented auth modes.
- The remaining tests characterize the *structural* goal of the task (the
  routers package must exist, be independently importable, and eventually
  fully replace the in-closure ``APIRouter`` declarations) and the isolation
  goal (``app.state``, not module globals).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app

BASELINE = Path(__file__).parent / "fixtures" / "openapi_baseline.json"

# Routes that are genuinely public by design (no auth dependency at all) --
# established by reading the handlers, NOT by trusting the generated OpenAPI
# `security` metadata: `create_gateway_app`'s `_custom_openapi()` stamps
# `security: [{"BearerAuth": []}]` onto *every* operation via `setdefault`,
# including these four, so the declared spec cannot be used to derive which
# routes actually enforce the dependency at request time.
_PUBLIC_PATHS = {
    "/",  # system.get_root
    "/health",  # system.get_health
    "/readiness",  # system.get_readiness
    # T-37: the OAuth popup callback is intentionally unauthenticated -- the
    # provider redirects the user's browser here directly and cannot carry a
    # bearer token. CSRF protection is the `state` parameter, not auth.
    "/integrations/oauth/callback",
}


@pytest.fixture
def control_plane() -> GatewayControlPlane:
    return GatewayControlPlane()


def test_openapi_spec_is_unchanged(control_plane: GatewayControlPlane) -> None:
    """The 100+-route contract must survive the move byte-for-byte.

    Any path, method, or status-code change is out of scope for T-47 (spec
    Sec. 9) and triggers BB-4 (spec + frontend types must be regenerated).
    """
    spec = create_gateway_app(control_plane).openapi()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert sorted(spec["paths"]) == sorted(baseline["paths"]), "route set changed"
    for path, methods in baseline["paths"].items():
        assert sorted(spec["paths"][path]) == sorted(methods), f"methods changed on {path}"


def test_protected_routes_reject_a_missing_token(control_plane: GatewayControlPlane) -> None:
    """Every protected route must still reject an unauthenticated caller.

    THE bypass guard: a router lifted to module scope loses its
    ``dependencies=[Depends(_require_auth)]`` silently, and no shape test
    would catch it -- this one does, against every declared path.
    """
    app = create_gateway_app(control_plane, auth_token="s3cret", bind_host="127.0.0.1")
    client = TestClient(app)

    protected = [p for p in app.openapi()["paths"] if p not in _PUBLIC_PATHS]
    assert len(protected) > 80, "sanity: expected most routes to be protected"

    # 401 = the auth dependency fired and rejected the (missing) credential.
    # 405 = the path exists but this probe's GET isn't one of its methods --
    # Starlette rejects on method before any dependency runs, so a probe that
    # can't prove enforcement this way still proves no *data* was served.
    unguarded = [p for p in protected if client.get(p).status_code not in (401, 403, 405)]
    assert unguarded == [], f"routes served without auth: {unguarded}"


def test_auth_parity_across_all_four_modes(
    control_plane: GatewayControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deps.require_auth`` must behave identically to the four documented
    auth modes (``fastapi_app.create_gateway_app`` docstring / T-47 Sec 3.4):

      1. token configured, valid credential   -> True
      2. token configured, missing/bad        -> 401
      3. ``HACHI_GATEWAY_NO_AUTH=1``           -> False, no enforcement
      4. ``auth_token=None`` and no ``bind_host`` -> False, no enforcement
         (the unit-test / OpenAPI-export path)

    ADR-0006 (T-61): ``require_auth`` used to return the caller's sha256
    token digest in mode 1 (consumed by the since-deleted per-session
    ownership layer) and ``None`` in modes 3/4. It now returns a plain
    ``bool`` -- nothing downstream needs a caller identity anymore, only
    whether this request was actually authenticated.

    Exercises ``require_auth`` exactly as every real protected route does --
    through FastAPI's ``Depends`` mechanism against a real running app, with
    real HTTP responses from a ``TestClient`` -- not a bare function call
    against a hand-built mock.
    """
    from hachi.gateway.deps import require_auth

    def _mount_probe(app):  # type: ignore[no-untyped-def]
        """Attach a diagnostic route that echoes require_auth's return value."""

        @app.get("/__t47_auth_probe__")
        def _probe(authenticated: bool = Depends(require_auth)) -> dict[str, bool]:
            return {"authenticated": authenticated}

        return app

    token = "t47-parity-token"

    # Modes 1 + 2: token configured.
    app1 = _mount_probe(create_gateway_app(control_plane, auth_token=token, bind_host="127.0.0.1"))
    client1 = TestClient(app1)

    ok = client1.get("/__t47_auth_probe__", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert ok.json()["authenticated"] is True

    missing = client1.get("/__t47_auth_probe__")
    assert missing.status_code == 401

    bad = client1.get("/__t47_auth_probe__", headers={"Authorization": "Bearer wrong-token"})
    assert bad.status_code == 401

    # Mode 3: HACHI_GATEWAY_NO_AUTH=1 (loopback only).
    monkeypatch.setenv("HACHI_GATEWAY_NO_AUTH", "1")
    app3 = _mount_probe(create_gateway_app(GatewayControlPlane(), bind_host="127.0.0.1"))
    client3 = TestClient(app3)
    r3 = client3.get("/__t47_auth_probe__")
    assert r3.status_code == 200
    assert r3.json()["authenticated"] is False
    monkeypatch.delenv("HACHI_GATEWAY_NO_AUTH", raising=False)

    # Mode 4: no token, no bind_host (unit-test / OpenAPI-export path).
    app4 = _mount_probe(create_gateway_app(GatewayControlPlane()))
    client4 = TestClient(app4)
    r4 = client4.get("/__t47_auth_probe__")
    assert r4.status_code == 200
    assert r4.json()["authenticated"] is False


def test_routers_package_is_importable_and_exposes_all_routers() -> None:
    """Acceptance criterion (T-47 Sec 8): the routers package must be
    importable WITHOUT calling ``create_gateway_app``, and must expose the
    full set -- proving every router is now a real, independently-importable
    module-scope object instead of a name trapped in a factory's closure.
    """
    from hachi.gateway import routers

    assert len(routers.ALL_ROUTERS) >= 19, (
        "expected at least the 19 pre-existing routers (7 of them replacing "
        "the split-up runtime_ops) to be importable from hachi.gateway.routers"
    )
    total_routes = sum(len(r.routes) for r in routers.ALL_ROUTERS)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline_route_count = sum(len(methods) for methods in baseline["paths"].values())
    assert total_routes == baseline_route_count, (
        f"routers package declares {total_routes} routes, "
        f"baseline OpenAPI spec has {baseline_route_count}"
    )


def test_fastapi_app_has_no_inline_api_routers() -> None:
    """Acceptance criterion (T-47 Sec 8): ``grep -c "APIRouter(" fastapi_app.py``
    must be 0 once every router has moved to ``hachi.gateway.routers`` --
    ``create_gateway_app`` should be left assembling module-scope routers,
    not declaring them.
    """
    import hachi.gateway.fastapi_app as fastapi_app_module

    source = Path(fastapi_app_module.__file__).read_text(encoding="utf-8")
    assert "APIRouter(" not in source, (
        "fastapi_app.py still declares an APIRouter inline -- T-47 is not complete"
    )


def test_two_apps_do_not_share_control_plane_state() -> None:
    """Acceptance criterion (T-47 Sec 8 / spec Sec 5 decision 1): collaborators
    must live on ``app.state``, not a module-level global -- two apps built in
    the same process (routine in this test suite) must never interfere.
    """
    cp_a = GatewayControlPlane(readiness_probe=lambda: (True, []))
    cp_b = GatewayControlPlane(readiness_probe=lambda: (False, ["custom-blocker"]))

    app_a = create_gateway_app(cp_a)
    app_b = create_gateway_app(cp_b)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        readiness_a = client_a.get("/readiness").json()
        readiness_b = client_b.get("/readiness").json()

    assert readiness_a["ready"] is True
    assert readiness_a["blockers"] == []
    assert readiness_b["ready"] is False
    assert readiness_b["blockers"] == ["custom-blocker"]
