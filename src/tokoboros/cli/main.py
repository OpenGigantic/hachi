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
import os
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from tokoboros.core.config_loader import load_effective_config, write_global_config
from tokoboros.core.credential_vault import CredentialVault
from tokoboros.core.onboarding import OnboardingService
from tokoboros.core.paths import get_tokoboros_paths
from tokoboros.core.permissions import PermissionGate
from tokoboros.core.secret_store import create_default_secret_store
from tokoboros.core.session_store import SessionStore
from tokoboros.gateway.control_plane import (
    ConnectRequest,
    GatewayControlPlane,
    GatewayEvent,
    GatewayHTTPServer,
    GatewayProtocolError,
)
from tokoboros.gateway.orchestrator import AgentSession, boot_agent
from tokoboros.gateway.workspace import (
    ensure_device_permissions,
    ensure_workspace,
    get_permissions_file,
    get_workspace_dir,
    run_permissions_wizard,
    show_current_permissions,
)
from tokoboros.jobs.scheduler import Scheduler, ScheduleStore
from tokoboros.tui.app import run_tui

app = typer.Typer(
    help="Tokoboros - The Autonomous Agent Engine powered by ObjectGraph",
    no_args_is_help=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_version() -> str:
    """Best-effort package version for ``--version`` (falls back to a literal)."""
    try:
        from importlib.metadata import version

        return version("tokoboros")
    except Exception:
        return "0.1.0"


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"tokoboros {_resolve_version()}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(  # noqa: ARG001 - consumed eagerly by the callback
        False,
        "--version",
        "-v",
        help="Show the Tokoboros version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Pick a past session to resume (full context reload).",
    ),
    continue_latest: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="Resume the most recent session.",
    ),
    repl: bool = typer.Option(
        False,
        "--repl",
        help="Launch the legacy Rich REPL instead of the Textual TUI.",
    ),
) -> None:
    """Tokoboros — your autonomous personal assistant.

    Run `tokoboros` with no command to start the assistant. Use a subcommand
    (run, setup, doctor, skill, ...) for everything else; `tokoboros -h` lists them.
    """
    if ctx.invoked_subcommand is not None:
        return
    _launch_assistant(repl=repl, resume=resume, continue_latest=continue_latest)


