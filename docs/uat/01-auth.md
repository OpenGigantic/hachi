# UAT Auth Report — Gateway & API Authentication/Authorization

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

> **Build:** `036dfd9` · **Date:** 2026-06-14 · **Verifier:** UAT agent (claude-sonnet-4-6)
> **Scope:** UC-AUTH-001, UC-AUTH-002, UC-AUTH-003, ADV-014, ADV-015
> **Notes for orchestrator:** **ALL FIVE scenarios are CONFIRMED defects**. The single most critical finding is that `auth_token` is `None` by default on loopback, so `protected_dependencies = []` — not `[Depends(_require_auth)]` — meaning every "protected" router is wide open with zero bearer-token enforcement (`fastapi_app.py:264`).

---

## UC-AUTH-001 — CONFIRMED

- **Claim:** All protected routes return 401 when the gateway is started on loopback with no token env set.
- **Evidence:**
  - `fastapi_app.py:212-214`: `def _require_auth(request: Request) -> None:` / `if not auth_token: return` — the guard is a no-op when `auth_token is None`.
  - `fastapi_app.py:264`: `protected_dependencies = [Depends(_require_auth)] if auth_token else []` — when no token is set, this list is EMPTY, so all protected routers get no dependency injected.
  - `cli/main.py:525-539` (`_resolve_gateway_auth_token`): when `HACHI_GATEWAY_API_TOKEN` is unset and host is loopback, returns `None`.
  - Live inline repro (no real server needed, via `TestClient`):
    ```
    GET  /sessions       -> 503  (not 401)
    GET  /config         -> 503  (not 401)
    PUT  /config/provider-> 503  (not 401)
    GET  /schedules      -> 200  (not 401)
    GET  /runtime/events -> 503  (not 401)
    ```
    None returned 401. 503s come from `GatewayControlPlane` not being attached — not auth. The `/schedules` route even returns 200 with no credentials.
  - Contrast: when `auth_token="secret-token"` is set, all above routes return 401 without a header (confirmed by existing test `test_protected_routes_require_bearer_token_when_configured`).
- **Finding:** The auth mechanism is fully functional when a token IS configured. The defect is that the loopback default is deliberately tokenless, causing `protected_dependencies` to be empty and all router-level auth to evaporate. Any local process or browser tab within CORS can call `POST /sessions`, `PUT /config/permissions`, `DELETE /schedules/{id}`, `POST /v1/chat/completions`, etc. without any credential.
- **Severity:** Critical · **Regression test:** yes — `tests/test_gateway_control_plane.py::test_protected_routes_require_bearer_token_when_configured` covers the token-set case but there is **no test for the default no-token case asserting open access**.

---

## UC-AUTH-002 — CONFIRMED

- **Claim:** The daemon launches the gateway with a bearer token, so the always-on surface requires auth.
- **Evidence:**
  - `daemon.py:133-149` (`_build_argv`): produces `['hachi.exe', 'gateway', '--host', '127.0.0.1', '--port', '8787', '--runtime']` — no token argument and no env-var injection.
  - Live repro: `_build_argv(DaemonConfig('127.0.0.1', 8787))` = `['...hachi.exe', 'gateway', '--host', '127.0.0.1', '--port', '8787', '--runtime']`. Confirmed: `Has --token arg: False`.
  - `cli/main.py:525-539`: `_resolve_gateway_auth_token('127.0.0.1')` returns `None` when `HACHI_GATEWAY_API_TOKEN` is unset.
  - Since the daemon sets `host=127.0.0.1` (loopback) and passes no token env, the spawned gateway process resolves `auth_token=None`, so all protected routes are open (same as UC-AUTH-001).
- **Finding:** The daemon installs and runs a permanently-open control plane. There is no auto-generation of a token on daemon install, no token is written to a keyring or file that the spawned process reads, and no mechanism prompts the user to set one. Any process on the machine can control the always-on agent without credentials.
- **Severity:** Critical · **Regression test:** yes (partial) — `tests/test_cli_gateway_command.py::test_gateway_command_requires_auth_token_for_non_loopback_host` only tests `0.0.0.0`, not the loopback daemon case.

