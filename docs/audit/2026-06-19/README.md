# Hachi Product Quality Audit 2026-06-19

> **⚠️ Read [`VERIFICATION.md`](./VERIFICATION.md) before acting on this report.**
> The 8 critical blockers were independently re-validated against source. Corrections:
> - **CRIT-004 (safety mismatch) is a FALSE POSITIVE** — a working `adaptTier`/`safetyToTier`
>   mapping exists in `config.ts`. Do **not** spend a sprint task on it; convert GATE-07 to a
>   regression test.
> - **CRIT-001 (auth bypass) is OVERSTATED** → downgrade to Low. The only tokenless caller is
>   `gateway-openapi`, which never serves traffic. Keep a fail-closed guard, but it is not a gate.
> - **HIGH-005 (work.tsx empty-data crash) is FIXED** in this pass (guards + empty-state).
>   The `index.tsx`/`og.tsx` co-citations were overstated (cosmetic only).
> - **HIGH-001**: DNS-rebinding TOCTOU confirmed; the "wildcard allowlist" clause is a false positive.
>
> Verified critical blockers: **6** (CRIT-002, -003, -005, -006, -007, -008). Verdict 44/100 NO-GO
> still stands. Machine-readable findings with verification status: [`findings-raw.json`](./findings-raw.json).

## Executive Summary

**Health Score: 44/100 — Verdict: NO-GO**

Hachi is an architecturally coherent autonomous agent engine with a compelling differentiator in its `.og` ObjectGraph skill format and a well-designed permission and spend governance model. The product as shipped is not ready for any public release across any surface.

The web frontend has five non-functional core features: Authoring (Generate button has no `onClick` handler; Apply/Revert endpoints are explicit stubs), the kennel skill browser, session resume, live-run streaming, and the MCP server add flow. These collectively mean the primary advertised workflows either crash silently or do nothing when clicked.

The security posture has one critical auth bypass (`create_gateway_app` called without a token silently disables all auth), an unsandboxed Python executor with full host filesystem and network access, and no session ownership enforcement, making the product unsafe for any multi-user or externally-exposed deployment.

Performance hot paths — BM25 O(V) prefix scan on every agent turn, blocking synchronous LLM call during context compaction, and per-operation SQLite connection churn — will produce visible latency degradation under modest real-world load.

The composite health score of 44/100 reflects a product that demonstrates genuine vision and strong foundational architecture but has a large and largely unfixed gap between its mock-mode demonstration and its live-mode reality.

---

## Audit Methodology

This audit used a 5-agent parallel review approach, each agent owning a distinct analysis domain. Agents operated independently and findings were synthesized into a unified report.

| Agent | Domain | Health Score | Files Reviewed |
|---|---|---|---|
| **Black Box** | Frontend UX, CLI surface, mock-vs-live fidelity | 38/100 | CLI backend, all 11 web frontend route files, Zustand store, README |
| **White Box** | Backend architecture, concurrency, test integrity | 55/100 | Gateway, orchestrator, execution loop, memory, jobs, sub-agents |
| **UX/Product** | Onboarding, ICP fit, brand, competitive positioning | 52/100 | 12 routes, onboarding service, README, ObjectGraph spec |
| **Security** | Auth, authorization, SSRF, prompt injection, CVEs | 47/100 | Gateway, permission layer, secret management, MCP client, OS tools, sandbox, channels |
| **Performance** | CPU complexity, LLM latency, database I/O, SSE delivery | 57/100 | Full stack hot path, 14 source files |

The composite score of 44/100 weights security and functional completeness at higher priority given the NO-GO threshold criteria. All findings are drawn from static analysis and behavioral tracing; no live deployment was exercised.

---

## Critical Blockers

Ten confirmed critical blockers prevent the product from being released in any mode.

| ID | Title | Domain | Files |
|---|---|---|---|
| `SEC-001` | Auth silently disabled when `create_gateway_app` called without `auth_token` | Security | `gateway/fastapi_app.py`, `cli/main.py` |
| `F-002 / F-037` | Authoring workflow entirely non-functional: Generate has no `onClick`, Apply/Revert are explicit stubs | Functional | `cli/main.py`, `authoring.tsx`, `domains/authoring.ts` |
| `F-003` | "Browse the kennel" button is a confirmed noop — primary skill acquisition flow broken | Functional | `frontend/src/routes/skills.tsx` |
| `F-001` | Safety level names mismatched frontend-to-backend — safety changes silently ignored | Security | `settings.tsx`, `types.ts`, `gateway/fastapi_app.py` |
| `SEC-006` | Python sandbox executor has full host filesystem access and unrestricted network egress | Security | `core/sandbox.py` |
| `SEC-004` | No session ownership enforcement — any bearer token can hijack any session | Security | `gateway/fastapi_app.py`, `gateway/control_plane.py` |
| `F-004` | Session resume `--resume` / `--continue` silently drops context and starts fresh | Functional | `cli/main.py` |
| `ONB-001` | Two-terminal setup blocks the 5-minute first-value path | Product | `README.md`, `cli/main.py`, `docker-compose.yml` |
| `F-015` | `/run` route and all "Watch live" buttons render a static redirect card — observability absent | Functional | `routes/run.tsx`, `routes/work.tsx`, `gateway/fastapi_app.py` |
| `F-001` | Frontend `SafetyLevel` values do not map to backend `PermissionGate` tiers | Security | `settings.tsx`, `gateway/fastapi_app.py` |

### Critical Finding Detail

**CRIT-001 — Auth bypass via `create_gateway_app`**

`_require_auth` short-circuits to a no-op return when `auth_token` is falsy. The `gateway-openapi` export command calls `create_gateway_app` without a token. Any programmatic gateway construction without an explicit token yields a fully unauthenticated app with no startup warning.

```
src/hachi/gateway/fastapi_app.py
src/hachi/cli/main.py
```

**CRIT-002 — Authoring workflow entirely non-functional in live mode**

The Generate button has no `onClick` handler wired. The Apply and Revert gateway endpoints are explicit `TODO` stubs that return no-op responses. The frontend shows success state but nothing is ever written to disk. The Authoring screen is the primary advertised differentiator and is entirely non-functional end-to-end.

```
src/hachi/cli/main.py
frontend/src/routes/authoring.tsx
frontend/src/lib/data/domains/authoring.ts
```

**CRIT-003 — Skills kennel browse button is a confirmed noop**

The "Browse the kennel" button has an explicit comment: `// noop`. Clicking the primary CTA for skill discovery produces no feedback and no action. The `.og` skills format is Hachi's stated competitive moat; its acquisition flow is broken at the entry point.

```
frontend/src/routes/skills.tsx
```

**CRIT-004 — Safety level mismatch frontend-to-backend**

Frontend `SafetyLevel` is `'guarded'/'standard'/'open'`; backend `PermissionGate` expects `'sandbox'/'guided'/'unrestricted'`. No mapping layer exists. Users who configure a restrictive safety tier from the web UI are silently left with whatever default the backend applies.

**CRIT-005 — Python sandbox executor: full host access**

Any LLM-prompted Python script can read `~/.hachi/gateway-token`, all vault credentials, and any file in the hachi process's home directory, then exfiltrate via unrestricted outbound HTTP. No cgroup/namespace/seccomp isolation. Labeled "development convenience" internally but registered as an LLM-callable tool.

```
src/hachi/core/sandbox.py
```

**CRIT-006 — No session ownership enforcement**

`GET /sessions/{id}`, `GET /sessions/{id}/events`, `POST /sessions/{id}/messages` accept any known `session_id` from any authenticated caller. Any bearer-token holder can read another session's conversation history, inject messages, and observe tool-call results in real time.

```
src/hachi/gateway/fastapi_app.py
src/hachi/gateway/control_plane.py
```

**CRIT-007 — Session resume silently drops context**

The CLI `--resume` and `--continue` flags print a yellow warning and start a fresh session, discarding the requested session ID. No non-zero exit code is returned. Workflows that rely on documented session resumption silently duplicate work and lose state.

```
src/hachi/cli/main.py
```

