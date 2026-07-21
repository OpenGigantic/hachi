"""T-50: Regression tests for MemoryManager session provenance.

``MemoryManager._session_id`` was read via ``getattr(self, "_session_id", "")``
(``core/memory.py``) but never assigned anywhere — the attribute did not exist on
the class and no setter did. Every background fact-extraction job was therefore
enqueued with ``session_id=""``, which meant:

  - extracted ``.og`` nodes never carried a ``->[:evidence=session:<id>]`` edge, and
  - every ``event`` node routed to ``memory/journal/unknown.og`` instead of
    ``memory/journal/<session-id>.og``.

These tests lock the fix: ``MemoryManager`` now declares ``_session_id`` in
``__init__``, exposes ``set_session_id()`` beside the existing ``set_vault`` /
``set_memory_writer`` setters, and ``sync_turn`` reads the real attribute instead
of a ``getattr`` default.
"""

from __future__ import annotations

from pathlib import Path

from hachi.core.memory import MemoryManager
from hachi.core.memory_writer import MemoryNode, MemoryWriter
from hachi.jobs import InMemoryJobQueue


def test_sync_turn_enqueues_job_with_real_session_id() -> None:
    """The fact-extraction job must carry the owning session's id.

    Regression (T-50): MemoryManager._session_id was read via
    getattr(self, "_session_id", "") and never assigned, so every extraction job
    was enqueued with session_id="" -- .og nodes got no evidence=session: edge and
    every event node routed to journal/unknown.og.
    """
    queue = InMemoryJobQueue()
    mgr = MemoryManager(job_queue=queue)
    mgr.set_vault(object())  # sync_turn only checks `if not self.vault:` (BB-3)
    mgr.set_session_id("sess-abc123")

    mgr.sync_turn(
        "I always deploy to fly.io, remember that.",
        "Noted -- fly.io is your deploy target.",
    )

    pending = queue.get_pending(limit=10)
    assert pending, "no extraction job was enqueued"
    assert pending[0].payload["session_id"] == "sess-abc123"


def test_written_node_carries_evidence_session_edge(tmp_path: Path) -> None:
    """A node written with the manager's session id gets the provenance edge on disk."""
    writer = MemoryWriter(memory_dir=tmp_path)
    writer.write_nodes(
        [MemoryNode(name="deploy-target", role="fact", dense="deploy target is fly.io")],
        session_id="sess-abc123",
    )
    written = (tmp_path / "core_memory.og").read_text(encoding="utf-8")
    assert "->[:evidence=session:sess-abc123]" in written


def test_event_nodes_route_to_the_real_session_journal(tmp_path: Path) -> None:
    """Event nodes must not all collapse into journal/unknown.og."""
    writer = MemoryWriter(memory_dir=tmp_path)
    writer.write_nodes(
        [
            MemoryNode(
                name="shipped-v1",
                role="event",
                dense="shipped v1",
                session_id="sess-abc123",
            )
        ],
        session_id="sess-abc123",
    )
    assert (tmp_path / "journal" / "sess-abc123.og").exists()
    assert not (tmp_path / "journal" / "unknown.og").exists()
