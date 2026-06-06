from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from tokoboros.cli.main import _apply_quick_setup_if_needed, app

runner = CliRunner()


def test_gateway_command_starts_and_stops_server(monkeypatch) -> None:
    server_state: dict[str, object] = {
        "started": False,
        "stopped": False,
        "host": None,
        "port": None,
    }

    class FakeServer:
        def __init__(
            self, _control_plane, host: str, port: int, auth_token: str | None = None
        ) -> None:
            server_state["host"] = host
            server_state["port"] = port
            server_state["auth_token"] = auth_token

        def start(self) -> int:
            server_state["started"] = True
            return 9001

        def stop(self) -> None:
            server_state["stopped"] = True

    def _raise_interrupt() -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main.GatewayHTTPServer", FakeServer)
    monkeypatch.setattr("tokoboros.cli.main._wait_until_interrupted", _raise_interrupt)
    monkeypatch.setenv("TOKOBOROS_GATEWAY_API_TOKEN", "test-token")

    result = runner.invoke(app, ["gateway", "--host", "0.0.0.0", "--port", "9000"])

    assert result.exit_code == 0
    assert server_state["started"] is True
    assert server_state["stopped"] is True
    assert server_state["host"] == "0.0.0.0"
    assert server_state["port"] == 9000
    assert server_state["auth_token"] == "test-token"


def test_gateway_command_returns_error_when_server_fails(monkeypatch) -> None:
    class FailingServer:
        def __init__(
            self, _control_plane, host: str, port: int, auth_token: str | None = None
        ) -> None:
            self._host = host
            self._port = port
            self._auth_token = auth_token

        def start(self) -> int:
            raise OSError("port already in use")

        def stop(self) -> None:
            return None

    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main.GatewayHTTPServer", FailingServer)

    result = runner.invoke(app, ["gateway", "--port", "9000"])

    assert result.exit_code == 1
    assert "Failed to start gateway server" in result.stdout


def test_gateway_command_requires_auth_token_for_non_loopback_host(monkeypatch) -> None:
    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.delenv("TOKOBOROS_GATEWAY_API_TOKEN", raising=False)

    result = runner.invoke(app, ["gateway", "--host", "0.0.0.0", "--port", "9000"])

    assert result.exit_code == 1
    assert "TOKOBOROS_GATEWAY_API_TOKEN_FILE" in result.stdout


def test_gateway_command_reads_auth_token_from_file(monkeypatch, tmp_path: Path) -> None:
    server_state: dict[str, object] = {}
    token_file = tmp_path / "gateway-token.txt"
    token_file.write_text("file-secret-token", encoding="utf-8")

    class FakeServer:
        def __init__(
            self, _control_plane, host: str, port: int, auth_token: str | None = None
        ) -> None:
            server_state["host"] = host
            server_state["port"] = port
            server_state["auth_token"] = auth_token

        def start(self) -> int:
            return 9001

        def stop(self) -> None:
            return None

    def _raise_interrupt() -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main.GatewayHTTPServer", FakeServer)
    monkeypatch.setattr("tokoboros.cli.main._wait_until_interrupted", _raise_interrupt)
    monkeypatch.delenv("TOKOBOROS_GATEWAY_API_TOKEN", raising=False)
    monkeypatch.setenv("TOKOBOROS_GATEWAY_API_TOKEN_FILE", str(token_file))

    result = runner.invoke(app, ["gateway", "--host", "0.0.0.0", "--port", "9000"])

    assert result.exit_code == 0
    assert server_state["auth_token"] == "file-secret-token"


def test_gateway_command_passes_runtime_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run_gateway_http_server(host: str, port: int, attach_runtime: bool) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["attach_runtime"] = attach_runtime

    monkeypatch.setattr(
        "tokoboros.cli.main._run_gateway_http_server", _fake_run_gateway_http_server
    )

    result = runner.invoke(app, ["gateway", "--port", "8999", "--no-runtime"])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8999
    assert captured["attach_runtime"] is False


