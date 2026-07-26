"""
Hachi CLI Entry Point

Provides the interactive terminal interface for users to communicate with the Hachi agent.
Uses rich to render a premium, step-by-step visual experience showing every tool call and result.

Commands:
    hachi run          — Start the interactive agent session
    hachi onboard      — Initialize the workspace
    hachi permissions  — View or reconfigure device permissions
"""

import json
import logging
import os
import sys
import threading
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from hachi.cli.commands.channel import channel_app
from hachi.cli.commands.daemon import daemon_app
from hachi.cli.commands.eval import eval_app
from hachi.cli.commands.jobs import jobs_app
from hachi.cli.commands.mcp import mcp_app
from hachi.cli.commands.memory import memory_app
from hachi.cli.commands.pack import ogpack_app
from hachi.cli.commands.schedule import schedule_app
from hachi.cli.commands.session import session_app
from hachi.cli.commands.skill import skill_app
from hachi.core.config_loader import (
    load_effective_config,
    read_global_config_file,
    write_global_config_dict,
)
from hachi.core.credential_vault import CredentialVault
from hachi.core.onboarding import (
    OnboardingService,
    default_api_key_env_for,
)
from hachi.core.paths import dotenv_autoload_disabled, get_hachi_paths
from hachi.core.permissions import PermissionGate
from hachi.core.secret_store import create_default_secret_store
from hachi.core.session_store import SessionStore
from hachi.gateway.control_plane import (
    GatewayControlPlane,
    GatewayHTTPServer,
)
from hachi.gateway.in_process_runtime import _InProcessGatewayRuntime
from hachi.gateway.orchestrator import AgentSession, boot_agent
from hachi.gateway.workspace import (
    ensure_device_permissions,
    ensure_workspace,
    get_permissions_file,
    get_workspace_dir,
    run_permissions_wizard,
    show_current_permissions,
)

app = typer.Typer(
    help="Hachi - The Autonomous Agent Engine powered by ObjectGraph",
    no_args_is_help=False,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(schedule_app, name="schedule")
app.add_typer(channel_app, name="channel")
app.add_typer(skill_app, name="skill")
app.add_typer(mcp_app, name="mcp")
app.add_typer(memory_app, name="memory")
app.add_typer(session_app, name="session")
app.add_typer(daemon_app, name="daemon")
app.add_typer(ogpack_app, name="pack")
app.add_typer(jobs_app, name="jobs")
app.add_typer(eval_app, name="eval")

# Sub-apps defined later in this file — registered here so they appear in --help
keys_app = typer.Typer(help="Manage named API keys and credentials in the secure vault.")
og_app = typer.Typer(help="Manage ObjectGraph (.og) skill version history.")
app.add_typer(keys_app, name="keys")
app.add_typer(og_app, name="og")

# On Windows the default console encoding is often cp1252, which cannot represent
# Unicode arrows/dashes used throughout CLI output.  Reconfigure stdout/stderr to
# UTF-8 with replacement characters so output degrades gracefully instead of raising
# UnicodeEncodeError.  The reconfigure() call is a no-op on Python builds where the
# stream doesn't support it (e.g. when stdout is not a TextIOWrapper).
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _load_dotenv_into_environ(dotenv_path: Path) -> None:
    """Merge a .env file into os.environ (existing vars are NOT overwritten).

    This ensures SMTP / Telegram / Slack credentials set in .env are visible to
    all tools that read os.environ directly — not just the provider API-key path
    in core/llm.py which has its own .env fallback.

    Set ``HACHI_DISABLE_DOTENV_AUTOLOAD=1`` to skip the merge entirely. The pytest
    harness sets this in ``tests/conftest.py`` so the suite never pulls the
    developer's real provider keys into the process — that previously flipped
    import-time skipif guards on live-gateway tests (flaky, order-dependent) and
    leaked real secrets into the test process.
    """
    if dotenv_autoload_disabled():
        return
    if not dotenv_path.exists():
        return
    try:
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass  # .env parse errors must never abort the CLI


_load_dotenv_into_environ(Path.cwd() / ".env")
# Also merge the user config-dir .env — that is where `hachi setup` persists API keys and
# OAuth client ids. Without this, a credential configured via the wizard was written to disk
# but never loaded on the next run (the loader only read cwd/.env), so Google and other
# integrations reported "not configured" despite being set. cwd/.env above wins on conflicts.
try:
    from hachi.core.paths import get_hachi_paths as _get_hachi_paths

    _load_dotenv_into_environ(_get_hachi_paths().config_dir / ".env")
except Exception:
    pass  # path resolution must never abort the CLI

_logger = logging.getLogger(__name__)

console = Console()


def _resolve_version() -> str:
    """Best-effort package version for ``--version`` (falls back to a literal)."""
    try:
        from importlib.metadata import version

        return version("hachi-og")
    except Exception:
        return "0.1.0"


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"hachi {_resolve_version()}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(  # noqa: ARG001 - consumed eagerly by the callback
        False,
        "--version",
        "-v",
        help="Show the Hachi version and exit.",
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
    """Hachi — your autonomous personal assistant.

    Run `hachi` with no command to start the assistant. Use a subcommand
    (run, setup, doctor, skill, ...) for everything else; `hachi -h` lists them.
    """
    if ctx.invoked_subcommand is not None:
        return
    _launch_assistant(repl=repl, resume=resume, continue_latest=continue_latest)


def _gateway_readiness_probe() -> tuple[bool, list[str], dict[str, str]]:
    """Derive readiness blockers for the gateway with per-check detail.

    Checks performed (all fast + side-effect-free; no paid API calls):
    - ``config_file`` — global settings TOML is present.
    - ``permissions_file`` — device-permissions .og file is present.
    - ``provider_configured`` — an LLM provider API key is resolvable from
      env / keyring (skipped for keyless providers such as Ollama).
    - ``session_store_writable`` — the parent directory for the SQLite
      sessions DB is writable (create + unlink a sentinel file).
    - ``job_worker`` — if a ResidentWorkerThread has been registered with the
      ``_GATEWAY_RESIDENT_WORKER`` module-level slot, it must be alive.

    Returns:
        A three-tuple ``(ready, blockers, checks)`` where:
        - ``ready`` is *False* when any critical check fails,
        - ``blockers`` lists the names of failing checks (empty ↔ ready),
        - ``checks`` maps every check name to a short human-readable reason.
    """
    import os as _os
    import tempfile

    from hachi.gateway import in_process_runtime as _runtime_mod

    paths = get_hachi_paths()
    checks: dict[str, str] = {}
    blockers: list[str] = []

    # --- 1. Config file present ---
    if paths.global_config_toml.exists():
        checks["config_file"] = "ok"
    else:
        checks["config_file"] = "global config TOML missing"
        blockers.append("config_file")

    # --- 2. Permissions file present ---
    if paths.permissions_file.exists():
        checks["permissions_file"] = "ok"
    else:
        checks["permissions_file"] = "device-permissions file missing"
        blockers.append("permissions_file")

    # --- 3. Provider API key resolvable (no live API call) ---
    try:
        cfg = load_effective_config()
        kind = (getattr(cfg.provider, "kind", None) or "anthropic").strip().lower()
        _keyless = {"ollama"}
        if kind in _keyless:
            checks["provider_configured"] = f"ok (keyless: {kind})"
        else:
            api_key_env = getattr(cfg.provider, "api_key_env", None)
            env_name = api_key_env or default_api_key_env_for(kind) or ""
            resolved = _os.environ.get(env_name) if env_name else None
            if not resolved and env_name:
                try:
                    resolved = create_default_secret_store().get(env_name)
                except Exception:
                    pass
            if resolved:
                checks["provider_configured"] = f"ok ({env_name})"
            else:
                checks["provider_configured"] = (
                    f"no API key for {kind!r} (expected env {env_name!r})"
                )
                blockers.append("provider_configured")
    except Exception as exc:  # config load failure is also a blocker
        checks["provider_configured"] = f"config load error: {exc}"
        blockers.append("provider_configured")

    # --- 4. Session-store directory writable ---
    try:
        store_dir = paths.sessions_db.parent
        store_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=store_dir, prefix=".readiness-")
        try:
            _os.close(fd)
        finally:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass
        checks["session_store_writable"] = "ok"
    except Exception as exc:
        checks["session_store_writable"] = f"not writable: {exc}"
        blockers.append("session_store_writable")

    # --- 5. Job-worker thread alive (if one has been registered) ---
    worker = _runtime_mod._GATEWAY_RESIDENT_WORKER
    if worker is not None:
        if worker.is_running:
            checks["job_worker"] = "ok"
        else:
            checks["job_worker"] = "resident worker thread is not alive"
            blockers.append("job_worker")
    else:
        checks["job_worker"] = "not started"  # not a blocker — pre-session startup

    return (len(blockers) == 0, blockers, checks)


#: Module-level slot for the most-recently-created in-process gateway runtime.
#: Set by :func:`_start_gateway_server` and the ``gateway`` command so that
#: :func:`~hachi.core.runtime.resume._do_resume` can call
#: :meth:`_InProcessGatewayRuntime.rehydrate_session` on cold-start resume
#: without importing the CLI or threading a runtime reference through every layer.
_ACTIVE_RUNTIME: "_InProcessGatewayRuntime | None" = None


def _wait_until_interrupted() -> None:
    """Keep process alive until Ctrl+C without busy looping."""
    sleeper = threading.Event()
    while True:
        sleeper.wait(60.0)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _default_gateway_token_path() -> Path:
    """Return the path of the persisted gateway bearer token file.

    The token is stored in ``~/.hachi/gateway-token`` and created with
    0o600 permissions (owner-read-write only) on POSIX systems.
    Windows does not support Unix permissions, but the file is placed in the
    user home directory which is access-controlled by the OS.
    """
    return Path.home() / ".hachi" / "gateway-token"


def _load_or_generate_gateway_token() -> tuple[str, bool]:
    """Load the persisted gateway bearer token or generate and persist a new one.

    Returns:
        A ``(token, generated)`` pair where ``generated`` is *True* when a new
        token was created (so the caller can print it to the console).
    """
    import secrets as _secrets
    import stat

    token_path = _default_gateway_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token, False

    # Generate a new token, write it, and chmod 0600 on POSIX.
    token = _secrets.token_urlsafe(32)
    token_path.write_text(token + "\n", encoding="utf-8")
    try:
        token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except NotImplementedError:
        pass  # Windows — chmod is a no-op here; OS-level ACLs protect the file.
    return token, True


