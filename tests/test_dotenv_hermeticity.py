"""Tests for the HACHI_DISABLE_DOTENV_AUTOLOAD guard in hachi.cli.main.

These tests verify that:
1. When HACHI_DISABLE_DOTENV_AUTOLOAD=1 the _load_dotenv_into_environ function
   is a no-op (real provider keys are never leaked into os.environ).
2. When HACHI_DISABLE_DOTENV_AUTOLOAD=0 the function works normally.
3. The test harness default (set by conftest.py) is "1", proving the suite
   always runs in hermetic mode.
"""

from __future__ import annotations

import os

import pytest

from hachi.cli.main import _load_dotenv_into_environ

# A unique sentinel key unlikely to collide with any real env var or .env entry.
_SENTINEL_KEY = "HACHI_TEST_SENTINEL_KEY_HERMETICITY_CHECK"
_SENTINEL_VALUE = "sentinel-value-abc123"


def test_autoload_skipped_when_flag_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """When HACHI_DISABLE_DOTENV_AUTOLOAD=1 the function must not set any keys."""
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "1")

    dotenv_file = tmp_path / ".env"  # type: ignore[operator]
    dotenv_file.write_text(f"{_SENTINEL_KEY}={_SENTINEL_VALUE}\n", encoding="utf-8")

    # Ensure the key is absent before calling the function.
    assert _SENTINEL_KEY not in os.environ, (
        f"{_SENTINEL_KEY} already in os.environ — test isolation failure"
    )

    _load_dotenv_into_environ(dotenv_file)

    assert _SENTINEL_KEY not in os.environ, (
        "Flag=1: _load_dotenv_into_environ must not set any env vars"
    )


def test_autoload_active_when_flag_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """When HACHI_DISABLE_DOTENV_AUTOLOAD=0 the function merges the .env normally."""
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "0")

    dotenv_file = tmp_path / ".env"  # type: ignore[operator]
    dotenv_file.write_text(f"{_SENTINEL_KEY}={_SENTINEL_VALUE}\n", encoding="utf-8")

    # Ensure the key is absent before calling the function so the non-overwrite
    # logic doesn't hide a bug.
    assert _SENTINEL_KEY not in os.environ, (
        f"{_SENTINEL_KEY} already in os.environ — test isolation failure"
    )

    try:
        _load_dotenv_into_environ(dotenv_file)
        assert os.environ.get(_SENTINEL_KEY) == _SENTINEL_VALUE, (
            "Flag=0: _load_dotenv_into_environ must set the key from .env"
        )
    finally:
        # Clean up the mutation so it doesn't leak to other tests (monkeypatch
        # restores env vars it set, but the function writes directly to os.environ).
        os.environ.pop(_SENTINEL_KEY, None)


def test_conftest_default_flag_is_set() -> None:
    """Regression guard: conftest.py must default HACHI_DISABLE_DOTENV_AUTOLOAD to '1'.

    If this fails the test harness is no longer hermetic — the developer's .env
    would be merged at import time, flipping import-time skipif guards on live-
    gateway tests and leaking real provider secrets into the test process.
    """
    assert os.environ.get("HACHI_DISABLE_DOTENV_AUTOLOAD") == "1", (
        "HACHI_DISABLE_DOTENV_AUTOLOAD must be '1' in the test harness "
        "(set by tests/conftest.py). The suite is not hermetic."
    )


# ---------------------------------------------------------------------------
# CF-17 / T-67 / BB-2: the CLI edge is not the only reader of a CWD .env — two
# core resolvers (core/llm.py, core/oauth.py) grew their own ungated
# `Path.cwd() / ".env"` fallback, independently of cli/main.py's gate. Both must
# honour HACHI_DISABLE_DOTENV_AUTOLOAD too, or a hermetic process still leaks
# ambient creds through the back door.
# ---------------------------------------------------------------------------


def test_llm_resolve_api_key_honours_disable_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """BB-2: with autoload disabled, core must NOT read .env from the CWD."""
    from hachi.core import llm

    (tmp_path / ".env").write_text("HACHI_TEST_ONLY_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HACHI_TEST_ONLY_KEY", raising=False)
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "1")

    assert llm._resolve_api_key("HACHI_TEST_ONLY_KEY") is None


def test_llm_resolve_api_key_still_reads_dotenv_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The capability is kept — only gated. Guards against over-deleting."""
    from hachi.core import llm

    (tmp_path / ".env").write_text("HACHI_TEST_ONLY_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HACHI_TEST_ONLY_KEY", raising=False)
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "0")

    assert llm._resolve_api_key("HACHI_TEST_ONLY_KEY") == "from-dotenv"


def test_oauth_resolve_env_honours_disable_flag(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The reader the audit missed. A fix that only touches llm.py leaves this open."""
    from hachi.core import oauth

    (tmp_path / ".env").write_text("GOOGLE_CLIENT_ID=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "1")

    assert oauth._resolve_env("GOOGLE_CLIENT_ID") is None
