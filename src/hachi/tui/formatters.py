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

from hachi.tui import theme

_EMPTY = "—"

_STATUS_COLORS = {
    "completed": "green",
    "ready": "green",
    "success": "green",
    "allow": "green",
    "running": "cyan",
    "starting": "cyan",
    "failed": "red",
    "error": "red",
    "deny": "red",
    "pending": "yellow",
    "timeout": "yellow",
    "requested": "yellow",
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


def format_usage(usage: dict[str, Any], model: str = "") -> Table:
    """Token + cost breakdown for the Ops dashboard (cache-read tokens called out)."""
    table = _titled("Usage & Cost", "Metric", "Value")
    if not usage or not usage.get("call_count"):
        table.add_row(Text("no LLM calls yet", style="dim"), "")
        return table

    def _n(key: str) -> str:
        return f"{int(usage.get(key, 0) or 0):,}"

    table.add_row("model", _truncate(model or _EMPTY, 30))
    table.add_row("LLM calls", str(usage.get("call_count", 0)))
    table.add_row("input tokens", _n("input_tokens"))
    table.add_row("output tokens", _n("output_tokens"))
    table.add_row(
        Text("cache-read tokens", style="green"),
        Text(
            f"{int(usage.get('cache_read_tokens', 0) or 0):,}  (served from cache)", style="green"
        ),
    )
    table.add_row("cache-write tokens", _n("cache_creation_tokens"))
    table.add_row(Text("total tokens", style="bold"), Text(_n("total_tokens"), style="bold"))
    cost = float(usage.get("estimated_cost_usd", 0.0) or 0.0)
    table.add_row(Text("est. cost", style=theme.ACCENT), Text(f"~${cost:.4f}", style=theme.ACCENT))
    return table


def format_subagents(reports: list[dict[str, Any]]) -> Table:
    """Sub-agent / delegation execution reports (role, status, iters, duration, summary)."""
    table = _titled("Sub-agents (delegations)", "Role", "Status", "Iters", "Duration", "Summary")
    if not reports:
        table.add_row(Text("no delegations yet", style="dim"), "", "", "", "")
        return table
    for report in reports:
        duration = report.get("duration_seconds")
        duration_s = f"{float(duration):.1f}s" if isinstance(duration, int | float) else _EMPTY
        table.add_row(
            _truncate(report.get("role"), 16),
            _status(report.get("status")),
            str(report.get("iterations") or ""),
            duration_s,
            _truncate(report.get("summary"), 50),
        )
    return table


def format_permissions(audit: list[dict[str, Any]]) -> Table:
    """Recent permission-gate decisions (tool, operation, target, decision)."""
    table = _titled("Permission Audit", "Tool", "Operation", "Target", "Decision")
    if not audit:
        table.add_row(Text("no permission checks", style="dim"), "", "", "")
        return table
    for row in audit:
        table.add_row(
            _truncate(row.get("tool"), 20),
            _truncate(row.get("operation"), 14),
            _truncate(row.get("target"), 36),
            _status(row.get("decision")),
        )
    return table


def _fmt_duration(seconds: Any) -> str:
    try:
        value = float(seconds or 0.0)
    except (TypeError, ValueError):
        return _EMPTY
    if value <= 0:
        return _EMPTY
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.1f}s"


def format_tool_timeline(calls: list[dict[str, Any]]) -> Table:
    """Recent tool calls as a timeline: tool, status, duration, result preview."""
    table = _titled("Tool Timeline", "Tool", "Status", "Duration", "Detail")
    if not calls:
        table.add_row(Text("no tool calls yet", style="dim"), "", "", "")
        return table
    # Newest first so the most recent activity is at the top of the panel.
    for call in reversed(calls):
        table.add_row(
            _truncate(call.get("name"), 22),
            _status(call.get("status")),
            _fmt_duration(call.get("duration_seconds")),
            _truncate(call.get("detail"), 44),
        )
    return table


