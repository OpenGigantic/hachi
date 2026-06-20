# Agent 2 — White Box Backend Audit (2026-06-20)

**Scope:** Backend source analysis of Hachi at `D:\OpenGigantic\hachi` — gateway, orchestrator,
core loop/memory/session/sub-agent, jobs queue, og_engine, tools, tests. Re-verification of
prior-audit (2026-06-19) remediation PRs #116–129 against current `main`, with explicit
skepticism about cosmetic/false-green fixes. BE-1 (#128) is treated as NOT on main.

**Test suite status:** `uv run --extra dev python -m pytest -q` → **2466 passed, 15 skipped,
0 failed** in ~261s. Skips are environment-gated (Playwright/pptx/symlink/launchd) EXCEPT one
that hides a real concurrency bug (see WB-07). A second-checkout pollution issue inflates the
collection set (see WB-09).

---

## 1. Summary assessment

The merged remediation work is **substantially real, not cosmetic** — this is a marked
improvement over the 2026-06-18 v2 pass. The five fixes I was asked to validate
(HachiError hierarchy, sub-agent model override, orchestrator silent-except logging, config-tool
integration tests with real types, `TurnContext.surface`) all landed correctly and exercise the
real code path: the config-tool tests now invoke through a live `ToolRegistry.execute(ToolCall)`
with real `ResolvedConfig` (no mocked namespaces), and the orchestrator `except` blocks carry
genuine `logger.warning(..., exc_info=True)` calls rather than comment-only stubs.

As predicted, the four BE-1 (#128, draft) items are **STILL-OPEN on main**: no session
ownership enforcement, dead-code session resume, no `hachi serve`, and a hardcoded gateway model
list. These remain the dominant backend release blockers. Beyond the assigned scope I found a
residual `MemoryVault` post-close `NoneType.upsert` race that the RLock fix did not fully close
(a test *skips* around it, masking it), and a second `tokoboros` checkout polluting test
collection — both undermine confidence in the green suite.

**Verdict for backend:** the architecture is coherent and the merged fixes are sound, but the
six confirmed criticals from 2026-06-19 are only partly retired on main — **NO-GO** stands for
any multi-user/exposed deployment, primarily on CRIT-006 (session ownership) and CRIT-007 (resume).

**Severity counts (this report):** Critical 3 · High 4 · Medium 5 · Low 3 = 15 findings.

---

## 2. Findings table

| ID | Title | Category | Severity | Verdict | Evidence |
|----|-------|----------|----------|---------|----------|
| WB-01 | No session ownership enforcement — any bearer token reads/writes any session | Security | Critical | STILL-OPEN | `gateway/fastapi_app.py:443-479`; no `owner_*`/`principal` anywhere in `gateway/` |
| WB-02 | Session resume is dead code; `--resume`/`--continue` start fresh, exit 0 | Functional | Critical | STILL-OPEN | `cli/main.py:1793` (`_resolve_resume_target`, unused) vs `:1860-1866` |
| WB-03 | No `hachi serve` single-process command | Product/Arch | Critical | STILL-OPEN | no `serve` command in `cli/main.py` |
| WB-04 | Gateway `list_models` hardcoded, missing running model | Functional | High | STILL-OPEN | `cli/main.py:501-503` `["claude-haiku-4-5","claude-sonnet-4-5"]` |
| WB-05 | `create_gateway_app(auth_token=None)` silently disables auth | Security | High | PARTIAL | `gateway/fastapi_app.py:331-336`; served path guarded, contract footgun remains |
| WB-06 | Python sandbox = process isolation only (full host FS/net) in unrestricted tier | Security | High | PARTIAL | `tools/sandbox_tools.py:99-110`; `core/sandbox.py` |
| WB-07 | `MemoryVault` post-`close()` access raises `NoneType.upsert`; RLock didn't close it | Reliability | High | PARTIAL | `core/memory_vault.py:314-340` null vs `:411-412` upsert; skip at `test_cockpit_gaps.py:104` |
| WB-08 | HachiError hierarchy replaces stub exceptions | Tech Debt | Medium | FIXED | `core/errors.py:16-133` |
| WB-09 | Stray `D:\OpenGigantic\tokoboros\` checkout pollutes test collection | Test Integrity | Medium | NEW | skip paths `..\tokoboros\tests\...` in pytest `-rs` |
| WB-10 | No test asserts dynamic/correct gateway model list (false-green for WB-04) | Test Integrity | Medium | STILL-OPEN | no `/models` assertion for `claude-sonnet-4-6`/`opus-4-8` in `tests/` |
| WB-11 | Sub-agent model override now respected | Architecture | Medium | FIXED | `core/sub_agent.py:591-601`; `tools/delegation_tools.py:106-113,549-561` |
| WB-12 | Orchestrator silent `except: pass` now logged | Reliability | Medium | FIXED | `gateway/orchestrator.py:758-765,780-784,895,933,…` |
| WB-13 | `TurnContext.surface` threaded from request, not hardcoded `'cli'` | Architecture | Low | FIXED | `orchestrator.py:1049,1133`; `turn_context.py:61,134`; `fastapi_app.py:433` |
| WB-14 | SQLiteJobQueue persistent connection; BM25 O(log V) bisect index | Performance | Low | FIXED | `jobs/queue.py:557-591`; `og_engine/engine.py:20,371,426-434` |
| WB-15 | `list_skill_models` fallback swallows errors to `[]` with no log | Reliability | Low | NEW | `gateway/orchestrator.py:610-613` |

---

## 3. Detailed findings

### WB-01 — No session ownership enforcement (CRIT-006)
- **Category:** Security · **Severity:** Critical · **Verdict:** STILL-OPEN · **Complexity:** Medium
- **Root cause:** Session routes accept only the path `session_id`; `_require_auth` returns no
  principal and the control plane gates solely on `session_id in connections`. No owner identity
  is stored at creation.
- **Impact:** Any holder of a valid bearer token can read another session's transcript, inject
  messages, delete it, and tail its SSE stream. Release-blocking for any multi-user or exposed
  deployment; bounded only by single-shared-token/loopback usage.
- **Evidence:** `gateway/fastapi_app.py:443` (`get_session`), `:447` (`post_message`),
  `:451` (`delete_session`), `:455` (`get_session_events`), `:463` (`stream_session_events`) —
  none take a caller identity. Grep of `gateway/` for `owner_token|principal|owner_id` → 0 hits.
- **Fix:** Store `owner_token_hash = sha256(token)` on the session record at creation; resolve the
  caller hash in `_require_auth`; return 403 on mismatch across read/write/delete/stream.

### WB-02 — Session resume is dead code (CRIT-007)
- **Category:** Functional · **Severity:** Critical · **Verdict:** STILL-OPEN · **Complexity:** Large
- **Root cause:** `_resolve_resume_target()` exists and is correct (`cli/main.py:1793-1840`) but is
  never called. `_launch_assistant` ignores the flags and prints a yellow "starting a fresh
  session" banner, returning exit 0.
- **Impact:** `--resume`/`--continue` silently discard the requested session and duplicate work.
  No non-zero exit on a missing session. The gateway `_boot_session_agent(session_id)` also ignores
  the id, so the fix must thread it through both the CLI and the gateway boot path.
- **Evidence:** `cli/main.py:1860-1866`.
- **Fix:** Wire `_resolve_resume_target` into `_launch_assistant`; load that session's history from
  `SessionStore` into `MemoryManager` before turn 1; non-zero exit on unknown id.

### WB-03 — No `hachi serve` single-process command (CRIT-008)
- **Category:** Product/Architecture · **Severity:** Critical · **Verdict:** STILL-OPEN · **Complexity:** Medium
- **Root cause:** No `serve` Typer command; README still mandates `hachi gateway` + `npm run dev`
  in two terminals.
- **Impact:** First-value path is 15–25 min; no way to serve the built frontend bundle from the
  gateway process. Onboarding blocker.
- **Evidence:** no `serve` command in `cli/main.py`.
- **Fix:** Add `hachi serve` mounting `frontend/dist` via `StaticFiles`, auto-generating a loopback
  token, opening the browser.

### WB-04 — Gateway `list_models` hardcoded (HIGH-008)
- **Category:** Functional · **Severity:** High · **Verdict:** STILL-OPEN · **Complexity:** Trivial
- **Root cause:** `cli/main.py:501-503` returns a static map `{"anthropic": ["claude-haiku-4-5",
  "claude-sonnet-4-5"]}` — missing `claude-opus-4-8` (the model Hachi itself runs on),
  `claude-sonnet-4-6`, `claude-haiku-4-6`.
- **Impact:** Web Settings model selector cannot show or select the running model. Note the
  *CLI* `hachi models` command already uses live discovery (`cli/main.py:2426`), so the gateway
  endpoint is the lone offender — prefer delegating to the same discovery path.
- **Fix:** Delegate gateway `list_models` to live provider discovery (as the CLI does) or a
  bundled `models.json`. ~21 test files reference these model strings — run the full suite after.

### WB-05 — `create_gateway_app(auth_token=None)` disables auth (CRIT-001, downgraded)
- **Category:** Security · **Severity:** High (defense-in-depth) · **Verdict:** PARTIAL · **Complexity:** Small
- **Root cause:** `_require_auth` short-circuits to a no-op when `auth_token` is falsy
  (`fastapi_app.py:331-336`). Intentional for unit-test construction.
- **Impact:** The served daemon is fail-closed: `_resolve_gateway_auth_token` generates/persists a
  loopback token and raises for non-loopback without a token, and startup guards reject `NO_AUTH=1`
  on non-loopback. So this is a latent contract footgun (default param = no-auth), not a reachable
  network vuln. `test_gateway_auth_default.py:116` codifies the no-auth default as *intended*.
- **Fix:** Make `auth_token` required (or default to a generated ephemeral token) with an explicit
  `allow_no_auth=True` test-only escape hatch, so no future caller silently ships unauthenticated.

### WB-06 — Python sandbox is process-isolation only (CRIT-005)
- **Category:** Security · **Severity:** High · **Verdict:** PARTIAL · **Complexity:** Large (true isolation)
- **Root cause:** `core/sandbox.py` runs LLM-supplied code via `uv run` with a scrubbed env but no
  namespace/seccomp/chroot — full host FS + network.
- **Impact / mitigation landed:** The interim env-gate (E3-T2) is real: `register_sandbox_tools`
  only registers `python_sandbox` when `HACHI_SANDBOX_PRODUCTION=1`, emitting a CRITICAL warning
  (`tools/sandbox_tools.py:99-110`); the tool is reachable only in the `unrestricted` tier. True
  container/seccomp isolation is still absent, and the tool description still implies a "secure
  sandbox."
- **Fix:** Ship real isolation (container/gVisor/seccomp) before un-gating; correct the misleading
  description meanwhile.

### WB-07 — MemoryVault post-`close()` access raises `NoneType.upsert` (HIGH-021 residual)
- **Category:** Reliability · **Severity:** High · **Verdict:** PARTIAL · **Complexity:** Small
- **Root cause:** `close()` nulls collection refs under `_vault_lock` (`memory_vault.py:314-340`).
  Write/read paths acquire the same RLock and then call `collection.upsert(...)` /
  `collection.query(...)` **without re-checking for `None`** (`:411-412`, `:446-447`, `:501`,
  etc.). The RLock prevents *interleaving* corruption but not the *close-then-access* ordering.
- **Impact:** After `close()`, any in-flight or subsequent tool-call write raises
  `'NoneType' object has no attribute 'upsert'`. This is exactly the symptom the suite *skips*
  around (`tests/test_cockpit_gaps.py:104`: "vault write failed: 'nonetype' object has no attribute
  'upsert'") — so the green suite hides it. False-green risk.
- **Fix:** After acquiring the lock, raise a typed `VaultClosedError` (or no-op + warn) when the
  collection is `None`; do not let `AttributeError` escape. Add a regression test that calls
  `close()` then `upsert_fact()` and asserts the typed error, not a skip.

### WB-08 — HachiError hierarchy (MED-028) — FIXED
- **Category:** Tech Debt · **Severity:** Medium · **Verdict:** FIXED · **Complexity:** —
- **Evidence:** `core/errors.py:16-133` defines `HachiError` (code/detail/http_status/context) with
  `OGParseError`, `OGValidationError`, `ConfigError`, `ConfigPermissionError`, `ToolError`,
  `MCPError`, `PermissionDeniedError`. Advisory HTTP-status mapping is present. Real hierarchy, not
  the two-stub placeholder.

### WB-09 — Second `tokoboros` checkout pollutes test collection — NEW
- **Category:** Test Integrity · **Severity:** Medium · **Verdict:** NEW · **Complexity:** Small
- **Root cause:** A parallel `D:\OpenGigantic\tokoboros\` clone (pre-rename repo) is collected into
  the same pytest run — its tests appear in `-rs` skip output (`..\tokoboros\tests\test_daemon.py`,
  `test_presentation_tools.py`, etc.). Memory records tokoboros as hard-renamed and ripped out;
  this stray on-disk clone re-injects it.
- **Impact:** Inflates pass counts with another repo's tests, can mask hachi gaps, and risks import
  shadowing (`No module named 'fastapi'` collection errors seen when run under bare Python 3.14).
  Undermines the "2466 passed" signal.
- **Fix:** Confine collection to `tests/` via `testpaths`/`rootdir`/`norecursedirs` in
  `pyproject.toml`; remove or relocate the sibling checkout from any path pytest walks.

### WB-10 — No test guards the gateway model list (false-green for WB-04) — STILL-OPEN
- **Category:** Test Integrity · **Severity:** Medium · **Verdict:** STILL-OPEN · **Complexity:** Small
- **Root cause:** Model-string assertions live only in config-get/set and pricing tests; no test
  exercises gateway `GET /models` to assert the current model is present.
- **Impact:** WB-04 can regress or persist undetected. Add a gateway test asserting the
  configured/running model id is in the `/models` response.

### WB-11 — Sub-agent model override respected (HIGH-013) — FIXED
- **Category:** Architecture · **Severity:** Medium · **Verdict:** FIXED · **Complexity:** —
- **Evidence:** `sub_agent.py:591-601` — when `model` is explicit, builds a fresh
  `_create_client(model)` even if `llm_client` is injected; else uses the injected client; else
  default haiku. `delegation_tools.py:106-113` exposes a `model` schema field; `:549-561` passes
  `model=model or None` through to `SubAgentRuntime`. Logged at INFO. End-to-end, not cosmetic.

### WB-12 — Orchestrator silent except-pass now logged (HIGH-012/E4-T7) — FIXED
- **Category:** Reliability · **Severity:** Medium · **Verdict:** FIXED · **Complexity:** —
- **Evidence:** ~15 `except Exception:` blocks in `orchestrator.py` now carry real
  `logger.warning(..., exc_info=True)` bodies (e.g. `:758-765` collection enumeration, `:780-784`
  quarantine, `:895` vault recall, `:933` prefetch, `:970` queue enqueue, `:996` title persist,
  `:1157` session log, `:1173` cost persist, `:1219` episode write, `:1267` Chroma close). Errors
  are still swallowed to preserve partial function but are now observable. (Two minor exceptions:
  WB-15 below and the `list_models` fallback at `:612` return `[]`/`debug` only.)

### WB-13 — TurnContext.surface threaded (MED-012) — FIXED
- **Category:** Architecture · **Severity:** Low · **Verdict:** FIXED · **Complexity:** —
- **Evidence:** `turn_context.py:61` default + `:77,134` param; `orchestrator.chat(surface="cli")`
  at `:1049` flows to `TurnContext.build(surface=surface)` at `:1133`; gateway provides surface via
  `SessionCreateRequest.surface` → `control_plane` (`fastapi_app.py:78,433`;
  `control_plane.py:181-202`). Audit trail no longer hardcoded `'cli'` for gateway sessions.

### WB-14 — JobQueue persistent conn + BM25 bisect index (E5-T2, HIGH-014) — FIXED
- **Category:** Performance · **Severity:** Low · **Verdict:** FIXED · **Complexity:** —
- **Evidence:** `jobs/queue.py:557-591` holds one persistent `sqlite3` connection with a re-entrant
  `_conn()` context manager (commit/rollback), replacing per-call connect/close churn.
  `og_engine/engine.py:20` imports `bisect`; `:371` `_sorted_tokens`; `:426-434`
  `_rebuild_sorted_tokens` for O(log V) prefix search. Matches the BE-5 #129 description.

### WB-15 — `list_skill_models` swallows errors to `[]` silently — NEW (minor)
- **Category:** Reliability · **Severity:** Low · **Verdict:** NEW · **Complexity:** Trivial
- **Root cause:** `orchestrator.py:610-613` returns `[]` on any exception from the client's
  `list_models()` with no log — inconsistent with the HIGH-012 pass elsewhere in the file.
- **Impact:** A broken provider-discovery call silently yields an empty model list in the UI with no
  diagnostic. Add `logger.warning(..., exc_info=True)` before the `return []`.

---

## 4. Verification scorecard (assigned items)

| Prior finding | Asked to verify | Verdict | Note |
|---|---|---|---|
| MED-028 HachiError hierarchy (#125) | landed, not stub | **FIXED** | WB-08 |
| HIGH-013 sub-agent model override (#127) | respected end-to-end | **FIXED** | WB-11 |
| HIGH-012/E4-T7 orchestrator except logging (#127) | real logs, not comments | **FIXED** | WB-12 |
| E2-T1/T2/T3 config/gateway tests with real types | not mocked namespaces | **FIXED** | tests 25-29 use real `ToolRegistry.execute` + `ResolvedConfig`; `test_gateway_auth_default.py` uses real `create_gateway_app` |
| MED-012 TurnContext.surface | not hardcoded `'cli'` | **FIXED** | WB-13 |
| CRIT-006 session ownership (BE-1) | undone on main? | **STILL-OPEN** | WB-01 |
| CRIT-007 resume threading (BE-1) | undone on main? | **STILL-OPEN** | WB-02 |
| CRIT-008 `hachi serve` (BE-1) | undone on main? | **STILL-OPEN** | WB-03 |
| HIGH-008 dynamic model list (BE-1) | undone on main? | **STILL-OPEN** | WB-04 + WB-10 |

The merged PRs validated here are genuine (real code paths exercised). The four BE-1 items are
confirmed absent from main, as expected.

---

## 5. Test-suite integrity notes (false-green watch)

- **Green but masking:** `test_cockpit_gaps.py:104` *skips* on the very `NoneType.upsert` vault
  race that should be a failing regression (WB-07).
- **Collection pollution:** the sibling `tokoboros` checkout adds foreign tests to the run (WB-09);
  the "2466 passed" figure includes them. Pin `testpaths` to isolate hachi.
- **Coverage gap:** no gateway `/models` assertion (WB-10) leaves WB-04 unguarded.
- **Positive:** config-tool and gateway-auth suites now use real types/app instances — the
  2026-06-18 v2 "mock the wrong type" failure mode is not present in the files I inspected.
