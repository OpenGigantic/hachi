# UAT Report: Sub-Agent Delegation, Leases & Recursion

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

> **HEADLINE FINDING: ADV-006 CONFIRMED CRITICAL** — When a role `.og` file fails to parse, the
> sub-agent falls back to the **full unfiltered parent tool set** (minus the static blocklist).
> A malformed or missing role `.og` grants the sub-agent every integration tool (e.g.
> `google_send_email`, `python_sandbox`) that would otherwise be role-restricted. The silently-
> swallowed exception at `delegation_tools.py:438-439` is the root cause. This is a
> **fail-open** security posture at the delegation boundary.
>
> **Tester:** UAT sub-agent · **Commit:** `036dfd9` · **Date:** 2026-06-14
> **Scope:** UC-AUTO-003, UC-AUTO-004, UC-AUTO-005, ADV-006, ADV-012
> **Offline tests run:** `tests/test_parallel_delegation.py` — 11/11 PASS

---

### UC-AUTO-003 — PARTIAL (High)

**Claim:** Sub-agent runs isolated, cannot recursively delegate, reports back; cost counted.

**Evidence:**

*Isolation — CONFIRMED.*
`_build_scoped_registry` (`delegation_tools.py:200-288`) creates a fresh `ToolRegistry`
stripped of every name in `BLOCKED_TOOL_NAMES` (line 261). Sub-agent memory is a separate
`SubAgentMemory` instance (`sub_agent.py:146-223`) with no access to the orchestrator's history.

*Recursive delegation blocked — CONFIRMED.*
`BLOCKED_TOOL_NAMES` at `delegation_tools.py:136-163` contains:
```python
"delegate_task",
"delegate_parallel_tasks",
"list_roles",
```
The offline test `test_parallel_tasks_blocked_for_sub_agents` (`test_parallel_delegation.py:249`)
confirms `delegate_parallel_tasks` is in the set. Static verification confirms `delegate_task`
and `list_roles` are also blocked.

*Reports back — CONFIRMED.*
`SubAgentReport.to_orchestrator_string()` (`sub_agent.py:110-138`) returns a structured 50-200
token string. `delegate_task` returns this string to the orchestrator at `delegation_tools.py:482`.

