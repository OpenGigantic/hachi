from __future__ import annotations

from pathlib import Path

import pytest

from tokoboros.core.onboarding import OnboardingService
from tokoboros.core.paths import TokoborosPaths


def _paths(tmp_path: Path) -> TokoborosPaths:
    return TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )


def _base_plan() -> dict:
    return {
        "tier": "lightning",
        "steps": ["welcome", "provider", "safety", "finish"],
        "answers": {},
        "enable_memory": False,
        "show_integrations": False,
        "show_advanced": False,
    }


def test_apply_setup_rejects_invalid_provider_kind(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["answers"] = {"provider": {"kind": "not-a-provider", "model": "x"}}

    with pytest.raises(ValueError, match="provider.kind must be one of"):
        service.apply_setup(payload)


def test_apply_setup_rejects_invalid_permission_tier(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["answers"] = {"permission_tier": "root"}

    with pytest.raises(ValueError, match="permission_tier must be one of"):
        service.apply_setup(payload)


def test_apply_setup_rejects_non_boolean_memory_flags(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["answers"] = {"memory": {"enable_session_history": "false"}}

    with pytest.raises(ValueError, match="memory.enable_session_history must be a boolean"):
        service.apply_setup(payload)


def test_apply_setup_rejects_invalid_tier_value(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["tier"] = "turbo"

    with pytest.raises(ValueError):
        service.apply_setup(payload)


def test_apply_setup_rejects_invalid_step_name(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["steps"] = ["welcome", "provider", "safety", "warp", "finish"]

    with pytest.raises(ValueError):
        service.apply_setup(payload)


def test_apply_setup_rejects_non_boolean_integration_flags(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["answers"] = {"integrations": {"slack": "enabled"}}

    with pytest.raises(ValueError, match="integrations.slack must be a boolean"):
        service.apply_setup(payload)


def test_apply_setup_rejects_non_boolean_integration_enabled_field(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload["answers"] = {"integrations": {"slack": {"enabled": "yes"}}}

    with pytest.raises(ValueError, match="integrations.slack.enabled must be a boolean"):
        service.apply_setup(payload)


def test_apply_setup_rejects_missing_required_tier(tmp_path: Path) -> None:
    service = OnboardingService(paths=_paths(tmp_path))
    payload = _base_plan()
    payload.pop("tier")

    with pytest.raises(ValueError):
        service.apply_setup(payload)