**CRIT-008 — Two-terminal setup blocks first-value path**

Getting started requires `uv sync`, then `hachi gateway` in terminal 1, then `npm run dev` in terminal 2. Estimated 15–25 minutes for a developer without all prerequisites. No `hachi serve` single-process path exists.

---

## Findings by Severity

### Critical (8 findings)

| ID | Title | Category | Effort | Files |
|---|---|---|---|---|
| CRIT-001 | Auth silently disabled when `create_gateway_app` called without `auth_token` | Security | Small | `gateway/fastapi_app.py`, `cli/main.py` |
| CRIT-002 | Authoring workflow entirely non-functional: Generate no `onClick`, Apply/Revert are stubs | Functional | Large | `cli/main.py`, `authoring.tsx`, `domains/authoring.ts` |
| CRIT-003 | Skills kennel browse button is a confirmed noop | Functional | Medium | `routes/skills.tsx` |
| CRIT-004 | Safety level names mismatched frontend-to-backend — changes silently ignored | Security | Small | `settings.tsx`, `types.ts`, `fastapi_app.py` |
| CRIT-005 | Python sandbox executor: full host filesystem access and unrestricted network egress | Security | XL | `core/sandbox.py` |
| CRIT-006 | No session ownership enforcement — any bearer token can hijack any session | Security | Medium | `fastapi_app.py`, `control_plane.py` |
| CRIT-007 | Session resume `--resume`/`--continue` silently drops context and starts fresh | Functional | Large | `cli/main.py` |
| CRIT-008 | Two-terminal setup blocks the 5-minute first-value path | Product | Medium | `README.md`, `cli/main.py`, `docker-compose.yml` |

### High (22 findings)

| ID | Title | Category | Effort |
|---|---|---|---|
| HIGH-001 | DNS rebinding window + wildcard allowlist bypass allows SSRF credential exfiltration | Security | Medium |
| HIGH-002 | Channel inbound messages bypass `guard_untrusted` — prompt injection from public bots | Security | Small |
| HIGH-003 | Parallel tool execution races on ChromaDB writes; chat lock exhausts anyio thread pool | Reliability | Medium |
| HIGH-004 | "Allow once" and "Always allow" call the same handler — no persistence distinction | Functional | Medium |
| HIGH-005 | Division-by-zero in Work chart, OG Explorer, Home budget bar on common new-user data | Functional | Trivial |
| HIGH-006 | `AddMcpRow` in Integrations has no `onClick` — MCP server addition inaccessible from web UI | Functional | Medium |
| HIGH-007 | Live Run screen renders only a static redirect card — sub-agent observability absent | Functional | XL |
| HIGH-008 | Hardcoded model list missing current Claude models including the one Hachi runs on | Functional | Small |
| HIGH-009 | Input token spend metric always 0 — spend reporting structurally incorrect | Functional | Medium |
| HIGH-010 | Memory recall "Closest Matches" shown before any query is run — fixture data as real results | Functional | Trivial |
| HIGH-011 | Sessions Resume button shows misleading toast; does not navigate or restore context | Functional | Medium |
| HIGH-012 | Broad `except Exception: pass` silently discards memory, persistence, and resume failures | Reliability | Small |
| HIGH-013 | Sub-agent model override silently ignored — all delegations run on hardcoded haiku | Architecture | Medium |
| HIGH-014 | BM25 prefix scan is O(V) per query token — full token-index scan on every agent turn | Performance | Small |
| HIGH-015 | Blocking synchronous LLM call during sliding-window compaction doubles latency every ~30 messages | Performance | Medium |
| HIGH-016 | `SQLiteJobQueue` opens a new connection on every operation — 40–90 redundant cycles per session | Performance | Small |
| HIGH-017 | Slash command menu has no keyboard navigation — mouse-only | Accessibility | Small |
| HIGH-018 | Brand vocabulary used without any first-encounter explanation | UX | Trivial |
| HIGH-019 | No first-run guidance in web frontend; chat starters assume Gmail connection that does not exist | UX | Medium |
| HIGH-020 | `.og` ObjectGraph — the core differentiator — is buried and unexplained in the UI | Product | Small |
| HIGH-021 | `MemoryVault.close()` nulls collections without guarding concurrent tool-call threads | Reliability | Small |
| HIGH-022 | Known CVEs unpatched in direct and gateway-adjacent dependencies | Security | Small |

### Medium (30 findings)

| ID | Title | Category | Effort |
|---|---|---|---|
| MED-001 | `SessionStore.log_message` commits on every single write — 3 disk-sync commits per turn | Performance | Small |
| MED-002 | SSE stream loop busy-polls with unconditional 50ms sleep during active streaming | Performance | Medium |
| MED-003 | Channel rate limiter keyed on user-supplied `sender_id` — trivially evaded | Security | Small |
| MED-004 | MCP tool responses returned to LLM with no length cap — context flooding attack vector | Security | Small |
| MED-005 | Telegram bot token embedded in URL path — logged at DEBUG and captured by proxies | Security | Small |
| MED-006 | `EncryptedFileSecretStore` salt reuse — initial salt permanently reused across all writes | Security | Medium |
| MED-007 | Session-approved commands stored with no expiry — `allow_once` is not single-use | Security | Small |
| MED-008 | Windows shell metacharacter blocklist missing `^` and `%` — cmd.exe/PowerShell injection possible | Security | Small |
| MED-009 | Memory recall and session search return raw content with no secret redaction | Security | Small |
| MED-010 | `SessionStore` single non-pooled connection; `ProcessSharedServices` double-checked locking race | Reliability | Medium |
| MED-011 | Zero test coverage for tool-callable `get_agent_config`/`set_agent_config` path | Technical Debt | Small |
| MED-012 | `TurnContext.surface` hardcoded to `'cli'` for all gateway sessions — audit trail unusable | Architecture | Small |
| MED-013 | `WorkDetail` slide-over has no focus trap and no Escape key close — WCAG 2.1.2 violation | Accessibility | Small |
| MED-014 | OG graph is mouse-only with no `tabIndex`, ARIA roles, or keyboard navigation on SVG nodes | Accessibility | Medium |
| MED-015 | Operator-mode features gated behind undiscoverable toggle | UX | Trivial |
| MED-016 | `addFolder` adds literal `'~/new-folder'`; `addCommand` adds literal `'cmd'` — no input dialog | UX | Small |
| MED-017 | Settings "unsaved changes" warning never clears after save | UX | Small |
| MED-018 | Spend caps (per-task, daily) are display-only in Settings — not editable from any UI | UX | Small |
| MED-019 | Memory screen combines six unrelated mental models — cognitive overload on trust-critical feature | UX | Medium |
| MED-020 | Failed message sends leave conversation in permanent streaming state with no error or recovery | Functional | Small |
| MED-021 | Approvals countdown bar never shown — gateway omits `expiresAt` from approval payloads | Functional | Small |
| MED-022 | Multiple independent `AnthropicClient` instantiations per turn; memory pipeline creates up to 4 | Performance | Small |
| MED-023 | ChromaDB cold-start blocks session creation — 5 serial `get_or_create` calls on init | Performance | Medium |
| MED-024 | No push notification or browser notification path for pending approvals — agent stalls silently | Missing Features | Medium |
| MED-025 | Setup wizard allows completion with empty API key | UX | Small |
| MED-026 | `list_mcp_servers` tool creates a new `SkillEngine` and re-indexes all `.og` files on every call | Performance | Trivial |
| MED-027 | Integration connect/disconnect has no loading or error feedback — double-clicks cause duplicates | UX | Small |
| MED-028 | `errors.py` contains only two stub exceptions — no structured `HachiError` hierarchy | Technical Debt | Medium |
| MED-029 | Lightning onboarding tier skips memory setup — fastest path disables core retention mechanic | Product | Trivial |
| MED-030 | Memory retrieval is invisible in the conversation flow — no "recalled N facts" indicator | Product | Medium |

### Low (25 findings)

