# QA Agent 2 — White-Box Source Audit (Hachi)

**Date:** 2026-06-18 · **Scope:** deep source read of `src/hachi/` (Python backend), `frontend/src/` (untracked SPA), `openapi/hachi-openapi.json`, CI/CD, Docker/compose. **Mode:** read-only.

This report builds on `docs/strategy/01-architecture-review.md` (2026-06-12), `docs/QA_BUG_REPORT.md`, and `docs/web/backend-api-gaps.md`. **An important meta-finding up front:** the architecture review (01) is now materially **stale** — several of its top-severity findings have since been fixed in the working tree and should not be re-reported as open:

- **TD-1 (concurrent `chat()` corrupts history) — FIXED.** `AgentSession` now has a per-session `threading.Lock` (`_chat_lock`) acquired in `chat()` with a `session.busy` event on contention (`gateway/orchestrator.py:432, 1014–1039`).
- **TD-2 (no streaming) — FIXED.** `ExecutionLoop._stream_generate` + `generate_stream` drive per-chunk `text.stream.delta` events end-to-end through SSE and the OpenAI facade (`core/loop.py:426–479, 713–719`; `gateway/fastapi_app.py:583–689`).
- **TD-4 (async routes block the event loop) — FIXED.** `openai_chat_completions`, `channel_inbound`, and `create_session` now wrap the synchronous turn in `anyio.to_thread.run_sync` (`fastapi_app.py:385, 566, 728`).
- **TD-13 (sequential tools) — FIXED.** A per-loop `ThreadPoolExecutor` runs batched tool calls concurrently, preserving result order (`loop.py:322–324, 838–882`).
- **TD-15 (500-handler leak; "GET /" 404) — FIXED.** The catch-all handler now returns a generic message + correlation id and logs details server-side only (`fastapi_app.py:318–339`); a root route exists (`fastapi_app.py:353–361`).
- **M-11 (OpenAPI omits auth scheme) — FIXED.** A custom `app.openapi()` injects `BearerAuth` + per-route 401s; the exported spec carries `securitySchemes.BearerAuth` (`fastapi_app.py:880–908`, verified in `openapi/hachi-openapi.json`).
- **TD-3 (in-memory-only queue) — PARTIALLY FIXED** (now a `SQLiteJobQueue` + resident worker; see WB-019 for the residual durability gap).

The findings below are the issues that remain **live in the code as read today**. Each is evidence-backed.

---

## Severity tally

| Severity | Count |
|----------|------|
| Critical | 1 |
| High | 7 |
| Medium | 18 |
| Low | 9 |
| **Total** | **35** |

---

## CI / CD

### WB-001 — `frontend-contract` CI job lints/runs files & scripts that do not exist (job hard-fails every run)
- **Category:** CI-CD · **Severity:** Critical · **Complexity:** M
- **Root cause:** `.github/workflows/ci.yml:130–174` runs `npm run --prefix frontend check:api`, `npm run --prefix frontend typecheck`, and `npm run --prefix frontend lint -- <files>`. But (a) `frontend/package.json` defines **only** `dev/build/preview/typecheck` — there is **no `check:api` and no `lint` script**; (b) every file passed to the lint step is **missing** from the actual frontend: `src/lib/api/hachi.server.ts`, `src/lib/api/hachi.functions.ts`, `src/types/api.ts`, `src/hooks/use-gateway-queries.ts`, `src/components/screens/session-workbench.tsx`, `src/components/screens/logs-screen.tsx`, `src/routes/s.$slug.tsx` (all confirmed absent). The real SPA lives under `src/lib/data/`, `src/lib/store/`, `src/routes/*.tsx`, `src/components/`. The CI job describes a *different, earlier* frontend architecture (TanStack Start server functions + React Query) than the one in the repo (TanStack **Router** SPA + zustand).
- **Impact:** `npm run check:api` exits non-zero (missing script) → the job fails on every push/PR. Either the job is currently red and ignored (so the contract is effectively unguarded — the exact `frontend-contract` regression 01 wanted to prevent), or it was never wired to the shipped frontend. CLAUDE.md's documented loop (`npm run generate:api`, `check:api`) is **unimplementable**: there is no `openapi-typescript` dependency and no `generate:api` script.
- **Recommended solution:** Rewrite the `frontend-contract` job against the real SPA: add `openapi-typescript` + `generate:api`/`check:api` scripts (diff `openapi/hachi-openapi.json`→generated `src/lib/data/types.ts`), point `typecheck` at the real tree, and drop the phantom file list (or add `eslint` + a `lint` script if lint is desired). Fix CLAUDE.md to match.
- **Files:** `.github/workflows/ci.yml:130–174`; `frontend/package.json:6–11`; absent: `frontend/src/lib/api/*`, `frontend/src/types/api.ts`, `frontend/src/hooks/`, `frontend/src/components/screens/`.

