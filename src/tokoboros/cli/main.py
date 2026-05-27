"""
Tokoboros CLI Entry Point

Provides the interactive terminal interface for users to communicate with the Tokoboros agent.
Uses rich to render a premium, step-by-step visual experience showing every tool call and result.

Commands:
    tokoboros run          — Start the interactive agent session
    tokoboros onboard      — Initialize the workspace
    tokoboros permissions  — View or reconfigure device permissions
"""

import json
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.syntax import Syntax

from tokoboros.gateway.workspace import (
    ensure_workspace,
    ensure_device_permissions,
    run_permissions_wizard,
    show_current_permissions,
    get_permissions_file,
)
from tokoboros.gateway.orchestrator import boot_agent, AgentSession

app = typer.Typer(help="Tokoboros - The Autonomous Agent Engine powered by ObjectGraph")
console = Console()


def _render_event(event_type: str, data: dict):
    """
    Callback invoked by the ExecutionLoop on every significant action.
    Renders a beautiful, real-time visual log in the terminal.
    """
    if event_type == "thinking":
        iteration = data.get("iteration", "?")
        console.print(Rule(f"[dim]Step {iteration}[/dim]", style="dim"))

    elif event_type == "tool_call":
        tool_name = data.get("name", "unknown")
        tool_input = data.get("input", {})

        # Format the input nicely
        input_display = json.dumps(tool_input, indent=2, default=str)
        if len(input_display) > 300:
            input_display = input_display[:300] + "\n  ..."

        console.print(
            Panel(
                f"[bold yellow]{tool_name}[/bold yellow]\n"
                f"[dim]{input_display}[/dim]",
                title="[bold cyan]Tool Call[/bold cyan]",
                border_style="cyan",
                padding=(0, 1)
            )
        )

    elif event_type == "tool_result":
        tool_name = data.get("name", "unknown")
        result = data.get("result", "")

        # Truncate long results for display
        if len(result) > 400:
            display_result = result[:400] + "\n..."
        else:
            display_result = result

        console.print(
            Panel(
                f"[dim]{display_result}[/dim]",
                title=f"[bold green]Result: {tool_name}[/bold green]",
                border_style="green",
                padding=(0, 1)
            )
        )

    elif event_type == "text_chunk":
        # Intermediate text from the model (before it decides on a tool call)
        text = data.get("text", "")
        if text.strip():
            console.print(f"  [dim italic]{text[:200]}[/dim italic]")

    elif event_type == "compaction":
        old_skill = data.get("old_skill", "none")
        new_skill = data.get("new_skill", "unknown")
        console.print(Panel(
            f"[bold]Context switch detected[/bold]\n"
            f"[dim]{old_skill}[/dim] -> [bold cyan]{new_skill}[/bold cyan]\n"
            f"[dim]Previous task context compacted to save tokens.[/dim]",
            title="[bold yellow]Memory Compaction[/bold yellow]",
            border_style="yellow",
            padding=(0, 1)
        ))


@app.command()
def onboard():
    """
    Initialize the ~/.tokoboros/ workspace.
    Run this when installing the agent for the first time.
    """
    try:
        ensure_workspace()
        console.print(Panel(
            "[bold green]Tokoboros workspace initialized successfully![/bold green]\n\n"
            "Your agent's brain is located at: [cyan]~/.tokoboros/brain.og[/cyan]\n"
            "Drop your new capabilities into:  [cyan]~/.tokoboros/skills/[/cyan]",
            title="Setup Complete"
        ))
    except Exception as e:
        console.print(f"[bold red]Error initializing workspace:[/] {e}")


