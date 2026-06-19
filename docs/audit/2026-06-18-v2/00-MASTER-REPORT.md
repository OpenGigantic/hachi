# Hachi — Product Quality Audit v2 (Re-Verification): Master Report

**Date:** 2026-06-18 (second pass, same day as v1)
**Audit lead:** Product Owner / QA Lead / Principal Architect / Eng Program Manager (consolidated)
**Method:** 5 independent QA agents (Black-Box, White-Box, UX/Product, Security/Reliability, Performance/Scalability) re-run over the **current** working tree, each tasked to (a) re-verify every v1 finding against live code and tag it `FIXED / PARTIAL / CHANGED / STILL-OPEN`, and (b) hunt new issues in the surfaces that changed since v1. Anchor findings reproduced first-hand (CLI crash run live; CI job and config shape read directly).
**Scope:** Backend engine (`src/hachi`), FastAPI gateway, Typer CLI, the **new** `src/hachi/client/` REST client, and the untracked Vite/React SPA (`frontend/`, ~60 files). **Excluded:** the in-flight Cockpit TUI (`src/hachi/tui/cockpit/**`) — owned by a concurrent session; audited only at contract level.

> Sub-reports (full evidence, file:line): [01 Black-Box](01-black-box.md) · [02 White-Box](02-white-box.md) · [03 UX & Product](03-ux-product.md) · [04 Security & Reliability](04-security-reliability.md) · [05 Performance & Scalability](05-performance-scalability.md)
> Downstream: [06 PO Decisions](06-product-owner-decisions.md) · [07 Execution Plan](07-execution-plan.md) · [08 Verification Strategy](08-verification-strategy.md) · [09 Release Readiness](09-release-readiness.md)

> **⚠ Audit-window note (the tree moved under us).** While this audit ran, the concurrent session edited `frontend/package.json`, `.github/workflows/ci.yml`, and `CLAUDE.md`. Most consequentially, **WB-001 was partially remediated mid-audit**: `check:api`/`generate:api` scripts and the `openapi-typescript` dep were added and the phantom-file lint step was removed. The residual blocker remains (the SPA is still untracked, so a clean CI checkout has nothing to verify) — but the v1 "nonexistent scripts" framing is now out of date. Sub-reports 01–05 are point-in-time snapshots captured before this change; **where they conflict with this note, this note is authoritative.** This is itself a live demonstration of the audit's thesis: a contract guard was being rewritten, untested against a clean checkout, mid-flight.

---

## 1. Executive Summary

**The headline of this second pass is not a finding — it is a pattern. Between v1 and v2, almost none of the critical issues were actually fixed; they were *dressed up*.** The CLI config commands were rewritten to look correct and still crash. A new unit test was added for them that mocks a config shape the real object does not have, so it passes green while the command fails 100% of the time. The CI job that is supposed to guard the frontend contract was pointed at a frontend that does not exist — and (see the audit-window note above) was being hot-patched even as this audit ran, still untested against a clean checkout. This is the same disease in three places: **the surface was made to look done; the substance was not closed, and the green checkmarks are lying about it.**

For a product whose entire pitch is *"OpenClaw for people who got burned by OpenClaw"* — i.e. **trust** — a verification system that reports success it cannot back up is the most dangerous condition the project can be in. It is more dangerous than the individual bugs, because it means the team (and any future AI agent) cannot tell what is real.

The good news is unchanged from v1 and remains true: **the backend engine is genuinely mature, and the fixes are shallow.** The new `src/hachi/client/` REST client is clean and well-built. The new gateway write-routes correctly carry bearer auth and Pydantic validation. A handful of real improvements landed (shared-services caching, lazy CLI imports, an app-wide focus-ring, a working standalone approvals POST). None of these are wasted. But **no full user journey was closed, no critical security hole was patched, and the cost/moat regression is byte-for-byte unchanged.**

### What actually changed since v1 (verification delta)