class _InProcessGatewayRuntime:
    """Bridge gateway API calls to the in-process AgentSession runtime."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSession] = {}
        self._agents_lock = threading.Lock()
        self._events_by_session: dict[str, list[GatewayEvent]] = {}
        self._sequence_by_session: dict[str, int] = {}
        self._events_lock = threading.Lock()

    def _boot_session_agent(self, session_id: str) -> AgentSession:
        def _emit(event_type: str, payload: dict) -> None:
            self._on_runtime_event(session_id, event_type, payload)

        return boot_agent(on_event=_emit)

    def _get_agent(self, session_id: str) -> AgentSession:
        with self._agents_lock:
            agent = self._agents.get(session_id)
        if agent is None:
            raise GatewayProtocolError("Unknown session_id")
        return agent

    def register_connection(self, session_id: str, _req: ConnectRequest) -> None:
        agent = self._boot_session_agent(session_id)
        replaced_agent: AgentSession | None = None
        with self._agents_lock:
            replaced_agent = self._agents.get(session_id)
            self._agents[session_id] = agent

        if replaced_agent is not None:
            try:
                replaced_agent.shutdown()
            except Exception:
                pass

        with self._events_lock:
            self._events_by_session.setdefault(session_id, [])
            self._sequence_by_session.setdefault(session_id, 0)

    def close_connection(self, session_id: str) -> None:
        with self._agents_lock:
            agent = self._agents.pop(session_id, None)
        if agent is not None:
            try:
                agent.shutdown()
            except Exception:
                pass
        with self._events_lock:
            self._events_by_session.pop(session_id, None)
            self._sequence_by_session.pop(session_id, None)

    def chat(self, session_id: str, prompt: str) -> str:
        agent = self._get_agent(session_id)
        return agent.chat(prompt)

    def read_events(self, session_id: str, after: int, limit: int) -> list[GatewayEvent]:
        with self._events_lock:
            queue = self._events_by_session.get(session_id, [])
            return [event for event in queue if event.sequence > after][:limit]

    def session_state(self, session_id: str) -> dict:
        return asdict(self._get_agent(session_id).snapshot_session_state())

    def recent_sessions(self, limit: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [dict(item) for item in agent.query_recent_sessions(limit=limit)]

    def runtime_events(self, session_id: str | None, limit: int) -> list[dict]:
        if session_id:
            agent = self._get_agent(session_id)
            events = agent.snapshot_runtime_events(limit=limit)
            return [asdict(event) for event in events]

        all_events: list[dict] = []
        with self._agents_lock:
            agents = list(self._agents.values())
        for agent in agents:
            all_events.extend(asdict(event) for event in agent.snapshot_runtime_events(limit=limit))
        all_events.sort(key=lambda item: str(item.get("timestamp", "")))
        return all_events[-limit:]

    def mcp_inventory(self) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"server_count": 0, "total_tool_count": 0, "servers": []}
        return asdict(agent.snapshot_mcp_inventory())

    def active_jobs(self) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(job) for job in agent.snapshot_active_jobs()]

    def failed_jobs(self, limit: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(job) for job in agent.snapshot_failed_jobs(limit=limit)]

    def permission_audit(self, limit: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(item) for item in agent.snapshot_permission_audit(limit=limit)]

    def subagent_reports(self, query: str, top_k: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        reports = agent.query_subagent_reports(query=query, top_k=top_k)
        return [asdict(item) for item in reports]

    def capabilities(self) -> list[dict]:
        config = load_effective_config()
        items: list[dict] = []
        for capability_id, state in config.capabilities.items():
            items.append(
                {
                    "id": capability_id,
                    "kind": "native_integration",
                    "enabled": bool(state.enabled),
                    "configured": bool(state.configured),
                    "secret_ref": state.secret_ref,
                }
            )
        return items

    def get_config(self) -> dict:
        return load_effective_config().model_dump(mode="python", exclude_none=True)

    def update_provider(self, payload: dict) -> dict:
        provider = payload.get("provider", payload)
        if not isinstance(provider, dict):
            raise GatewayProtocolError("provider payload must be an object")

        config = load_effective_config()
        kind = provider.get("kind")
        model = provider.get("model")
        base_url = provider.get("base_url")
        api_key_ref = provider.get("api_key_ref")
        api_key_env = provider.get("api_key_env")

        if kind is not None:
            kind_text = str(kind)
            if kind_text not in {"anthropic", "openai-compatible", "ollama"}:
                raise GatewayProtocolError(
                    "provider.kind must be anthropic, openai-compatible, or ollama"
                )
            config.provider.kind = kind_text  # type: ignore[assignment]

        if model is not None:
            model_text = str(model).strip()
            if not model_text:
                raise GatewayProtocolError("provider.model cannot be empty")
            config.provider.model = model_text

        if base_url is not None:
            config.provider.base_url = str(base_url).strip() or None
        if api_key_ref is not None:
            config.provider.api_key_ref = str(api_key_ref).strip() or None
        if api_key_env is not None:
            config.provider.api_key_env = str(api_key_env).strip() or None

        write_global_config(config)
        return config.model_dump(mode="python", exclude_none=True)

    def validate_provider(self, payload: dict) -> dict:
        provider = payload.get("provider", payload)
        if not isinstance(provider, dict):
            raise GatewayProtocolError("provider payload must be an object")

        kind = str(provider.get("kind") or "").strip()
        model = str(provider.get("model") or "").strip()
        api_key_ref = str(provider.get("api_key_ref") or "").strip()
        api_key_env = str(provider.get("api_key_env") or "").strip()
        errors: list[str] = []
        warnings: list[str] = []

        if kind not in {"anthropic", "openai-compatible", "ollama"}:
            errors.append("provider.kind must be anthropic, openai-compatible, or ollama")
        if not model:
            errors.append("provider.model is required")

        if kind in {"anthropic", "openai-compatible"} and not (api_key_ref or api_key_env):
            warnings.append("api_key_ref or api_key_env should be set for remote providers")

        if kind == "ollama" and not str(provider.get("base_url") or "").strip():
            warnings.append("base_url is recommended for ollama providers")

        return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}

    def update_permissions(self, payload: dict) -> dict:
        permissions = payload.get("permissions", payload)
        if not isinstance(permissions, dict):
            raise GatewayProtocolError("permissions payload must be an object")

        tier = str(permissions.get("tier") or "").strip()
        if tier and tier not in {"sandbox", "guided", "unrestricted"}:
            raise GatewayProtocolError("permissions.tier must be sandbox, guided, or unrestricted")

        config = load_effective_config()
        if tier:
            config.permissions.tier = tier  # type: ignore[assignment]

        write_global_config(config)
        return config.model_dump(mode="python", exclude_none=True)

    def update_memory(self, payload: dict) -> dict:
        memory = payload.get("memory", payload)
        if not isinstance(memory, dict):
            raise GatewayProtocolError("memory payload must be an object")

        config = load_effective_config()
        if "enable_session_history" in memory:
            value = memory["enable_session_history"]
            if not isinstance(value, bool):
                raise GatewayProtocolError("memory.enable_session_history must be a boolean")
            config.memory.enable_session_history = value
        if "enable_semantic_memory" in memory:
            value = memory["enable_semantic_memory"]
            if not isinstance(value, bool):
                raise GatewayProtocolError("memory.enable_semantic_memory must be a boolean")
            config.memory.enable_semantic_memory = value

        write_global_config(config)
        return config.model_dump(mode="python", exclude_none=True)

    def list_models(self) -> list[dict]:
        provider_models = {
            "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-5"],
            "openai-compatible": ["gpt-4.1-mini", "gpt-4.1"],
            "ollama": ["llama3.1:8b", "qwen2.5-coder:7b"],
        }
        config = load_effective_config()
        kind = config.provider.kind
        return [
            {
                "id": model,
                "label": model,
                "provider": kind,
                "recommended": model == config.provider.model,
            }
            for model in provider_models.get(kind, [config.provider.model])
        ]

    def setup_state(self) -> dict:
        service = OnboardingService()
        state = service.resume_state()
        return {
            "state": state.model_dump(mode="python", exclude_none=True),
            "needs_setup": service.needs_setup(),
            "environment": service.detect_environment(),
        }

    def apply_setup(self, payload: dict) -> dict:
        plan_payload = payload.get("plan", payload)
        if not isinstance(plan_payload, dict):
            raise GatewayProtocolError("setup payload must be an object")

        service = OnboardingService()
        try:
            plan = service.apply_setup(plan_payload)
        except ValueError as exc:
            raise GatewayProtocolError(str(exc)) from exc
        return {
            "ok": True,
            "plan": plan.model_dump(mode="python", exclude_none=True),
            "state": service.resume_state().model_dump(mode="python", exclude_none=True),
        }

    def _on_runtime_event(self, session_id: str, event_type: str, payload: dict) -> None:
        with self._events_lock:
            if session_id not in self._events_by_session:
                return
            next_sequence = self._sequence_by_session.get(session_id, 0) + 1
            self._sequence_by_session[session_id] = next_sequence
            self._events_by_session[session_id].append(
                GatewayEvent(
                    sequence=next_sequence,
                    event_type=event_type,
                    payload=dict(payload),
                    time=_now_iso(),
                )
            )
            if len(self._events_by_session[session_id]) > 2000:
                self._events_by_session[session_id] = self._events_by_session[session_id][-2000:]

    def shutdown(self) -> None:
        with self._agents_lock:
            agents = list(self._agents.values())
            self._agents.clear()
        for agent in agents:
            try:
                agent.shutdown()
            except Exception:
                pass


def _gateway_readiness_probe() -> tuple[bool, list[str]]:
    """Derive readiness blockers for local gateway clients."""
    paths = get_tokoboros_paths()
    blockers: list[str] = []

    if not paths.global_config_toml.exists():
        blockers.append("config_missing")
    if not paths.permissions_file.exists():
        blockers.append("device_permissions_missing")

    return (len(blockers) == 0, blockers)


def _wait_until_interrupted() -> None:
    """Keep process alive until Ctrl+C without busy looping."""
    sleeper = threading.Event()
    while True:
        sleeper.wait(60.0)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _resolve_gateway_auth_token(host: str) -> str | None:
    token = (os.environ.get("TOKOBOROS_GATEWAY_API_TOKEN") or "").strip()
    if not token:
        token_file = (os.environ.get("TOKOBOROS_GATEWAY_API_TOKEN_FILE") or "").strip()
        if token_file:
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise OSError(
                    f"Unable to read TOKOBOROS_GATEWAY_API_TOKEN_FILE at '{token_file}': {exc}"
                ) from exc
    if token:
        return token
    if _is_loopback_host(host):
        return None
    raise OSError(
        "TOKOBOROS_GATEWAY_API_TOKEN (or TOKOBOROS_GATEWAY_API_TOKEN_FILE) is required "
        "when binding the gateway to a non-loopback host."
    )


def _run_gateway_http_server(host: str, port: int, attach_runtime: bool) -> None:
    """Run typed gateway endpoints for web and TUI consumers."""
    ensure_workspace()
    _apply_quick_setup_if_needed()
    server: GatewayHTTPServer | None = None
    runtime: _InProcessGatewayRuntime | None = None
    try:
        server, base_url, runtime = _start_gateway_server(
            host=host,
            port=port,
            attach_runtime=attach_runtime,
        )
    except OSError as exc:
        console.print(f"[bold red]Failed to start gateway server:[/] {exc}")
        raise typer.Exit(1) from exc

    runtime_lines = ""
    if attach_runtime:
        runtime_lines = (
            f"Send Message: [cyan]{base_url}/sessions/{{session_id}}/messages[/cyan]\n"
            f"Session Events: [cyan]{base_url}/sessions/{{session_id}}/events[/cyan]\n"
            f"Session State: [cyan]{base_url}/sessions/{{session_id}}[/cyan]\n"
        )

    auth_lines = ""
    if os.environ.get("TOKOBOROS_GATEWAY_API_TOKEN"):
        auth_lines = "Auth: [cyan]Authorization: Bearer <TOKOBOROS_GATEWAY_API_TOKEN>[/cyan]\n"

    console.print(
        Panel(
            "[bold green]Gateway HTTP server is running.[/bold green]\n\n"
            f"Base URL: [cyan]{base_url}[/cyan]\n"
            f"Readiness: [cyan]{base_url}/readiness[/cyan]\n"
            f"Create Session: [cyan]{base_url}/sessions[/cyan]\n"
            f"OpenAPI: [cyan]{base_url}/openapi.json[/cyan]\n"
            f"Docs: [cyan]{base_url}/docs[/cyan]\n\n"
            f"{auth_lines}"
            f"{runtime_lines}"
            "Press [bold]Ctrl+C[/bold] to stop.",
            title="Gateway",
            border_style="green",
        )
    )

    try:
        _wait_until_interrupted()
    except KeyboardInterrupt:
        console.print("[dim]Stopping gateway server...[/dim]")
    finally:
        if runtime is not None:
            runtime.shutdown()
        if server is not None:
            server.stop()


def _start_gateway_server(
    host: str = "127.0.0.1",
    port: int = 0,
    attach_runtime: bool = False,
) -> tuple[GatewayHTTPServer, str, _InProcessGatewayRuntime | None]:
    """Start local gateway server and return `(server, base_url, runtime_bridge)`."""
    auth_token = _resolve_gateway_auth_token(host)
    runtime = _InProcessGatewayRuntime() if attach_runtime else None
    support = runtime or _InProcessGatewayRuntime()

    server = GatewayHTTPServer(
        _build_gateway_control_plane(runtime=runtime, support=support),
        host=host,
        port=port,
        auth_token=auth_token,
    )

    try:
        bound_port = server.start()
    except OSError as exc:
        console.print(f"[bold red]Failed to start gateway server:[/] {exc}")
        raise

    return server, f"http://{host}:{bound_port}", runtime


def _build_gateway_control_plane(
    runtime: _InProcessGatewayRuntime | None,
    support: _InProcessGatewayRuntime,
) -> GatewayControlPlane:
    try:
        max_active_sessions = int(os.environ.get("TOKOBOROS_GATEWAY_MAX_SESSIONS", "100"))
    except ValueError:
        max_active_sessions = 100
    try:
        max_prompt_chars = int(os.environ.get("TOKOBOROS_GATEWAY_MAX_PROMPT_CHARS", "100000"))
    except ValueError:
        max_prompt_chars = 100000

    return GatewayControlPlane(
        readiness_probe=_gateway_readiness_probe,
        on_connect=runtime.register_connection if runtime else None,
        on_disconnect=runtime.close_connection if runtime else None,
        on_chat=runtime.chat if runtime else None,
        read_events=runtime.read_events if runtime else None,
        get_session_state=runtime.session_state if runtime else None,
        get_recent_sessions=runtime.recent_sessions if runtime else None,
        get_runtime_events=runtime.runtime_events if runtime else None,
        get_mcp_inventory=runtime.mcp_inventory if runtime else None,
        get_active_jobs=runtime.active_jobs if runtime else None,
        get_failed_jobs=runtime.failed_jobs if runtime else None,
        get_permission_audit=runtime.permission_audit if runtime else None,
        get_subagent_reports=runtime.subagent_reports if runtime else None,
        get_capabilities=support.capabilities,
        get_config=support.get_config,
        update_provider=support.update_provider,
        validate_provider=support.validate_provider,
        update_permissions=support.update_permissions,
        update_memory=support.update_memory,
        list_models=support.list_models,
        get_setup_state=support.setup_state,
        apply_setup=support.apply_setup,
        max_active_sessions=max_active_sessions,
        max_prompt_chars=max_prompt_chars,
    )


def _apply_quick_setup_if_needed() -> None:
    service = OnboardingService()
    if not service.needs_setup():
        return

    detected = service.detect_environment()
    provider: dict[str, str] = {
        "kind": "anthropic",
        "model": "claude-haiku-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
    }

    if detected.get("ollama_cli"):
        provider = {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama3.1:8b",
        }
    elif detected.get("openai_env"):
        provider = {
            "kind": "openai-compatible",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-4.1-mini",
        }
    elif detected.get("anthropic_env"):
        provider = {
            "kind": "anthropic",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-haiku-4-5",
        }

    plan = service.build_tier_plan(
        "lightning",
        {
            "provider": provider,
            "permission_tier": "guided",
        },
    )
    service.apply_setup(plan)


def _render_event(event_type: str, data: dict):
    """
    Callback invoked by the ExecutionLoop on every significant action.
    Renders a beautiful, real-time visual log in the terminal.
    """
    if event_type in ("thinking", "thinking.step"):
        iteration = data.get("iteration", "?")
        console.print(Rule(f"[dim]Step {iteration}[/dim]", style="dim"))

    elif event_type in ("tool_call", "tool.call.started"):
        tool_name = data.get("name", "unknown")
        tool_input = data.get("input", {})

        # Format the input nicely
        input_display = json.dumps(tool_input, indent=2, default=str)
        if len(input_display) > 300:
            input_display = input_display[:300] + "\n  ..."

        console.print(
            Panel(
                f"[bold yellow]{tool_name}[/bold yellow]\n[dim]{input_display}[/dim]",
                title="[bold cyan]Tool Call[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    elif event_type in ("tool_result", "tool.call.completed"):
        tool_name = data.get("name", "unknown")
        result = data.get("result", data.get("result_preview", ""))

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
                padding=(0, 1),
            )
        )

    elif event_type in ("text_chunk", "text.stream.delta"):
        # Intermediate text from the model (before it decides on a tool call)
        text = data.get("text", "")
        if text.strip():
            console.print(f"  [dim italic]{text[:200]}[/dim italic]")

    elif event_type in ("compaction", "memory.compaction"):
        old_skill = data.get("old_skill", "none")
        new_skill = data.get("new_skill", "unknown")
        console.print(
            Panel(
                f"[bold]Context switch detected[/bold]\n"
                f"[dim]{old_skill}[/dim] -> [bold cyan]{new_skill}[/bold cyan]\n"
                f"[dim]Previous task context compacted to save tokens.[/dim]",
                title="[bold yellow]Memory Compaction[/bold yellow]",
                border_style="yellow",
                padding=(0, 1),
            )
        )

    elif event_type == "boot.progress":
        idx = data.get("index", "?")
        total = data.get("total", "?")
        message = data.get("message", "Booting")
        console.print(f"[dim]Boot {idx}/{total}: {message}[/dim]")

    elif event_type == "boot.complete":
        console.print("[green]Runtime boot complete.[/green]")

    elif event_type == "permission.decided":
        decision = data.get("decision", "unknown")
        tool = data.get("tool", "unknown")
        console.print(f"[bold]Permission {decision}[/bold] for {tool}")

    elif event_type == "subagent.started":
        console.print(f"[cyan]Sub-agent started:[/cyan] {data.get('role', 'unknown')}")

    elif event_type == "subagent.completed":
        console.print(
            f"[cyan]Sub-agent completed:[/cyan] {data.get('role', 'unknown')} "
            f"({data.get('status', 'unknown')})"
        )


@app.command()
def onboard():
    """
    Initialize the ~/.tokoboros/ workspace.
    Run this when installing the agent for the first time.
    """
    try:
        ensure_workspace()
        console.print(
            Panel(
                "[bold green]Tokoboros workspace initialized successfully![/bold green]\n\n"
                f"Your config directory: [cyan]{get_workspace_dir()}[/cyan]\n"
                f"Your skills directory: [cyan]{get_tokoboros_paths().skills_dir}[/cyan]",
                title="Setup Complete",
            )
        )
    except Exception as e:
        console.print(f"[bold red]Error initializing workspace:[/] {e}")


@app.command()
def quickstart(
    provider: str | None = typer.Option(
        None, "--provider", help="anthropic | openai-compatible | ollama (skips the prompt)."
    ),
    tier: str | None = typer.Option(
        None, "--tier", help="Safety tier: sandbox | guided | unrestricted (skips the prompt)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept defaults without prompting."),
):
    """Fastest path from zero to chatting — a few plain questions, keys checked up front.

    Designed for non-technical users: pick a provider, we validate your API key
    immediately (instead of failing on the first message), choose a safety level,
    and you're ready to run ``tokoboros run``.
    """
    from tokoboros.core.onboarding import OnboardingService, default_api_key_env_for

    console.print(
        Panel(
            "[bold]Welcome to Tokoboros[/bold] 🪢\nLet's get you set up in under a minute.",
            title="Quickstart",
        )
    )
    ensure_workspace()
    service = OnboardingService()

    # 1. Provider
    provider_choices = {"1": "anthropic", "2": "openai-compatible", "3": "ollama"}
    chosen = (provider or "").strip().lower()
    if chosen not in {"anthropic", "openai-compatible", "ollama"}:
        if yes:
            chosen = "anthropic"
        else:
            console.print(
                "\nWhich AI provider do you want to use?\n"
                "  [cyan]1[/cyan] Anthropic (Claude) — recommended\n"
                "  [cyan]2[/cyan] OpenAI-compatible (OpenAI, etc.)\n"
                "  [cyan]3[/cyan] Ollama (free, runs locally)"
            )
            pick = Prompt.ask("Provider", choices=["1", "2", "3"], default="1")
            chosen = provider_choices[pick]

    # 2. Validate the API key BEFORE anything else (the usual non-geek failure point).
    env_name = default_api_key_env_for(chosen)
    ok, message = service.validate_provider(kind=chosen, api_key_env=env_name)
    if ok:
        console.print(f"[green]✓[/green] {message}")
    else:
        console.print(f"[yellow]⚠ {message}[/yellow]")
        if not yes and not typer.confirm("Continue setup anyway?", default=True):
            raise typer.Exit(1)

    # 3. Safety tier
    chosen_tier = (tier or "").strip().lower()
    if chosen_tier not in {"sandbox", "guided", "unrestricted"}:
        if yes:
            chosen_tier = "guided"
        else:
            console.print(
                "\nHow much freedom should the agent have?\n"
                "  [cyan]sandbox[/cyan]   — read-only, safest\n"
                "  [cyan]guided[/cyan]    — asks before risky actions (recommended)\n"
                "  [cyan]unrestricted[/cyan] — full access, no prompts"
            )
            chosen_tier = Prompt.ask(
                "Safety tier",
                choices=["sandbox", "guided", "unrestricted"],
                default="guided",
            )

    # 4. Apply and confirm
    try:
        plan = service.quick_setup(kind=chosen, permission_tier=chosen_tier)
    except ValueError as e:
        console.print(f"[red]Setup failed:[/red] {e}")
        raise typer.Exit(1) from e

    provider_answer = plan.answers.get("provider", {})
    console.print(
        Panel(
            "[bold green]You're ready![/bold green]\n\n"
            f"Provider: [cyan]{provider_answer.get('kind')}[/cyan] "
            f"(model: {provider_answer.get('model')})\n"
            f"Safety tier: [cyan]{chosen_tier}[/cyan]\n\n"
            "Start chatting with:  [bold]tokoboros run[/bold]\n"
            "Schedule proactive tasks with:  [bold]tokoboros schedule add[/bold]",
            title="Setup Complete",
        )
    )


@app.command()
def permissions(
    reset: bool = typer.Option(
        False, "--reset", help="Re-run the permissions wizard from scratch."
    ),
    tier: str | None = typer.Option(
        None, "--tier", help="Set the tier directly: sandbox, guided, or unrestricted."
    ),
    allow: str | None = typer.Option(None, "--allow", help="Add a folder path to allowed_paths."),
    block: str | None = typer.Option(None, "--block", help="Add a folder path to blocked_paths."),
):
    """
    View or modify device permissions.

    Without flags, shows the current permission configuration.
    Use --reset to re-run the wizard, --tier to switch tiers,
    or --allow/--block to add paths.
    """
    from tokoboros.core.permissions import generate_permissions_og

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
            console.print(
                f"[bold red]Invalid tier:[/] '{tier}'. Must be: sandbox, guided, unrestricted."
            )
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
    if not permissions_file.exists():
        console.print(
            "[yellow]No permissions file found. Run 'tokoboros permissions --reset' first.[/yellow]"
        )
        return

    # Read current file content
    with open(permissions_file, encoding="utf-8") as f:
        content = f.read()

    # Find the filesystem node's field and append the path
    resolved = str(Path(path).expanduser().resolve())
    lines = content.splitlines()
    modified = False

    for i, line in enumerate(lines):
        if line.strip().startswith(f"{field}="):
            current_value = line.split("=", 1)[1].strip()
            # Compare exact comma-separated tokens, not a substring (otherwise
            # adding 'C:\\a' is wrongly rejected when 'C:\\abc' is already present).
            existing = [token.strip() for token in current_value.split(",") if token.strip()]
            if resolved in existing:
                console.print(f"[yellow]'{resolved}' is already in {field}.[/yellow]")
                return
            existing.append(resolved)
            lines[i] = f"{field}={', '.join(existing)}"
            modified = True
            break

    if not modified:
        console.print(
            f"[red]Could not find '{field}' in the permissions file. Re-run with --reset.[/red]"
        )
        return

    with open(permissions_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    console.print(f"[bold green]Added[/] '{resolved}' to {field}.")
    show_current_permissions(console)


@app.command()
def reauth():
    """
    Clear stored API credentials (like Slack/Notion tokens) so you can update them.
    """
    vault = CredentialVault()
    deleted = vault.clear_all()
    if deleted > 0:
        console.print(
            Panel(
                "[bold green]Credentials cleared successfully![/bold green]\n\n"
                "The next time you boot Tokoboros and trigger an integration,\n"
                "you will be prompted to enter your new tokens.",
                title="Reauthentication Complete",
                border_style="green",
            )
        )
    else:
        console.print("[yellow]No credentials vault found. You're already starting fresh.[/yellow]")


def _run_repl() -> None:
    """
    Start the compatibility Rich REPL agent session.
    """
    ensure_workspace()
    _apply_quick_setup_if_needed()

    # Ensure device permissions exist (runs wizard on first launch)
    ensure_device_permissions(console)

    console.print("[dim]Booting Tokoboros...[/dim]")

    try:
        agent: AgentSession = boot_agent(on_event=_render_event)
    except Exception as e:
        console.print(f"\n[bold red]Failed to boot agent:[/] {e}")
        console.print(
            "[yellow]Hint: Make sure your ANTHROPIC_API_KEY environment variable is set.[/yellow]"
        )
        raise typer.Exit(1) from None

    # Boot banner with permission tier
    skill_count = agent.skill_engine.node_count
    perm_summary = agent.permission_gate.summary

    console.print(
        Panel(
            f"[bold magenta]Tokoboros is online.[/bold magenta]\n"
            f"Skills loaded: [cyan]{skill_count}[/cyan] | {perm_summary}\n"
            f"Type [bold]'exit'[/bold] or [bold]'quit'[/bold] to terminate.",
            border_style="magenta",
        )
    )

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


def _resolve_resume_target(*, resume: bool, continue_latest: bool) -> str | None:
    """Resolve which prior session (if any) to resume into the new TUI session.

    ``--continue`` picks the most recent session non-interactively; ``--resume``
    prints a numbered list and asks the operator to choose. Returns the chosen
    session id, or None when nothing is selected.
    """
    if not (resume or continue_latest):
        return None

    store = SessionStore()
    try:
        if continue_latest:
            sid = store.get_latest_session_id()
            if not sid:
                console.print("[yellow]No previous session to continue. Starting fresh.[/yellow]")
            return sid

        sessions = store.get_recent_sessions(limit=10)
        if not sessions:
            console.print("[yellow]No previous sessions found. Starting fresh.[/yellow]")
            return None

        console.print(Rule("[bold magenta]Resume a session[/bold magenta]", style="magenta"))
        for index, item in enumerate(sessions, start=1):
            started = str(item.get("started_at", ""))[:19].replace("T", " ")
            turns = item.get("total_turns", 0)
            model = item.get("model", "unknown")
            console.print(
                f"  [cyan]{index}[/cyan]. {started}  ·  {turns} turns  ·  [dim]{model}[/dim]"
            )

        choice = Prompt.ask(
            "Select a session number (or press Enter to start fresh)", default=""
        ).strip()
        if not choice:
            return None
        try:
            selected = sessions[int(choice) - 1]
        except (ValueError, IndexError):
            console.print("[yellow]Invalid selection. Starting fresh.[/yellow]")
            return None
        return str(selected.get("id"))
    finally:
        store.shutdown()


def _launch_assistant(*, repl: bool, resume: bool, continue_latest: bool) -> None:
    """Shared entry point for `tokoboros` and `tokoboros run`.

    Boots the Textual TUI in-process (local mode) so chat, interactive permission
    approvals, and slash commands all operate directly on the live AgentSession.
    The standalone HTTP gateway lives behind `tokoboros gateway` for web clients.
    """
    ensure_workspace()
    _apply_quick_setup_if_needed()
    ensure_device_permissions(console)

    if repl:
        _run_repl()
        return

    resume_session_id = _resolve_resume_target(resume=resume, continue_latest=continue_latest)

    try:
        run_tui(resume_session_id=resume_session_id)
    except Exception as e:
        console.print(f"[bold red]Failed to launch Textual UI:[/] {e}")
        console.print("[yellow]Falling back to compatibility REPL...[/yellow]")
        _run_repl()


@app.command()
def run(
    repl: bool = typer.Option(
        False,
        "--repl",
        help="Launch the legacy Rich REPL instead of the Textual TUI.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Pick a past session to resume (full context reload).",
    ),
    continue_latest: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="Resume the most recent session.",
    ),
):
    """
    Start Tokoboros.

    Default: Textual TUI.
    Fallback: pass --repl for the legacy terminal REPL.
    """
    _launch_assistant(repl=repl, resume=resume, continue_latest=continue_latest)


@app.command("run-repl")
def run_repl_command():
    """Launch the compatibility Rich REPL explicitly."""
    _run_repl()


@app.command("gateway")
def gateway(
    host: str = typer.Option("127.0.0.1", "--host", help="Gateway bind address."),
    port: int = typer.Option(8787, "--port", min=0, max=65535, help="Gateway bind port."),
    runtime: bool = typer.Option(
        True,
        "--runtime/--no-runtime",
        help="Attach in-process runtime so chat/events/session endpoints are available.",
    ),
):
    """Run the typed gateway HTTP server for connect/readiness flow consumers."""
    _run_gateway_http_server(host=host, port=port, attach_runtime=runtime)


@app.command("gateway-openapi")
def gateway_openapi(
    output: Path = typer.Option(  # noqa: B008
        Path("openapi/tokoboros-openapi.json"),
        "--output",
        help="Write OpenAPI schema JSON to this path.",
    ),
):
    """Export the local gateway OpenAPI schema without starting a server process."""
    from tokoboros.gateway.fastapi_app import create_gateway_app

    ensure_workspace()
    support = _InProcessGatewayRuntime()
    control_plane = _build_gateway_control_plane(runtime=None, support=support)
    app_instance = create_gateway_app(control_plane)
    schema = app_instance.openapi()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    console.print(f"[bold green]OpenAPI schema written:[/] {output}")


@app.command()
def setup(
    tier: str = typer.Option(
        "lightning", "--tier", help="Setup tier: lightning, recommended, enhanced, power."
    ),
    permission_tier: str = typer.Option(
        "guided", "--permission-tier", help="Permission tier: sandbox, guided, unrestricted."
    ),
):
    """Run or resume setup without launching a session."""
    ensure_workspace()

    service = OnboardingService()
    detected = service.detect_environment()

    provider: dict[str, str] = {
        "kind": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-haiku-4-5",
    }

    if detected.get("ollama_cli"):
        provider = {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama3.1:8b",
        }
    elif detected.get("openai_env"):
        provider = {
            "kind": "openai-compatible",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-4.1-mini",
        }

    plan = service.build_tier_plan(
        tier,
        {
            "provider": provider,
            "permission_tier": permission_tier,
        },
    )
    service.apply_setup(plan)

    console.print(
        Panel(
            "[bold green]Setup saved.[/bold green]\n\n"
            f"Tier: [cyan]{tier}[/cyan]\n"
            f"Provider: [cyan]{provider['kind']}[/cyan]\n"
            f"Permission tier: [cyan]{permission_tier}[/cyan]\n"
            "You can rerun setup anytime with [bold]tokoboros setup[/bold].",
            title="Onboarding",
        )
    )


@app.command()
def config(
    show_paths: bool = typer.Option(
        False, "--paths", help="Show canonical config/data/cache/state paths."
    ),
):
    """Show the effective resolved configuration."""
    cfg = load_effective_config().model_dump(mode="python", exclude_none=True)
    console.print(Panel(json.dumps(cfg, indent=2), title="Effective Config", border_style="cyan"))

    if show_paths:
        p = get_tokoboros_paths()
        path_payload = {
            "config_dir": str(p.config_dir),
            "data_dir": str(p.data_dir),
            "cache_dir": str(p.cache_dir),
            "state_dir": str(p.state_dir),
            "legacy_dir": str(p.legacy_dir),
            "settings_toml": str(p.global_config_toml),
        }
        console.print(
            Panel(
                json.dumps(path_payload, indent=2), title="Canonical Paths", border_style="magenta"
            )
        )


@app.command()
def secrets(
    list_only: bool = typer.Option(False, "--list", help="List stored secret metadata."),
    delete: str | None = typer.Option(None, "--delete", help="Delete a secret by logical name."),
    check: bool = typer.Option(False, "--check", help="Run secret backend health check."),
):
    """Inspect or manage secure secrets."""
    store = create_default_secret_store()

    if check:
        console.print(
            Panel(json.dumps(store.healthcheck(), indent=2), title="Secret Backend Health")
        )
        return

    if delete:
        store.delete(delete)
        console.print(f"Deleted secret metadata and value for: [cyan]{delete}[/cyan]")
        return

    if list_only:
        rows = store.list_metadata()
        console.print(Panel(json.dumps(rows, indent=2), title="Secret Metadata"))
        return

    console.print("Use --list, --delete <name>, or --check")


@app.command()
def doctor():
    """Run environment diagnostics for onboarding and runtime health."""
    import sys

    paths = get_tokoboros_paths()
    cfg = load_effective_config()
    service = OnboardingService(paths=paths)
    store = create_default_secret_store(paths=paths)

    # The most common "why isn't it working" cause: the configured provider's API
    # key isn't actually resolvable. Surface it explicitly.
    provider_ok, provider_msg = service.validate_provider(
        kind=cfg.provider.kind,
        api_key_env=getattr(cfg.provider, "api_key_env", None),
    )

    skill_count = 0
    skills_health = "unknown"
    try:
        from tokoboros.og_engine.engine import SkillEngine, lint_og_file

        engine = SkillEngine()
        engine.load_directory(paths.skills_dir)
        skill_count = engine.node_count

        if paths.skills_dir.exists():
            og_files = sorted(paths.skills_dir.glob("**/*.og"))
            reports = [lint_og_file(f) for f in og_files]
            errors = sum(r["summary"]["errors"] for r in reports)
            warnings = sum(r["summary"]["warnings"] for r in reports)
            skills_health = (
                f"{len(og_files)} file(s), {errors} error(s), {warnings} warning(s) "
                "(run `tokoboros skill lint` for detail)"
            )
        else:
            skills_health = "no skills directory yet"
    except Exception:
        skill_count = -1  # could not load skills directory
        skills_health = "error scanning skills"

    checks = {
        "python_version": sys.version.split()[0],
        "config_dir_exists": paths.config_dir.exists(),
        "data_dir_exists": paths.data_dir.exists(),
        "state_dir_exists": paths.state_dir.exists(),
        "settings_toml_exists": paths.global_config_toml.exists(),
        "provider_kind": cfg.provider.kind,
        "provider_model": cfg.provider.model,
        "provider_api_key": "ok" if provider_ok else provider_msg,
        "permissions_tier": cfg.permissions.tier,
        "skills_loaded": skill_count,
        "skills_health": skills_health,
        "session_cost_cap_usd": os.environ.get("TOKOBOROS_MAX_SESSION_COST_USD", "unset"),
        "onboarding_needs_setup": service.needs_setup(),
        "secret_backend": store.healthcheck(),
        "environment_detection": service.detect_environment(),
    }
    console.print(
        Panel(json.dumps(checks, indent=2), title="Tokoboros Doctor", border_style="green")
    )


@app.command("security-audit")
def security_audit(
    fail_on_high: bool = typer.Option(
        False, "--fail-on-high", help="Exit non-zero when any high severity finding exists."
    ),
    fail_on_medium: bool = typer.Option(
        False, "--fail-on-medium", help="Exit non-zero when high or medium severity findings exist."
    ),
    max_findings: int | None = typer.Option(
        None, "--max-findings", help="Exit non-zero if finding count exceeds this threshold."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON (for CI/automation)."
    ),
):
    """
    Run a focused security posture audit for local Tokoboros deployments.

    Phase 1 Enhancement (CI-ready):
        - --json: Output machine-readable JSON to stdout for CI/automation integration.
        - --fail-on-high: Exit code 2 if any high-severity findings exist.
        - --fail-on-medium: Exit code 2 if any high or medium-severity findings exist.
        - --max-findings: Exit code 2 if total findings exceed threshold.

    Default behavior: Pretty-print findings with human-friendly formatting.
    """
    cfg = load_effective_config()
    permissions_file = get_permissions_file()
    gate = PermissionGate(permissions_file)

    findings = []

    def add_finding(severity: str, check_id: str, message: str, remediation: str):
        findings.append(
            {
                "severity": severity,
                "check_id": check_id,
                "message": message,
                "remediation": remediation,
            }
        )

    if not os.environ.get("GOOGLE_CLIENT_ID"):
        add_finding(
            "high",
            "oauth.google_client_id_missing",
            "GOOGLE_CLIENT_ID is not configured; Google OAuth will fail and should fail closed.",
            "Set GOOGLE_CLIENT_ID explicitly in your runtime environment.",
        )

    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if secret.startswith("GOCSPX-"):
        add_finding(
            "high",
            "oauth.google_client_secret_embedded",
            "GOOGLE_CLIENT_SECRET appears to be a concrete client secret and may be "
            "exposed in process environment.",
            "Store OAuth secret in a secure secret backend and avoid plaintext shell profile "
            "exports.",
        )

    if cfg.permissions.tier == "unrestricted":
        add_finding(
            "high",
            "permissions.unrestricted_tier",
            "Permission tier is set to unrestricted.",
            "Use guided tier for routine operation and explicitly approve exceptional commands.",
        )

    if gate._term.allow_all:
        add_finding(
            "high",
            "terminal.allow_all",
            "Terminal policy allows all commands.",
            "Constrain terminal allowed_commands to a minimal allowlist.",
        )

    rest_allowlist = os.environ.get("TOKOBOROS_REST_ALLOWLIST", "").strip()
    if not rest_allowlist:
        add_finding(
            "high",
            "rest.allowlist_missing",
            "TOKOBOROS_REST_ALLOWLIST is empty; secret-bearing REST calls are blocked "
            "and outbound posture is broad.",
            "Define explicit allowed hosts, e.g. "
            "TOKOBOROS_REST_ALLOWLIST=api.example.com,*.example.org",
        )

    if os.environ.get("TOKOBOROS_REST_ALLOW_PRIVATE", "false").lower() == "true":
        add_finding(
            "high",
            "rest.private_egress_enabled",
            "Private-network REST egress is enabled.",
            "Unset TOKOBOROS_REST_ALLOW_PRIVATE or set it to false.",
        )

    add_finding(
        "medium",
        "sandbox.process_backend",
        "Current sandbox backend is process-isolation only and development-grade.",
        "Use a container-backed sandbox for production deployments.",
    )

    for cap_name, state in cfg.capabilities.items():
        if state.enabled and not state.secret_ref:
            add_finding(
                "medium",
                f"capability.{cap_name}.secret_ref_missing",
                f"Capability '{cap_name}' is enabled without a secret_ref.",
                "Bind the capability to a secret reference managed by the secret backend.",
            )

    high_count = sum(1 for f in findings if f["severity"] == "high")
    medium_count = sum(1 for f in findings if f["severity"] == "medium")
    low_count = sum(1 for f in findings if f["severity"] == "low")
    total_count = len(findings)

    # Build result payload
    result = {
        "status": "pass" if high_count == 0 else "fail",
        "high": high_count,
        "medium": medium_count,
        "low": low_count,
        "total": total_count,
        "findings": findings,
    }

    # Determine exit code based on fail thresholds
    exit_code = 0
    fail_reasons = []

    if fail_on_high and high_count > 0:
        exit_code = 2
        fail_reasons.append(f"{high_count} high-severity finding(s)")

    if fail_on_medium and (high_count > 0 or medium_count > 0):
        exit_code = 2
        if not fail_reasons:
            fail_reasons.append(f"{medium_count} medium-severity finding(s)")

    if max_findings is not None and total_count > max_findings:
        exit_code = 2
        fail_reasons.append(f"finding count ({total_count}) exceeds max ({max_findings})")

    result["exit_code"] = exit_code
    result["fail_reasons"] = fail_reasons if fail_reasons else []

    # Output format selection
    if json_output:
        # Plain print (not console.print) so Rich never soft-wraps the JSON
        # payload to the terminal width — that would inject newlines mid-token
        # and break `json.loads` for CI consumers.
        print(json.dumps(result, indent=2))
    else:
        # Human-friendly format with rich rendering
        border = "red" if high_count > 0 else "yellow" if medium_count > 0 else "green"
        console.print(
            Panel(
                json.dumps(
                    {
                        "status": result["status"],
                        "high": high_count,
                        "medium": medium_count,
                        "low": low_count,
                        "total": total_count,
                        "findings": findings,
                    },
                    indent=2,
                ),
                title="Tokoboros Security Audit",
                border_style=border,
            )
        )

    if exit_code != 0 and not json_output:
        console.print(f"\n[bold red]Audit failed:[/] {', '.join(fail_reasons)}")
        console.print(
            "[dim]Use --json flag to export machine-readable output for CI integration.[/dim]"
        )

    if exit_code != 0:
        raise typer.Exit(exit_code)


schedule_app = typer.Typer(help="Manage proactive scheduled tasks (cron / interval / once).")
app.add_typer(schedule_app, name="schedule")


def _get_scheduler() -> Scheduler:
    return Scheduler(ScheduleStore(get_tokoboros_paths().schedules_file))


@schedule_app.command("add")
def schedule_add(
    name: str = typer.Argument(..., help="Short name for the schedule."),
    task: str = typer.Argument(..., help="Instruction the agent runs when it fires."),
    cron: str | None = typer.Option(None, "--cron", help="5-field cron, e.g. '0 9 * * 1-5'."),
    every: int | None = typer.Option(None, "--every", help="Interval in seconds."),
    at: str | None = typer.Option(None, "--at", help="One-shot ISO datetime (UTC)."),
    deliver_to: str = typer.Option("chat", "--deliver-to", help="chat | webhook | log."),
    webhook_url: str | None = typer.Option(None, "--webhook-url", help="Webhook URL."),
):
    """Add a proactive schedule. Provide exactly one of --cron / --every / --at."""
    chosen = [("cron", cron), ("interval", every), ("once", at)]
    selected = [(k, v) for k, v in chosen if v is not None]
    if len(selected) != 1:
        console.print("[red]Provide exactly one of --cron, --every, or --at.[/red]")
        raise typer.Exit(2)
    kind = selected[0][0]
    scheduler = _get_scheduler()
    try:
        schedule = scheduler.add(
            name=name,
            task=task,
            kind=kind,
            cron=cron,
            interval_seconds=every,
            run_at=at,
            deliver_to=deliver_to,
            webhook_url=webhook_url,
        )
    except ValueError as e:
        console.print(f"[red]Invalid schedule:[/red] {e}")
        raise typer.Exit(2) from e
    console.print(
        f"[green]Scheduled[/green] '{schedule.name}' ({kind}) — id {schedule.schedule_id}\n"
        f"Next run: {schedule.next_run}"
    )


@schedule_app.command("list")
def schedule_list():
    """List all configured schedules."""
    schedules = _get_scheduler().list_schedules()
    if not schedules:
        console.print("[dim]No schedules configured.[/dim]")
        return
    for s in schedules:
        state = "enabled" if s.enabled else "disabled"
        console.print(
            f"[bold]{s.name}[/bold] [{s.kind.value}, {state}] id={s.schedule_id}\n"
            f"  task: {s.task}\n  next_run: {s.next_run}  runs: {s.run_count}"
        )


@schedule_app.command("remove")
def schedule_remove(schedule_id: str = typer.Argument(..., help="Schedule id to remove.")):
    """Remove a schedule by id."""
    if _get_scheduler().cancel(schedule_id):
        console.print(f"[green]Removed[/green] {schedule_id}")
    else:
        console.print(f"[yellow]No schedule found:[/yellow] {schedule_id}")
        raise typer.Exit(1)


@schedule_app.command("run-due")
def schedule_run_due():
    """Run every schedule that is currently due (intended for cron/daemon invocation)."""
    scheduler = _get_scheduler()
    due = scheduler.due()
    if not due:
        console.print("[dim]No schedules are due.[/dim]")
        return

    agent = boot_agent()
    try:

        def fire(schedule):
            console.print(f"[cyan]Running[/cyan] '{schedule.name}': {schedule.task}")
            reply = agent.chat(schedule.task)
            _deliver_schedule_result(schedule, reply)

        fired = scheduler.tick(fire)
        console.print(f"[green]Fired {len(fired)} schedule(s).[/green]")
    finally:
        try:
            agent.shutdown()
        except Exception:
            pass


def _deliver_schedule_result(schedule, reply: str) -> None:
    """Deliver a scheduled task's result to its configured destination."""
    if schedule.deliver_to == "webhook" and schedule.webhook_url:
        import urllib.request

        from tokoboros.core.net_guard import validate_outbound_url

        # The webhook URL is agent/user-supplied — block SSRF / file:// / internal hosts.
        ok, reason = validate_outbound_url(schedule.webhook_url)
        if not ok:
            console.print(f"[red]Webhook blocked[/red] ({reason}): {schedule.webhook_url}")
            return

        # Scrub leaked credentials from the result before it leaves the machine
        # (same guard as outbound messaging / REST). Opt out via the shared env var.
        from tokoboros.core.secret_scanner import redact_outbound

        safe_reply, _redacted = redact_outbound(reply)
        payload = json.dumps({"schedule": schedule.name, "result": safe_reply}).encode("utf-8")
        request = urllib.request.Request(
            schedule.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=15)  # noqa: S310 (user-provided URL)
        except Exception as e:
            console.print(f"[yellow]Webhook delivery failed:[/yellow] {e}")
    else:
        console.print(Panel(Markdown(reply), title=f"Scheduled: {schedule.name}"))


