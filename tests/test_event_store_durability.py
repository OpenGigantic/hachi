"""T-75 Stage B — durable event log wiring, end to end through RuntimeReadModelStore.

Spec: docs/audit/2026-07-19/tasks/T-75-durable-event-log.md §7. Real tests over
``tmp_path`` SQLite databases, no mocks. Each test constructs its own
``EventStore(db_path=...)`` explicitly and never touches the process-wide
``get_event_store()`` singleton (that accessor is exercised indirectly via
``boot_agent`` in production, not here).
"""

from __future__ import annotations

from pathlib import Path

from hachi.core.events import EventStore
from hachi.core.runtime_events import RuntimeEvent
from hachi.core.runtime_read_models import RuntimeReadModelStore


def _ev(name: str, payload: dict, timestamp: str = "2026-08-27T10:00:00") -> RuntimeEvent:
    return RuntimeEvent(name=name, payload=payload, timestamp=timestamp)


def test_events_survive_store_restart(tmp_path: Path) -> None:
    """T-75: a durable event log must outlive the process that wrote it.

    Must FAIL before the fix with ``TypeError: RuntimeReadModelStore.__init__()
    takes 1 positional argument but 2 were given`` (or the keyword equivalent)
    -- __init__ takes no arguments today.
    """
    db = tmp_path / "events.db"

    first = RuntimeReadModelStore(event_store=EventStore(db_path=db), session_id="s1")
    for i in range(3):
        first.append_event(_ev("tool.call.completed", {"i": i}))
    before = first.read_events_since(after=0, limit=100)
    assert len(before) == 3

    # "Restart": a brand-new store object over the same file. No shared state.
    second = RuntimeReadModelStore(event_store=EventStore(db_path=db), session_id="s1")
    after = second.read_events_since(after=0, limit=100)

    assert len(after) == 3, "events did not survive the restart"
    assert [e.name for e in after] == [e.name for e in before]

    # Sequence continues; it does not restart at 1.
    second.append_event(_ev("session.shutdown", {}))
    assert second.read_events_since(after=3, limit=10)[0].sequence == 4


def test_replay_fidelity_matches_live_fidelity(tmp_path: Path) -> None:
    """A governance event unrepresentable by message-row synthesis survives replay.

    ``permission.decided`` is the canonical case spec §4.3 proves today's
    ``_synthesize_history_events`` cannot emit -- it can only derive 4 event
    types from message rows. The durable path must return it verbatim.
    """
    db = tmp_path / "events.db"
    store = RuntimeReadModelStore(event_store=EventStore(db_path=db), session_id="s1")
    store.append_event(_ev("permission.decided", {"tool": "shell", "decision": "allow"}))

    durable = store.read_events_since(after=0, limit=10)

    assert len(durable) == 1
    assert durable[0].name == "permission.decided"
    assert durable[0].payload == {"tool": "shell", "decision": "allow"}


def test_stale_cursor_is_served_not_clamped(tmp_path: Path) -> None:
    """The gap becomes impossible, not merely detectable (the T-30 vs T-75 line).

    A cursor older than the ring's 2000-entry tail is served from disk, never
    clamped to the ring's oldest surviving sequence.
    """
    db = tmp_path / "events.db"
    store = RuntimeReadModelStore(event_store=EventStore(db_path=db), session_id="s1")
    for i in range(2100):
        store.append_event(_ev("tool.call.completed", {"i": i}))

    result = store.read_events_since(after=5, limit=10)

    assert result[0].sequence == 6, "gap: a stale cursor was clamped instead of served from disk"


def test_streaming_deltas_are_not_persisted(tmp_path: Path) -> None:
    """Locks the deny-list (spec §5.4): the streaming firehose never reaches disk."""
    db = tmp_path / "events.db"
    event_store = EventStore(db_path=db)
    store = RuntimeReadModelStore(event_store=event_store, session_id="s1")

    store.append_event(_ev("text.stream.delta", {"text": "hi"}))
    store.append_event(_ev("tool.call.completed", {"name": "shell"}))

    rows = event_store.read_since("s1", after=0, limit=100)
    assert len(rows) == 1
    assert rows[0].name == "tool.call.completed"
    # Independent corroboration via the store's own frozen API: AUTOINCREMENT
    # only ever advanced once, so exactly one row was ever inserted -- not just
    # "exactly one row visible to this session_id".
    assert event_store.max_seq() == 1


def test_durable_attached_store_never_reports_a_base_sequence_gap(tmp_path: Path) -> None:
    """T-75 spec §3 item 4: ``oldest_sequence()`` must stay truthful once attached.

    It backs T-30's ``base_sequence`` gap signal (clients compare
    ``base_sequence > after + 1``). Left reporting the ring's oldest surviving
    sequence (101, after the 2000-entry trim below), it would keep raising a
    gap alarm for a gap that durable persistence has made structurally
    impossible -- a false alarm, not "stops indicating" (spec §9).
    """
    db = tmp_path / "events.db"
    store = RuntimeReadModelStore(event_store=EventStore(db_path=db), session_id="s1")
    for i in range(2100):
        store.append_event(_ev("tool.call.completed", {"i": i}))

    assert store.oldest_sequence() != 101
    assert store.oldest_sequence() == 0


