# QA Agent 2 — White-Box Source Audit v2 (Hachi)

**Date:** 2026-06-18 (v2 re-verification) · **Mode:** read-only deep source read.
**Scope:** `src/hachi/` (Python backend), the new `src/hachi/client/**` REST client, the new gateway web routes (`gateway/fastapi_app.py`, `gateway/control_plane.py`, `gateway/orchestrator.py`), `cli/main.py` (+498), `openapi/hachi-openapi.json`, the uncommitted `frontend/` SPA (59 TS/TSX files), CI/CD, Docker/compose, deps.
**Excluded from scoring (per audit charter):** `src/hachi/tui/cockpit/**` (6,912 LOC / 27 files) is owned by a concurrent session and is OFF-LIMITS — only contract-level notes about its consumption of `hachi.client` appear here.

This report **re-verifies every prior WB-* finding** from `docs/audit/2026-06-18/02-white-box.md` against the code as it stands today, tags each FIXED / STILL-OPEN / PARTIAL / CHANGED with current `file:line` evidence, and adds new findings (`WB-N0x`) from the churn since the prior audit.

---

## 0. Headline verdicts (verified first-hand)

- **WB-001 (broken `frontend-contract` CI job) — STILL-OPEN / Critical. CONFIRMED with hard evidence.** The job (`.github/workflows/ci.yml:158-173`) runs `npm run --prefix frontend check:api`, `npm run … lint -- <files>`, but `frontend/package.json` defines **only** `dev/build/preview/typecheck` (no `check:api`, no `lint`, no `generate:api`; no `openapi-typescript` dep). All seven files passed to the lint step are **absent** (verified each: `src/lib/api/hachi.server.ts`, `src/lib/api/hachi.functions.ts`, `src/types/api.ts`, `src/hooks/use-gateway-queries.ts`, `src/components/screens/session-workbench.tsx`, `src/components/screens/logs-screen.tsx`, `src/routes/s.$slug.tsx`). Exact CI fix in §CI.
- **BB-001 / BB-002 (CLI config crashes) — STILL-OPEN / Critical. REPRODUCED LIVE.** `hachi config-get` crashes with a raw `AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'` traceback on **every** call. Root cause and the reason the test suite is green anyway (a mock-shape divergence) are new findings WB-N06 / WB-N07. These sit in the heavily-modified `cli/main.py`, so they are in white-box scope.
- **OpenAPI export is current.** Freshly regenerated `app.openapi()` is **byte-identical** to the committed `openapi/hachi-openapi.json` (43 paths, `BearerAuth` present, all new web routes exported). The route↔OpenAPI seam is clean. The drift is at the **frontend↔payload** and **stub-backend** seams, and the **contract is unguarded** because the CI job that would enforce it is broken (WB-001).

---

## 1. Prior-finding verification table

