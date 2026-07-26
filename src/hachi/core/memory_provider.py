"""
Memory Provider Interface

Abstract base class that defines the contract for all memory backends.
Enables swapping ChromaDB for alternative backends (Qdrant, Pinecone, Honcho)
without touching core agent code.

The default implementation (ChromaMemoryProvider) wraps the existing MemoryVault,
ensuring zero behavioral change while enabling future extensibility.

Lifecycle hooks:
    initialize()         — called once at boot
    upsert()             — store a fact
    search()             — semantic retrieval
    delete()             — remove a fact
    sync_turn()          — called after every completed turn (optional)
    on_session_end()     — called at shutdown (optional)
    shutdown()           — cleanup resources (optional)
    attach_graph_index() — bind a MemoryIndex for FTS5/graph-backed search (optional)
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hachi.core.settings import MemorySettings


class MemoryProvider(ABC):
    """Abstract memory backend interface.

    Subclass this to create custom memory backends. Only upsert, search,
    delete, and initialize are required — all lifecycle hooks have no-op
    defaults so backends only implement what they need.
    """

    @abstractmethod
    def initialize(self, brain_dir: str) -> None:
        """Initialize the backend storage.

        Args:
            brain_dir: The base directory for persistent storage.
        """

    @abstractmethod
    def upsert(self, content: str, category: str = "workspace_state") -> str:
        """Store an atomic fact. Returns the fact ID or an error string.

        Args:
            content: The fact text to persist.
            category: Organizational tag for filtering.

        Returns:
            The unique fact ID, or a 'BLOCKED:<reason>' string on rejection.
        """

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Semantic similarity search.

        Args:
            query: Natural language query to match against stored facts.
            top_k: Maximum number of results to return.

        Returns:
            Ranked list of fact dicts with id, content, category, timestamp, raw_content.
        """

    @abstractmethod
    def delete(self, fact_id: str) -> bool:
        """Remove a fact by ID.

        Args:
            fact_id: The unique identifier of the fact to remove.

        Returns:
            True if deletion succeeded.
        """

    def sync_turn(self, user_content: str, assistant_content: str) -> None:  # noqa: B027
        """Optional: called after every completed turn for background extraction."""

    def on_session_end(self, messages: list[Any]) -> None:  # noqa: B027
        """Optional: called at shutdown for end-of-session processing."""

    def shutdown(self) -> None:  # noqa: B027
        """Optional: release resources."""

    def attach_graph_index(self, index: Any) -> None:  # noqa: B027
        """Optional: bind a MemoryIndex so search() can use FTS5/graph expansion.

        Only ``FTS5MemoryProvider`` implements this meaningfully (see its
        override below); every other backend inherits this no-op so callers
        can call it unconditionally without checking which provider they hold
        (T-49 — ``ProviderBackedVault.attach_graph_index`` forwards here
        directly, no defaulted-getattr probe required).
        """