| Domain | Prior findings | FIXED | PARTIAL | STILL-OPEN | Notable |
|---|---|---|---|---|---|
| Black-Box | 36 (4 now obsolete: deleted TUI) | 0 | ~6 | ~26 | BB-001/002 **reproduced crashing live** (v1 had them as suspected-fixed) |
| White-Box | 31 | 0 | 4 | 26 (2 worse) | New **false-green test** masks the config crash (WB-N07); WB-001 partially remediated mid-audit |
| UX / Product | 26 | 0 | 4 | 21 (1 changed) | Sub-components wired; **0 journeys closed** |
| Security / Reliability | 30 | 0 | 1 | 29 | Every SEC/REL finding still open; NO_AUTH blast radius **grew** |
| Performance | 26 | 1 | 2 | 23 | PERF-001 unchanged; `.og` library bloated ~2.5× toward the token-crossover |

**Net: 1 finding cleanly fixed (PERF-024) out of ~149 re-verified.** The rest is partial scaffolding or untouched.

### Scorecard (v2, with v1 delta)

| Dimension | v1 | **v2** | Why it moved |
|---|---|---|---|
| Backend architecture & engine | 78 | **78** | Stable; clean new `client/` module is a net positive offset by no refactors landing |
| Security posture (backend) | 70 | **68** | No criticals fixed; NO_AUTH kill-switch now also disables config/memory/capability **writes** (SEC-N01) |
| Reliability | 72 | **70** | REL-001 open; new web handlers block the loop; new wrong-session memory routing (WB-N03) |
| Performance & scalability | 64 | **62** | PERF-001 unchanged; `.og` corpus grew to ~24k tok (toward the ~30k crossover); +small for PERF-024/lazy imports |
| Web — functional completeness | 40 | **42** | A few sub-wirings (authoring apply/revert, standalone approvals POST, config persist); no journey closed |
| Web — accessibility | 28 | **38** | Real progress: app-wide `:focus-visible` ring; clickable-`div`/modal-semantics gaps remain |
| UX / product trust integrity | 45 | **43** | New spend **double-count** (UX-N01); in-context leash no-op **confirmed**; memory writes faked |
| Testing & CI integrity | 48 | **44** | CI still red **and** a new false-green test now hides a live crash — net worse |
| Documentation | 70 | **70** | Unchanged |

**Overall Product Health: ~60 / 100** (v1: 61). Essentially flat — because the remediation was cosmetic.

**Release Readiness:**
- **Full product (incl. web UI): 32 / 100 — NO-GO.** Trust-eroding fakes (now including spend double-count + faked memory persistence + a fabricated auto-decline countdown), broken CI, and a confirmed CLI config crash.
- **Backend + CLI engine (no web UI): 64 / 100 — CONDITIONAL GO** after Sprint 1, with a **new explicit gate: delete the false-green config test and prove the command runs.**

See [09 — Release Readiness](09-release-readiness.md).

### Top risks

1. **"Green" is not green.** CI still can't go green for the frontend contract (WB-001 — scripts were fixed mid-audit, but the SPA is uncommitted so a clean checkout has no frontend to verify); strict lint/mypy are non-blocking on `main`; a unit test passes for a command that always crashes (WB-N07); the frontend has zero test tooling. We cannot currently prove any change is safe — **fix this first or every other fix is unverifiable.**
2. **Trust collapse on first failure.** A self-hoster's gateway hiccups and the UI shows fabricated healthy data (UX-018), a fake "validated" key (UX-001), a fake spend number (UX-N01/WB-009), a fabricated "auto-declines in 30s" countdown (BB-N03), and memory edits that vanish on reload (BB-N04). The exact audience we target will notice and leave.
3. **The leash is decorative where it matters most.** The in-context allow/deny prompt — the control the user actually uses during a task — never calls the backend (BB-N02 / WB-007 / UX-006). The standalone `/approvals` screen does POST; the inline one does not.
4. **Supply-chain arbitrary execution.** `hachi pack install` from the open web → ZIP zip-slip (SEC-001) + unsigned (SEC-007) + MCP server command executed at boot with no tier gate (SEC-005).
5. **Cost & moat regression, now worse.** Loaded skills re-billed every turn (PERF-001) is unchanged, *and* the shipped `.og` library has grown ~2.5× (≈9k → ≈24k tokens), pushing it toward the break-even point where the token-saving moat stops paying off.

