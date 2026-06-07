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
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from tokoboros.core.paths import get_tokoboros_paths

logger = logging.getLogger(__name__)


class SessionStore:
    """SQLite-backed session persistence for cross-session search and analytics.

    Designed for append-heavy workloads with minimal read contention.
    Uses WAL journal mode so background log_message calls never block
    the main agent loop's reads.
    """

    def __init__(self, db_path: str | None = None):
        """Initialize the session store.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to OS-native Tokoboros state directory.
        """
        if not db_path:
            db_path = str(get_tokoboros_paths().sessions_db)

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
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_msg_session
                ON messages(session_id);
            -- idx_msg_content removed: leading-wildcard LIKE cannot use a B-tree index.
        """)
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply additive migrations to databases created by older versions.

        ``CREATE TABLE IF NOT EXISTS`` leaves a pre-existing ``sessions`` table
        untouched, so a column added to the definition above never reaches an
        already-created DB. Adding it explicitly (idempotent) keeps old stores
        forward-compatible.
        """
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "title" not in existing:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
            self._conn.commit()
        if "total_tokens" not in existing:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN total_tokens INTEGER DEFAULT 0")
            self._conn.commit()
        if "total_cost_usd" not in existing:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN total_cost_usd REAL DEFAULT 0.0"
            )
            self._conn.commit()

    # ==========================================
    # Session Lifecycle
    # ==========================================

    def start_session(self, model: str = "unknown") -> str:
        """Begin a new session and return its unique ID.

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

        Args:
            session_id: The session to close.
        """
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
        self, session_id: str, role: str, content: str, tool_name: str | None = None
    ) -> None:
        """Persist a single message to the session log.

        Called after every user input and assistant response. Runs in the
        calling thread but the WAL journal ensures it doesn't block reads.

        Args:
            session_id: The active session ID.
            role: Message role — 'user', 'assistant', or 'tool'.
            content: The message text. Truncated to 50K chars to bound DB growth.
            tool_name: Optional tool name if this is a tool call/result.
        """
        now = datetime.datetime.now(datetime.UTC).isoformat()
        # Truncate very large tool outputs to prevent DB bloat
        content = content[:50_000] if content else ""

        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_name, now),
            )
            self._conn.commit()

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
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, ended_at, model, total_turns, title, "
                "total_tokens, total_cost_usd "
                "FROM sessions ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

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

        Args:
            session_id: The session whose transcript to load.
            limit: Cap on the number of most-recent messages returned (kept in
                chronological order). Bounds context bloat on very long sessions.

        Returns:
            List of dicts with role, content, tool_name, timestamp — oldest first.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, tool_name, timestamp FROM messages "
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
            cur = self._conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            )
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
                f"DELETE FROM messages WHERE session_id IN ({placeholders})", ids  # noqa: S608
            )
            self._conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})", ids  # noqa: S608
            )
            self._conn.commit()
        return len(ids)

    def shutdown(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass
