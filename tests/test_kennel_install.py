"""Regression tests for the kennel pack store (T-04 / BB-98).

The skill kennel was a complete lie end-to-end:

1. ``_InProcessGatewayRuntime.get_kennel`` imported
   ``hachi.og_engine.bundled_packs.list_bundled_packs`` -- the wrong *package*
   (the module is ``hachi.core.bundled_packs``) and a symbol that exists
   nowhere in ``src/``. The resulting ``ModuleNotFoundError`` was swallowed by
   a bare ``except Exception: pass``, so ``GET /skills/kennel`` never listed a
   single bundled pack.
2. ``add_skill_from_kennel`` validated ``kennel_id`` as non-empty and then
   never used it again -- no install call existed in the method body -- before
   returning a hardcoded ``{"ok": True, "installed": True}`` for *any* id,
   including ids that do not exist.
3. Its re-index call was ``agent.skill_engine.reindex()``; ``SkillEngine`` has
   no ``reindex`` method, so that raised ``AttributeError`` into another
   ``except Exception: pass``.

These tests assert real filesystem state (a ``.og`` file landing under
``skills_dir/packs/<id>/``) rather than the shape of the returned dict --
today's broken code already returns ``installed: True``, so a dict-shape
assertion alone passes against the bug (see spec ยง7).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hachi.cli.main import _InProcessGatewayRuntime
from hachi.core.paths import HachiPaths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_paths(tmp_path: Path) -> HachiPaths:
    """Return a HachiPaths rooted in *tmp_path* so no real user data is touched.

    ``install_bundled_pack`` has no dry-run mode -- it writes to a real
    directory -- so every test here MUST route through a tmp_path-backed
    ``skills_dir`` or it would write into the developer's real ``~/.hachi``
    data dir (see spec ยง12).
    """
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    cache_dir = tmp_path / "cache"

    for d in (config_dir, data_dir, state_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    paths = HachiPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        state_dir=state_dir,
    )
    paths.skills_dir.mkdir(parents=True, exist_ok=True)
    return paths


@pytest.fixture
def runtime_and_paths(tmp_path: Path):
    """A fresh runtime (no booted agents) paired with tmp_path-backed paths.

    Patches ``hachi.cli.main.get_hachi_paths`` -- the established pattern in
    tests/test_backup_restore.py -- so the module-level name the runtime
    methods resolve at call time is redirected to the fake paths.
    """
    paths = _make_fake_paths(tmp_path)
    runtime = _InProcessGatewayRuntime()
    with patch("hachi.cli.main.get_hachi_paths", return_value=paths):
        yield runtime, paths


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_kennel_lists_real_bundled_packs(runtime_and_paths) -> None:
    runtime, _paths = runtime_and_paths
    kennel = runtime.get_kennel()
    ids = {item["id"] for item in kennel}
    # These five ship in the wheel at src/hachi/packs/.
    assert {"crm-agent", "research-agent", "inbox-sentinel"} <= ids

    crm = next(item for item in kennel if item["id"] == "crm-agent")
    assert crm["description"], "expected real manifest text, not ''"
    assert crm["version"] == "1.0.0"  # from pack.toml `semver`


def test_add_skill_from_kennel_actually_installs_og_files(runtime_and_paths) -> None:
    runtime, paths = runtime_and_paths
    result = runtime.add_skill_from_kennel("inbox-sentinel")
    assert result["ok"] is True

    installed = paths.skills_dir / "packs" / "inbox-sentinel"
    assert installed.exists(), "pack directory was not created -- nothing installed"
    assert any(p.suffix == ".og" for p in installed.iterdir())


def test_add_skill_from_kennel_rejects_unknown_pack(runtime_and_paths) -> None:
    from hachi.gateway.control_plane import GatewayProtocolError

    runtime, paths = runtime_and_paths
    with pytest.raises(GatewayProtocolError):
        runtime.add_skill_from_kennel("no-such-pack")

    # An unknown id must not report success or write anything either.
    assert not (paths.skills_dir / "packs" / "no-such-pack").exists()