### Critical blockers (the 10, re-verified)

All ten v1 criticals are **still open** — with one caveat: UX-023 is downgraded to High (focus-ring foundation landed) so UX-009 remains the 10th critical, and **WB-001 is now PARTIAL** (scripts fixed mid-audit, but the SPA is uncommitted so CI still cannot go green). The substance of all ten remains unshipped:

`BB-001`/`WB-N06` (config-get crashes) · `BB-002` (config-set crashes on safety/budget keys) · `WB-001` (CI hard-fails) · `UX-001`/`BB-006` (fake key validation) · `UX-018`/`BB-004` (silent fixture fallback) · `SEC-001` (pack ZIP zip-slip) · `SEC-005` (MCP arbitrary exec at boot) · `PERF-001` (re-billed history / moat) · `PERF-025` (unvirtualized transcript) · `UX-009` (no governance status bar).

**Plus one new must-fix that is the root of why the above survived:** `WB-N07` — a unit test that mocks a non-existent config shape, turning a 100%-reproducible crash green.

---

## 2. Findings by Severity (deduplicated across agents)

### 🔴 Critical (10 + 1 root-cause)

| ID(s) | Title | Area | Status | Evidence |
|---|---|---|---|---|
| **BB-001 / WB-N06** | `hachi config-get` crashes — reads `cfg.safety_tier` / `.max_tokens` / `.session_cost_cap_usd`, none of which exist on `ResolvedConfig` | CLI | STILL-OPEN (reproduced live) | `cli/main.py:2205-2213` → `AttributeError` |
| **BB-002** | `hachi config-set safety_tier/max_tokens/session_cost_cap_usd` fails — the safety & budget controls | CLI | STILL-OPEN (live) | `cli/main.py:2258,2260,2262` |
| **WB-N07** | Unit test mocks a flat config shape the real object lacks → false-green over a 100% crash | Test integrity | NEW | `tests/test_config_get_set.py:20-34` |
| **WB-001** | `frontend-contract` CI cannot go green. *Mid-audit* the concurrent session added `check:api`/`generate:api`/`openapi-typescript` and dropped the phantom lint step — but `frontend/` (incl. generated `schema.d.ts`) is still **untracked**, so a clean CI checkout has no frontend to `npm install`/verify | CI/CD | PARTIAL / CHANGED (remediated mid-audit; residual: SPA uncommitted) | `ci.yml:152-162`, `frontend/package.json:11-12,26`; `git status` → `?? frontend/` |
| **UX-001 / BB-006** | SetupWizard shows green "Key looks good — validated" for any ≥12-char string; real validator exists but is never called | Trust/Web | STILL-OPEN | `SetupWizard.tsx:51,236-240` |
| **UX-018 / BB-004** | Loaders silently fall back to seeded fixtures — fabricated healthy data when gateway is down | Trust/Web | STILL-OPEN | `__root.tsx:17`, `domains/work.ts:201-203` (+9 sibling loaders) |
| **SEC-001** | Community-pack **ZIP** extraction zip-slip + bomb (tar path hardened, ZIP path bypasses `safe_extract`) | Security | STILL-OPEN | `core/community_packs.py:243-264` |
| **SEC-005** | MCP server command/args/env config-controlled, web-installable, not tier-gated → arbitrary exec at boot | Security | STILL-OPEN | `core/mcp_client.py:447-498`, `gateway/orchestrator.py:1239-1260` |
| **PERF-001** | Loaded skills & tool results re-billed at full input price every turn (only 2 of 4 cache breakpoints used) | Perf/Cost/Moat | STILL-OPEN (byte-identical) | `core/llm.py:247-260`, `core/loop.py:704` |
| **PERF-025** | Chat transcript unvirtualized + whole `messages` array remapped on every SSE delta | Perf/Web | STILL-OPEN | `frontend/.../conversation.tsx:746,570`, `domains/chat.ts:134-146` |
| **UX-009** | No governance status bar (model / session / live-cost / connection) on any route | UX/Trust chrome | STILL-OPEN | `frontend/src/routes/__root.tsx:22-49` |