### WB-002 — Strict type/lint gates are non-blocking on `main`; strict-mypy gate covers only ~28 files
- **Category:** CI-CD · **Severity:** Medium · **Complexity:** L
- **Root cause:** `pyproject.toml` declares `[tool.mypy] strict = true` globally, but `ci.yml:78–84` runs `ruff check` and `mypy src` with `continue-on-error: true`. The only blocking type check is `mypy --config-file mypy-strict-gate.ini` gated `if: github.event_name == 'pull_request'` (`ci.yml:86–88`), and `mypy-strict-gate.ini` enumerates only a hand-picked ~28-file "currently clean" subset (most of `tools/`, `tui/`, `jobs/`, `loop.py`, `llm.py`, `orchestrator.py` are excluded). Lint/broad-mypy never block.
- **Impact:** "strict mypy" is aspirational; the bulk of the codebase has no enforced typing or lint floor. Regressions land freely on `main`.
- **Recommended solution:** Either expand the strict gate incrementally (add modules as they go clean) or flip `ruff check` to blocking now (ruff is reported clean). Make the broad `mypy` at least blocking on PRs once Optional/`Any`-base debt in `integrations/*` is burned down.
- **Files:** `pyproject.toml:108–111`; `mypy-strict-gate.ini:12–48`; `.github/workflows/ci.yml:78–88`.

### WB-003 — `release.yml` ships only the PyPI wheel — no Docker image, no frontend, no SBOM/signing
- **Category:** CI-CD · **Severity:** Medium · **Complexity:** M
- **Root cause:** `release.yml` builds + publishes only the Python package via PyPI Trusted Publishing (OIDC — good). Despite a shipped `Dockerfile` + `docker-compose.yml`, no container image is built/pushed to any registry; the `frontend/` SPA is never built or deployed; there is no SBOM, Sigstore/cosign signing, SLSA provenance, or GitHub Release asset upload. The release build also does not re-run the test suite as a gate (relies on `ci.yml` having passed on the commit).
- **Impact:** Operators who follow the Docker/compose path get no published image; the web UI has no release artifact at all. Supply-chain posture is thin for an OSS security-adjacent tool.
- **Recommended solution:** Add a Docker build+push job (GHCR) on tag, a frontend build/publish step, and SBOM + provenance attestation (`actions/attest-build-provenance`).
- **Files:** `.github/workflows/release.yml`; `Dockerfile`; `docker-compose.yml`.

---

## Infrastructure

### WB-004 — `docker-compose` frontend service references a non-existent `frontend/Dockerfile`
- **Category:** Infra · **Severity:** High · **Complexity:** S
- **Root cause:** `docker-compose.yml:21–24` builds the `frontend` service with `context: ./frontend, dockerfile: Dockerfile`, but `frontend/Dockerfile` **does not exist** (confirmed). The backend `Dockerfile` builds only the Python package — no Node stage.
- **Impact:** `docker compose build` / `up` fails for the frontend service. The advertised full-stack compose deployment is broken out of the box.
- **Recommended solution:** Add `frontend/Dockerfile` (Node build → static serve, e.g. nginx) and a prod-mode token/base-URL wiring, or remove the frontend service until it exists.
- **Files:** `docker-compose.yml:21–35`; missing `frontend/Dockerfile`.

### WB-005 — Container runs as root and binds `0.0.0.0:8787` published to the host
- **Category:** Infra / Secrets · **Severity:** Medium · **Complexity:** S
- **Root cause:** `Dockerfile` has **no `USER` directive** (runs as root; volumes mount into `/root/...`). `docker-compose.yml:7–8,19` publishes `8787:8787` and binds `--host 0.0.0.0`, reachable on the LAN. Auth *is* on by default in compose via the `gateway_api_token` Docker secret, which mitigates exposure — but a root process on a network-published port is a poor default, and the gateway supports `HACHI_GATEWAY_NO_AUTH=1`.
- **Impact:** Larger blast radius on container compromise; LAN-reachable control plane.
- **Recommended solution:** Add a non-root `USER`, drop Linux capabilities, and document binding to `127.0.0.1` (or a reverse proxy) for non-isolated hosts.
- **Files:** `Dockerfile:1–15`; `docker-compose.yml:2–19`.

### WB-006 — No container healthcheck; Dockerfile Python 3.12 vs CI/test matrix 3.11
- **Category:** Infra · **Severity:** Low · **Complexity:** S
- **Root cause:** No `HEALTHCHECK` in `Dockerfile` and no `healthcheck:` in compose, so `frontend.depends_on: hachi` waits for container *start*, not gateway *readiness* (a `/readiness` endpoint exists and is unused here). Separately, `Dockerfile:1` is `FROM python:3.12-slim` while CI pins `python-version: "3.11"` and pyproject floors `>=3.11` — the shipped runtime differs from the tested one.
- **Impact:** Race on frontend start; runtime/test divergence can mask 3.12-only behavior.
- **Recommended solution:** Add a `HEALTHCHECK` curling `/readiness`; pin the image to 3.11 to match the test matrix (or add 3.12 to CI).
- **Files:** `Dockerfile:1`; `docker-compose.yml:26–27`.

---

## API contract drift

