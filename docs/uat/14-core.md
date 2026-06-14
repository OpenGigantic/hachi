# UAT Report — 14: Core Assistant, Conversation & Persistence

> **Commit:** `036dfd9` · **Date:** 2026-06-14 · **Agent:** UAT Verification (read-only)
> **TOP FINDING:** UC-CORE-005 — Esc/interrupt in TUI in-process mode does NOT cancel an in-flight LLM call. `action_interrupt()` only sets `_gateway_stop_event` (the gateway poller flag); it never calls `loop.cancel()`. This is a design gap, not merely a UX gap — the claim "cancel stops generation" is untrue for the default in-process TUI mode.

---

## UC-CORE-001 — PARTIAL (High · Regression test: YES)

### Claim
First-run with no credentials gives clear, actionable guidance to set a key (`tokoboros secrets --set …`), does not crash, and exits cleanly when run non-interactively / piped (non-TTY).

### Evidence
**File:** `src/tokoboros/cli/main.py`

Lines 840–852 — the rich Panel message is constructed and printed before the TTY gate:
```
"[bold]Fix (one command):[/bold]\n"
"  [green]export {env_name}=<your-key>[/green]\n\n"
"{link_line}"
"Then run [bold]tokoboros run[/bold] again.\n"
"Or run [bold]tokoboros quickstart[/bold] to choose a different provider."
```

Lines 854–863 — the interactive recovery prompt is gated on `sys.stdin.isatty()`:
```python
if sys.stdin.isatty():
    fixed = _interactive_first_run_setup(env_name, cfg.provider.kind)
    ...
return False
```
→ In non-TTY mode the function returns `False` immediately after printing the banner.

Lines 1342–1347 — TUI failure → REPL fallback:
```python
try:
    run_tui(resume_session_id=resume_session_id)
except Exception as e:
    ...
    _run_repl()
```

### Finding
**PARTIAL.** The TTY-detection logic at line 855 correctly skips the interactive prompt in non-TTY mode, so a piped invocation will not hang at a `Prompt.ask` call. The Panel banner (lines 840–852) is printed before the guard, giving actionable guidance.

However two issues remain:

1. **Guidance wording mismatch.** The banner says `export {env_name}=<your-key>` and recommends `tokoboros quickstart`, but there is no mention of `tokoboros secrets --set` (the secrets sub-command advertised in the plan). Minor UX gap, but the wording could mislead a new user on Windows where `export` is not a shell command.

2. **Double-boot on TUI→REPL fallback (lines 1342–1347).** If `run_tui()` raises *after* the TUI's `_boot_runtime` has already called `boot_agent()` and created a live `AgentSession` (e.g., due to a Textual crash post-boot), the fallback `_run_repl()` at line 1347 calls `boot_agent()` again via `_run_repl()` → line 1225. The first session is leaked (no shutdown path is invoked on the failed TUI's agent). This is low-probability in the no-key scenario (boot fails before `boot_agent`), but real when TUI itself crashes post-boot.

3. **`_resolve_resume_target` calls `Prompt.ask` without a TTY guard** (lines 1307–1316). If `--resume` is passed in a piped context, it prints the session list and blocks at `Prompt.ask`. However this path requires the explicit `--resume` flag, so the plain `tokoboros run` non-TTY path is safe.

**Severity:** Medium (UX gap for new users; hang path only triggered by `--resume` in a pipe, not the default flow)
**Regression test:** `tests/test_onboarding_quickstart.py`, `tests/test_onboarding_validation.py` — 33 tests PASS. The tests do cover the `_pre_flight_check` non-TTY return path.

---

## UC-CORE-002 — BLOCKED-NEEDS-LIVE (Low · Regression test: NO)

### Claim
The assistant should NOT claim memory at-rest encryption is on by default (MR-10). Self-capability answers must match reality.

### Evidence
**File:** `src/tokoboros/core/memory_vault.py` — grep for `encrypt`, `plaintext`, `at.rest` → no matches. ChromaDB storage is plaintext by default. No UI/TUI copy was found claiming encryption is on.

**File:** `docs/strategy/09-execution-plan.md` (MR-10): "Memory at-rest encryption is OFF by default (decision D-3 unresolved)."

