"""
Gateway Orchestrator

Binds the LLM client, workspace, skill engine, memory manager, integration runtime,
MCP servers, and all tools into a cohesive agent session.

Phase 2 memory architecture:
    - Frozen Snapshot: User profile + workspace facts loaded once, prefix-cache safe.
    - Async Prefetch: Background recall starts during the previous turn.
    - Auto-Sync: Facts extracted after every completed turn (via loop.py hook).
"""

import datetime
import importlib.util
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from tokoboros.core.llm import create_llm_client
from tokoboros.core.loop import ExecutionLoop, ToolRegistry
from tokoboros.core.memory import MemoryManager
from tokoboros.core.integrations import IntegrationRuntime
from tokoboros.core.mcp_client import MCPManager
from tokoboros.core.permissions import InteractivePermissionGate, PermissionDecision, PermissionGate, PermissionRequest
from tokoboros.gateway.workspace import (
    get_skills_dir,
    get_workspace_dir,
    install_optional_skill_pack,
    load_brain,
    load_config,
)
from tokoboros.og_engine.engine import SkillEngine
from tokoboros.tools.os_tools import register_os_tools
from tokoboros.tools.og_tools import register_og_tools
from tokoboros.core.memory_vault import MemoryVault
from tokoboros.core.session_store import SessionStore
from tokoboros.tools.memory_tools import register_memory_tools, register_session_tools
from tokoboros.tools.workflow_tools import register_workflow_tools
from tokoboros.tools.sandbox_tools import register_sandbox_tools
from tokoboros.tools.delegation_tools import register_delegation_tools
from tokoboros.core.rest_adapter import UniversalRestAdapter
from tokoboros.core.credential_vault import CredentialVault
from tokoboros.core.secret_store import create_default_secret_store
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
    SESSION_SHUTDOWN,
    RuntimeEvent,
    adapt_event,
    now_iso,
)
from tokoboros.core.runtime_read_models import (
    JobSnapshot,
    MCPInventorySnapshot,
    MCPServerSnapshot,
    MemoryRecallSnapshot,
    PermissionAuditSnapshot,
    RuntimeEventSnapshot,
    RuntimeReadModelStore,
    SessionStateSnapshot,
    SubAgentReportSnapshot,
    TaskLedgerItemSnapshot,
)


@dataclass(slots=True)
class CapabilityDescriptor:
    id: str
    kind: str
    enabled: bool
    configured: bool
    available: bool
    requires_scopes: tuple[str, ...]
    setup_hint: str
    factory: Optional[Callable[[], Any]] = None