> The OpenAPI export was verified against the live FastAPI routes: **all 40 paths match**, including every new uncommitted endpoint (`/skills`, `/memory`, `/memory/recall`, `/og/graph`, `/authoring/*`, `/spend`, `PUT /capabilities/{id}/enabled`), and `securitySchemes.BearerAuth` is present. The route↔OpenAPI layer is in good shape. The drift is on the **frontend↔event-payload** side and in **stub backends**.

### WB-007 — Web "leash" allow/deny is a no-op: never resolves the agent's pending approval
- **Category:** API Contract · **Severity:** High · **Complexity:** M
- **Root cause:** During a live chat turn the SSE stream emits `permission.requested`, and the UI shows an in-context leash. But `allowLeashLive()` / `denyLeashLive()` (`frontend/src/lib/data/domains/chat.ts:395–414`) only clear local state with a comment "In a full implementation, POST to an approval endpoint here" — they never call `POST /approvals/{request_id}`. Worse, the event-payload contract is mismatched: `handleSseMessage` reads `payload.request / action / why` (`chat.ts:216–225`), but the gateway/registry payload (per `docs/web/backend-api-gaps.md` §7 and `core/approvals.py`) is keyed `request_id / operation / tool / reason`. There is no `request_id` in the UI's read, so it could not resolve the approval even if it tried.
- **Impact:** From the web UI, "Allow" lets the agent's pending action **time out and get denied** (or hang), and "Deny" just aborts the local stream. The headline "in-context leash" feature is non-functional in live mode.
- **Recommended solution:** Guarantee `request_id` (+ human fields) in the `permission.requested` payload (backend), then have `allowLeashLive`/`denyLeashLive` call `POST /approvals/{request_id}` with `allow_once`/`deny`. Align the `handleSseMessage` keys to the real event shape.
- **Files:** `frontend/src/lib/data/domains/chat.ts:216–225, 395–414`; `src/hachi/gateway/fastapi_app.py:760–793` (approvals route exists); `src/hachi/core/approvals.py`.

### WB-008 — Authoring `apply`/`revert` endpoints are stubs that write nothing
- **Category:** API Contract / Tech Debt · **Severity:** Medium · **Complexity:** M
- **Root cause:** `POST /authoring/targets/{name}/apply` and `/revert` resolve to `control_plane.authoring_apply/revert` (`control_plane.py:496–504`), which call CLI handlers whose own code comments say the proposed-patch pipeline does not exist (`cli/main.py:703,714` — "when the proposed-patch pipeline exists, apply the pending diff here"). The frontend `applyPatch()` then optimistically toasts "Patch applied — re-indexed in 0.3s" (`store.ts:890–898`) regardless.
- **Impact:** The Authoring screen claims success and shows a fabricated re-index time while nothing is written or reverted — a silent no-op that misleads the user. Also a latent security surface once implemented (writing `.og`/config files; the gap doc flags it must be `PermissionGate`-scoped).
- **Recommended solution:** Either return `501`/a clear "not implemented" until the patch pipeline lands, or implement it behind `PermissionGate` workspace-root checks. Stop the UI from asserting success on a stub.
- **Files:** `src/hachi/gateway/control_plane.py:496–504`; `src/hachi/cli/main.py:703,714`; `frontend/src/lib/data/domains/authoring.ts`; `frontend/src/lib/store/store.ts:890–911`.

### WB-009 — Web UI per-turn spend double-counts cumulative cost on every `usage.updated`
- **Category:** API Contract / State Mgmt · **Severity:** Medium · **Complexity:** S
- **Root cause:** `loop.py:760` emits `usage` with the **cumulative** `CostTracker.snapshot().to_dict()` (i.e. `estimated_cost_usd` is the running session total). The frontend `handleSseMessage` `usage.updated` handler **adds** that value to `spent`/`taskSpent` each event: `taskSpent + costUsd` (`chat.ts:227–243`). Because the payload is cumulative, summing successive snapshots inflates spend geometrically within a turn.
- **Impact:** The web budget/spend readout is wrong (over-counts) for any multi-iteration turn. Misleads users watching cost.
- **Recommended solution:** Either emit a per-event delta in the usage payload, or have the client *set* `taskSpent`/`spent` to the snapshot value (not add). The `/spend` endpoint should be the authoritative aggregate.
- **Files:** `src/hachi/core/loop.py:756–760`; `frontend/src/lib/data/domains/chat.ts:227–243`.

### WB-010 — `/skills` and `/og/graph` silently return empty instead of 503 when unattached
- **Category:** API Contract / Error Handling · **Severity:** Low · **Complexity:** S
- **Root cause:** Unlike sibling handlers that use `_require_handler` (→ 503 `GatewayUnavailableError`), `control_plane.skills()` returns `[]` and `og_graph()` returns `{"nodes": []}` when their handler is `None` (`control_plane.py:464–468, 480–484`); `spend()` likewise returns empty (`506–510`).
- **Impact:** A misconfigured/unattached runtime is indistinguishable from "genuinely no skills" — the frontend renders an empty Skills/OG screen with no error signal. Inconsistent with the rest of the control plane's fail-loud contract.
- **Recommended solution:** Use `_require_handler` for these too (503 when unattached), or document the empty-list-as-unattached convention and surface it in the UI.
- **Files:** `src/hachi/gateway/control_plane.py:464–468, 480–484, 506–510`.

