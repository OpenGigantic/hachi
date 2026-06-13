from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "tokoboros"


@dataclass(slots=True)
class TokoborosPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    state_dir: Path
    legacy_dir: Path

    @property
    def global_config_toml(self) -> Path:
        return self.config_dir / "settings.toml"

    @property
    def legacy_config_json(self) -> Path:
        return self.legacy_dir / "tokoboros.json"

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
    def tui_state_db(self) -> Path:
        return self.state_dir / "tui_state.db"

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


def get_tokoboros_paths() -> TokoborosPaths:
    dirs = PlatformDirs(appname=APP_NAME, appauthor=False, ensure_exists=False)
    return TokoborosPaths(
        config_dir=Path(dirs.user_config_path),
        data_dir=Path(dirs.user_data_path),
        cache_dir=Path(dirs.user_cache_path),
        state_dir=Path(dirs.user_state_path),
        legacy_dir=Path.home() / ".tokoboros",
    )


def ensure_directories(paths: TokoborosPaths) -> None:
    for directory in (
        paths.config_dir,
        paths.data_dir,
        paths.cache_dir,
        paths.state_dir,
        paths.skills_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def migrate_legacy_skill_usage(paths: TokoborosPaths) -> bool:
    """Copy ~/.tokoboros/brain/skill_usage.json to state_dir if it doesn't exist yet."""
    target = paths.skill_usage_json
    if target.exists():
        return False
    legacy = paths.legacy_dir / "brain" / "skill_usage.json"
    if not legacy.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    return True


def import_legacy_workspace(paths: TokoborosPaths) -> bool:
    """Import baseline files from ~/.tokoboros if canonical config does not exist yet."""
    legacy = paths.legacy_dir
    if not legacy.exists():
        return False

    imported_any = False

    if not paths.global_config_toml.exists() and paths.legacy_config_json.exists():
        imported_any = True

    legacy_permissions = legacy / "device-permissions.og"
    if legacy_permissions.exists() and not paths.permissions_file.exists():
        paths.permissions_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_permissions, paths.permissions_file)
        imported_any = True

    legacy_brain = legacy / "brain.og"
    if legacy_brain.exists() and not paths.brain_file.exists():
        paths.brain_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_brain, paths.brain_file)
        imported_any = True

    legacy_skills = legacy / "skills"
    if legacy_skills.exists() and not any(paths.skills_dir.iterdir()):
        shutil.copytree(legacy_skills, paths.skills_dir, dirs_exist_ok=True)
        imported_any = True

    return imported_any