channel_app = typer.Typer(help="Talk to Tokoboros from external chat apps (Telegram, ...).")
app.add_typer(channel_app, name="channel")


@channel_app.command("telegram")
def channel_telegram(
    token: str | None = typer.Option(
        None, "--token", envvar="TELEGRAM_BOT_TOKEN", help="Telegram bot token from @BotFather."
    ),
    allow: str | None = typer.Option(
        None, "--allow", help="Comma-separated Telegram chat ids to restrict access to."
    ),
    poll_interval: float = typer.Option(2.0, "--interval", help="Seconds between polls."),
):
    """Run a Telegram bot that relays messages to Tokoboros (one session per chat)."""
    import time

    from tokoboros.channels import ChannelRouter
    from tokoboros.channels.runner import GatewaySessionRunner
    from tokoboros.channels.telegram import TelegramAdapter

    if not token:
        console.print(
            "[red]No bot token.[/red] Pass --token or set TELEGRAM_BOT_TOKEN "
            "(create a bot via @BotFather)."
        )
        raise typer.Exit(2)

    allowlist = [a.strip() for a in allow.split(",") if a.strip()] if allow else None
    adapter = TelegramAdapter(token)
    runner = GatewaySessionRunner()
    router = ChannelRouter(runner, allowlist=allowlist)
    console.print("[green]Telegram bot running.[/green] Press Ctrl+C to stop.")
    try:
        while True:
            for inbound in adapter.poll_once():
                console.print(
                    f"[cyan]{inbound.sender_name or inbound.sender_id}[/cyan]: {inbound.text}"
                )
                adapter.send(router.dispatch(inbound))
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping Telegram bot...[/dim]")
    finally:
        runner.shutdown()