---

## State Management

### WB-011 — zustand store ships large hardcoded mock chat scripts and persists almost nothing
- **Category:** State Mgmt / Tech Debt · **Severity:** Medium · **Complexity:** M
- **Root cause:** `frontend/src/lib/store/store.ts` interleaves live and mock logic in the same actions. The mock branch of `startTask` (`store.ts:1025–1088`), `icAllow` (`1121–1154`), and the entire `openRun` run-simulation queue (`1263–1313`) contain hardcoded narrative fixtures ("Priya — Q3 deck feedback", "reader-1 → PR #482 auth refactor", fabricated costs). `partialize` persists only `{operator, theme}` (`store.ts:1389–1392`) — so provider config, safety tier, sessions, etc. are lost on reload (acceptable for server-backed data, but the live/mock split is fragile).
- **Impact:** ~400 lines of demo script live in production state logic; a `dataMode` regression would surface canned fake content to real users. High maintenance coupling between presentation and the simulated state machine.
- **Recommended solution:** Extract the mock simulator into a separate dev-only module gated by `USE_MOCKS`; keep `store.ts` actions thin over `hydrate` + domain loaders.
- **Files:** `frontend/src/lib/store/store.ts:1025–1313, 1389–1392`.

### WB-012 — Per-turn frozen snapshot runs a constant `semantic_search("important context")` every turn
- **Category:** State Mgmt / Scalability · **Severity:** Medium · **Complexity:** S
- **Root cause:** `_build_frozen_snapshot()` runs `self.vault.semantic_search("important context", top_k=8)` on **every** `chat()` call (`orchestrator.py:855–894`), in addition to the user-query prefetch. The query string is fixed, so the result is cache-unfriendly and adds a Chroma round-trip to the hot path of each turn.
- **Impact:** A constant latency tax per turn and cache-busting prompt-prefix churn; scales poorly as the vault grows.
- **Recommended solution:** Cache the frozen snapshot per session and invalidate on vault writes (it is explicitly "frozen at session start" per the prompt label — yet recomputed each turn). 01's R20 proposed this.
- **Files:** `src/hachi/gateway/orchestrator.py:855–894, 1056`.

---

## Error Handling / Observability

### WB-013 — Silent-swallow `except Exception` masks credential-store and memory-write failures
- **Category:** Error Handling · **Severity:** High · **Complexity:** M
- **Root cause:** 320 `except Exception` sites across 80 files (0 bare `except:` — guardrail respected). The dangerous subset silently degrades persistence with no log: `core/memory_provider.py:236` swallows an upsert failure and **returns a fake `uuid4()` node id** so the caller believes the write succeeded; `core/secret_store.py:48–50, 101–102, 148–149, 261–262` swallow corrupt-metadata, keyring-delete, corrupt-blob, and secret-read failures (read returns `None`, indistinguishable from "not set"); `core/memory_vault.py:184, 484, 555, 572` return `False`/`""`/`[]` on any error; `gateway/orchestrator.py` has ~11 `pass`-swallows in snapshot/recall paths. None emit a warning.
- **Impact:** A user is told "I'll remember that" when the write silently failed; a deleted credential may persist; a broken vault looks like "no results". Matches 01's W4/TD-5, still live.
- **Recommended solution:** Convert persistence-path swallows to log-and-(optionally)-surface; never fabricate a success id (`memory_provider.py:236`). Route all memory writes through the job queue so `job.failed` read-models flag breakage.
- **Files:** `src/hachi/core/memory_provider.py:236–249`; `src/hachi/core/secret_store.py:48,101,148,261`; `src/hachi/core/memory_vault.py:184,484,555,572`; `src/hachi/gateway/orchestrator.py:758,774,886,971,1111,1124,1165,1186,1201`.

### WB-014 — Untracked fire-and-forget daemon threads in `core/notifier.py` (violates repo guardrail)
- **Category:** Error Handling / Tech Debt · **Severity:** Medium · **Complexity:** S
- **Root cause:** CLAUDE.md mandates "prefer queued background work over fire-and-forget threads." `core/notifier.py:136` and `:223` spawn `threading.Thread(target=_run, daemon=True).start()` with **no handle, no join, no queue**. Secondary untracked spawns: `core/mcp_client.py:194, 258` (boot + stderr drain), `integrations/google/meet.py:282` (also flagged in QA_BUG_REPORT L-16), `tui/app.py:3129, 3294`.
- **Impact:** Notification errors vanish; threads aren't joined on shutdown; inconsistent with the durable-queue architecture. On process exit a notify can be lost mid-flight.
- **Recommended solution:** Route notifier work through `jobs/queue.py` (the resident worker already exists), or at minimum retain + join the threads on shutdown.
- **Files:** `src/hachi/core/notifier.py:136, 223`; `src/hachi/core/mcp_client.py:194, 258`; `src/hachi/integrations/google/meet.py:282`.

