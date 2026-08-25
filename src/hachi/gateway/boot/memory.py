"""T-44 stage: the memory manager and the long-term memory vault it binds to.

Moved verbatim from `boot_agent`'s old step "7. Initialize the Memory Manager"
(`:3021`, the `MemoryManager(...)` construction itself) and the vault/graph/writer
binding that follows it (`:3053-3197`). In the original file those two spans are
NOT contiguous -- `:3033-3051` (tool_registry/checkpoint_manager/scheduler/
session_store, now `boot.tool_infra.build_tool_infrastructure`) sits physically
between them, under a "8. Build infrastructure..." comment, even though the vault
code that follows it is still conceptually finishing "7", per the ORIGINAL
step-7 comment's own words: "(Moved up so Vault can bind to it)".

`build_memory` and `boot.tool_infra.build_tool_infrastructure` have NO
cross-dependency (this stage never reads `tool_registry`/`checkpoint_manager`/
`scheduler`/`session_store`; that stage never reads `memory`/`vault`/
`graph_index` -- verified by reading every name each block touches). `boot_agent`
therefore calls this stage BEFORE tool_infra, making both fully contiguous --
the one deliberate, explicitly-flagged reordering in this task; see the T-44
report for the full proof of why it is behaviour-neutral (no runtime events fire
in this span, and the two spans mutate disjoint attributes of `shared`).
"""

from __future__ import annotations

import logging
from typing import Any

from hachi.core.memory import MemoryManager
from hachi.core.memory.provider import (
    ChromaMemoryProvider,
    FTS5MemoryProvider,
    ProviderBackedVault,
    resolve_provider,
)
from hachi.core.settings import MemorySettings
from hachi.jobs import JobQueue

from .stages import MemoryStage

logger = logging.getLogger(__name__)


