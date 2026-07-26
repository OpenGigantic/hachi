from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "hachi"


@dataclass(frozen=True, slots=True)
class StoreSpec:
    """One durable artefact Hachi owns.

    ``HachiPaths.all_stores()`` is the single source of truth for every store
    Hachi persists to disk (T-55 / BB-145): ``hachi backup``, ``hachi restore``
    and ``hachi doctor`` all enumerate stores from here instead of hand-rolling
    their own lists -- a list that lives in a second place is how a store gets
    silently forgotten. Register a new store in ``all_stores()`` when you add
    one; ``tests/test_backup_restore.py::test_every_hachi_path_is_registered_or_explicitly_exempt``
    fails if a new ``HachiPaths`` path property is added without registering it.
    """

    name: str  # display name + relative path inside a backup dir, e.g. "sessions.db"
    path: Path  # absolute filesystem path to the live store
    kind: str  # "sqlite" | "dir" | "file"
    derived: bool  # True => rebuildable from another store; skipped by backup, on purpose
    note: str  # what it holds, or (if derived) what rebuilds it and from what


@dataclass(slots=True)
class HachiPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    state_dir: Path

    @property
    def global_config_toml(self) -> Path:
        return self.config_dir / "settings.toml"

    @property
    def permissions_file(self) -> Path:
        return self.config_dir / "device-permissions.og"

    @property
    def onboarding_state_file(self) -> Path:
        return self.state_dir / "onboarding_state.json"

    @property
    def skills_dir(self) -> Path:
        return self.data_dir / "skills"

    @property
    def brain_file(self) -> Path:
        return self.config_dir / "brain.og"

    @property
    def sessions_db(self) -> Path:
        return self.state_dir / "sessions.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma_vault"

    @property
    def schedules_file(self) -> Path:
        return self.state_dir / "schedules.json"

    @property
    def checkpoints_dir(self) -> Path:
        return self.state_dir / "checkpoints"

    @property
    def secrets_fallback_file(self) -> Path:
        return self.state_dir / "secrets.enc"

    @property
    def secrets_metadata_file(self) -> Path:
        return self.state_dir / "secrets_metadata.json"

    @property
    def skill_usage_json(self) -> Path:
        return self.state_dir / "skill_usage.json"

    @property
    def user_roles_dir(self) -> Path:
        return self.data_dir / "skills" / "roles"

    @property
    def memory_dir(self) -> Path:
        """Root of the OG memory graph (profile.og, agent.og, projects/, people/, journal/)."""
        return self.data_dir / "memory"

    @property
    def memory_index_db(self) -> Path:
        """SQLite FTS5 index for the OG memory graph."""
        return self.state_dir / "memory_index.db"

    @property
    def blobs_dir(self) -> Path:
        """Root directory for raw file blobs (Initiative E — file attachment platform)."""
        return self.data_dir / "blobs"

    @property
    def files_db(self) -> Path:
        """SQLite database for file metadata (Initiative E — file attachment platform)."""
        return self.state_dir / "files.db"

    @property
    def jobs_db(self) -> Path:
        """SQLite database for the durable background job queue (T-53 / BB-131)."""
        return self.state_dir / "jobs.db"

    @property
    def runs_db(self) -> Path:
        """SQLite database for durable run state (WAITING runs, resume cursors).

        Deliberately stays on ``data_dir`` (not ``state_dir``, unlike its SQLite
        siblings) -- T-53 explicitly deferred that move; see
        ``core/runtime/store.py:_default_db_path`` and T-54 §9 (not an oversight).
        """
        return self.data_dir / "runs.db"

    def journal_shard_path(self, device_id: str | None = None) -> Path:
        """Per-device journal file path for conflict-free git sync.

        Uses socket.gethostname() as the default device identifier.

        Args:
            device_id: Optional explicit device identifier. Defaults to the
                system hostname. Any characters outside ``[a-zA-Z0-9_-]`` are
                replaced with ``-`` and the result is capped at 32 characters
                so the filename is safe on all filesystems.

        Returns:
            A :class:`~pathlib.Path` inside ``memory_dir`` of the form
            ``journal-<safe-device-id>.og``.
        """
        import socket

        _device = device_id or socket.gethostname()
        safe = re.sub(r"[^a-zA-Z0-9_-]", "-", _device)[:32]
        return self.memory_dir / f"journal-{safe}.og"

    def all_stores(self) -> list[StoreSpec]:
        """Every durable artefact Hachi owns. THE source of truth (T-55 / BB-145).

        ``hachi backup``, ``hachi restore`` and ``hachi doctor`` all enumerate
        stores from here rather than hardcoding their own lists. A store added
        to Hachi MUST be registered here -- otherwise it is silently excluded
        from backup/restore/doctor, which is the exact bug this method exists
        to prevent. See
        ``tests/test_backup_restore.py::test_every_hachi_path_is_registered_or_explicitly_exempt``.

        Depends on nothing but this instance's own fields (BB-6: must work
        before any session/service has booted).
        """
        return [
            # --- Non-derived: captured by backup, written back by restore. ---
            StoreSpec(
                name="sessions.db",
                path=self.sessions_db,
                kind="sqlite",
                derived=False,
                note="conversation transcripts (online hot-backup via SessionStore.backup())",
            ),
            StoreSpec(
                name="jobs.db",
                path=self.jobs_db,
                kind="sqlite",
                derived=False,
                note="durable background job queue (T-53)",
            ),
            StoreSpec(
                name="runs.db",
                path=self.runs_db,
                kind="sqlite",
                derived=False,
                note="durable run state: WAITING runs and resume cursors",
            ),
            StoreSpec(
                name="files.db",
                path=self.files_db,
                kind="sqlite",
                derived=False,
                note="file-attachment metadata",
            ),
            StoreSpec(
                name="chroma_vault",
                path=self.chroma_dir,
                kind="dir",
                derived=False,
                note="semantic memory vault (ChromaDB backend)",
            ),
            StoreSpec(
                name="memory",
                path=self.memory_dir,
                kind="dir",
                derived=False,
                note=(
                    "the OG memory graph -- CANONICAL fact store under the fts5_only "
                    "memory backend, where no Chroma vault exists"
                ),
            ),
            StoreSpec(
                name="blobs",
                path=self.blobs_dir,
                kind="dir",
                derived=False,
                note="raw file-attachment payloads",
            ),
            StoreSpec(
                name="checkpoints",
                path=self.checkpoints_dir,
                kind="dir",
                derived=False,
                note="file-change undo history (rollback_changes)",
            ),
            StoreSpec(
                name="skills",
                path=self.skills_dir,
                kind="dir",
                derived=False,
                note="the user's authored .og skills",
            ),
            StoreSpec(
                name="brain.og",
                path=self.brain_file,
                kind="file",
                derived=False,
                note="the root skill graph",
            ),
            StoreSpec(
                name="schedules.json",
                path=self.schedules_file,
                kind="file",
                derived=False,
                note="cron/interval/once schedules",
            ),
            StoreSpec(
                name="skill_usage.json",
                path=self.skill_usage_json,
                kind="file",
                derived=False,
                note="usage stats driving skill ranking",
            ),
            StoreSpec(
                name="onboarding_state.json",
                path=self.onboarding_state_file,
                kind="file",
                derived=False,
                note="setup completion state",
            ),
            StoreSpec(
                name="secrets_metadata.json",
                path=self.secrets_metadata_file,
                kind="file",
                derived=False,
                note="secret names/labels (not values)",
            ),
            StoreSpec(
                name="secrets.enc",
                path=self.secrets_fallback_file,
                kind="file",
                derived=False,
                note="encrypted secret store fallback",
            ),
            StoreSpec(
                name="settings.toml",
                path=self.global_config_toml,
                kind="file",
                derived=False,
                note="non-secret configuration",
            ),
            StoreSpec(
                name="device-permissions.og",
                path=self.permissions_file,
                kind="file",
                derived=False,
                note="permission tier + grants",
            ),
            # --- Derived: rebuildable, skipped by backup on purpose. ---
            StoreSpec(
                name="memory_index.db",
                path=self.memory_index_db,
                kind="sqlite",
                derived=True,
                note="FTS5 index rebuilt by MemoryIndex.refresh() from memory/",
            ),
            StoreSpec(
                name="og_skill_embeddings",
                path=self.state_dir / "og_skill_embeddings",
                kind="dir",
                derived=True,
                note="embedding rank index rebuilt by SkillEngine from skills/",
            ),
        ]


def dotenv_autoload_disabled() -> bool:
    """True when HACHI_DISABLE_DOTENV_AUTOLOAD=1 forbids reading .env files.

    BB-2: .env belongs at the CLI edge. Core resolvers may read it lazily as a
    local-dev convenience, but must fall silent when the harness (or an operator)
    disables autoload — otherwise a hermetic process still picks up ambient creds.
    Single shared definition of the rule (CF-17 / T-67): cli/main.py, core/llm.py
    and core/oauth.py all call this instead of each re-reading the env var.
    """
    return os.environ.get("HACHI_DISABLE_DOTENV_AUTOLOAD", "").strip() == "1"


def get_hachi_paths() -> HachiPaths:
    dirs = PlatformDirs(appname=APP_NAME, appauthor=False, ensure_exists=False)
    return HachiPaths(
        config_dir=Path(dirs.user_config_path),
        data_dir=Path(dirs.user_data_path),
        cache_dir=Path(dirs.user_cache_path),
        state_dir=Path(dirs.user_state_path),
    )


def ensure_directories(paths: HachiPaths) -> None:
    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.cache_dir,
        paths.state_dir,
        paths.skills_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
