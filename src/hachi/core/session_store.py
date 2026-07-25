"""
Session Persistence Store

Lightweight SQLite backend that persists every session's messages, tool calls,
and metadata for cross-session search, analytics, and audit trails.

Schema:
    sessions: id, started_at, ended_at, model, total_turns, title
    messages: id, session_id, role, content, tool_name, timestamp

Thread-safety is handled via a reentrant lock around all writes.
Reads use WAL mode for concurrent non-blocking access.
"""

import datetime
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from hachi.core.migrations import apply_migrations, ensure_schema_version
from hachi.core.paths import get_hachi_paths

logger = logging.getLogger(__name__)

# T-52: the role a compaction-checkpoint row is persisted under. Deliberately
# distinct from "user"/"assistant" — a persisted "user" row would be
# indistinguishable from something a human actually typed, and would render as
# one in GET /sessions/{id}/messages and `hachi session show`. The in-memory
# Message MemoryManager splices into _history stays role="user" (that disguise
# is an LLM-API concern — see MemoryManager._sliding_window_compact); this
# constant is the separate, honest-about-provenance persisted role. `role` has
# no CHECK constraint (see the messages table below), so nothing else stops a
# typo — every write/update/resume-filter site must import and use this
# constant rather than the literal string.
CHECKPOINT_ROLE = "system"


