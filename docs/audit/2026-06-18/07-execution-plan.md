# Hachi — Engineering Execution Plan

**Date:** 2026-06-18 · **Owner:** Engineering Program Manager
**Inputs:** [00 — Master Report](00-MASTER-REPORT.md), [06 — PO Decisions](06-product-owner-decisions.md).

This plan is written so that **parallel engineering agents can begin immediately without further clarification.** Every task carries: Objective · Acceptance Criteria · Files/Components · Effort · Risk · Dependencies.

**Effort key:** S = ≤0.5 day · M = 1–2 days · L = 3–5 days · XL = >1 week.
**Risk key:** how likely the change is to break something else (Low/Med/High).

> **Hard constraint for all agents:** `src/hachi/tui/**` is owned by a concurrent session — **do not edit it.** TUI-tagged findings are handed off separately. Run `uv run ruff check`, `uv run mypy src`, `uv run pytest -q`, and (for frontend) `npm run --prefix frontend typecheck` before declaring a task done (see [08 — Verification Strategy](08-verification-strategy.md)).

---

## Epic Structure

| Epic | Theme | Sprint(s) | Owner agent |
|---|---|---|---|
| **E1** | CLI Stability & Config | 1 | A |
| **E2** | CI/CD & Release Integrity | 1–2 | B |
| **E3** | Frontend Trust Integrity (kill the fakes) | 1–2 | C |
| **E4** | Spend & Governance Visibility | 1–2 | D |
| **E5** | Accessibility (WCAG 2.1 AA) | 1–3 | E |
| **E6** | Security Hardening — Pack Supply Chain & Tool Egress | 1–2 | F |
| **E7** | Reliability & Backend Robustness | 2–3 | G |
| **E8** | Performance & Scalability | 1–2 | H |
| **E9** | Frontend Test & Quality Foundation | 3 | B |
| **E10** | Documentation & Onboarding Consistency | 3 | A |
| **E11** | Backlog / Tech Debt | 4 | rotating |

---

## E1 — CLI Stability & Config (Agent A)

### T1.1 — Fix `hachi config-get` crash *(FIX NOW)*
- **Objective:** `hachi config-get [key]` returns the value (or whole config) without a traceback.
- **Acceptance:** `config-get` with no args prints the resolved config; `config-get safety_tier` prints the value; unknown key prints a friendly error + exit 2; covered by a regression test.
- **Files:** `src/hachi/cli/main.py:~2070`, `src/hachi/core/settings.py`, `src/hachi/core/config_validate.py`.
- **Effort:** S · **Risk:** Low · **Deps:** none.

### T1.2 — Fix `hachi config-set` for safety/budget keys *(FIX NOW)*
- **Objective:** `config-set` works for all documented keys, especially `safety_tier`, `max_tokens`, `session_cost_cap_usd`.
- **Acceptance:** each documented key round-trips (`config-set` then `config-get` returns it); invalid values produce a validation error not a traceback; persisted to the config file; regression test per key.
- **Files:** `src/hachi/cli/main.py:~2113-2123`, `config_validate.py`, `settings.py`.
- **Effort:** S–M · **Risk:** Low · **Deps:** T1.1 (shared config plumbing).

### T1.3 — Fix credential-error guidance & `secrets --set` UX *(BEFORE RELEASE)*
- **Objective:** Error messages point at a command form that actually works; `secrets --set` behaves consistently in non-TTY.
- **Acceptance:** the "missing credential" message matches real CLI syntax; non-TTY path documented/handled (no silent abort); covered by `test_cli` for the message + a non-TTY case.
- **Files:** `cli/main.py:1256,2306`, `core/credential_vault.py`.
- **Effort:** S · **Risk:** Low · **Deps:** none.

---

## E2 — CI/CD & Release Integrity (Agent B)

### T2.1 — Repair `frontend-contract` CI job *(FIX NOW — unblocks everything)*
- **Objective:** The job validates the *actual* frontend and passes on a clean tree.
- **Acceptance:** `ci.yml` no longer references nonexistent scripts/paths (`check:api`, `src/lib/api/*`, `src/types/api.ts`, `src/routes/s.$slug.tsx`); it runs `npm ci`/`npm install`, `npm run typecheck`, and (once T9.x lands) `npm test`; the OpenAPI freshness check either uses a real `generate:api` + `check:api` script added to `package.json`, or is replaced with a diff check of `openapi/hachi-openapi.json`; the job is green on `main`.
- **Files:** `.github/workflows/ci.yml:130-174`, `frontend/package.json` (add `generate:api`, `check:api` scripts + `openapi-typescript` dep, or remove the steps).
- **Effort:** M · **Risk:** Low · **Deps:** none. *Land before merging any other PR so the rest is verifiable.*

