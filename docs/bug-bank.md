# Bug Bank — Hachi's regression memory

This file is the **memory layer that survives across sessions**. It is the direct
cure for *"a bug I already fixed came back"* and *"I fixed the same bug three times."*

Every non-trivial bug or hard-won, non-obvious constraint gets a short **autopsy**
here: the symptom, the root cause, the fix, and what now *locks* it (a regression
test or a mechanical guard). When an agent starts a session, the `SessionStart`
hook (`.claude/hooks/session_start.py`) injects the entries below into context, so
the warnings are loaded *before* planning — never relearned the hard way.

> **The regression law (CLAUDE.md):** for every bug you fix, first write a test
> that *fails* because of the bug, then fix the code, then confirm it goes green.
> That test is permanent. Add the autopsy here and link the test under **Locked by**.

## How to add an entry

Copy the template, give it the next `BB-N` number, keep it tight:

```markdown
### BB-N — Short title
- **Symptom:** what you observe (the failure, not the cause).
- **Root cause:** the actual mechanism.
- **Avoid:** the one-line rule a future agent must follow. ← the SessionStart hook surfaces this.
- **Locked by:** the regression test path, or the mechanical guard. "None yet — add when next touched" is an honest answer.
```

Keep **Avoid:** to a single imperative line: it is what gets injected at session start.

---

## Entries

### BB-1 — Windows pytest/mypy `.exe` shim fails with OS error 4551
- **Symptom:** `uv run pytest` or `uv run mypy` aborts on Windows with `OSError 4551`
  (editable-install lock) before collecting anything.
- **Root cause:** the generated `.exe` console-script shim contends with the
  editable-install lock on Windows.
- **Avoid:** on Windows, fall back to `python -m pytest` / `python -m mypy`, or
  `PYTHONPATH=src uv run --with pytest --no-project pytest -q`. `scripts/preflight.py`
  invokes tools through `uv run`, which usually sidesteps the shim.
- **Locked by:** mechanical — documented fallback; no test (environment-specific).

### BB-2 — `.env` autoload belongs at the CLI edge, never in the core import path
- **Symptom:** adding a top-level `.env` autoload to core/library modules turned the suite
  red across unrelated tests; credentials "sometimes" resolve depending on import/test order.
- **Root cause:** loading `.env` during a *library* import leaks ambient env into hermetic
  tests and couples units to global state. A real attempt to do this caused a cascade and was
  reverted.
- **Avoid:** keep `.env` loading at the **CLI entry edge only** —
  `cli/main.py::_load_dotenv_into_environ()` runs at CLI import but is gated by
  `HACHI_DISABLE_DOTENV_AUTOLOAD=1` (conftest sets it). Core code (`core/oauth.py`,
  `core/llm.py`, `tools/google_tools.py`) resolves credentials **lazily at call time** — never
  add a library/core import-time autoload.
- **Locked by:** `tests/test_dotenv_hermeticity.py` (the guard works + conftest defaults the
  flag to `"1"`); lazy call-time readers in core. Related: BB-8.

### BB-3 — `patch.dict(sys.modules)` over `chromadb` poisons later tests
- **Symptom:** a test that patches `sys.modules["chromadb"]` passes, then a *later*
  test dies with `cannot load module more than once` (PyO3 native extension).
- **Root cause:** `patch.dict(sys.modules)` wipes chromadb's compiled PyO3 extension;
  it cannot be re-imported in the same process. A "restore" fixture made it worse by
  forcing re-import.
- **Avoid:** never `patch.dict(sys.modules)` over `chromadb`. `conftest` **warms
  chromadb at import** so the native module loads exactly once — leave that warm-up in.
- **Locked by:** `tests/conftest.py` chromadb warm-up (deterministic suite, ~8× faster).

### BB-4 — OpenAPI ↔ frontend contract drift fails CI
- **Symptom:** the `frontend-contract` CI job fails on a `schema.d.ts` diff after a
  gateway route change.
- **Root cause:** the generated TypeScript transport contract
  (`frontend/src/lib/data/schema.d.ts`) is derived from `openapi/hachi-openapi.json`;
  changing a route without regenerating both leaves them out of sync.
- **Avoid:** after **any** gateway route change run
  `uv run hachi gateway-openapi --output openapi/hachi-openapi.json` **and**
  `npm run --prefix frontend generate:api`. `scripts/preflight.py` checks this.
- **Locked by:** CI `frontend-contract` job; `npm run --prefix frontend check:api`.

### BB-5 — `paths.workspace_dir` does not exist
- **Symptom:** `AttributeError` reaching for `paths.workspace_dir`.
- **Root cause:** the path accessor was never named that.
- **Avoid:** use `paths.skills_dir` (and the other `get_hachi_paths()` accessors:
  `config_dir`, `data_dir`, `state_dir`, `cache_dir`).