| ID | Title | Category | Effort |
|---|---|---|---|
| LOW-001 | Work cost chart hardcodes `'turn 1'`/`'turn 7'` axis labels regardless of actual turn count | Functional | Trivial |
| LOW-002 | Authoring prompt textarea retains previous example text when target changes | UX | Trivial |
| LOW-003 | Authoring diff summary always shows hardcoded `'+3 -2 lines'` | Functional | Trivial |
| LOW-004 | Empty section headers rendered with no content guard on Memory, Approvals, Work screens | UX | Trivial |
| LOW-005 | Home starter prompts hardcoded — not filtered by connected integrations | UX | Small |
| LOW-006 | Memory recall pre-filled with repo-specific example query | UX | Trivial |
| LOW-007 | "From the kennel" section header renders with empty grid when no kennel items exist | UX | Trivial |
| LOW-008 | No loading state for recall fetch — users may double-click | UX | Trivial |
| LOW-009 | Authoring screen shows no empty state or loading state when no `.og` targets are returned | UX | Trivial |
| LOW-010 | Home approval list sliced to 3 with no "view N more" indicator | UX | Trivial |
| LOW-011 | Settings restart-required notice does not specify which settings require restart | UX | Trivial |
| LOW-012 | OpenAI-compat endpoint returns hardcoded zero usage tokens — breaks Open WebUI and LibreChat | Technical Debt | Small |
| LOW-013 | `CredentialVault` in-memory cache has no TTL — rotated credentials persist until process restart | Security | Small |
| LOW-014 | MCP stdout reader thread death is silent — all subsequent tool calls time out after 30s | Reliability | Small |
| LOW-015 | `SkillEngine.record_use` writes full `usage.json` to disk on every `load_skill` call | Performance | Trivial |
| LOW-016 | Prompt cache breakpoints on last-2-messages shift every turn — cache hit rates low | Performance | Medium |
| LOW-017 | `TurnContext.build` produces trailing double-newline when `dynamic_system` is empty | Technical Debt | Trivial |
| LOW-018 | No structured logging — all log messages are unstructured strings | Technical Debt | Medium |
| LOW-019 | README subtitle "OpenClaw-class autonomy" anchors Hachi to a competitor brand at first impression | Product | Trivial |
| LOW-020 | No session selector or session title in the web Conversation screen | Missing Features | Small |
| LOW-021 | Zustand store initializes 20+ fixture arrays unconditionally — thrown away in live mode | Performance | Trivial |
| LOW-022 | Activity audit trail is operator-only — non-operator users cannot verify what the agent did | Product | Small |
| LOW-023 | Multi-agent delegation is undiscoverable — no slash command, no explanation | Product | Small |
| LOW-024 | Web slash command menu missing `/plan`, `/memory`, `/sessions`, `/model`, `/delegate` | Missing Features | Small |
| LOW-025 | Web frontend is entirely desktop-only — no responsive layout for mobile approval monitoring | Missing Features | Medium |

---

## Findings by Category

| Category | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| Security | 3 | 3 | 7 | 1 | **14** |
| Functional Bugs | 3 | 8 | 2 | 2 | **15** |
| Performance | 0 | 3 | 6 | 3 | **12** |
| Reliability | 0 | 3 | 1 | 2 | **6** |
| UX Issues | 0 | 2 | 8 | 5 | **15** |
| Product Gaps | 1 | 2 | 2 | 5 | **10** |
| Architecture | 0 | 1 | 1 | 0 | **2** |
| Accessibility | 0 | 1 | 2 | 0 | **3** |
| Missing Features | 0 | 0 | 1 | 3 | **4** |
| Technical Debt | 0 | 0 | 2 | 4 | **6** |
| **Total** | **8** | **22** | **30** | **25** | **85** |

---

## Product Gap Analysis

### Competitive Comparison

| Dimension | OpenClaw | Hermes | Hachi (current) | Hachi (target) |
|---|---|---|---|---|
| **Setup time** | Single command | Single command | 15–25 min (two terminals) | `hachi serve` single-process |
| **Skill discovery** | Marketplace (primary growth vector) | Built-in library | Kennel button is noop | Functional kennel browse + install |
| **Conversational recall** | Limited | FTS5 integrated into chat flow | ChromaDB exists but invisible at point of use | Recall visible in conversation turn |
| **Live observability** | Real-time agent trace | Real-time agent trace | `/run` is a static placeholder | Live SSE streaming on `/run` |
| **Mobile approvals** | Desktop-only | Desktop-only | Desktop-only | Web Push + responsive layout |
| **Spend transparency** | None | None | Analytics exist (leading); caps display-only | Editable spend caps |
| **Sub-agent delegation** | Partial | No | Implemented but silent model ignore | Per-delegation model selection |
| **Slash commands (web)** | Full | N/A | Missing `/plan`, `/memory`, `/sessions`, `/model`, `/delegate` | CLI parity |

### Unique Moat Not Yet Surfaced

The `.og` ObjectGraph token-efficiency story, versioned skills, and three-pass read model are not explained anywhere in the primary user flow. No screen tells a first-time user that skills are versioned, diffable, and dramatically more token-efficient than markdown. The core competitive differentiator is invisible to a first-time user.

### Missing Capabilities Required Before Release

1. `hachi serve` single-process command serving the pre-built frontend bundle
2. Live run streaming endpoint: `GET /sessions/{id}/stream` mapped to `runtime_events.py` typed events
3. Authoring Generate backend: endpoint accepting a prompt and target `.og` file, returning a real diff
4. Authoring Apply and Revert backend: actual filesystem write and revert handlers replacing `TODO` stubs
5. Skills kennel browser: modal listing available `.og` skills, filtering installed ones, wiring `addSkill()`
6. Session ownership enforcement: binding sessions to authenticated identity at creation
7. MCP server add flow: `AddMcpRow` dialog wired to the gateway's MCP registration endpoint
8. Push/browser notifications for pending approvals
9. Session selector in the Conversation screen
10. Safe sandbox isolation before the Python executor is registered as an LLM-callable tool in production
11. Dynamic model list replacing the hardcoded two-entry list
12. Always-allow rule persistence surviving gateway restart

---

## Risk Assessment Matrix

| Risk | Likelihood | Impact | Mitigation | Action |
|---|---|---|---|---|
| Silent auth bypass in production | High | Critical | Any code path calling `create_gateway_app` without a token produces an unauthenticated app | Raise at construction time or auto-generate ephemeral token; add startup assertion (E1-T1) |
| Sandbox-to-credential exfiltration | High | Critical | LLM can be prompted to read `~/.hachi/gateway-token` and exfiltrate via unrestricted HTTP | Gate sandbox tool registration behind `HACHI_SANDBOX_PRODUCTION` env var; add working-directory restriction (E3-T2) |
| Safety tier enforcement broken end-to-end | High | Critical | Frontend sends `'guarded'`; backend expects `'sandbox'`; no mapping; user believes they are protected | Add a frontend-to-backend mapping layer; add a round-trip integration test (CRIT-004) |
| Mock-vs-live fidelity gap causes false confidence | High | High | Authoring, kennel browse, session resume, and live run all work in mock but are stubs in live | Each stub must be replaced with a real implementation before release; tracked in E4 epics |
| Prompt injection from external channels | High | High | Any public-facing Telegram/Slack bot can be hijacked by any message sender | Wrap all inbound channel messages in `guard_untrusted()` before LLM dispatch (E3-T3) |
| Concurrent session thread pool exhaustion | Medium | High | 40 concurrent slow LLM turns exhaust the default anyio thread pool, making health checks unresponsive | Move compaction to background job; document thread-pool sizing (E5-T3) |
| DNS rebinding SSRF with credential headers | Medium | High | SSRF validation and TCP connect are separate operations; short-TTL DNS rebinding can redirect to cloud metadata | Atomically resolve and pin IP at check time; reject wildcard allowlist entries (E3-T5) |
| Test suite gives false confidence | Medium | High | Zero test coverage for the tool-callable `get_agent_config`/`set_agent_config` path | Add integration tests for the tool-callable path (E2-T1) |
| Sub-agent model override silently ignored | High | Medium | `delegate_task(model='claude-opus-4-8')` always runs on hardcoded haiku with no warning | Create new client when `model` override is explicitly specified in `delegate_task` (E6-T2) |
| Known unpatched CVEs in direct dependencies | Medium | Medium | `cryptography` (GHSA-537c-gmf6-5ccf), `starlette` CVEs, `chromadb` CVE; all acknowledged but unpatched | Bump to patched versions; run security audit to confirm (E3-T4) |
| BM25 O(V) scan degrades every agent turn | High | Medium | Tens of millions of comparisons per search at 10k-token vocabulary; compounds under load | Replace with sorted index + bisect for O(log V) lookup (E5-T1) |
| Session isolation missing in multi-user deployment | High | High | Any authenticated bearer token can enumerate and read any session | Bind `owner_token_hash` at session creation; enforce on all read/write/stream routes (E3-T1) |

