"""EventStore -- durable SQLite persistence for runtime events (T-75 Stage A).

Copies ``RunStateStore``'s shape (``src/hachi/core/runtime/store.py``) verbatim,
per T-75 spec §5.1 -- it is the only store in the repo with the full discipline
an events table needs: a single persistent connection, WAL, an ``RLock``, an
explicit transaction contextmanager, and indexes:

- Single persistent connection with RLock
- WAL journal mode
- Additive migrations via core.migrations.apply_migrations
- schema_version stamped via core.migrations.ensure_schema_version (T-54)

Nothing calls this yet. Stage B wires it into ``RuntimeReadModelStore``,
``gateway/orchestrator.py`` and ``gateway/control_plane.py``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hachi.core.migrations import apply_migrations, ensure_schema_version
from hachi.core.secret_scanner import REDACTION_PLACEHOLDER, redact_structure

logger = logging.getLogger(__name__)

_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    timestamp   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session_seq ON events (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_name        ON events (name);
"""
# AUTOINCREMENT is load-bearing: it is what makes `seq` monotonic *across
# restarts* (SQLite persists the highest-ever-used rowid for the table in its
# own internal `sqlite_sequence` bookkeeping), which is the entire point of
# this task -- do not "simplify" this to a plain INTEGER PRIMARY KEY.

# Migration list: (column, definition) pairs to add if missing.
# Use apply_migrations() for safe additive evolution.
_MIGRATIONS: list[tuple[str, str]] = [
    # Example future migration:
    # ("created_at", "TEXT"),
]

# Names deliberately NOT persisted: per-chunk streaming noise whose content is
# already durable as a message row in SessionStore. Deny-list, not allow-list
# (T-75 spec §5.4): a deny-list fails *safe* -- a newly added event name is
# persisted by default -- an allow-list fails *silent*, which is the CF-1
# pattern this audit exists to stop. Mirrors the shape of `_SPAN_EVENTS` at
# `src/hachi/core/obs/sink.py:37-49` (that one is an allow-list for a
# different purpose -- span-attribute stamping -- only the module-level
# frozenset shape is being mirrored here, not its allow/deny direction).
_EPHEMERAL_EVENTS: frozenset[str] = frozenset({"text.stream.delta", "thinking.step"})

# Payload keys whose VALUE is a credential by construction, not by pattern.
# redact_structure() (applied in EventStore._redact_payload, below) is the
# general, fail-safe layer: it scrubs any string leaf matching one of the 13
# vendor prefixes in core/secret_scanner.py, which covers API keys echoed
# into str(exception) text. It cannot help here -- `wait_key` for
# wait_kind="webhook" is the token used to RESUME a suspended run
# (gateway/orchestrator.py:1449, tools/workflow_tools.py:56-62) and carries no
# recognisable prefix, so no pattern will ever match it. This list is
# additive defence for that provable blind spot, NOT a replacement for the
# matcher.
_SENSITIVE_PAYLOAD_KEYS: frozenset[str] = frozenset({"wait_key"})


