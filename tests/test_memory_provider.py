"""Tests for the pluggable memory provider system.

Covers:
- InMemoryProvider: upsert / search / delete / shutdown
- resolve_provider factory: chroma → ChromaMemoryProvider, in_memory → InMemoryProvider
- memory_tools null-vault guards: all tool callables return the graceful degradation message
- ProviderBackedVault (T-49): the in_memory / fts5_only backends actually store and
  retrieve THROUGH the provider's ABC contract methods instead of bypassing them
  (BB-146)
"""

from __future__ import annotations

from hachi.core.memory_provider import (
    ChromaMemoryProvider,
    InMemoryProvider,
    MemoryProvider,
    resolve_provider,
)
from hachi.core.settings import MemorySettings

# ---------------------------------------------------------------------------
# InMemoryProvider — upsert / search / delete / shutdown
# ---------------------------------------------------------------------------


def _provider() -> InMemoryProvider:
    p = InMemoryProvider()
    p.initialize(brain_dir="/tmp/unused")
    return p


def test_inmemory_upsert_returns_id() -> None:
    p = _provider()
    fid = p.upsert("the project uses FastAPI")
    assert isinstance(fid, str)
    assert len(fid) > 0


def test_inmemory_search_finds_exact_match() -> None:
    p = _provider()
    p.upsert("the project uses FastAPI", category="architecture")
    results = p.search("FastAPI project")
    assert len(results) >= 1
    assert any("FastAPI" in r["content"] for r in results)


def test_inmemory_search_returns_empty_for_no_match() -> None:
    p = _provider()
    p.upsert("unrelated fact about bananas")
    results = p.search("XYZ_TOTALLY_UNRELATED_KEYWORD_12345")
    assert results == []


def test_inmemory_delete_removes_fact() -> None:
    p = _provider()
    fid = p.upsert("fact to delete")
    assert p.delete(fid) is True
    results = p.search("fact to delete")
    assert not any(r["id"] == fid for r in results)


def test_inmemory_delete_unknown_id_returns_false() -> None:
    p = _provider()
    assert p.delete("nonexistent-id-zzz") is False


def test_inmemory_search_respects_top_k() -> None:
    p = _provider()
    for i in range(10):
        p.upsert(f"shared keyword fact number {i}")
    results = p.search("keyword fact", top_k=3)
    assert len(results) <= 3


def test_inmemory_shutdown_clears_all_facts() -> None:
    p = _provider()
    p.upsert("fact one")
    p.upsert("fact two")
    p.shutdown()
    # After shutdown, no facts remain.
    assert p.search("fact") == []


def test_inmemory_implements_provider_interface() -> None:
    """InMemoryProvider satisfies the MemoryProvider ABC contract."""
    p = InMemoryProvider()
    assert isinstance(p, MemoryProvider)


def test_inmemory_result_has_expected_keys() -> None:
    p = _provider()
    p.upsert("hello world", category="test_cat")
    results = p.search("hello")
    assert results, "Expected at least one result"
    for key in ("id", "content", "raw_content", "category"):
        assert key in results[0], f"Missing key '{key}' in result"
    assert results[0]["category"] == "test_cat"


# ---------------------------------------------------------------------------
# resolve_provider factory
# ---------------------------------------------------------------------------


def test_resolve_provider_chroma_returns_chroma_provider() -> None:
    settings = MemorySettings(backend="chroma")
    provider = resolve_provider(settings)
    assert isinstance(provider, ChromaMemoryProvider)


def test_resolve_provider_in_memory_returns_inmemory_provider() -> None:
    settings = MemorySettings(backend="in_memory")
    provider = resolve_provider(settings)
    assert isinstance(provider, InMemoryProvider)


def test_resolve_provider_default_is_chroma() -> None:
    settings = MemorySettings()
    assert settings.backend == "chroma"
    provider = resolve_provider(settings)
    assert isinstance(provider, ChromaMemoryProvider)


# ---------------------------------------------------------------------------
# memory_tools null-vault guards
# ---------------------------------------------------------------------------