def _resolve_gateway_auth_token(host: str) -> str:
    """Resolve (or generate) the bearer token for the gateway HTTP server.

    Resolution order (UC-AUTH-001):
    1. ``HACHI_GATEWAY_API_TOKEN`` env var (explicit, takes precedence).
    2. ``HACHI_GATEWAY_API_TOKEN_FILE`` env var → read the file.
    3. Persisted token file (``~/.hachi/gateway-token``).
    4. Generate a new ``secrets.token_urlsafe(32)``, persist it, print once.

    Loopback binding no longer exempts authentication (MR-2 / ADV-014).
    Non-loopback binding still REQUIRES an explicit token (options 1 or 2 above)
    because the auto-generated token path is only safe for a single machine.

    Returns:
        The resolved bearer token string.

    Raises:
        OSError: When binding to a non-loopback host and no explicit token is set.
    """
    token = (os.environ.get("HACHI_GATEWAY_API_TOKEN") or "").strip()
    if not token:
        token_file = (os.environ.get("HACHI_GATEWAY_API_TOKEN_FILE") or "").strip()
        if token_file:
            try:
                token = Path(token_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise OSError(
                    f"Unable to read HACHI_GATEWAY_API_TOKEN_FILE at '{token_file}': {exc}"
                ) from exc
    if token:
        return token

    # No explicit token configured.
    if not _is_loopback_host(host):
        raise OSError(
            "HACHI_GATEWAY_API_TOKEN (or HACHI_GATEWAY_API_TOKEN_FILE) is required "
            "when binding the gateway to a non-loopback host."
        )

    # Loopback: load or generate a persistent token.
    token, generated = _load_or_generate_gateway_token()
    if generated:
        token_path = _default_gateway_token_path()
        console.print(
            f"\n[bold yellow]Gateway token generated and saved to:[/bold yellow] {token_path}\n"
            f"[bold]Token:[/bold] [cyan]{token}[/cyan]\n"
            f"[dim]Set Authorization: Bearer {token} in your client.\n"
            f"Re-run to reuse the same token, or delete the file to rotate.[/dim]\n"
        )
    return token


def _run_gateway_http_server(host: str, port: int, attach_runtime: bool) -> None:
    """Run typed gateway endpoints for web and TUI consumers."""
    # P3-07: enable rotating log when running as daemon (log path from env or default).
    import os as _os

    # Mark this (headless server) process non-interactive so a first-time Google OAuth call
    # fails fast with a "run `hachi auth google`" message instead of opening a browser and
    # blocking ~120s on a server with no display. `hachi auth google` runs in its own
    # interactive process and is unaffected.
    _os.environ.setdefault("HACHI_OAUTH_NONINTERACTIVE", "1")

    from hachi.daemon import _default_log_path, setup_rotating_log

    _daemon_log = _os.environ.get("HACHI_DAEMON_LOG")
    if _daemon_log:
        setup_rotating_log(Path(_daemon_log))
    elif not any(True for h in logging.getLogger().handlers if hasattr(h, "baseFilename")):
        # Only attach file logging if no file handler is already present (e.g. not in tests).
        setup_rotating_log(_default_log_path())

    ensure_workspace()
    _apply_quick_setup_if_needed()
    if not _pre_flight_check():
        raise typer.Exit(1)
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

    # Auth is always enabled; show guidance regardless of env var origin.
    no_auth = os.environ.get("HACHI_GATEWAY_NO_AUTH", "").strip() == "1"
    if no_auth:
        auth_lines = "[bold red]WARNING: Auth disabled (HACHI_GATEWAY_NO_AUTH=1).[/bold red]\n"
    else:
        auth_lines = "Auth: [cyan]Authorization: Bearer <token>[/cyan]\n"
        token_path = _default_gateway_token_path()
        if not os.environ.get("HACHI_GATEWAY_API_TOKEN") and token_path.exists():
            auth_lines += f"Token file: [cyan]{token_path}[/cyan]\n"

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
    resume_session_id: str | None = None,
) -> tuple[GatewayHTTPServer, str, _InProcessGatewayRuntime | None]:
    """Start local gateway server and return ``(server, base_url, runtime_bridge)``.

    Startup guards (E8/T8.2):
    - ``HACHI_GATEWAY_NO_AUTH=1`` is rejected when binding a non-loopback host.
    - A non-loopback bind without a resolvable auth token raises :class:`OSError`.

    Both checks run before the server thread is created so failures are surfaced
    immediately at call time, not asynchronously inside the server thread.
    """
    # E8/T8.2 — Guard 1: NO_AUTH is only permissible on loopback.
    _no_auth_env = os.environ.get("HACHI_GATEWAY_NO_AUTH", "").strip() == "1"
    if _no_auth_env and not _is_loopback_host(host):
        raise OSError(
            f"HACHI_GATEWAY_NO_AUTH=1 is not permitted when binding to a non-loopback "
            f"host ('{host}'). Remove the env var or bind to 127.0.0.1/::1/localhost."
        )

    # E8/T8.2 — Guard 2: non-loopback bind requires an explicit auth token.
    # _resolve_gateway_auth_token already raises OSError for non-loopback without a token,
    # but we call it here unconditionally so the error is clear and happens pre-server-start.
    auth_token = _resolve_gateway_auth_token(host)
    runtime = _InProcessGatewayRuntime() if attach_runtime else None
    # When resuming, capture the session id so the first on_connect call replays it.
    if runtime is not None and resume_session_id is not None:
        runtime._pending_resume_session_id = resume_session_id
    support = runtime or _InProcessGatewayRuntime()
    # Publish the active runtime so resume.py can rehydrate cold-start sessions.
    if runtime is not None:
        global _ACTIVE_RUNTIME  # noqa: PLW0603
        _ACTIVE_RUNTIME = runtime

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
        max_active_sessions = int(os.environ.get("HACHI_GATEWAY_MAX_SESSIONS", "100"))
    except ValueError:
        max_active_sessions = 100
    try:
        max_prompt_chars = int(os.environ.get("HACHI_GATEWAY_MAX_PROMPT_CHARS", "100000"))
    except ValueError:
        max_prompt_chars = 100000

    return GatewayControlPlane(
        readiness_probe=_gateway_readiness_probe,
        on_connect=runtime.register_connection if runtime else None,
        on_disconnect=runtime.close_connection if runtime else None,
        on_chat=runtime.chat if runtime else None,
        read_events=runtime.read_events if runtime else None,
        read_events_base=runtime.read_events_base if runtime else None,
        get_session_state=runtime.session_state if runtime else None,
        get_recent_sessions=runtime.recent_sessions if runtime else None,
        get_session_messages=runtime.session_messages if runtime else None,
        delete_session=runtime.delete_session_record if runtime else None,
        get_runtime_events=runtime.runtime_events if runtime else None,
        get_mcp_inventory=runtime.mcp_inventory if runtime else None,
        get_active_jobs=runtime.active_jobs if runtime else None,
        get_failed_jobs=runtime.failed_jobs if runtime else None,
        get_permission_audit=runtime.permission_audit if runtime else None,
        # T-15: the approval registry is a process singleton, not gated on a
        # live AgentSession — wire unconditionally via `support` (always
        # constructed, unlike `runtime` which is None pre-session), mirroring
        # get_capabilities=support.capabilities below.
        list_pending_approvals=support.pending_approvals,
        resolve_approval=support.resolve_approval,
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
        list_schedules=runtime.list_schedules if runtime else None,
        cancel_schedule=runtime.cancel_schedule if runtime else None,
        toggle_schedule=runtime.toggle_schedule if runtime else None,
        run_schedule_now=runtime.run_schedule_now if runtime else None,
        # Web UI endpoints (#1–#6):
        get_skills=runtime.get_skills if runtime else None,
        get_memory=runtime.get_memory if runtime else None,
        get_memory_recall=runtime.get_memory_recall if runtime else None,
        get_og_graph=runtime.get_og_graph if runtime else None,
        get_authoring_targets=support.get_authoring_targets,
        get_authoring_target=support.get_authoring_target,
        authoring_generate=support.authoring_generate,
        authoring_apply=support.authoring_apply,
        authoring_revert=support.authoring_revert,
        authoring_save_source=support.authoring_save_source,
        get_spend=runtime.get_spend if runtime else None,
        get_spend_daily=support.get_spend_daily,
        set_capability_enabled=support.set_capability_enabled,
        # Memory CRUD:
        add_memory_fact=runtime.add_memory_fact if runtime else None,
        delete_memory_fact=runtime.delete_memory_fact if runtime else None,
        # T-08 A2/A3 — MemoryGardener review surface. get_memory_gardener_diffs needs
        # a live agent (mirrors get_memory/get_skills); decide_memory_gardener_diff
        # operates directly on the filesystem so it is always available via `support`
        # (mirrors get_kennel/register_mcp_server — see BB-6 for the runtime-vs-support
        # distinction this file already relies on).
        get_memory_gardener_diffs=runtime.get_memory_gardener_diffs if runtime else None,
        decide_memory_gardener_diff=support.decide_memory_gardener_diff,
        # G1 — new handlers:
        update_limits=support.update_limits,
        add_allowed_folder=support.add_allowed_folder,
        add_allowed_command=support.add_allowed_command,
        remove_allowed_folder=support.remove_allowed_folder,
        remove_allowed_command=support.remove_allowed_command,
        add_always_allow=support.add_always_allow,
        register_mcp_server=support.register_mcp_server,
        set_mcp_enabled=support.set_mcp_enabled,
        delete_mcp_server=support.delete_mcp_server,
        get_kennel=support.get_kennel,
        add_skill=support.add_skill_from_kennel,
        # Multi-provider admin:
        list_providers=support.list_providers,
        add_or_update_provider=support.add_or_update_provider,
        remove_provider=support.remove_provider,
        get_routing=support.get_routing,
        set_routing=support.set_routing,
        validate_routing=support.validate_routing,
        rollback_routing=support.rollback_routing,
        get_routing_audit=support.get_routing_audit,
        test_provider_connection=support.test_provider_connection,
        list_all_models=support.list_all_models,
        probe_provider=support.probe_provider,
        # P1-4 — server-side turn interrupt
        on_interrupt=runtime.interrupt_session if runtime else None,
        # Credential manager (BB-9)
        store_secret=support.store_secret,
        list_secret_names=support.list_secret_names,
        delete_secret=support.delete_secret,
        # Tavily live reload (BB-11)
        reload_search_tools=support.reload_search_tools,
        max_active_sessions=max_active_sessions,
        max_prompt_chars=max_prompt_chars,
    )


def _apply_quick_setup_if_needed() -> None:
    """Silently apply a sensible default config on first run.

    Uses :meth:`~hachi.core.onboarding.OnboardingService.best_provider_from_env`
    to pick the highest-priority provider whose credentials are present. Falls
    back to anthropic (key may be missing — ``_pre_flight_check`` surfaces that).
    """
    service = OnboardingService()
    if not service.needs_setup():
        return

    provider = service.best_provider_from_env() or {
        "kind": "anthropic",
        "model": "claude-haiku-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
    }

    plan = service.build_tier_plan(
        "lightning",
        {
            "provider": provider,
            "permission_tier": "guided",
        },
    )
    service.apply_setup(plan)


