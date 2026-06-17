# UAT Report: Autonomy, Spend Caps & Background Job Queue

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

**Verifier:** UAT agent (read-only) · **Commit:** 036dfd9 · **Date:** 2026-06-14  
**Most important finding: ADV-013 / UC-SPEND-001 — spend caps default to 0.0 (= UNLIMITED); only the iteration cap (15) protects a runaway session from infinite cost. No wall-clock cap exists in the main loop.**

---

### UC-AUTO-001 — CONFIRMED
**Severity:** Low · **Regression test?** Yes (unit tests cover iteration cap path)

**Claim:** Multi-step task hitting `max_iterations=15` stops cleanly with an `[INCOMPLETE]` message and a "continue" prompt.

**Evidence:**
- `src/hachi/core/loop.py:457` — `def run(self, max_iterations: int = 15, ...)`
- `src/hachi/core/loop.py:505` — `while iterations < max_iterations:`
- `src/hachi/core/loop.py:722–733`:
  ```python
  self.on_event(SESSION_ITERATION_LIMIT, {"iterations": max_iterations, "status": "incomplete"})
  self.memory.complete_task(user_input_for_turn, f"Partial: iteration limit reached ({max_iterations})")
  return (
      f"[INCOMPLETE] The task reached the {max_iterations}-iteration limit and "
      "could not be fully completed. Reply 'continue' to pick up where it left off."
  )
  ```
- `src/hachi/core/runtime_events.py:25` — `SESSION_ITERATION_LIMIT = "session.iteration.limit"` (distinct event name, distinguishable from `[BUDGET LIMIT REACHED]`)

**Finding:** Claim fully confirmed. The loop fires a named event, records a partial completion in the task ledger, and returns a user-readable `[INCOMPLETE]` message with continuation instructions. The message is textually distinguishable from budget stops (`[BUDGET LIMIT REACHED]`) and cancellations (`[CANCELLED]`). A `TurnContext.max_iterations` override is honoured at `loop.py:498–500`, allowing per-request tuning.

---

### UC-AUTO-002 — BLOCKED-NEEDS-LIVE
**Severity:** Medium · **Regression test?** No

**Claim:** Agent clarifies ambiguous goals rather than executing a confident wrong plan.

**Evidence (structural):** Clarification behaviour is LLM-emergent; no static guard or `clarify()` primitive was found in the source tree. `src/hachi/core/loop.py` drives a pure ReAct loop with no forced planning or confirmation step before tool calls. The only structural check is `if not response.tool_calls` at line 616 (agent stopped reasoning) or a `SuspendWorkflow` exception from `core_suspend_workflow`.

**Finding:** Cannot be verified offline. The agent's tendency to clarify vs. act depends on the system prompt in the loaded `brain-*.og` file and LLM behaviour. Requires a live session. Flag for human tester: check whether a single ambiguous instruction ("help me get organised for the launch") triggers immediate calendar/email mutations or a clarification turn.

---

### UC-AUTO-006 — CONFIRMED
**Severity:** Low (improvement confirmed) · **Regression test?** Yes (test_job_queue.py 8 tests, all PASS)

**Claim (three sub-claims from 036dfd9):**
1. RUNNING→PENDING reset on boot (crash recovery)
2. Named/registry jobs re-run; func-only jobs discarded on crash recovery
3. Unexecutable jobs fail loudly via `job.failed` event (not raise); a handler that raises still lands in `jobs/failed` with retry/backoff

**Evidence:**

Sub-claim 1 & 2 — `src/hachi/jobs/queue.py:574–595` (`_recover_running`):
```python
cur = conn.execute(
    "UPDATE jobs SET status = 'pending', started_at = NULL WHERE status = 'running'"
)
recovered = int(cur.rowcount)
conn.execute(
    """
    UPDATE jobs
    SET status = 'failed',
        result_error = 'Discarded on recovery: func-only job cannot execute after restart',
        completed_at = datetime('now')
    WHERE status = 'pending'
      AND (handler_name IS NULL OR handler_name = '')
    """
)
return recovered
```
RUNNING rows → PENDING; func-only PENDING rows (no `handler_name`) → FAILED immediately. Named/registry jobs (those with a populated `handler_name`) are left as PENDING and re-run by the resident worker after restart. `SQLiteJobQueue.__init__` calls `_recover_running()` unconditionally at line 549.

