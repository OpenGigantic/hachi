# Gateway (FastAPI REST + SSE) & OpenAPI Contract — QA Report

**Scope:** `src/tokoboros/gateway/fastapi_app.py`, `src/tokoboros/gateway/orchestrator.py`,
`src/tokoboros/gateway/control_plane.py`, `src/tokoboros/cli/main.py` (gateway cmd ~L1318, gateway-openapi ~L1379),
`openapi/tokoboros-openapi.json`.

**Environment:** Windows 11, Python 3.14.3, `./.venv/Scripts/python.exe`, `PYTHONPATH=src`.
fastapi 0.136.3 / uvicorn 0.48.0 / httpx 0.28.1 / starlette 1.2.1. Tier = `unrestricted`. Port **8795**.
Creds sourced from repo `.env` (`set -a; source .env; set +a`).

## Summary

The gateway **booted and served** cleanly on port 8795 (both with and without an auth token). Health,
readiness, capabilities, models, session create/list/state/close, runtime events, config, and the **SSE
stream** all behave correctly and return well-shaped JSON / event frames. **Auth gating is correct**
(401 without/with-wrong bearer, 200 with valid bearer; `/health` + `/readiness` stay public).
**The OpenAPI contract is in sync** — a fresh `gateway-openapi` export is **byte-identical** to the committed
`openapi/tokoboros-openapi.json`, so the CI `frontend-contract` job would pass. All **67 gateway tests pass**.