### WB-015 — Tracing spans are sparse (3 span types) and unconnected to runtime events
- **Category:** Logging-Observability · **Severity:** Low · **Complexity:** M
- **Root cause:** `core/tracing.py` is a clean, opt-in OTel wrapper, but only three span sites exist: `llm.generate`, `tool.call` (`loop.py:340, 722`) and none in the gateway request path, MCP calls, memory ops, or sub-agent delegation. Spans carry no trace/span IDs into the rich `runtime_events` taxonomy, so the two observability systems are disjoint.
- **Impact:** Distributed-trace coverage is thin; you can't correlate a slow turn to a specific tool/MCP/memory span, and the runtime-event log isn't trace-linked.
- **Recommended solution:** Add spans at gateway entry, MCP client calls, vault read/write, and delegation; attach `trace_id` to emitted runtime events.
- **Files:** `src/hachi/core/tracing.py`; `src/hachi/core/loop.py:340, 722`.

---

## Modularity / Tech Debt

### WB-016 — Monolith files concentrate change risk (4 files > 1,700 LOC)
- **Category:** Modularity · **Severity:** Medium · **Complexity:** XL
- **Root cause:** `tui/app.py` (3,386 LOC), `cli/main.py` (2,747), `gateway/orchestrator.py` (1,961), `og_engine/engine.py` (1,711). `cli/main.py` mixes arg-parsing, gateway bridge, config mutation, provider setup, secrets, doctor, permissions; `orchestrator.py` mixes boot + session + 12 read-model snapshots + prefetch + scheduling. (Note `tui/app.py` is being edited by another session — **TUI in flight**; report only.)
- **Impact:** High merge-conflict surface, hard to test units in isolation, slow comprehension. 01's TD-9.
- **Recommended solution:** Decompose `cli/main.py` into `cli/commands/*` over a shared `RuntimeBuilder` (Typer sub-apps already exist as seams); split `AgentSession` snapshots into a `ReadModelFacade`.
- **Files:** `src/hachi/cli/main.py`; `src/hachi/gateway/orchestrator.py`; `src/hachi/og_engine/engine.py`; `src/hachi/tui/app.py`.

### WB-017 — Config-read sprawl: 110 `os.environ`/`getenv` sites, no central typed Settings
- **Category:** Config · **Severity:** Medium · **Complexity:** L
- **Root cause:** 110 raw env reads across 27 files; top offenders `cli/main.py` (27), `core/onboarding.py` (14), `core/llm.py` (5), `gateway/orchestrator.py` (4), `gateway/fastapi_app.py` (4). A typed `MemorySettings` model exists (`core/settings.py`) but covers only memory config. `core/onboarding.py:131` mutates `os.environ[...]` in place.
- **Impact:** Scattered defaults, no single source of truth, hard to validate or document the full env surface; import-time captures (per 01) ignore later changes.
- **Recommended solution:** Extend the `Settings` model to gateway/voice/notifier/channel/limits env vars; read once at boot and inject. 01's R12.
- **Files:** `src/hachi/cli/main.py`; `src/hachi/core/onboarding.py:118–370`; `src/hachi/core/llm.py`; `src/hachi/core/settings.py`.

### WB-018 — Hardcoded `surface="cli"` in the gateway turn path; per-turn TurnContext mislabels web/webhook origin
- **Category:** Tech Debt · **Severity:** Low · **Complexity:** S
- **Root cause:** `_chat_impl` builds every `TurnContext` with `surface="cli"  # TODO: configurable from caller` (`orchestrator.py:1087`), even when the turn originates from the web SPA, OpenAI facade, or a channel webhook. `TurnContext.surface` exists precisely for audit/tracing of origin.
- **Impact:** Audit/trace data attributes all gateway turns to "cli", undermining origin-based auditing and any per-surface policy.
- **Recommended solution:** Thread the originating surface from `create_session`/`complete_chat` into `chat()` → `TurnContext`.
- **Files:** `src/hachi/gateway/orchestrator.py:1078–1088`.

### WB-019 — "Durable" job queue is durable only for registry-dispatch jobs; all of the agent's actual jobs are non-durable
- **Category:** Tech Debt / Scalability · **Severity:** Medium · **Complexity:** M
- **Root cause:** `SQLiteJobQueue` persists only `handler_name`+`payload` jobs; live-`func` jobs are held in an in-memory `_func_table` keyed by job_id (`jobs/queue.py:556, 643, 661`) and are **explicitly discarded on restart** (`queue.py:591, 601` — "Discard func-only jobs that survived a restart — they have no handler_name"). But every job the orchestrator enqueues is a live-`func` job: prefetch (`orchestrator.py:937–946`), title generation (`975–989`), and memory sync (via `MemoryManager`). None set `handler_name`.
- **Impact:** The "durable queue that survives restarts" (CLAUDE.md, queue docstring) does not actually persist any real workload — prefetch/title/fact-extraction jobs are lost on restart exactly like the old in-memory queue. The durability is real only for code paths that don't exist yet.
- **Recommended solution:** Migrate the orchestrator's jobs to the `JobRegistry` (`handler_name` + serializable payload) so they survive restarts, per 01's R5 assumption.
- **Files:** `src/hachi/jobs/queue.py:556, 591, 601, 643, 661`; `src/hachi/gateway/orchestrator.py:937–946, 975–989`.