def test_run_command_starts_gateway_and_passes_url_to_tui(monkeypatch) -> None:
    state: dict[str, object] = {
        "start_called": False,
        "stop_called": False,
        "runtime_shutdown": False,
        "gateway_url": None,
    }

    class FakeServer:
        def stop(self) -> None:
            state["stop_called"] = True

    class FakeRuntime:
        def shutdown(self) -> None:
            state["runtime_shutdown"] = True

    def _fake_start_gateway_server(
        host: str = "127.0.0.1",
        port: int = 0,
        attach_runtime: bool = False,
    ):
        state["start_called"] = True
        assert host == "127.0.0.1"
        assert port == 0
        assert attach_runtime is True
        return FakeServer(), "http://127.0.0.1:8111", FakeRuntime()

    def _fake_run_tui(gateway_base_url: str | None = None) -> None:
        state["gateway_url"] = gateway_base_url

    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main.ensure_device_permissions", lambda _console: None)
    monkeypatch.setattr("tokoboros.cli.main._start_gateway_server", _fake_start_gateway_server)
    monkeypatch.setattr("tokoboros.cli.main.run_tui", _fake_run_tui)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert state["start_called"] is True
    assert state["stop_called"] is True
    assert state["runtime_shutdown"] is True
    assert state["gateway_url"] == "http://127.0.0.1:8111"


def test_run_repl_skips_gateway_bootstrap(monkeypatch) -> None:
    state: dict[str, bool] = {
        "run_repl_called": False,
        "gateway_started": False,
    }

    def _fake_run_repl() -> None:
        state["run_repl_called"] = True

    def _fake_start_gateway_server(
        host: str = "127.0.0.1",
        port: int = 0,
        attach_runtime: bool = False,
    ):
        state["gateway_started"] = True
        raise AssertionError("gateway should not start for --repl")

    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main.ensure_device_permissions", lambda _console: None)
    monkeypatch.setattr("tokoboros.cli.main._run_repl", _fake_run_repl)
    monkeypatch.setattr("tokoboros.cli.main._start_gateway_server", _fake_start_gateway_server)

    result = runner.invoke(app, ["run", "--repl"])

    assert result.exit_code == 0
    assert state["run_repl_called"] is True
    assert state["gateway_started"] is False


