"""
Gateway Orchestrator

Binds the LLM client, workspace, skill engine, memory manager, integration runtime,
MCP servers, and all tools into a cohesive agent session.

Phase 2 memory architecture:
    - Session Snapshot: User profile + workspace facts refreshed per turn.
    - Async Prefetch: Background recall starts during the previous turn.
    - Auto-Sync: Facts extracted after every completed turn (via loop.py hook).

Phase 1 Update (Runtime Correctness):
    - Immutable TurnContext per turn (no mutation of shared loop state)
    - Durable job queue for background work (replaces daemon threads)
"""

import datetime
import importlib.util
import os
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from tokoboros.core.checkpoints import CheckpointManager
from tokoboros.core.cost import CostTracker
from tokoboros.core.credential_vault import CredentialVault
from tokoboros.core.integrations import IntegrationRuntime
from tokoboros.core.llm import Message, create_llm_client
from tokoboros.core.loop import ExecutionLoop, ToolRegistry
from tokoboros.core.mcp_client import MCPManager
from tokoboros.core.memory import MemoryManager
from tokoboros.core.memory_provider import ChromaMemoryProvider, resolve_provider
from tokoboros.core.memory_vault import MemoryVault
from tokoboros.core.paths import get_tokoboros_paths
from tokoboros.core.permissions import (
    InteractivePermissionGate,
    PermissionDecision,
    PermissionGate,
    PermissionRequest,
)
from tokoboros.core.rest_adapter import UniversalRestAdapter
from tokoboros.core.runtime_events import (
    BOOT_COMPLETE,
    BOOT_PROGRESS,
    MCP_SERVER_FAILED,
    MCP_SERVER_READY,
    MCP_SERVER_STARTING,
    MCP_SHUTDOWN,
    SESSION_CHAT_COMPLETED,
    SESSION_CHAT_STARTED,
    SESSION_PERSISTED,
    SESSION_RESUMED,
    SESSION_SHUTDOWN,
    RuntimeEvent,
    adapt_event,
    now_iso,
)
from tokoboros.core.runtime_read_models import (
    JobFailureSnapshot,
    JobSnapshot,
    MCPInventorySnapshot,
    MCPServerSnapshot,
    MemoryRecallSnapshot,
    PermissionAuditSnapshot,
    RuntimeEventSnapshot,
    RuntimeReadModelStore,
    SessionStateSnapshot,
    SkillSnapshot,
    SubAgentReportSnapshot,
    TaskLedgerItemSnapshot,
    ToolCallSnapshot,
)
from tokoboros.core.secret_store import create_default_secret_store
from tokoboros.core.session_store import SessionStore
from tokoboros.core.sub_agent import DEFAULT_SUB_AGENT_MODEL
from tokoboros.core.tracing import configure_from_env
from tokoboros.core.turn_context import TurnContext
from tokoboros.gateway.workspace import (
    get_skills_dir,
    get_workspace_dir,
    install_optional_skill_pack,
    load_brain,
    load_config,
)
from tokoboros.jobs import InMemoryJobQueue, Job, JobQueue, JobStatus, JobWorker
from tokoboros.jobs.scheduler import Scheduler, ScheduleStore
from tokoboros.og_engine.engine import SkillEngine
from tokoboros.tools.browser_tools import register_browser_tools
from tokoboros.tools.config_tools import register_config_tools
from tokoboros.tools.delegation_tools import register_delegation_tools
from tokoboros.tools.memory_tools import register_memory_tools, register_session_tools
from tokoboros.tools.messaging_tools import register_messaging_tools
from tokoboros.tools.notification_tools import register_notification_tools
from tokoboros.tools.og_tools import USE_TOOLS_SCHEMA, _make_use_tools, register_og_tools
from tokoboros.tools.os_tools import register_os_tools
from tokoboros.tools.sandbox_tools import register_sandbox_tools
from tokoboros.tools.schedule_tools import register_schedule_tools
from tokoboros.tools.vision_tools import register_vision_tools
from tokoboros.tools.voice_tools import register_voice_tools
from tokoboros.tools.workflow_tools import register_workflow_tools


@dataclass(slots=True)
class CapabilityDescriptor:
    id: str
    kind: str
    enabled: bool
    configured: bool
    available: bool
    requires_scopes: tuple[str, ...]
    setup_hint: str
    factory: Callable[[], Any] | None = None


