#!/usr/bin/env python
"""Hachi preflight — the single local gate that everything ships through.

Run it before proposing a push:

    uv run python scripts/preflight.py            # full gate (mirrors CI)
    uv run python scripts/preflight.py --quick    # fast inner-loop (skips full pytest + FE build)
    uv run python scripts/preflight.py --no-frontend

Exit code is 0 **only** if every blocking check passes. "Looks done" is not done —
a change the gate can't verify is a change that doesn't land (see CLAUDE.md).

Design notes for this repo specifically:
- There is deliberately **no global `ruff format --check`** step: Hachi is not
  fully ruff-formatted (a repo-wide reformat reflows ~100 files and is forbidden by
  CLAUDE.md Rule 3). Formatting is kept clean *per touched file* by the
  PostToolUse auto-format hook instead — see .claude/hooks/auto_format.py.
- `mypy` runs the **scoped strict gate** (mypy-strict-gate.ini), not the full tree:
  the full tree has known pre-existing debt and is advisory-only in CI.
- pytest runs the **full** suite (the contract, CLAUDE.md Rule 2) serially, `-n 0`
  (T-71 REVERTED): `-n auto` exposed three pre-existing races in **production
  code** under `src/hachi/` -- the parallel suite is the detector, not the
  location -- see the comment above the "pytest (full suite)" Step below for
  the full account and file anchors. Eight of the ten wall-clock-duration /
  cross-thread-timestamp assertion sites (across seven files) that T-71
  converted to injected clocks / threading.Event / threading.Barrier handoffs
  stay regardless of `-n` (tests/test_test_suite_hygiene.py still locks this
  down); `test_loop_backstop_cancel.py`'s other two sites still assert a real
  production deadline (5x margin) and remain the deliberate holdout. Still
  NOT `-p no:xdist`: that disables the plugin and makes `--dist=loadfile` in
  `addopts` an unrecognized argument (see pyproject.toml).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# Windows consoles default to cp1252 and choke on non-ASCII output; force UTF-8 so the
# gate never crashes on a glyph. Best-effort.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# ANSI colours, disabled when output is not a TTY (CI logs, redirects).
_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


GREEN = lambda s: _c("32", s)  # noqa: E731
RED = lambda s: _c("31", s)  # noqa: E731
YELLOW = lambda s: _c("33", s)  # noqa: E731
DIM = lambda s: _c("2", s)  # noqa: E731
BOLD = lambda s: _c("1", s)  # noqa: E731


@dataclass
class Step:
    """One gate check. `cmds` is a sequence of argv lists run in order; the step
    fails if any command exits non-zero (or its executable is missing)."""

    name: str
    cmds: list[list[str]]
    blocking: bool = True
    in_quick: bool = True
    needs_frontend: bool = False


# The gate. Ordered fast-to-slow so cheap failures surface first.
STEPS: list[Step] = [
    Step("ruff check", [["uv", "run", "ruff", "check", "src", "tests"]]),
    Step(
        "mypy strict-gate",
        [["uv", "run", "python", "-m", "mypy", "--config-file", "mypy-strict-gate.ini"]],
    ),
    Step("skill-lint (.og)", [["uv", "run", "python", "scripts/lint_skills.py"]]),
    # Guardrails (CF-17 / T-67): mechanical enforcement of the bans recorded in CLAUDE.md
    # "Out of scope" (e.g. no TOKOBOROS_* compat shims) -- a ban with no check is advice,
    # not a gate. Blocking from day one: its baseline is zero the moment the banned
    # pattern is deleted, so there is nothing to ratchet. See scripts/check_bans.py.
    Step("ban-lint (reintroduced shims)", [["uv", "run", "python", "scripts/check_bans.py"]]),
    # Architectural rail (Guardrails Phase 2) -- enforces the import layering so the domain
    # can't grow a new dependency on its delivery edges. Blocking & green; see
    # pyproject [tool.importlinter]. A new cross-layer import fails here.
    Step("import-linter (layers)", [["uv", "run", "lint-imports"]]),
    # Advisory -- mirrors CI (the security job is continue-on-error on main). Surfaced, never blocks.
    Step(
        "security audit (high)",
        [["uv", "run", "hachi", "security-audit", "--json", "--fail-on-high"]],
        blocking=False,
    ),
    # Advisory rails (Guardrails Phase 2): surfaced every run, never block. Ratchet each to
    # blocking once its baseline reaches zero. Config in pyproject [tool.deptry]/[tool.vulture]
    # and [tool.ruff.lint.pylint]. deptry currently flags 4 declared-but-unused SDK extras.
    Step("deptry (unused/missing deps)", [["uv", "run", "deptry", "src"]], blocking=False),
    Step("vulture (dead code)", [["uv", "run", "vulture"]], blocking=False),
    Step(
        "complexity (PLR outliers)",
        [
            [
                "uv",
                "run",
                "ruff",
                "check",
                "src",
                "--select",
                "PLR0911,PLR0912,PLR0913,PLR0915",
                "--statistics",
            ]
        ],
        blocking=False,
    ),
    # Docs rail (Guardrails Phase 3): the ADRs under docs/architecture/adr/ stay well-formed and
    # indexed (no orphans, NNNN-kebab names, required sections). Baseline is clean, so this can
    # ratchet to blocking; kept advisory for one cycle like the other rails. See scripts/check_adrs.py.
    Step("adr-lint (docs)", [["uv", "run", "python", "scripts/check_adrs.py"]], blocking=False),
    # Advisory rail (CF-1 / T-14 / BB-107): a defaulted getattr against one of our own
    # types converts a missing attribute into a silent fallback -- it masked four dead
    # features in the 2026-07-19 audit. Baseline is clean (fully allowlisted with a
    # reason per entry); ratchet to blocking once the allowlist has been stable for one
    # cycle. See scripts/check_getattr.py.
    Step(
        "getattr-lint (own types)",
        [["uv", "run", "python", "scripts/check_getattr.py"]],
        blocking=False,
    ),
    Step("eval regression", [["uv", "run", "hachi", "eval", "run"]]),
    # BLOCKING (T-13 / CF-1 / BB-127): the structural fix for "~4,100 green tests
    # could not detect eight completely dead features" -- boots a REAL session
    # (the only test in the repo that calls the real boot_agent(), not a fake)
    # into an isolated tmp_path profile and asserts one live property per
    # formerly-dead T-04..T-12 feature. Measured ~8-10s locally -- kept in_quick
    # so the fast inner loop still catches the next dead feature.
    Step(
        "feature liveness (boot smoke)",
        [["uv", "run", "pytest", "-q", "tests/test_feature_liveness.py"]],
        blocking=True,
    ),
    Step(
        "frontend typecheck",
        [["npm", "run", "--prefix", "frontend", "typecheck"]],
        needs_frontend=True,
    ),
    Step("frontend vitest", [["npm", "run", "--prefix", "frontend", "test"]], needs_frontend=True),
    Step(
        "openapi <-> frontend contract",
        [
            ["uv", "run", "hachi", "gateway-openapi", "--output", "openapi/hachi-openapi.json"],
            ["npm", "run", "--prefix", "frontend", "check:api"],
        ],
        needs_frontend=True,
    ),
    # Slow steps — skipped by --quick.
    # -n 0 (T-71 REVERTED): pinned back to serial, deliberately -- this undoes
    # T-71's flip, it is not a reversion to a pre-T-71 state. `-n auto` flipped
    # after three consecutive full-suite parallel runs came back green (4640
    # passed / 21 skipped, 0 failed, each run), then went red roughly 1-in-5 on a
    # quiet machine and 2-for-2 under concurrent load, a DIFFERENT test failing
    # each time. Investigation proved three independent race mechanisms -- all
    # in production code under src/hachi/, none in test-harness code. Running
    # the suite in parallel is what EXPOSED them; it did not create them:
    #   1. Config write race -- src/hachi/core/config_loader.py -- THREE
    #      distinct defects, not one:
    #      (a) `_CONFIG_LOCK` (:33) is a threading.Lock: process-local, so it
    #          gives zero cross-process serialisation. Neither fix below
    #          touches this one.
    #      (b) Read-modify-write (:371-372): `for key, value in
    #          payload.items(): doc[key] = value` reapplies the caller's
    #          already-stale FULL payload over the doc freshly read at
    #          :365-366. THIS is the silent lost update -- no exception, any
    #          platform, any tmp filename.
    #      (c) Fixed-name `.tmp` sibling (:375), shared by every writer: THIS
    #          is what collides -- WinError 32 / FileNotFoundError on the
    #          second `os.replace`. It is the only one of the three that
    #          `tempfile.mkstemp()` (src/hachi/core/atomic_io.py:56) fixes.
    #          Adopting it alone leaves (a) and (b) fully intact and would
    #          tell the fixer they're done when they are not -- exactly the
    #          partial fix this revert exists to prevent.
    #      NOT TEST-ONLY: this is a live defect the suite happens to make
    #      reproducible, not an artifact of testing it. Real writers: five
    #      call sites in gateway/in_process_runtime.py, three in
    #      gateway/provider_admin.py, plus gateway/routers/integrations.py,
    #      cli/main.py, core/onboarding.py, channels/telegram_setup.py, and
    #      others. tools/config_tools.py wires `set_agent_config` as an
    #      agent-callable tool, so an AGENT TURN can write settings.toml while
    #      a CLI or gateway process does -- no test harness involved, the
    #      single strongest piece of evidence this is a live-path bug.
    #      provider_admin.py's own module docstring (:12-15) states the
    #      load->mutate->write section "is serialised by `_CONFIG_LOCK`" --
    #      true within one process, false across processes, and a production
    #      module is relying on it today.
    #   2. Wall-clock startup deadline -- src/hachi/gateway/control_plane.py:1474:
    #      a hard `deadline = time.time() + 5.0` spin waiting for the
    #      `gateway-http` thread to come up. Under N-way CPU contention from other
    #      xdist workers, that thread does not get scheduled inside the budget.
    #   3. chromadb write-then-read window -- src/hachi/core/memory/vault.py:
    #      1025-1049: `_query_collection` queries immediately after an upsert and
    #      can hit `InternalError: ... Nothing found on disk`, which is caught and
    #      fails open to `[]` -- the one of the three that corrupts results
    #      silently rather than erroring.
    # The config race alone is a multi-day fix to code with prior-incident
    # history; a partial fix would lower the flake rate without curing it, which
    # is strictly worse than an honest slow gate. THE FIX IS TO REPAIR THESE
    # THREE, not to add retries, `flaky` markers, `xdist_group`, reruns, or
    # per-test exclusions. T-71's eight converted wall-clock-duration /
    # cross-thread-timestamp assertion sites (across seven files -- injected
    # clocks / threading.Event / threading.Barrier) STAY
    # regardless of `-n` -- they are correct independently of the parallelism
    # decision, and tests/test_test_suite_hygiene.py still enforces that no new
    # `elapsed <` / `wall_time <` assertion lands outside its allowlist. Still NOT
    # `-p no:xdist` (BB-166): that disables the plugin outright and makes
    # `--dist=loadfile` in addopts an unrecognized argument. Before flipping this
    # back to `-n auto`, fix the three races above -- then requalify with the
    # arithmetic, not three green runs: at this ~20%-per-run failure rate,
    # three greens is only a ~51% coin flip (0.8^3) -- which is exactly what
    # T-71's own "three consecutive" bar was, not proof. ~14 consecutive
    # full-suite green runs clears it at ~95% confidence. `-n auto` also
    # manufactures its own N-way contention among workers, so there is no such
    # thing as a low-contention "quiet" run to requalify on.
    Step("pytest (full suite)", [["uv", "run", "pytest", "-q", "-n", "0"]], in_quick=False),
    Step(
        "frontend build",
        [["npm", "run", "--prefix", "frontend", "build"]],
        in_quick=False,
        needs_frontend=True,
    ),
    # BLOCKING (T-22 / BB-123): fails the gate if a demo/mock fixture string ever ships
    # in the production web bundle again. Must run immediately after "frontend build" so
    # it always inspects fresh output -- see scripts/check_bundle.py's module docstring
    # for the marker-list scope note (deliberately excludes strings duplicated inline in
    # store.ts's 13 untouched `dataMode === "live"` mock forks; those remain in-bundle by
    # design until the Wave-2 CF-16 store-slice work lands).
    Step(
        "frontend bundle purity (no demo fixtures)",
        [["uv", "run", "python", "scripts/check_bundle.py"]],
        in_quick=False,
        needs_frontend=True,
    ),
]


@dataclass
class Result:
    step: Step
    status: str  # "pass" | "fail" | "skip"
    seconds: float = 0.0
    detail: str = ""
    tail: str = ""


def _resolve(cmd: list[str]) -> list[str] | None:
    """Resolve the leading executable on PATH (handles npm.cmd / uv.exe on Windows)."""
    exe = shutil.which(cmd[0])
    return None if exe is None else [exe, *cmd[1:]]


def run_step(step: Step) -> Result:
    start = time.monotonic()
    for cmd in step.cmds:
        resolved = _resolve(cmd)
        if resolved is None:
            return Result(
                step, "fail", time.monotonic() - start, detail=f"`{cmd[0]}` not found on PATH"
            )
        proc = subprocess.run(
            resolved,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            combined = (proc.stdout or "") + (proc.stderr or "")
            tail = "\n".join(combined.strip().splitlines()[-40:])
            return Result(
                step,
                "fail",
                time.monotonic() - start,
                detail=f"exit {proc.returncode}: {' '.join(cmd)}",
                tail=tail,
            )
    return Result(step, "pass", time.monotonic() - start)


def main() -> int:
    parser = argparse.ArgumentParser(description="Hachi preflight gate")
    parser.add_argument(
        "--quick", action="store_true", help="skip the slow steps (full pytest, FE build)"
    )
    parser.add_argument("--no-frontend", action="store_true", help="skip all frontend (npm) steps")
    args = parser.parse_args()

    frontend_ready = (FRONTEND / "node_modules").is_dir()
    print(BOLD("\nHachi preflight") + DIM(f"  ({'quick' if args.quick else 'full'} mode)\n"))

    results: list[Result] = []
    for step in STEPS:
        if args.quick and not step.in_quick:
            results.append(Result(step, "skip", detail="--quick"))
            print(f"  {YELLOW('skip')}  {step.name} {DIM('(--quick)')}")
            continue
        if step.needs_frontend and (args.no_frontend or not frontend_ready):
            why = (
                "--no-frontend"
                if args.no_frontend
                else "frontend/node_modules absent (npm install --prefix frontend)"
            )
            results.append(Result(step, "skip", detail=why))
            print(f"  {YELLOW('skip')}  {step.name} {DIM('(' + why + ')')}")
            continue

        print(f"  {DIM('....')}  {step.name} ...", end="\r", flush=True)
        res = run_step(step)
        results.append(res)
        if res.status == "pass":
            mark = GREEN("pass")
        elif res.step.blocking:
            mark = RED("FAIL")
        else:
            mark = YELLOW("warn")
        print(f"  {mark}  {step.name} {DIM(f'({res.seconds:.1f}s)')}            ")
        if res.status == "fail":
            label = "blocking" if res.step.blocking else "advisory"
            print(DIM(f"        -> {label}: {res.detail}"))
            if res.tail:
                for line in res.tail.splitlines():
                    print(DIM(f"        | {line}"))

    # Summary
    failed = [r for r in results if r.status == "fail" and r.step.blocking]
    skipped = [r for r in results if r.status == "skip"]
    passed = [r for r in results if r.status == "pass"]
    print()
    print(
        BOLD("  Summary: ") + f"{GREEN(str(len(passed)) + ' passed')}, "
        f"{RED(str(len(failed)) + ' failed') if failed else '0 failed'}, "
        f"{YELLOW(str(len(skipped)) + ' skipped')}"
    )

    if failed:
        print(RED("\n  [X] preflight FAILED -- do not push. Fix the above, then re-run.\n"))
        return 1
    advisory = [r for r in results if r.status == "fail" and not r.step.blocking]
    if advisory:
        print(
            YELLOW(f"  ({len(advisory)} advisory check(s) failed -- informational, not blocking.)")
        )
    if skipped:
        print(
            YELLOW(
                "\n  [OK] all blocking checks passed; some were skipped -- run without --quick"
                " / install frontend deps for the full gate.\n"
            )
        )
        return 0
    print(GREEN("\n  [OK] preflight passed clean. Safe to propose a push.\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
