"""Persistent state storage for Textual app hydration.

This store is intentionally separated from SessionStore so UI layout and draft
state do not pollute operator/audit history.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Dict, Tuple

from tokoboros.core.paths import get_tokoboros_paths


@dataclass(frozen=True, slots=True)
class TUIStateSnapshot:
    """Durable UI snapshot used for startup hydration."""

    last_mode: str = "session"
    draft_input: str = ""
    authoring_target: str = ""
    recent_authoring_targets: Tuple[str, ...] = ()
    filters_json: str = "{}"
    setup_resume_json: str = "{}"


class TUIStateStore:
    """SQLite-backed store for app-specific Textual state."""

    def __init__(self, db_path: str | None = None):
        if not db_path:
            db_path = str(get_tokoboros_paths().tui_state_db)

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _set(self, key: str, value: str) -> None:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_state (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, ts),
            )
            self._conn.commit()

    def _get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return default
        return str(row[0])

    def save_snapshot(self, snapshot: TUIStateSnapshot) -> None:
        self._set("last_mode", snapshot.last_mode)
        self._set("draft_input", snapshot.draft_input)
        self._set("authoring_target", snapshot.authoring_target)
        self._set("filters_json", snapshot.filters_json)
        self._set("setup_resume_json", snapshot.setup_resume_json)
        self._set("recent_authoring_targets", json.dumps(list(snapshot.recent_authoring_targets)))

    def load_snapshot(self) -> TUIStateSnapshot:
        recent_raw = self._get("recent_authoring_targets", "[]")
        try:
            recent = tuple(str(item) for item in json.loads(recent_raw))
        except Exception:
            recent = ()

        return TUIStateSnapshot(
            last_mode=self._get("last_mode", "session") or "session",
            draft_input=self._get("draft_input", ""),
            authoring_target=self._get("authoring_target", ""),
            recent_authoring_targets=recent,
            filters_json=self._get("filters_json", "{}") or "{}",
            setup_resume_json=self._get("setup_resume_json", "{}") or "{}",
        )

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM app_state")
            self._conn.commit()

    def shutdown(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
