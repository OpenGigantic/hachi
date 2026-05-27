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
import threading
from typing import Any, Callable, List, Optional

from tokoboros.core.llm import AnthropicClient
from tokoboros.core.loop import ExecutionLoop, ToolRegistry
from tokoboros.core.memory import MemoryManager
from tokoboros.core.integrations import IntegrationRuntime
from tokoboros.core.mcp_client import MCPManager
from tokoboros.core.permissions import PermissionGate
from tokoboros.integrations.google import GoogleIntegrationHub
from tokoboros.gateway.workspace import load_brain, load_config, get_skills_dir, get_workspace_dir
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
        permission_gate: PermissionGate
    ):
        self.loop = loop
        self.skill_engine = skill_engine
        self.memory = memory
        self.integrations = integrations
        self.mcp_manager = mcp_manager
        self.permission_gate = permission_gate
        self.vault = None
        self._original_system_prompt = loop.base_system_prompt

        # Phase 2: Frozen snapshot (built once, stable for prefix cache)
        self._memory_snapshot: Optional[str] = None

        # Phase 2: Async prefetch pipeline
        self._prefetch_result: Optional[List[dict]] = None
        self._prefetch_lock = threading.Lock()

        # Phase 3: Session persistence
        self._session_store: Optional[SessionStore] = None
        self._session_id: Optional[str] = None

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
            except Exception:
                pass

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
        self.mcp_manager.shutdown()


# ==========================================
# MCP Dynamic Configuration
# ==========================================

def _configure_mcp_servers(
    skill_engine: SkillEngine,
    mcp_manager: MCPManager,
    gate: PermissionGate
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
        if node.name == "filesystem_mcp":
            # Skip entirely if user disabled MCP filesystem
            if not gate.is_mcp_filesystem_enabled():
                continue

            # Override the hardcoded args with resolved paths from device-permissions.og
            allowed = gate.get_allowed_paths()
            if allowed:
                path_args = " ".join(str(p) for p in allowed)
                node.config["args"] = f"-y @modelcontextprotocol/server-filesystem {path_args}"

        # Start the server (filesystem with dynamic paths, or other MCP servers unchanged)
        mcp_manager.setup_from_node(node)


# ==========================================
# Boot Sequence
# ==========================================

def boot_agent(on_event: Optional[Callable[..., None]] = None) -> AgentSession:
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
    # 0. Load Device Permissions (BEFORE anything else)
    permissions_file = get_workspace_dir() / "device-permissions.og"
    gate = PermissionGate(permissions_file)

    # 1. Load Local Configuration and Brain
    config = load_config()
    brain_prompt = load_brain()

    model_name = config.get("agent", {}).get("model", "claude-haiku-4-5")
    max_tokens = config.get("agent", {}).get("max_tokens", 8192)

    # 2. Initialize the Skill Engine (the .og brain)
    skill_engine = SkillEngine()
    skills_dir = get_skills_dir()
    skill_engine.load_directory(skills_dir)

    skill_count = skill_engine.node_count

    # 3. Initialize the Integration Runtime (direct SMTP/HTTP connections)
    integrations = IntegrationRuntime()
    integration_count = 0

    for node in skill_engine.get_nodes_by_role("env"):
        integrations.load_env_node(node)

    for node in skill_engine.get_nodes_by_role("integration"):
        if integrations.setup_integration(node):
            integration_count += 1

    # 4. Initialize MCP Manager (dynamically configured from permissions)
    mcp_manager = MCPManager()
    _configure_mcp_servers(skill_engine, mcp_manager, gate)
    mcp_tool_count = mcp_manager.total_tool_count

    # 4.5 Initialize Native Integrations (Google Workspace, Notion, Slack, etc.)
    google_integration = None
    notion_pages = None
    notion_db = None
    slack_chat = None
    slack_workspace = None
    
    native_nodes = skill_engine.get_nodes_by_role("native_integration")
    for node in native_nodes:
        if node.name == "google_workspace":
            from tokoboros.integrations.google import GoogleIntegrationHub
            google_integration = GoogleIntegrationHub()
        elif node.name == "notion":
            from tokoboros.integrations.notion import NotionPages, NotionDatabases
            notion_pages = NotionPages()
            notion_db = NotionDatabases()
        elif node.name == "slack":
            from tokoboros.integrations.slack import SlackChat, SlackWorkspace
            slack_chat = SlackChat()
            slack_workspace = SlackWorkspace()

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
    system_prompt += gate.get_system_prompt_injection()

    # 6. Initialize the LLM Provider
    client = AnthropicClient(model=model_name, max_tokens=max_tokens)

    # 7. Initialize the Memory Manager (Moved up so Vault can bind to it)
    memory = MemoryManager(max_active_messages=30)

    # 8. Register all tools
    tool_registry = ToolRegistry()
    register_os_tools(tool_registry, gate)  # OS tools gated by PermissionGate
    register_og_tools(tool_registry, skill_engine)
    register_workflow_tools(tool_registry)
    register_sandbox_tools(tool_registry, gate)
    
    # Register Universal REST APIs parsed from .og files
    credential_vault = CredentialVault()
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
    register_delegation_tools(tool_registry, gate, on_event, vault)

    # Register Native integration tools
    if google_integration:
        google_integration.register_tools(tool_registry)
    if notion_pages:
        notion_pages.register_tools(tool_registry)
        notion_db.register_tools(tool_registry)
    if slack_chat:
        slack_chat.register_tools(tool_registry)
        slack_workspace.register_tools(tool_registry)

    # 9. Instantiate the Brain Loop
    loop = ExecutionLoop(
        llm_client=client,
        registry=tool_registry,
        memory=memory,
        base_system_prompt=system_prompt,
        on_event=on_event,
        skill_engine=skill_engine
    )

    # 10. Create the Session
    session = AgentSession(loop, skill_engine, memory, integrations, mcp_manager, gate)
    session.set_vault(vault)
    session.set_session_store(session_store, model=model_name)
    
    return session
