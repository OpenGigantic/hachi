"""Regression tests for T-03 / BB-124 — config writers must persist the FILE
layer only, never the merged (defaults + file + env + CLI) view.

Every writer used to call ``load_effective_config()`` (which merges built-in
defaults + the settings file + environment variables/.env + CLI overrides)
and hand the whole merged ``ResolvedConfig`` to ``write_global_config()``,
which serialises ALL of it back to ``settings.toml``. So a transient
environment override — including ``HACHI_PERMISSION_TIER``, the agent's
safety posture — got baked permanently into the user's config file the next
time ANY unrelated setting was saved, anywhere in the CLI, the gateway, or
even via the LLM-facing ``set_agent_config`` tool.

These tests drive REAL production writers (not the raw config_loader
functions in isolation) against a real settings.toml under ``tmp_path``, so
they prove the actual entry points are fixed — not just that the underlying
mechanism could, in principle, be used correctly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hachi.core.paths import HachiPaths


def _paths(tmp_path: Path) -> HachiPaths:
    return HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )


def test_env_override_is_not_persisted_by_unrelated_write(tmp_path: Path, monkeypatch) -> None:
    """A transient HACHI_PERMISSION_TIER must NOT be baked into settings.toml by an
    unrelated settings write.

    Config precedence promises env overrides the file *temporarily* (see
    ``load_effective_config``'s docstring: "environment variables" sit above
    the file and below CLI flags). Writers that loaded the merged view and
    persisted it converted that override into durable state — silently
    downgrading the agent's safety posture with no prompt, no audit row, and
    no way to tell it happened by looking at the UI.

    ``add_or_update_provider`` (gateway/provider_admin.py) is a real,
    production writer — chosen instead of calling ``load_effective_config`` /
    ``write_global_config`` directly so this proves an actual entry point is
    fixed, not just the underlying mechanism in isolation.
    """
    from hachi.gateway.provider_admin import add_or_update_provider

    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.global_config_toml.write_text('[agent]\nname = "Hachi"\n', encoding="utf-8")

    monkeypatch.setenv("HACHI_PERMISSION_TIER", "unrestricted")

    with patch("hachi.core.config_loader.get_hachi_paths", return_value=paths):
        # An unrelated write: change the primary provider's model. Nothing
        # about this touches permissions.
        add_or_update_provider("__primary__", {"kind": "anthropic", "model": "claude-opus-4-8"})

    text = paths.global_config_toml.read_text(encoding="utf-8")
    assert "unrestricted" not in text, f"env override leaked into settings.toml:\n{text}"
    assert "[permissions]" not in text, f"unwritten section persisted:\n{text}"
    # The actual mutation still landed.
    assert "claude-opus-4-8" in text
    # The untouched [agent] section from the seed file must survive verbatim.
    assert 'name = "Hachi"' in text


def test_env_override_is_not_persisted_by_set_agent_config_tool(
    tmp_path: Path, monkeypatch
) -> None:
    """The LLM-facing set_agent_config tool must not leak env state either.

    set_agent_config is a tool the model can call on its own initiative (e.g.
    to change agent.name) — CF-10's review missed that this path runs the
    same load-merged -> write-merged cycle as every other writer
    (tools/config_tools.py:721-732, the INJECTED writer).
    """
    from hachi.core.llm import ToolCall
    from hachi.core.loop import ToolRegistry
    from hachi.tools.config_tools import register_config_tools

    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.global_config_toml.write_text('[agent]\nname = "Hachi"\n', encoding="utf-8")

    monkeypatch.setenv("HACHI_PERMISSION_TIER", "unrestricted")

    registry = ToolRegistry()
    with patch("hachi.core.config_loader.get_hachi_paths", return_value=paths):
        register_config_tools(registry)
        result = registry.execute(
            ToolCall(
                id="tc-1",
                name="set_agent_config",
                input={"key": "agent.name", "value": "Renamed"},
            )
        )

    assert "updated" in result.lower(), result

    text = paths.global_config_toml.read_text(encoding="utf-8")
    assert "unrestricted" not in text, f"env override leaked into settings.toml:\n{text}"
    assert "[permissions]" not in text, f"unwritten section persisted:\n{text}"
    assert "Renamed" in text
