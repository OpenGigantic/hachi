"""T-51: memory "forget" must be durable across the tri-store (vault/writer/graph).

Facts dual-write to Chroma AND an ``.og`` file, but ``MemoryVault.delete_fact``
used to remove only the Chroma row. ``build_graph_recall_section`` — the real
per-turn call site — reads ``.og`` files straight off disk, so a fact the user
asked the agent to forget was re-injected into the very next turn's context.

These tests assert the REAL, observed behaviour through the actual read path
(``build_graph_recall_section`` / ``MemoryIndex.search``), not that some mock
delete method was called.

BB-3: never ``patch.dict(sys.modules)`` over ``chromadb`` — conftest warms the
chromadb PyO3 extension once per process. Most tests below are deliberately
Chroma-free (``MemoryWriter`` + ``graph_recall`` + ``MemoryIndex`` only, exactly
mirroring the T-51 spec's own regression-test sketch). The one test that needs
vault-level coverage (the ``memory.graph=false`` acceptance criterion, which is
inherently about ``MemoryVault.delete_*``) constructs a real ``MemoryVault``
against ``tmp_path`` and skips when chromadb is unavailable — the same pattern
``tests/test_memory_reliability.py`` and ``tests/test_memory_vault_close.py``
already use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hachi.core.graph_recall import build_graph_recall_section, get_node_index
from hachi.core.memory_writer import MemoryNode, MemoryWriter

_SECRET = "the user previously worked at Initech"


def _write_fact(memory_dir: Path) -> None:
    MemoryWriter(memory_dir=memory_dir).write_nodes(
        [
            MemoryNode(
                name="former-employer",
                role="fact",
                dense=_SECRET,
                keywords=["employer", "Initech"],
            )
        ],
        session_id="sess-1",
    )


def _clear_graph_recall_cache() -> None:
    """Clear the module-level mtime-keyed LRU cache (``graph_recall._cached_load``).

    Writing the tombstone changes the ``.og`` file's mtime, which naturally
    invalidates the cache key on its own — but mtime granularity on some
    filesystems can collapse two writes issued in close succession into the
    same ``mtime_ns``, so this clears the cache explicitly rather than relying
    on that alone or adding a ``sleep`` (T-51 spec §7 notes). ``get_node_index``
    itself has no ``cache_clear`` — the ``@lru_cache`` lives on the inner
    ``_cached_load`` it delegates to.
    """
    from hachi.core import graph_recall as gr_module

    gr_module._cached_load.cache_clear()


def _require_chroma() -> None:
    """Skip the test if chromadb is not installed (CI base-install may lack it)."""
    pytest.importorskip("chromadb", reason="chromadb not installed — skipping MemoryVault test")


# ---------------------------------------------------------------------------
# The three mandated regression tests (T-51 spec §7)
# ---------------------------------------------------------------------------


def test_forgotten_fact_does_not_resurface_via_graph_recall(tmp_path: Path) -> None:
    """A tombstoned fact must not be injected into the next turn's context.

    Regression (T-51): facts dual-wrote to .og + Chroma but delete_fact only
    removed the Chroma row. graph_recall reads .og straight off disk, so a
    "forgotten" fact was re-injected on the very next turn.
    """
    _write_fact(tmp_path)
    # Sanity: it IS recalled before the tombstone (guards against a vacuous pass).
    assert "Initech" in build_graph_recall_section("where did I work?", tmp_path)

    ok = MemoryWriter(memory_dir=tmp_path).write_tombstone("former-employer")
    assert ok is True
    _clear_graph_recall_cache()

    section = build_graph_recall_section("where did I work?", tmp_path)
    assert "Initech" not in section, f"forgotten fact resurfaced: {section!r}"


def test_tombstone_suppresses_node_even_when_unseeded(tmp_path: Path) -> None:
    """Suppression must not depend on the tombstone matching the query.

    graph_recall falls back to the last 5 nodes when nothing matches
    (graph_recall.py:193-194), so a query with zero overlap must still not
    resurface the forgotten fact.
    """
    _write_fact(tmp_path)
    MemoryWriter(memory_dir=tmp_path).write_tombstone("former-employer")

    section = build_graph_recall_section("what is the weather", tmp_path)
    assert "Initech" not in section


def test_fts5_delete_uses_the_real_node_id(tmp_path: Path) -> None:
    """The FTS5 delete must target the composite node_id MemoryIndex actually stores."""
    from hachi.core.memory_index import MemoryIndex

    _write_fact(tmp_path)
    index = MemoryIndex(memory_dir=tmp_path, db_path=tmp_path / "memory_index.db")
    index.refresh()
    assert index.search("Initech", top_k=5), "precondition: fact must be indexed"

    MemoryWriter(memory_dir=tmp_path).write_tombstone("former-employer")
    index.refresh()
    assert not index.search("Initech", top_k=5), "tombstoned node survived a refresh"


# ---------------------------------------------------------------------------
# Additional coverage: emitted .og text, append-only guarantee, no-op cases
# ---------------------------------------------------------------------------


def test_write_tombstone_emits_supersedes_edge_and_no_secret_content(tmp_path: Path) -> None:
    """Unit coverage for the emitted .og text (T-51 spec §6 step 2).

    The tombstone must carry a ``->[:supersedes=<name>]`` edge, a
    ``->[:role=tombstone]`` edge, and the given ``reason`` — and must NEVER
    copy the superseded node's dense content, only its name, so it cannot
    become a new resurrection vector (T-51 spec §12 "known traps").
    """
    _write_fact(tmp_path)
    ok = MemoryWriter(memory_dir=tmp_path).write_tombstone(
        "former-employer", reason="user-requested"
    )
    assert ok is True

    text = (tmp_path / "core_memory.og").read_text(encoding="utf-8")
    assert "->[:supersedes=former-employer]" in text
    assert "->[:role=tombstone]" in text
    assert "->[:reason=user-requested]" in text

    # Isolate the tombstone's own block — it must not repeat the secret content.
    tombstone_block = text.split("::node[tombstone-former-employer-", 1)[1]
    assert "Initech" not in tombstone_block


def test_write_tombstone_returns_false_when_node_not_found(tmp_path: Path) -> None:
    """write_tombstone is a no-op (returns False) when the named node doesn't exist."""
    ok = MemoryWriter(memory_dir=tmp_path).write_tombstone("nonexistent-node")
    assert ok is False


