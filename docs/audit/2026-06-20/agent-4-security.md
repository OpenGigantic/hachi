# Agent 4 — Security & Reliability Audit (2026-06-20)

Repo: `D:\OpenGigantic\hachi` @ `main` (HEAD 0e4dcb6). Scope: auth, authz, session
management, input validation, secrets, SSRF, sandbox, channel injection, rate
limiting, reliability/resilience. Each prior-audit fix verified against source on main.

**Method note:** read of `fastapi_app.py`, `control_plane.py`, `net_guard.py`,
`rest_adapter.py`, `sandbox.py`/`sandbox_tools.py`, `secret_store.py`,
`credential_vault.py`, `permissions.py`, `os_tools.py`, `untrusted_content.py`,
`router.py`, `telegram.py`, `memory_vault.py`, `mcp_client.py`, `rate_limit.py`,
`cli/main.py` (runtime wiring). Dependency versions read from `uv.lock`.

---

## Findings table

| ID | Title | Severity | Verdict | File |
|----|-------|----------|---------|------|
| SEC-01 | No session-ownership enforcement — any bearer token hijacks any session | **Critical** | **STILL-OPEN** | `gateway/control_plane.py:261,342,374,325` |
| SEC-02 | Gateway `/channels/{channel}/inbound` webhook bypasses `guard_untrusted` | High | **PARTIAL** | `gateway/fastapi_app.py:759-775` |
| SEC-03 | Memory recall (`/memory/recall`) returns stored facts un-redacted | Medium | **STILL-OPEN** | `cli/main.py:593-616`, `memory_vault.py:415` |
| SEC-04 | SSRF DNS-pinning (TOCTOU) | High | **FIXED** | `core/net_guard.py`, `core/rest_adapter.py:283-314` |
| SEC-05 | Channel injection guard on polling adapters | High | **FIXED** | `channels/router.py:94-99`, `core/untrusted_content.py` |
| SEC-06 | Sandbox gated behind `HACHI_SANDBOX_PRODUCTION` + AST scan | Critical | **FIXED** | `tools/sandbox_tools.py:87-147` |
| SEC-07 | Telegram bot token redaction in log URL | Medium | **FIXED** | `channels/telegram.py:31-33` |
| SEC-08 | Encrypted secret-store salt reuse | Medium | **FIXED** | `core/secret_store.py:170-194` |
| SEC-09 | Credential in-memory cache TTL | Low | **FIXED** | `core/credential_vault.py:10-62` |
| SEC-10 | `allow_once` single-use (no permanent grant) | Medium | **FIXED** | `core/permissions.py:914-940` |
| SEC-11 | Channel rate-limiter keyed on spoofable `sender_id` | Medium | **PARTIAL** | `gateway/fastapi_app.py:756-771` |
| SEC-12 | Windows shell metachar blocklist (`^`, `%`) | Medium | **FIXED** | `tools/os_tools.py:122-124` |
| SEC-13 | MCP tool-response length cap | Medium | **FIXED** | `core/mcp_client.py:35-36` |
| SEC-14 | Outbound secret redaction (REST kwargs) | Medium | **FIXED** | `core/rest_adapter.py:30-50,273-274` |
| SEC-15 | Dependency CVE bumps (cryptography/starlette/chromadb) | Medium | **FIXED** | `uv.lock` |
| SEC-16 | NO_AUTH only on loopback; non-loopback w/o token = hard error | High | **FIXED** | `gateway/fastapi_app.py:256-340` |
| REL-01 | No per-agent turn lock — concurrent `agent.chat()` races ChromaDB/MemoryManager | High | **STILL-OPEN** | `cli/main.py:189-246` |
| REL-02 | MemoryVault.close() best-effort, no `_closed` flag / lock | Medium | **PARTIAL** | `core/memory_vault.py:157-185` |
| REL-03 | MCP stdout reader-thread death handled with sentinel | Low | **FIXED** | `core/mcp_client.py:96-155` |
| REL-04 | No backup/recovery story for SQLite stores (session/jobs/vault) | Medium | **STILL-OPEN** (NEW) | `core/session_store.py`, `jobs/queue.py` |
| REL-05 | Catch-all session-slot leak guard on boot failure | Low | **FIXED** | `gateway/control_plane.py:220-228` |

