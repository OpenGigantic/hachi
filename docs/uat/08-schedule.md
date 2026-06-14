# UAT Report: Scheduling, Recurring Tasks, Cross-Process State
**Agent:** UAT verification agent · **Commit:** `036dfd9` · **Date:** 2026-06-14
**Test runner:** `uv run pytest tests/test_scheduler.py` — 14/14 PASSED

> **Headline finding:** UC-RSCH-002 is the highest-severity confirmed bug — the resident runner
> (`ScheduleRunner` / `boot_agent`) unconditionally calls `session.chat(s.task)` and never reads
> `deliver_to` or `webhook_url`.  Webhook delivery is **only reachable via `tokoboros schedule
> run-due`**.  This silently voids the delivery contract for every TUI and daemon session.
> ADV-021 / UC-PERF-003 are also confirmed: no OS-level file lock exists — two processes share a
> `threading.RLock` that is **process-local** only, guaranteeing last-write-wins corruption.

---

## UC-RSCH-001 — CONFIRMED
**Claim:** All schedule math is UTC so a user asking for "8am daily" fires at the wrong local hour.

**Evidence:**
- `src/tokoboros/jobs/scheduler.py:130-131`
  ```python
  def _now() -> datetime:
      return datetime.now(UTC)
  ```
- `scheduler.py:134-139` — `_parse_dt` treats naive ISO strings as UTC:
  ```python
  def _parse_dt(value: str) -> datetime:
      dt = datetime.fromisoformat(value)
      if dt.tzinfo is None:
          dt = dt.replace(tzinfo=UTC)
      return dt
  ```
- `scheduler.py:186-187` — cron matching delegates to `cron_next(self.cron, after)`, which
  operates on the UTC `datetime` returned by `_now()`.  There is no local-timezone conversion at
  any point in the chain (neither in `cron_next`, `cron_matches`, nor `compute_next`).
- `cli/commands/schedule.py:31` — the `--at` flag help text reads `"One-shot ISO datetime (UTC)"`,
  confirming UTC-only intent, but the product UI gives no equivalent warning for cron expressions.
- Tests confirm UTC semantics: `test_cron_schedule_due_and_advance` uses
  `datetime(2026, 6, 5, 8, 0, tzinfo=UTC)` and verifies next_run at `9:00 UTC` —
  not at `9:00 local`.

**Finding:** A user who enters `0 8 * * 1-5` ("8am weekdays") gets 8:00 **UTC**.  On UTC+5:30
(IST) that fires at 1:30 pm local.  There is no timezone parameter, no `--tz` flag, and no
runtime conversion.  The CLI `--at` help is the only hint and it is cron-invisible.

**Severity:** High
**Regression test?** Yes — extend `tests/test_scheduler.py` with a local-tz parametrized test
once a `--tz` parameter is added.

---

## UC-RSCH-002 — CONFIRMED
**Claim:** The resident runner ignores `deliver_to`/`webhook_url` — webhook fires only via
`tokoboros schedule run-due`.

**Evidence:**

*Resident runner (orchestrator.py:1771-1777):*
```python
def _fire_schedule(s: Schedule) -> None:
    try:
        session.chat(s.task)          # <-- no reference to s.deliver_to or s.webhook_url
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).exception("Schedule fire error for '%s'", s.name)
```
`session.chat()` runs the task through the LLM and emits it to the session event pipeline —
it has no delivery parameter and never calls `_deliver_schedule_result`.

*run-due path (schedule.py:172-176):*
```python
def fire(schedule):
    reply = agent.chat(schedule.task)
    _deliver_schedule_result(schedule, reply)      # <-- webhook branch here
```

*`_deliver_schedule_result` (schedule.py:223-249):*
```python
def _deliver_schedule_result(schedule, reply: str) -> None:
    if schedule.deliver_to == "webhook" and schedule.webhook_url:
        ...
        urllib.request.urlopen(request, timeout=15)
    else:
        console.print(Panel(Markdown(reply), title=f"Scheduled: {schedule.name}"))
```

**Finding:** The webhook branch exists **only** in `_deliver_schedule_result`, which is called
only from `run-due` and `run-now`.  The `ScheduleRunner` background thread (used by TUI and
daemon sessions via `boot_agent`) calls `session.chat(s.task)` and discards the result, never
inspecting `deliver_to`.  A schedule with `deliver_to="webhook"` running in the resident runner
silently drops the webhook; the user sees no error, no fallback panel, and no delivery.

**Severity:** High (P0)
**Regression test?** Yes — add a unit test that creates a `webhook`-deliver schedule and ticks
a mock `_fire_schedule` stub; assert it calls `_deliver_schedule_result`.

