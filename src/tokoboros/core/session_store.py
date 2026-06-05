"""
Session Persistence Store

Lightweight SQLite backend that persists every session's messages, tool calls,
and metadata for cross-session search, analytics, and audit trails.

Schema:
    sessions: id, started_at, ended_at, model, total_turns
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
                total_turns INTEGER DEFAULT 0
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
                "SELECT id, started_at, ended_at, model, total_turns "
                "FROM sessions ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def shutdown(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass
