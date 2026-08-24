"""UI read models for a live session (T-46).

`AgentSession` (`hachi.gateway.orchestrator`) used to carry 20 `snapshot_*` /
`query_*` methods that exist only to feed the TUI and web UI -- pure reads with
nothing to do with running a turn. This module lifts them behind a
`SessionReadModels` facade so a UI-projection change touches a file the turn
engine does not import.

Wraps BOTH the event-sourced `RuntimeReadModelStore`
(`hachi.core.runtime_read_models`) and the live subsystems (skill engine, MCP
manager, memory manager, scheduler, session store, vault). The store is fed
exclusively by `append_event` and carries the monotonic sequence counter SSE
paging depends on -- it cannot answer questions about live subsystem state
that never passed through an event, so this facade is the join point, not an
extension of the store. See the T-46 spec section 3.3.

Of the 20 read models:
    - 6 are store-derived (thin, 2-4 line delegations to `RuntimeReadModelStore`):
      `permission_audit`, `active_jobs`, `failed_jobs`, `tool_calls`,
      `runtime_events`, `events_since`.
    - 2 are hybrids that touch both the store and a live subsystem:
      `session_state`, `subagent_reports`.
    - 12 are purely live, computed from a subsystem the store has never seen:
      `usage`, `task_ledger`, `skills`, `gardener_diffs`, `og_explorer`,
      `schedules`, `mcp_inventory`, `memory_facts`, `og_graph`,
      `recent_sessions`, `session_messages`, `memory_recall`.

Every method here is the SAME body as the `AgentSession.snapshot_*`/`query_*`
method it replaces, moved verbatim -- only `self.<attr>` references became
`self._<attr>` (or, for the three attributes below, a getter call). Docstrings,
comments, log message text, and `getattr`/`except Exception` guards are
unchanged; CF-1 tracks those guards separately, and "fixing" them during a
relocation is unreviewable (T-46 spec section 9).

Why `vault`, `session_store`, and `session_id` are CALLABLES, not plain
constructor-captured values
------------------------------------------------------------------------
`AgentSession.vault` starts as `None` and is only assigned later, via
`set_vault()`; `AgentSession._session_store` and `._session_id` likewise start
as `None` and are only assigned later, via `set_session_store()`. Both setters
run AFTER `AgentSession.__init__` returns (real sessions boot with no vault or
session store at all, wire the skill engine/MCP manager/etc. into this facade
at construction time, and only attach a vault/session store afterwards). A
facade that captured `session.vault`/`session._session_store`/
`session._session_id` as plain values at construction time would freeze them
at `None` forever -- `memory_facts`, `memory_recall`, `recent_sessions`,
`session_messages`, and the vault half of `subagent_reports` would silently
and permanently return empty results even after a real vault/session store
attached, the exact "dead feature reports success" failure mode this repo is
trying to eliminate (see the store's own "fresh-store trap" in the T-46 spec
section 12, for the same shape of bug). Every OTHER subsystem reference
(`skill_engine`, `mcp_manager`, `memory`, `loop`, `scheduler`, `model_name`,
and the `RuntimeReadModelStore` itself) is assigned exactly once, in
`AgentSession.__init__`, and never reassigned -- confirmed by grepping every
`self.<attr> =` site in `orchestrator.py` -- so those are safe to capture
directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from hachi.core.loop import ExecutionLoop
from hachi.core.mcp_client import MCPManager
from hachi.core.memory import MemoryManager
from hachi.core.memory.vault import MemoryVault
from hachi.core.paths import get_hachi_paths
from hachi.core.runtime_read_models import (
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
from hachi.core.session_store import SessionStore
from hachi.og_engine.engine import SkillEngine

logger = logging.getLogger(__name__)


class SessionReadModels:
    """UI read models for one live `AgentSession`.

    Constructed once, inside `AgentSession.__init__`, and held as
    `self._read_model_facade`. See the module docstring for why `vault`,
    `session_store`, and `session_id` are passed as getters rather than
    values.
    """

    def __init__(
        self,
        *,
        store: RuntimeReadModelStore,
        skill_engine: SkillEngine,
        mcp_manager: MCPManager,
        memory: MemoryManager,
        loop: ExecutionLoop,
        model_name: str,
        vault_getter: Callable[[], MemoryVault | None],
        session_store_getter: Callable[[], SessionStore | None],
        session_id_getter: Callable[[], str | None],
        scheduler: Any = None,
    ) -> None:
        self._store = store
        self._skill_engine = skill_engine
        self._mcp_manager = mcp_manager
        self._memory = memory
        self._loop = loop
        self._model_name = model_name
        self._vault_getter = vault_getter
        self._session_store_getter = session_store_getter
        self._session_id_getter = session_id_getter
        self._scheduler = scheduler

    # ------------------------------------------------------------------
    # Hybrid (store + live)
    # ------------------------------------------------------------------

    def session_state(self) -> SessionStateSnapshot:
        """Immutable summary of current session state for UI inspection."""
        tracker = getattr(self._loop, "cost_tracker", None)
        cost = tracker.snapshot() if tracker is not None else None
        return SessionStateSnapshot(
            session_id=self._session_id_getter() or "",
            active_skill=self._memory.active_skill,
            last_user_query=getattr(self._memory, "_last_user_query", ""),
            model_name=self._model_name,
            boot_ready=self._store.boot_ready(),
            pending_job_count=len(self._store.active_jobs()),
            total_tokens=cost.total_tokens if cost else 0,
            estimated_cost_usd=round(cost.estimated_cost_usd, 6) if cost else 0.0,
        )

    def subagent_reports(
        self, query: str = "recent delegation", top_k: int = 10
    ) -> tuple[SubAgentReportSnapshot, ...]:
        """Sub-agent execution reports from memory vault plus recent runtime events."""
        runtime_reports = list(self._store.subagent_reports(limit=top_k))
        vault = self._vault_getter()
        if not vault:
            return tuple(runtime_reports)

        try:
            vault_rows = vault.search_delegations(query=query, top_k=top_k)
        except RuntimeError:
            # Vault closed (e.g. a process-teardown race) — degrade to runtime-only
            # reports instead of surfacing a 500. The shared-vault lifecycle fix makes
            # this rare, but the endpoint must never crash on a closed vault.
            logger.warning("query_subagent_reports: vault closed; returning runtime reports only")
            return tuple(runtime_reports)
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

    # ------------------------------------------------------------------
    # Store-derived
    # ------------------------------------------------------------------

    def permission_audit(self, limit: int = 50) -> tuple[PermissionAuditSnapshot, ...]:
        """Recent permission request/decision audit entries."""
        return self._store.permission_audit(limit=limit)

    def active_jobs(self) -> tuple[JobSnapshot, ...]:
        """Active background jobs (delegations and long-running runtime operations)."""
        return self._store.active_jobs()

    def failed_jobs(self, limit: int = 20) -> tuple[JobFailureSnapshot, ...]:
        """Recently failed jobs for operator diagnostics."""
        return self._store.failed_jobs(limit=limit)

    def tool_calls(self, limit: int = 50) -> tuple[ToolCallSnapshot, ...]:
        """Recent tool-call timeline (start→completion pairs with durations)."""
        return self._store.tool_calls(limit=limit)

    def runtime_events(self, limit: int = 100) -> tuple[RuntimeEventSnapshot, ...]:
        """Recent normalized runtime events for operator diagnostics."""
        return self._store.runtime_events(limit=limit)

    def events_since(self, after: int, limit: int = 100) -> tuple[RuntimeEventSnapshot, ...]:
        """Sequenced events past ``after`` — the single source for gateway polling."""
        return self._store.read_events_since(after, limit=limit)

    # ------------------------------------------------------------------
    # Live
    # ------------------------------------------------------------------

    def usage(self) -> dict[str, Any]:
        """Detailed token/cost breakdown for the Ops dashboard.

        Exposes the full CostSnapshot (input/output/cache-read/cache-creation/total
        tokens, call_count, estimated_cost_usd). Empty dict before the first LLM call.
        """
        tracker = getattr(self._loop, "cost_tracker", None)
        return tracker.snapshot().to_dict() if tracker is not None else {}

    def task_ledger(self, limit: int = 50) -> tuple[TaskLedgerItemSnapshot, ...]:
        """Immutable task ledger records for timeline views."""
        records = self._memory.task_ledger[-limit:]
        return tuple(
            TaskLedgerItemSnapshot(
                skill_name=record.skill_name,
                user_query=record.user_query,
                summary=record.summary,
            )
            for record in records
        )

    def skills(self, limit: int = 100) -> tuple[SkillSnapshot, ...]:
        """Immutable inventory of loaded .og skills with usage stats.

        Surfaces the ObjectGraph moat in the TUI: which skills are loaded, how
        often each has been used, and when. Sorted by use_count desc (most-used
        first) by the engine.
        """
        rows = self._skill_engine.get_skill_inventory()[:limit]
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

    def gardener_diffs(self) -> list[dict]:
        """Return pending memory-diff proposals from the last gardener run (T-08 A2).

        Reads the pending_diffs.json file written by MemoryGardener.run(). Returns
        an empty list if the file is missing or unreadable.

        BB-5 / T-08: previously read ``getattr(self.memory, "memory_dir", None)`` —
        ``MemoryManager`` has no such attribute, so the getattr default always fired
        and this returned ``[]`` before ever touching the file. The real memory
        directory is ``get_hachi_paths().memory_dir``, the same path
        MemoryIndex/MemoryWriter already use (shared_services.py) and the same path
        :func:`~hachi.gateway.orchestrator._start_memory_gardener` (A1) writes to.

        T-08: this method's original (dead, unreachable) code also referenced
        ``GardeningResult._PENDING_DIFFS_FILE`` — that constant is declared on
        ``MemoryGardener``, not ``GardeningResult``, so it would have raised
        ``AttributeError`` the moment the getattr-masking bug above was fixed
        without also fixing this. Caught by
        tests/test_memory_gardener_wiring.py — a second, previously-unreachable
        defect the T-08 spec's own §3.3 audit did not catch either, because
        nothing ever got far enough to execute this line before A1/A2 existed.
        """
        from dataclasses import asdict

        from hachi.core.memory.gardener import GardeningResult, MemoryGardener

        diffs_path = get_hachi_paths().memory_dir / MemoryGardener._PENDING_DIFFS_FILE
        result = GardeningResult.load(diffs_path)

        return [asdict(p) for p in result.pending_review]

    def og_explorer(self) -> dict:
        """Return data for the OG Explorer TUI screen.

        Includes the full skill inventory with usage stats plus pass-depth
        counters so the explorer can show token-efficiency metrics.
        """
        inventory = self._skill_engine.get_skill_inventory()
        counters = self._skill_engine.get_usage_counters()
        node_count = len(self._skill_engine.get_all_nodes())
        return {
            "skills": inventory,
            "counters": counters,
            "node_count": node_count,
        }

    def schedules(self) -> list[dict[str, Any]]:
        """Current schedules serialized for the companion web UI."""
        if self._scheduler is None:
            return []
        return [s.to_dict() for s in self._scheduler.list_schedules()]

    def mcp_inventory(self) -> MCPInventorySnapshot:
        """Current MCP server and tool inventory."""
        inventory_rows = self._mcp_manager.get_inventory()
        servers = tuple(
            MCPServerSnapshot(
                name=row.get("name", "unknown"),
                status=row.get("status", "unknown"),
                tool_count=int(row.get("tool_count", 0)),
                tools=tuple(row.get("tools", [])),
                transport=row.get("transport", "stdio"),
                err=row.get("err"),
            )
            for row in inventory_rows
        )
        return MCPInventorySnapshot(
            server_count=len(servers),
            total_tool_count=sum(server.tool_count for server in servers),
            servers=servers,
        )

    def memory_facts(self) -> dict:
        """Return grouped memory facts, blocked items, and proposed changes for the web UI.

        Enumerates the three vault collections (user_profile, agent_profile, core_memory)
        and the quarantine collection, building a structured payload for the Memory screen.

        Returns:
            Dict with keys ``groups`` (list of fact groups), ``blocked`` (list of quarantined
            items), and ``proposed`` (always None — no moderation pipeline yet).
        """
        vault = self._vault_getter()
        if vault is None:
            return {"groups": [], "blocked": [], "proposed": None}

        groups: list[dict] = []

        collection_defs = [
            (vault.user_collection, "user_profile", "About you"),
            (vault.agent_profile, "agent_profile", "About Hachi"),
            (vault.collection, "core_memory", "Workspace"),
        ]

        for coll, group_id, title in collection_defs:
            facts: list[dict] = []
            try:
                rows = coll.get()
                ids = rows.get("ids", [])
                docs = rows.get("documents", [])
                metas = rows.get("metadatas", []) or []
                for i, fact_id in enumerate(ids):
                    doc = docs[i] if i < len(docs) else ""
                    meta = (metas[i] if i < len(metas) else None) or {}
                    facts.append(
                        {
                            "id": fact_id,
                            "text": doc,
                            "source": meta.get("category", ""),
                        }
                    )
            except Exception:
                # HIGH-012: one bad collection must not kill the whole response,
                # but log so ChromaDB/persistence failures are visible.
                logger.warning(
                    "snapshot_memory_facts: failed to enumerate collection %r",
                    group_id,
                    exc_info=True,
                )
            groups.append({"id": group_id, "title": title, "facts": facts})

        blocked: list[dict] = []
        try:
            quarantine_rows = vault.get_quarantine_entries()
            for row in quarantine_rows:
                blocked.append(
                    {
                        "id": row.get("id", ""),
                        "text": row.get("content", ""),
                        # Reason sourced from threat metadata fields; tolerate absence.
                        "reason": row.get("threat_id") or row.get("status") or "quarantined",
                    }
                )
        except Exception:
            # HIGH-012: quarantine enumeration errors must not kill the response.
            logger.warning(
                "snapshot_memory_facts: failed to enumerate quarantine entries", exc_info=True
            )

        # proposed: no moderation pipeline yet — always null.
        return {"groups": groups, "blocked": blocked, "proposed": None}

    def og_graph(self) -> dict:
        """Return ObjectGraph node graph for the OG Explorer web screen.

        Delegates to :func:`og_graph_from_engine` using this session's skill engine.

        Returns:
            Dict with key ``nodes`` containing a list of node dicts and ``count``
            with the number of nodes.
        """
        # Deferred import: hachi.gateway.orchestrator imports this module (to build
        # the facade in AgentSession.__init__), so a module-level import here would
        # be circular. hachi.gateway.in_process_runtime already imports
        # og_graph_from_engine the same way, for the same reason.
        from hachi.gateway.orchestrator import og_graph_from_engine

        return og_graph_from_engine(self._skill_engine)

    def recent_sessions(self, limit: int = 10) -> tuple[dict[str, Any], ...]:
        """Recent persisted sessions from SessionStore."""
        session_store = self._session_store_getter()
        if not session_store:
            return tuple()
        return tuple(session_store.get_recent_sessions(limit=limit))

    def session_messages(self, session_id: str, limit: int = 500) -> tuple[dict[str, Any], ...]:
        """Chronological transcript for a persisted session (resume/history replay)."""
        session_store = self._session_store_getter()
        if not session_store:
            return tuple()
        return tuple(session_store.get_session_messages(session_id, limit=limit))

    def memory_recall(self, query: str, top_k: int = 8) -> tuple[MemoryRecallSnapshot, ...]:
        """Immutable memory recall search snapshots."""
        vault = self._vault_getter()
        if not vault:
            return tuple()
        rows = vault.semantic_search(query, top_k=top_k)
        return tuple(
            MemoryRecallSnapshot(
                fact_id=row.get("id", ""),
                content=row.get("raw_content") or row.get("content", ""),
                category=row.get("category", "workspace_state"),
                timestamp=row.get("timestamp", ""),
            )
            for row in rows
        )
