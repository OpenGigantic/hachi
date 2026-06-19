# Hachi — Product Quality Audit: Master Report

**Date:** 2026-06-18
**Audit lead:** Product Owner / QA Lead / Principal Architect (consolidated)
**Method:** 5 independent QA agents (Black-Box, White-Box, UX/Product, Security/Reliability, Performance/Scalability) running in parallel over the full codebase, plus first-hand verification of anchor findings by the audit lead.
**Scope:** Backend engine (`src/hachi`, ~50K LOC, 146 src / 186 test files), FastAPI gateway, Typer CLI, Textual TUI *(in flight — owned by a concurrent session)*, and the new untracked Vite/React web SPA (`frontend/`, 59 files).

> Sub-reports (full detail, with reproduction and file:line evidence):
> - [01 — Black-Box](01-black-box.md) · [02 — White-Box](02-white-box.md) · [03 — UX & Product](03-ux-product.md) · [04 — Security & Reliability](04-security-reliability.md) · [05 — Performance & Scalability](05-performance-scalability.md)
> - Downstream: [06 — Product Owner Decisions](06-product-owner-decisions.md) · [07 — Execution Plan](07-execution-plan.md) · [08 — Verification Strategy](08-verification-strategy.md) · [09 — Release Readiness](09-release-readiness.md)

---

## 1. Executive Summary

Hachi is in an unusual and important state: **the backend engine is genuinely mature, but the product surface is a high-fidelity prototype wearing the costume of a finished product.** That mismatch — not the engine — is the central risk.

The 2026-06-14 security/reliability remediation campaign (MR-2…MR-10) materially landed: gateway auth is on by default with a constant-time bearer token, loopback is no longer exempt, there is no `shell=True` anywhere, the job queue is durable (WAL + crash recovery), and the 500 handler does not leak traces. The architecture-review debt items in `docs/strategy/01-architecture-review.md` (concurrent-chat corruption, no streaming, event-loop blocking, sequential tools) are **already fixed in the working tree** — that document is stale and must not be used to drive work.

The problems cluster in three places:

1. **The web SPA fakes things a trust product must not fake.** It silently renders seeded fixtures as real data when the gateway is down; it tells the user their API key is "validated" when it only checked the string length; its "watch live" run view is a scripted simulation; its leash (permission) allow/deny buttons never call the backend; and several "Apply" actions toast success while writing nothing. For a product whose pitch is *"OpenClaw for people who got burned by OpenClaw,"* governance controls that look real but do nothing are worse than honestly-absent ones.
2. **The verification system is itself broken.** The `frontend-contract` CI job references scripts and files that do not exist — it hard-fails on every push (confirmed first-hand). CI has been red, strict mypy/lint are `continue-on-error` on main, the frontend has zero test tooling, and `hachi config-get`/`config-set` crash with raw tracebacks on the safety/budget keys (reproduced live). We cannot currently trust "green."
3. **A focused, fixable set of security and performance hotspots.** Web-fetched community packs become trusted boot-time configuration (skills, HTTP integrations, MCP server commands) with a zip-slip hole, no mandatory signatures, and no tier gate — four findings, one root cause. Performance is undermined by uncached message history (loaded skills re-billed every turn, which also quietly defeats the `.og` token-saving moat) and an unvirtualized chat transcript.

**None of these are deep architectural flaws. Almost all are "finish the wiring," "delete the fake," "add the guard," or "fix the gate."** The foundations are sound; the product is not yet honest about what works.

### Scorecard

| Dimension | Score /100 | Notes |
|---|---|---|
| Backend architecture & engine | **78** | Clean ReAct loop, frozen TurnContext, durable queue, typed runtime events. Some monoliths (`cli/main.py` 2,747 LOC). |
| Security posture (backend) | **70** | Remediation landed; residual risk concentrated in pack supply chain & untrusted-input→tool egress. |
| Reliability | **72** | Durable queue, WAL, cross-process locks. Sync approval-wait can exhaust the thread pool. |
| Performance & scalability | **64** | Sound primitives; ~8 hotspots. `.og` token claim currently self-defeating (PERF-001). |
| Web — functional completeness | **40** | Many stubbed/simulated flows; untracked & uncommitted. |
| Web — accessibility (WCAG 2.1 AA) | **28** | Systemic: no focus rings, no dialog semantics, unlabeled controls, sub-AA contrast, desktop-only. |
| UX / product trust integrity | **45** | Excellent copy & approvals UX, but hollow controls underneath. |
| Testing & CI integrity | **48** | 186 backend test files (good) vs. broken contract job, zero frontend tests, non-blocking gates. |
| Documentation | **70** | Extensive; some stale (arch review, design brief brand). |

