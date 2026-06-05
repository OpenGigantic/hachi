"""Tests for the TUI snapshot formatters.

These render to plain text via a Rich console so we can assert the table content
deterministically without launching a Textual app.
"""

from __future__ import annotations

from rich.console import Console, RenderableType

from tokoboros.tui.formatters import (
    format_events,
    format_integrations,
    format_jobs,
    format_memory,
    format_ops,
)


def _render(renderable: RenderableType, width: int = 120) -> str:
    console = Console(width=width, record=True, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def test_jobs_table_lists_rows() -> None:
    out = _render(
        format_jobs(
            [{"name": "sync_memory", "job_type": "persist", "status": "running"}],
            title="Active Jobs",
        )
    )
    assert "Active Jobs" in out
    assert "sync_memory" in out
    assert "running" in out


def test_jobs_table_handles_empty() -> None:
    out = _render(format_jobs([], title="Failed Jobs"))
    assert "Failed Jobs" in out
    assert "none" in out


def test_events_table_shows_detail() -> None:
    out = _render(
        format_events(
            [
                {
                    "timestamp": "2026-06-05T09:00:00",
                    "name": "tool.call.started",
                    "payload": {"name": "read_file"},
                }
            ]
        )
    )
    assert "tool.call.started" in out
    assert "read_file" in out


def test_integrations_summarizes_mcp() -> None:
    mcp = {
        "server_count": 1,
        "total_tool_count": 3,
        "servers": [{"name": "filesystem", "status": "ready", "tool_count": 3}],
    }
    out = _render(format_integrations(mcp))
    assert "filesystem" in out
    assert "ready" in out
    assert "3" in out


def test_ops_groups_all_sections() -> None:
    out = _render(
        format_ops(
            jobs=[{"name": "j1", "job_type": "background", "status": "running"}],
            failures=[],
            mcp={"server_count": 0, "total_tool_count": 0, "servers": []},
            events=[],
        )
    )
    assert "Active Jobs" in out
    assert "Failed Jobs" in out
    assert "MCP" in out
    assert "Recent Events" in out


def test_memory_groups_tasks_and_recall() -> None:
    out = _render(
        format_memory(
            tasks=[{"skill_name": "deploy", "query": "ship it", "summary": "done"}],
            recalls=[
                {"category": "user", "content": "prefers dark mode", "timestamp": "2026-06-05"}
            ],
        )
    )
    assert "Task Ledger" in out
    assert "deploy" in out
    assert "Memory Recall" in out
    assert "dark mode" in out
