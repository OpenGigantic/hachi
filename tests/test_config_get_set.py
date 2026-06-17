"""Tests for `hachi config-get` and `hachi config-set`.

All tests mock load_effective_config / write_global_config so no real
settings file is touched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from hachi.cli.main import app

runner = CliRunner()


def _fake_cfg():
    provider = SimpleNamespace(kind="anthropic", model="claude-sonnet-4-6", base_url=None)
    cfg = SimpleNamespace(
        provider=provider,
        safety_tier="guided",
        max_tokens=8192,
        session_cost_cap_usd=5.0,
    )
    return cfg


def _patch_cfg(cfg=None):
    if cfg is None:
        cfg = _fake_cfg()
    return patch("hachi.cli.main.load_effective_config", return_value=cfg)


def _patch_write():
    return patch("hachi.cli.main.write_global_config")


def _patch_paths():
    fake_paths = MagicMock()
    fake_paths.workspace_dir = "/home/user/.hachi/workspace"
    return patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths)


# ─── config-get ──────────────────────────────────────────────────────────────


class TestConfigGet:
    def test_no_key_lists_all(self) -> None:
        with _patch_cfg(), _patch_paths():
            result = runner.invoke(app, ["config-get"])
        assert result.exit_code == 0
        assert "provider.model" in result.output
        assert "safety_tier" in result.output
        assert "max_tokens" in result.output

    def test_specific_key_shows_value(self) -> None:
        with _patch_cfg(), _patch_paths():
            result = runner.invoke(app, ["config-get", "provider.model"])
        assert result.exit_code == 0
        assert "claude-sonnet-4-6" in result.output

    def test_json_no_key_emits_dict(self) -> None:
        with _patch_cfg(), _patch_paths():
            result = runner.invoke(app, ["config-get", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["provider.model"] == "claude-sonnet-4-6"
        assert data["safety_tier"] == "guided"
        assert data["max_tokens"] == 8192

    def test_json_specific_key(self) -> None:
        with _patch_cfg(), _patch_paths():
            result = runner.invoke(app, ["config-get", "session_cost_cap_usd", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["session_cost_cap_usd"] == 5.0

    def test_unknown_key_exits_1(self) -> None:
        with _patch_cfg(), _patch_paths():
            result = runner.invoke(app, ["config-get", "no.such.key"])
        assert result.exit_code == 1
        assert "Unknown key" in result.output

    def test_unknown_key_lists_known_keys(self) -> None:
        with _patch_cfg(), _patch_paths():
            result = runner.invoke(app, ["config-get", "bogus"])
        assert "provider.model" in result.output or "Known keys" in result.output


# ─── config-set ──────────────────────────────────────────────────────────────


class TestConfigSet:
    def test_set_provider_model(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write() as mock_write, _patch_paths():
            result = runner.invoke(app, ["config-set", "provider.model", "claude-opus-4-8"])
        assert result.exit_code == 0
        assert cfg.provider.model == "claude-opus-4-8"
        mock_write.assert_called_once_with(cfg)

    def test_set_safety_tier_valid(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "safety_tier", "sandbox"])
        assert result.exit_code == 0
        assert cfg.safety_tier == "sandbox"

    def test_set_safety_tier_invalid_exits_1(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "safety_tier", "danger"])
        assert result.exit_code == 1
        assert "sandbox" in result.output or "safety_tier" in result.output

    def test_set_max_tokens_integer(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "max_tokens", "16384"])
        assert result.exit_code == 0
        assert cfg.max_tokens == 16384

    def test_set_max_tokens_non_int_exits_1(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "max_tokens", "not-a-number"])
        assert result.exit_code == 1

    def test_set_session_cost_cap_float(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "session_cost_cap_usd", "2.5"])
        assert result.exit_code == 0
        assert cfg.session_cost_cap_usd == 2.5

    def test_set_provider_base_url(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(
                app, ["config-set", "provider.base_url", "https://proxy.example.com"]
            )
        assert result.exit_code == 0
        assert cfg.provider.base_url == "https://proxy.example.com"

    def test_set_unknown_key_exits_1(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "no.such.key", "value"])
        assert result.exit_code == 1
        assert "Cannot set" in result.output

    def test_json_output_on_success(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(
                app, ["config-set", "provider.model", "claude-haiku-4-5", "--json"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["key"] == "provider.model"
        assert data["value"] == "claude-haiku-4-5"

    def test_human_output_shows_set_confirmation(self) -> None:
        cfg = _fake_cfg()
        with _patch_cfg(cfg), _patch_write(), _patch_paths():
            result = runner.invoke(app, ["config-set", "safety_tier", "unrestricted"])
        assert result.exit_code == 0
        assert "Set" in result.output or "safety_tier" in result.output