### Finding
**BLOCKED-NEEDS-LIVE.** The static analysis shows no in-code text asserting at-rest encryption. Whether the *live agent* hallucinates capability claims (e.g., responding "yes, your memories are encrypted") requires a live LLM call which cannot be verified offline. The code itself makes no such promise. The risk originates from the LLM's training data or system prompt — the system prompt composition is in `src/tokoboros/core/prompt_composer.py`; no explicit "memory is encrypted" claim was found there via grep.

**Severity:** Medium (if the LLM hallucinates this claim it is a false security promise; static risk is LOW because no hardcoded claim found)
**Regression test:** Not automatable without live LLM. A golden-file prompt test against a mock LLM could be added.

---

## UC-CORE-003 — CONFIRMED (Medium · Regression test: YES)

### Claim
Long 60-turn conversation crossing the compaction boundary: sliding-window summarizes without losing the referenced facts; `tool_use`/`tool_result` pairs are never split across the boundary.

### Evidence
**File:** `src/tokoboros/core/memory.py`

Lines 751–783 (`_sliding_window_compact`): anchors the first 2 messages, selects `keep_count = max_active_messages - 2` recent messages, then calls `_find_safe_sliding_boundary(candidate_cut)`.

Lines 846–907 (`_find_safe_sliding_boundary`): walks backward from `candidate_cut` until `_is_safe(cut)` is true. `_is_safe` checks that every `tool_result` in `history[cut:]` has its `tool_use_id` also present in `history[cut:]`:
```python
for msg in history[cut:]:
    ...
    if block.get("type") == "tool_result":
        if block.get("tool_use_id") not in seen_tool_use_ids:
            return False
return True
```

Lines 784–804: additional guard — if any `tool_use` id in the **anchor** has its `tool_result` in the *retained* slice, compaction is skipped entirely to avoid splitting an anchor-to-retained pair.

Lines 838–843: the checkpoint is injected as a `user` role `[CONTEXT CHECKPOINT]` message between `anchor` and `recent`.

Lines 678–725 (`repair_tool_pairs`): called on every turn via `prepare_for_turn()` (line 659). Injects synthetic `[result unavailable]` tool_results for any orphaned `tool_use` blocks to prevent Anthropic 400.

### Finding
**CONFIRMED.** The `_find_safe_sliding_boundary` correctly prevents tool_use/tool_result splits at the compaction cut. The `repair_tool_pairs` backstop further prevents any orphan from reaching the API. All 169 compaction/memory tests pass.

**One nuance (not a defect):** `_find_safe_sliding_boundary` walks backward one index at a time (lines 904–906), which is O(n²) in the worst case when many consecutive positions are unsafe. For a 60-turn session this is negligible. Also, `_generate_structured_summary` (dropped messages → LLM summary) runs synchronously (line 836), which could add latency at the compaction point — but this is the intended design and not a correctness issue.

**Severity:** None (behavior confirmed correct) · already covered by extensive tests.
**Regression test:** `tests/test_memory*.py` — 169 pass.

---

## UC-CORE-004 — PARTIAL (Medium · Regression test: NO)

### Claim
Context switching between unrelated tasks: skill-switch compaction (`signal_skill_switch`) does not drop the wrong messages and there are no active-skill races.

### Evidence
**File:** `src/tokoboros/core/memory.py`

Lines 439–453 (`signal_skill_switch`): sets `_pending_compaction = True` and records `_pending_old_skill` when the active skill changes.

Lines 455–508 (`apply_pending_compaction`): executes only between turns, finds the most-recent "real" user message via `_find_safe_compact_boundary` (lines 510–525), and replaces everything before that boundary with a 2-message compact summary.

Lines 510–525 (`_find_safe_compact_boundary`): walks backward to find the last `user` message where content is a `str` (not a list of tool_results):
```python
for i in range(len(self._history) - 1, -1, -1):
    msg = self._history[i]
    if msg.role == "user":
        if isinstance(msg.content, str):
            return i
```
This returns the index of the *most recent* real user message. Compaction drops everything before that index.

