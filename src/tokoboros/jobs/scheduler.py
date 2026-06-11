"""Recurring schedules for proactive agents.

The job queue (:mod:`tokoboros.jobs.queue`) already handles one-shot delayed work
with retries/backoff. This module adds the missing piece for proactive agents:
**recurring** schedules — ``once`` at a time, ``every`` N seconds, or a ``cron``
expression — persisted across restarts so the agent can act without being asked.

The cron matcher is a dependency-free implementation of the common 5-field subset
(``*``, ``*/n``, ``a,b``, ``a-b``, ``a-b/n``) for minute / hour / day-of-month /
month / day-of-week. Day-of-week uses cron numbering (0 or 7 = Sunday).

The :class:`Scheduler` is pure logic over an injected ``now``, so it is fully
testable without a clock, network, or running agent. Execution/delivery is wired
in by passing a ``fire`` callback to :meth:`Scheduler.tick`.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ScheduleKind(StrEnum):
    """How a schedule recurs."""

    ONCE = "once"  # fire a single time at run_at
    INTERVAL = "interval"  # fire every interval_seconds
    CRON = "cron"  # fire on a 5-field cron expression


# ==========================================
# Cron matching (dependency-free, 5-field)
# ==========================================

# day-of-week upper bound is 7 because cron allows both 0 and 7 for Sunday.
_FIELD_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # min hr dom mon dow


def _parse_cron_field(field_expr: str, lo: int, hi: int) -> set[int]:
    """Expand a single cron field into the concrete set of values it matches."""
    values: set[int] = set()
    for part in field_expr.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, _, step_str = part.partition("/")
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"cron step must be >= 1, got {step} in {part!r}")
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start_str, _, end_str = base.partition("-")
            start, end = int(start_str), int(end_str)
        else:
            start = end = int(base)
        # Reject out-of-range values instead of silently accepting a field that
        # can never match (e.g. minute "99" would otherwise create a dead schedule).
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron field {part!r} out of range [{lo}, {hi}]")
        for v in range(start, end + 1):
            if (v - start) % step == 0:
                values.add(v)
    return values


def _parse_cron(expr: str) -> list[set[int]]:
    """Parse a 5-field cron expression into per-field value sets."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields, got {len(fields)}: {expr!r}")
    return [_parse_cron_field(f, lo, hi) for f, (lo, hi) in zip(fields, _FIELD_BOUNDS, strict=True)]


def cron_matches(expr: str, dt: datetime) -> bool:
    """Return True if ``dt`` (minute resolution) satisfies the cron expression."""
    minute, hour, dom, mon, dow = _parse_cron(expr)
    # cron day-of-week: 0/7 = Sunday; Python isoweekday() is Mon=1..Sun=7
    cron_dow = dt.isoweekday() % 7
    if dt.minute not in minute or dt.hour not in hour or dt.month not in mon:
        return False
    # Standard cron OR semantics between day-of-month and day-of-week when both
    # are restricted; if one is a full wildcard it does not constrain.
    dom_full = dom == set(range(1, 32))
    # dow is unrestricted only when it covers every weekday (Sun-Sat); `*` expands
    # to {0..7}, so test superset of {0..6} (a lone {7} is Sunday-only, NOT full).
    dow_full = set(range(0, 7)) <= dow
    dom_ok = dt.day in dom
    dow_ok = cron_dow in dow or (7 in dow and cron_dow == 0)
    if dom_full and dow_full:
        return True
    if dom_full:
        return dow_ok
    if dow_full:
        return dom_ok
    return dom_ok or dow_ok


def cron_next(expr: str, after: datetime) -> datetime:
    """Return the next datetime strictly after ``after`` that matches ``expr``."""
    _parse_cron(expr)  # validate early
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Cap the search at ~4 years to bound pathological/invalid expressions.
    for _ in range(4 * 366 * 24 * 60):
        if cron_matches(expr, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"no cron match found within 4 years for {expr!r}")


# ==========================================
# Schedule model
# ==========================================


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: str) -> datetime:
    """Parse an ISO datetime, assuming UTC if no timezone is present."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass(slots=True)
class Schedule:
    """A recurring (or one-shot) proactive task."""

    schedule_id: str
    name: str
    task: str  # the instruction handed to the proactive agent when it fires
    kind: ScheduleKind

    # kind-specific timing
    run_at: str | None = None  # ONCE: ISO datetime
    interval_seconds: int | None = None  # INTERVAL
    cron: str | None = None  # CRON: 5-field expression

    # delivery of the result
    deliver_to: str = "chat"  # "chat" | "webhook" | "log"
    webhook_url: str | None = None

    # bookkeeping
    enabled: bool = True
    created_at: str = field(default_factory=lambda: _now().isoformat())
    last_run: str | None = None
    next_run: str | None = None
    run_count: int = 0
    max_retries: int = 3

    def compute_next(self, after: datetime) -> datetime | None:
        """Compute the next fire time strictly after ``after`` (None if finished)."""
        if self.kind == ScheduleKind.ONCE:
            if self.run_count > 0 or not self.run_at:
                return None
            return _parse_dt(self.run_at)
        if self.kind == ScheduleKind.INTERVAL:
            if not self.interval_seconds:
                return None
            base = _parse_dt(self.last_run) if self.last_run else _parse_dt(self.created_at)
            nxt = base + timedelta(seconds=self.interval_seconds)
            # Catch up without firing in a storm if ticks were missed.
            while nxt <= after:
                nxt += timedelta(seconds=self.interval_seconds)
            return nxt
        if self.kind == ScheduleKind.CRON and self.cron:
            return cron_next(self.cron, after)
        return None

    def is_due(self, now: datetime) -> bool:
        return self.enabled and self.next_run is not None and _parse_dt(self.next_run) <= now

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        data = dict(data)
        data["kind"] = ScheduleKind(data["kind"])
        return cls(**data)


# ==========================================
# Persistent store
# ==========================================


class ScheduleStore:
    """JSON-backed persistence for schedules (survives restart)."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()

    def load(self) -> list[Schedule]:
        with self._lock:
            if not self._path.exists():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return [Schedule.from_dict(d) for d in raw]
            except Exception as e:
                logger.warning("Failed to load schedules from %s: %s", self._path, e)
                return []

    def save(self, schedules: list[Schedule]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps([s.to_dict() for s in schedules], indent=2),
                encoding="utf-8",
            )