---

## UC-RSCH-003 — CONFIRMED (behavior is as-claimed; documented, not a bug per se, but unannounced)
**Claim:** Cron drops all-but-one missed occurrence; interval/once collapse to single catch-up.

**Evidence:**

*Interval catch-up (scheduler.py:177-185):*
```python
if self.kind == ScheduleKind.INTERVAL:
    base = _parse_dt(self.last_run) if self.last_run else _parse_dt(self.created_at)
    nxt = base + timedelta(seconds=self.interval_seconds)
    # Catch up without firing in a storm if ticks were missed.
    while nxt <= after:
        nxt += timedelta(seconds=self.interval_seconds)
    return nxt
```
The `while` loop advances `nxt` past `after` in a single step — all intermediate ticks are
skipped.  Only one fire occurs no matter how many intervals were missed.

*Cron (scheduler.py:186-187):*
```python
if self.kind == ScheduleKind.CRON and self.cron:
    return cron_next(self.cron, after)
```
`cron_next(expr, after)` returns the **next** match strictly after `after` — it never returns
a past occurrence.  All past missed fires are dropped; only the next future one is scheduled.

*Once (scheduler.py:173-176):*
```python
if self.kind == ScheduleKind.ONCE:
    if self.run_count > 0 or not self.run_at:
        return None
    return _parse_dt(self.run_at)
```
A `once` schedule that fires late still fires exactly once.

*Test proof:* `test_interval_catch_up_does_not_storm` directly confirms this with a 5-hour gap:
```python
fired = sched.tick(lambda _s: None, now=t0 + timedelta(hours=5))
assert len(fired) == 1
assert sched.get(s.schedule_id).run_count == 1
```

**Finding:** The catch-up-collapses-to-one behavior is intentional and correctly implemented.
The problem is **user expectation gap**: the product documentation and CLI help text do not state
this behavior.  A user who expects an hourly digest to fire 8 times after 8 hours offline will
silently receive only 1 catch-up fire.  No changelog entry, no UI warning.

**Severity:** Medium
**Regression test?** Already covered by `test_interval_catch_up_does_not_storm`.  Add a cron
variant.

---

## UC-RSCH-004 — PARTIAL
**Claim:** A slow scheduled task blocks the whole tick loop and starves other schedules; user
turn is queued behind a long scheduled task.

**Evidence — serialization via chat lock is CONFIRMED:**
`orchestrator.py:888-895` — `session.chat()` acquires `_chat_lock`:
```python
if not self._chat_lock.acquire(blocking=False):
    self._emit_event(SESSION_BUSY, {"queued_input_preview": user_input[:80]})
    self._chat_lock.acquire()   # blocks until the current turn is done
```
The resident runner fires via `session.chat(s.task)` on the `ScheduleRunner` background thread.
If a scheduled task is in-progress, a user turn will block at `_chat_lock.acquire()` and the
`SESSION_BUSY` event will be emitted.  That part is serialized correctly.

**Evidence — tick loop starvation is CONFIRMED:**
`scheduler.py:440-445` — the `ScheduleRunner._loop` is:
```python
def _loop(self) -> None:
    while not self._stop.wait(self._interval):
        try:
            self._scheduler.tick(self._fire)
        except Exception:
            logger.exception("ScheduleRunner tick error")
```
`tick()` iterates `due()` and calls `fire(schedule)` synchronously for each.  The `fire`
callback is `_fire_schedule` → `session.chat()`, which blocks until the full LLM turn
completes.  With N due schedules in the same tick, each runs sequentially:
`tick = fire(s1) + fire(s2) + ... + fire(sN)`.  A 60-second LLM response per schedule with
3 simultaneously-due schedules means 180 seconds of tick time.  The tick interval is 30 s, so
the next tick fires immediately after — no starvation per se, but all other schedules are
blocked for the full wall-clock duration of the in-progress fires.

**Claim about blocking other schedules within a tick:** CONFIRMED — `tick()` has no
concurrency within a single invocation.

**Claim about user turn serialization corrupting state:** NOT-REPRODUCED statically — the
chat lock prevents interleaving; state corruption does not occur.  The user turn is delayed
(queued), not corrupted.

**Finding:** The severity is less than stated.  State is safe.  The real issue is that a long
scheduled task delays (a) all subsequent due-at-same-tick schedules sequentially, and (b) the
user's next interactive turn until the lock is released.  There is no starvation-forever
scenario, but latency stacking is real.  This is a `PARTIAL` match: the serialization is
correct, but the tick-loop latency-stacking is confirmed.