### 🟠 High (deduplicated)

**Frontend trust & completeness**
- `UX-N01 / WB-009` — **Web spend double-counts**: gateway emits *cumulative* `usage`; the SPA *adds* it per event. Corrupts the single most important governance number. `domains/chat.ts:236-239` vs `core/loop.py:757-760`. (The backend `/spend` endpoint is correct.)
- `BB-N02 / WB-007 / UX-006` — **In-context leash allow/deny is a no-op** — clears the UI, never POSTs (`domains/chat.ts:395-414`). The standalone `/approvals` screen *does* POST (PARTIAL fix), but the inline prompt — the one users actually hit mid-task — does nothing. No countdown, no decline reason, "Always allow" fires with no confirm.
- `BB-N03` — **Fabricated auto-decline countdown** on live approvals: `expiresAt`/`totalMs` hardcoded to 0, UI shows a counting-down "auto-declines in {time}" safety promise that doesn't exist. `domains/approvals.ts:117-118`, `approvals.tsx:242-280`.
- `BB-N04` — **Memory writes are local-only + faked**: add/edit/delete and proposed-change approve/reject toast "Hachi remembers it" but never call the gateway — **even though `POST`/`DELETE /memory/facts` exist and work** (`fastapi_app.py:840-855`). Edits vanish on reload. `domains/memory.ts:8-11`.
- `BB-N01` — **`--resume` / `-c` advertised but no-op**: top-level help and `run` advertise resume; it silently starts a fresh session (`cli/main.py:1842-1845`); the web resume is a toast-only stub too.
- `UX-017 / BB-005` — `/run` "watch live" is a scripted simulation, self-labeled in code (`run.tsx:7-12`, `store.ts:1299-1312`).
- `BB-010 / UX-014` — Authoring "Generate" inert / patch summary hardcoded; "Browse the kennel" + operator skill-edit are dead buttons (`authoring.tsx:455-478`, `skills.tsx:202`). *(apply/revert are now real — PARTIAL.)*
- `UX-002` — Wizard hard-codes Anthropic (no provider choice) and never sets a spend cap (`SetupWizard.tsx:209`).
- `UX-008` — Settings: read-only `$1.00` cap, no Save button, persistent "unsaved changes" badge (`settings.tsx:540,64,153`). *(safety/provider now persist — PARTIAL.)*
- `UX-011` — Spend (the moat visualization) buried as a tab inside `/work`, not a nav item (`work.tsx:834`).
- `BB-003` — Web SPA desktop-only; zero width-based responsive logic. *(PO-deferred — §5.)*

**Accessibility (High; focus-ring foundation now landed → UX-023 PARTIAL)**
- `BB-007` — Modals lack `role="dialog"`/`aria-modal`/focus-trap/Esc (SetupWizard, KennelConsent, Work detail).
- `BB-008` — Clickable `<div>`/`<g>` rows not keyboard-focusable, no role (`work.tsx:1030-1044`, `og.tsx:191-198`).
- `BB-009` — Light-mode muted text fails WCAG AA; hardcoded amber pill (`tokens/colors.css:12`).
- `BB-011` — Icon-only buttons incl. destructive delete have no accessible name (`memory.tsx:120,123`). *(PARTIAL.)*

**Security & reliability**
- `SEC-N01` — **NEW**: `HACHI_GATEWAY_NO_AUTH=1` (or a no-token embedding) now disables auth on the **write** routes added since v1 (capability toggle, memory write/delete, config/setup apply), widening the kill-switch blast radius. `fastapi_app.py:299-304,840-855,887-894`.
- `SEC-003` — SSRF in dynamic `http_request` tool: no `net_guard`, follows redirects, leaks auth header (`core/integrations.py:244-270`).
- `SEC-004` — `browse_web`/Playwright validates only the initial URL; redirects reach internal hosts (`tools/browser_tools.py:49`, `core/browser.py:193`).
- `SEC-002` — Untrusted-content framing is detection-gated; scanner-evading injection reaches the model unframed (`core/untrusted_content.py:42-46`).
- `SEC-007` — Pack install enforces no signature/hash; the verifier exists but is never called (`core/community_packs.py:299-389`, `pack_index.py:217-225`).
- `REL-001` — Synchronous 180s approval wait blocks anyio worker threads → gateway thread-pool exhaustion under load (`core/approvals.py:309-357`).
- `WB-013` — Silent `except` masks credential/memory write failures; `Fts5OnlyMemoryProvider` still fabricates a uuid on write failure (`memory_provider.py:236-239`). *(default vault now fails loudly — PARTIAL.)*

