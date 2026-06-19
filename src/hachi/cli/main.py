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
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from hachi.cli.commands.channel import channel_app
from hachi.cli.commands.daemon import daemon_app
from hachi.cli.commands.mcp import mcp_app
from hachi.cli.commands.memory import memory_app
from hachi.cli.commands.pack import ogpack_app
from hachi.cli.commands.schedule import schedule_app
from hachi.cli.commands.session import session_app
from hachi.cli.commands.skill import skill_app
from hachi.core.config_loader import load_effective_config, write_global_config
from hachi.core.credential_vault import CredentialVault
from hachi.core.onboarding import (
    OnboardingService,
    default_api_key_env_for,
    looks_like_env_var_name,
    persist_provider_api_key,
)
from hachi.core.paths import get_hachi_paths
from hachi.core.permissions import PermissionGate
from hachi.core.secret_store import create_default_secret_store
from hachi.core.session_store import SessionStore
from hachi.gateway.control_plane import (
    ConnectRequest,
    GatewayControlPlane,
    GatewayEvent,
    GatewayHTTPServer,
    GatewayProtocolError,
)
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
    """
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


class _InProcessGatewayRuntime:
    """Bridge gateway API calls to the in-process AgentSession runtime."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSession] = {}
        self._agents_lock = threading.Lock()

    def _boot_session_agent(self, session_id: str) -> AgentSession:
        # Events are recorded by the agent's own RuntimeReadModelStore (the single
        # event log); read_events serves them by sequence. No parallel queue.
        return boot_agent()

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

    def close_connection(self, session_id: str) -> None:
        with self._agents_lock:
            agent = self._agents.pop(session_id, None)
        if agent is not None:
            try:
                agent.shutdown()
            except Exception:
                pass

    def _reload_agents(self) -> None:
        """Reboot cached session agents so provider/credential changes apply live.

        Provider config (model, base URL) and the resolved API key are baked into
        the LLM client at agent boot, so a running session keeps using whatever it
        started with. After a config mutation we rebuild each cached agent in
        place — re-reading the freshly written config + process env — and retire
        the old one. No-op when no sessions are connected (e.g. the standalone
        gateway started without ``--runtime``).
        """
        with self._agents_lock:
            session_ids = list(self._agents.keys())
        for session_id in session_ids:
            try:
                fresh = self._boot_session_agent(session_id)
            except Exception:
                continue  # keep the existing agent if a reboot fails
            with self._agents_lock:
                old = self._agents.get(session_id)
                self._agents[session_id] = fresh
            if old is not None and old is not fresh:
                try:
                    old.shutdown()
                except Exception:
                    pass

    def chat(self, session_id: str, prompt: str) -> str:
        agent = self._get_agent(session_id)
        return agent.chat(prompt)

    def read_events(self, session_id: str, after: int, limit: int) -> list[GatewayEvent]:
        snapshots = self._get_agent(session_id).snapshot_events_since(after, limit)
        return [
            GatewayEvent(
                sequence=snap.sequence,
                event_type=snap.name,
                payload=snap.payload,
                time=snap.timestamp,
            )
            for snap in snapshots
        ]

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

    def list_schedules(self) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return agent.snapshot_schedules()

    def cancel_schedule(self, schedule_id: str) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"ok": False, "detail": "no active session"}
        ok = agent.cancel_schedule(schedule_id)
        return {"ok": ok, "schedule_id": schedule_id}

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"ok": False, "detail": "no active session"}
        ok = agent.toggle_schedule_enabled(schedule_id, enabled=enabled)
        return {"ok": ok, "schedule_id": schedule_id, "enabled": enabled}

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
        current_provider_kind = str(config.provider.kind).strip()
        current_provider_model = str(config.provider.model).strip()
        kind = provider.get("kind")
        model = provider.get("model")
        base_url = provider.get("base_url")
        api_key = provider.get("api_key")
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
            from hachi.core.onboarding import resolve_provider_model

            config.provider.model = resolve_provider_model(
                str(config.provider.kind),
                model_text,
                previous_kind=current_provider_kind,
                previous_model=current_provider_model,
            )
        elif kind is not None and str(config.provider.kind).strip() != current_provider_kind:
            from hachi.core.onboarding import resolve_provider_model

            config.provider.model = resolve_provider_model(
                str(config.provider.kind),
                None,
                previous_kind=current_provider_kind,
                previous_model=current_provider_model,
            )

        if base_url is not None:
            config.provider.base_url = str(base_url).strip() or None

        # API key handling — persist raw secrets to the secret store + process
        # env (never the config file); record only the env-var NAME. A raw key may
        # arrive via `api_key` (preferred) or, for backward compatibility, as a
        # non-env-name `api_key_ref`.
        raw_api_key = api_key
        if not raw_api_key and api_key_ref and not looks_like_env_var_name(str(api_key_ref)):
            raw_api_key = api_key_ref
            api_key_ref = None

        if api_key_env is not None:
            config.provider.api_key_env = str(api_key_env).strip() or None

        if raw_api_key:
            env_name = persist_provider_api_key(
                str(config.provider.kind),
                str(raw_api_key),
                api_key_env=config.provider.api_key_env,
                secret_store=create_default_secret_store(),
            )
            if env_name:
                config.provider.api_key_ref = env_name
                config.provider.api_key_env = env_name
        elif api_key_ref is not None:
            config.provider.api_key_ref = str(api_key_ref).strip() or None

        write_global_config(config)
        # Provider/credential changes are baked into the LLM client at boot, so
        # reboot any live session to apply them without a manual restart.
        self._reload_agents()
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
        """Return provider-specific model lists.

        Anthropic models are kept current (HIGH-008); other providers use their
        known defaults.  The ``recommended`` flag marks the currently configured model.
        """
        provider_models: dict[str, list[str]] = {
            # HIGH-008: include all current Anthropic release-track models.
            "anthropic": [
                "claude-haiku-4-5",
                "claude-sonnet-4-5",
                "claude-sonnet-4-6",
                "claude-opus-4-8",
            ],
            "openai-compatible": ["gpt-4.1-mini", "gpt-4.1"],
            "ollama": ["llama3.1:8b", "qwen2.5-coder:7b"],
        }
        config = load_effective_config()
        kind = config.provider.kind
        current = config.provider.model
        models = provider_models.get(kind, [current])
        # Always include the currently configured model even if not in the list
        # (e.g. user set a preview or custom model).
        if current and current not in models:
            models = [current, *models]
        return [
            {
                "id": model,
                "label": model,
                "provider": kind,
                "recommended": model == current,
            }
            for model in models
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
        # Apply provider/credential changes from the wizard to any live session.
        self._reload_agents()
        return {
            "ok": True,
            "plan": plan.model_dump(mode="python", exclude_none=True),
            "state": service.resume_state().model_dump(mode="python", exclude_none=True),
        }

    def shutdown(self) -> None:
        with self._agents_lock:
            agents = list(self._agents.values())
            self._agents.clear()
        for agent in agents:
            try:
                agent.shutdown()
            except Exception:
                pass

    # ==========================================
    # Web endpoint handlers — added for web UI (#1–#6)
    # ==========================================

    def get_skills(self) -> list[dict]:
        """Return the .og skill inventory (route: GET /skills)."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(s) for s in agent.snapshot_skills()]

    def get_memory(self) -> dict:
        """Return grouped memory facts for the Memory screen (route: GET /memory)."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"groups": [], "blocked": [], "proposed": None}
        return agent.snapshot_memory_facts()

    def get_memory_recall(self, query: str, top_k: int = 8) -> list[dict]:
        """Return semantic recall hits (route: GET /memory/recall).

        Calls vault.semantic_search directly to preserve the similarity score,
        which query_memory_recall (on AgentSession) drops when building its snapshot.
        """
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None or agent.vault is None:
            return []
        try:
            rows = agent.vault.semantic_search(query, top_k=top_k)
        except Exception:
            return []
        results: list[dict] = []
        for row in rows:
            fact = row.get("raw_content") or row.get("content", "")
            src = row.get("category", "")
            # The row uses key "score" (from RRF merge) or "distance" (from Chroma).
            # Tolerate absence of both keys.
            raw_score = row.get("score") or row.get("distance")
            score = str(round(float(raw_score), 2)) if raw_score is not None else ""
            results.append({"fact": fact, "score": score, "src": src})
        return results

    def get_og_graph(self) -> dict:
        """Return the ObjectGraph node graph (route: GET /og/graph)."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"nodes": []}
        return agent.snapshot_og_graph()

    def get_authoring_targets(self) -> list[dict]:
        """List editable .og and config targets from the workspace (route: GET /authoring/targets).

        Reads config-dir files independently of a live agent session.
        """
        paths = get_hachi_paths()
        targets: list[dict] = []

        # Collect .og skill files from the skills directory.
        skills_dir = paths.skills_dir
        if skills_dir.exists():
            for og_file in sorted(skills_dir.glob("*.og")):
                kind = "brain" if og_file.name.startswith("brain-") else "skill"
                rel = og_file.relative_to(skills_dir).as_posix()
                targets.append({"name": og_file.name, "path": rel, "kind": kind, "dirty": False})

        # Include the permissions file (device-permissions.og).
        perm_file = paths.permissions_file
        if perm_file.exists():
            targets.append(
                {
                    "name": perm_file.name,
                    "path": perm_file.name,
                    "kind": "config",
                    "dirty": False,
                }
            )

        # Include the global config toml.
        config_toml = paths.global_config_toml
        if config_toml.exists():
            targets.append(
                {
                    "name": config_toml.name,
                    "path": config_toml.name,
                    "kind": "config",
                    "dirty": False,
                }
            )

        return targets

    def _resolve_authoring_target_path(self, name: str) -> Path:
        """Resolve a target name to a filesystem path, enforcing security constraints.

        SECURITY: reject any name containing ``/``, ``\\``, or ``..`` to prevent
        path-traversal attacks. Only names present in the allowed target list are
        accepted.

        Args:
            name: The target name (e.g. ``"brain-core.og"``).

        Returns:
            Absolute :class:`~pathlib.Path` to the file.

        Raises:
            GatewayProtocolError: When the name is unsafe or not in the allowed set.
        """
        if "/" in name or "\\" in name or ".." in name:
            raise GatewayProtocolError(f"Invalid target name '{name}'")

        allowed_targets = self.get_authoring_targets()
        allowed_names = {t["name"] for t in allowed_targets}
        if name not in allowed_names:
            raise GatewayProtocolError(f"Target '{name}' not found in allowed set")

        # Re-derive the actual path from the known safe locations.
        paths = get_hachi_paths()
        skill_path = paths.skills_dir / name
        if skill_path.exists():
            return skill_path
        perm_path = paths.permissions_file
        if perm_path.name == name:
            return perm_path
        config_path = paths.global_config_toml
        if config_path.name == name:
            return config_path
        raise GatewayProtocolError(f"Target '{name}' could not be resolved")

    def get_authoring_target(self, name: str) -> dict:
        """Return the source text for a single authoring target (GET /authoring/targets/{name})."""
        file_path = self._resolve_authoring_target_path(name)
        source = file_path.read_text(encoding="utf-8")
        return {"name": name, "source": source}

    def authoring_apply(self, name: str) -> dict:
        """Apply a patch to an authoring target (POST /authoring/targets/{name}/apply).

        STUB: There is no pending-patch store yet. Real patch-apply awaits a
        proposed-patch pipeline. Validates the target name and returns a successful
        no-op response so the UI flow works end-to-end.
        """
        self._resolve_authoring_target_path(name)
        # TODO: when the proposed-patch pipeline exists, apply the pending diff here
        # and trigger a skill-engine re-index, setting reindexed_ms to the elapsed ms.
        return {"ok": True, "reindexed_ms": 0}

    def authoring_revert(self, name: str) -> dict:
        """Revert an authoring target (POST /authoring/targets/{name}/revert).

        STUB: There is no pending-patch store yet. Real revert awaits a proposed-patch
        pipeline. Validates the target name and returns a successful no-op response.
        """
        self._resolve_authoring_target_path(name)
        # TODO: when the proposed-patch pipeline exists, drop the pending diff here.
        return {"ok": True}

    def get_spend(self, session_id: str | None = None) -> dict:
        """Return per-turn token/cost rows, compaction events, and model totals.

        Best-effort aggregation: scans the last 500 runtime events for
        ``usage.updated`` events (cumulative counters) and builds per-turn deltas.
        Compaction events (``memory.compaction``) are surfaced as ``tidies``.
        ``models`` is a single entry from the current config + snapshot_usage().

        NOTE: Per-turn input/output token split is not separately tracked in the
        runtime event payload today. The ``inT`` field is always 0 and ``out``
        carries the full token delta. Update when the event payload is enriched.
        """
        # Pick the agent: prefer explicit session_id, fall back to most-recent.
        with self._agents_lock:
            if session_id and session_id in self._agents:
                agent = self._agents[session_id]
            else:
                agent = next(iter(self._agents.values()), None)

        if agent is None:
            return {"turns": [], "tidies": [], "models": []}

        events = agent.snapshot_runtime_events(limit=500)

        turns: list[dict] = []
        tidies: list[dict] = []
        prev_total_tokens: float = 0.0
        prev_total_cost: float = 0.0
        turn_idx = 0

        for event in events:
            event_name = event.name
            payload = event.payload or {}

            # Per-turn rows from usage.updated events (cumulative → delta).
            if event_name == "usage.updated":
                total_tokens = float(payload.get("total_tokens", 0) or 0)
                total_cost = float(payload.get("estimated_cost_usd", 0) or 0)

                delta_tokens = total_tokens - prev_total_tokens
                delta_cost = total_cost - prev_total_cost

                if delta_tokens > 0 or delta_cost > 0:
                    turn_idx += 1
                    # NOTE: Per-turn input/output split not available; out = delta.
                    turns.append(
                        {
                            "n": turn_idx,
                            "inT": 0,
                            "out": int(delta_tokens),
                            "cost": round(delta_cost, 4),
                        }
                    )

                prev_total_tokens = total_tokens
                prev_total_cost = total_cost

            # Compaction events → tidy rows.
            # Real event name from runtime_events.py: "memory.compaction" (COMPACTION).
            if "memory" in event_name and ("compact" in event_name or "tidy" in event_name):
                ts = event.timestamp or ""
                # Use HH:MM portion of ISO timestamp if available.
                if len(ts) >= 16:
                    ts = ts[11:16]
                tidies.append(
                    {
                        "ts": ts,
                        "before": int(payload.get("before_tokens", 0) or 0),
                        "after": int(payload.get("after_tokens", 0) or 0),
                    }
                )

        # Model totals from snapshot_usage().
        config = load_effective_config()
        usage = agent.snapshot_usage()
        models: list[dict] = []
        if usage:
            models.append(
                {
                    "name": config.provider.model,
                    "tokens": int(usage.get("total_tokens", 0) or 0),
                    "cost": float(usage.get("estimated_cost_usd", 0) or 0),
                    "color": "",  # Client assigns color.
                }
            )

        return {"turns": turns, "tidies": tidies, "models": models}

    def set_capability_enabled(self, cap_id: str, enabled: bool) -> dict:
        """Toggle a capability on/off and persist to global config (PUT /capabilities/{id}/enabled).

        Mirrors update_permissions: load config, mutate, write, return result.
        """
        config = load_effective_config()
        if cap_id not in config.capabilities:
            raise GatewayProtocolError(f"unknown capability '{cap_id}'")
        config.capabilities[cap_id].enabled = bool(enabled)
        write_global_config(config)
        return {
            "id": cap_id,
            "enabled": bool(enabled),
            "configured": bool(config.capabilities[cap_id].configured),
        }

    # ------ Memory CRUD ------

    _VAULT_GROUP_MAP: dict[str, str] = {
        "core_memory": "upsert_fact",
        "user_profile": "upsert_user_fact",
        "agent_profile": "upsert_agent_fact",
    }

    def add_memory_fact(self, group: str, text: str) -> dict:
        """Add a fact to the specified vault collection (POST /memory/facts).

        Maps ``group`` to the appropriate upsert method on MemoryVault:
          - ``core_memory``   → vault.upsert_fact
          - ``user_profile``  → vault.upsert_user_fact
          - ``agent_profile`` → vault.upsert_agent_fact

        Args:
            group: Collection name — one of core_memory, user_profile, agent_profile.
            text:  Plain-text fact to persist.

        Returns:
            Dict with ``id`` (the content-hash fact id, possibly prefixed with
            ``BLOCKED:`` or ``LIMIT_REACHED`` when the vault rejects the write).
        """
        if not text.strip():
            raise GatewayProtocolError("text cannot be empty")
        if group not in self._VAULT_GROUP_MAP:
            raise GatewayProtocolError(
                f"group must be one of: {', '.join(sorted(self._VAULT_GROUP_MAP))}"
            )
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None or agent.vault is None:
            return {"id": "", "error": "no active session or vault unavailable"}
        method_name = self._VAULT_GROUP_MAP[group]
        upsert_fn = getattr(agent.vault, method_name, None)
        if not callable(upsert_fn):
            return {"id": "", "error": "vault method unavailable"}
        try:
            fact_id: str = upsert_fn(text.strip())
        except Exception as exc:
            return {"id": "", "error": f"vault write failed: {exc}"}
        return {"id": fact_id}

    def delete_memory_fact(self, fact_id: str) -> dict:
        """Delete a fact from the vault by its content-hash id (DELETE /memory/facts/{fact_id}).

        Tries to delete from all three collections; Chroma deletes are idempotent
        on missing IDs so multi-collection sweep is safe.

        Args:
            fact_id: SHA-256 content hash returned by any upsert method.

        Returns:
            Dict with ``ok`` boolean.
        """
        if not fact_id.strip():
            raise GatewayProtocolError("fact_id cannot be empty")
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None or agent.vault is None:
            return {"ok": False, "error": "no active session or vault unavailable"}
        vault = agent.vault
        # Try all three writable collections — each delete is idempotent on absent ids.
        results: list[bool] = []
        for fn_name in ("delete_fact", "delete_user_fact", "delete_agent_fact"):
            delete_fn = getattr(vault, fn_name, None)
            if callable(delete_fn):
                try:
                    results.append(bool(delete_fn(fact_id.strip())))
                except Exception:
                    results.append(False)
        return {"ok": any(results)}


def _gateway_readiness_probe() -> tuple[bool, list[str]]:
    """Derive readiness blockers for local gateway clients."""
    paths = get_hachi_paths()
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
        list_schedules=runtime.list_schedules if runtime else None,
        cancel_schedule=runtime.cancel_schedule if runtime else None,
        toggle_schedule=runtime.toggle_schedule if runtime else None,
        # Web UI endpoints (#1–#6):
        get_skills=runtime.get_skills if runtime else None,
        get_memory=runtime.get_memory if runtime else None,
        get_memory_recall=runtime.get_memory_recall if runtime else None,
        get_og_graph=runtime.get_og_graph if runtime else None,
        get_authoring_targets=support.get_authoring_targets,
        get_authoring_target=support.get_authoring_target,
        authoring_apply=support.authoring_apply,
        authoring_revert=support.authoring_revert,
        get_spend=runtime.get_spend if runtime else None,
        set_capability_enabled=support.set_capability_enabled,
        # Memory CRUD:
        add_memory_fact=runtime.add_memory_fact if runtime else None,
        delete_memory_fact=runtime.delete_memory_fact if runtime else None,
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

    if resume or continue_latest:
        console.print(
            "[yellow]Session resume is not yet wired into the cockpit; "
            "starting a fresh session.[/yellow]"
        )

    _launch_cockpit()


def _launch_cockpit() -> None:
    """Boot an in-process gateway and run the Hachi Cockpit against it.

    The cockpit is a REST-only surface: it talks to the gateway over HTTP/SSE on
    loopback, never to the orchestrator in-process. The gateway (and its attached
    runtime) is torn down cleanly when the cockpit exits.
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
        server, base_url, runtime = _start_gateway_server(host=host, port=0, attach_runtime=True)
    except OSError as exc:
        console.print(f"[bold red]Failed to start the gateway:[/] {exc}")
        raise typer.Exit(1) from exc

    try:
        CockpitApp(base_url=base_url, token=token).run()
    finally:
        if runtime is not None:
            runtime.shutdown()
        server.stop()


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
    Start Hachi.

    Default: Textual TUI.
    Fallback: pass --repl for the legacy terminal REPL.
    """
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
    cfg = load_effective_config()

    canonical = _CONFIG_KEY_ALIASES.get(key, key)
    try:
        if canonical == "provider.model":
            cfg.provider.model = value
        elif canonical == "provider.base_url":
            cfg.provider.base_url = value
        elif canonical == "permissions.tier":
            if value not in {"sandbox", "guided", "unrestricted"}:
                console.print(
                    "[red]permission tier must be one of:[/red] sandbox, guided, unrestricted"
                )
                raise typer.Exit(1)
            cfg.permissions.tier = value
        elif canonical == "agent.max_tokens":
            cfg.agent.max_tokens = int(value)
        elif canonical == "limits.max_session_cost_usd":
            parsed = float(value)
            if parsed < 0:
                console.print("[red]session_cost_cap_usd must be >= 0[/red]")
                raise typer.Exit(1)
            cfg.limits.max_session_cost_usd = parsed
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

    write_global_config(cfg)

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


if __name__ == "__main__":
    app()