### WB-020 — Global skill-node namespace: same-named `.og` nodes silently overwrite
- **Category:** Tech Debt / Scalability · **Severity:** Medium · **Complexity:** M
- **Root cause:** Carried from 01's W7/TD-7 and still present: the skill engine keys nodes by bare `node.name` in a flat dict, so two `.og` files defining the same node name overwrite each other (user `learned/` skills can shadow shipped ones). The new `snapshot_og_graph()` (`orchestrator.py:780–849`) also keys result nodes by bare `node.name`, inheriting the collision.
- **Impact:** Silent shadowing; a community/agent-authored pack can override a shipped skill node with no warning. Data-quality failure at ecosystem scale.
- **Recommended solution:** Qualify node IDs as `<file-stem>:<node>` with bare-name resolution when unambiguous; warn on collision at load.
- **Files:** `src/hachi/og_engine/engine.py` (node indexing); `src/hachi/gateway/orchestrator.py:780–849`.

### WB-021 — `_api_sessions` facade map can accumulate one auto-session per OpenAI stream id
- **Category:** Scalability · **Severity:** Low · **Complexity:** S
- **Root cause:** The OpenAI streaming path keys an auto-session as `openai-{chat_id}` with a fresh `uuid4` per request (`fastapi_app.py:587`), so each streamed completion creates a **new** gateway session (booting a runtime via `on_connect`) and never closes it. Non-streaming completions reuse the default key (fine). `complete_chat`'s default key reuse mitigates only the non-stream path.
- **Impact:** Under load each streamed OpenAI request can boot+leak a session toward the `max_active_sessions=100` cap (and, pre-shared-services, a full MCP/Chroma runtime). Close logic exists (`close_session`) but the streaming path never calls it.
- **Recommended solution:** Reuse a stable per-client session key for streaming, or close the `openai-{chat_id}` session in the stream's `finally`.
- **Files:** `src/hachi/gateway/fastapi_app.py:585–590, 624–671`; `src/hachi/gateway/control_plane.py:287–319`.

---

## Dependencies

### WB-022 — All Python deps floored (`>=`) with no lockfile; `pytest>=9.0.3` floor is likely unsatisfiable
- **Category:** Dependencies · **Severity:** Medium · **Complexity:** S
- **Root cause:** `pyproject.toml:34–50` floors every runtime dep (`fastapi>=0.136.3`, `anthropic>=0.105.2`, `chromadb>=1.5.9`, …) with no upper bounds, and no `uv.lock` is committed (per git status). The `dev` extra floors `pytest>=9.0.3` (`pyproject.toml:53–57`) — pytest 9.x is an aggressive/likely-typo floor (8.x was current at the Jan 2026 cutoff), which can make `uv sync --extra dev` unresolvable.
- **Impact:** Non-reproducible installs; a new major of any dep can break CI/users without warning; the pytest floor risks a hard resolve failure.
- **Recommended solution:** Commit `uv.lock`; add upper bounds on volatile deps (anthropic, fastapi, chromadb); verify/correct the pytest floor.
- **Files:** `pyproject.toml:34–57`.

### WB-023 — Redundant duplicate deps; `memory`/`local-models` extras duplicate core deps
- **Category:** Dependencies · **Severity:** Low · **Complexity:** S
- **Root cause:** `chromadb` is both a core dep (`pyproject.toml:40`) and the `memory` extra (`68–70`); `httpx` is core (`39`) and in the `local-models` extra (`71–73`). The `unofficial` extra is empty (`84–87`, gated by `HACHI_UNOFFICIAL=1`) — harmless but a placeholder.
- **Impact:** The `memory` extra is a no-op (Chroma already installed); confusing dependency surface.
- **Recommended solution:** Drop the redundant extras or make core deps optional and let extras pull them in (not both).
- **Files:** `pyproject.toml:38–87`.

### WB-024 — Frontend has zero test tooling and no API-type generator
- **Category:** Dependencies / Testing · **Severity:** Medium · **Complexity:** M
- **Root cause:** `frontend/package.json` has **no** vitest/jest/testing-library/playwright, **no** `openapi-typescript`, **no** eslint/`lint` script. Only `prettier` appears transitively. `tsconfig.json` sets `strict:true` but relaxes `noUnusedLocals`/`noUnusedParameters` to false.
- **Impact:** A multi-screen SPA with live SSE/state logic has no automated test coverage and no enforced lint; the documented `generate:api` contract can't run (see WB-001). 0 `*.test.*`/`*.spec.*` files exist under `frontend/src`.
- **Recommended solution:** Add vitest + testing-library for the store/adapters, `openapi-typescript` for `types.ts` generation, and eslint; wire into the (rewritten) CI job.
- **Files:** `frontend/package.json`; `frontend/tsconfig.json`.