skill_app = typer.Typer(help="Manage and import agent skills (.og / SKILL.md).")
app.add_typer(skill_app, name="skill")


@skill_app.command("import")
def skill_import(
    path: str = typer.Argument(..., help="A SKILL.md file or a directory containing .md skills."),
):
    """Import SKILL.md (Agent Skills) file(s) into Tokoboros' token-efficient .og library."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    target = Path(path)
    if not target.exists():
        console.print(f"[red]Not found:[/red] {path}")
        raise typer.Exit(2)

    engine = SkillEngine()
    skills_dir = get_skills_dir()
    engine.load_directory(skills_dir)

    files = sorted(target.glob("**/*.md")) if target.is_dir() else [target]
    if not files:
        console.print("[yellow]No .md files found.[/yellow]")
        return

    imported = 0
    for md in files:
        result = engine.import_skill_md(md)
        if result.get("status") == "imported":
            imported += 1
            kw = ", ".join(result.get("keywords", [])[:5])
            console.print(
                f"[green]Imported[/green] {md.name} → {result['name']}  [dim]({kw})[/dim]"
            )
        else:
            console.print(f"[yellow]Skipped[/yellow] {md.name}: {result.get('reason')}")

    console.print(
        f"\n[bold]Imported {imported}/{len(files)} skill(s)[/bold] into {skills_dir / 'imported'}."
    )


@skill_app.command("list")
def skill_list():
    """List all loaded .og skills (name, role, keywords)."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())
    skills = engine.list_all_skills()
    if not skills:
        console.print("[dim]No skills loaded.[/dim]")
        return
    for item in sorted(skills, key=lambda s: s["name"]):
        keywords = item.get("keywords", "")
        console.print(
            f"[bold]{item['name']}[/bold] [dim][{item.get('role', 'skill')}][/dim]  {keywords}"
        )
    console.print(f"\n[bold]{len(skills)} skill(s) loaded.[/bold]")