**Performance**
- `PERF-N01 / PERF-011` — New + existing read handlers are `async def` doing blocking Chroma/graph/file work with no `to_thread` → event-loop stalls across ~8 endpoints (`fastapi_app.py:810-857,822,827,857,882`).
- `WB-N03` — New multi-session handlers (`get_skills/get_memory/get_memory_recall/get_og_graph/add_memory_fact/delete_memory_fact`) resolve `next(iter(self._agents.values()))` → **memory facts written to / recalled from the wrong session** under concurrency (only `get_spend` honors `session_id`).
- `PERF-019` — ChromaDB default all-MiniLM ONNX embedding → measured ~4s cold start + hidden network dependency (`memory_vault.py:121-148`).
- `PERF-015` — SessionStore single connection + global lock defeats WAL reader concurrency (`session_store.py:49-51`).
- `PERF-012` — `/memory` fetches ALL docs from unbounded `core_memory`, no pagination/cache (`gateway/orchestrator.py:741-760`).
- `PERF-022` — CLI still eagerly imports `anthropic`+`uvicorn`+`cryptography`+`keyring` at module load (measured ~4.1s `import`, ~4.6–4.9s `--help`). *(chroma/textual/fastapi/onnx now lazy — PARTIAL.)*
- `PERF-016` — Cross-session search uses unindexable leading-wildcard `LIKE %q%` (`session_store.py:277-280`).
- `PERF-005` — SSE active stream still busy-polls a synchronous lock-taking read on the loop. *(idle poll now 1s vs 50ms — PARTIAL.)*

**CI/CD & infra**
- `WB-004` — `docker-compose.yml:21-24` references a non-existent `frontend/Dockerfile`.
- `SEC-011` — CI `security` job runs only Hachi's own auditor — no dependency-CVE scan / SAST / Dependabot anywhere.
- `WB-002/003/005` — Release ships PyPI-only (no Docker image/frontend/SBOM); container runs as root on `0.0.0.0`; strict mypy/lint non-blocking on `main`.

### 🟡 Medium (by theme) — full detail in sub-reports 01–05

- **Backend correctness/durability:** non-durable func-jobs discarded on restart (`WB-019`, `queue.py:591-603`) — every orchestrator-enqueued job (prefetch/title/sync) is non-durable; per-turn constant `semantic_search("important context")` busts the prompt cache (`WB-012`); global `.og` snapshot namespace still keys by bare name (`WB-020` PARTIAL); O(n) event scan per poll (`PERF-006`); `og_graph()` recomputes deps+token estimates over all nodes per request (`PERF-N02`).
- **Encoding:** `client/models.py` is BOM-prefixed with 10 mojibake em-dashes (`â€"`) → Cockpit renders garbled placeholder cells (`WB-N01`).
- **Config & secrets:** 110 raw `os.environ` reads, no typed Settings model (`WB-016`, now worse — `cli/main.py` 2,886 LOC); cwd-relative `.env` re-read per client build (`PERF-N06`); `secrets --set KEY=value` guidance broken on Windows/non-TTY (`BB-033`).
- **Frontend:** zustand store ships ~250 lines of hardcoded mock chat scripts inside live action logic (`WB-011`, `store.ts:1066-1310`); `partialize` persists only `{operator,theme}`; dev-only "Preview reconnecting" + static "Recents" shipped; `Markdown` not memoized → O(n²) re-parse while streaming (`PERF-026`).
- **Security (medium):** channel webhook fail-OPEN on empty allowlist (`SEC-006`); rate limiter keyed by spoofable `sender_id`, never reaped → slow OOM (`REL-N02`); plaintext memory/transcripts at rest (`SEC-010`); `NO_AUTH` not host-gated (`SEC-013`); Docker root + `0.0.0.0` (`SEC-012`).
- **Reliability:** OpenAI stream facade scans events from sequence 0 with `limit=10000` per stream start (`PERF-N03`); no SQLite integrity-check/backup (`REL-008`); poll-based cancel (`REL-012`).
- **Tooling/deps:** `pytest>=9.0.3` likely-unsatisfiable floor (`WB-022` PARTIAL — lockfile now committed); frontend has no `openapi-typescript` despite the documented `generate:api` loop (`WB-024`).

