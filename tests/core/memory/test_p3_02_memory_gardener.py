"""P3-02: M3 MemoryGardener — dedupe, expire, and diff-card tests.

Tests:
  1. GardeningResult.save() / load() round-trip.
  2. find_dedup_same_name() detects same node in two files.
  3. Auto-apply writes ::changelog to the older file.
  4. find_similar_content() proposes merge for high-Jaccard nodes.
  5. find_expired() flags past valid_until nodes.
  6. run() returns GardeningResult with auto_applied + pending_review.
  7. run() writes pending_diffs.json.
  8. _jaccard() baseline cases.
  9. BB-177: MERGE_SIMILAR write-back target must not depend on directory
     enumeration order (macOS/APFS-only CI failure — reproduced without macOS).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NODE_A = """\
::meta
  title: Project A

::node[billing-task]
->[:role=task]
->[:status=open]
->[:keywords=billing, payments]
->[:updated=2026-06-10T00:00:00Z]
::dense
Migrate the billing table to the new schema.
"""

_NODE_B = """\
::meta
  title: Project B (same node name)

::node[billing-task]
->[:role=task]
->[:status=open]
->[:keywords=billing, payments]
->[:updated=2026-06-12T00:00:00Z]
::dense
Migrate the billing table to the new schema (newer copy).
"""

_NODE_SIMILAR = """\
::meta
  title: Project Similar

::node[billing-work]
->[:role=task]
->[:status=open]
->[:keywords=billing, payments, migrate]
->[:updated=2026-06-10T00:00:00Z]
::dense
Migrate the billing table to the new schema, in progress.
"""

_NODE_EXPIRED = """\
::meta
  title: Expired Facts

