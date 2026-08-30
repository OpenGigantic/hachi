"""Tests for gateway auth-by-default posture (UC-AUTH-001/002/003, ADV-014, UC-PERM-006).

Covers:
- Protected routes return 401 without a bearer token (new default posture).
- Protected routes return 200 with the correct bearer token.
- Token is generated + persisted + printed when none is configured (loopback).
- HACHI_GATEWAY_NO_AUTH=1 disables enforcement and logs a warning.
- GET /approvals and POST /approvals/{id} exist and require auth.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app
from tests.conftest import control_plane

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN = "test-bearer-token-abc123"
_CONNECT = {
    "client_id": "c",
    "surface": "tui",
    "protocol_version": "1.0",
    "capabilities": [],
}


def _app_with_token(token: str = _TOKEN) -> TestClient:
    """Return a TestClient for a gateway app created with ``auth_token=token``."""
    cp = GatewayControlPlane()
    app = create_gateway_app(cp, auth_token=token)
    return TestClient(app)


def _app_no_token() -> TestClient:
    """Return a TestClient for a gateway app created with ``auth_token=None``.

    Under the new default posture, even ``auth_token=None`` requires auth unless
    ``HACHI_GATEWAY_NO_AUTH=1`` is set in the environment.
    """
    cp = GatewayControlPlane()
    app = create_gateway_app(cp, auth_token=None)
    return TestClient(app)


# ---------------------------------------------------------------------------
# MR-2 / UC-AUTH-001 / ADV-014 — protected routes require Bearer token
# ---------------------------------------------------------------------------


class TestAuthRequired:
    """Protected routes must reject requests without a valid bearer token."""

    def test_sessions_endpoint_returns_401_without_token(self) -> None:
        client = _app_with_token()
        resp = client.post("/sessions", json=_CONNECT)
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "unauthorized"

    def test_sessions_endpoint_returns_200_with_correct_token(self) -> None:
        client = _app_with_token()
        resp = client.post(
            "/sessions",
            json=_CONNECT,
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

    def test_sessions_endpoint_returns_401_with_wrong_token(self) -> None:
        client = _app_with_token()
        resp = client.post(
            "/sessions",
            json=_CONNECT,
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_config_endpoint_returns_401_without_token(self) -> None:
        client = _app_with_token()
        resp = client.get("/config")
        assert resp.status_code == 401

    def test_config_endpoint_returns_200_with_token(self) -> None:
        cp = control_plane(get_config=lambda: {"provider": {"kind": "anthropic"}})
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)
        resp = client.get("/config", headers={"Authorization": f"Bearer {_TOKEN}"})
        assert resp.status_code == 200

    def test_runtime_events_endpoint_returns_401_without_token(self) -> None:
        client = _app_with_token()
        resp = client.get("/runtime/events")
        assert resp.status_code == 401

    def test_health_endpoint_is_public(self) -> None:
        """The /health endpoint must remain unauthenticated for load balancers."""
        client = _app_with_token()
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_readiness_endpoint_is_public(self) -> None:
        client = _app_with_token()
        resp = client.get("/readiness")
        assert resp.status_code in {200, 503}

    def test_auth_token_none_does_not_enforce_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auth_token=None → no enforcement (unit-test helper mode, no token configured).

        The security fix is in the CLI layer: ``_resolve_gateway_auth_token`` now
        always generates a token for loopback hosts, so ``auth_token`` will be
        populated in every real deployment.  Unit tests that call
        ``create_gateway_app(cp)`` directly without a token are still supported.
        """
        monkeypatch.delenv("HACHI_GATEWAY_NO_AUTH", raising=False)
        client = _app_no_token()
        resp = client.post("/sessions", json=_CONNECT)
        # No token configured → auth check is skipped (no enforcement without a token).
        assert resp.status_code == 200

    def test_loopback_host_does_not_exempt_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loopback binding must NOT skip authentication (MR-2 / ADV-014)."""
        # The app itself does not know the bind address — the auth check runs
        # regardless.  This test ensures no "loopback bypass" is snuck back in.
        monkeypatch.delenv("HACHI_GATEWAY_NO_AUTH", raising=False)
        client = _app_with_token()
        resp = client.get("/config")
        assert resp.status_code == 401


class TestNoAuthOptOut:
    """HACHI_GATEWAY_NO_AUTH=1 disables enforcement for power users."""

    def test_sessions_returns_200_without_token_when_no_auth_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HACHI_GATEWAY_NO_AUTH", "1")
        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)
        resp = client.post("/sessions", json=_CONNECT)
        # No token header sent, but auth is disabled via env var.
        assert resp.status_code == 200

    def test_no_auth_env_zero_keeps_enforcement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HACHI_GATEWAY_NO_AUTH", "0")
        client = _app_with_token()
        resp = client.post("/sessions", json=_CONNECT)
        assert resp.status_code == 401

    def test_no_auth_env_not_set_keeps_enforcement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HACHI_GATEWAY_NO_AUTH", raising=False)
        client = _app_with_token()
        resp = client.post("/sessions", json=_CONNECT)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Token generation + persistence (cli/main.py path)
# ---------------------------------------------------------------------------


class TestTokenGeneration:
    """_resolve_gateway_auth_token generates + persists + prints a token for loopback."""

    def test_token_generated_and_persisted_when_none_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.cli.main import (
            _resolve_gateway_auth_token,
        )

        token_path = tmp_path / "gateway-token"
        monkeypatch.setattr("hachi.cli.main._default_gateway_token_path", lambda: token_path)
        monkeypatch.delenv("HACHI_GATEWAY_API_TOKEN", raising=False)
        monkeypatch.delenv("HACHI_GATEWAY_API_TOKEN_FILE", raising=False)

        token = _resolve_gateway_auth_token("127.0.0.1")
        assert token  # truthy
        assert token_path.exists()
        assert token_path.read_text(encoding="utf-8").strip() == token

    def test_generated_token_is_reused_on_second_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.cli.main import (
            _resolve_gateway_auth_token,
        )

        token_path = tmp_path / "gateway-token"
        monkeypatch.setattr("hachi.cli.main._default_gateway_token_path", lambda: token_path)
        monkeypatch.delenv("HACHI_GATEWAY_API_TOKEN", raising=False)
        monkeypatch.delenv("HACHI_GATEWAY_API_TOKEN_FILE", raising=False)

        token_1 = _resolve_gateway_auth_token("127.0.0.1")
        token_2 = _resolve_gateway_auth_token("127.0.0.1")
        assert token_1 == token_2

    def test_explicit_env_token_takes_precedence_over_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.cli.main import _resolve_gateway_auth_token

        token_path = tmp_path / "gateway-token"
        token_path.write_text("old-persisted-token\n", encoding="utf-8")
        monkeypatch.setattr("hachi.cli.main._default_gateway_token_path", lambda: token_path)
        monkeypatch.setenv("HACHI_GATEWAY_API_TOKEN", "env-explicit-token")

        token = _resolve_gateway_auth_token("127.0.0.1")
        assert token == "env-explicit-token"

    def test_non_loopback_host_requires_explicit_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.cli.main import _resolve_gateway_auth_token

        monkeypatch.delenv("HACHI_GATEWAY_API_TOKEN", raising=False)
        monkeypatch.delenv("HACHI_GATEWAY_API_TOKEN_FILE", raising=False)

        with pytest.raises(OSError, match="HACHI_GATEWAY_API_TOKEN"):
            _resolve_gateway_auth_token("0.0.0.0")

    def test_load_or_generate_returns_generated_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.cli.main import _load_or_generate_gateway_token

        token_path = tmp_path / "new-token"
        monkeypatch.setattr("hachi.cli.main._default_gateway_token_path", lambda: token_path)

        token, generated = _load_or_generate_gateway_token()
        assert generated is True
        assert len(token) > 16  # urlsafe(32) → ~43 chars

    def test_load_or_generate_returns_not_generated_when_already_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.cli.main import _load_or_generate_gateway_token

        token_path = tmp_path / "existing-token"
        existing = "already-here-abc"
        token_path.write_text(existing + "\n", encoding="utf-8")
        monkeypatch.setattr("hachi.cli.main._default_gateway_token_path", lambda: token_path)

        token, generated = _load_or_generate_gateway_token()
        assert generated is False
        assert token == existing


# ---------------------------------------------------------------------------
# UC-AUTH-002 — daemon ensures token before install
# ---------------------------------------------------------------------------


class TestDaemonTokenEnsure:
    """DaemonInstaller.install() must call _ensure_daemon_token() (UC-AUTH-002)."""

    def test_install_generates_token_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.daemon import DaemonConfig, DaemonInstaller

        token_path = tmp_path / "gateway-token"
        monkeypatch.setattr("hachi.daemon._default_token_path", lambda: token_path)

        # Stub out the actual schtasks/launchctl/systemctl subprocess calls.
        def fake_runner(cmd, **_kwargs):
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        # Stub locate_executable so it does not call shutil.which on CI.
        monkeypatch.setattr(
            "hachi.daemon.locate_executable",
            lambda: Path("/usr/bin/hachi"),
        )

        installer = DaemonInstaller(DaemonConfig(host="127.0.0.1", port=8787), runner=fake_runner)
        # Patch the platform-specific install method so we only test token creation.
        called: dict[str, bool] = {"install": False}

        def _fake_platform_install(self_inner: DaemonInstaller) -> str:
            called["install"] = True
            return "installed"

        import sys as _sys

        platform = (
            "macos"
            if _sys.platform == "darwin"
            else ("linux" if _sys.platform.startswith("linux") else "windows")
        )
        method = {
            "macos": "_install_launchd",
            "linux": "_install_systemd",
            "windows": "_install_windows",
        }[platform]
        monkeypatch.setattr(DaemonInstaller, method, _fake_platform_install)
        monkeypatch.setattr("hachi.daemon._detect_platform", lambda: platform)

        installer.install()

        assert token_path.exists(), "Token file should be created by install()"
        token = token_path.read_text(encoding="utf-8").strip()
        assert token  # non-empty token was persisted

    def test_ensure_daemon_token_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hachi.daemon import _ensure_daemon_token

        token_path = tmp_path / "gateway-token"
        existing = "idempotent-existing-token"
        token_path.write_text(existing + "\n", encoding="utf-8")
        monkeypatch.setattr("hachi.daemon._default_token_path", lambda: token_path)

        token1 = _ensure_daemon_token()
        token2 = _ensure_daemon_token()
        assert token1 == existing
        assert token2 == existing


# ---------------------------------------------------------------------------
# UC-PERM-006 — /approvals endpoints require auth and match Agent A interface
# ---------------------------------------------------------------------------


class TestApprovalsEndpoints:
    """GET /approvals and POST /approvals/{id} exist, require auth, forward to registry."""

    def _make_app_with_fake_registry(
        self,
        pending: list[dict] | None = None,
        resolve_returns: bool = True,
    ) -> tuple[TestClient, MagicMock]:
        """Build a gateway app with a stubbed approval registry.

        T-15: injected via ``GatewayControlPlane(list_pending_approvals=...,
        resolve_approval=...)`` — the control-plane seam — rather than
        monkeypatching ``hachi.gateway.fastapi_app.get_approval_registry``,
        which no longer exists (the route reaches the registry only through
        ``_InProcessGatewayRuntime`` in ``cli/main.py`` now). This also fixes a
        latent bug in the prior version: its ``with patch(...):`` block closed
        before returning the client, so the patches were already undone by the
        time a caller made a request through it — constructor injection has no
        such lifetime problem.
        """
        fake_registry = MagicMock()
        fake_registry.list_pending.return_value = pending if pending is not None else []
        fake_registry.resolve.return_value = resolve_returns

        cp = control_plane(
            list_pending_approvals=fake_registry.list_pending,
            resolve_approval=lambda request_id, action, rationale="": fake_registry.resolve(
                request_id, SimpleNamespace(action=action, rationale=rationale)
            ),
        )
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)
        return client, fake_registry

    def test_get_approvals_returns_401_without_token(self) -> None:
        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)
        resp = client.get("/approvals")
        assert resp.status_code == 401

    def test_post_approvals_returns_401_without_token(self) -> None:
        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)
        resp = client.post("/approvals/req-123", json={"action": "approve"})
        assert resp.status_code == 401

    def test_get_approvals_returns_pending_list_with_token(self) -> None:
        """GET /approvals forwards control_plane.pending_approvals() verbatim.

        T-15 correction (CORRECTIONS.md Sec "Four tests will legitimately
        fail", item 4) — NOT a weakening: the original assertion compared
        ``body["pending"]`` to the exact same list object handed to the fake
        registry, so it passed *because* the old route mutated that object in
        place to inject a fabricated ``expiresAt`` — the test was structurally
        blind to the bug it existed to catch. Injecting through
        ``GatewayControlPlane`` (the patch target legitimately moved — the
        route no longer imports ``get_approval_registry`` at all) and
        asserting against a freshly-built literal here would catch either an
        added or a dropped field, which the old aliased comparison could not.
        """

        def _list_pending() -> list[dict]:
            return [{"request_id": "req-1", "tool": "write_file", "target": "/tmp/x"}]

        cp = control_plane(list_pending_approvals=_list_pending)
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.get("/approvals", headers={"Authorization": f"Bearer {_TOKEN}"})
        assert resp.status_code == 200
        body = resp.json()
        assert "pending" in body
        # Fresh literal, deliberately NOT the object `_list_pending` returned —
        # see the aliasing-trap note above.
        assert body["pending"] == [
            {"request_id": "req-1", "tool": "write_file", "target": "/tmp/x"}
        ]

    def test_post_approval_approve_calls_resolve_and_returns_resolved(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def _resolve(request_id: str, action: str, rationale: str = "") -> bool:
            calls.append((request_id, action, rationale))
            return True

        cp = control_plane(resolve_approval=_resolve)
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.post(
            "/approvals/req-999",
            json={"action": "approve", "rationale": "looks safe"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"resolved": True}

        # T-15: the route now forwards (request_id, action, rationale) to
        # control_plane.resolve_approval() rather than constructing a
        # PermissionDecision itself (that moved to
        # _InProcessGatewayRuntime.resolve_approval in cli/main.py), so this
        # asserts the route's actual contract with the control plane.
        assert calls == [("req-999", "approve", "looks safe")]

    def test_post_approval_deny_action_works(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def _resolve(request_id: str, action: str, rationale: str = "") -> bool:
            calls.append((request_id, action, rationale))
            return True

        cp = control_plane(resolve_approval=_resolve)
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.post(
            "/approvals/req-100",
            json={"action": "deny"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 200
        assert resp.json()["resolved"] is True
        assert calls == [("req-100", "deny", "")]

    def test_post_approval_allow_once_action_works(self) -> None:
        cp = control_plane(resolve_approval=lambda request_id, action, rationale="": True)
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.post(
            "/approvals/req-200",
            json={"action": "allow_once"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 200

    def test_post_approval_returns_404_when_resolve_returns_false(self) -> None:
        cp = control_plane(resolve_approval=lambda request_id, action, rationale="": False)
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.post(
            "/approvals/does-not-exist",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 404

    def test_post_approval_invalid_action_returns_422(self) -> None:
        # Validation happens before control_plane.resolve_approval() is ever
        # called, so no handler needs to be wired for this case.
        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.post(
            "/approvals/req-bad",
            json={"action": "delete_everything"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert resp.status_code == 422

    def test_approvals_503_when_module_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Graceful degradation when Agent A's module is not yet present."""
        # The patch must be active when the request is processed (not just when
        # create_gateway_app is called), because the route handler reads the global
        # _APPROVALS_AVAILABLE at handler-call time.
        monkeypatch.setattr("hachi.gateway.fastapi_app._APPROVALS_AVAILABLE", False)

        cp = GatewayControlPlane()
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.get("/approvals", headers={"Authorization": f"Bearer {_TOKEN}"})
        assert resp.status_code == 503

    def test_get_approvals_does_not_fabricate_expires_at(self) -> None:
        """GET /approvals must return data OBTAINED THROUGH the control plane, and
        must not invent a deadline the registry never recorded.

        Regression (T-15): get_approvals() used to reach around the control plane
        into get_approval_registry() directly, and injected a fabricated camelCase
        expiresAt = now + 60s per request — while T-16 gives PendingApprovalRegistry
        a REAL snake_case created_at/expires_at (epoch ms, derived from the timeout
        RegistryApprover actually enforces). This test builds its own stub entry
        (with T-16-shaped keys already on it) and injects it via
        GatewayControlPlane(list_pending_approvals=...) — the only route into the
        registry the fixed code should use — then asserts the response is that
        entry byte-for-byte: no expiresAt was added, and created_at/expires_at
        passed through unchanged (not recomputed/overwritten by the route).
        """

        def _list_pending() -> list[dict]:
            # Fresh list/dict built inside the stub each call, shaped exactly like
            # PendingApprovalRegistry.list_pending() after T-16 (8 keys, snake_case
            # created_at/expires_at) — so the route cannot pass this test merely by
            # forwarding whatever the REAL process-wide registry happens to hold.
            return [
                {
                    "request_id": "req-1",
                    "operation": "action",
                    "tool": "write_file",
                    "target": "/tmp/x",
                    "reason": "because",
                    "policy_summary": "action:write_file",
                    "created_at": 1_752_969_000_000,
                    "expires_at": 1_752_969_180_000,
                }
            ]

        cp = control_plane(list_pending_approvals=_list_pending)
        app = create_gateway_app(cp, auth_token=_TOKEN)
        client = TestClient(app)

        resp = client.get("/approvals", headers={"Authorization": f"Bearer {_TOKEN}"})
        assert resp.status_code == 200
        entry = resp.json()["pending"][0]
        assert "expiresAt" not in entry, "route must not fabricate a camelCase deadline"
        assert entry["expires_at"] == 1_752_969_180_000, "T-16's real expires_at must pass through"
        assert entry["created_at"] == 1_752_969_000_000, "T-16's real created_at must pass through"
        assert set(entry) == {
            "request_id",
            "operation",
            "tool",
            "target",
            "reason",
            "policy_summary",
            "created_at",
            "expires_at",
        }