---

## Engineering Execution Plan

### Epic E1 — Critical Stability and Security Blockers
**Priority: Critical | Sprint: 1**

Eight confirmed critical blockers prevent safe or functional use of Hachi in any mode. Auth bypass, division-by-zero crashes, hardcoded model list, and fixture data shown as real results all affect every user on every session.

---

**E1-T1 — Fix `create_gateway_app` auth bypass when `auth_token` is None**
Agent: C | Effort: Small | Risk: High

*Objective:* Ensure that any call to `create_gateway_app` without an explicit `auth_token` either raises at construction time or generates a random token automatically, eliminating the silent unauthenticated mode. The `gateway-openapi` export command must be audited and either given an explicit ephemeral token or use a separate no-auth build scoped to schema export only.

*Files:*
```
src/hachi/gateway/fastapi_app.py
src/hachi/cli/main.py
```

*Acceptance Criteria:*
- `uv run hachi gateway-openapi --output /tmp/test.json` with no `HACHI_GATEWAY_API_TOKEN` set either succeeds without starting a serving gateway, or fails fast with a clear error before any port is bound
- Calling `create_gateway_app(control_plane)` with no `auth_token` either raises `ValueError` or logs a `WARNING` and generates an ephemeral token — never silently serves unauthenticated
- An integration test creates an app via `create_gateway_app(cp)` without a token, sends a request to a protected route, and verifies `401`
- The existing test suite passes without modification

*Verification:*
```bash
uv run hachi gateway-openapi --output /tmp/schema.json  # must not bind port or must exit non-zero
```

---

**E1-T2 — Fix division-by-zero and `Math.max(-Infinity)` crashes in Work, Home, and OG screens**
Agent: D | Effort: Trivial | Risk: Low

*Objective:* Guard all three crash-producing math expressions: (1) `computeSpend` divides by `(cum.length-1)` when `spendTurns` has exactly one entry; (2) `Math.max(...[])` returns `-Infinity` when `spendTurns` is empty; (3) `BudgetSummary` divides by `budget` when `budget` is 0.

*Files:*
```
frontend/src/routes/work.tsx
frontend/src/routes/index.tsx
frontend/src/routes/og.tsx
```

*Acceptance Criteria:*
- Navigating to `/work` with zero spend turns renders an empty-state message, not a broken chart with `NaN` values
- Navigating to `/work` with exactly one spend turn renders a valid single-bar chart with no `NaN` CSS values
- Navigating to `/` with `budget=0` renders `BudgetSummary` with "0% of unlimited" or similar
- All three fixes covered by Vitest unit tests

*Verification:*
```bash
npm run --prefix frontend test -- --testPathPattern=work
```

---

**E1-T3 — Replace hardcoded model list with config-file-driven list including `claude-sonnet-4-6`**
Agent: A | Effort: Small | Risk: Low

*Objective:* `list_models()` returns a hardcoded list missing the running model. Replace with a list that reads from a `models.json` config file bundled with the package, and update the Settings UI selector to show the actually-configured model as selected.

*Files:*
```
src/hachi/cli/main.py
frontend/src/routes/settings.tsx
frontend/src/lib/data/domains/config.ts
```

*Acceptance Criteria:*
- `GET /models` response includes `'claude-sonnet-4-6'` and `'claude-haiku-4-6'`
- Settings screen model selector highlights the model returned by `GET /config` as the current selection
- Selecting a different model and saving results in a `PUT /config/provider` call with the new model name
- The models list can be extended by editing a config file without code changes

*Verification:*
```bash
curl -H 'Authorization: Bearer $TOKEN' http://localhost:8787/models | jq '.[] | select(.id == "claude-sonnet-4-6")'
```

---

**E1-T4 — Fix memory recall fixture data shown before any query is run**
Agent: D | Effort: Trivial | Risk: Low

*Objective:* `recallDone` is initialized to `true` in the Zustand store fixtures, causing the Closest Matches section to render with stale fixture data before any query. Initialize `recallDone` to `false` in live mode.

*Files:*
```
frontend/src/lib/store/store.ts
frontend/src/routes/memory.tsx
```

*Acceptance Criteria:*
- In live mode (`USE_MOCKS=false`), the Closest Matches section is hidden on initial render and only appears after the user submits a query
- After submitting a query in live mode, real results from `GET /memory/recall` are shown
- Mock mode behavior is unchanged

---

**E1-T5 — Implement `hachi serve` single-process command eliminating two-terminal setup**
Agent: A | Effort: Medium | Risk: Medium | Dependencies: E1-T1

*Objective:* Add a `hachi serve` Typer command that (1) builds or serves the pre-built frontend bundle from a static file directory, (2) starts the gateway on `127.0.0.1:8787` with an auto-generated token, and (3) opens the browser to `http://localhost:8787`. Should work without Node.js installed if the frontend build artifact is pre-bundled.

*Files:*
```
src/hachi/cli/main.py
src/hachi/gateway/fastapi_app.py
README.md
```

*Acceptance Criteria:*
- `uv run hachi serve` starts within 10 seconds and prints a URL to stdout
- Opening the printed URL shows the Hachi web frontend connected to the gateway (not the Vite dev server)
- No second terminal is required
- `hachi serve --port 9090` uses the specified port

*Verification:*
```bash
uv run hachi serve  # browser opens to localhost URL, sends a chat message, receives a response without second terminal
```

---

**E1-T6 — Fix session resume `--resume` and `--continue` to actually restore session context**
Agent: A | Effort: Large | Risk: Medium

*Objective:* Implement actual session restoration by loading the selected session's message history from `SessionStore` into `MemoryManager` before the first turn. `--continue` should load the most recent session. `--resume` should accept a session ID.

*Files:*
```
src/hachi/cli/main.py
src/hachi/gateway/orchestrator.py
src/hachi/core/session_store.py
```

*Acceptance Criteria:*
- `hachi --continue` after a prior session resumes the conversation with prior context visible
- No yellow "starting fresh" warning is shown when a valid session is resumed
- Exit code is non-zero if the requested session ID does not exist

*Verification:*
```bash
hachi run  # send "my name is Alex", exit
hachi --continue  # send "what is my name?" — response must reference Alex
```

---

### Epic E2 — Test Integrity and False-Green Prevention
**Priority: Critical | Sprint: 1**

The prior audit's v2 remediation was largely cosmetic because tests mocked the wrong types. The tool-callable path for `get_agent_config`/`set_agent_config` has zero end-to-end coverage.

---

**E2-T1 — Add integration tests for the tool-callable `get_agent_config` and `set_agent_config` path**
Agent: B | Effort: Small | Risk: Medium

*Objective:* No test exercises the registered tool as the LLM would call it: by invoking the tool through `ToolRegistry` with a JSON input dict and verifying the JSON string response.

*Files:*
```
tests/test_config_tools.py
src/hachi/tools/config_tools.py
```

*Acceptance Criteria:*
- A test calls `register_config_tools(registry)` then invokes `'get_agent_config'` via the registry with no args and parses the JSON response — must succeed without `AttributeError`
- A test calls `'set_agent_config'` via the registry with `{key: 'agent.name', value: 'test-bot'}` and verifies the config write is called
- A test calls `'set_agent_config'` with `{key: 'permissions.tier', value: 'autonomous'}` and verifies a blocked/error response, not an exception
- All new tests run against real `ResolvedConfig` instances, not mocked namespaces

