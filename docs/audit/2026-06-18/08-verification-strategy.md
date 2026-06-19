# Hachi — Verification Strategy

**Date:** 2026-06-18 · **Owner:** QA Lead
**Purpose:** Define how every fix in [07 — Execution Plan](07-execution-plan.md) is validated before it is considered done, and how the program as a whole is regression-protected.

---

## 0. Global verification gate (Definition of Done for every task)

A task is **not done** until all apply:

1. **Backend:** `uv run ruff check src tests` clean · `uv run mypy src --ignore-missing-imports` clean for touched modules · `uv run pytest -q` green (on Windows, per project note, use `PYTHONPATH=src uv run --with pytest --no-project pytest -q` if the editable-install lock breaks).
2. **Frontend:** `npm run --prefix frontend typecheck` clean · `npm run --prefix frontend test` green (once E9 lands).
3. **New regression test** that fails before the fix and passes after, referenced by finding ID in the test name/docstring.
4. **CI green** on the PR (only meaningful after **T2.1** repairs the contract job — that is why T2.1 is first).
5. **OpenAPI contract current** if any gateway route changed: `uv run hachi gateway-openapi --output openapi/hachi-openapi.json` and `npm run --prefix frontend generate:api` produce no diff.
6. **No new fakes:** a changed UI control either performs a real backend action or is visibly disabled — never toasts success without effect (enforced by review checklist + e2e where feasible).

---

## 1. Per-finding verification — Critical (Sprint 1)

| Finding | Test cases | Regression | Integration | Perf/Sec | UAT acceptance |
|---|---|---|---|---|---|
| **WB-001** CI | Run the job on a clean tree; on a deliberately drifted OpenAPI | Job fails on drift, passes clean | Full `ci.yml` run | — | Maintainer sees green CI on `main` |
| **BB-001/002** CLI config | `config-get`/`config-set` for every documented key incl. `safety_tier`, `max_tokens`, `session_cost_cap_usd`; invalid value | `test_config_get_set.py` extended per key | CLI ↔ settings file round-trip | — | User sets a budget cap from the CLI and reads it back |
| **SEC-001** zip-slip | Malicious zip with `../escape.og`, absolute path, and a decompression bomb | `test_security_hardening` / new pack test asserts refusal | Pack install end-to-end with crafted archive | **Security:** confirm no file written outside dest; size cap enforced | Installing a malicious pack fails safely with a clear error |
| **SEC-005** MCP exec | Pack/config with an MCP server command; with and without tier/consent | Test asserts unapproved MCP entry does **not** spawn | Boot with a web-sourced MCP entry | **Security:** default-deny verified | User is prompted before any MCP command runs |
| **UX-001/BB-006** key validation | Valid key, invalid key, 12-char junk | Component test: "validated" only after real round-trip | Wizard → backend validate endpoint | — | Junk key never shows "validated" |
| **UX-018/BB-004** fixtures | Gateway up / down / slow for each domain loader | Data-layer state-machine unit tests (loading/empty/error) | SPA against a killed gateway shows error, not fake data | — | With gateway off, every screen shows honest error/empty |
| **PERF-001** cache | Multi-turn session token accounting | Test asserts cache-read on turn ≥2; cache breakpoints present | Loop with a loaded skill pack | **Perf:** `hachi skill benchmark` shows per-turn input-cost drop; no answer regression | Cost meter for a long session is materially lower |
| **PERF-025** transcript | 1k+ message transcript; rapid SSE deltas | Render perf test (no full remap per delta) | Live stream into a long conversation | **Perf:** no jank/lockup at 1k msgs; CPU bounded | Long chat scrolls smoothly |
| **UX-009** status bar | Every route; model/session/cost/connection | Component test renders all four; connection pill reflects SSE state | SPA across routes with gateway up/down | — | User can always see model, session, live cost, connection |
| **UX-023 + BB-007/008/011** a11y | Keyboard-only nav of every screen; screen-reader names; Esc on modals | `axe` unit checks; focus-trap tests | Full keyboard pass | **A11y:** no critical `axe` violations on touched screens | Tab reaches every control with a visible ring; modals trap+restore focus |

---

## 2. Per-finding verification — High (Sprint 2)

