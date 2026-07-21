"""Shared SQLite migration helper.

Provides:

- ``apply_migrations`` — a safe, additive, idempotent helper for adding new
  columns to existing tables. Used by all SQLite stores in Hachi to close debt
  D9 (each store had its own ad-hoc migration logic).
- ``ensure_schema_version`` / ``set_schema_version`` (T-54) — a one-row
  ``schema_version`` table every live store stamps on open, so a database can
  answer "which shape am I?" and a downgraded build refuses to silently
  misread a newer schema.  See ``docs/DECISIONS.md`` (T-54 ADR) and
  ``docs/audit/2026-07-19/tasks/T-54-schema-version-tables.md`` for the design.

Usage::

    from hachi.core.migrations import apply_migrations, ensure_schema_version

    ensure_schema_version(conn, current=1)   # after CREATE TABLE IF NOT EXISTS,
                                              # before any apply_migrations call
    apply_migrations(conn, "sessions", [("new_col", "TEXT"), ("other_col", "INTEGER")])
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# The one table every live SQLite store gains under T-54 (see docs/DECISIONS.md).
SCHEMA_VERSION_TABLE = "schema_version"


class SchemaTooNewError(Exception):
    """A store's on-disk ``schema_version`` is newer than this build supports.

    This means the database was written by a NEWER build of Hachi than the one
    currently running. Opening it anyway would read a schema this build does
    not know about — silent misreads, not the "additive migrations are always
    safe" case the rest of this module relies on. Every store's constructor
    MUST let this propagate; do not catch it and continue (T-54 §5).
    """

    def __init__(self, *, db_path: str, stored: int, current: int) -> None:
        self.db_path = db_path
        self.stored = stored
        self.current = current
        where = db_path or "<in-memory database>"
        super().__init__(
            f"{where} is stamped schema_version={stored}, but this build of Hachi "
            f"only understands up to version {current}. It was written by a newer "
            f"build of Hachi. Upgrade Hachi to open it, or restore a backup made "
            f"with a compatible version."
        )


def apply_migrations(
    conn: sqlite3.Connection,
    table: str,
    migrations: list[tuple[str, str]],
) -> None:
    """Apply additive column migrations to *table* on *conn*.

    Each migration is a ``(column_name, column_definition)`` pair.  A column is
    only added when it does not already exist in the table schema; the call is
    therefore idempotent and safe to run at every boot.

    Args:
        conn:       An open SQLite connection.  The caller retains ownership;
                    this function does NOT commit or close.
        table:      The table to migrate.
        migrations: Ordered list of ``(column, definition)`` pairs to add.
                    Example: ``[("created_at", "TEXT"), ("score", "REAL DEFAULT 0")]``

    Raises:
        sqlite3.OperationalError: If the table does not exist or the ALTER fails
                                   for a reason other than "duplicate column".
    """
    if not migrations:
        return

    existing_cols: set[str] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    for column, definition in migrations:
        if column in existing_cols:
            continue
        sql = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        logger.debug("apply_migrations: %s", sql)
        conn.execute(sql)
        conn.commit()
        existing_cols.add(column)


# ---------------------------------------------------------------------------
# T-54 — schema_version
# ---------------------------------------------------------------------------


def _create_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create ``schema_version`` if absent. Idempotent; does not commit.

    ``CHECK (id = 1)`` makes "exactly one row, ever" a database-enforced
    invariant instead of a convention every caller has to honour.
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            version        INTEGER NOT NULL,
            app_version    TEXT    NOT NULL,
            updated_at     TEXT    NOT NULL
        )
        """
    )


def _app_version() -> str:
    """Best-effort installed package version (mirrors cli/main.py's ``--version`` fallback).

    ``core`` must not import the ``cli`` edge, so this does not reuse
    ``cli.main._resolve_version`` — it independently repeats the same
    ``importlib.metadata`` lookup, which is stdlib and layering-safe here.
    """
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("hachi-og")
    except Exception:
        return "unknown"


def ensure_schema_version(conn: sqlite3.Connection, *, current: int) -> int:
    """Create the schema_version table if absent and return the DB's version.

    An unstamped (pre-T-54) database is stamped as version 1 — "the schema as it
    existed when versioning was introduced" — NOT as *current*. Do not attempt
    schema archaeology (probing for known columns to infer a "truer" historical
    version): it is guesswork against user data we do not have, and the additive
    migrations already made every historical shape forward-compatible. See
    docs/DECISIONS.md (T-54 ADR, Decision 2).

    Args:
        conn:    An open SQLite connection. The caller retains ownership; this
                 function commits its own writes but does not close the connection.
        current: The schema version this build of Hachi expects/supports.

    Returns:
        The database's schema version — 1 for a freshly-stamped or legacy DB, or
        whatever a prior :func:`set_schema_version` call recorded.

    Raises:
        SchemaTooNewError: when the stored version exceeds *current*, i.e. the
            database was written by a newer build of Hachi. Callers MUST let this
            propagate out of the store's constructor — do not catch and continue.
    """
    _create_schema_version_table(conn)
    conn.commit()

    row = conn.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE id = 1").fetchone()
    if row is None:
        set_schema_version(conn, 1)
        return 1

    stored = int(row[0])
    if stored > current:
        db_row = conn.execute("PRAGMA database_list").fetchone()
        db_path = db_row[2] if db_row else ""
        raise SchemaTooNewError(db_path=db_path, stored=stored, current=current)
    return stored


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Record *version* as the database's schema version. Call after a migration.

    Safe to call even when the ``schema_version`` table does not exist yet (it
    creates it) — e.g. ``memory_index.db``'s rebuild-on-mismatch path
    (``core/memory_index.py``) drops and recreates its data tables and
    re-stamps in one step. ``INSERT ... ON CONFLICT`` means the single row
    (``id = 1``, enforced by the table's own ``CHECK (id = 1)``) is created on
    first write and overwritten on every later call — never more than one row.

    Commits immediately: a stamp that only survives as long as the next
    uncommitted write is worse than no stamp at all (mirrors the deferred-commit
    trap in ``core/session_store.py``'s ``log_message`` — this function must not
    reproduce it). ``apply_migrations`` already commits per ALTER; this follows
    that precedent.
    """
    _create_schema_version_table(conn)
    conn.execute(
        f"""
        INSERT INTO {SCHEMA_VERSION_TABLE} (id, version, app_version, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            version = excluded.version,
            app_version = excluded.app_version,
            updated_at = excluded.updated_at
        """,
        (version, _app_version(), datetime.now(UTC).isoformat()),
    )
    conn.commit()