def _interactive_first_run_setup(env_name: str, provider_kind: str) -> bool:
    """Interactively collect an API key when running in a terminal.

    Called by ``_pre_flight_check`` when a key is missing and stdin is a TTY.
    Offers three paths in priority order:

    1. Type the key right now — written to ``os.environ`` for this session and
       appended to ``~/.config/hachi/.env`` for future sessions.
    2. Switch to a free alternative (Gemini free tier or local Ollama).
    3. Skip — return *False* and let ``_pre_flight_check`` abort the boot.

    Returns *True* if a key was accepted or a free provider was configured.
    """
    service = OnboardingService()
    detected = service.detect_environment()

    # Build a menu of free alternatives available right now.
    free_opts: list[tuple[str, str]] = []
    if detected.get("ollama_cli"):
        free_opts.append(("ollama", "Ollama (free, runs locally — already detected)"))
    # Gemini free tier is always an option (user may have a key or can get one).
    free_opts.append(("gemini", "Gemini free tier (get a key at https://aistudio.google.com/)"))

    console.print(
        "\n[bold yellow]You can still start right now — choose an option:[/bold yellow]\n"
        f"  [cyan]1[/cyan] Enter my [bold]{provider_kind}[/bold] API key now"
    )
    for i, (_, label) in enumerate(free_opts, start=2):
        console.print(f"  [cyan]{i}[/cyan] Switch to: {label}")
    console.print(f"  [cyan]{len(free_opts) + 2}[/cyan] Skip (I'll set the key manually)")

    valid_choices = [str(i) for i in range(1, len(free_opts) + 3)]
    try:
        pick = Prompt.ask("Your choice", choices=valid_choices, default="1")
    except (EOFError, KeyboardInterrupt):
        return False

    if pick == "1":
        # User wants to enter the key directly.
        try:
            key_value = Prompt.ask(f"Paste your {env_name}", password=True)
        except (EOFError, KeyboardInterrupt):
            return False
        key_value = (key_value or "").strip()
        if not key_value:
            console.print("[red]No key entered — aborting.[/red]")
            return False
        # Inject into this process immediately.
        os.environ[env_name] = key_value
        # Persist to .env so future runs pick it up without a shell export.
        paths = get_hachi_paths()
        env_file = paths.config_dir / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        lines = [ln for ln in lines if not ln.startswith(f"{env_name}=")]
        lines.append(f"{env_name}={key_value}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.print(f"[green]✓[/green] Key saved to {env_file} and active for this session.")
        return True

    # Switch to a free alternative.
    opt_index = int(pick) - 2  # 0-based into free_opts
    if 0 <= opt_index < len(free_opts):
        alt_kind = free_opts[opt_index][0]
        if alt_kind == "gemini":
            try:
                key_value = Prompt.ask("Paste your GEMINI_API_KEY", password=True)
            except (EOFError, KeyboardInterrupt):
                return False
            key_value = (key_value or "").strip()
            if not key_value:
                console.print("[red]No key entered — aborting.[/red]")
                return False
            os.environ["GEMINI_API_KEY"] = key_value
            paths = get_hachi_paths()
            env_file = paths.config_dir / ".env"
            env_file.parent.mkdir(parents=True, exist_ok=True)
            lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
            lines = [ln for ln in lines if not ln.startswith("GEMINI_API_KEY=")]
            lines.append(f"GEMINI_API_KEY={key_value}")
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Re-configure the provider to use the chosen alternative.
        provider_cfg = service.best_provider_from_env() or {
            "kind": alt_kind,
            "model": "gemini-2.0-flash" if alt_kind == "gemini" else "llama3",
        }
        plan = service.build_tier_plan(
            "lightning", {"provider": provider_cfg, "permission_tier": "guided"}
        )
        service.apply_setup(plan)
        console.print(f"[green]✓[/green] Switched to [bold]{provider_cfg['kind']}[/bold].")
        return True

    # Skip chosen.
    return False


def _pre_flight_check() -> bool:
    """Validate that the configured provider is ready to use.

    Runs **after** ``_apply_quick_setup_if_needed`` so the config is always
    present. Returns *True* when the provider is ready; prints a clear
    actionable banner and returns *False* when the API key is missing or the
    provider config is broken.

    When stdin is a TTY, offers an interactive recovery path so first-time
    users can paste their key without dropping to a shell.
    """
    cfg = load_effective_config()
    service = OnboardingService()
    api_key_env = getattr(cfg.provider, "api_key_env", None)
    ok, msg = service.validate_provider(kind=cfg.provider.kind, api_key_env=api_key_env)
    if ok:
        return True

    env_name = api_key_env or default_api_key_env_for(cfg.provider.kind) or "YOUR_API_KEY_ENV"
    provider_links: dict[str, str] = {
        "anthropic": "https://console.anthropic.com/settings/keys",
        "openai-compatible": "https://platform.openai.com/api-keys",
        "groq": "https://console.groq.com/keys",
        "together": "https://api.together.xyz/settings/api-keys",
        "deepseek": "https://platform.deepseek.com/api_keys",
        "openrouter": "https://openrouter.ai/keys",
        "gemini": "https://aistudio.google.com/",
    }
    link = provider_links.get(cfg.provider.kind, "")
    link_line = f"Get a key: [link]{link}[/link]\n" if link else ""

    # UC-CORE-001: The onboarding panel must work in non-interactive/piped
    # contexts (CI, Docker, subprocess).  Use ``hachi secrets --set``
    # (cross-platform) instead of Unix-only ``export VAR=...`` so the guidance
    # is actionable on Windows where ``export`` does not exist.
    if sys.stdin.isatty():
        # TTY: show export as a convenient shell shorthand alongside the CLI.
        fix_cmd = (
            f"  hachi secrets --set {env_name}=<your-key>\n"
            f"  [dim]or set the env var:[/dim] [green]{env_name}=<your-key>[/green]"
        )
    else:
        # Non-TTY/piped: skip the shell env-var hint — it may not be applicable
        # and we must not hang waiting for input.  Give the CLI command only.
        fix_cmd = f"  hachi secrets --set {env_name}=<your-key>"

    console.print(
        Panel(
            f"[bold red]API key missing — cannot start.[/bold red]\n\n"
            f"{msg}\n\n"
            f"[bold]Fix (one command):[/bold]\n"
            f"{fix_cmd}\n\n"
            f"{link_line}"
            f"Then run [bold]hachi run[/bold] again.\n"
            f"Or run [bold]hachi quickstart[/bold] to choose a different provider.",
            title="[bold red]Not Ready[/bold red]",
            border_style="red",
        )
    )

    # Interactive recovery: offer to collect the key right now when in a TTY.
    # In non-TTY contexts we return False immediately — never hang on input.
    if sys.stdin.isatty():
        fixed = _interactive_first_run_setup(env_name, cfg.provider.kind)
        if fixed:
            cfg2 = load_effective_config()
            api_key_env2 = getattr(cfg2.provider, "api_key_env", None)
            ok2, _ = service.validate_provider(kind=cfg2.provider.kind, api_key_env=api_key_env2)
            return ok2

    return False


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
    Initialize the ~/.hachi/ workspace.
    Run this when installing the agent for the first time.
    """
    try:
        ensure_workspace()
        console.print(
            Panel(
                "[bold green]Hachi workspace initialized successfully![/bold green]\n\n"
                f"Your config directory: [cyan]{get_workspace_dir()}[/cyan]\n"
                f"Your skills directory: [cyan]{get_hachi_paths().skills_dir}[/cyan]",
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
    and you're ready to run ``hachi run``.
    """
    console.print(
        Panel(
            "[bold]Welcome to Hachi[/bold] 🐕\nLet's get you set up in under a minute.",
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
            "Start chatting with:  [bold]hachi run[/bold]\n"
            "Schedule proactive tasks with:  [bold]hachi schedule add[/bold]",
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
    from hachi.core.permissions import generate_permissions_og

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
        console.print("Run [bold]hachi permissions --reset[/bold] to set up permissions.")
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
            "[yellow]No permissions file found. Run 'hachi permissions --reset' first.[/yellow]"
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
                "The next time you boot Hachi and trigger an integration,\n"
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

    console.print("[dim]Booting Hachi...[/dim]")

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
            f"[bold magenta]Hachi is online.[/bold magenta]\n"
            f"Skills loaded: [cyan]{skill_count}[/cyan] | {perm_summary}\n"
            f"Type [bold]'exit'[/bold] or [bold]'quit'[/bold] to terminate.",
            border_style="magenta",
        )
    )

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")

            if user_input.strip().lower() in ["exit", "quit"]:
                console.print("[dim]Shutting down Hachi...[/dim]")
                agent.shutdown()
                break

            if not user_input.strip():
                continue

            # The loop will emit events via _render_event as it executes
            response = agent.chat(user_input)

            # Render the final response
            console.print(Rule("[bold magenta]Hachi[/bold magenta]", style="magenta"))
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
            title = (item.get("title") or "").strip()
            title_prefix = f"[white]{title}[/white]  " if title else ""
            console.print(
                f"  [cyan]{index}[/cyan]. {title_prefix}{started}"
                f"  ·  {turns} turns  ·  [dim]{model}[/dim]"
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
    """Shared entry point for `hachi` and `hachi run`.

    REST-only: boots an in-process gateway HTTP server on loopback and launches
    the Hachi Cockpit (Textual TUI) against it over the typed REST/SSE API — the
    same gateway that serves the web UI. Pass ``--repl`` for the legacy Rich REPL.
    """
    ensure_workspace()
    _apply_quick_setup_if_needed()
    if not _pre_flight_check():
        raise typer.Exit(1)
    ensure_device_permissions(console)

    if repl:
        _run_repl()
        return

    resume_id = _resolve_resume_target(resume=resume, continue_latest=continue_latest)
    if (resume or continue_latest) and not resume_id:
        # _resolve_resume_target already printed an appropriate message.
        pass

    _launch_cockpit(resume_session_id=resume_id)


def _launch_cockpit(resume_session_id: str | None = None) -> None:
    """Boot an in-process gateway and run the Hachi Cockpit against it.

    The cockpit is a REST-only surface: it talks to the gateway over HTTP/SSE on
    loopback, never to the orchestrator in-process. The gateway (and its attached
    runtime) is torn down cleanly when the cockpit exits.

    Args:
        resume_session_id: When set, the first session booted by the runtime will
            replay this prior session's transcript so context carries over (G1-T7).
    """
    from hachi.daemon import _default_log_path, setup_rotating_log
    from hachi.tui.cockpit.app import CockpitApp

    # Route gateway/uvicorn logs to a rotating file so they never spew over the
    # Textual UI (or leave a stray traceback on exit). The cockpit owns the
    # terminal; the gateway runs silently in a background thread.
    if not any(hasattr(h, "baseFilename") for h in logging.getLogger().handlers):
        setup_rotating_log(_default_log_path())

    host = "127.0.0.1"
    try:
        token = _resolve_gateway_auth_token(host)
        server, base_url, runtime = _start_gateway_server(
            host=host,
            port=0,
            attach_runtime=True,
            resume_session_id=resume_session_id,
        )
    except OSError as exc:
        console.print(f"[bold red]Failed to start the gateway:[/] {exc}")
        raise typer.Exit(1) from exc

    try:
        CockpitApp(base_url=base_url, token=token).run()
    finally:
        if runtime is not None:
            runtime.shutdown()
        server.stop()


def _run_once(prompt: str) -> None:
    """Run a single agent turn non-interactively and print the reply.

    Powers ``hachi run --once`` (and the bare positional ``hachi run "<task>"``):
    boots an agent, runs one :meth:`AgentSession.chat` turn, writes ONLY the reply
    to stdout so it pipes byte-clean into scripts and CI, then exits. Diagnostics
    and errors go through ``console`` (Rich/stderr-ish) so they never contaminate
    the piped reply on success.
    """
    try:
        agent: AgentSession = boot_agent()
    except Exception as exc:  # noqa: BLE001 - any boot failure is a clean exit 1
        console.print(f"[bold red]Failed to boot agent:[/] {exc}")
        console.print(
            "[yellow]Hint: set your provider API key (e.g. ANTHROPIC_API_KEY) "
            "or run `hachi setup`.[/yellow]"
        )
        raise typer.Exit(1) from None

    try:
        response = agent.chat(prompt)
    except Exception as exc:  # noqa: BLE001 - a turn failure is a clean exit 1
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(1) from None
    finally:
        try:
            agent.shutdown()
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            pass

    # Raw echo (not console.print): keep stdout unwrapped and ANSI-free so the
    # reply pipes cleanly into `jq`, files, and downstream commands.
    typer.echo(response)


@app.command()
def run(
    prompt: str | None = typer.Argument(
        None,
        help="One-shot task text. With this (or --once) Hachi runs a single turn, "
        "prints the reply, and exits — no TUI. Quote multi-word prompts.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single turn and exit instead of opening the TUI. Reads the "
        "prompt from the argument, or from stdin when piped.",
    ),
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
    Start Hachi.

    Default: Textual TUI.
    One-shot: ``hachi run --once "your task"`` (or pipe a prompt via stdin) runs a
    single turn, prints the reply, and exits — no TUI. Handy in scripts and CI.
    Fallback: pass --repl for the legacy terminal REPL.
    """
    # --resume / --continue are no-argument session pickers; a stray token after
    # them (e.g. the old `hachi run --resume <id>` form) now binds to `prompt`, so
    # reject that combination explicitly instead of silently running a one-shot.
    if (resume or continue_latest) and prompt is not None:
        console.print(
            "[bold red]Error:[/] --resume/--continue take no argument. Use them "
            'alone to pick a session, or `hachi run --once "your task"` for a '
            "one-shot run."
        )
        raise typer.Exit(2)

    # One-shot: an explicit --once, or any positional prompt, means "single turn,
    # no TUI". --once with no argument reads the prompt from stdin when piped.
    if once or prompt is not None:
        text = (prompt or "").strip()
        if not text and once and not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        if not text:
            console.print(
                "[bold red]Error:[/] one-shot mode needs a prompt — pass it as an "
                'argument (hachi run --once "your task") or pipe it via stdin.'
            )
            raise typer.Exit(2)
        _run_once(text)
        return

    _launch_assistant(repl=repl, resume=resume, continue_latest=continue_latest)


@app.command("cockpit")
def cockpit(
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        envvar="HACHI_GATEWAY_URL",
        help="Gateway base URL (e.g. http://127.0.0.1:8787). "
        "Falls back to $HACHI_GATEWAY_URL; omit for offline placeholder mode.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        envvar="HACHI_GATEWAY_TOKEN",
        help="Bearer token for the gateway. Falls back to $HACHI_GATEWAY_TOKEN.",
    ),
):
    """Launch the Hachi Cockpit — the REST-only terminal cockpit UI.

    Talks to a running ``hachi gateway`` over HTTP when ``--base-url`` (or
    ``$HACHI_GATEWAY_URL``) is set; otherwise renders representative placeholder
    data so the surface is always demonstrable. For an interactive agent chat
    session, use ``hachi run`` (live chat streaming is not yet wired here).
    """
    # Lazy import: the cockpit is a self-contained REST-only surface and must not
    # pull the in-process agent stack into CLI startup.
    from hachi.tui.cockpit.app import CockpitApp

    CockpitApp(base_url=base_url, token=token).run()


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


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Gateway bind address."),
    port: int = typer.Option(8787, "--port", min=0, max=65535, help="Gateway bind port."),
    no_frontend: bool = typer.Option(
        False,
        "--no-frontend",
        help="Skip mounting the pre-built frontend (frontend/dist).",
    ),
):
    """Start the Hachi gateway AND serve the pre-built web frontend in one command.

    Binds the gateway HTTP server on *host:port*, auto-generates a bearer token
    (or reuses the persisted one at ~/.hachi/gateway-token), mounts the pre-built
    frontend at ``frontend/dist`` on the root path, and prints the URL.

    This is the recommended single-terminal way to run Hachi:

        hachi serve            # http://127.0.0.1:8787

    Use ``hachi gateway`` if you want the backend API without the frontend.
    """
    # Headless server → fail fast on a first-time Google OAuth call (run `hachi auth google`)
    # instead of opening a browser and blocking ~120s on a display-less server.
    os.environ.setdefault("HACHI_OAUTH_NONINTERACTIVE", "1")

    # Locate the pre-built frontend distribution directory.
    _frontend_dist: Path | None = None
    if not no_frontend:
        candidates = [
            Path(__file__).resolve().parents[3] / "frontend" / "dist",
            Path.cwd() / "frontend" / "dist",
        ]
        for candidate in candidates:
            if candidate.is_dir():
                _frontend_dist = candidate
                break
        if _frontend_dist is None:
            console.print(
                "[yellow]Warning: frontend/dist not found — "
                "run [bold]npm run build --prefix frontend[/bold] first "
                "to serve the web UI. Starting gateway-only.[/yellow]"
            )

    ensure_workspace()
    _apply_quick_setup_if_needed()
    if not _pre_flight_check():
        raise typer.Exit(1)

    runtime: _InProcessGatewayRuntime | None = None
    server: GatewayHTTPServer | None = None
    try:
        auth_token = _resolve_gateway_auth_token(host)
        runtime = _InProcessGatewayRuntime()
        # Publish the active runtime so resume.py can rehydrate cold-start sessions.
        global _ACTIVE_RUNTIME  # noqa: PLW0603
        _ACTIVE_RUNTIME = runtime

        server = GatewayHTTPServer(
            _build_gateway_control_plane(runtime=runtime, support=runtime),
            host=host,
            port=port,
            auth_token=auth_token,
            frontend_dist=_frontend_dist,
        )
        bound_port = server.start()
        base_url = f"http://{host}:{bound_port}"
    except OSError as exc:
        console.print(f"[bold red]Failed to start gateway:[/] {exc}")
        raise typer.Exit(1) from exc

    frontend_line = f"Web UI:     [cyan]{base_url}[/cyan]\n" if _frontend_dist else ""
    console.print(
        Panel(
            "[bold green]Hachi is running.[/bold green]\n\n"
            f"{frontend_line}"
            f"API:        [cyan]{base_url}/docs[/cyan]\n"
            f"Readiness:  [cyan]{base_url}/readiness[/cyan]\n"
            f"Auth:       [cyan]Authorization: Bearer <token>[/cyan]\n"
            f"Token file: [cyan]{_default_gateway_token_path()}[/cyan]\n\n"
            "Press [bold]Ctrl+C[/bold] to stop.",
            title="Hachi Serve",
            border_style="green",
        )
    )

    try:
        _wait_until_interrupted()
    except KeyboardInterrupt:
        console.print("[dim]Stopping...[/dim]")
    finally:
        if runtime is not None:
            runtime.shutdown()
        if server is not None:
            server.stop()


@app.command("gateway-status")
def gateway_status(
    host: str = typer.Option("127.0.0.1", "--host", help="Gateway host to check."),
    port: int = typer.Option(8787, "--port", help="Gateway port to check."),
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
    timeout: float = typer.Option(2.0, "--timeout", help="Connection timeout in seconds."),
) -> None:
    """Check whether a local Hachi gateway is reachable and healthy."""
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/health"
    result: dict = {"host": host, "port": port, "url": url}
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            result["status"] = "reachable"
            result["http_status"] = resp.status
            try:
                result["detail"] = json.loads(body)
            except Exception:
                result["detail"] = body[:200]
    except urllib.error.URLError as exc:
        result["status"] = "unreachable"
        result["error"] = str(exc.reason)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    if as_json:
        print(json.dumps(result))
        raise typer.Exit(0 if result.get("status") == "reachable" else 1)

    ok = result.get("status") == "reachable"
    icon = "[green]●[/green]" if ok else "[red]●[/red]"
    state = "reachable" if ok else result.get("status", "unknown")
    console.print(f"{icon} {host}:{port} — {state}")
    if not ok:
        console.print(f"  [dim]{result.get('error', '')}[/dim]")
        console.print(
            f"  Start gateway with: [bold]hachi gateway --host {host} --port {port}[/bold]"
        )
        raise typer.Exit(1)
    http_status = result.get("http_status", "")
    console.print(f"  HTTP {http_status}  {url}")


@app.command("gateway-openapi")
def gateway_openapi(
    output: Path = typer.Option(  # noqa: B008
        Path("openapi/hachi-openapi.json"),
        "--output",
        help="Write OpenAPI schema JSON to this path.",
    ),
):
    """Export the local gateway OpenAPI schema without starting a server process."""
    from hachi.gateway.fastapi_app import create_gateway_app

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
            "model": "gpt-4o-mini",
        }

    plan = service.build_tier_plan(
        tier,
        {
            "provider": provider,
            "permission_tier": permission_tier,
        },
    )
    service.apply_setup(plan)

    # Validate the API key immediately after saving — surface missing keys now,
    # not cryptically at boot time.
    key_ok, key_msg = service.validate_provider(
        kind=provider["kind"], api_key_env=provider.get("api_key_env")
    )
    key_status = "[green]✓ API key found.[/green]" if key_ok else f"[yellow]⚠ {key_msg}[/yellow]"

    console.print(
        Panel(
            "[bold green]Setup saved.[/bold green]\n\n"
            f"Tier: [cyan]{tier}[/cyan]\n"
            f"Provider: [cyan]{provider['kind']}[/cyan]\n"
            f"Permission tier: [cyan]{permission_tier}[/cyan]\n"
            f"{key_status}\n\n"
            "You can rerun setup anytime with [bold]hachi setup[/bold].",
            title="Onboarding",
        )
    )

    # Post-setup suggestion chips — first-magic prompts.
    try:
        from hachi.core.suggestion_chips import chips_for_context, render_chips_text

        chips = chips_for_context(max_chips=4, prioritise_composer=True)
        header = "Start here — paste any prompt into the agent:"
        chips_text = render_chips_text(chips, header=header)
        console.print(f"\n[dim]{chips_text}[/dim]")
        console.print("\n[dim]Run [bold]hachi run[/bold] to open the agent.[/dim]")
    except Exception:
        pass  # Chips are cosmetic; never block setup completion.


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
        p = get_hachi_paths()
        path_payload = {
            "config_dir": str(p.config_dir),
            "data_dir": str(p.data_dir),
            "cache_dir": str(p.cache_dir),
            "state_dir": str(p.state_dir),
            "settings_toml": str(p.global_config_toml),
        }
        console.print(
            Panel(
                json.dumps(path_payload, indent=2), title="Canonical Paths", border_style="magenta"
            )
        )


@app.command("config-validate")
def config_validate(
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON (for CI/automation)."
    ),
):
    """Validate the effective configuration and exit non-zero on any problem.

    Checks that settings.toml parses, the schema is sound (provider kind,
    permission tier, max_tokens), the configured model is consistent with the
    allowed_models guardrail, and the provider API key resolves. CI-friendly."""
    import tomllib

    from hachi.core.config_validate import validate_config

    paths = get_hachi_paths()
    checks: list[dict] = []

    # load_effective_config silently falls back to defaults on malformed TOML, so
    # parse the raw file here to surface syntax errors rather than hide them.
    toml_path = paths.global_config_toml
    if toml_path.exists():
        try:
            with toml_path.open("rb") as handle:
                tomllib.load(handle)
            checks.append({"name": "settings_toml_parses", "ok": True, "detail": str(toml_path)})
        except Exception as exc:
            checks.append(
                {"name": "settings_toml_parses", "ok": False, "detail": f"parse error: {exc}"}
            )
    else:
        checks.append(
            {"name": "settings_toml_parses", "ok": True, "detail": "no settings.toml (defaults)"}
        )

    cfg = load_effective_config()
    service = OnboardingService(paths=paths)
    key_ok, key_msg = service.validate_provider(
        kind=cfg.provider.kind, api_key_env=getattr(cfg.provider, "api_key_env", None)
    )
    report = validate_config(cfg, provider_key_ok=key_ok, provider_key_detail=key_msg)

    all_checks = checks + report["checks"]
    overall = all(c["ok"] for c in all_checks)
    result = {"ok": overall, "checks": all_checks}

    if json_output:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps(result, indent=2))
        raise typer.Exit(0 if overall else 1)

    console.print("[bold]Config validation[/bold]")
    for check in all_checks:
        mark = "[green]✓[/green]" if check["ok"] else "[red]✗[/red]"
        console.print(f"  {mark} {check['name']}  [dim]{check['detail']}[/dim]")
    verdict = "[green]Config OK[/green]" if overall else "[red]Hachi config has problems[/red]"
    console.print(f"\n{verdict}")
    raise typer.Exit(0 if overall else 1)


# Documented short config keys → their canonical nested path on ResolvedConfig.
# Keeping the friendly names as aliases avoids breaking existing docs/scripts
# while the canonical dotted keys remove the CLI/web/env tier-name drift.
_CONFIG_KEY_ALIASES = {
    "safety_tier": "permissions.tier",
    "max_tokens": "agent.max_tokens",
    "session_cost_cap_usd": "limits.max_session_cost_usd",
}


@app.command("config-get")
def config_get(
    key: str | None = typer.Argument(None, help="Dot-separated key, e.g. provider.model."),
    as_json: bool = typer.Option(False, "--json", help="Emit as JSON."),
) -> None:
    """Show one or all effective configuration values.

    Pass a key (dot-separated) to show a single value, or omit to show all
    top-level settings.  Read-only — use config-set to change values.
    """
    cfg = load_effective_config()
    flat: dict[str, object] = {
        "provider.kind": cfg.provider.kind,
        "provider.model": cfg.provider.model,
        "provider.base_url": cfg.provider.base_url,
        "permissions.tier": cfg.permissions.tier,
        "agent.max_tokens": cfg.agent.max_tokens,
        "limits.max_session_cost_usd": cfg.limits.max_session_cost_usd,
        "workspace_dir": str(get_workspace_dir()),
    }

    if key is not None:
        canonical = _CONFIG_KEY_ALIASES.get(key, key)
        if canonical not in flat:
            console.print(f"[red]Unknown key:[/red] {key!r}")
            known = sorted(flat) + sorted(_CONFIG_KEY_ALIASES)
            console.print(f"  Known keys: {', '.join(known)}")
            raise typer.Exit(1)
        value = flat[canonical]
        if as_json:
            print(json.dumps({key: value}))
        else:
            console.print(f"{key} = {value!r}")
        return

    if as_json:
        print(json.dumps(flat, default=str))
        return
    for k, v in sorted(flat.items()):
        console.print(f"[bold]{k}[/bold] = {v!r}")


@app.command("config-set")
def config_set(
    key: str = typer.Argument(..., help="Dot-separated key, e.g. provider.model."),
    value: str = typer.Argument(..., help="New value (always stored as a string or parsed)."),
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
) -> None:
    """Set a configuration value and persist it to settings.toml.

    Writable keys (canonical, with documented aliases):
      provider.model, provider.base_url,
      permissions.tier             (alias: safety_tier),
      agent.max_tokens             (alias: max_tokens),
      limits.max_session_cost_usd  (alias: session_cost_cap_usd).
    Changes take effect on the next ``hachi run``.
    """
    # BB-124: read the FILE layer only — load_effective_config() merges in
    # env/CLI/defaults, and persisting that view would bake a transient
    # override (e.g. HACHI_PERMISSION_TIER) permanently into settings.toml.
    doc = read_global_config_file()

    canonical = _CONFIG_KEY_ALIASES.get(key, key)
    try:
        if canonical == "provider.model":
            doc.setdefault("provider", {})["model"] = value
        elif canonical == "provider.base_url":
            doc.setdefault("provider", {})["base_url"] = value
        elif canonical == "permissions.tier":
            if value not in {"sandbox", "guided", "unrestricted"}:
                console.print(
                    "[red]permission tier must be one of:[/red] sandbox, guided, unrestricted"
                )
                raise typer.Exit(1)
            doc.setdefault("permissions", {})["tier"] = value
        elif canonical == "agent.max_tokens":
            doc.setdefault("agent", {})["max_tokens"] = int(value)
        elif canonical == "limits.max_session_cost_usd":
            parsed = float(value)
            if parsed < 0:
                console.print("[red]session_cost_cap_usd must be >= 0[/red]")
                raise typer.Exit(1)
            doc.setdefault("limits", {})["max_session_cost_usd"] = parsed
        else:
            console.print(f"[red]Cannot set:[/red] {key!r}  (not a writable key)")
            console.print(
                "  Writable keys: provider.model, provider.base_url, permissions.tier "
                "(safety_tier), agent.max_tokens (max_tokens), limits.max_session_cost_usd "
                "(session_cost_cap_usd)"
            )
            raise typer.Exit(1)
    except (ValueError, TypeError) as exc:
        console.print(f"[red]Invalid value for {key}:[/red] {exc}")
        raise typer.Exit(1) from exc

    write_global_config_dict(doc)

    if as_json:
        print(json.dumps({"key": key, "value": value, "ok": True}))
        return
    console.print(f"[green]Set[/green] {key} = {value!r}")
    console.print("[dim]Restart the agent for changes to take effect.[/dim]")


@app.command("google-auth")
def google_auth(
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
    revoke: bool = typer.Option(
        False, "--revoke", help="Clear stored Google credentials and exit."
    ),
) -> None:
    """Authenticate with Google Workspace (Gmail, Calendar, Drive, …).

    Opens a browser window to complete the OAuth consent flow.  Credentials
    are cached in the secure store so subsequent runs do not re-prompt.

    Requires GOOGLE_CLIENT_ID (and optionally GOOGLE_CLIENT_SECRET) to be set
    in your environment.  Run ``hachi doctor`` to check the current status.
    """
    import os

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        msg = (
            "GOOGLE_CLIENT_ID is not set.  "
            "Create OAuth 2.0 credentials at https://console.cloud.google.com/apis/credentials "
            "and export GOOGLE_CLIENT_ID=<your-id> before running this command."
        )
        if as_json:
            print(json.dumps({"ok": False, "error": "GOOGLE_CLIENT_ID not set", "detail": msg}))
            raise typer.Exit(1)
        console.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)

    from hachi.core.oauth import OAuthManager

    oauth = OAuthManager(secret_store=create_default_secret_store())

    if revoke:
        oauth.save_token("google", {})
        if as_json:
            print(json.dumps({"ok": True, "revoked": True}))
            return
        console.print("[green]Google credentials cleared.[/green]")
        return

    # Collect all scopes from the hub so we request them in one consent screen.
    from hachi.integrations.google import GoogleIntegrationHub

    hub = GoogleIntegrationHub()
    all_scopes: list[str] = []
    for svc in hub._services:
        for scope in svc.required_scopes:
            if scope not in all_scopes:
                all_scopes.append(scope)

    try:
        token = oauth.get_google_credentials(all_scopes)
        ok = bool(token)
    except Exception as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": str(exc)}))
            raise typer.Exit(1) from exc
        console.print(f"[red]Authentication failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if as_json:
        print(json.dumps({"ok": ok, "scopes_requested": len(all_scopes)}))
        if not ok:
            raise typer.Exit(1)
        return

    if ok:
        console.print("[green]✓[/green] Google Workspace authenticated successfully.")
        console.print(
            f"  [dim]{len(all_scopes)} scope(s) granted — "
            "Gmail, Calendar, Drive and more are now available.[/dim]"
        )
    else:
        console.print("[red]Authentication did not return a token.[/red]")
        raise typer.Exit(1)


@app.command()
def models(
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON (for CI/automation)."
    ),
):
    """List models the active provider exposes; also shows all configured providers."""
    from hachi.core.llm import create_llm_client, resolve_available_models

    cfg = load_effective_config()
    provider = cfg.provider
    current = provider.model
    allowed = list(getattr(provider, "allowed_models", []) or [])

    provider_config = {
        "kind": provider.kind,
        "model": provider.model,
        "api_key_ref": getattr(provider, "api_key_ref", None),
        "api_key_env": getattr(provider, "api_key_env", None),
        "base_url": getattr(provider, "base_url", None),
    }
    client = create_llm_client(provider_config, secret_store=create_default_secret_store())
    try:
        discovered = client.list_models()
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    options = resolve_available_models(discovered, allowed, current)
    source = "allowlist" if allowed else ("provider" if discovered else "configured")
    result: dict[str, object] = {
        "provider": provider.kind,
        "current": current,
        "source": source,
        "restricted": bool(allowed),
        "models": options,
    }
    named_providers: dict = getattr(cfg, "providers", {}) or {}
    if named_providers:
        result["other_providers"] = {
            n: {"kind": ps.kind, "default_model": ps.model} for n, ps in named_providers.items()
        }

    if json_output:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps(result, indent=2))
        return

    console.print(
        f"[bold]Models[/bold] for [cyan]{provider.kind}[/cyan]"
        f"  [dim](active · source: {source})[/dim]"
    )
    if not options:
        console.print("  [dim]no models available[/dim]")
    else:
        for name in options:
            mark = "  [green](current)[/green]" if name == current else ""
            console.print(f"  • {name}{mark}")
    if allowed:
        console.print("  [dim]switching is restricted to the configured allowlist.[/dim]")
    elif not discovered:
        console.print(
            "  [dim]live discovery unavailable (offline or no API key); "
            "showing the configured model.[/dim]"
        )

    if named_providers:
        console.print()
        console.print("[bold]Other configured providers[/bold]")
        for pname, ps in named_providers.items():
            console.print(f"  • [cyan]{pname}[/cyan]  [{ps.kind}]  default: {ps.model}")
        console.print(
            "  [dim]Switch with:[/dim]  /model <provider>  [dim]or[/dim]  /model <provider> <model>"
        )


@app.command()
def secrets(
    list_only: bool = typer.Option(False, "--list", help="List stored secret metadata."),
    delete: str | None = typer.Option(None, "--delete", help="Delete a secret by logical name."),
    check: bool = typer.Option(False, "--check", help="Run secret backend health check."),
    set_key: str | None = typer.Option(
        None, "--set", metavar="KEY", help="Store a secret (prompts for the value)."
    ),
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

    if set_key:
        import sys

        if not sys.stdin.isatty():
            console.print("[red]--set requires an interactive terminal (stdin is not a TTY).[/red]")
            raise typer.Exit(1)
        value = typer.prompt(f"Value for '{set_key}'", hide_input=True, confirmation_prompt=True)
        if not value:
            console.print("[red]No value provided — secret not saved.[/red]")
            raise typer.Exit(1)
        store.set(set_key, value, metadata={"name": set_key, "labels": {"source": "cli"}})
        console.print(f"[green]Saved[/green] secret: [cyan]{set_key}[/cyan]")
        return

    if list_only:
        rows = store.list_metadata()
        console.print(Panel(json.dumps(rows, indent=2), title="Secret Metadata"))
        return

    console.print("Use --list, --delete <name>, --set <key>, or --check")


@app.command()
def doctor(
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON (for CI/automation)."
    ),
):
    """Run environment diagnostics for onboarding and runtime health."""
    import sqlite3
    import sys

    paths = get_hachi_paths()
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
    mcp_servers_declared = 0
    engine = None
    try:
        from hachi.og_engine.engine import SkillEngine, lint_og_file

        engine = SkillEngine()
        engine.load_directory(paths.skills_dir)
        skill_count = engine.node_count
        mcp_servers_declared = len(engine.get_nodes_by_role("mcp"))

        if paths.skills_dir.exists():
            og_files = sorted(paths.skills_dir.glob("**/*.og"))
            reports = [lint_og_file(f) for f in og_files]
            errors = sum(r["summary"]["errors"] for r in reports)
            warnings = sum(r["summary"]["warnings"] for r in reports)
            skipped = len(engine._skipped_files)
            skipped_note = f", {skipped} unparseable" if skipped else ""
            skills_health = (
                f"{len(og_files)} file(s){skipped_note}, {errors} error(s), {warnings} warning(s) "
                "(run `hachi skill lint` for detail)"
            )
        else:
            skills_health = "no skills directory yet"
    except Exception:
        skill_count = -1  # could not load skills directory
        skills_health = "error scanning skills"
        mcp_servers_declared = -1

    # Runtime safety + limits posture: the levers that silently stop or constrain
    # an autonomous run. Surfaced so "why did it halt / get rate-limited / refuse
    # to send" is diagnosable at a glance.
    safety_and_limits = {
        "session_cost_cap_usd": os.environ.get("HACHI_MAX_SESSION_COST_USD", "unset"),
        "session_token_cap": os.environ.get("HACHI_MAX_SESSION_TOKENS", "unset"),
        "channel_rate_limit": os.environ.get("HACHI_CHANNEL_RATE_LIMIT", "default"),
        "openai_rate_limit": os.environ.get("HACHI_OPENAI_RATE_LIMIT", "default"),
        "channel_allowlist": os.environ.get("HACHI_CHANNEL_ALLOWLIST", "unset"),
        "outbound_secret_guard": (
            "disabled"
            if os.environ.get("HACHI_ALLOW_OUTBOUND_SECRETS", "").strip().lower()
            in {"1", "true", "yes"}
            else "enabled"
        ),
        "rest_allow_private": os.environ.get("HACHI_REST_ALLOW_PRIVATE", "false"),
        "tracing": os.environ.get("HACHI_TRACING", "off"),
    }

    # Per-store presence/size/health, from the SAME registry `hachi backup`
    # and `hachi restore` enumerate (T-55 / BB-145) — so a store `doctor`
    # cannot see is a store backup cannot see either, by construction.
    store_checks: dict[str, dict[str, object]] = {}
    for spec in paths.all_stores():
        present = spec.path.exists()
        store_entry: dict[str, object] = {
            "present": present,
            "kind": spec.kind,
            "derived": spec.derived,
            "size_bytes": _store_size_bytes(spec.path) if present else 0,
        }
        if present and spec.kind == "sqlite":
            # A successful open is enough for a fast diagnostic command; a full
            # PRAGMA integrity_check can take a long time on a large database
            # and doctor is expected to return quickly.
            try:
                probe_conn = sqlite3.connect(str(spec.path), timeout=2)
                try:
                    probe_conn.execute("SELECT 1")
                finally:
                    probe_conn.close()
                store_entry["ok"] = True
            except Exception as exc:
                store_entry["ok"] = False
                store_entry["error"] = str(exc)
        store_checks[spec.name] = store_entry

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
        "skills_parse_failures": (
            [s["file"] for s in engine._skipped_files] if engine is not None else []
        ),
        "mcp_servers_declared": mcp_servers_declared,
        "session_cost_cap_usd": safety_and_limits["session_cost_cap_usd"],
        "safety_and_limits": safety_and_limits,
        "onboarding_needs_setup": service.needs_setup(),
        "secret_backend": store.healthcheck(),
        "environment_detection": service.detect_environment(),
        # Additive (T-55): existing keys above are unchanged so `doctor --json`
        # stays backward-compatible for CI/automation consumers.
        "stores": store_checks,
    }
    if json_output:
        # Plain print: Rich's console soft-wraps long JSON to terminal width.
        print(json.dumps(checks, indent=2))
        return
    console.print(Panel(json.dumps(checks, indent=2), title="Hachi Doctor", border_style="green"))


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
    Run a focused security posture audit for local Hachi deployments.

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

    rest_allowlist = os.environ.get("HACHI_REST_ALLOWLIST", "").strip()
    if not rest_allowlist:
        add_finding(
            "high",
            "rest.allowlist_missing",
            "HACHI_REST_ALLOWLIST is empty; secret-bearing REST calls are blocked "
            "and outbound posture is broad.",
            "Define explicit allowed hosts, e.g. "
            "HACHI_REST_ALLOWLIST=api.example.com,*.example.org",
        )

    if os.environ.get("HACHI_REST_ALLOW_PRIVATE", "false").lower() == "true":
        add_finding(
            "high",
            "rest.private_egress_enabled",
            "Private-network REST egress is enabled.",
            "Unset HACHI_REST_ALLOW_PRIVATE or set it to false.",
        )

    from hachi.core.sandbox import container_backend_available, sandbox_audit_finding

    _sb_backend = os.environ.get("HACHI_SANDBOX_BACKEND", "process")
    _sb_finding = sandbox_audit_finding(_sb_backend, container_backend_available())
    if _sb_finding is not None:
        add_finding(*_sb_finding)

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
                title="Hachi Security Audit",
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


@app.command("memoir")
def memoir(
    year: int = typer.Option(
        None, "--year", "-y", help="Year to summarise (default: current year)."
    ),
    output: Path = typer.Option(  # noqa: B008
        None, "--output", "-o", help="Write output to file."
    ),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Generate a yearly memoir from your agent's memory journal."""
    import datetime

    from hachi.og_engine.parser import parse_og_file

    _year = year or datetime.date.today().year
    year_str = str(_year)

    paths = get_hachi_paths()
    memory_dir = paths.memory_dir

    facts: list[str] = []
    decisions: list[str] = []
    events: list[str] = []

    _role_buckets: dict[str, list[str]] = {
        "fact": facts,
        "decision": decisions,
        "episode": events,
        "event": events,
    }

    try:
        og_files = sorted(set(memory_dir.glob("*.og")) | set(memory_dir.glob("journal*.og")))
    except FileNotFoundError:
        og_files = []

    if not og_files:
        console.print("[dim]No memory files found.[/dim]")
        return

    for og_file in og_files:
        try:
            parsed = parse_og_file(str(og_file))
        except Exception:
            continue

        for node in parsed.nodes:
            # Check if the year appears anywhere in the node's text content
            node_text = " ".join(
                [
                    node.dense,
                    node.full,
                    node.summary,
                    " ".join(node.steps),
                    " ".join(node.assertions),
                    node.note,
                ]
            )
            if year_str not in node_text:
                continue

            role = (node.role or "").lower()
            bucket = _role_buckets.get(role)
            if bucket is not None:
                entry = node.dense.strip() or node.full.strip()[:120] or node.name
                bucket.append(entry)
            else:
                # Unknown role: add to events as a catch-all
                entry = node.dense.strip() or node.full.strip()[:120] or node.name
                events.append(entry)

    result: dict = {
        "year": _year,
        "facts": facts,
        "decisions": decisions,
        "events": events,
    }

    if json_out:
        output_text = json.dumps(result, indent=2)
        if output:
            output.write_text(output_text, encoding="utf-8")
            console.print(f"[green]Memoir written to[/green] {output}")
        else:
            print(output_text)
        return

    lines = [f"# Memoir {_year}\n"]
    if decisions:
        lines.append("## Decisions")
        for d in decisions:
            lines.append(f"- {d}")
        lines.append("")
    if facts:
        lines.append("## Facts")
        for f in facts:
            lines.append(f"- {f}")
        lines.append("")
    if events:
        lines.append("## Events")
        for e in events:
            lines.append(f"- {e}")
        lines.append("")

    if not (decisions or facts or events):
        lines.append(f"No memory entries found for {_year}.")

    human_text = "\n".join(lines)
    if output:
        output.write_text(human_text, encoding="utf-8")
        console.print(f"[green]Memoir written to[/green] {output}")
    else:
        console.print(Markdown(human_text))


# ---------------------------------------------------------------------------
# Task 6c — Credential management commands
# ---------------------------------------------------------------------------

KNOWN_CREDENTIAL_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "Anthropic LLM provider",
    "OPENAI_API_KEY": "OpenAI-compatible provider",
    "GOOGLE_CLIENT_ID": "Google OAuth client ID (legacy)",
    "GOOGLE_CLIENT_SECRET": "Google OAuth client secret (legacy)",
    "HACHI_OAUTH_GOOGLE_CLIENT_ID": "Google connector client ID",
    "HACHI_OAUTH_GOOGLE_CLIENT_SECRET": "Google connector client secret",
    "HACHI_OAUTH_SLACK_CLIENT_ID": "Slack connector client ID",
    "HACHI_OAUTH_SLACK_CLIENT_SECRET": "Slack connector client secret",
    "HACHI_OAUTH_DISCORD_CLIENT_ID": "Discord connector client ID",
    "HACHI_OAUTH_DISCORD_CLIENT_SECRET": "Discord connector client secret",
    "HACHI_OAUTH_NOTION_CLIENT_ID": "Notion connector client ID",
    "HACHI_OAUTH_NOTION_CLIENT_SECRET": "Notion connector client secret",
    "HACHI_OAUTH_GITHUB_CLIENT_ID": "GitHub connector client ID",
    "HACHI_OAUTH_GITHUB_CLIENT_SECRET": "GitHub connector client secret",
    "telegram_bot_token": "Telegram bot token",
    "tavily_api_key": "Tavily web search API key",
    "META_WHATSAPP_TOKEN": "WhatsApp Meta access token",
    "META_WHATSAPP_PHONE_ID": "WhatsApp phone number ID",
    "TWILIO_ACCOUNT_SID": "Twilio account SID (WhatsApp optional)",
    "TWILIO_AUTH_TOKEN": "Twilio auth token (WhatsApp optional)",
    "HACHI_STT_API_KEY": "Speech-to-text API key",
    "HACHI_TTS_API_KEY": "Text-to-speech API key",
    "HACHI_SECRET_PASSPHRASE": "Passphrase for encrypted file secret store",
}


def _mask_secret(value: str) -> str:
    """Return a masked representation of a secret value.

    Shows first 4 and last 4 characters with ellipsis in between.
    For short values (< 8 chars), masks everything.
    """
    if len(value) >= 8:
        return f"{value[:4]}…{value[-4:]}"
    return "****"


def _get_vault() -> CredentialVault:
    """Instantiate a CredentialVault using the default backing store."""
    return CredentialVault()


# -- hachi set KEY=value [KEY2=value2 ...] ------------------------------------


@app.command(name="set")
def cred_set(
    pairs: list[str] = typer.Argument(  # noqa: B008
        ...,
        metavar="KEY=value",
        help="One or more KEY=value pairs to store in the credential vault.",
    ),
) -> None:
    """Store one or more named credentials in the secure vault.

    Each argument must be in KEY=value form.  The key is validated against
    KNOWN_CREDENTIAL_KEYS; unknown keys are accepted with a warning.

    Example::

        hachi set ANTHROPIC_API_KEY=sk-abc123 tavily_api_key=tv-xyz
    """
    from rich.table import Table

    vault = _get_vault()
    table = Table(show_header=False, box=None, padding=(0, 1))

    for pair in pairs:
        if "=" not in pair:
            console.print(
                f"[red]Error:[/red] {pair!r} is not a KEY=value pair — "
                "each argument must contain '='."
            )
            raise typer.Exit(1)

        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            console.print("[red]Error:[/red] Empty key in argument — provide KEY=value.")
            raise typer.Exit(1)

        if key not in KNOWN_CREDENTIAL_KEYS:
            console.print(f"[yellow]Warning:[/yellow] unknown key {key!r} — storing anyway.")

        vault.set_token(key, value)
        table.add_row("[green]Saved:[/green]", f"[cyan]{key}[/cyan]")

    console.print(table)


# -- hachi get KEY ------------------------------------------------------------


@app.command(name="get")
def cred_get(
    key: str = typer.Argument(..., metavar="KEY", help="Credential key to retrieve."),
) -> None:
    """Retrieve a credential from the vault and print it masked.

    The full value is NEVER printed.  Use this to confirm a key is set
    without exposing it in terminal history.
    """
    vault = _get_vault()
    value: str | None = None
    try:
        value = vault.get_token(key)
    except Exception:
        value = None

    if value is None:
        console.print(f"[yellow]{key}[/yellow]: [dim]\\[not set][/dim]")
    else:
        console.print(f"[cyan]{key}[/cyan]: {_mask_secret(value)}")


# -- hachi list-keys ----------------------------------------------------------


@app.command(name="list-keys")
def cred_list_keys() -> None:
    """List all known credential keys with their current status.

    Checks the vault first, then falls back to environment variables.
    Extra vault keys not in the built-in schema are appended at the bottom.
    """
    from rich.table import Table

    vault = _get_vault()

    # Collect vault metadata to detect extra keys
    try:
        vault_meta = vault.list_token_metadata()
        vault_keys_present: set[str] = {m["name"] for m in vault_meta}
    except Exception:
        vault_keys_present = set()

    table = Table(title="Credential Keys", show_header=True, header_style="bold")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Status")

    shown: set[str] = set()

    for key, description in KNOWN_CREDENTIAL_KEYS.items():
        shown.add(key)
        # Check vault first
        vault_has: bool = key in vault_keys_present
        env_has: bool = key in os.environ

        if vault_has:
            status = "[green]SET (vault)[/green]"
        elif env_has:
            status = "[yellow]SET (env)[/yellow]"
        else:
            status = "[dim]not set[/dim]"

        table.add_row(key, description, status)

    # Append any extra vault keys not already in KNOWN_CREDENTIAL_KEYS
    extra_keys = vault_keys_present - shown
    for key in sorted(extra_keys):
        table.add_row(key, "[dim](custom)[/dim]", "[green]SET (vault)[/green]")

    console.print(table)


# -- hachi unset KEY ----------------------------------------------------------


@app.command(name="unset")
def cred_unset(
    key: str = typer.Argument(..., metavar="KEY", help="Credential key to remove."),
) -> None:
    """Remove a credential from the vault.

    If an environment variable of the same name is still set in the current
    shell session, a warning is printed because the env var will continue to
    shadow any future vault writes until the shell is restarted.
    """
    vault = _get_vault()
    try:
        vault.delete_token(key)
        console.print(f"[green]Removed:[/green] [cyan]{key}[/cyan]")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] could not remove {key!r} from vault: {exc}")

    if key in os.environ:
        console.print(
            f"[yellow]Note:[/yellow] environment variable [cyan]{key}[/cyan] is still set "
            "in the current shell session and will continue to shadow the vault entry "
            "until you restart your shell or unset it manually."
        )


# -- keys sub-app aliases (hachi keys set/get/list/unset) ---------------------


@keys_app.command(name="set")
def keys_set(
    pairs: list[str] = typer.Argument(  # noqa: B008
        ...,
        metavar="KEY=value",
        help="One or more KEY=value pairs to store in the credential vault.",
    ),
) -> None:
    """Store one or more named credentials (alias of ``hachi set``)."""
    cred_set(pairs)


@keys_app.command(name="get")
def keys_get(
    key: str = typer.Argument(..., metavar="KEY", help="Credential key to retrieve."),
) -> None:
    """Retrieve a masked credential value (alias of ``hachi get``)."""
    cred_get(key)


@keys_app.command(name="list")
def keys_list() -> None:
    """List all credential keys and their status (alias of ``hachi list-keys``)."""
    cred_list_keys()


@keys_app.command(name="unset")
def keys_unset(
    key: str = typer.Argument(..., metavar="KEY", help="Credential key to remove."),
) -> None:
    """Remove a credential from the vault (alias of ``hachi unset``)."""
    cred_unset(key)


# ---------------------------------------------------------------------------
# Task 8c — ObjectGraph version history and rollback
# ---------------------------------------------------------------------------


@og_app.command(name="history")
def og_history_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Skill name (without .og extension)."),
) -> None:
    """Show the version history of an ObjectGraph skill file.

    Reads from the append-only .og-history sidecar in the skills directory.
    """
    from rich.table import Table

    from hachi.core.og_history import list_versions

    skills_dir = get_hachi_paths().skills_dir
    versions = list_versions(name, skills_dir=skills_dir)

    if not versions:
        console.print(f"[yellow]No history found for skill:[/yellow] {name!r}")
        raise typer.Exit(0)

    table = Table(title=f"Version history: {name}", show_header=True, header_style="bold")
    table.add_column("Version", style="cyan", justify="right")
    table.add_column("Timestamp")
    table.add_column("Author")
    table.add_column("Summary")

    for entry in versions:
        table.add_row(
            str(entry.get("version", "?")),
            str(entry.get("timestamp", "")),
            str(entry.get("author", "")),
            str(entry.get("summary", "")),
        )

    console.print(table)