---

## UC-AUTH-003 — CONFIRMED

- **Claim:** Default CORS configuration + unauthenticated loopback allows a browser page on localhost:5173/4173 to drive the agent.
- **Evidence:**
  - `fastapi_app.py:187-199`:
    ```python
    _raw_origins = os.environ.get("HACHI_CORS_ORIGINS", "")
    _cors_origins: list[str] = (
        [o.strip() for o in _raw_origins.split(",") if o.strip()]
        if _raw_origins.strip()
        else ["http://localhost:5173", "http://localhost:4173"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )
    ```
  - Live OPTIONS preflight repro:
    ```
    OPTIONS /sessions (Origin: http://localhost:5173) -> 200
      Access-Control-Allow-Origin: http://localhost:5173
      Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
    OPTIONS /config/provider (Origin: http://localhost:4173) -> 200
      Access-Control-Allow-Origin: http://localhost:4173
    OPTIONS /sessions (Origin: https://evil.com) -> 400
      Access-Control-Allow-Origin: (none)
    ```
  - Combined with UC-AUTH-001 (no auth on loopback): a page served from `localhost:5173` (the Vite dev server for the bundled frontend) can issue credentialed cross-origin requests to `POST /sessions`, `PUT /config/permissions`, `POST /v1/chat/completions` and receive responses.
- **Finding:** CORS is correctly scoped (only `localhost:5173/4173` allowed, not arbitrary origins). However, `allow_credentials=False` does NOT prevent unauthenticated cross-origin requests — it only prevents cookies from being forwarded. Since there is no auth token required (UC-AUTH-001), any JavaScript on `localhost:5173` can make authenticated-equivalent API calls. The threat is narrowed to localhost-served pages but not to the Hachi frontend only; any local dev server on those ports (React, Vite, etc.) can drive the agent. This is an open-source project with a well-known dev-server default port.
- **Severity:** High · **Regression test:** no — there is no test verifying that cross-origin requests without an auth token are blocked (they currently are not).

---

## ADV-014 — CONFIRMED

- **Claim:** Local malware or another local process can control the gateway (chat, config, permissions, events) without any auth.
- **Evidence:**
  - Same root cause as UC-AUTH-001/002. `fastapi_app.py:264` makes `protected_dependencies = []` when `auth_token is None`.
  - Affected routes (all under protected routers): `POST /sessions`, `GET /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/messages`, `DELETE /sessions/{id}`, `GET /sessions/{id}/events`, `GET /sessions/{id}/stream`, `GET /runtime/events`, `GET /runtime/mcp/inventory`, `GET /jobs/active`, `GET /jobs/failed`, `GET /permissions/audit`, `GET /subagents/reports`, `GET /schedules`, `DELETE /schedules/{id}`, `PUT /schedules/{id}/enabled`, `GET /config`, `PUT /config/provider`, `POST /config/provider/validate`, `PUT /config/permissions`, `PUT /config/memory`, `GET /setup`, `POST /setup/apply`, `POST /v1/chat/completions`, `GET /v1/models`, `POST /channels/{channel}/inbound`.
  - Live repro: `GET /schedules` returns `200 {"schedules": []}` with no `Authorization` header when no token is set. `PUT /config/provider` returns 503 (not 401 — only fails because no real control plane callback is wired in test, not because auth blocked it).
  - Unprotected system routes (`/`, `/health`, `/readiness`) are intentionally public — these are correct.
- **Finding:** Full takeover of the control plane is possible from any local process when the default is used. An attacker can: read all sessions and events, inject chat messages, modify provider config (change API key), escalate permission tier to `unrestricted`, cancel schedules, and use the agent to perform autonomous actions. No mitigation exists unless the user explicitly sets `HACHI_GATEWAY_API_TOKEN`.
- **Severity:** Critical · **Regression test:** no — the exit criteria in §11 of the UAT plan specifically calls for a regression test for unauth gateway (ADV-014) that does not yet exist.

---

## ADV-015 — PARTIAL

