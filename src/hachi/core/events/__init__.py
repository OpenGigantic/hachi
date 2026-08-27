"""Durable runtime-event log (T-75 Stage A + Stage B).

A ``EventStore`` writes runtime events to a SQLite ``events`` table so they
survive a process restart, unlike ``RuntimeReadModelStore``'s in-memory ring
(``core/runtime_read_models.py``). Stage B (this module's ``get_event_store``)
threads it into ``RuntimeReadModelStore`` / ``orchestrator.py`` /
``control_plane.py`` -- see
``docs/audit/2026-07-19/tasks/T-75-durable-event-log.md`` §1.

Public API::

    from hachi.core.events import EventRecord, EventStore, get_event_store
"""

from __future__ import annotations

import threading
from pathlib import Path

from hachi.core.events.store import EventRecord, EventStore

__all__ = [
    "EventRecord",
    "EventStore",
    "get_event_store",
]

# Process-wide singleton -- one EventStore per process, not per request or
# per event (BB-42: a store opened per call holds *.db-wal/*.db-shm handles
# open indefinitely on Windows). Mirrors get_wait_registry()
# (core/runtime/waits.py:100-107): module-global, threading.Lock,
# double-checked `if _x is None` inside and outside the lock -- extended here
# (T-75 Stage E) with `_event_store_path`, the resolved path the cached store
# was opened against, so the singleton can detect a repointed state dir
# instead of outliving it. Single slot, not a dict keyed by path: a per-path
# cache would accumulate one open SQLite connection per repoint (the BB-42
# leak in a new costume), and production never repoints this path anyway.
_event_store: EventStore | None = None
_event_store_path: Path | None = None
_event_store_lock = threading.Lock()


def get_event_store() -> EventStore:
    """Return the process-wide EventStore singleton.

    ``get_hachi_paths().events_db`` is re-resolved on *every* call and
    compared against the path the cached store was last opened against; a
    mismatch rebuilds the store. In production that path never changes for
    the life of the process, so this comparison is a no-op and the function
    behaves exactly like an unconditional cache -- do NOT "optimise" the
    check away, it is load-bearing for the case below, not dead weight.

    Without it, a test that isolates a real boot by patching
    ``get_hachi_paths`` to a temp directory (``test_boot_agent_stages.py``,
    ``test_feature_liveness.py``) permanently pinned the singleton to that
    patched path: the patch unwinds and the temp directory is deleted, but
    the singleton kept its open SQLite handle and every later ``append()``
    kept reporting success against an orphaned file -- silently, no
    exception raised (T-75 Stage E). That is the exact fail-open pattern
    this task exists to eliminate, so the fix is a path check here, not a
    try/except around the write-through in ``EventStore.append``.

    The path is resolved exactly once per call -- not once for this
    comparison and again inside ``EventStore.__init__`` -- and passed
    explicitly as ``db_path=`` so there is no window for the two resolutions
    to disagree.

    Closing the superseded store while another thread might still hold a
    reference to it (e.g. a ``RuntimeReadModelStore`` built from the store
    before it was repointed) is a real but accepted edge: production never
    repoints this path, and the alternative -- leaking a handle per repoint
    -- is the BB-42 leak this store exists to avoid, so this does not track
    refcounts to close more carefully.
    """
    global _event_store, _event_store_path
    from hachi.core.paths import get_hachi_paths  # local: re-resolved fresh on every call

    resolved_path = get_hachi_paths().events_db
    # The comparison below is the whole fix -- see the docstring.
    if _event_store is None or _event_store_path != resolved_path:
        with _event_store_lock:
            if _event_store is None or _event_store_path != resolved_path:
                previous = _event_store
                _event_store = EventStore(db_path=resolved_path)
                _event_store_path = resolved_path
                if previous is not None:
                    previous.close()
    return _event_store
