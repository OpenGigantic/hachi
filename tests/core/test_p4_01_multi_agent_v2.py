"""P4-01: Multi-agent v2 — leases, needs continuations, provenance edges.

Tests:
  1. LeaseManager.acquire() succeeds when target is free.
  2. LeaseManager.acquire() fails (conflict) when target is already held.
  3. Prefix match: a lease on 'projects/billing' blocks 'projects/billing/schema'.
  4. LeaseManager.release() clears the held target.
  5. Expired leases (TTL=0) do not block new acquisitions.
  6. LeaseManager.snapshot() returns currently active leases.
  7. SubAgentReport has a 'needs' field defaulting to None.
  8. SubAgentReport.to_orchestrator_string() includes continuation hint when needs set.
  9. MemoryWriter.write_nodes() injects provenance edge when by_agent is set.
  10. MemoryWriter.write_fact() convenience method writes a fact node.
  11. delegate_task blocks and returns error when lease conflict exists.
"""

from __future__ import annotations

import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. LeaseManager.acquire() succeeds for a free target
# ---------------------------------------------------------------------------


def test_lease_acquire_free_target() -> None:
    from hachi.core.lease_manager import LeaseManager

    mgr = LeaseManager()
    ok, conflict = mgr.acquire("researcher", ["projects/billing"])
    assert ok is True
    assert conflict is None


# ---------------------------------------------------------------------------
# 2. LeaseManager.acquire() fails when target is already held
# ---------------------------------------------------------------------------


def test_lease_acquire_conflict() -> None:
    from hachi.core.lease_manager import LeaseManager

    mgr = LeaseManager()
    mgr.acquire("researcher", ["projects/billing"])
    ok, conflict = mgr.acquire("analyst", ["projects/billing"])

    assert ok is False
    assert conflict is not None
    assert "researcher" in conflict


# ---------------------------------------------------------------------------
# 3. Prefix match: lease on 'a/b' blocks 'a/b/c'
# ---------------------------------------------------------------------------


def test_lease_prefix_match() -> None:
    from hachi.core.lease_manager import LeaseManager

    mgr = LeaseManager()
    mgr.acquire("agent-a", ["projects/billing"])
    ok, conflict = mgr.acquire("agent-b", ["projects/billing/schema"])

    assert ok is False, "Sub-prefix should be blocked by parent lease"


# ---------------------------------------------------------------------------
# 4. LeaseManager.release() clears the held target
# ---------------------------------------------------------------------------


def test_lease_release_clears_target() -> None:
    from hachi.core.lease_manager import LeaseManager

    mgr = LeaseManager()
    mgr.acquire("researcher", ["projects/billing"])
    mgr.release("researcher", ["projects/billing"])

    ok, _ = mgr.acquire("analyst", ["projects/billing"])
    assert ok is True, "Target should be available after release"


# ---------------------------------------------------------------------------
# 5. Expired leases (TTL≈0) do not block new acquisitions
# ---------------------------------------------------------------------------


def test_lease_ttl_expiry() -> None:
    """BB-184: TTL and wait derived from the monotonic clock's own resolution.

    ``time.monotonic()``'s resolution is interpreter/platform dependent — CPython 3.11
    on Windows (what CI runs) backs it with ``GetTickCount64()``, resolution 0.015625s
    (15.625ms); this dev box's interpreter backs it with ``QueryPerformanceCounter()``,
    resolution 1e-07s. A hardcoded ``ttl_seconds=0.001`` with a fixed ``sleep(0.01)`` is
    not observable on the coarse clock: two ``time.monotonic()`` reads landing in the
    same ~15.6ms tick come back bit-for-bit equal, so ``now == acquired_at``, therefore
    ``now < acquired_at + 0.001`` and the lease reads as not-yet-expired — flaky
    depending on where in the tick the first read lands (measured empirically at
    ~33% failure on 3.11), not a real product bug.

    Deriving both the TTL and the wait from ``time.get_clock_info('monotonic').resolution``
    keeps this a genuine real-time test (still a real ``time.sleep()``, unlike the frozen
    -clock exact-boundary case in ``test_lease_expiry_boundary_is_inclusive`` above, which
    covers BB-178's ``now == expires_at`` tie separately) while making its timing premise
    valid on any clock. ``wait = 3 * resolution`` is the smallest margin that is safe on
    any tick phase: in the worst case the "acquire" read lands a hair before a tick edge,
    so up to one full tick can elapse before the *next* distinguishable reading —
    ``2 * resolution`` is therefore the analytic minimum (confirmed empirically: 0
    failures/500 trials on 3.11 at exactly 2x, 182/500 at 1x); the third resolution unit
    is margin for scheduler jitter on a loaded CI box, not a guessed inflation.
    """
    from hachi.core.lease_manager import LeaseManager

    resolution = time.get_clock_info("monotonic").resolution
    ttl_seconds = resolution
    wait_seconds = resolution * 3

    mgr = LeaseManager()
    mgr.acquire("researcher", ["projects/old"], ttl_seconds=ttl_seconds)
    time.sleep(wait_seconds)  # let it expire — see BB-184 docstring for why 3x

    ok, _ = mgr.acquire("analyst", ["projects/old"])
    assert ok is True, "Expired lease should not block new acquisition"