### Finding
**PARTIAL.** The logic is sound for the common case. Two issues identified:

1. **Context bleed on very rapid skill switch.** If the user sends a new message in the same turn as a skill switch fires (i.e., `signal_skill_switch` is called but `apply_pending_compaction` has not yet run because it is deferred to the next `prepare_for_turn`), the pending compaction happens at the *start of the next turn*. If that next turn's first message is already from the new task, the compact summary includes the new user message as the "boundary marker" (it is the most-recent string user message) but that message belongs to the new task. The summary (`[Compacted: previous task using skill '...']`) plus one or two assistant snippets from old context will be prepended. The new user message is preserved correctly because `compact_boundary = i` and `_history[compact_boundary:]` is kept. So no message is lost, but the transition summary can be confusing.

2. **No deduplication guard on `signal_skill_switch`.** If `load_skill` is called twice with the same skill name in rapid succession (e.g., agent retries), `signal_skill_switch` will set `_pending_compaction = True` on the second call only if `_active_skill != new_skill_name`, which prevents spurious compaction for same-skill reloads (line 449). This works correctly.

**Severity:** Low — no data loss, some context confusion possible at skill-switch boundaries.
**Regression test:** No dedicated test found for multi-skill rapid switching. Add one.

---

## UC-CORE-005 — CONFIRMED DEFECT (High · Regression test: NO)

### Claim
Mid-turn Esc cancellation: cancel is iteration-boundary only (cannot interrupt an in-flight LLM call); user understands this; partial text is committed; next turn starts clean.

### Evidence — CONFIRMED DEFECT (TUI in-process mode: Esc does nothing to in-flight chat)

**File:** `src/tokoboros/tui/app.py`

Lines 1517–1528 (`on_key`): Esc on the `SessionScreen` calls `action_interrupt()` if `_chat_thread.is_alive()`:
```python
interrupt = getattr(app, "action_interrupt", None)
if callable(interrupt):
    interrupt()
```

Lines 3007–3013 (`action_interrupt`):
```python
def action_interrupt(self) -> None:
    self._gateway_stop_event.set()
    self._resolve_all_pending_approvals("Interrupted by operator")
    self.notify(
        "Interrupt requested. Event polling stopped and pending approvals were dismissed.",
        timeout=4,
    )
```

**`_gateway_stop_event` is only used for the gateway event polling loop** (`_poll_gateway_events`, lines 2276–2300). In **in-process TUI mode** (the default: `tokoboros run` without a gateway URL), the `_chat_thread` runs `_run_chat()` which calls `agent.loop.run(...)` directly. The `_gateway_stop_event` has no effect on that thread.

**File:** `src/tokoboros/core/loop.py`

Lines 372–380 — `loop.cancel()` sets `_cancel_event`:
```python
def cancel(self) -> None:
    self._cancel_event.set()
```

Lines 508–516 — The cancel check happens before each LLM call:
```python
if self._cancel_event.is_set():
    ...
    return f"[CANCELLED] ..."
```

**There is no code path from `action_interrupt()` → `agent.loop.cancel()`** for in-process mode. The `_run_chat()` path does not check `_gateway_stop_event`. The Esc key in in-process mode effectively does nothing to the running generation — it only dismisses pending approval dialogs and sets the poller stop flag (which is irrelevant since there is no gateway event poller running in in-process mode).

**File:** `src/tokoboros/tui/app.py` lines 3007–3013 — the `notify()` message says "Event polling stopped" which is accurate for gateway mode but misleading for in-process mode where no event polling is running.

### Finding
**CONFIRMED DEFECT.** In the default in-process TUI mode (`tokoboros run`), pressing Esc does not cancel or signal the running LLM call in any way. The `action_interrupt()` → `_gateway_stop_event.set()` path is a no-op for in-process execution. The user sees a toast saying "Interrupt requested. Event polling stopped" but the LLM call continues to completion. This is a **one-way door** — once Esc is pressed, the user cannot undo the (false) impression of cancellation without waiting.

The `loop.cancel()` mechanism exists and is correctly wired for the execution loop (`loop.py:372–380, 512`), but it is never called from the TUI Esc path.