class SessionStore:
    """SQLite-backed session persistence for cross-session search and analytics.

    Designed for append-heavy workloads with minimal read contention.
    Uses WAL journal mode so background log_message calls never block
    the main agent loop's reads.

    T-52: this store is the single durable transcript of a session.
    ``MemoryManager._history`` is only the live working set sent to the LLM this
    turn (including a compaction checkpoint — see :data:`CHECKPOINT_ROLE` — which
    does not survive a restart unless it is also written here); ``.og`` memory is
    extracted, structured facts. If a message is not written to this store, it is
    gone once the process exits.

    Write batching (MED-001): ``log_message`` accumulates rows in memory and
    flushes to SQLite either when ``_BATCH_SIZE`` rows are pending or after
    ``_BATCH_INTERVAL_S`` seconds, whichever comes first.  A background timer
    thread fires the interval flush.  Call ``flush_sync()`` for an immediate
    guaranteed flush (e.g. before ``end_session``).
    """

    _BATCH_SIZE: int = 20
    _BATCH_INTERVAL_S: float = 2.0

    def __init__(self, db_path: str | None = None):
        """Initialize the session store.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to OS-native Hachi state directory.
        """
        if not db_path:
            db_path = str(get_hachi_paths().sessions_db)

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Wait up to 5s for a competing writer instead of immediately raising
        # "database is locked" — multiple sessions open separate connections to
        # the same sessions.db.
        self._conn.execute("PRAGMA busy_timeout=5000")

        # MED-001: commit-batching. Rows are INSERTed synchronously (so foreign-key
        # violations raise immediately and same-connection reads see the row and its
        # truncation); only the COMMIT is deferred to amortise disk-sync overhead.
        self._uncommitted: int = 0
        self._last_flush: float = time.monotonic()
        self._flush_timer: threading.Timer | None = None

        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                started_at  TEXT NOT NULL,
                ended_at    TEXT,
                model       TEXT,
                total_turns INTEGER DEFAULT 0,
                title       TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT,
                tool_name   TEXT,
                timestamp   TEXT NOT NULL,
                model       TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_msg_session_id
                ON messages(session_id, id);
            -- idx_msg_content removed: leading-wildcard LIKE cannot use a B-tree index.

            -- Daily spend ledger: one row per calendar date (ISO, e.g. "2026-06-22").
            -- Rows are only created when spend is first recorded for a date, so a
            -- missing row is equivalent to 0.0.  Rollover is automatic — a new local
            -- date produces a fresh row without any explicit reset logic.
            CREATE TABLE IF NOT EXISTS daily_spend (
                date        TEXT PRIMARY KEY,
                total_cost_usd  REAL NOT NULL DEFAULT 0.0,
                updated_at  REAL NOT NULL
            );

            -- Ingress idempotency (AUDIT P0-3): maps a client-supplied Idempotency-Key to
            -- the COMPLETED turn response, so a retried / reconnected / double-clicked
            -- POST /messages replays the stored result instead of re-running the whole turn
            -- (no double spend, no duplicate side effects). Scoped per session; first write
            -- wins. Durable, so it also survives a gateway restart between the two POSTs.
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                session_id     TEXT NOT NULL,
                idem_key       TEXT NOT NULL,
                response_json  TEXT NOT NULL,
                created_at     REAL NOT NULL,
                PRIMARY KEY (session_id, idem_key)
            );

            -- Per-interaction eval/audit log (AUDIT P0-2): one row per completed turn — the
            -- row-level ground truth that unlocks good/bad rating, failure mining, per-turn
            -- audit, and output-regression eval. Written best-effort (never blocks a turn).
            -- tool_calls / memory_hits are JSON arrays; rating + notes are filled later via a
            -- /review surface (NULL until then).
            CREATE TABLE IF NOT EXISTS interaction_log (
                id            TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL,
                turn_id       TEXT,
                ts            TEXT NOT NULL,
                user_input    TEXT,
                agent_output  TEXT,
                tool_calls    TEXT,
                memory_hits   TEXT,
                model_used    TEXT,
                token_cost    REAL,
                tokens        INTEGER,
                rating        TEXT,
                notes         TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_interaction_session
                ON interaction_log(session_id);
        """)
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Stamp/verify the schema version, then apply migrations for older databases.

        ``CREATE TABLE IF NOT EXISTS`` leaves a pre-existing ``sessions`` table
        untouched, so a column added to the definition above never reaches an
        already-created DB. Adding it explicitly (idempotent) keeps old stores
        forward-compatible.
        """
        # T-54: stamp/verify schema_version FIRST, before any migration runs, so a
        # database written by a newer build fails loudly here instead of being
        # silently altered by this (older) build's migration list. SessionStore is
        # the sole owner of this version row — sessions.db also has a second,
        # independent schema author (CorrectionLog -> profile_corrections in
        # core/user_profile.py, out of scope for this task) that does not
        # participate in it (see docs/DECISIONS.md, T-54 ADR).
        ensure_schema_version(self._conn, current=1)

        apply_migrations(
            self._conn,
            "sessions",
            [
                ("title", "TEXT"),
                ("total_tokens", "INTEGER DEFAULT 0"),
                ("total_cost_usd", "REAL DEFAULT 0.0"),
            ],
        )
        # Bug 6: messages.model column was added after initial release.
        apply_migrations(self._conn, "messages", [("model", "TEXT")])

        # B8: replace single-column idx_msg_session with composite (session_id, id).
        # The composite covers both "all messages for a session" and "messages after
        # cursor X in a session" without write-amplification from two overlapping indexes.
        indexes = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'"
            )
        }
        if "idx_msg_session" in indexes:
            self._conn.execute("DROP INDEX idx_msg_session")
            self._conn.commit()
        if "idx_msg_session_id" not in indexes:
            self._conn.execute("CREATE INDEX idx_msg_session_id ON messages(session_id, id)")
            self._conn.commit()
        # NOTE(issue12): no automatic ghost-cleanup runs here. Silently deleting
        # message-less session rows on every DB open is dangerous (it would also
        # remove legacy rows a caller still expects). Cleanup is explicit instead —
        # via delete_empty_sessions(), the POST /sessions/cleanup-empty route, and
        # `hachi doctor`.

    # ==========================================
    # Session Lifecycle
    # ==========================================

    def start_session(self, model: str = "unknown") -> str:
        """Begin a new session and return its unique ID.

        The ``sessions`` row is inserted and committed synchronously so the
        foreign-key constraint holds for subsequent message writes and the row is
        immediately visible to other connections (callers read session state right
        after creation, e.g. per-session cost recording).

        Ghost (zero-message) sessions are NOT prevented by deferring this insert —
        deferral removed FK-integrity and synchronous-commit guarantees that other
        code and tests rely on. Instead, empty sessions are hidden from the UI by
        :meth:`get_recent_sessions` and removed on demand by
        :meth:`delete_empty_sessions` (Issue 12; rationale in DECISIONS.md).

        Args:
            model: The LLM model name used for this session.

        Returns:
            A UUID string identifying this session.
        """
        session_id = uuid.uuid4().hex[:16]
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, started_at, model) VALUES (?, ?, ?)",
                (session_id, now, model),
            )
            self._conn.commit()
        logger.debug("Session started: %s (model=%s)", session_id, model)
        return session_id

    def end_session(self, session_id: str) -> None:
        """Mark a session as ended and record the final turn count.

        Flushes any pending message writes first so the turn count is accurate.

        Args:
            session_id: The session to close.
        """
        self.flush_sync()
        now = datetime.datetime.now(datetime.UTC).isoformat()

        with self._lock:
            # Count total turns (user messages only)
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                (session_id,),
            ).fetchone()
            turns = row[0] if row else 0

            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, total_turns = ? WHERE id = ?",
                (now, turns, session_id),
            )
            self._conn.commit()

        logger.debug("Session ended: %s (%d turns)", session_id, turns)

    def set_session_title(self, session_id: str, title: str) -> None:
        """Set (or overwrite) the human-readable title for a session.

        Args:
            session_id: The session to title.
            title: The display title. Stored verbatim; callers are responsible
                for length-capping and cleaning (see ``core.session_titler``).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            self._conn.commit()

        logger.debug("Session titled: %s -> %r", session_id, title)

    def record_session_cost(
        self, session_id: str, total_tokens: int, total_cost_usd: float
    ) -> None:
        """Persist cumulative token and cost totals for a session.

        Called after each turn so the values stay up-to-date even if the
        process crashes before :meth:`end_session` is called.

        Args:
            session_id: The session to update.
            total_tokens: Cumulative token count for the session so far.
            total_cost_usd: Cumulative estimated cost in US dollars.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET total_tokens = ?, total_cost_usd = ? WHERE id = ?",
                (total_tokens, total_cost_usd, session_id),
            )
            self._conn.commit()

        logger.debug(
            "Session cost recorded: %s — %d tokens / $%.6f",
            session_id,
            total_tokens,
            total_cost_usd,
        )

    def record_daily_cost(self, date_str: str, delta_usd: float) -> None:
        """Atomically add *delta_usd* to the running daily spend total for *date_str*.

        The ledger row is created on first access (INSERT OR IGNORE) and then
        incremented (UPDATE … = total_cost_usd + ?) inside a single transaction so
        concurrent writers never lose a write.

        Args:
            date_str: ISO date string, e.g. ``"2026-06-22"`` (from
                ``datetime.date.today().isoformat()``).
            delta_usd: The per-turn cost increment (positive float, USD).  Zero or
                negative values are silently ignored to guard against accounting glitches.
        """
        if delta_usd <= 0:
            return
        now_ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO daily_spend (date, total_cost_usd, updated_at) "
                "VALUES (?, 0.0, ?)",
                (date_str, now_ts),
            )
            self._conn.execute(
                "UPDATE daily_spend SET total_cost_usd = total_cost_usd + ?, updated_at = ? "
                "WHERE date = ?",
                (delta_usd, now_ts, date_str),
            )
            self._conn.commit()
        logger.debug("Daily spend: +$%.6f → %s", delta_usd, date_str)

    def get_daily_spend(self, date_str: str) -> float:
        """Return the accumulated spend in USD for *date_str*, or 0.0 if no row exists.

        Args:
            date_str: ISO date string, e.g. ``"2026-06-22"``.

        Returns:
            Cumulative spend for the requested date in US dollars.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT total_cost_usd FROM daily_spend WHERE date = ?",
                (date_str,),
            ).fetchone()
        if row is None:
            return 0.0
        val: float = row["total_cost_usd"] or 0.0
        return val

    # ------------------------------------------------------------------
    # Ingress idempotency (AUDIT P0-3 / Theme A)
    # ------------------------------------------------------------------

    def get_idempotent_response(self, session_id: str, idem_key: str) -> dict[str, Any] | None:
        """Return the stored response for a prior completed turn with this key, or None.

        Used by ``POST /sessions/{id}/messages`` to replay the result of a duplicate
        request (client retry / reconnect / double-click) instead of re-running the turn.

        Args:
            session_id: The session the request targets.
            idem_key: The client-supplied ``Idempotency-Key``.

        Returns:
            The previously stored response dict, or ``None`` if this key is unseen (or the
            stored payload is unreadable — in which case the turn is safely re-run).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json FROM idempotency_keys WHERE session_id = ? AND idem_key = ?",
                (session_id, idem_key),
            ).fetchone()
        if row is None:
            return None
        try:
            data: dict[str, Any] = json.loads(row["response_json"])
        except (ValueError, TypeError):
            return None
        return data

    def save_idempotent_response(
        self, session_id: str, idem_key: str, response: dict[str, Any]
    ) -> None:
        """Persist a completed turn's response under ``(session_id, idem_key)`` for replay.

        ``INSERT OR IGNORE`` makes this first-write-wins: if two concurrent duplicates both
        complete, the first stored response is the one that replays — never a mix.
        """
        payload = json.dumps(response)
        now_ts = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys "
                "(session_id, idem_key, response_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, idem_key, payload, now_ts),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Interaction log — per-turn eval/audit ground truth (AUDIT P0-2)
    # ------------------------------------------------------------------

    def log_interaction(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        user_input: str,
        agent_output: str,
        tool_calls: list[str] | None = None,
        memory_hits: list[str] | None = None,
        model_used: str | None = None,
        token_cost: float | None = None,
        tokens: int | None = None,
        rating: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Append one ``interaction_log`` row (one completed turn). Returns the new row id.

        This is the keystone of the eval loop: a durable, queryable record of what the user
        asked, what Hachi answered, which tools/memories it used, the model, and the per-turn
        cost — so turns can be rated, mined for failures, audited, and used as a regression
        baseline. ``tool_calls`` and ``memory_hits`` are stored as JSON arrays.
        """
        row_id = str(uuid.uuid4())
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO interaction_log (id, session_id, turn_id, ts, user_input, "
                "agent_output, tool_calls, memory_hits, model_used, token_cost, tokens, "
                "rating, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    session_id,
                    turn_id,
                    ts,
                    user_input,
                    agent_output,
                    json.dumps(tool_calls or []),
                    json.dumps(memory_hits or []),
                    model_used,
                    token_cost,
                    tokens,
                    rating,
                    notes,
                ),
            )
            self._conn.commit()
        return row_id

    def get_interactions(
        self, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return recent ``interaction_log`` rows (newest first), optionally per session.

        ``tool_calls`` / ``memory_hits`` are decoded back into lists. Powers a future
        ``/review`` surface and trajectory-eval suite.
        """
        with self._lock:
            if session_id is not None:
                rows = self._conn.execute(
                    "SELECT * FROM interaction_log WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM interaction_log ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(row)
            rec["tool_calls"] = json.loads(rec["tool_calls"]) if rec.get("tool_calls") else []
            rec["memory_hits"] = json.loads(rec["memory_hits"]) if rec.get("memory_hits") else []
            out.append(rec)
        return out

    def set_interaction_rating(
        self, interaction_id: str, rating: str, notes: str | None = None
    ) -> bool:
        """Set a turn's ``rating`` (and optionally ``notes``); returns True if a row matched.

        The write side of a thumbs-up/down review. ``notes`` is only overwritten when a
        non-None value is supplied (``COALESCE``), so rating and notes can be set separately.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE interaction_log SET rating = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (rating, notes, interaction_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ==========================================
    # Retention / Pruning (BB-72)
    # ==========================================

    def cleanup_idempotency_keys(self, older_than_hours: int = 48) -> int:
        """Delete idempotency_keys rows created more than ``older_than_hours`` ago.

        The replay window for a POST /messages retry is at most a few minutes;
        48 hours is extremely conservative.  Returns the number of rows deleted.
        """
        cutoff = time.time() - older_than_hours * 3600
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM idempotency_keys WHERE created_at < ?",
                (cutoff,),
            )
            self._conn.commit()
            count = int(cur.rowcount)
        logger.debug("SessionStore: pruned %d idempotency key(s)", count)
        return count

    def cleanup_interaction_log(self, older_than_days: int = 90) -> int:
        """Delete interaction_log rows older than ``older_than_days`` days.

        The eval/audit trail is retained for 90 days by default.  Returns the
        number of rows deleted.
        """
        cutoff = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=older_than_days)
        ).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM interaction_log WHERE ts < ?",
                (cutoff,),
            )
            self._conn.commit()
            count = int(cur.rowcount)
        logger.debug("SessionStore: pruned %d interaction_log row(s)", count)
        return count

    def cleanup_daily_spend(self, older_than_days: int = 365) -> int:
        """Delete daily_spend rows for dates older than ``older_than_days`` days.

        A year of spend history (default) is enough for tax/billing purposes.
        Returns the number of rows deleted.
        """
        cutoff = (
            (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=older_than_days))
            .date()
            .isoformat()
        )
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM daily_spend WHERE date < ?",
                (cutoff,),
            )
            self._conn.commit()
            count = int(cur.rowcount)
        logger.debug("SessionStore: pruned %d daily_spend row(s)", count)
        return count

    def get_sessions_missing_title(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Return sessions that have no title yet (oldest first), for backfill.

        A session with at least one user message is eligible; empty sessions are
        skipped since there is nothing to title.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            List of dicts with id, started_at, model.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, model FROM sessions "
                "WHERE title IS NULL OR title = '' "
                "ORDER BY started_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ==========================================
    # Message Logging
    # ==========================================

    def log_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        model: str | None = None,
    ) -> None:
        """Insert a message row synchronously; batch only the COMMIT.

        Called after every user input and assistant response.  The INSERT runs
        immediately (so foreign-key violations raise here and same-connection reads
        see the row + 50K truncation), while the COMMIT is deferred (MED-001) and
        fired by ``_BATCH_SIZE`` rows, the ``_BATCH_INTERVAL_S`` timer, or an explicit
        :meth:`flush_sync` — whichever comes first.

        Args:
            session_id: The active session ID.
            role: Message role — 'user', 'assistant', or 'tool'.
            content: The message text. Truncated to 50K chars to bound DB growth.
            tool_name: Optional tool name if this is a tool call/result.
            model: LLM model id used to generate this message (assistant rows only).
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        # Truncate very large tool outputs to prevent DB bloat.
        content = content[:50_000] if content else ""

        with self._lock:
            # Synchronous INSERT: an unknown session_id raises immediately (foreign-key
            # enforcement — start_session must have created the row) and same-connection
            # reads see the row without a flush.
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, timestamp, model) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, tool_name, now, model),
            )
            self._uncommitted += 1
            self._schedule_flush()
            if self._uncommitted >= self._BATCH_SIZE:
                self._flush_locked()

    def _schedule_flush(self) -> None:
        """Arm the interval flush timer if it isn't already running (lock must be held)."""
        if self._flush_timer is None or not self._flush_timer.is_alive():
            self._flush_timer = threading.Timer(self._BATCH_INTERVAL_S, self._timer_flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _timer_flush(self) -> None:
        """Periodic commit triggered by the background timer."""
        with self._lock:
            try:
                self._flush_locked()
            except Exception:
                logger.exception("SessionStore: timed commit failed")

    def _flush_locked(self) -> None:
        """Commit any rows inserted since the last commit (lock must be held)."""
        if getattr(self, "_uncommitted", 0) == 0:
            return
        self._conn.commit()
        self._uncommitted = 0
        self._last_flush = time.monotonic()

    def flush_sync(self) -> None:
        """Immediately flush all pending message writes to SQLite.

        Call before reading message data that must include recently buffered writes
        (e.g. before ``end_session`` or when performing cross-session search after
        a very recent write).
        """
        with self._lock:
            self._flush_locked()

    # ==========================================
    # T-52: compaction-checkpoint persistence
    # ==========================================
    #
    # A checkpoint is the summary MemoryManager splices into the live _history in
    # place of messages dropped by sliding-window compaction (see
    # MemoryManager._sliding_window_compact). Without these two methods it existed
    # only in that process's Python list — a resumed session silently lost the
    # compacted middle of the conversation (BB-140). The messages table stays
    # append-only for every other caller; update_message_content is the one
    # narrow, single-column exception, added solely to converge a checkpoint row
    # onto the real background summary once it lands.

    def log_checkpoint(
        self,
        session_id: str,
        content: str,
        model: str | None = None,
    ) -> int:
        """Insert a compaction-checkpoint row and return its row id.

        Persisted under :data:`CHECKPOINT_ROLE` — never ``"user"`` or
        ``"assistant"`` — so a resumed session's transcript, ``GET
        /sessions/{id}/messages``, and ``hachi session show`` can all tell it
        apart from something a human actually typed. The returned id lets the
        caller update this exact row in place via :meth:`update_message_content`
        once the real background summary lands, mirroring the in-memory
        sentinel apply-back in ``MemoryManager._apply_summary_to_history``.

        Mirrors ``log_message``'s synchronous-INSERT / batched-COMMIT contract
        (see its docstring) rather than delegating to it, so ``log_message``'s
        widely-used two-argument-role contract is untouched by this task.

        Args:
            session_id: The active session ID.
            content: The checkpoint text — the placeholder or, on the no-queue
                synchronous path, the real summary already. Truncated to 50K
                chars like ``log_message``, to bound DB growth.
            model: LLM model id, for parity with ``log_message``'s assistant rows.

        Returns:
            The autoincrement row id of the inserted message.
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        content = content[:50_000] if content else ""

        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, timestamp, model) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, CHECKPOINT_ROLE, content, None, now, model),
            )
            self._uncommitted += 1
            self._schedule_flush()
            if self._uncommitted >= self._BATCH_SIZE:
                self._flush_locked()
            row_id = cursor.lastrowid
            if row_id is None:
                # Unreachable in practice — a successful INSERT into an
                # AUTOINCREMENT primary key always sets lastrowid — but the
                # sqlite3 stubs type it as int | None, so guard explicitly and
                # fail loudly rather than silently return a wrong id.
                raise RuntimeError("log_checkpoint: INSERT did not return a row id")
            return row_id

    def update_message_content(self, message_id: int, content: str) -> None:
        """Overwrite a single message row's content in place.

        The narrowest possible exception to the messages table being append-only:
        one column (``content``), one row (``message_id``), one caller
        (``MemoryManager._apply_summary_to_history``, converging a checkpoint
        logged by :meth:`log_checkpoint` onto the real background summary).  Not
        a general message-mutation API — do not grow its surface.

        Best-effort: silently no-ops if ``message_id`` no longer exists (e.g. the
        session was deleted between the checkpoint being logged and the
        background job completing).  Batches its COMMIT exactly like
        ``log_message`` — call :meth:`flush_sync` for an immediate flush.

        Args:
            message_id: The row id returned by :meth:`log_checkpoint`.
            content: The replacement content. Truncated to 50K chars like
                ``log_message``, for the same reason.
        """
        content = content[:50_000] if content else ""

        with self._lock:
            self._conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, message_id),
            )
            self._uncommitted += 1
            self._schedule_flush()
            if self._uncommitted >= self._BATCH_SIZE:
                self._flush_locked()

    # ==========================================
    # Cross-Session Search
    # ==========================================

    def search_sessions(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Full-text search across all session messages.

        Uses SQLite LIKE for simplicity — sufficient for the expected data volumes
        (~1000s of sessions). Searches message content and returns the session
        context around each match.

        Args:
            query: The search term (case-insensitive substring match).
            limit: Maximum number of results to return.

        Returns:
            List of dicts with session_id, role, content snippet, timestamp.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.session_id, m.role, m.content, m.timestamp, s.started_at, s.model "
                "FROM messages m "
                "JOIN sessions s ON m.session_id = s.id "
                "WHERE m.content LIKE ? "
                "ORDER BY m.timestamp DESC "
                "LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()

        results = []
        for row in rows:
            content = row["content"] or ""
            # Find the query position and extract a surrounding snippet
            lower = content.lower()
            pos = lower.find(query.lower())
            start = max(0, pos - 80)
            end = min(len(content), pos + len(query) + 80)
            snippet = content[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(content):
                snippet = snippet + "..."

            results.append(
                {
                    "session_id": row["session_id"],
                    "session_date": row["started_at"],
                    "model": row["model"],
                    "role": row["role"],
                    "snippet": snippet,
                    "timestamp": row["timestamp"],
                }
            )

        return results

    def get_recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return metadata for the N most recent sessions.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            List of session metadata dicts.
        """
        # Make recently-logged messages visible so derived title/turns are accurate.
        self.flush_sync()
        with self._lock:
            # Issue 12: hide ghost sessions (nothing logged) but keep any session with
            # real signal — messages, recorded cost, a stored title, or counted turns.
            rows = self._conn.execute(
                "SELECT id, started_at, ended_at, model, total_turns, title, "
                "total_tokens, total_cost_usd "
                "FROM sessions "
                "WHERE (EXISTS (SELECT 1 FROM messages WHERE messages.session_id = sessions.id) "
                "OR COALESCE(total_cost_usd, 0) > 0 OR title IS NOT NULL "
                "OR COALESCE(total_turns, 0) > 0) "
                "ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

            items = [dict(row) for row in rows]

            # B7a: batch-derive title and turn-count in two queries instead of 2N.
            # Sessions with a stored value skip the batch; only the missing subset is fetched.
            sids_no_title = [item["id"] for item in items if not item.get("title")]
            sids_no_turns = [item["id"] for item in items if not item.get("total_turns")]

            first_content: dict[str, str] = {}
            if sids_no_title:
                ph = ",".join("?" * len(sids_no_title))
                for row in self._conn.execute(
                    f"SELECT session_id, content FROM messages "  # noqa: S608
                    f"WHERE session_id IN ({ph}) AND role = 'user' ORDER BY id ASC",
                    sids_no_title,
                ):
                    sid = row["session_id"]
                    if sid not in first_content:  # keep only first per session
                        first_content[sid] = row["content"] or ""

            turn_count: dict[str, int] = {}
            if sids_no_turns:
                ph = ",".join("?" * len(sids_no_turns))
                for row in self._conn.execute(
                    f"SELECT session_id, COUNT(*) AS c FROM messages "  # noqa: S608
                    f"WHERE session_id IN ({ph}) AND role = 'user' GROUP BY session_id",
                    sids_no_turns,
                ):
                    turn_count[row["session_id"]] = int(row["c"])

            result: list[dict[str, Any]] = []
            for item in items:
                sid = item["id"]
                if not item.get("title"):
                    raw = first_content.get(sid, "")
                    stripped = raw.strip()
                    if stripped:
                        item["title"] = stripped.splitlines()[0][:60].strip()
                if not item.get("total_turns"):
                    item["total_turns"] = turn_count.get(sid, 0)
                result.append(item)

        return result

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return the metadata row for a single session, or None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, started_at, ended_at, model, total_turns, title, "
                "total_tokens, total_cost_usd "
                "FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_latest_session_id(self) -> str | None:
        """Return the id of the most recently started session, or None if empty."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return str(row["id"]) if row else None

    def get_session_messages(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """Return the chronological message transcript for a single session.

        Used to resume a prior conversation: the returned user/assistant turns are
        replayed into the new session's active memory so context carries over.

        Flushes any pending (uncommitted) writes first so callers always see the
        full set of logged messages, even when called shortly after ``log_message``.

        Args:
            session_id: The session whose transcript to load.
            limit: Cap on the number of most-recent messages returned (kept in
                chronological order). Bounds context bloat on very long sessions.

        Returns:
            List of dicts with role, content, tool_name, timestamp, model — oldest first.
        """
        self.flush_sync()
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, tool_name, timestamp, model FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()

        # Fetched newest-first to honour the limit, then reversed to chronological.
        return [dict(row) for row in reversed(rows)]

    def delete_session(self, session_id: str) -> bool:
        """Permanently delete a session and all its messages from the store.

        Both the ``sessions`` row and the associated ``messages`` rows are removed
        atomically inside a single transaction.

        Args:
            session_id: The session to delete (must be an exact full ID).

        Returns:
            True if a session with that ID existed and was deleted; False otherwise.
        """
        with self._lock:
            cur = self._conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
            if cur.fetchone() is None:
                return False
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
        return True

    def delete_sessions_before(self, cutoff_iso: str) -> int:
        """Delete all sessions that started before *cutoff_iso* and their messages.

        Both the ``sessions`` rows and the associated ``messages`` rows are removed
        atomically inside a single transaction.

        Args:
            cutoff_iso: ISO-8601 timestamp string (e.g. ``"2026-05-01T00:00:00"``).
                Sessions whose ``started_at`` is strictly less than this value are
                removed.

        Returns:
            The number of sessions deleted (0 if none matched the cutoff).
        """
        with self._lock:
            # Find IDs first so we can delete messages and count in one pass.
            rows = self._conn.execute(
                "SELECT id FROM sessions WHERE started_at < ?", (cutoff_iso,)
            ).fetchall()
            if not rows:
                return 0
            ids = [row[0] for row in rows]
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"DELETE FROM messages WHERE session_id IN ({placeholders})",
                ids,  # noqa: S608
            )
            self._conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})",
                ids,  # noqa: S608
            )
            self._conn.commit()
        return len(ids)

    def delete_empty_sessions(self) -> int:
        """Delete all sessions that have no messages and return the count deleted.

        A session is an "abandoned ghost" only when it has NO real signal at all —
        no messages, no recorded cost, no title, and no turns. Sessions with any of
        those are preserved (mirrors the get_recent_sessions visibility predicate).

        Called by the gateway's maintenance route and the CLI ``doctor`` command.

        Returns:
            Number of session rows deleted (0 if none found).
        """
        with self._lock:
            ghost_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT id FROM sessions "
                    "WHERE NOT EXISTS "
                    "(SELECT 1 FROM messages WHERE messages.session_id = sessions.id) "
                    "AND COALESCE(total_cost_usd, 0) = 0 AND title IS NULL "
                    "AND COALESCE(total_turns, 0) = 0"
                ).fetchall()
            ]
            if not ghost_ids:
                return 0
            placeholders = ",".join("?" * len(ghost_ids))
            self._conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})",  # noqa: S608
                ghost_ids,
            )
            self._conn.commit()
        logger.info("delete_empty_sessions: removed %d ghost session(s)", len(ghost_ids))
        return len(ghost_ids)

    def backup(self, dest: Path) -> None:
        """Create an online hot-backup of the database to *dest*.

        Uses sqlite3's built-in ``Connection.backup()`` API, which works safely
        even while other connections are reading or writing (no exclusive lock is
        held for the full duration — the backup proceeds page-by-page).

        Pending (uncommitted) writes are flushed first so the backup reflects the
        most recent state.  The destination file is created or overwritten.

        To restore: close this ``SessionStore``, then copy *dest* over the original
        DB path (e.g. ``shutil.copy2(dest, original_path)``), and re-open.

        Args:
            dest: Filesystem path for the output backup file.  The parent directory
                must already exist.

        Raises:
            sqlite3.Error: If the backup operation fails (e.g. disk full).
        """
        self.flush_sync()
        dest = Path(dest)
        with self._lock, sqlite3.connect(str(dest)) as backup_conn:
            self._conn.backup(backup_conn)
        logger.debug("SessionStore backup written to %s", dest)

    def shutdown(self) -> None:
        """Flush pending writes and close the database connection."""
        # Cancel the periodic timer so it doesn't fire after close.
        with self._lock:
            timer = getattr(self, "_flush_timer", None)
            if timer is not None:
                timer.cancel()
                self._flush_timer = None
        # Commit any rows inserted since the last commit before closing.
        self.flush_sync()
        try:
            self._conn.close()
        except Exception:
            pass
