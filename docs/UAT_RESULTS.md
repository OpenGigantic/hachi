# Hachi — UAT Verification Results (reconciliation pass)

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

> **Method:** Code-grounded verification of every scenario in `docs/UAT_BETA_TEST_PLAN.md` against the live working tree.
> **Build:** `036dfd9` · **Date:** 2026-06-14 · **Coverage:** 110/110 scenarios (84 `UC-*` + 26 `ADV-*`)
> **How it was run:** 18 parallel verification subagents (Sonnet 4.6 / Haiku), each owning a code-area cluster, orchestrated by a "brain" session. Each agent read the cited source (verifying line numbers live), ran targeted offline tests/repros, and marked live-only scenarios `BLOCKED-NEEDS-LIVE` while still tracing the code path. Per-area evidence is in `docs/uat/01..18-*.md`.
> **Important:** This is **static/offline** verification (no live Google/Slack/Telegram accounts, no second machine, no live multi-turn LLM). It proves the *code path* and the *presence/absence of a control*; it does not prove live end-to-end behavior. This document **reconciles the plan with the code** — satisfying MR-1 / exit-criterion #4.

---

## 1. Verdict legend & tally

| Status | Meaning | Count (approx) |
|---|---|---|
| ❌ **DEFECT** | Claimed defect confirmed present in current code | ~41 |
| ⚠️ **PARTIAL** | Mechanism exists but has a real gap/caveat (several are must-fix at Critical severity) | ~45 |
| ✅ **PASS / CLEARED** | Behaves correctly, or the claimed bug is already fixed (NOT-REPRODUCED) | ~18 |
| 🚫 **BLOCKED** | Needs live services / live LLM to confirm behavioral outcome (code path traced) | ~6 |

**Bottom line:** the build is **not beta-ready**. All but one of the §1 meta-risks are confirmed in code; two meta-risks (MR-3 leakage, MR-4 tarball) are *worse* than the plan described. The good news: the recently-hardened items (config-key block, notifier injection, Gmail HTML handling, job-failure handling, Telegram resilience, Windows date formatting) genuinely check out.

---

## 2. Meta-risk reconciliation (the headline)

