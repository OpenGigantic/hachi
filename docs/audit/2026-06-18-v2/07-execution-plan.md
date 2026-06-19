# Hachi — Execution Plan (Audit v2)

**Date:** 2026-06-18 · **Owner:** Engineering Program Manager
**Inputs (sources of truth):** [00 — Master Report](00-MASTER-REPORT.md) (§6 Prioritized Fix List, §5 Risk Matrix) · [06 — PO Decisions](06-product-owner-decisions.md) (NOW / BEFORE-WEB / NEXT / BACKLOG / RISK-ACCEPT / REJECT) · sub-reports [01-black-box](01-black-box.md) · [02-white-box](02-white-box.md) · [03-ux-product](03-ux-product.md) · [04-security-reliability](04-security-reliability.md) · [05-performance-scalability](05-performance-scalability.md).
**Gates:** [09 — Release Readiness](09-release-readiness.md) (Gate A engine/CLI; Gate B web; Gate C scale).

This plan is written so an engineering agent can pick up any task and implement it **with no further clarification**. Findings are referenced by ID; evidence lives in the sub-reports at the cited `file:line`.

---

## 1. Overview

### Mapping to sprints / P0–P3
The plan is organized into **12 epics** (E1–E12) decomposed into **~60 tasks**. Every task is tagged to a sprint that mirrors the Master Report §6 buckets and the PO dispositions in 06:

| Sprint | Bucket | PO disposition | Gate it unlocks |
|---|---|---|---|
| **Sprint 1** | P0 — critical blockers | `NOW` | Gate A (engine/CLI release) |
| **Sprint 2** | P1 — high | `BEFORE-WEB` | Gate B (web beta) |
| **Sprint 3** | P2 — medium | `NEXT` | Gate C (partial) |
| **Sprint 4** | P3 — low / enhancements | `BACKLOG` | Gate C / GA polish |

### The one hard rule (overriding decision from 06 §0)
> **Nothing is "done" without a test that reproduces the original failure and runs green in CI** — and CI must itself be honest (see E1). This audit exists because a prior remediation was reported done and was not, and the tooling that should have caught it (CI, a unit test, frontend tests) was broken or fabricated. Every task below has an acceptance criterion of the form *"a test reproduces the original failure"*; a fix without that test is not accepted, regardless of green checkmarks.

**Corollary — sequencing:** **T1.1 (CI green-for-real) and T1.2 (kill the false-green config test) land FIRST.** Until "green" means something, no other fix is verifiable and parallel work cannot be trusted. See §4 critical path and §6 collision notes.

### Scope guardrails (from 06 §3 REJECT)
- **No backend rewrite / re-architecture.** Every critical is "wire it / delete the fake / fix the gate," not a redesign. The engine is the strongest asset (78/100).
- **Do not action `docs/strategy/01-architecture-review.md`** — confirmed stale; mark it stale instead (T12.10).
- **No native desktop/mobile apps** in this program (founder-deferred).
- **Do not ship the web UI before Gate B.**

---

## 2. Epic Structure

| Epic | Title | Tasks | Primary lane | Sprint span |
|---|---|---|---|---|
| **E1** | Verification Integrity | T1.1–T1.5 (5) | Infra/CI | S1 (T1.1/1.2), S3 (T1.4) |
| **E2** | CLI Config & Settings Correctness | T2.1–T2.4 (4) | Backend-engine | S1, S3 (T2.4) |
| **E3** | Pack Supply-Chain Security | T3.1–T3.3 (3) | Security | S1 (T3.1/3.2), S2 (T3.3) |
| **E4** | Stop Faking Data | T4.1–T4.7 (7) | Frontend-SPA (+1 engine) | S1, S2 |
| **E5** | The Leash, End-to-End | T5.1–T5.4 (4) | Frontend-SPA + Gateway | S2 |
| **E6** | Governance Chrome & Spend | T6.1–T6.5 (5) | Frontend-SPA | S1 (T6.1/6.2), S2 |
| **E7** | Cost & Frontend Performance | T7.1–T7.4 (4) | Backend-engine + Frontend-SPA | S1 |
| **E8** | Gateway Perf & Reliability | T8.1–T8.8 (8) | Gateway | S2 |
| **E9** | Network / Egress Security | T9.1–T9.4 (4) | Security | S2 |
| **E10** | Accessibility | T10.1–T10.4 (4) | Frontend-SPA | S2 |
| **E11** | Distribution & Supply-Chain Hygiene | T11.1–T11.5 (5) | Infra/CI | S2 |
| **E12** | Tech-Debt & Polish | T12.1–T12.11 (11) | Mixed | S3, S4 |

**Total: 62 tasks across 12 epics.** Effort key: **S** = <0.5d · **M** = 0.5–2d · **L** = 2–5d. Risk = chance the change breaks something / is subtle.

---

## 3. Per-Task Specifications

> Every task carries: **ID + objective · Findings · Acceptance criteria · Files (with line refs) · Effort · Risk · Dependencies.**

### E1 — Verification Integrity  *(Sprint 1 lead; the gate for everything else)*

#### T1.1 — Make `frontend-contract` CI point at the real SPA and go green-for-real
- **Findings:** WB-001
- **Acceptance:**
  - The `frontend-contract` job no longer runs `check:api`/`lint` against scripts/files that don't exist; it executes only steps that resolve against the shipped SPA.
  - Recommended (Master §7 Option A): add `openapi-typescript` to `frontend/devDependencies`; add `generate:api` (`openapi-typescript ../openapi/hachi-openapi.json -o src/lib/data/api-types.ts`) and `check:api` (`generate:api && git diff --exit-code`); job runs `Export OpenAPI` → `check:api` → `typecheck`.
  - If Option A is too large to land same-day, ship Option B stop-gap: job runs only `npm run --prefix frontend typecheck` (which exists) — but file a follow-up to reach Option A (becomes T11.5 link).
  - CI is **green on a clean push** to a throwaway branch (proven, not assumed).
- **Files:** `.github/workflows/ci.yml:130-173`; `frontend/package.json:6-11`; (Option A) new `frontend/src/lib/data/api-types.ts`; `CLAUDE.md` generate:api instructions.
- **Effort:** M · **Risk:** Med (CI plumbing; `frontend/` is uncommitted — see §6) · **Deps:** none. **CRITICAL PATH — do first.**

#### T1.2 — Delete/repair the false-green config test; test the *real* command
- **Findings:** WB-N07 (root cause that let BB-001/002 survive)
- **Acceptance:**
  - `tests/test_config_get_set.py` no longer mocks a flat `SimpleNamespace(safety_tier=…, max_tokens=…, session_cost_cap_usd=…)` that the real `ResolvedConfig` lacks.
  - The test invokes the actual `config-get`/`config-set` CLI commands (via Typer `CliRunner`) against a **real on-disk settings file in a tmp dir** — no patch of `load_effective_config`.
  - The test **fails against today's code** (reproduces the crash) and passes only once T2.1 lands. Commit the failing test first (red), then T2.1 turns it green.
- **Files:** `tests/test_config_get_set.py:20-34` (the `_fake_cfg`/patch); add a real-command test. Cross-check `tests/test_gateway_config_mutations.py` (the runtime mutators it *does* cover correctly — leave intact).
- **Effort:** S · **Risk:** Low · **Deps:** none (pairs with T2.1). **CRITICAL PATH.**

#### T1.3 — Make strict ruff + mypy blocking on `main`
- **Findings:** WB-002
- **Acceptance:** the `lint`/type-check gate is no longer `continue-on-error` on `main`; strict ruff + mypy run blocking on shipped modules. The 36-file `mypy-strict-gate.ini` subset is expanded toward full `src/` coverage (or a tracked, shrinking allowlist with a deadline).
- **Files:** `.github/workflows/ci.yml:78-84` (`continue-on-error`); `mypy-strict-gate.ini`.
- **Effort:** M · **Risk:** Med (may surface latent type errors that must be fixed to go green) · **Deps:** none (independent of T1.1/T1.2).