def test_tombstone_is_append_only_original_node_survives_on_disk(tmp_path: Path) -> None:
    """The original .og node is never removed — only suppressed (acceptance criteria)."""
    _write_fact(tmp_path)
    MemoryWriter(memory_dir=tmp_path).write_tombstone("former-employer")

    text = (tmp_path / "core_memory.og").read_text(encoding="utf-8")
    assert "::node[former-employer]" in text
    assert _SECRET in text  # the original dense content is still physically on disk

    # But suppression is global at the load boundary: the node no longer
    # appears in the loaded index (memory_graph.load_memory_dir), regardless
    # of whether anything seeded or traversed to the tombstone.
    from hachi.core.memory_graph import load_memory_dir

    assert "former-employer" not in load_memory_dir(tmp_path)


def test_get_node_index_reflects_the_tombstone_after_cache_invalidation(tmp_path: Path) -> None:
    """get_node_index (the graph_recall-facing accessor) also drops the suppressed node."""
    _write_fact(tmp_path)
    assert "former-employer" in get_node_index(tmp_path)

    MemoryWriter(memory_dir=tmp_path).write_tombstone("former-employer")
    _clear_graph_recall_cache()

    assert "former-employer" not in get_node_index(tmp_path)


# ---------------------------------------------------------------------------
# Acceptance criterion: the tombstone is written even when memory.graph=false
# (self._graph_index is None) — for all three vault delete methods.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("upsert_name", "delete_name", "write_role"),
    [
        ("upsert_fact", "delete_fact", "fact"),
        ("upsert_user_fact", "delete_user_fact", "preference"),
        ("upsert_agent_fact", "delete_agent_fact", "rule"),
    ],
)
def test_delete_tombstones_og_node_even_without_graph_index(
    tmp_path: Path, upsert_name: str, delete_name: str, write_role: str
) -> None:
    """Every vault delete method must tombstone the .og node even with no
    MemoryIndex attached (memory.graph=false) — so a later flip of
    memory.graph to true does not rebuild the FTS5 index with the forgotten
    fact still present (T-51 spec §5 item 3).
    """
    _require_chroma()
    from hachi.core.memory_graph import load_memory_dir
    from hachi.core.memory_vault import MemoryVault

    memory_dir = tmp_path / "memory"
    vault = MemoryVault(brain_dir=str(tmp_path), memory_dir=memory_dir)

    # Simulate the dual-write: the same content lands in Chroma AND an .og node.
    node_name = "delete-me"
    MemoryWriter(memory_dir=memory_dir).write_nodes(
        [MemoryNode(name=node_name, role=write_role, dense=_SECRET)]
    )
    fact_id = getattr(vault, upsert_name)(_SECRET)
    assert not fact_id.startswith("BLOCKED:"), f"MemoryGuard blocked the fixture write: {fact_id}"

    ok = getattr(vault, delete_name)(fact_id)
    assert ok is True, f"{delete_name} reported failure"

    all_text = "\n".join(p.read_text(encoding="utf-8") for p in memory_dir.rglob("*.og"))
    assert f"::node[{node_name}]" in all_text, "append-only: original node must survive on disk"
    assert f"->[:supersedes={node_name}]" in all_text, (
        f"{delete_name} did not write a tombstone despite graph_index=None"
    )
    assert node_name not in load_memory_dir(memory_dir), (
        f"{delete_name} did not suppress the .og node despite graph_index=None"
    )