# ==========================================
# Scheduler
# ==========================================


class Scheduler:
    """Manage recurring schedules over an injected clock.

    All time-dependent methods accept an explicit ``now`` (defaulting to the real
    UTC clock) so behaviour is deterministic and unit-testable.
    """

    def __init__(self, store: ScheduleStore):
        self._store = store
        self._schedules: dict[str, Schedule] = {s.schedule_id: s for s in store.load()}

    # ---- queries ----

    def list_schedules(self) -> list[Schedule]:
        return list(self._schedules.values())

    def get(self, schedule_id: str) -> Schedule | None:
        """Return a schedule by exact id, or by unique prefix."""
        if schedule_id in self._schedules:
            return self._schedules[schedule_id]
        matches = [s for sid, s in self._schedules.items() if sid.startswith(schedule_id)]
        return matches[0] if len(matches) == 1 else None

    def due(self, now: datetime | None = None) -> list[Schedule]:
        now = now or _now()
        return [s for s in self._schedules.values() if s.is_due(now)]

    # ---- mutations ----

    def add(
        self,
        *,
        name: str,
        task: str,
        kind: ScheduleKind | str,
        run_at: str | None = None,
        interval_seconds: int | None = None,
        cron: str | None = None,
        deliver_to: str = "chat",
        webhook_url: str | None = None,
        now: datetime | None = None,
    ) -> Schedule:
        now = now or _now()
        kind = ScheduleKind(kind)
        self._validate(kind, run_at, interval_seconds, cron)
        schedule = Schedule(
            schedule_id=str(uuid.uuid4()),
            name=name,
            task=task,
            kind=kind,
            run_at=run_at,
            interval_seconds=interval_seconds,
            cron=cron,
            deliver_to=deliver_to,
            webhook_url=webhook_url,
            created_at=now.isoformat(),
        )
        nxt = schedule.compute_next(after=now - timedelta(seconds=1))
        schedule.next_run = nxt.isoformat() if nxt else None
        self._schedules[schedule.schedule_id] = schedule
        self._persist()
        return schedule

    def cancel(self, schedule_id: str) -> bool:
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            self._persist()
            return True
        return False

    def set_enabled(self, schedule_id: str, enabled: bool) -> bool:
        s = self._schedules.get(schedule_id)
        if not s:
            return False
        s.enabled = enabled
        self._persist()
        return True

    def mark_fired(self, schedule: Schedule, now: datetime) -> None:
        """Record a fire and advance (or retire) the schedule."""
        schedule.last_run = now.isoformat()
        schedule.run_count += 1
        if schedule.kind == ScheduleKind.ONCE:
            schedule.enabled = False
            schedule.next_run = None
        else:
            nxt = schedule.compute_next(after=now)
            schedule.next_run = nxt.isoformat() if nxt else None
        self._persist()

    def tick(
        self,
        fire: Callable[[Schedule], None],
        now: datetime | None = None,
    ) -> list[Schedule]:
        """Fire every due schedule via ``fire`` and advance each. Returns those fired.

        A ``fire`` callback that raises does not advance the schedule (so it stays
        due and is retried on the next tick); other schedules are unaffected.
        """
        now = now or _now()
        fired: list[Schedule] = []
        for schedule in self.due(now):
            try:
                fire(schedule)
            except Exception as e:
                logger.error(
                    "Schedule %s (%s) fire failed: %s", schedule.schedule_id, schedule.name, e
                )
                continue
            self.mark_fired(schedule, now)
            fired.append(schedule)
        return fired

    # ---- internals ----

    @staticmethod
    def _validate(
        kind: ScheduleKind,
        run_at: str | None,
        interval_seconds: int | None,
        cron: str | None,
    ) -> None:
        if kind == ScheduleKind.ONCE:
            if not run_at:
                raise ValueError("kind 'once' requires run_at (ISO datetime)")
            _parse_dt(run_at)
        elif kind == ScheduleKind.INTERVAL:
            if not interval_seconds or interval_seconds <= 0:
                raise ValueError("kind 'interval' requires a positive interval_seconds")
        elif kind == ScheduleKind.CRON:
            if not cron:
                raise ValueError("kind 'cron' requires a cron expression")
            _parse_cron(cron)  # raises on malformed expressions

    def _persist(self) -> None:
        self._store.save(list(self._schedules.values()))


class ScheduleRunner:
    """Background thread that calls ``scheduler.tick()`` on a fixed interval.

    Start it once at session boot; stop it on shutdown. The tick interval
    defaults to 30 s so sub-minute interval schedules fire on time without
    hammering the agent.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        fire: Callable[[Schedule], None],
        tick_interval_seconds: int = 30,
    ):
        self._scheduler = scheduler
        self._fire = fire
        self._interval = tick_interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="schedule-runner")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._scheduler.tick(self._fire)
            except Exception:
                logger.exception("ScheduleRunner tick error")