def build_memory(
    config: dict[str, Any],
    *,
    job_queue: JobQueue,
    llm_client: Any,
    shared: Any,
    memory_dir: str,
) -> MemoryStage:
    """Moved verbatim from `:3021` + `:3053-3197`.

    Args:
        llm_client: the (non-routed) primary LLM client -- G3-T6: background
            memory ops (fact extraction, sliding-window summary) reuse this
            client's already-open HTTP/TLS connection pool instead of
            constructing a new one per background job.
        shared: the `ProcessSharedServices` singleton (`SkillEngineStage.shared`).
        memory_dir: `str(get_hachi_paths().data_dir)` for the provider /
            `get_hachi_paths().memory_dir` for the graph index -- callers pass
            `get_hachi_paths()` itself is avoided here so this stage does not
            need to import it; see call site in `boot_agent`.
    """
    # G3-T6: pass the shared LLM client so background memory ops (fact extraction,
    # sliding-window summary) reuse the already-open HTTP/TLS connection pool
    # instead of constructing a new AnthropicClient on every background job.
    memory = MemoryManager(max_active_messages=30, job_queue=job_queue, llm_client=llm_client)

    # Initialize and register the Long-Term Memory Vault (backend-selectable).
    # backend="chroma" (default) — ChromaDB persistent vault, full semantic tools.
    # backend="in_memory" / "fts5_only" — lightweight, no ChromaDB required. T-49:
    #   these route through ProviderBackedVault, which adopts the MemoryProvider
    #   seam (upsert/search/delete) so core/user/agent-profile facts are genuinely
    #   stored and retrieved — episodic memory, delegation search, and the
    #   MemoryGuard quarantine trail stay honestly unavailable (BB-146).
    _raw_memory = config.get("memory", {})
    memory_settings = MemorySettings.model_validate(_raw_memory)

    # P4-02: reuse shared vault when already initialised (avoids opening a new Chroma
    # PersistentClient per session — up to 100 clients per process before this fix).
    if shared.vault is not None:
        vault: Any = shared.vault
        if isinstance(vault, ProviderBackedVault):
            # T-49: the shared vault is already provider-backed (in_memory /
            # fts5_only) — reuse its wrapped provider directly instead of
            # wrapping it in a ChromaMemoryProvider, which would misreport
            # _is_fts5_only below and break the FTS5 graph/writer attachment.
            _provider = vault.provider
        else:
            _provider = ChromaMemoryProvider(vault=vault)
    else:
        _provider = resolve_provider(memory_settings)
        # T-06 / BB-99: the settings that actually control retrieval behaviour
        # (recall_distance_max, core_memory_max_entries, decay_half_life_days)
        # are applied by ProcessSharedServices._init (shared_services.py) — that
        # is the vault every session reuses via the `_shared.vault is not None`
        # branch above. This `else` only runs when no shared vault exists yet
        # (e.g. BB-6, before the first session has booted in this process); it
        # mirrors _init's settings-passing so a vault built directly here is not
        # silently under-configured. Non-Chroma backends keep the plain
        # initialize() signature — they have no recall/decay machinery.
        if isinstance(_provider, ChromaMemoryProvider):
            _provider.initialize(
                brain_dir=memory_dir,
                recall_distance_max=memory_settings.recall_distance_max,
                core_memory_max_entries=memory_settings.core_memory_max_entries,
                decay_half_life_days=memory_settings.decay_half_life_days,
            )
            vault = _provider.vault
        else:
            _provider.initialize(brain_dir=memory_dir)
            # T-49: adopt the seam — route reads/writes through the provider's
            # upsert/search/delete contract instead of discarding it to
            # vault=None (BB-146).
            vault = ProviderBackedVault(_provider)
    memory.set_vault(vault)

    # T-06 / BB-99: make a settings/vault mismatch loud instead of silent. If this
    # ever fires it means a [memory] retrieval control was configured but the live
    # vault was built from a different value — the exact failure mode that made
    # M2 decay inert for every user. Read the properties directly (never a
    # defaulted getattr — T-14) so a missing property raises instead of passing.
    # T-49: scoped to Chroma — in_memory/fts5_only have no recall/decay machinery
    # (shared_services.py's comment on the sibling branch above), so there is
    # nothing for this vault-vs-settings comparison to verify for them.
    if vault is not None and isinstance(_provider, ChromaMemoryProvider):
        if vault.decay_half_life_days != memory_settings.decay_half_life_days:
            logger.warning(
                "[memory] decay_half_life_days=%s was configured but the live vault "
                "reports %s — memory settings are not reaching the vault",
                memory_settings.decay_half_life_days,
                vault.decay_half_life_days,
            )
        if vault.recall_distance_max != memory_settings.recall_distance_max:
            logger.warning(
                "[memory] recall_distance_max=%s was configured but the live vault "
                "reports %s — memory settings are not reaching the vault",
                memory_settings.recall_distance_max,
                vault.recall_distance_max,
            )
        if vault.core_memory_max_entries != memory_settings.core_memory_max_entries:
            logger.warning(
                "[memory] core_memory_max_entries=%s was configured but the live "
                "vault reports %s — memory settings are not reaching the vault",
                memory_settings.core_memory_max_entries,
                vault.core_memory_max_entries,
            )

    # P4-06: M4 — fts5_only backend forces graph=True (files are canonical; no Chroma needed)
    is_fts5_only = isinstance(_provider, FTS5MemoryProvider)
    use_graph = memory_settings.graph or is_fts5_only

    # P2-04: M0 — attach FTS5 graph index when memory.graph=true (or fts5_only backend)
    # P4-02: reuse the shared graph_index when the process singleton already has one.
    graph_index = None
    if use_graph and (vault is not None or is_fts5_only):
        if shared.graph_index is not None:
            graph_index = shared.graph_index
            # Attach to vault if not already attached (idempotent guard).
            if vault is not None and getattr(vault, "_graph_index", None) is None:
                vault.attach_graph_index(graph_index)
            if is_fts5_only:
                _provider.attach_graph_index(graph_index)
        else:
            try:
                from hachi.core.memory.index import MemoryIndex
                from hachi.core.paths import get_hachi_paths

                _paths = get_hachi_paths()
                graph_index = MemoryIndex(
                    memory_dir=_paths.memory_dir,
                    db_path=_paths.memory_index_db,
                )
                graph_index.refresh()
                if vault is not None:
                    vault.attach_graph_index(graph_index)
                if is_fts5_only:
                    _provider.attach_graph_index(graph_index)
                logger.info(
                    "MemoryIndex: %d nodes indexed from %s",
                    graph_index.node_count,
                    _paths.memory_dir,
                )
            except Exception:
                logger.debug("P2-04: MemoryIndex init failed; graph recall disabled", exc_info=True)

    # P2-05: M1 — MemoryWriter for typed-node write path (always on when graph flag set)
    # P4-02: reuse the shared memory_writer when available.
    if use_graph:
        if shared.memory_writer is not None:
            memory.set_memory_writer(shared.memory_writer)
            if is_fts5_only:
                _provider.attach_memory_writer(shared.memory_writer)
        else:
            try:
                from hachi.core.memory.writer import MemoryWriter
                from hachi.core.paths import get_hachi_paths

                _paths = get_hachi_paths()
                _memory_writer = MemoryWriter(
                    memory_dir=_paths.memory_dir,
                    graph_index=graph_index,
                )
                memory.set_memory_writer(_memory_writer)
                if is_fts5_only:
                    _provider.attach_memory_writer(_memory_writer)
                logger.info("MemoryWriter: M1 write path active (memory_dir=%s)", _paths.memory_dir)
            except Exception:
                logger.debug(
                    "P2-05: MemoryWriter init failed; falling back to Chroma-only", exc_info=True
                )

    return MemoryStage(
        memory=memory,
        vault=vault,
        memory_settings=memory_settings,
        graph_index=graph_index,
        is_fts5_only=is_fts5_only,
    )
