"""Memory command group: inspect and manage the agent's long-term memory vault."""

from __future__ import annotations

import json

import typer
from rich.console import Console

console = Console()

memory_app = typer.Typer(help="Inspect the agent's long-term memory vault.")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Natural-language query to search memory for."),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Max number of results."),
    profile: bool = typer.Option(
        False, "--profile", help="Search the user_profile collection instead of core memory."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
):
    """Semantic search over the long-term memory vault from the terminal.

    Searches core_memory by default (what the agent has learned about the
    workspace); --profile searches the bounded user_profile (identity/prefs)."""
    from tokoboros.core.memory_vault import MemoryVault

    vault = MemoryVault()
    if profile:
        rows = vault.search_user_profile(query, top_k)
    else:
        rows = vault.semantic_search(query, top_k)
    scope = "user profile" if profile else "core memory"

    if as_json:
        print(json.dumps({"query": query, "scope": scope, "results": rows}, indent=2))
        return

    if not rows:
        console.print(f"[dim]No {scope} facts matched:[/dim] {query}")
        return
    console.print(f"[bold]{len(rows)}[/bold] {scope} match(es) for [cyan]{query}[/cyan]:")
    for row in rows:
        ts = row.get("timestamp", "")
        category = row.get("category", "")
        console.print(
            f"  [dim]{ts}[/dim] [magenta]{category}[/magenta]  {row.get('raw_content', '')}"
        )


@memory_app.command("stats")
def memory_stats(
    as_json: bool = typer.Option(False, "--json", help="Emit counts as JSON."),
):
    """Show how many facts are stored in each memory collection."""
    from tokoboros.core.memory_vault import MemoryVault

    vault = MemoryVault()
    stats = {
        "core_memory": vault.collection.count(),
        "user_profile": vault.user_collection.count(),
        "agent_profile": vault.agent_profile.count(),
        "delegation_log": vault.delegation_collection.count(),
        "quarantine": vault.quarantine_collection.count(),
    }

    if as_json:
        print(json.dumps(stats, indent=2))
        return

    console.print("[bold]Memory vault[/bold]")
    for name, count in stats.items():
        console.print(f"  {name:<16} [cyan]{count}[/cyan]")


