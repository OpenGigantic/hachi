# Hachi — Security & Reliability Audit v2 (QA Agent 4)

> **Scope:** Static + design review only (no live exploitation). READ-ONLY.
> **Date:** 2026-06-18 (v2) · **Branch:** `main` (incl. uncommitted working-tree changes + new
> `src/hachi/client/**`, new gateway web routes, `frontend/`, `docs/web/`).
> **Auditor:** QA Agent 4 — Security & Reliability.
> **Mandate:** Re-verify every prior SEC-*/REL-* finding against CURRENT code; audit the NEW attack
> surface (new gateway routes, REST client module, heavily-modified CLI).
> **Off-limits:** `src/hachi/tui/cockpit/**` (concurrent session) — excluded from scope and scoring.

**Verdict in one line:** None of the prior P0/P1 security criticals or highs have been remediated
since the 2026-06-18 v1 audit. The major churn (new web routes + REST client) is, encouragingly,
**clean on auth and transport** — every new route carries the bearer dependency, the new client never
logs the token and keeps TLS verification on — but it **widens the blast radius** of the still-open
defects (more token-holder-reachable mutation routes; a new internal-call vector for the SSRF and the
thread-pool-exhaustion bugs).

---

## 0. Headline counts

**Prior findings re-verified:** 18 SEC + 12 REL = 30.
- **FIXED:** 0 SEC, 0 REL. (One adjacent white-box item, WB-001 CI, appears fixed — not in this agent's scope.)
- **STILL-OPEN:** 18 SEC, 12 REL (all of them).
- **PARTIAL / CHANGED:** WB-013 is **PARTIAL** — the *primary* vault write path now fails loudly (MR-7 holds), but the `Fts5OnlyMemoryProvider.upsert` fallback still returns a fake UUID on write failure.

**New findings this pass:** 0 Critical · 1 High · 3 Medium · 2 Low.
(The new web routes did not introduce a new auth/authz hole; the new issues are amplifications and
two small robustness gaps.)

**Aggregate severity (prior + new), still-open:** **2 Critical · 10 High · 15 Medium · 9 Low.**

---

## 1. Prior-finding verification table

Every prior SEC-*/REL-* ID → current status → current file:line evidence.

| ID | Title | Prior sev | Status | Current evidence |
|----|-------|:---------:|:------:|------------------|
| **SEC-001** | ZIP pack extraction zip-slip + bomb (bypasses `safe_extract`) | Critical | **STILL-OPEN** | `core/community_packs.py:243-264` — `_extract_zip` writes `dest / rel` then `target.write_bytes` with **no** `..`/escape check, **no** member/size cap. Tar path (`:188-240`) IS hardened; ZIP still is not. |
| **SEC-005** | MCP command/args/env config-controlled, web-installable, no tier gate → arb. exec at boot | Critical | **STILL-OPEN** | `core/mcp_client.py:447-498` (`setup_from_node`), `:226-248` (`_start_inner` → `subprocess.Popen(full_command, shell=False)`). `gateway/orchestrator.py:1239-1260` starts EVERY `role=mcp` node; only `filesystem_mcp` has a guard — no tier check, no command allowlist. |
| **SEC-003** | SSRF in dynamic `http_request` integration tool | High | **STILL-OPEN** | `core/integrations.py:244-270` — builds `url` from `base_url`, calls `urllib.request.urlopen(req, timeout=30)`; **no** `validate_outbound_url`, default redirect handler (urllib follows 3xx), auth header in `headers` is resent on redirect. |
| **SEC-004** | `browse_web`/Playwright validates only initial URL; redirects reach internal hosts | High | **STILL-OPEN** | `tools/browser_tools.py:49` validates initial URL only; `core/browser.py:193` `page.goto(url, wait_until="domcontentloaded")` follows redirects with no per-hop revalidation. |
| **SEC-002** | Untrusted-content framing is detection-gated | High | **STILL-OPEN** | `core/untrusted_content.py:42-46` — `is_safe, threat = scan(content); if is_safe: return content, False`. Safe-scored content returned unframed. |
| **SEC-007** | Pack install does not enforce signature/hash | High | **STILL-OPEN** | `core/community_packs.py:299-389` (`install_pack`) never calls `verify_index_entry_sha256`. The verifier exists (`core/pack_index.py:217-225`) but returns `True` on empty hash and is unused by the install flow. No CLI `--unsafe`/`sha256` enforcement. |
| **SEC-006** | Channel webhook fail-OPEN on empty allowlist | Medium | **STILL-OPEN** | `gateway/fastapi_app.py:733-736` — `if channel_allowlist and not (...)`: when `HACHI_CHANNEL_ALLOWLIST` unset, guard is skipped, any `sender_id` accepted. |
| **SEC-008** | `run_shell_script` unbounded shell escape once tier permits a shell | Medium | **STILL-OPEN** | `tools/os_tools.py` (script-run path) writes a temp script and runs via `powershell.exe -ExecutionPolicy Bypass -File` / `bash`, gated only by `check_command`. Unchanged. |
| **SEC-009** | DNS-rebinding TOCTOU across `net_guard` callers | Medium | **STILL-OPEN** | `core/net_guard.py:61-72` resolves + validates; clients re-resolve at connect. No IP pinning. |
| **SEC-010** | Memory/transcripts plaintext at rest | Medium | **STILL-OPEN** | Chroma + FTS5 SQLite + `sessions.db` plaintext; `core/session_store.py` logs raw tool outputs; no at-rest encryption (D-3 unimplemented). |
| **SEC-011** | CI security job has no dep-CVE / SAST scan | Medium | **STILL-OPEN** | `.github/workflows/ci.yml:36-59` — `security` job runs ONLY `hachi security-audit`. No `pip-audit`/`uv pip audit`, no `npm audit`, no `bandit`. |
| **SEC-012** | Dockerfile binds `0.0.0.0` and runs as root | Medium | **STILL-OPEN** | `Dockerfile:11` `pip install .` as root, `:15` `CMD [..., "--host", "0.0.0.0", ...]`, no `USER`. (Mitigation intact: non-loopback bind without explicit token refuses to start — see SEC-013.) |
| **SEC-013** | `HACHI_GATEWAY_NO_AUTH=1` disables auth on all routes regardless of bind host | Medium | **STILL-OPEN** | `gateway/fastapi_app.py:243-250, 299-300` — when set, `_require_auth` returns immediately for every route. WARNING logged (`:246`, and `cli/main.py:1050-1052`) but no host gate. |
| **SEC-014** | `_require_auth` no-ops when `auth_token` is falsy | Low | **STILL-OPEN** | `gateway/fastapi_app.py:301-304` — `if not auth_token: return`. Enforcement relies entirely on the CLI always passing a truthy token. |
| **SEC-015** | Authoring endpoints expose config/permission file source over HTTP | Low | **STILL-OPEN** (well-defended) | `cli/main.py:688-692` returns raw source; path traversal solidly blocked at `:651-686` (rejects `/ \ ..`, allow-lists names, re-derives path). Auth-gated. Residual is info-exposure of config posture only. |
| **SEC-016** | `run_shell_script` temp file world-readable during execution | Low | **STILL-OPEN** | `tools/os_tools.py` `NamedTemporaryFile(delete=False)` with default perms; no `chmod 0600`. Unchanged. |
| **SEC-017** | Drive search results not untrusted-framed | Low | **STILL-OPEN** | `integrations/google/drive.py` reflects file names/links without `guard_untrusted`. Unchanged. |
| **SEC-018** | `VITE_HACHI_TOKEN` direct mode inlines bearer into JS bundle | Low | **STILL-OPEN** | `frontend/src/lib/data/client.ts` reads `import.meta.env.VITE_HACHI_TOKEN`; statically embedded in direct-mode build. Default proxy mode safe. |
| **REL-001** | Synchronous 180s approval wait blocks anyio worker threads → pool exhaustion | High | **STILL-OPEN** | `core/approvals.py:309-357` `RegistryApprover.require` → `wait(request_id, timeout=180.0)` (`:293-307` default). Invoked on gateway/channel/OpenAI path (`gateway/orchestrator.py:1365`) which runs inside `anyio.to_thread.run_sync` (`fastapi_app.py:579, 631, 741`). Registry has no cap on `create` (`approvals.py:89-98`). |
| **REL-002** | In-memory rate limiter grows unboundedly with distinct keys | Medium | **STILL-OPEN** | `core/rate_limit.py:24` `defaultdict(deque)`; empty buckets never reaped. Couples with SEC-006 (any `sender_id` accepted → unbounded keys). |
| **REL-003** | OpenAI/channel facade shares session per (spoofable) key, no caller isolation | Medium | **STILL-OPEN** | `gateway/control_plane.py:291-323` keys `_api_sessions` by caller-supplied value; `fastapi_app.py:737` derives `session_key = f"{channel}:{sender}"` from spoofable `sender_id`. |
| **REL-004** | Pending-approval registry entry leak on thread death | Low | **STILL-OPEN** | `core/approvals.py:152-155` `_cleanup` only runs after `wait` returns; entry lingers if the waiting thread dies. |
| **REL-005** | Daemon-spawned gateway depends on side-channel token file (race/drift) | Medium | **STILL-OPEN** | `daemon.py:173` `_build_argv` passes no token; subprocess auto-generates/reads `~/.hachi/gateway-token` (`cli/main.py:931-1008`). No explicit token hand-off. |
| **REL-006** | `SQLiteJobQueue` opens a fresh connection per op under a process-wide lock | Low | **STILL-OPEN** | `jobs/queue.py` `_conn` connect/commit/close per call; writes serialize on `self._lock`. Unchanged. |
| **REL-007** | Func-only jobs non-durable (silent loss of ephemeral work on crash) | Low | **STILL-OPEN** (by design) | `jobs/queue.py` in-process `_func_table`; `_recover_running` fails func-only PENDING on restart (not silently re-run — good); work itself lost. |
| **REL-008** | No automatic backup / rotation / integrity-check for SQLite stores | Medium | **STILL-OPEN** | No `PRAGMA integrity_check`/`VACUUM`/periodic backup anywhere; `session_store.py` WAL + `synchronous=NORMAL`. |
| **REL-009** | Provider/integration outage degradation is per-call retry only; no circuit breaker | Low | **STILL-OPEN** | Loop retries + wall-clock backstop; no breaker. Unchanged. |
| **REL-010** | ZIP path lacks decompression-bomb cap (reliability facet of SEC-001) | Medium | **STILL-OPEN** | `core/community_packs.py:243-264` — no member-count / cumulative-size cap on the ZIP path. |
| **REL-011** | `apply_setup`/`update_provider` reboot live sessions synchronously inside the request | Low | **STILL-OPEN** | `cli/main.py` provider/setup-apply call `_reload_agents()` inline (re-spawns MCP) within the request thread. |
| **REL-012** | SSE/stream turn cancellation poll-based; orphaned turns on abrupt drops | Low | **STILL-OPEN** | `gateway/fastapi_app.py:454, 640-642, 677` — `request.is_disconnected()` polled between events; in-flight tool turn continues until next checkpoint (bounded by wall-clock). |
| **WB-013** | Silent `except` masks credential/memory failures; memory_provider fake-uuid on write failure | High | **PARTIAL** | **FIXED for default path:** `core/memory_vault.py:239-265` `_guarded_write` does NOT swallow `collection.upsert` errors → loud failure (MR-7 holds). **STILL-OPEN for fts5_only backend:** `core/memory_provider.py:236-239` `except Exception: pass` then `return str(uuid.uuid4())`. New `POST /memory/facts` handler (`cli/main.py:858-861`) correctly returns a structured error instead of a fake id. |

### MR-2…MR-10 re-confirmation (carried forward)

| Meta-risk | v1 verdict | v2 verdict |
|---|---|---|
| MR-2 Unauth gateway | Fixed | **Still holds** — token enforced, loopback not exempt (`fastapi_app.py:282-308`, `cli/main.py:959-1008`). Residuals SEC-013/014. |
| MR-3 Secret leak into events | Confirmed | Not re-disproven; display-surface redaction unchanged. At-rest still plaintext (SEC-010). |
| MR-4 Archive traversal | Partial (tar only) | **Still partial** — ZIP path remains unprotected (SEC-001/REL-010). |
| MR-5 Approval gate | Confirmed | **Still holds** (`orchestrator.py:1353-1367`). Availability cost REL-001. |
| MR-6 Spend caps | Confirmed | Not re-disproven (out of agent focus). |
| MR-7 Silent memory loss | Confirmed | **Holds for default vault**; `fts5_only` provider still fakes (WB-013 partial). |
| MR-9 Cross-process lock | Confirmed | Not re-disproven. |
| MR-10 Plaintext at rest | Unimplemented | **Still unimplemented** (SEC-010). |

---

## 2. New-surface audit

### 2a. New gateway routes (`gateway/fastapi_app.py`, `control_plane.py`, `orchestrator.py`)

All new web-UI routes are mounted on routers that carry `dependencies=protected_dependencies`
(the `_require_auth` bearer check). Verified per route:

| Route | Method | Auth? | Input validation | Notes |
|---|---|:---:|---|---|
| `/skills` | GET | ✅ (`skills_router`, `:812`) | n/a (read) | Returns inventory; safe. |
| `/memory` | GET | ✅ (`memory_router`, `:813-815`) | n/a (read) | Reads all `core_memory` (PERF-012, not security). |
| `/memory/recall` | GET | ✅ | `query` min_length=1, `top_k` 1..50 (`:832-838`) | OK. |
| `/memory/facts` | POST | ✅ | `group` allow-listed, `text` non-empty (`cli/main.py:844-849`) | **No fake-uuid** — returns structured error. Good. |
| `/memory/facts/{id}` | DELETE | ✅ | `fact_id` non-empty (`cli/main.py:876`) | Idempotent multi-collection sweep. OK. |
| `/og/graph` | GET | ✅ (`og_router`, `:816`) | n/a | OK. |
| `/authoring/targets` | GET | ✅ | n/a | Lists config/skill files. |
| `/authoring/targets/{name}` | GET | ✅ | **path traversal solidly blocked** (`cli/main.py:651-686`) | Exposes raw config source (SEC-015, Low). |
| `/authoring/targets/{name}/apply` & `/revert` | POST | ✅ | name validated; **no-op stubs** | Trust concern (UX), not security. |
| `/spend` | GET | ✅ (`spend_router`, `:820`) | optional `session_id` | OK. |
| `/capabilities/{id}/enabled` | PUT | ✅ (explicit `Depends(_require_auth)`, `:887-894`) | `enabled: bool` | Persists to global config — token-gated mutation. OK. |

**Result:** No new authentication or authorization hole was introduced by the new routes. Pydantic
models validate bodies; the 422/500 handlers do not leak internals (`:320-352`). The catch-all 500
handler logs the traceback server-side and returns only a correlation id (`:331-352`) — good.

Two pre-existing weaknesses persist on this surface and now matter more because there are more
mutation routes behind a single shared token:
- **SEC-013/SEC-014** — a single env flag (or a future no-token embedding) removes auth from ALL of
  these mutation routes (config, capabilities, memory writes, setup apply).
- **REL-003 / "one token == one trust domain"** — every route is gated by one bearer token with no
  per-principal identity; the channel webhook (SEC-006) lets a token holder act as an arbitrary
  `sender_id`.

### 2b. New REST client module (`src/hachi/client/**`)

Files: `gateway.py` (sync `httpx.Client`), `sse.py` (SSE reader), `models.py`, `gaps.py`
(placeholder data), `__init__.py`.

**Security posture — clean:**
- **Bearer handling:** token stored on the instance and injected only as
  `headers["Authorization"] = f"Bearer {self._token}"` (`gateway.py:88-90`). Never logged; error
  messages contain only method + path + status code (`:106-126`), never the token or response body.
- **TLS:** uses `httpx.Client` defaults — **verification is ON**. No `verify=False` anywhere in
  `src/` (grep-confirmed). No custom unsafe transport.
- **Deserialization:** only `resp.json()` and `json.loads` on SSE envelopes (`sse.py:102, 160`).
  No `pickle`/`yaml.load`/`eval`. Malformed frames are swallowed safely.
- **`base_url`:** operator-supplied (CLI/config), not attacker-influenced in normal flows;
  `rstrip("/")` only.

**One robustness gap (new, Low) — `SEC-N02`:** the client interpolates path parameters directly,
e.g. `f"/authoring/targets/{name}"` (`gateway.py:224`), `f"/memory/facts/{fact_id}"` (`:187`),
`f"/schedules/{schedule_id}"`. Values here originate from server responses, so this is not currently
exploitable, but unescaped path interpolation is a latent SSRF/path-confusion footgun if any of these
ever accept user-typed input. Use `httpx`'s param/quoting or validate the segment.

### 2c. CLI (`cli/main.py`, heavily modified)

- **Secrets:** gateway token resolution is solid — `secrets.token_urlsafe(32)`, persisted to
  `~/.hachi/gateway-token` with `chmod 0600` on POSIX (`:931-956`), non-loopback bind **requires** an
  explicit token (`:992-996`), constant-time compare at the gateway (`fastapi_app.py:307`). Token is
  printed to console once on generation (`:1002-1007`) — acceptable for a local first-run UX.
- **subprocess:** no `shell=True`, no `os.system`, no `eval/exec/pickle` in the CLI (grep-confirmed).
  The only spawn is the daemon path in `daemon.py`, which builds a list-form argv (`daemon.py:173`).
- **File writes with attacker-influenced paths:** the authoring path resolver (`:651-686`) is the
  only client-influenced write/read target and is well-defended (reject `/ \ ..`, allow-list,
  re-derive from known-safe dirs).
- **No interactive credential prompt during Textual boot** — CLAUDE.md guardrail holds; secrets are
  resolved lazily / via `hachi secrets`.

---

## 3. New findings (this pass)

### SEC-N01 — New mutation routes widen the SEC-013 blast radius (auth kill-switch now disables config/memory/capability writes)
- **Category:** Security / Authentication (amplification)
- **Severity:** High
- **Status:** NEW (amplifies SEC-013)
- **Root cause:** The new churn added several **state-mutating, token-gated** routes —
  `PUT /capabilities/{id}/enabled` (`fastapi_app.py:887-894`), `POST /memory/facts` /
  `DELETE /memory/facts/{id}` (`:840-855`), plus the existing `PUT /config/*` and `POST /setup/apply`.
  All of them are bypassed wholesale by `HACHI_GATEWAY_NO_AUTH=1` (`:299-300`) or by any code path
  that constructs the app without a token (`:301-304`).
- **Impact:** Before, the unauthenticated surface was mostly sessions/chat. Now a single env var
  exposes **config mutation, capability toggling, memory writes/deletes, and setup apply** to anyone
  on a reachable interface — e.g. an attacker can enable a dangerous capability or inject memory facts
  that steer future turns.
- **Recommendation:** Gate `HACHI_GATEWAY_NO_AUTH` to loopback-only bind (refuse the combination of
  no-auth + non-loopback); treat "auth enabled + no token" as a hard error rather than a silent skip
  (close SEC-014). Both are small, high-leverage changes.
- **Complexity:** S

### SEC-N02 — REST client path-parameter interpolation is unescaped (latent SSRF/path-confusion)
- **Category:** Security / Input validation (latent)
- **Severity:** Low
- **Status:** NEW
- **Root cause:** `client/gateway.py:187, 224, 230, 234, 257, 329` and similar build request paths via
  f-string interpolation of `name`/`fact_id`/`schedule_id`/`cap_id` without URL-encoding or segment
  validation.
- **Impact:** Not exploitable today (values come from server responses), but a future caller passing
  user input would allow `../` or encoded path tricks against the gateway.
- **Recommendation:** Validate/quote path segments (reject `/`, `\`, `..`; `urllib.parse.quote`).
- **Complexity:** S

### REL-N01 — New web routes call blocking control-plane work directly on the event loop (no `to_thread`)
- **Category:** Reliability / Resilience (event-loop blocking)
- **Severity:** Medium
- **Status:** NEW (overlaps prior PERF-011)
- **Root cause:** `GET /memory`, `/memory/recall`, `/og/graph`, `/skills`, `/spend`,
  `/authoring/targets*` are `async def` handlers (`fastapi_app.py:822-885`) that call synchronous,
  potentially slow control-plane methods (ChromaDB recall, full skill-graph build, full `core_memory`
  scan) **inline** — unlike `create_session`/`complete_chat`, which correctly wrap work in
  `anyio.to_thread.run_sync`. A slow Chroma recall or a large OG graph stalls the entire asyncio loop,
  freezing health checks and all in-flight SSE streams.
- **Impact:** Under a cold Chroma start (multi-second, see prior PERF-019) or a large memory store, a
  single `/memory` or `/memory/recall` request degrades the whole gateway's responsiveness.
- **Recommendation:** Wrap the blocking calls in `anyio.to_thread.run_sync` like the session routes.
- **Complexity:** S

### REL-N02 — Per-sender rate-limiter dict growth is now reachable from more identity-spoofable surfaces
- **Category:** Reliability / Resource exhaustion (amplification of REL-002)
- **Severity:** Medium
- **Status:** NEW (amplifies REL-002 + SEC-006)
- **Root cause:** Two `RateLimiter` instances are created per app (`fastapi_app.py:563` openai,
  `:724` channel). Keys are never reaped (`rate_limit.py:24`). The channel limiter is keyed by the
  spoofable `f"{channel}:{sender}"` (`:737-738`) which, with the fail-open allowlist (SEC-006), lets a
  token holder mint unbounded distinct keys → slow OOM of the long-lived gateway.
- **Recommendation:** Evict empty/expired buckets on `allow` (or LRU-cap distinct keys); fix SEC-006
  fail-open in tandem.
- **Complexity:** S

### REL-N03 — Streaming OpenAI/channel facade still parks an anyio worker per blocked approval (REL-001 reachable from the new streaming path too)
- **Category:** Reliability / Availability
- **Severity:** Low (sub-case of REL-001, called out separately because the streaming path is newer)
- **Root cause:** The streaming OpenAI path runs the full turn via
  `anyio.to_thread.run_sync(... send_message ...)` inside `_run_turn` (`fastapi_app.py:629-637`). If
  that turn hits a destructive action with no approver, it blocks a worker thread for 180 s exactly
  like the non-streaming path (REL-001). The streaming SSE loop itself is async, but the underlying
  turn thread is still parked.
- **Recommendation:** Same fix as REL-001 — make the approval wait async or fail-fast to a pending
  state; cap concurrent pending approvals.
- **Complexity:** M (shared with REL-001)

### REL-N04 — `Fts5OnlyMemoryProvider.upsert` masks write failures with a fabricated id
- **Category:** Reliability / Data integrity (the still-open half of WB-013)
- **Severity:** Low
- **Status:** carried from WB-013 (PARTIAL) — tracked as its own ID for closure
- **Root cause:** `core/memory_provider.py:230-239` — `try: write_fact(...) except Exception: pass`
  then `return str(uuid.uuid4())`. A caller that persists a fact when `memory.backend = "fts5_only"`
  receives a success-looking id even though nothing was written.
- **Recommendation:** Propagate the failure (raise or return a sentinel like the vault path / the new
  `/memory/facts` handler do); never synthesize a fake id.
- **Complexity:** S

---

## 4. Security risk assessment

The security posture is **unchanged from v1 and concentrated in the same two zones**:

1. **Community-pack supply chain (P0).** A web-fetched pack becomes trusted boot configuration. Three
   compounding holes remain wide open: ZIP zip-slip + bomb (SEC-001), MCP arbitrary-command exec at
   boot with no tier gate (SEC-005), and no mandatory signature/hash on install (SEC-007). Chained:
   `hachi pack install <evil>` → unsigned pack passes → ZIP member escapes `dest` to overwrite
   `~/.hachi/device-permissions.og` and/or plant a `role=mcp` node → next boot spawns
   `python -c "<exfil>"`. This is the single most serious exposure for a "trust product."
2. **Untrusted-input → tool egress (P1).** Prompt-injection framing is detection-gated (SEC-002), and
   two outbound tools perform SSRF: the dynamic `http_request` integration (SEC-003, no net_guard +
   follows redirects + leaks the auth header) and `browse_web` (SEC-004, initial-URL-only validation,
   Chromium follows redirects). The metadata-endpoint / internal-service reach is real.

**New surface did not add a security hole** — but it added more token-gated mutation routes behind the
same SEC-013/SEC-014 kill-switch and the same single-trust-domain bearer model (SEC-N01).

**Process gap (P2):** CI still runs no dependency-CVE scan and no SAST (SEC-011); the dependency
floors in `pyproject.toml` (e.g. `cryptography>=48`, `fastapi>=0.136`, `chromadb>=1.5.9`) are recent,
but nothing in CI catches a future CVE bump or a code-level vuln like the SSRF/zip-slip above.

**Deployment (P2):** Docker still root + `0.0.0.0` (SEC-012), mitigated only by the non-loopback
token requirement.

## 5. Reliability risk assessment

Reliability remains the stronger half (durable queue, WAL, cross-process locks, wall-clock backstop —
none regressed). The residual exposure is **availability and isolation under adversarial / partial
load**, and the new web routes added a fresh event-loop-blocking vector:

- **Thread-pool exhaustion (P1):** the 180 s synchronous approval wait (REL-001), now also reachable
  via the streaming facade (REL-N03), parks anyio workers; a flood of approval-triggering messages can
  hang `/health` and all SSE.
- **Event-loop stalls (P2):** the new read routes call blocking work inline (REL-N01) — a cold Chroma
  or large memory store freezes the whole gateway.
- **Unbounded growth (P2/P3):** rate-limiter keys (REL-002/REL-N02) and pending-approval entries
  (REL-004) are never reaped; combined with the spoofable webhook (SEC-006) an attacker can drive slow
  OOM.
- **Durability boundaries (P2):** no SQLite integrity-check/backup (REL-008); func-only jobs lost on
  crash (REL-007, by design).
- **No data-corruption defects found.** Gaps are availability- and isolation-shaped.

---

## 6. Risk priority matrix (likelihood × impact → P0/P1/P2)

| ID | Finding | Likelihood | Impact | Priority |
|----|---------|:---------:|:------:|:--------:|
| SEC-001 | ZIP pack zip-slip + bomb (bypasses safe_extract) | M | H | **P0** |
| SEC-005 | MCP command web-installable, no tier gate → arb. exec at boot | M | H | **P0** |
| SEC-003 | `http_request` integration SSRF (no net_guard, follows redirects, leaks auth) | M | H | **P0** |
| SEC-002 | Untrusted framing detection-gated → injection reaches model unframed | H | M | **P1** |
| SEC-004 | `browse_web` Playwright redirect SSRF | M | H | **P1** |
| SEC-007 | Pack install lacks mandatory signature/hash | M | H | **P1** |
| REL-001 | Approval-blocking exhausts gateway thread pool | M | H | **P1** |
| SEC-N01 | NO_AUTH/no-token kill-switch now disables config/memory/capability writes | M | H | **P1** |
| REL-N01 | New web read routes block the event loop (no to_thread) | M | M | **P1** |
| SEC-006 | Channel webhook fail-open on empty allowlist (sender spoof) | M | M | P2 |
| SEC-011 | CI security job has no dep-CVE/SAST scan | H | M | P2 |
| SEC-010 | Memory/transcripts plaintext at rest | M | M | P2 |
| SEC-013 | `HACHI_GATEWAY_NO_AUTH=1` disables all auth | L | H | P2 |
| SEC-012 | Dockerfile `0.0.0.0` + root user | L | H | P2 |
| REL-008 | No SQLite backup/integrity-check | L | H | P2 |
| REL-003 | Facade shares session per (spoofable) key | M | M | P2 |
| REL-010 | ZIP path no decompression-bomb cap | M | M | P2 |
| REL-N02 | Rate-limiter unbounded growth via spoofable webhook | M | M | P2 |
| SEC-009 | DNS-rebinding TOCTOU in net_guard | L | M | P2 |
| WB-013 / REL-N04 | fts5_only provider fakes id on write failure | L | M | P3 |
| SEC-008 | `run_shell_script` unbounded shell escape | L | M | P3 |
| REL-005 | Daemon token side-channel drift | L | M | P3 |
| REL-002 | Rate-limiter unbounded key growth (base) | L | M | P3 |
| REL-009 | No provider circuit breaker | L | M | P3 |
| SEC-014 | `_require_auth` no-ops on falsy token | L | M | P3 |
| REL-N03 | Streaming facade parks worker on blocked approval | L | M | P3 |
| SEC-017 | Drive results not untrusted-framed | L | L | P3 |
| SEC-015 | Authoring endpoints expose config source | L | L | P3 |
| SEC-016 | Temp script world-readable | L | L | P3 |
| SEC-018 | `VITE_HACHI_TOKEN` inlined in bundle (direct mode) | L | L | P3 |
| SEC-N02 | REST client path interpolation unescaped (latent) | L | L | P3 |
| REL-004 | Approval-registry entry leak on thread death | L | L | P3 |
| REL-006 | Job-queue connection churn under lock | L | L | P3 |
| REL-007 | Func-only job loss on crash (documented) | L | L | P3 |
| REL-011 | Config mutation reboots sessions inline | L | M | P3 |
| REL-012 | SSE cancel poll-based, orphaned turns | L | L | P3 |

**Top priorities:** SEC-001, SEC-005, SEC-003 (P0); then SEC-002, SEC-004, SEC-007, REL-001, SEC-N01,
REL-N01 (P1).

---

## 7. Recommended fix order (security/reliability slice)

**P0 — fix before any release that can install packs:**
1. Route `_extract_zip` through per-member validation + member/size caps (SEC-001/REL-010) — S.
2. Tier-gate + explicit-confirm MCP server start; never auto-start a community `role=mcp` node
   (SEC-005) — M.
3. `validate_outbound_url` + no-redirect opener in `http_request` (SEC-003) — S.

**P1:**
4. Make untrusted-content framing unconditional; use `scan()` only to escalate (SEC-002) — S.
5. Filter Playwright navigations per-hop against net_guard / document residual (SEC-004) — L.
6. Enforce hash/signature on pack install; `--unsafe` opt-out only (SEC-007) — M.
7. Make the approval wait async / fail-fast + cap pending approvals (REL-001/REL-N03) — M.
8. Gate `HACHI_GATEWAY_NO_AUTH` to loopback; treat auth-enabled-no-token as a hard error
   (SEC-N01/SEC-013/SEC-014) — S.
9. Wrap new web read routes in `to_thread` (REL-N01) — S.

**P2:**
10. Webhook fail-closed on empty allowlist (SEC-006) — S.
11. Add `uv pip audit` + `npm audit` + `bandit` to CI (SEC-011) — S.
12. Reap rate-limiter buckets (REL-002/REL-N02) — S.
13. SQLite integrity-check on boot + opt-in backup (REL-008) — M.
14. Non-root `USER` in Dockerfile (SEC-012) — S.

**P3:** at-rest encryption (SEC-010); fts5_only fake-id (REL-N04); DNS-pin (SEC-009);
client path-segment escaping (SEC-N02); remaining low items.