**Overall Product Health: 61 / 100.**

**Release Readiness:**
- **Full product (incl. web UI): 34 / 100 — NOT READY.** Trust-eroding fakes + broken CI + CLI crashes.
- **Backend + CLI engine (no web UI): 68 / 100 — CONDITIONALLY READY** after the CLI config crashes and pack supply-chain criticals are fixed.

See [09 — Release Readiness](09-release-readiness.md) for gates.

### Top risks

1. **Trust collapse on first failure** — a self-hoster runs Hachi, the gateway hiccups, and the UI cheerfully shows fabricated healthy data and a fake "validated" key. The exact audience we target will notice and leave.
2. **Supply-chain RCE-adjacent path** — `hachi pack install` from the open web → zip-slip + unsigned + MCP-command-injection → arbitrary `.og`/MCP config executed at boot.
3. **Red CI / unenforced gates** — we cannot prove any change is safe; "passing" is currently meaningless.
4. **Cost & moat regression** — loaded skills re-billed every turn inflates spend and undercuts the `.og` token-efficiency story that is the product's stated moat.

### Critical blockers (must clear before any release that includes the web UI)

`BB-001`, `BB-002` (CLI config crashes) · `WB-001` (CI hard-fails) · `UX-001/BB-006` (fake key validation) · `SEC-001` (zip-slip) · `SEC-005` (MCP arbitrary exec) · `UX-018/BB-004` (silent fixture fallback) · `PERF-001` (uncached history) · `PERF-025` (unvirtualized transcript) · `UX-009` (no governance status bar) · `UX-023` (no accessibility foundation).

---

## 2. Findings by Severity (deduplicated across agents)

Raw totals across the five agents: **153 findings — 10 Critical · 44 High · 66 Medium · 33 Low.** After cross-agent de-duplication the actionable set is **~118 distinct issues**. Critical and High are listed in full below; Medium/Low are summarized by theme with pointers to the sub-reports (which contain every finding with full evidence).

### 🔴 Critical (10 distinct)

