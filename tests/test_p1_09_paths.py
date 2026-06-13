"""P1-09: Path unification — skill-usage DB and RoleResolver use platformdirs.

Two invariants:
  1. Fresh env: SkillEngine._usage_path is under get_tokoboros_paths().state_dir.
  2. Legacy migration: if ~/.tokoboros/brain/skill_usage.json exists and state_dir file
     doesn't, the data is copied once on first SkillEngine boot.
  3. RoleResolver: _roles_dir is under get_tokoboros_paths().data_dir (not ~/.tokoboros).
  4. RoleResolver legacy shim: legacy ~/.tokoboros/skills/roles/ is checked as fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokoboros.core.paths import TokoborosPaths, migrate_legacy_skill_usage


def _mock_paths(tmp_path: Path) -> TokoborosPaths:
    return TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )


# ---------------------------------------------------------------------------
# SkillEngine usage path
# ---------------------------------------------------------------------------


def test_skill_engine_uses_platformdirs_state_dir(tmp_path, monkeypatch):
    """_usage_path resolves to state_dir/skill_usage.json, not ~/.tokoboros."""
    import tokoboros.og_engine.engine as engine_mod

    mock_paths = _mock_paths(tmp_path)
    mock_paths.state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(engine_mod, "get_tokoboros_paths", lambda: mock_paths)

    eng = engine_mod.SkillEngine()
    assert eng._usage_path == mock_paths.state_dir / "skill_usage.json"
    assert ".tokoboros" not in str(eng._usage_path)


def test_skill_engine_migrates_legacy_usage_on_first_boot(tmp_path, monkeypatch):
    """Legacy ~/.tokoboros/brain/skill_usage.json is copied to state_dir once."""
    import tokoboros.og_engine.engine as engine_mod

    mock_paths = _mock_paths(tmp_path)
    mock_paths.state_dir.mkdir(parents=True, exist_ok=True)

    # Populate the legacy path with known data.
    legacy_brain = mock_paths.legacy_dir / "brain"
    legacy_brain.mkdir(parents=True)
    legacy_usage = legacy_brain / "skill_usage.json"
    legacy_data = {"old_skill": {"use_count": 7, "last_used": "2026-01-01"}}
    legacy_usage.write_text(json.dumps(legacy_data), encoding="utf-8")

    monkeypatch.setattr(engine_mod, "get_tokoboros_paths", lambda: mock_paths)

    eng = engine_mod.SkillEngine()
    # The engine should have loaded the migrated data.
    assert "old_skill" in eng._usage
    assert eng._usage["old_skill"]["use_count"] == 7
    # The canonical path should now exist.
    assert eng._usage_path.exists()


def test_migrate_legacy_skill_usage_is_idempotent(tmp_path):
    """Running the migration twice does not overwrite newer state_dir data."""
    mock_paths = _mock_paths(tmp_path)
    mock_paths.state_dir.mkdir(parents=True, exist_ok=True)

    legacy_brain = mock_paths.legacy_dir / "brain"
    legacy_brain.mkdir(parents=True)
    (legacy_brain / "skill_usage.json").write_text('{"legacy": 1}', encoding="utf-8")

    # First run: migrates.
    assert migrate_legacy_skill_usage(mock_paths) is True
    assert mock_paths.skill_usage_json.read_text() == '{"legacy": 1}'

    # Overwrite state_dir with newer data.
    mock_paths.skill_usage_json.write_text('{"newer": 99}', encoding="utf-8")

    # Second run: no-op — state_dir file already exists.
    assert migrate_legacy_skill_usage(mock_paths) is False
    assert mock_paths.skill_usage_json.read_text() == '{"newer": 99}'


# ---------------------------------------------------------------------------
# RoleResolver path
# ---------------------------------------------------------------------------


def test_role_resolver_uses_platformdirs_data_dir(tmp_path, monkeypatch):
    """RoleResolver._roles_dir is under data_dir/skills/roles, not ~/.tokoboros."""
    import tokoboros.core.sub_agent as sub_agent_mod

    mock_paths = _mock_paths(tmp_path)
    monkeypatch.setattr(sub_agent_mod, "get_tokoboros_paths", lambda: mock_paths)

    resolver = sub_agent_mod.RoleResolver()
    assert resolver._roles_dir == mock_paths.data_dir / "skills" / "roles"
    assert ".tokoboros" not in str(resolver._roles_dir)


def test_role_resolver_resolves_builtin_role(tmp_path, monkeypatch):
    """Built-in roles (default_skills/roles/) still resolve after the path change."""
    import tokoboros.core.sub_agent as sub_agent_mod

    mock_paths = _mock_paths(tmp_path)
    monkeypatch.setattr(sub_agent_mod, "get_tokoboros_paths", lambda: mock_paths)

    resolver = sub_agent_mod.RoleResolver()
    # "coder" is a shipped built-in role.
    path = resolver.resolve("coder")
    assert path.exists()
    assert path.stem == "coder"


def test_role_resolver_falls_back_to_legacy_roles(tmp_path, monkeypatch):
    """If a role exists only in the legacy dir, it is found via the legacy shim."""
    import tokoboros.core.sub_agent as sub_agent_mod

    mock_paths = _mock_paths(tmp_path)
    monkeypatch.setattr(sub_agent_mod, "get_tokoboros_paths", lambda: mock_paths)

    # Create a custom role in the legacy location.
    legacy_roles = tmp_path / "legacy" / "skills" / "roles"
    legacy_roles.mkdir(parents=True)
    custom_role = legacy_roles / "custom_legacy.og"
    custom_role.write_text("::meta\n  title: Custom Legacy\n  version: 1.0.0\n", encoding="utf-8")

    # Point the legacy shim to our tmp_path legacy dir.
    resolver = sub_agent_mod.RoleResolver()
    resolver._legacy_roles_dir = legacy_roles  # override for test isolation

    path = resolver.resolve("custom_legacy")
    assert path == custom_role


def test_role_resolver_explicit_workspace_dir_disables_legacy_shim(tmp_path):
    """When workspace_dir is passed explicitly, the legacy shim is not applied."""
    from tokoboros.core.sub_agent import RoleResolver

    resolver = RoleResolver(workspace_dir=tmp_path)
    assert resolver._legacy_roles_dir is None


@pytest.mark.parametrize("role", ["analyst", "coder", "researcher"])
def test_role_resolver_lists_builtin_roles(tmp_path, monkeypatch, role):
    """list_available_roles() includes all shipped roles."""
    import tokoboros.core.sub_agent as sub_agent_mod

    mock_paths = _mock_paths(tmp_path)
    monkeypatch.setattr(sub_agent_mod, "get_tokoboros_paths", lambda: mock_paths)

    resolver = sub_agent_mod.RoleResolver()
    assert role in resolver.list_available_roles()