| ID | Title (abbrev) | Prior sev | **Status now** | Current evidence |
|----|----------------|-----------|----------------|------------------|
| WB-001 | `frontend-contract` runs nonexistent scripts/files | Critical | **STILL-OPEN** | `ci.yml:158-173`; `frontend/package.json:6-11`; 7 phantom files all absent |
| WB-002 | Strict mypy/lint non-blocking on main; gate=subset | Medium | **STILL-OPEN** | `ci.yml:78-84` `continue-on-error`; `mypy-strict-gate.ini` = 36-file subset |
| WB-003 | `release.yml` PyPI-only; no image/frontend/SBOM/sign | Medium | **STILL-OPEN** | `release.yml` = `uv build` + PyPI publish only |
| WB-004 | compose `frontend` → nonexistent `frontend/Dockerfile` | High | **STILL-OPEN** | `docker-compose.yml:21-24`; `frontend/Dockerfile` absent; backend `Dockerfile` no Node stage |
| WB-005 | Container root + `0.0.0.0:8787` published | Medium | **STILL-OPEN** | `Dockerfile` no `USER`; `docker-compose.yml:7-8` `8787:8787`, `:19` `--host 0.0.0.0` |
| WB-006 | No HEALTHCHECK; Dockerfile 3.12 vs CI 3.11 | Low | **STILL-OPEN** | no HEALTHCHECK; `Dockerfile:1` `python:3.12-slim` vs `ci.yml:25` `3.11` |
| WB-007 | Web leash allow/deny no-op + wrong payload keys | High | **STILL-OPEN** | `frontend/src/lib/data/domains/chat.ts:216-225, 395-414`; backend emits `request_id/operation/tool/target/reason` |
| WB-008 | Authoring apply/revert stubs write nothing | Medium | **STILL-OPEN** | `cli/main.py:694-714` (no-op stubs); `store.ts:890-899` toasts fake "re-indexed in 0.3s" |
| WB-009 | Web spend double-counts cumulative usage | Medium→High | **STILL-OPEN** | `chat.ts:227-244` adds; `loop.py:757-760` emits cumulative `CostTracker.snapshot()` (`cost.py:99,110`) |
| WB-010 | `/skills`,`/og/graph`,`/spend` return empty not 503 | Low | **STILL-OPEN (worse)** | `control_plane.py:468-472, 484-488, 510-514`; now also `cancel/toggle_schedule`, `add/delete_memory_fact` (`449-462, 521-531`) |
| WB-011 | zustand store ships ~400 LOC mock scripts | Medium | **STILL-OPEN** | `store.ts:1066-1138` (Priya), `1285-1310` (reader-1/PR #482); `partialize:1389-1392` persists only `{operator,theme}` |
| WB-012 | Constant `semantic_search("important context")` every turn | Medium | **STILL-OPEN** | `orchestrator.py:882` (constant query), `:1056` rebuilt every turn, not cached despite "frozen" label `:893` |
| WB-013 | Silent-swallow masks credential/memory failures | High | **STILL-OPEN** | `memory_provider.py:232-239` fake `uuid4()`; `secret_store.py:44-50,99-102,146-149,252-262`; `memory_vault.py:481-485,552-556,560-573` |
| WB-014 | Untracked fire-and-forget daemon threads | Medium | **STILL-OPEN** | `notifier.py:136,223`; `mcp_client.py:258` (note: `:194` IS tracked — prior false positive) |
| WB-015 | Sparse tracing spans, unlinked to runtime events | Low | **STILL-OPEN** | `core/tracing.py`; spans only at `loop.py:340,722`; new web routes add none |
| WB-016 | Monolith files concentrate change risk | Medium | **STILL-OPEN (worse)** | `cli/main.py` now **2,886** LOC (was 2,747); `orchestrator.py` 1,961; `og_engine/engine.py` 1,711; `tui/app.py` DELETED |
| WB-017 | 110 `os.environ`/getenv reads, no central Settings | Medium | **STILL-OPEN** | 110 reads / top `cli/main.py:27`; `core/settings.py` covers config-file shape only |
| WB-018 | Hardcoded `surface="cli"` in gateway turn path | Low | **STILL-OPEN** | `orchestrator.py:1087` `surface="cli"  # TODO` verbatim; not threaded from caller |
| WB-019 | "Durable" queue: real (func) jobs non-durable | Medium | **STILL-OPEN** | `orchestrator.py:937-946,981-989` enqueue `func=` w/o `handler_name`; `queue.py:591-603` discards func-only on restart |
| WB-020 | Global skill-node namespace overwrite | Medium | **PARTIAL** | engine FIXED (`engine.py:535,539-552` qualified id + collision guard); `snapshot_og_graph` STILL bare-keyed (`orchestrator.py:837-846`) |
| WB-021 | OpenAI streaming session leak | Low | **STILL-OPEN** | `fastapi_app.py:600-603` fresh `openai-{chat_id}` per request; `finally:678-684` never `close_session` |
| WB-022 | Floored deps, no lock, bad pytest floor | Medium | **PARTIAL** | `uv.lock` now committed (FIXED); `>=`-only no caps + `pytest>=9.0.3` remain (`pyproject.toml:34-57`) |
| WB-023 | Redundant/empty extras | Low | **STILL-OPEN** | `chromadb` core+`memory` extra; `httpx` core+`local-models`; `unofficial = []` (`pyproject.toml:40,68-73,87`) |
| WB-024 | Frontend zero test tooling / no API generator | Medium | **STILL-OPEN** | `frontend/package.json` no vitest/jest/playwright/openapi-typescript/lint; 0 `*.test.*` under `frontend/src` |
| WB-025 | Credential guidance points to unsupported `--set KEY=value` | Medium | **STILL-OPEN** | `cli/main.py:1332,1338` print `--set KEY=<your-key>` but `--set` metavar=`KEY` prompts/aborts non-TTY (`:2444-2467`); `credential_vault.py:38` |
| WB-026 | LLM key fallback reads `.env` from cwd | Low | **STILL-OPEN** | `core/llm.py:51-53` `_read_dotenv(Path.cwd()/".env")` |
| WB-027 | `unofficial`/chatgpt-web provider surface | Low | **STILL-OPEN** | `pyproject.toml:87` `unofficial=[]`; `llm.py:649-674` chatgpt-web kind; `core/unofficial_providers.py` present |
| WB-028 | Per-session full runtime boot | Medium | **PARTIAL** | `shared_services.py` shares engine/vault/graph/writer; per-session still builds MCPManager+subprocs, MemoryManager, ExecutionLoop, ScheduleRunner, resident worker (`orchestrator.py:1431,1452,1522,1877,1924,1944`); shared `mcp_manager` is built but **dead** |
| WB-029 | Iteration/budget exits return magic strings | Low | **STILL-OPEN** | `loop.py` sentinel strings `[INCOMPLETE]`/`[BUDGET LIMIT REACHED]`/etc. |
| WB-030 | Catch-and-discard prefetch/recall → silent empty | Low | **STILL-OPEN** | `orchestrator.py:915-920` (`_run_prefetch_job`→[]), `:1060-1064` (`_chat_impl` fallback→[]) |
| WB-031 | Duplicated URL builders / capability factory triple | Low | **CHANGED/PARTIAL** | client side now consolidated in `client/sse.py` mirroring TS; capability `__import__` repetition pattern persists (`orchestrator.py` `_build_capability_registry`) |

**Prior-finding roll-up:** of 31 prior WB findings — **0 fully FIXED**, **3 PARTIAL** (WB-020, WB-022, WB-028 — each landed a real improvement: collision guard, committed lockfile, shared read-services), **1 CHANGED** (WB-031), **27 STILL-OPEN**. Two prior items got *worse* (WB-010 grew more empty-return handlers; WB-016 `cli/main.py` grew ~140 LOC).

---

## 2. New findings (this churn)

### WB-N01 — `src/hachi/client/models.py` is UTF-8-BOM-prefixed and contains 10 mojibake em-dashes shipped into runtime display strings
- **Category:** Encoding / Correctness · **Severity:** Medium · **Complexity:** S · **Status:** NEW
- **Root cause:** `models.py` begins with a UTF-8 BOM (`ef bb bf`) and contains **zero** proper em-dashes (`e2 80 94`) but **10** double-encoded mojibake sequences (`c3 a2 e2 82 ac` = an em-dash that was decoded latin-1 then re-encoded UTF-8). Verified at the byte level and at runtime: `McpServer.from_api(...).transport` is the **3-character string `â€"`**, not `—` (`is mojibake 3-char: True`). The corruption is confined to this one file — `gateway.py`, `sse.py`, `gaps.py` are clean (proper em-dashes, no BOM).
- **Affected lines** (decoded): module docstring `:2`; `_format_ts`/humanizer fallbacks `:92,112,117` (`return "—"` → renders `â€"`); `:218` comment; `McpServer` defaults `transport="—"` `:703`, `uptime="—"` `:704`; `AgentRow` `task`/`outcome` defaults `:863,865`; `TrustBarSnapshot` `model` default `:970`.
- **Impact:** The Hachi Cockpit (which consumes `hachi.client`) renders garbled `â€"` wherever these placeholder/empty-value strings appear — MCP server transport/uptime columns, empty sub-agent task/outcome cells, every `_format_ts` "no timestamp" fallback, and the trust-bar model when config is absent. The leading BOM can also break tooling that doesn't strip it (some linters, `git` blame heuristics, naive `open()` without `utf-8-sig`). The new client tests (`test_client_models.py`) assert adapter logic but never assert on these display strings, so they don't catch it.
- **Recommended solution:** Re-save `src/hachi/client/models.py` as UTF-8 **without** BOM and replace the 10 mojibake sequences with real `—` (or ASCII `-`). Add a unit assertion (e.g. `assert "â€" not in inspect.getsource(models)`) or a repo-wide `scripts/check_encoding.py` so it can't regress. Ruff/`UP` won't catch this; consider a pre-commit `fixencoding`/`check-byte-order-marker` hook.

### WB-N02 — New `/skills`, `/memory`, `/memory/recall`, `/og/graph`, `/authoring/*`, `/spend` routes are `async def` calling blocking control-plane work directly (no `to_thread`)
- **Category:** Performance / Concurrency · **Severity:** Medium · **Complexity:** S · **Status:** NEW (overlaps PERF-011)
- **Root cause:** Every new web route is declared `async def` yet calls a **synchronous** control-plane method on the event loop: `get_skills` (`fastapi_app.py:823`), `get_memory` (`:828`), `get_memory_recall` (`:833`), `get_og_graph` (`:858`), `get_authoring_targets` (`:863`), `get_authoring_target` (`:868`), `get_spend` (`:883`). These descend into blocking work: `og_graph()`/`snapshot_og_graph` walks the skill engine; `get_memory_recall` and `get_spend` perform Chroma `semantic_search` / scan up to 500 runtime events (`cli/main.py:587,738`); `get_authoring_target` does synchronous `file_path.read_text` (`cli/main.py:691`). Contrast with `create_session` (`:398`) and the OpenAI/channel turns (`:579,742`) which correctly wrap in `anyio.to_thread.run_sync`, and `post_message` (`:416`) which is a plain `def` (so FastAPI offloads it).
- **Impact:** Each of these calls blocks the single asyncio event loop for the duration of a Chroma query / file read / engine walk, stalling SSE keepalives and `/health` for all concurrent clients. Under the always-on multi-client ambition this serializes the control plane.
- **Recommended solution:** Either make these handlers plain `def` (let FastAPI run them in the threadpool) or wrap the control-plane call in `anyio.to_thread.run_sync`, matching `create_session`.

### WB-N03 — New multi-session handlers resolve "the first agent", ignoring the requesting session
- **Category:** Correctness / Scalability · **Severity:** Medium · **Complexity:** M · **Status:** NEW
- **Root cause:** `get_skills`, `get_memory`, `get_memory_recall`, `get_og_graph`, `add_memory_fact`, `delete_memory_fact` all pick the agent via `next(iter(self._agents.values()), None)` (`cli/main.py:563,571,583,604,851,…`) — an arbitrary "first" session. Only `get_spend` honours an explicit `session_id` (`:730-733`). The routes don't even accept a `session_id` for the others.
- **Impact:** In any multi-session deployment (the gateway allows up to `max_active_sessions=100`), `/skills`, `/memory`, `/memory/recall`, `/og/graph`, and memory writes operate on whichever session happens to be first in the dict — not the caller's. Memory facts can be written to the wrong session's vault; recall returns another session's context. Latent today (single-session in practice) but a correctness landmine.
- **Recommended solution:** Thread a `session_id` query param into these routes and resolve the specific agent (as `get_spend` does); fall back to a documented default only when unspecified.

### WB-N04 — Authoring `apply`/`revert` write nothing but assert success up and down the stack
- **Category:** API Contract / Trust · **Severity:** Medium · **Complexity:** M · **Status:** NEW (concretises WB-008)
- **Root cause:** `_InProcessGatewayRuntime.authoring_apply` / `authoring_revert` (`cli/main.py:694-714`) validate the target path then `return {"ok": True, "reindexed_ms": 0}` / `{"ok": True}` — explicit no-op stubs ("`# TODO: when the proposed-patch pipeline exists…`"). `get_authoring_targets` always emits `dirty: False` (`:623,633,644`), and there is no proposed-patch source at all, so the diff is always empty. The OpenAPI descriptions self-document the stub (`hachi-openapi.json` "stub — no patch pipeline yet"). The frontend then optimistically toasts "Patch applied — re-indexed in 0.3s" (`store.ts:890-899`).
- **Impact:** A user editing a brain/skill `.og` and clicking Apply gets a green success toast and a fabricated re-index time while **nothing is written or re-indexed**. For a trust-positioned product this is an actively misleading control. It is also a latent security surface once implemented (writes `.og`/config — must be `PermissionGate`-scoped); note the path-traversal guard already exists (`cli/main.py:651-686`).
- **Recommended solution:** Return `501 Not Implemented` (or a `{"applied": false, "reason": "not implemented"}`) until the patch pipeline lands, and stop the UI asserting success. When implemented, gate writes behind `PermissionGate` workspace-root checks.

### WB-N05 — `WB-010` regression set widened: more control-plane handlers silently return empty instead of 503
- **Category:** API Contract / Error Handling · **Severity:** Low · **Complexity:** S · **Status:** NEW (extends WB-010)
- **Root cause:** Beyond the prior `skills()`/`og_graph()`/`spend()`, the new handlers `cancel_schedule`/`toggle_schedule` return `{"ok": False, "detail": "schedules not available"}` (`control_plane.py:454-462`) and `add_memory_fact`/`delete_memory_fact` return `{"id": "", "error": …}` / `{"ok": False, "error": …}` (`:521-531`) when their handler is `None`, rather than using `_require_handler` (→ 503). Sibling read handlers (`memory`, `memory_recall`, `authoring_*`) correctly use `_require_handler`.
- **Impact:** Inconsistent fail-loud contract: an unattached/misconfigured runtime is indistinguishable from "operation legitimately did nothing", so the cockpit/web cannot surface a runtime-detached error for these paths.
- **Recommended solution:** Route schedule + memory-write handlers through `_require_handler` for a uniform 503-on-unattached contract, or document the empty-return convention and surface it client-side.

### WB-N06 — `hachi config-get` and `config-set safety_tier|max_tokens|session_cost_cap_usd` crash with raw `AttributeError` (verifies BB-001/BB-002)
- **Category:** Functional bug · **Severity:** Critical · **Complexity:** S · **Status:** STILL-OPEN (Critical)
- **Root cause:** Both commands read top-level attributes that do **not** exist on `ResolvedConfig`. `config_get` builds `flat` with `cfg.safety_tier` / `cfg.max_tokens` / `cfg.session_cost_cap_usd` (`cli/main.py:2209-2211`); `config_set` assigns the same (`:2258,2260,2262`). Verified live: `ResolvedConfig` exposes these under nested models — `cfg.permissions.tier` (= `unrestricted`) and `cfg.agent.max_tokens` (= `8192`) — and `session_cost_cap_usd` is **absent entirely**. Running `hachi config-get` raises `AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'` (reproduced: full Rich traceback printed). `config_set`'s `except` only catches `ValueError`/`TypeError` (`:2270`), so the `AttributeError` escapes as a raw traceback too.
- **Impact:** The entire config-get surface is dead on arrival; the safety/budget controls (`safety_tier`, `max_tokens`, `session_cost_cap_usd`) — the product's anti-bill-shock controls — cannot be read or written from the CLI. Every invocation is a raw stack trace.
- **Recommended solution:** Map the keys to the real model paths: `safety_tier`→`cfg.permissions.tier`, `max_tokens`→`cfg.agent.max_tokens`; add a real `session_cost_cap_usd` field (or map to the existing budget field / `HACHI_MAX_SESSION_COST_USD`). Broaden the `config_set` `except` to include `AttributeError`. See WB-N07 for why CI didn't catch this.

### WB-N07 — `test_config_get_set.py` mocks a config shape the real object does not have → false-green (masks WB-N06)
- **Category:** Testing integrity · **Severity:** High · **Complexity:** S · **Status:** NEW
- **Root cause:** `tests/test_config_get_set.py:_fake_cfg()` (lines 20-28) returns a `SimpleNamespace(provider=…, safety_tier="guided", max_tokens=8192, session_cost_cap_usd=5.0)` — i.e. the three attributes as **top-level** fields — and patches `hachi.cli.main.load_effective_config` to return it (`:31-34`). Every test then passes because the *fake* satisfies the access pattern the *real* `ResolvedConfig` violates. 11 tests are green while the command 100% crashes in production.
- **Impact:** This is precisely why BB-001/002 survived to a second audit despite "passing" CI — the test encodes the wrong contract. It is a systemic over-mocking risk: a mock whose shape diverges from the production type gives false confidence on exactly the surface that's broken.
- **Recommended solution:** Build the fake from the real config (e.g. `load_effective_config()` against a tmp config dir, or `ResolvedConfig`/`SimpleNamespace` with the **nested** `permissions.tier` / `agent.max_tokens` shape). Better: have the test invoke the command against a real on-disk settings file in a tmp dir (no mock of `load_effective_config` at all) so the attribute paths are exercised.

---

## 3. API contract drift (OpenAPI ↔ routes ↔ client ↔ frontend types)

**The route↔OpenAPI seam is clean and current.** Regenerating `app.openapi()` yields a spec **byte-identical** to the committed `openapi/hachi-openapi.json`: 43 paths (3 more than the prior audit's 40 — `POST/DELETE /memory/facts*` and `PUT /capabilities/{cap_id}/enabled` added), `components.securitySchemes.BearerAuth` present, every new web route exported. So the OpenAPI *export step* is healthy. Two structural weaknesses and three concrete payload drifts remain — and **none are guarded**, because (a) the contract CI job is broken (WB-001) and (b) the frontend doesn't consume the spec at all.

### 3.1 Structural weakness — the frontend does NOT consume the OpenAPI spec
There is **no generated types module and no generator** in the SPA. `client.ts:4` comments that the client is "Shaped to the gateway OpenAPI", but `frontend/package.json` has no `generate:api` script and no `openapi-typescript` dependency, and the only "openapi" reference under `frontend/src` is that comment. Every gateway shape is **hand-mirrored** as a local `interface Gateway*` in each domain adapter (`chat.ts`, `work.ts`, `approvals.ts`, `spend.ts`, …). The Python `hachi.client.models` mirror the *same* shapes again, by hand, in a third place. So three independent hand-maintained copies of the contract exist with **zero compile-time linkage** to `openapi/hachi-openapi.json`. The CLAUDE.md `generate:api`/`check:api` loop is unimplementable here.

### 3.2 Structural weakness — most response bodies & all SSE payloads are untyped in OpenAPI
The SSE envelope `GatewayEventPayload` types only `{sequence, event_type, payload, time}` with `payload: additionalProperties` — so **every event body is opaque** in the spec (`text.stream.delta.text`, `usage.updated.estimated_cost_usd`, the permission keys, etc. are undocumented). And ~14 GET endpoints return an inline untyped `{"title": "Response …"}` with no component `$ref`: `/skills`, `/memory`, `/memory/recall`, `/og/graph`, `/authoring/targets(/{name})`, `/spend`, `/approvals`, `/permissions/audit`, `/config`, `/jobs/active`, `/jobs/failed`, `/schedules`, `GET /sessions`. For these, the de-facto contract is whatever the Python handler returns; OpenAPI provides no verification surface.

### 3.3 Concrete payload drifts

| # | Drift | Evidence | Sev |
|---|-------|----------|-----|
| D-1 | **`permission.requested` keys 100% mismatch.** Backend emits `request_id/operation/tool/target/reason/policy_summary` (passthrough — not remapped by `adapt_event`, `runtime_events.py:259`); chat adapter reads `payload.request/action/why` (`chat.ts:216-225`). All three are always `undefined`; the `request_id` needed to resolve the approval is discarded. This is the mechanical reason the web leash is a no-op (WB-007). | `chat.ts:216-225`; `core/approvals.py:328-338`; `core/permissions.py:815-825` | High |
| D-2 | **`usage.updated` cumulative treated as incremental.** `loop.py:757-760` emits the cumulative `CostTracker.snapshot()` (`cost.py:99 self._cost += cost`, `:110 estimated_cost_usd=self._cost`); the frontend **adds** it each event (`taskSpent + costUsd`, `chat.ts:227-244`), so spend inflates super-linearly within a turn. *(Note: the backend `/spend` endpoint computes per-turn deltas correctly server-side — `cli/main.py:751-771` — so `/spend` is the authoritative aggregate; the bug is purely the live SSE handler's add-vs-set.)* | `chat.ts:227-244`; `loop.py:757-760`; `cli/main.py:751-771` | High |
| D-3 | **`/spend` shape is undocumented & atypically camelCase.** OpenAPI 200 is an untyped `Response Get Spend`. Backend returns `{turns:[{n,inT,out,cost}], tidies:[{ts,before,after}], models:[{name,tokens,cost,color}]}` (`cli/main.py:761-802`); the Python client (`models.py:SpendTurn` `in_t`/`out`) and TS adapter (`spend.ts`) both mirror these UI-shaped abbreviations. Held together only by convention, unenforced. `inT` is always 0 (documented limitation, `cli/main.py:724-726,764`). | `cli/main.py:716-802`; `client/models.py:527-598`; `openapi/hachi-openapi.json` `/spend` | Medium |
| D-4 | **Backend ahead of frontend on memory writes.** OpenAPI exposes `POST /memory/facts` and `DELETE /memory/facts/{fact_id}`, and the Python client wires them (`gateway.py:165-197`), but the SPA's `memory.ts` treats memory as read-only and keeps add/delete/approve local-only — silent local fictions (same class as WB-008). | `gateway.py:165-197`; `frontend/src/lib/data/domains/memory.ts` | Low |

**Client ↔ gateway alignment (the new `src/hachi/client/`):** the Python client is well-built and largely correct — lazy `httpx.Client`, thread-safe, `GatewayError` on every transport/HTTP failure, context-manager support, careful `from_api` adapters that mirror the TS logic, and SSE parsing (`sse.py`) that faithfully mirrors `frontend/src/lib/data/sse.ts`. Endpoint paths and verbs all match the live routes and OpenAPI. The one defect is the `models.py` encoding corruption (WB-N01). One minor note: `client.create_session(surface="tui")` (gateway.py:441) is faithfully sent, but the gateway then overwrites it to `"cli"` in the turn (WB-018) — so the client's correct surface is discarded downstream.

---

## 4. Test coverage delta

**Deleted (10 test files, all TUI/legacy):** `test_tui_app.py`, `test_tui_authoring.py`, `test_tui_formatters.py`, `test_tui_state_store.py`, `test_tui_widgets.py`, `test_tui_setup_apikey.py`, `test_p2_01c_streaming_render.py`, `test_p2_09_tui_governance.py`, `test_p3_05_og_explorer.py`, `test_resume_and_slash_commands.py`. These covered the now-deleted `tui/app.py` and legacy REPL surfaces, so their removal is appropriate — but `test_p2_01c_streaming_render.py` (streaming render) and `test_resume_and_slash_commands.py` (resume / slash commands) covered *behaviours* (mid-stream render, session resume) that the new cockpit/web must still satisfy; that behaviour coverage is **not** obviously replaced at the engine level.

**Added (12 untracked test files):** `test_client_models.py` (489 LOC), `test_client_sse.py` (145), `test_client_gaps.py` (251), `test_gateway_web_endpoints.py` (349), plus 8 `test_cockpit_*.py` (cockpit is off-limits; their existence is noted, not assessed). The 4 client/gateway-web suites **pass (116 tests green)** and genuinely cover the new client adapters, SSE frame parsing, gap placeholders, and the new web routes' happy paths.

**What's covered well:** new client `from_api` adapters; SSE parser; new gateway route happy paths (`test_gateway_web_endpoints.py`).

**Gaps that bite (assertions that would have caught live bugs):**
- **No test asserts the `/spend` SSE handler arithmetic** (D-2 / WB-009) — a unit over the frontend `usage.updated` handler, or a contract test that `/spend` deltas reconcile with summed `usage.updated`, would surface it. (Frontend has *zero* tests — WB-024.)
- **No test asserts `permission.requested` payload keys** (D-1 / WB-007) — a single assertion that the SSE payload carries `request_id` and the consumer reads it would catch the 100% miss.
- **The config command tests are actively wrong** (WB-N07): they mock a config shape the real object lacks, so they're green while the command crashes (WB-N06/BB-001). This is the highest-value test-integrity fix.
- **No SQLite job-durability boundary test** (WB-019): a test asserting func-jobs survive a restart would have shown they don't (they're discarded — `queue.py:591-603`).
- **No mid-stream cancel/timeout or per-session `_chat_lock` concurrency test** (carried from prior audit) — the `_StreamCancelled`/`_StreamTimedOut` paths in `loop.py` and the streaming session-leak (WB-021) are untested.
- **No encoding/lint guard** would catch WB-N01 — the client model tests assert adapter logic but never the display strings.

---

## 5. Medium/Low themes (full detail above & in prior report)

- **Backend durability/correctness:** non-durable func-jobs (WB-019); constant per-turn `semantic_search("important context")` busting the prompt cache (WB-012, also a Perf hotspot); `snapshot_og_graph` still bare-keyed despite the engine collision fix (WB-020 PARTIAL); multi-session handlers resolve "first agent" (WB-N03). **(4)**
- **Config & secrets:** 110 raw env reads, no central typed Settings (WB-017); cwd-relative `.env` (WB-026); `--set KEY=value` guidance is non-functional and non-TTY-hostile (WB-025); `unofficial`/chatgpt-web scaffolding present (WB-027). **(4)**
- **Frontend:** ~400 LOC hardcoded mock chat scripts inside live store actions (WB-011); `partialize` persists only `{operator,theme}`; silent fixture fallback with only a global `reconnecting` flag, no per-domain error/loading/empty/stale state (UX-018 class) across all 8 domain loaders. **(3)**
- **API contract / stubs:** authoring apply/revert assert fake success (WB-008/N04); `/skills`,`/og/graph`,`/spend`,schedule,memory-write empty-vs-503 (WB-010/N05); backend-ahead memory writes unused by SPA (D-4). **(3)**
- **Error handling/observability:** silent-swallow persistence failures incl. fake-uuid on write failure (WB-013); catch-and-discard recall (WB-030); untracked daemon threads (WB-014); sparse tracing, no spans in new routes (WB-015); magic-string turn exits (WB-029). **(5)**
- **Infra/CI/deps:** broken contract job (WB-001); strict gates non-blocking + 36-file subset (WB-002); PyPI-only release, no image/SBOM/sign (WB-003); phantom compose Dockerfile (WB-004); root + `0.0.0.0` (WB-005); no HEALTHCHECK + 3.12/3.11 drift (WB-006); no dep-CVE/SAST/Dependabot anywhere (only `ci.yml`+`release.yml`); floored deps + suspect pytest floor (WB-022); redundant extras (WB-023); frontend no test tooling (WB-024). **(10)**
- **Modularity/perf-in-code:** monoliths (`cli/main.py` 2,886 LOC) (WB-016); per-session boot beyond shared services (WB-028); async-calling-sync new routes (WB-N02); OpenAI streaming session leak (WB-021); duplicated builders/capability factory (WB-031). **(5)**
- **Encoding:** BOM + mojibake in `client/models.py` (WB-N01). **(1)**

---

## 6. Severity tally (this report)

| Severity | New this report | Prior still-relevant |
|----------|-----------------|----------------------|
| Critical | 1 (WB-N06/BB-001-002) | WB-001 |
| High | 1 (WB-N07) | WB-004, WB-007, WB-009, WB-013 |
| Medium | 4 (WB-N01, WB-N02, WB-N03, WB-N04) | WB-002/003/008/011/012/016/017/019/022/024/025/028 |
| Low | 1 (WB-N05) | WB-006/010/014/015/018/020/021/023/026/027/029/030/031 |

**Prior WB findings:** 0 FIXED · 3 PARTIAL (WB-020, WB-022, WB-028) · 1 CHANGED (WB-031) · 27 STILL-OPEN.

---

## 7. CI — exact fix for WB-001 (Critical, unblocks all verification)

The `frontend-contract` job (`ci.yml:130-173`) targets a *different, earlier* frontend (TanStack **Start** server-functions + React Query) than the shipped SPA (TanStack **Router** + zustand). It hard-fails on `npm run … check:api` ("Missing script") before reaching the phantom file list. Two valid fixes:

**Option A — make the contract real (recommended).**
1. `frontend/package.json`: add `"openapi-typescript"` to `devDependencies`; add scripts:
   - `"generate:api": "openapi-typescript ../openapi/hachi-openapi.json -o src/lib/data/api-types.ts"`
   - `"check:api": "npm run generate:api && git diff --exit-code src/lib/data/api-types.ts"`
   - (optional) add `eslint` + `"lint": "eslint"` if lint is desired.
2. `ci.yml:158-173`: keep `Export OpenAPI` + `check:api` + `typecheck`; **delete** the `Frontend API lint` step's phantom file list (or point `lint` at the real tree). Then wire the generated `api-types.ts` into the adapters so drift (D-1…D-4) actually fails CI.
3. Fix CLAUDE.md's `generate:api`/`check:api` instructions to match the real scripts.

**Option B — stop-gap (if A is too large now).** Replace the three frontend steps with just `npm run --prefix frontend typecheck` (which exists) and remove `check:api` + the lint file list, so the job stops hard-failing. This unblocks CI but leaves the contract unguarded (so it's a bridge to Option A, not a destination).

Either way, **do this first** — until CI is green and meaningful, none of the other fixes can be trusted as landed.

---

*End of v2 report. New findings: 1 Critical, 1 High, 4 Medium, 1 Low. Prior WB findings: 0 FIXED, 3 PARTIAL, 1 CHANGED, 27 STILL-OPEN. OpenAPI export current; contract unguarded (WB-001) and unconsumed by the SPA.*
