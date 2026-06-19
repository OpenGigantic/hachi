from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from hachi.cli.main import _InProcessGatewayRuntime
from hachi.core.onboarding import looks_like_env_var_name, persist_provider_api_key
from hachi.gateway.control_plane import GatewayProtocolError


class _FakeStore:
    """In-memory SecretStore double that records writes."""

    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.saved.get(name)

    def set(self, name: str, value: str, metadata=None) -> None:
        self.saved[name] = value

    def delete(self, name: str) -> None:
        self.saved.pop(name, None)

    def list_metadata(self) -> list:
        return []

    def healthcheck(self) -> dict:
        return {}


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

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

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


def test_update_provider_persists_raw_api_key_securely(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()
    store = _FakeStore()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)
    monkeypatch.setattr("hachi.cli.main.create_default_secret_store", lambda: store)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # register for auto-cleanup

    result = runtime.update_provider(
        {"provider": {"kind": "anthropic", "api_key": "sk-ant-secret-1234567890"}}
    )

    # Raw key persisted to the secret store + process env under the env-var NAME.
    assert store.saved["ANTHROPIC_API_KEY"] == "sk-ant-secret-1234567890"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-1234567890"
    # Config records only the NAME — the raw secret never lands in the config.
    assert result["provider"]["api_key_ref"] == "ANTHROPIC_API_KEY"
    assert result["provider"]["api_key_env"] == "ANTHROPIC_API_KEY"


def test_update_provider_treats_raw_key_in_api_key_ref_as_secret(monkeypatch) -> None:
    # Backward-compat: older web clients sent the raw key as `api_key_ref`.
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()
    store = _FakeStore()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)
    monkeypatch.setattr("hachi.cli.main.create_default_secret_store", lambda: store)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    result = runtime.update_provider(
        {"provider": {"kind": "anthropic", "api_key_ref": "sk-ant-raw-9876543210"}}
    )

    assert store.saved["ANTHROPIC_API_KEY"] == "sk-ant-raw-9876543210"
    assert result["provider"]["api_key_ref"] == "ANTHROPIC_API_KEY"


def test_update_provider_preserves_env_name_api_key_ref(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()
    store = _FakeStore()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)
    monkeypatch.setattr("hachi.cli.main.create_default_secret_store", lambda: store)

    result = runtime.update_provider(
        {"provider": {"kind": "anthropic", "api_key_ref": "ANTHROPIC_API_KEY"}}
    )

    # A legitimate reference NAME passes through unchanged; nothing is stored.
    assert result["provider"]["api_key_ref"] == "ANTHROPIC_API_KEY"
    assert store.saved == {}


def test_looks_like_env_var_name() -> None:
    assert looks_like_env_var_name("ANTHROPIC_API_KEY")
    assert looks_like_env_var_name("OPENAI_API_KEY")
    assert not looks_like_env_var_name("sk-ant-abc123")
    assert not looks_like_env_var_name("lower_case")
    assert not looks_like_env_var_name("")


def test_persist_provider_api_key(monkeypatch) -> None:
    store = _FakeStore()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    name = persist_provider_api_key("anthropic", "sk-ant-xyz", secret_store=store)

    assert name == "ANTHROPIC_API_KEY"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-xyz"
    assert store.saved["ANTHROPIC_API_KEY"] == "sk-ant-xyz"


def test_persist_provider_api_key_blank_and_keyless_are_noops() -> None:
    store = _FakeStore()
    assert persist_provider_api_key("anthropic", "   ", secret_store=store) is None
    # ollama has no conventional env var → nothing to persist
    assert persist_provider_api_key("ollama", "whatever", secret_store=store) is None
    assert store.saved == {}


def test_update_provider_rejects_invalid_kind_and_blank_model(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

    with pytest.raises(GatewayProtocolError, match="provider.kind must be"):
        runtime.update_provider({"provider": {"kind": "not-real"}})

    with pytest.raises(GatewayProtocolError, match="provider.model cannot be empty"):
        runtime.update_provider({"provider": {"model": "   "}})


def test_update_provider_currently_accepts_malformed_base_url(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

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

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

    with pytest.raises(GatewayProtocolError, match="permissions.tier must be"):
        runtime.update_permissions({"permissions": {"tier": "root"}})


def test_update_permissions_accepts_supported_tiers(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

    for tier in ("sandbox", "guided", "unrestricted"):
        result = runtime.update_permissions({"permissions": {"tier": tier}})
        assert result["permissions"]["tier"] == tier


def test_update_memory_is_idempotent_for_noop_payload(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

    baseline = runtime.update_memory({"memory": {}})
    repeated = runtime.update_memory({"memory": {}})

    assert baseline["memory"] == repeated["memory"]
    assert baseline["memory"]["enable_session_history"] is True
    assert baseline["memory"]["enable_semantic_memory"] is True


def test_update_memory_partial_write_preserves_other_fields(monkeypatch) -> None:
    runtime = _InProcessGatewayRuntime()
    cfg = _FakeConfig()

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.write_global_config", lambda _config: None)

    result = runtime.update_memory({"memory": {"enable_session_history": False}})

    assert result["memory"]["enable_session_history"] is False
    assert result["memory"]["enable_semantic_memory"] is True