@skill_app.command("export")
def skill_export(
    name: str = typer.Argument(..., help="Exact skill node name (see `skill list`)."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file vs stdout."),
):
    """Export a loaded skill back to .og text."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())
    text = engine.export_skill(name)
    if text is None:
        console.print(f"[red]Skill not found:[/red] {name} [dim](see `tokoboros skill list`)[/dim]")
        raise typer.Exit(1)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        console.print(f"[green]Exported[/green] {name} → {output}")
    else:
        console.print(text)


@skill_app.command("deps")
def skill_deps(
    name: str = typer.Argument(..., help="Exact skill node name (see `skill list`)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the dependency report as JSON."),
):
    """Resolve a skill's transitive `requires` dependency chain (load these first)."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())
    result = engine.get_dependency_chain(name)
    if result is None:
        console.print(f"[red]Skill not found:[/red] {name} [dim](see `tokoboros skill list`)[/dim]")
        raise typer.Exit(1)

    if as_json:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps(result, indent=2))
        return

    console.print(f"[bold]{name}[/bold] dependencies:")
    console.print(f"  direct:     {', '.join(result['direct']) or '(none)'}")
    console.print(f"  full chain: {', '.join(result['chain']) or '(none)'}")
    if result["missing"]:
        console.print(f"  [yellow]missing (not loaded):[/yellow] {', '.join(result['missing'])}")
    if result["has_cycle"]:
        console.print("  [red]⚠ dependency cycle detected[/red]")


