"""T-74 — repo hygiene: no blanket asset globs, no no-op extras, no sdist worktree leak.

Three invariants that are genuinely mechanical, permanent, and worth locking — all three
are exactly the trap that produced the finding (see `docs/audit/2026-07-19/tasks/
T-74-repo-hygiene-sweep.md` and `docs/bug-bank.md` BB-139):

1. An unanchored `*.png` / `*.jpg` / `*.jpeg` / `*.gif` / `*.html` line in `.gitignore`
   does not filter noise — it silently decides what CAN be tracked. `git add` succeeds,
   `git status` stays clean, the file never lands. It hid 52.6 MB of design-tool working
   trees from every git command for weeks.
2. A `[project.optional-dependencies]` extra whose every pin is already in
   `dependencies` installs nothing — `pip install hachi-og[memory]` promised something
   base already provides.
3. hatchling's default sdist VCS-exclusion only reads the repo-ROOT `.gitignore` — it
   never walks into nested `.gitignore` files, so `.claude/worktrees/` (excluded from git
   only via the nested `.claude/.gitignore`) was swept into every built sdist wholesale.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

ROOT = REPO_ROOT
BLANKET = {"*.png", "*.jpg", "*.jpeg", "*.gif", "*.html"}


def test_no_blanket_asset_globs_in_gitignore() -> None:
    """A repo-wide *.png / *.html rule silently swallows tracked files.

    Regression: these globs hid 52.6 MB of design trees from `git status` and made
    any future tracked HTML or image un-addable with NO signal — `git add` succeeds,
    the file never lands. `!frontend/index.html` was the single fossil of that.
    """
    lines = [
        ln.strip()
        for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    offenders = sorted(BLANKET.intersection(lines))
    assert not offenders, (
        f"unanchored asset globs in .gitignore: {offenders} — scope them to a "
        "directory (e.g. docs/**/*.html) so tracked assets stay trackable"
    )


def test_no_no_op_optional_dependencies() -> None:
    """An extra whose every pin is already in `dependencies` installs nothing."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = set(data["project"]["dependencies"])
    extras = data["project"].get("optional-dependencies", {})
    noop = sorted(
        name
        for name, deps in extras.items()
        if name not in {"dev", "security"} and deps and set(deps).issubset(base)
    )
    assert not noop, (
        f"no-op extras (every pin already in base): {noop} — delete them, or move "
        "the dependency out of `dependencies` so the extra means something"
    )


@pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv is required to build a real sdist for this check"
)
def test_sdist_excludes_claude_worktrees(tmp_path: Path) -> None:
    """A fresh sdist must not sweep in `.claude/worktrees/` (stale agent worktree copies).

    Regression: hatchling's default sdist VCS-exclusion only reads the repo-ROOT
    `.gitignore` (verified against the installed hatchling source —
    `hatchling/builders/config.py::vcs_exclusion_files` calls
    `locate_file(root, ".gitignore", boundary=".git")`, which finds exactly one file and
    never walks into nested `.gitignore` files). `.claude/worktrees/` is excluded from git
    ONLY via the nested `.claude/.gitignore:4`, so — without an explicit
    `[tool.hatch.build.targets.sdist].exclude` — a fresh `uv build` swept 60,643,104 B of
    stale agent worktree checkouts (~80% of the archive's uncompressed content, sdist:wheel
    ratio ~18x) into the sdist. This drives the REAL build backend rather than
    re-implementing hatchling's matcher, so it catches a regression in the actual tool
    chain (e.g. someone deleting the `pyproject.toml` exclude), not just a config typo.
    """
    result = subprocess.run(
        ["uv", "build", "--sdist", "-o", str(tmp_path), str(ROOT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"uv build --sdist failed:\n{result.stdout}\n{result.stderr}"

    sdists = list(tmp_path.glob("*.tar.gz"))
    assert len(sdists) == 1, f"expected exactly one built sdist in {tmp_path}, found {sdists}"

    with tarfile.open(sdists[0]) as tf:
        leaked = sorted(
            m.name for m in tf.getmembers() if ".claude/worktrees" in m.name.replace("\\", "/")
        )
    assert not leaked, (
        f"sdist contains {len(leaked)} file(s) under .claude/worktrees/ (first: "
        f"{leaked[0]!r}) — restore `[tool.hatch.build.targets.sdist].exclude` in "
        "pyproject.toml"
    )