### T2.2 — Fix docker-compose phantom Dockerfile *(BEFORE RELEASE)*
- **Objective:** `docker compose up` builds.
- **Acceptance:** either add `frontend/Dockerfile` (multi-stage build → static serve) or remove/repoint the frontend service; `docker compose config` validates; `docker compose build` succeeds in CI.
- **Files:** `docker-compose.yml:21-24`, new `frontend/Dockerfile`, root `Dockerfile`.
- **Effort:** M · **Risk:** Low · **Deps:** none.

### T2.3 — Make strict gates blocking on PR + add dep/SAST scans *(BEFORE RELEASE)*
- **Objective:** Quality gates actually gate.
- **Acceptance:** mypy-strict + ruff are blocking on PR (already partly so — extend coverage to the new gateway/frontend-adjacent modules); add `pip-audit` (Python) and `npm audit --audit-level=high` (frontend) steps; add a SAST step (e.g. `bandit` or CodeQL) to the `security` job; document which are `continue-on-error` on main vs strict on PR.
- **Files:** `.github/workflows/ci.yml`, `pyproject.toml` (mypy file coverage).
- **Effort:** M · **Risk:** Med (may surface latent type/lint errors — budget cleanup) · **Deps:** T2.1.

### T2.4 — Release pipeline: Docker image + SBOM + frontend artifact *(BEFORE RELEASE)*
- **Objective:** `release.yml` ships what self-hosters install.
- **Acceptance:** release publishes a Docker image (non-root user, configurable bind host), a frontend build artifact, and an SBOM (e.g. `syft`); PyPI publish retained.
- **Files:** `.github/workflows/release.yml`, `Dockerfile` (drop root, parametrize host).
- **Effort:** M–L · **Risk:** Med · **Deps:** T2.2.

---

## E3 — Frontend Trust Integrity (Agent C) — *kill the fakes*