**Severity:** Low (latency, not correctness)
**Regression test?** No — requires live timing.  Static analysis sufficient here.

---

## UC-PERF-003 — CONFIRMED
**Claim:** TUI+daemon over the same state dir — no file lock on schedules.json / SessionStore →
double-fire & last-write-wins.

**Evidence:**

*`ScheduleStore._lock` is a `threading.RLock` (scheduler.py:217):*
```python
self._lock = threading.RLock()
```
A `threading.RLock` is a **within-process** re-entrant lock only.  It provides no mutual
exclusion between two separate OS processes.

*`ScheduleStore.save` (scheduler.py:230-236):*
```python
def save(self, schedules: list[Schedule]) -> None:
    with self._lock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([s.to_dict() for s in schedules], indent=2),
            encoding="utf-8",
        )
```
This is a full-file overwrite of `schedules.json` with the **in-memory state** of the
`Scheduler` instance in the calling process.  If two processes each load the file at startup
and one later writes `cancel(id_A)` → overwrites the file → the second process then marks
`id_B` fired and overwrites the file again, the first process's cancel is silently lost.

*No OS-level file lock anywhere in the codebase:*
Grep for `fcntl`, `msvcrt`, `FileLock`, `portalocker`, `LOCK_EX` returns zero matches across
all of `src/`.

*Double-fire scenario:*
Both processes load `schedules.json` at `__init__` time.  Both `ScheduleRunner` threads call
`tick()` independently.  If `next_run` is due, both processes fire and both call `mark_fired`
→ both write back a `run_count += 1` state, but each is based on the stale in-memory copy
loaded at startup — the second write silently discards the first write's `run_count`.  Result:
`run_count` is under-counted and both processes executed the task.

**Finding:** No cross-process file lock exists.  Any scenario running TUI and daemon (or two
daemon instances) against the same `schedules.json` will double-fire schedules and lose writes.
This matches MR-9 exactly.

**Severity:** High
**Regression test?** Requires multi-process test harness.  Add `BLOCKED-NEEDS-LIVE` note.

---

## ADV-021 — CONFIRMED
**Claim:** Cross-process schedule corruption / double-execution — no file lock → both fire;
full-file overwrite loses a cancel.

**Evidence:** Same as UC-PERF-003 above.  Specific adversarial scenario:

1. Process A boots, loads `schedules.json`, holds in-memory dict with schedules `[s1, s2]`.
2. User calls `tokoboros schedule remove s1` — this boots a **new** `Scheduler` instance
   (via `_get_scheduler()` in `schedule.py:21-22`), loads the file fresh, deletes `s1`, saves.
3. Process A's `ScheduleRunner` fires at the 30 s mark — `mark_fired(s1, now)` is called on
   the in-memory copy which still contains `s1` — writes the full dict back to disk, **restoring
   s1**.
4. s1 is effectively un-cancelled.

*Root cause:* `Scheduler.__init__` loads once into `self._schedules` (scheduler.py:253):
```python
self._schedules: dict[str, Schedule] = {s.schedule_id: s for s in store.load()}
```
No re-read before each `_persist()`.  A cross-process cancel is invisible to the running
instance's in-memory state.

**Finding:** CONFIRMED.  A cancel (or add) from `tokoboros schedule remove/add` in a separate
process is silently overwritten by the next tick of the running `ScheduleRunner`.  This is a
direct consequence of the last-write-wins full-file overwrite design with no OS-level locking
and no re-read-before-write.

**Severity:** High (data integrity, not just latency)
**Regression test?** Requires multi-process test.  BLOCKED-NEEDS-LIVE for execution, but
statically confirmed.

---

## Summary Table

| ID | Verdict | Severity | Regression test? |
|----|---------|----------|-----------------|
| UC-RSCH-001 | CONFIRMED | High | Yes (needs `--tz` feature) |
| UC-RSCH-002 | CONFIRMED | High (P0) | Yes |
| UC-RSCH-003 | CONFIRMED | Medium | Already covered (cron variant missing) |
| UC-RSCH-004 | PARTIAL | Low (latency only, not correctness) | BLOCKED-NEEDS-LIVE |
| UC-PERF-003 | CONFIRMED | High | BLOCKED-NEEDS-LIVE |
| ADV-021 | CONFIRMED | High | BLOCKED-NEEDS-LIVE |

**Tests run:** `tests/test_scheduler.py` — 14/14 PASSED (all scheduler logic correct in-process).
**Key gap:** The test suite validates single-process logic exhaustively but has zero cross-process
or delivery-path coverage.
