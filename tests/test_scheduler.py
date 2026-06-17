"""Tests for the recurring-schedule engine (proactive agents).

All time-dependent behaviour is driven by an injected ``now`` so the tests are
deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hachi.jobs.scheduler import (
    Schedule,
    ScheduleKind,
    Scheduler,
    ScheduleStore,
    cron_matches,
    cron_next,
)


def _scheduler(tmp_path: Path) -> Scheduler:
    return Scheduler(ScheduleStore(tmp_path / "schedules.json"))


# ---- cron matcher ----


def test_cron_matches_basic_fields() -> None:
    # 9:00 on any day
    assert cron_matches("0 9 * * *", datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
    assert not cron_matches("0 9 * * *", datetime(2026, 6, 5, 9, 1, tzinfo=UTC))
    assert not cron_matches("0 9 * * *", datetime(2026, 6, 5, 10, 0, tzinfo=UTC))


def test_cron_step_and_list_and_range() -> None:
    every_15 = "*/15 * * * *"
    assert cron_matches(every_15, datetime(2026, 6, 5, 8, 30, tzinfo=UTC))
    assert not cron_matches(every_15, datetime(2026, 6, 5, 8, 31, tzinfo=UTC))
    # 2026-06-05 is a Friday (isoweekday 5 -> cron dow 5)
    assert cron_matches("0 9 * * 1-5", datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
    assert not cron_matches("0 9 * * 0,6", datetime(2026, 6, 5, 9, 0, tzinfo=UTC))


def test_cron_sunday_is_zero_or_seven() -> None:
    sunday = datetime(2026, 6, 7, 9, 0, tzinfo=UTC)  # a Sunday
    assert cron_matches("0 9 * * 0", sunday)
    assert cron_matches("0 9 * * 7", sunday)


def test_cron_next_finds_following_match() -> None:
    after = datetime(2026, 6, 5, 9, 30, tzinfo=UTC)
    nxt = cron_next("0 9 * * *", after)
    assert nxt == datetime(2026, 6, 6, 9, 0, tzinfo=UTC)


def test_cron_invalid_raises() -> None:
    with pytest.raises(ValueError):
        cron_next("0 9 * *", datetime(2026, 6, 5, tzinfo=UTC))  # only 4 fields


def test_cron_zero_step_raises_valueerror_not_zerodivision() -> None:
    # "*/0" previously raised ZeroDivisionError (uncaught by the tool layer).
    with pytest.raises(ValueError):
        cron_matches("*/0 * * * *", datetime(2026, 6, 5, 9, 0, tzinfo=UTC))


def test_cron_out_of_range_value_raises() -> None:
    # A minute of 99 can never match — reject it instead of creating a dead schedule.
    with pytest.raises(ValueError):
        cron_matches("99 * * * *", datetime(2026, 6, 5, 9, 0, tzinfo=UTC))


# ---- schedule kinds + scheduler ----


def test_once_fires_exactly_once(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    t0 = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    s = sched.add(
        name="reminder",
        task="say hi",
        kind=ScheduleKind.ONCE,
        run_at=t0.isoformat(),
        now=t0 - timedelta(minutes=5),
    )
    assert not s.is_due(t0 - timedelta(minutes=1))
    assert s.is_due(t0)

    fired: list[Schedule] = []
    assert sched.tick(fired.append, now=t0) == [s]
    # already fired -> never due again
    assert sched.due(t0 + timedelta(days=1)) == []
    assert sched.get(s.schedule_id).enabled is False


def test_interval_recurs_and_advances(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    t0 = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    s = sched.add(name="poll", task="check", kind="interval", interval_seconds=3600, now=t0)
    # first fire is one interval after creation
    assert not s.is_due(t0 + timedelta(minutes=30))
    assert s.is_due(t0 + timedelta(hours=1))

    fired = sched.tick(lambda _s: None, now=t0 + timedelta(hours=1))
    assert len(fired) == 1
    assert sched.get(s.schedule_id).run_count == 1
    # advanced to the next hour, not due immediately
    assert not sched.get(s.schedule_id).is_due(t0 + timedelta(hours=1, minutes=1))
    assert sched.get(s.schedule_id).is_due(t0 + timedelta(hours=2))


def test_interval_catch_up_does_not_storm(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    t0 = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    s = sched.add(name="poll", task="check", kind="interval", interval_seconds=60, now=t0)
    # Tick far in the future after many missed intervals: fires once, not N times.
    fired = sched.tick(lambda _s: None, now=t0 + timedelta(hours=5))
    assert len(fired) == 1
    assert sched.get(s.schedule_id).run_count == 1


def test_cron_schedule_due_and_advance(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    created = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)
    s = sched.add(name="daily", task="briefing", kind="cron", cron="0 9 * * *", now=created)
    assert s.next_run == datetime(2026, 6, 5, 9, 0, tzinfo=UTC).isoformat()

    sched.tick(lambda _s: None, now=datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
    assert sched.get(s.schedule_id).next_run == datetime(2026, 6, 6, 9, 0, tzinfo=UTC).isoformat()


def test_failed_fire_does_not_advance(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    t0 = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    s = sched.add(name="x", task="t", kind="interval", interval_seconds=60, now=t0)

    def boom(_s: Schedule) -> None:
        raise RuntimeError("delivery failed")

    fired = sched.tick(boom, now=t0 + timedelta(minutes=2))
    assert fired == []
    assert sched.get(s.schedule_id).run_count == 0
    assert sched.get(s.schedule_id).is_due(t0 + timedelta(minutes=2))  # still due


def test_add_validation_errors(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    with pytest.raises(ValueError):
        sched.add(name="x", task="t", kind="interval", interval_seconds=0)
    with pytest.raises(ValueError):
        sched.add(name="x", task="t", kind="cron", cron="not a cron")
    with pytest.raises(ValueError):
        sched.add(name="x", task="t", kind="once")  # missing run_at


def test_persistence_and_cancel_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "schedules.json"
    sched = Scheduler(ScheduleStore(path))
    t0 = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    s = sched.add(name="daily", task="briefing", kind="cron", cron="0 9 * * *", now=t0)

    # A fresh scheduler reads the persisted schedule back.
    reloaded = Scheduler(ScheduleStore(path))
    assert reloaded.get(s.schedule_id) is not None
    assert reloaded.get(s.schedule_id).name == "daily"

    assert reloaded.cancel(s.schedule_id) is True
    assert Scheduler(ScheduleStore(path)).get(s.schedule_id) is None
