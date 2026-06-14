# UAT Findings — Human Approval Workflow & Destructive-Action Gating
**Assignment:** MR-5 / UC-PERM-005 / UC-PERM-006 / ADV-020
**Commit:** `036dfd9`
**Verified:** 2026-06-14
**Agent:** UAT verification agent (read-only; source not modified)

---

## HEADLINE FINDING (MR-5 cross-cutting answer)

**CONFIRMED: Zero destructive integration tools call any approval gate before executing.**
`google_send_email`, `google_delete_event`, `google_delete_contact`, `google_delete_task`,
`google_trash_file`, and `google_share_file` all execute immediately on the first LLM call.
The only mitigation for `google_delete_contact` is a prompt-engineering instruction in the
tool description ("ALWAYS verbally ask the user"), which the LLM can and will skip under
autonomous-mode or injection pressure. There is **no code-level gate** on any of them.

---

## UC-PERM-005 — PARTIAL (Severity: High)

### Claim
TUI interactive approval correctly handles approve / deny / allow-once; `allow_once` is not
persisted (session-only); the 180-second timeout produces a deny.

### Evidence

**approve persists for session — CONFIRMED**
`src/tokoboros/core/permissions.py:820-822`:
```python
if decision.action == "approve":
    self._allowed_session_paths.add((operation, resolved_target))
    return PermissionResult(True, "Approved for session by operator.")
```
`self._allowed_session_paths` is an in-memory `set` on the `InteractivePermissionGate`
instance; it is never written to `device-permissions.og`. Restarting the process loses it.
Offline test `tests/test_permissions_interactive.py::test_interactive_gate_approve_persists_for_session`
PASSES, confirming single-prompt semantics: handler called once, second call uses the cached set.

**allow_once NOT persisted — CONFIRMED**
`src/tokoboros/core/permissions.py:823-824`:
```python
if decision.action == "allow_once":
    return PermissionResult(True, "Allowed once by operator.")
```
No entry is added to `_allowed_session_paths` or `_allowed_session_commands` — every
subsequent call re-invokes the handler. Offline test
`test_interactive_gate_allow_once_applies_single_call` PASSES (3/3 tests pass).

**180s timeout → auto-deny — CONFIRMED, UX CONCERN**
`src/tokoboros/tui/app.py:2323-2327`:
```python
completed = waiter.wait(timeout=180)
if not completed:
    with self._approval_lock:
        self._approval_waiters.pop(request.request_id, None)
    return PermissionDecision(action="deny", rationale="Approval timed out")
```
A timeout silently denies. There is no countdown, no user notification before the 180s
elapses, and no way to distinguish a timed-out deny from an explicit user deny in the audit
log (both emit `permission.decided` with `action=deny`). The agent loop that was waiting on
the tool result will surface only a generic denial reason.

### Finding
The three branches (approve / deny / allow-once) are individually correct and all three
offline tests pass. However:
1. The timeout produces a deny **with no UX signal before it fires** — a user who steps
   away during a long autonomous task will silently block the workflow.
2. `allow_once` is correctly not persisted; `approve` is correctly session-scoped.
3. The gate is only in play when `InteractivePermissionGate` is constructed (i.e., the TUI
   in-process path) — see UC-PERM-006 for the API/gateway gap.

**PARTIAL** because the mechanism is correct but the timeout UX (no countdown, no warning,
deny indistinguishable from explicit deny in audit) is a usability defect.

- **Severity:** High (silent timeout can block autonomous tasks irreversibly; audit log
  loses information about _why_ something was denied)
- **Regression test?** Yes — `tests/test_permissions_interactive.py` covers the three
  decision branches. A test for timeout-deny UX does not yet exist.

---

## UC-PERM-006 — CONFIRMED (Severity: Critical)

### Claim
API / channel / OpenAI-facade sessions have a working approval path via the web "approvals
inbox."

### Evidence

**boot_agent only wires InteractivePermissionGate when on_permission_request is supplied**
`src/tokoboros/gateway/orchestrator.py:1201-1206`:
```python
if on_permission_request:
    runtime_gate = InteractivePermissionGate(
        base_gate=gate,
        approval_handler=on_permission_request,
        event_emitter=emit_runtime_event,
    )
```

