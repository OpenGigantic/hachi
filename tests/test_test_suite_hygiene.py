"""Meta-test: guard the suite against new wall-clock duration assertions.

T-71: ten tests used to assert on `time.monotonic()` deltas (`elapsed < 0.05`) or raw
cross-thread timestamps, and nothing stopped an eleventh from being added. Under
`pytest-xdist` (`-n auto`), parallel workers steal the CPU those assertions assume,
so a growing pile of them is a straight line back to a suite that can only run
sequentially. This test scans the real `tests/` tree for the `elapsed <` / `wall_time <`
idiom and fails if any file outside the allowlist below contains one.

Regression: eight `elapsed <` / `wall_time <` sites (across seven files) plus two more
in `test_loop_backstop_cancel.py` (ten sites, eight files total) forced the entire
suite to run sequentially, and nothing enforced that ceiling — see docs/bug-bank.md.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PATTERN = re.compile(r"assert\s+(elapsed|wall_time)\s*<")

# Shrink this to the smallest possible set as you convert. It may only grow with a
# written reason attached (below) -- never silently.
#
# tests/test_p2_03c_cost_latency.py is deliberately absent: its assertions on
# avg_engine_ms / total_estimate_ms are structural invariants on model-computed
# values, not `elapsed <` / `wall_time <` wall-clock comparisons (T-71 section 3.5),
# so they never match PATTERN in the first place.
#
# tests/test_loop_backstop_cancel.py stays on this list permanently (not a TODO):
# its two assertions (`test_wall_deadline_fires_when_stream_stops_delivering_chunks`,
# `test_cancel_fires_when_stream_stops_delivering_chunks`) exercise ExecutionLoop's
# real `max_wall_seconds` deadline and `cancel()` interrupting a stalled stream --
# genuine production timeout/cancellation behaviour, not "did not block" proxies, and
# each already carries a 5x margin (`elapsed < 5.0` against a documented 1.0s
# deadline). Converting them would mean injecting a clock into ExecutionLoop
# (src/hachi/core/loop.py), which is not on this wave's T-71 file allow-list.
# Left alone deliberately, per T-71's own guidance: "convert or leave alone —
# never loosen", and an empty set is not the acceptance bar, a *documented* set is.
KNOWN_WALL_CLOCK = {
    "test_loop_backstop_cancel.py",
}


def test_no_new_wall_clock_duration_assertions() -> None:
    """Wall-clock duration assertions starve under `-n auto` and gate the whole suite.

    Regression: ten such assertion sites (across eight files) forced the entire
    suite to run sequentially, and nothing stopped an eleventh being added.
    """
    offenders = sorted(
        p.name
        for p in TESTS.rglob("test_*.py")
        if p.name not in KNOWN_WALL_CLOCK and PATTERN.search(p.read_text(encoding="utf-8"))
    )
    assert not offenders, (
        "new wall-clock duration assertions found (assert an injected clock, a "
        "threading.Event handoff, or a threading.Barrier instead — see T-71): "
        f"{offenders}"
    )
