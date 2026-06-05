"""Tests for the TUI rendering helpers and theme palette.

These cover the *pure* builders (no running Textual app required): the theme
status mapping, the sidebar/settings formatters, and the small app-level
helpers that classify tool-call payloads.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from tokoboros.tui import theme
from tokoboros.tui.app import _event_input_summary, _looks_like_error
from tokoboros.tui.formatters import format_session_info, format_settings


def _render(renderable) -> str:
    console = Console(file=StringIO(), width=80, no_color=True)
    console.print(renderable)
    return console.file.getvalue()


# --- theme ---------------------------------------------------------------
def test_status_color_known_and_unknown() -> None:
    assert theme.status_color("completed") == theme.SUCCESS
    assert theme.status_color("running") == theme.ACCENT
    assert theme.status_color("failed") == theme.ERROR
    assert theme.status_color("totally-unknown") == theme.MUTED


def test_status_glyph_maps_lifecycle() -> None:
    assert theme.status_glyph("completed") == theme.GLYPH_DONE
    assert theme.status_glyph("failed") == theme.GLYPH_ERROR
    assert theme.status_glyph("running") == theme.GLYPH_RUNNING
    assert theme.status_glyph(None) == theme.GLYPH_RUNNING


# --- app helpers ---------------------------------------------------------
def test_event_input_summary_truncates_and_joins() -> None:
    summary = _event_input_summary({"role": "coder", "task": "x" * 80})
    assert "role=coder" in summary
    assert "…" in summary  # long value truncated
    assert _event_input_summary({}) == "running…"
    assert _event_input_summary(None) == "running…"


def test_looks_like_error_detects_failures() -> None:
    assert _looks_like_error("Delegation failed: ANTHROPIC_API_KEY must be provided")
    assert _looks_like_error("Traceback (most recent call last)")
    assert not _looks_like_error("4")
    assert not _looks_like_error("ok, computed result")


# --- sidebar formatter ---------------------------------------------------
def test_format_session_info_renders_status_and_tools() -> None:
    out = _render(
        format_session_info(
            {
                "status": "Working",
                "status_kind": "running",
                "model": "claude-haiku-4-5",
                "tier": "guided",
                "workspace": "tokoboros",
                "session": "abc123",
                "stream": "live",
                "jobs": 2,
                "approvals": 1,
                "warnings": 0,
                "recent_tools": [
                    {"name": "delegate_task", "status": "failed"},
                    {"name": "read_file", "status": "completed"},
                ],
            }
        )
    )
    assert "Working" in out
    assert "claude-haiku-4-5" in out
    assert "guided" in out
    assert "delegate_task" in out
    assert "read_file" in out


def test_format_session_info_handles_empty_tools_and_tokens() -> None:
    out = _render(
        format_session_info(
            {
                "status": "Idle",
                "status_kind": "idle",
                "recent_tools": [],
                "tokens": 12345,
                "cost": 0.0123,
            }
        )
    )
    assert "no tool activity yet" in out
    assert "12,345" in out


# --- settings formatter --------------------------------------------------
def test_format_settings_renders_overview_and_raw() -> None:
    out = _render(
        format_settings(
            {"session_id": "sess-123"},
            {
                "provider": {"kind": "anthropic", "model": "claude-haiku-4-5"},
                "permissions": {"tier": "guided"},
                "memory": {"enable_semantic_memory": True},
            },
            [{"session_id": "sess-123", "created_at": "2026-06-05"}],
        )
    )
    assert "sess-123" in out
    assert "anthropic" in out
    assert "guided" in out
    assert "Raw config" in out