**TUI passes on_permission_request; FastAPI gateway does NOT**
`src/tokoboros/tui/app.py:2198-2202`: TUI calls
`boot_agent(..., on_permission_request=self._request_permission_decision, ...)`.

`src/tokoboros/gateway/fastapi_app.py`: Full search finds zero calls to `boot_agent` or
any reference to `on_permission_request` / `approval_handler` / `InteractivePermissionGate`.
The gateway boots its agent via `control_plane.create_session()` → `_on_connect()`, which
does not pass a permission handler.

**Web approvals inbox is READ-ONLY display**
`src/tokoboros/gateway/fastapi_app.py:412-416` exposes only a `GET /permissions/audit`
route fed by `RuntimeReadModelStore._permission_audit`. This is an append-only event list.
There is no `POST /permissions/approve` or `PUT /permissions/decisions/{id}` route anywhere
in the gateway or control plane.

`frontend/src/components/screens/approvals-inbox-screen.tsx:73-141`:
The React component renders `AuditCard` items and has sections for "pending" and "recent."
The `AuditCard` renders a `Badge` (display only) — no approve/deny button, no action
handler, no API call to resolve a pending decision. The UI shows approval state but cannot
change it.

**No approval handler → auto-deny**
`src/tokoboros/core/permissions.py:777-778`:
```python
if not self._approval_handler:
    decision = PermissionDecision(action="deny", rationale="No approval handler available")
```
Without a handler, any escalation is auto-denied. In practice the gateway path never even
reaches `InteractivePermissionGate` — it uses bare `PermissionGate` — so escalations do
not emit `permission.requested` events at all, and the audit log shows nothing.

### Finding
**CONFIRMED defect.** API / channel / OpenAI-facade sessions get a bare `PermissionGate`
with no interactive escalation. Any action the policy would normally escalate is silently
denied (or if the policy allows it, executes without any approval prompt). The web
"approvals inbox" is purely a historical audit display with no ability to approve or deny
in-flight requests. The MR-5 UAT plan description is accurate.

- **Severity:** Critical (breaks the "human-in-the-loop" promise for all non-TUI surfaces)
- **Regression test?** No test exists for the gateway-session approval path. Adding one is
  strongly recommended before beta.

---

## ADV-020 — PARTIAL (Severity: Medium)

### Claim
Workflow corruption / TOCTOU: is the action re-validated at execution time, or does
`TurnContext` frozen-at-turn-start mean it runs against changed state? Is there a suspend
timeout?

### Evidence

**TurnContext is frozen at turn START, not before each tool call**
`src/tokoboros/core/turn_context.py:24` — `@dataclass(frozen=True, slots=True)`.
`src/tokoboros/core/loop.py:471-472`:
```python
prev_context = self._current_turn_context
self._current_turn_context = turn_context
```
The context (system prompt, recalled memory, user profile snapshot) is captured once at
`run()` entry. Tools execute after the LLM decides to call them; by the time a tool fires,
the TurnContext may be stale if underlying state changed mid-turn (e.g., a concurrent
session modifies the same file, or an operator changes `device-permissions.og`). However,
the `PermissionGate` itself is **also loaded once at boot** and is immutable
(`src/tokoboros/core/permissions.py:310`, docstring: "Immutable after construction"). So a
mid-turn change to `device-permissions.og` is NOT re-read — neither during the turn nor
between turns without a restart.

**No re-validation between approval and execution**
The approval flow is synchronous and blocking within the tool-dispatch thread pool
(`src/tokoboros/core/loop.py:658-669`). The sequence is:
1. LLM decides to call tool (tool_call emitted).
2. `_execute_single_tool` is submitted to `_tool_pool`.
3. Inside the thread, `InteractivePermissionGate.check_command/check_path` blocks on
   `waiter.wait(timeout=180)`.
4. On approval, the tool immediately executes.

There is no window between "approval received" and "tool executes" where the decision could
be reviewed again — execution is atomic with the approval unblock. This is correct for
TOCTOU but means: if a user approves an action that was queued _before_ they changed policy
(a different browser tab, say), the approval is honoured for the old target with no
re-check.

