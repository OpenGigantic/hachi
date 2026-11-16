"""Tests for the `hachi models` CLI command — lists provider-exposed models
(live), narrowed by the allowed_models guardrail, marking the active model."""

from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from hachi.cli.main import app

runner = CliRunner()


class _FakeClient:
    def __init__(self, models: list[str]) -> None:
        self._models = models
        self.closed = False

    def list_models(self) -> list[str]:
        return self._models

    def close(self) -> None:
        self.closed = True


def _cfg(*, model: str = "claude-haiku-4-5", allowed: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        provider=SimpleNamespace(
            kind="anthropic",
            model=model,
            allowed_models=allowed or [],
            api_key_ref=None,
            api_key_env="ANTHROPIC_API_KEY",
            base_url=None,
        ),
        providers={},
    )


def _patch(monkeypatch, cfg: SimpleNamespace, discovered: list[str]) -> _FakeClient:
    client = _FakeClient(discovered)
    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: cfg)
    monkeypatch.setattr("hachi.cli.main.create_default_secret_store", lambda: object())
    # create_llm_client is imported inside the command → patch it at its source.
    monkeypatch.setattr("hachi.core.llm.create_llm_client", lambda *a, **k: client)
    return client


def test_models_lists_discovered(monkeypatch) -> None:
    client = _patch(monkeypatch, _cfg(), ["claude-haiku-4-5", "claude-sonnet-4-5"])
    result = runner.invoke(app, ["models", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source"] == "provider"
    assert payload["current"] == "claude-haiku-4-5"
    assert "claude-sonnet-4-5" in payload["models"]
    assert payload["restricted"] is False
    assert client.closed is True  # client is always closed


def test_models_allowlist_caps_and_marks_restricted(monkeypatch) -> None:
    _patch(monkeypatch, _cfg(allowed=["claude-haiku-4-5"]), ["a", "b", "claude-haiku-4-5"])
    result = runner.invoke(app, ["models", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source"] == "allowlist"
    assert payload["restricted"] is True
    assert payload["models"] == ["claude-haiku-4-5"]  # discovery-only entries excluded


def test_models_discovery_unavailable_shows_current(monkeypatch) -> None:
    _patch(monkeypatch, _cfg(), [])  # list_models returns [] (offline / no key)
    result = runner.invoke(app, ["models", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source"] == "configured"
    assert payload["models"] == ["claude-haiku-4-5"]


def test_models_human_readable_marks_current(monkeypatch) -> None:
    _patch(monkeypatch, _cfg(), ["claude-haiku-4-5", "claude-sonnet-4-5"])
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0, result.output
    assert "claude-sonnet-4-5" in result.output


# ---------------------------------------------------------------------------
# P-06 — a missing API key is the expected first-run state, not a crash.
#
# `models` built the LLM client OUTSIDE its try/except, so
# AnthropicClient.__init__ raising ValueError escaped as a Rich traceback and
# the command exited 1. The "live discovery unavailable (offline or no API key)"
# branch further down was unreachable. Precedent for the correct shape:
# the TUI/memory list path (~line 870) wraps construction in try/except.
# ---------------------------------------------------------------------------


def _patch_client_build_failure(monkeypatch) -> None:
    """Make client construction fail the way it does with no credential present."""
    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: _cfg())
    monkeypatch.setattr("hachi.cli.main.create_default_secret_store", lambda: object())

    def _raise(*_args, **_kwargs):
        raise ValueError("ANTHROPIC_API_KEY must be provided or set in the environment.")

    # create_llm_client is imported inside the command, so patch it at its source.
    monkeypatch.setattr("hachi.core.llm.create_llm_client", _raise)


def test_models_without_api_key_exits_zero(monkeypatch) -> None:
    """The command must degrade to the configured model, not crash."""
    _patch_client_build_failure(monkeypatch)

    result = runner.invoke(app, ["models", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source"] == "configured"
    assert payload["models"] == ["claude-haiku-4-5"]


def test_models_without_api_key_shows_no_traceback(monkeypatch) -> None:
    """Human-readable output must be the friendly hint, not a stack trace."""
    _patch_client_build_failure(monkeypatch)

    result = runner.invoke(app, ["models"])

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "ANTHROPIC_API_KEY must be provided" not in result.output
    assert "live discovery unavailable" in result.output
    assert "(current)" in result.output