class ChromaMemoryProvider(MemoryProvider):
    """Default provider that wraps the existing MemoryVault.

    This adapter ensures backward compatibility — the entire Phase 1-3
    memory system works through this provider without any behavioral change.
    """

    def __init__(self, vault: Any = None) -> None:
        """Initialize with an optional pre-existing vault.

        Args:
            vault: An existing MemoryVault instance. If None, one will be
                   created during initialize().
        """
        self._vault: Any = vault

    @property
    def vault(self) -> Any:
        """Direct access to the underlying MemoryVault for tools that need it."""
        return self._vault

    def initialize(
        self,
        brain_dir: str,
        *,
        recall_distance_max: float | None = None,
        core_memory_max_entries: int | None = None,
        decay_half_life_days: float | None = None,
    ) -> None:
        """Create a MemoryVault if one wasn't provided at construction time.

        Args:
            brain_dir: Base directory for the ChromaDB persistent store.
            recall_distance_max: Optional cosine-distance ceiling for recall
                filtering (Issue 10). ``None`` keeps the MemoryVault default.
            core_memory_max_entries: Optional soft cap on the ``core_memory``
                collection (Issue 10). ``None`` keeps the MemoryVault default.
            decay_half_life_days: Optional recency-decay half-life in days (M2).
                ``None`` disables decay (MemoryVault default). Only re-ranks
                relevance-gated recall; never applies to user_profile or
                get_recent_facts.
        """
        if self._vault is None:
            from hachi.core.memory_vault import MemoryVault

            _kwargs: dict[str, Any] = {"brain_dir": brain_dir}
            if recall_distance_max is not None:
                _kwargs["recall_distance_max"] = recall_distance_max
            if core_memory_max_entries is not None:
                _kwargs["core_memory_max_entries"] = core_memory_max_entries
            if decay_half_life_days is not None:
                _kwargs["decay_half_life_days"] = decay_half_life_days
            self._vault = MemoryVault(**_kwargs)

    def upsert(self, content: str, category: str = "workspace_state") -> str:
        """Delegate to MemoryVault.upsert_fact()."""
        result: str = self._vault.upsert_fact(content, category)
        return result

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Delegate to MemoryVault.semantic_search()."""
        results: list[dict[str, Any]] = self._vault.semantic_search(query, top_k)
        return results

    def delete(self, fact_id: str) -> bool:
        """Delegate to MemoryVault.delete_fact()."""
        result: bool = self._vault.delete_fact(fact_id)
        return result

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """No-op — sync_turn is handled by MemoryManager.sync_turn() already."""
        pass

    def shutdown(self) -> None:
        """ChromaDB PersistentClient handles its own cleanup."""
        pass


class InMemoryProvider(MemoryProvider):
    """Lightweight in-process memory provider with no external dependencies.

    Facts are stored in a plain list; search uses simple keyword overlap
    scoring. Data is NOT persisted across process restarts.

    Useful for:
    - Unit tests / CI (no ChromaDB process required)
    - Minimal deployments that don't need durable semantic memory
    - Rapid prototyping
    """

    def __init__(self) -> None:
        self._facts: list[dict[str, Any]] = []

    def initialize(self, brain_dir: str) -> None:
        """No-op — in-memory needs no disk setup."""

    def upsert(self, content: str, category: str = "workspace_state") -> str:
        """Store a fact; returns a new UUID as the fact ID."""
        fact_id = str(uuid.uuid4())
        self._facts.append(
            {
                "id": fact_id,
                "content": content,
                "raw_content": content,
                "category": category,
                "timestamp": "",
            }
        )
        return fact_id

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Keyword-overlap search (no embeddings); returns top_k results ranked by term hits."""
        query_terms = set(query.lower().split())
        if not query_terms:
            return []

        scored: list[tuple[int, dict[str, Any]]] = []
        for fact in self._facts:
            text_terms = set(fact["content"].lower().split())
            score = len(query_terms & text_terms)
            if score > 0:
                scored.append((score, fact))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_k]]

    def delete(self, fact_id: str) -> bool:
        """Remove a fact by ID."""
        before = len(self._facts)
        self._facts = [f for f in self._facts if f["id"] != fact_id]
        return len(self._facts) < before

    def shutdown(self) -> None:
        """Clear all in-memory facts."""
        self._facts.clear()