Sub-claim 3 — `src/hachi/jobs/queue.py:368–395` (unexecutable job path):
```python
else:
    # No callable and no registered handler — structurally unexecutable.
    duration = (datetime.now(UTC) - start_time).total_seconds()
    result = JobResult(status=JobStatus.FAILED, error=f"Job {job.name!r}: no callable func …")
    logger.warning("Job %r is unexecutable (no func, no handler) — failing without retry", job.name)
    self.on_job_event("job.failed", { … })
    self.queue.update_status(job.job_id, JobStatus.FAILED, result=result)
    return True
```
`src/hachi/jobs/queue.py:414–459` (handler raises exception): increments `job.attempt`, schedules backoff (`job.scheduled_for = …`), emits `job.retry` then (after retries exhausted) `job.failed`. The exception path does **not** bubble up — `execute_one()` catches all `Exception` and updates status.

`src/hachi/core/loop.py:829–831` (ResidentWorkerThread): uncaught errors inside `execute_one()` are caught by the thread loop with `logger.exception(…)`, preventing worker thread death.

**Finding:** All three sub-claims confirmed statically and via 8/8 passing unit tests (including `test_failed_job_reaches_read_model_projection` which confirms `job.failed` event lands in the `RuntimeReadModelStore`). One nuance: `SQLiteJobQueue._recover_running()` discards func-only **PENDING** (not RUNNING) jobs on restart — the comment in the test plan says "func-only jobs discarded on crash recovery", which is correct, though it applies to previously-pending func-only jobs left behind after a crash, not only those that were RUNNING.

---

### UC-SPEND-001 — CONFIRMED (with critical caveat on MR-6)
**Severity:** High · **Regression test?** Yes (test_per_turn_budget.py 8 tests, all PASS)

**Claim:** Per-session spend cap hard-stops with `[BUDGET LIMIT REACHED]`; per-turn caps abort before tool calls; default 0 = unlimited.

**Evidence:**

Default 0 = unlimited — `src/hachi/core/loop.py:232–235`:
```python
max_cost_usd: float = 0.0,
max_tokens: int = 0,
max_cost_per_turn: float = 0.0,
max_tokens_per_turn: int = 0,
```
No non-zero default anywhere. `_coerce_float` at `orchestrator.py:249–258` returns `0.0` when no config key or env var is set.

Session cap enforcement — `loop.py:422–433` (`_budget_exceeded`):
```python
if self.cost_tracker is None or (self.max_cost_usd <= 0 and self.max_tokens <= 0):
    return None
```
Returns `None` (no check) when both caps are 0. The per-session check fires at the **top of each iteration** (`loop.py:519–523`), before the LLM is called for that iteration, so it can stop before incurring more cost.

Per-turn cap — `loop.py:435–455` (`_turn_budget_exceeded`): Fired **after** each LLM call in the iteration (`loop.py:584–588`). Checks cumulative delta vs. the baseline captured at `run()` start. Returns `[BUDGET LIMIT REACHED] <reason>` on trip.

Both cap-trip return strings:
- Session: `f"[BUDGET LIMIT REACHED] {budget_reason}"` (line 523)
- Per-turn: `f"[BUDGET LIMIT REACHED] {turn_budget_reason}"` (line 588)

**Finding:** Mechanism confirmed correct. **Critical caveat (MR-6):** caps are silently off until explicitly configured. There is no onboarding nudge, UI warning, or documented safe default. A new user running an autonomous 15-iteration session with no caps set incurs an uncapped cost on every session. Sub-agent spend is pooled into the same `cost_tracker` (shared by sub-agents created by the orchestrator) — no per-delegation budget breakdown is exposed.

---

### UC-SPEND-002 — CONFIRMED (accuracy acceptable; one model gap)
**Severity:** Low · **Regression test?** Yes (test_cost_tracking.py 8 tests, all PASS)