### 🟢 Low (by theme)

Polish/hardening: REST client interpolates path params unescaped (latent, not exploitable today — `SEC-N02`); SSE parser front-slices buffer per line → O(n²) on a large frame (`PERF-N05`); world-readable temp script (`SEC-016`); inlined frontend token (`SEC-018`); two near-identical pack systems (`BB-012`); permission-tier names drift across CLI/web/docs; `SECURITY.md` unlinked from README; canonical `web-ui-design-brief.md` still describes the retired teal/ouroboros brand; `doctor` buries 23 skill-lint errors with no verdict (`BB-013`). Full list in sub-reports.

---

## 3. Findings by Category

| Category | Count (deduped) | Headline items |
|---|---|---|
| **Functional bugs** | ~16 | CLI config crashes (BB-001/002); false-green test (WB-N07); spend double-count (UX-N01); leash no-op (BB-N02); faked memory writes (BB-N04); fabricated countdown (BB-N03); wrong-session routing (WB-N03) |
| **UX issues** | ~22 | No status bar (UX-009); spend buried (UX-011); settings no Save/cap (UX-008); wizard no provider/cap (UX-002) |
| **Performance** | ~27 | Re-billed history (PERF-001); unvirtualized transcript (PERF-025); blocking async handlers (PERF-N01/011); embedding cold start (PERF-019); CLI startup (PERF-022) |
| **Security** | ~18 | Pack zip-slip (SEC-001); MCP exec (SEC-005); SSRF (SEC-003/004); unframed injection (SEC-002); unsigned packs (SEC-007); NO_AUTH blast radius (SEC-N01) |
| **Reliability** | ~13 | Sync approval wait (REL-001); wrong-session memory (WB-N03); silent except (WB-013); non-durable func-jobs (WB-019); limiter OOM (REL-N02) |
| **Accessibility** | ~9 | Non-focusable rows (BB-008); modal semantics (BB-007); contrast (BB-009); unlabeled controls (BB-011) — focus-ring foundation now landed |
| **Architecture / tech debt** | ~14 | Monoliths worse (WB-016); no central Settings; global `.og` namespace; mock scripts in live store (WB-011); encoding corruption (WB-N01) |
| **CI/CD & infra** | ~9 | Broken contract job (WB-001); **false-green test (WB-N07)**; phantom compose Dockerfile (WB-004); no dep-CVE/SAST (SEC-011) |
| **Trust integrity (cross-cut)** | ~9 | Fake validation (UX-001); silent fixtures (UX-018); fake spend (UX-N01); faked memory (BB-N04); fabricated countdown (BB-N03); simulated /run (UX-017) |
| **Missing features / product gaps** | ~12 | See §4 |

---

## 4. Missing Product Capabilities (Product Gap Analysis)

For the "burned by OpenClaw / disillusioned self-hoster" ICP:

**Expected-and-absent (table stakes):**
1. **Set & enforce a spend cap from the UI** — the #1 anti-bill-shock pitch is read-only (`$1.00`) and unconfigurable (UX-002/008). *Highest-leverage gap.*
2. **Trustworthy spend numbers** — currently double-counted (UX-N01). Fix before promoting Spend to a nav item.
3. **Provider freedom at setup** — Ollama / OpenAI-compatible / OpenRouter. The "escape Claude-Code lock-in" argument is missing from the wizard (UX-002).
4. **Honest failure states** — error/loading/empty/stale instead of silent fixture substitution (UX-018).
5. **Real session resume + export** — resume is a no-op on the CLI (BB-N01) and a toast-only stub on the web.