---

**E2-T2 — Add live-mode gateway integration smoke tests**
Agent: B | Effort: Medium | Risk: Medium | Dependencies: E1-T1

*Objective:* All existing gateway tests use mocked control planes. Add a `@pytest.mark.integration` suite that spins up a real `create_gateway_app` instance with a real `GatewayControlPlane` and a test auth token.

*Files:*
```
tests/test_gateway_integration.py
.github/workflows/ci.yml
```

*Acceptance Criteria:*
- An integration test posts to `/sessions` without a bearer token and receives `401`
- An integration test posts to `/sessions` with a valid bearer token and receives a `session_id`
- An integration test calls `PUT /config/permissions` with `tier='guided'` and receives `200`
- Tests are marked `@pytest.mark.integration` and skipped in the default `uv run pytest -q` run

---

**E2-T3 — Verify and lock the config-get/set CLI round-trip against real on-disk state**
Agent: B | Effort: Small | Risk: Medium

*Objective:* Verify the CLI config-get and config-set commands work end-to-end with a real temporary `settings.toml` file. The test must write the file and then read it back, proving persistence is not mocked.

*Files:*
```
tests/test_config_get_set.py
```

*Acceptance Criteria:*
- At least one test writes a real `settings.toml` via `'hachi config-set provider.model claude-sonnet-4-6'`, then reads it back via `'hachi config-get provider.model'`, and asserts the value matches without any patches to the file I/O path
- No test patches `write_global_config` or `load_effective_config` in the actual assertion path

---

### Epic E3 — Security Hardening
**Priority: High | Sprint: 2**

Six confirmed critical and high security findings. The sandbox exfiltration and session hijacking risks are the most severe.

---

**E3-T1 — Add session ownership enforcement**
Agent: C | Effort: Medium | Risk: High | Dependencies: E1-T1

*Objective:* Every session must be owned by the identity (bearer token hash or user_id) that created it. Store `owner_token_hash` in the session record at creation time. Enforce on all read/write/stream routes.

*Files:*
```
src/hachi/gateway/fastapi_app.py
src/hachi/gateway/control_plane.py
src/hachi/core/session_store.py
```

*Acceptance Criteria:*
- `POST /sessions` creates a session with `owner_token_hash` set to `sha256(bearer_token)`
- `GET /sessions/{id}`, `POST /sessions/{id}/messages`, and `GET /sessions/{id}/stream` with a different valid bearer token return `403`
- With the creating token, all return `200`

---

**E3-T2 — Add network egress blocklist and working directory restriction to Python sandbox**
Agent: C | Effort: Medium | Risk: High

*Objective:* As an interim measure before full container isolation: restrict the execution working directory to `sandbox_root`, block reads of `~/.hachi/` via a pre-execution AST scan, and gate LLM tool registration behind `HACHI_SANDBOX_PRODUCTION=0`.

*Files:*
```
src/hachi/core/sandbox.py
src/hachi/tools/os_tools.py
src/hachi/gateway/orchestrator.py
```

*Acceptance Criteria:*
- A sandboxed script that reads `Path.home() / '.hachi' / 'gateway-token'` raises an exception or returns empty string
- The sandbox tool is NOT registered in `ToolRegistry` unless `HACHI_SANDBOX_PRODUCTION=1` is explicitly set
- A CRITICAL `log.warning` is emitted at registration time if `HACHI_SANDBOX_PRODUCTION=1`

---

**E3-T3 — Wrap channel inbound messages in `guard_untrusted` before LLM dispatch**
Agent: C | Effort: Small | Risk: Medium

*Objective:* All channel inbound messages (Telegram, Slack, Discord, WhatsApp) are passed verbatim to the LLM. Wrap all inbound channel message text in `guard_untrusted()` before adding to the message queue.

*Files:*
```
src/hachi/channels/telegram.py
src/hachi/channels/slack.py
src/hachi/channels/discord.py
src/hachi/channels/whatsapp.py
src/hachi/channels/router.py
```

*Acceptance Criteria:*
- All adapters wrap `InboundMessage.text` in `guard_untrusted()` before returning
- A unit test verifies an injection payload is wrapped with the untrusted-content marker

---

**E3-T4 — Bump unpatched CVE dependencies**
Agent: C | Effort: Small | Risk: Medium

*Objective:* Bump `cryptography` (GHSA-537c-gmf6-5ccf), `starlette` (CVE-2026-54282, CVE-2026-54283), and `chromadb` (CVE-2026-45829) to patched versions.

*Files:*
```
pyproject.toml
uv.lock
SECURITY.md
```

*Acceptance Criteria:*
- `uv run hachi security-audit --json` shows no HIGH findings for these CVE IDs
- `uv run pytest -q` passes with 0 failures after the bumps

---

**E3-T5 — Fix DNS rebinding SSRF window in `net_guard`**
Agent: C | Effort: Medium | Risk: Medium

*Objective:* `validate_outbound_url` resolves the hostname at check time but TCP connect happens later, creating a TOCTOU window. Fix by resolving the IP at check time and pinning it for the subsequent connection. Reject wildcard allowlist entries.

*Files:*
```
src/hachi/core/net_guard.py
src/hachi/core/rest_adapter.py
```

*Acceptance Criteria:*
- `validate_outbound_url` raises `ValueError` for allowlist entries containing `'*'` in the hostname position
- A test mocks `socket.getaddrinfo` to return a private IP on the second call and verifies the connection is blocked

---

### Epic E4 — Frontend Functional Completeness
**Priority: High | Sprint: 2**

Nine confirmed high-severity functional bugs prevent core user workflows from completing in live mode.

---

**E4-T1 — Wire `onAlwaysAllow` to a persistent backend endpoint, separate from `onAllowOnce`**
Agent: D | Effort: Medium | Risk: Medium

*Objective:* In `conversation.tsx` and `run.tsx`, both permission buttons call the same `icAllow` handler. Implement `icAlwaysAllow` as a separate store action calling `POST /sessions/{id}/permissions/always-allow`. The backend must write the rule to a persistent store so it survives session restart.

*Files:*
```
frontend/src/routes/conversation.tsx
frontend/src/routes/run.tsx
frontend/src/lib/store/store.ts
src/hachi/gateway/fastapi_app.py
src/hachi/core/permissions.py
```

*Acceptance Criteria:*
- Clicking "Always allow" calls a different code path than "Allow once"
- After clicking "Always allow" and restarting the agent, the same action is auto-approved without a prompt
- `onAllowOnce` still approves only for the current session and prompts again on restart

---

**E4-T2 — Implement Authoring Generate endpoint and wire the Generate button `onClick`**
Agent: A (backend), D (frontend) | Effort: Large | Risk: High

*Objective:* The Generate button in `authoring.tsx` has no `onClick`. Add it to call a new `POST /authoring/targets/{name}/generate` endpoint with the user's prompt. The endpoint must use the LLM to generate a real diff of the target `.og` file. Replace Apply and Revert stubs with real filesystem write handlers.

*Files:*
```
frontend/src/routes/authoring.tsx
frontend/src/lib/data/domains/authoring.ts
src/hachi/cli/main.py
src/hachi/gateway/fastapi_app.py
```

*Acceptance Criteria:*
- `POST /authoring/targets/{name}/generate` calls the LLM and returns a real diff
- The diff renders in `DiffView` with actual `+` and `-` lines from the LLM's suggested changes
- `POST /authoring/targets/{name}/apply` writes the patched file to disk and triggers skill engine re-index
- `POST /authoring/targets/{name}/revert` removes the pending patch and restores previous content
- Agent A delivers backend endpoint contract by sprint day 3; Agent D wires frontend by sprint day 5

---

**E4-T3 — Wire kennel browse button to `KennelConsent` overlay and implement `addSkill` flow**
Agent: D | Effort: Medium | Risk: Medium

*Objective:* Wire the "Browse the kennel" button to open the `KennelConsent` overlay. Implement a kennel listing endpoint returning available `.og` skills. Wire the `addSkill()` store action to `POST /skills/add`.