**No suspend timeout for core_suspend_workflow**
`src/tokoboros/tools/workflow_tools.py:15-21`:
```python
def suspend_workflow(reason: str) -> str:
    raise SuspendWorkflow(reason=reason)
```
`src/tokoboros/core/loop.py:718-720`:
```python
if suspend_reason:
    self.memory.complete_task(user_input_for_turn, f"Suspended: {suspend_reason}")
    return f"[WORKFLOW SUSPENDED] {suspend_reason}"
```
`SuspendWorkflow` is agent-initiated (the LLM calls the tool). There is no external timer
that auto-resumes or auto-cancels after N seconds. A workflow can stay suspended
indefinitely. The only way it resumes is if an external trigger (webhook, scheduled wake-up)
fires, or the user manually sends another message.

**TOCTOU on the base PermissionGate**
Because the gate is constructed once at boot and is immutable, a change to
`device-permissions.og` while the app is running has no effect until the next restart.
This is a coherent design decision (fail-safe, not fail-open) but means:
- An operator who tightens permissions mid-session cannot immediately block a running workflow.
- An operator who loosens permissions mid-session cannot have that take effect until restart.
Neither is a security regression, but the first is a usability gap (emergency policy
tightening is ineffective while a workflow is running).

### Finding
**PARTIAL.** There is no true TOCTOU exploit path for the normal approval flow (approval
and execution are atomically linked in the blocking thread). However:
1. There is no suspend timeout: suspended workflows can persist indefinitely.
2. The frozen `PermissionGate` means mid-session policy changes (either direction) require
   a restart to take effect — emergency revocation is not possible in-process.
3. `TurnContext` frozen at turn start is the correct design; there are no observed
   cross-turn mutation races because each turn gets its own frozen snapshot.

- **Severity:** Medium (no suspend timeout is a reliability/UX gap, not an acute security
  hole; policy-change-requires-restart is documented as a design invariant but not
  communicated to users)
- **Regression test?** A test for "suspend_workflow suspends and holds indefinitely" would
  be useful; none exists. The core TOCTOU cases are covered by the existing permissions
  tests.

---

## MR-5 Cross-Cutting — Destructive integration tools

**All destructive integration tools execute immediately with no approval gate. CONFIRMED.**

| Tool | File | Approval? | Notes |
|---|---|---|---|
| `google_send_email` | `integrations/google/gmail.py:358` | None | Executes immediately |
| `google_delete_event` | `integrations/google/calendar.py:360` | None | Executes immediately |
| `google_delete_contact` | `integrations/google/contacts.py:222` | Prompt-engineering only | Description says "ALWAYS verbally ask" — not enforced in code |
| `google_delete_task` | `integrations/google/tasks.py:216` | None | Description: "Permanently delete" |
| `google_trash_file` | `integrations/google/drive.py:232` | None | Trash is recoverable, but no gate |
| `google_share_file` | `integrations/google/drive.py:230` | None | External share executes immediately |

The `google_delete_contact` tool description contains the human-readable warning "DANGER:
Permanently delete ... ALWAYS verbally ask the user for confirmation" (contacts.py:223-225)
but this is an LLM-instruction, not a code-level enforcement. Under autonomous mode, prompt
injection, or a multi-step workflow, the LLM may call the tool without verbal confirmation.

`InteractivePermissionGate.check_path` and `check_command` are only wired to filesystem
and terminal operations. The integration tools bypass the gate entirely — they make direct
HTTP calls to Google APIs without going through any `PermissionGate.check_*` method.

**Severity of MR-5 gap: Critical for production** — irreversible actions (send, delete) can
be taken by the agent without any user confirmation in all non-TUI sessions, and even in TUI
sessions only if the filesystem/terminal gate would have denied the action (Google API
calls are never checked by the gate regardless of session type).

---

## Summary table

| Scenario | Verdict | Severity | Regression test? |
|---|---|---|---|
| UC-PERM-005 (TUI approval approve/deny/allow-once; timeout) | PARTIAL | High | Partial (3/3 unit tests pass; no timeout-UX test) |
| UC-PERM-006 (API/channel/OpenAI approval path) | CONFIRMED defect | Critical | None |
| ADV-020 (TOCTOU / suspend timeout) | PARTIAL | Medium | None for suspend timeout |
| MR-5 cross-cutting (destructive integration tools) | CONFIRMED defect | Critical | None |