| ID(s) | Title | Area | Evidence |
|---|---|---|---|
| **BB-001** | `hachi config-get` crashes with a raw traceback on every call | CLI | `cli/main.py:2070` (reproduced live) |
| **BB-002** | `hachi config-set` crashes for `safety_tier` / `max_tokens` / `session_cost_cap_usd` — i.e. the safety/budget controls | CLI | `cli/main.py:2113-2123` (reproduced live) |
| **WB-001** | `frontend-contract` CI job runs nonexistent scripts/files → hard-fails every push | CI/CD | `ci.yml:158-173` (verified: no `check:api`/`lint` script; paths don't exist) |
| **UX-001 / BB-006** | SetupWizard shows green "Key looks good — validated" for any ≥12-char string | Trust/Web | `SetupWizard.tsx:51,236` |
| **SEC-001** | Community-pack **ZIP** extraction zip-slip + tar-bomb (tar path hardened, ZIP path missed) | Security | `community_packs.py:243-264` (verified: no containment check) |
| **SEC-005** | MCP server command/args/env are config-controlled, web-installable, not tier-gated → arbitrary exec at boot | Security | `mcp_client.py:447-498` |
| **UX-018 / BB-004** | Loaders silently fall back to seeded fixtures with no error/loading/stale state — fake healthy data when gateway is down | Trust/Web | `__root.tsx:18`, `domains/work.ts:201-202` |
| **PERF-001** | Loaded skills & tool results re-billed at full input price every turn (only 2 of 4 cache breakpoints used) | Perf/Cost | `llm.py:247-260`, `loop.py:704` |
| **PERF-025** | Chat transcript unvirtualized + whole `messages` array remapped on every SSE delta | Perf/Web | `conversation.tsx:746`, `domains/chat.ts:134-166` |
| **UX-009 / UX-023** | No governance status bar (model/session/live-cost/connection) **and** no accessibility foundation (focus rings, dialog semantics) — both are non-negotiable per the design specs and for a dev audience | UX/A11y/Web | `__root.tsx`, `Sidebar.tsx`; ~27 focus/aria gaps across 18 files |

> UX-009 and UX-023 are listed together as the "trust chrome + accessibility foundation" critical pair; they are split into separate epics in the execution plan (E4, E5).

### 🟠 High (deduplicated to ~30)

**Frontend trust & completeness**
- `BB-005 / UX-017` — `/run` "Watch live" is a full simulation, not the real SSE stream (`run.tsx:7` `TODO(live-run)`).
- `WB-007 / UX-006 / UX-007` — Web leash allow/deny is a **no-op**: never POSTs to `/approvals/{id}`, reads wrong event-payload keys; in-context prompt has no countdown; "Always allow" fires with no confirm; Decline captures no reason (`domains/chat.ts:216-225,395-414`, `conversation.tsx:761`, `approvals.tsx:322`).
- `BB-010 / WB-008 / UX-014` — Authoring "Generate" is inert, patch summary hardcoded; `apply`/`revert` are stubs that toast "Patch applied" while writing nothing; "Browse the kennel" + operator skill-edit are dead buttons (`authoring.tsx:455-478`, `control_plane.py:496-504`, `skills.tsx:202`).
- `WB-009` — Web spend **double-counts**: loop emits cumulative `usage`, frontend *adds* it per event (`loop.py:760` vs `chat.ts:227-243`). *(Elevated from Medium: spend correctness is product-central.)*
- `UX-002` — Web wizard hard-codes Anthropic (no provider choice) and never sets a spend cap (`SetupWizard.tsx:209`).
- `UX-008` — Settings has no Validate, no key field, read-only `$1.00` cap, persistent "unsaved changes" badge with no Save button (`settings.tsx:521,594,153`).
- `UX-011` — Spend (the moat visualization) is buried as a tab inside `/work`, not a nav item (`work.tsx:829`).
- `BB-003` — Web SPA is desktop-only; zero width-based responsive logic (`__root.tsx:22-49`). *(PO-deferred — see §3.)*

**Accessibility (folded into E5; tracked as High)**
- `BB-007` — Modals lack `role`/`aria-modal`/focus-trap/Esc (Work detail, SetupWizard, KennelConsent).
- `BB-008` — Clickable `<div>`/`<g>` rows not keyboard-focusable, no role (`work.tsx:1030-1044`, `og.tsx:192-198`).
- `BB-009` — Light-mode muted text fails WCAG AA (measured 3.03–3.42:1; amber pill 3.95:1) (`tokens/colors.css:12-13`).
- `BB-011` — Icon-only buttons incl. destructive delete have no accessible name (`memory.tsx:31-47`).

**Security**
- `SEC-003` — SSRF in dynamic `http_request` integration tool: no `net_guard`, follows redirects, leaks auth header (`integrations.py:244-270`).
- `SEC-002` — Untrusted-content framing is detection-gated; scanner-evading injection reaches the model unframed (`untrusted_content.py:42-46`).
- `SEC-004` — `browse_web`/Playwright validates only the initial URL; redirects reach internal hosts (`browser_tools.py:49`, `browser.py:193`).
- `SEC-007` — Community-pack install does not enforce signature/hash (empty `sha256s` passes silently).

**Reliability**
- `REL-001` — Synchronous 180s approval wait blocks anyio worker threads → gateway thread-pool exhaustion under load (`approvals.py:309-340`). *(Couples with PERF-011.)*
- `WB-013` — Silent `except Exception` masks credential-store & memory-write failures; `memory_provider.py:236` returns a fake uuid on write failure.

**CI/CD & release**
- `WB-004` — `docker-compose.yml:21-24` frontend service references a non-existent `frontend/Dockerfile`.
- `WB-002/003/005` — Release ships PyPI-only (no Docker image / frontend / SBOM); container runs as root on `0.0.0.0:8787`; strict mypy/lint non-blocking on main.

**Performance**
- `PERF-011` — `/memory`, `/og/graph`, `/skills` are `async def` calling blocking work directly (no `to_thread`) → event-loop stalls (`fastapi_app.py:810-830`).
- `PERF-005` — SSE endpoints busy-poll a synchronous, lock-taking read every 50 ms on the asyncio loop (`fastapi_app.py:436-469`).
- `PERF-019` — ChromaDB uses default `all-MiniLM` ONNX embedding (no `embedding_function`) → multi-second cold start + hidden network dependency (`memory_vault.py:121-148`).
- `PERF-015` — SessionStore funnels all reads+writes through one connection + global lock, defeating WAL reader concurrency (`session_store.py:49-51`).
- `PERF-012` — `/memory` fetches ALL docs from unbounded `core_memory`, no pagination/cache (`orchestrator.py:741-760`).
- `PERF-022` — CLI eagerly imports the full runtime (TUI, orchestrator, Anthropic, Chroma) at module load; even `--help`/`doctor` pay it (`cli/main.py:28-64`).
- `PERF-016` — Cross-session search uses unindexable leading-wildcard `LIKE %q%` (`session_store.py:273-281`).

### 🟡 Medium (66 raw → ~50 distinct) — by theme

- **Backend durability/correctness:** durable queue discards func-only jobs on restart so every orchestrator-enqueued job (prefetch/title/sync) is non-durable (`WB-019`); per-turn constant `semantic_search("important context")` busts the prompt cache in the hot path (`WB-012`, also Perf); global `.og` node namespace — same-named nodes silently overwrite (`WB-020`); event poll is O(n) over a 2000-element list (`PERF-006`).
- **Config & secrets:** 110 raw `os.environ` reads, no typed Settings model (`WB-016`); cwd-relative `.env` loading; credential error message recommends `hachi secrets --set KEY=value` but `--set` takes a bare KEY and prompts/aborts non-TTY (`WB-025`, `BB-033`); provider choices contradict across boot banner / CLI setup / web wizard.
- **Frontend:** zustand store ships ~400 lines of hardcoded mock chat scripts inside live action logic (`WB-011`); dev-only "Preview reconnecting" action shipped; static CommandPalette "Recents"; KennelConsent permissions hardcoded.
- **Security (medium):** generic channel webhook fail-OPEN on empty allowlist in the gateway path (`SEC-006`); CI `security` job runs only Hachi's own auditor — no dep-CVE scan / SAST (`SEC-011`); memory/transcripts plaintext at rest (`SEC-010`); `HACHI_GATEWAY_NO_AUTH=1` disables auth on all routes regardless of bind host (`SEC-013`); Dockerfile root + `0.0.0.0` (`SEC-012`).
- **Tooling/deps:** all Python deps floored with no lockfile committed historically (now `uv.lock`/`package-lock.json` present — verify); `pytest>=9.0.3` likely unsatisfiable floor (`WB-022`); frontend has no `openapi-typescript` despite the CLAUDE.md `generate:api` loop (`WB-024`).
- **Code health:** 4 monoliths >1,700 LOC (`tui/app.py` 3,386 *TUI in flight*; `cli/main.py` 2,747) (`WB-017`); per-session full runtime boot at scale (`WB-028`).

Full Medium list: [01](01-black-box.md)–[05](05-performance-scalability.md).

### 🟢 Low (33) — by theme

Polish and hardening: Windows-hostile `export` onboarding guidance; two near-identical pack systems (`hachi pack` vs `hachi skill pack`) (`BB-012`); minor copy drift; permission-tier names drift across CLI/web/docs; SECURITY.md exists but is unlinked from README/COOKBOOK; canonical `web-ui-design-brief.md` still describes the retired teal/ouroboros brand; latent `Pillow>=10.3.0` floor; unverified `chromadb 1.5.9` / `@tanstack 1.114.x` pins. Full list in the sub-reports.

---

## 3. Findings by Category

| Category | Count (approx, deduped) | Headline items |
|---|---|---|
| **Functional bugs** | ~14 | CLI config crashes (BB-001/002); leash no-op (WB-007); authoring stubs (WB-008); spend double-count (WB-009); webhook fail-open (SEC-006) |
| **UX issues** | ~22 | No status bar (UX-009); spend buried (UX-011); settings has no Save/Validate (UX-008); no countdown on in-context leash (UX-007) |
| **Performance** | ~26 | Uncached history (PERF-001); unvirtualized transcript (PERF-025); blocking async handlers (PERF-011); SSE poll (PERF-005); embedding cold start (PERF-019) |
| **Security** | ~18 | Pack zip-slip (SEC-001); MCP exec (SEC-005); SSRF http_request (SEC-003) & browser redirect (SEC-004); unframed injection (SEC-002); unsigned packs (SEC-007) |
| **Reliability** | ~12 | Sync approval wait (REL-001); silent except swallowing (WB-013); non-durable func-jobs (WB-019) |
| **Accessibility** | ~10 | No focus rings/dialog semantics (UX-023/BB-007); non-focusable rows (BB-008); contrast (BB-009); unlabeled controls (BB-011); desktop-only (BB-003) |
| **Architecture / tech debt** | ~14 | Monoliths (WB-017); no central Settings (WB-016); global `.og` namespace (WB-020); mock scripts in live store (WB-011) |
| **CI/CD & infra** | ~8 | Broken contract job (WB-001); phantom compose Dockerfile (WB-004); no dep-CVE/SAST (SEC-011); release artifacts/SBOM (WB-002/003/005) |
| **Trust integrity** | ~8 (cross-cut) | Fake key validation (UX-001); silent fixture fallback (UX-018); simulated /run (UX-017); hollow apply/resume |
| **Missing features / product gaps** | ~12 | See §4 |

---

## 4. Missing Product Capabilities (Product Gap Analysis)

Features users will expect but that are absent, incomplete differentiators, and strategic opportunities — prioritized for the "burned by OpenClaw / disillusioned self-hoster" ICP:

**Expected-and-absent (table stakes):**
1. **Set & enforce a spend cap from the UI.** The #1 anti-bill-shock pitch is read-only (`$1.00`) and unconfigurable (`UX-002`, `UX-008`). *Highest-leverage gap.*
2. **Provider freedom at setup** — Ollama / OpenAI-compatible / OpenRouter. The core "switch away from Claude-Code lock-in" argument is missing from the wizard (`UX-002`).
3. **Real live-run / "watch live"** — currently simulated (`UX-017`).
4. **Session resume + export** — Resume is a toast-only stub (`memory.tsx:239`).
5. **Working error/loading/empty states** — instead of silent fixture substitution (`UX-018`).

**Incomplete differentiators (the moat, half-built):**
6. **Skill authoring + "browse the kennel"** from the library — the hero `save_skill` flow has no working UI entry point (`UX-014`).
7. **Ambient governance** — model, session, live cost, connection status, and a real spend dashboard surfaced as first-class chrome (`UX-009`, `UX-011`).
8. **`.og` token-efficiency, realized** — the moat only pays off once PERF-001 (caching) and the keyword retriever land; today it is self-defeating on the shipped 9k-token pack (`PERF-001`, `docs/strategy/02-object-graph-eval.md`).
9. **Real command palette** — recents, sessions, slash commands (currently ~4 of ~20 static stubs that don't execute).

**Strategic opportunities:**
10. **Plain-language connect flows** (Google device-code, Telegram QR) surfaced from the integration buttons.
11. **Memory gardener review at scale** — proposed-memory-change review beyond a single hardcoded card.
12. **Trust artifacts** — link `SECURITY.md`, ship an SBOM, surface "what Hachi can/can't do without asking" — concrete signals for the skeptical ICP.

---

## 5. Risk Assessment Matrix

Likelihood × Impact for the highest-priority risks (P0 = fix immediately, P1 = before release, P2 = next sprint).

| Risk | Source | Likelihood | Impact | Priority |
|---|---|---|---|---|
| Web shows fabricated data / fake "validated" on gateway failure → trust collapse | UX-018, UX-001 | **High** | **High** | **P0** |
| Pack zip-slip writes attacker `.og` outside dest | SEC-001 | Medium | **High** | **P0** |
| MCP server arbitrary exec at boot via web-installable config | SEC-005 | Medium | **High** | **P0** |
| Red CI / unenforced gates → unverifiable changes | WB-001 | **High (certain)** | **High** | **P0** |
| CLI config crashes on safety/budget keys | BB-001/002 | **High (certain)** | Medium | **P0** |
| Cost overrun + moat regression (uncached history) | PERF-001 | **High** | **High** | **P0** |
| SSRF via http_request / browser redirects → internal network / metadata | SEC-003, SEC-004 | Medium | **High** | **P1** |
| Unframed prompt injection → unintended tool calls | SEC-002 | Medium | **High** | **P1** |
| Unsigned community packs (supply chain) | SEC-007 | Medium | **High** | **P1** |
| Gateway thread-pool exhaustion under approval flood | REL-001 | Medium | Medium | **P1** |
| Frontend lockups on long transcripts | PERF-025 | **High** | Medium | **P1** |
| Silent except swallowing hides data-loss | WB-013 | Medium | Medium | **P1** |
| Non-durable orchestrator jobs lost on restart | WB-019 | Medium | Medium | **P2** |
| Plaintext memory/transcripts at rest | SEC-010 | Low (local threat model) | Medium | **P2** |
| Accessibility non-compliance (dev audience + legal) | UX-023 cluster | **High** | Medium | **P1** |

---

## 6. Prioritized Fix List (one-line, in execution order)

**P0 — Sprint 1 (blockers):**
1. Fix CI `frontend-contract` job to the real frontend (WB-001) — *unblocks all verification.*
2. Fix `hachi config-get`/`config-set` crashes (BB-001/002).
3. Patch pack ZIP zip-slip + add containment/size limits (SEC-001).
4. Tier-gate + confirm MCP server install/exec (SEC-005).
5. Remove silent fixture fallback; add real error/loading/empty states (UX-018/BB-004).
6. Real API-key validation in wizard + remove fake "validated" (UX-001/BB-006).
7. Cache message-history prefix; use all 4 cache breakpoints (PERF-001).
8. Virtualize transcript + throttle SSE deltas + memoize items (PERF-025).
9. Add governance status bar (model/session/cost/connection) (UX-009).
10. Accessibility foundation: focus rings, dialog semantics, focus trap, labels (UX-023/BB-007/008/011).

**P1 — Sprint 2 (high):** wire real leash allow/deny (WB-007); real `/run` SSE (UX-017); finish authoring apply/revert or hide them (WB-008); spend cap set/enforce + provider choice (UX-002/008); fix spend double-count (WB-009); SSRF guards (SEC-003/004); injection framing (SEC-002); pack signatures (SEC-007); approval wait off the event loop (REL-001/PERF-011); stop silent except-swallowing (WB-013); dep-CVE/SAST in CI (SEC-011); fix compose Dockerfile + release artifacts (WB-004/002/003/005); contrast (BB-009); SSE push, embedding pre-warm, SessionStore concurrency, /memory pagination, CLI lazy imports (PERF-005/019/015/012/022).

**P2 — Sprint 3 (medium):** durable func-jobs (WB-019); event-poll deque (PERF-006); per-turn semantic-search hot path (WB-012); central Settings model (WB-016); remove mock scripts from live store (WB-011); search index/FTS (PERF-016); webhook fail-closed (SEC-006); NO_AUTH host-gate (SEC-013); a11y completion; **frontend test foundation**; docs consistency.

**P3 — Sprint 4 (low / enhancements):** responsive/mobile (BB-003); encryption at rest (SEC-010); monolith refactors (WB-017); pack-system consolidation (BB-012); brand/doc cleanup; provider/tier naming unification.

---

*Continue to [06 — Product Owner Decisions](06-product-owner-decisions.md) for triage rationale, then [07 — Execution Plan](07-execution-plan.md).*
