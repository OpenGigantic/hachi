"""Targeted authoring helpers for Tokoboros runtime assets."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Tuple

_ALLOWED_SUFFIXES = {".og", ".json", ".md", ".txt"}


def discover_authoring_targets(workspace_dir: Path, skills_dir: Path, limit: int = 200) -> Tuple[Path, ...]:
    """Return curated editable assets for v1 authoring workflows."""
    targets: list[Path] = []

    for path in (
        workspace_dir / "brain.og",
        workspace_dir / "tokoboros.json",
        workspace_dir / "device-permissions.og",
    ):
        if path.exists() and path.is_file():
            targets.append(path)

    if skills_dir.exists():
        for skill_path in sorted(skills_dir.rglob("*.og")):
            if skill_path.is_file():
                targets.append(skill_path)

    deduped: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = str(target.resolve())
        if key not in seen:
            seen.add(key)
            deduped.append(target)
        if len(deduped) >= limit:
            break

    return tuple(deduped)


def is_authoring_target(path: Path, workspace_dir: Path) -> bool:
    """Validate that edits stay within runtime workspace and supported file types."""
    try:
        resolved = path.resolve()
        workspace = workspace_dir.resolve()
        resolved.relative_to(workspace)
    except Exception:
        return False

    return resolved.suffix.lower() in _ALLOWED_SUFFIXES and resolved.is_file()


def generate_unified_patch(path: Path, original: str, updated: str) -> str:
    """Build a unified patch preview for review/apply decisions."""
    original_lines = original.splitlines(keepends=True)
    updated_lines = updated.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            original_lines,
            updated_lines,
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            n=3,
        )
    )


def apply_authoring_update(path: Path, updated: str) -> None:
    """Apply accepted authoring updates to disk."""
    path.write_text(updated, encoding="utf-8")