---

## Secrets / Config

### WB-025 — Credential error guidance points to an unsupported command form
- **Category:** Secrets · **Severity:** Medium · **Complexity:** S
- **Root cause:** `cli/main.py:1256, 1262` instruct users to run `hachi secrets --set {env_name}=<your-key>`, but the `--set` option takes a bare KEY (`metavar="KEY"`, `cli/main.py:2306`) and **interactively prompts** for the value, rejecting non-TTY (`2326–2329`). Passing `KEY=value` would store a secret literally named `ANTHROPIC_API_KEY=<your-key>`. `credential_vault.py:38` also suggests `hachi secrets --set …` in contexts (TUI/gateway) that are non-TTY, where `--set` aborts. (Note: the older QA_BUG_REPORT GLOBAL-1 claim about `~/.hachi/vault.json` is now **stale** — the real fallback is platformdirs `state_dir/secrets.enc`, and the error text references no such file.)
- **Impact:** Users hitting a missing credential get instructions that don't work or, in non-TTY surfaces, can't be followed at all — an onboarding dead-end.
- **Recommended solution:** Either accept `KEY=value` in `--set`, or fix the guidance to the prompt form; provide a non-TTY-safe path for TUI/gateway.
- **Files:** `src/hachi/cli/main.py:1256, 1262, 2300–2342`; `src/hachi/core/credential_vault.py:38`.

### WB-026 — LLM API-key fallback reads `.env` from `Path.cwd()`
- **Category:** Secrets / Config · **Severity:** Low · **Complexity:** S
- **Root cause:** `core/llm.py:39–53` falls back to `Path.cwd() / ".env"` (line 52) for the provider key, hand-parsed with naive quote-stripping. Secret resolution thus varies with the directory the process is launched from.
- **Impact:** A stray `.env` in any launch dir is silently honored; surprising/footgun behavior, and not validated.
- **Recommended solution:** Resolve `.env` from a fixed config dir (platformdirs) or require explicit opt-in; remove cwd-relative loading. 01's TD-17.
- **Files:** `src/hachi/core/llm.py:39–53`.

