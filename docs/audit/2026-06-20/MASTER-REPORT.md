# Hachi Product Quality Audit — Master Report (2026-06-20)

> Fresh 5-agent verification audit of the **post-remediation** state (HEAD `0e4dcb6`),
> following the 2026-06-19 audit (85 findings) and remediation PRs #116–129.
> Per-agent source reports: [`agent-1-blackbox.md`](./agent-1-blackbox.md),
> [`agent-2-whitebox.md`](./agent-2-whitebox.md), [`agent-3-ux.md`](./agent-3-ux.md),
> [`agent-4-security.md`](./agent-4-security.md), [`agent-5-performance.md`](./agent-5-performance.md).

---

## 1. Executive Summary

**Health Score: 59/100** (up from 44/100 on 2026-06-19) — **Verdict: NO-GO for public / multi-user release; GO for loopback single-user dev preview with caveats.**

| Release surface | Verdict | Gating findings |
|---|---|---|
| Loopback, single-user, single-token dev preview | **GO (with caveats)** | Headline live web flows still broken (C2, H1, H2, H4, H5) |
| Multi-user / shared-token | **NO-GO** | C1 session ownership, H9 turn-lock race, H10 webhook injection |
| Network-exposed / public web product | **NO-GO** | All of the above + C3/C4 onboarding/resume |

**The remediation was genuine.** Unlike the 2026-06-18 v2 pass (which was cosmetic because tests mocked the wrong types), the 2026-06-19 backlog was worked seriously and **verified in source**: 12 of 16 security findings fixed, both major performance bottlenecks fixed, all 5 backend-debt items fixed and exercising real code paths, and the entire frontend crash/fixture class eliminated. Roughly **48 of the prior 85 findings are confirmed FIXED.**

**Two structural problems block release:**

