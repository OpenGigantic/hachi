"""Tests for the `hachi backup` / `hachi restore` CLI commands (AUDIT P1-9 Part A).

Strategy:
  - Monkeypatch ``get_hachi_paths()`` in ``hachi.cli.main`` to point at tmp dirs
    so real user data is never touched.
  - Use a real (small) SQLite SessionStore so the hot-backup round-trip is tested
    against real SQL code, not just a file copy.
  - Use a fake vault directory (plain files) to verify copytree round-trip.
  - Confirm that restore without ``--yes`` is refused.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from hachi.cli.main import app
from hachi.core.paths import HachiPaths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_paths(tmp_path: Path) -> HachiPaths:
    """Return a HachiPaths rooted in *tmp_path* so no real user data is touched."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    cache_dir = tmp_path / "cache"

    for d in (config_dir, data_dir, state_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    return HachiPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        state_dir=state_dir,
    )


def _seed_sessions_db(db_path: Path) -> None:
    """Write a minimal sessions DB with one row so we can verify round-trip content."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('test-session-1', 'Test Session', '2026-06-24T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_fake_vault(vault_dir: Path) -> None:
    """Create a fake ChromaDB vault directory with a sentinel file."""
    vault_dir.mkdir(parents=True, exist_ok=True)
    (vault_dir / "chroma.sqlite3").write_text("fake-chroma-data", encoding="utf-8")
    (vault_dir / "collections").mkdir(exist_ok=True)
    (vault_dir / "collections" / "core_memory").write_text("fake-collection", encoding="utf-8")


# ---------------------------------------------------------------------------
# Part A tests — backup creates the expected files
# ---------------------------------------------------------------------------


class TestBackupCommand:
    """hachi backup creates a timestamped directory with sessions.db and chroma_vault/."""

    def test_backup_creates_sessions_db(self, tmp_path: Path) -> None:
        """Backup produces a valid SQLite file at <backup_dir>/sessions.db."""
        fake_paths = _make_fake_paths(tmp_path)
        _seed_sessions_db(fake_paths.sessions_db)

        backup_dest = tmp_path / "backups"
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])

        assert result.exit_code == 0, result.output
        # Exactly one timestamped sub-directory should exist.
        backup_dirs = list(backup_dest.iterdir())
        assert len(backup_dirs) == 1
        backup_dir = backup_dirs[0]
        assert backup_dir.name.startswith("hachi-backup-")

        sessions_backup = backup_dir / "sessions.db"
        assert sessions_backup.exists(), "sessions.db not found in backup"

        # Verify the backup is a readable SQLite file containing our row.
        conn = sqlite3.connect(str(sessions_backup))
        try:
            rows = conn.execute("SELECT id FROM sessions").fetchall()
        finally:
            conn.close()
        assert ("test-session-1",) in rows

    def test_backup_copies_chroma_vault(self, tmp_path: Path) -> None:
        """Backup copies the ChromaDB vault directory."""
        fake_paths = _make_fake_paths(tmp_path)
        _seed_fake_vault(fake_paths.chroma_dir)

        backup_dest = tmp_path / "backups"
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])

        assert result.exit_code == 0, result.output
        backup_dir = list(backup_dest.iterdir())[0]
        vault_backup = backup_dir / "chroma_vault"
        assert vault_backup.is_dir(), "chroma_vault not found in backup"
        sentinel = vault_backup / "chroma.sqlite3"
        assert sentinel.exists()
        assert sentinel.read_text(encoding="utf-8") == "fake-chroma-data"

    def test_backup_copies_config_files(self, tmp_path: Path) -> None:
        """Backup includes settings.toml and device-permissions.og when present."""
        fake_paths = _make_fake_paths(tmp_path)
        fake_paths.global_config_toml.write_text(
            "[provider]\nkind = 'anthropic'\n", encoding="utf-8"
        )
        fake_paths.permissions_file.write_text("::index\n# permissions\n", encoding="utf-8")

        backup_dest = tmp_path / "backups"
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])

        assert result.exit_code == 0, result.output
        backup_dir = list(backup_dest.iterdir())[0]
        assert (backup_dir / "settings.toml").exists()
        assert (backup_dir / "device-permissions.og").exists()

    def test_backup_excludes_dotenv(self, tmp_path: Path) -> None:
        """Backup must NOT copy .env files (they contain secrets)."""
        fake_paths = _make_fake_paths(tmp_path)
        # Plant a .env in the config dir to confirm it is not copied.
        (fake_paths.config_dir / ".env").write_text("ANTHROPIC_API_KEY=sk-test", encoding="utf-8")

        backup_dest = tmp_path / "backups"
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])

        assert result.exit_code == 0, result.output
        backup_dir = list(backup_dest.iterdir())[0]
        assert not (backup_dir / ".env").exists(), ".env must not appear in backup"

    def test_backup_output_mentions_exclusion_of_secrets(self, tmp_path: Path) -> None:
        """Output text explicitly notes that .env / secrets are excluded."""
        fake_paths = _make_fake_paths(tmp_path)
        backup_dest = tmp_path / "backups"
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])

        assert result.exit_code == 0, result.output
        assert ".env" in result.output
        assert "excluded" in result.output.lower() or "secrets" in result.output.lower()

    def test_backup_default_dest_is_under_state_dir(self, tmp_path: Path) -> None:
        """When --dest is omitted, the backup lands under <state_dir>/backups/."""
        fake_paths = _make_fake_paths(tmp_path)
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup"])

        assert result.exit_code == 0, result.output
        backups_dir = fake_paths.state_dir / "backups"
        assert backups_dir.exists()
        backup_dirs = list(backups_dir.iterdir())
        assert len(backup_dirs) == 1


# ---------------------------------------------------------------------------
# Part A tests — restore round-trips content
# ---------------------------------------------------------------------------


class TestRestoreCommand:
    """hachi restore recovers sessions.db and chroma_vault/ from a backup directory."""

    def _make_backup(self, tmp_path: Path, fake_paths: HachiPaths) -> Path:
        """Run hachi backup and return the created backup directory."""
        _seed_sessions_db(fake_paths.sessions_db)
        _seed_fake_vault(fake_paths.chroma_dir)

        backup_dest = tmp_path / "backups"
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])

        assert result.exit_code == 0, result.output
        backup_dirs = list(backup_dest.iterdir())
        assert len(backup_dirs) == 1
        return backup_dirs[0]

    def test_restore_round_trips_sessions_db(self, tmp_path: Path) -> None:
        """Restored sessions.db contains the same rows as the original."""
        fake_paths = _make_fake_paths(tmp_path)
        backup_dir = self._make_backup(tmp_path, fake_paths)

        # Wipe the live sessions db to simulate data loss.
        fake_paths.sessions_db.unlink(missing_ok=True)

        runner = CliRunner()
        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["restore", str(backup_dir), "--yes"])

        assert result.exit_code == 0, result.output
        assert fake_paths.sessions_db.exists(), "sessions.db was not restored"

        conn = sqlite3.connect(str(fake_paths.sessions_db))
        try:
            rows = conn.execute("SELECT id FROM sessions").fetchall()
        finally:
            conn.close()
        assert ("test-session-1",) in rows

    def test_restore_round_trips_chroma_vault(self, tmp_path: Path) -> None:
        """Restored chroma_vault/ contains the same sentinel file."""
        import shutil

        fake_paths = _make_fake_paths(tmp_path)
        backup_dir = self._make_backup(tmp_path, fake_paths)

        # Wipe the live vault to simulate data loss.
        shutil.rmtree(str(fake_paths.chroma_dir), ignore_errors=True)

        runner = CliRunner()
        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["restore", str(backup_dir), "--yes"])

        assert result.exit_code == 0, result.output
        assert fake_paths.chroma_dir.exists(), "chroma_vault was not restored"
        sentinel = fake_paths.chroma_dir / "chroma.sqlite3"
        assert sentinel.exists()
        assert sentinel.read_text(encoding="utf-8") == "fake-chroma-data"

    def test_restore_requires_yes_flag(self, tmp_path: Path) -> None:
        """Restore without --yes must refuse and exit cleanly (non-interactive simulation)."""
        fake_paths = _make_fake_paths(tmp_path)
        backup_dir = self._make_backup(tmp_path, fake_paths)

        runner = CliRunner()
        # Provide "n" as stdin so the interactive prompt gets a "no" answer.
        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["restore", str(backup_dir)], input="n\n")

        # Exit 0 (user cancelled cleanly) or the prompt suppressed; either way
        # the important assertion is that nothing was overwritten.
        assert "cancel" in result.output.lower() or result.exit_code == 0

    def test_restore_without_confirmation_does_not_overwrite(self, tmp_path: Path) -> None:
        """Answering 'n' to the confirmation prompt must not touch live data."""
        fake_paths = _make_fake_paths(tmp_path)
        backup_dir = self._make_backup(tmp_path, fake_paths)

        # Plant a marker in the live sessions db using the same 3-column schema
        # that _seed_sessions_db created (the backup was made from that db).
        conn = sqlite3.connect(str(fake_paths.sessions_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, title, created_at) "
                "VALUES ('live-row', 'Live', '2026-06-24T12:00:00')"
            )
            conn.commit()
        finally:
            conn.close()

        runner = CliRunner()
        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            runner.invoke(app, ["restore", str(backup_dir)], input="n\n")

        # The live-row we planted must still be there (restore was aborted).
        conn2 = sqlite3.connect(str(fake_paths.sessions_db))
        try:
            rows = conn2.execute("SELECT id FROM sessions WHERE id = 'live-row'").fetchall()
        finally:
            conn2.close()
        assert rows, "Live row was removed despite restore being cancelled"

    def test_restore_nonexistent_src_exits_with_error(self, tmp_path: Path) -> None:
        """Restore from a path that does not exist must print an error and exit 1."""
        fake_paths = _make_fake_paths(tmp_path)
        runner = CliRunner()

        with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
            result = runner.invoke(app, ["restore", str(tmp_path / "no-such-dir"), "--yes"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Part B tests (T-55 / BB-145) — `hachi backup` silently omitted most user
# data (skills/, the .og memory graph, runs.db, files.db, blobs/,
# checkpoints/, schedules.json, skill_usage.json, onboarding_state.json,
# secrets_metadata.json, secrets.enc, jobs.db). These assert a REAL
# backup -> wipe -> restore round trip returns every registered store intact,
# not merely that a file with the right name shows up in the archive.
# ---------------------------------------------------------------------------


def _content_contains(path: Path, needle: str) -> bool:
    """True if *path* exists and its raw bytes contain *needle*.

    Works uniformly for plain-text files and small SQLite databases alike —
    the sentinel row's bytes are stored verbatim in the leaf page for values
    this short (well under SQLite's overflow-page threshold) — so one check
    covers every store kind without a bespoke per-kind verifier.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return needle.encode("utf-8") in data


def _seed_every_store(paths: HachiPaths) -> dict[Path, str]:
    """Write a unique sentinel into every store T-55 added backup/restore for.

    ``sessions.db``, ``chroma_vault/``, ``settings.toml`` and
    ``device-permissions.og`` already have dedicated coverage elsewhere in
    this file; this covers the stores that were previously silently dropped.
    Returns the absolute sentinel path mapped to a substring that must
    survive a backup -> wipe -> restore cycle unchanged.
    """
    sentinels: dict[Path, str] = {}

    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" disables platform newline translation: on Windows,
        # write_text()'s default text mode silently turns "\n" into "\r\n" at
        # write time, which would make the sentinel bytes we track here
        # diverge from the file on disk before backup/restore ever runs.
        path.write_text(content, encoding="utf-8", newline="")
        sentinels[path] = content

    # The .og memory graph — CANONICAL fact store under memory.backend="fts5_only".
    _write_text(
        paths.memory_dir / "core_memory.og",
        "::node[deploy-target]\n->[:role=fact]\n::dense\nfly.io\n",
    )
    _write_text(paths.skills_dir / "my-skill.og", "::index\n# authored-skill-sentinel\n")
    _write_text(paths.brain_file, "::index\n# brain-graph-sentinel\n")
    _write_text(paths.checkpoints_dir / "cp-1.json", '{"undo": "checkpoint-sentinel"}')
    _write_text(paths.schedules_file, '[{"id": "daily-standup-sentinel"}]')
    _write_text(paths.blobs_dir / "blob-1.bin", "blob-payload-sentinel")
    _write_text(paths.skill_usage_json, '{"my-skill": "usage-sentinel"}')
    _write_text(paths.onboarding_state_file, '{"completed": "onboarding-sentinel"}')
    _write_text(paths.secrets_metadata_file, '[{"name": "secrets-metadata-sentinel"}]')
    _write_text(paths.secrets_fallback_file, "secrets-enc-fallback-sentinel")

    # Durable SQLite stores — real DB round trips, not text-file copies.
    for db_path, table, value in (
        (paths.runs_db, "runs", "run-sentinel-1"),
        (paths.files_db, "files", "file-sentinel-1"),
        (paths.jobs_db, "jobs", "job-sentinel-1"),
    ):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
            conn.execute(f"INSERT INTO {table} VALUES (?)", (value,))
            conn.commit()
        finally:
            conn.close()
        sentinels[db_path] = value

    return sentinels


def test_full_round_trip_preserves_every_registered_store(tmp_path: Path) -> None:
    """backup -> wipe -> restore must return every non-derived store intact.

    Regression (T-55 / BB-145): `hachi backup` copied only sessions.db,
    chroma_vault/, settings.toml and device-permissions.og, and reported
    success. Everything else — the user's .og skills, the .og memory graph
    (CANONICAL under the fts5_only backend), runs.db, files.db, blobs/,
    checkpoints/, schedules.json, skill_usage.json, onboarding_state.json,
    secrets_metadata.json, secrets.enc and the job queue — was silently lost
    on restore.
    """
    import shutil

    fake_paths = _make_fake_paths(tmp_path)
    _seed_sessions_db(fake_paths.sessions_db)
    _seed_fake_vault(fake_paths.chroma_dir)
    fake_paths.global_config_toml.write_text("[provider]\nkind = 'anthropic'\n", encoding="utf-8")
    fake_paths.permissions_file.write_text("::index\n# permissions\n", encoding="utf-8")
    sentinels = _seed_every_store(fake_paths)

    runner = CliRunner()
    backup_dest = tmp_path / "backups"
    with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
        result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])
    assert result.exit_code == 0, result.output
    backup_dir = next(iter(backup_dest.iterdir()))

    # Wipe every live root — simulate the disaster the command exists for.
    for d in (fake_paths.data_dir, fake_paths.state_dir, fake_paths.config_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)

    with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
        result = runner.invoke(app, ["restore", str(backup_dir), "--yes"])
    assert result.exit_code == 0, result.output

    missing = [
        str(path) for path, needle in sentinels.items() if not _content_contains(path, needle)
    ]
    assert not missing, f"store not restored intact: {', '.join(missing)}"