# ---------------------------------------------------------------------------
# T-75 Stage D — the boot-window orphan: RuntimeReadModelStore is constructed
# with session_id="" (the real id is not known yet -- see boot_agent in
# orchestrator.py) and rebinds later via set_session_id(). Everything written
# before that rebind must still be reachable once the real id is known.
# ---------------------------------------------------------------------------


def test_events_emitted_before_the_session_id_is_known_are_not_orphaned(tmp_path: Path) -> None:
    """T-75 Stage D: the boot window must not orphan durably-written events.

    ``RuntimeReadModelStore`` is constructed (``orchestrator.py``'s ``boot_agent``)
    before the real session id exists -- ``SessionStore.start_session()`` assigns
    it later, and ``set_session_id`` rebinds this store to it then. Events emitted
    in between (eleven ``boot.progress`` events in production) must still be
    reachable through the real id once it binds, not stranded under whatever tag
    was live at write time.

    Must FAIL before the fix: pre-fix, boot-window events are tagged the shared
    constant ``session_id=""``, ``set_session_id`` never rebinds existing rows,
    and ``read_since`` filters by exact ``session_id`` equality -- so this
    returns 0 of the 3 events, not 3.
    """
    db = tmp_path / "events.db"
    store = RuntimeReadModelStore(event_store=EventStore(db_path=db))  # no session_id yet

    for i in range(3):
        store.append_event(_ev("boot.progress", {"stage": f"stage-{i}"}))

    store.set_session_id("s1")

    result = store.read_events_since(after=0, limit=100)

    assert len(result) == 3, "boot-window events were orphaned under the old session tag"
    assert [r.payload["stage"] for r in result] == ["stage-0", "stage-1", "stage-2"]


def test_rebind_does_not_capture_another_sessions_boot_events(tmp_path: Path) -> None:
    """The shortcut this fix must NOT be: ``UPDATE ... WHERE session_id = ''``.

    Several sessions share one process-wide ``EventStore`` (``get_event_store()``).
    Two ``RuntimeReadModelStore``s over the same store, both still in their boot
    window (no session id yet), must not collide -- binding one to a real id must
    never sweep up the other's still-unbound boot rows, no matter which one binds
    first.
    """
    db = tmp_path / "events.db"
    event_store = EventStore(db_path=db)
    store_a = RuntimeReadModelStore(event_store=event_store)  # boot window
    store_b = RuntimeReadModelStore(event_store=event_store)  # boot window, same process-wide store

    store_a.append_event(_ev("boot.progress", {"who": "a-1"}))
    store_b.append_event(_ev("boot.progress", {"who": "b-1"}))
    store_a.append_event(_ev("boot.progress", {"who": "a-2"}))
    store_b.append_event(_ev("boot.progress", {"who": "b-2"}))

    # "a" binds first -- the exact ordering that would let a `WHERE session_id
    # = ''` shortcut sweep b's still-unbound rows into session-a.
    store_a.set_session_id("session-a")
    store_b.set_session_id("session-b")

    a_events = store_a.read_events_since(after=0, limit=100)
    b_events = store_b.read_events_since(after=0, limit=100)

    assert [e.payload["who"] for e in a_events] == ["a-1", "a-2"]
    assert [e.payload["who"] for e in b_events] == ["b-1", "b-2"]


def test_set_session_id_is_idempotent(tmp_path: Path) -> None:
    """Calling ``set_session_id`` twice -- including with the same id twice --
    must neither duplicate rows nor lose them."""
    db = tmp_path / "events.db"
    store = RuntimeReadModelStore(event_store=EventStore(db_path=db))  # boot window

    store.append_event(_ev("boot.progress", {"stage": "boot-1"}))

    store.set_session_id("s1")
    store.set_session_id("s1")  # same id again -- must be a no-op, not a re-rebind

    after_first_bind = store.read_events_since(after=0, limit=100)
    assert [e.payload["stage"] for e in after_first_bind] == ["boot-1"]

    store.append_event(_ev("tool.call.completed", {"stage": "post-bind"}))
    store.set_session_id("s1")  # idempotent call after more writes under the real id

    final = store.read_events_since(after=0, limit=100)
    assert [e.payload["stage"] for e in final] == ["boot-1", "post-bind"]


def test_gap_signal_is_honest_after_the_boot_window_rebinds(tmp_path: Path) -> None:
    """T-30 consequence (spec §9): ``oldest_sequence() == 0`` must mean what it
    says -- that every event, including the boot window, is genuinely
    retrievable, not merely that the orphaned rows are invisible to the check."""
    db = tmp_path / "events.db"
    store = RuntimeReadModelStore(event_store=EventStore(db_path=db))  # boot window

    for i in range(3):
        store.append_event(_ev("boot.progress", {"stage": f"stage-{i}"}))
    store.set_session_id("s1")
    store.append_event(_ev("session.chat.started", {}))

    assert store.oldest_sequence() == 0, "no-gap signal"
    result = store.read_events_since(after=0, limit=100)
    assert len(result) == 4, "the 'no gap' claim must be backed by there being no gap"