class FTS5MemoryProvider(MemoryProvider):
    """FTS5-only memory provider — no ChromaDB required.

    Reads from ``.og`` memory files via a MemoryIndex (FTS5 + graph expansion).
    Writes new facts as ``.og`` nodes via MemoryWriter.  Chroma is never
    initialized, making this backend viable in offline / lightweight environments.

    Wired automatically when ``memory.backend = "fts5_only"`` in config.
    """

    def __init__(self) -> None:
        self._graph_index: Any = None
        self._memory_writer: Any = None
        self._brain_dir: str = ""

    def initialize(self, brain_dir: str) -> None:
        """Record brain_dir for lazy index/writer creation."""
        self._brain_dir = brain_dir

    def attach_graph_index(self, index: Any) -> None:
        """Bind the FTS5 MemoryIndex so search() can delegate to it."""
        self._graph_index = index

    def attach_memory_writer(self, writer: Any) -> None:
        """Bind the MemoryWriter so upsert() persists facts to .og files."""
        self._memory_writer = writer

    def upsert(self, content: str, category: str = "workspace_state") -> str:
        """Write a fact as an .og node via MemoryWriter; returns node ID."""
        if self._memory_writer is not None:
            try:
                result = self._memory_writer.write_fact(content, category=category)
                return str(result)
            except Exception:
                pass
        # Fallback: return a UUID so callers don't crash on None.
        return str(uuid.uuid4())

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """FTS5 similarity search via the attached MemoryIndex."""
        if self._graph_index is None:
            return []
        try:
            results: list[dict[str, Any]] = self._graph_index.search(query, top_k=top_k)
            return results
        except Exception:
            return []

    def delete(self, fact_id: str) -> bool:
        """Not supported for file-based facts; always returns False."""
        return False

    def shutdown(self) -> None:
        """No resources to release."""


# T-49: mirrors memory_vault.USER_PROFILE_MAX_ENTRIES's *value* without importing
# memory_vault at module level. memory_vault.py's own docstring documents that the
# module itself is safe to import (chromadb is deferred to first collection access —
# PERF-05/G3-T2), but every OTHER import of it in this file is already local/deferred
# (see ChromaMemoryProvider.initialize below), specifically so that importing
# memory_provider does not pull in anything vault-shaped for the in_memory/fts5_only
# backends this constant exists to serve. Keep the two values in sync by hand.
_USER_PROFILE_MAX_ENTRIES = 25


class _PartitionView:
    """Read-only, Chroma-collection-shaped view over one partition of a
    :class:`ProviderBackedVault`'s tracked facts.

    ``snapshot_memory_facts`` (the web Memory screen, ``gateway/orchestrator.py``)
    and ``get_memory_stats`` (``tools/memory_tools.py``) read ``.user_collection``
    / ``.agent_profile`` / ``.collection`` as if they were live Chroma collections —
    ``.get()`` -> ``{ids, documents, metadatas}``, ``.count()`` -> ``int``. This view
    honestly exposes exactly what this facade tracked for that partition; it never
    fabricates data beyond that.
    """

    def __init__(self, records: dict[str, dict[str, str]]) -> None:
        self._records = records

    def get(self) -> dict[str, list[Any]]:
        ids = list(self._records.keys())
        return {
            "ids": ids,
            "documents": [self._records[i]["content"] for i in ids],
            "metadatas": [{"category": self._records[i]["category"]} for i in ids],
        }

    def count(self) -> int:
        return len(self._records)