def _default_db_path() -> Path:
    from hachi.core.paths import get_hachi_paths

    db_path: Path = get_hachi_paths().events_db
    return db_path


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One durable row read back from the ``events`` table for a single session.

    Field names deliberately mirror ``RuntimeEventSnapshot``
    (``core/runtime_read_models.py``) -- ``name``, ``payload``, ``timestamp``,
    ``sequence`` -- so Stage B can construct one from the other without a
    manual remap. ``session_id`` is intentionally omitted: ``read_since`` is
    already scoped to one ``session_id``, so it would be redundant on every
    row -- consistent with ``RuntimeEvent`` itself, which also carries no
    ``session_id`` (T-75 spec §5.3).
    """

    name: str
    payload: dict[str, Any]
    timestamp: str
    sequence: int


class EventStore:
    """SQLite-backed durable log for runtime events.

    Thread-safe via ``RLock``. Single persistent connection (WAL mode). One
    ``EventStore`` per process, not per event and not per request (BB-42) --
    events are appended from the agent's worker thread while the gateway
    reads them from a request thread, and a store opened per call would hold
    ``*.db-wal``/``*.db-shm`` handles open indefinitely on Windows.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """
        Args:
            db_path: Path to the SQLite database file.
                     Defaults to ``<hachi-state-dir>/events.db``.
        """
        self._db_path = db_path or _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self._db_path), timeout=10, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    @contextmanager
    def _db(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_EVENTS_SCHEMA)
            self._conn.commit()
            # T-54: stamp/verify schema_version before any migration -- same
            # connection, same lock, see core/migrations.py.
            ensure_schema_version(self._conn, current=1)
            apply_migrations(self._conn, "events", _MIGRATIONS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, session_id: str, name: str, payload: dict[str, Any], timestamp: str) -> int:
        """Persist one event and return its assigned ``seq``.

        ``seq`` is the durable sequence authority: global (not per-session),
        monotonic across restarts (T-75 spec §5.2).

        Names in :data:`_EPHEMERAL_EVENTS` are deliberately not written -- the
        deny-list choke point lives here, not at any call site, so a future
        emit site cannot bypass it. Returns ``0`` for a denied name: a falsy
        sentinel that never collides with a real row, since AUTOINCREMENT
        ``seq`` values start at 1.

        ``payload`` is redacted (see :meth:`_redact_payload`) before it is
        serialized to disk. ``session_id``, ``name`` and ``timestamp`` are
        structural, not content -- they are written verbatim so Stage B's
        sequencing and filtering keep working. Only the durable copy is
        scrubbed: redaction happens here, at the store boundary, not at the
        event's emit site, so the in-memory ``RuntimeEvent``, the TUI
        transcript and the gateway SSE stream are all unaffected by this
        method.
        """
        if name in _EPHEMERAL_EVENTS:
            return 0
        safe_payload = self._redact_payload(payload)
        with self._db() as conn:
            cursor = conn.execute(
                "INSERT INTO events (session_id, name, payload, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, name, json.dumps(safe_payload), timestamp),
            )
            seq = cursor.lastrowid
        if seq is None:  # pragma: no cover -- unreachable for a successful INSERT
            raise RuntimeError("EventStore.append: INSERT did not return a rowid")
        return seq

    def read_since(self, session_id: str, after: int, limit: int) -> list[EventRecord]:
        """Events for ``session_id`` with ``seq > after``, oldest to newest, capped at ``limit``.

        Two sessions' events never bleed into each other -- ``session_id`` is
        an equality filter, not a prefix or best-effort match.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT seq, name, payload, timestamp
                FROM events
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, after, limit),
            ).fetchall()
        return [
            EventRecord(
                name=row["name"],
                payload=self._safe_loads(row["payload"], int(row["seq"])),
                timestamp=row["timestamp"],
                sequence=int(row["seq"]),
            )
            for row in rows
        ]

    def rebind_session(self, old_session_id: str, new_session_id: str) -> int:
        """Retag every row currently tagged ``old_session_id`` as ``new_session_id``.

        T-75 Stage D: closes the boot-window orphan. ``RuntimeReadModelStore``
        is constructed before the real session id exists (``orchestrator.py``'s
        ``boot_agent``) and tags its earliest writes with a per-instance
        placeholder instead (``core/runtime_read_models.py``'s ``__init__``);
        once the real id is known, :meth:`RuntimeReadModelStore.set_session_id`
        calls this to reattach those rows.

        ``session_id`` is matched by exact equality here, the same discipline
        as :meth:`read_since` -- never a prefix or ``LIKE`` match. Safety
        depends entirely on the caller passing an ``old_session_id`` that could
        only ever belong to ONE session's rows -- e.g. this store's own unique
        per-instance placeholder. Do **not** call this with a value shared
        across instances, such as the empty string the placeholder replaces:
        several sessions share one process-wide ``EventStore``
        (:func:`get_event_store`), so ``UPDATE ... WHERE session_id = ''``
        would rebind *other concurrent sessions'* still-unbound boot rows into
        whichever session happened to bind first. That shared-tag shortcut is
        the bug this method exists to avoid, not a valid way to call it.

        Returns the number of rows rebound (0 if ``old_session_id`` had none,
        including when ``old_session_id == new_session_id``).
        """
        with self._db() as conn:
            cursor = conn.execute(
                "UPDATE events SET session_id = ? WHERE session_id = ?",
                (new_session_id, old_session_id),
            )
            return int(cursor.rowcount)

    def max_seq(self) -> int:
        """The highest assigned ``seq`` across all sessions, or ``0`` when empty.

        Global, not per-session (T-75 spec §5.2) -- Stage B seeds the
        in-memory sequence counter from this so it continues rather than
        restarting at 0 after a process restart.
        """
        with self._lock:
            row = self._conn.execute("SELECT MAX(seq) FROM events").fetchone()
        value = row[0] if row is not None else None
        return int(value) if value is not None else 0

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Scrub ``payload`` before it is written to disk. Two layers, in order:

        1. ``redact_structure`` -- general, pattern-based. Recursively scrubs every
           string leaf anywhere in the structure that matches one of the 13
           vendor-prefix secret shapes in ``core/secret_scanner.py``. This is the
           realistic leak vector for the six event names that embed raw
           ``str(exception)`` text (``llm.retry``, ``job.retry``,
           ``job.dead_letter``, ``job.failed``, ``mcp.server.failed``,
           ``routing.boot_failed``): provider SDKs routinely echo the failing
           request, auth headers included, into exception text.
        2. :data:`_SENSITIVE_PAYLOAD_KEYS` -- narrow, name-based. Layer 1 is a
           prefix matcher, not a secret detector: it cannot see a credential
           with no recognisable vendor prefix, e.g. ``run.suspended``'s
           ``wait_key`` (the token used to resume that run). This layer
           replaces those top-level values outright, regardless of shape, as
           additive defence for that provable blind spot -- not a replacement
           for layer 1.

        Only the durable (SQLite) copy is scrubbed. This runs inside
        :meth:`append`, at the store boundary -- not at the event's emit site
        -- so the in-memory ``RuntimeEvent``, the TUI transcript and the
        gateway SSE stream are all unaffected by this method.
        """
        scrubbed = redact_structure(payload)
        redacted: dict[str, Any] = scrubbed if isinstance(scrubbed, dict) else {}
        for key in _SENSITIVE_PAYLOAD_KEYS:
            if key in redacted:
                redacted[key] = REDACTION_PLACEHOLDER
        return redacted

    @staticmethod
    def _safe_loads(raw: str, seq: int) -> dict[str, Any]:
        """Decode one row's ``payload`` column.

        An audit log must not fail open: a row that failed to decode used to
        silently degrade to ``{}`` with no trace at all -- the row survives
        with its name, timestamp and sequence intact while its content quietly
        disappears, and nobody ever finds out. ``{}`` is still returned on
        failure (raising would take down a whole ``read_since`` call over one
        bad row), but the failure is now loud: logged with ``seq`` so the
        damaged row is identifiable.
        """
        try:
            loaded = json.loads(raw)
        except Exception:
            logger.warning(
                "EventStore: payload for seq=%s failed to decode as JSON", seq, exc_info=True
            )
            return {}
        if not isinstance(loaded, dict):
            logger.warning(
                "EventStore: payload for seq=%s decoded to %s, not an object",
                seq,
                type(loaded).__name__,
            )
            return {}
        return loaded
