"""Tests for apply_migrations — idempotent SQLite column migration (GATE-MIGRATE).

Also covers T-54 — the ``schema_version`` table every live SQLite store now stamps.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hachi.core.migrations import apply_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_table(conn: sqlite3.Connection, table: str = "test_table") -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id   TEXT PRIMARY KEY,
            name TEXT
        )
        """
    )
    conn.commit()


def _get_columns(conn: sqlite3.Connection, table: str = "test_table") -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------


class TestApplyMigrations:
    def test_adds_new_column(self):
        conn = sqlite3.connect(":memory:")
        _create_table(conn)
        apply_migrations(conn, "test_table", [("score", "REAL DEFAULT 0")])
        assert "score" in _get_columns(conn)

    def test_adds_multiple_columns(self):
        conn = sqlite3.connect(":memory:")
        _create_table(conn)
        apply_migrations(
            conn,
            "test_table",
            [("created_at", "TEXT"), ("updated_at", "TEXT"), ("score", "INTEGER DEFAULT 0")],
        )
        cols = _get_columns(conn)
        assert "created_at" in cols
        assert "updated_at" in cols
        assert "score" in cols

    def test_empty_migrations_no_op(self):
        conn = sqlite3.connect(":memory:")
        _create_table(conn)
        cols_before = _get_columns(conn)
        apply_migrations(conn, "test_table", [])
        assert _get_columns(conn) == cols_before


# ---------------------------------------------------------------------------
# GATE-MIGRATE: idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_existing_column_not_duplicated(self):
        """GATE-MIGRATE: apply_migrations is idempotent on existing column."""
        conn = sqlite3.connect(":memory:")
        _create_table(conn)

        # First run: add the column
        apply_migrations(conn, "test_table", [("extra_col", "TEXT")])
        assert "extra_col" in _get_columns(conn)

        # Second run: same migration must not raise
        apply_migrations(conn, "test_table", [("extra_col", "TEXT")])
        # Column still exists, not duplicated
        assert "extra_col" in _get_columns(conn)

    def test_multiple_idempotent_runs(self):
        conn = sqlite3.connect(":memory:")
        _create_table(conn)
        migrations = [("col_a", "TEXT"), ("col_b", "INTEGER DEFAULT 0")]

        for _ in range(5):
            apply_migrations(conn, "test_table", migrations)

        cols = _get_columns(conn)
        assert "col_a" in cols
        assert "col_b" in cols

    def test_partial_existing_columns(self):
        """Only the missing column is added; existing one is skipped."""
        conn = sqlite3.connect(":memory:")
        _create_table(conn)

        # Add col_a first
        apply_migrations(conn, "test_table", [("col_a", "TEXT")])
        cols_after_first = _get_columns(conn)
        assert "col_a" in cols_after_first
        assert "col_b" not in cols_after_first

        # Now run with both; col_a should be skipped, col_b added
        apply_migrations(conn, "test_table", [("col_a", "TEXT"), ("col_b", "INTEGER")])
        cols_after_second = _get_columns(conn)
        assert "col_a" in cols_after_second
        assert "col_b" in cols_after_second


# ---------------------------------------------------------------------------
# Original columns untouched
# ---------------------------------------------------------------------------


class TestOriginalColumnsUnchanged:
    def test_original_columns_still_accessible(self):
        conn = sqlite3.connect(":memory:")
        _create_table(conn)
        conn.execute("INSERT INTO test_table (id, name) VALUES ('1', 'Alice')")
        conn.commit()

        apply_migrations(conn, "test_table", [("score", "INTEGER DEFAULT 0")])

        row = conn.execute("SELECT id, name, score FROM test_table WHERE id='1'").fetchone()
        assert row is not None
        assert row[0] == "1"
        assert row[1] == "Alice"
        assert row[2] == 0  # default value applied

    def test_data_preserved_after_migration(self):
        conn = sqlite3.connect(":memory:")
        _create_table(conn)
        conn.execute("INSERT INTO test_table (id, name) VALUES ('row1', 'Bob')")
        conn.commit()

        apply_migrations(conn, "test_table", [("notes", "TEXT")])

        rows = conn.execute("SELECT id, name FROM test_table").fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "Bob"


# ---------------------------------------------------------------------------
# RunStateStore uses migrations (integration smoke test)
# ---------------------------------------------------------------------------


class TestRunStateStoreMigrations:
    def test_store_schema_has_expected_columns(self, tmp_path):
        """RunStateStore._init_schema creates the runs table with correct columns."""
        from hachi.core.runtime.store import RunStateStore

        store = RunStateStore(db_path=tmp_path / "runs.db")
        cols = _get_columns(store._conn, "runs")
        expected = {
            "run_id",
            "session_id",
            "status",
            "wait_kind",
            "wait_key",
            "wait_timeout_at",
            "resume_cursor",
            "payload",
            "updated_at",
        }
        assert expected.issubset(cols)
        store.close()

    def test_store_init_idempotent(self, tmp_path):
        """Opening the same DB twice does not fail."""
        from hachi.core.runtime.store import RunStateStore

        db = tmp_path / "runs.db"
        store1 = RunStateStore(db_path=db)
        store1.close()
        store2 = RunStateStore(db_path=db)
        store2.close()


# ---------------------------------------------------------------------------
# T-54: schema_version — every live SQLite store records its schema version
# ---------------------------------------------------------------------------


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


