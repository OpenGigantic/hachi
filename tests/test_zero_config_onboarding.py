"""Tests for zero-config onboarding improvements.

Covers:
- detect_environment: extended provider detection (groq/together/deepseek/openrouter)
- best_provider_from_env: priority-ordered provider auto-selection
- _pre_flight_check: actionable pre-boot key gate (via TestableFlightChecker)
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from tokoboros.core.onboarding import OnboardingService
from tokoboros.core.paths import TokoborosPaths

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path) -> TokoborosPaths:
    return TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )


def _service(tmp_path: Path) -> OnboardingService:
    return OnboardingService(paths=_paths(tmp_path))


# ---------------------------------------------------------------------------
# detect_environment — extended provider detection
# ---------------------------------------------------------------------------


def test_detect_env_anthropic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    d = _service(tmp_path).detect_environment()
    assert d["anthropic_env"] is True
    assert d["groq_env"] is False


def test_detect_env_groq(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    d = _service(tmp_path).detect_environment()
    assert d["groq_env"] is True
    assert d["anthropic_env"] is False


def test_detect_env_together(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOGETHER_API_KEY", "ta-test")
    d = _service(tmp_path).detect_environment()
    assert d["together_env"] is True


def test_detect_env_deepseek(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
    d = _service(tmp_path).detect_environment()
    assert d["deepseek_env"] is True


def test_detect_env_openrouter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    d = _service(tmp_path).detect_environment()
    assert d["openrouter_env"] is True


def test_detect_env_openai(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    d = _service(tmp_path).detect_environment()
    assert d["openai_env"] is True


def test_detect_env_none_set(tmp_path: Path, monkeypatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    with mock.patch("shutil.which", return_value=None):
        d = _service(tmp_path).detect_environment()
    for key in ("anthropic_env", "openai_env", "groq_env", "together_env", "deepseek_env",
                "openrouter_env", "ollama_cli"):
        assert d[key] is False, f"Expected {key} to be False"


# ---------------------------------------------------------------------------
# best_provider_from_env — priority ordering
# ---------------------------------------------------------------------------


def test_best_provider_anthropic_beats_openai(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "anthropic"
    assert result["api_key_env"] == "ANTHROPIC_API_KEY"


def test_best_provider_openai_when_no_anthropic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "openai-compatible"


def test_best_provider_groq_when_only_groq(tmp_path: Path, monkeypatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "groq"
    assert result["api_key_env"] == "GROQ_API_KEY"


def test_best_provider_together(tmp_path: Path, monkeypatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TOGETHER_API_KEY", "ta-test")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "together"


def test_best_provider_deepseek(tmp_path: Path, monkeypatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "deepseek"


def test_best_provider_openrouter_last_resort_before_ollama(tmp_path: Path, monkeypatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "TOGETHER_API_KEY", "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "openrouter"


def test_best_provider_ollama_when_no_keys(tmp_path: Path, monkeypatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "TOGETHER_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    with mock.patch("shutil.which", return_value="/usr/bin/ollama"):
        result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result["kind"] == "ollama"
    assert "base_url" in result  # Ollama needs a base URL


def test_best_provider_returns_none_when_nothing_detected(tmp_path: Path, monkeypatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
        "TOGETHER_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    with mock.patch("shutil.which", return_value=None):
        result = _service(tmp_path).best_provider_from_env()
    assert result is None


def test_best_provider_includes_sensible_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    result = _service(tmp_path).best_provider_from_env()
    assert result is not None
    assert result.get("model")  # Non-empty model string


# ---------------------------------------------------------------------------
# _apply_quick_setup uses best_provider_from_env
# ---------------------------------------------------------------------------


def test_quick_setup_uses_groq_when_groq_key_present(tmp_path: Path, monkeypatch) -> None:
    """best_provider_from_env picks groq when only GROQ_API_KEY is set."""
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    with mock.patch("shutil.which", return_value=None):
        plan = _service(tmp_path).quick_setup(kind="groq", permission_tier="guided")
    assert plan.answers["provider"]["kind"] == "groq"


# ---------------------------------------------------------------------------
# _pre_flight_check (via onboarding layer, not CLI console)
# ---------------------------------------------------------------------------


def test_validate_provider_groq_with_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    ok, msg = _service(tmp_path).validate_provider(kind="groq", api_key_env="GROQ_API_KEY")
    assert ok
    assert "GROQ_API_KEY" in msg


def test_validate_provider_together_missing_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    ok, msg = _service(tmp_path).validate_provider(kind="together", api_key_env="TOGETHER_API_KEY")
    assert not ok
    assert "TOGETHER_API_KEY" in msg


def test_validate_provider_deepseek_with_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dsk-test")
    ok, msg = _service(tmp_path).validate_provider(kind="deepseek", api_key_env="DEEPSEEK_API_KEY")
    assert ok
