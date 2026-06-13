"""shared_services.py — P4-02: process-level service singletons.

Rationale (01-architecture-review.md §R13 / TD-8):
  Each gateway session currently boots a *complete* runtime — MCP subprocess
  servers, Chroma client, full .og skill index — even though these are
  read-mostly and fully shareable.  With the 100-session cap that is up to
  100 MCP server sets per process.

Shared across all sessions (one per process):
  - SkillEngine  — indexes all .og files; read-mostly, thread-safe counters.
  - MCPManager   — manages MCP subprocess servers; sessions share the pool.
  - MemoryVault  — Chroma PersistentClient; handles concurrent access natively.
  - MemoryIndex  — FTS5 index over memory/*.og files (when memory.graph=true).

Per-session only:
  - ExecutionLoop, MemoryManager, ConversationMemory, CostTracker, session state.

Usage::

    shared = ProcessSharedServices.get_or_create(config, brain_dir)
    # re-call with same config → returns the same instance (no re-init)
    same  = ProcessSharedServices.get_or_create(config, brain_dir)
    assert shared is same
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_INSTANCE: ProcessSharedServices | None = None


@dataclass
class ProcessSharedServices:
    """Container for process-level singleton services.

    Obtain via ``ProcessSharedServices.get_or_create()`` — never construct
    directly, to avoid bypassing the singleton guard.
    """

    skill_engine: Any   # SkillEngine
    mcp_manager: Any    # MCPManager
    vault: Any          # MemoryVault | None
    graph_index: Any    # MemoryIndex | None
    memory_writer: Any  # MemoryWriter | None
    brain_dir: str
    initialized_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(
        cls,
        config: dict[str, Any],
        brain_dir: str,
        *,
        force_reinit: bool = False,
    ) -> ProcessSharedServices:
        """Return the process-level singleton, initialising it on the first call.

        Subsequent calls with the same ``brain_dir`` return the cached instance
        immediately.  Pass ``force_reinit=True`` in tests that need a clean state.

        Args:
            config:       Full agent config dict (used for memory-backend selection).
            brain_dir:    Base data directory (platformdirs).
            force_reinit: If True, destroy the existing singleton and create a new one.
        """
        global _INSTANCE
        if not force_reinit and _INSTANCE is not None:
            return _INSTANCE
        with _LOCK:
            if force_reinit or _INSTANCE is None:
                _INSTANCE = cls._init(config, brain_dir)
        return _INSTANCE

    @classmethod
    def clear(cls) -> None:
        """Destroy the singleton (for testing only)."""
        global _INSTANCE
        with _LOCK:
            _INSTANCE = None

    def uptime_seconds(self) -> float:
        """How long (seconds) since this singleton was first created."""
        return time.monotonic() - self.initialized_at

    # ------------------------------------------------------------------
    # Private initialisation
    # ------------------------------------------------------------------

    @classmethod
    def _init(cls, config: dict[str, Any], brain_dir: str) -> ProcessSharedServices:
        """One-time creation of all shared services."""
        from tokoboros.core.memory_provider import ChromaMemoryProvider, resolve_provider
        from tokoboros.core.mcp_client import MCPManager
        from tokoboros.core.paths import get_tokoboros_paths
        from tokoboros.core.settings import MemorySettings
        from tokoboros.og_engine.engine import SkillEngine

        paths = get_tokoboros_paths()

        # --- SkillEngine (shared read-mostly index) ---
        skill_engine = SkillEngine(agent_role="orchestrator")
        skills_dir = paths.workspace_dir / "skills"
        default_skills_dir = Path(__file__).parent.parent / "default_skills"
        try:
            skill_engine.index_directory(paths.workspace_dir, recursive=True)
            if default_skills_dir.exists():
                skill_engine.index_directory(default_skills_dir, recursive=True)
        except Exception:
            logger.debug("ProcessSharedServices: skill indexing failed", exc_info=True)

        # --- MCPManager (shared MCP subprocess pool) ---
        mcp_manager = MCPManager()

        # --- MemoryVault (shared Chroma client) ---
        vault: Any = None
        _raw_memory = config.get("memory", {})
        _memory_settings = MemorySettings.model_validate(_raw_memory)
        _provider = resolve_provider(_memory_settings)
        _provider.initialize(brain_dir=brain_dir)
        if isinstance(_provider, ChromaMemoryProvider):
            vault = _provider.vault

        # --- MemoryIndex / MemoryWriter (when memory.graph=true) ---
        graph_index: Any = None
        memory_writer: Any = None
        if _memory_settings.graph or _memory_settings.backend == "fts5_only":
            try:
                from tokoboros.core.memory_index import MemoryIndex
                from tokoboros.core.memory_writer import MemoryWriter

                graph_index = MemoryIndex(
                    memory_dir=paths.memory_dir,
                    db_path=paths.memory_index_db,
                )
                graph_index.refresh()
                memory_writer = MemoryWriter(
                    memory_dir=paths.memory_dir,
                    graph_index=graph_index,
                )
                logger.info(
                    "ProcessSharedServices: MemoryIndex %d nodes; MemoryWriter ready",
                    graph_index.node_count,
                )
            except Exception:
                logger.debug(
                    "ProcessSharedServices: graph index init failed", exc_info=True
                )

        logger.info(
            "ProcessSharedServices: initialised (skills=%d, mcp=%s, vault=%s, graph=%s)",
            len(skill_engine.get_all_nodes()),
            "ok",
            "ok" if vault else "none",
            "ok" if graph_index else "none",
        )

        return cls(
            skill_engine=skill_engine,
            mcp_manager=mcp_manager,
            vault=vault,
            graph_index=graph_index,
            memory_writer=memory_writer,
            brain_dir=brain_dir,
        )