def test_memory_tools_save_degrades_gracefully_when_vault_is_none() -> None:
    """save_memory returns informational message, not AttributeError, when vault=None."""
    from unittest.mock import MagicMock

    from hachi.tools.memory_tools import register_memory_tools

    registry = MagicMock()
    registered: dict[str, object] = {}

    def _capture(schema: dict, fn: object, **_kwargs: object) -> None:
        registered[schema["name"]] = fn

    registry.register.side_effect = _capture
    register_memory_tools(registry, vault=None)

    save_fn = registered["core_save_memory"]
    result = save_fn(facts=[{"content": "test fact"}])  # type: ignore[operator]
    assert "not available" in result.lower()


def test_memory_tools_search_degrades_gracefully_when_vault_is_none() -> None:
    from unittest.mock import MagicMock

    from hachi.tools.memory_tools import register_memory_tools

    registry = MagicMock()
    registered: dict[str, object] = {}

    def _capture(schema: dict, fn: object, **_kwargs: object) -> None:
        registered[schema["name"]] = fn

    registry.register.side_effect = _capture
    register_memory_tools(registry, vault=None)

    search_fn = registered["core_search_memory"]
    result = search_fn(query="anything", top_k=5)  # type: ignore[operator]
    assert "not available" in result.lower()


def test_memory_tools_delete_degrades_gracefully_when_vault_is_none() -> None:
    from unittest.mock import MagicMock

    from hachi.tools.memory_tools import register_memory_tools

    registry = MagicMock()
    registered: dict[str, object] = {}

    def _capture(schema: dict, fn: object, **_kwargs: object) -> None:
        registered[schema["name"]] = fn

    registry.register.side_effect = _capture
    register_memory_tools(registry, vault=None)

    delete_fn = registered["core_delete_memory"]
    result = delete_fn(fact_id="abc123")  # type: ignore[operator]
    assert "not available" in result.lower()


# ---------------------------------------------------------------------------
# ProviderBackedVault — T-49: adopt the seam (BB-146)
#
# Before this fix, `in_memory` / `fts5_only` resolved to a provider whose
# `.upsert()` / `.search()` / `.delete()` were never called by production code —
# boot unwrapped `_provider.vault` (an attribute only ChromaMemoryProvider has)
# and fell back to `vault=None`. These tests assert the ADOPTED behaviour: a
# write/read on these backends genuinely goes through the provider's ABC
# contract methods, not a mock's return value.
# ---------------------------------------------------------------------------


class _SpyProvider(InMemoryProvider):
    """Records every ABC contract-method call so a test can assert the seam is
    actually exercised -- not just that the end result happens to look right."""

    def __init__(self) -> None:
        super().__init__()
        self.upsert_calls: list[tuple[str, str]] = []
        self.search_calls: list[tuple[str, int]] = []
        self.delete_calls: list[str] = []

    def upsert(self, content: str, category: str = "workspace_state") -> str:
        self.upsert_calls.append((content, category))
        return super().upsert(content, category)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        self.search_calls.append((query, top_k))
        return super().search(query, top_k)

    def delete(self, fact_id: str) -> bool:
        self.delete_calls.append(fact_id)
        return super().delete(fact_id)


def test_provider_backed_vault_upsert_routes_through_provider_upsert() -> None:
    """T-49 / BB-146: vault.upsert_fact() must call provider.upsert(), not bypass it."""
    from hachi.core.memory_provider import ProviderBackedVault

    spy = _SpyProvider()
    spy.initialize(brain_dir="/tmp/unused")
    vault = ProviderBackedVault(spy)

    vault.upsert_fact("the deploy target is fly.io", category="workspace_state")

    assert spy.upsert_calls == [("the deploy target is fly.io", "workspace_state")], (
        "upsert_fact must call the provider's ABC upsert() -- it did not"
    )


def test_provider_backed_vault_search_routes_through_provider_search() -> None:
    """T-49 / BB-146: vault.semantic_search() must call provider.search(), not bypass it."""
    from hachi.core.memory_provider import ProviderBackedVault

    spy = _SpyProvider()
    spy.initialize(brain_dir="/tmp/unused")
    vault = ProviderBackedVault(spy)
    vault.upsert_fact("the deploy target is fly.io")

    vault.semantic_search("deploy target", top_k=3)

    assert spy.search_calls == [("deploy target", 3)], (
        "semantic_search must call the provider's ABC search() -- it did not"
    )


