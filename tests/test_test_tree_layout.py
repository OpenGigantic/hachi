"""T-70 Stage 2 — lock the tree-mirroring invariant so it cannot drift flat again.

Regression: all 358 test files used to sit flat in ``tests/``, so "where are the
tests for module X" had no mechanical answer and duplicate suites drifted apart
(see docs/audit/2026-07-19/tasks/T-70-test-tree-mirroring.md). This file is the
permanent lock for the structural move: every test file must live in a directory
that mirrors a real ``src/hachi`` package, and no two test files may share a
basename (fatal under pytest's default ``prepend`` import mode once files stop
being flat).

Stays at ``tests/`` root permanently — it is about the tree itself, not about a
single package.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from tests.conftest import REPO_ROOT

TESTS = REPO_ROOT / "tests"
SRC = REPO_ROOT / "src" / "hachi"

# Files legitimately at tests/ root: cross-cutting suites that belong to no single
# src/hachi package. Every entry needs a written reason — this set must stay small
# or it defeats the whole point of the move.
ROOT_ALLOWED = {
    # This file: about the tree itself, not about a package.
    "test_test_tree_layout.py",
    # T-13's cross-cutting boot smoke — imports from eight different packages
    # (cli, core, core/routing, core/runtime, core/memory, gateway, ...) and
    # belongs to none of them. Also hard-coded by path in the gate at
    # scripts/preflight.py ("uv run pytest -q tests/test_feature_liveness.py") —
    # moving it would silently break a blocking gate step.
    "test_feature_liveness.py",
    # hachi/daemon.py is the one top-level module directly under src/hachi/ (no
    # src/hachi/daemon/ package directory exists for tests/daemon/ to mirror —
    # every other test package here mirrors a real directory, not a bare module).
    "test_daemon.py",
    # Batch file (P3-07 ops hygiene): tests log rotation + Windows task XML
    # (daemon.py, its plurality subject at 3 of 5 tests), ScheduleRunner.stop
    # (jobs/scheduler.py), and MemoryVault.close (core/memory/vault.py). Splitting
    # a batch file by subject is spec §5 part 2 — deliberately out of scope for
    # this move — so it stays alongside test_daemon.py rather than being
    # force-fit into jobs/ or core/memory/ for one minority test each.
    "test_p3_07_ops_hygiene.py",
    # The following ten test the repository/tooling itself, not any src/hachi
    # package — verified by grep: each imports nothing from `hachi.` at all.
    "test_bundle_guard.py",  # scripts/check_bundle.py (frontend bundle guard)
    "test_check_getattr.py",  # scripts/check_getattr.py
    "test_core_tool_cost_guard.py",  # scripts/measure_turn.py token-budget guard
    "test_docker_assets.py",  # docker/ self-host recipe structural guards
    "test_governance_bans.py",  # scripts/check_bans.py (CLAUDE.md ban enforcement)
    "test_lint_skills_reserved_tags.py",  # scripts/lint_skills.py reserved-tag rule
    "test_memory_package_boundary.py",  # import-path boundary lock, not memory behaviour
    "test_p1_14_mypy_gate.py",  # the mypy gate itself
    "test_repo_hygiene.py",  # T-74 .gitignore / pyproject / sdist guards
    "test_test_suite_hygiene.py",  # T-71 wall-clock-assertion scanner over tests/ itself
    # Same family as test_core_tool_cost_guard.py above: its sole real subject is
    # `from scripts.measure_turn import build_core_registry` -- scripts/ has no
    # mirror under src/hachi/. The two hachi imports it does have (core.paths,
    # a fixture; one docstring mention of gateway.orchestrator) are incidental,
    # not the module under test (verified by reading all 11 test functions).
    "test_measure_turn.py",
    # Structural exception, not a subject-mirroring one: tests/gateway/
    # test_tool_groups_at_registration.py:96 does
    # `from tests.test_delegation_blocklist_integrity import _registered_tool_names`
    # to reuse its AST tool-inventory scanner (T-34, BB-89) rather than duplicating
    # it. That is an absolute dotted import keyed to this exact path, so this file
    # cannot move without either breaking that import or editing its content --
    # both against this move's discipline (spec §0 / §5: zero content edits). Its
    # own subject is tools/delegation_tools.py's BLOCKED_TOOL_NAMES machinery
    # (see the module docstring), which is why it is not a natural root file --
    # it is pinned here only by the cross-import. Spec §3.5's "no cross-test
    # imports" sweep (`rg -n "^\s*(from|import)\s+test_" tests/`) verified there
    # were none, but its pattern only matches the bare `from test_x import` form
    # and misses the qualified `from tests.test_x import` form this site uses --
    # a gap in the spec, found by re-running the sweep against the actual
    # dotted-path form after the move surfaced one real ModuleNotFoundError.
    "test_delegation_blocklist_integrity.py",
}


def test_every_test_file_mirrors_a_src_package() -> None:
    """tests/ mirrors src/hachi/. A file at the root belongs to no module — say which.

    Regression: all 358 test files sat flat in tests/, so 'where are the tests for
    module X' had no mechanical answer and duplicate suites drifted apart.
    """
    orphans = []
    for path in TESTS.rglob("test_*.py"):
        rel = path.relative_to(TESTS)
        if rel.parent == Path("."):
            if path.name not in ROOT_ALLOWED:
                orphans.append(str(rel))
            continue
        if not (SRC / rel.parent).is_dir():
            orphans.append(f"{rel}  (no src/hachi/{rel.parent})")
    assert not orphans, "test files that mirror no src package:\n  " + "\n  ".join(orphans)


def test_no_test_basename_collisions() -> None:
    """pytest runs in 'prepend' import mode: two same-named modules collide fatally.

    This passes both before AND after the tree-mirroring move -- before the move
    every file sits flat at tests/ root, so all 358 basenames are trivially unique
    (there is nothing yet for them to collide with). That is expected and correct,
    not vacuous: this is a permanent guard against a *future* collision once files
    live at different depths, which is exactly the hazard prepend import mode makes
    fatal (two files named e.g. tests/core/test_memory.py and
    tests/tools/test_memory.py would otherwise abort collection with
    "import file mismatch ... unique basename for your test file modules").
    """
    names = Counter(p.name for p in TESTS.rglob("test_*.py"))
    dupes = {n: c for n, c in names.items() if c > 1}
    assert not dupes, f"duplicate test basenames (needs __init__.py or importlib mode): {dupes}"