The headline issue: **every chat surface returns a raw, unstructured HTTP 500 (`text/plain` "Internal Server
Error") when the underlying agent/LLM call raises** — escaping the gateway's otherwise-consistent
`ErrorResponse` JSON envelope. This was live-triggered because this machine's stored `settings.toml` has a
**mismatched credential reference** (`provider.kind=anthropic` but `api_key_ref="OPENAI_API_KEY"`), which feeds
an OpenAI key to the Anthropic client → upstream `401 invalid x-api-key` → uncaught → bare 500. The 500-shape
gap is a genuine gateway robustness bug independent of the credential cause. Secondary findings concern OpenAPI
completeness (no documented auth/security scheme, no 401/500 responses) and minor error-envelope inconsistency.

Severity counts: **High 1, Medium 2, Low 2, Nit 1.**

## Findings

### [High] Uncaught agent/provider exceptions return a raw `text/plain` 500, bypassing the `ErrorResponse` envelope
- **Where:** `fastapi_app.py` — `post_message` (L271-273), `openai_chat_completions` (L407-429),
  `channel_inbound` (L454-472); root cause path `control_plane.send_message` → `_on_chat` →
  `orchestrator.AgentSession.chat` (orchestrator.py L854) → `loop.run` → LLM client. There are exception
  handlers for `GatewayProtocolError` (400), `GatewayUnavailableError` (503), and `RequestValidationError`
  (422), but **no handler for generic `Exception`**.
- **Repro (live-verified):**
  ```
  POST /sessions/{id}/messages {"prompt":"say hi"}   -> 500  text/plain  "Internal Server Error"
  POST /v1/chat/completions {"messages":[...]}        -> 500  text/plain  "Internal Server Error"
  POST /channels/web/inbound {"sender_id":"u","text":"hi"} -> 500 text/plain "Internal Server Error"
  ```
  Server log shows the traceback ending in
  `anthropic.AuthenticationError: Error code: 401 - {... 'message': 'invalid x-api-key'}`
  surfacing uncaught through `run_endpoint_function`.
- **Expected:** A structured `ErrorResponse` JSON body (`{"code","message","details"}`) with an appropriate
  status (e.g. 502/503 for an upstream provider failure, 500 for an internal error) — consistent with the
  400/422/503 envelopes the gateway already returns for every other error class. The SSE event log even
  records `session.chat.started` and `thinking.step` before the failure, so the turn is half-observable but
  the HTTP response is opaque.
- **Actual:** Starlette's default plain-text `500 Internal Server Error`; `content-type: text/plain`. Clients
  (and the SvelteKit frontend, which is generated against the typed contract) cannot parse the failure.
- **Impact:** All three externally-reachable chat entry points fail opaquely on any provider hiccup, rate
  limit, timeout, or transient agent error. No machine-readable error code; no way for a client to distinguish
  "bad gateway / provider down" from a real internal bug.
- **Suggested fix:** Add a catch-all `@app.exception_handler(Exception)` that returns
  `_error_response(500, "internal_error", "...")` (or map known upstream errors like
  `anthropic.APIStatusError` to 502/503). Optionally wrap `_on_chat` in `control_plane.send_message` to raise
  `GatewayUnavailableError` on provider failures so it maps to the existing 503 handler.

### [Medium] Stored provider config feeds the wrong provider's key to the Anthropic client (`api_key_ref` wins over `api_key_env`)
- **Where:** `core/llm.py` `create_llm_client` (L441-452); stored `settings.toml`
  (`C:\Users\opsin\AppData\Local\tokoboros\settings.toml`) has
  `provider = {kind:"anthropic", api_key_ref:"OPENAI_API_KEY", api_key_env:"ANTHROPIC_API_KEY"}`.
- **Repro (live-verified mechanism):**
  ```
  secret_store.get("OPENAI_API_KEY")   -> present, len 164, prefix "sk-proj-Hn"   (an OpenAI key)
  env ANTHROPIC_API_KEY                 -> present, len 108, prefix "sk-ant-api03-" (valid; direct call = 200)
  create_llm_client resolution         -> picks api_key_ref first => "sk-proj-..." handed to AnthropicClient
  Anthropic                             -> 401 invalid x-api-key
  ```
  A direct `POST https://api.anthropic.com/v1/messages` with the env `ANTHROPIC_API_KEY` returns **200**, so
  the key itself is valid — only the gateway's resolution order selects the wrong one.
- **Expected:** When `kind == "anthropic"`, the resolved key should be an Anthropic key. Either the config
  writer should never set `api_key_ref` to a foreign provider's secret name, or `create_llm_client` should
  prefer the provider-appropriate `api_key_env` and treat a key whose prefix clearly belongs to another
  provider as a misconfiguration.
- **Actual:** `api_key_ref` is resolved first and unconditionally; a stale/foreign secret silently shadows the
  correct env var, producing a confusing upstream 401 with no hint that the *reference* is wrong.
- **Impact:** In this environment it makes **every** chat turn fail. More broadly it's a foot-gun: a leftover
  `api_key_ref` from a prior provider switch silently breaks auth. (Note: this is config-data driven and lives
  just outside the strict gateway files, but it is the live cause of the High finding above.)
- **Suggested fix:** Validate provider/key coherence on `update_provider`/setup (reject/clear an `api_key_ref`
  that doesn't match `kind`), and/or fall back to `api_key_env` when the `api_key_ref` lookup yields a key
  whose format is inconsistent with `kind`.

### [Medium] OpenAPI contract omits the auth scheme entirely — no `securitySchemes`, no per-route `security`, no 401 responses
- **Where:** `fastapi_app.py` `_require_auth` (L196-205) is wired as a plain `Depends(_require_auth)`
  (L228-234) rather than a FastAPI `Security(...)` / `APIKeyHeader` dependency.
- **Repro (live-verified):** Compared the committed schema, a fresh export, and the **token-protected running
  server's** `/openapi.json`:
  ```
  live(token server) paths == committed paths : True
  components.securitySchemes present           : False (both)
  operations carrying a `security` requirement : 0 (both)
  operations documenting a 401 response        : 0  (of 25 paths)
  ```
- **Expected:** Protected routes should declare a bearer/security scheme and a documented `401` response so
  generated clients (the SvelteKit `frontend/`) know auth is required and can render it in `/docs`.
- **Actual:** The schema is **identical whether auth is on or off**; consumers get zero signal that a Bearer
  token gates `/sessions/*`, `/config/*`, `/runtime/*`, `/v1/*`, `/channels/*`, `/capabilities`, `/models`.
- **Impact:** Contract under-documents security. Frontend/codegen omit the Authorization header; `/docs`
  shows no "Authorize" affordance. (Does **not** fail `frontend-contract` CI, which only checks export-vs-committed equality.)
- **Suggested fix:** Replace the plain `Depends` with an `APIKeyHeader`/`HTTPBearer` security dependency (so
  FastAPI emits `securitySchemes` + per-op `security`), and add `responses={401: {"model": ErrorResponse}}`
  to the protected routers. Re-export the schema afterward.

### [Low] Auth 401 and facade endpoints use FastAPI's `{"detail": ...}` shape instead of the gateway `ErrorResponse` envelope
- **Where:** `_require_auth` raises `HTTPException(401, detail=...)` (L201-205); `openai_chat_completions`
  and `channel_inbound` raise `HTTPException(400/429, detail=...)` (L411-413, L457-466).
- **Repro (live-verified):**
  ```
  GET /sessions (no bearer)            -> 401 {"detail":"Missing or invalid bearer token"}
  POST /v1/chat/completions (no text)  -> 400 {"detail":"no user message content"}
  POST /channels/web/inbound (no text) -> 400 {"detail":"text is required"}
  ```
  vs. the structured envelope used elsewhere: `{"code":"protocol_error","message":...,"details":null}`.
- **Expected:** One consistent error shape across the API (the `ErrorResponse` envelope), or — for the
  OpenAI-compat facade specifically — OpenAI's `{"error":{...}}` shape that OpenAI clients expect.
- **Actual:** Three different error shapes coexist (`ErrorResponse` for protocol/validation, `{"detail":...}`
  for auth/facade HTTPExceptions, `text/plain` for uncaught 500s).
- **Impact:** Clients must special-case multiple error formats; the OpenAI-compat endpoint is neither the
  gateway shape nor a real OpenAI error shape.
- **Suggested fix:** Add an `@app.exception_handler(HTTPException)` that re-wraps into `ErrorResponse`, or map
  facade errors to the OpenAI error schema on the `/v1/*` routes.

### [Low] `complete_chat` session reuse can resurrect a closed/unknown facade session, raising `protocol_error`
- **Where:** `control_plane.py` `complete_chat` (L261-282) caches `session_key -> session_id` in
  `_api_sessions` and reuses it only if still in `_connections`; the underlying agent session id (from
  `recent_sessions`) differs from the gateway connection id. **Code-reading** observation (not separately
  live-triggered because the live key failure short-circuits before reuse matters).
- **Repro (code-reading):** If a facade-created connection is closed (e.g. via `DELETE /sessions/{id}` or a
  boot failure cleanup at control_plane L196-198), the next facade call recreates a session — generally fine —
  but the `_api_sessions` map is never pruned on `close_session`, so the stale entry lingers until the
  `known` check rebuilds it. Low risk; flagged for awareness.
- **Expected:** Closing a session should also evict any `_api_sessions` mapping that points at it.
- **Actual:** `close_session` (L284-295) pops from `_connections` only; `_api_sessions` is not touched.
- **Impact:** Minor memory/staleness; functionally self-healing via the `known` recheck. No observed failure.
- **Suggested fix:** In `close_session`, also drop matching `_api_sessions` entries.

### [Nit] `GET /` returns 404 (no root/index route)
- **Where:** No route registered for `/`. **Live-verified:** `GET /` -> `404 {"detail":"Not Found"}`.
- **Expected/Impact:** Cosmetic. The CLI panel advertises `/health`, `/readiness`, `/docs`, `/openapi.json`,
  which all work; a bare `GET /` 404 is normal for an API but can confuse a human who opens the base URL.
- **Suggested fix (optional):** Add a tiny root route returning service name + links, or a redirect to `/docs`.

## What passed

- **Gateway boots and serves** on port 8795 with `--runtime` (both unauthenticated and token-protected modes).
- **Health / readiness:** `GET /health` -> 200 `{status:"ok", protocol_version:"1.0", ...}`;
  `GET /readiness` -> 200 `{ready:true, blockers:[], active_connections:N}`.
- **Sessions:** create (`POST /sessions` -> 200 with `session_id`, `server_capabilities`), list
  (`GET /sessions` -> 200 array), state (`GET /sessions/{id}` -> 200 snapshot), and the protocol-error guards
  all return correct structured 400/422 envelopes (unknown session, empty prompt, bad protocol version,
  negative `after`).
- **SSE stream** (`GET /sessions/{id}/stream`) — live-verified: `200`, `content-type: text/event-stream`,
  `: connected` preamble, well-formed `id:`/`event:`/`data:` frames (real `session.chat.started`,
  `thinking.step`, `thinking` events), `: keepalive` heartbeats, and clean self-close after `max_idle_cycles`.
  Unknown-session is validated up front and returns a 400 envelope rather than a broken stream.
- **Auth gating** (live-verified, `TOKOBOROS_GATEWAY_API_TOKEN=testtoken`): protected routes
  (`/sessions*`, `/capabilities`, `/models`, `/config`, `/runtime/*`, `/v1/*`, `/channels/*`) return **401**
  without a bearer and with a **wrong** bearer; **200** with the valid bearer. `/health` and `/readiness`
  remain public. Default (no token) on loopback allows requests, as designed
  (`_resolve_gateway_auth_token`, main.py L472).
- **OpenAPI contract in sync:** `gateway-openapi --output <tmp>` produces a file **byte-identical** to the
  committed `openapi/tokoboros-openapi.json` (`cmp` clean; `json.tool` diff empty; both 38533 bytes; 25 paths).
  The `frontend-contract` CI job would **pass**. The live token-server `/openapi.json` matches the committed paths exactly.
- **CORS default is safe** (live-verified): with `TOKOBOROS_CORS_ORIGINS` unset it defaults to
  `["http://localhost:5173","http://localhost:4173"]` (NOT `*` — the wildcard default the task hint warned
  about has already been tightened). Preflight reflects only the allowed origin, rejects unknown origins
  (no ACAO; 400), and `allow_credentials=False`. Residual note: an operator who explicitly sets
  `TOKOBOROS_CORS_ORIGINS=*` gets wildcard CORS, but with credentials off the impact is limited to
  bearer-token-in-JS scenarios.
- **No secret leakage** via `GET /config`: only reference names (`api_key_ref`, `api_key_env`) are returned,
  never secret values.
- **Tests:** all **67** gateway tests pass —
  `test_gateway_control_plane`, `test_gateway_openai_channels`, `test_gateway_resilience`,
  `test_gateway_runtime_validation`, `test_gateway_sse`, `test_cli_gateway_command`,
  `test_gateway_status_cmd`, `test_gateway_config_mutations`.

---
*All gateway processes started during this QA were killed (PIDs 9988 and 23612; port 8795 confirmed free).
No source files were modified.*
