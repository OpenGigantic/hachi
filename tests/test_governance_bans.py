"""Tests for scripts/check_bans.py — the mechanical enforcement of CLAUDE.md's bans.

CF-17 / T-67: a ban written in prose only (CLAUDE.md "Out of scope") is advice, not a
gate. This module locks the mechanism, not just the current absence of the shim: it
runs the REAL scanner against the REAL tracked tree so a reintroduction fails here
instead of surviving until the next audit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_tokoboros_env_shim_in_src() -> None:
    """CLAUDE.md bans tokoboros shims; the ban must be mechanical, not advisory.

    Regression: a TOKOBOROS_* -> HACHI_* env shim was reintroduced at cli/main.py
    module scope after the hard rename and survived because nothing checked.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_bans.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"ban-lint reported violations:\n{proc.stdout}"