*Cost counted — PARTIAL.*
`SubAgentReport` carries a `token_estimate` field computed by `_estimate_tokens_from_memory`
(`sub_agent.py:763-783`) using the `chars // 4` heuristic. The field is logged and persisted
to the delegation log. However, the estimate is:
- A rough heuristic (not reconciled against Anthropic's billing), and
- There is no per-delegation breakdown surfaced in the orchestrator's cost report; the
  `cost_tracker` is shared (`delegation_tools.py:456`, `sub_agent.py:630`) so spend is pooled,
  not itemised per delegation.

**Finding:** Core isolation and recursion block work. Cost attribution is approximate and
non-itemised; the sub-agent's spend merges into the parent's `cost_tracker` without a
delegation-level label exposed to the user.

**Severity:** Medium  
**Regression test?** Yes — `test_parallel_tasks_blocked_for_sub_agents` covers the
recursion block. No test covers per-delegation cost breakdown.

---

### UC-AUTO-004 — PARTIAL (Medium)

**Claim:** One agent acquires a write-target lease, the other is blocked; lease released
on exit; TTL 300s; broad prefix matching may produce false positives.

**Evidence (all verified live via `uv run python`):**

*Acquire/block — CONFIRMED.*
`LeaseManager.acquire` (`lease_manager.py:86-121`) is atomic under `self._lock`. The test
`mgr.acquire('coder', ['projects/billing'])` while `'projects/billing'` is held by
`'researcher'` returns `(False, "target 'projects/billing' held by 'researcher'")`.
The blocked delegate returns a structured error string immediately
(`delegation_tools.py:410-415`).

*Release on exit — CONFIRMED.*
`delegation_tools.py:508-511`:
```python
finally:
    # P4-01: always release leases on exit (success, error, or exception).
    if lease_manager is not None and targets:
        lease_manager.release(role, targets)
```
The `finally` block runs on all paths including exceptions.

*TTL 300s — CONFIRMED, with a collision risk.*
`_LeaseEntry.ttl_seconds = 300.0` at `lease_manager.py:40`.
`_expire_stale()` (`lease_manager.py:157-161`) removes expired entries on each
`acquire` or `held_by` call. **However:** when a lease expires, the still-running
first sub-agent continues to execute, and a second sub-agent can now acquire the same
targets. The original holder has **no notification** that its lease was revoked. This
creates a real write-collision window if the first delegation takes more than 300s.
Live-verified: after a 0.15s TTL, `mgr.acquire` succeeds for a second agent while the
first is still "running."

*Prefix matching — CONFIRMED over-matching.*
`_find_conflict` (`lease_manager.py:163-168`):
```python
if target.startswith(held_target) or held_target.startswith(target):
    return entry
```
Bidirectional prefix check means:
- Lease on `"projects"` blocks `"projects/billing"` (correct).
- Lease on `"projects/billing"` also blocks `"projects"` (false positive — a new
  delegation claiming only the broad root `"projects"` is blocked by a narrow child
  lease). Live-verified.

*Parallel isolation — CONFIRMED.*
`delegate_parallel_tasks` dispatches via `ThreadPoolExecutor` with
`_PARALLEL_MAX_WORKERS=3`. Each sub-agent gets its own scoped registry copy. Test
`test_multiple_tasks_all_complete` passes.

**Note:** Leases are in-process only — no cross-session/cross-process protection (as
documented at `lease_manager.py:64-74`). Running two TUI/gateway instances over the
same workspace provides no write serialisation.

**Finding:** The core acquire/block/release cycle is sound. Two gaps: (1) TTL expiry can
let a second writer acquire while the first is still active; (2) bidirectional prefix
matching produces false-positive blocks (a child lease blocks a parent-root claim).

**Severity:** Medium  
**Regression test?** No test covers TTL expiry collision or the bidirectional prefix
false-positive.

---

### UC-AUTO-005 — PARTIAL (Medium)

**Claim:** Sub-agent wall-clock timeout is 120s; enforced at event boundaries only;
cancel works at the next boundary.

**Evidence:**

*Timeout constant — CONFIRMED.*
`DEFAULT_TIMEOUT_SECONDS = 120.0` at `sub_agent.py:54`.

*Timeout mechanism — CONFIRMED, event-boundary only.*
`_execute_with_timeout` (`sub_agent.py:715-747`):
```python
def _timeout_aware_event(*args, **kwargs):
    elapsed = time.monotonic() - start_time
    if elapsed > self._timeout_seconds:
        raise TimeoutError(...)
    original_event(*args, **kwargs)

loop.on_event = _timeout_aware_event
```
The `TimeoutError` is raised inside the `on_event` callback wrapper. This callback is
fired by `ExecutionLoop` between iterations (not mid-LLM-call). A single long LLM
call that does not return within 120s will not be interrupted until the call completes
and `on_event` is next invoked. The docstring at line 727 confirms:
"The timeout is checked between iterations by the ExecutionLoop's natural pausing
points."

*Ambiguous status — CONFIRMED.*
Both `AgentException` (iteration cap) and `TimeoutError` map to
`SubAgentStatus.TIMEOUT` (`sub_agent.py:663-695`). The orchestrator's report string
will say "Sub-agent exceeded iteration limit" or "timed out" but both appear as
`Status: timeout`, making them indistinguishable from the caller's perspective.

*No "time running out" warning — CONFIRMED.*
There is no pre-timeout warning event or signal; the sub-agent only discovers the
timeout when `TimeoutError` is raised and the loop exits.

*Cancel during a live delegation (Esc) — BLOCKED-NEEDS-LIVE.*
Functional cancel behaviour requires a running TUI session and live LLM — not
verifiable offline.

**Finding:** 120s constant confirmed. Timeout is event-boundary-only — an in-flight LLM
call can exceed 120s with no interruption. Both timeout and iteration-cap result in the
same `TIMEOUT` status, making disambiguation impossible from the orchestrator string.

**Severity:** Low (by design, documented); Medium for the status ambiguity (no way to
tell timeout-vs-iteration-cap from the report string).  
**Regression test?** `test_timeout_marks_slow_task` covers the parallel-level timeout
(300s wrapper), not the 120s sub-agent wall-clock path. No test covers the event-
boundary-only characteristic.

---

### ADV-006 — CONFIRMED CRITICAL

**Claim:** On role `.og` parse failure, sub-agent falls back to UNFILTERED tools
(scope escalation) via the bare `except Exception: pass` at `delegation_tools.py:438-439`.

**Evidence:**

The exact code path (`delegation_tools.py:430-439`):
```python
tool_patterns = ""
try:
    resolver = RoleResolver()
    role_path = resolver.resolve(role)
    from hachi.og_engine.parser import parse_og_file
    role_og = parse_og_file(str(role_path))
    tool_patterns = role_og.meta.tools
except Exception:
    pass  # Fall back to unfiltered tools if .og parsing fails
```

`tool_patterns` is initialised to `""`. After a parse failure, `tool_patterns` remains
`""`. In `_build_scoped_registry` (`delegation_tools.py:255`):
```python
has_filter = bool(allowed_prefixes or allowed_exact)
```
An empty `tool_patterns` → `has_filter = False` → Stage 2 (role-based filtering) is
**entirely skipped**. The sub-agent receives every non-blocked tool in the parent registry.

**Live-verified** with a synthetic parent registry containing:
`google_send_email`, `google_calendar_create`, `python_sandbox`, `read_file`,
`write_file`, `run_terminal_command`.

- Parse SUCCESS with `tool_patterns="read_file"`: sub-agent gets `['read_file',
  'run_terminal_command', 'write_file']` (core tools always pass through).
- Parse FAILURE (`tool_patterns=""`): sub-agent gets ALL: `['google_calendar_create',
  'google_send_email', 'python_sandbox', 'read_file', 'run_terminal_command',
  'write_file']`.
- **DIFFERENCE on failure: `['google_calendar_create', 'google_send_email',
  'python_sandbox']`** — integration actions the role was never supposed to have.

**Attack surface:** An attacker who can cause a role `.og` file to be unparseable (e.g.
malformed syntax injection via a skill-pack install, MR-4 path traversal, or simply
naming a file `<role>.og` with a parse-breaking payload) elevates any sub-agent to the
full tool set. With `python_sandbox` and `google_send_email` available, the sub-agent
could exfiltrate data, send emails, or modify calendar events outside its declared scope.

**Secure behaviour would be:** `except Exception: raise` or return an error report
immediately — never silently fall through to an expanded tool set.

**Severity:** Critical  
**Regression test?** No test covers the parse-failure fallback. Recommend:
```python
def test_parse_failure_fails_closed():
    # Patch parse_og_file to raise; verify _build_scoped_registry is never reached
    # OR verify scoped_registry only contains _CORE_TOOLS on exception path.
```

---

### ADV-012 — PARTIAL (High)

**Claim:** Recursive delegation is blocked by tool-removal; no depth limit or cycle
guard exists as a backstop.

**Evidence:**

*Tool-removal recursion block — CONFIRMED.*
`BLOCKED_TOOL_NAMES` (`delegation_tools.py:136-163`) contains `"delegate_task"`,
`"delegate_parallel_tasks"`, and `"list_roles"`. Static verification and
`test_parallel_tasks_blocked_for_sub_agents` confirm these are never added to a
sub-agent's scoped registry. A sub-agent LLM cannot call `delegate_task` because the
tool is not in its schema list.

*No max_depth guard — CONFIRMED ABSENT.*
There is no `depth` counter in `SubAgentRuntime.__init__`, `delegate_task`, or
`_build_scoped_registry`. No `max_depth` constant exists in `sub_agent.py` or
`delegation_tools.py`. The only backstop is tool-removal.

*No cycle guard — CONFIRMED ABSENT.*
There is no set of "in-flight delegation IDs" or a registry of active roles that would
detect `A → B → A` patterns. Because tool-removal prevents sub-agents from calling
`delegate_task` at all, the cycle guard is moot for a single level. However, this
reasoning assumes the `delegate_task` tool name is stable and cannot be aliased —
no check enforces that a custom skill cannot re-expose a delegation function under a
different name.

*Practical risk assessment:*
The tool-removal approach is effective for the one-level case (which is the only case
supported by the architecture). However:

1. If `delegate_parallel_tasks` were somehow available (e.g. via ADV-006 parse failure
   + a custom tool that wraps it under a new name), the depth guard would be bypassed.
2. There is no audit trail of delegation depth in the event stream — an orchestrator
   that calls `delegate_task` in a loop across turns (not recursively within a single
   sub-agent execution) is not detected or rate-limited.

**Finding:** The one-level recursion block (tool-removal) is correct and effective for
the designed architecture. The stated "no depth limit / no cycle guard backstop" is
accurate — the implementation relies entirely on tool-removal and would be bypassed if
that mechanism failed (e.g. ADV-006 parse failure does NOT introduce delegation tools
back, since they are in the static `BLOCKED_TOOL_NAMES` check which runs first, but a
future bug in that set would have no secondary defence).

**Severity:** High (architectural single-point dependency)  
**Regression test?** `test_parallel_tasks_blocked_for_sub_agents` provides partial
coverage. No test covers: (a) a dynamically-added tool with a delegation-like function,
(b) orchestrator-level delegation loops across turns.