**Incomplete differentiators (the moat, half-built):**
6. **The leash, end-to-end** — the in-context prompt (the one users actually use) must perform a real backend approval with countdown + decline reason (BB-N02/UX-006).
7. **Working memory management** — add/edit/delete/approve must call the *already-existing* `/memory/facts` endpoints (BB-N04).
8. **`.og` token-efficiency, realized** — only pays off once PERF-001 (caching) lands; today it is self-defeating *and* the corpus has grown toward the break-even point (PERF-001, §1 risk 5).
9. **Skill authoring + "browse the kennel"** from the library — the hero `save_skill` flow has no working UI entry point (UX-014).
10. **Ambient governance chrome** — model/session/live-cost/connection status bar + first-class Spend (UX-009/011).

**Strategic opportunities:**
11. Plain-language connect flows (Google device-code, Telegram QR) from the integration buttons.
12. Trust artifacts — link `SECURITY.md`, ship an SBOM, surface "what Hachi can/can't do without asking."

---

## 5. Risk Assessment Matrix

| Risk | Source | Likelihood | Impact | Priority |
|---|---|---|---|---|
| "Green" is unreliable (red CI + false-green test + no FE tests) → unverifiable changes | WB-001, WB-N07 | **Certain** | **High** | **P0** |
| Web shows fabricated data / fake "validated" / fake spend on failure → trust collapse | UX-018, UX-001, UX-N01 | **High** | **High** | **P0** |
| CLI config crashes on safety/budget keys | BB-001/002 | **Certain** | Medium | **P0** |
| Pack ZIP zip-slip writes attacker `.og` outside dest | SEC-001 | Medium | **High** | **P0** |
| MCP arbitrary exec at boot via web-installable config | SEC-005 | Medium | **High** | **P0** |
| Cost overrun + moat regression (re-billed history) | PERF-001 | **High** | **High** | **P0** |
| In-context leash is decorative (no-op approve/deny) | BB-N02 | **High** | **High** | **P0/P1** |
| Memory facts written to / recalled from wrong session | WB-N03 | Medium | **High** | **P1** |
| NO_AUTH/no-token disables config+memory+capability writes | SEC-N01 | Medium | **High** | **P1** |
| SSRF via http_request / browser redirects | SEC-003/004 | Medium | **High** | **P1** |
| Unframed prompt injection → unintended tool calls | SEC-002 | Medium | **High** | **P1** |
| Unsigned community packs (supply chain) | SEC-007 | Medium | **High** | **P1** |
| Gateway thread-pool exhaustion under approval flood | REL-001 | Medium | Medium | **P1** |
| Frontend lockups on long transcripts | PERF-025 | **High** | Medium | **P1** |
| Accessibility non-compliance (dev audience + legal) | BB-007/008/009 | **High** | Medium | **P1** |
| Non-durable orchestrator jobs lost on restart | WB-019 | Medium | Medium | **P2** |
| Rate-limiter unbounded growth → slow OOM | REL-N02 | Low | Medium | **P2** |
| Plaintext memory/transcripts at rest | SEC-010 | Low (local model) | Medium | **P2** |

---

## 6. Prioritized Fix List (execution order)