@app.command()
def permissions(
    reset: bool = typer.Option(False, "--reset", help="Re-run the permissions wizard from scratch."),
    tier: str = typer.Option(None, "--tier", help="Set the tier directly: sandbox, guided, or unrestricted."),
    allow: str = typer.Option(None, "--allow", help="Add a folder path to allowed_paths."),
    block: str = typer.Option(None, "--block", help="Add a folder path to blocked_paths."),
):
    """
    View or modify device permissions.

    Without flags, shows the current permission configuration.
    Use --reset to re-run the wizard, --tier to switch tiers,
    or --allow/--block to add paths.
    """
    from tokoboros.core.permissions import PermissionGate, generate_permissions_og
    from tokoboros.og_engine.parser import parse_og_file

    permissions_file = get_permissions_file()

    # --reset: Re-run the wizard
    if reset:
        if permissions_file.exists():
            permissions_file.unlink()
        run_permissions_wizard(console)
        return

    # --tier: Quick tier switch
    if tier:
        if tier not in ("sandbox", "guided", "unrestricted"):
            console.print(f"[bold red]Invalid tier:[/] '{tier}'. Must be: sandbox, guided, unrestricted.")
            raise typer.Exit(1)

        content = generate_permissions_og(tier)
        with open(permissions_file, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[bold green]Permission tier set to:[/] {tier}")
        show_current_permissions(console)
        return

    # --allow: Add a path to allowed_paths
    if allow:
        _modify_permissions_path(permissions_file, "allowed_paths", allow, "add", console)
        return

    # --block: Add a path to blocked_paths
    if block:
        _modify_permissions_path(permissions_file, "blocked_paths", block, "add", console)
        return

    # No flags: Show current permissions
    if not permissions_file.exists():
        console.print("[yellow]No permissions configured yet.[/yellow]")
        console.print("Run [bold]tokoboros permissions --reset[/bold] to set up permissions.")
        return

    show_current_permissions(console)


def _modify_permissions_path(
    permissions_file: Path,
    field: str,
    path: str,
    action: str,
    console,
):
    """
    Modify a path field (allowed_paths or blocked_paths) in device-permissions.og.

    Reads the current file, finds the filesystem node's config, appends the path,
    and rewrites the file cleanly.

    Args:
        permissions_file: Path to device-permissions.og.
        field: 'allowed_paths' or 'blocked_paths'.
        path: The path string to add.
        action: 'add' (future: 'remove').
        console: Rich console for output.
    """
    from tokoboros.core.permissions import PermissionGate

    if not permissions_file.exists():
        console.print("[yellow]No permissions file found. Run 'tokoboros permissions --reset' first.[/yellow]")
        return

    # Read current file content
    with open(permissions_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the filesystem node's field and append the path
    resolved = str(Path(path).expanduser().resolve())
    lines = content.splitlines()
    modified = False

    for i, line in enumerate(lines):
        if line.strip().startswith(f"{field}="):
            current_value = line.split("=", 1)[1].strip()
            if resolved in current_value:
                console.print(f"[yellow]'{resolved}' is already in {field}.[/yellow]")
                return
            lines[i] = f"{field}={current_value}, {resolved}"
            modified = True
            break

    if not modified:
        console.print(f"[red]Could not find '{field}' in the permissions file. Re-run with --reset.[/red]")
        return

    with open(permissions_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    console.print(f"[bold green]Added[/] '{resolved}' to {field}.")
    show_current_permissions(console)


@app.command()
def reauth():
    """
    Clear stored API credentials (like Slack/Notion tokens) so you can update them.
    """
    vault_path = Path.home() / ".tokoboros" / "vault.json"
    if vault_path.exists():
        vault_path.unlink()
        console.print(Panel(
            "[bold green]Credentials cleared successfully![/bold green]\n\n"
            "The next time you boot Tokoboros and trigger an integration,\n"
            "you will be prompted to enter your new tokens.",
            title="Reauthentication Complete",
            border_style="green"
        ))
    else:
        console.print("[yellow]No credentials vault found. You're already starting fresh.[/yellow]")


@app.command()
def run():
    """
    Start the Tokoboros interactive agent session.
    """
    ensure_workspace()

    # Ensure device permissions exist (runs wizard on first launch)
    ensure_device_permissions(console)

    console.print("[dim]Booting Tokoboros...[/dim]")

    try:
        agent: AgentSession = boot_agent(on_event=_render_event)
    except Exception as e:
        console.print(f"\n[bold red]Failed to boot agent:[/] {e}")
        console.print("[yellow]Hint: Make sure your ANTHROPIC_API_KEY environment variable is set.[/yellow]")
        raise typer.Exit(1)

    # Boot banner with permission tier
    skill_count = agent.skill_engine.node_count
    perm_summary = agent.permission_gate.summary

    console.print(Panel(
        f"[bold magenta]Tokoboros is online.[/bold magenta]\n"
        f"Skills loaded: [cyan]{skill_count}[/cyan] | {perm_summary}\n"
        f"Type [bold]'exit'[/bold] or [bold]'quit'[/bold] to terminate.",
        border_style="magenta"
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")

            if user_input.strip().lower() in ["exit", "quit"]:
                console.print("[dim]Shutting down Tokoboros...[/dim]")
                agent.shutdown()
                break

            if not user_input.strip():
                continue

            # The loop will emit events via _render_event as it executes
            response = agent.chat(user_input)

            # Render the final response
            console.print(Rule("[bold magenta]Tokoboros[/bold magenta]", style="magenta"))
            console.print(Markdown(response))

        except KeyboardInterrupt:
            console.print("\n[dim]Session interrupted. Shutting down...[/dim]")
            agent.shutdown()
            break
        except Exception as e:
            console.print(f"\n[bold red]Error:[/] {e}")


if __name__ == "__main__":
    app()