#### T1.4 — Frontend test foundation (vitest + testing-library + gateway smoke)
- **Findings:** WB-024, and Gate B G-B12
- **Acceptance:** `frontend/` has `vitest` + `@testing-library/react`; at least the trust-critical units have tests (spend cumulative-vs-add T6.2, leash POST T5.1, fixture-fallback-removed T4.1); a `*.test.*` run is wired into the `frontend-contract` CI job; a gateway-integration smoke (SPA hits a live/mocked gateway) exists per [08](08-verification-strategy.md).
- **Files:** `frontend/package.json`; new `frontend/src/**/*.test.tsx`; `.github/workflows/ci.yml`.
- **Effort:** L · **Risk:** Low · **Deps:** T1.1 (CI job must be real first). **Sprint 3** per 06 (NEXT), but the *individual* trust-fix tests (T4.x/T5.x/T6.x) ship in their own sprints; this task is the shared harness.

#### T1.5 — SQLite job-durability boundary test (regression scaffold for WB-019)
- **Findings:** WB-019 (test half; fix is T12.1)
- **Acceptance:** a test asserts func-only jobs do **not** survive a restart today (documents the gap), and is upgraded to assert they *do* survive once T12.1 lands.
- **Files:** new `tests/test_job_durability.py`; `src/hachi/jobs/queue.py:591-603`.
- **Effort:** S · **Risk:** Low · **Deps:** none. **Sprint 3** (pairs with T12.1).

---

### E2 — CLI Config & Settings Correctness  *(Sprint 1)*

#### T2.1 — Fix `config-get`/`config-set` to read/write the real config shape
- **Findings:** BB-001, BB-002, WB-N06
- **Acceptance:**
  - `hachi config-get` (no arg, and `provider.model`, and `safety_tier`) returns **without traceback**.
  - `config-set safety_tier guided`, `config-set max_tokens 4096` persist to the real paths (`safety_tier`→`cfg.permissions.tier`, `max_tokens`→`cfg.agent.max_tokens`) and round-trip via `config-get`.
  - The flat dict no longer eagerly reads non-existent attributes; key lookups are lazy/mapped.
  - `config_set`'s `except` also catches `AttributeError` (defensive).
  - **T1.2's real-command test passes.** `config-validate` continues to work (positive control).
- **Files:** `src/hachi/cli/main.py:2205-2213` (flat dict, crash at `:2209`), `:2247-2272` (set: `:2258/2260/2262`); `src/hachi/core/settings.py:85-97` (`ResolvedConfig` → `permissions.tier`, `agent.max_tokens`).
- **Effort:** S · **Risk:** Med (the wrong-attr pattern is the exact thing that masqueraded as fixed twice — verify by *running* the command, not by re-reading the mock) · **Deps:** T1.2 (test exists red first). **Pairs with T2.2.**