| Finding | Key test cases | Acceptance |
|---|---|---|
| **WB-007 leash** | Allow/Deny → assert POST to `/approvals/{id}` with correct body; payload keys match emitted event; countdown ticks; decline reason captured | A real approval flows end-to-end; auto-decline countdown matches backend |
| **UX-017 /run** | Subscribe to real SSE; no active run → empty state | "Watch live" shows the actual stream, never simulation |
| **WB-008/BB-010 authoring** | apply/revert persist (or are disabled); no fake success toast | Apply either writes or is disabled; diff stats are real |
| **WB-009 spend** | Reducer test: cumulative `usage` set not added; matches `cost.py` | Meter matches backend total within rounding |
| **UX-002/008 cap+provider** | Set cap → persists + enforced; provider choice persists | User sets/enforces a budget cap and picks a provider in setup |
| **SEC-003/004 SSRF** | Redirect-to-internal, metadata IP (169.254.169.254), auth-header-on-redirect | Egress blocked to private/link-local; no header leak across origin |
| **SEC-002 framing** | Scanner-evading injection still framed | All external content framed regardless of scanner verdict |
| **SEC-007 signing** | Unsigned/empty-hash install refused without override | Default-deny unsigned packs |
| **REL-001/PERF-011 loop** | N concurrent pending approvals; blocking-call audit of async routes | Gateway stays responsive under approval flood; no blocking in `async def` |
| **WB-013 swallow** | Forced memory/credential write failure | Failure surfaces (raises/explicit result); no fake uuid |
| **WB-004/002/003/005 infra** | `docker compose build`; release dry-run | Compose builds; release ships Docker+SBOM+frontend, non-root |
| **SEC-011 scans** | `pip-audit`/`npm audit`/SAST in CI | Known-vuln dep fails the PR |
| **PERF-005/019/015/012/022** | SSE push (no 50ms poll); embedding pre-warm; concurrent SessionStore reads; `/memory` pagination; CLI `--help` startup time | Each hotspot measurably improved; no functional regression |

---

## 3. Regression suite (run on every PR after T2.1)

- **Backend:** full `pytest` (186 files) — must stay green; the security-hardening, permission-gate, gateway-auth, SSRF/net-guard, job-queue, session-concurrency, and prompt-cache suites are the high-value regression anchors.
- **Frontend (after E9):** Vitest unit (data-layer state machine, spend reducer, approvals wiring) + Playwright smoke (setup → conversation → approval) against a mocked gateway.
- **Contract:** OpenAPI export + `generate:api` diff check (T9.2).
- **Lint/type:** ruff + mypy-strict blocking on PR (T2.3).

## 4. Integration tests (new, gateway-level)

The web frontend was "NOT yet run vs live gateway." Add an integration layer:
- Boot the gateway in-process (existing test fixtures) and drive the documented REST + SSE endpoints used by each `domains/*.ts` adapter; assert payload shapes match what the frontend reads (this is exactly where the leash no-op and spend double-count slipped through).
- Killed-gateway and slow-gateway scenarios to validate the new error/loading states (T3.1).

## 5. Performance tests

- **Token/cost:** extend `hachi skill benchmark` assertions to prove PERF-001 (cached message-history prefix) — per-turn input cost on turn N must be a fraction of turn 1 for a loaded pack.
- **Concurrency:** approval-flood and multi-session load against the gateway (REL-001/T7.1, PERF-005/T8.4); assert p95 latency bounded.
- **Frontend:** transcript render benchmark at 100/1k/5k messages (PERF-025).
- **Startup:** measure CLI `--help`/`doctor` cold start before/after PERF-022; memory-query cold start before/after PERF-019.

## 6. Security tests

- **Pack supply chain:** malicious-zip (slip + bomb), unsigned-pack, MCP-command entry — all must fail safely (SEC-001/005/007).
- **Egress:** SSRF matrix (private ranges, metadata IP, redirect chains, auth-header leakage) (SEC-003/004).
- **Injection:** scanner-evading payloads remain framed (SEC-002).
- **Auth:** confirm auth-on-by-default stays true; NO_AUTH only on loopback; webhook fail-closed (SEC-006/013).
- **Pipeline:** `pip-audit`, `npm audit`, SAST run in CI and fail on high (SEC-011).

## 7. User-acceptance criteria (per surface)

- **CLI:** a new user runs `setup`, sets a budget cap, runs a task, and inspects spend — no traceback, accurate numbers.
- **Web:** a new user completes setup with real key validation and provider choice; sees model/session/cost/connection at all times; approves a real leash prompt; watches a real live run; with the gateway off, sees honest errors — never fabricated data.
- **Security-minded self-hoster:** can read `SECURITY.md`, see an SBOM, install via Docker as non-root, and confirm packs/MCP require consent.
- **Accessibility:** a keyboard-only and screen-reader user can complete setup → chat → approval.

---

*Gates that map these to a go/no-go decision: [09 — Release Readiness](09-release-readiness.md).*