@skill_app.command("show")
def skill_show(
    name: str = typer.Argument(..., help="Exact skill node name (see `skill list`)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the skill view as JSON."),
):
    """Inspect one skill's Pass-2 view: metadata, dense summary, and a manifest of
    heavy blocks with their token cost — without loading the full body (the .og
    3-pass model). Shows why selective loading saves tokens."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())

    node = engine.get_node(name)
    if node is None:
        console.print(f"[red]Skill not found:[/red] {name} [dim](see `tokoboros skill list`)[/dim]")
        raise typer.Exit(1)

    peeked = engine.peek([name]).get(name, {})
    changelog = engine.get_node_changelog(name) or {}
    available = peeked.get("available_blocks", {})
    result = {
        "name": name,
        "role": node.role,
        "scope": node.scope,
        "version": changelog.get("version", ""),
        "keywords": list(node.keywords),
        "requires": list(node.requires),
        "requires_auth": list(node.requires_auth),
        "dense": peeked.get("dense", ""),
        "warning": peeked.get("warning", ""),
        "available_blocks": available,
        "full_load_tokens": sum(available.values()),
    }

    if as_json:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps(result, indent=2))
        return

    console.print(f"[bold]{name}[/bold]  [dim]({node.role} · {node.scope})[/dim]")
    if result["version"]:
        console.print(f"  version:   {result['version']}")
    console.print(f"  keywords:  {', '.join(result['keywords']) or '(none)'}")
    console.print(f"  requires:  {', '.join(result['requires']) or '(none)'}")
    if result["requires_auth"]:
        console.print(f"  auth:      {', '.join(result['requires_auth'])}")
    console.print(f"  dense:     {result['dense'] or '(none)'}")
    if result["warning"]:
        console.print(f"  [yellow]warning:   {result['warning']}[/yellow]")
    if available:
        blocks = ", ".join(f"{block} (~{cost} tok)" for block, cost in available.items())
        console.print(f"  blocks:    {blocks}")
        console.print(
            f"  [dim]full load ≈ {result['full_load_tokens']} tokens "
            "(loaded only on demand via load_skill)[/dim]"
        )
    else:
        console.print("  blocks:    [dim](dense-only; no heavy content)[/dim]")


@skill_app.command("search")
def skill_search(
    query: str = typer.Argument(..., help="Keywords / natural-language query to route on."),
    as_json: bool = typer.Option(False, "--json", help="Emit matches as JSON."),
):
    """Test Pass-1 routing: which skills match a query by keyword (the agent's
    first step, before peek/load). Shows the .og index doing O(1) routing."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())
    # search() returns set-ordered results; sort for deterministic output.
    matches = sorted(engine.search(query), key=lambda m: m["name"])

    if as_json:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps({"query": query, "matches": matches}, indent=2))
        return

    if not matches:
        console.print(
            f"[yellow]No skills matched:[/yellow] {query} [dim](try `tokoboros skill list`)[/dim]"
        )
        return
    console.print(f"[bold]{len(matches)} skill(s)[/bold] matched [cyan]{query}[/cyan]:")
    for match in matches:
        console.print(
            f"  • [bold]{match['name']}[/bold]  [dim]({match['role']})[/dim]  {match['keywords']}"
        )


