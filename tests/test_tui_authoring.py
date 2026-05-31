from __future__ import annotations

from pathlib import Path

from tokoboros.tui.authoring import (
    apply_authoring_update,
    discover_authoring_targets,
    generate_unified_patch,
    is_authoring_target,
)


def test_discover_targets_and_patch_apply(tmp_path: Path) -> None:
    workspace = tmp_path / ".tokoboros"
    skills = workspace / "skills"
    skills.mkdir(parents=True, exist_ok=True)

    brain = workspace / "brain.og"
    config = workspace / "tokoboros.json"
    skill = skills / "sample.og"

    brain.write_text("old brain\n", encoding="utf-8")
    config.write_text("{}\n", encoding="utf-8")
    skill.write_text("::node[sample]\n", encoding="utf-8")

    targets = discover_authoring_targets(workspace, skills)
    assert brain in targets
    assert config in targets
    assert skill in targets

    original = brain.read_text(encoding="utf-8")
    updated = "new brain\n"
    patch = generate_unified_patch(brain, original, updated)

    assert "--- a/brain.og" in patch
    assert "+new brain" in patch

    apply_authoring_update(brain, updated)
    assert brain.read_text(encoding="utf-8") == updated


def test_is_authoring_target_limits_scope(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    inside = workspace / "brain.og"
    outside = tmp_path / "outside.og"
    wrong_ext = workspace / "binary.bin"

    inside.write_text("ok", encoding="utf-8")
    outside.write_text("no", encoding="utf-8")
    wrong_ext.write_text("no", encoding="utf-8")

    assert is_authoring_target(inside, workspace) is True
    assert is_authoring_target(outside, workspace) is False
    assert is_authoring_target(wrong_ext, workspace) is False