::node[old-decision]
->[:role=fact]
->[:valid_until=2025-01-01T00:00:00Z]
->[:keywords=old, expired]
->[:updated=2025-01-01T00:00:00Z]
::dense
Use DMS for migration. (expired — replaced by pgloader decision)
"""


def _write_file(directory: Path, name: str, content: str, mtime_offset: float = 0.0) -> Path:
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    if mtime_offset:
        new_mtime = p.stat().st_mtime + mtime_offset
        import os

        os.utime(p, (new_mtime, new_mtime))
    return p


# ---------------------------------------------------------------------------
# 1. GardeningResult save/load round-trip
# ---------------------------------------------------------------------------


def test_gardening_result_save_load(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import GardeningProposal, GardeningResult, ProposalKind

    result = GardeningResult(
        run_ts="2026-06-13T00:00:00Z",
        auto_applied=[
            GardeningProposal(
                kind=ProposalKind.DEDUP_SAME_NAME,
                name_a="billing-task",
                reason="duplicate",
                confidence=1.0,
                file_path_a="a.og",
                file_path_b="b.og",
                auto_applied=True,
            )
        ],
        pending_review=[
            GardeningProposal(
                kind=ProposalKind.MERGE_SIMILAR,
                name_a="billing-work",
                name_b="billing-task",
                reason="Jaccard 0.85",
                confidence=0.85,
            )
        ],
    )
    path = tmp_path / "pending_diffs.json"
    result.save(path)
    loaded = GardeningResult.load(path)
    assert loaded.run_ts == "2026-06-13T00:00:00Z"
    assert len(loaded.auto_applied) == 1
    assert len(loaded.pending_review) == 1
    assert loaded.auto_applied[0].name_a == "billing-task"
    assert loaded.pending_review[0].kind == "merge_similar"


# ---------------------------------------------------------------------------
# 2. find_dedup_same_name detects same node in two files
# ---------------------------------------------------------------------------


def test_find_dedup_same_name(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import MemoryGardener

    _write_file(tmp_path, "projects/a.og", _NODE_A, mtime_offset=-100)
    _write_file(tmp_path, "projects/b.og", _NODE_B)

    gardener = MemoryGardener(tmp_path)
    result = gardener.run(auto_apply=False)

    kinds = [p.kind for p in result.pending_review]
    assert "dedup_same_name" in kinds, f"Expected dedup proposal, got: {kinds}"
    dup_props = [p for p in result.pending_review if p.kind == "dedup_same_name"]
    assert any(p.name_a == "billing-task" for p in dup_props)


# ---------------------------------------------------------------------------
# 3. Auto-apply writes ::changelog to the older file
# ---------------------------------------------------------------------------


def test_auto_apply_writes_changelog(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import MemoryGardener

    older = _write_file(tmp_path, "projects/a.og", _NODE_A, mtime_offset=-100)
    _write_file(tmp_path, "projects/b.og", _NODE_B)

    gardener = MemoryGardener(tmp_path)
    result = gardener.run(auto_apply=True)

    assert len(result.auto_applied) >= 1, "Expected at least one auto-applied dedup"
    older_text = older.read_text(encoding="utf-8")
    assert "::changelog" in older_text, "::changelog must be appended to the older file"


# ---------------------------------------------------------------------------
# 4. find_similar_content proposes merge for high-Jaccard nodes
# ---------------------------------------------------------------------------


def test_find_similar_content(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import MemoryGardener

    _write_file(tmp_path, "projects/a.og", _NODE_A)
    _write_file(tmp_path, "projects/similar.og", _NODE_SIMILAR)

    gardener = MemoryGardener(tmp_path, similarity_threshold=0.5)
    result = gardener.run(auto_apply=False)

    kinds = [p.kind for p in result.pending_review]
    assert "merge_similar" in kinds, f"Expected merge_similar proposal; got {kinds}"


# ---------------------------------------------------------------------------
# 5. find_expired flags past valid_until nodes
# ---------------------------------------------------------------------------


def test_find_expired(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import MemoryGardener

    _write_file(tmp_path, "projects/expired.og", _NODE_EXPIRED)
    gardener = MemoryGardener(tmp_path)
    result = gardener.run(auto_apply=False)

    kinds = [p.kind for p in result.pending_review]
    assert "expire_stale" in kinds, f"Expected expire_stale; got {kinds}"
    exp = next(p for p in result.pending_review if p.kind == "expire_stale")
    assert exp.name_a == "old-decision"


# ---------------------------------------------------------------------------
# 6. run() returns GardeningResult with both lists
# ---------------------------------------------------------------------------


def test_run_returns_result(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import GardeningResult, MemoryGardener

    _write_file(tmp_path, "projects/a.og", _NODE_A, mtime_offset=-100)
    _write_file(tmp_path, "projects/b.og", _NODE_B)
    _write_file(tmp_path, "projects/expired.og", _NODE_EXPIRED)

    gardener = MemoryGardener(tmp_path)
    result = gardener.run()

    assert isinstance(result, GardeningResult)
    assert result.run_ts, "run_ts should be set"


# ---------------------------------------------------------------------------
# 7. run() writes pending_diffs.json
# ---------------------------------------------------------------------------


def test_run_writes_pending_diffs_json(tmp_path: Path) -> None:
    from hachi.core.memory.gardener import MemoryGardener

    _write_file(tmp_path, "projects/expired.og", _NODE_EXPIRED)
    gardener = MemoryGardener(tmp_path)
    gardener.run()

    diffs_path = tmp_path / "pending_diffs.json"
    assert diffs_path.exists(), "pending_diffs.json must be written by run()"
    import json

    data = json.loads(diffs_path.read_text(encoding="utf-8"))
    assert "pending_review" in data
    assert "auto_applied" in data


# ---------------------------------------------------------------------------
# 8. _jaccard baseline cases
# ---------------------------------------------------------------------------


def test_jaccard_identical() -> None:
    from hachi.core.memory.gardener import _jaccard

    assert _jaccard("billing migrate schema", "billing migrate schema") == 1.0


def test_jaccard_disjoint() -> None:
    from hachi.core.memory.gardener import _jaccard

    assert _jaccard("billing migrate schema", "weather forecast rain") == 0.0


def test_jaccard_partial() -> None:
    from hachi.core.memory.gardener import _jaccard

    sim = _jaccard("billing migrate schema", "billing migrate payments")
    assert 0.3 < sim < 0.9


# ---------------------------------------------------------------------------
# 9. BB-177: MERGE_SIMILAR write-back target must not depend on directory
#    enumeration order (the macOS-only CI failure in
#    tests/gateway/test_memory_gardener_wiring.py, reproduced here without
#    macOS).
#
# Root cause: _parse_dir() builds its name -> [_ParsedNode] index by iterating
# `memory_dir.rglob("*.og")` with no sort. The stdlib documents that order as
# unspecified ("glob.glob: the order of the returned list is undefined"), and
# it is realised differently per filesystem: NTFS/ext4 happen to return small
# directories in creation/alphabetical order (why this passes today on
# windows-latest/ubuntu-latest), but APFS (macOS's default filesystem) does
# not make that guarantee. _find_similar_content() then does
# `names = list(index.keys())` and assigns name_a/name_b (and therefore
# file_path_a/file_path_b) purely from that iteration order. decide() ->
# _apply_reviewed_proposal() always appends the ::changelog to file_path_b
# (falling back to file_path_a only when file_path_b is empty). So on macOS,
# for the exact same pair of files, file_path_a and file_path_b can come out
# swapped relative to Windows/Linux -- the changelog lands on the OTHER file,
# and the file a human/test expects to change looks completely untouched
# (nothing raises; the route still returns 200 ok=True).
# ---------------------------------------------------------------------------


def test_merge_similar_changelog_target_is_order_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file that receives the approved-merge ::changelog must be a
    deterministic function of the two files being merged, not of the
    incidental order Path.rglob() happened to enumerate them in.

    Drives MemoryGardener.run() -> decide() through a monkeypatched
    Path.rglob() that returns the SAME two files in both possible orders
    (simulating "NTFS/ext4 order" and "APFS order" without needing macOS),
    and asserts the write-back lands on the same file either way. Before the
    fix this fails on ANY OS (it does not rely on the real filesystem's scan
    order at all) because _parse_dir() has no sort — proving the mechanism
    directly rather than merely observing it on a platform we cannot reach.
    """
    from hachi.core.memory.gardener import MemoryGardener

    real_rglob = Path.rglob

    def _run_once(*, b_seen_first: bool) -> str:
        mem_dir = tmp_path / ("b_first" if b_seen_first else "a_first")
        mem_dir.mkdir()
        node_a = mem_dir / "a.og"
        node_b = mem_dir / "b.og"
        node_a.write_text(
            "::node[topic_alpha]\n::dense\nloves long hikes in the mountains on weekends\n",
            encoding="utf-8",
        )
        node_b.write_text(
            "::node[topic_beta]\n::dense\nloves long hikes in the mountains on weekends\n",
            encoding="utf-8",
        )

        def fake_rglob(self: Path, pattern: str, **kwargs: object) -> object:
            if self == mem_dir and pattern == "*.og":
                seq = [node_b, node_a] if b_seen_first else [node_a, node_b]
                return iter(seq)
            return real_rglob(self, pattern, **kwargs)

        monkeypatch.setattr(Path, "rglob", fake_rglob)
        try:
            gardener = MemoryGardener(mem_dir)
            result = gardener.run()
            assert len(result.pending_review) == 1, (
                f"expected exactly one merge_similar proposal, got {result.pending_review}"
            )
            gardener.decide(result.pending_review[0].id, approve=True)
        finally:
            monkeypatch.setattr(Path, "rglob", real_rglob)

        a_changed = "::changelog" in node_a.read_text(encoding="utf-8")
        b_changed = "::changelog" in node_b.read_text(encoding="utf-8")
        assert a_changed != b_changed, "exactly one of the two files should receive the changelog"
        return "a.og" if a_changed else "b.og"

    target_a_first = _run_once(b_seen_first=False)
    target_b_first = _run_once(b_seen_first=True)

    assert target_a_first == target_b_first, (
        "MERGE_SIMILAR write-back target flips depending only on directory-scan "
        f"order (a-seen-first -> {target_a_first}, b-seen-first -> {target_b_first}) "
        "instead of being determined by the files/nodes themselves -- this is the "
        "BB-177 mechanism behind the macOS-only failure in "
        "tests/gateway/test_memory_gardener_wiring.py."
    )