@og_app.command(name="rollback")
def og_rollback_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Skill name (without .og extension)."),
    version: int = typer.Option(
        ..., "--version", "-v", help="Version number to roll back to.", show_default=False
    ),
) -> None:
    """Roll back an ObjectGraph skill to a previous version.

    Retrieves the snapshot from history, writes it back to the skills
    directory (overwriting the current file), and records the rollback as
    a new history entry so the audit trail is preserved.
    """
    from hachi.core.og_history import get_version, record_version

    skills_dir = get_hachi_paths().skills_dir

    # Retrieve the historical snapshot
    content = get_version(name, version, skills_dir=skills_dir)
    if content is None:
        console.print(
            f"[red]Error:[/red] version {version} of skill {name!r} not found in history."
        )
        raise typer.Exit(1)

    # Write the snapshot back to the skills dir (match the naming convention used
    # by upsert_skill: top-level .og for hand-authored, learned/<name>.og for
    # agent-authored).  We write to whichever location already has the file;
    # if neither exists yet, we use the top-level skills dir.
    candidate_paths: list[Path] = []
    if skills_dir.exists():
        candidate_paths = [
            skills_dir / f"{name}.og",
            skills_dir / "learned" / f"{name}.og",
        ]

    target: Path | None = None
    for p in candidate_paths:
        if p.exists():
            target = p
            break

    if target is None:
        # Skill file doesn't exist yet — write to top-level skills dir
        skills_dir.mkdir(parents=True, exist_ok=True)
        target = skills_dir / f"{name}.og"

    # Atomic write via tempfile + replace to avoid corruption
    import tempfile

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=target.parent, prefix=f".{name}_rollback_", suffix=".og.tmp"
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        tmp_path.replace(target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    # Record the rollback as a new human-authored version
    new_version = record_version(
        name,
        content,
        author="human",
        summary=f"rollback to v{version}",
        skills_dir=skills_dir,
    )

    console.print(
        f"[green]Rolled back[/green] [cyan]{name}[/cyan] to v{version} "
        f"→ written to [dim]{target}[/dim] (recorded as v{new_version})."
    )


def _store_size_bytes(path: Path) -> int:
    """Best-effort on-disk size of a store: file size, or the sum of every
    regular file inside a directory. Never raises -- an unreadable path just
    reports 0 rather than failing the backup/restore/doctor command around it
    (T-55 / BB-145).
    """
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def _sqlite_online_backup(src: Path, dest: Path) -> None:
    """Consistent copy of a (possibly WAL-mode) SQLite database, in either
    direction (live -> backup file, or backup file -> live).

    Uses sqlite3's online backup API instead of a raw file copy so a
    concurrently-running gateway holding jobs.db/runs.db/files.db open in WAL
    mode cannot produce a torn or stale copy, and so a restore never leaves a
    freshly-copied main file mismatched against a stale -wal/-shm sidecar
    (T-55 §12).
    """
    import sqlite3

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Start from a clean destination so no stale -wal/-shm sidecar left over
    # from a previous file at this path can desync with the copied contents.
    for suffix in ("", "-wal", "-shm"):
        Path(str(dest) + suffix).unlink(missing_ok=True)

    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


@app.command("backup")
def backup(
    dest: Path = typer.Option(  # noqa: B008
        None,
        "--dest",
        help=(
            "Parent directory for the backup. Defaults to a 'backups' sub-dir of the Hachi"
            " state dir."
        ),
        show_default=False,
    ),
) -> None:
    """Create a timestamped backup of every durable store Hachi owns.

    Enumerates ``HachiPaths.all_stores()`` — the single source of truth shared
    with ``hachi restore`` and ``hachi doctor`` (T-55) — and captures every
    non-derived store: the sessions database (online hot-backup), the ChromaDB
    memory vault (directory copy), the OG memory graph (the CANONICAL fact
    store under the ``fts5_only`` memory backend), the user's authored
    ``.og`` skills, the job queue, run state, file attachments, checkpoints,
    schedules and non-secret config. Derived stores (the memory FTS5 index,
    the skill-embedding rank index) are skipped on purpose — they rebuild
    from a captured store — and every skip is recorded, with its reason, in
    ``manifest.json`` alongside the backup.

    Secrets (.env files) are intentionally excluded because they contain credentials
    that should be managed through your secret manager, not copied into backup dirs.

    The backup is written to a timestamped sub-directory:
        <dest>/hachi-backup-<YYYYMMDD-HHMMSS>/
    """
    import shutil
    from datetime import UTC, datetime

    paths = get_hachi_paths()

    # Resolve destination directory: default to <state_dir>/backups/
    if dest is None:
        dest = paths.state_dir / "backups"

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = dest / f"hachi-backup-{timestamp}"

    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        console.print(f"[red]Backup directory already exists: {backup_dir}[/red]")
        raise typer.Exit(1) from None
    except OSError as exc:
        console.print(f"[red]Cannot create backup directory {backup_dir}: {exc}[/red]")
        raise typer.Exit(1) from exc

    manifest: list[str] = []
    manifest_stores: list[dict[str, object]] = []
    errors: list[str] = []

    # Every store Hachi owns comes from ONE registry (core/paths.py) — do not
    # hand-maintain a second list here. See HachiPaths.all_stores().
    for spec in paths.all_stores():
        store_dest = backup_dir / spec.name
        entry: dict[str, object] = {
            "name": spec.name,
            "source": str(spec.path),
            "kind": spec.kind,
        }

        if spec.derived:
            # Derived stores rebuild from a store captured above — skipping
            # them is deliberate, not an omission. Say so, in the manifest too.
            entry["status"] = "skipped"
            entry["reason"] = f"derived (rebuildable): {spec.note}"
            manifest.append(f"  {spec.name}  (skipped — derived: {spec.note})")
            manifest_stores.append(entry)
            continue

        if not spec.path.exists():
            entry["status"] = "skipped"
            entry["reason"] = "not yet created"
            manifest.append(f"  {spec.name}  (skipped — not yet created)")
            manifest_stores.append(entry)
            continue

        try:
            if spec.name == "sessions.db":
                # Online hot-backup — safe while the gateway is running (unlike
                # a plain file copy of a live WAL database).
                store = SessionStore(db_path=spec.path)
                try:
                    store.backup(store_dest)
                finally:
                    store.shutdown()
            elif spec.kind == "sqlite":
                _sqlite_online_backup(spec.path, store_dest)
            elif spec.kind == "dir":
                # Directory copy — stop-the-world snapshot.
                shutil.copytree(str(spec.path), str(store_dest))
            else:
                store_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(spec.path), str(store_dest))
            entry["status"] = "captured"
            entry["size_bytes"] = _store_size_bytes(store_dest)
            manifest.append(f"  {spec.name}  ({spec.path})")
        except Exception as exc:
            entry["status"] = "error"
            entry["reason"] = str(exc)
            errors.append(f"{spec.name}: {exc}")

        manifest_stores.append(entry)

    # Secrets are explicitly excluded — note it in the output and the manifest.
    manifest.append("  .env files   (excluded — contain secrets; manage separately)")
    manifest_stores.append(
        {
            "name": ".env",
            "source": None,
            "kind": "file",
            "status": "excluded",
            "reason": "contains credentials; manage via your secret manager, not backups",
        }
    )

    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "stores": manifest_stores,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if errors:
        console.print("[yellow]Backup completed with errors:[/yellow]")
        for err in errors:
            console.print(f"  [red]ERROR:[/red] {err}")

    console.print(
        Panel(
            f"[bold green]Backup created:[/bold green] {backup_dir}\n\n"
            "Contents:\n" + "\n".join(manifest),
            title="hachi backup",
            border_style="green",
        )
    )