class CapabilityRegistry:
    """Capability state and lazy runtime instantiation for optional components."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor]):
        self._descriptors: dict[str, CapabilityDescriptor] = {item.id: item for item in descriptors}
        self._instances: dict[str, Any] = {}

    def iter_enabled(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(item for item in self._descriptors.values() if item.enabled)

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(capability_id)

    def status_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            item.id: {
                "kind": item.kind,
                "enabled": item.enabled,
                "configured": item.configured,
                "available": item.available,
                "requires_scopes": list(item.requires_scopes),
                "setup_hint": item.setup_hint,
            }
            for item in self._descriptors.values()
        }

    def instantiate(self, capability_id: str) -> Any:
        if capability_id in self._instances:
            return self._instances[capability_id]

        descriptor = self._descriptors.get(capability_id)
        if not descriptor:
            raise KeyError(f"Unknown capability: {capability_id}")
        if not descriptor.enabled:
            raise RuntimeError(f"Capability '{capability_id}' is disabled")
        if not descriptor.available:
            raise RuntimeError(f"Capability '{capability_id}' is not available on this system")
        if not descriptor.configured:
            raise RuntimeError(f"Capability '{capability_id}' is not configured")
        if descriptor.factory is None:
            raise RuntimeError(f"Capability '{capability_id}' has no runtime factory")

        instance = descriptor.factory()
        self._instances[capability_id] = instance
        return instance


def _is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


# --- Lazy tool-loading taxonomy ------------------------------------------
# Curated "core" tools sent to the model on every turn. Everything else is
# lazily unlocked via use_tools(group) / load_skill (see ToolRegistry.classify).
_CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # filesystem / shell
        "read_file",
        "write_file",
        "run_terminal_command",
        "rollback_changes",
        # ObjectGraph skills (3-pass) + the lazy-loading meta-tool
        "search_skills",
        "list_skills",
        "peek_skill",
        "load_skill",
        "skill_dependencies",
        "check_skill_assertions",
        "skill_changelog",
        "save_skill",
        "use_tools",
        # long-term memory / sessions
        "core_save_memory",
        "core_search_memory",
        "core_delete_memory",
        "core_save_user_profile",
        "core_search_user_profile",
        "core_delete_user_profile",
        "core_search_sessions",
        # delegation
        "delegate_task",
        "list_roles",
        # web (the common case; richer browser actions are gated under "browser")
        "browse_web",
    }
)

# Non-core, statically-known tools → their on-demand group. Per-skill REST tools
# are tagged at registration (group == skill name) and are not listed here.
_GROUP_BY_TOOL_NAME: dict[str, str] = {
    "browser_act": "browser",
    "browser_screenshot": "browser",
    "python_sandbox": "sandbox",
    "schedule_task": "schedule",
    "list_schedules": "schedule",
    "cancel_schedule": "schedule",
    "core_suspend_workflow": "workflow",
    "send_slack_message": "messaging",
    "send_discord_message": "messaging",
    "send_whatsapp_message": "messaging",
    "notify_user": "notifications",
    "send_email_digest": "notifications",
    "view_image": "vision",
    "transcribe_audio": "voice",
    "speak_text": "voice",
    "play_audio": "voice",
    "get_audio_info": "voice",
}


def _coerce_float(*candidates: Any) -> float:
    """Return the first parseable float among candidates (config, then env), else 0.0."""
    for value in candidates:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _coerce_int(*candidates: Any) -> int:
    """Return the first parseable int among candidates (config, then env), else 0."""
    for value in candidates:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _build_capability_registry(config: dict[str, Any]) -> CapabilityRegistry:
    caps_cfg = config.get("capabilities", {})

    def cap_flags(cap_id: str) -> tuple[bool, bool]:
        payload = caps_cfg.get(cap_id, {}) if isinstance(caps_cfg, dict) else {}
        if not isinstance(payload, dict):
            return False, False
        return bool(payload.get("enabled", False)), bool(payload.get("configured", False))

    google_enabled, google_configured = cap_flags("google")
    slack_enabled, slack_configured = cap_flags("slack")
    notion_enabled, notion_configured = cap_flags("notion")

    return CapabilityRegistry(
        [
            CapabilityDescriptor(
                id="google",
                kind="native_integration",
                enabled=google_enabled,
                configured=google_configured,
                available=_is_module_available("tokoboros.integrations.google"),
                requires_scopes=(
                    "openid",
                    "gmail.readonly",
                    "calendar.readonly",
                    "drive.readonly",
                ),
                setup_hint="Run `tokoboros setup --tier enhanced` then connect Google in setup or "
                "settings.",
                factory=lambda: __import__(
                    "tokoboros.integrations.google", fromlist=["GoogleIntegrationHub"]
                ).GoogleIntegrationHub(),
            ),
            CapabilityDescriptor(
                id="slack",
                kind="native_integration",
                enabled=slack_enabled,
                configured=slack_configured,
                available=_is_module_available("tokoboros.integrations.slack"),
                requires_scopes=("channels:history", "chat:write", "users:read"),
                setup_hint="Run `tokoboros setup --tier enhanced` then connect Slack in setup or "
                "settings.",
                factory=lambda: (
                    __import__(
                        "tokoboros.integrations.slack", fromlist=["SlackChat", "SlackWorkspace"]
                    ).SlackChat(),
                    __import__(
                        "tokoboros.integrations.slack", fromlist=["SlackChat", "SlackWorkspace"]
                    ).SlackWorkspace(),
                ),
            ),
            CapabilityDescriptor(
                id="notion",
                kind="native_integration",
                enabled=notion_enabled,
                configured=notion_configured,
                available=_is_module_available("tokoboros.integrations.notion"),
                requires_scopes=("read:content", "write:content"),
                setup_hint="Run `tokoboros setup --tier enhanced` then connect Notion in setup or "
                "settings.",
                factory=lambda: (
                    __import__(
                        "tokoboros.integrations.notion", fromlist=["NotionPages", "NotionDatabases"]
                    ).NotionPages(),
                    __import__(
                        "tokoboros.integrations.notion", fromlist=["NotionPages", "NotionDatabases"]
                    ).NotionDatabases(),
                ),
            ),
        ]
    )


def _register_capability_setup_stub(
    tool_registry: ToolRegistry, descriptor: CapabilityDescriptor
) -> None:
    schema = {
        "name": f"setup_{descriptor.id}_capability",
        "description": (
            f"Explain how to finish setup for the optional {descriptor.id} capability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }

    def _handler() -> str:
        state_bits = []
        if not descriptor.available:
            state_bits.append("integration SDK is not installed")
        if not descriptor.configured:
            state_bits.append("credentials/scopes are not configured")
        state = "; ".join(state_bits) if state_bits else "capability is disabled"
        return f"{descriptor.id} capability is unavailable: {state}. {descriptor.setup_hint}"

    tool_registry.register(schema, _handler)


class AgentSession:
    """A persistent session wrapping the execution loop.

    Memory architecture (Phase 2):
            - Session Snapshot: User profile + top workspace facts refreshed each
                turn so newly saved profile facts are visible on the next response.
    - Async Prefetch: After each turn, a background thread pre-fetches context
      relevant to the user's latest message. The result is consumed at the START
      of the next turn — zero blocking latency.
    - Auto-Sync: The execution loop calls memory.sync_turn() after every completed
      task, extracting facts in the background without tool calls.
    """

    def __init__(
        self,
        loop: ExecutionLoop,
        skill_engine: SkillEngine,
        memory: MemoryManager,
        integrations: IntegrationRuntime,
        mcp_manager: MCPManager,
        permission_gate: Any,
        model_name: str,
        event_emitter: Callable[[str, dict[str, Any]], None] | None = None,
        read_models: RuntimeReadModelStore | None = None,
        job_queue: JobQueue | None = None,
    ):
        self.loop = loop
        self.skill_engine = skill_engine
        self.memory = memory
        self.integrations = integrations
        self.mcp_manager = mcp_manager
        self.permission_gate = permission_gate
        self.model_name = model_name
        self.vault = None
        self._original_system_prompt = loop.base_system_prompt
        self._event_emitter = event_emitter or (lambda *_a, **_kw: None)
        self._read_models = read_models or RuntimeReadModelStore()
        self._job_queue = job_queue
        self._job_worker = (
            JobWorker(job_queue, on_job_event=self._emit_event) if job_queue else None
        )

        # Phase 2: Session snapshot (rebuilt each turn to keep profile updates visible)
        self._memory_snapshot: str | None = None

        # Phase 2: Queue-backed prefetch pipeline (FIX medium: keyed by turn_id to avoid
        # cross-turn races where a stale prefetch from turn N is consumed by turn N+1)
        self._prefetch_results: dict = {}  # turn_id -> List[dict]
        self._last_prefetch_turn_id: str | None = None  # turn_id most recently enqueued
        self._prefetch_lock = threading.Lock()

        # Phase 3: Session persistence
        self._session_store: SessionStore | None = None
        self._session_id: str | None = None

        # Resume support: prior-session turns replayed for TUI transcript rendering.
        # (role, content) pairs, oldest first; empty unless booted with resume.
        self._resumed_transcript: list[tuple[str, str]] = []

    @property
    def resumed_transcript(self) -> list[tuple[str, str]]:
        """User/assistant turns carried over from a resumed session (oldest first)."""
        return list(self._resumed_transcript)

    def _emit_event(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        self._event_emitter(event_name, payload or {})

    def set_vault(self, vault: MemoryVault):
        self.vault = vault

    def set_session_store(self, store: SessionStore, model: str = "unknown"):
        """Bind a SessionStore and start a new persistent session.

        Args:
            store: The SQLite-backed session store.
            model: The LLM model name for this session's metadata.
        """
        self._session_store = store
        self._session_id = store.start_session(model=model)

    def resume_from_session(self, source_session_id: str, max_messages: int = 40) -> int:
        """Replay a prior session's transcript into active memory (full context reload).

        The bound SessionStore must already be set. Prior user/assistant turns are
        seeded into the MemoryManager so the next LLM call sees the earlier
        conversation, and cached as ``resumed_transcript`` for the UI to render.
        Tool rows are skipped — only the visible dialogue is restored.

        Args:
            source_session_id: The session whose transcript to load.
            max_messages: Cap on how many recent messages to carry over.

        Returns:
            The number of messages seeded into memory.
        """
        if not self._session_store:
            return 0

        rows = self._session_store.get_session_messages(source_session_id, limit=max_messages)
        seeded: list[Message] = []
        transcript: list[tuple[str, str]] = []
        for row in rows:
            role = str(row.get("role", ""))
            content = row.get("content") or ""
            if role not in {"user", "assistant"} or not content:
                continue
            seeded.append(Message(role=role, content=content))
            transcript.append((role, content))

        self._resumed_transcript = transcript
        return self.memory.seed_history(seeded)

    # ==========================================
    # TUI Read-Model Snapshots
    # ==========================================

    def snapshot_session_state(self) -> SessionStateSnapshot:
        """Immutable summary of current session state for UI inspection."""
        tracker = getattr(self.loop, "cost_tracker", None)
        cost = tracker.snapshot() if tracker is not None else None
        return SessionStateSnapshot(
            session_id=self._session_id or "",
            active_skill=self.memory.active_skill,
            last_user_query=getattr(self.memory, "_last_user_query", ""),
            model_name=self.model_name,
            boot_ready=self._read_models.boot_ready(),
            pending_job_count=len(self._read_models.active_jobs()),
            total_tokens=cost.total_tokens if cost else 0,
            estimated_cost_usd=round(cost.estimated_cost_usd, 6) if cost else 0.0,
        )

    def snapshot_usage(self) -> dict[str, Any]:
        """Detailed token/cost breakdown for the Ops dashboard.

        Exposes the full CostSnapshot (input/output/cache-read/cache-creation/total
        tokens, call_count, estimated_cost_usd). Empty dict before the first LLM call.
        """
        tracker = getattr(self.loop, "cost_tracker", None)
        return tracker.snapshot().to_dict() if tracker is not None else {}

    def snapshot_task_ledger(self, limit: int = 50) -> tuple[TaskLedgerItemSnapshot, ...]:
        """Immutable task ledger records for timeline views."""
        records = self.memory.task_ledger[-limit:]
        return tuple(
            TaskLedgerItemSnapshot(
                skill_name=record.skill_name,
                user_query=record.user_query,
                summary=record.summary,
            )
            for record in records
        )

    def snapshot_skills(self, limit: int = 100) -> tuple[SkillSnapshot, ...]:
        """Immutable inventory of loaded .og skills with usage stats.

        Surfaces the ObjectGraph moat in the TUI: which skills are loaded, how
        often each has been used, and when. Sorted by use_count desc (most-used
        first) by the engine.
        """
        rows = self.skill_engine.get_skill_inventory()[:limit]
        return tuple(
            SkillSnapshot(
                name=str(row.get("name", "")),
                role=str(row.get("role", "")),
                keyword_count=int(row.get("keyword_count", 0) or 0),
                use_count=int(row.get("use_count", 0) or 0),
                last_used=row.get("last_used"),
            )
            for row in rows
        )

    def list_provider_models(self) -> list[str]:
        """Best-effort live model discovery from the active provider.

        Delegates to the LLM client's ``list_models`` (network-bound); returns
        ``[]`` on any failure so callers fall back to a curated/configured list.
        """
        client = getattr(self.loop, "llm_client", None)
        lister = getattr(client, "list_models", None)
        if not callable(lister):
            return []
        try:
            return list(lister())
        except Exception:
            return []

    def query_recent_sessions(self, limit: int = 10) -> tuple[dict[str, Any], ...]:
        """Recent persisted sessions from SessionStore."""
        if not self._session_store:
            return tuple()
        return tuple(self._session_store.get_recent_sessions(limit=limit))

    def query_memory_recall(self, query: str, top_k: int = 8) -> tuple[MemoryRecallSnapshot, ...]:
        """Immutable memory recall search snapshots."""
        if not self.vault:
            return tuple()
        rows = self.vault.semantic_search(query, top_k=top_k)
        return tuple(
            MemoryRecallSnapshot(
                fact_id=row.get("id", ""),
                content=row.get("raw_content") or row.get("content", ""),
                category=row.get("category", "workspace_state"),
                timestamp=row.get("timestamp", ""),
            )
            for row in rows
        )

    def snapshot_permission_audit(self, limit: int = 50) -> tuple[PermissionAuditSnapshot, ...]:
        """Recent permission request/decision audit entries."""
        return self._read_models.permission_audit(limit=limit)

    def snapshot_mcp_inventory(self) -> MCPInventorySnapshot:
        """Current MCP server and tool inventory."""
        inventory_rows = self.mcp_manager.get_inventory()
        servers = tuple(
            MCPServerSnapshot(
                name=row.get("name", "unknown"),
                status=row.get("status", "unknown"),
                tool_count=int(row.get("tool_count", 0)),
                tools=tuple(row.get("tools", [])),
            )
            for row in inventory_rows
        )
        return MCPInventorySnapshot(
            server_count=len(servers),
            total_tool_count=sum(server.tool_count for server in servers),
            servers=servers,
        )

    def snapshot_active_jobs(self) -> tuple[JobSnapshot, ...]:
        """Active background jobs (delegations and long-running runtime operations)."""
        return self._read_models.active_jobs()

    def snapshot_failed_jobs(self, limit: int = 20) -> tuple[JobFailureSnapshot, ...]:
        """Recently failed jobs for operator diagnostics."""
        return self._read_models.failed_jobs(limit=limit)

    def query_subagent_reports(
        self, query: str = "recent delegation", top_k: int = 10
    ) -> tuple[SubAgentReportSnapshot, ...]:
        """Sub-agent execution reports from memory vault plus recent runtime events."""
        runtime_reports = list(self._read_models.subagent_reports(limit=top_k))
        if not self.vault:
            return tuple(runtime_reports)

        vault_rows = self.vault.search_delegations(query=query, top_k=top_k)
        for row in vault_rows:
            runtime_reports.append(
                SubAgentReportSnapshot(
                    role=row.get("role", "unknown"),
                    status=row.get("status", "unknown"),
                    duration_seconds=float(row.get("duration") or 0.0),
                    iterations=int(row.get("iterations") or 0),
                    summary=row.get("content", ""),
                )
            )

        return tuple(runtime_reports[-top_k:])

    def snapshot_tool_calls(self, limit: int = 50) -> tuple[ToolCallSnapshot, ...]:
        """Recent tool-call timeline (start→completion pairs with durations)."""
        return self._read_models.tool_calls(limit=limit)

    def snapshot_runtime_events(self, limit: int = 100) -> tuple[RuntimeEventSnapshot, ...]:
        """Recent normalized runtime events for operator diagnostics."""
        return self._read_models.runtime_events(limit=limit)

    # ==========================================
    # Session Memory Snapshot
    # ==========================================

    def _build_frozen_snapshot(self) -> str:
        """Build the memory snapshot used for the current chat turn.

        Called at the start of each chat() call. Combines user profile facts
        with top workspace facts so explicit memory saves are reflected on the
        very next turn.

        Returns:
            A formatted string block, or empty string if no vault/facts exist.
        """
        if not self.vault:
            return ""

        parts = []

        # User profile (bounded, high-value)
        profile = self.vault.get_user_snapshot()
        if profile:
            parts.append(profile)

        # Agent persona (bounded, evolving)
        agent_persona = self.vault.get_agent_snapshot()
        if agent_persona:
            parts.append(agent_persona)

        # Top workspace facts (general context)
        try:
            top_facts = self.vault.semantic_search("important context", top_k=8)
            if top_facts:
                lines = [f"- {f['raw_content']}" for f in top_facts]
                parts.append("## Known Context\n" + "\n".join(lines))
        except Exception:
            pass

        if not parts:
            return ""

        return (
            "\n\n--- LONG-TERM MEMORY (frozen at session start) ---\n" + "\n\n".join(parts) + "\n"
        )

    # ==========================================
    # Async Prefetch Pipeline
    # ==========================================

    def _drain_job_queue(self) -> None:
        if not self._job_worker:
            return

        while self._job_worker.execute_one():
            continue

    def _run_prefetch_job(self, query: str, turn_id: str = "compat") -> list[dict]:
        """Execute the vault recall and store the result keyed by *turn_id*."""
        results: list[dict] = []
        if self.vault:
            try:
                results = self.vault.semantic_search(query, top_k=5)
            except Exception:
                results = []

        with self._prefetch_lock:
            # FIX medium: store under turn_id so _pop_prefetch can validate provenance
            self._prefetch_results[turn_id] = results

        return results

    def _enqueue_prefetch_job(self, query: str, turn_id: str) -> None:
        # FIX medium: record the turn_id so _pop_prefetch can validate provenance
        with self._prefetch_lock:
            self._last_prefetch_turn_id = turn_id

        if not self._job_queue:
            self._run_prefetch_job(query, turn_id)
            return

        job = Job(
            job_id="",
            name="prefetch_recall",
            status=JobStatus.PENDING,
            func=self._run_prefetch_job,
            args=(query, turn_id),  # FIX medium: pass turn_id so result is stored under it
            job_type="prefetch",
            session_id=self._session_id,
            turn_id=turn_id,
        )

        try:
            self._job_queue.enqueue(job)
        except Exception:
            self._run_prefetch_job(query, turn_id)

    def _fire_prefetch(self, query: str) -> None:
        """Compatibility wrapper for the queue-backed prefetch pipeline."""
        self._enqueue_prefetch_job(query, turn_id="compat")  # legacy callers have no turn_id

    def _pop_prefetch(self) -> list[dict]:
        """Consume and delete the prefetched results for the last enqueued turn.

        FIX medium: validates the turn_id before consuming so a stale result from a
        previous (or concurrent) turn is never served to the wrong turn.
        Returns an empty list if the result is not yet ready or there is no matching entry.
        """
        with self._prefetch_lock:
            tid = self._last_prefetch_turn_id
            if tid is None:
                return []
            results = self._prefetch_results.pop(tid, [])
            self._last_prefetch_turn_id = None
        return results

    # ==========================================
    # Chat Entry Point
    # ==========================================

    @staticmethod
    def _build_time_header() -> str:
        """Generate the dynamic time block (changes every turn — NOT cached)."""
        local_dt = datetime.datetime.now().astimezone()
        current_time = local_dt.strftime("%A, %Y-%m-%d %H:%M:%S")
        tz_offset = local_dt.strftime("%z")
        tz_fmt = f"{tz_offset[:3]}:{tz_offset[3:]}" if len(tz_offset) == 5 else tz_offset

        return (
            f"--- CURRENT TIME AND DATE ---\n"
            f"The current local time is: {current_time}\n"
            f"Your Local Timezone Offset is: {tz_fmt}\n"
            f"Use this context to resolve relative times like 'today' or 'tomorrow', "
            f"and ALWAYS append the timezone offset when creating ISO strings.\n\n"
        )

    def chat(self, user_input: str) -> str:  # always returns str, never None
        """Send a message to the agent and trigger the autonomous loop.

        Architecture (Phase 1 update):
            - Creates an immutable TurnContext per turn (no shared state mutation)
            - Passes the context to ExecutionLoop.run() for use during execution
            - Previous turns' prefetch results are consumed, not blocking

        Prompt composition (per TurnContext):
            1. Time header        — changes every turn (not cached)
            2. Frozen snapshot    — stable across all turns (prefix-cached)
            3. Recalled context   — from async prefetch (turn-specific addendum)
            4. Original prompt    — brain.og + skills + permissions (stable)
        """
        # 1. Refresh memory snapshot at turn start
        turn_id = str(uuid.uuid4())[:8]
        self._emit_event(
            SESSION_CHAT_STARTED,
            {
                "turn_id": turn_id,
                "session_id": self._session_id,
                "prompt_preview": user_input[:140],
            },
        )

        self._drain_job_queue()

        self._memory_snapshot = self._build_frozen_snapshot()

        # 2. Consume async prefetch from previous turn (or sync-fetch on first turn)
        recalled = self._pop_prefetch()
        if not recalled and self.vault:
            try:
                recalled = self.vault.semantic_search(user_input, top_k=5)
            except Exception:
                recalled = []

        # 3. Build the dynamic recall injection
        recall_block = ""
        if recalled:
            lines = [f"- {r['content']}" for r in recalled]
            recall_block = (
                "\n[Recalled Context]\nRelevant facts from long-term memory:\n"
                + "\n".join(lines)
                + "\n"
            )

        # 4. Create immutable TurnContext (Phase 1)
        # This replaces the mutation of loop.base_system_prompt
        turn_context = TurnContext.build(
            turn_id=turn_id,
            session_id=self._session_id or "unknown",
            user_input=user_input,
            system_prompt_base=self._original_system_prompt,
            active_skill=self.memory.active_skill,
            user_profile_snapshot=self._memory_snapshot if self._memory_snapshot else None,
            recalled_context=recall_block if recall_block else None,
            max_iterations=15,
            surface="cli",  # TODO: configurable from caller
        )

        # 5. Add user message and run with TurnContext (no mutation)
        self.loop.add_user_message(user_input)
        # FIX medium: loop.run() may return None (e.g. empty content path); normalise to str
        response: str = self.loop.run(turn_context=turn_context) or ""

        # 6. Queue prefetch for the NEXT turn
        self._enqueue_prefetch_job(user_input, turn_id)

        # 7. Log messages to session store (non-critical, fire-and-forget)
        if self._session_store and self._session_id:
            try:
                self._session_store.log_message(self._session_id, "user", user_input)
                self._session_store.log_message(self._session_id, "assistant", response or "")
                self._emit_event(
                    SESSION_PERSISTED,
                    {
                        "turn_id": turn_id,
                        "session_id": self._session_id,
                        "messages_written": 2,
                    },
                )
            except Exception:
                pass

        self._emit_event(
            SESSION_CHAT_COMPLETED,
            {
                "turn_id": turn_id,
                "session_id": self._session_id,
                "response_preview": (response or "")[:180],
            },
        )

        return response  # guaranteed str by the `or ""` normalisation above

    def shutdown(self):
        """Gracefully close all persistent connections, MCP servers, and session store."""
        self._drain_job_queue()

        # End the persistent session
        if self._session_store and self._session_id:
            try:
                self._session_store.end_session(self._session_id)
                self._session_store.shutdown()
            except Exception:
                pass

        # Close LLM HTTP clients (OpenAICompatibleClient/ollama hold an httpx.Client;
        # AnthropicClient has no close() — guarded by getattr so both are safe).
        for client in (
            getattr(self.loop, "llm_client", None),
            getattr(self, "_sub_agent_client", None),
        ):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        # Shut down the browser worker thread + headless browser if one was started.
        browser = getattr(self, "_browser_controller", None)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        self.integrations.shutdown()
        self._emit_event(MCP_SHUTDOWN, {"server_count": self.mcp_manager.server_count})
        self.mcp_manager.shutdown()
        self._emit_event(
            SESSION_SHUTDOWN,
            {
                "session_id": self._session_id,
                "timestamp": now_iso(),
            },
        )


# ==========================================
# MCP Dynamic Configuration
# ==========================================


def _configure_mcp_servers(
    skill_engine: SkillEngine,
    mcp_manager: MCPManager,
    gate: PermissionGate,
    emit: Callable[[str, dict[str, Any]], None],
):
    """
    Start MCP servers with permissions-aware configuration.

    The filesystem MCP server's allowed directories are pulled dynamically from
    the PermissionGate instead of being hardcoded in mcp-services.og.
    Non-filesystem MCP servers (Gmail, Calendar, etc.) are started as-is.

    Args:
        skill_engine: The loaded skill engine containing MCP node definitions.
        mcp_manager: The MCP manager to start servers on.
        gate: The PermissionGate loaded from device-permissions.og.
    """
    for node in skill_engine.get_nodes_by_role("mcp"):
        emit(MCP_SERVER_STARTING, {"server": node.name})
        if node.name == "filesystem_mcp":
            # Skip entirely if user disabled MCP filesystem
            if not gate.is_mcp_filesystem_enabled():
                emit(
                    MCP_SERVER_FAILED,
                    {
                        "server": node.name,
                        "reason": "filesystem MCP disabled by permissions",
                    },
                )
                continue

            # Override the hardcoded args with resolved paths from device-permissions.og
            allowed = gate.get_allowed_paths()
            if allowed:
                path_args = " ".join(str(p) for p in allowed)
                node.config["args"] = f"-y @modelcontextprotocol/server-filesystem {path_args}"

        # Start the server (filesystem with dynamic paths, or other MCP servers unchanged)
        started = mcp_manager.setup_from_node(node)
        if started:
            emit(
                MCP_SERVER_READY,
                {
                    "server": node.name,
                    "tool_count": mcp_manager.get_server_tool_count(node.name),
                },
            )
        else:
            emit(
                MCP_SERVER_FAILED,
                {
                    "server": node.name,
                    "reason": "startup failed",
                },
            )


# ==========================================
# Boot Sequence
# ==========================================


def boot_agent(
    on_event: Callable[..., None] | None = None,
    on_permission_request: Callable[[PermissionRequest], PermissionDecision] | None = None,
    resume_session_id: str | None = None,
) -> AgentSession:
    """
    Factory function to initialize and boot the Tokoboros agent.

    Boot Sequence:
        0. Load device permissions (PermissionGate)
        1. Load config and brain.og
        2. Initialize Skill Engine (index all .og files)
        3. Load env credentials + direct integrations
        4. Start MCP servers (with dynamic filesystem paths from permissions)
        5. Build the base system prompt (including permission context)
        6. Register all tools (OS tools gated by PermissionGate)
        7. Initialize Memory
        8. Create the Execution Loop

    Args:
        on_event: Optional callback for real-time UI event rendering.

    Returns:
        An active AgentSession ready to receive user input.
    """
    # Enable OpenTelemetry tracing when TOKOBOROS_TRACING is set (no-op otherwise).
    configure_from_env()

    read_models = RuntimeReadModelStore()
    job_queue = InMemoryJobQueue()

    def emit_runtime_event(name: str, payload: dict[str, Any] | None = None) -> None:
        event = RuntimeEvent(name=name, payload=payload or {}, timestamp=now_iso())
        read_models.append_event(event)
        if on_event:
            on_event(event.name, event.payload)

    def relay_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
        envelopes = adapt_event(event_type, payload)
        for envelope in envelopes:
            event = envelope.primary
            read_models.append_event(event)
            if on_event:
                on_event(event.name, event.payload)
                for alias in envelope.legacy_aliases:
                    on_event(alias, event.payload)

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "permissions",
            "index": 1,
            "total": 11,
            "message": "Loading device permissions",
        },
    )

    # 0. Load Device Permissions (BEFORE anything else)
    permissions_file = get_workspace_dir() / "device-permissions.og"
    gate = PermissionGate(permissions_file)
    runtime_gate: Any = gate

    if on_permission_request:
        runtime_gate = InteractivePermissionGate(
            base_gate=gate,
            approval_handler=on_permission_request,
            event_emitter=emit_runtime_event,
        )

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "config",
            "index": 2,
            "total": 11,
            "message": "Loading config and brain prompt",
        },
    )

    # 1. Load Local Configuration and Brain
    config = load_config()
    brain_prompt = load_brain()

    provider_config = config.get("provider", {})
    model_name = provider_config.get("model") or config.get("agent", {}).get(
        "model", "claude-haiku-4-5"
    )
    max_tokens = config.get("agent", {}).get("max_tokens", 8192)

    capability_registry = _build_capability_registry(config)
    enabled_capabilities = [item.id for item in capability_registry.iter_enabled()]
    for capability_id in enabled_capabilities:
        install_optional_skill_pack(capability_id)

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "skills",
            "index": 3,
            "total": 11,
            "message": "Indexing .og skills",
        },
    )

    # 2. Initialize the Skill Engine (the .og brain).
    # The main session agent is the orchestrator — pass its role so that
    # brain.og's orchestrator-scoped identity and rule nodes are visible,
    # while hypothetical admin-only or worker-only nodes remain hidden.
    skill_engine = SkillEngine(agent_role="orchestrator")
    skills_dir = get_skills_dir()
    skill_engine.load_directory(skills_dir)

    skill_count = skill_engine.node_count

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "integrations",
            "index": 4,
            "total": 11,
            "message": "Initializing direct integrations",
        },
    )

    # 3. Initialize the Integration Runtime (direct SMTP/HTTP connections)
    integrations = IntegrationRuntime()
    integration_count = 0

    for node in skill_engine.get_nodes_by_role("env"):
        integrations.load_env_node(node)

    for node in skill_engine.get_nodes_by_role("integration"):
        if integrations.setup_integration(node):
            integration_count += 1

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "mcp",
            "index": 5,
            "total": 11,
            "message": "Starting MCP servers",
        },
    )

    # 4. Initialize MCP Manager (dynamically configured from permissions)
    mcp_manager = MCPManager()
    _configure_mcp_servers(skill_engine, mcp_manager, gate, emit_runtime_event)
    mcp_tool_count = mcp_manager.total_tool_count

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "native_integrations",
            "index": 6,
            "total": 11,
            "message": "Resolving optional capabilities",
        },
    )

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "prompt",
            "index": 7,
            "total": 11,
            "message": "Building system prompt",
        },
    )

    # 5. Build the base system prompt
    system_prompt = brain_prompt

    total_tools = integration_count + mcp_tool_count

    if skill_count > 0:
        system_prompt += (
            "\n\n--- IMPORTANT: ObjectGraph Skills Available (3-pass loading) ---\n"
            f"You have {skill_count} skill(s) loaded from .og files. Read them in three passes "
            "so you only spend tokens on what you actually need:\n"
            "1. `search_skills` (Pass 1) — ALWAYS call first to find relevant skills by keyword.\n"
            "2. `peek_skill` (Pass 2) — read the candidates' compressed summaries cheaply to pick "
            "the right one; don't load blind.\n"
            "3. `load_skill` (Pass 3) — load the chosen skill's full instructions (optionally "
            "narrowing to specific `blocks`), then follow them precisely.\n"
            "If a skill has ASSERTIONS, you MUST run each assertion command and verify it passes "
            "before continuing.\n"
        )

    if total_tools > 0:
        system_prompt += (
            "\n--- IMPORTANT: Live Service Integrations Active ---\n"
            f"You have {total_tools} live service tool(s) connected.\n"
            "These are REAL tools with persistent connections (e.g., email, calendar, APIs).\n"
            "Use them directly when the user's request matches their purpose.\n"
            "Do NOT write scripts or use run_terminal_command for tasks these tools handle.\n"
        )

    # 5.5 Inject Permission Context (tells the LLM its boundaries upfront)
    system_prompt += runtime_gate.get_system_prompt_injection()

    capability_snapshot = capability_registry.status_snapshot()
    if capability_snapshot:
        system_prompt += (
            "\n--- OPTIONAL CAPABILITIES ---\n"
            "Only use tools from capabilities that are enabled and configured.\n"
            f"Status: {capability_snapshot}\n"
        )

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "llm",
            "index": 8,
            "total": 11,
            "message": "Initializing LLM client",
        },
    )

    # 6. Initialize the LLM Provider
    secret_store = create_default_secret_store()
    client = create_llm_client(provider_config, secret_store=secret_store, max_tokens=max_tokens)

    # Sub-agents reuse the SAME resolved provider credentials as the main agent so
    # delegation never fails with "ANTHROPIC_API_KEY must be provided". For Anthropic
    # we drop to a cheaper model to keep delegation cost-aware; other providers keep
    # the user's configured model.
    sub_agent_provider = dict(provider_config)
    if str(provider_config.get("kind", "anthropic")).lower() == "anthropic":
        sub_agent_provider["model"] = DEFAULT_SUB_AGENT_MODEL
    sub_agent_client = create_llm_client(
        sub_agent_provider, secret_store=secret_store, max_tokens=4096
    )

    # Shared per-session token/cost tracker — used by the main loop AND threaded
    # into delegated sub-agents so their spend is included in the session total.
    cost_tracker = CostTracker()

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "memory",
            "index": 9,
            "total": 11,
            "message": "Initializing memory and persistence",
        },
    )

    # 7. Initialize the Memory Manager (Moved up so Vault can bind to it)
    memory = MemoryManager(max_active_messages=30, job_queue=job_queue)

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "tools",
            "index": 10,
            "total": 11,
            "message": "Registering tools",
        },
    )

    # 8. Register all tools
    tool_registry = ToolRegistry()
    # File-change checkpoints give the user an undo for agent edits (rollback_changes tool).
    checkpoint_manager = CheckpointManager(get_tokoboros_paths().checkpoints_dir)
    register_os_tools(tool_registry, runtime_gate, checkpoints=checkpoint_manager)
    register_og_tools(tool_registry, skill_engine)
    # Lazy tool-loading meta-tool (core): lets the model unlock optional groups.
    tool_registry.register(USE_TOOLS_SCHEMA, _make_use_tools(tool_registry), group="core")
    register_workflow_tools(tool_registry)
    register_sandbox_tools(tool_registry, runtime_gate)

    # Headless browser automation (optional: needs `tokoboros[browser]` + a browser binary).
    # The controller is lazy — no browser launches until the agent first calls browse_web.
    browser_controller = register_browser_tools(tool_registry, runtime_gate)

    # Outbound messaging (Slack/Discord/WhatsApp). Credentials resolve lazily at call time, so
    # these register cleanly even with no token set (return a "not configured" hint until then).
    register_messaging_tools(tool_registry)

    # Desktop push notifications + SMTP email digests. Zero config for desktop;
    # email requires TOKOBOROS_SMTP_* env vars (degrades gracefully if unset).
    register_notification_tools(tool_registry)

    # Vision input — view local images. Image blocks only flow to the Anthropic
    # provider, so the tool is provider-aware (it reports instead of analyzing on others).
    register_vision_tools(
        tool_registry,
        runtime_gate,
        provider_kind=str(provider_config.get("kind", "anthropic")),
    )

    # Voice input — transcribe local audio files. Credentials (OpenAI/compatible
    # Whisper) resolve lazily; the tool degrades to a "not configured" hint until set.
    register_voice_tools(tool_registry, runtime_gate)

    # Proactive scheduling (cron/interval/once) — persisted across restarts.
    scheduler = Scheduler(ScheduleStore(get_tokoboros_paths().schedules_file))
    register_schedule_tools(tool_registry, scheduler)

    # Register Universal REST APIs parsed from .og files
    credential_vault = CredentialVault(secret_store=secret_store)
    rest_adapter = UniversalRestAdapter(tool_registry, credential_vault)
    for node in skill_engine.get_all_nodes():
        rest_adapter.register_node_apis(node)

    # Initialize and register the Long-Term Memory Vault (backend-selectable).
    # backend="chroma" (default) — ChromaDB persistent vault, full semantic tools.
    # backend="in_memory" — lightweight in-process store, no ChromaDB required;
    #   semantic memory tools degrade gracefully (return an informational message).
    from tokoboros.core.settings import MemorySettings

    _raw_memory = config.get("memory", {})
    _memory_settings = MemorySettings.model_validate(_raw_memory)
    _provider = resolve_provider(_memory_settings)
    _provider.initialize(brain_dir=str(get_tokoboros_paths().data_dir))
    vault: MemoryVault | None = (
        _provider.vault if isinstance(_provider, ChromaMemoryProvider) else None
    )
    memory.set_vault(vault)
    register_memory_tools(tool_registry, vault, job_queue=job_queue)

    # Initialize Session Store (Phase 3: cross-session persistence)
    session_store = SessionStore()
    register_session_tools(tool_registry, session_store)

    # Register direct integration tools
    integrations.register_tools(tool_registry)

    # Register MCP tools
    mcp_manager.register_tools(tool_registry)

    # Agent-accessible configuration + MCP management tools.
    # Registered AFTER MCP manager so list_mcp_servers sees all nodes.
    register_config_tools(tool_registry, gate=runtime_gate)

    # Register Delegation tools (MUST be LAST — needs full parent registry)
    register_delegation_tools(
        tool_registry,
        runtime_gate,
        relay_event,
        vault,
        job_queue=job_queue,
        sub_agent_llm=sub_agent_client,
        cost_tracker=cost_tracker,
    )

    # Register native capability tools lazily, with setup stubs for incomplete states.
    native_registration_count = 0
    for descriptor in capability_registry.iter_enabled():
        if descriptor.kind != "native_integration":
            continue
        if not descriptor.available or not descriptor.configured:
            _register_capability_setup_stub(tool_registry, descriptor)
            continue

        try:
            instance = capability_registry.instantiate(descriptor.id)
        except Exception as exc:
            _register_capability_setup_stub(tool_registry, descriptor)
            emit_runtime_event(
                MCP_SERVER_FAILED,
                {
                    "server": descriptor.id,
                    "reason": f"capability init failed: {exc}",
                },
            )
            continue

        if isinstance(instance, tuple):
            for sub_instance in instance:
                if hasattr(sub_instance, "register_tools"):
                    sub_instance.register_tools(tool_registry)
                    native_registration_count += 1
        elif hasattr(instance, "register_tools"):
            instance.register_tools(tool_registry)
            native_registration_count += 1

    # Classify tools into lazy-loading groups now that every tool is registered.
    # Per-skill REST tools were already tagged at registration (group == skill name);
    # classify() leaves those intact and stamps the rest.
    tool_registry.classify(
        core_names=set(_CORE_TOOL_NAMES),
        group_by_name=_GROUP_BY_TOOL_NAME,
        # Google integration tools all share the "google_" prefix → lazy "google" group.
        prefix_groups={"google_": "google"},
        default="integrations",
    )

    # Tell the model which optional domain groups it can unlock on demand. Skill
    # groups are omitted here — load_skill auto-activates those — so this lists only
    # the static domain groups (e.g. schedule, sandbox, browser, workflow).
    skill_group_names = {node.name for node in skill_engine.get_all_nodes()}
    domain_groups = sorted(tool_registry.available_groups() - {"core"} - skill_group_names)
    if domain_groups:
        system_prompt += (
            "\n--- OPTIONAL TOOL GROUPS (lazy-loaded) ---\n"
            "To keep your context lean, only core tools are shown by default. These extra "
            f"groups are available on demand: {', '.join(domain_groups)}.\n"
            'Call use_tools(group="<name>") to activate a group for the rest of the turn. '
            "load_skill(name=...) auto-activates that skill's own tools.\n"
        )

    emit_runtime_event(
        BOOT_PROGRESS,
        {
            "step": "loop",
            "index": 11,
            "total": 11,
            "message": "Finalizing execution loop",
        },
    )

    # Per-session safety caps (0 = unlimited): stop a runaway loop from draining the
    # user's API budget. Read from the optional [limits] config section, else env vars.
    limits_cfg = config.get("limits", {}) if isinstance(config.get("limits"), dict) else {}
    max_session_cost_usd = _coerce_float(
        limits_cfg.get("max_session_cost_usd"), os.environ.get("TOKOBOROS_MAX_SESSION_COST_USD")
    )
    max_session_tokens = _coerce_int(
        limits_cfg.get("max_session_tokens"), os.environ.get("TOKOBOROS_MAX_SESSION_TOKENS")
    )
    max_turn_cost_usd = _coerce_float(
        limits_cfg.get("max_turn_cost_usd"), os.environ.get("TOKOBOROS_MAX_TURN_COST_USD")
    )
    max_turn_tokens = _coerce_int(
        limits_cfg.get("max_turn_tokens"), os.environ.get("TOKOBOROS_MAX_TURN_TOKENS")
    )

    # 9. Instantiate the Brain Loop (cost_tracker created earlier, shared with sub-agents)
    loop = ExecutionLoop(
        llm_client=client,
        registry=tool_registry,
        memory=memory,
        base_system_prompt=system_prompt,
        on_event=relay_event,
        skill_engine=skill_engine,
        cost_tracker=cost_tracker,
        max_cost_usd=max_session_cost_usd,
        max_tokens=max_session_tokens,
        max_cost_per_turn=max_turn_cost_usd,
        max_tokens_per_turn=max_turn_tokens,
    )

    # 10. Create the Session
    session = AgentSession(
        loop,
        skill_engine,
        memory,
        integrations,
        mcp_manager,
        runtime_gate,
        model_name=model_name,
        event_emitter=emit_runtime_event,
        read_models=read_models,
        job_queue=job_queue,
    )
    session.set_vault(vault)
    session.set_session_store(session_store, model=model_name)
    if resume_session_id:
        try:
            seeded = session.resume_from_session(resume_session_id)
            emit_runtime_event(
                SESSION_RESUMED,
                {
                    "source_session_id": resume_session_id,
                    "messages_restored": seeded,
                },
            )
        except Exception:
            # Resume is best-effort — a corrupt/missing transcript must not block boot.
            pass
    session._sub_agent_client = sub_agent_client  # closed on shutdown alongside the main client
    session._browser_controller = browser_controller  # closed on shutdown (no-op if never used)

    emit_runtime_event(
        BOOT_COMPLETE,
        {
            "skills": skill_count,
            "integrations": integration_count,
            "native_integrations": native_registration_count,
            "capabilities": capability_snapshot,
            "mcp_servers": mcp_manager.server_count,
            "mcp_tools": mcp_tool_count,
        },
    )

    return session
