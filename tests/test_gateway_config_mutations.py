from __future__ import annotations

from types import SimpleNamespace

import pytest

from tokoboros.cli.main import _InProcessGatewayRuntime
from tokoboros.gateway.control_plane import GatewayProtocolError


class _FakeConfig:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(
            kind="anthropic",
            model="claude-haiku-4-5",
            base_url=None,
            api_key_ref=None,
            api_key_env="ANTHROPIC_API_KEY",
        )
        self.permissions = SimpleNamespace(tier="guided")
        self.memory = SimpleNamespace(
            enable_session_history=True,
            enable_semantic_memory=True,
        )

    def model_dump(self, mode: str = "python", exclude_none: bool = True):
        return {
            "provider": {
                "kind": self.provider.kind,
                "model": self.provider.model,
                "base_url": self.provider.base_url,
                "api_key_ref": self.provider.api_key_ref,
                "api_key_env": self.provider.api_key_env,
            },
            "permissions": {"tier": self.permissions.tier},
            "memory": {
                "enable_session_history": self.memory.enable_session_history,
                "enable_semantic_memory": self.memory.enable_semantic_memory,
            },
        }


def test_update_provider_accepts_supported_kinds(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()
    cfg.provider.kind = "openai-compatible"
    cfg.provider.model = "gpt-4.1-mini"

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    anthropic = runtime.update_provider(
        {"provider": {"kind": "anthropic", "model": "gpt-4.1-mini"}}
    )
    assert anthropic["provider"]["kind"] == "anthropic"
    assert anthropic["provider"]["model"] == "claude-haiku-4-5"

    openai = runtime.update_provider(
        {"provider": {"kind": "openai-compatible", "model": "gpt-4.1-mini"}}
    )
    assert openai["provider"]["kind"] == "openai-compatible"

    ollama = runtime.update_provider(
        {
            "provider": {
                "kind": "ollama",
                "model": "qwen2.5-coder:7b",
                "base_url": "http://127.0.0.1:11434",
            }
        }
    )
    assert ollama["provider"]["kind"] == "ollama"
    assert ollama["provider"]["base_url"] == "http://127.0.0.1:11434"


def test_update_provider_rejects_invalid_kind_and_blank_model(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    with pytest.raises(GatewayProtocolError, match="provider.kind must be"):
        runtime.update_provider({"provider": {"kind": "not-real"}})

    with pytest.raises(GatewayProtocolError, match="provider.model cannot be empty"):
        runtime.update_provider({"provider": {"model": "   "}})


def test_update_provider_currently_accepts_malformed_base_url(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    result = runtime.update_provider(
        {
            "provider": {
                "kind": "openai-compatible",
                "model": "gpt-4.1-mini",
                "base_url": "not-a-url",
            }
        }
    )

    # QA finding: malformed base_url is accepted verbatim today.
    assert result["provider"]["base_url"] == "not-a-url"


def test_validate_provider_reports_expected_warnings_and_errors() -> None:
    runtime = _InProcessGatewayRuntime()

    invalid = runtime.validate_provider({"provider": {"kind": "", "model": ""}})
    assert invalid["ok"] is False
    assert any("provider.kind" in item for item in invalid["errors"])
    assert any("provider.model" in item for item in invalid["errors"])

    remote_missing_auth = runtime.validate_provider(
        {"provider": {"kind": "openai-compatible", "model": "gpt-4.1-mini"}}
    )
    assert remote_missing_auth["ok"] is True
    assert any("api_key_ref or api_key_env" in item for item in remote_missing_auth["warnings"])

    ollama_missing_base = runtime.validate_provider(
        {"provider": {"kind": "ollama", "model": "llama3.1:8b"}}
    )
    assert ollama_missing_base["ok"] is True
    assert any("base_url is recommended" in item for item in ollama_missing_base["warnings"])


def test_update_permissions_rejects_invalid_tier(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    with pytest.raises(GatewayProtocolError, match="permissions.tier must be"):
        runtime.update_permissions({"permissions": {"tier": "root"}})


def test_update_permissions_accepts_supported_tiers(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    for tier in ("sandbox", "guided", "unrestricted"):
        result = runtime.update_permissions({"permissions": {"tier": tier}})
        assert result["permissions"]["tier"] == tier


def test_update_memory_is_idempotent_for_noop_payload(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    baseline = runtime.update_memory({"memory": {}})
    repeated = runtime.update_memory({"memory": {}})

    assert baseline["memory"] == repeated["memory"]
    assert baseline["memory"]["enable_session_history"] is True
    assert baseline["memory"]["enable_semantic_memory"] is True


def test_update_memory_partial_write_preserves_other_fields(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("tokoboros.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("tokoboros.cli.main.write_global_config", lambda _config: None)

    result = runtime.update_memory({"memory": {"enable_session_history": False}})

    assert result["memory"]["enable_session_history"] is False
    assert result["memory"]["enable_semantic_memory"] is True