*Files:*
```
frontend/src/routes/skills.tsx
frontend/src/components/overlays/KennelConsent.tsx
frontend/src/lib/store/store.ts
src/hachi/gateway/fastapi_app.py
src/hachi/cli/main.py
```

*Acceptance Criteria:*
- Clicking "Browse the kennel" opens the `KennelConsent` overlay
- The overlay shows a list of at least 3 available skills (can use `bundled_packs.py` content initially)
- Clicking "Add" on a skill calls `addSkill()` and the skill appears in the installed skills list
- The flow works in live mode, not only mock mode

---

**E4-T4 — Fix Sessions Resume toast to actually navigate to conversation with restored context**
Agent: D | Effort: Medium | Risk: Medium

*Objective:* The Resume button in `memory.tsx` calls `showToast('Session resumed — loading conversation')` but does not navigate or restore context. Implement: call `POST /sessions/{id}` to restore, set active session in store, navigate to `/conversation`, show historical messages.

*Files:*
```
frontend/src/routes/memory.tsx
frontend/src/lib/store/store.ts
frontend/src/routes/conversation.tsx
```

*Acceptance Criteria:*
- Clicking Resume navigates to `/conversation` and shows historical messages from that session
- The toast message is accurate: "Resuming session — loading messages..."
- If resume fails, a red error toast is shown instead of the misleading green toast

---

**E4-T5 — Implement `AddMcpRow` dialog for adding MCP servers from the web UI**
Agent: D | Effort: Medium | Risk: Medium

*Objective:* `AddMcpRow` in `integrations.tsx` renders a clickable row with no `onClick`. Add a modal dialog with fields for server name, command, args, and optional env vars. Validate inputs client-side and wire to the gateway MCP registration endpoint.

*Files:*
```
frontend/src/routes/integrations.tsx
frontend/src/lib/store/store.ts
frontend/src/lib/data/domains/integrations.ts
```

*Acceptance Criteria:*
- Clicking "Add MCP server" opens a dialog
- The dialog has name, command (dropdown of allowed commands), and args fields
- The dialog can be closed with Escape and has a focus trap per WCAG 2.1.2
- Submitting a valid form shows the new server in the list

---

**E4-T6 — Add first-run guidance to Home screen and fix broken conversation starter chips**
Agent: D | Effort: Medium | Risk: Low

*Objective:* Home screen shows "Nothing running right now" with no next step for new users. Add a first-run guide panel when `sessions.length === 0`. Filter starter chips to only show ones whose required integration is connected.

*Files:*
```
frontend/src/routes/index.tsx
frontend/src/routes/conversation.tsx
frontend/src/lib/store/store.ts
```

*Acceptance Criteria:*
- A new user with no sessions sees an onboarding guide panel with a "Start your first task" CTA
- Starter chips requiring Gmail are hidden when Gmail is not connected
- An existing user who has run sessions sees the normal session list

---

**E4-T7 — Add exception logging to orchestrator silent `except`-pass blocks**
Agent: A | Effort: Small | Risk: Low

*Objective:* At least 6 bare `except Exception: pass` clauses in `orchestrator.py` silently discard ChromaDB failures, session store persistence failures, and resume errors. Replace each with at minimum a `logger.warning(exc_info=True)` call.

*Files:*
```
src/hachi/gateway/orchestrator.py
```

*Acceptance Criteria:*
- All bare `except Exception: pass` clauses replaced with logged exceptions
- A ChromaDB failure surfaces as a `WARNING` or `ERROR` log entry with traceback
- No behavior changes — errors are still swallowed to maintain partial functionality, but now visible

---

### Epic E5 — Performance and Reliability
**Priority: Medium | Sprint: 3**

Three confirmed high-severity performance issues compound under concurrent sessions and long conversations.

---

**E5-T1 — Replace O(V) BM25 prefix scan with sorted index and bisect**
Agent: E | Effort: Small | Risk: Low

*Objective:* `SkillEngine.search()` iterates the full `_token_index` dict for each query token to find prefix matches — O(V) twice. Replace with a sorted list of tokens and `bisect.bisect_left` for O(log V) prefix matching.

```
src/hachi/og_engine/engine.py
```

*Acceptance Criteria:*
- `search()` returns identical results to the current implementation for the same query
- A benchmark test with a 10k-token vocabulary shows `search()` completes in <1ms for a 3-token query

---

**E5-T2 — Convert `SQLiteJobQueue` to persistent connection**
Agent: E | Effort: Small | Risk: Medium

*Objective:* `SQLiteJobQueue._conn()` opens and closes a new `sqlite3` connection on every method call. With `ResidentWorkerThread` polling at 0.5s, this creates 2+ connections/second continuously. Refactor to hold a persistent connection using a `threading.Lock` for write serialization.

```
src/hachi/jobs/queue.py
```

*Acceptance Criteria:*
- `SQLiteJobQueue` holds a single persistent `sqlite3` connection opened in `__init__`
- All existing job queue tests pass
- A benchmark shows `enqueue + get_pending + update_status` completes in <5ms total

---

**E5-T3 — Move sliding-window compaction LLM call to background job**
Agent: E | Effort: Medium | Risk: Medium | Dependencies: E5-T2

*Objective:* `MemoryManager._sliding_window_compact()` makes a synchronous Anthropic API call inside `prepare_for_turn()`, adding 1–10 seconds of latency to every ~30th message. Move the compaction call to a background job via the jobs queue.

```
src/hachi/core/memory.py
src/hachi/jobs/queue.py
src/hachi/gateway/orchestrator.py
```

*Acceptance Criteria:*
- `prepare_for_turn()` does not make any synchronous LLM API calls
- A test verifies `prepare_for_turn()` returns in <100ms even when compaction is needed (mocked slow LLM sleeping 2s)

---

**E5-T4 — Batch `SessionStore.log_message` commits**
Agent: E | Effort: Small | Risk: Medium

*Objective:* `SessionStore.log_message()` issues a `sqlite3` commit on every call (3 per agent turn). Add a write buffer that accumulates messages and commits in batches of 10 or on a 500ms timer.

```
src/hachi/core/session_store.py
```

*Acceptance Criteria:*
- A session with 30 messages results in no more than 6 commit calls
- `flush_sync()` commits all pending messages before returning

---

**E5-T5 — Guard `MemoryVault.close()` with a threading lock**
Agent: E | Effort: Small | Risk: Medium

*Objective:* `MemoryVault.close()` sets collection references to `None` without coordinating with in-flight tool calls. Add a `threading.RLock` acquired by both `close()` and all collection access paths.

```
src/hachi/core/memory_vault.py
```

*Acceptance Criteria:*
- A test calls `close()` from thread A while `semantic_search()` is running in thread B and verifies no `AttributeError`
- The undocumented `_server.stop()` call is wrapped in `try/except AttributeError` with a log warning

---

### Epic E6 — Product Completeness and UX Polish
**Priority: Medium | Sprint: 3**

The `.og` moat is invisible to users, brand vocabulary is unexplained, sub-agent model override is silently ignored, and medium-level UX bugs erode trust.

---

**E6-T1 — Add `.og` explainer panels to Skills, OG Explorer, and Authoring screens**
Agent: F | Effort: Small | Risk: Low

*Acceptance Criteria:*
- First-time visitors to `/skills`, `/og`, and `/authoring` see a dismissible explainer panel
- The panel includes a one-sentence token-efficiency claim with a "Why?" tooltip
- Dismissal state persists via `localStorage`

---

**E6-T2 — Fix sub-agent model override: pass explicit model to `SubAgentRuntime`**
Agent: A | Effort: Medium | Risk: Medium

*Objective:* `SubAgentRuntime.__init__` uses `llm_client or self._create_client(model)`. When an injected client is always provided from `boot_agent`, the `model` parameter is always discarded. When `delegate_task` is called with an explicit model, create a NEW `AnthropicClient` with that model.

```
src/hachi/core/sub_agent.py
src/hachi/gateway/orchestrator.py
```