def format_ops(
    jobs: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    mcp: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    usage: dict[str, Any] | None = None,
    subagents: list[dict[str, Any]] | None = None,
    permissions: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    model: str = "",
) -> RenderableType:
    """Compose the Ops dashboard. The new panels render only when their data is
    provided, so the original 4-arg call (jobs/failures/mcp/events) still works."""
    sections: list[RenderableType] = []
    if usage is not None:
        sections.extend([format_usage(usage, model), Text("")])
    if tool_calls is not None:
        sections.extend([format_tool_timeline(tool_calls), Text("")])
    if subagents is not None:
        sections.extend([format_subagents(subagents), Text("")])
    sections.extend(
        [
            format_jobs(jobs, title="Active Jobs"),
            Text(""),
            format_jobs(failures, title="Failed Jobs"),
            Text(""),
        ]
    )
    if permissions is not None:
        sections.extend([format_permissions(permissions), Text("")])
    sections.extend([format_mcp(mcp), Text(""), format_events(events)])
    return Group(*sections)


def format_skills(skills: list[dict[str, Any]]) -> Table:
    """Loaded .og skill inventory with usage stats (the ObjectGraph moat)."""
    table = _titled("Loaded Skills (.og)", "Skill", "Role", "Keywords", "Used", "Last used")
    if not skills:
        table.add_row(Text("no skills loaded", style="dim"), "", "", "", "")
        return table
    for skill in skills:
        used = int(skill.get("use_count", 0) or 0)
        table.add_row(
            _truncate(skill.get("name"), 28),
            _truncate(skill.get("role"), 14),
            str(skill.get("keyword_count", 0)),
            Text(str(used), style="dim" if used == 0 else theme.ACCENT),
            _truncate(skill.get("last_used") or _EMPTY, 25),
        )
    return table


def format_memory(
    tasks: list[dict[str, Any]],
    recalls: list[dict[str, Any]],
    skills: list[dict[str, Any]] | None = None,
    diffs: list[dict[str, Any]] | None = None,
) -> RenderableType:
    parts: list[RenderableType] = [format_tasks(tasks), Text(""), format_recalls(recalls)]
    if skills is not None:
        parts.extend([Text(""), format_skills(skills)])
    if diffs is not None:
        parts.extend([Text(""), format_memory_diffs(diffs)])
    return Group(*parts)


def format_memory_diffs(diffs: list[dict[str, Any]]) -> Table:
    """Reviewable gardener diff cards: proposals the user needs to approve or reject."""
    table = _titled(
        "Memory Diffs — pending review", "Kind", "Node A", "Node B", "Confidence", "Reason"
    )
    if not diffs:
        table.add_row(Text("none pending", style="dim"), "", "", "", "")
        return table
    _KIND_COLORS = {
        "dedup_same_name": "cyan",
        "merge_similar": "yellow",
        "supersede_contradiction": "magenta",
        "expire_stale": "red",
    }
    for diff in diffs:
        kind = str(diff.get("kind", ""))
        conf_raw = diff.get("confidence", 1.0)
        conf = f"{float(conf_raw or 0):.0%}"
        table.add_row(
            Text(kind, style=_KIND_COLORS.get(kind, "white")),
            _truncate(diff.get("name_a"), 24),
            _truncate(diff.get("name_b") or _EMPTY, 24),
            conf,
            _truncate(diff.get("reason"), 50),
        )
    return table


def format_og_explorer(data: dict[str, Any]) -> RenderableType:
    """OG Explorer screen: library tree with pass-depth counters and usage stats."""
    skills: list[dict[str, Any]] = data.get("skills", [])
    counters: dict[str, Any] = data.get("counters", {})
    node_count: int = int(data.get("node_count", 0))

    # Header: pass-depth summary (the moat made visible)
    p1 = int(counters.get("pass1_search", 0))
    p2 = int(counters.get("pass2_peek", 0))
    p3 = int(counters.get("pass3_resolve", 0))
    total = max(1, p1)
    p2_pct = f"{p2 / total:.0%}" if total else "—"
    p3_pct = f"{p3 / total:.0%}" if total else "—"

    header = Table.grid(expand=True)
    header.add_column()
    header.add_row(
        Text(
            f"OG Library · {node_count} nodes  |  "
            f"Pass 1: {p1} searches · Pass 2: {p2} ({p2_pct}) · Pass 3: {p3} ({p3_pct})",
            style="bold",
        )
    )

    # Skills table with progressive-disclosure indicators
    table = _titled(
        "Skills (sorted by use count)", "Skill", "Role", "Keywords", "Pass depth", "Used"
    )
    if not skills:
        table.add_row(Text("no skills loaded", style="dim"), "", "", "", "")
    for skill in skills:
        used = int(skill.get("use_count", 0) or 0)
        kw_count = int(skill.get("keyword_count", 0) or 0)
        pass_depth = "P1" if used == 0 else ("P2" if used < 5 else "P3")
        _p2_style = "yellow" if pass_depth == "P2" else "green"
        _depth_style = "dim" if pass_depth == "P1" else _p2_style
        table.add_row(
            _truncate(skill.get("name"), 30),
            _truncate(skill.get("role"), 14),
            str(kw_count),
            Text(pass_depth, style=_depth_style),
            Text(str(used), style="dim" if used == 0 else theme.ACCENT),
        )

    return Group(header, Text(""), table)