@app.command("restore")
def restore(
    src: Path = typer.Argument(  # noqa: B008
        ...,
        help="Path to a backup directory created by `hachi backup`.",
    ),
    yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt (required for non-interactive / CI use).",
    ),
) -> None:
    """Restore every durable store from a backup directory.

    Mirrors ``hachi backup`` exactly, via the same ``HachiPaths.all_stores()``
    registry (T-55): every store present in the backup replaces its live
    counterpart. Any data created after the backup was taken will be lost. A
    store that was skipped or missing at backup time is left untouched —
    restore only ever replaces a target whose source is present in the
    backup, so a partial backup can never delete data it never captured.

    Requires ``--yes`` or interactive confirmation to proceed.

    The gateway must not be running when restoring directory-based stores
    (the ChromaDB vault, the OG memory graph) — they do not support
    concurrent writers; SQLite stores are restored via
    ``sqlite3.Connection.backup()`` and are safe.
    """
    import shutil

    paths = get_hachi_paths()
    src = Path(src)

    if not src.exists() or not src.is_dir():
        console.print(f"[red]Source backup directory not found: {src}[/red]")
        raise typer.Exit(1)

    # Guard: require explicit confirmation before overwriting live data.
    if not yes:
        confirm = typer.confirm(
            f"This will overwrite your live Hachi data with the backup at:\n  {src}\nContinue?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Restore cancelled.[/yellow]")
            raise typer.Exit(0)

    # Read the manifest (if this backup has one) purely to enrich the "gap"
    # reporting below — the actual restore decision is driven by what is
    # physically present under `src`, so a pre-T-55 backup without a
    # manifest.json still restores everything it captured.
    manifest_by_name: dict[str, dict[str, object]] = {}
    manifest_path = src / "manifest.json"
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_by_name = {
                entry["name"]: entry for entry in payload.get("stores", []) if "name" in entry
            }
        except Exception:
            manifest_by_name = {}

    restored: list[str] = []
    errors: list[str] = []

    # Every store Hachi owns comes from ONE registry (core/paths.py) — do not
    # hand-maintain a second list here. See HachiPaths.all_stores().
    for spec in paths.all_stores():
        if spec.derived:
            # Derived stores were never in the backup — they rebuild live
            # from a store restored below. Nothing to do.
            continue

        store_src = src / spec.name
        if not store_src.exists():
            manifest_entry = manifest_by_name.get(spec.name)
            if manifest_entry and manifest_entry.get("status") == "error":
                restored.append(
                    f"  {spec.name}  (NOT in backup — backup failed: "
                    f"{manifest_entry.get('reason', 'unknown error')})"
                )
            else:
                restored.append(f"  {spec.name}  (not in backup — skipped)")
            continue

        try:
            spec.path.parent.mkdir(parents=True, exist_ok=True)
            if spec.kind == "sqlite":
                _sqlite_online_backup(store_src, spec.path)
            elif spec.kind == "dir":
                # Only reached when store_src exists, so it is safe to
                # replace the live directory wholesale — never delete a
                # target whose source is absent (T-55 §12).
                if spec.path.exists():
                    shutil.rmtree(str(spec.path))
                shutil.copytree(str(store_src), str(spec.path))
            else:
                shutil.copy2(str(store_src), str(spec.path))
            restored.append(f"  {spec.name}  → {spec.path}")
        except Exception as exc:
            errors.append(f"{spec.name}: {exc}")

    if errors:
        console.print("[yellow]Restore completed with errors:[/yellow]")
        for err in errors:
            console.print(f"  [red]ERROR:[/red] {err}")
        raise typer.Exit(1)

    console.print(
        Panel(
            "[bold green]Restore complete.[/bold green]\n\nRestored:\n" + "\n".join(restored),
            title="hachi restore",
            border_style="green",
        )
    )


if __name__ == "__main__":
    app()
