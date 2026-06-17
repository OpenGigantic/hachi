# Hachi — UAT Remediation Record

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

> **Supersedes the open findings in `UAT_RESULTS.md`.** This is the authoritative
> "what was fixed" record for the remediation campaign run against `UAT_RESULTS.md`.
> **Branch:** `fix/uat-remediation` · **Base build:** `036dfd9` · **Date:** 2026-06-14
> **Verification:** `2206 passed, 14 skipped, 0 failed` · `ruff check` clean ·
> strict mypy gate green (36/36) · OpenAPI + frontend API types regenerated.

## How it was run

18 subagents across 3 dependency-ordered waves, each owning a **disjoint** set of
files (no two agents ever edited the same file). Shared behavior was built once in
Wave 1 and *consumed* by Wave 2:

- **Wave 1 — foundations:** approval gate, secret redaction, safe tar-extraction,
  input validators, delegation fail-closed, gateway auth.
- **Wave 2 — leaf fixes:** Gmail, Calendar/Tasks, Drive/Docs/Sheets,
  Contacts/Planning/Meet, memory, scheduling, loop/cancel, MCP/shared-services,
  Slack/Notion, voice/channels, OS-tools, OG-engine.
- **Wave 3 — integration:** cross-cutting reconciliation, full suite, lint, mypy,
  contract regen (this record).

## Meta-risk resolution

| # | Risk | Status | Fix |
|---|---|---|---|
| **MR-2** | Unauthenticated gateway/daemon | ✅ Fixed | `fastapi_app.py`: bearer token required on all protected routes; loopback no longer exempts auth; token auto-generated + persisted (`~/.hachi/gateway-token`, 0600) + printed once; `daemon.py` passes the same token. Opt-out `HACHI_GATEWAY_NO_AUTH=1` logs a warning. |
| **MR-3** | Secrets leak into events/SSE/transcript | ✅ Fixed (display surfaces) | `runtime_events.adapt_event()` now redacts tool-call input (recursively) and tool-result preview via `secret_scanner.redact_structure`; `runtime_read_models` scrubs web-facing snapshots. *Caveat:* the SQLite **message transcript** still stores real tool outputs (the agent needs them) — its protection is at-rest encryption (MR-10), not redaction. |
| **MR-4** | `.ogpack` tarball path traversal / tar-bomb | ✅ Fixed | New `core/archive_safety.safe_extract()` rejects `..`/absolute/symlink members and caps file count + total bytes; wired into all 3 `extractall` sites and the manual `community_packs._extract_tar`. Version-independent (verified on Py 3.14). |
| **MR-5** | No approval gate on destructive actions | ✅ Fixed | New `core/approvals.py` (`ActionApprover`, `PendingApprovalRegistry`) + `GoogleService.require_approval()`. All 6 destructive Google tools (send_email, delete event/task/contact, trash/share file) gate before the API call. **Tier-gated policy:** auto-approve at `unrestricted`, prompt/deny otherwise. TUI uses the modal; API/channel sessions use the registry + `GET/POST /approvals` REST. |
| **MR-6** | Spend caps default off, no wall-clock | ✅ Fixed (backstop) | `loop.py`: always-on `max_wall_seconds` (default 600s) → `[TIME LIMIT REACHED]`; token safety ceiling (2M) independent of opt-in budget; one-time startup nudge. $/token caps stay opt-in (no surprise cut-offs), per chosen posture. |
| **MR-7** | Memory writes fail silently | ✅ Fixed | `memory_tools.core_save_memory` now returns a loud `MEMORY WRITE FAILED:` result when enqueue fails — never claims success on loss. Daemon-thread fallback removed. |
| **MR-9** | No cross-process lock on schedules | ✅ Fixed | New `core/file_lock.py` (msvcrt/fcntl advisory lock); `ScheduleStore` load/save serialized + atomic (`os.replace`). |
| **MR-10** | Memory plaintext at rest | ◻️ By design (opt-in, D-3) | No code change to storage; verified **no product string falsely claims encryption**. Encryption remains a separate opt-in decision. |

## Other notable fixes (by area)

- **Delegation (ADV-006/012):** role-`.og` parse failure now **fails closed** (aborts; never falls back to the unfiltered tool set); added depth/cycle backstop.
- **Validation (new `core/validators.py`):** email / ISO-datetime / date / time-range / RRULE / A1-range / phone validators consumed across Gmail, Calendar, Tasks, Drive, Sheets, Contacts.
- **Pagination / size caps:** Gmail list/search, Tasks, Notion `search_workspace`, Docs/Sheets reads, Drive uploads (size + symlink guard).
- **Gmail:** nested-`<script>` strip fixed; untrusted-content framing on inbound bodies (ADV-002); robust HTTP-status 404 detection; batch partial-failure reporting; reply-to-empty-`To` guarded.
- **Meet:** removed `print()` to stdout, managed job/thread lifecycle, surfaced timeout + transcription-required notice, no swallowed errors.
- **Scheduling:** local/IANA timezone cron math (added `tzdata` dependency); resident runner now honors `deliver_to`/`webhook_url` via `deliver_result()` (secret-redacted); missed-run collapse logged.
- **Loop/TUI cancel (UC-CORE-005):** Esc now calls `loop.cancel()`; cancel is cooperative mid-stream; distinct statuses for cancel/time-limit/iteration-cap.
- **Shared services (ADV-024):** singletons keyed by config fingerprint (no wrong-backend reuse); MCP health-check + boot timeout.
- **OG engine:** malformed `.og` surfaced (not silently dropped); MemoryGuard scan on skill load (ADV-011); node-count + JSON-depth caps (ADV-025).
- **OS tools:** read-size check fails closed; bare-filename-from-blocked-cwd path-checked; checkpoint failures surfaced; non-blocking pre-warmed notifier.
- **Permissions:** UNC-path guard; action-level `require_action()`.
- **Channels:** sender allowlist fails **closed** when empty (opt-in flag for open access).

## New modules & tests

- **New shared modules:** `core/approvals.py`, `core/archive_safety.py`, `core/file_lock.py`, `core/validators.py`.
- **New test suites (15):** action-approval, archive-safety, validators, delegation-failclosed, gateway-auth-default, runtime-events-redaction, gmail / calendar-tasks / drive-docs-sheets / contacts-planning-meet hardening, memory-reliability, scheduler-hardening, loop-backstop-cancel, shared-services-mcp, slack-notion, voice-channel-allowlist, os-tools-hardening, og-engine-hardening.

## Follow-ups (intentionally out of scope)

- **Web approvals UI:** the `/approvals` REST surface is live (curl-/API-drivable); a React inbox component to drive it from the web companion is a frontend follow-up.
- **At-rest encryption (MR-10):** still an opt-in decision (D-3), not implemented here.
- **MR-8 (PyPI / BYO-OAuth):** environmental, not code-fixable.

## Reproduce verification

```bash
uv sync --extra dev --extra google
uv run python -m pytest -q --continue-on-collection-errors
uv run ruff check src tests
uv run python -m mypy --config-file mypy-strict-gate.ini
```