def format_integrations(mcp: dict[str, Any]) -> RenderableType:
    return format_mcp(mcp)


def format_status_bar(
    *,
    eye: str,
    eye_color: str,
    state: str,
    model: str = "",
    tokens: int = 0,
    cost: float = 0.0,
) -> Text:
    """The always-visible header bar: hachi ▸  {state}  ·  {model}   {tokens} tok   spent ~${cost}.

    Layout: ``hachi`` wordmark (bold ink) + space + ``▸`` prompt in eye_color,
    then the status word (muted), then model (muted), then token/cost in navy
    (ACCENT) — omitted until the first turn produces usage.

    Pure (no Textual) so it can be unit-tested via a Rich console.
    """
    bar = Text()
    # "hachi ▸" — wordmark bold ink, then glyph in the state color
    bar.append(theme.HACHI_WORDMARK, style=f"bold {theme.FOREGROUND}")
    bar.append(f" {eye}", style=eye_color)
    # status word in muted (working / on watch / done / blocked)
    bar.append(f"  {theme.status_word(state)}", style=theme.MUTED)
    # model in muted, separated by a dim dot — NOT red
    if model:
        bar.append("  ·  ", style=theme.MUTED)
        bar.append(str(model), style=theme.MUTED)
    # tokens + cost in navy (ACCENT) — omitted until usage arrives
    if tokens:
        bar.append(f"   {int(tokens):,} tok", style=theme.ACCENT)
        bar.append(f"   spent ~${float(cost):.4f}", style=theme.ACCENT)
    return bar


def format_search_results(query: str, hits: list[dict[str, Any]]) -> RenderableType:
    """Render in-conversation transcript search hits (or a friendly miss)."""
    if not hits:
        return Text(f'No matches for "{query}" in this conversation.', style=theme.MUTED)
    total = sum(int(h.get("count", 1) or 1) for h in hits)
    table = _titled(
        f'{total} match(es) for "{query}"  ·  {len(hits)} message(s)',
        "#",
        "Role",
        "Match",
    )
    for hit in hits:
        table.add_row(
            str(hit.get("index", "")),
            _truncate(hit.get("role"), 12),
            _truncate(hit.get("snippet"), 72),
        )
    return table


def format_welcome() -> RenderableType:
    """The friendly first-run greeting shown above the clickable starter prompts.

    Hachi voice: sentence case, no emoji, no ALL CAPS. Addresses the user as
    "you". Keeps the word "suggestion" so tests and UI copy remain consistent.
    """
    title = Text()
    title.append(f"{theme.GLYPH_PROMPT} ", style=theme.BRAND)
    title.append("hachi is on watch", style=f"bold {theme.FOREGROUND}")
    subtitle = Text(
        "your local agent, here on your machine. "
        "you hold the leash — type a prompt, or try a suggestion:",
        style=theme.MUTED,
    )
    return Group(title, Text(""), subtitle)


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


def format_status_strip(info: dict[str, Any]) -> Text:
    """Compact always-visible governance strip: state, jobs, approvals, spend.

    Pure over a plain dict so it can be unit-tested without a Textual app.
    Expected keys: status, status_kind, jobs, approvals, warnings, cost (opt).
    """
    from hachi.tui import theme as _theme

    strip = Text()
    status_kind = str(info.get("status_kind", "idle"))
    state = str(info.get("status", "Idle"))
    strip.append("● ", style=_theme.status_color(status_kind))
    strip.append(state, style="bold")

    jobs = int(info.get("jobs", 0) or 0)
    strip.append("   jobs:", style=_theme.MUTED)
    strip.append(str(jobs), style=_theme.ACCENT if jobs else _theme.MUTED)

    appr = int(info.get("approvals", 0) or 0)
    strip.append("   appr:", style=_theme.MUTED)
    appr_style = _theme.WARNING if appr else _theme.MUTED
    strip.append(f"{appr}{'!' if appr else ''}", style=appr_style)

    warn = int(info.get("warnings", 0) or 0)
    if warn:
        strip.append("   warn:", style=_theme.MUTED)
        strip.append(str(warn), style=_theme.ERROR)

    if info.get("cost") is not None:
        cost = float(info.get("cost", 0.0) or 0.0)
        strip.append(f"   spend ~${cost:.4f}", style=_theme.ACCENT)

    return strip