- **Locked by:** `src/hachi/core/paths.py` (covered by the strict mypy gate).

### BB-6 — `ProcessSharedServices` is `None` before a session boots
- **Symptom:** `AttributeError`/`NoneType` when touching shared services outside a
  live session (e.g. in a CLI path or a test that didn't boot one).
- **Root cause:** `ProcessSharedServices` is lazy — it does not exist until a session
  boots.
- **Avoid:** never assume shared services exist outside a live session; guard for
  `None` or boot a session first.
- **Locked by:** None yet — add a regression test when next touched.

### BB-7 — `hachi` ↔ `tokoboros` directory junction (double collection)
- **Symptom:** the suite appears to collect twice / a sibling `tokoboros` path shows
  the same files; `git core.hooksPath` may point into the junction.
- **Root cause:** `D:\OpenGigantic\hachi` and a sibling `tokoboros` path are the same
  files via a Windows directory junction.
- **Avoid:** rely on pytest `norecursedirs` (skips `tokoboros`) so the suite isn't
  collected twice. Don't "fix" paths by chasing the junction.
- **Locked by:** `pyproject.toml` `[tool.pytest.ini_options] norecursedirs`.

### BB-8 — OAuth client id read as an import-time global never saw `.env`
- **Symptom:** Google Workspace config looked correct but auth never picked up
  `GOOGLE_CLIENT_ID`; spend/integration silently inert.
- **Root cause:** the id was read into a module-level global at import, before any
  `.env` was loaded (and there is no global autoload — see BB-2).
- **Avoid:** read integration credentials **lazily at call time**, loading
  `config_dir/.env` inside the call — not as a top-level constant.
- **Locked by:** regression test for lazy OAuth credential resolution (added with the fix).

### BB-9 — OpenAI-compatible streaming reported `$0` cost → daily spend cap inert
- **Symptom:** per-turn cost/tokens were `$0` across many real turns on OpenAI-compatible
  providers; the daily spend cap never triggered.
- **Root cause:** `OpenAICompatibleClient.generate_stream` omitted
  `stream_options.include_usage`, so the provider streamed no usage and cost tracking
  saw nothing — the spend cap had no data to act on.
- **Avoid:** keep the default-on `stream_usage` flag; OpenAI-compatible streaming must
  request `include_usage` or the cost/limit machinery goes silently dark.
- **Locked by:** regression test asserting usage is requested + cost > 0 on a streamed
  OpenAI-compat turn (added with the fix).

### BB-10 — Web-UI OAuth token invisible to agent (key prefix mismatch + wrong CLI command)
- **Symptom:** agent always treats the user as unauthenticated with Google even after they
  authenticated through the web UI; every Google tool call raises "Google needs a one-time
  browser sign-in… Run `hachi auth google` once" — but `hachi auth google` does not exist.
- **Root cause:** two separate code paths share the same `SecretStore` but use different key
  prefixes: the web UI (`integrations/oauth_flow.py`) writes under `"oauth2.<provider>"`;
  `OAuthManager.get_token()` only checked `"oauth.<provider>"` (via `_key()`), so it always
  returned `None`. The error message then named the non-existent `hachi auth google` command
  instead of the real `hachi google-auth` command.
- **Avoid:** when two writers share a `SecretStore`, always read both key prefixes or unify
  the write path; always verify CLI command names exist in `cli/main.py` before embedding
  them in user-facing error strings.
- **Locked by:** `tests/test_oauth.py::test_get_token_finds_web_ui_oauth2_prefix` and
  `tests/test_oauth.py::test_noninteractive_error_mentions_correct_cli_command`.

### BB-11 — Connecting Google via web UI never enables the agent capability
- **Symptom:** Integrations page shows Google as "connected" (green dot), but every Gmail
  query reports "Gmail is currently disabled." Re-connecting doesn't help; restarting the
  gateway doesn't help.
- **Root cause:** Two disconnected systems: `oauth_flow.py` stores the OAuth token
  (`oauth2.google`) but never touches `capabilities.google.enabled` in config, which
  defaults to `False`. `_build_capability_registry` reads that flag at startup — if it's
  `False`, Google tools are not registered and the agent has no Gmail capability regardless
  of the stored token.
- **Avoid:** after any OAuth connect/disconnect, always flip `capabilities.<id>.enabled`
  accordingly via `control_plane.set_capability_enabled`. At startup, fall back to the
  token store: if a token exists and the config flag is `False`, treat it as enabled.
- **Locked by:** `tests/test_oauth_integrations.py::TestOAuthAutoEnablesCapability`
  (`test_callback_success_calls_set_capability_enabled`,
  `test_callback_failure_does_not_enable_capability`).

### BB-12 — Idempotency key reuse across page reloads returns stale response instantly
- **Symptom:** a new query instantly returns the previous turn's exact response verbatim;
  the response appears before the LLM has had time to process the new prompt.
- **Root cause:** `store.ts`'s module-level `_id` counter resets to 100 on every page
  load. Since the gateway session UUID is preserved across refreshes via `sessionStorage`,
  the idempotency key `turn-102` from a prior page load collides with the new turn's key
  and the backend replays the cached old response.
- **Avoid:** include a per-page-load nonce (`_PAGE_NONCE` in `chat.ts`, generated once at
  module load via `crypto.randomUUID()`) in every turn's idempotency key. Never use a
  predictable counter alone as the idempotency key when the session may outlive the page.
- **Locked by:** `frontend/src/lib/data/domains/chat.test.ts` — *buildTurnKey produces
  different keys for the same hachiMsgId with different page-load nonces*.

### BB-9 — Secret keys blocked with no escape hatch; `secret:` refs not resolved at MCP connect time
- **Symptom:** POST `/runtime/mcp/register` with an env var like `API_KEY` always 422s
  even when the value is a `secret:mykey` reference. Conversely, servers whose TOML env
  contains raw `secret:` strings boot with the literal string instead of the resolved value.
- **Root cause:** the secret-pattern guard checked key names unconditionally; `setup_from_config`
  passed `env`/`headers` straight to the transport without resolving `secret:` refs.
- **Avoid:** env/header keys that look like secrets may carry a `secret:<name>` reference value —
  the guard must allow these and verify `cred:<name>` exists. `setup_from_config` must call
  `_resolve_secret_refs` before building the transport. System keys (non-`cred:` namespace) are
  never listed or deleted via the credential manager API.
- **Locked by:** `tests/test_mcp_registration.py` (`TestResolveSecretRefs`,
  `TestMcpSetupFromConfigSecretRefs`); `tests/test_secret_store.py::TestSecretStoreGatewayAPI`.

### BB-10 — TOML-only MCP servers invisible until a live session exists
- **Symptom:** after `POST /runtime/mcp/register`, GET `/runtime/mcp/inventory` returns an
  empty list if no conversation has been started yet. Users think registration failed.
- **Root cause:** `mcp_inventory()` in `GatewayControlPlane` only enriched servers reported by
  the live agent; TOML-persisted servers that haven't been connected in this session were dropped.
- **Avoid:** after enriching live-agent servers, merge TOML `[mcp_servers]` entries that aren't
  in the live set, reporting them as `status: "pending_restart"`.
- **Locked by:** `tests/test_mcp_registration.py::TestMcpInventoryTomlOnly`.

### BB-11 — Search provider is a boot-time registry decision; Tavily key stored live has no effect
- **Symptom:** user saves their Tavily API key via the Integrations UI; the current conversation
  still reports "web search unavailable" and no `web_search` tool appears.
- **Root cause:** `ProviderRegistry` is built once at `boot_agent`; storing a credential later
  doesn't rebuild it, so the Tavily provider is never discovered.
- **Avoid:** after storing Tavily credentials, call `reload_search_provider()` on the live
  `AgentSession` — it rebuilds the registry, re-resolves the provider, and hot-patches the
  tool registry (idempotent: updates handlers without duplicating schemas).
- **Locked by:** `tests/test_search_platform.py::TestTavilyLiveReload`.

> ⚠️ Numbering note: the three entries above (secret refs / TOML-only MCP / search reload)
> were added in a prior session that restarted the count at BB-9, colliding with the
> pre-existing BB-9/10/11. They should eventually be renumbered to BB-9b/10b/11b (or
> 13/14/15) — left as-is here to avoid touching that session's many code comments. The
> entries below continue the sequence from the real BB-12.

### BB-13 — Conversation dropdown empty on boot — `recent_sessions` short-circuited with no live agent
- **Symptom:** at server start the "New conversation" dropdown shows "No recent sessions"
  even though `sessions.db` holds prior conversations; they only appear after the first turn.
- **Root cause:** `_InProcessGatewayRuntime.recent_sessions` returned `[]` whenever `_agents`
  was empty (no agent had booted yet), so it never read the persisted `SessionStore`. The same
  `agent is None: return []` shape exists in `get_skills`/`get_memory`/etc.
- **Avoid:** gateway read-only history endpoints must fall back to the durable store when no
  agent is live — don't gate persisted data behind an in-memory agent. Open a `SessionStore`
  on `get_hachi_paths().sessions_db` and `shutdown()` it after the read.
- **Locked by:** `tests/test_web_gui_diag.py::TestRecentSessionsNoAgent`.

### BB-14 — Tavily key saved via UI invisible to provider + Test Connection (key-name mismatch)
- **Symptom:** after saving a Tavily API key in Integrations, "Test Connection" reports
  "No Tavily API key stored" and web search stays unavailable.
- **Root cause:** the credential dialog writes the namespaced key
  `apikey.tavily.tavily_api_key` (`oauth_flow.store_apikey_credential`), but
  `TavilySearchProvider._resolve_api_key` and the Test Connection `_resolve_secret` only read
  the bare `tavily_api_key`. `ProviderRegistry.from_config` already checked BOTH keys for
  *detection*, so the provider registered but then failed to find the key at call time.
- **Avoid:** readers of a connector credential must resolve the SAME key the writer used —
  check both `apikey.<connector>.<field>` and the bare name (mirror the registry's list).
- **Locked by:** `tests/test_web_gui_diag.py::TestTavilyKeyResolution`,
  `::TestTavilyTestConnectionEndpoint`.

### BB-15 — Permission tier silently reverts to Standard when the external `/models` call fails
- **Symptom:** setting the safety tier to "Open" reverts to "Standard" (on reload / intermittently),
  even though `settings.toml` correctly persists `tier = "unrestricted"`.
- **Root cause:** `loadConfig` fetched `/config` (12 ms, the safety source of truth) and
  `/models` (a LIVE external provider round-trip, ~1.6 s, flaky) in one `Promise.all`. Any
  `/models` rejection dropped into the catch, which reset `safety: "standard"`.
- **Avoid:** never couple a local source-of-truth read to a flaky external call in one
  all-or-nothing fetch. Fetch `/config` on its own; treat `/models` as a non-critical dropdown
  list whose failure can only degrade the picker, never config-derived state.
- **Locked by:** `frontend/src/lib/data/domains/config.test.ts`
  ("keeps the /config tier when /models REJECTS").

### BB-16 — MCP server stuck showing "starting" — unmapped `pending_restart` status
- **Symptom:** a TOML-persisted MCP server (e.g. a legacy "demo") shows "starting" forever
  even when no session is live and it is not actually booting.
- **Root cause:** the backend reports `pending_restart` (registered, not connected this session),
  but `adaptMcpServer.statusMap` and `mcpMeta` had no entry for it and fell back to `starting`.
- **Avoid:** keep the frontend status vocabulary in sync with the backend — `pending_restart`
  and `stopped` map to a distinct `pending` ("needs restart") state, not the `starting` fallback.
- **Locked by:** `frontend/src/lib/data/domains/integrations.test.ts`
  ("adaptMcpServer — real mapping (BB-16)").

### BB-17 — MCP servers table columns misaligned with their headers
- **Symptom:** in the MCP servers table, cell values (status/tools/transport/uptime/toggle)
  don't line up under their column headers.
- **Root cause:** the header reserved fixed widths (Enabled 40, spacer 32) but the row's toggle
  cell had no width (`flexShrink:0` only) and the delete button was content-sized, so every
  cell after Uptime drifted by the cumulative `gap`.
- **Avoid:** flex "table" rows must give every cell the SAME fixed width as its header column;
  a width-less cell collapses to content width and shifts everything after it.
- **Locked by:** visual verification (no clean unit seam for CSS layout) — toggle cell pinned to
  `width:40` (= Switch width = header), delete button to a `width:32` slot (= header spacer).

### BB-18 — Registering an MCP server with an API key was impossible from the UI
- **Symptom:** registering e.g. context7 with `CONTEXT7_API_KEY` returned HTTP 422
  ("looks like a secret; store the value via POST /runtime/secrets …"); the dialog also
  hard-blocked secret-looking stdio env keys client-side.
- **Root cause:** the backend (correctly) refuses raw secret-looking env/header values and wants
  them stored via `/runtime/secrets` then referenced as `secret:<name>`, but the dialog had no
  support for that two-step flow.
- **Avoid:** when a backend requires store-then-reference for secrets, drive it transparently —
  detect secret-looking env/header keys, `POST /runtime/secrets` first, then send `secret:<name>`
  in the register payload. The raw value never reaches `settings.toml`.
- **Locked by:** `frontend/src/lib/data/domains/integrations.test.ts`
  ("extractMcpSecrets — transparent secret handling").

<!--
When you fix a bug: add BB-N here with a one-line **Avoid:**, link the locking test,
and (if the constraint is subtle) drop a comment next to the code itself — the agent
gets far better attention on a comment beside the function than on a doc three
directories away.
-->