def test_lease_expiry_boundary_is_inclusive(monkeypatch) -> None:
    """BB-178: ``_LeaseEntry.is_expired()`` must treat "now == expires_at" as expired,
    not just "now > expires_at". ``test_lease_ttl_expiry`` (above) proves this with a
    real ``time.sleep()``, but a 0.001s TTL racing a coarse Windows clock can leave the
    two reads tied by chance, which is exactly what made that test flaky on CI while
    green here — this test removes the race by controlling the clock directly so the
    boundary itself is what's under test, not timing luck.
    """
    import hachi.core.lease_manager as lease_manager

    mgr = lease_manager.LeaseManager()
    ok, _ = mgr.acquire("researcher", ["projects/old"], ttl_seconds=5.0)
    assert ok is True
    entry = mgr._leases["projects/old"]
    boundary = entry.acquired_at + entry.ttl_seconds  # == entry.expires_at, exactly

    # Freeze "now" to land EXACTLY on the expiry instant — a tie, not "clearly past
    # it" — the same same-tick scenario a coarse clock produces under real timing.
    monkeypatch.setattr(lease_manager.time, "monotonic", lambda: boundary)

    ok, _ = mgr.acquire("analyst", ["projects/old"])
    assert ok is True, "A lease exactly at its TTL boundary must count as expired"


# ---------------------------------------------------------------------------
# 6. LeaseManager.snapshot() returns active leases
# ---------------------------------------------------------------------------


def test_lease_snapshot() -> None:
    from hachi.core.lease_manager import LeaseManager

    mgr = LeaseManager()
    mgr.acquire("researcher", ["projects/billing", "drive.write"])
    snap = mgr.snapshot()

    assert len(snap) == 1
    assert snap[0]["role"] == "researcher"
    assert "expires_in_s" in snap[0]


# ---------------------------------------------------------------------------
# 7. SubAgentReport has 'needs' field defaulting to None
# ---------------------------------------------------------------------------


def test_sub_agent_report_needs_default() -> None:
    from hachi.core.sub_agent import SubAgentReport, SubAgentStatus

    report = SubAgentReport(
        status=SubAgentStatus.SUCCESS,
        summary="done",
        role="researcher",
        task="find billing schema",
    )
    assert report.needs is None


# ---------------------------------------------------------------------------
# 8. SubAgentReport.to_orchestrator_string() includes needs when set
# ---------------------------------------------------------------------------


def test_sub_agent_report_needs_in_string() -> None:
    from hachi.core.sub_agent import SubAgentReport, SubAgentStatus

    report = SubAgentReport(
        status=SubAgentStatus.SUCCESS,
        summary="Research complete.",
        role="researcher",
        task="find billing schema",
        needs={"role": "analyst", "task": "analyse the schema"},
    )
    text = report.to_orchestrator_string()

    assert "Continuation requested" in text
    assert "analyst" in text
    assert "REQUEST" in text  # safety note present


# ---------------------------------------------------------------------------
# 9. MemoryWriter.write_nodes() injects provenance when by_agent set
# ---------------------------------------------------------------------------


def test_memory_writer_provenance_edge(tmp_path: Path) -> None:
    from hachi.core.memory.writer import MemoryNode, MemoryWriter

    writer = MemoryWriter(memory_dir=tmp_path)
    node = MemoryNode(name="billing-fact", role="fact", dense="billing schema v2")
    writer.write_nodes([node], by_agent="researcher")

    written_file = tmp_path / "core_memory.og"
    assert written_file.exists()
    content = written_file.read_text(encoding="utf-8")
    assert "->[:by=agent:researcher]" in content


# ---------------------------------------------------------------------------
# 10. MemoryWriter.write_fact() creates a fact node
# ---------------------------------------------------------------------------


def test_memory_writer_write_fact(tmp_path: Path) -> None:
    from hachi.core.memory.writer import MemoryWriter

    writer = MemoryWriter(memory_dir=tmp_path)
    fact_id = writer.write_fact("billing migration uses pgloader", category="decision")

    assert isinstance(fact_id, str)
    assert fact_id.startswith("fact-")
    # File should exist with a ::node block
    og_file = tmp_path / "core_memory.og"
    assert og_file.exists()
    assert "::node[" in og_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 11. delegate_task returns error when lease conflict exists
# ---------------------------------------------------------------------------


def test_delegate_task_blocked_by_lease() -> None:
    from hachi.core.lease_manager import LeaseManager
    from hachi.core.loop import ToolRegistry
    from hachi.tools.delegation_tools import _make_delegate_task

    mgr = LeaseManager()
    mgr.acquire("existing-agent", ["projects/billing"])

    registry = ToolRegistry()
    handler = _make_delegate_task(
        parent_registry=registry,
        lease_manager=mgr,
    )

    result = handler(role="coder", task="update billing", write_targets=["projects/billing"])
    assert "error" in result.lower() or "blocked" in result.lower(), (
        f"Expected blocked error, got: {result}"
    )