#### T2.2 — Model a real `session_cost_cap_usd` field
- **Findings:** BB-002, WB-N06, and the product gap (anti-bill-shock #1)
- **Acceptance:**
  - `ResolvedConfig` gains a real cost-cap field (e.g. `agent.session_cost_cap_usd` or a dedicated budget model), persisted in `settings.toml`, defaulting safely; or map to the existing budget field / `HACHI_MAX_SESSION_COST_USD`.
  - `config-get session_cost_cap_usd` and `config-set session_cost_cap_usd 5` work and round-trip.
  - The field is the one the web cap UI (T6.4) and enforcement read — choose the name once, here, and document it so T6.4 binds to it.
  - A test sets the cap via CLI and asserts it is read back and (if enforcement lands) honored.
- **Files:** `src/hachi/core/settings.py:85-97`; `src/hachi/cli/main.py:2209-2211,2262`; the budget/cost path in `src/hachi/core/cost.py` (enforcement hook).
- **Effort:** M · **Risk:** Med (new persisted field; must not break existing config load) · **Deps:** T2.1. **Blocks T6.4** (web cap UI needs the field).

#### T2.3 — Fix Windows-hostile `secrets --set` guidance and onboarding `export` line
- **Findings:** BB-033, WB-025
- **Acceptance:**
  - `core/onboarding.py:310` no longer emits a Unix-only `export {env}=sk-...` as the primary path on Windows; emits a platform-correct instruction.
  - The banner's `hachi secrets --set {env}=<your-key>` is reconciled with the actual `--set` behavior (takes a bare KEY then prompts) — either fix the banner copy or make `--set KEY=VALUE` parse; and `--set` must not be suggested in the non-TTY branch where it hard-exits (`main.py:1338` vs `:2465-2467`).
  - A test covers the non-TTY path producing actionable guidance (not an abort with a suggestion that aborts).
- **Files:** `src/hachi/core/onboarding.py:310`; `src/hachi/cli/main.py:1332,1338,2462-2474`.
- **Effort:** S · **Risk:** Low · **Deps:** none. *(P2-adjacent but cheap; lands S1 opportunistically or S2.)*

#### T2.4 — Central typed Settings model (stretch; freeze monolith growth)
- **Findings:** WB-016, WB-017 (110 raw `os.environ` reads, no typed Settings)
- **Acceptance:** a single typed Settings object centralizes env reads; new code reads through it; `cli/main.py` does not grow further (RISK-ACCEPT'd in 06 §2 — freeze, don't necessarily shrink). Incremental: introduce the model + migrate the highest-traffic reads.
- **Files:** `src/hachi/core/settings.py`; `src/hachi/cli/main.py` (2,886 LOC — top env reads `:27`).
- **Effort:** L · **Risk:** Med · **Deps:** T2.1/T2.2. **Sprint 3** (NEXT).

---

### E3 — Pack Supply-Chain Security  *(Sprint 1 P0; T3.3 → Sprint 2)*

#### T3.1 — Close ZIP zip-slip + add member/size caps (tar/zip parity)
- **Findings:** SEC-001, REL-010
- **Acceptance:**
  - `_extract_zip` validates each member's resolved path is inside `dest` (reject `..`/absolute/escape), mirroring the hardened tar path.
  - Per-member size cap + cumulative-size cap + member-count cap (decompression-bomb guard).
  - A test extracts a crafted ZIP with a `../escape.og` member and asserts it is rejected (write stays inside `dest`); a test with an oversized/over-count archive is rejected.
- **Files:** `src/hachi/core/community_packs.py:243-264` (`_extract_zip`); tar reference `:188-240` (already hardened — match it).
- **Effort:** S · **Risk:** Med (security-critical; get the path-resolution check exactly right) · **Deps:** none. **CRITICAL — gates pack feature.**

#### T3.2 — Tier-gate + explicit-confirm MCP server install/exec at boot
- **Findings:** SEC-005
- **Acceptance:**
  - A `role=mcp` node from a (web-installable) pack is **not** auto-started at boot without a tier check + explicit user confirmation; only the built-in `filesystem_mcp`-style guarded path may auto-start.
  - The command/args/env are gated by permission tier (no arbitrary `subprocess.Popen` of community-supplied commands at boot).
  - A test asserts an untrusted `role=mcp` node does not spawn a process without consent/tier; an allowed one does.
- **Files:** `src/hachi/core/mcp_client.py:447-498` (`setup_from_node`), `:226-248` (`_start_inner`→`Popen`); `src/hachi/gateway/orchestrator.py:1239-1260` (starts every `role=mcp`).
- **Effort:** M · **Risk:** High (changes boot behavior; must not break the legitimate built-in MCP path) · **Deps:** none. **CRITICAL — gates pack feature.**

#### T3.3 — Enforce hash/signature on pack install
- **Findings:** SEC-007
- **Acceptance:** `install_pack` calls the existing verifier; install fails on hash/signature mismatch; the verifier no longer returns `True` on empty hash; an `--unsafe` opt-out is the only bypass and is loud. A test asserts an unsigned/tampered pack is rejected unless `--unsafe`.
- **Files:** `src/hachi/core/community_packs.py:299-389` (`install_pack`); `src/hachi/core/pack_index.py:217-225` (`verify_index_entry_sha256`, currently unused + empty-hash-True).
- **Effort:** M · **Risk:** Med · **Deps:** T3.1 (extraction hardening should land first). **Sprint 2** (BEFORE-WEB).

---

### E4 — Stop Faking Data  *(Sprint 1 P0 for T4.1/T4.2/T4.3; Sprint 2 for the rest)*

#### T4.1 — Remove silent fixture fallback; add honest loading/error/empty/stale states
- **Findings:** UX-018, BB-004, BB-014
- **Acceptance:**
  - No domain loader silently leaves seeded fixtures rendered on gateway failure. Each route shows a real **loading**, **error (with retry)**, **empty**, or **stale/"showing sample data — couldn't reach Hachi"** state — fabricated healthy data is never shown as if live.
  - `__root.tsx:17`'s "degrades to seeded fixtures … never blanks the UI" behavior is removed/replaced.
  - A test (vitest) asserts that with the gateway mocked-down, a data route renders an error/stale affordance, **not** populated fixtures.
- **Files:** `frontend/src/routes/__root.tsx:17`; `frontend/src/lib/data/domains/work.ts:201-203` and the 9 sibling loaders (`memory.ts`, `og.ts`, `skills.ts`, `integrations.ts`, `config.ts`, `authoring.ts`, `approvals.ts`, `chat.ts`, `spend.ts`).
- **Effort:** L · **Risk:** Med (touches every data route; coordinate with E5/E6 which also edit `chat.ts`/`approvals.ts` — see §6 lane notes) · **Deps:** T1.4 (test harness). **CRITICAL trust item.**

#### T4.2 — Real API-key validation in the SetupWizard
- **Findings:** UX-001, BB-006
- **Acceptance:** Step 1 calls the existing `validateProvider()` (`POST /config/provider/validate`); the "Key looks good — validated" badge shows **only** on a real 200; the length-only `keyValid = length>=12` gate is removed/relegated to a pre-check. A test asserts a bogus key surfaces a failure, not a green check.
- **Files:** `frontend/src/components/.../SetupWizard.tsx:51,236-240`; validator already exists at `frontend/src/lib/data/domains/config.ts:174-191`.
- **Effort:** S · **Risk:** Low · **Deps:** T1.4. **CRITICAL trust item.**

#### T4.3 — Fix web spend double-count (set cumulative, don't add)
- **Findings:** UX-N01, WB-009 (drift D-2)
- **Acceptance:** the `usage.updated` handler treats the payload as **cumulative** (track a per-turn baseline like the CLI does at `cli/main.py:750-753`) and **sets** `taskSpent`/`spent` to the latest cumulative value rather than `+=`. A unit test feeds cumulative `[0.01, 0.03, 0.06]` and asserts the displayed total is `0.06`, not `0.10`. Pairs with T6.1 (status bar surfaces the number).
- **Files:** `frontend/src/lib/data/domains/chat.ts:227-244` (esp. `:236-239`); backend reference `src/hachi/core/loop.py:757-760`, `src/hachi/cli/main.py:720,750-753` (backend `/spend` is already correct — do not change it).
- **Effort:** S · **Risk:** Low · **Deps:** T1.4. **CRITICAL trust item; pairs with T6.1.**

#### T4.4 — Wire memory add/edit/delete + proposed-change approve/reject to `/memory/facts`
- **Findings:** BB-N04, UX-010, drift D-4
- **Acceptance:**
  - `saveFact`/`delFact`/`approveProposed`/`rejectProposed` call the **existing** `POST /memory/facts` and `DELETE /memory/facts/{id}` instead of toasting local-only success; edits survive a reload.
  - The stale `memory.ts:8-11` "no write endpoints" comment is removed.
  - Destructive delete gets a confirm/undo (UX-010).
  - A test asserts a save issues a POST and the fact persists across a reload; a failed write surfaces an error (not a fake "Hachi remembers it").
- **Files:** `frontend/src/lib/data/domains/memory.ts:8-11`; `frontend/src/routes/memory.tsx:120,123,592`; backend already real at `src/hachi/gateway/fastapi_app.py:840-855`. **Depends on T8.3** (wrong-session routing) for correctness under multi-session.
- **Effort:** M · **Risk:** Med · **Deps:** T1.4; coordinate with T8.3. **Sprint 2** (BEFORE-WEB).

#### T4.5 — Real `/run` SSE stream, or remove the screen
- **Findings:** UX-017, BB-005
- **Acceptance:** `/run` either consumes the real `GET /sessions/{id}/stream` SSE (real terminal lines, real spend meter, real leash prompts) **or** the screen is removed and the "Watch live" entry hidden. No screen labeled "live" runs the scripted `setInterval` simulation. A test asserts no `_runQueue`/`runTick` simulation path is reachable in live mode.
- **Files:** `frontend/src/routes/run.tsx:7-12,107-111`; `frontend/src/lib/data/store.ts:1299-1312` (`_runQueue`, `runTick`); entry at `work.tsx:933`.
- **Effort:** L (if wired) / S (if removed) · **Risk:** Med · **Deps:** T1.4; SSE infra shared with T5.x. **Sprint 2.**

#### T4.6 — Finish or hide authoring "Generate" + "Browse the kennel" + operator skill-edit
- **Findings:** UX-014, BB-010
- **Acceptance:** the inert "Generate" button (no onClick) either produces a real patch or is hidden; the hardcoded "+3 −2 lines" summary and fabricated "re-indexed in 0.3s" line are removed/made real; "Browse the kennel" and operator skill-edit are wired (deep-link to `/authoring`) or hidden. **Note the gateway apply/revert is a documented no-op stub** — see T4.7: don't let the UI assert success over a stub. A test asserts no dead button claims success.
- **Files:** `frontend/src/routes/authoring.tsx:455-478,528,626`; `frontend/src/routes/skills.tsx:40,202`.
- **Effort:** M · **Risk:** Low · **Deps:** T4.7 (backend stub must stop asserting success first). **Sprint 2.**

#### T4.7 — Make authoring apply/revert honest (501 until pipeline exists) — *engine task*
- **Findings:** WB-008, WB-N04
- **Acceptance:** `authoring_apply`/`authoring_revert` return `501 Not Implemented` (or `{"applied": false, "reason": "not implemented"}`) instead of `{"ok": True, "reindexed_ms": 0}` while there is no patch pipeline; `get_authoring_targets` stops always emitting `dirty: False` as if a diff exists. The OpenAPI description matches. A test asserts apply returns the not-implemented contract. (When the pipeline lands later, writes must be `PermissionGate`-scoped; path guard already exists at `cli/main.py:651-686`.)
- **Files:** `src/hachi/cli/main.py:694-714` (no-op stubs), `:623,633,644` (`dirty:False`); `src/hachi/gateway/control_plane.py:500-508`; regenerate `openapi/hachi-openapi.json`.
- **Effort:** S · **Risk:** Low · **Deps:** none. **Sprint 2** (pairs with T4.6).

---

### E5 — The Leash, End-to-End  *(Sprint 2 — BEFORE-WEB; lead candidate to pull into P0 per 06)*

#### T5.1 — Wire in-context leash allow/deny to real backend approvals
- **Findings:** BB-N02, WB-007, UX-006, drift D-1
- **Acceptance:**
  - `allowLeashLive()`/`denyLeashLive()` POST to `POST /approvals/{request_id}` with the correct payload (`{action, rationale}`) instead of optimistically clearing the UI. The standalone `/approvals` screen already does this — reuse the pattern (`approvals.ts:185-233`).
  - The chat adapter reads the **correct** `permission.requested` keys (`request_id/operation/tool/target/reason/policy_summary`) — today it reads `payload.request/action/why` which are always `undefined`, discarding the `request_id` needed to resolve the approval (drift D-1).
  - "Always allow" requires a confirm/undo; Decline collects a reason and passes it.
  - A test asserts an in-context Allow issues the POST with the real `request_id`, and a Decline passes the reason.
- **Files:** `frontend/src/lib/data/domains/chat.ts:216-225,395-414`; reference `frontend/src/lib/data/domains/approvals.ts:185-233`; backend emits keys at `src/hachi/core/runtime_events.py:259`, `src/hachi/core/approvals.py:328-338`, `src/hachi/core/permissions.py:815-825`. Confirm UI in `approvals.tsx:322-339`, `LeashPrompt.tsx:245-271`.
- **Effort:** M · **Risk:** Med (the payload-key mismatch is the mechanical root cause — fix the keys, then the POST) · **Deps:** T1.4; coordinate with T4.1 (both edit `chat.ts`). **The leash is the product — highest-trust P1.**

#### T5.2 — Real auto-decline countdown (remove fabricated 0/0)
- **Findings:** BB-N03, UX-007
- **Acceptance:** the countdown is driven by a real server-sent expiry (or the "auto-declines in {time}" bar is removed until the server emits one). Today `approvals.ts:117-118` hardcodes `expiresAt: 0, totalMs: 0` while the UI always renders a counting-down promise (`0s`, NaN%). Either thread a real `expiresAt`/`totalMs` from the gateway, or hide the bar when absent. The in-context `LeashPrompt` `countdown` prop must actually be passed (today `grep countdown=` → 0). A test asserts: no fabricated countdown renders on live data lacking an expiry.
- **Files:** `frontend/src/lib/data/domains/approvals.ts:117-118`; `frontend/src/routes/approvals.tsx:242-280`; `conversation.tsx:761-768`, `run.tsx:580-588`; `LeashPrompt.tsx:24,174-186`. If a server expiry is added: `src/hachi/core/approvals.py` (emit expiry in the `permission.requested` payload).
- **Effort:** M · **Risk:** Med · **Deps:** T5.1. **Fold into the leash work.**

#### T5.3 — Approval wait off the event loop / thread pool (fail-fast + cap)
- **Findings:** REL-001, REL-N03
- **Acceptance:** the synchronous 180s approval wait no longer parks an anyio worker thread for the full timeout (async wait or fast-return-to-pending), and pending approvals are capped so a flood can't exhaust the pool or grow the registry unbounded. A test simulates N concurrent approval-triggering turns and asserts `/health` stays responsive (no thread-pool starvation).
- **Files:** `src/hachi/core/approvals.py:293-307,309-357,89-98` (no cap on `create`); invoked via `src/hachi/gateway/orchestrator.py:1365` inside `anyio.to_thread.run_sync` (`fastapi_app.py:579,629-637,741`).
- **Effort:** M · **Risk:** High (concurrency change on the approval path — the safety-critical gate; must not drop approvals) · **Deps:** none. *(Gateway lane; pairs conceptually with E5 but lives in backend approval code.)*

#### T5.4 — Fix the leash payload contract in OpenAPI (type the SSE permission event)
- **Findings:** drift D-1 structural (02 §3.2), supports T5.1
- **Acceptance:** the `permission.requested` SSE payload is typed in `openapi/hachi-openapi.json` (not opaque `additionalProperties`) so the FE binds to a real contract and drift fails CI (via T1.1 Option A). A contract test asserts the payload carries `request_id` and the consumer reads it.
- **Files:** `src/hachi/gateway/fastapi_app.py` (SSE envelope `GatewayEventPayload`); `openapi/hachi-openapi.json`; regenerate.
- **Effort:** S · **Risk:** Low · **Deps:** T5.1, T1.1. *(Optional polish within Sprint 2; can defer to S3.)*

---

### E6 — Governance Chrome & Spend  *(Sprint 1 for T6.1/T6.2; Sprint 2 for caps/provider/nav)*

#### T6.1 — Add the governance status bar (model / session / live-cost / connection)
- **Findings:** UX-009, UX-025, UX-N02
- **Acceptance:** a persistent top strip on **every** route shows: active model, session id, live tokens + cost, and a connection pill. The live token count (currently parsed then thrown away at `chat.ts:228-242`) is surfaced. The mascot/eye can host a compact ambient state (UX-025). A test asserts the bar renders on multiple routes and reflects connection state.
- **Files:** `frontend/src/routes/__root.tsx:22-49` (renders only Sidebar+Outlet today); `frontend/src/lib/data/domains/chat.ts:228-242` (`liveTokens` field); `Sidebar.tsx`/mascot.
- **Effort:** M · **Risk:** Low · **Deps:** T4.3 (spend must be correct before it's displayed). **CRITICAL UX chrome; fix together with T4.3 per 06.**

#### T6.2 — Wire the live-cost readout to the corrected spend
- **Findings:** UX-N01, UX-N02 (display side of T4.3)
- **Acceptance:** the status bar's cost + token readout reads the corrected cumulative spend store field; reconciles with the backend `/spend` aggregate. A test asserts the displayed live cost equals the cumulative value, not the summed one.
- **Files:** `frontend/src/lib/data/domains/chat.ts:227-244`; the status-bar component (T6.1).
- **Effort:** S · **Risk:** Low · **Deps:** T4.3, T6.1. **Sprint 1.**

#### T6.3 — Promote Spend to a first-class nav item
- **Findings:** UX-011
- **Acceptance:** Spend is a left-nav item (not a tab buried inside `/work`); the sidebar budget widget deep-links to it. A test asserts the nav item exists and routes to the Spend view.
- **Files:** `frontend/src/components/.../Sidebar.tsx:18-29,60-63`; `frontend/src/routes/work.tsx:834`.
- **Effort:** S · **Risk:** Low · **Deps:** T4.3 (don't promote a wrong number). **Sprint 2.**

#### T6.4 — Make the spend cap settable + enforced from the UI
- **Findings:** UX-008, UX-002 (cap half)
- **Acceptance:** the read-only `$1.00` cap becomes an editable field with a Save button that persists (via a config write to the `session_cost_cap_usd` field from T2.2); the orphan permanent "unsaved changes" badge is resolved (cleared on save). The cap is enforced (engine reads the field). A test asserts setting a cap persists and that exceeding it triggers enforcement.
- **Files:** `frontend/src/routes/settings.tsx:540,64,153-157`; backend field from **T2.2**; enforcement in `src/hachi/core/cost.py` / loop budget path.
- **Effort:** M · **Risk:** Med · **Deps:** **T2.2** (field must exist), T1.4. **Sprint 2 — #1 anti-bill-shock gap.**

#### T6.5 — Provider choice at setup (incl. Ollama / OpenAI-compatible / OpenRouter)
- **Findings:** UX-002 (provider half), BB-018, UX-021 (tier-name drift)
- **Acceptance:** the wizard offers a provider picker (the engine already supports 7 kinds: anthropic, openai-compatible, openrouter, groq, together, deepseek, ollama) instead of hard-coding Anthropic; `setup.ts` stops forcing `anthropic`. As part of this, unify the permission-tier vocabulary across CLI/web/docs (README "lightning/guided/strict" vs web "guarded/standard/open" vs gateway "sandbox/guided/unrestricted"). A test asserts a non-Anthropic provider selection flows through setup.
- **Files:** `frontend/src/components/.../SetupWizard.tsx:209-212`; `frontend/src/lib/data/domains/setup.ts:88-91,68-74`; engine kinds `src/hachi/core/settings.py:8-10`; tier names in README + `setup.ts:68-74`.
- **Effort:** M · **Risk:** Med · **Deps:** T1.4. **Sprint 2.**

---

### E7 — Cost & Frontend Performance  *(Sprint 1 P0)*

#### T7.1 — Cache the message-history prefix; use all 4 Anthropic cache breakpoints
- **Findings:** PERF-001 (the moat/cost regression), PERF-002
- **Acceptance:** loaded skills + tool results in `messages` are no longer re-billed at full input price every turn; `cache_control` breakpoints are applied so the history prefix is cached (use all 4 available breakpoints, not just system-prefix + last-tool). Verify via the API `usage` cache-read/write counters (already read at `llm.py:273-279`). A test asserts cache breakpoints are present on the message prefix and that a second turn reports cache reads. *(See `claude-api` skill / `ObjectGraph` docs for breakpoint mechanics.)*
- **Files:** `src/hachi/core/llm.py:247-260` (only system `:249` + last tool `:258` cached today); `src/hachi/core/loop.py:704-706` (sends `self.memory.history` raw; rebuilds schemas per iter).
- **Effort:** M · **Risk:** Med (cache-breakpoint placement is subtle; group-unlock can invalidate the prefix — verify deltas don't bust cache) · **Deps:** none. **CRITICAL — restores the cost claim / Gate A G-A7.**

#### T7.2 — Virtualize the chat transcript
- **Findings:** PERF-025
- **Acceptance:** the transcript is windowed/virtualized so long sessions don't lock the UI; the whole `messages` array is not remapped to a new ref on every `text.stream.delta`. A test (or perf assertion) confirms render work is bounded as transcript length grows.
- **Files:** `frontend/src/routes/conversation.tsx:746-754` (`messages.map`, no virtualization), `:570` (`s.messages` whole-array selector); `frontend/src/lib/data/domains/chat.ts:134-146` (`appendTextDelta` new array per delta); add a virtualization lib to `frontend/package.json`.
- **Effort:** M · **Risk:** Med (virtualization can break scroll/auto-stick behavior — test streaming) · **Deps:** T1.4. **CRITICAL — primary interaction surface.**

#### T7.3 — Throttle SSE deltas + memoize transcript items
- **Findings:** PERF-025 (throttle), PERF-026
- **Acceptance:** SSE text deltas are coalesced/throttled (e.g. rAF/batched) so React doesn't re-render per token; `Markdown` is `React.memo` + `useMemo(parseBlocks)`; `MessageItem` is memoized. A test asserts `Markdown`/items don't re-parse/re-render for unrelated deltas.
- **Files:** `frontend/src/lib/data/domains/chat.ts:134-146`; `frontend/src/components/ui/Markdown.tsx` (no memo today); `conversation.tsx:464` (`MessageItem` plain function).
- **Effort:** S · **Risk:** Low · **Deps:** T7.2 (same files). **Sprint 1 (pairs with T7.2).**

#### T7.4 — Finish CLI lazy-import (defer `anthropic` + `uvicorn`)
- **Findings:** PERF-022
- **Acceptance:** `import hachi.cli.main` and `hachi --help` no longer eagerly import `anthropic` (~3s) and `uvicorn` (~0.6s); they resolve lazily at call time. `--help` measured time drops materially from ~4.6s (chroma/textual/fastapi/onnx are already lazy). A test/CI check asserts `anthropic`/`uvicorn` are **not** in `sys.modules` after `import hachi.cli.main`.
- **Files:** import chain `schedule→orchestrator→cost→llm→anthropic` and `control_plane→uvicorn` (`src/hachi/cli/main.py:27` top; `core/llm.py`, `core/cost.py`, `gateway/control_plane.py`).
- **Effort:** M · **Risk:** Med (lazy-import refactors can introduce import-time-vs-call-time bugs) · **Deps:** none. **Sprint 1** per Master §6 #8 grouping (PERF-022 is also listed P1 in 06 — schedule S1 if capacity, else S2).

---

### E8 — Gateway Perf & Reliability  *(Sprint 2 — BEFORE-WEB)*

#### T8.1 — Move blocking read handlers to `to_thread` (≈8 endpoints)
- **Findings:** PERF-N01, PERF-011, REL-N01
- **Acceptance:** `get_skills`, `get_memory`, `get_memory_recall`, `get_og_graph`, `get_authoring_targets`, `get_spend`, `get_capabilities` either become plain `def` (Starlette threadpool) or wrap their control-plane call in `anyio.to_thread.run_sync` — matching `create_session`. A test asserts a slow control-plane call on one endpoint does not block `/health`.
- **Files:** `src/hachi/gateway/fastapi_app.py:386,822,827,832,857,862,882` (and `:810-885`).
- **Effort:** S · **Risk:** Low (mechanical; well-understood pattern) · **Deps:** none. **Removes a whole class of event-loop stalls.**

#### T8.2 — Gate `HACHI_GATEWAY_NO_AUTH` to loopback; hard-error on auth-enabled-no-token
- **Findings:** SEC-N01, SEC-013, SEC-014
- **Acceptance:** `HACHI_GATEWAY_NO_AUTH=1` is refused in combination with a non-loopback bind; "auth enabled + falsy token" is a hard startup error, not a silent `_require_auth` no-op. A test asserts no-auth + `0.0.0.0` refuses to start, and that an empty token is rejected. Closes the widened blast radius over config/memory/capability writes.
- **Files:** `src/hachi/gateway/fastapi_app.py:243-250,299-304,840-855,887-894`.
- **Effort:** S · **Risk:** Med (don't break the legit loopback dev convenience) · **Deps:** none.

#### T8.3 — Thread `session_id` through multi-session read/write handlers (stop "first agent")
- **Findings:** WB-N03
- **Acceptance:** `get_skills/get_memory/get_memory_recall/get_og_graph/add_memory_fact/delete_memory_fact` resolve the requesting session (accept a `session_id` query/param) instead of `next(iter(self._agents.values()))`; `get_spend` already does this — match it. A test with 2 sessions asserts memory writes/recall hit the **correct** session's vault.
- **Files:** `src/hachi/cli/main.py:563,571,583,604,851` (the `next(iter(...))` resolutions); `:730-733` (`get_spend` reference).
- **Effort:** M · **Risk:** Med (correctness landmine; must default sanely when unspecified) · **Deps:** none. **Blocks/pairs with T4.4** (web memory writes must target the right session).

#### T8.4 — Pre-warm / pin the Chroma embedding model
- **Findings:** PERF-019
- **Acceptance:** the ~4s cold-start embedding download is pre-warmed (background) or the model is pinned/bundled to remove the hidden network dependency; first recall doesn't pay the full cold-start inline. A test/measurement confirms recall latency after warm-up.
- **Files:** `src/hachi/core/memory_vault.py:121-148` (no `embedding_function` set today → default ONNX MiniLM).
- **Effort:** M · **Risk:** Med · **Deps:** none.

#### T8.5 — SessionStore thread-local connections (restore WAL reader concurrency)
- **Findings:** PERF-015
- **Acceptance:** reads no longer serialize behind one global lock + single connection; thread-local (or pooled) connections restore WAL reader concurrency. A test asserts concurrent reads don't serialize (and writes remain safe). 
- **Files:** `src/hachi/core/session_store.py:49-51` (single conn + lock), read methods `:319,331,342,362`.
- **Effort:** M · **Risk:** Med (SQLite concurrency — verify no write/read corruption) · **Deps:** none.

#### T8.6 — Paginate + cache `/memory` (unbounded `core_memory`)
- **Findings:** PERF-012, PERF-020
- **Acceptance:** `/memory` no longer fetches ALL docs from unbounded `core_memory`; it paginates and/or caches with a bound. A test asserts a large vault doesn't return an unbounded payload.
- **Files:** `src/hachi/gateway/orchestrator.py:741-760` (`snapshot_memory_facts`, `coll.get()` no limit `:744`); `src/hachi/core/memory_vault.py:58-59` ("Unbounded").
- **Effort:** M · **Risk:** Med · **Deps:** none.

#### T8.7 — Event-poll `deque(maxlen)` + bisect; cheap max-sequence accessor
- **Findings:** PERF-006, PERF-N03
- **Acceptance:** `read_events_since` is O(log n) (deque + bisect) instead of O(n) per poll; stream-start uses a cheap "current max sequence" accessor instead of `read_session_events(after=0, limit=10000)`. A test asserts poll/stream-start cost is bounded.
- **Files:** `src/hachi/core/runtime_read_models.py:428,246-247`; `src/hachi/gateway/fastapi_app.py:606` (10000-event scan), `:677` (poll).
- **Effort:** S · **Risk:** Low · **Deps:** none. *(Listed P2 in 06 but cheap and pairs with E8 — schedule S2 with the perf cluster or S3.)*

#### T8.8 — `/og/graph` payload caching at engine init
- **Findings:** PERF-013, PERF-N02
- **Acceptance:** the graph payload (referenced-name set + per-node token estimates) is computed once on skill-engine init / mutation and cached on shared services; `/og/graph` becomes a dict return. Add `ETag`/`Cache-Control` (ties to PERF-014). A test asserts repeated `/og/graph` doesn't recompute token estimates.
- **Files:** `src/hachi/gateway/orchestrator.py:780-849` (recompute per request); cache on `src/hachi/core/shared_services.py`.
- **Effort:** S · **Risk:** Low · **Deps:** T8.1 (offload first). *(P2 in 06 — schedule S2 with cluster or S3.)*

---

### E9 — Network / Egress Security  *(Sprint 2 — BEFORE-WEB)*

#### T9.1 — SSRF guard on dynamic `http_request` tool
- **Findings:** SEC-003
- **Acceptance:** `http_request` calls `validate_outbound_url` (net_guard), uses a **no-redirect** opener (or revalidates each hop), and does **not** resend the auth header on redirect. A test asserts a request to an internal/metadata address is blocked and that redirects don't leak the auth header.
- **Files:** `src/hachi/core/integrations.py:244-270`.
- **Effort:** S · **Risk:** Med · **Deps:** none.

#### T9.2 — Per-hop redirect validation for `browse_web`/Playwright
- **Findings:** SEC-004
- **Acceptance:** Playwright navigations are filtered per-hop against net_guard (not initial-URL-only), so a redirect can't reach internal hosts; residual risk documented if full per-hop filtering isn't feasible. A test/integration asserts a redirect to an internal host is blocked.
- **Files:** `src/hachi/tools/browser_tools.py:49`; `src/hachi/core/browser.py:193`.
- **Effort:** L · **Risk:** Med (Playwright hook complexity) · **Deps:** none.

#### T9.3 — Make untrusted-content framing unconditional
- **Findings:** SEC-002
- **Acceptance:** untrusted content is **always** framed; `scan()` is used only to *escalate*, never to skip framing. Today safe-scored content is returned unframed (`if is_safe: return content, False`). A test asserts scanner-evading content still reaches the model framed.
- **Files:** `src/hachi/core/untrusted_content.py:42-46`.
- **Effort:** S · **Risk:** Low · **Deps:** none.

#### T9.4 — Fix `fts5_only` provider fake-uuid on write failure
- **Findings:** WB-013 (remaining half), REL-N04
- **Acceptance:** `Fts5OnlyMemoryProvider.upsert` propagates write failures (raise or sentinel) instead of `except: pass; return uuid4()`; matches the default vault path (which now fails loudly) and the new `/memory/facts` handler. A test asserts a failed fts5 write does not return a fake success id.
- **Files:** `src/hachi/core/memory_provider.py:230-239`.
- **Effort:** S · **Risk:** Low · **Deps:** none. *(06 lists the fts5 half BEFORE-WEB under WB-013; the risk matrix puts REL-N04 at P3 — schedule S2 with the security cluster.)*

---

### E10 — Accessibility  *(Sprint 2 — BEFORE-WEB; focus-ring foundation already landed)*

#### T10.1 — Make clickable `<div>`/`<g>` rows keyboard-operable
- **Findings:** BB-008, UX-023
- **Acceptance:** clickable rows/nodes become `<button>` or get `role` + `tabIndex` + `onKeyDown` (Enter/Space); they receive the existing focus ring. A test (or axe check) asserts the rows are focusable and keyboard-activatable.
- **Files:** `frontend/src/routes/work.tsx:1030-1044`; `frontend/src/routes/og.tsx:191-198,330-345`; `integrations.tsx:479-503` (AddMcpRow — also wire its missing onClick, BB-N08); `authoring.tsx:201-216`; `index.tsx:204-218`.
- **Effort:** M · **Risk:** Low · **Deps:** T1.4.

#### T10.2 — Dialog semantics + focus-trap + Esc on modals
- **Findings:** BB-007, UX-024
- **Acceptance:** SetupWizard, KennelConsent, and Work-detail modals get `role="dialog"` + `aria-modal` + focus-trap + Esc-to-close; streaming text + approval cards get `aria-live`. A test/axe check asserts modal semantics and a live region for streaming.
- **Files:** modals in `frontend/src/components/`; `conversation.tsx:522-543` (streaming region); `LeashPrompt.tsx` (has `role="dialog"` but no `aria-modal`/trap).
- **Effort:** M · **Risk:** Low · **Deps:** T1.4.

#### T10.3 — Accessible names on icon-only buttons (incl. destructive delete)
- **Findings:** BB-011, UX-023
- **Acceptance:** all icon-only buttons get `aria-label` (memory fact edit/delete, Work close, wizard/consent close, conversation send/stop); the `Icon` primitive supports title/aria. A test/axe check asserts no unnamed interactive controls remain on shipped screens.
- **Files:** `frontend/src/routes/memory.tsx:120,123`; `work.tsx:253-268`; conversation send/stop; `frontend/src/components/ui/Icon.tsx`.
- **Effort:** S · **Risk:** Low · **Deps:** T1.4.

#### T10.4 — Fix light-mode contrast + hardcoded amber pill (AA)
- **Findings:** BB-009
- **Acceptance:** light-mode muted text meets WCAG AA; the hardcoded amber `#A8651A`/`--hachi-muted` values are tokenized and AA-compliant. An axe contrast check passes on shipped screens.
- **Files:** `frontend/src/styles/tokens/colors.css:12`; inline amber at `index.tsx:31`, `work.tsx:31`, `conversation.tsx:32`, `approvals.tsx:39`, `run.tsx:36`.
- **Effort:** S · **Risk:** Low · **Deps:** none.

---

### E11 — Distribution & Supply-Chain Hygiene  *(Sprint 2 — BEFORE-WEB)*

#### T11.1 — Fix the phantom compose Dockerfile + build a frontend image
- **Findings:** WB-004
- **Acceptance:** `docker-compose.yml` no longer references a non-existent `frontend/Dockerfile`; either a real `frontend/Dockerfile` (Node build stage) is added or the frontend service is removed/stubbed honestly. `docker compose build` succeeds. 
- **Files:** `docker-compose.yml:21-24`; new `frontend/Dockerfile` (or remove the service).
- **Effort:** M · **Risk:** Low · **Deps:** none.

#### T11.2 — Run the container non-root on a configurable host
- **Findings:** WB-005, SEC-012, WB-006
- **Acceptance:** the backend `Dockerfile` adds a non-root `USER`; the bind host is configurable (not hardcoded `0.0.0.0` in compose) and pairs with the SEC-N01/SEC-013 token requirement; add a `HEALTHCHECK`; reconcile Python 3.12 (Dockerfile) vs 3.11 (CI). A smoke test runs the image as non-root.
- **Files:** `Dockerfile` (no `USER`, `:1` `python:3.12-slim`, no HEALTHCHECK); `docker-compose.yml:7-8,19`.
- **Effort:** S · **Risk:** Low · **Deps:** none.

#### T11.3 — Ship release artifacts: Docker image + SBOM (+ frontend)
- **Findings:** WB-002, WB-003
- **Acceptance:** `release.yml` publishes a Docker image and an SBOM in addition to the PyPI wheel; the frontend build is included or explicitly out-of-scope-documented. 
- **Files:** `.github/workflows/release.yml`.
- **Effort:** M · **Risk:** Low · **Deps:** T11.1/T11.2 (image must build first).

#### T11.4 — Add dependency-CVE scan + SAST to CI
- **Findings:** SEC-011
- **Acceptance:** the CI `security` job adds `uv pip audit` (or `pip-audit`), `npm audit` (frontend), and a SAST pass (e.g. `bandit`); high CVEs fail the build (Gate A G-A5). A clean run is proven. 
- **Files:** `.github/workflows/ci.yml:36-59` (security job runs only `hachi security-audit` today).
- **Effort:** S · **Risk:** Low · **Deps:** T1.1 (CI must be sane first).

#### T11.5 — Wire generated `api-types.ts` into the SPA adapters (close the contract)
- **Findings:** WB-001 (Option A completion), drift D-1…D-4, WB-024
- **Acceptance:** the SPA's hand-mirrored `interface Gateway*` shapes consume the generated `api-types.ts` so OpenAPI drift fails `check:api` in CI; `SECURITY.md` is linked from README (Gate A G-A6). A CI run proves drift is now caught.
- **Files:** `frontend/src/lib/data/domains/*.ts`; `frontend/src/lib/data/api-types.ts` (from T1.1); `README.md`, `SECURITY.md`.
- **Effort:** M · **Risk:** Med (regenerated types may surface real mismatches to fix) · **Deps:** T1.1.

---

### E12 — Tech-Debt & Polish  *(Sprint 3 = P2; Sprint 4 = P3)*

| ID | Objective | Findings | Acceptance (short) | Files | Effort | Risk | Deps | Sprint |
|---|---|---|---|---|---|---|---|---|
| **T12.1** | Durable orchestrator func-jobs | WB-019, REL-007 | Func-jobs (prefetch/title/sync) survive a restart; T1.5 test goes green | `src/hachi/gateway/orchestrator.py:937-946,981-989`; `src/hachi/jobs/queue.py:591-603` | M | Med | T1.5 | S3 |
| **T12.2** | Fix `client/models.py` encoding | WB-N01 | Re-save UTF-8 **no BOM**; replace 10 mojibake `â€"`→`—`; add encoding guard test/hook | `src/hachi/client/models.py:2,92,112,117,218,703,704,863,865,970` | S | Low | none | S3 |
| **T12.3** | Remove mock chat/run scripts from live store | WB-011 | ~250–400 LOC hardcoded scripts removed from live action methods; `partialize` reviewed; dev "Preview reconnecting" + static Recents removed | `frontend/src/lib/data/store.ts:1066-1138,1285-1310,1389-1392`; `CommandPalette.tsx:102,105-109` | M | Med | T4.5 | S3 |
| **T12.4** | Per-turn `semantic_search` hot path | WB-012 | Stop the constant `semantic_search("important context")` busting the prompt cache every turn; cache/condition it | `src/hachi/gateway/orchestrator.py:882,1056` | S | Med | none | S3 |
| **T12.5** | FTS index for cross-session search | PERF-016 | Replace unindexable leading-wildcard `LIKE %q%` with an FTS-backed search | `src/hachi/core/session_store.py:277-280` | M | Med | T8.5 | S3 |
| **T12.6** | Webhook fail-closed on empty allowlist | SEC-006 | Empty `HACHI_CHANNEL_ALLOWLIST` denies (not accepts) all senders | `src/hachi/gateway/fastapi_app.py:733-736` | S | Med | none | S3 |
| **T12.7** | Reap rate-limiter buckets | REL-002, REL-N02 | Empty/expired buckets evicted (or LRU cap); pairs with T12.6 | `src/hachi/core/rate_limit.py:24`; `fastapi_app.py:563,724,737-738` | S | Low | T12.6 | S3 |
| **T12.8** | `doctor` emits a PASS/FAIL verdict | BB-013 | `doctor` surfaces skill-lint errors with an overall verdict (like `security-audit`/`config-validate`) | `src/hachi/cli/main.py` (doctor handler) | S | Low | none | S3 |
| **T12.9** | `--resume`/`-c` honest (wire or stop advertising) | BB-N01 | Resume either reloads a past session or is removed from help; web "Resume" stub fixed/removed | `src/hachi/cli/main.py:155-174,1842-1845,1891-1902`; `frontend/.../memory.tsx` resume | M | Med | none | S3 |
| **T12.10** | Mark stale arch-review doc + brand/docs cleanup | 06 §3 REJECT; UX-004/021; Low docs items | `docs/strategy/01-architecture-review.md` marked stale (do-not-action); README install paths reconciled; retired teal/ouroboros brand brief updated; `SECURITY.md` linked | `docs/strategy/01-architecture-review.md`; `README.md:58,277`; `web-ui-design-brief.md` | S | Low | none | S3 |
| **T12.11** | Consolidate the two pack systems | BB-012 | One pack system (or clearly delineated scopes) for `hachi pack` vs `hachi skill pack` | `src/hachi/cli/commands/pack.py`, `commands/skill.py` | L | Med | T3.1/T3.3 | S4 |
| **T12.12** | Responsive/tablet pass (RISK-ACCEPT for v1) | BB-003 | "Don't break < 900px" tablet pass (S3); full responsive (S4). *RISK-ACCEPT per 06 §2.* | `frontend/` layout/CSS | L | Low | none | S3 (tablet) / S4 (full) |
| **T12.13** | Encryption at rest (opt-in; RISK-ACCEPT) | SEC-010 | Document plaintext-at-rest threat model (S3); opt-in encrypted vault (S4). *RISK-ACCEPT per 06 §2.* | `src/hachi/core/session_store.py`, `memory_vault.py`; docs | L | Med | none | S4 |
| **T12.14** | SSE parser + REST path-escape hardening | PERF-N05, SEC-N02 | SSE parser O(n) (no front-slicing); client path segments validated/quoted | `src/hachi/client/sse.py:83-86`; `src/hachi/client/gateway.py:187,224,230,234,257,329` | S | Low | none | S4 |
| **T12.15** | Misc Low polish | PERF-N04/N06, SEC-016/018, WB-018/021/023, REL-008 | Inventory cache+ETag; cached `.env`; temp-script `chmod 0600`; remove inlined FE token in direct mode; thread `surface`; OpenAI stream session cleanup; SQLite integrity-check/backup | per finding IDs in 04/05 | M | Low | none | S4 |

> **RISK-ACCEPT (06 §2 — documented, not blocking v1):** desktop-only/no-mobile (BB-003 → T12.12), plaintext-at-rest (SEC-010 → T12.13, **must be documented**), monolith files (WB-016 → freeze via T2.4), non-durable func-jobs until T12.1 (WB-019), `.og` corpus near token break-even (track a per-pack budget ceiling).

---

## 4. Parallelization Strategy

### Lane assignment (5 agents, chosen so they don't collide on the same files)

| Agent | Lane | Owns (file domains) | Tasks |
|---|---|---|---|
| **Agent A** | **Backend-engine** | `core/{settings,llm,loop,cost,community_packs,mcp_client,integrations,untrusted_content,memory_provider}.py`, `cli/main.py` (config/secrets/authoring/doctor) | T2.1, T2.2, T2.3, T2.4, T3.1, T3.2, T3.3, T4.7, T7.1, T7.4, T9.1, T9.2, T9.3, T9.4, T12.1, T12.4, T12.8, T12.9 |
| **Agent B** | **Gateway** | `gateway/{fastapi_app,control_plane,orchestrator}.py`, `core/{approvals,session_store,memory_vault,runtime_read_models}.py`, `jobs/queue.py`, `openapi/` | T5.3, T5.4, T8.1–T8.8, T12.5, T12.6, T12.7 |
| **Agent C** | **Frontend-SPA** | `frontend/src/**` (routes, components, `lib/data/domains/*`, `store.ts`, styles) | T4.1, T4.2, T4.3, T4.4, T4.5, T4.6, T5.1, T5.2, T6.1–T6.5, T7.2, T7.3, T10.1–T10.4, T12.3 |
| **Agent D** | **Security** | cross-cuts but security-reviewed; pairs with A/B on the security tasks; owns dep-scan/SAST + hardening | (reviews T3.x/T8.2/T9.x/T11.4) + T12.13, T12.14, SEC-016/018 portions of T12.15 |
| **Agent E** | **Infra / CI** | `.github/workflows/*.yml`, `Dockerfile`, `docker-compose.yml`, `frontend/package.json`, `mypy-strict-gate.ini`, `tests/test_config_get_set.py`, test harness | T1.1, T1.2, T1.3, T1.4, T1.5, T11.1, T11.2, T11.3, T11.4, T11.5, T12.2, T12.10 |

**Collision rule:** Agent C (SPA) and the leash/spend work both touch `chat.ts`/`approvals.ts` — serialize within Agent C (do T4.1 fixture-removal → then T4.3 spend → then T5.1 leash → then T6.x chrome). Agent A owns all of `cli/main.py`'s config/authoring; Agent E owns only the *test* and *CI* edits to that area (the false-green test), not the command body — A and E coordinate via T1.2↔T2.1 (E writes the red test, A makes it green).

### Critical path (must finish before parallel work is *trusted*)
```
T1.1 (CI green-for-real)  ─┐
T1.2 (kill false-green test) ─┼─►  ALL other tasks  (until these land, "green" is meaningless)
T1.3 (blocking lint/mypy) ─┘
        │
        └─► T2.1 (config fix turns T1.2 red→green) ─► T2.2 (cost-cap field) ─► T6.4 (web cap UI)
```

### Dependency graph (indented; "→" = blocks)
```
SPRINT 1 (P0)
  T1.1  CI real ───────────────► (gates trust for everything; T1.4, T11.x)
  T1.2  false-green test ───────► T2.1
  T1.3  blocking gates
  T2.1  config fix ─────────────► T2.2 ──► T6.4
  T2.2  cost-cap field ─────────► T6.4
  T3.1  zip-slip ───────────────► T3.3 (S2), T12.11 (S4)
  T3.2  MCP exec gate
  T4.1  remove fixtures ────────► (precedes C's T4.3/T5.1 in same files)
  T4.2  real key validation
  T4.3  spend no-double-count ──► T6.1, T6.2, T6.3
  T6.1  status bar ─────────────► T6.2
  T6.2  live-cost readout
  T7.1  cache history (PERF-001)
  T7.2  virtualize transcript ──► T7.3
  T7.3  throttle + memo
  T7.4  CLI lazy import   (S1 if capacity, else S2)

SPRINT 2 (P1 / BEFORE-WEB)
  T1.4  FE test harness  (shared by all C/leash/spend tests)
  T3.3  pack signing            (← T3.1)
  T4.4  memory writes wired     (↔ T8.3 for correct session)
  T4.5  /run real or removed
  T4.6  authoring UI            (← T4.7)
  T4.7  authoring 501 honest
  T5.1  in-context leash POST   (↔ T4.1 same files)  ──► T5.2, T5.4
  T5.2  real countdown          (← T5.1)
  T5.3  approval wait off-loop
  T5.4  type leash SSE payload  (← T5.1, T1.1)
  T6.3  Spend nav               (← T4.3)
  T6.4  cap settable+enforced   (← T2.2)
  T6.5  provider choice + tier names
  T8.1  to_thread read handlers
  T8.2  NO_AUTH loopback gate
  T8.3  session_id routing      ──► T4.4
  T8.4  embedding pre-warm
  T8.5  SessionStore conns      ──► T12.5
  T8.6  /memory pagination
  T8.7  event deque + max-seq    (S2 or S3)
  T8.8  /og/graph cache  (← T8.1)  (S2 or S3)
  T9.1  http_request SSRF
  T9.2  browser redirect SSRF
  T9.3  unconditional framing
  T9.4  fts5 fake-id fix
  T10.1 focusable rows  T10.2 modal semantics  T10.3 labels  T10.4 contrast
  T11.1 compose Dockerfile ──► T11.3
  T11.2 non-root container ──► T11.3
  T11.3 release artifacts/SBOM
  T11.4 dep-CVE/SAST     (← T1.1)
  T11.5 wire api-types    (← T1.1)

SPRINT 3 (P2) : T1.5, T2.4, T8.7/T8.8 (if not done), T12.1(←T1.5), T12.2, T12.3, T12.4, T12.5(←T8.5), T12.6, T12.7(←T12.6), T12.8, T12.9, T12.10, T12.12(tablet)
SPRINT 4 (P3) : T12.11(←T3.1/T3.3), T12.12(full), T12.13, T12.14, T12.15
```

---

## 5. Sprint Roadmap

### Sprint 1 — P0 critical only → unlocks **Gate A** (engine/CLI release)
- **Tasks:** T1.1, T1.2, T1.3, T2.1, T2.2, T3.1, T3.2, T4.1, T4.2, T4.3, T6.1, T6.2, T7.1, T7.2, T7.3 (+ T7.4 if capacity).
- **Run order:** **T1.1 + T1.2 + T1.3 first** (Agent E), in parallel with the start of T7.1 (Agent A, independent). Then everything else fans out.
- **Agents in parallel:** A (T2.1→T2.2, T3.1, T3.2, T7.1, T7.4) · B (idle/early-start T8.1) · C (T4.1→T4.3, T4.2, T6.1→T6.2, T7.2→T7.3) · D (review T3.x) · E (T1.1, T1.2, T1.3).
- **Exit criteria (Gate A, from 09):** G-A0 CI green-for-real + false-green test repaired + blocking gates (T1.1/T1.2/T1.3); G-A2 config-get/set work for all keys + real cost-cap (T2.1/T2.2); G-A3 ZIP zip-slip+caps (T3.1); G-A4 MCP tier-gate+confirm (T3.2); G-A7 PERF-001 landed (T7.1). *(G-A5 dep-CVE and G-A6 Docker non-root are Gate-A items satisfied in Sprint 2 by T11.4/T11.2 — flag if Gate A must ship before them; PO may pull T11.2/T11.4 into S1.)*

### Sprint 2 — P1 high → unlocks **Gate B** (web beta)
- **Tasks:** T1.4, T3.3, T4.4, T4.5, T4.6, T4.7, T5.1, T5.2, T5.3, T5.4, T6.3, T6.4, T6.5, T8.1, T8.2, T8.3, T8.4, T8.5, T8.6 (+ T8.7/T8.8), T9.1, T9.2, T9.3, T9.4, T10.1, T10.2, T10.3, T10.4, T11.1, T11.2, T11.3, T11.4, T11.5.
- **Agents in parallel:** A (T3.3, T4.7, T9.1–T9.4) · B (T5.3, T5.4, T8.1–T8.8) · C (T4.4→T4.6, T5.1→T5.2, T6.3→T6.5, T10.1–T10.4) · D (review T9.x/T8.2/T11.4) · E (T1.4, T11.1–T11.5).
- **Exit criteria (Gate B, from 09):** G-B1 no fabricated data (T4.1) · G-B2 real key validation (T4.2) · G-B3 in-context leash real + countdown + decline reason + Always-allow confirm (T5.1/T5.2) · G-B4 memory writes persist + correct session (T4.4/T8.3) · G-B5 /run real or removed (T4.5) · G-B6 spend accurate + nav + cap settable (T4.3/T6.3/T6.4) · G-B7 provider choice consistent (T6.5) · G-B8 status bar (T6.1) · G-B9 a11y (T10.1–T10.4) · G-B10 SSRF + framing + pack signing + NO_AUTH gate (T9.1/T9.2/T9.3/T3.3/T8.2) · G-B11 approval off-loop + to_thread (T5.3/T8.1) · G-B12 FE tests + gateway smoke (T1.4).

### Sprint 3 — P2 medium → **Gate C** (partial / scale hardening)
- **Tasks:** T1.5, T2.4, T8.7/T8.8 (if deferred), T12.1, T12.2, T12.3, T12.4, T12.5, T12.6, T12.7, T12.8, T12.9, T12.10, T12.12 (tablet "don't break < 900px").
- **Agents in parallel:** A (T2.4, T12.1, T12.4, T12.8, T12.9) · B (T8.7/T8.8, T12.5, T12.6, T12.7) · C (T12.3, T12.12-tablet) · D (security reviews) · E (T1.5, T12.2, T12.10).
- **Exit criteria:** durable jobs (T12.1); SessionStore concurrency + FTS search (T8.5/T12.5); embedding pre-warm (T8.4 from S2); webhook fail-closed (T12.6); `doctor` verdict (T12.8); FE test foundation broadened (T1.4 → coverage); docs consistent (T12.10).

### Sprint 4 — P3 low / enhancements
- **Tasks:** T12.11 (pack consolidation), T12.12 (full responsive), T12.13 (encryption-at-rest opt-in), T12.14 (SSE/path hardening), T12.15 (misc low).
- **Exit criteria (toward GA, Gate C full):** full a11y AA + CI a11y gate; dep-CVE/SAST blocking (T11.4 made blocking); responsive; `.og` corpus budget ceiling; encryption-at-rest documented + opt-in.

---

## 6. Collision-Avoidance Notes  *(read before starting any task)*

1. **`src/hachi/tui/cockpit/**` is OWNED BY A CONCURRENT SESSION and is OFF-LIMITS.** Do **not** edit any file under it. It consumes `src/hachi/client/**` and several gateway read endpoints — so server-side fixes (T8.1, T8.3, T8.6, T8.8, T12.2 encoding) **benefit** the cockpit, but the fix always lands **server-side / in `client/`**, never in `cockpit/`. The Performance report confirms the cockpit's 3s auto-refresh amplifies the read-path costs; the fix belongs in E8, not the cockpit.

2. **`frontend/` is UNCOMMITTED (untracked).** All of Agent C's work and T1.1/T1.4/T11.5 touch untracked files. Before editing: confirm the working-tree state, and **commit the frontend** as an early step (coordinate with the team) so CI (T1.1) has something real to point at. Until `frontend/` is committed, the `frontend-contract` job cannot be validated end-to-end.

3. **CI (T1.1) + the false-green test (T1.2) must land before parallel work is trusted.** Per 06 §0 and 09 G-A0, a fix without a CI-run test that reproduces the original failure is **not done**. Treat any "it's fixed" before T1.1/T1.2 land as unverified.

4. **Within Agent A's lane, `cli/main.py` is a 2,886-LOC monolith** touched by T2.1/T2.2/T2.3/T4.7/T8.3/T12.8/T12.9. Serialize edits to it (or split by clearly non-overlapping regions: config block `:2205-2272`, secrets `:2444-2474`, authoring `:651-714`, multi-session resolvers `:563-604,730-733`, doctor handler). Do **not** grow it further (WB-016 freeze).

5. **`chat.ts` / `approvals.ts` are edited by T4.1 (fixtures), T4.3 (spend), T5.1 (leash), T6.1/T6.2 (chrome).** All are Agent C — run them **in that order** to avoid self-collision; each builds on the prior.

6. **`openapi/hachi-openapi.json` is regenerated** by T4.7, T5.4, and any gateway route change — always run `uv run hachi gateway-openapi --output openapi/hachi-openapi.json` after a route/contract edit, and (once T1.1 Option A lands) `npm run --prefix frontend generate:api`. The export is currently byte-identical to the routes (02 §3) — keep it that way.

7. **Do not action `docs/strategy/01-architecture-review.md`** (06 §3 REJECT — confirmed stale). T12.10 marks it stale; everything else ignores it.

---

*Continue to [08 — Verification Strategy](08-verification-strategy.md) → [09 — Release Readiness](09-release-readiness.md).*