*Acceptance Criteria:*
- `delegate_task(role='researcher', task='...', model='claude-opus-4-8')` creates a `SubAgentRuntime` whose LLM client uses `claude-opus-4-8`
- A log entry at INFO level records the model used for each sub-agent
- Without a model parameter, default haiku client is used (behavior unchanged)

---

**E6-T3 — Add `addFolder` and `addCommand` input dialogs**
Agent: D | Effort: Small | Risk: Low

*Acceptance Criteria:*
- Clicking "+" next to Allowed Folders opens an input field for the user to type a path
- Pressing Enter inserts the typed path, not the literal `'~/new-folder'`
- Invalid paths (empty string, whitespace) are rejected with an inline error

---

**E6-T4 — Fix Settings "unsaved changes" indicator to clear after save**
Agent: D | Effort: Small | Risk: Low

*Acceptance Criteria:*
- Changing a safety level and waiting for the gateway `PUT` to succeed clears the unsaved indicator
- If the `PUT` fails, the indicator remains
- Navigating away from Settings clears the indicator

---

**E6-T5 — Add keyboard navigation to slash command menu**
Agent: D | Effort: Small | Risk: Low

*Acceptance Criteria:*
- `ArrowDown`/`ArrowUp` moves selection, `Enter` selects, `Escape` closes the menu
- A Vitest unit test covers the keyboard navigation logic

---

**E6-T6 — Set `TurnContext.surface` to the actual request surface**
Agent: A | Effort: Small | Risk: Low

*Objective:* `TurnContext.surface` is hardcoded to `'cli'` for all gateway sessions. Pass the surface from `SessionCreateRequest.surface` through to `TurnContext` on every turn.

```
src/hachi/gateway/orchestrator.py
src/hachi/core/turn_context.py
```

---

**E6-T7 — Add brand vocabulary first-encounter tooltips to Approvals and Leash screens**
Agent: F | Effort: Trivial | Risk: Low

*Acceptance Criteria:*
- First-time visitors to `/approvals` see a "What is the leash?" info band
- The `LeashPrompt` dialog includes a one-sentence explanation
- Both are dismissible and suppressed after first interaction

---

## Parallel Agent Assignment Plan

| Agent | Specialty | Sprint 1 Tasks | Sprint 2 Tasks | Sprint 3 Tasks |
|---|---|---|---|---|
| **Agent A** | Backend / Orchestrator | E1-T1, E1-T3, E1-T5, E1-T6 | E4-T2 (backend), E4-T7 | E6-T2, E6-T6 |
| **Agent B** | Tests / QA Integrity | E2-T1, E2-T2, E2-T3 | — | — |
| **Agent C** | Security | — | E3-T1, E3-T2, E3-T3, E3-T4, E3-T5 | — |
| **Agent D** | Frontend | E1-T2, E1-T4 | E4-T1, E4-T2 (frontend), E4-T3, E4-T4, E4-T5, E4-T6 | E6-T3, E6-T4, E6-T5 |
| **Agent E** | Performance | — | — | E5-T1, E5-T2, E5-T3, E5-T4, E5-T5 |
| **Agent F** | Product / Docs | — | — | E6-T1, E6-T7 |

**File ownership (no cross-agent conflicts by design):**

| Agent | Owned Paths |
|---|---|
| A | `src/hachi/cli/main.py`, `src/hachi/gateway/orchestrator.py`, `src/hachi/core/sub_agent.py`, `src/hachi/gateway/fastapi_app.py` |
| B | `tests/` directory (no source file edits) |
| C | `src/hachi/core/sandbox.py`, `src/hachi/core/net_guard.py`, `src/hachi/channels/*`, `src/hachi/gateway/control_plane.py`, `pyproject.toml` |
| D | `frontend/src/routes/*`, `frontend/src/lib/store/store.ts`, `frontend/src/components/*` |
| E | `src/hachi/og_engine/engine.py`, `src/hachi/jobs/queue.py`, `src/hachi/core/memory.py`, `src/hachi/core/session_store.py`, `src/hachi/core/memory_vault.py` |
| F | `frontend/src/routes/approvals.tsx` (tooltips only), `frontend/src/components/brand/LeashPrompt.tsx` |

**Sprint 2 coordination note:** E4-T2 (Authoring) requires Agent A to deliver the backend endpoint contract by sprint day 3 and Agent D to wire the frontend by sprint day 5. No other sprint 2 tasks have intra-sprint dependencies.

---

## Sprint Roadmap

### Sprint 1 — Critical Blockers and Test Integrity

All tasks in Sprint 1 are independent and can be parallelized across all agents simultaneously, with one exception: E1-T5 and E2-T2 depend on E1-T1.

| Task | Title | Agent | Effort | Depends On |
|---|---|---|---|---|
| E1-T1 | Fix `create_gateway_app` auth bypass | A | Small | — |
| E1-T2 | Fix division-by-zero crashes in Work, Home, OG | D | Trivial | — |
| E1-T3 | Replace hardcoded model list | A | Small | — |
| E1-T4 | Fix memory recall fixture data shown pre-query | D | Trivial | — |
| E1-T5 | Implement `hachi serve` single-process command | A | Medium | E1-T1 |
| E1-T6 | Fix session resume `--resume`/`--continue` | A | Large | — |
| E2-T1 | Add tool-callable config tests | B | Small | — |
| E2-T2 | Add live-mode gateway integration smoke tests | B | Medium | E1-T1 |
| E2-T3 | Lock config-get/set CLI round-trip | B | Small | — |

### Sprint 2 — Security Hardening and Frontend Functional Completeness

All Sprint 2 tasks within each agent track are parallel. Agent A's E4-T2 backend must precede Agent D's E4-T2 frontend wiring.

| Task | Title | Agent | Effort |
|---|---|---|---|
| E3-T1 | Session ownership enforcement | C | Medium |
| E3-T2 | Python sandbox egress blocklist + production gate | C | Medium |
| E3-T3 | Wrap channel inbound messages in `guard_untrusted` | C | Small |
| E3-T4 | Bump unpatched CVE dependencies | C | Small |
| E3-T5 | Fix DNS rebinding SSRF window in `net_guard` | C | Medium |
| E4-T1 | Wire `onAlwaysAllow` to persistent backend endpoint | D | Medium |
| E4-T2 | Implement Authoring Generate endpoint + wire `onClick` | A/D | Large |
| E4-T3 | Wire kennel browse to `KennelConsent` overlay | D | Medium |
| E4-T4 | Fix Sessions Resume toast to navigate + restore context | D | Medium |
| E4-T5 | Implement `AddMcpRow` dialog | D | Medium |
| E4-T6 | Add first-run guidance + fix starter chips | D | Medium |
| E4-T7 | Add exception logging to orchestrator silent `except`-pass | A | Small |

### Sprint 3 — Performance, Reliability, and UX Polish

All Sprint 3 tasks are parallel except E5-T3 which depends on E5-T2.

| Task | Title | Agent | Effort |
|---|---|---|---|
| E5-T1 | Replace O(V) BM25 prefix scan with bisect | E | Small |
| E5-T2 | Convert `SQLiteJobQueue` to persistent connection | E | Small |
| E5-T3 | Move sliding-window compaction to background job | E | Medium |
| E5-T4 | Batch `SessionStore.log_message` commits | E | Small |
| E5-T5 | Guard `MemoryVault.close()` with threading lock | E | Small |
| E6-T1 | Add `.og` explainer panels to Skills, OG, Authoring | F | Small |
| E6-T2 | Fix sub-agent model override in `delegate_task` | A | Medium |
| E6-T3 | Add `addFolder`/`addCommand` input dialogs | D | Small |
| E6-T4 | Fix Settings unsaved changes indicator | D | Small |
| E6-T5 | Add keyboard navigation to slash command menu | D | Small |
| E6-T6 | Set `TurnContext.surface` to actual request surface | A | Small |
| E6-T7 | Add brand vocabulary first-encounter tooltips | F | Trivial |

### Sprint 4 — Remaining Medium Findings and Missing Features

