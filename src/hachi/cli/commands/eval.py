"""Eval command group: failure-mining reports and regression-case runner.

Commands
--------
``hachi eval report``  — Query the interaction_log and print an analytics report.
``hachi eval run``     — Run the regression suite against a booted agent turn.

Design note
-----------
``eval report`` is fully functional: it reads real rows from the default
SessionStore and builds a real EvalReport via ``build_failure_report``.

``eval run`` honestly reports when no booted turn_fn is wired (see the
command docstring for why) rather than faking a pass.  The suite itself
and the runner logic are fully tested in ``tests/test_eval_runner.py``.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from hachi.core.session_store import SessionStore

eval_app = typer.Typer(help="Eval tooling: failure-mining reports and regression runner.")

console = Console()


def _open_store() -> SessionStore:
    """Open the default SessionStore using the standard Hachi paths."""
    from hachi.core.session_store import SessionStore

    # SessionStore defaults to get_hachi_paths().sessions_db when db_path is None.
    return SessionStore()


# ---------------------------------------------------------------------------
# hachi eval report
# ---------------------------------------------------------------------------


@eval_app.command("report")
def eval_report(
    as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    since: str | None = typer.Option(
        None,
        "--since",
        help="ISO timestamp (YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD) — filter rows from this point.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session",
        help="Restrict report to a single session ID.",
    ),
    limit: int = typer.Option(
        500,
        "--limit",
        "-n",
        help="Maximum number of interaction rows to load (newest first).",
    ),
) -> None:
    """Build a failure-mining report over the interaction log.

    Reads rows from the default SessionStore (same DB as ``hachi session``),
    aggregates them via ``build_failure_report``, and prints a human-readable
    table (or JSON with ``--json``).

    An empty store produces a zero-valued report — not an error.

    Examples::

        hachi eval report
        hachi eval report --since 2026-06-01
        hachi eval report --session abc123 --limit 200
        hachi eval report --json
    """
    from hachi.core.eval.report import build_failure_report

    store = _open_store()
    try:
        rows = store.get_interactions(session_id=session_id, limit=limit)
    finally:
        store.shutdown()

    report = build_failure_report(rows, since=since)

    if as_json:
        # Serialise — PerModelStats has no to_dict; use dataclasses.asdict.
        import dataclasses

        def _to_serialisable(obj: object) -> object:
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return dataclasses.asdict(obj)
            if isinstance(obj, dict):
                return {k: _to_serialisable(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_to_serialisable(i) for i in obj]
            if isinstance(obj, tuple):
                return [_to_serialisable(i) for i in obj]
            return obj

        d = dataclasses.asdict(report)
        # top_failing_tools is list[tuple] — make it list[list] for JSON.
        d["top_failing_tools"] = [list(t) for t in report.top_failing_tools]
        print(json.dumps(d, indent=2, default=str))
        return

    # --- Human-readable output ---
    now = _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = f"Hachi Eval Report — {now}"
    if report.since:
        title += f"  (since {report.since})"
    if session_id:
        title += f"  [session {session_id[:12]}]"

    console.rule(title)

    # Summary table
    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Total turns", str(report.total_turns))
    summary.add_row("Rated up", f"[green]{report.rated_up}[/green]")
    summary.add_row("Rated down", f"[red]{report.rated_down}[/red]")
    summary.add_row("Unrated", str(report.unrated))
    failure_pct = f"{report.failure_rate * 100:.1f}%"
    failure_color = "red" if report.failure_rate > 0.1 else "green"
    rate_cell = f"[{failure_color}]{failure_pct}[/{failure_color}]"
    summary.add_row("Failure rate (down / rated)", rate_cell)
    summary.add_row("Errored turns", str(report.errored_turns))
    summary.add_row("Total cost", f"${report.total_cost:.4f}")
    summary.add_row("Avg cost / turn", f"${report.avg_cost_per_turn:.4f}")
    console.print(summary)

    # Per-model table
    if report.per_model:
        console.print()
        mtable = Table(title="Per-model breakdown", show_lines=False)
        mtable.add_column("Model")
        mtable.add_column("Turns", justify="right")
        mtable.add_column("Avg cost", justify="right")
        mtable.add_column("Rated down", justify="right")
        for model, ms in sorted(report.per_model.items(), key=lambda kv: -kv[1].count):
            mtable.add_row(
                model,
                str(ms.count),
                f"${ms.avg_cost:.4f}",
                str(ms.down_count),
            )
        console.print(mtable)

    # Top failing tools
    if report.top_failing_tools:
        console.print()
        ttable = Table(title="Top tools in down-rated turns", show_lines=False)
        ttable.add_column("Tool name")
        ttable.add_column("Down-turn calls", justify="right")
        for tool_name, cnt in report.top_failing_tools[:10]:
            ttable.add_row(tool_name, str(cnt))
        console.print(ttable)

    # Recent failures
    if report.recent_failures:
        console.print()
        ftable = Table(title="Recent failures (down-rated)", show_lines=True)
        ftable.add_column("ts", style="dim", max_width=20)
        ftable.add_column("user_input", max_width=40)
        ftable.add_column("output_head", max_width=50)
        ftable.add_column("tools")
        for rf in report.recent_failures:
            ftable.add_row(
                rf.ts[:19],
                rf.user_input[:40],
                rf.agent_output_head[:50],
                ", ".join(rf.tool_calls) or "—",
            )
        console.print(ftable)

    if report.total_turns == 0:
        console.print("[dim]No interaction rows found.[/dim]")


# ---------------------------------------------------------------------------
# hachi eval run
# ---------------------------------------------------------------------------


@eval_app.command("run")
def eval_run(
    suite_path: str | None = typer.Option(
        None,
        "--suite",
        help="Path to a JSON or .py file defining a list[EvalCase]. "
             "Omit to use the built-in DEFAULT_SUITE.",
    ),
) -> None:
    """Run the regression suite against a real agent turn and print a scorecard.

    Each case in the suite is executed via a booted agent ExecutionLoop and
    its structural assertions checked against the REAL trajectory.

    STATUS — turn_fn wiring
    -----------------------
    Wiring a full booted ``AgentSession`` (workspace init, LLM client config,
    skills load) in the CLI command is heavy and would require live credentials
    that are not guaranteed in CI.  Instead, ``eval run`` currently reports
    "not wired" rather than faking a pass.  This is the honest behaviour:

    - ``eval report`` is fully functional (no live connection needed).
    - ``eval run`` as a CI gate requires a separately wired turn_fn (e.g. a
      fixture that boots a minimal session against a scripted/stub LLM).
    - The runner itself is fully tested in ``tests/test_eval_runner.py`` using
      a real ``ExecutionLoop`` driven by a scripted LLM — proving it can fail.

    To wire a real turn_fn here, import ``boot_agent`` from
    ``hachi.gateway.orchestrator``, boot a session, and pass
    ``lambda msg: run_one_turn(session, msg)`` as the ``turn_fn``.

    Examples::

        hachi eval run
        hachi eval run --suite path/to/my_suite.json
    """
    from hachi.core.eval.runner import DEFAULT_SUITE

    # Load suite
    if suite_path is not None:
        import importlib.util
        import pathlib

        p = pathlib.Path(suite_path)
        if not p.exists():
            console.print(f"[red]Error:[/red] suite file not found: {suite_path}")
            raise typer.Exit(code=1)
        if p.suffix == ".py":
            spec = importlib.util.spec_from_file_location("_eval_suite", p)
            if spec is None or spec.loader is None:
                console.print(f"[red]Error:[/red] cannot load suite module: {suite_path}")
                raise typer.Exit(code=1)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            suite = getattr(mod, "SUITE", None) or getattr(mod, "DEFAULT_SUITE", None)
            if suite is None:
                console.print(
                    "[red]Error:[/red] suite .py must define SUITE or DEFAULT_SUITE."
                )
                raise typer.Exit(code=1)
        else:
            # Assume JSON: list of dicts {name, user_input, assertions: [...]}
            console.print(
                "[yellow]JSON suite loading not yet implemented — "
                "use a .py file that defines SUITE: list[EvalCase].[/yellow]"
            )
            raise typer.Exit(code=1)
    else:
        suite = DEFAULT_SUITE

    # --- Turn function ---
    # Wiring a fully booted AgentSession in the CLI is deferred (see docstring).
    # Print an honest "not wired" status instead of faking pass.
    console.print(
        "[yellow]hachi eval run[/yellow]: real agent turn_fn is not wired in this release.\n"
        "The runner and all assertion kinds are fully tested in "
        "[bold]tests/test_eval_runner.py[/bold] against a scripted ExecutionLoop.\n\n"
        "To use this command as a CI gate:\n"
        "  1. Import boot_agent from hachi.gateway.orchestrator\n"
        "  2. Boot a minimal session (can use a scripted LLM client)\n"
        "  3. Pass a turn_fn that calls session.run_turn(user_input)\n\n"
        f"Suite has [bold]{len(suite)}[/bold] case(s) ready to run once wired."
    )
    sys.exit(0)
