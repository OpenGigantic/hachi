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
    format_permissions,
    format_subagents,
    format_usage,
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


def test_usage_table_shows_breakdown() -> None:
    usage = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 5000,
        "cache_creation_tokens": 50,
        "total_tokens": 6250,
        "estimated_cost_usd": 0.0123,
        "call_count": 3,
    }
    out = _render(format_usage(usage, model="claude-haiku-4-5"))
    assert "Usage" in out
    assert "claude-haiku-4-5" in out
    assert "cache-read" in out and "cache" in out
    assert "6,250" in out  # total tokens, thousands-separated
    assert "0.0123" in out  # estimated cost


def test_usage_empty_when_no_calls() -> None:
    assert "no LLM calls yet" in _render(format_usage({}))


def test_subagents_table_lists_delegations() -> None:
    out = _render(
        format_subagents(
            [
                {
                    "role": "coder",
                    "status": "success",
                    "iterations": 4,
                    "duration_seconds": 12.5,
                    "summary": "built the API",
                }
            ]
        )
    )
    assert "Sub-agents" in out
    assert "coder" in out
    assert "success" in out
    assert "built the API" in out
    assert "12.5s" in out


def test_subagents_empty() -> None:
    assert "no delegations yet" in _render(format_subagents([]))


def test_permissions_table_lists_audit() -> None:
    out = _render(
        format_permissions(
            [
                {
                    "tool": "run_terminal_command",
                    "operation": "execute",
                    "target": "rm -rf /tmp/x",
                    "decision": "deny",
                }
            ]
        )
    )
    assert "Permission Audit" in out
    assert "run_terminal" in out
    assert "execute" in out
    assert "deny" in out


def test_permissions_empty() -> None:
    assert "no permission checks" in _render(format_permissions([]))


def test_ops_dashboard_includes_new_panels() -> None:
    out = _render(
        format_ops(
            jobs=[],
            failures=[],
            mcp={"server_count": 0, "total_tool_count": 0, "servers": []},
            events=[],
            usage={"call_count": 2, "total_tokens": 100, "estimated_cost_usd": 0.01},
            subagents=[{"role": "researcher", "status": "success", "summary": "found it"}],
            permissions=[{"tool": "write_file", "operation": "write", "decision": "allow"}],
            model="claude-sonnet-4",
        )
    )
    # New panels present alongside the original sections.
    assert "Usage" in out
    assert "Sub-agents" in out
    assert "Permission Audit" in out
    assert "Active Jobs" in out
    assert "MCP" in out


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