| Task | Title | Effort |
|---|---|---|
| MED-002 | Fix SSE stream busy-poll 50ms unconditional sleep | Medium |
| MED-006 | Fix `EncryptedFileSecretStore` salt reuse | Medium |
| MED-007 | Fix session-approved commands with no expiry | Small |
| MED-008 | Add `^` and `%` to Windows shell metacharacter blocklist | Small |
| MED-013 | Add focus trap and Escape key to `WorkDetail` slide-over (WCAG 2.1.2) | Small |
| MED-015 | Make operator-mode features discoverable via visible Settings toggle | Trivial |
| MED-019 | Refactor Memory screen into separate Recall and Audit subviews | Medium |
| MED-024 | Add Web Push API browser notification for pending approvals | Medium |
| HIGH-007 | Implement live run streaming `GET /sessions/{id}/run-stream` and wire `/run` route | XL |
| HIGH-013 | Full sub-agent model override architecture — per-delegation client pool | Medium |
| LOW-016 | Fix prompt cache breakpoints to anchor on stable system prompt sections | Medium |
| LOW-020 | Add session selector and title to Conversation screen | Small |
| LOW-025 | Add responsive CSS layout for Approvals, Home, and Conversation screens | Medium |

---

## Verification Strategy

### Sprint 1 Verification

Each task in Sprint 1 must pass its own verification test before the sprint is considered complete. All three check commands must pass on the sprint-end commit:

```bash
uv run pytest -q                          # 0 failures, 0 errors
npm run --prefix frontend typecheck       # 0 errors
uv run ruff check src tests               # 0 violations
```

Additional sprint 1 manual checks:

```bash
# E1-T1: Verify auth bypass is closed
uv run hachi gateway-openapi --output /tmp/schema.json  # must not bind port

# E1-T2: Verify no NaN crashes
# Open /work, /og, / in browser with zero data — DevTools must show 0 console errors

# E1-T5: Verify single-process serve
uv run hachi serve  # browser opens, sends message, receives response, no second terminal

# E1-T6: Verify session resume
hachi run  # send "my name is Alex", exit
hachi --continue  # send "what is my name?" — must reference Alex
```

### Sprint 2 Verification

```bash
# E3-T1: Session isolation
# Integration test: create session with token A, access with token B — assert 403

# E3-T4: CVE remediation
uv run hachi security-audit --json --fail-on-high  # must exit 0

# E4-T2: Authoring live-mode
# Open /authoring, select target, type prompt, click Generate — real diff appears within 10s
# Click Apply — verify .og file on disk changed
# Click Revert — verify .og file restored

# E4-T3: Kennel browse
# Click 'Browse the kennel' — overlay opens with skill list
# Click 'Add' — skill appears in installed list
```

### Sprint 3 Verification

```bash
# E5-T1: BM25 performance
uv run pytest tests/test_og_engine.py -v
uv run pytest tests/test_benchmark.py -v -k 'search'  # <1ms for 3-token query on 10k vocab

# E5-T2: SQLite connection benchmark
uv run pytest tests/test_benchmark.py -v -k 'job_queue'  # <5ms for enqueue+get+update

# E5-T3: Compaction latency
# Test: prepare_for_turn with 35-message window completes in <100ms with mocked slow LLM

# E5-T5: MemoryVault close race
uv run pytest tests/test_memory_vault.py -v -k 'close'
```

### Sprint 4 Verification

```bash
uv run hachi security-audit --json --fail-on-medium  # PR-level check

# HIGH-007: Live run streaming
# Navigate to Work, click 'Watch live' on an active session — real event stream renders, not static card
```

---

## Release Readiness Recommendation

**Current Status: NO-GO**

The following 10 gate criteria must all pass before a conditional-GO milestone can be considered. Each gate maps to one or more sprint tasks.

| Gate | Criterion | Blocking Task |
|---|---|---|
| **GATE-01 AUTH** | `curl http://localhost:8787/sessions` without a bearer token returns `401`. With a valid bearer token returns `200`. Must pass for the default `hachi serve` deployment path. | E1-T1 |
| **GATE-02 NO-CRASH** | Navigating to `/work`, `/og`, and `/` in a brand-new account with zero sessions, zero spend, and `budget=0` produces zero JavaScript console errors and no `NaN` values in any CSS property. | E1-T2 |
| **GATE-03 SESSION-ISOLATION** | Creating session A with token-A and accessing `GET /sessions/{A}` with token-B returns `403`. Verified by the integration test added in E2-T2. | E3-T1 |
| **GATE-04 AUTHORING-LIVE** | `POST /authoring/targets/{name}/generate` with a valid prompt returns a non-empty diff. `POST /authoring/targets/{name}/apply` writes the patched content to the file on disk. | E4-T2 |
| **GATE-05 KENNEL-LIVE** | Clicking "Browse the kennel" opens the `KennelConsent` overlay with at least one installable skill. Clicking Add installs the skill and it appears in the skills list without a page reload. | E4-T3 |
| **GATE-06 ONBOARDING** | A developer with `uv` and Python 3.11+ installed can run `uv sync && uv run hachi serve` and have a functional chat session in one terminal within 5 minutes, measured from a fresh clone. | E1-T5 |
| **GATE-07 SAFETY-ROUND-TRIP** | Selecting "Guarded" in Settings, saving, and reloading shows "Guarded" still selected. `GET /config` returns `permissions.tier='sandbox'`. Verified end-to-end in a live browser session. | CRIT-004 fix |
| **GATE-08 ALWAYS-ALLOW-PERSIST** | Clicking "Always allow" on a permission prompt, restarting the gateway, and triggering the same action does not show a permission prompt. | E4-T1 |
| **GATE-09 SECURITY-AUDIT** | `uv run hachi security-audit --json --fail-on-high` exits `0`. No HIGH or CRITICAL findings for GHSA-537c-gmf6-5ccf, CVE-2026-54282, CVE-2026-54283, or CVE-2026-45829. | E3-T4 |
| **GATE-10 TEST-SUITE** | `uv run pytest -q` exits `0` with `0` failures. `npm run --prefix frontend typecheck` exits `0`. `uv run ruff check src tests` exits `0`. All three must pass on the release commit without any `--ignore` or skip flags. | E2-T1, E2-T2, E2-T3 |

### What is working and must be preserved

The following are genuine product strengths that should not be regressed during remediation:

- The Approvals/leash screen is well-executed: risk labeling, countdown timers, file chip "what this touches", and the "Runs without a leash check" section are all strong trust signals
- The Settings screen's three-tier safety selector with plain-language descriptions is the right UX pattern for the permission model
- The Work screen's spend analytics (token burn by turn, tidy-ups, model donut) are industry-leading transparency features with no competitor equivalent
- The conversation composer footer ("Hachi runs on your machine · it asks before anything risky · you can stop anytime") is the best single-line trust signal in the product
- The `.og` ObjectGraph three-pass architecture is technically sound and the competitive moat is real — it is not surfaced, but it is not fictitious
- `TurnContext` immutability and `PermissionGate` enforcement at call time are strong foundational architectural decisions that reduce the blast radius of future regressions
- The `guard_untrusted` framing for web and MCP content is well-named and well-placed; the gap is that inbound channel messages bypass it

### Estimated remediation timeline

Assuming 6 agents working in parallel across 4 sprints:

| Sprint | Duration | Exit Criteria |
|---|---|---|
| Sprint 1 | 1 week | All 8 critical blockers addressed; test integrity restored; GATE-01, GATE-02, GATE-06, GATE-10 pass |
| Sprint 2 | 1–2 weeks | Session isolation, sandbox gated, CVEs patched, authoring live, kennel functional; GATE-03–GATE-09 pass |
| Sprint 3 | 1 week | Performance hot paths fixed; UX polish; all GATE-01–GATE-10 hold under concurrent load |
| Sprint 4 | 1–2 weeks | Remaining medium findings; live run streaming; mobile layout; conditional-GO for limited release |

**Conditional-GO threshold:** All 10 release gates pass + `uv run pytest -q` green + `security-audit --fail-on-high` clean. This is achievable within 4–6 weeks of focused remediation with the parallel agent plan above.