class TestSchemaVersion:
    """T-54: every live SQLite store stamps a ``schema_version`` table on open."""

    def test_every_live_sqlite_store_records_a_schema_version(self, tmp_path: Path) -> None:
        """Every SQLite store Hachi owns must stamp its schema version on creation.

        Regression (T-54): no schema_version table existed anywhere. Six stores held
        user data across two roots with no way to ask a database what shape it was in,
        so no non-additive migration was possible and a downgraded build would read a
        newer schema silently.
        """
        from hachi.core.files.store import FileMetaStore
        from hachi.core.runtime.store import RunStateStore
        from hachi.core.session_store import SessionStore
        from hachi.jobs.queue import SQLiteJobQueue

        built: dict[str, Path] = {}

        sessions = tmp_path / "sessions.db"
        store = SessionStore(db_path=str(sessions))
        store.shutdown()
        built["sessions.db"] = sessions

        files = tmp_path / "files.db"
        FileMetaStore(files)
        built["files.db"] = files

        runs = tmp_path / "runs.db"
        RunStateStore(db_path=runs)
        built["runs.db"] = runs

        jobs = tmp_path / "jobs.db"
        SQLiteJobQueue(db_path=jobs).shutdown()
        built["jobs.db"] = jobs

        for name, path in built.items():
            assert "schema_version" in _tables(path), f"{name} has no schema_version table"

    def test_unstamped_database_is_stamped_as_version_one(self, tmp_path: Path) -> None:
        """A pre-T-54 database is version 1 — not the current version (§5 Decision 2)."""
        from hachi.core.migrations import ensure_schema_version

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            conn.commit()
            assert ensure_schema_version(conn, current=7) == 1
        finally:
            conn.close()

    def test_opening_a_future_version_database_raises(self, tmp_path: Path) -> None:
        """A database written by a NEWER build must fail loudly, not be read regardless."""
        from hachi.core.migrations import (
            SchemaTooNewError,
            ensure_schema_version,
            set_schema_version,
        )

        db = tmp_path / "future.db"
        conn = sqlite3.connect(str(db))
        try:
            ensure_schema_version(conn, current=1)
            set_schema_version(conn, 99)
            with pytest.raises(SchemaTooNewError):
                ensure_schema_version(conn, current=1)
        finally:
            conn.close()

    def test_opening_sessions_db_at_a_future_version_raises(self, tmp_path: Path) -> None:
        """§8 acceptance: the raise must propagate through the real store's
        constructor, not merely through ensure_schema_version() in isolation —
        a store swallowing SchemaTooNewError would defeat the whole task.
        """
        from hachi.core.migrations import SchemaTooNewError, set_schema_version
        from hachi.core.session_store import SessionStore

        db = tmp_path / "sessions.db"
        SessionStore(db_path=str(db)).shutdown()

        conn = sqlite3.connect(str(db))
        try:
            set_schema_version(conn, 99)
        finally:
            conn.close()

        with pytest.raises(SchemaTooNewError):
            SessionStore(db_path=str(db))

    def test_opening_runs_db_at_a_future_version_raises(self, tmp_path: Path) -> None:
        """§8 acceptance: same propagation guarantee for runs.db."""
        from hachi.core.migrations import SchemaTooNewError, set_schema_version
        from hachi.core.runtime.store import RunStateStore

        db = tmp_path / "runs.db"
        RunStateStore(db_path=db).close()

        conn = sqlite3.connect(str(db))
        try:
            set_schema_version(conn, 99)
        finally:
            conn.close()

        with pytest.raises(SchemaTooNewError):
            RunStateStore(db_path=db)

    def test_memory_index_rebuilds_instead_of_raising_on_version_mismatch(
        self, tmp_path: Path
    ) -> None:
        """memory_index.db is a derived cache: a mismatch rebuilds, it does not raise."""
        from hachi.core.memory_index import MemoryIndex
        from hachi.core.migrations import set_schema_version

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "profile.og").write_text(
            "::meta\n"
            "  title: User Profile\n"
            "  version: 1.0.0\n"
            "\n"
            "::node[name]\n"
            "->[:role=preference]\n"
            "->[:keywords=name, identity]\n"
            "::dense\n"
            "User's name is Alice.\n",
            encoding="utf-8",
        )
        db_path = tmp_path / "memory_index.db"

        idx = MemoryIndex(memory_dir=memory_dir, db_path=db_path)
        idx.refresh()
        before = idx.search("Alice", top_k=5)
        assert before, "sanity: the seeded node must be findable before the mismatch"

        # Simulate a database written by a much newer build of Hachi.
        conn = sqlite3.connect(str(db_path))
        try:
            set_schema_version(conn, 99)
        finally:
            conn.close()

        # Re-opening must NOT raise (unlike sessions.db/runs.db) — a mismatch here
        # rebuilds instead, because the whole index is a disposable cache of .og.
        rebuilt = MemoryIndex(memory_dir=memory_dir, db_path=db_path)

        # The discriminating assertion: opening must actually have *noticed* the
        # mismatch and corrected the stamp back to what this build expects. A
        # MemoryIndex that never looks at schema_version at all would also "not
        # raise" and would also return identical search results (refresh() always
        # rebuilds unconditionally from .og regardless of versioning) — so those
        # two checks alone would pass even with no wiring. Reading the stamp back
        # is the one assertion that can't pass by accident.
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == 1, (
            "a version mismatch must be detected and corrected on open, not left "
            "at the stale/bogus stamp"
        )

        rebuilt.refresh()
        after = rebuilt.search("Alice", top_k=5)
        assert {r["id"] for r in after} == {r["id"] for r in before}
