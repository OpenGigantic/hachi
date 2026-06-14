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

    # A caller requesting a *different* memory backend gets its own instance,
    # keyed by a (brain_dir, memory_backend, memory_graph) fingerprint.
    other = ProcessSharedServices.get_or_create(alt_config, brain_dir)
    assert other is not shared
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# ADV-024: keyed by config fingerprint so distinct configs get distinct instances.
_INSTANCES: dict[str, ProcessSharedServices] = {}


def _config_fingerprint(config: dict[str, Any], brain_dir: str) -> str:
    """Return a stable cache key for a (config, brain_dir) pair.

    Only the fields that affect *shared* service construction are included:
    ``memory.backend``, ``memory.graph``, and ``brain_dir``.  All other
    per-session config keys are irrelevant to the singleton.
    """
    memory = config.get("memory", {}) if isinstance(config.get("memory"), dict) else {}
    backend = str(memory.get("backend", "chroma"))
    graph = str(bool(memory.get("graph", False)))
    raw = f"{brain_dir}|{backend}|{graph}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class ProcessSharedServices:
    """Container for process-level singleton services.

    Obtain via ``ProcessSharedServices.get_or_create()`` — never construct
    directly, to avoid bypassing the singleton guard.

    Instances are keyed by a fingerprint derived from ``brain_dir`` and the
    memory-backend fields of ``config``.  A caller that requests a different
    memory backend always gets a distinct instance — it is never silently
    handed the wrong backend (ADV-024).
    """

    skill_engine: Any  # SkillEngine
    mcp_manager: Any  # MCPManager
    vault: Any  # MemoryVault | None
    graph_index: Any  # MemoryIndex | None
    memory_writer: Any  # MemoryWriter | None
    brain_dir: str
    _fingerprint: str = field(default="", repr=False)
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
        """Return the singleton for this (config, brain_dir) combination.

        FIX ADV-024 — config fingerprint check:
          Instances are keyed by a fingerprint of ``(brain_dir, memory.backend,
          memory.graph)``.  A caller requesting a different memory backend gets a
          distinct instance; it never silently inherits the first session's client.

        FIX UC-INT-004 — MCP health check:
          Before returning a cached instance the MCP manager is health-checked.
          If any server in the pool is dead the entire instance is torn down and
          re-initialised so sessions never inherit a broken pool.

        Args:
            config:       Full agent config dict (used for memory-backend selection).
            brain_dir:    Base data directory (platformdirs).
            force_reinit: If True, destroy the cached instance for this fingerprint
                          and create a fresh one.  Pass this in tests that need a
                          clean slate.
        """
        global _INSTANCES
        fp = _config_fingerprint(config, brain_dir)

        # Fast-path: check without the lock first (double-checked locking).
        if not force_reinit:
            existing = _INSTANCES.get(fp)
            if existing is not None:
                # UC-INT-004: health-check before handing back a cached instance.
                if cls._mcp_pool_healthy(existing.mcp_manager):
                    return existing
                else:
                    logger.warning(
                        "ProcessSharedServices[%s]: MCP pool is unhealthy — "
                        "tearing down and re-initialising (UC-INT-004).",
                        fp,
                    )
                    force_reinit = True

        with _LOCK:
            # Re-check under the lock to avoid double-init races.
            if not force_reinit:
                existing = _INSTANCES.get(fp)
                if existing is not None:
                    if cls._mcp_pool_healthy(existing.mcp_manager):
                        return existing
                    logger.warning(
                        "ProcessSharedServices[%s]: MCP pool unhealthy under lock — "
                        "re-initialising.",
                        fp,
                    )

            new_instance = cls._init(config, brain_dir, fingerprint=fp)
            _INSTANCES[fp] = new_instance

        return new_instance

    @classmethod
    def _mcp_pool_healthy(cls, mcp_manager: Any) -> bool:
        """Return True if the MCP manager has no dead server connections.

        A dead pool is detected when at least one previously-started server is
        no longer alive.  An empty pool (no servers configured) is considered
        healthy because there is nothing to revive.
        """
        try:
            servers: dict[str, Any] = mcp_manager._servers  # type: ignore[attr-defined]
        except AttributeError:
            return True  # unknown manager type — assume healthy

        for server in servers.values():
            try:
                if not server.is_alive:
                    return False
            except Exception:
                return False  # unexpected error → treat as dead
        return True

    @classmethod
    def clear(cls) -> None:
        """Destroy all cached singletons (for testing only)."""
        global _INSTANCES
        with _LOCK:
            _INSTANCES.clear()

    @classmethod
    def clear_fingerprint(cls, config: dict[str, Any], brain_dir: str) -> None:
        """Destroy the singleton for a specific (config, brain_dir) pair (for testing only)."""
        global _INSTANCES
        fp = _config_fingerprint(config, brain_dir)
        with _LOCK:
            _INSTANCES.pop(fp, None)

    def uptime_seconds(self) -> float:
        """How long (seconds) since this singleton was first created."""
        return time.monotonic() - self.initialized_at

    # ------------------------------------------------------------------
    # Private initialisation
    # ------------------------------------------------------------------

    @classmethod
    def _init(
        cls, config: dict[str, Any], brain_dir: str, *, fingerprint: str = ""
    ) -> ProcessSharedServices:
        """One-time creation of all shared services."""
        from tokoboros.core.mcp_client import MCPManager
        from tokoboros.core.memory_provider import ChromaMemoryProvider, resolve_provider
        from tokoboros.core.paths import get_tokoboros_paths
        from tokoboros.core.settings import MemorySettings
        from tokoboros.og_engine.engine import SkillEngine

        paths = get_tokoboros_paths()

        # --- SkillEngine (shared read-mostly index) ---
        skill_engine = SkillEngine(agent_role="orchestrator")
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
                logger.debug("ProcessSharedServices: graph index init failed", exc_info=True)

        logger.info(
            "ProcessSharedServices[%s]: initialised (skills=%d, mcp=%s, vault=%s, graph=%s)",
            fingerprint or "default",
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
            _fingerprint=fingerprint,
        )