@memory_app.command("add")
def memory_add(
    text: str = typer.Argument(..., help="Fact text to store."),
    to: str = typer.Option(
        "core",
        "--to",
        help="Collection to write to: core | user | agent",
    ),
    category: str = typer.Option(
        "",
        "--category",
        "-c",
        help=(
            "Category tag (core default: workspace_state; "
            "user default: preference; agent default: directive)."
        ),
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
) -> None:
    """Add a fact directly to the long-term memory vault.

    Examples::

        tokoboros memory add "The project uses PostgreSQL 15."
        tokoboros memory add "User prefers dark mode." --to user
        tokoboros memory add "Always cite sources." --to agent --category directive
    """
    from tokoboros.core.memory_vault import MemoryVault

    valid_collections = {"core", "user", "agent"}
    if to not in valid_collections:
        console.print(f"[red]Unknown collection:[/red] {to!r} — choose core, user, or agent.")
        raise typer.Exit(1)

    vault = MemoryVault()
    if to == "core":
        cat = category or "workspace_state"
        fact_id = vault.upsert_fact(text, category=cat)
    elif to == "user":
        cat = category or "preference"
        fact_id = vault.upsert_user_fact(text, category=cat)
    else:  # agent
        cat = category or "directive"
        fact_id = vault.upsert_agent_fact(text, category=cat)

    if as_json:
        print(json.dumps({"id": fact_id, "collection": to, "category": cat}))
        return

    if fact_id.startswith("BLOCKED:"):
        console.print(f"[red]Blocked by memory guard:[/red] {fact_id}")
        raise typer.Exit(1)
    if fact_id == "LIMIT_REACHED":
        console.print(
            "[yellow]User profile is full.[/yellow] Remove facts with "
            "`tokoboros memory forget` before adding more."
        )
        raise typer.Exit(1)

    console.print(f"[green]Stored[/green] [dim]({to}/{cat})[/dim]  {fact_id[:16]}…")


@memory_app.command("forget")
def memory_forget(
    fact_id: str = typer.Argument(
        ..., help="Fact ID returned by `memory search` or `memory add`."
    ),
    from_: str = typer.Option(
        "core",
        "--from",
        help="Collection to delete from: core | user | agent",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
) -> None:
    """Delete a fact from the memory vault by its ID.

    Run `tokoboros memory search <query>` to find the ID shown on each result.
    """
    from tokoboros.core.memory_vault import MemoryVault

    valid_collections = {"core", "user", "agent"}
    if from_ not in valid_collections:
        console.print(f"[red]Unknown collection:[/red] {from_!r} — choose core, user, or agent.")
        raise typer.Exit(1)

    vault = MemoryVault()
    if from_ == "core":
        ok = vault.delete_fact(fact_id)
    elif from_ == "user":
        ok = vault.delete_user_fact(fact_id)
    else:  # agent
        ok = vault.delete_agent_fact(fact_id)

    if as_json:
        print(json.dumps({"deleted": ok, "id": fact_id, "collection": from_}))
        return

    if ok:
        console.print(f"[green]Deleted[/green] {fact_id[:16]}… from [dim]{from_}[/dim]")
    else:
        console.print(f"[yellow]Not found:[/yellow] {fact_id!r} in {from_} collection.")
        raise typer.Exit(1)


@memory_app.command("import")
def memory_import(
    file: str = typer.Argument(..., help="Path to the exported conversation file (JSON)."),
    format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="Export format: chatgpt | gemini | hermes | auto (default: auto-detect).",
    ),
    no_memory: bool = typer.Option(
        False,
        "--no-memory",
        help="Skip writing summary facts to MemoryVault (sessions are still imported).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse and report what would be imported without writing anything.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
) -> None:
    """Import conversation history from a ChatGPT, Gemini, or Hermes export file.

    After export, run:

    \b
    ChatGPT  — download conversations.json from chatgpt.com > Settings > Data exports
    Gemini   — Google Takeout > Gemini > conversations.json
    Hermes   — any JSON file containing [{role, content}] turns

    Each conversation becomes a searchable session (visible in `tokoboros session list`)
    and a summary fact in long-term memory for semantic retrieval.
    """
    from pathlib import Path

    from tokoboros.core.memory_importer import ImportResult, ingest, parse_file
    from tokoboros.core.memory_vault import MemoryVault
    from tokoboros.core.session_store import SessionStore

    path = Path(file)
    if not path.exists():
        console.print(f"[red]File not found:[/red] {file}")
        raise typer.Exit(1)

    try:
        conversations, detected_format = parse_file(path, format=format)
    except (ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]Parse error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if dry_run:
        result = ImportResult(
            format_detected=detected_format,
            conversations_parsed=len(conversations),
            sessions_created=0,
            memory_facts_added=0,
            skipped=sum(1 for c in conversations if not c.turns),
        )
        if as_json:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "format": detected_format,
                        "conversations_found": result.conversations_parsed,
                        "would_import": result.conversations_parsed - result.skipped,
                        "skipped_empty": result.skipped,
                    }
                )
            )
        else:
            console.print(
                f"[cyan]Dry run[/cyan] — detected format: [bold]{detected_format}[/bold]\n"
                f"  Found {result.conversations_parsed} conversation(s), "
                f"{result.skipped} empty (would skip).\n"
                f"  Would create {result.conversations_parsed - result.skipped} session(s) "
                f"and memory facts."
            )
        return

    store = SessionStore()
    vault = None if no_memory else MemoryVault()
    try:
        result = ingest(conversations, store, vault, model_label=f"import:{detected_format}")
    finally:
        store.shutdown()

    result.format_detected = detected_format

    if as_json:
        print(
            json.dumps(
                {
                    "format": result.format_detected,
                    "conversations_parsed": result.conversations_parsed,
                    "sessions_created": result.sessions_created,
                    "memory_facts_added": result.memory_facts_added,
                    "skipped": result.skipped,
                }
            )
        )
        return

    console.print(
        f"[green]Import complete[/green] — format: [bold]{result.format_detected}[/bold]\n"
        f"  Parsed:  {result.conversations_parsed} conversation(s)\n"
        f"  Created: {result.sessions_created} session(s)\n"
        f"  Memory:  {result.memory_facts_added} fact(s) added\n"
        f"  Skipped: {result.skipped} empty conversation(s)"
    )