Severity counts (open/partial only): Critical 1, High 2, Medium 4, Low 0.
All verdicts: FIXED 12, PARTIAL 3, STILL-OPEN 4, REGRESSED 0, NEW 1 (REL-04).

---

## Critical & high detail

### SEC-01 (Critical, STILL-OPEN) — No session-ownership enforcement
**Root cause.** `GatewayControlPlane` authorizes every session operation purely on
`session_id in self._connections`. There is no association between the bearer-token
identity (there is only one shared token) and a session, and no per-session owner
field. `send_message` (`control_plane.py:261`), `read_session_events` (:342),
`session_state` (:374), and `close_session` (:325) all check membership only.

**Exploit.** Any holder of the single gateway bearer token — every channel webhook
caller, every OpenAI-compat client, the web UI, any local process that read the
token file — can enumerate session IDs via `GET /sessions` (`fastapi_app.py:439`,
returns `recent_sessions`), then `POST /sessions/{id}/messages`, read another
user's event stream/transcript, or close their session. In a multi-client
deployment (Telegram + Slack + web UI sharing one daemon) this is full cross-tenant
session hijack and transcript disclosure.

**Impact.** Confidentiality + integrity break across all concurrent users/channels.
This is exactly the gap BE-1 (PR #128, still DRAFT/unmerged) was meant to close;
confirmed still present on `main`.

**Fix.** Add an `owner`/`principal` field at `create_session` (derived from an
authenticated principal, or at minimum a per-session capability token returned to
the creator and required on subsequent ops). Enforce `owner == caller` in
`send_message`/`read_session_events`/`session_state`/`close_session`, and scope
`recent_sessions` to the caller. Merge BE-1. **Complexity: Medium.**

### SEC-02 (High, PARTIAL) — Channel webhook bypasses untrusted-content guard
**Root cause.** The HTTP webhook `POST /channels/{channel}/inbound`
(`fastapi_app.py:759`) calls `control_plane.complete_chat(payload.text, ...)` with
the **raw** inbound text. That path resolves to `runtime.chat` →
`agent.chat(prompt)` (`cli/main.py:244-246`) with no `guard_untrusted` wrapping.
The polling adapters route through `ChannelRouter.dispatch`, which DOES wrap
(`router.py:94-99`) — but the webhook is a parallel ingress that skips the router.

**Exploit.** A third party who can POST to the webhook (anyone with the bearer
token, or any allowlisted sender) delivers a prompt-injection payload that reaches
the LLM unframed — the spotlighting mitigation that protects the polling path is
absent here.

**Impact.** Prompt-injection / agent-hijack via the webhook ingress; inconsistent
with the documented HIGH-002 remediation.

**Fix.** Wrap webhook text with `guard_untrusted(payload.text,
source=f"{channel}:{sender}")` before `complete_chat`, OR push the guard down into
`complete_chat`/`runtime.chat` so every ingress is covered uniformly (preferred —
removes the per-ingress footgun). **Complexity: Low.**

### SEC-11 (Medium, PARTIAL) — Rate-limiter keyed on spoofable sender_id
**Root cause.** `channel_limiter.allow(f"{channel}:{sender}")`
(`fastapi_app.py:769-770`) and the allowlist both trust the client-supplied
`payload.sender_id`. An attacker rotates `sender_id` per request to get a fresh
bucket each time, defeating the per-sender cap. There is no global/IP fallback
limiter on this route.

**Impact.** Cost-amplification / DoS — each inbound boots or drives an agent turn
(LLM spend). The cap is bypassable by a single client.

**Fix.** Add a second limiter keyed on a non-spoofable dimension (source IP from
`request.client.host`, or a global token-bucket on the route) in addition to the
per-sender bucket. For platform webhooks, verify the platform signature so
`sender_id` is trustworthy. **Complexity: Low.**

### REL-01 (High, STILL-OPEN) — No per-agent turn serialization
**Root cause.** `_agents_lock` (`cli/main.py:182`) guards only the `_agents` dict
lookup (`_get_agent`, :189-194). `chat()` (:244-246) releases the lock and then
calls `agent.chat(prompt)` with no per-agent lock. Two concurrent requests for the
**same** `session_id` therefore run `agent.chat()` reentrantly.

**Failure scenario.** The OpenAI streaming path runs `_run_turn` in a thread while
the SSE loop reads events (`fastapi_app.py:661-709`); a second
`POST /sessions/{id}/messages` (or a duplicate webhook for the same sender) can
start a concurrent turn on the same agent. Reentrant turns race the agent's
MemoryManager message history and ChromaDB writes (Chroma's embedded SQLite is not
safe under concurrent writers from one process), producing corrupted context,
interleaved tool calls, or `database is locked` errors.

**Impact.** Data corruption in transcript/memory; intermittent turn failures under
concurrency. Likelihood rises with multi-channel deployments.

**Fix.** Add a per-`AgentSession` `threading.Lock` (or an asyncio-serialized
work-queue per session) and hold it for the duration of `agent.chat()`. Reject or
queue overlapping turns for the same session. **Complexity: Medium.**

### SEC-16 / CRIT-001 context (High, FIXED) — Auth posture
`create_gateway_app` enforces: NO_AUTH only on loopback (`:259-263`), non-loopback
without token = hard `ValueError` at startup (`:266-270`), constant-time
`secrets.compare_digest` on the bearer (`:339`), CORS allow-list (no `*` with
credentials, `allow_credentials=False`), and a catch-all exception handler that
logs server-side with a correlation id and never leaks tracebacks to clients
(`:363-384`). The previously-overstated "auth disabled when no token configured"
case (`_require_auth` returns when `auth_token` is falsy, `:333-336`) only triggers
in test helpers / the OpenAPI-export path that never serves — confirmed Low, not
re-raised as Critical per scope.

---

## Verified-fixed (condensed)

- **SEC-04 SSRF/TOCTOU — FIXED.** `validate_outbound_url` resolves once, rejects
  non-public IPs across *all* resolved addresses, and returns `resolved_ips`. The
  REST caller pins one resolved IP into the connect URL, sets the `Host` header for
  SNI/vhost, and uses `follow_redirects=False` (`rest_adapter.py:283-314`) — the
  DNS-rebinding window is closed. `localhost`/`*.localdomain` rejected by name too.
- **SEC-05 channel injection — FIXED** for the router/polling path: text wrapped
  unconditionally in a DATA envelope (`untrusted_content.py:37-63`); scanner only
  escalates the banner, never gates framing. (Webhook ingress gap = SEC-02.)
- **SEC-06 sandbox — FIXED.** Tool only registered when `HACHI_SANDBOX_PRODUCTION=1`
  (logs CRITICAL when active), tier-gated against sandbox/guided, and AST + raw-text
  scan blocks `.hachi`/gateway-token references (`sandbox_tools.py:99-147`).
  Dependency names validated against shell metachars (`sandbox.py:100-118`); minimal
  env passed to subprocess. (The host-isolation caveat is honestly documented.)
- **SEC-07 Telegram token — FIXED.** Separate `_API_LOG` template with `<redacted>`.
- **SEC-08 salt reuse — FIXED.** Fresh `os.urandom(16)` per write (`secret_store.py:177`).
- **SEC-09 cache TTL — FIXED.** 300s monotonic TTL, evict-on-access (`credential_vault.py`).
- **SEC-10 allow_once — FIXED.** Single-use; not added to session allow-set (`permissions.py:914-940`).
- **SEC-12 Windows metachars — FIXED.** `^` and `%` added to `blocked_fragments` (`os_tools.py:124`).
- **SEC-13 MCP cap — FIXED.** Hard response cap with truncation marker (`mcp_client.py:35`).
- **SEC-14 outbound secret redaction — FIXED.** `_redact_mapping_secrets` on REST
  params/body, opt-out via `HACHI_ALLOW_OUTBOUND_SECRETS` (`rest_adapter.py:273-274`).
- **SEC-15 CVE bumps — FIXED.** `cryptography 49.0.0`, `starlette 1.3.1`,
  `chromadb 1.5.9`, `fastapi 0.136.3`, `anyio 4.13.0`, `httpx 0.28.1` — all current,
  none in known-CVE ranges. (Static-version inspection; runtime `security-audit`
  not run — `.exe` blocked on this Windows host.)
- **REL-03 MCP reader death — FIXED.** `_reader_dead` sentinel set on read error/EOF;
  waiters woken via the condition variable (`mcp_client.py:96-155`).
- **REL-05 boot-failure slot leak — FIXED.** `create_session` pops the session slot
  if `on_connect` raises (`control_plane.py:220-228`).

## Remaining-open reliability

- **REL-02 (Medium, PARTIAL).** `MemoryVault.close()` is best-effort
  (`memory_vault.py:157-185`): no `_closed` flag and no lock, so a `close()`
  concurrent with an in-flight `semantic_search`/`upsert` can hit a None'd
  collection reference and raise. Add a `_closed` flag + lock and guard read/write
  paths. **Complexity: Low.**
- **REL-04 (Medium, NEW).** No backup/recovery story for the SQLite stores
  (`session_store.py` transcripts, `jobs/queue.py` queue, embedded Chroma WAL). No
  documented snapshot/restore, no integrity check on boot, no WAL-checkpoint on
  graceful shutdown beyond the vault `close()`. A crash mid-write or a corrupted DB
  has no recovery path. Recommend: periodic `VACUUM INTO` snapshot, integrity-check
  on startup with quarantine-and-rebuild fallback, documented restore. **Complexity: Medium.**

---

## Risk Priority Matrix (likelihood × impact)

```
            IMPACT →     Low            Medium              High / Critical
 LIKELIHOOD
   High                  REL-02         SEC-11, REL-04      SEC-01, SEC-02, REL-01
   Medium                SEC-09         SEC-03              —
   Low                   —              —                   —
```

- **Top priority (fix before any multi-client / network exposure):** SEC-01
  (cross-tenant session hijack — Critical, high likelihood once >1 client),
  SEC-02 (webhook injection bypass), REL-01 (concurrent-turn data corruption).
- **Next:** SEC-11 (rate-limit bypass DoS/cost), REL-04 (no backup story),
  SEC-03 (recall secret disclosure).
- **Lower:** REL-02 (close race), SEC-09 (already FIXED, residual rotation window).

---

## Security posture assessment

The merged remediation work is **real, not cosmetic**: SSRF DNS-pinning, sandbox
env-gating + AST scan, salt-per-write, allow_once single-use, Windows metachar
blocklist, MCP cap, token redaction, and dependency bumps are all genuinely
implemented and verifiable in source. The auth posture (constant-time compare,
loopback-gated NO_AUTH, hard-fail on non-loopback-without-token, leak-proof error
handler) is sound. However, the **single most dangerous gap — CRIT-006 session
ownership (SEC-01) — remains open on `main`** because BE-1 (#128) is still
unmerged: with one shared bearer token and no per-session owner, any client can
hijack, read, or close any other client's session. Combined with the webhook
injection bypass (SEC-02) and the absent per-agent turn lock (REL-01), the gateway
is **not safe for multi-client or network-exposed deployment** until SEC-01/02 and
REL-01 are closed; loopback single-user use is acceptable.