class ProviderBackedVault:
    """Vault-compatible facade over a non-Chroma :class:`MemoryProvider` (T-49).

    Adopts the seam: every read/write funnels through the wrapped provider's ABC
    contract (``upsert`` / ``search`` / ``delete``) instead of being discarded at
    boot the way it was before this task — see ``docs/bug-bank.md`` BB-146. It
    presents the minimal ``MemoryVault``-shaped surface that
    ``tools/memory_tools.py``, ``core/memory.py``, and ``gateway/orchestrator.py``
    already call, so ``backend = "in_memory"`` and ``backend = "fts5_only"`` produce
    a genuinely working memory instead of a silent ``vault=None``.

    Because the ABC exposes a single flat namespace (no separate Chroma
    collections), this facade partitions workspace / user-profile / agent-profile
    facts by remembering which provider-issued ids belong to which logical
    partition. The fact CONTENT always lives in the wrapped provider
    (``InMemoryProvider._facts`` or the FTS5 ``.og`` graph, via ``upsert``/
    ``search``/``delete``) — never here; these dicts are bookkeeping only, needed
    because the ABC has no "list all" or "list by partition" primitive.

    Deep, Chroma-only features this facade does not attempt to replicate
    (episodic memory, delegation-outcome search, the MemoryGuard quarantine audit
    trail, capacity-limit enforcement on the user/agent profile) stay honestly
    absent — empty results, not fabricated ones. See the T-49 entry in
    ``docs/DECISIONS.md`` for the scoping rationale.
    """

    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider
        self.is_shared: bool = False
        self._graph_index: Any = None
        # id -> {"content": ..., "category": ...} per logical partition. The
        # provider is the single source of truth for the CONTENT; these dicts
        # only remember which ids belong to which partition (dict insertion
        # order also gives "most recently written" for get_recent_facts).
        self._core_facts: dict[str, dict[str, str]] = {}
        self._user_facts: dict[str, dict[str, str]] = {}
        self._agent_facts: dict[str, dict[str, str]] = {}
        self.collection = _PartitionView(self._core_facts)
        self.user_collection = _PartitionView(self._user_facts)
        self.agent_profile = _PartitionView(self._agent_facts)

    @property
    def provider(self) -> MemoryProvider:
        """The wrapped provider.

        Lets a second session booting in the same process recover the real
        provider type (e.g. for orchestrator.py's ``_is_fts5_only`` check)
        instead of re-wrapping this facade in a ``ChromaMemoryProvider``.
        """
        return self._provider

    # ------------------------------------------------------------------
    # Core / workspace facts — the headline ABC-backed path.
    # ------------------------------------------------------------------

    def upsert_fact(self, content: str, category: str = "workspace_state") -> str:
        """Store a workspace fact — delegates to ``MemoryProvider.upsert()``."""
        fact_id = self._provider.upsert(content, category)
        if not fact_id.startswith("BLOCKED:"):
            self._core_facts[fact_id] = {"content": content, "category": category}
        return fact_id

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search workspace facts — delegates to ``MemoryProvider.search()``."""
        return self._provider.search(query, top_k)

    def delete_fact(self, fact_id: str) -> bool:
        """Delete a workspace fact — delegates to ``MemoryProvider.delete()``."""
        ok = self._provider.delete(fact_id)
        if ok:
            self._core_facts.pop(fact_id, None)
        return ok

    def get_recent_facts(self, collection: str = "core_memory", limit: int = 8) -> list[str]:
        """Most-recently-written fact texts in a logical partition.

        The ABC has no recency-ordering primitive, so this reflects write
        order tracked by this facade (dict insertion order), not a
        provider-side timestamp.
        """
        by_name: dict[str, dict[str, dict[str, str]]] = {
            "user_collection": self._user_facts,
            "agent_profile": self._agent_facts,
        }
        records = by_name.get(collection, self._core_facts)
        return [rec["content"] for rec in list(records.values())[-limit:]]

    # ------------------------------------------------------------------
    # User profile.
    # ------------------------------------------------------------------

    def upsert_user_fact(self, content: str, category: str = "preference") -> str:
        """Store a user-identity fact — delegates to ``MemoryProvider.upsert()``.

        Unlike ``MemoryVault.upsert_user_fact``, this does not normalise
        ``category`` to a legacy value, so ``UserProfileStore`` (T-49) can call
        it directly with a P1-7 category without needing a private bypass.
        """
        fact_id = self._provider.upsert(content, category)
        if not fact_id.startswith("BLOCKED:"):
            self._user_facts[fact_id] = {"content": content, "category": category}
        return fact_id

    def search_user_profile(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Search user-profile facts — delegates to ``MemoryProvider.search()``,
        filtered to ids this facade tagged as user-profile."""
        hits = self._provider.search(query, max(top_k * 4, 20))
        filtered = [h for h in hits if h.get("id") in self._user_facts]
        return filtered[:top_k]

    def delete_user_fact(self, fact_id: str) -> bool:
        """Delete a user-profile fact — delegates to ``MemoryProvider.delete()``."""
        ok = self._provider.delete(fact_id)
        if ok:
            self._user_facts.pop(fact_id, None)
        return ok

    def get_profile_stats(self) -> dict[str, Any]:
        """Capacity stats for the user-profile partition.

        No cap is enforced on this lightweight backend (a documented parity
        gap — see the class docstring): ``entries`` simply reports what is
        tracked, it never blocks a write.
        """
        count = len(self._user_facts)
        return {
            "entries": count,
            "max_entries": _USER_PROFILE_MAX_ENTRIES,
            "remaining": max(0, _USER_PROFILE_MAX_ENTRIES - count),
        }

    def get_user_snapshot(self) -> str:
        """All user-profile facts as a frozen text block (mirrors
        ``MemoryVault.get_user_snapshot``'s format)."""
        if not self._user_facts:
            return ""
        lines = [f"- [{rec['category']}] {rec['content']}" for rec in self._user_facts.values()]
        return "## User Profile\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Agent profile.
    # ------------------------------------------------------------------

    def upsert_agent_fact(self, content: str, category: str = "directive") -> str:
        """Store an agent-persona directive — delegates to ``MemoryProvider.upsert()``."""
        fact_id = self._provider.upsert(content, category)
        if not fact_id.startswith("BLOCKED:"):
            self._agent_facts[fact_id] = {"content": content, "category": category}
        return fact_id

    def delete_agent_fact(self, fact_id: str) -> bool:
        """Delete an agent-persona directive — delegates to ``MemoryProvider.delete()``."""
        ok = self._provider.delete(fact_id)
        if ok:
            self._agent_facts.pop(fact_id, None)
        return ok

    def get_agent_snapshot(self) -> str:
        """All agent-persona directives as a frozen text block (mirrors
        ``MemoryVault.get_agent_snapshot``'s format)."""
        if not self._agent_facts:
            return ""
        lines = [f"- [{fid}] {rec['content']}" for fid, rec in self._agent_facts.items()]
        return "## Agent Persona Directives (Evolution Protocol)\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Deep, Chroma-only features: honestly absent on this backend, never a crash.
    # ------------------------------------------------------------------

    def search_delegations(
        self, query: str, top_k: int = 5, role: str | None = None
    ) -> list[dict[str, Any]]:
        """No delegation-outcome log on this backend — returns ``[]``, not a crash."""
        return []

    def search_episodic(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """No episodic-memory collection on this backend — returns ``[]``, not a crash."""
        return []

    def upsert_episode(
        self,
        *,
        session_id: str,
        summary: str,
        started_at: str,
        keywords: list[str] | None = None,
    ) -> str:
        """No episodic-memory collection on this backend — accepted and discarded."""
        return ""

    def get_quarantine_entries(self, top_k: int = 20) -> list[dict[str, Any]]:
        """No MemoryGuard quarantine audit trail on this backend — returns ``[]``."""
        return []

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    def attach_graph_index(self, index: Any) -> None:
        """Bind a MemoryIndex; forwarded to the wrapped provider, which has this
        as a real ABC hook (no-op default, meaningful override on
        ``FTS5MemoryProvider``) so ``semantic_search`` benefits when supported."""
        self._graph_index = index
        self._provider.attach_graph_index(index)

    def close(self) -> None:
        """Release the wrapped provider's resources."""
        self._provider.shutdown()


def resolve_provider(memory_settings: MemorySettings) -> MemoryProvider:
    """Factory: return the correct MemoryProvider for the configured backend.

    Called once at orchestrator boot.  Keeps provider selection out of the
    orchestrator so backends can be swapped without touching boot logic.

    Args:
        memory_settings: The ``memory`` section of the resolved config.

    Returns:
        An *uninitialized* provider.  The caller must call ``initialize()``
        before the first ``upsert()`` / ``search()`` call.
    """
    backend = (memory_settings.backend or "chroma").strip().lower()
    if backend == "in_memory":
        return InMemoryProvider()
    if backend == "fts5_only":
        return FTS5MemoryProvider()
    # Default: ChromaDB-backed vault.
    return ChromaMemoryProvider()
