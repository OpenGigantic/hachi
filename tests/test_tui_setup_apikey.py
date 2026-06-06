"""Tests for interactive API-key entry in the TUI setup wizard:
the SetupWizardController key flow + apply_setup persisting api_key_ref."""

from __future__ import annotations

import os
from pathlib import Path

import tokoboros.tui.app as app_mod
from tokoboros.core.config_loader import load_effective_config
from tokoboros.core.onboarding import OnboardingService
from tokoboros.core.paths import TokoborosPaths
from tokoboros.tui.app import SetupWizardController


def _paths(tmp_path: Path) -> TokoborosPaths:
    return TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )


def _controller(tmp_path: Path) -> SetupWizardController:
    return SetupWizardController(OnboardingService(paths=_paths(tmp_path)))


# --------------------------------------------------------------------------- #
# apply_setup persistence
# --------------------------------------------------------------------------- #
def test_apply_setup_persists_api_key_ref(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = OnboardingService(paths=paths)
    plan = service.build_tier_plan(
        "lightning",
        {
            "provider": {
                "kind": "anthropic",
                "model": "claude-haiku-4-5",
                "api_key_env": "ANTHROPIC_API_KEY",
                "api_key_ref": "ANTHROPIC_API_KEY",
            },
            "permission_tier": "guided",
        },
    )
    service.apply_setup(plan)
    config = load_effective_config(paths=paths)
    assert config.provider.api_key_ref == "ANTHROPIC_API_KEY"


# --------------------------------------------------------------------------- #
# Controller key flow
# --------------------------------------------------------------------------- #
def test_provider_key_status_ok_with_pasted_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctrl = _controller(tmp_path)
    ctrl.set_answer("provider", {"kind": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"})
    ctrl.set_api_key("sk-ant-test123")
    ok, _msg = ctrl.provider_key_status()
    assert ok is True


def test_provider_key_status_missing_without_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ctrl = _controller(tmp_path)
    ctrl.set_answer("provider", {"kind": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"})
    ok, msg = ctrl.provider_key_status()
    assert ok is False
    assert "No API key" in msg


def test_ollama_is_keyless(tmp_path: Path) -> None:
    ctrl = _controller(tmp_path)
    ctrl.set_answer("provider", {"kind": "ollama"})
    assert ctrl.provider_is_keyless() is True
    assert ctrl.provider_key_status()[0] is True


def test_preselect_provider_from_env(tmp_path: Path) -> None:
    ctrl = _controller(tmp_path)
    ctrl.answers.pop("provider", None)
    ctrl.environment = {"ollama_cli": False, "anthropic_env": True, "openai_env": False}
    ctrl.preselect_provider()
    assert ctrl.answers["provider"]["kind"] == "anthropic"


def test_persist_api_key_sets_env_ref_and_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # also auto-restored on teardown
    ctrl = _controller(tmp_path)
    ctrl.set_answer("provider", {"kind": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"})
    ctrl.set_api_key("sk-ant-secret")

    stored: dict[str, str] = {}

    class _FakeStore:
        def set(self, name: str, value: str, metadata=None) -> None:
            stored[name] = value

    monkeypatch.setattr(app_mod, "create_default_secret_store", lambda: _FakeStore())
    ctrl._persist_api_key_if_pasted()

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-secret"  # immediate boot
    assert ctrl.answers["provider"]["api_key_ref"] == "ANTHROPIC_API_KEY"
    assert stored == {"ANTHROPIC_API_KEY": "sk-ant-secret"}  # persisted to secret store


def test_persist_skips_keyless_provider(tmp_path: Path, monkeypatch) -> None:
    ctrl = _controller(tmp_path)
    ctrl.set_answer("provider", {"kind": "ollama"})
    ctrl.set_api_key("should-be-ignored")
    monkeypatch.setattr(
        app_mod, "create_default_secret_store", lambda: (_ for _ in ()).throw(AssertionError())
    )
    ctrl._persist_api_key_if_pasted()  # must not touch the secret store
    assert "api_key_ref" not in ctrl.answers["provider"]