def test_provider_backed_vault_delete_routes_through_provider_delete() -> None:
    """T-49 / BB-146: vault.delete_fact() must call provider.delete(), not bypass it."""
    from hachi.core.memory_provider import ProviderBackedVault

    spy = _SpyProvider()
    spy.initialize(brain_dir="/tmp/unused")
    vault = ProviderBackedVault(spy)
    fact_id = vault.upsert_fact("fact to delete")

    vault.delete_fact(fact_id)

    assert spy.delete_calls == [fact_id], "delete_fact must call the provider's ABC delete()"


def test_provider_backed_vault_in_memory_write_then_read_round_trips() -> None:
    """T-49: the real, observed behaviour -- write a fact, read it back -- for
    backend=in_memory. This is exactly the scenario BB-146 describes: before the
    fix, boot discarded the provider and this search would have found nothing
    (vault was None, so the tool layer never even reached semantic_search)."""
    from hachi.core.memory_provider import ProviderBackedVault

    provider = InMemoryProvider()
    provider.initialize(brain_dir="/tmp/unused")
    vault = ProviderBackedVault(provider)

    fact_id = vault.upsert_fact("the deploy target is fly.io", category="workspace_state")
    assert not fact_id.startswith("BLOCKED:")

    hits = vault.semantic_search("deploy target", top_k=5)
    assert any("fly.io" in (h.get("raw_content") or h.get("content", "")) for h in hits), (
        f"fact not found via provider-backed vault: {hits}"
    )


def test_provider_backed_vault_fts5_only_write_then_read_round_trips(tmp_path) -> None:
    """T-49: same round-trip proof for backend=fts5_only, using the real
    MemoryWriter/MemoryIndex wiring (no Chroma) -- mirrors the attachment
    gateway/orchestrator.py performs at boot (attach_graph_index /
    attach_memory_writer)."""
    from hachi.core.memory_index import MemoryIndex
    from hachi.core.memory_provider import FTS5MemoryProvider, ProviderBackedVault
    from hachi.core.memory_writer import MemoryWriter

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    index = MemoryIndex(memory_dir=memory_dir, db_path=tmp_path / "memory_index.db")
    writer = MemoryWriter(memory_dir=memory_dir, graph_index=index)

    provider = FTS5MemoryProvider()
    provider.initialize(brain_dir=str(tmp_path))
    vault = ProviderBackedVault(provider)
    vault.attach_graph_index(index)  # also forwards to provider.attach_graph_index
    provider.attach_memory_writer(writer)

    fact_id = vault.upsert_fact("the deploy target is fly.io", category="workspace_state")
    assert not fact_id.startswith("BLOCKED:")

    hits = vault.semantic_search("deploy target", top_k=5)
    assert any("fly.io" in (h.get("raw_content") or h.get("content", "")) for h in hits), (
        f"fact not found via fts5_only provider-backed vault: {hits}"
    )


def test_shared_services_in_memory_backend_no_longer_produces_a_none_vault(tmp_path) -> None:
    """T-49 / BB-146 -- the exact regression site (gateway/shared_services.py):
    before the fix, ProcessSharedServices._init discarded the provider and
    `shared.vault` was None for backend=in_memory. It must now be a working,
    provider-backed vault that stores and retrieves through the real boot path
    (not a test-local reimplementation of it)."""
    from hachi.core.memory_provider import ProviderBackedVault
    from hachi.gateway.shared_services import ProcessSharedServices

    ProcessSharedServices.clear()
    try:
        shared = ProcessSharedServices.get_or_create(
            {"memory": {"backend": "in_memory"}}, str(tmp_path), force_reinit=True
        )
        assert shared.vault is not None, (
            "backend 'in_memory' resolved but stores nothing: vault is None"
        )
        assert isinstance(shared.vault, ProviderBackedVault)

        fact_id = shared.vault.upsert_fact("the deploy target is fly.io")
        assert not fact_id.startswith("BLOCKED:")
        hits = shared.vault.semantic_search("deploy target")
        assert any("fly.io" in (h.get("raw_content") or h.get("content", "")) for h in hits), (
            f"fact not found via ProcessSharedServices-booted vault: {hits}"
        )
    finally:
        ProcessSharedServices.shutdown_all()