**Claim:** Cost estimate is accurate vs. provider; cache pricing is sane.

**Evidence — `src/hachi/core/cost.py:19–30`:**
```python
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
}
_DEFAULT_PRICING = (1.0, 5.0)
```
Cache multipliers at `cost.py:28–30`:
```python
_CACHE_READ_MULTIPLIER = 0.1    # 10% of base input price
_CACHE_WRITE_MULTIPLIER = 1.25  # 125% of base input price
```
`cost.py:87–92` — `input_tokens`, `cache_read_tokens`, and `cache_creation_tokens` treated as **additive** (the Anthropic API returns them as mutually exclusive counters for fresh, read, and write portions respectively). This matches Anthropic's documented billing model.

**Finding:** Cache pricing (10% read, 125% write) matches Anthropic's documented rates for prompt caching at time of this review. Model pricing table matches Anthropic 2026 published rates for Opus/Sonnet/Haiku 4-family. GPT-4o pricing matches OpenAI circa 2025 rates (may be stale). Prefix-match on model id correctly resolves dated variants (confirmed by `test_pricing_longest_prefix_match`). The `_DEFAULT_PRICING` fallback of `(1.0, 5.0)` means unknown models are priced as Haiku — this is a safe low-side default (underestimates for expensive models). The estimate is explicitly flagged "best-effort" in the module docstring; no reconciliation against the Anthropic billing API is performed.

---

### ADV-013 — PARTIAL
**Severity:** High · **Regression test?** No (no wall-clock test exists)

**Claim:** Infinite-loop / runaway protection via iteration cap + wall-clock timeout + spend cap.

**Evidence:**

Iteration cap — confirmed at `loop.py:505,731`. The 15-iteration ceiling is the only always-on runaway guard.

Wall-clock timeout — **does not exist** in the main `ExecutionLoop.run()`. A full-text search of `src/hachi/core/loop.py` finds no `time.time()`, `time.monotonic()`, or deadline comparison in the main loop body. Sub-agent loops have a 120-second wall-clock limit at `src/hachi/core/sub_agent.py` (per UAT plan reference `sub_agent.py:54,715–747`), but the primary session loop has none.

Spend caps — as confirmed in UC-SPEND-001, caps default to 0 (unlimited). Without explicit configuration, spend caps provide zero protection.

**Finding:** PARTIAL confirmation. The iteration cap (15 iterations/turn, hardcoded) is the only always-on runaway guard in the primary execution loop. Wall-clock protection is absent for the main loop (only sub-agents have it). Spend caps must be explicitly enabled and provide no default protection. A task designed to pause at each iteration boundary and resume ("continue") could in principle execute 15 iterations per turn indefinitely, with no wall-clock or spend gate stopping it. The effective protection posture is: iteration cap prevents deep per-turn spirals; no other guardrail fires by default.

---

## Summary table

| Scenario | Verdict | Severity | Key note |
|---|---|---|---|
| UC-AUTO-001 | CONFIRMED | Low | `[INCOMPLETE]` at iter 15, clean event + continuation prompt |
| UC-AUTO-002 | BLOCKED-NEEDS-LIVE | Medium | No static clarification gate; LLM-emergent behaviour |
| UC-AUTO-006 | CONFIRMED | Low | All 3 sub-claims confirmed; 8/8 tests pass |
| UC-SPEND-001 | CONFIRMED | High | Mechanism correct but MR-6 confirmed: caps default OFF |
| UC-SPEND-002 | CONFIRMED | Low | Cache pricing correct; GPT-4o price may be stale |
| ADV-013 | PARTIAL | High | Only iteration cap fires by default; no wall-clock, spend caps OFF |

## Test runs

All 33 offline tests passed (33/33, Python 3.14.3, commit 036dfd9):
- `tests/test_job_queue.py` — 8/8
- `tests/test_cost_tracking.py` — 8/8
- `tests/test_per_turn_budget.py` — 8/8
- `tests/test_session_cost_tracking.py` — 9/9
