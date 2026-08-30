"""T-53 — jobs.db path unification: migration from the legacy opengigantic root.

Regression coverage for CF-13
(docs/audit/2026-07-19/tasks/T-53-jobs-db-path-unification.md): before this fix,
``jobs.db`` resolved under ``PlatformDirs("hachi", "opengigantic")`` — a second
on-disk root invisible to every ``get_hachi_paths()``-based tool (backup, restore,
doctor). ``jobs/queue.py::_default_db_path`` and the duplicated inline resolution in
``cli/commands/jobs.py::_open_queue`` also independently resolved the same wrong
root, which meant a *partial* fix (only one of the two updated) would split the
queue in two — the failure mode ``test_cli_and_gateway_resolve_the_same_jobs_db``
guards against.

Every test below points ``get_hachi_paths`` and ``_legacy_jobs_db_path`` at
``tmp_path`` so the suite never reads or writes a developer's real
``%LOCALAPPDATA%\\opengigantic\\hachi\\jobs.db`` — that is their actual job queue.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hachi.core.paths import HachiPaths


def _mock_paths(tmp_path: Path) -> HachiPaths:
    return HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )


def _seed_legacy_db(path: Path) -> None:
    """Write a legacy jobs.db with one recoverable row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO jobs VALUES ('legacy-1', 'fact_extraction')")
        conn.commit()
    finally:
        conn.close()


def test_legacy_jobs_db_is_moved_to_the_canonical_path(tmp_path, monkeypatch):
    """An existing user's queued jobs move to the new path — they are not orphaned.

    Regression (T-53): jobs.db resolved under PlatformDirs("hachi", "opengigantic"),
    a second on-disk root invisible to every get_hachi_paths()-based tool. Pointing
    it at state_dir without a migration would strand every user's dead-letter queue.
    """
    import hachi.jobs.queue as queue_mod

    mock_paths = _mock_paths(tmp_path)
    legacy = tmp_path / "legacy" / "jobs.db"
    _seed_legacy_db(legacy)
    # WAL sidecar: SQLiteJobQueue sets PRAGMA journal_mode=WAL, so committed rows
    # can live in "-wal" and not yet be in the main file. Moving jobs.db alone can
    # silently drop them — assert the sidecar moves too.
    legacy_wal = legacy.with_name(legacy.name + "-wal")
    legacy_wal.write_bytes(b"fake-wal-bytes")

    monkeypatch.setattr(queue_mod, "get_hachi_paths", lambda: mock_paths, raising=False)
    monkeypatch.setattr(queue_mod, "_legacy_jobs_db_path", lambda: legacy, raising=False)

    resolved = queue_mod._default_db_path()

    # Compare against the HachiPaths property (not a hand-built literal) so this
    # also catches the pre-fix state where the property does not exist yet.
    assert resolved == mock_paths.jobs_db
    assert resolved.exists(), "jobs.db was not migrated"
    assert not legacy.exists(), "legacy jobs.db was left behind"
    assert not legacy_wal.exists(), "legacy jobs.db-wal sidecar was left behind"
    assert resolved.with_name(resolved.name + "-wal").exists(), (
        "jobs.db-wal sidecar was not migrated"
    )

    conn = sqlite3.connect(str(resolved))
    try:
        rows = conn.execute("SELECT job_id FROM jobs").fetchall()
    finally:
        conn.close()
    assert ("legacy-1",) in rows, "migrated DB lost its rows"


def test_migration_never_overwrites_an_existing_target(tmp_path, monkeypatch):
    """If a jobs.db already exists at the new path, the legacy file must not clobber it."""
    import hachi.jobs.queue as queue_mod

    mock_paths = _mock_paths(tmp_path)

    target = mock_paths.jobs_db
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO jobs VALUES ('target-1', 'current-owner')")
        conn.commit()
    finally:
        conn.close()

    legacy = tmp_path / "legacy" / "jobs.db"
    _seed_legacy_db(legacy)  # a different row set than the target's

    monkeypatch.setattr(queue_mod, "get_hachi_paths", lambda: mock_paths, raising=False)
    monkeypatch.setattr(queue_mod, "_legacy_jobs_db_path", lambda: legacy, raising=False)

    resolved = queue_mod._default_db_path()

    assert resolved == target
    assert legacy.exists(), "legacy jobs.db must survive untouched when the target already exists"

    conn = sqlite3.connect(str(resolved))
    try:
        rows = conn.execute("SELECT job_id FROM jobs").fetchall()
    finally:
        conn.close()
    assert rows == [("target-1",)], "the pre-existing target's own row was clobbered"

    legacy_conn = sqlite3.connect(str(legacy))
    try:
        legacy_rows = legacy_conn.execute("SELECT job_id FROM jobs").fetchall()
    finally:
        legacy_conn.close()
    assert legacy_rows == [("legacy-1",)], "the legacy DB's row set was mutated"


def test_cli_and_gateway_resolve_the_same_jobs_db(tmp_path, monkeypatch):
    """The CLI and the gateway must never open two different job databases.

    Guards the partial-fix failure mode: changing jobs/queue.py without
    cli/commands/jobs.py splits the queue in two, and `hachi jobs dead-letter`
    silently reports an empty queue while jobs accumulate elsewhere.
    """
    import hachi.jobs.queue as queue_mod
    from hachi.cli.commands.jobs import _open_queue
    from hachi.jobs.queue import SQLiteJobQueue

    mock_paths = _mock_paths(tmp_path)
    mock_paths.state_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(queue_mod, "get_hachi_paths", lambda: mock_paths, raising=False)
    monkeypatch.setattr(
        queue_mod,
        "_legacy_jobs_db_path",
        lambda: tmp_path / "no-legacy" / "jobs.db",
        raising=False,
    )

    cli_queue = _open_queue()
    gateway_queue = SQLiteJobQueue()
    try:
        # Each must independently resolve to the canonical path -- not merely to
        # each other -- because today (pre-fix) both entry points independently
        # resolve the SAME wrong legacy path, which would make a naive mutual
        # equality check pass "for free" without proving either side is correct.
        assert cli_queue._db_path == mock_paths.jobs_db
        assert gateway_queue._db_path == mock_paths.jobs_db
        assert cli_queue._db_path == gateway_queue._db_path
    finally:
        cli_queue.shutdown()
        gateway_queue.shutdown()


def test_migration_failure_logs_warning_and_does_not_crash_boot(tmp_path, monkeypatch, caplog):
    """A failed migration must not raise — boot proceeds with a fresh DB at the new
    path — but the failure must be visible at WARNING, not swallowed at DEBUG."""
    import logging

    import hachi.jobs.queue as queue_mod

    mock_paths = _mock_paths(tmp_path)
    legacy = tmp_path / "legacy" / "jobs.db"
    _seed_legacy_db(legacy)

    monkeypatch.setattr(queue_mod, "get_hachi_paths", lambda: mock_paths, raising=False)
    monkeypatch.setattr(queue_mod, "_legacy_jobs_db_path", lambda: legacy, raising=False)

    def _boom(*_args, **_kwargs):
        raise OSError("simulated cross-volume move failure")

    monkeypatch.setattr(queue_mod.shutil, "move", _boom)

    with caplog.at_level(logging.WARNING, logger="hachi.jobs.queue"):
        resolved = queue_mod._default_db_path()  # must not raise

    assert resolved == mock_paths.jobs_db
    assert legacy.exists(), "legacy file must not be lost when the move fails"
    assert any(
        record.levelno >= logging.WARNING and str(legacy) in record.getMessage()
        for record in caplog.records
    ), "migration failure must be logged at WARNING, not swallowed"