@skill_app.command("changelog")
def skill_changelog(
    name: str = typer.Argument(..., help="Exact skill node name (see `skill list`)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the changelog as JSON."),
):
    """Show a skill's version + ::changelog entries — the .og delta-load story:
    check what changed (a few tokens) without re-reading the heavy content."""
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())
    result = engine.get_node_changelog(name)
    if result is None:
        console.print(f"[red]Skill not found:[/red] {name} [dim](see `tokoboros skill list`)[/dim]")
        raise typer.Exit(1)

    if as_json:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps(result, indent=2))
        return

    version = result.get("version") or "(unversioned)"
    header = f"[bold]{name}[/bold]  v{version}"
    if result.get("updated"):
        header += f"  [dim](updated {result['updated']})[/dim]"
    console.print(header)
    entries = result.get("changelog") or []
    if not entries:
        console.print("  [dim]no changelog entries[/dim]")
        return
    for entry in entries:
        console.print(
            f"  {entry.get('date', '')}  [cyan]{entry.get('action', '')}[/cyan]  "
            f"{entry.get('target', '')}  {entry.get('description', '')}".rstrip()
        )


@skill_app.command("report")
def skill_report(
    stale_days: int = typer.Option(
        30, "--stale-days", help="Days of inactivity before a skill is flagged stale."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON (for CI/automation)."
    ),
) -> None:
    """Show skill-usage analytics: most-used, never-used, and stale skills.

    This realizes the .og self-optimization loop — usage is tracked every time
    the agent calls load_skill, so this report reveals which skills earn their
    keep and which are dead weight worth pruning or refining.
    """
    from tokoboros.gateway.workspace import get_skills_dir
    from tokoboros.og_engine.engine import SkillEngine

    engine = SkillEngine()
    engine.load_directory(get_skills_dir())

    report = engine.get_usage_report()
    stale = engine.get_stale_skills(stale_days=stale_days)
    never_used = [s for s in stale if s["status"] == "never_used"]
    aged = [s for s in stale if s["status"] == "stale"]

    if json_output:
        # Plain print (not console.print) so Rich never soft-wraps the JSON payload.
        print(
            json.dumps(
                {"report": report, "stale": aged, "never_used": never_used},
                indent=2,
                default=str,
            )
        )
        return

    if report["total_skills"] == 0:
        console.print("[dim]No skills loaded.[/dim]")
        return

    summary = (
        f"Total: [bold]{report['total_skills']}[/bold]   "
        f"Tracked: [bold]{report['tracked_skills']}[/bold]   "
        f"Stale (>{stale_days}d): [yellow]{report['stale_count']}[/yellow]   "
        f"Never used: [yellow]{report['never_used_count']}[/yellow]"
    )
    console.print(Panel(summary, title="Skill Usage Report", border_style="cyan"))

    top = report.get("top_skills", [])
    if top:
        console.print("[bold]Most used[/bold]")
        for item in top:
            last = item.get("last_used") or "-"
            console.print(
                f"  [green]{item['use_count']:>4}x[/green]  {item['name']}  [dim]{last}[/dim]"
            )

    if aged:
        console.print(f"\n[bold yellow]Stale (>{stale_days}d inactive)[/bold yellow]")
        for item in sorted(aged, key=lambda s: s.get("days_inactive") or 0, reverse=True):
            console.print(
                f"  [dim]{item['days_inactive']}d ago[/dim]  {item['name']} "
                f"[dim](used {item['use_count']}x)[/dim]"
            )

    if never_used:
        console.print("\n[bold]Never used[/bold] [dim](candidates to prune or promote)[/dim]")
        for item in sorted(never_used, key=lambda s: s["name"]):
            console.print(f"  [dim]-[/dim] {item['name']}")