| # | Meta-risk | Status | Evidence |
|---|---|---|---|
| **MR-1** | Stale QA tracker | **Addressed by this pass** | This document is the reconciliation; per-area `docs/uat/*.md` regenerated. |
| **MR-2** | Gateway/daemon unauthenticated on loopback | ❌ **CONFIRMED (Critical)** | `fastapi_app.py:264` `protected_dependencies = []` when `auth_token is None`; daemon spawns gateway with no token. UC-AUTH-001/002/003, ADV-014. |
| **MR-3** | Secrets leak unredacted into event/SSE/transcript/SQLite | ❌ **CONFIRMED & WORSE (Critical)** | `redact_outbound` only wraps Slack/Discord/webhook; absent from `runtime_events.adapt_event()`. Preview cap 500→2000. Also surfaces in the web memory browser/event feed. ADV-009, ADV-022, UC-MOB-001. |
| **MR-4** | `.ogpack` tarball path traversal | ❌ **CONFIRMED, TWO bugs (Critical)** | `community_packs._extract_tar` writes outside dest on **all** Python versions; `og_pack.py:403` + `pack.py:57,184` `extractall` with no `filter=` (exploitable on 3.11/3.12; accidentally safe on dev's 3.14). ADV-010, ADV-025. |
| **MR-5** | No approval gate on destructive integration actions | ❌ **CONFIRMED (Critical)** | All six destructive Google tools (`send_email`, delete event/contact/task, trash/share file) call **zero** gates; API/gateway sessions never receive `on_permission_request`; web approvals inbox is read-only. UC-PERM-006, UC-MAIL-001, UC-CAL-005, UC-DRIVE-002, UC-TASK-003, UC-CONTACT-002. |
| **MR-6** | Spend caps default OFF | ❌ **CONFIRMED (High)** | `max_cost_usd`/`max_tokens`/per-turn variants all default `0.0`/`0` = unlimited; **no wall-clock timeout in the main loop** (only sub-agents have 120s). Iteration cap (15) is the only always-on guard. UC-SPEND-001, ADV-013. |
| **MR-7** | Memory writes can fail silently | ⚠️ **PARTIALLY FIXED** | `sync_turn` auto-extraction path was hardened in `036dfd9`, **but** the LLM-callable `core_save_memory` tool still falls back to an unmonitored daemon thread and returns "queued" regardless (`memory_tools.py:58-59,119-120`). UC-MEM-001/006. |
| **MR-8** | Not on PyPI; BYO-OAuth | ⏸️ Environmental (not code-testable) | Assumed true; cold-start cliff stands. |
| **MR-9** | No cross-process file lock | ❌ **CONFIRMED (High)** | `ScheduleStore._lock` is an in-process `threading.RLock`; `save()` is a full-file overwrite; no `fcntl`/`portalocker` anywhere. Double-fire + lost cancels. UC-PERF-003, ADV-021. |
| **MR-10** | Memory plaintext at rest | ❌ **CONFIRMED (High)** | Chroma + FTS5 SQLite + sessions.db all plaintext; D-3 opt-in unimplemented. **No product string falsely claims encryption** (good). UC-MEM-005. |

---

## 3. P0 / Critical failures — the beta gate

These map almost 1:1 to the meta-risks and **must** be fixed (or get a written, user-visible mitigation) before any external beta.

| ID | Title | Root cause (file:line) |
|---|---|---|
| UC-AUTH-001/002 · ADV-014 | Unauthenticated gateway & always-on daemon | `fastapi_app.py:264`; `daemon.py:133-149` |
| ADV-009 | Secrets leak into events/SSE/transcript/SQLite | `runtime_events.py` (`adapt_event` has no `redact_*`); `loop.py:687` |
| ADV-010 | Tarball path traversal (two distinct bugs) | `community_packs.py:171-199`; `og_pack.py:403`; `pack.py:57,184` |
| ADV-006 | Delegation falls back to **unfiltered** tool set on role-`.og` parse failure | `delegation_tools.py:438-439` `except Exception: pass` |
| UC-PERM-006 | No approval path for API/gateway/channel sessions; web inbox read-only | gateway never passes `on_permission_request` to `boot_agent` |
| ADV-002 | Incoming-email injection → destructive | email body reaches model with no `guard_untrusted` framing + no send/delete gate |
| UC-CAL-005 · UC-DRIVE-002 | Destructive calendar delete / external Drive share, no gate or validation | `calendar.py:160`; `drive.py:103-107` |
| UC-MEET-001 | Meet monitor: daemon thread, `print("You: ")` to stdout, silent 30-min timeout, swallowed errors | `meet.py:70,104,138,182-184` |
| ADV-023 | Cross-account memory contamination (no per-account namespace) | single `PlatformDirs` path, no `account_id` anywhere |
| UC-MEM-001/006 | Silent memory write-loss via `core_save_memory` daemon-thread fallback | `memory_tools.py:58-59,119-120` (Critical, PARTIAL) |
| UC-SPEND-001 · ADV-013 | Spend caps default unlimited; no main-loop wall-clock timeout | `loop.py:232-235,422-455` |

---

## 4. New findings (not in the plan)

1. **Gmail nested-`<script>` regex leak** — `_html_to_text` matches only to the first `</script>`, so nested script tags leak the outer script body into "clean" text. (`gmail.py`, UC-MAIL-006)
2. **`reply_thread` sends to an empty `To:`** silently when the original `From:` header is absent. (`gmail.py:214`, UC-MAIL-002)
3. **Slack remediation message is a dead-end** — the bot-token guard tells users to `hachi secrets --set slack_user_token`, but `SlackService.__init__` only ever reads `slack_bot_token`; there is no code path that uses a `xoxp-` token. (UC-INT-001)
4. **`"404" in str(e)` false-trigger** in the Gmail thread-read fallback — fires the message-level fallback for any error whose text contains the digits "404" (e.g. a thread-id starting with 404), masking the real error. (UC-MAIL-005)
5. **Config-ignored singleton returns the wrong backend** — `ProcessSharedServices.get_or_create` fast-paths on `_INSTANCE is not None` with no config/brain-dir equality check; a session requesting a non-default `memory.backend` silently gets the first session's Chroma/FTS5 client, no warning. (`shared_services.py:80`, ADV-024)
6. **FTS5 refresh TOCTOU** — `refresh()` does `DELETE`+bulk-`INSERT` on one connection while `search()` uses another → a window of empty results (not corruption) during refresh. (UC-PERF-002)
7. **Plan over-counted exception-swallow sites** — actual `except: pass`-style count is ~6–8, not the "104" claimed; logging opacity is the real issue, not the count. (UC-OFF-003)
8. **`run_shell_script` unusable at `guided`** but its schema advertises it as the fallback "when `run_terminal_command` is too restrictive" — misleading. (UC-PERM-004)

---

## 5. Cleared — claimed bugs that are already fixed / not reproduced (good news)

- **ADV-007** config tier/key escalation — blocked (`config_tools.py:87-101`), 24 tests pass.
- **ADV-016 / UC-DESK-001 injection** — title/body travel as env vars, `-Command` is a constant; the live Windows injection test passed on this box.
- **UC-CFG-001** config allowlist + secret/metachar rejection — works.
- **UC-AUTH-004** — invalid provider key does not leak into the client response (clean by construction).
- **UC-PLAN-001 (Windows date)** — `%-d` glibc bug already fixed; uses portable `str(_d.day)`.
- **UC-INT-002 Telegram** — 4096-char chunking **and** `ok:false` poll logging are present (refutes the plan's "deaf/no-chunk" claim).
- **UC-INT-005 voice** — `.opus`/`.aac` round-trip works; WAV duration sentinel is fixed. (Only `.pcm` is a real write/reject dead-end.)
- **UC-INT-003 Notion** — `query_database`/`read_page_blocks` paginate; the `.upper()`-on-`None` crash is guarded. (Only `search_workspace` still caps at 20.)
- **UC-OFF-001** — failed scheduled tasks are logged and retried, **not** silently marked fired (refutes the plan).
- **UC-PERM-001** sandbox tier — all four dangerous tools correctly blocked (40 hardening tests pass).
- **UC-CORE-003 / UC-CORE-006** — compaction never splits tool_use/tool_result pairs (safe-boundary + repair); resume produces no duplicates; `_api_sessions` pruning already implemented.
- **UC-AUTO-006** — all three `036dfd9` job-queue recovery claims verified (RUNNING→PENDING reset, func-only discard, loud `job.failed`).
- **UC-PERF-001/004/006** — per-session serialization + `session.busy`, 100k prompt cap, 3-retry backoff with dup-output guard all work.

---

## 6. Full per-scenario verdict matrix

> Status: ❌ DEFECT · ⚠️ PARTIAL · ✅ PASS/CLEARED · 🚫 BLOCKED. Detail + quoted code in the linked per-area file.

### Core, conversation, memory — [01](uat/01-auth.md) [07](uat/07-memory.md) [14](uat/14-core.md)
| ID | Status | Sev | Note |
|---|---|---|---|
| UC-CORE-001 | ⚠️ | Med | Non-TTY exits cleanly; guidance is Unix-only `export`, no `hachi secrets`; REPL-fallback may leak a booted session |
| UC-CORE-002 | 🚫 | Med | No hardcoded encryption claim in source; live hallucination check needs LLM |
| UC-CORE-003 | ✅ | — | Safe-boundary + repair correct; 169 tests pass |
| UC-CORE-004 | ⚠️ | Low | Skill-switch compaction safe; minor summary pollution |
| UC-CORE-005 | ❌ | High | **Esc does not stop an in-flight LLM call** in in-process TUI; `loop.cancel()` exists but Esc never calls it (`tui/app.py:3007`) |
| UC-CORE-006 | ✅ | — | No duplicate on resume; pruning implemented; 27 tests pass |
| UC-MEM-001 | ⚠️ | **Crit** | `core_save_memory` daemon-thread fallback → silent loss; returns "queued" regardless |
| UC-MEM-002 | ✅ | Low | 25-entry cap enforced cleanly with `LIMIT_REACHED` |
| UC-MEM-003 | 🚫 | Med | Consolidation design sound; malformed LLM JSON leaves both facts |
| UC-MEM-004 | ⚠️ | High | Chroma delete works; FTS5 has no delete (stale); `.og` never purged; sessions still surface "forgotten" facts |
| UC-MEM-005 | ❌ | High | Plaintext at rest confirmed (no false encryption claim) |
| UC-MEM-006 | ⚠️ | **Crit** | Same root cause as MEM-001; dead worker leaves queue undrained |

### Auth, leakage, permissions, config — [01](uat/01-auth.md) [02](uat/02-leak.md) [05](uat/05-permpath.md) [06](uat/06-ssrf-cfg.md)
| ID | Status | Sev | Note |
|---|---|---|---|
| UC-AUTH-001 | ❌ | Crit | Gateway loopback: no auth on any protected route |
| UC-AUTH-002 | ❌ | Crit | Daemon spawns gateway with no token (always-on open surface) |
| UC-AUTH-003 | ❌ | High | CORS allows localhost:5173/4173 + unauth = browser-driven control |
| UC-AUTH-004 | ✅ | Low | Provider key not leaked to client on error |
| UC-PERM-001 | ✅ | — | Sandbox blocks terminal/sandbox/browser/vision |
| UC-PERM-002 | ⚠️ | High | `../` blocked; **UNC path has no explicit guard** and bypasses blocked-paths at `unrestricted`; symlink untested on Windows |
| UC-PERM-003 | ⚠️ | Med | Operators/interpreters/wrappers blocked; bare `cat id_rsa` from a blocked cwd is un-path-checked (documented, untested) |
| UC-PERM-004 | ⚠️ | Low | Operators rejected; `run_shell_script` unusable at `guided` + misleading schema |
| UC-PERM-005 | ⚠️ | High | approve/deny/once correct; 180s timeout silently auto-denies, indistinguishable from explicit deny |
| UC-PERM-006 | ❌ | Crit | No approval path for API/gateway/channel sessions; web inbox read-only |
| UC-CFG-001 | ✅ | — | Allowlist + secret/metachar rejection works (24 tests) |
| UC-DESK-001 | ⚠️ | Low | ~8s toast latency (structural); injection re-verified safe |
| ADV-005 | ⚠️ | High | net_guard + no-redirect everywhere **except** `pack_index.py:153` `urlopen` on user `--index-url` (follows redirects) |
| ADV-007 | ✅ | — | Tier/key escalation blocked |
| ADV-009 | ❌ | Crit | Secrets unredacted across all 7 event/SSE/transcript/SQLite surfaces |
| ADV-014 | ❌ | Crit | Full control-plane takeover, zero credentials |
| ADV-015 | ⚠️ | High | Rate limits work, but empty channel allowlist accepts any `sender_id` |
| ADV-016 | ✅ | — | Notifier PowerShell injection fixed (live test passed) |
| ADV-022 | ⚠️ | High | Event feed + memory browser render unredacted payloads |

### Skills, delegation, autonomy, spend, scheduling — [03](uat/03-packs.md) [08](uat/08-schedule.md) [09](uat/09-delegation.md) [10](uat/10-autonomy-spend.md)
| ID | Status | Sev | Note |
|---|---|---|---|
| UC-SKILL-001 | ✅ | Low | Version bump 1.0.0→1.0.2 + changelog + hot-load work (noisy logger) |
| UC-SKILL-002 | ⚠️ | High | SSRF-guarded main path; `pack_index` fetch unguarded; **no signature verification** (`sha256s={}` passes silently) |
| UC-SKILL-003 | ⚠️ | Med | Binary/IO parse errors surface; a zero-node syntax typo silently disappears even from `doctor` |
| UC-AUTO-001 | ✅ | Low | Iteration cap → clean `[INCOMPLETE]` + distinct event |
| UC-AUTO-002 | 🚫 | Med | No static clarification gate (LLM-emergent) |
| UC-AUTO-003 | ⚠️ | Med | Isolation + recursion block OK; cost pooled, no per-delegation breakdown |
| UC-AUTO-004 | ⚠️ | Med | Lease acquire/block/release OK; TTL(300s) expiry collision; prefix false-positives |
| UC-AUTO-005 | ⚠️ | Med | 120s timeout is event-boundary-only; TIMEOUT vs iteration-cap indistinguishable |
| UC-AUTO-006 | ✅ | Low | Crash recovery verified (reset/discard/loud-fail) |
| UC-SPEND-001 | ❌ | High | Caps correct but **default 0 = unlimited**; no safe default/nudge |
| UC-SPEND-002 | ✅ | Low | Cache pricing matches; GPT-4o price may be stale; fallback underestimates |
| UC-RSCH-001 | ❌ | High | UTC-only schedule math → fires at wrong local hour |
| UC-RSCH-002 | ❌ | High | Resident runner ignores `deliver_to`/`webhook_url` — webhook only via `run-due` |
| UC-RSCH-003 | ❌ | Med | Missed runs collapse to a single catch-up (undocumented) |
| UC-RSCH-004 | ⚠️ | Low | Serialized correctly; slow task stacks latency (not corruption) |
| UC-PERF-003 | ❌ | High | No OS file lock on `schedules.json` → double-fire + lost writes |
| ADV-006 | ❌ | Crit | Role-`.og` parse failure → **unfiltered** tool set (`send_email`, `python_sandbox` appear) |
| ADV-010 | ❌ | Crit | Two tarball path-traversal bugs (one version-independent) |
| ADV-011 | ⚠️ | High | Injected `::full` reaches LLM unsanitized (MemoryGuard not on skill load); `::code`/`::assertion` are NOT eval'd (safe) |
| ADV-012 | ⚠️ | High | One-level recursion block works; no depth/cycle backstop |
| ADV-013 | ⚠️ | High | Iteration cap only; no main-loop wall-clock; spend caps off |
| ADV-021 | ❌ | High | Cross-process cancel overwritten → double-execution |
| ADV-024 | ❌ | High | `get_or_create` ignores config after first call → wrong memory backend silently |
| ADV-025 | ⚠️ | High | Tar bomb unbounded; no `.og` node cap; no JSON depth limit; unpaginated reads |

### Google Workspace — [11](uat/11-gmail.md) [12](uat/12-google-cal-task.md) [13](uat/13-google-drive-docs.md)
| ID | Status | Sev | Note |
|---|---|---|---|
| UC-MAIL-001 | ❌ | High | Sends immediately, no gate |
| UC-MAIL-002 | ❌ | Med | No address validation; `reply_thread` can send to empty `To:` |
| UC-MAIL-003 | ❌ | High | Defaults to 5 results, no pagination, no truncation note |
| UC-MAIL-004 | ❌ | High | No attachment size cap (OOM)/symlink check/permission gate |
| UC-MAIL-005 | ⚠️ | Low | 404 fallback works but `"404" in str(e)` is fragile |
| UC-MAIL-006 | ⚠️ | Med | script/style stripped, but nested `<script>` leaks; NL injection unframed |
| UC-MAIL-007 | ❌ | Med | `batchModify` partial failure reported as full success |
| ADV-002 | ❌ | Crit | Email body raw to agent (no `guard_untrusted`) + no send/delete gate = full attack chain |
| UC-CAL-001 | ❌ | High | Timezone UTC-hardcoded; attendee emails only whitespace-stripped |
| UC-CAL-002 | ❌ | Med | No ISO / start≤end validation |
| UC-CAL-003 | ❌ | Med | No RFC-5545 RRULE validation |
| UC-CAL-004 | ❌ | Med | free-time math all UTC |
| UC-CAL-005 | ❌ | Crit | Delete immediate, no gate |
| UC-TASK-001 | ❌ | Med | No pagination beyond `max_results=20` |
| UC-TASK-002 | ❌ | Low | Invalid due date appended `T00:00:00Z` unvalidated |
| UC-TASK-003 | ❌ | Med | Delete immediate, no gate |
| UC-CONTACT-001 | ❌ | Low | No email/phone validation; unranked substring search |
| UC-CONTACT-002 | ❌ | Med | Delete immediate despite "DANGER" label, no gate |
| UC-PLAN-001 | ⚠️ | Med | Windows `%-d` already fixed; exceptions still swallowed → "(unavailable)" with no reason |
| UC-DRIVE-001 | ❌ | High | No size limit; full file into memory |
| UC-DRIVE-002 | ❌ | Crit | No email validation + no gate → accidental external share |
| UC-DRIVE-003 | ✅ | — | Error contract consistent |
| UC-DOCS-001 | ❌ | High | Fragile end-index off-by-one on empty/single-char docs |
| UC-DOCS-002 | ❌ | High | No pagination; full doc into memory |
| UC-SHEET-001 | ❌ | High | No range validation; full sheet into memory |
| UC-MEET-001 | ❌ | Crit | Daemon thread + stdout print + 30-min silent timeout + swallowed errors |

### Integrations, perf, offline, behavioral — [15](uat/15-integrations.md) [16](uat/16-perf-concur.md) [17](uat/17-offline-recovery.md) [18](uat/18-adv-behavioral.md)
| ID | Status | Sev | Note |
|---|---|---|---|
| UC-INT-001 | ⚠️ | Med | Bot-token guard present but `slack_user_token` remediation is a no-op |
| UC-INT-002 | ✅ | — | Chunking + ok:false logging present (claim refuted) |
| UC-INT-003 | ⚠️ | Med | `search_workspace` caps at 20; query/read paginate; crash guarded |
| UC-INT-004 | ❌ | High | Shared MCP singleton: no health check/re-init; dead pool inherited; ~37s boot hang |
| UC-INT-005 | ⚠️ | Low | `.pcm` write/reject dead-end; `.opus`/`.aac`/WAV refuted |
| UC-PERF-001 | ✅ | Low | Per-session lock + `session.busy` (32 tests) |
| UC-PERF-002 | ⚠️ | Med | Chroma defers to SQLite lock; FTS5 refresh TOCTOU empty-window |
| UC-PERF-004 | ✅ | Low | 100k prompt cap enforced |
| UC-PERF-005 | ⚠️ | Med | Read-file size check bypassable via `except Exception: pass` (`os_tools.py:390`) |
| UC-PERF-006 | ✅ | Low | 3-retry backoff + dup-output guard |
| ADV-024 | ❌ | High | Stale singleton ignores config (see New findings) |
| ADV-025 | ⚠️ | High | Tar bomb + unbounded `.og` + no JSON depth limit |
| UC-OFF-001 | ✅ | — | Scheduled failures logged + retried (claim refuted) |
| UC-OFF-002 | ⚠️ | Low | Ollama works; curated fallback picker not implemented |
| UC-OFF-003 | ⚠️ | Med | Degrades safely; swallow count ~6–8 (not 104); logging opacity real |
| UC-OFF-004 | ✅ | Low | Empty/large/Unicode inputs handled |
| UC-DEV-001 | ⚠️ | Med | TTY-aware boot; MCP init has no timeout (hang risk) |
| UC-MOB-001 | ⚠️ | High | Responsive present; memory browser leaks unredacted secrets (escalates MR-3) |
| ADV-001 | ⚠️ | High | No structural injection fence; `brain.og` rules are soft only |
| ADV-003 | ❌ | High | `google_read_doc` returns raw text with no `guard_untrusted` (inconsistent vs `browse_web`) |
| ADV-004 | ⚠️ | Med | `browse_web`/REST call `guard_untrusted` (tested); Playwright redirect SSRF + sub-threshold residuals |
| ADV-017 | ⚠️ | Med | Real tool results channel-separated; doc-embedded fakes arrive unlabeled (chains ADV-003) |
| ADV-018 | 🚫 | Med | No structural contradiction resolver; MR-5 makes destructive resolution irreversible |
| ADV-026 | ⚠️ | High | Rollback checkpoint gap (`except Exception: pass`, `os_tools.py:411-416`); no bulk-destructive disambiguation |
| UC-RES-001 | ⚠️ | Med | net_guard works; Playwright redirect residual; citation fabrication undetected |
| UC-RES-002 | ⚠️ | Med | URL path guarded; doc path not; no truncation notice |
| UC-RES-003 | ⚠️ | Med | Draft vs send are distinct tools but nothing prevents the wrong one (MR-5) |

---

## 7. Recommended remediation order

1. **One-line / small-surface security wins (highest ROI):**
   - MR-2: require a bearer token by default (or bind off-loopback only with auth) — `fastapi_app.py:264`, `daemon.py`.
   - MR-3: apply `redact_secrets` inside `runtime_events.adapt_event()` (single choke point) + scrub the web memory browser/event feed.
   - MR-4: `tf.extractall(..., filter='data')` + reject `..`/absolute members in `community_packs._extract_tar`; add the regression test exit-criterion #6 requires.
   - ADV-006: fail **closed** on role-`.og` parse failure in `delegation_tools.py:438` (deny tools / abort), not `except: pass`.
2. **Approval gate (MR-5):** bind an action-level confirmation to the six destructive Google tools and wire an approver for API/gateway/channel sessions (make the web inbox actually approve).
3. **Memory trust (MR-7/MR-10/ADV-023):** make `core_save_memory` fail loudly; namespace memory per account; add FTS5/`.og` delete; surface the plaintext-at-rest posture.
4. **Scheduling correctness (MR-9):** local-tz schedule math, honor `deliver_to` in the resident runner, add a cross-process file lock.
5. **Validation/pagination sweep (Google + Sheets/Docs/Gmail):** address the broad "no input validation / no pagination / no size cap" class — mostly Medium but high-volume.
6. **UX/correctness:** Esc cancel must call `loop.cancel()` (UC-CORE-005); spend-cap safe defaults (MR-6); MCP init timeout + health check (UC-INT-004); fix the Slack remediation dead-end and Gmail nested-script leak.

---

## 8. Per-area evidence index

| File | Area |
|---|---|
| [docs/uat/01-auth.md](uat/01-auth.md) | Gateway/daemon auth, CORS, rate limits |
| [docs/uat/02-leak.md](uat/02-leak.md) | Secret leakage into events/SSE/transcript/web |
| [docs/uat/03-packs.md](uat/03-packs.md) | Tarball traversal, malicious skill content, skill mgmt |
| [docs/uat/04-approval.md](uat/04-approval.md) | Approval gates, TOCTOU, MR-5 cross-check |
| [docs/uat/05-permpath.md](uat/05-permpath.md) | Tiers, path containment, shell allow/block |
| [docs/uat/06-ssrf-cfg.md](uat/06-ssrf-cfg.md) | SSRF, config security, notifier injection |
| [docs/uat/07-memory.md](uat/07-memory.md) | Memory reliability/poisoning/exfil/cross-account |
| [docs/uat/08-schedule.md](uat/08-schedule.md) | Scheduling, cross-process state |
| [docs/uat/09-delegation.md](uat/09-delegation.md) | Sub-agents, leases, recursion/escalation |
| [docs/uat/10-autonomy-spend.md](uat/10-autonomy-spend.md) | Loop, spend caps, job queue |
| [docs/uat/11-gmail.md](uat/11-gmail.md) | Gmail send/validate/search/attach/reply/read + injection |
| [docs/uat/12-google-cal-task.md](uat/12-google-cal-task.md) | Calendar, Tasks, Contacts, Planning |
| [docs/uat/13-google-drive-docs.md](uat/13-google-drive-docs.md) | Drive, Docs, Sheets, Meet |
| [docs/uat/14-core.md](uat/14-core.md) | Onboarding, compaction, cancel, resume |
| [docs/uat/15-integrations.md](uat/15-integrations.md) | Slack, Telegram, Notion, MCP, voice |
| [docs/uat/16-perf-concur.md](uat/16-perf-concur.md) | Concurrency, scale, large inputs |
| [docs/uat/17-offline-recovery.md](uat/17-offline-recovery.md) | Offline, recovery, desktop/mobile |
| [docs/uat/18-adv-behavioral.md](uat/18-adv-behavioral.md) | Behavioral injection, contradictions, research safety |