**Fix:** `action_interrupt()` should check `self._agent` and, if present, call `self._agent.loop.cancel()`.

**Severity:** High — user-facing claim of cancel is false in the primary usage mode.
**Regression test:** None exists. A unit test mocking `_chat_thread` + asserting `loop._cancel_event.is_set()` after `action_interrupt()` would catch this.

---

## UC-CORE-006 — CONFIRMED (Low · Regression test: YES)

### Claim
Resume after restart: prior transcript restored from SessionStore, no duplicate messages, continuity preserved. Risks: `_api_sessions` not pruned on close (LOW-12); session title corruption.

### Evidence
**File:** `src/tokoboros/gateway/orchestrator.py`

Lines 464–494 (`resume_from_session`): loads `get_session_messages(source_session_id, limit=40)`, skips `tool` rows, seeds `Message` objects into `memory.seed_history()`. A new `session_id` is created by `set_session_store` (line 1744) so the resumed messages go into a new session in the store — **no re-logging of old messages** occurs.

Lines 950–958 (`run_turn`): only logs `self._session_id` (new session) for new messages:
```python
self._session_store.log_message(self._session_id, "user", user_input)
self._session_store.log_message(self._session_id, "assistant", response or "")
```

**File:** `src/tokoboros/tui/app.py` lines 2443–2459 (`_render_resumed_transcript`): reads `agent.resumed_transcript` (set during `resume_from_session`), renders it to the screen once at boot-complete. No re-render path found.

**`_api_sessions` pruning (LOW-12):** `src/tokoboros/gateway/control_plane.py` lines 308–312 show that `_api_sessions` IS pruned when a session is disconnected:
```python
stale_keys = [k for k, v in self._api_sessions.items() if v == normalized_session_id]
for k in stale_keys:
    del self._api_sessions[k]
```
LOW-12 appears resolved.

**Session title corruption:** `set_session_title` in `src/tokoboros/core/session_store.py` lines 159–174 uses a simple `UPDATE ... SET title = ?`. No sanitization or length cap is applied at the store level — the docstring says "callers are responsible for length-capping" and delegates to `core.session_titler`. No truncation bug was found in `session_titler` (the concern is at the caller level). No corruption mechanism was identified in the static code path.

### Finding
**CONFIRMED (behavior correct).** Resume correctly seeds prior messages into a new session without duplication. The TUI renders the resumed transcript once at boot. `_api_sessions` is pruned on disconnect (LOW-12 was already fixed). Session title concern is a caller-responsibility documentation issue, not a code bug.

**Severity:** None (behavior confirmed correct).
**Regression test:** `tests/test_resume_and_slash_commands.py` — 27 tests PASS. `tests/test_session_store_title.py`, `tests/test_session_store_foreign_keys.py` also PASS.

---

## Summary Table

| ID | Verdict | Severity | Key Finding |
|---|---|---|---|
| UC-CORE-001 | PARTIAL | Medium | Non-TTY path exits cleanly (no hang). Banner guidance mentions `export` not `tokoboros secrets --set`. TUI→REPL fallback leaks the first agent session if TUI crashed post-boot. `--resume` flag would block in a pipe. |
| UC-CORE-002 | BLOCKED-NEEDS-LIVE | Medium | No hardcoded encryption claim found in code. LLM self-reporting requires live verification. |
| UC-CORE-003 | CONFIRMED | None | `_find_safe_sliding_boundary` + `repair_tool_pairs` correctly prevent tool_use/tool_result splits. 169 tests pass. |
| UC-CORE-004 | PARTIAL | Low | Skill-switch compaction is functionally safe; minor context confusion possible at rapid-switch boundaries. No data loss. |
| UC-CORE-005 | CONFIRMED DEFECT | High | **Esc does not cancel in-flight LLM call in in-process TUI mode.** `action_interrupt()` only sets the gateway poller stop flag; `loop.cancel()` is never called. Fix: wire `action_interrupt()` → `self._agent.loop.cancel()`. |
| UC-CORE-006 | CONFIRMED | None | Resume correctly restores transcript without duplication. `_api_sessions` pruning (LOW-12) already implemented. |