@skill_app.command("lint")
def skill_lint(
    path: str = typer.Argument(..., help="A .og file or a directory of .og skill files."),
    strict: bool = typer.Option(
        False, "--strict", help="Treat warnings as failures (exit non-zero) — good for CI."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON (for CI/automation)."
    ),
) -> None:
    """Lint .og skill files for structure + token efficiency before shipping them.

    Flags un-routable nodes (missing ::index entry/keywords), missing or oversized
    ::dense blocks, empty nodes, and dangling ::index entries — the conventions the
    3-pass loader and search depend on. Exits non-zero on errors (or any warning with
    --strict), so it can gate community skill packs in CI.
    """
    from tokoboros.og_engine.engine import lint_og_file

    target = Path(path)
    if not target.exists():
        console.print(f"[red]Not found:[/red] {path}")
        raise typer.Exit(2)

    if target.is_dir():
        files = sorted(target.glob("**/*.og"))
    else:
        files = [target]
    if not files:
        console.print("[yellow]No .og files found.[/yellow]")
        return

    reports = [lint_og_file(f) for f in files]

    total_errors = sum(r["summary"]["errors"] for r in reports)
    total_warnings = sum(r["summary"]["warnings"] for r in reports)

    if json_output:
        # Plain print (not console.print) so Rich never soft-wraps the JSON payload.
        print(json.dumps({"reports": reports}, indent=2, default=str))
    else:
        _level_style = {"error": "red", "warning": "yellow", "info": "dim"}
        for report in reports:
            name = Path(report["file"]).name
            count = report["node_count"]
            if not report["issues"]:
                console.print(f"[green]OK[/green] {name} [dim]({count} node(s))[/dim]")
                continue
            verdict = "[red]FAIL[/red]" if not report["ok"] else "[yellow]WARN[/yellow]"
            console.print(f"{verdict} {name} [dim]({count} node(s))[/dim]")
            for issue in report["issues"]:
                color = _level_style.get(issue["level"], "white")
                console.print(
                    f"  [{color}]{issue['level']:<7}[/{color}] "
                    f"[bold]{issue['node']}[/bold] [dim]{issue['code']}[/dim]: {issue['message']}"
                )
        console.print(
            f"\n[bold]{len(files)} file(s)[/bold]  "
            f"[red]{total_errors} error(s)[/red]  "
            f"[yellow]{total_warnings} warning(s)[/yellow]"
        )

    failed = total_errors > 0 or (strict and total_warnings > 0)
    if failed:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