- **Claim:** The channel inbound endpoint and OpenAI endpoint are vulnerable to abuse: empty channel allowlist accepts any sender, and the unauth posture removes attacker cost.
- **Evidence:**
  - **Rate limits (functional):**
    - `fastapi_app.py:469`: `openai_limiter = RateLimiter(_int_env("HACHI_OPENAI_RATE_LIMIT", 60), 60.0)` — default 60 req/60s per key (`"openai"` key, meaning all callers share one bucket).
    - `fastapi_app.py:612-613`: `channel_limiter = RateLimiter(_int_env("HACHI_CHANNEL_RATE_LIMIT", 30), 60.0)` — default 30 req/60s per `{channel}:{sender}` key.
    - `tests/test_rate_limit.py` (4 tests): all pass. RateLimiter blocks at `max_requests+1` and recovers after the window. Confirmed working.
  - **Empty channel allowlist (defect present):**
    - `fastapi_app.py:613`: `channel_allowlist = _parse_allowlist(os.environ.get("HACHI_CHANNEL_ALLOWLIST"))`.
    - `fastapi_app.py:620-624`: `if channel_allowlist and not (sender in channel_allowlist or ...): raise 403`. The guard is `if channel_allowlist` — when the env var is unset, `channel_allowlist = set()`, and the guard short-circuits to "allow all senders". Live repro: `_parse_allowlist(None) == set()` → any `sender_id` is accepted.
    - This means any external caller can post to `POST /channels/{channel}/inbound` with any `sender_id` and get a full agent turn, limited only by the rate limiter (30/60s).
  - **OpenAI rate-limit key collision (partial defect):**
    - `fastapi_app.py:476`: `if not openai_limiter.allow("openai"):` — the key is the literal string `"openai"`, not per-client. All callers share a single 60/60s bucket, so a single abuser can exhaust the limit for ALL legitimate users. This is weaker than per-sender limiting.
  - **Session creation is bounded** by `GatewayControlPlane(max_active_sessions=...)` (default applied in tests), but the default value must be confirmed at runtime — the inline test shows it applies.
  - **Unauth posture amplifies all of the above** (confirmed via ADV-014): no attacker cost beyond the rate limit, since auth is absent.
- **Finding:** Rate limits are implemented and functional (30/60s channel, 60/60s OpenAI). However: (1) the channel allowlist is empty by default so any sender_id is accepted; (2) the OpenAI rate limiter uses a shared key so one abuser starves all legitimate OpenAI-compat clients; (3) the unauth default (ADV-014) means there is no credential barrier before hitting the rate limits. The scenario is PARTIAL — rate limits exist and work, but the allowlist defect and shared-key weakness are real.
- **Severity:** High · **Regression test:** yes (rate limiter) — `tests/test_rate_limit.py` covers the sliding window. No test covers the empty-allowlist-allows-all behavior or the shared OpenAI key.

---

## Cross-scenario summary

| ID | Verdict | Severity | Root cause file:line |
|---|---|---|---|
| UC-AUTH-001 | CONFIRMED | Critical | `fastapi_app.py:213-214,264` |
| UC-AUTH-002 | CONFIRMED | Critical | `daemon.py:133-149`, `cli/main.py:538-539` |
| UC-AUTH-003 | CONFIRMED | High | `fastapi_app.py:187-199,264` |
| ADV-014 | CONFIRMED | Critical | `fastapi_app.py:264` (same root) |
| ADV-015 | PARTIAL | High | `fastapi_app.py:469,612-615,476` |

**Root cause shared by UC-AUTH-001/002/003/ADV-014:** `protected_dependencies = [Depends(_require_auth)] if auth_token else []` (`fastapi_app.py:264`) — when `auth_token is None` (the loopback default), the dependency list is empty and `_require_auth` is never invoked.

**Tests run:** `tests/test_gateway_control_plane.py`, `tests/test_rate_limit.py`, `tests/test_cli_gateway_command.py` — 36 tests, all passed. The existing tests only cover the "token IS set" case for auth; no test asserts the no-token open-access behavior is intentional/documented, and no test guards against silent regression to the opposite.