def test_run_command_falls_back_to_repl_when_tui_launch_fails(monkeypatch) -> None:
    state: dict[str, bool] = {
        "start_called": False,
        "stop_called": False,
        "runtime_shutdown": False,
        "repl_called": False,
    }

    class FakeServer:
        def stop(self) -> None:
            state["stop_called"] = True

    class FakeRuntime:
        def shutdown(self) -> None:
            state["runtime_shutdown"] = True

    def _fake_start_gateway_server(
        host: str = "127.0.0.1",
        port: int = 0,
        attach_runtime: bool = False,
    ):
        state["start_called"] = True
        return FakeServer(), "http://127.0.0.1:8111", FakeRuntime()

    def _boom_run_tui(gateway_base_url: str | None = None) -> None:
        raise RuntimeError(f"tui failed at {gateway_base_url}")

    def _fake_repl() -> None:
        state["repl_called"] = True

    monkeypatch.setattr("tokoboros.cli.main.ensure_workspace", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main._apply_quick_setup_if_needed", lambda: None)
    monkeypatch.setattr("tokoboros.cli.main.ensure_device_permissions", lambda _console: None)
    monkeypatch.setattr("tokoboros.cli.main._start_gateway_server", _fake_start_gateway_server)
    monkeypatch.setattr("tokoboros.cli.main.run_tui", _boom_run_tui)
    monkeypatch.setattr("tokoboros.cli.main._run_repl", _fake_repl)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert state["start_called"] is True
    assert state["repl_called"] is True
    assert state["runtime_shutdown"] is True
    assert state["stop_called"] is True
    assert "Falling back to compatibility REPL" in result.stdout


def test_apply_quick_setup_runs_only_when_needed(monkeypatch) -> None:
    state: dict[str, int] = {
        "detect": 0,
        "build": 0,
        "apply": 0,
    }

    class FakeOnboardingService:
        def __init__(self) -> None:
            self._needs_setup = True

        def needs_setup(self) -> bool:
            return self._needs_setup

        def detect_environment(self) -> dict[str, bool]:
            state["detect"] += 1
            return {"anthropic_env": True}

        def build_tier_plan(self, tier: str, answers: dict) -> dict:
            state["build"] += 1
            assert tier == "lightning"
            assert answers["permission_tier"] == "guided"
            return {"tier": tier, "answers": answers}

        def apply_setup(self, plan: dict) -> None:
            state["apply"] += 1
            assert plan["tier"] == "lightning"

    monkeypatch.setattr("tokoboros.cli.main.OnboardingService", FakeOnboardingService)
    _apply_quick_setup_if_needed()

    assert state == {"detect": 1, "build": 1, "apply": 1}


def test_apply_quick_setup_skips_when_already_configured(monkeypatch) -> None:
    state: dict[str, int] = {
        "detect": 0,
        "build": 0,
        "apply": 0,
    }

    class FakeOnboardingService:
        def needs_setup(self) -> bool:
            return False

        def detect_environment(self) -> dict[str, bool]:
            state["detect"] += 1
            return {}

        def build_tier_plan(self, tier: str, answers: dict) -> dict:
            state["build"] += 1
            return {}

        def apply_setup(self, plan: dict) -> None:
            state["apply"] += 1

    monkeypatch.setattr("tokoboros.cli.main.OnboardingService", FakeOnboardingService)
    _apply_quick_setup_if_needed()

    assert state == {"detect": 0, "build": 0, "apply": 0}


def test_doctor_command_reports_environment(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    legacy_dir = tmp_path / "legacy"
    settings_toml = config_dir / "settings.toml"

    for directory in (config_dir, data_dir, state_dir, legacy_dir):
        directory.mkdir()
    settings_toml.write_text("[provider]\nkind='anthropic'\n", encoding="utf-8")

    fake_paths = SimpleNamespace(
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        global_config_toml=settings_toml,
        legacy_dir=legacy_dir,
        skills_dir=data_dir / "skills",
    )
    fake_config = SimpleNamespace(
        provider=SimpleNamespace(kind="anthropic", model="claude-haiku-4-5", api_key_env=None),
        permissions=SimpleNamespace(tier="guided"),
    )

    class FakeOnboardingService:
        def __init__(self, paths) -> None:
            self.paths = paths

        def needs_setup(self) -> bool:
            return False

        def validate_provider(self, *, kind, api_key_env=None):
            return (True, "ok")

        def detect_environment(self) -> dict[str, bool]:
            return {"anthropic_env": True}

    class FakeSecretStore:
        def healthcheck(self) -> dict[str, str]:
            return {"backend": "memory", "status": "ok"}

    monkeypatch.setattr("tokoboros.cli.main.get_tokoboros_paths", lambda: fake_paths)
    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: fake_config)
    monkeypatch.setattr("tokoboros.cli.main.OnboardingService", FakeOnboardingService)
    monkeypatch.setattr(
        "tokoboros.cli.main.create_default_secret_store",
        lambda paths=None: FakeSecretStore(),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "provider_kind" in result.stdout
    assert "anthropic" in result.stdout
    assert "secret_backend" in result.stdout
    assert "environment_detection" in result.stdout
    # skills_health is reported; with no skills dir it degrades gracefully.
    assert "skills_health" in result.stdout
    assert "no skills directory yet" in result.stdout


def test_security_audit_json_reports_findings_and_exit_code(monkeypatch, tmp_path: Path) -> None:
    permissions_file = tmp_path / "device-permissions.og"
    permissions_file.write_text("", encoding="utf-8")
    captured: list[str] = []

    fake_config = SimpleNamespace(
        permissions=SimpleNamespace(tier="unrestricted"),
        capabilities={
            "slack": SimpleNamespace(enabled=True, secret_ref=None),
            "notion": SimpleNamespace(enabled=False, secret_ref=None),
        },
    )

    class FakePermissionGate:
        def __init__(self, _permissions_file: Path) -> None:
            self._term = SimpleNamespace(allow_all=True)

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: fake_config)
    monkeypatch.setattr("tokoboros.cli.main.get_permissions_file", lambda: permissions_file)
    monkeypatch.setattr("tokoboros.cli.main.PermissionGate", FakePermissionGate)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TOKOBOROS_REST_ALLOWLIST", raising=False)
    monkeypatch.setenv("TOKOBOROS_REST_ALLOW_PRIVATE", "false")
    monkeypatch.setattr(
        "tokoboros.cli.main.console.print",
        lambda payload, *args, **kwargs: captured.append(str(payload)),
    )

    result = runner.invoke(app, ["security-audit", "--json", "--fail-on-high"])

    assert result.exit_code == 2
    assert len(captured) == 1
    payload = json.loads(captured[0])
    check_ids = {finding["check_id"] for finding in payload["findings"]}

    assert payload["status"] == "fail"
    assert payload["exit_code"] == 2
    assert "oauth.google_client_id_missing" in check_ids
    assert "permissions.unrestricted_tier" in check_ids
    assert "terminal.allow_all" in check_ids
    assert "rest.allowlist_missing" in check_ids
    assert "sandbox.process_backend" in check_ids
