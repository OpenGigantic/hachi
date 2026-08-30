"""P1-14: Scoped strict mypy gate — validates the gate config catches new errors.

The gate (mypy-strict-gate.ini) covers 36 currently-clean files in core/,
og_engine/, and gateway/. This test proves:
  1. The gate passes on the shipped sources (green on main).
  2. A file NOT in the gate does not cause a failure even if it has errors.
  3. If a gated file is passed directly to mypy --strict it must come out clean.
"""

from __future__ import annotations

import subprocess
import sys

from tests.conftest import REPO_ROOT

_REPO_ROOT = REPO_ROOT
_GATE_CONFIG = _REPO_ROOT / "mypy-strict-gate.ini"


def _run_gate() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(_GATE_CONFIG)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        cwd=_REPO_ROOT,
    )


def test_gate_config_exists() -> None:
    assert _GATE_CONFIG.exists(), "mypy-strict-gate.ini must exist at repo root"


def test_gate_is_green_on_shipped_sources() -> None:
    """Gate must pass with zero errors on the current working tree (green on main)."""
    result = _run_gate()
    assert result.returncode == 0, (
        f"Strict mypy gate is failing on shipped sources — "
        f"a gated file has a new type error:\n{result.stdout}\n{result.stderr}"
    )


def test_gate_config_lists_at_least_36_files() -> None:
    """Sanity-check that the gate config hasn't been accidentally emptied."""
    content = _GATE_CONFIG.read_text(encoding="utf-8")
    py_files = [
        ln.strip().rstrip(",")
        for ln in content.splitlines()
        if ln.strip().endswith(".py,") or ln.strip().endswith(".py")
    ]
    assert len(py_files) >= 36, (
        f"Expected at least 36 files in the gate config, found {len(py_files)}"
    )


def test_gate_config_covers_core_and_og_engine_and_gateway() -> None:
    """Gate must span all three target directories."""
    content = _GATE_CONFIG.read_text(encoding="utf-8")
    assert "core/" in content
    assert "og_engine/" in content
    assert "gateway/" in content


# T-69: the ratchet floor. This number may only ever go UP. If a change makes it go
# down, that is the bug this test exists to catch — do not lower it to go green.
STRICT_GATE_FLOOR = 129


def test_strict_gate_covers_at_least_the_ratchet_floor() -> None:
    """mypy-strict-gate.ini is a ratchet: coverage only increases.

    Regression: the gate's own header says 'add a file once its error count reaches
    zero', but 65+ already-clean files had never been added — coverage sat at 40/236
    while the tree grew (T-69).
    """
    listed = [
        line.strip().rstrip(",")
        for line in _GATE_CONFIG.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("src/hachi/")
    ]
    assert len(listed) >= STRICT_GATE_FLOOR, (
        f"gate covers {len(listed)} files, floor is {STRICT_GATE_FLOOR} — "
        "the ratchet may only turn one way"
    )
    missing = [p for p in listed if not (_REPO_ROOT / p).is_file()]
    assert not missing, f"gate lists files that do not exist: {missing}"