class CapabilityRegistry:
    """Capability state and lazy runtime instantiation for optional components."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor]):
        self._descriptors: Dict[str, CapabilityDescriptor] = {item.id: item for item in descriptors}
        self._instances: Dict[str, Any] = {}

    def iter_enabled(self) -> Tuple[CapabilityDescriptor, ...]:
        return tuple(item for item in self._descriptors.values() if item.enabled)

    def get(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        return self._descriptors.get(capability_id)

    def status_snapshot(self) -> Dict[str, Dict[str, Any]]:
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


def _build_capability_registry(config: Dict[str, Any]) -> CapabilityRegistry:
    caps_cfg = config.get("capabilities", {})

    def cap_flags(cap_id: str) -> Tuple[bool, bool]:
        payload = caps_cfg.get(cap_id, {}) if isinstance(caps_cfg, dict) else {}
        if not isinstance(payload, dict):
            return False, False
        return bool(payload.get("enabled", False)), bool(payload.get("configured", False))

    google_enabled, google_configured = cap_flags("google")
    slack_enabled, slack_configured = cap_flags("slack")
    notion_enabled, notion_configured = cap_flags("notion")

    return CapabilityRegistry([
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
            setup_hint="Run `tokoboros setup --tier enhanced` then connect Google in setup or settings.",
            factory=lambda: __import__("tokoboros.integrations.google", fromlist=["GoogleIntegrationHub"]).GoogleIntegrationHub(),
        ),
        CapabilityDescriptor(
            id="slack",
            kind="native_integration",
            enabled=slack_enabled,
            configured=slack_configured,
            available=_is_module_available("tokoboros.integrations.slack"),
            requires_scopes=("channels:history", "chat:write", "users:read"),
            setup_hint="Run `tokoboros setup --tier enhanced` then connect Slack in setup or settings.",
            factory=lambda: (
                __import__("tokoboros.integrations.slack", fromlist=["SlackChat", "SlackWorkspace"]).SlackChat(),
                __import__("tokoboros.integrations.slack", fromlist=["SlackChat", "SlackWorkspace"]).SlackWorkspace(),
            ),
        ),
        CapabilityDescriptor(
            id="notion",
            kind="native_integration",
            enabled=notion_enabled,
            configured=notion_configured,
            available=_is_module_available("tokoboros.integrations.notion"),
            requires_scopes=("read:content", "write:content"),
            setup_hint="Run `tokoboros setup --tier enhanced` then connect Notion in setup or settings.",
            factory=lambda: (
                __import__("tokoboros.integrations.notion", fromlist=["NotionPages", "NotionDatabases"]).NotionPages(),
                __import__("tokoboros.integrations.notion", fromlist=["NotionPages", "NotionDatabases"]).NotionDatabases(),
            ),
        ),
    ])


def _register_capability_setup_stub(tool_registry: ToolRegistry, descriptor: CapabilityDescriptor) -> None:
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
        return (
            f"{descriptor.id} capability is unavailable: {state}. "
            f"{descriptor.setup_hint}"
        )

    tool_registry.register(schema, _handler)


class AgentSession:
    """A persistent session wrapping the execution loop.

    Memory architecture (Phase 2):
        - Frozen Snapshot: User profile + top workspace facts loaded ONCE at first
          chat() call. Injected as a stable prefix so Anthropic's prompt cache
          can hit on every subsequent turn (~40% cost reduction).
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
        event_emitter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        read_models: Optional[RuntimeReadModelStore] = None,
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

        # Phase 2: Frozen snapshot (built once, stable for prefix cache)
        self._memory_snapshot: Optional[str] = None

        # Phase 2: Async prefetch pipeline
        self._prefetch_result: Optional[List[dict]] = None
        self._prefetch_lock = threading.Lock()

        # Phase 3: Session persistence
        self._session_store: Optional[SessionStore] = None
        self._session_id: Optional[str] = None

    def _emit_event(self, event_name: str, payload: Optional[Dict[str, Any]] = None) -> None:
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

    # ==========================================
    # TUI Read-Model Snapshots
    # ==========================================

    def snapshot_session_state(self) -> SessionStateSnapshot:
        """Immutable summary of current session state for UI inspection."""
        return SessionStateSnapshot(
            session_id=self._session_id or "",
            active_skill=self.memory.active_skill,
            last_user_query=getattr(self.memory, "_last_user_query", ""),
            model_name=self.model_name,
            boot_ready=self._read_models.boot_ready(),
            pending_job_count=len(self._read_models.active_jobs()),
        )

    def snapshot_task_ledger(self, limit: int = 50) -> Tuple[TaskLedgerItemSnapshot, ...]:
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

    def query_recent_sessions(self, limit: int = 10) -> Tuple[Dict[str, Any], ...]:
        """Recent persisted sessions from SessionStore."""
        if not self._session_store:
            return tuple()
        return tuple(self._session_store.get_recent_sessions(limit=limit))

    def query_memory_recall(self, query: str, top_k: int = 8) -> Tuple[MemoryRecallSnapshot, ...]:
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

    def snapshot_permission_audit(self, limit: int = 50) -> Tuple[PermissionAuditSnapshot, ...]:
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

    def snapshot_active_jobs(self) -> Tuple[JobSnapshot, ...]:
        """Active background jobs (delegations and long-running runtime operations)."""
        return self._read_models.active_jobs()

    def query_subagent_reports(self, query: str = "recent delegation", top_k: int = 10) -> Tuple[SubAgentReportSnapshot, ...]:
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

    def snapshot_runtime_events(self, limit: int = 100) -> Tuple[RuntimeEventSnapshot, ...]:
        """Recent normalized runtime events for operator diagnostics."""
        return self._read_models.runtime_events(limit=limit)

    # ==========================================
    # Frozen Memory Snapshot
    # ==========================================

    def _build_frozen_snapshot(self) -> str:
        """Build the memory snapshot that stays frozen for the entire session.

        Called ONCE on the first chat() call. Combines user profile facts
        with the top workspace facts into a stable text block that never
        changes mid-session — this is what makes Anthropic's prefix cache work.

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

        return "\n\n--- LONG-TERM MEMORY (frozen at session start) ---\n" + "\n\n".join(parts) + "\n"

    # ==========================================
    # Async Prefetch Pipeline
    # ==========================================

    def _fire_prefetch(self, query: str) -> None:
        """Start a background thread to pre-fetch relevant context for the next turn.

        The result is stored in _prefetch_result and consumed (popped) at the
        start of the next chat() call. If the thread hasn't finished by then,
        we gracefully skip — no blocking.
        """
        if not self.vault:
            return

        def _run():
            try:
                results = self.vault.semantic_search(query, top_k=5)
                with self._prefetch_lock:
                    self._prefetch_result = results
            except Exception:
                pass

        threading.Thread(target=_run, name="prefetch-recall", daemon=True).start()

    def _pop_prefetch(self) -> List[dict]:
        """Consume and clear the prefetched results. Returns empty list if not ready."""
        with self._prefetch_lock:
            results = self._prefetch_result or []
            self._prefetch_result = None
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

    def chat(self, user_input: str) -> str:
        """Send a message to the agent and trigger the autonomous loop.

        Prompt structure (top to bottom):
            1. Time header        — changes every turn (not cached)
            2. Frozen snapshot    — stable across all turns (prefix-cached)
            3. Recalled context   — from async prefetch (turn-specific addendum)
            4. Original prompt    — brain.og + skills + permissions (stable)
        """
        # 1. Freeze snapshot on first turn only
        self._emit_event(SESSION_CHAT_STARTED, {
            "session_id": self._session_id,
            "prompt_preview": user_input[:140],
        })

        if self._memory_snapshot is None:
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
            recall_block = "\n[Recalled Context]\nRelevant facts from long-term memory:\n" + "\n".join(lines) + "\n\n"

        # 4. Assemble system prompt: time (dynamic) + snapshot (frozen) + recall + base
        self.loop.base_system_prompt = (
            self._build_time_header()
            + self._memory_snapshot
            + recall_block
            + self._original_system_prompt
        )

        self.loop.add_user_message(user_input)
        response = self.loop.run()

        # 5. Fire async prefetch for the NEXT turn (runs while user reads response)
        self._fire_prefetch(user_input)

        # 6. Log messages to session store (non-critical, fire-and-forget)
        if self._session_store and self._session_id:
            try:
                self._session_store.log_message(self._session_id, "user", user_input)
                self._session_store.log_message(self._session_id, "assistant", response or "")
                self._emit_event(SESSION_PERSISTED, {
                    "session_id": self._session_id,
                    "messages_written": 2,
                })
            except Exception:
                pass

        self._emit_event(SESSION_CHAT_COMPLETED, {
            "session_id": self._session_id,
            "response_preview": (response or "")[:180],
        })

        return response

    def shutdown(self):
        """Gracefully close all persistent connections, MCP servers, and session store."""
        # End the persistent session
        if self._session_store and self._session_id:
            try:
                self._session_store.end_session(self._session_id)
                self._session_store.shutdown()
            except Exception:
                pass

        self.integrations.shutdown()
        self._emit_event(MCP_SHUTDOWN, {"server_count": self.mcp_manager.server_count})
        self.mcp_manager.shutdown()
        self._emit_event(SESSION_SHUTDOWN, {
            "session_id": self._session_id,
            "timestamp": now_iso(),
        })


# ==========================================
# MCP Dynamic Configuration
# ==========================================

def _configure_mcp_servers(
    skill_engine: SkillEngine,
    mcp_manager: MCPManager,
    gate: PermissionGate,
    emit: Callable[[str, Dict[str, Any]], None],
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
                emit(MCP_SERVER_FAILED, {
                    "server": node.name,
                    "reason": "filesystem MCP disabled by permissions",
                })
                continue

            # Override the hardcoded args with resolved paths from device-permissions.og
            allowed = gate.get_allowed_paths()
            if allowed:
                path_args = " ".join(str(p) for p in allowed)
                node.config["args"] = f"-y @modelcontextprotocol/server-filesystem {path_args}"

        # Start the server (filesystem with dynamic paths, or other MCP servers unchanged)
        started = mcp_manager.setup_from_node(node)
        if started:
            emit(MCP_SERVER_READY, {
                "server": node.name,
                "tool_count": mcp_manager.get_server_tool_count(node.name),
            })
        else:
            emit(MCP_SERVER_FAILED, {
                "server": node.name,
                "reason": "startup failed",
            })


# ==========================================
# Boot Sequence
# ==========================================

def boot_agent(
    on_event: Optional[Callable[..., None]] = None,
    on_permission_request: Optional[Callable[[PermissionRequest], PermissionDecision]] = None,
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
    read_models = RuntimeReadModelStore()

    def emit_runtime_event(name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        event = RuntimeEvent(name=name, payload=payload or {}, timestamp=now_iso())
        read_models.append_event(event)
        if on_event:
            on_event(event.name, event.payload)

    def relay_event(event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        envelopes = adapt_event(event_type, payload)
        for envelope in envelopes:
            event = envelope.primary
            read_models.append_event(event)
            if on_event:
                on_event(event.name, event.payload)
                for alias in envelope.legacy_aliases:
                    on_event(alias, event.payload)

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "permissions",
        "index": 1,
        "total": 11,
        "message": "Loading device permissions",
    })

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

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "config",
        "index": 2,
        "total": 11,
        "message": "Loading config and brain prompt",
    })

    # 1. Load Local Configuration and Brain
    config = load_config()
    brain_prompt = load_brain()

    provider_config = config.get("provider", {})
    model_name = provider_config.get("model") or config.get("agent", {}).get("model", "claude-haiku-4-5")
    max_tokens = config.get("agent", {}).get("max_tokens", 8192)

    capability_registry = _build_capability_registry(config)
    enabled_capabilities = [item.id for item in capability_registry.iter_enabled()]
    for capability_id in enabled_capabilities:
        install_optional_skill_pack(capability_id)

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "skills",
        "index": 3,
        "total": 11,
        "message": "Indexing .og skills",
    })

    # 2. Initialize the Skill Engine (the .og brain)
    skill_engine = SkillEngine()
    skills_dir = get_skills_dir()
    skill_engine.load_directory(skills_dir)

    skill_count = skill_engine.node_count

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "integrations",
        "index": 4,
        "total": 11,
        "message": "Initializing direct integrations",
    })

    # 3. Initialize the Integration Runtime (direct SMTP/HTTP connections)
    integrations = IntegrationRuntime()
    integration_count = 0

    for node in skill_engine.get_nodes_by_role("env"):
        integrations.load_env_node(node)

    for node in skill_engine.get_nodes_by_role("integration"):
        if integrations.setup_integration(node):
            integration_count += 1

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "mcp",
        "index": 5,
        "total": 11,
        "message": "Starting MCP servers",
    })

    # 4. Initialize MCP Manager (dynamically configured from permissions)
    mcp_manager = MCPManager()
    _configure_mcp_servers(skill_engine, mcp_manager, gate, emit_runtime_event)
    mcp_tool_count = mcp_manager.total_tool_count

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "native_integrations",
        "index": 6,
        "total": 11,
        "message": "Resolving optional capabilities",
    })

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "prompt",
        "index": 7,
        "total": 11,
        "message": "Building system prompt",
    })

    # 5. Build the base system prompt
    system_prompt = brain_prompt
    
    total_tools = integration_count + mcp_tool_count

    if skill_count > 0:
        system_prompt += (
            "\n\n--- IMPORTANT: ObjectGraph Skills Available ---\n"
            f"You have {skill_count} skill(s) loaded from .og files.\n"
            "When the user asks you to do something, ALWAYS use the `search_skills` tool first "
            "to check if there is a relevant .og skill that tells you exactly how to do it.\n"
            "If a skill is found, use `load_skill` to load the full instructions, then follow them precisely.\n"
            "If a skill has ASSERTIONS, you MUST run each assertion command and verify it passes before continuing.\n"
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

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "llm",
        "index": 8,
        "total": 11,
        "message": "Initializing LLM client",
    })

    # 6. Initialize the LLM Provider
    secret_store = create_default_secret_store()
    client = create_llm_client(provider_config, secret_store=secret_store, max_tokens=max_tokens)

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "memory",
        "index": 9,
        "total": 11,
        "message": "Initializing memory and persistence",
    })

    # 7. Initialize the Memory Manager (Moved up so Vault can bind to it)
    memory = MemoryManager(max_active_messages=30)

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "tools",
        "index": 10,
        "total": 11,
        "message": "Registering tools",
    })

    # 8. Register all tools
    tool_registry = ToolRegistry()
    register_os_tools(tool_registry, runtime_gate)  # OS tools gated by PermissionGate
    register_og_tools(tool_registry, skill_engine)
    register_workflow_tools(tool_registry)
    register_sandbox_tools(tool_registry, runtime_gate)
    
    # Register Universal REST APIs parsed from .og files
    credential_vault = CredentialVault(secret_store=secret_store)
    rest_adapter = UniversalRestAdapter(tool_registry, credential_vault)
    for node in skill_engine.get_all_nodes():
        rest_adapter.register_node_apis(node)
    
    # Initialize and register the Long-Term Memory Vault
    vault = MemoryVault()
    memory.set_vault(vault)
    register_memory_tools(tool_registry, vault)

    # Initialize Session Store (Phase 3: cross-session persistence)
    session_store = SessionStore()
    register_session_tools(tool_registry, session_store)

    # Register direct integration tools
    integrations.register_tools(tool_registry)

    # Register MCP tools
    mcp_manager.register_tools(tool_registry)

    # Register Delegation tools (MUST be LAST — needs full parent registry)
    register_delegation_tools(tool_registry, runtime_gate, relay_event, vault)

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
            emit_runtime_event(MCP_SERVER_FAILED, {
                "server": descriptor.id,
                "reason": f"capability init failed: {exc}",
            })
            continue

        if isinstance(instance, tuple):
            for sub_instance in instance:
                if hasattr(sub_instance, "register_tools"):
                    sub_instance.register_tools(tool_registry)
                    native_registration_count += 1
        elif hasattr(instance, "register_tools"):
            instance.register_tools(tool_registry)
            native_registration_count += 1

    emit_runtime_event(BOOT_PROGRESS, {
        "step": "loop",
        "index": 11,
        "total": 11,
        "message": "Finalizing execution loop",
    })

    # 9. Instantiate the Brain Loop
    loop = ExecutionLoop(
        llm_client=client,
        registry=tool_registry,
        memory=memory,
        base_system_prompt=system_prompt,
        on_event=relay_event,
        skill_engine=skill_engine
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
    )
    session.set_vault(vault)
    session.set_session_store(session_store, model=model_name)

    emit_runtime_event(BOOT_COMPLETE, {
        "skills": skill_count,
        "integrations": integration_count,
        "native_integrations": native_registration_count,
        "capabilities": capability_snapshot,
        "mcp_servers": mcp_manager.server_count,
        "mcp_tools": mcp_tool_count,
    })
    
    return session