### T3.1 — Remove silent fixture fallback; real load states *(FIX NOW)*
- **Objective:** No screen ever shows seeded fixture data as if it were real.
- **Acceptance:** every domain loader distinguishes loading / loaded / empty / error; on gateway failure the UI shows an honest error + retry, never fabricated data; fixtures are confined to tests/Storybook, not shipped load paths; a "disconnected" banner appears when the gateway is unreachable (reuse `ReconnectingBanner`).
- **Files:** `frontend/src/lib/data/domains/*.ts` (esp. `work.ts:201-202`), `frontend/src/routes/__root.tsx:18`, `frontend/src/lib/store/store.ts`, `frontend/src/components/overlays/ReconnectingBanner.tsx`.
- **Effort:** L · **Risk:** Med (touches every screen's data path) · **Deps:** none. *Coordinate file ownership with D/E (see collision map).* 

### T3.2 — Real API-key validation in SetupWizard *(FIX NOW)*
- **Objective:** "Validated" means we actually called the provider/validation endpoint.
- **Acceptance:** the green "validated" state only appears after a real validation round-trip (wizard calls the existing setup/validate path used at Finish); a length check shows at most "format looks right," never "validated"; failure shows a clear error.
- **Files:** `frontend/src/components/overlays/SetupWizard.tsx:51,236-240`, `frontend/src/lib/data/domains/setup.ts`, backend `core/onboarding.py` (validate endpoint).
- **Effort:** M · **Risk:** Low · **Deps:** T3.1 (shared data layer patterns).

### T3.3 — Wire the leash (approvals) allow/deny *(BEFORE RELEASE)*
- **Objective:** Allow/Deny actually approve/deny on the backend.
- **Acceptance:** allow/deny POST to `/approvals/{id}` with the correct payload; the event-payload keys read by the frontend match what the gateway emits (fix the key mismatch); "Always allow" requires a confirm; Decline captures an optional reason; in-context leash prompt shows a live countdown matching the backend auto-decline timeout.
- **Files:** `frontend/src/lib/data/domains/chat.ts:216-225,395-414`, `frontend/src/routes/approvals.tsx:322`, `frontend/src/routes/conversation.tsx:761`, `frontend/src/components/brand/LeashPrompt.tsx:245`; backend `gateway/control_plane.py`/`fastapi_app.py` approvals route + `core/approvals.py` event payload.
- **Effort:** L · **Risk:** Med · **Deps:** T3.1; coordinate with REL-001/T7.1 on approval semantics.

### T3.4 — Make `/run` "watch live" real *(BEFORE RELEASE)*
- **Objective:** Replace the simulation with the real SSE run stream.
- **Acceptance:** `/run` subscribes to the live run/session SSE (reuse conversation stream infra); fake output/pack/spend simulation removed; if no run is active, an honest empty state; the `TODO(live-run)` is resolved.
- **Files:** `frontend/src/routes/run.tsx`, `frontend/src/lib/data/sse.ts`, `frontend/src/lib/data/live.ts`, `frontend/src/lib/data/domains/work.ts`.
- **Effort:** L · **Risk:** Med · **Deps:** T3.1.

### T3.5 — Fix spend double-count *(BEFORE RELEASE)*
- **Objective:** Spend meter reflects true cumulative cost.
- **Acceptance:** frontend treats the loop's `usage` as cumulative (set, not add); meter matches backend `cost.py` totals within rounding; unit test on the reducer.
- **Files:** `frontend/src/lib/data/domains/chat.ts:227-243`; reference `core/loop.py:760`.
- **Effort:** S · **Risk:** Low · **Deps:** T3.1.

### T3.6 — Authoring/kennel: make real or honestly disable *(BEFORE RELEASE)*
- **Objective:** No action toasts success without doing the thing.
- **Acceptance:** "Generate" either calls the real authoring endpoint or is disabled with "coming soon"; `apply`/`revert` either persist (and the backend stub `control_plane.py:496-504` / `cli/main.py:703,714` is implemented) or the buttons are disabled and don't toast success; "Browse the kennel" + operator skill-edit either navigate to a real view or are removed; hardcoded "+3 −2 lines" patch summary replaced with real diff stats or removed.
- **Files:** `frontend/src/routes/authoring.tsx:455-478,528`, `frontend/src/routes/skills.tsx:202,40`, `gateway/control_plane.py:496-504`, `cli/main.py:703,714`.
- **Effort:** L (real) / S (honest-disable) · **Risk:** Low · **Deps:** T3.1. *PO note: ship honest-disable for v1 if the real backend isn't ready; do not ship fake success.*

---

## E4 — Spend & Governance Visibility (Agent D)

### T4.1 — Global governance status bar *(FIX NOW)*
- **Objective:** Persistent chrome shows model, session id, live cost, and connection/stream status.
- **Acceptance:** a status bar in `__root.tsx`/shell shows current model, active session id, running session cost (from live SSE usage), and a connection pill (connected/reconnecting/offline) consistent with T3.1's disconnected state; present on every route.
- **Files:** `frontend/src/routes/__root.tsx`, `frontend/src/components/shell/Sidebar.tsx`, `frontend/src/components/shell/BudgetWidget.tsx`, `frontend/src/lib/store/store.ts`, `frontend/src/lib/data/live.ts`.
- **Effort:** M · **Risk:** Med (shared shell files — coordinate with C/E) · **Deps:** T3.1 (connection state).

### T4.2 — Spend cap: settable & enforced from UI *(BEFORE RELEASE)*
- **Objective:** The anti-bill-shock control actually controls.
- **Acceptance:** Settings exposes an editable `session_cost_cap_usd` with a working Save; value persists via the config endpoint and is enforced by the backend cap (`core/cost.py`/loop); the read-only `$1.00` placeholder is gone; the "unsaved changes" badge clears on Save (add the missing Save button).
- **Files:** `frontend/src/routes/settings.tsx:153,521,594`, `frontend/src/lib/data/domains/config.ts`, `gateway/control_plane.py` config-mutation route, `core/cost.py`.
- **Effort:** M · **Risk:** Med · **Deps:** T1.2 (config plumbing), T4.1.

### T4.3 — Promote Spend to a nav item *(BEFORE RELEASE)*
- **Objective:** The moat visualization is discoverable.
- **Acceptance:** Spend is a first-class sidebar nav route, not a hidden tab in `/work`; deep-linkable.
- **Files:** `frontend/src/components/shell/Sidebar.tsx:18`, `frontend/src/routes/` (new `spend.tsx` or promote existing), `frontend/src/routes/work.tsx:829`.
- **Effort:** S–M · **Risk:** Low · **Deps:** T4.1.

### T4.4 — Provider choice in setup *(BEFORE RELEASE)*
- **Objective:** Provider freedom (Anthropic / OpenAI-compatible / Ollama / OpenRouter) at onboarding.
- **Acceptance:** SetupWizard offers provider selection consistent with what the backend supports (`core/unofficial_providers.py`, `provider_presets`); the chosen provider is persisted and used; matches the CLI setup options (resolve the cross-surface contradiction).
- **Files:** `frontend/src/components/overlays/SetupWizard.tsx:209`, `frontend/src/lib/data/domains/setup.ts`, `core/onboarding.py`, `core/unofficial_providers.py`.
- **Effort:** M · **Risk:** Med · **Deps:** T3.2.

---

## E5 — Accessibility / WCAG 2.1 AA (Agent E)

### T5.1 — Focus-visible foundation *(FIX NOW)*
- **Objective:** Every interactive element has a visible keyboard focus indicator.
- **Acceptance:** global `:focus-visible` styles; remove inline `outline:'none'` overrides; tab through every screen reaches all controls with a visible ring; verified with keyboard-only navigation.
- **Files:** `frontend/src/styles/**`, `frontend/src/components/ui/*` (Button, Input, Switch, etc.).
- **Effort:** M · **Risk:** Low · **Deps:** none.

### T5.2 — Dialog semantics + focus trap + Esc *(FIX NOW)*
- **Objective:** Modals are accessible.
- **Acceptance:** Work-detail, SetupWizard, KennelConsent (and any overlay) have `role="dialog"`/`aria-modal`, focus is trapped while open and restored on close, Esc closes; reuse the pattern already correct in `LeashPrompt`.
- **Files:** `frontend/src/routes/work.tsx:213-238`, `frontend/src/components/overlays/SetupWizard.tsx`, `KennelConsent.tsx`, a shared `Dialog` primitive in `components/ui/`.
- **Effort:** M · **Risk:** Low · **Deps:** none.

### T5.3 — Keyboard-focusable interactive rows *(FIX NOW)*
- **Objective:** Clickable non-button elements are operable by keyboard.
- **Acceptance:** clickable `<div>`/`<g>` rows become `<button>`/`role+tabIndex+onKeyDown`; Enter/Space activate; verified for work rows and OG nodes.
- **Files:** `frontend/src/routes/work.tsx:1030-1044`, `frontend/src/routes/og.tsx:192-198`.
- **Effort:** M · **Risk:** Low · **Deps:** T5.1.

### T5.4 — Accessible names for controls *(FIX NOW)*
- **Objective:** Icon-only buttons, inputs, switches have accessible names.
- **Acceptance:** all icon buttons (esp. destructive delete-fact) have `aria-label`; inputs have associated labels; switches have labels + state; status conveyed by more than color.
- **Files:** `frontend/src/routes/memory.tsx:31-47`, `frontend/src/components/ui/Switch.tsx`, `Input.tsx`, `StatusPill.tsx`, and grep remaining icon buttons.
- **Effort:** M · **Risk:** Low · **Deps:** none.

### T5.5 — Contrast tokens to AA *(BEFORE RELEASE)*
- **Objective:** Text/UI contrast meets WCAG AA.
- **Acceptance:** muted text ≥4.5:1 (or ≥3:1 for large), status pills ≥3:1; fix the measured failures (`#8A8580`, amber pill); verify with a contrast checker across light/dark.
- **Files:** `frontend/src/styles/tokens/colors.css:12-13`.
- **Effort:** S · **Risk:** Low (visual review needed) · **Deps:** none.

### T5.6 — Live regions + completion sweep *(NEXT SPRINT)*
- **Objective:** Async updates announced; full AA pass.
- **Acceptance:** SSE/toast updates use `aria-live`; full keyboard nav of every flow; an `axe`/Lighthouse a11y pass in CI with no critical violations.
- **Files:** frontend-wide; `components/overlays/ToastHost.tsx`, conversation stream.
- **Effort:** L · **Risk:** Low · **Deps:** T5.1–T5.4, T9.x (CI a11y check).

---

## E6 — Security Hardening (Agent F)

### T6.1 — Pack ZIP extraction containment + limits *(FIX NOW)*
- **Objective:** No pack archive can write outside the destination or zip-bomb.
- **Acceptance:** `_extract_zip` resolves each target and rejects any path escaping `dest` (resolve + `is_relative_to`); reject absolute paths and `..`; enforce per-file and total uncompressed size limits; mirror the tar-path hardening; regression tests for a malicious zip (zip-slip + bomb).
- **Files:** `src/hachi/core/community_packs.py:243-264` (and audit the tar path at `:230-240` for parity).
- **Effort:** S–M · **Risk:** Low · **Deps:** none.

### T6.2 — Gate MCP server install/exec *(FIX NOW)*
- **Objective:** Web-installable MCP config cannot silently launch arbitrary commands.
- **Acceptance:** MCP server commands from packs/config require an explicit safety-tier gate + interactive consent before first launch; the command/args/env are surfaced to the user for approval; default-deny for web-sourced MCP entries; regression test that an unapproved MCP entry does not spawn.
- **Files:** `src/hachi/core/mcp_client.py:447-498`, `core/permissions.py`, `device-permissions.og`.
- **Effort:** M · **Risk:** Med · **Deps:** none.

### T6.3 — Mandatory pack signatures/hashes *(BEFORE RELEASE)*
- **Objective:** Close the pack supply chain.
- **Acceptance:** install verifies a signature or per-file hash manifest; an empty `sha256s` no longer passes; unsigned/unhashed installs are refused unless an explicit `--trust-unsigned` override is passed (and logged); regression tests.
- **Files:** `src/hachi/core/community_packs.py`, `core/og_pack.py`, `core/pack_index.py`.
- **Effort:** M–L · **Risk:** Med · **Deps:** T6.1.

### T6.4 — SSRF guard on http_request + browser redirects *(BEFORE RELEASE)*
- **Objective:** Tool egress can't reach internal hosts/metadata or leak auth across redirects.
- **Acceptance:** the dynamic `http_request` integration tool routes through `net_guard` (block private/link-local/metadata ranges), does not auto-follow cross-origin redirects with the auth header attached, and re-validates each redirect hop; `browse_web`/Playwright re-validates post-redirect URLs; regression tests for redirect-to-internal and metadata IP.
- **Files:** `src/hachi/core/integrations.py:244-270`, `core/net_guard.py`, `tools/browser_tools.py:49`, `core/browser.py:193`.
- **Effort:** M · **Risk:** Med · **Deps:** none.

### T6.5 — Unconditional untrusted-content framing *(BEFORE RELEASE)*
- **Objective:** Injection framing is not detection-gated.
- **Acceptance:** all externally-sourced content fed to the model is framed/delimited as untrusted regardless of scanner verdict; the scanner becomes an *additional* signal, not the gate; tests confirm framing on scanner-evading input.
- **Files:** `src/hachi/core/untrusted_content.py:42-46`, callers in tools/loop.
- **Effort:** M · **Risk:** Med (prompt changes — watch for behavior regressions) · **Deps:** none.

### T6.6 — Webhook fail-closed + NO_AUTH host-gate *(NEXT SPRINT)*
- **Objective:** Close two smaller auth gaps.
- **Acceptance:** the gateway channel-webhook path fails closed on empty allowlist (match the router fix); `HACHI_GATEWAY_NO_AUTH=1` only takes effect when bound to loopback; tests for both.
- **Files:** `gateway/fastapi_app.py:714-723,230-237`, `channels/router.py` (reference).
- **Effort:** S · **Risk:** Low · **Deps:** none.

---

## E7 — Reliability & Backend Robustness (Agent G)

### T7.1 — Approval wait off the event loop *(BEFORE RELEASE)*
- **Objective:** A flood of pending approvals can't exhaust the gateway thread pool.
- **Acceptance:** the 180s synchronous approval wait no longer blocks anyio worker threads — convert to an async wait (event/condition) or bounded executor with backpressure; load test: N concurrent pending approvals keeps the gateway responsive; regression test.
- **Files:** `src/hachi/core/approvals.py:309-340`, `gateway/fastapi_app.py` approvals route.
- **Effort:** M–L · **Risk:** Med (concurrency change) · **Deps:** coordinate with T3.3 (approval payload).

### T7.2 — Stop silent exception swallowing *(BEFORE RELEASE)*
- **Objective:** Persistence failures surface, never fake success.
- **Acceptance:** `memory_provider.py:236` no longer returns a fabricated uuid on write failure — it raises/surfaces; broad `except Exception` around credential/memory writes log at error and propagate or return an explicit failure result; tests assert failure is observable.
- **Files:** `src/hachi/core/memory_provider.py:236`, `core/memory_vault.py`, `core/secret_store.py`.
- **Effort:** M · **Risk:** Med (callers must handle new failures) · **Deps:** none.

### T7.3 — Durable orchestrator jobs *(NEXT SPRINT)*
- **Objective:** Func-only jobs survive restart.
- **Acceptance:** the SQLite queue's `_func_table` side-table persists/reconstructs func-jobs (prefetch/title/sync) across restart, or those jobs are made serializable; test that enqueued jobs survive a simulated restart.
- **Files:** `src/hachi/jobs/queue.py:591,601`, `core/orchestrator.py` enqueue sites.
- **Effort:** M · **Risk:** Med · **Deps:** none.

### T7.4 — `.og` node namespace collisions *(NEXT SPRINT/BACKLOG)*
- **Objective:** Same-named nodes don't silently overwrite.
- **Acceptance:** the engine warns/namespaces on duplicate node ids across packs; `snapshot_og_graph` reflects collisions; test with two packs defining the same node.
- **Files:** `src/hachi/og_engine/*`, `tools/og_tools.py`.
- **Effort:** M · **Risk:** Low · **Deps:** none.

---

## E8 — Performance & Scalability (Agent H)

### T8.1 — Cache the message-history prefix *(FIX NOW)*
- **Objective:** Stop re-billing loaded skills/tool results every turn; restore the `.og` token advantage.
- **Acceptance:** use all 4 Anthropic `cache_control` breakpoints, including a breakpoint on the stable message-history prefix (loaded skills, prior tool results); token accounting shows cached-read on turn ≥2; benchmark (`docs/BENCHMARK.md`/`hachi skill benchmark`) shows the per-turn input cost drop; no correctness regression.
- **Files:** `src/hachi/core/llm.py:247-260`, `core/loop.py:704,760`, `core/prompt_composer.py`.
- **Effort:** M · **Risk:** Med (cache-key ordering is subtle) · **Deps:** consider T8.4 (avoid cache-busting per-turn search).

### T8.2 — Virtualize transcript + throttle SSE deltas *(FIX NOW)*
- **Objective:** Chat view stays smooth on long conversations.
- **Acceptance:** transcript uses windowing/virtualization; SSE deltas are batched/throttled (e.g. rAF) rather than remapping the whole `messages` array per token; message items memoized; smooth at 1k+ messages.
- **Files:** `frontend/src/routes/conversation.tsx:746`, `frontend/src/lib/data/domains/chat.ts:134-166`, `frontend/src/lib/store/store.ts`.
- **Effort:** M · **Risk:** Med · **Deps:** coordinate with C (chat.ts shared).

### T8.3 — Offload blocking work out of async handlers *(BEFORE RELEASE)*
- **Objective:** Read endpoints don't stall the event loop.
- **Acceptance:** `/memory`, `/og/graph`, `/skills` (and peers) wrap blocking control-plane calls in `anyio.to_thread`/`run_in_executor`, or become truly async; verified no synchronous blocking in `async def` route bodies.
- **Files:** `src/hachi/gateway/fastapi_app.py:810-830` and peer routes.
- **Effort:** S · **Risk:** Low · **Deps:** none.

### T8.4 — SSE push instead of 50ms poll *(BEFORE RELEASE)*
- **Objective:** Remove the busy-poll of a synchronous lock-taking read on the asyncio loop.
- **Acceptance:** SSE endpoints subscribe to a push notification (asyncio event/queue) rather than polling every 50ms; the lock-taking read no longer runs on the loop thread; latency unchanged or better; load test stable.
- **Files:** `src/hachi/gateway/fastapi_app.py:436-469,620-664`, `core/runtime_read_models.py`.
- **Effort:** M · **Risk:** Med · **Deps:** T8.6 (event poll structure).

### T8.5 — Pre-warm / pin Chroma embedding model *(BEFORE RELEASE)*
- **Objective:** Eliminate multi-second cold start + hidden network dependency.
- **Acceptance:** an explicit `embedding_function` is configured and pre-warmed at startup (or lazily once, cached); model source is pinned/bundled or documented as an offline-capable download; first memory query after boot is fast; offline behavior documented.
- **Files:** `src/hachi/core/memory_vault.py:121-148`.
- **Effort:** M · **Risk:** Med · **Deps:** none.

### T8.6 — SessionStore reader concurrency + event poll *(BEFORE RELEASE)*
- **Objective:** Don't serialize all reads behind one lock; fix O(n) event scan.
- **Acceptance:** SessionStore allows concurrent readers under WAL (per-thread/pool connections rather than one global-locked connection); `read_events_since` uses a `deque` + bisect on monotonic sequence instead of O(n) scan; tests for concurrent read + correctness.
- **Files:** `src/hachi/core/session_store.py:49-51`, `core/runtime_read_models.py:422-429`.
- **Effort:** M · **Risk:** Med · **Deps:** none.

### T8.7 — Paginate `/memory` + lazy CLI imports *(BEFORE RELEASE)*
- **Objective:** Bounded memory fetch; fast CLI startup.
- **Acceptance:** `/memory` paginates and caches instead of fetching all of `core_memory`; CLI defers heavy imports (TUI/orchestrator/Anthropic/Chroma) so `--help`/`doctor` start fast (measure before/after); `LIKE %q%` cross-session search replaced/augmented with FTS5 or an index (may slip to Sprint 3).
- **Files:** `src/hachi/gateway/orchestrator.py:741-760`, `core/session_store.py:273-281`, `cli/main.py:28-64`.
- **Effort:** M · **Risk:** Med · **Deps:** none.

---

## E9 — Frontend Test & Quality Foundation (Agent B, Sprint 3)

### T9.1 — Add Vitest + React Testing Library + smoke e2e
- **Objective:** The frontend has tests and they run in CI.
- **Acceptance:** Vitest + RTL configured; unit tests for the spend reducer (T3.5), data-layer load-state machine (T3.1), and approvals wiring (T3.3); a Playwright smoke test boots the SPA against a mocked gateway and exercises setup → conversation → approval; `npm test` wired into `frontend-contract` (T2.1).
- **Files:** `frontend/` (new `vitest.config.ts`, `src/**/*.test.tsx`, `e2e/`), `frontend/package.json`, `ci.yml`.
- **Effort:** L · **Risk:** Low · **Deps:** T2.1, T3.x.

### T9.2 — `openapi-typescript` generation wired
- **Objective:** Make the CLAUDE.md contract loop real.
- **Acceptance:** `npm run generate:api` produces TS types from `openapi/hachi-openapi.json`; `check:api` fails on drift; documented in CLAUDE.md remains accurate.
- **Files:** `frontend/package.json`, `frontend/src/lib/data/types.ts`.
- **Effort:** M · **Risk:** Low · **Deps:** T2.1.

---

## E10 — Documentation & Onboarding Consistency (Agent A, Sprint 3)

- **T10.1** — Unify provider/tier naming across CLI setup, web wizard, and docs (resolve contradictions). *(S)*
- **T10.2** — Link `SECURITY.md` from README + COOKBOOK; add a "what Hachi does/doesn't do without asking" trust page. *(S)*
- **T10.3** — Mark `docs/strategy/01-architecture-review.md` as **historical/superseded** (its debt items are fixed) to stop phantom work. *(S)*
- **T10.4** — Update `docs/design/web-ui-design-brief.md` to the live hound/Torii-red/leash brand (retire teal/ouroboros). *(S)*
- **T10.5** — Document local-first data-at-rest posture (SEC-010) and offline embedding behavior (T8.5). *(S)*

---

## E11 — Backlog / Tech Debt (Sprint 4+)

Responsive/mobile pass (BB-003) · encryption-at-rest opt-in (SEC-010) · `cli/main.py`/monolith splits (WB-017) · central typed `Settings` model (WB-016) · consolidate `hachi pack`/`hachi skill pack` (BB-012) · remove mock chat scripts from live store (WB-011) · per-session boot cost at scale (WB-028) · dep floor/lockfile verification (WB-022/024).

---

## Parallelization Strategy

### Agent assignment

| Agent | Epics | Surface | Sprint 1 load |
|---|---|---|---|
| **A** | E1, E10 | CLI / backend config / docs | T1.1, T1.2, T1.3 |
| **B** | E2, E9 | CI/CD, infra, frontend test infra | T2.1 (**first**), T2.2 |
| **C** | E3 | Frontend data layer & screens | T3.1, T3.2 |
| **D** | E4 | Frontend shell/settings + config backend | T4.1 |
| **E** | E5 | Frontend a11y / design system | T5.1, T5.2, T5.3, T5.4 |
| **F** | E6 | Backend security | T6.1, T6.2 |
| **G** | E7 | Backend reliability | (Sprint 2 start; can pick T7.2 early) |
| **H** | E8 | Backend perf + 1 frontend perf task | T8.1, T8.2 |

### Dependency graph (Sprint 1 critical path)

```
T2.1 (fix CI)  ──►  [gates all merges — land first]
                         │
   ┌─────────────────────┼───────────────────────────┐
   ▼                     ▼                             ▼
T1.1►T1.2 (CLI)   T3.1 (kill fixtures) ──► T3.2, T4.1, T8.2
                         │                     │
                         └──► T4.1 (status bar)─┘ (shared __root.tsx — sequence)
   F: T6.1 ∥ T6.2 (security, independent)
   E: T5.1►T5.3, T5.2 ∥ T5.4 (a11y, mostly independent)
   H: T8.1 (cache) ∥ T8.2 (virtualize, coordinate chat.ts with C)
```

**Independent (run fully parallel):** E1 (A), E6 (F), E5 (E), E8-backend (H, except T8.2). These touch disjoint files.

**Frontend collision zone (C, D, E, H-T8.2):** all edit `frontend/src`. Mitigation — **file-ownership map**:

| File | Owner |
|---|---|
| `lib/data/domains/*.ts`, `lib/data/bootstrap.ts`, `store.ts` (data paths) | **C** |
| `lib/data/domains/chat.ts` | **C** owns; **H** coordinates for T8.2/T3.5 reducer (single PR or pairing) |
| `routes/__root.tsx`, `components/shell/*`, `settings.tsx`, `routes/spend` | **D** |
| `components/ui/*`, `styles/**`, dialog/focus primitives, `memory.tsx` controls | **E** |
| `routes/conversation.tsx` | **H** (virtualization) + **C** (data) — coordinate |

**Recommended isolation:** run C, D, E, H in **separate git worktrees** and integrate via small PRs in the order C → D → E (status bar after data layer; a11y last so it styles the final markup). H's backend tasks are independent; H-T8.2 lands after C's chat.ts stabilizes.

### Cross-cutting coordination points
- **Approvals:** T3.3 (frontend wiring) ↔ T7.1 (backend async wait) ↔ payload-key fix — assign C+G to a joint mini-spec first.
- **Config:** T1.2 (CLI) ↔ T4.2 (UI cap) ↔ T2.x — A provides the config endpoint contract D consumes.
- **Spend:** T3.5 (double-count) ↔ T4.1/T4.2 (display/cap) ↔ T8.1 (cost accounting) — single source of truth in `core/cost.py`.

---

## Sprint Roadmap

### Sprint 1 — Critical blockers (target: CI green, no fakes, no crashes, no critical CVE-class holes)
**Goal:** make the product *honest and verifiable*.
- B: **T2.1** (fix CI — do first), T2.2
- A: T1.1, T1.2, T1.3
- F: T6.1, T6.2
- C: T3.1, T3.2
- D: T4.1
- E: T5.1, T5.2, T5.3, T5.4
- H: T8.1, T8.2, T8.3
**Exit criteria:** all 10 Criticals closed; CI green on main; no UI shows fabricated data; CLI config works; pack zip-slip + MCP exec closed.

### Sprint 2 — High priority (target: real controls + secured egress + perf hotspots)
- C: T3.3, T3.4, T3.5, T3.6
- D: T4.2, T4.3, T4.4
- F: T6.3, T6.4, T6.5
- G: T7.1, T7.2
- H: T8.4, T8.5, T8.6, T8.7
- B: T2.3, T2.4
- E: T5.5
**Exit criteria:** leash/run/spend/authoring are real or honestly disabled; SSRF + injection + pack signing closed; gateway stable under concurrency; release pipeline ships Docker+SBOM+frontend.

### Sprint 3 — Medium improvements
- E: T5.6 (a11y completion + CI a11y gate)
- B: T9.1, T9.2 (frontend tests + contract generation)
- G: T7.3, T7.4
- F: T6.6
- H: search FTS (T8.7 remainder), PERF-006 deque, WB-012 hot-path search
- A: E10 docs (T10.1–T10.5); central Settings (WB-016) start
**Exit criteria:** frontend has tests in CI; a11y AA pass; durability + remaining auth gaps closed; docs consistent.

### Sprint 4 — Enhancements & optimization
- E11 backlog: responsive/tablet pass, encryption-at-rest opt-in, monolith splits, pack-system consolidation, remaining tech debt.

---

*Verification for every task: [08 — Verification Strategy](08-verification-strategy.md). Release gates: [09 — Release Readiness](09-release-readiness.md).*