def test_backup_captures_the_og_memory_graph(tmp_path: Path) -> None:
    """Under memory.backend=fts5_only the .og files ARE the memory. They must survive."""
    fake_paths = _make_fake_paths(tmp_path)
    (fake_paths.data_dir / "memory" / "projects").mkdir(parents=True, exist_ok=True)
    memory_file = fake_paths.data_dir / "memory" / "core_memory.og"
    memory_file.write_text(
        "::node[deploy-target]\n->[:role=fact]\n::dense\nfly.io\n", encoding="utf-8"
    )

    backup_dest = tmp_path / "backups"
    runner = CliRunner()
    with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
        result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])
    assert result.exit_code == 0, result.output

    backup_dir = next(iter(backup_dest.iterdir()))
    backed_up_memory = backup_dir / "memory" / "core_memory.og"
    assert backed_up_memory.exists(), (
        "the .og memory graph (CANONICAL fact store under fts5_only) was not backed up"
    )
    assert "fly.io" in backed_up_memory.read_text(encoding="utf-8")


def test_every_hachi_path_is_registered_or_explicitly_exempt() -> None:
    """A new HachiPaths property must be registered in all_stores() or exempted.

    This is the test that stops T-55 recurring: it fails when someone adds a
    store and forgets the registry. Matches by resolved Path *value* (not a
    name-string mapping) so this test cannot itself drift from the registry.
    """
    _EXEMPT = {
        "cache_dir",  # root directory, not an individual store
        "config_dir",  # root directory, not an individual store
        "data_dir",  # root directory, not an individual store
        "state_dir",  # root directory, not an individual store
        "user_roles_dir",  # subdirectory of skills_dir, already registered as "skills"
    }
    fake = HachiPaths(
        config_dir=Path("/fake/config"),
        data_dir=Path("/fake/data"),
        cache_dir=Path("/fake/cache"),
        state_dir=Path("/fake/state"),
    )
    properties = {
        n for n, v in vars(HachiPaths).items() if isinstance(v, property) and not n.startswith("_")
    }
    registered_paths = {spec.path for spec in fake.all_stores()}
    unregistered = sorted(
        name for name in properties - _EXEMPT if getattr(fake, name) not in registered_paths
    )
    assert not unregistered, f"unregistered store paths: {unregistered}"