1. **BE-1 (PR #128) was never merged** (it remains DRAFT after a SessionStore-batching change regressed FK tests). That single PR contained four release blockers, all confirmed **still open on `main`**: session-ownership enforcement (C1), session resume (C3), the `hachi serve` single-process command (C4), and the dynamic gateway model list (H6).

2. **A new regression class introduced by the split remediation.** Frontend PRs #116–124 merged handlers wired against gateway routes that only the unmerged BE-1 would have provided. The result: the live web product calls **five endpoints that do not exist** (`/authoring/{name}/generate`, `/config/limits`, `/runtime/mcp/register`, `/sessions/{id}/permissions/always-allow`, `/skills/add`) and several "mutations" are local-store-only with success toasts that misreport persistence. The UI is polished and validated; the headline journeys (edit Hachi's brain, install a skill, save settings, add an MCP server, grant a standing permission) cannot complete against a real gateway.

**Top risks (ranked):**
1. **Session hijacking (C1)** — one shared bearer token + no per-session owner; any client can read/inject/close any session, and `GET /sessions` hands out the IDs. Single most dangerous gap.
2. **Broken live web flows (C2/H1/H2/H4/H5)** — five core mutations fail silently against a real gateway → false-confidence, trust collapse on first real use.
3. **Onboarding still two-terminal (C4)** + **resume still dead code (C3)** — first-value path unfixed for the target ICP.
4. **Concurrency hazards (H9 turn-lock, H10 webhook injection, H12 MemoryVault race)** — ChromaDB corruption / prompt injection under real multi-client load.
5. **Latency under load (H7 sync compaction, H8 ChromaDB cold-start)** — the two highest-impact hot-path issues remain open.

**Critical blockers: 4. The composite improved by 15 points; the remaining work is concentrated, not diffuse — landing one coordinated backend PR (BE-1 + the five missing routes) clears the majority of the live-product blockers.**

---

## 2. QA Findings Report

### Methodology

Five independent agents audited `main` at HEAD `0e4dcb6`, each verifying prior fixes against source (not changelog) and hunting regressions/new issues:

| Agent | Domain | Findings (open/partial/new) | Notable |
|---|---|---|---|
| Black Box | Frontend/CLI user flows, live-vs-mock fidelity | 2C · 5H · 4M (+3 positive FIXED) | Found the "FE wired to nonexistent routes" regression class |
| White Box | Backend architecture, tests, debt | 3C · 4H · 5M · 3L | **Test suite: 2466 passed, 15 skipped, 0 failed** |
| UX / Product | Onboarding, discoverability, moat visibility | 1C · 5H · 8M · 4L | `.og` moat now explained; setup still two-terminal |
| Security / Reliability | Auth, sandbox, SSRF, channels, resilience | 1C · 2H · 4M | 12 FIXED; session-ownership still open |
| Performance / Scalability | CPU complexity, LLM latency, DB I/O, SSE | 3H · 4M · 5L | BM25 + queue fixed; compaction + cold-start open |

Test suite was executed (`python -m pytest`): **2466 passed / 15 skipped / 0 failed (~261s)**. One skip masks a residual MemoryVault close-ordering race (L-test); a stray sibling `tokoboros` checkout pollutes collection (L1).

### Consolidated, de-duplicated open backlog

Findings reported by multiple agents are merged under a single canonical ID. **Status legend:** STILL-OPEN (never fixed), REGRESSED (broke during remediation), PARTIAL (improved but incomplete), NEW (introduced/newly found).

#### Critical (4)

| ID | Title | Category | Status | Source IDs | Evidence |
|---|---|---|---|---|---|
| **C1** | No session-ownership — any bearer token can read/inject/close any session; `GET /sessions` leaks IDs | Security | STILL-OPEN | SEC-01 / WB-01 / CRIT-006 | `control_plane.py:261,325,342,374`; `fastapi_app.py:443-479` |
| **C2** | Authoring "Generate" calls `POST /authoring/{name}/generate` which returns 404 — no backend route; Apply/Revert were stubs | Functional | STILL-BROKEN | BB-01 / UX-12 / CRIT-002 | `authoring.ts:151`; `fastapi_app.py:894-912` (route absent) |
| **C3** | Session resume `--resume`/`--continue` is dead code — prints "not yet wired", starts fresh, exit 0 | Functional | STILL-OPEN | WB-02 / UX-03 / CRIT-007 | `cli/main.py:1793` (dead resolver) vs `:1860` |
| **C4** | No `hachi serve` single-process command — onboarding still two terminals + Node | Onboarding | STILL-OPEN | WB-03 / UX-01 / CRIT-008 | `cli/main.py` (absent); `README.md`; `docker-compose.yml` |

C2/C3/C4 (+H6) are the contents of the unmerged BE-1 PR #128.

#### High (12)

| ID | Title | Category | Status | Source | Evidence |
|---|---|---|---|---|---|
| **H1** | Settings "Save" PUTs nonexistent `/config/limits` → always "Save failed" | Functional | REGRESSED | BB-03 | `settings.tsx:296`; `fastapi_app.py:620-637` |
| **H2** | Kennel browse/add-skill is store-only & empty in live; no `POST /skills/add` or listing endpoint — moat funnel non-functional | Functional/Product | PARTIAL | BB-02 / UX-02 | `store.ts:352,866-896`; `skills.tsx:361` |
| **H3** | Allowed folders/commands never persist (store-only; existing endpoint unused) | Functional | STILL-BROKEN | BB-04 | `store.ts:988-1009`; `fastapi_app.py:632` |
| **H4** | "Always allow" (chat) hits nonexistent endpoint (404 swallowed); Home Allow/Decline never reaches gateway | Functional/Security | PARTIAL | BB-05 / BB-06 | `store.ts:1212-1230,1421-1441,690-701` |
| **H5** | MCP "Add server" POSTs nonexistent `/runtime/mcp/register` | Functional | PARTIAL | BB-07 | `integrations.ts:457`; `fastapi_app.py:526` |
| **H6** | Gateway `list_models` hardcoded/stale — missing the running model (Opus/4.x) | Functional | STILL-OPEN | WB-04 / BB-10 / HIGH-008 | `cli/main.py:501-503,2426` |
| **H7** | Sliding-window compaction makes a synchronous, un-timeout'd LLM call inside `prepare_for_turn` — adds 1–10s every ~30 messages | Performance | STILL-OPEN | PERF-03 / HIGH-015 | `core/memory.py:831,1012` |
| **H8** | ChromaDB cold-start: eager `import chromadb` + 5 serial `get_or_create_collection` on boot path dominate time-to-first-turn | Performance | STILL-OPEN | PERF-05 / HIGH-021 | `core/memory_vault.py:121-148` |
| **H9** | No per-agent turn lock — concurrent `chat()` races ChromaDB writes | Reliability | STILL-OPEN | REL-01 / HIGH-003 | `cli/main.py:189-246` |
| **H10** | `/channels/{channel}/inbound` webhook path bypasses `guard_untrusted` (polling adapters were fixed) | Security | PARTIAL | SEC-02 / HIGH-002 | `fastapi_app.py:759-775` |
| **H11** | Python sandbox is process-isolation only in `unrestricted` tier (env-gate + AST scan landed; true namespace/seccomp isolation absent) | Security | PARTIAL | WB-06 / SEC-06 / CRIT-005 | `sandbox_tools.py:99-147` |
| **H12** | `MemoryVault.close()` close-then-access ordering race; RLock fixed interleaving but not ordering — a skipped test masks it | Reliability | PARTIAL | WB-07 / REL-02 / PERF-06 / HIGH-021 | `memory_vault.py:157-185,314-340` |

#### Medium (12)

| ID | Title | Category | Status | Source |
|---|---|---|---|---|
| **M1** | `SessionStore.log_message` commits per write (3/turn); BE-1 batching regressed FK tests — fix = sync `start_session` + batched `log_message` | Performance | STILL-OPEN | PERF-04 / MED-001 |
| **M2** | Memory subsystem opens a new `AnthropicClient`/httpx pool per background op instead of reusing `loop.llm_client` | Performance | STILL-OPEN | PERF-07 / MED-022 |
| **M3** | `/memory/recall` returns stored facts with no secret redaction | Security | STILL-OPEN | SEC-03 / MED-009 |
| **M4** | Channel rate-limiter keyed on spoofable `sender_id` | Security | PARTIAL | SEC-11 / MED-003 |
| **M5** | No backup/recovery/WAL strategy for SQLite stores (session + jobs) | Reliability | NEW | REL-04 |
| **M6** | Memory recall invisible at point of use — no "recalled N facts" chip in conversation | UX/Product | STILL-OPEN | UX-04 / MED-030 |
| **M7** | Lightning onboarding tier disables memory — fastest path turns off the core retention mechanic | Product | STILL-OPEN | UX-14 / MED-029 |
| **M8** | No session selector / title in web Conversation screen | UX | STILL-OPEN | UX-15 / LOW-020 |
| **M9** | Spend caps (per-task, daily) are display-only — not editable from any UI | UX | STILL-OPEN | UX-16 / MED-018 |
| **M10** | No push/browser notification for pending approvals — agent stalls silently | Missing Feature | STILL-OPEN | UX-17 / MED-024 |
| **M11** | Live Run screen is an honest static placeholder — no sub-agent streaming/observability | Functional | STILL-OPEN | BB-09 / UX-05 / HIGH-007 |
| **M12** | SSE active-stream/chat loops still busy-poll at ~20 Hz | Performance | PARTIAL | PERF-08 / MED-002 |

#### Low (9)

| ID | Title | Category | Status | Source |
|---|---|---|---|---|
| **L1** | Stray sibling `tokoboros` checkout pollutes pytest collection | Test Integrity | NEW | WB-09 |
| **L2** | No test guards gateway model list (false-green for H6) | Test Integrity | STILL-OPEN | WB-10 |
| **L3** | `list_skill_models` swallows errors to `[]` silently | Reliability | NEW | WB-15 |
| **L4** | Authoring shows hardcoded "re-indexed in 0.3s" fabricated timing | UX | NEW | UX-19 |
| **L5** | Duplicate bisect prefix-set computation in skill search | Performance | NEW | PERF-11 |
| **L6** | O(N²·B) sliding-boundary scan in compaction | Performance | NEW | PERF-12 |
| **L7** | Web frontend desktop-only — no mobile approval view | Missing Feature | STILL-OPEN | UX-18 / LOW-025 |
| **L8** | README anchors to competitor + advertises not-yet-live flows | Product/Docs | STILL-OPEN | UX-13 / LOW-019 |
| **L9** | Prompt-cache breakpoints shift per turn (low cache-hit) — partly by-design | Performance | PARTIAL | PERF-09 / LOW-016 |

### Verified FIXED (high-confidence, exercised in source) — selected

Security: SSRF DNS-pinning TOCTOU (validate→pin IP→Host header→`follow_redirects=False`), channel injection guard on polling adapters, sandbox env-gate + AST scan, Telegram token redaction, secret-store salt-per-write, single-use `allow_once`, Windows `^`/`%` metachar blocklist, MCP response cap, outbound secret redaction, dependency CVE bumps (cryptography 49.0.0, starlette 1.3.1, chromadb 1.5.9), loopback-gated NO_AUTH + constant-time token compare.
Backend: HachiError hierarchy, sub-agent model override respected, orchestrator silent-except logging, `TurnContext.surface` threaded, config-tool integration tests on real `ResolvedConfig` via live `ToolRegistry`.
Performance: BM25 O(log V) bisect (results-preserving), persistent job-queue connection, `record_use` debounce.
Frontend: division/empty-state guards (work/index/og/home), Home first-run + integration-filtered starters, slash-menu keyboard nav, focus traps, `.og` explainer panels, brand-vocabulary glossing, Memory Recall/Audit tab split, no more fixture-as-real-data.

---

## 3. Product Gap Analysis

### Findings by category (open only)

| Category | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| Functional | 2 | 5 | 1 | 0 | 8 |
| Security | 1 | 2 | 2 | 0 | 5 |
| Performance | 0 | 2 | 3 | 3 | 8 |
| Reliability | 0 | 1 | 1 | 0 | 2 |
| UX | 0 | 0 | 4 | 0 | 4 |
| Product | 0 | 1 | 2 | 1 | 4 |
| Onboarding | 1 | 0 | 0 | 0 | 1 |
| Missing Feature | 0 | 0 | 1 | 1 | 2 |
| Test Integrity | 0 | 0 | 0 | 2 | 2 |
| **Total** | **4** | **12** | **12** | **9** | **37** |

### Missing product capabilities (required before web-product launch)

1. `hachi serve` single-process command serving the pre-built `frontend/dist` bundle (C4).
2. Authoring backend: `POST /authoring/{name}/generate` (LLM diff), real Apply/Revert filesystem handlers (C2).
3. Live kennel backend: skill-listing endpoint + `POST /skills/add` with persistence (H2).
4. Persisted permission/config mutations: `/config/limits`, allowed folders/commands, `/sessions/{id}/permissions/always-allow` (H1/H3/H4).
5. MCP registration route `/runtime/mcp/register` (H5).
6. Session-ownership binding at creation + enforcement on all read/write/stream routes (C1).
7. Functional session resume threaded through CLI **and** gateway `_boot_session_agent` (C3).
8. Dynamic model list (live provider discovery, as the `hachi models` CLI already does) (H6).
9. Live run / sub-agent observability via SSE on `/run` (M11).
10. Recall-at-point-of-use chip, approval notifications, mobile approval view, session selector, editable spend caps, memory-on-by-default for Lightning (M6–M10, L7).

### Moat status

The `.og` ObjectGraph differentiator is now **explained** at every entry point (skills/og/authoring explainer panels — a genuine 2026-06-19 win), but its **acquisition funnel is non-functional live**: the kennel is empty and "Add" does not persist (H2). The moat is visible but not usable end-to-end.

---

## 4. Risk Assessment Matrix

```
              IMPACT →   Low              Medium                     High / Critical
 LIKELIHOOD
   High                  L4, L8           H1, H2, M5, M7             C1, C2, H4, H9, H10
   Medium               L1, L2, L3        H3, H5, M3, M6, M11        C3, C4, H7, H8
   Low                  L5, L6, L9        M4, M8, M9, M12            H11, H12
```

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Session hijacking in any shared deployment | High | Critical | Bind `owner_token_hash` at creation; enforce on read/write/stream; scope `GET /sessions` (C1) |
| Headline live web flows fail silently → trust collapse | High | Critical | Land the five missing gateway routes (C2/H1/H2/H4/H5) |
| Prompt injection via channel webhook | High | High | Wrap webhook inbound text in `guard_untrusted` (H10) |
| ChromaDB corruption under concurrent turns | High | High | Per-agent turn lock; guard MemoryVault close ordering (H9/H12) |
| First-value path abandonment (two-terminal + dead resume) | Medium | High | `hachi serve` + real resume (C4/C3) |
| Latency spikes under load | Med–High | High | Move compaction to background job; lazy/parallel ChromaDB init (H7/H8) |
| Data loss on SQLite corruption | Medium | Medium | WAL + periodic backup/restore (M5) |

---

## 5. Product Owner Analysis — Disposition

As Product Owner, each finding is dispositioned with rationale.

| Disposition | Findings | Rationale |
|---|---|---|
| **Fix immediately (this remediation, Sprint 1)** | C1, C2, C3, C4, H1, H2, H4, H5, H6 | The critical security gap (C1) plus the entire "FE wired to nonexistent backend" regression cluster + BE-1 contents. These define the gap between demo and reality; nothing ships until they close. H6 is trivial and test-backable now. |
| **Fix before release (Sprint 2)** | H3, H7, H8, H9, H10, H11, H12, M11 | Persistence gap (H3), concurrency/injection hazards (H9/H10/H12), latency (H7/H8), sandbox hardening (H11), and live observability (M11). Required for multi-user/exposed, not for a loopback dev preview. |
| **Next sprint (Sprint 3)** | M1, M2, M3, M4, M5, M6, M12 | Performance/reliability/security depth + recall-visibility. High value, not release-gating for the first surface. |
| **Backlog (Sprint 4)** | M7, M8, M9, M10, L4, L5, L6, L7, L8, L9 | Product polish, enhancements, notifications, mobile. Schedule by demand. |
| **Do now, cheap (fold into Sprint 1)** | L1, L2, L3 | Test-integrity hygiene; trivial and prevent false-green. |
| **Reject / not a defect** | (carryover) CRIT-004 safety mapping, CRIT-001 critical severity | Verified false-positive / overstated in the 2026-06-19 VERIFICATION pass; keep only as regression tests. |

**Strategic note:** the most efficient path is a **single coordinated backend PR** that lands BE-1 (session ownership, resume, serve, models) *together with* the five missing routes the frontend already calls — because they share files (`fastapi_app.py`, `control_plane.py`, `cli/main.py`) and the frontend is already wired for them. Splitting backend from frontend is what created this regression class; do not repeat it.

---

## 6. Engineering Execution Plan

### Epic G1 — Gateway Backend Completion (Critical) — *the lead epic*

One coordinated backend PR. Owns `gateway/fastapi_app.py`, `gateway/control_plane.py`, `gateway/orchestrator.py`, `cli/main.py`, `core/session_store.py`, `core/permissions.py`. **Do not parallelize across these files** — single owner to avoid the split-remediation regression.

| Task | Objective | Files | Effort | Risk |
|---|---|---|---|---|
| G1-T1 | Session ownership: store `owner_token_hash=sha256(bearer)` at session create; enforce 403 on get/messages/stream; scope `GET /sessions` to owner (C1) | fastapi_app.py, control_plane.py, session_store.py | Medium | High |
| G1-T2 | Authoring backend: `POST /authoring/{name}/generate` (LLM diff), real `apply`/`revert` filesystem handlers + skill re-index (C2) | fastapi_app.py, cli/main.py, control_plane.py | Large | High |
| G1-T3 | `POST /config/limits` (spend caps) + persist allowed folders/commands via existing config write path (H1, H3) | fastapi_app.py, control_plane.py | Small | Med |
| G1-T4 | `POST /sessions/{id}/permissions/always-allow` persisted rule (survives restart); wire Home Allow/Decline to gateway (H4) | fastapi_app.py, permissions.py | Medium | Med |
| G1-T5 | `POST /runtime/mcp/register` MCP registration route (H5) | fastapi_app.py, control_plane.py | Medium | Med |
| G1-T6 | Kennel: skill-listing endpoint + `POST /skills/add` with persistence (H2) | fastapi_app.py, cli/main.py | Medium | Med |
| G1-T7 | Session resume: thread `session_id` through CLI `_resolve_resume_target` **and** gateway `_boot_session_agent`; load history into MemoryManager; non-zero exit on bad id (C3) | cli/main.py, orchestrator.py, session_store.py | Large | Med |
| G1-T8 | `hachi serve`: single process, auto-token, mount `frontend/dist` via `StaticFiles`, open browser (C4) | cli/main.py, fastapi_app.py, README.md | Medium | Med |
| G1-T9 | Dynamic gateway `list_models` via live provider discovery (reuse `hachi models` path) + regression test (H6, L2) | cli/main.py, tests/ | Trivial | Low |

### Epic G2 — Frontend Re-wire & Honesty (High)

After G1 contract lands. Owns `frontend/`. Replace store-only mutations with real gateway calls; surface true success/error; remove fabricated timing.

| Task | Objective | Effort |
|---|---|---|
| G2-T1 | Point skill add/remove, allowed folders/commands, always-allow, settings-save, MCP-add at the new G1 routes; show real error toasts on failure (H1–H5) | Medium |
| G2-T2 | Kennel list from G1-T6 endpoint; persist add; show empty/error states (H2) | Small |
| G2-T3 | Remove hardcoded "re-indexed in 0.3s"; show real re-index result (L4) | Trivial |
| G2-T4 | Recall-at-point-of-use chip in conversation ("recalled N facts") (M6) | Medium |

### Epic G3 — Concurrency, Latency & Reliability (High/Medium)

Owns `core/memory.py`, `core/memory_vault.py`, `core/session_store.py`, `cli/main.py` (turn lock).

| Task | Objective | Effort |
|---|---|---|
| G3-T1 | Move sliding-window compaction LLM call to background job; `prepare_for_turn` returns <100ms (H7) | Medium |
| G3-T2 | Lazy `import chromadb` + parallel/deferred collection init (H8) | Medium |
| G3-T3 | Per-agent turn lock around `chat()` to serialize ChromaDB writes (H9) | Small |
| G3-T4 | Guard `MemoryVault.close()` ordering with `_closed` flag under the RLock; un-skip the masking test (H12) | Small |
| G3-T5 | Batch `SessionStore.log_message` commits while keeping `start_session` synchronous (fixes the FK regression that blocked BE-1) (M1) | Small |
| G3-T6 | Reuse `loop.llm_client` in memory background ops (M2) | Small |
| G3-T7 | SQLite WAL pragma + periodic backup/restore helper (M5) | Small |

### Epic G4 — Security Depth (Medium)

| Task | Objective | Effort |
|---|---|---|
| G4-T1 | Wrap channel webhook inbound text in `guard_untrusted` (H10) | Small |
| G4-T2 | Redact secrets in `/memory/recall` responses (M3) | Small |
| G4-T3 | Re-key channel rate-limiter off transport identity, not `sender_id` (M4) | Small |
| G4-T4 | Sandbox hardening roadmap: namespace/seccomp interim doc + working-dir confinement test (H11) | Medium |

### Epic G5 — Product Polish & Backlog (Low/Medium)

Live-run SSE observability (M11), session selector (M8), editable spend caps UI (M9), approval notifications (M10), memory-on-by-default Lightning (M7), mobile layout (L7), README de-anchor + remove not-live claims (L8), perf micro-opts (L5/L6/L9), `list_skill_models` logging (L3), stray-checkout test hygiene (L1).

### Test-integrity hygiene (fold into Sprint 1)

L1 (exclude sibling `tokoboros` checkout from collection — `pyproject.toml`/`conftest` `norecursedirs`), L2 (model-list regression test), L3 (`list_skill_models` logs on failure).

---

## 7. Parallel Agent Assignment Plan

**Dependency graph:** `G1 (backend contract) → G2 (frontend rewire)`. `G3`, `G4`, `G5` are independent of G1/G2 and of each other. Within G1, tasks share files → **serialize G1 under one owner** (worktree-isolated). Frontend (G2) is a separate file tree → parallel-safe with G3/G4/G5.

| Agent | Model | Specialty | Sprint 1 | Sprint 2 | Sprint 3+ |
|---|---|---|---|---|---|
| **Agent BE** | sonnet | Gateway/backend | G1-T1…T9 (serial, one owner) | — | — |
| **Agent FE** | sonnet | Frontend | G1-T9 contract consume | G2-T1…T4 | G5 (UI: M8/M9/M11) |
| **Agent PERF** | sonnet | Core perf/reliability | G3-T5 (FK-safe batching) | G3-T1…T4, T6, T7 | L5/L6 |
| **Agent SEC** | sonnet | Security | G4-T1 (webhook guard) | G4-T2…T4 | — |
| **Agent CLEAN** | haiku | Test hygiene/trivial | L1, L2, L3, H6 regression test | L4, L8, L9 | — |

**Collision rule:** Agent BE and Agent PERF both touch `core/session_store.py` (G1-T1 vs G3-T5). Sequence: BE-T1 lands the ownership column first, then PERF-T5 batches `log_message` on top. Use worktree isolation and merge BE before PERF.

---

## 8. Sprint Roadmap

| Sprint | Theme | Tasks | Exit gate |
|---|---|---|---|
| **Sprint 1** | Critical: land the coordinated backend + cheap wins | G1-T1…T9, G2-T1…T3, G3-T5, G4-T1, L1/L2/L3 | All five missing routes exist & return 2xx; session-ownership 403 enforced; webhook guarded; suite green |
| **Sprint 2** | Before-release: concurrency, latency, persistence | G2-T4, G3-T1…T4,T6,T7, G4-T2…T4, H11 | `prepare_for_turn`<100ms; no ChromaDB race under 40-concurrent test; recall chip visible |
| **Sprint 3** | Depth & polish | M3, M5, M6, M11, M12, L5, L6 | Live-run streaming; redacted recall; WAL+backup |
| **Sprint 4** | Enhancements | M7, M8, M9, M10, L4, L7, L8, L9 | Editable caps; notifications; mobile; honest README |

---

## 9. Verification Strategy

**Per-task acceptance is in the epic tables; cross-cutting gates below.**

| Gate | Test | Type |
|---|---|---|
| GATE-OWN | Session created by token A returns 403 on get/messages/stream for token B; 200 for A; `GET /sessions` lists only A's | Integration (security) |
| GATE-ROUTES | Each of the five formerly-404 routes (`/authoring/{name}/generate`, `/config/limits`, `/runtime/mcp/register`, `/sessions/{id}/permissions/always-allow`, `/skills/add`) returns 2xx with a valid token and persists its effect across process restart | Integration |
| GATE-AUTHORING | Generate returns a real diff; Apply writes the file & re-indexes; Revert restores prior content (round-trip on a temp `.og`) | Integration |
| GATE-RESUME | `hachi run` ("my name is Alex"), exit, `hachi --continue` ("what is my name?") → response references Alex; bad id → non-zero exit | E2E (CLI) |
| GATE-SERVE | `uv run hachi serve` starts <10s, prints URL, serves built frontend (not Vite), no second terminal | E2E |
| GATE-INJECT | Webhook inbound injection payload is wrapped with untrusted marker before LLM dispatch | Unit (security) |
| GATE-CONCUR | 40 concurrent `chat()` turns produce no ChromaDB `AttributeError`/corruption; health endpoint stays responsive | Load/concurrency |
| GATE-LATENCY | `prepare_for_turn()` returns <100ms with a mocked 2s compaction LLM; time-to-first-turn measured before/after lazy ChromaDB | Performance |
| GATE-MODELS | `GET /models` includes the configured running model; regression test pins it | Integration |
| GATE-GREEN | Full suite green with the sibling `tokoboros` checkout excluded; the MemoryVault close-race skip removed and passing | Regression |
| GATE-NO-REGRESS | `ruff check`, `ruff format --check`, `mypy src`, `npm run typecheck`, `check:api` (schema diff clean after `gateway-openapi` re-export) | CI |

**Per fix:** unit + the relevant integration gate above + a regression test named for the finding ID. Security fixes (C1, H10, M3) require an adversarial test (the attack must be demonstrably blocked). Performance fixes (H7, H8, M1, M12) require a before/after benchmark assertion.

---

## 10. Release Readiness Recommendation

**Do not release a public, multi-user, or network-exposed build.** The product is **acceptable for a loopback, single-user, single-token developer preview** *only after* Sprint 1 closes the five broken live flows and the session-ownership gap (because even single-user, the broken Authoring/Settings/MCP/kennel flows make the preview misrepresent itself).

**Recommended path:**
1. **Sprint 1 is mandatory** before any external eyes. Its exit gate (GATE-OWN + GATE-ROUTES + GATE-AUTHORING + GATE-MODELS + GATE-GREEN) converts the score from 59 → an estimated ~75.
2. **Sprint 2** is mandatory before any multi-user or exposed deployment (GATE-INJECT, GATE-CONCUR, GATE-LATENCY).
3. **Process fix:** never again merge a frontend PR whose backend routes are not on `main`. Add a CI check that greps frontend `fetch`/route calls against the exported OpenAPI `paths` and fails on any frontend-called path that the gateway does not serve. This single check would have caught the entire H1/H2/H4/H5 + C2 regression class.

**Bottom line:** the engine is sound and the security/perf/backend remediation was real. The remaining work is concentrated in one coordinated backend PR plus its frontend rewire. Land that, and Hachi crosses from "demos well" to "works."
