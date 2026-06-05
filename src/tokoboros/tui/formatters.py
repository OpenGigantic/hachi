"""Rich renderable builders for the TUI snapshot screens.

These replace the previous raw ``json.dumps`` dumps on the Ops / Memory /
Integrations screens with scannable tables. They are deliberately **pure**
functions over plain dicts (the same shapes the screens already produce via
``asdict``), so they can be unit-tested without launching a Textual app.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from tokoboros.tui import theme

_EMPTY = "—"

_STATUS_COLORS = {
    "completed": "green",
    "ready": "green",
    "running": "cyan",
    "starting": "cyan",
    "failed": "red",
    "pending": "yellow",
    "cancelled": "dim",
}


def _truncate(value: Any, width: int = 60) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _status(value: Any) -> Text:
    label = str(value or "")
    return Text(label or _EMPTY, style=_STATUS_COLORS.get(label, "white"))


def _titled(title: str, *columns: str) -> Table:
    table = Table(title=title, title_justify="left", expand=True, header_style="bold")
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


def _event_detail(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    parts = [f"{k}={_truncate(v, 30)}" for k, v in list(payload.items())[:4]]
    return " ".join(parts)


def format_jobs(jobs: list[dict[str, Any]], *, title: str) -> Table:
    table = _titled(title, "Name", "Type", "Status", "When")
    if not jobs:
        table.add_row(Text("none", style="dim"), "", "", "")
        return table
    for job in jobs:
        table.add_row(
            _truncate(job.get("name") or job.get("label")),
            _truncate(job.get("job_type") or job.get("kind")),
            _status(job.get("status")),
            _truncate(job.get("started_at") or job.get("created_at") or _EMPTY, 25),
        )
    return table


def format_mcp(mcp: dict[str, Any]) -> Table:
    title = (
        f"MCP — {mcp.get('server_count', 0)} server(s), {mcp.get('total_tool_count', 0)} tool(s)"
    )
    table = _titled(title, "Server", "Status", "Tools")
    servers = mcp.get("servers", []) or []
    if not servers:
        table.add_row(Text("no servers connected", style="dim"), "", "")
        return table
    for server in servers:
        tool_count = server.get("tool_count")
        if tool_count is None:
            tool_count = len(server.get("tools", []) or [])
        table.add_row(_truncate(server.get("name")), _status(server.get("status")), str(tool_count))
    return table


def format_events(events: list[dict[str, Any]], *, title: str = "Recent Events") -> Table:
    table = _titled(title, "Time", "Event", "Detail")
    if not events:
        table.add_row(Text("no events yet", style="dim"), "", "")
        return table
    for event in events:
        table.add_row(
            _truncate(event.get("timestamp"), 19),
            _truncate(event.get("name")),
            _truncate(_event_detail(event.get("payload")), 80),
        )
    return table


def format_tasks(tasks: list[dict[str, Any]]) -> Table:
    table = _titled("Task Ledger", "Skill", "Query", "Summary")
    if not tasks:
        table.add_row(Text("no tasks tracked", style="dim"), "", "")
        return table
    for task in tasks:
        table.add_row(
            _truncate(task.get("skill_name"), 24),
            _truncate(task.get("query"), 40),
            _truncate(task.get("summary"), 60),
        )
    return table


def format_recalls(recalls: list[dict[str, Any]]) -> Table:
    table = _titled("Memory Recall", "Category", "Content", "When")
    if not recalls:
        table.add_row(Text("no facts recalled", style="dim"), "", "")
        return table
    for row in recalls:
        table.add_row(
            _truncate(row.get("category"), 18),
            _truncate(row.get("content"), 60),
            _truncate(row.get("timestamp"), 25),
        )
    return table


def format_ops(
    jobs: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    mcp: dict[str, Any],
    events: list[dict[str, Any]],
) -> RenderableType:
    return Group(
        format_jobs(jobs, title="Active Jobs"),
        Text(""),
        format_jobs(failures, title="Failed Jobs"),
        Text(""),
        format_mcp(mcp),
        Text(""),
        format_events(events),
    )


def format_memory(tasks: list[dict[str, Any]], recalls: list[dict[str, Any]]) -> RenderableType:
    return Group(format_tasks(tasks), Text(""), format_recalls(recalls))


def format_integrations(mcp: dict[str, Any]) -> RenderableType:
    return format_mcp(mcp)


def _kv(label: str, value: Any, *, value_style: str = theme.FOREGROUND) -> Text:
    line = Text()
    line.append(f"{label}  ", style=theme.MUTED)
    line.append(str(value), style=value_style)
    return line


def format_session_info(info: dict[str, Any]) -> RenderableType:
    """Render the live session sidebar (status, context, recent tools).

    Pure over a plain dict so it can be unit-tested without a Textual app.
    Expected keys: status, status_kind, model, tier, workspace, session,
    stream, jobs, approvals, warnings, recent_tools (list of {name, status}).
    """
    status = str(info.get("status", "Idle"))
    status_kind = str(info.get("status_kind", "idle"))

    header = Text()
    header.append("● ", style=theme.status_color(status_kind))
    header.append(status, style="bold")

    context = Table.grid(padding=(0, 1))
    context.add_column(justify="left")
    context.add_row(_kv("model", info.get("model", _EMPTY)))
    context.add_row(_kv("tier", info.get("tier", _EMPTY)))
    context.add_row(_kv("workspace", info.get("workspace", _EMPTY)))
    context.add_row(_kv("session", info.get("session", _EMPTY)))
    context.add_row(_kv("stream", info.get("stream", _EMPTY)))
    if info.get("tokens") is not None:
        tokens = int(info.get("tokens", 0) or 0)
        cost = float(info.get("cost", 0.0) or 0.0)
        context.add_row(_kv("tokens", f"{tokens:,}  ~${cost:.4f}", value_style=theme.ACCENT))

    counters = Text()
    counters.append("jobs ", style=theme.MUTED)
    counters.append(f"{info.get('jobs', 0)}", style=theme.ACCENT)
    counters.append("   appr ", style=theme.MUTED)
    appr = int(info.get("approvals", 0) or 0)
    counters.append(f"{appr}", style=theme.WARNING if appr else theme.MUTED)
    counters.append("   warn ", style=theme.MUTED)
    warn = int(info.get("warnings", 0) or 0)
    counters.append(f"{warn}", style=theme.ERROR if warn else theme.MUTED)

    recent = info.get("recent_tools") or []
    tool_lines: list[RenderableType] = []
    if recent:
        for tool in recent[-6:]:
            row = Text()
            tstatus = str(tool.get("status", "running"))
            row.append(f"{theme.status_glyph(tstatus)} ", style=theme.status_color(tstatus))
            row.append(_truncate(tool.get("name"), 22), style=theme.FOREGROUND)
            tool_lines.append(row)
    else:
        tool_lines.append(Text("no tool activity yet", style=theme.MUTED))

    return Group(
        header,
        Text(""),
        context,
        Text(""),
        counters,
        Rule(title="recent tools", style=theme.BORDER_MUTED, characters="·"),
        *tool_lines,
    )


def format_settings(
    session_state: dict[str, Any],
    config: dict[str, Any],
    sessions: list[dict[str, Any]],
) -> RenderableType:
    """Render the Settings screen as scannable panels instead of a raw json dump."""
    provider = config.get("provider", {}) if isinstance(config, dict) else {}
    permissions = config.get("permissions", {}) if isinstance(config, dict) else {}
    memory = config.get("memory", {}) if isinstance(config, dict) else {}

    overview = Table.grid(padding=(0, 2))
    overview.add_column(justify="left")
    overview.add_row(_kv("session", session_state.get("session_id", _EMPTY)))
    overview.add_row(_kv("provider", provider.get("kind", _EMPTY)))
    overview.add_row(_kv("model", provider.get("model", _EMPTY)))
    overview.add_row(_kv("permission tier", permissions.get("tier", _EMPTY)))
    overview.add_row(_kv("memory", "on" if memory.get("enable_semantic_memory") else "off"))

    sessions_table = _titled("Recent Sessions", "Session", "Started")
    if not sessions:
        sessions_table.add_row(Text("none", style="dim"), "")
    else:
        for row in sessions[:8]:
            sessions_table.add_row(
                _truncate(row.get("session_id") or row.get("id"), 28),
                _truncate(row.get("created_at") or row.get("started_at"), 25),
            )

    raw = json.dumps(
        {
            "provider": provider,
            "permissions": permissions,
            "memory": memory,
            "capabilities": config.get("capabilities", {}) if isinstance(config, dict) else {},
        },
        indent=2,
    )

    return Group(
        Rule(title="Session", style=theme.BORDER_MUTED, characters="·"),
        overview,
        Text(""),
        sessions_table,
        Text(""),
        Rule(title="Raw config", style=theme.BORDER_MUTED, characters="·"),
        Text(raw, style=theme.MUTED),
    )