### WB-027 — `chatgpt-web` / `unofficial` reverse-engineered provider remains a reputational surface
- **Category:** Config · **Severity:** Low · **Complexity:** S
- **Root cause:** The `unofficial` extra (`pyproject.toml:84–87`, gated `HACHI_UNOFFICIAL=1`) and the reverse-engineered web provider path (per 01's W8/TD-16, `core/llm.py`) ship in-repo with a ToS-risk comment. Currently the extra is empty, but the gate + provider scaffolding remains.
- **Impact:** ToS/legal/reputational surface for an OSS project even if dormant.
- **Recommended solution:** Move to an out-of-core community plugin with an explicit risk README, or keep the extra empty and remove the provider scaffolding until needed.
- **Files:** `pyproject.toml:84–87`; `src/hachi/core/llm.py`.

---

## Architecture / Performance (code-level flags for the Perf/Security agents)

### WB-028 — Per-session full runtime boot in the gateway (mitigated by shared services, but still per-session loop/memory/MCP wiring)
- **Category:** Scalability · **Severity:** Medium · **Complexity:** L
- **Root cause:** `create_session` → `boot_agent` runs an 11-step boot per session. `ProcessSharedServices` now shares the skill engine, vault, graph index, and memory writer across sessions (`orchestrator.py:1407–1623`), which fixes the worst of 01's TD-8 (no longer 100 Chroma clients). But each session still constructs its own `ExecutionLoop`, `MemoryManager`, `MCPManager` + MCP subprocess set, `IntegrationRuntime`, schedule runner, and resident worker thread.
- **Impact:** With `max_active_sessions=100`, up to 100 MCP server sets / resident worker threads / schedule runners per process. Single-process ceiling for the always-on ambition (01's §5.1).
- **Recommended solution:** Extend shared services to a single MCP manager + scheduler per process; per-session only loop+memory. 01's R13.
- **Files:** `src/hachi/gateway/orchestrator.py:1284–1961`; `src/hachi/gateway/shared_services.py`.

### WB-029 — Iteration-limit and budget exits return error-shaped strings rather than structured status
- **Category:** Error Handling · **Severity:** Low · **Complexity:** S
- **Root cause:** `loop.run()` returns sentinel strings like `"[INCOMPLETE] …"`, `"[BUDGET LIMIT REACHED] …"`, `"[TIME LIMIT REACHED] …"`, `"[CANCELLED] …"` (`loop.py:631, 660, 670, 735, 914`). The SSE emits a structured `session.iteration.limit` event, but the chat HTTP response body is a magic string the frontend must pattern-match.
- **Impact:** Clients string-match magic prefixes to detect incomplete/over-budget turns — brittle. 01's TD-13/R15 (the iteration case now degrades more gracefully than "raise", which is an improvement).
- **Recommended solution:** Return a structured result (status + partial text) from the chat surface, or document the sentinel contract and surface it in OpenAPI.
- **Files:** `src/hachi/core/loop.py:631, 660, 670, 735, 906–917`.

### WB-030 — Catch-and-discard around prefetch/recall can serve stale-but-empty context without signal
- **Category:** Error Handling · **Severity:** Low · **Complexity:** S
- **Root cause:** `_chat_impl` falls back to a synchronous `vault.semantic_search(user_input, …)` wrapped in `except Exception: recalled = []` (`orchestrator.py:1060–1064`), and `_run_prefetch_job` swallows search errors to `[]` (`orchestrator.py:913–926`). A broken vault yields "no recall" silently every turn.
- **Impact:** Memory recall can be silently disabled by a vault fault; the agent loses long-term context with no operator signal.
- **Recommended solution:** Log/emit a one-shot warning event when recall fails (distinct from "no hits").
- **Files:** `src/hachi/gateway/orchestrator.py:913–926, 1060–1064`.

---

## Duplication

### WB-031 — Duplicated SSE/URL builders and rate-limit/allowlist parsing between client and SSE modules
- **Category:** Duplication · **Severity:** Low · **Complexity:** S
- **Root cause:** `buildUrl` (`client.ts:59–69`) and `buildSseUrl` (`sse.ts:38–48`) are near-identical query-string builders; the backend has parallel `RateLimiter` + allowlist-parse wiring for the OpenAI and channel routers (`fastapi_app.py:550, 711–712`). Capability `__import__(...)` factory blocks are repeated three times verbatim in `_build_capability_registry` (`orchestrator.py:315–353`).
- **Impact:** Minor maintenance drift risk; the capability `__import__` triple is the clearest copy-paste.
- **Recommended solution:** Share one `buildQuery` helper; factor the capability factory into a small helper keyed by module + class names.
- **Files:** `frontend/src/lib/data/client.ts:59–69`; `frontend/src/lib/data/sse.ts:38–48`; `src/hachi/gateway/orchestrator.py:315–353`.

---

## Testing coverage assessment

**Backend:** 185 tracked test files / ~1,300–2,200 test functions (per 01 and prior QA), with genuinely strong security suites (injection guards, redaction, RBAC) and an in-repo OG benchmark wired as a CI gate (`ci.yml:110–129`). Skip/xfail discipline is healthy — 01 observed 13 skips, no widespread `pytest.mark.xfail` abuse. Notable **gaps** (largely confirming 01's W9): no end-to-end harness drives a multi-turn, multi-tool `ExecutionLoop` against a scripted fake LLM exercising tool-error→`is_error`, mid-conversation compaction, budget/wall-clock stops, **mid-stream cancel/timeout** (the new `_StreamCancelled`/`_StreamTimedOut` paths in `loop.py:426–479` are intricate and high-value to cover), and the **per-session `_chat_lock` concurrency** (a 2-thread hammer test on one session). The new uncommitted gateway endpoints (`/skills`, `/memory`, `/og/graph`, `/authoring/*`, `/spend`, capability toggle) have backend handler tests per the memory note (21 new tests in `test_gateway_web_endpoints.py`), but the **stub semantics** (WB-008) and **empty-vs-503** behavior (WB-010) are exactly the kind of thing that needs explicit assertions. Over-mocking risk is moderate: many tests inject fakes at the LLM/vault boundary, which is appropriate, but there is little coverage of the *real* SQLite job durability boundary (WB-019) — a test asserting func-jobs survive restart would have caught that they don't.

**Frontend:** **Zero** test tooling and **zero** test files (WB-024). The store's live/mock split, SSE frame parser (`sse.ts`), and domain adapters (`chat.ts`, `skills.ts`, etc.) are entirely untested — including the double-count spend bug (WB-009) and the no-op leash (WB-007), both of which a unit test over `handleSseMessage` would surface immediately.

## API contract drift summary

The **route layer is clean**: `openapi/hachi-openapi.json` (40 paths) matches the FastAPI routes one-to-one, all new uncommitted endpoints are exported, and `BearerAuth` + per-route 401s are present — so the OpenAPI export step itself is current. Drift is concentrated at two seams: (1) the **event-payload contract** consumed by the frontend — `permission.requested` keys mismatch (`request`/`action`/`why` vs the registry's `request_id`/`operation`/`tool`), making the web leash a no-op (WB-007), and the cumulative-vs-delta `usage` payload causing spend double-counting (WB-009); and (2) **stub backends** that the UI treats as real (`authoring apply/revert` write nothing yet assert success — WB-008; `/skills`,`/og/graph`,`/spend` return empty instead of 503 — WB-010). The **CI contract job is non-functional** (WB-001), so none of this is currently guarded automatically, and the `generate:api`/`check:api` loop documented in CLAUDE.md cannot run.

---

*End of report. 35 findings (1 Critical, 7 High, 18 Medium, 9 Low).*