**P0 — Sprint 1 (blockers):**
1. **Fix CI `frontend-contract` to the real SPA** (WB-001) *and* **delete/repair the false-green config test** (WB-N07) — *until "green" means something, nothing else is verifiable.* Make strict lint/mypy blocking on `main`.
2. **Fix `hachi config-get`/`config-set`** to read/write the real config shape (`permissions.tier`, `agent.max_tokens`) and model a real `session_cost_cap_usd` field (BB-001/002/WB-N06). Add a test that runs the actual command.
3. **Patch pack ZIP zip-slip + size/count caps; tar/zip parity** (SEC-001).
4. **Tier-gate + confirm MCP server install/exec** (SEC-005).
5. **Remove silent fixture fallback; add real error/loading/empty/stale states** (UX-018/BB-004).
6. **Real API-key validation in the wizard** (call the existing validator; delete the fake "validated") (UX-001/BB-006).
7. **Cache message-history prefix; use all 4 Anthropic cache breakpoints** (PERF-001).
8. **Virtualize transcript + throttle SSE deltas + memoize items/Markdown** (PERF-025/026).
9. **Add governance status bar** (model/session/cost/connection) (UX-009).
10. **Fix web spend double-count** (set cumulative, don't add) (UX-N01/WB-009) — pairs with #9.

**P1 — Sprint 2 (high):** wire the in-context leash to real approvals + countdown + decline reason (BB-N02/WB-007/UX-006); wire memory add/edit/delete/approve to `/memory/facts` (BB-N04); fix wrong-session routing (WB-N03); real `/run` SSE or remove it (UX-017); finish or hide authoring Generate/kennel (UX-014); spend cap set/enforce + provider choice at setup (UX-002/008); SSRF guards (SEC-003/004); unconditional injection framing (SEC-002); pack signatures (SEC-007); approval wait off the event loop (REL-001); move all blocking read handlers to `to_thread` (PERF-N01/011); host-gate NO_AUTH (SEC-N01/013); stop silent except-swallowing (WB-013); dep-CVE/SAST in CI (SEC-011); fix compose Dockerfile + release artifacts (WB-004/002/003/005); accessibility (focusable rows, dialog semantics, contrast, labels) (BB-007/008/009/011); embedding pre-warm, SessionStore concurrency, /memory pagination, CLI lazy imports (PERF-019/015/012/022).

**P2 — Sprint 3 (medium):** durable func-jobs (WB-019); fix `client/models.py` encoding (WB-N01); remove mock scripts from live store (WB-011); event-poll deque (PERF-006); `/og/graph` caching (PERF-N02); per-turn semantic-search hot path (WB-012); central Settings model (WB-016); search FTS index (PERF-016); webhook fail-closed (SEC-006); rate-limiter reaping (REL-N02); fabricated-countdown fix if not done in P1 (BB-N03); frontend test foundation; docs consistency.

**P3 — Sprint 4 (low/enhancements):** responsive/mobile (BB-003); encryption at rest (SEC-010); monolith refactors (WB-016); pack-system consolidation (BB-012); brand/doc cleanup; provider/tier naming unification; SSE parser + path-escape hardening (PERF-N05/SEC-N02).

---

## 7. The meta-finding: verification integrity

Three independent mechanisms that are supposed to tell the team "this works" are currently lying:

1. **WB-001** — the CI job that guards the frontend contract could not pass: at v1 it ran scripts/files that don't exist; *during this very audit* the concurrent session hot-patched the scripts (`check:api`/`openapi-typescript` now present) — yet the job still cannot be green because `frontend/` is uncommitted, so a clean checkout has nothing to verify. That the contract guard was rewritten mid-audit, untested against a clean checkout, is itself an instance of the disease.
2. **WB-N07** — a unit test for the config commands mocks a `SimpleNamespace` with `safety_tier`/`max_tokens`/`session_cost_cap_usd` as flat attributes; the real `ResolvedConfig` exposes `permissions.tier` and `agent.max_tokens` and has no cost-cap field at all. The test passes; the command crashes 100% of the time. **This is precisely why BB-001/002 survived from v1 to v2 "fixed."**
3. **The frontend has zero test tooling** — no vitest, no testing-library, no integration smoke against the gateway — so every "done" claim about a web journey is unfalsifiable.

**Recommendation:** Sprint 1, Task 1 is not a fix — it is *restoring the ability to know whether fixes worked.* Land it before anything else, and treat any future "it's fixed" with the question: *what test reproduces the failure, and does it run in CI?*

---

*Continue to [06 — Product Owner Decisions](06-product-owner-decisions.md) → [07 — Execution Plan](07-execution-plan.md) → [08 — Verification Strategy](08-verification-strategy.md) → [09 — Release Readiness](09-release-readiness.md).*