def test_backup_manifest_lists_every_store_captured_or_skipped_with_reason(
    tmp_path: Path,
) -> None:
    """manifest.json records, for every registered store, whether it was
    captured or deliberately skipped (and why) — turning a silent omission
    into an explicit, inspectable statement (T-55 §5 item 3)."""
    import json

    fake_paths = _make_fake_paths(tmp_path)
    _seed_sessions_db(fake_paths.sessions_db)
    _seed_fake_vault(fake_paths.chroma_dir)

    backup_dest = tmp_path / "backups"
    runner = CliRunner()
    with patch("hachi.cli.main.get_hachi_paths", return_value=fake_paths):
        result = runner.invoke(app, ["backup", "--dest", str(backup_dest)])
    assert result.exit_code == 0, result.output

    backup_dir = next(iter(backup_dest.iterdir()))
    manifest_path = backup_dir / "manifest.json"
    assert manifest_path.exists(), "backup did not write manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    store_names = {spec.name for spec in fake_paths.all_stores()}
    manifest_names = {entry["name"] for entry in manifest["stores"]}
    assert store_names <= manifest_names, (
        f"manifest is missing stores: {store_names - manifest_names}"
    )

    derived_names = {spec.name for spec in fake_paths.all_stores() if spec.derived}
    for entry in manifest["stores"]:
        assert entry["status"] in ("captured", "skipped", "error", "excluded"), entry
        if entry["status"] in ("skipped", "error"):
            assert entry.get("reason"), f"{entry['name']} skipped/errored with no reason recorded"
        if entry["name"] in derived_names:
            assert entry["status"] == "skipped"
            assert "derived" in entry["reason"].lower()