def format_spend(
    usage: dict[str, Any],
    model: str = "",
    events: list[dict[str, Any]] | None = None,
) -> RenderableType:
    """Full token/cost breakdown for the Spend screen.

    ``usage`` is a CostSnapshot.to_dict() (input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, total_tokens, estimated_cost_usd,
    call_count). ``events`` is a list of RuntimeEventSnapshot dicts used to
    surface compaction events.
    """
    from rich.rule import Rule as _Rule

    if not usage or not usage.get("call_count"):
        empty = Text("No LLM calls yet — start a conversation to see costs.", style=theme.MUTED)
        return Group(empty)

    def _n(key: str) -> int:
        return int(usage.get(key, 0) or 0)

    def _usd(tok: int, rate_per_mtok: float) -> float:
        return (tok / 1_000_000) * rate_per_mtok

    calls = int(usage.get("call_count", 0))
    suffix = "s" if calls != 1 else ""
    title = f"Token Cost — {model or 'unknown model'}   ({calls} LLM call{suffix})"

    breakdown = Table(title=title, title_justify="left", expand=True, header_style="bold")
    breakdown.add_column("Token type", overflow="fold")
    breakdown.add_column("Tokens", justify="right")
    breakdown.add_column("Rate", justify="right")
    breakdown.add_column("Est. USD", justify="right", style=theme.ACCENT)

    inp = _n("input_tokens")
    out = _n("output_tokens")
    cr = _n("cache_read_tokens")
    cw = _n("cache_creation_tokens")
    total = _n("total_tokens")
    total_cost = float(usage.get("estimated_cost_usd", 0.0) or 0.0)

    breakdown.add_row(
        Text("input (fresh)", style=theme.FOREGROUND),
        f"{inp:,}",
        "$3.00/Mtok",
        f"~${_usd(inp, 3.0):.4f}",
    )
    breakdown.add_row(
        Text("cache-read", style="green"),
        f"{cr:,}",
        "$0.30/Mtok",
        Text(f"~${_usd(cr, 0.30):.4f}", style="green"),
    )
    breakdown.add_row(
        Text("cache-write", style=theme.MUTED),
        f"{cw:,}",
        "$3.75/Mtok",
        f"~${_usd(cw, 3.75):.4f}",
    )
    breakdown.add_row(
        Text("output", style=theme.FOREGROUND),
        f"{out:,}",
        "$15.00/Mtok",
        f"~${_usd(out, 15.0):.4f}",
    )
    breakdown.add_section()
    breakdown.add_row(
        Text("total", style="bold"),
        Text(f"{total:,}", style="bold"),
        "",
        Text(f"~${total_cost:.4f}", style=f"bold {theme.ACCENT}"),
    )

    cache_hit_pct = (cr / total * 100) if total else 0.0
    cache_note = Text(
        f"  cache-hit {cache_hit_pct:.1f}% of all tokens served from cache",
        style=theme.MUTED,
    )

    sections: list[RenderableType] = [breakdown, cache_note]

    compaction_events = [e for e in (events or []) if "compact" in str(e.get("name", "")).lower()]
    comp_table = Table(
        title="Context Compaction Events",
        title_justify="left",
        expand=True,
        header_style="bold",
    )
    comp_table.add_column("Time", overflow="fold")
    comp_table.add_column("Detail")
    if compaction_events:
        for ev in compaction_events[-10:]:
            comp_table.add_row(
                _truncate(ev.get("timestamp"), 19),
                _truncate(_event_detail(ev.get("payload")), 80),
            )
    else:
        comp_table.add_row(Text("no compaction events", style="dim"), "")

    sections.extend([Text(""), _Rule(style=theme.BORDER_MUTED, characters="·"), comp_table])
    return Group(*sections)


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
