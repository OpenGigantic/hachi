# Bug Bank — Hachi's regression memory

This file is the **memory layer that survives across sessions**. It is the direct
cure for _"a bug I already fixed came back"_ and _"I fixed the same bug three times."_

Every non-trivial bug or hard-won, non-obvious constraint gets a short **autopsy**
here: the symptom, the root cause, the fix, and what now _locks_ it (a regression
test or a mechanical guard). When an agent starts a session, the `SessionStart`
hook (`.claude/hooks/session_start.py`) injects the entries below into context, so
the warnings are loaded _before_ planning — never relearned the hard way.

> **The regression law (CLAUDE.md):** for every bug you fix, first write a test
> that _fails_ because of the bug, then fix the code, then confirm it goes green.
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
- **Avoid:** on Windows, fall back to `uv run python -m pytest` / `uv run python -m mypy`
  (NOT bare `python -m …` — on this dev machine bare `python` resolves to a global
  Python-Install-Manager shim under `AppData/Local/Python/` that lacks `hachi`/`pytest-xdist`
  and silently runs the wrong environment; see BB-143), or
  `PYTHONPATH=src uv run --with pytest --no-project pytest -q`. `scripts/preflight.py`
  invokes tools through `uv run`, which usually sidesteps the shim.
- **Locked by:** mechanical — documented fallback; no test (environment-specific).

### BB-2 — `.env` autoload belongs at the CLI edge, never in the core import path

- **Symptom:** adding a top-level `.env` autoload to core/library modules turned the suite
  red across unrelated tests; credentials "sometimes" resolve depending on import/test order.
- **Root cause:** loading `.env` during a _library_ import leaks ambient env into hermetic
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

- **Symptom:** a test that patches `sys.modules["chromadb"]` passes, then a _later_
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
- **Locked by:** `frontend/src/lib/data/domains/chat.test.ts` — _buildTurnKey produces
  different keys for the same hachiMsgId with different page-load nonces_.

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
  _detection_, so the provider registered but then failed to find the key at call time.
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

### BB-19 — Re-registering an MCP server clobbered its persisted `enabled` flag

- **Symptom:** an `[mcp_servers.NAME]` entry that had `transport`, `url`, **and**
  `enabled = true` came back with only `transport` + `url` after the server was
  registered again (e.g. the user edited its URL in the UI). The lifecycle flag was
  silently dropped — looked like data loss on the entry.
- **Root cause:** `_InProcessGatewayRuntime.register_mcp_server` persisted with
  `mcp_section[name] = cfg.to_toml_entry()`, which **replaces the whole sub-table**
  from the in-memory `MCPServerConfig`. That dataclass has no `enabled` field (it's
  written separately by `set_mcp_enabled`), so `to_toml_entry()` never emits it and
  the re-register wiped it. (Siblings were already safe — register/delete only ever
  touched the targeted key — but the _targeted_ entry lost any field the config
  doesn't model.)
- **Avoid:** when persisting a server, edit ONLY the keys the config owns
  (`MCPServerConfig.TOML_MANAGED_KEYS`) — overwrite those, prune managed keys that no
  longer apply (e.g. a stdio `command` after switching to http), and leave every other
  key (notably `enabled`) untouched. Never rebuild a `[mcp_servers.NAME]` table from an
  in-memory config that doesn't carry the full key set.
- **Locked by:** `tests/test_mcp_registration.py::TestMcpRegisterPreservesEntryFields`
  (`test_reregister_preserves_enabled`,
  `test_reregister_transport_switch_prunes_stale_keys_keeps_enabled`,
  `test_register_then_delete_leaves_sibling_byte_for_byte`,
  `test_toml_managed_keys_cover_to_toml_entry`).

### BB-20 — Default OpenAI model was `gpt-4.1-mini` (not in catalog)

- **Symptom:** cost tracking returned `$0` for sessions using the default model; model-picker UI showed no entry highlighted.
- **Root cause:** `config_loader.py:217` and `cli/main.py:3798` (tier-plan builder) both hardcoded `"gpt-4.1-mini"` as the fallback, but that id was absent from `BUNDLED_MODEL_DEFAULTS`. `ModelRegistry.get("gpt-4.1-mini")` returned `None`.
- **Avoid:** whenever changing the default model, verify `ModelRegistry.build(...).get(model_id) is not None`. The locking test asserts this.
- **Locked by:** `tests/test_config_loader.py::test_openai_default_model_is_catalog_member`.

### BB-21 — Messages table had no `model` column; replayed sessions showed wrong model label

- **Symptom:** reloading a past session showed the current provider model on all past messages instead of the model used when the message was generated.
- **Root cause:** `messages` table had no `model` column; `log_message()` never accepted one; the synthesised `session.chat.completed` event from `control_plane._synthesize_history_events` emitted `payload: {}`, not `payload: {"model": ...}`. Frontend reads `payload.model` on this event to update `lastUsedModel`.
- **Avoid:** when adding a new per-turn metadata field, touch all four layers: DB schema + migration, `log_message()` signature, `get_session_messages()` SELECT, and the history-replay event synthesis.
- **Locked by:** `tests/test_session_store_model_column.py` (4 tests).

### BB-22 — New OpenAI models (gpt-4.1, o3-mini, o4-mini) missing from catalog; o3/o3-mini prefix collision risk

- **Symptom:** configuring `gpt-4.1-mini`, `o3-mini`, or `o4-mini` as the provider model gave `$0` cost tracking and no entry in the model picker.
- **Root cause:** `BUNDLED_MODEL_DEFAULTS` only had `gpt-4o` and `gpt-4o-mini`; `MODEL_PRICING` had no entries for the GPT-4.1 family or o-series. Without an explicit `"o3-mini"` entry, `pricing_for` would prefix-match `"o3"` and use o3's (10× higher) price.
- **Avoid:** when adding an o-series model, always add BOTH the base id AND any cheaper variants (e.g. `o3` AND `o3-mini`) as explicit keys in `MODEL_PRICING` so longer-prefix-wins resolves them independently. Extend `TestCurrentOpenAIModels` when adding future models.
- **Locked by:** `tests/test_routing_registry.py::TestCurrentOpenAIModels` (5 tests).

### BB-24 — Episodic recall on turns 2+ uses the previous turn's query (B10 staleness)

- **Symptom:** on turn N+1, recalled episode summaries are matched to turn N's question instead of
  the current one. In most conversations this is invisible, but a sharp topic pivot can surface
  an episodic hit that feels irrelevant to the new question.
- **Root cause:** `_run_prefetch_job` is enqueued at the end of turn N with turn N's query; the
  result is consumed at the start of turn N+1. Episodic recall was moved into this prefetch job
  (B10, `perf/streaming-optimizations`) to avoid blocking the inline turn path on turns 2+.
  Turn 1 is always exact (inline fallback runs both semantic and episodic synchronously).
- **Avoid:** do NOT add a second inline episodic call on every turn to "fix" the lag —
  that defeats the entire purpose of B10 and re-introduces the latency regression.
  Only add an inline fallback if the turn N vs N+1 query similarity drops below a threshold
  (defer until a real complaint surfaces). Document the tradeoff in docs/DECISIONS.md §B10.
- **Locked by:** `tests/test_b10_episodic_recall_offload.py` (parity tests confirm turn-1 inline
  path still calls `search_episodic` and that dedup works identically in both paths).

### BB-23 — MCP servers that failed before transport setup were invisible in the operator inventory

- **Symptom:** an MCP server with an invalid TOML config or gate-denied by the permission tier emitted `MCP_SERVER_FAILED` at boot but never appeared in `GET /runtime/mcp/inventory`, making it impossible to diagnose why a server wasn't available.
- **Root cause:** `MCPManager.get_inventory()` only iterated `_servers`, which is populated only after a successful transport setup. Servers that failed in `MCPServerConfig.from_toml_entry()` or were denied by `gate.is_mcp_server_allowed()` never entered `_servers`.
- **Avoid:** call `mcp_manager.register_boot_failure(name, reason)` at every early-exit failure path in `_configure_mcp_servers` before the `mcp_manager.setup_from_config()` call. `get_inventory()` merges `_boot_failures` into the result.
- **Locked by:** `tests/test_mcp_registration.py::TestMcpBootFailureInventory` (3 tests).

### BB-24 — Tavily connector showed "disconnected" when package not installed (no actionable UI path)

- **Symptom:** users without `tavily-python` installed saw a dead-end API-key form ("Enter your Tavily API key") when the package wasn't installed — entering a key silently failed because the import guard prevented the provider from loading.
- **Root cause:** `connector_status("tavily")` returned `"disconnected"` regardless of whether the Python package was installed, conflating two distinct states.
- **Avoid:** for connectors backed by optional Python packages, check `importlib.util.find_spec(package_name)` first. If `None`, return `status="unavailable"` and `package_installed=False` so the UI can show install guidance instead of a key-entry form.
- **Locked by:** `tests/test_tavily_status_tristate.py::TestTavilyStatusTristate` (4 tests).

### BB-25 — `update_permissions(tier)` wrote settings.toml but never regenerated device-permissions.og

- **Symptom:** changing the permission tier via the Settings UI appeared to save, but `PermissionGate` (which reads `device-permissions.og`) remained at the old tier on the next boot and for any new session started after the change.
- **Root cause:** `_InProcessGatewayRuntime.update_permissions()` called `write_global_config(config)` which persists to `settings.toml`, but `PermissionGate` reads `device-permissions.og` — a separate file that was never regenerated. The gap was invisible until a fresh boot, because the in-process gate object (already built) wasn't rebuilt either.
- **Avoid:** after any tier change, atomically regenerate `device-permissions.og` using `generate_permissions_og(tier, custom_paths)`, preserving existing `allowed_paths`. Also add a boot-reconcile step in `boot_agent` after `load_config()`: if `config.permissions.tier != gate.tier`, rebuild gate + approver.
- **Locked by:** `tests/test_permissions_update.py::TestUpdatePermissionsRegeneratesOg` (3 tests).

### BB-26 — `SessionExpiredError` (HTTP 410) left user to re-type their message

- **Symptom:** after a gateway restart, the next `POST /sessions/{id}/messages` returned 410 with `"session_expired"` and the UI showed a toast asking the user to resend manually — the in-flight prompt was lost.
- **Root cause:** the frontend `sendMessage` catch handler recognised `session_expired` but only cleared the stale session ID and showed a toast. It did not mint a new session or replay the prompt.
- **Avoid:** on `session_expired` (410), immediately call `ensureSession()` to mint a fresh session and replay the original `POST` with a new idempotency key. Use a distinct `_r`-suffixed nonce so the server does not de-duplicate it against the dead-session key. The daily spend cap is enforced server-side (durable ledger) and still applies on retry — do not add client-side spend bypass.
- **Locked by:** `frontend/src/lib/data/domains/chat.ts` auto-retry in `.catch(async …)` block. No dedicated vitest yet (SSE stream integration is hard to mock end-to-end).

### BB-27 — Opus-4 family mispriced 3× (legacy Claude-3-Opus rate)

- **Symptom:** the web UI / `/spend` showed ~3× the true cost for any turn that ran
  on an Opus-4 model; spend felt inexplicably high.
- **Root cause:** `core/cost.py` `MODEL_PRICING` **and** `core/routing/descriptor.py`
  `BUNDLED_MODEL_DEFAULTS` both priced `claude-opus-4` and `claude-opus-4-8` at
  `(15.0, 75.0)` — the legacy Claude-3-Opus rate. Current Opus (4.5+) is `$5/$25` per
  MTok, so every Opus turn's estimated cost was inflated 3×.
- **Avoid:** price current Opus at `(5.0, 25.0)`; keep the two pricing tables in sync
  (the parity guard in `tests/test_routing_registry.py::TestPricingParity` enforces it).
  When adding a model variant, verify against Anthropic's published per-MTok pricing —
  never copy an older entry's numbers.
- **Locked by:** `tests/test_cost_tracking.py::test_opus_4_family_priced_at_current_rate_not_legacy`
  and `::test_pricing_longest_prefix_match`; `tests/test_routing_registry.py::TestPricingParity`.

### BB-28 — OpenAI cached input billed at full price + Anthropic discount assumed for all

- **Symptom:** OpenAI/openai-compatible cache savings were invisible, and displayed
  cost *overstated* OpenAI spend — cached input tokens were billed at the full input
  rate, then (where any cache tokens existed) discounted with Anthropic's 0.1× rate.
- **Root cause:** two gaps. (1) `OpenAICompatibleClient` parsed only `prompt_tokens` /
  `completion_tokens` and ignored `prompt_tokens_details.cached_tokens`. (2)
  `CostTracker.record` hard-coded `_CACHE_READ_MULTIPLIER = 0.1` instead of the
  per-model `Pricing.cache_read_mult` — so even captured cache reads were priced with
  Anthropic economics for every provider.
- **Avoid:** parse `cached_tokens` into `cache_read_tokens` (split it out of
  `prompt_tokens`, both streaming and non-streaming); price cache reads with the
  resolved model's `cache_read_mult` (OpenAI 0.5×, Anthropic 0.1×). Registry-less
  trackers fall back to the module constants unchanged.
- **Locked by:** `tests/test_cost_tracking.py::test_cost_tracker_uses_per_model_cache_multiplier_from_registry`;
  `tests/test_streaming.py::test_openai_stream_splits_cached_prompt_tokens`,
  `::test_openai_generate_splits_cached_prompt_tokens`.

### BB-29 — OpenAI auto-cache defeated by the time header inside the system message

- **Symptom:** OpenAI's automatic prompt caching never engaged across turns; every
  turn re-billed the full ~6K system+tools prefix at fresh-input price.
- **Root cause:** `OpenAICompatibleClient._build_request` appended the volatile dynamic
  block (time header / recall) to the END of the system message, so the cached prefix
  changed every turn. Prompt caching is a prefix match — one byte change invalidates it.
- **Avoid:** keep the system message byte-stable across turns; place the dynamic block as
  its own trailing message AFTER the conversation so the stable system+tools prefix stays
  cacheable. (Mirrors the Anthropic split, where the dynamic tail sits after the cache
  breakpoint.)
- **Locked by:** `tests/test_streaming.py::test_openai_request_keeps_time_header_out_of_system_message`.

### BB-30 — Every chat turn silently routed to the priciest model

- **Symptom:** cost per trivial turn was inexplicably high; the model that actually
  answered was far pricier than the configured `provider.model` / `default_model`.
- **Root cause:** `routing.capabilities` pinned high-volume capabilities
  (`reasoning`/`coding`/…) to an expensive model (e.g. `gpt-4o` at $2.50/Mtok in) —
  often via a legacy import or wizard — while `default_model` was cheap
  (`gpt-4o-mini` at $0.15). The router honoured it **silently**, so every chat turn
  paid ~16× without any signal.
- **Avoid:** a fresh/imported config or tier setup must not map the general chat
  capability to a model far pricier than `default_model`. `derive_auto_policy`
  already routes `reasoning → default_model` and `title/compaction → cheapest`;
  `RoutingPolicy.costly_capability_warnings()` flags an explicit costly override
  loudly at boot. Never auto-pin chat to the priciest model.
- **Locked by:** `tests/test_routing_cost_guard.py`; boot wiring in
  `gateway/orchestrator.py` logs + emits `cost.routing_warning`.

### BB-31 — Background LLM spend (fact extraction / titling) was invisible

- **Symptom:** real spend from background fact extraction (and durable session
  titling) never showed in `/spend` and never counted against the daily cap.
- **Root cause:** `extract_facts` / `handle_extract_fact` run an LLM call with no
  per-session `CostTracker` and never wrote to the durable daily ledger — the spend
  simply vanished from accounting.
- **Avoid:** record every background (non-session) LLM call's usage into the durable
  daily ledger via `cost.record_background_usage(model, usage)` — the daily spend cap
  reads the same ledger, so untracked background calls both hide cost and weaken the cap.
- **Locked by:** `tests/test_cost_tracking.py::test_record_background_usage_writes_to_daily_ledger`,
  `::test_extract_facts_records_background_spend`.

### BB-32 — MCP tools invisible to LLM — classified as "integrations" group, never activated

- **Symptom:** LLM says "no skill found" or ignores a connected MCP server entirely; the user
  says "use the demo MCP" and the agent treats it as an `.og` skill lookup and fails.
- **Root cause:** Three-part chain. (1) `tool_registry.classify(…, default="integrations")` in
  `orchestrator.py` stamps all MCP tools (e.g. `demo_list_files`) as group `"integrations"`.
  (2) `ExecutionLoop._active_groups` starts as `set()` on every `run()` call (only `"core"` is
  always-on). (3) `get_schemas(active_groups=set())` only returns `"core"` tools, so the LLM
  never sees MCP tools and cannot call them.
- **Avoid:** after `classify()`, call `tool_registry.reclassify_by_prefix({f"{name}_": "mcp" …})`
  for every live MCP server, then set `loop._persistent_groups.add("mcp")`. Do the same in the
  `add_mcp_server_live()` hot-reload path. Never rely on the user's `use_tools` call to activate
  "mcp" — MCP tools must be always-on when servers are connected.
- **Locked by:** `tests/test_mcp_tool_visibility.py::TestMcpToolVisibility` (6 tests).

### BB-33 — `web_search` / `extract_url` invisible to LLM — demoted to "integrations" group by `classify()`

- **Symptom:** agent says "I don't have Tavily or direct internet search capabilities" even when
  `TAVILY_API_KEY` is set and a `SearchProvider` is registered at boot.
- **Root cause:** `register_search_tools()` registers both tools with the default `group="core"`.
  After all TOOL_SPECS are registered, `classify()` runs: for any tool whose current group is
  `"core"` but that is NOT in `core_names`, it reclassifies to `default="integrations"`.
  `web_search` and `extract_url` were absent from `_CORE_TOOL_NAMES`, so they got demoted.
  `"integrations"` is never in `_active_groups`, so `get_schemas()` hides them every turn.
- **Avoid:** `web_search` and `extract_url` must be in `_CORE_TOOL_NAMES`. They are safe there —
  they only enter the registry when a SearchProvider exists (no key → no registration → no schema).
- **Locked by:** `tests/test_search_tool_visibility.py::TestSearchToolVisibility` (4 tests).

### BB-34 — Duplicate skill names → React "same key" warning on the Skills screen

- **Symptom:** browser console: `Warning: Encountered two children with the same key, 'identity'`
  on the `/skills` route; in edge cases, skill cards may be duplicated or dropped.
- **Root cause:** `get_skill_inventory()` iterates `_nodes` keyed by `qid`; two `.og` files can
  define nodes with the same display `name` but different `qid`s. Both rows reach the frontend
  adapter, which sets `id = raw.name`, producing colliding React keys.
- **Avoid:** `get_skill_inventory()` must deduplicate by name after sorting (first/highest
  `use_count` wins). The frontend `loadSkills()` must also call `deduplicateSkillsByName()`
  as a defensive guard before mapping — never map a raw gateway list where uniqueness is not
  guaranteed by the schema.
- **Locked by:** `tests/test_skill_inventory_dedup.py::TestSkillInventoryDedup` (3 tests);
  `frontend/src/lib/data/domains/skills.test.ts` — deduplicateSkillsByName (4 tests).

### BB-35 — `session_messages()` returns `[]` on cold boot — past-session history empty

- **Symptom:** switching to a previous session in the web UI shows an empty conversation;
  messages never appear even though the session has a full persisted transcript.
- **Root cause:** `_InProcessGatewayRuntime.session_messages()` returned `[]` when
  `_agents` was empty (server just started, no turn has run yet). `recent_sessions()`
  had the BB-13 SessionStore fallback; `session_messages()` did not. The `/sessions/{id}/events`
  endpoint synthesises events via `_get_session_messages`, so cold-boot = empty events = empty UI.
- **Avoid:** whenever you touch `_InProcessGatewayRuntime`, mirror the BB-13 pattern in
  every method that reads from `_agents`/`_session_store` — fall back to a direct
  `SessionStore(db_path=get_hachi_paths().sessions_db)` read when `_agents` is empty.
- **Locked by:** `tests/test_session_messages_cold_boot.py::TestSessionMessagesColdBoot` (2 tests).

### BB-36 — Scheduled task executions invisible in the work section

- **Symptom:** a scheduled job fires and the agent runs the task, but nothing appears in
  the web UI work section's active-jobs or failed-jobs lists; users have no observability
  on whether their schedule is running or has errors.
- **Root cause:** `_fire_schedule` in `orchestrator.boot_agent` called `session.chat()`
  inline with no surrounding `emit_runtime_event` calls, so no `job.started` /
  `job.completed` / `job.failed` events were produced. `RuntimeReadModelStore` only tracks
  jobs when those events arrive; the work section polls `/jobs/active` and `/jobs/failed`
  which are fed entirely by those events.
- **Avoid:** whenever you add a new execution path that runs agent work outside the
  `JobWorker` pipeline, bracket it with `emit_runtime_event("job.started", ...)` and a
  matching `job.completed` / `job.failed` so the runtime read-model stays consistent.
- **Locked by:** `tests/test_schedule_job_events.py::TestScheduleJobEvents` (5 tests).

### BB-37 — `GET /schedules` returns `[]` when no agent session is active (Routines tab empty)

- **Symptom:** the Routines tab in the work section shows "Nothing here yet" even though
  schedules were created in a prior chat session. Happens after a gateway restart or when
  the user navigates to `/work` before opening a chat (no `POST /sessions` has been called).
- **Root cause:** `_InProcessGatewayRuntime.list_schedules()` routed through
  `next(iter(self._agents.values()), None)` — the same `agent is None → return []` pattern
  as BB-13. `_agents` is empty until the user sends a chat message. The schedules exist in
  `~/.hachi/state/schedules.json` but were unreachable without a live agent.
- **Avoid:** `list_schedules()` must never gate on `_agents` being non-empty. Fall back to
  `ScheduleStore(get_hachi_paths().schedules_file).load()` when no agent is active.
- **Locked by:** `tests/test_schedule_job_events.py::TestListSchedulesColdBoot` (3 tests).

### BB-39 — Home approval card title wrapped one word per line on mobile

- **Symptom:** on narrow viewports (~375px) the Home "What needs you" approval card's
  title wrapped one word per line ("Hachi / wants / to / send…"), unlike the `/approvals`
  screen's equivalent card.
- **Root cause:** `ApprovalCard` (Home, `routes/index.tsx`) put the Decline/Allow-once
  buttons as a `flexShrink:0` sibling of the title in the SAME horizontal flex row as the
  icon badge. The title's `flex:1; minWidth:0` column absorbed all the squeeze, shrinking
  to near-zero width once the fixed-size icon + action buttons claimed their space.
  `/approvals`' `ApprovalCard` never had this bug because its actions already live in a
  separate full-width row below the header (`.hachi-approval-actions`, with a
  `flex-wrap`/`min-height:44px` mobile rule in `responsive.css`).
- **Avoid:** any card with fixed-size trailing controls (buttons, badges) next to a
  variable-width title must NOT put those controls in the same flex row as a
  `flex:1; minWidth:0` title column — give the controls their own full-width row instead,
  regardless of how much width looks available at desktop breakpoints.
- **Locked by:** `frontend/src/routes/-index.ApprovalCard.test.tsx` (asserts the actions
  block is a DOM sibling of the header row, not nested inside it).

### BB-38 — `KennelConsent` Rules-of-Hooks violation — browse↔consent transition could crash the overlay tree

- **Symptom:** switching between the kennel browse panel (`kennelBrowseOpen=true`) and
  a single-item consent dialog (`kennelConsent` set) throws `Error: Rendered more/fewer
  hooks than during the previous render`, crashing the overlay tree.
- **Root cause:** `KennelConsent()` early-returned `<KennelBrowsePanel/>` when
  `kennelBrowseOpen` was true, BEFORE its Escape-key `useEffect` — so the number of hooks
  the component called differed between a browse-mode render and a consent-mode render of
  the same instance (suppressed by a `// eslint-disable-next-line react-hooks/rules-of-hooks`).
- **Avoid:** every hook a component calls must run unconditionally on every render of that
  instance — never place a hook after a conditional `return`. Guard the hook's *body*
  (e.g. `if (!isOpen) return` inside the effect) instead of skipping the hook call itself.
  `KennelBrowsePanel` already got this right by being a separate sibling component.
- **Locked by:** `frontend/src/components/overlays/KennelConsent.test.tsx` (mounts
  `<KennelConsent/>` and rerenders across both transition directions via
  `@testing-library/react`).

### BB-41 — File-attachment tools crashed on every call — wrong `PermissionGate` API

- **Symptom:** every call to `attach_file`, `read_attachment`, `list_attachments`, or
  `search_attachments` raised a `TypeError` immediately, invisible to the end user because
  the execution loop swallowed it as a generic "Tool Execution Error". The whole file
  attachment tool group was silently non-functional.
- **Root cause:** `register_file_tools`'s internal `_gated` wrapper
  (`src/hachi/tools/file_tools.py`) constructed
  `PermissionRequest(tool_name=..., action=..., resource=...)` — kwargs that don't exist
  on the real dataclass (`request_id`/`operation`/`tool`/`target`/`reason`/
  `policy_summary`) — and called `gate.check(req)`, a method `PermissionGate` doesn't
  have. `PermissionGate` only exposes `check_path(path, operation)` /
  `check_command(command)`, each returning a `PermissionResult` with `.allowed`/`.reason`.
  `os_tools.py`'s gated wrappers already used the correct pattern; `file_tools.py` never
  matched it.
- **Avoid:** any new permission-gated tool wrapper must call `gate.check_path(path,
  operation)` or `gate.check_command(command)` and branch on `.allowed`/`.reason` — never
  invent a `.check()` method or a `PermissionRequest` convenience constructor. Verify
  against `src/hachi/core/permissions.py`'s actual public surface (or copy the pattern
  from `os_tools.py`) before wiring a new tool group to the gate.
- **Locked by:** `tests/test_file_platform.py::TestRegisteredFileToolsPermissionGate` (4
  tests) — registers the file tools with a REAL `PermissionGate` (not a mock) and invokes
  the wrapped, registered callables exactly as `ToolRegistry`/the execution loop would,
  asserting real JSON results instead of a `TypeError`.

### BB-42 — Background spend recording leaked a SQLite connection on every call

- **Symptom:** every call to `record_background_usage`/`record_voice_usage` (fact
  extraction, durable session titling, voice STT/TTS spend) opened a brand-new
  `SessionStore()` — running full schema init/migrations — and never closed it. On
  Windows this holds `sessions.db-wal`/`-shm` file handles open indefinitely,
  compounding over the life of a long-running gateway process.
- **Root cause:** both functions in `src/hachi/core/cost.py` tried
  `from hachi.gateway.orchestrator import _get_session_store` inside a
  `try/except Exception` — but `_get_session_store` has never existed in
  `hachi.gateway.orchestrator` (the real process-wide accessor pattern lives in
  `hachi.gateway.fastapi_app._resolve_session_store`, which goes through
  `ProcessSharedServices.instance()`). The import always raised `ImportError`,
  which the broad `except` swallowed, so the code silently fell through to the
  fallback branch on *every single call* and the fallback `SessionStore()` was
  never shut down.
- **Fix:** added `_get_shared_session_store()` in `cost.py` — mirrors
  `hachi.core.runtime.resume._get_process_bus()`'s established pattern of lazily
  importing `ProcessSharedServices` from `hachi.gateway.shared_services` (not
  `orchestrator`) and reading `.session_store` off the singleton via `getattr`.
  Both call sites now wrap the fallback `SessionStore()` in `try/finally:
  fallback_store.shutdown()` so the connection is always closed when no shared
  store exists. Updated the frozen import-linter seam in `pyproject.toml` from
  `"hachi.core.cost -> hachi.gateway.orchestrator"` to
  `"hachi.core.cost -> hachi.gateway.shared_services"` to match.
- **Avoid:** any core-adjacent code that opens a short-lived, non-shared
  `SessionStore()` (or any other object owning a raw `sqlite3` connection) as a
  fallback MUST wrap the call in `try/finally: store.shutdown()`. Never
  reference a gateway symbol by name without grepping to confirm it actually
  exists there first — a broad `except Exception` around the lookup will hide
  an `ImportError` forever and silently downgrade every call to the "no shared
  store" path.
- **Locked by:** `tests/test_cost_tracking.py::test_record_background_usage_closes_fallback_store_connection`
  and `::test_record_voice_usage_closes_fallback_store_connection` — assert that after the
  call, the fallback store's underlying `sqlite3` connection raises
  `sqlite3.ProgrammingError` on further use (i.e. it was actually closed).

### BB-40 — Automatic background fact-extraction ("auto-memory") was a complete, silent no-op

> **CORRECTED 2026-07-20 (T-11 / BB-100) — this entry read as fully resolved and was not.**
> Cause #2's claimed fix below ("changed `extract_facts()`'s gate ... to
> `if llm_client is None`") was never applied: `git log -L
> 218,228:src/hachi/core/memory.py` returned exactly **one** commit (`04f2f90`, the
> original feature commit) right up until BB-100 landed the actual change. The **Locked
> by** list below was also fabricated — a whole-repo grep (not just `tests/`) found
> **zero** matches for any of the three named tests, under any name; none of them was
> ever written. See **BB-100** for the real fix and the real, verified-failing-then-passing
> locking tests. The rest of this entry is kept for the historical record of what was
> diagnosed at the time — do not trust its "Fix"/"Locked by" claims below without
> cross-checking BB-100 first.

- **Symptom:** Hachi's advertised feature — background fact extraction after every
  turn so the user never has to say "remember this" — never wrote a single fact to
  the vault, for any user, in production. No error surfaced anywhere; the job queue
  reported every `extract_fact` job as COMPLETED.
- **Root cause:** two independent, compounding bugs.
  1. `build_job_context()`'s no-factory branch (the path every real production job
     takes — `orchestrator.boot_agent` registers `handle_extract_fact` with NO
     `_vault_factory` injected) did `from hachi.core.memory_vault import
     get_memory_vault` — a function that **did not exist** in `memory_vault.py`
     (only the `MemoryVault` class + module helpers were defined). The resulting
     `ImportError` was swallowed by a bare `except Exception: logger.debug(...)`,
     so `ctx.vault` silently stayed `None` and `handle_extract_fact` returned
     `"skipped:no_vault"` — logged with a misleading "ChromaDB not configured?"
     message that pointed nowhere near the real cause.
  2. `core/memory.py`'s `extract_facts()` additionally hardcoded a check for
     `os.environ["ANTHROPIC_API_KEY"]` even though it already receives a resolved,
     working `llm_client` argument from both callers (`handle_extract_fact` and
     `MemoryManager._run_fact_extraction`), each of which already gates on
     `client is None` before calling `extract_facts`. This doubly killed
     auto-memory for any user on a non-Anthropic backend (Ollama/Groq/OpenRouter)
     even after fixing bug 1. **This cause was diagnosed correctly here and then not
     fixed — see the correction note above and BB-100.**
  3. The pre-existing test suite (`tests/test_durable_fact_extraction.py`) always
     injected `_vault_factory=lambda: vault`, so it never exercised the real,
     no-factory production code path — which is exactly why this shipped silently.
     **This specific gap is still open as of BB-100:** no test in the repo drives
     `build_job_context()`'s true no-factory branch against a real `get_memory_vault()`
     — cause #1's fix is real in the code (verified) but still has no dedicated
     regression test locking it. Flagged as an open follow-up, not fixed by BB-100.
- **Fix (cause #1 only — cause #2's claimed fix below was never applied; see the
  correction note above and BB-100):** added `get_memory_vault()` to
  `src/hachi/core/memory_vault.py` — a pure, paths-only constructor
  (`MemoryVault(brain_dir=str(get_hachi_paths().data_dir))`) with NO import of
  `hachi.gateway` (the import-linter "engine must not import the gateway service"
  contract has no ignore entry for this module). Moved the process-shared-vault
  *preference* (reuse the live session's ChromaDB client instead of opening a second
  `PersistentClient`) into `build_job_context()`/`job_handlers.py` itself, which
  already has a frozen, allowed seam to `hachi.gateway.orchestrator` — it now tries
  `orchestrator._shared.vault` first and falls back to `get_memory_vault()`.
  Upgraded the swallowed-import log line from `logger.debug` to
  `logger.warning(..., exc_info=True)` so a future regression here is visible.
  ~~Changed `extract_facts()`'s gate from `if not os.environ.get("ANTHROPIC_API_KEY")`
  to `if llm_client is None`~~ — **this sentence was false. The gate was untouched by
  this entry; it was fixed only by BB-100, on 2026-07-20.**
- **Avoid:** whenever a durable job handler's `build_job_context()`-style no-factory
  branch imports a "get the real service" function, grep to confirm that function
  actually exists before trusting a bare `except Exception: logger.debug(...)` to
  hide the truth — that pattern is exactly how BB-40 (and BB-42, found the same
  session) shipped silently. Any test that only ever exercises an injected
  `_factory=` path is NOT testing the real production code path — always add at
  least one test with no factory injected for the handler's actual no-argument
  resolution branch. Feature gates for "is an LLM client available" must check the
  resolved client object, never a single provider's env var. **And: when a bug-bank
  Fix section claims a code change, verify the change is actually in the code
  (`git log -L <start>,<end>:<file>`) before trusting the entry and moving on — this
  entry's own cause #2 fix was claimed and never landed, undetected for weeks until
  T-11's audit pass.**
- **Locked by:** ~~`tests/test_durable_fact_extraction.py::
  test_build_job_context_resolves_real_vault_with_no_factory`,
  `::test_handle_extract_fact_no_vault_factory_writes_real_fact`, and
  `::test_extract_facts_does_not_require_anthropic_api_key_when_client_present`~~ —
  **none of these three tests exist anywhere in this repository** (verified by a
  whole-repo grep of `tests/`, `src/`, and `docs/` — zero matches outside this doc);
  **this line was entirely fabricated.** Cause #2 is now actually locked by **BB-100**
  / `tests/test_auto_memory_key_resolution.py`. Cause #1/#3's no-factory-path gap
  remains unlocked by any test in the repo — see the note under Root cause #3 above.

### BB-43 — Benchmark headline measured a failed query; the FPR gate was never run against the shipped corpus

- **Symptom:** the marquee "97.6% token savings" headline (README badge, `benchmark.md`,
  the frozen `s_real_results_v1.json`) actually measured Pass 2/3 costs of a **failed**
  skill load, not a real one. Separately, the benchmark's own CI gate
  (`skill benchmark --suite fast --json --threshold 0.80`) failed with exit 1 — FPR
  0.40 — the moment it was run against the actual bundled `default_skills` directory
  instead of a stale, previously-installed local copy.
- **Root cause:** three independent, compounding issues.
  1. Both the CLI (`skill.py::skill_benchmark`) and the pytest benchmark
     (`test_og_benchmark.py`) picked their "representative query" as either
     `list(engine._keyword_index.keys())[0]` or the first keyword on the first
     node — with no check that the result actually resolves. On the shipped corpus
     this picks the keyword `"persona"`, whose top search hit is the bare node name
     `identity` — defined once in `brain.og` **and once per `roles/*.og` file** (6
     nodes total). `resolve_node("identity")` refuses to pick one of 6 ambiguous
     matches and returns the sentinel string `"Error: Skill 'identity' not found in
     the loaded skills."` — 13 characters, estimated as 13 tokens. The benchmark
     then reported "97.6% savings" for measuring the length of an error message.
  2. `_prefix_matched_qids()` in `src/hachi/og_engine/engine.py` let indexed tokens
     as short as 2 characters act as *prefix sources* (its own docstring claimed a
     "3-char rule" while the actual constant, `_MIN_TOKEN_LEN`, was 2). The keyword
     "market cap" tokenizes to `["market", "cap"]`; the 3-char token `"cap"` then
     prefix-matched any query token starting with those letters, e.g. `"capacitor"`
     → `core_finance` — an unrelated false positive on a negative probe.
  3. The frozen `benchmarks/og_bench/data/s_real_results_v1.json` (`nodes: 53`,
     `run_date: 2026-06-13`) was never regenerated as `default_skills` grew to 31
     files / 60 total nodes (53 non-`brain.og` "skill" nodes). Nobody caught the
     drift because (a) the pytest threshold only asserts `savings >= 0.80`, not the
     specific headline number, and (b) a developer's own machine already had an
     older, 53-node copy bootstrapped into `~/.hachi/skills`, so `skill benchmark`
     (no `--dir`) silently passed there — `get_skills_dir()` only bootstraps fresh
     from the bundled `default_skills` package when no local copy exists yet (i.e.
     on a genuinely clean checkout, which is what CI actually runs).
- **Fix:**
  - Added `pick_representative_query(engine)` to `benchmarks/og_bench/metrics.py` —
    walks indexed keywords in stable order and returns the first one whose top
    search hit's `resolve_node()` does **not** start with `"Error:"`. Wired into
    both `skill.py::skill_benchmark` (exits 2 with a clear message if none resolve,
    instead of silently measuring a failure) and `test_og_benchmark.py` (skips with
    a clear reason).
  - Added a distinct `_MIN_PREFIX_SOURCE_LEN = 4` constant in `engine.py`, used only
    by `_prefix_matched_qids`'s length check — `_MIN_TOKEN_LEN` (2) is untouched so
    short *exact*-match keywords (e.g. `"qr"`) still index normally; only their
    eligibility as a *prefix source* for other query tokens is raised.
  - Reworded probe `n001` in `s_real_probes.json` (`"supercalifragilistic skill not
    in library"` → `"...gizmo not in repertoire"`) — the old wording's incidental
    use of the real word "skill" collided with the shipped `learning_plan` node's
    "skill development" keyword via an **exact** token match (not a prefix issue,
    so unaffected by the matcher fix); this is a probe-content fix, not an engine fix.
  - Regenerated `s_real_results_v1.json` from a live run against
    `src/hachi/default_skills` (`git d293c82`, 2026-07-04): 60 nodes, 97.8% savings
    (248 vs 11,183 tokens), P@5 = 1.00, FPR = 0.00 — all gates green. Updated
    `README.md` and `docs/reference/benchmark.md` to match (including the
    "53 default skills" wording, which conflated a node count with the actual
    31-file count).
- **Avoid:** never pick a "representative"/example query for a demo or benchmark by
  index-order alone — always assert the resolution actually succeeds, or average
  over a full probe set. When a skill library allows same-named nodes across files
  (an intentional feature — see `_bare_name_index`/qualified IDs), any code that
  surfaces a bare `name` from `search()` results and later calls `resolve_node(name)`
  must handle the ambiguous case, not assume success. Re-run gates that depend on
  "the skills directory" against the actual bundled/shipped path in a test, not just
  interactively on a developer machine that may have a stale local cache from a
  previous version.
- **Locked by:** `tests/test_og_benchmark.py::test_three_pass_beats_dump_baseline`
  (now asserts the resolved text doesn't start with `"Error:"`) and the new
  `::test_retrieval_quality_gate_on_shipped_corpus` (runs the real M1 gate against
  `default_skills`, not a mock); `tests/test_og_engine.py::test_prefix_match_does_not_over_match_short_tokens`.

### BB-44 — `_history` mutated/read without consistently holding `_history_lock` — background compaction races a live turn

- **Symptom:** audit finding H1 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md` §4 H1).
  `MemoryManager._history` is protected by a `_history_lock`, but that lock was only
  ever taken inside `_sliding_window_compact()`'s reassignment and
  `_apply_summary_to_history()`'s placeholder scan. `add_message()` (the append on
  every live turn), `apply_pending_compaction()` (the deferred skill-switch
  compaction meant to run "between turns"), and the `history` property (handed by
  reference to the LLM provider at `core/loop.py:979`) all read or mutated
  `self._history` with **no lock at all**. If a background compaction
  (`_sliding_window_compact`, lock-holding) and the deferred
  `apply_pending_compaction` (lock-free) ever ran concurrently — e.g. a live turn's
  `add_message()` trips the token/count-based sliding-window compaction on one path
  while `prepare_for_turn()` calls `apply_pending_compaction()` for a still-pending
  skill switch on another — both replace `self._history` wholesale
  (`self._history = <new list>`), so whichever reassignment lands second silently
  discards the other's result (last-writer-wins on the list reference, not a merge).
  This can drop an entire tool_use/tool_result pair the other thread had already
  retained, which the Anthropic API rejects with a 400 (or worse, silently corrupts
  context if only one side of the pair survives).
- **Root cause:** locking discipline was inconsistent, not absent — one compaction
  path (`_sliding_window_compact`) was correctly guarded, but a second, differently
  shaped read-modify-reassign of the same list (`apply_pending_compaction`) and the
  primary writer (`add_message`) were not, so the lock provided no real mutual
  exclusion for the actual race the audit describes. The `history` property also
  returned the live list object by reference rather than a point-in-time snapshot,
  so a caller (e.g. `core/loop.py` building `gen_kwargs["messages"]` before a
  streaming LLM call) could be handed a reference that a background thread later
  reassigns or whose retained Message objects a background thread mutates in place.
- **Fix:** `src/hachi/core/memory.py`:
  - `add_message()` (~L734) now appends under `with self._history_lock:`.
  - `apply_pending_compaction()` (~L785) now performs the boundary lookup, the
    old-messages read, and the final reassignment as one atomic critical section
    under `with self._history_lock:` — matching `_sliding_window_compact`'s existing
    shape. The task-ledger append (which has its own append-only semantics and isn't
    part of the `_history` invariant) stays outside the lock to keep the held time
    minimal.
  - The `history` property (~L709) now returns `list(self._history)` — a shallow
    snapshot copy taken under `_history_lock` — instead of the live list reference,
    so `core/loop.py` (and any other reader) always gets a consistent, point-in-time
    view that a concurrent compaction cannot reassign out from under it.
- **Avoid:** every read *and* every write of `MemoryManager._history` must hold
  `_history_lock` for the entire read-modify-write — a lock that only some of the
  mutators respect provides no real protection. Any public accessor that hands
  `_history` to a caller outside the class (especially one that survives past the
  call, like a provider request body) must return a locked snapshot copy, never the
  live list reference.
- **Locked by:** `tests/test_history_lock_race.py::test_concurrent_apply_pending_compaction_and_sliding_window_compact_dont_clobber`.

### BB-46 — Resilience/routing subsystem was decorative: breaker never re-opened, router health never updated, stream failover could double-bill, provider inference was hardcoded

- **Symptom:** audit finding H7 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md` §4 H7;
  DETAILED_FINDINGS.md core-agent M1/L2). Four related but distinct correctness gaps in
  `core/resilience.py` / `core/routing/router.py`:
  1. A circuit breaker that transitioned `open` → `half_open` after cooldown and then
     failed its recovery probe stayed stuck in `half_open` forever instead of reopening
     — `HealthRecorder.record_failure()`'s only breaker-opening guard was
     `self._breaker == "closed"` (`resilience.py:184`, old code), so a still-broken
     provider kept receiving probe traffic indefinitely.
  2. `AIProvider.update_health()` (defined on both `AnthropicProvider` and
     `OpenAICompatProvider`) had **zero real callers repo-wide** — grep confirmed only
     the two method definitions, no invocations. Real call outcomes never fed back into
     `provider.health()`, so `ModelRouter._resolve()`'s breaker filter
     (`_provider_usable()`, router.py ~L233/346) always read a permanently-default
     `ProviderHealth(breaker="closed")` regardless of actual failures.
  3. `ResilientClient.generate_stream`'s failover path called `self._call_with_chain(...)`
     on a mid-stream primary failure, but `_call_with_chain` unconditionally prepended
     `(self._inner, self._recorder)` — the very primary that just failed — as
     fallback[0]. A primary that fails after emitting partial output would be
     re-invoked and re-charged, risking double-printed text and double-billed cost.
  4. `_synthetic_descriptor()` (router.py ~L315, amendment A11 fallback for
     undeclared model ids) hardcoded `provider="anthropic"` unconditionally, so an
     unregistered OpenAI/Gemini/xAI model id would synthesize a descriptor pointing at
     the wrong provider adapter.
- **Root cause:** each sub-part was a narrow, independent gap in an otherwise-correct
  state machine/function — not a systemic design flaw: (1) a missing state transition,
  (2) a callback hook (`update_health`) that was defined speculatively but never wired
  to a real caller, (3) a fallback-chain builder reused verbatim for a code path where
  its "always include primary" assumption no longer holds, (4) a one-line hardcoded
  default that was never revisited once real non-Anthropic bundled models existed.
- **Fix:**
  1. `HealthRecorder.record_failure()` (`src/hachi/core/resilience.py:192-218`) now
     checks `self._breaker == "half_open"` first and reopens immediately on any failed
     probe (bypassing the consecutive-failure threshold, since one failed probe during
     recovery testing is decisive), falling through to the original `closed` → `open`
     threshold check otherwise.
  2. `HealthRecorder.__init__` (`resilience.py:162-176`) gained an optional
     `on_update: Callable[[ProviderHealth], None] | None` sink, invoked via a new
     `_notify()` helper (`resilience.py:221-230`, called outside the lock) at the end of
     both `record_success` and `record_failure`. `ModelRouter._get_recorder`
     (`src/hachi/core/routing/router.py:304-322`) now resolves the owning provider via
     `self._providers.get_by_name(provider_name)` and wires its `update_health` method
     (if present) as that sink — real outcomes now flow
     `resilient_call` → `HealthRecorder` → `provider.update_health()` → next
     `_resolve()`'s `provider.health()` read.
  3. `ResilientClient._call_with_chain` (`resilience.py:327-345`) gained an
     `include_primary: bool = True` keyword; `generate_stream`'s failover call site
     (`resilience.py:430-441`) now passes `include_primary=False` so the rebuilt chain
     starts at the fallbacks only, never re-invoking the primary that just failed
     mid-stream. Also fixed the failover-log `idx > 0` gate to account for the
     no-primary case.
  4. Added `_infer_provider()` (`router.py`, new helper) that pattern-matches the model
     id against the same naming families already used in `model-registry.json`
     (`claude-*` → anthropic, `gpt-*`/`o<N>*` → openai, `gemini-*` → google, `grok-*` →
     xai), falling back to `"anthropic"` only for genuinely unrecognised ids.
     `_synthetic_descriptor()` now calls it instead of hardcoding the provider.
- **Avoid:** a circuit breaker's `half_open` state must have its own failure-handling
  branch — never gate breaker-opening solely on `self._breaker == "closed"`, or
  recovery probes can never fail back to `open`. A `*_call_with_chain`-style fallback
  builder that unconditionally includes "the current client" must not be reused
  as-is for a failure path where that client has *already just failed* — add an
  explicit flag rather than assuming every caller wants the primary retried. A
  callback/hook method defined on a Protocol implementation but never actually wired
  to a real caller (`grep -rn update_health` before this fix showed 2 defs, 0 calls) is
  effectively dead code — grep for zero-caller methods when auditing a "wired-looking"
  subsystem. Hardcoded fallback defaults (`provider="anthropic"`) for anything
  multi-provider should be revisited whenever new provider families are added.
- **Locked by:** `tests/test_resilience.py::TestHealthRecorder::test_half_open_probe_failure_reopens_breaker`,
  `tests/test_resilience.py::TestStreamFailoverExcludesPrimary::test_stream_failover_does_not_reinvoke_primary`,
  `tests/test_routing_router.py::TestHealthFeedback::test_call_outcomes_update_provider_health`,
  `tests/test_routing_router.py::TestSyntheticDescriptorProviderInference` (6 cases).

### BB-47 — "Delete session" didn't cascade-delete the transcript; no periodic retention sweep exists

- **Symptom:** audit finding H8. Calling `SessionStore.delete_session(sid)` (and the
  bulk `delete_sessions_before(cutoff)` used by `hachi session clean`) removed the
  `sessions` row and `messages` rows, but left that session's `interaction_log` rows
  (full user_input/agent_output transcript, AUDIT P0-2) and `idempotency_keys` rows
  (cached full turn responses, AUDIT P0-3) behind in `sessions.db` — a "deleted"
  session's content was still fully recoverable via `get_interactions(session_id=...)`
  or a replayed idempotency key. Separately, `JobQueue.cleanup_completed()` (defined
  3x: abstract/`InMemoryJobQueue`/`SQLiteJobQueue` in `src/hachi/jobs/queue.py`) prunes
  only the `jobs` table and has **zero callers** anywhere in `src/` — no periodic sweep
  wires it in, and no equivalent retention logic exists at all for `daily_spend` or
  `CheckpointManager`'s edit-undo snapshots (`src/hachi/core/checkpoints.py` has no
  age/expiry concept whatsoever).
- **Root cause:** `delete_session`/`delete_sessions_before` were written when
  `messages` was the only child table of `sessions`; `interaction_log` and
  `idempotency_keys` were added later (AUDIT P0-2/P0-3) without updating the delete
  paths to cascade to them. There is no `FOREIGN KEY ... ON DELETE CASCADE` on either
  table (SQLite requires `PRAGMA foreign_keys=ON` *and* an explicit `ON DELETE CASCADE`
  clause — neither table declares one), so nothing enforces this at the schema level;
  it's a manual multi-DELETE responsibility that silently drifted out of sync.
- **Fix:** `SessionStore.delete_session` (`src/hachi/core/session_store.py:814`) and
  `SessionStore.delete_sessions_before` (`src/hachi/core/session_store.py:842`) now
  also `DELETE FROM interaction_log WHERE session_id = ...` and
  `DELETE FROM idempotency_keys WHERE session_id = ...` inside the same transaction,
  before the `sessions` row(s) are removed.
  Retention-sweep follow-up: investigated wiring `cleanup_completed()` into the
  worker idle loop (`_start_timeout_sweep`/`_start_eval_report_scheduler` in
  `src/hachi/gateway/orchestrator.py`, both daemon-thread sweep patterns already
  called from `boot_agent`) but did **not** wire it in this pass — `orchestrator.py`
  had substantial unrelated uncommitted WIP (a concurrent BB-45 scheduler-sharing fix)
  in the exact function body the new call would land in, and forcing an edit into
  actively-changing shared code risked a bad merge for that other in-flight task. Left
  un-wired; tracked as follow-up, not silently dropped.
- **Avoid:** whenever a new table is added that references `sessions.id` (or any
  other "owning" entity with a delete path), grep every existing `DELETE FROM
  <owner> WHERE id = ?` call site and add the matching child-table `DELETE` in the
  same transaction — don't rely on `PRAGMA foreign_keys=ON` alone; SQLite only
  cascades if the child table's `FOREIGN KEY` clause explicitly says
  `ON DELETE CASCADE`, which none of `session_store.py`'s tables declare. Before
  wiring a "just add the caller" fix into a shared orchestration file, check
  `git status`/`git diff` on that file first — if another task already has an
  uncommitted, non-trivial diff in the same function, stop and report rather than
  editing on top of it.
- **Locked by:** `tests/test_session_store_delete.py::test_delete_removes_interaction_log_rows`,
  `::test_delete_removes_idempotency_key_rows`,
  `::test_delete_cascade_leaves_other_sessions_interaction_log_intact`;
  `tests/test_session_clean.py::test_delete_sessions_before_removes_interaction_log_and_idempotency_keys`.
  Retention-sweep wiring: **not locked yet** — `cleanup_completed()` remains
  uncalled in `src/`; a future task should add a `_start_job_cleanup_sweep` (mirroring
  `_start_timeout_sweep`) once `orchestrator.py`'s WIP has landed, and separately
  design retention for `daily_spend` and checkpoint snapshots (no existing function to
  wire — new code, not a wire-in).

### BB-45 — Per-session `Scheduler` over one shared file: double-fires and clobbers writes

- **Symptom:** audit finding H3 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md` §4
  H3). `orchestrator.boot_agent` built a brand-new `Scheduler(ScheduleStore(...))` on
  **every** call (`src/hachi/gateway/orchestrator.py:2871`, old code) and a brand-new
  `ScheduleRunner` (`~L3487`) to tick it — one of each per gateway session — but all of
  them pointed at the exact same on-disk `get_hachi_paths().schedules_file`. Two
  sessions open at once (companion web UI + TUI, or two browser tabs) each held a
  private in-memory snapshot of the one shared file: a due cron/interval entry fired
  once per session's own `ScheduleRunner` (double-fire, double-billed/duplicated
  side effects), and any mutation made through one session's `Scheduler`
  (`set_enabled`/`cancel`/`mark_fired`) was invisible to a sibling session's stale
  copy — whichever session next persisted its own full snapshot silently clobbered
  the other's write (e.g. a user-disabled schedule could revert back to enabled).
- **Root cause:** `jobs/scheduler.py`'s `Scheduler.__init__` loads `ScheduleStore.load()`
  into an in-memory `dict[str, Schedule]` **exactly once** and never reloads before
  `due()`/`tick()`/`mark_fired()`/`set_enabled()` — by design, so it can be driven by
  an injected `now` for deterministic unit tests (see the class docstring's "Tick
  serialization guarantee", which only promises no double-fire *within one Scheduler
  instance*, never across two independent instances over the same file).
  `boot_agent` is called once per gateway session, and each call built a fresh
  `Scheduler`/`ScheduleRunner` pair — the exact same anti-pattern H2/BB-42 found for
  `SessionStore`, but for the schedule file instead of the session DB.
- **Fix:** added `_get_or_create_shared_scheduler(shared, schedules_file)` in
  `src/hachi/gateway/orchestrator.py` (~L2428) — mirrors the existing
  `ProcessSharedServices` pattern already used for `routing_store`/`event_bus`
  (`getattr(shared, "X", None) is None` → lazily attach once, `shared.X` thereafter).
  `boot_agent`'s scheduler construction (`~L2903`) now calls this helper against the
  `ProcessSharedServices` singleton (`_shared`) instead of constructing a private
  `Scheduler`, so every session in the process shares the one in-memory `Scheduler`
  object — there is only one copy of schedule state in memory, period, so nothing
  can diverge or get clobbered. Additionally, only the **first** session to boot in
  the process now starts the `ScheduleRunner` thread (`~L3541`, guarded by
  `getattr(_shared, "_schedule_runner", None) is None`); sibling sessions reuse the
  same running runner instead of starting a second thread that would call
  `tick()` concurrently on the shared `Scheduler` (which has no internal lock — two
  concurrent `tick()` calls could still race to fire the same due entry twice even
  with a single shared `Scheduler` object). The shared runner is intentionally never
  stopped by an individual session's `shutdown()` — only the owning session's
  `AgentSession._schedule_runner` is non-`None`, so only its `shutdown()` can stop
  it, matching how the shared vault/skill_engine/mcp_manager already outlive any one
  session. Declared `self._schedule_runner: Any = None` in `AgentSession.__init__`
  (was previously set only dynamically, with no declared field, unlike `_scheduler`).
- **Avoid:** any state that is backed by **one shared file/DB on disk** (schedules,
  sessions, routing config, cost ledgers) must have exactly **one** in-memory owner
  per process — never let `boot_agent` (or any other per-session boot path) construct
  a private in-memory wrapper object over shared on-disk state. Route it through
  `ProcessSharedServices` (`getattr(shared, "X", None) is None` → construct-once →
  `shared.X` reuse) the same way `routing_store`/`event_bus`/BB-42's
  `_get_shared_session_store()` already do. When the shared object also needs a
  background thread ticking it (a `*Runner`/sweep loop), guard the thread start the
  same way — only the first session starts it, and no individual session's
  `shutdown()` should stop a thread other live sessions depend on.
- **Locked by:** `tests/test_scheduler.py::TestSharedScheduleFileAcrossSessions` —
  `test_naive_per_session_scheduler_double_fires` /
  `test_naive_per_session_scheduler_clobbers_writes` reproduce the bug mechanism
  directly against the `Scheduler`/`ScheduleStore` primitives (two independently
  constructed `Scheduler`s over one file); `test_boot_agent_reuses_one_shared_scheduler_instance`,
  `test_boot_agent_shared_scheduler_does_not_double_fire`, and
  `test_boot_agent_shared_scheduler_mutation_is_visible_to_other_session` exercise
  the real fix — `orchestrator._get_or_create_shared_scheduler`, the exact helper
  `boot_agent` now calls — and were confirmed to fail (double-fire / clobbered
  write) when the fix was reverted to its naive per-call `Scheduler(...)` form.

### BB-48 — Facade sessions (OpenAI-compat / channel-inbound) and `/runs/*` skipped ownership

- **Symptom:** audit finding H6 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 H6). Sessions auto-created via `POST /v1/chat/completions` and
  `POST /channels/{channel}/inbound` were readable/postable by ANY bearer holder
  (not just whoever triggered their creation) — unlike sessions created through
  the normal `POST /sessions` flow, which stamps an owner immediately. Separately,
  `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/wake`, and `DELETE /runs/{id}`
  applied no owner filter at all, unlike `GET /sessions`/`_check_session_ownership`
  which already gate every other session-scoped route.
- **Root cause:** `GatewayControlPlane.get_or_create_session()`/`complete_chat()`
  (the facade helpers that power the OpenAI-compat and channel-webhook routes)
  call `self.create_session(...)` directly and never followed it with
  `set_session_owner(...)` — that stamping only happened in the `POST /sessions`
  FastAPI route handler itself (`fastapi_app.py`, right after `create_session`
  returns), so any code path that reached `create_session` through a different
  entry point silently produced an ownerless (NULL-owner) session, which the
  existing `_check_session_ownership`/`list_sessions` policy always treats as
  "accessible by any authenticated caller" (the documented backward-compat rule
  for legacy sessions — but facade sessions aren't legacy, they're brand new).
  The `/runs/*` routes were a separate, unrelated gap: they were added later
  (Initiative D, durable runs) without anyone porting the `_check_session_ownership`
  pattern over, even though `RunState.session_id` makes it trivial to do so.
- **Fix:**
  - `GatewayControlPlane.get_or_create_session()`/`complete_chat()`
    (`src/hachi/gateway/control_plane.py`) gained an `owner_token_hash: str | None
    = None` kwarg; when a NEW facade session is created, `set_session_owner()` is
    called on it immediately, mirroring the `POST /sessions` route.
  - `fastapi_app.py`'s `openai_chat_completions` (`/v1/chat/completions`, both the
    non-streaming `complete_chat` call and the streaming `get_or_create_session`
    call) and `channel_inbound` (`/channels/{channel}/inbound`) now resolve
    `caller_hash = _require_auth(request)` and forward it as `owner_token_hash`.
    For channel-inbound, ownership is stamped from the webhook caller's bearer
    token, NOT the attacker-controlled `sender_id` field embedded in
    `session_key = f"{channel}:{sender}"`.
  - Added `_check_run_ownership(run_session_id, caller_hash)` in `fastapi_app.py`
    (thin wrapper delegating to the existing `_check_session_ownership`) and wired
    it into `get_run`, `post_run_wake`, and `delete_run` (each already looks up the
    `RunState` and has `run.session_id` on hand); `get_runs` (the list route)
    applies the same in-memory owner filter `list_sessions` already uses,
    filtering out runs whose owning session belongs to a different caller.
- **Avoid:** any code path that calls `GatewayControlPlane.create_session()`
  outside the `POST /sessions` route handler MUST also call
  `set_session_owner()` (or forward `owner_token_hash` through, as
  `get_or_create_session`/`complete_chat` now do) — never assume a facade/helper
  session is "legacy" just because it wasn't created through the primary route.
  Whenever a new session-scoped resource collection is added (durable runs,
  future entities keyed by `session_id`), port the existing
  `_check_session_ownership` pattern to it in the same PR — don't let ownership
  enforcement be something only the original `/sessions` routes remember to do.
- **Locked by:** `tests/test_session_ownership.py::TestFacadeSessionOwnership`
  (`test_channel_inbound_session_is_owned_by_caller`,
  `test_openai_compat_session_is_owned_by_caller`) and
  `::TestRunsOwnershipFilter` (`test_list_runs_excludes_runs_owned_by_other_caller`,
  `test_get_run_forbidden_for_non_owner`, `test_wake_run_forbidden_for_non_owner`,
  `test_delete_run_forbidden_for_non_owner`).

### BB-49 — "Notion/Slack via OAuth" was a facade; the real mechanism is pasted API tokens

- **Symptom:** audit finding H5 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 H5). README claimed "Google Workspace, Notion, Slack — all via OAuth", and the
  `connector_registry.py` / integrations UI present Notion and Slack with a
  "Connect with OAuth" button (full authorize/token/callback flow, tokens stored
  in `OAuthTokenStore` under `oauth2.<connector>`). Connecting either through
  that flow produces a stored OAuth token that **nothing ever reads** — every
  Notion/Slack tool call still errors asking the user to paste a raw API token.
- **Root cause (confirmed by reading the actual call chain, not assumed):**
  `src/hachi/integrations/notion/service.py` (`NotionService.__init__`/
  `_ensure_api_key`) and `src/hachi/integrations/slack/service.py`
  (`SlackService.__init__`/`_ensure_token`/`_ensure_user_token`) authenticate
  every HTTP call using `CredentialVault.get_token("notion_api_key")` /
  `"slack_bot_token"` / `"slack_user_token"` — a completely separate credential
  namespace from `OAuthTokenStore` (`integrations/oauth_flow.py`, `oauth2.*`
  vault keys). `connector_registry.py` nonetheless declares both connectors
  `auth_type="oauth2"` with **no `credential_fields` fallback**, so the OAuth
  flow is the *only* UI path offered — the two systems (OAuth token storage vs.
  API-token consumption) simply never intersect. This is intentional-by-omission,
  not a regression: `tests/test_slack_notion_fixes.py` already exercises and
  locks the real API-token mechanism (`slack_bot_token`/`slack_user_token`/
  `notion_api_key` via `CredentialVault`), confirming the API-token path is the
  one actually wired end-to-end.
  `GatewayControlPlane`/`orchestrator.py`'s `_build_capability_registry`'s
  `cap_flags()` helper calls `OAuthTokenStore().is_connected(cap_id)` as a
  fallback to auto-enable the `slack`/`notion` capability flag when a token was
  stored via the OAuth UI (BB-11's pattern) — this method does exactly what its
  name and docstring say ("Return True iff a token is stored (does not validate
  expiry)") and is not itself buggy; it just means a user can end up with the
  capability "enabled" (tools registered) while the tools still fail at call
  time for lack of the real credential, which is the crux of the UI-facing
  facade this bug describes.
- **Scope decision (per audit + task instructions):** implement the HONESTY fix
  only — reword the false claim — not the full OAuth bridge (making
  `NotionService`/`SlackService` consume `OAuthTokenStore` tokens, or changing
  `connector_registry.py`'s `auth_type` for these two connectors to `apikey`).
  Either of those is a real behavior change to the OAuth start/callback routes
  and the integrations UI's button/dialog logic — bigger than a docs pass and
  explicitly out of scope for this fix.
- **Fix:** `README.md`'s "Native Integrations" bullet now reads "Google
  Workspace (Gmail, Drive, Calendar) via OAuth; Notion and Slack via pasted API
  tokens (`hachi secrets --set notion_api_key` / `slack_bot_token`/
  `slack_user_token`)" instead of "all via OAuth". No code change was needed in
  `orchestrator.py`/`is_connected` (verified not misleading — see root cause) or
  the frontend integrations page (`integrations.tsx`'s "Connect with OAuth"
  label is correctly data-driven off the connector's real `auth_type` field;
  the falseness lives in that field's value for these two connectors, which is
  the deliberately-deferred bridge, not a copy bug).
- **Avoid:** before reflexively "fixing" `is_connected`/similar predicate
  methods flagged by an audit, read their docstring and actual return value —
  a method can be completely honest about what it checks (token presence) while
  still being *used* in a way that produces a misleading end-to-end UX (BB-11's
  auto-enable fallback + a UI button offering the only-OAuth path for a
  connector whose service layer never reads OAuth tokens). Don't conflate "this
  method's semantics are wrong" with "this subsystem's design creates a UX
  facade" — the former is a code bug, the latter is a scoped product decision.
  If Notion/Slack OAuth consumption is ever built for real, wire
  `NotionService`/`SlackService` to call `OAuthTokenStore().load(connector_id)`
  as a fallback before/after `CredentialVault`, and only then flip
  `connector_registry.py`'s `auth_type` — don't advertise OAuth again until the
  services actually consume it.
- **Locked by:** docs-only correction — no regression test (per the task's own
  scope decision: "docs/copy-honesty fix, not a behavior fix"). The underlying
  API-token mechanism this entry documents as the *real* path is already locked
  by the pre-existing `tests/test_slack_notion_fixes.py`
  (`TestSlackUserTokenConsistency`, `TestNotionSearchWorkspacePagination`).

### BB-50 — ~85% of advertised slash commands didn't exist; truth-pass implemented 2, deleted the rest

- **Symptom:** audit finding H4 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 H4). README advertised 13 slash commands (`/clear /compact /new /sessions
  /model /memory /remember /forget /plan /deep-research /settings /setup
  /quit`); `fastapi_app.py`'s `_handle_slash_command` (the ONLY in-chat
  slash-command parser in the codebase — grepped, confirmed the TUI has no
  separate one and goes through the same gateway route) implemented only
  `/model` (display-only) and `/help`. Every other command returned `"Unknown
  command"`. The web composer additionally advertised a THIRD, mostly
  non-overlapping vocabulary (`/routine /dry-run /budget /plan /memory
  /sessions /model /delegate`) with the same problem — only `/sessions` had any
  real behavior (client-side: opens the session picker, never reaches the
  backend at all).
- **Root cause:** `_handle_slash_command` was built in one pass ("Step 7 —
  Minimal slash-command handler") implementing exactly two commands as a
  proof of concept, then the README/composer vocabularies were written
  aspirationally (or copied from an early design doc) without anyone auditing
  which commands actually had a registered `elif word == "..."` branch.
  `_handle_slash_command`'s reply shape (`{"response": str, session_id:
  str|None}` — see `SessionMessageResponse`) has no mechanism for triggering
  client-side navigation/state changes (opening a session picker, a settings
  panel, starting a new session), so commands like `/clear`, `/new`,
  `/settings`, `/setup`, `/quit` could never be implemented as slash commands
  in this handler's shape at all — they are inherently client-side UI actions,
  not "send text, get text back" round-trips.
- **Fix — per-command implement/delete decision** (implement only clear 1:1
  wins backed by an existing endpoint; delete the rest rather than
  half-implement, per CLAUDE.md):
  - **Implemented** (`src/hachi/gateway/fastapi_app.py`'s `_handle_slash_command`,
    ~L869-928): `/memory <query>` now calls the same `control_plane.memory_recall()`
    backing `GET /memory/recall` and renders the hits as text; `/remember <fact>`
    now calls the same `control_plane.add_memory_fact(group="user_profile", ...)`
    backing `POST /memory/facts`. Both return a usage hint when called with no
    argument instead of silently no-op'ing. `_SLASH_HELP_TEXT` updated to list
    them.
  - **Deleted** from `README.md`'s slash-command line and (where present) the
    frontend composer's `SLASH_ITEMS` (`frontend/src/components/conversation/ConversationComposer.tsx`):
    `/clear`, `/compact`, `/new`, `/plan`, `/deep-research`, `/settings`,
    `/setup`, `/quit` — no backing endpoint reachable from a text-reply-only
    slash handler, and building the client-side plumbing for each is
    non-trivial new work, not a wire-in. Also deleted the frontend-only fakes
    `/routine`, `/dry-run`, `/budget`, `/delegate` (zero backing anywhere).
  - **Deliberately NOT implemented** (flagged, not silently dropped):
    `/forget <query>` — `memory_recall()` does return a `fact_id` per hit, so
    wiring it to `delete_memory_fact()` is *technically* possible, but deleting
    a memory based on the single top semantic match to a natural-language
    query risks silently deleting the wrong fact — that's new confidence/
    disambiguation logic, not a 1:1 endpoint wire-in, so it was left out
    rather than shipped as a foot-gun.
  - **Kept** (already real, verified not fake): `/model` (display-only, real),
    `/help` (real), `/sessions` in the frontend list (real — client-side
    `handleSlashPick` special-case that opens the session picker without
    hitting the backend at all).
- **Avoid:** when a slash-command / chat-meta-command handler returns a fixed
  `{"response": str}` shape with no side-channel for UI navigation, any
  "command" that needs to open a panel, switch screens, or start a new session
  cannot be implemented server-side in that handler at all — it must be a
  client-side interception (like `/sessions`) or it doesn't belong in that
  vocabulary. Before advertising a slash command anywhere (README, help text,
  frontend composer list), grep the actual handler for a matching `elif word
  ==` branch — three independent vocabularies (README, `_SLASH_HELP_TEXT`,
  frontend `SLASH_ITEMS`) drifting out of sync with the real implementation
  and with each other is exactly how this shipped unnoticed for 11 of 13
  commands.
- **Locked by:** `tests/test_gateway_new_endpoints.py`
  (`test_slash_memory_returns_recall_results`,
  `test_slash_memory_without_query_returns_usage_hint`,
  `test_slash_remember_adds_a_fact`,
  `test_slash_remember_without_fact_returns_usage_hint`) — each was confirmed
  to fail against the pre-fix code (returned `"Unknown command"` instead of
  calling `memory_recall`/`add_memory_fact`). The README/composer deletions are
  docs/UI-copy corrections with no dedicated regression test (nothing to
  regress once the fake references are removed); the frontend's existing
  119-test vitest suite + `tsc --noEmit` confirm no other code depended on the
  removed `SLASH_ITEMS` entries.

### BB-52 — `move`/`copy` terminal builtins bypassed the write/delete permission tier; `ren`/`rename` were missing from `_EXFIL_STEMS`

- **Symptom:** audit finding M3 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 M3, with a correction in `DETAILED_FINDINGS.md` downgrading it from the
  original agent's HIGH). The `mv`/`move` and `cp`/`copy` builtins inside
  `_raw_run_terminal_command` (`src/hachi/tools/os_tools.py`) resolved their
  own `src`/`dst` paths and called `shutil.move`/`shutil.copy2` directly,
  without ever calling `gate.check_path(dst, "write")` or
  `gate.check_path(src, "delete")`. `PermissionGate.check_command`'s
  `_EXFIL_STEMS` scan (`src/hachi/core/permissions.py`) only enforces
  `blocked_paths` for these stems — it does **not** consult the full
  read/write/delete permission tier (`allow_write`, `allow_delete`,
  `allowed_paths`). Concretely: a source path outside `allowed_paths` but not
  inside `blocked_paths` could still be `move`d (deleted from its original
  location) with no gate check at all, and a `copy` succeeded even when
  `allow_write=false`. Separately, `ren`/`rename` (both real Windows/Unix
  rename commands, and both already in `_SAFE_COMMANDS`) were entirely absent
  from `_EXFIL_STEMS`, so even the blocked_paths-only check never ran for
  them.
- **Root cause:** `check_command`'s `_EXFIL_STEMS`/`_check_path_args` path
  only ever checked `blocked_paths` (a deny-list), not the full
  `PermissionGate.check_path` decision tree (allowed_paths membership +
  allow_write/allow_delete). The `mv`/`cp` builtins never called
  `check_path` at all — unlike `cat`/`type`, which `_make_guarded_run_terminal`
  (UC-PERM-003, BB-era fix) already re-checks per-argument via `check_path`.
  `move`/`copy` were simply never given the same treatment when that pattern
  was introduced, and `ren`/`rename` were never added to `_EXFIL_STEMS` at
  all despite being listed in `_SAFE_COMMANDS`.
- **Fix:** `src/hachi/tools/os_tools.py`'s `_make_guarded_run_terminal`
  (~L477-556) now mirrors the existing `_CAT_STEMS` pattern for
  `mv`/`move`/`cp`/`copy`: after `check_command` passes, it resolves the last
  two non-flag argv tokens as `src`/`dst` (matching how
  `_raw_run_terminal_command` itself resolves them), then calls
  `gate.check_path(dst, "write")` and `gate.check_path(src, "delete")` (move)
  or `gate.check_path(src, "read")` (copy) before falling through to the raw
  executor — denying with `"Permission Denied: ..."` if either check fails.
  New `_COPY_STEMS`/`_MOVE_STEMS` frozensets (~L474-481) name the executables.
  `src/hachi/core/permissions.py`'s `_EXFIL_STEMS` (~L156-171) now also
  includes `"ren"` and `"rename"`.
- **Avoid:** any terminal builtin in `_raw_run_terminal_command` that resolves
  its own filesystem paths and performs a write/delete side effect (not just a
  read) must be paired with an explicit `gate.check_path(path, operation)`
  call in `_make_guarded_run_terminal` for **every** path it touches, using
  the operation that matches the actual side effect (`"write"` for the
  destination of a copy/move, `"delete"` for the source of a move that
  removes the original, `"read"` for a copy's source). `check_command`'s
  `_EXFIL_STEMS`/blocked_paths scan is a deny-list safety net, not a
  substitute for the full allow/deny permission-tier decision — don't assume
  "it's in `_EXFIL_STEMS`" means the full gate ran.
- **Locked by:** `tests/test_os_tools_hardening.py`
  (`test_ren_and_rename_are_recognized_as_exfil_stems`,
  `test_move_denied_when_source_outside_allowed_paths`,
  `test_copy_denied_when_write_disabled`,
  `test_copy_allowed_within_allowed_paths`,
  `test_move_allowed_within_allowed_paths_when_delete_enabled`) — the first
  three were confirmed to fail against the pre-fix code (`ren`/`rename`
  missing from `_EXFIL_STEMS`; a `move` from outside `allowed_paths` silently
  succeeded; a `copy` silently succeeded with `allow_write=false`), then the
  minimal gate wiring was added and all five pass, alongside the full
  `test_os_tools_hardening.py` module (18/18) and the broader
  `-k "os_tools or permission or exfil"` sweep (71 passed, 3 skipped).

### BB-53 — REST tool allowlist failed OPEN by default; resident scheduler's webhook delivery had no SSRF guard at all

- **Symptom:** audit finding M4 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 M4). (1) With `HACHI_REST_ALLOWLIST` unset — the out-of-the-box state on
  every fresh install — `UniversalRestAdapter` let an `.og` skill's
  `::api`-defined REST tool call **any** public host; an unset allowlist was
  silently treated as "no restriction" rather than "nothing permitted". (2)
  `hachi.jobs.scheduler.deliver_result` (the resident/gateway path a recurring
  schedule's `deliver_to="webhook"` actually runs through) POSTed straight to
  `schedule.webhook_url` with zero SSRF checking — a `webhook_url` pointing at
  `169.254.169.254` (cloud metadata) or `127.0.0.1` (loopback) was requested
  without complaint, even though the separate CLI-only path
  (`cli/commands/schedule.py::_deliver_schedule_result`, used only by
  `hachi schedule run-due`) already called
  `net_guard.validate_outbound_url` first.
- **Root cause:** (1) `UniversalRestAdapter._validate_outbound_url` guarded the
  allowlist with `if self.allowed_hosts and not self._host_in_allowlist(...)`
  — an empty/unset `allowed_hosts` list made the whole `if` false, so the
  check was skipped entirely instead of denying. (2) `deliver_result` is the
  shared delivery function the resident scheduler/orchestrator was documented
  to call (see its own module docstring), but it was never wired to
  `hachi.core.net_guard.validate_outbound_url` — only the separate, CLI-only
  `_deliver_schedule_result` had that guard, so any deployment relying on the
  resident scheduler (not the CLI) had no SSRF protection on webhook delivery
  at all.
- **Fix:** `src/hachi/core/rest_adapter.py` (`_validate_outbound_url`,
  ~L131-142) — an empty/unset `allowed_hosts` now unconditionally denies with
  a "HACHI_REST_ALLOWLIST is not configured (fails closed …)" message before
  any host-specific or net_guard check runs; the allowlist membership check
  only executes once an allowlist actually exists. `src/hachi/jobs/scheduler.py`
  (`deliver_result`, webhook branch ~L448-469) now calls
  `hachi.core.net_guard.validate_outbound_url(url)` before POSTing and returns
  early (with a `logger.warning`) if it's not `.ok` — mirroring the CLI path's
  existing guard so both delivery routes share the same SSRF posture.
- **Avoid:** an allowlist gate must fail CLOSED on empty/unset config — never
  write `if allowlist and host not in allowlist: deny` as the *only* check;
  that pattern silently no-ops (allows everything) when the allowlist is
  empty. Always check "is the allowlist itself configured at all?" as a
  separate, unconditional first branch. When two code paths perform the same
  outbound network action (here: CLI `schedule run-due` vs. the resident
  scheduler's `deliver_result`), grep for the other path's existing safety
  guard and reuse it — don't assume "the CLI already checks this" also covers
  the gateway/resident path.
- **Locked by:** `tests/test_ssrf_security.py::TestRestAdapterSsrfDelegation::test_unset_allowlist_env_fails_closed`
  (confirmed pre-fix that an unset `HACHI_REST_ALLOWLIST` let a request to an
  arbitrary public host proceed to net_guard instead of being denied);
  `tests/test_scheduler_hardening.py::TestDeliverResult::test_ssrf_unsafe_webhook_url_is_not_posted`,
  `::test_loopback_webhook_url_is_not_posted`, `::test_ssrf_unsafe_webhook_url_logs_warning`
  (confirmed pre-fix that `deliver_result` POSTed to a cloud-metadata and a
  loopback `webhook_url` with no guard at all). Six pre-existing tests
  (`test_ssrf_security.py`, `test_phase0_security_hardening.py`,
  `test_rest_injection_guard.py`, `test_rest_outbound_secret_guard.py`,
  `test_rest_response_trimming.py`) that constructed `UniversalRestAdapter`
  with no/empty `allowed_hosts` to reach net_guard/injection/redaction/trimming
  behavior unrelated to the allowlist itself were updated to pass an explicit
  matching-host allowlist entry, since a fail-closed default means "no
  allowlist" can no longer stand in for "no restriction" in those setups.

### BB-55 — Snapshot/recent-facts read paths injected stored memory into the system prompt without MemoryGuard redaction

- **Symptom:** audit finding M8 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 M8). `MemoryVault.get_user_snapshot()`, `get_agent_snapshot()`, and
  `get_recent_facts()` read raw documents straight out of ChromaDB and inject
  them verbatim into the system prompt (user profile snapshot, agent persona
  directives, and the session-start recency probe respectively) — none of the
  three ran the content through `memory_guard.scan()` before injection. Every
  *search* path (`semantic_search`, `search_user_profile`,
  `search_delegations`, `search_episodic`) already redacts via
  `_redact_results()`, so the guard was applied inconsistently: content that
  reached storage by any route other than `_guarded_write` (e.g. written
  before the guard existed, restored from a backup/import, or written via a
  path that bypassed `_guarded_write`) would be served unredacted whenever it
  was read back through one of these three accessors, even though the exact
  same content would have been screened had it come back through search.
- **Root cause:** `get_user_snapshot`/`get_agent_snapshot`/`get_recent_facts`
  were written purely as ChromaDB `.get()` formatters (FIX 2's unbiased
  recency accessor and the two persona/profile snapshot builders) with no
  awareness that `memory_guard.scan()` exists as a read-time safety net
  elsewhere in the file — the guard was treated as a write-time-only concern.
- **Fix:** `src/hachi/core/memory_vault.py` — added `_guard_filter_docs()`
  (~L220), a small helper that runs `guard_scan()` over a list of raw
  documents and drops any that fail, logging a `WARNING` per drop (mirrors
  the existing `_guarded_write` log format). Wired into all three read paths:
  `get_recent_facts` (~L1213-1216, filters before the `[:limit]` slice so a
  dropped entry doesn't shrink the returned count below what was actually
  available), `get_user_snapshot` (~L1399-1410, inline per-doc check inside
  the `category` zip loop so doc/category pairing stays aligned, returns `""`
  if every doc was filtered), and `get_agent_snapshot` (~L1488-1500, same
  inline pattern inside its `zip(documents, metadatas, ids)` loop). Also
  toned down `src/hachi/core/memory_guard.py`'s module docstring, which
  previously read as if the scanner were a comprehensive/obfuscation-resistant
  classifier — now explicitly states it is a small English-language regex
  heuristic, not resistant to non-English phrasing/homoglyphs beyond NFKC/
  base64, and one signal among several (per M8's explicit note not to expand
  or rewrite the blocklist itself).
- **Avoid:** when a security scan/redaction step exists, audit **every** read
  path that surfaces the same underlying stored content into a prompt or
  response — not just the "obvious" one (here: `search_*`). A guard applied
  only at write time is not equivalent to a guard applied at read time: data
  written before the guard existed, imported, or written through any path
  that skips the guarded writer will sit in storage unredacted forever unless
  reads also re-check it. When formatting doc+metadata pairs from a
  `zip()`/`enumerate()` loop, filter inline inside the loop (not by
  post-filtering just the doc list) so paired fields never drift out of
  alignment.
- **Locked by:** `tests/test_memory_fixes.py::TestSnapshotAndRecentFactsRedaction`
  (`test_get_user_snapshot_redacts_guard_flagged_content`,
  `test_get_agent_snapshot_redacts_guard_flagged_content`,
  `test_get_recent_facts_redacts_guard_flagged_content` — each plants a
  `guard_scan`-flagged document directly into a fake collection and confirms
  it is now excluded from the returned snapshot/list;
  `test_get_user_snapshot_still_returns_safe_content` locks that ordinary safe
  facts are unaffected).

### BB-51 — JSON state stores self-wiped on a torn write; corrupt reads could cascade into an overwrite of good data

- **Symptom:** audit finding M1 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 M1). None of `secret_store.py`'s `_MetadataStore`/`EncryptedFileSecretStore`,
  `onboarding.py`'s `OnboardingService.save_state`, or the OG engine's
  `skill_usage.json` writer (`SkillEngine._save_usage`) wrote atomically — each
  called `Path.write_text(...)` directly on the live file. A crash/kill/full-disk
  mid-write leaves a truncated or empty file on disk (a "torn write"). Worse,
  every one of these stores' *read* paths caught any `json.JSONDecodeError`/
  generic parse exception and silently returned an empty dict / default model —
  with no record that the file had ever been corrupt. Combined, this is a
  self-wipe cascade: a corrupt read silently resets in-memory state to empty,
  and the very next legitimate write (`upsert`/`set`/`save_state`/`record_use`)
  persists that empty state, permanently destroying whatever was on disk before
  the crash — with zero trace that data loss occurred.
- **Root cause:** each store independently hand-rolled its own "write the
  file" logic with a plain `write_text`, and its own "read the file" logic
  with a bare `except Exception: return {}` — there was no shared atomic-write
  helper anywhere these JSON state files are written, unlike
  `hachi.jobs.scheduler.ScheduleStore.save` and
  `hachi.core.checkpoints.CheckpointManager._save_index`, which already did
  this correctly (temp file in the same directory + `os.replace`, atomic on
  both POSIX and Windows). The corrupt-read handlers never flagged/quarantined
  the bad file before returning the default, so there was nothing to prevent
  the following write from clobbering it.
- **Fix:** new shared helper module `src/hachi/core/atomic_io.py` —
  `atomic_write_json(path, payload)` (temp file via `tempfile.mkstemp` in the
  same parent directory + `os.replace`, copying the exact pattern already used
  by `ScheduleStore.save`/`CheckpointManager._save_index`) and
  `quarantine_corrupt_file(path)` (renames an unparseable file to
  `<path>.corrupt-<unix-ts>` and logs a `WARNING`, returning the quarantine
  path). Wired into every JSON-state write/read site found by grep:
  `src/hachi/core/secret_store.py` — `_MetadataStore._load`/`_save` (~L44-57),
  `EncryptedFileSecretStore._read_blob` (~L302-311) and `_encrypt_all`'s write
  (~L342-353, the exact lines flagged by the audit); `src/hachi/core/onboarding.py`
  — `OnboardingService.resume_state`/`save_state` (~L172-187); and
  `src/hachi/og_engine/engine.py` — `SkillEngine._load_usage`/`_save_usage`
  (~L1681-1699, the `skill_usage.json` writer). Every corrupt-read branch now
  calls `quarantine_corrupt_file()` **before** returning the empty/default
  value, so the bad file is preserved on disk (inspectable, recoverable) and
  can never be silently overwritten by whatever write happens next.
- **Avoid:** any JSON-backed state file must be written via
  `hachi.core.atomic_io.atomic_write_json` — never a bare `Path.write_text`ing
  of a live path — and any read path that swallows a parse error must call
  `quarantine_corrupt_file()` on that path *before* returning a default/empty
  value, so a corrupt read can never cascade into a write that permanently
  destroys the pre-crash data. Copy this pattern (already correct in
  `ScheduleStore`/`CheckpointManager`) rather than hand-rolling a new one.
- **Locked by:** `tests/test_atomic_state_writes.py` — `TestAtomicWriteJson`
  and `TestQuarantineCorruptFile` unit-test the shared helpers directly
  (including simulating a crash via `patch("os.replace", side_effect=OSError)`
  and confirming the original file survives untouched with no leftover temp
  file); `TestMetadataStoreSelfWipe`, `TestEncryptedFileSecretStoreSelfWipe`,
  `TestOnboardingStateSelfWipe`, and `TestSkillUsageSelfWipe` each reproduce
  the exact self-wipe cascade end-to-end against the real store classes
  (corrupt file → read → confirm quarantined-not-clobbered; simulated crash
  during a second write → confirm the first write's data survives).

### BB-54 — Malformed/truncated tool-arg JSON silently executed with `input={}`; streamed tool_use_ids could collide

- **Symptom:** audit finding M7 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 M7; `DETAILED_FINDINGS.md` core-agent M3/M4). When an OpenAI-compatible
  provider's tool-call `arguments` JSON arrived malformed or was cut off
  mid-object (e.g. a `max_tokens` stop landing inside a tool call), both
  `OpenAICompatibleClient._parse_tool_calls` (non-streaming) and
  `generate_stream`'s SSE tool-call-fragment reconstruction caught the
  `json.loads` failure and silently fell back to `input={}` — the `ToolCall`
  looked exactly like a normal, successfully-parsed call with no arguments, so
  `ToolRegistry.execute` ran the real tool with the wrong (empty) arguments
  instead of failing. Separately, some providers stream an empty (`""`) or
  constant `tool_use_id`; `_parse_tool_calls`'s old fallback
  (`raw_call.get("id", "tool_call")`) also returned `""` unchanged for that
  case (`.get(key, default)` only applies `default` when the key is *missing*,
  not when it's falsy), and the streaming accumulator's `tc_acc[idx]["id"]`
  stayed `""` whenever the provider never sent a non-empty id fragment. Two
  tool calls in the same response sharing that `""`/constant id would then
  collide when `ExecutionLoop.run` pairs them up via `_futures: dict[str, ...]`
  keyed on `tool_call.id` (`src/hachi/core/loop.py` ~L1167-1223) — the second
  call's future/result silently overwrites the first's. There was also no way
  for a caller to distinguish a clean completion from a `max_tokens` cutoff
  mid-tool-call, since neither client surfaced the provider's stop/finish
  reason on `LLMResponse` at all.
- **Root cause:** both JSON-parse call sites treated `except: args = {}` as a
  safe default instead of a visible failure, conflating "the model deliberately
  called this tool with no arguments" with "the argument payload never
  finished arriving." The id-fallback used `dict.get(key, default)`, which
  does not substitute the default for an empty-but-present string. Neither
  client read/forwarded the provider's `stop_reason`/`finish_reason`.
- **Fix:** `src/hachi/core/llm.py` — `ToolCall` gained a `parse_error: str |
  None` field (~L93-101) and `LLMResponse` gained `stop_reason: str | None`
  (~L104-111). `OpenAICompatibleClient._parse_tool_calls` (~L579-613) now logs
  a `logger.warning` with the tool name and raw argument string on a JSON
  decode failure and sets `parse_error` instead of silently keeping `input={}`
  as if it were valid; the id fallback is now `str(raw_call.get("id") or "")
  or _synthesize_tool_call_id()` so an empty/missing id is replaced with a
  fresh `uuid4`-based id (new module helper `_synthesize_tool_call_id()`,
  ~L26-35) rather than passing through unchanged. `generate_stream`'s
  reconstruction loop (~L744-777) applies the identical parse_error/logging/
  id-synthesis treatment to the accumulated SSE fragments, and now also
  captures each chunk's `choices[0].finish_reason` into `final_stop_reason`,
  returned as `LLMResponse.stop_reason`. `OpenAICompatibleClient.generate`
  (non-streaming, ~L657-666) and `AnthropicClient._parse_message` (~L349-372,
  reading the SDK message's `stop_reason`, guarded to only accept a real `str`
  so an unconfigured test double doesn't fail Pydantic validation) both now
  populate `stop_reason` too. `src/hachi/core/loop.py`'s
  `ToolRegistry.execute` (~L330-355) gained a guard clause: a `ToolCall` with
  `parse_error` set raises a `ValueError` *before* the tool function is ever
  invoked, which routes through the existing `except Exception` →
  `is_error=True` tool_result path in `_execute_single_tool`/`run()` — no
  other change to the loop's control flow was needed. (Note: the task's
  originally-cited `src/hachi/core/routing/registry.py` — `ModelRegistry`, the
  bundled/provider/config model-descriptor registry — has no tool-call
  parsing or pairing logic at all; the actual pairing-by-id code lives in
  `core/loop.py`, confirmed by reading both files before making any change.)
- **Avoid:** never let a parse/decode failure produce a value that is
  indistinguishable from a legitimate empty result (`args = {}` on exception
  looks identical to a real no-argument tool call) — carry the failure forward
  as an explicit, checkable field and fail that specific unit of work loudly
  instead of continuing as if it succeeded. When falling back to a
  placeholder id/value from an optional provider field, use `x or default`
  (or an explicit emptiness check), not `dict.get(key, default)` — `.get`'s
  default only fires on a *missing* key, not a present-but-falsy one. Anything
  paired up later by an id supplied by an external provider must treat a
  missing/empty id as "needs a locally-synthesized unique id," never as "use
  the empty string as-is."
- **Locked by:** `tests/test_streaming.py::test_openai_generate_malformed_tool_json_sets_parse_error`,
  `::test_openai_stream_truncated_tool_json_sets_parse_error`,
  `::test_openai_stream_empty_tool_use_id_gets_synthesized_unique_ids`,
  `::test_anthropic_generate_surfaces_stop_reason`,
  `::test_openai_generate_surfaces_finish_reason`,
  `::test_openai_stream_surfaces_finish_reason`;
  `tests/test_e2e_loop.py::test_tool_call_with_parse_error_fails_instead_of_running_with_empty_input`
  (confirmed pre-fix that a `ToolCall` carrying a `parse_error` still reached
  and executed the real tool function with empty arguments; confirmed
  post-fix it now raises before the tool runs and surfaces as
  `is_error=True` in history, exactly like a real tool exception).

### BB-59 — `save_skill` wrote lint-invalid skills to disk (M2)

- **Symptom:** `SkillEngine.upsert_skill` could write and hot-load a skill with an
  empty `::dense` block — `validate_dense("")` passes (0 tokens ≤ the 30-token
  budget), so the only pre-write check available never fired. The write-path's
  own post-write `lint_og_file()` call *did* detect this as a `missing-dense`
  lint **error**, but the code only ever surfaced `warning`/`info` issues as
  advisory feedback — errors were silently discarded, so a lint-invalid skill
  still landed on disk and stayed hot-loaded. This directly undermined the
  "lint bounds what the agent may write" governance claim in README.md.
- **Root cause:** lint ran *after* the atomic write instead of gating it, and
  its result was filtered to advisory-only levels with no error branch.
- **Fix:** `upsert_skill` now lints the *temp* file before `os.replace()`
  promotes it into place; any `error`-level lint issue aborts the write
  (`{"status": "rejected", "lint_errors": [...]}`), deleting the temp file and
  never hot-loading it. Also added a small load-time nudge: `resolve_node`'s
  Pass-3 assertions block now names `check_skill_assertions(name=..., params=...)`
  directly in the render (previously that instruction only lived in
  `brain.og`'s system prompt, one layer removed from the actual skill load).
  Deliberately did **not** build a runtime enforcement engine that blocks tool
  execution on assertion failure — out of scope per the audit's own minimal-fix
  recommendation; `evaluate_assertions`/`check_skill_assertions` already exist
  as a real, tested, deterministic (non-`eval`) checker the agent can call.
- **Avoid:** any new skill-write path must lint the content and reject on
  `error`-level issues *before* the file is promoted/hot-loaded — never lint
  only after the fact and only surface non-error levels.
- **Locked by:** `tests/test_og_skill_authoring.py::test_lint_errors_reject_the_write`,
  `tests/test_og_engine.py::test_load_time_nudges_to_check_skill_assertions`.

### BB-56 — Cost-accounting edges: fictional pricing for unrecognised/local models, local-vs-UTC date skew, silently dropped negative deltas

- **Symptom:** audit finding M14 (`docs/audit/2026-07-03/PRODUCTION_READINESS.md`
  §4 M14). Three independent, compounding correctness gaps in
  `src/hachi/core/cost.py` (and its one call site in
  `src/hachi/gateway/orchestrator.py`):
  1. Any model id with no entry in `MODEL_PRICING` and no matching registry
     descriptor — most notably an Ollama-served local model
     (`OpenAICompatProvider.models()` returns `[]` for `kind="ollama"`, so no
     descriptor is ever registered for it) — priced at `_DEFAULT_PRICING =
     (1.0, 5.0)` USD/Mtok. A genuinely free local model could therefore
     accumulate a fabricated non-zero "cost" and trip a user's daily spend cap
     on usage that cost nothing.
  2. `record_voice_usage`/`record_background_usage` keyed the daily-spend
     ledger with `datetime.date.today().isoformat()` (host **local** time),
     while `AgentSession._daily_cap_exceeded`/the per-turn recording block in
     `orchestrator.py` independently did the same with their own
     `date.today()` call. Every one of those calls resolves in the host's
     local timezone, not UTC — a user running the gateway outside UTC (e.g.
     IST, UTC+5:30) has spend near local midnight land on the wrong ledger
     date relative to a UTC-anchored day boundary, silently splitting or
     merging a day's total incorrectly depending on which side of the actual
     UTC-day boundary a given call's local wall-clock landed on. **This bug
     reproduced live during this session's own test run**: the pre-existing
     `tests/test_daily_cap_enforcement.py` fixtures wrote spend via
     `datetime.date.today().isoformat()` (local); once the code was fixed to
     read via UTC, those fixtures' writes landed on a different day than the
     UTC-based read the instant local and UTC dates diverged (crossing local
     midnight IST while still on the prior UTC day) — 5 tests failed for
     exactly the reason this bug describes, not because of a mistake in the
     fix.
  3. `orchestrator.py`'s per-turn cost-recording block computed
     `turn_delta = snap.estimated_cost_usd - self._last_recorded_cost_usd` and
     silently clamped/skipped a negative delta (`_turn_cost_usd = max(0.0,
     turn_delta)` for the per-turn log, `if turn_delta > 0:` guarding the
     ledger write) — technically safe (never records negative spend), but a
     negative delta is not supposed to happen (cost is monotonic within a
     tracker's lifetime); it only occurs after some kind of cost-tracker
     regression (e.g. a mid-session reset). Dropping it with **no trace at
     all** hid a real anomaly instead of surfacing it.
- **Root cause:** (1) `_DEFAULT_PRICING` was a leftover "better than crashing"
  guess from before any provider could report per-token cost as genuinely
  unknown — but the module already has a convention for "unknown pricing"
  (`ModelDescriptor.pricing: Pricing | None`, where `None` means "cost
  unknown," honoured correctly by `ModelRegistry.pricing_for_model`); the
  fallback constant contradicted that convention by inventing a non-zero
  number instead of treating "no pricing found anywhere" as "$0 known cost."
  (2) the daily-spend ledger's write side (`cost.py`) and read side
  (`orchestrator.py`'s cap check) each independently called
  `datetime.date.today()`/`date.today()` — nothing enforced that both sides
  resolve the same instant to the same date string, and neither used an
  explicit timezone. (3) the `max(0.0, ...)` / `if turn_delta > 0:` guards
  were written purely to keep the ledger non-negative; nobody added a log
  line for the "this shouldn't happen" branch, so the previous behaviour was
  indistinguishable from "everything is fine."
- **Fix:**
  - `src/hachi/core/cost.py`: `_DEFAULT_PRICING` (~L47) changed from
    `(1.0, 5.0)` to `(0.0, 0.0)` — an unrecognised model id now prices at $0,
    consistent with the existing "pricing unknown → 0 known cost" convention
    elsewhere in this module.
  - Added `today_utc_str()` (~L112) — `datetime.datetime.now(datetime.UTC).date().isoformat()`
    — and rewired `record_voice_usage`/`record_background_usage` (~L222, ~L300)
    to call it instead of local `datetime.date.today().isoformat()`.
    `src/hachi/gateway/orchestrator.py`'s `_daily_cap_exceeded` (~L1535) and the
    per-turn recording block (~L1707) now import and call the same
    `today_utc_str()` helper, so every reader and writer of the daily-spend
    ledger resolves the same UTC day boundary — removed the now-unused
    `from datetime import date as _date` import.
  - Added `clamp_nonnegative_cost_delta(delta_usd, *, context)` (~L132) —
    clamps a negative delta to `0.0` **and** logs a `warning` (previously
    nothing was logged). `orchestrator.py`'s per-turn recording block (~L1699)
    now calls this instead of the bare `max(0.0, turn_delta)`, so a
    tracker-reset-mid-session anomaly is visible in logs instead of invisible.
  - `tests/test_daily_cap_enforcement.py`'s fixtures (`today = datetime.date.today().isoformat()`,
    6 call sites) were updated to `today = today_utc_str()` — they must write
    to the same UTC-keyed bucket the fixed code now reads from.
- **Avoid:** an "unknown/unpriced" fallback must default to the same
  semantics the rest of the pricing system already uses for "cost unknown" —
  never invent a nonzero guessed price for a model nobody actually priced;
  a fabricated bill on a genuinely free/local model can trip a real spend cap.
  Any code that reads AND writes the same daily/periodic ledger must derive
  its date key from one shared, timezone-explicit helper (UTC) — never let
  two independent call sites each call bare `date.today()` and assume they
  agree; this includes TEST FIXTURES that seed a ledger for a test to read
  back — a fixture using local `date.today()` against UTC-keyed production
  code is itself a latent, time-of-day-dependent flake. A cumulative metric
  that "should" be monotonic (cost, tokens) going negative is a signal worth
  logging, not just clamping — silently dropping an impossible value with no
  trace makes a real regression undebuggable later.
- **Locked by:** `tests/test_cost_tracking.py::test_unknown_model_prices_at_zero_not_a_fictional_default`,
  `::test_cost_tracker_records_zero_cost_for_unknown_local_model`,
  `::test_today_utc_str_uses_utc_not_local_date`,
  `::test_record_background_usage_keys_daily_ledger_by_utc_date`,
  `::test_clamp_nonnegative_cost_delta_clamps_and_logs`,
  `::test_clamp_nonnegative_cost_delta_passes_through_positive`; the
  pre-existing `::test_pricing_longest_prefix_match` was updated in place
  (its "unknown model" assertion encoded the exact bug being fixed — the
  fallback value itself, not the lookup mechanism, was under test).
  `tests/test_daily_cap_enforcement.py` (all 13 tests) re-verified green after
  switching its fixtures to `today_utc_str()`.

### BB-57 — `GET /config` echoed unmasked secret refs / raw header values

- **Symptom:** `GET /config` returned the raw `ResolvedConfig.model_dump()` verbatim —
  `provider.api_key_ref` / `api_key_env` (env-var and vault-ref names), `providers.*`
  (same), and raw `headers` values (e.g. a live `Authorization: Bearer ...` token) were
  all echoed back to any caller, and `capabilities.*.secret_ref` leaked vault-key names.
  Inconsistent with the already-masked `GET /config/providers`.
- **Root cause:** `_InProcessGatewayRuntime.get_config()` (`cli/main.py`) dumped
  `ResolvedConfig` straight to JSON with no redaction step, while the sibling
  `GET /config/providers` route went through `provider_admin._mask_provider()`
  (drops `api_key_ref`/`api_key_env` down to a `configured` boolean, replaces header
  *values* with `"***"`). Two endpoints serializing the same underlying secrets with
  two different (one masked, one not) code paths.
- **Avoid:** never add a second serializer for a config/settings object that contains
  secret-shaped fields (`*_ref`, `*_env`, `headers`, `secret_ref`) — route ALL such
  serialization through the one existing masking helper (`provider_admin._mask_provider`
  for providers; extend it, don't bypass it, for new secret-shaped fields).
- **Locked by:** `tests/test_gateway_new_endpoints.py::test_get_config_masks_provider_secrets`,
  `::test_get_config_endpoint_masks_secrets_via_http`.

### BB-58 — OpenAI-compat endpoint ignored the requested `model`; one global rate-limit bucket

- **Symptom:** (a) `POST /v1/chat/completions` accepted any `model` string, never
  validated it, and only echoed it back in the response's `model` field — the actual
  agent turn always ran on the session's already-booted model regardless of what was
  requested, with no error if the model was nonsense. (b) `openai_limiter.allow("openai")`
  used the hardcoded literal `"openai"` as its rate-limit key for every caller, so one
  caller exhausting the sliding-window limit locked out every other caller sharing the
  same gateway instance.
- **Root cause:** (a) `model_name = payload.model or "hachi"` was computed only for
  display, never checked against anything. (b) the rate limiter key was a constant
  instead of a per-caller identity.
- **Avoid:** (a) when a request carries a caller-selectable `model`, validate it against
  the real allowlist (`control_plane.list_all_models()`) before running the turn — reject
  unrecognized models with 400 instead of silently substituting a default; fail open only
  when there is genuinely no allowlist data to check against (handler not wired). (b) any
  shared `RateLimiter` on an externally-reachable route must be keyed by the resolved
  caller identity (the same `caller_hash` from `_require_auth` that H6 uses for session
  ownership), never by a route-name constant — a constant key always degrades to one
  global bucket no matter how many distinct principals call it.
- **Locked by:** `tests/test_gateway_openai_channels.py::test_openai_chat_rejects_unknown_model`,
  `::test_openai_chat_accepts_known_model`, `::test_openai_chat_no_allowlist_data_does_not_reject`,
  `::test_openai_chat_rate_limit_is_keyed_per_caller`.

### BB-60 — TUI Cockpit chat right-rail rendered hardcoded demo plan/approval data (L4)

- **Symptom:** the Textual Cockpit's Chat view right rail always showed a fixed 5-step "plan"
  (search sources / read top 5 articles / …), a fake `skill research-protocol` line, and
  "⚠ 1 approval waiting / browse_web (researcher)" — even in live mode with a real gateway
  session that had zero pending approvals or a completely different tool pending.
- **Root cause:** `ChatView._render_rail()` in `src/hachi/tui/cockpit/views/chat.py` built the
  rail purely from module-level literals (`_PLAN_STEPS`, the skill string, the approval card
  text) and never called `CockpitData.approvals()` — even though that method already existed
  and was already used correctly by `ApprovalsView` (`views/approvals.py`). No gateway data
  model exists for a live per-turn "plan" at all (confirmed: no such field on `ConfigSnapshot`/
  `SessionInfo`; frontend `run.tsx` hides its own Plan section for the identical reason — "live
  mode emits no plan events").
- **Avoid:** any view field that claims to reflect session state must be sourced from a
  `CockpitData` method (which itself falls back to `placeholder_*()` offline) — never a bare
  module-level literal. When no live data model exists for a demo field (e.g. a "plan"), gate
  it behind `CockpitData.is_live` and omit it live rather than fabricating steps; don't invent a
  new backend model to fill the gap without discussing scope first.
- **Locked by:** `tests/test_cockpit_chat_live.py::TestChatViewOffline::test_rail_reflects_real_pending_approval`,
  `::test_rail_shows_no_approvals_when_none_pending`.

### BB-61 — Icon registry silently renders blank `<svg>` for unregistered names (L2)

- **Symptom:** `SpendSection.tsx`'s "Breakdown" heading and `memory.tsx`'s recall button showed
  no icon at all — an empty space where the icon should be, no console warning, no crash.
- **Root cause:** `components/icons/Icon.tsx`'s `registry[name] ?? ''` silently falls back to an
  empty string for any unrecognized `name`, so a typo'd/never-added icon name renders a valid but
  visually blank `<svg>`. Two call sites referenced names that were never added to the registry:
  `'bar-chart'` (SpendSection.tsx:486) and `'loader'` (memory.tsx recall button, was line 546).
- **Fix:** added `'bar-chart'` to the registry (no existing substitute — a real gap). For
  `'loader'`, changed the call site to the existing `'refresh'` icon, matching the loading-state
  convention already used elsewhere (e.g. `conversation.tsx`'s upload button swaps
  `paperclip`→`refresh` while uploading) rather than growing the icon set for a near-duplicate.
- **Avoid:** when adding an `<Icon name="...">` call, verify the name exists in
  `components/icons/Icon.tsx`'s `registry` (or add it) — the component gives no runtime signal
  when it doesn't.
- **Locked by:** `frontend/src/components/icons/Icon.test.tsx` (3 tests: bar-chart renders real
  markup, an unregistered name documents the blank-fallback failure mode, and a sweep over the
  in-use icon names asserts none render blank).

### BB-62 — Work screen "Run now" had zero gateway call (audit L1/gap 1)

- **Symptom:** clicking "Run now" on a routine in the `/work` detail panel only closed the
  panel and showed a toast — unlike Retry/Cancel-run (already wired to durable runs) and
  Approve/Decline (already wired to `/approvals`), `runRoutineNow` in `store.ts` never touched
  the gateway; a scheduled task force-triggered from the UI never actually ran.
- **Root cause:** no gateway route existed to force-fire one schedule outside its own
  `next_run` tick — `Scheduler` only had `tick()` (due-schedules only) and the CLI's
  `hachi schedule run-now` booted a brand-new, throwaway agent session rather than reusing
  the resident session already running the proactive `ScheduleRunner`.
- **Fix:** added `Scheduler.run_now(schedule_id, fire, now=None)` (mirrors `tick()`'s
  fire+`mark_fired` sequencing but bypasses the `due()` gate — an explicit user action always
  fires); `AgentSession.run_schedule_now(schedule_id)` reuses the *live* `ScheduleRunner`'s
  `_fire` callback (same `session.chat(...)` + `deliver_result` as a real tick) and dispatches
  it through the existing background job queue (`func=` closure, mirrors `_enqueue_title_job`)
  so the HTTP request returns immediately; new route `POST /schedules/{id}/run-now` wired
  through `GatewayControlPlane.run_schedule_now`; frontend `runRoutineNowLive()` in
  `work.ts` calls it and `work.tsx`'s `onRunNow` now dispatches on the routine's real id.
- **Investigated, no fix needed (false positive):** audit gap 2 — the scripted demo
  `_runQueue`/`runTick()`/`finishRun()` path in `store.ts` (source of the hardcoded
  `"Summarised 6 PRs…"` result) is unreachable whenever `dataMode === 'live'`; every entry
  point (`openRun`, `runAllowLeash`, `runAlwaysAllow`, `runDenyLeash`, `stopRun`) has an
  explicit live-mode early return before the mock queue is ever seeded, and `dataMode` is a
  fixed constant set once from the build-time `USE_MOCKS` flag (never toggled at runtime).
  Locked with a regression test rather than a code change.
- **Avoid:** when a "force this scheduled/recurring thing to run now" feature is needed,
  reuse the *live* session's existing fire callback/job queue (via `AgentSession`) instead of
  booting a second, throwaway agent inline in the request handler — a synchronous LLM call in
  an HTTP handler blocks the response and double-boots session state. When auditing a mock vs.
  live data path for a "have we shipped fake data to a real user" concern, check the *entry
  points* into the mock branch, not just the code that produces the fake string — one guard at
  the top of the shared trigger function is enough to make every downstream fake-data path
  provably unreachable in live mode.
- **Locked by:** `tests/test_scheduler.py` (4 new `run_now` tests);
  `tests/test_schedule_run_now_gateway.py` (8 tests: `AgentSession.run_schedule_now` +
  `POST /schedules/{id}/run-now`); `frontend/src/lib/data/domains/work.test.ts` (3 tests);
  `frontend/src/lib/store/store.test.ts` (4 tests, gap-2 false-positive lock).

### BB-63 — `HealthRecorder.snapshot()` p50 latency off-by-one on even-length windows (L3)

- **Symptom:** `ProviderHealth.p50_latency_ms` reports a value one rank too high whenever the
  recorder has collected an even number of latency samples (e.g. 4 samples `[100, 200, 300, 400]`
  reported 300.0 instead of the correct 200.0).
- **Root cause:** `p50 = lats[len(lats) // 2]` picks the *upper*-middle element for even `n`
  (e.g. `n=4` -> index 2, the 3rd-smallest value), which is one rank higher than the standard
  nearest-rank percentile (`rank = ceil(p * n)`, 1-indexed -> index 1 for `n=4, p=0.5`). It
  happened to match for odd `n` (median), masking the bug in the one existing test.
- **Avoid:** compute percentiles with the nearest-rank formula
  `lats[max(0, math.ceil(p * n) - 1)]`, not `len // 2` — verify against both an odd- and an
  even-length fixed dataset, never just one parity.
- **Locked by:** `tests/test_resilience.py::TestHealthRecorder::test_p50_latency_even_count_uses_nearest_rank`.

### BB-64 — Tool execution exceptions swallowed with no traceback ever logged

- **Symptom:** when a tool raises an unexpected exception, the LLM receives a compact
  `"Tool Execution Error: <str(exc)>"` message (correct/intended), but there is zero log
  record anywhere with the exception or its traceback — operators have no way to debug the
  real failure beyond the one-line message.
- **Root cause:** `ExecutionLoop._execute_single_tool()`'s `except Exception as exc:` branch
  only stringified `exc` into the tool-result text; it never called `logger.error(...,
  exc_info=exc)` or otherwise preserved the traceback/chain.
- **Avoid:** whenever an except-branch converts an exception into a user/model-facing string,
  also log it with `exc_info=exc` (or re-raise `from exc`) so the traceback isn't silently
  discarded — stringifying for a consumer and logging for debugging are two separate needs.
- **Locked by:** `tests/test_p4_03_parallel_tools.py::test_execute_single_tool_error_preserves_traceback_in_log`.

### BB-65 — `ExecutionLoop._generate_with_retry` dead code left in `loop.py`

- **Symptom:** none at runtime — purely a maintainability/audit finding. A full non-streaming
  retry-wrapper method (`_generate_with_retry`) sat unreferenced in `loop.py`, confusing future
  readers about which retry path `run()` actually uses.
- **Root cause:** P1-2 (`docs/DECISIONS.md`) moved turn generation to streaming
  (`_stream_generate`, its own independent retry loop) but never deleted the older
  non-streaming `_generate_with_retry` it superseded. Grep confirmed zero call sites anywhere
  in the repo (only a stale docstring cross-reference in `_stream_generate` and a comment in
  `tests/test_iteration_limit.py`).
- **Avoid:** when a code path is superseded (new retry/generation strategy, etc.), delete the
  old implementation in the same change — don't leave it "just in case"; grep the whole repo
  for call sites before trusting a function is dead, then remove it and any stale
  cross-reference comments pointing at it.
- **Locked by:** `tests/test_p4_03_parallel_tools.py::test_generate_with_retry_removed_as_dead_code`.

### BB-66 — `AgentSession.shutdown()` never shuts down the per-session tool thread pool

- **Symptom:** every session leaked its `ExecutionLoop._tool_pool` (a 4-worker
  `ThreadPoolExecutor` created per `ExecutionLoop`) for the remaining life of the process —
  no observable crash, just accumulating idle threads across session churn (CLI restarts,
  gateway session ends, `_reload_agents`).
- **Root cause:** `AgentSession.shutdown()` closes the session store, LLM HTTP clients,
  browser controller, memory vault, integrations, and MCP manager, but never called
  `self.loop.shutdown()` — the one method that calls `self._tool_pool.shutdown()`.
- **Avoid:** whenever a component owns a `ThreadPoolExecutor` (or similar resource) with its
  own `shutdown()`, the *owner's* shutdown/teardown method must explicitly delegate to it —
  a resource is never freed "by association" just because it's an attribute of something
  that got cleaned up.
- **Locked by:** `tests/test_agent_session_shutdown.py::TestAgentSessionShutdown::test_shutdown_shuts_down_tool_pool`.

### BB-67 — Budget/token safety ceiling checked only post-hoc; a single call can overshoot it

- **Symptom:** with a hard `_TOKEN_SAFETY_CEILING` (or an opt-in `max_tokens`) close to the
  current cumulative usage, the loop still let ONE MORE full LLM call complete before
  stopping — cumulative usage could land arbitrarily far past the ceiling (bounded only by
  that one call's actual token cost) instead of being refused before it started.
- **Root cause:** `ExecutionLoop._safety_ceiling_exceeded()` compared only the
  already-recorded `cost_tracker.snapshot().total_tokens` against the ceiling. That snapshot
  reflects calls that already completed; there was no estimate of the *upcoming* call's
  worst-case token cost, so a call that would clearly blow the budget was allowed to run
  anyway and only caught one iteration later, after the overrun already happened.
- **Avoid:** when a resource cap is checked in a loop with expensive steps between checks,
  the check must account for the worst-case cost of the *next* step, not just the
  already-recorded total — "check after every step" is not equivalent to "never exceed the
  cap by more than one step's cost" unless the next step's cost is bounded and added in.
- **Locked by:** `tests/test_loop_backstop_cancel.py::test_token_safety_ceiling_is_preflight_not_post_hoc`.

### BB-68 — Streaming LLM calls with unknown/zero usage silently under-count spend

- **Symptom:** when a provider stream never sends a usage chunk (e.g. `stream_usage=False`
  for a strict local server, or a provider that omits `stream_options`), `generate_stream()`
  returns an all-zero `TokenUsage()` with zero visibility — cost/token budget caps
  (`_budget_exceeded`, `_safety_ceiling_exceeded`) silently under-count these calls' real
  spend since there is nothing distinguishing "really used 0 tokens" from "usage unknown."
- **Root cause:** `OpenAICompatibleClient.generate_stream()` initializes
  `final_usage = TokenUsage()` and only overwrites it if a usage chunk arrives; when none
  does, the zero default is returned with no log signal at all.
- **Avoid:** the zero-usage fallback return value must stay (breaking it would ripple into
  every caller that already treats it as a valid `TokenUsage`), but any code path that
  returns a placeholder/unknown value silently must at minimum log a WARNING so the gap is
  operationally visible — never let "unknown" and "verified zero" be indistinguishable in
  both the return value AND the logs.
- **Locked by:** `tests/test_streaming.py::test_openai_stream_no_usage_chunk_logs_warning`
  (companion to the pre-existing `test_openai_stream_no_usage_chunk_returns_zeros`, which
  pins the unchanged zero-usage return value).

### BB-69 — Sub-agents ran with no inherited budget cap, invisibly draining shared spend

- **Symptom:** a delegated sub-agent (`delegate_task`/`delegate_parallel_tasks`) could run
  its full `max_iterations` budget completely unchecked by any cost/token cap even when the
  parent session had `max_session_cost_usd`/`max_session_tokens`/`max_turn_cost_usd`/
  `max_turn_tokens` configured — the parent's `CostTracker` recorded the spend correctly
  (it's shared), but nothing stopped the sub-agent's own loop from continuing to spend
  against it. The parent's own budget checks wouldn't fire until its NEXT turn, by which
  time the sub-agent had already run unchecked.
- **Root cause:** `SubAgentRuntime.run()` constructed its own `ExecutionLoop` passing only
  `llm_client`/`registry`/`memory`/`base_system_prompt`/`on_event`/`cost_tracker` — never
  `max_cost_usd`/`max_tokens`/`max_cost_per_turn`/`max_tokens_per_turn`, so those defaulted
  to `0` (unlimited) regardless of what the parent session had configured.
- **Avoid:** when a child execution unit shares a parent's tracked resource (cost tracker,
  rate limiter, etc.), it must also inherit the parent's ENFORCEMENT of that resource's caps
  by default — sharing the accounting without sharing the limit lets the child spend past
  what the parent would ever allow itself.
- **Locked by:** `tests/test_sub_agent_budget_caps.py` (2 tests: cap propagation stops the
  loop early; `SubAgentRuntime` accepts all 4 cap kwargs). Threaded end-to-end via
  `register_delegation_tools()`/`_make_delegate_task()`/`_make_delegate_parallel_tasks()` in
  `src/hachi/tools/delegation_tools.py`, wired from `boot_agent()`'s existing
  `max_session_cost_usd`/`max_session_tokens`/`max_turn_cost_usd`/`max_turn_tokens` in
  `src/hachi/gateway/orchestrator.py`.

### BB-70 — Sub-agent iteration-limit exhaustion reported as SUCCESS, not TIMEOUT

- **Symptom:** when a delegated sub-agent never finished its task and was cut off by
  `max_iterations`, the orchestrator received `Status: success` in the delegation report —
  indistinguishable from a task that genuinely completed — even though the summary text
  said `"[INCOMPLETE] The task reached the N-iteration limit..."`. The incomplete result was
  also persisted to the memory vault as a confirmed `delegation_result` fact (BB-42's
  sibling: `_enqueue_delegation_persistence` only skips the fact-upsert for non-SUCCESS).
- **Root cause:** `ExecutionLoop.run()` never raises `AgentException` on iteration-limit
  exhaustion (P1-04 made it a graceful `"[INCOMPLETE] ..."` string return, not a raised
  exception — see `tests/test_iteration_limit.py`). `SubAgentRuntime.run()`'s
  `except AgentException as exc:` branch, written to map this case to
  `SubAgentStatus.TIMEOUT`, was therefore unreachable: the `[INCOMPLETE]` string flowed
  straight into the `try` block's normal return path, reported as `SUCCESS`.
- **Avoid:** when a callee changes from "raises on incomplete" to "returns a sentinel string
  on incomplete" (or vice versa), every caller's exception handler for the old contract
  becomes silently dead code — grep for the exception type across the codebase whenever you
  change how a loop signals a non-exceptional-but-not-fully-successful outcome, and update
  callers to check the new signal instead of leaving a now-unreachable except clause.
- **Locked by:**
  `tests/test_sub_agent_iteration_limit_status.py::test_sub_agent_iteration_limit_exhaustion_is_not_success`.

### BB-71 — Router fallback (`_resolve_turn_client`) silently swallowed at DEBUG level

- **Symptom:** when the `ModelRouter` failed to resolve a route (misconfiguration, all
  candidates unavailable, or any unexpected error) and the loop silently fell back to the
  default `llm_client`, the ONLY trace of this was a DEBUG log line — invisible in normal
  operation (INFO/WARNING is the typical default level), so a misrouted/degraded session
  looked identical to a healthy one in the logs.
- **Root cause:** `ExecutionLoop._resolve_turn_client()`'s `except NoRouteError` and
  `except Exception` branches both called `logger.debug(...)` instead of `logger.warning(...)`
  before falling back — an operationally significant event (the model actually serving this
  turn silently changed) was logged at a level nobody watches by default.
- **Avoid:** a fallback path that silently changes user-visible behavior (which model
  answers, which provider is used, etc.) must log at WARNING or above — DEBUG is for
  developer tracing, not for "something degraded and the user should be able to find out."
- **Locked by:** `tests/test_hot_path_routing.py::TestNoRouteErrorFallback` — new
  `test_no_route_error_logs_at_warning_not_debug` and
  `test_arbitrary_router_exception_logs_at_warning_not_debug`.

### BB-72 — `cleanup_completed()` defined 3×, called by zero production code; session data stores grew without bound

- **Symptom:** `jobs.db` accumulated completed/failed/cancelled rows forever;
  `interaction_log`, `idempotency_keys`, and `daily_spend` tables in `sessions.db` had
  no prune path; `CheckpointManager` snapshot files piled up on disk.  A "governed"
  product that never prunes its own audit trail leaks data and disk indefinitely.
- **Root cause:** `SQLiteJobQueue.cleanup_completed()` and `InMemoryJobQueue.cleanup_completed()`
  were defined (and tested) but never wired into any background call path.  The
  `ResidentWorkerThread._run()` loop only called `execute_one()` on idle and had no
  periodic maintenance step.  Session-store tables had no cleanup methods at all;
  `CheckpointManager` had no prune method.
- **Fix:**
  - Added `SessionStore.cleanup_idempotency_keys()`, `cleanup_interaction_log()`,
    and `cleanup_daily_spend()` (`core/session_store.py`).
  - Added `CheckpointManager.prune_old_snapshots()` (`core/checkpoints.py`).
  - Added `RetentionSettings` to `core/settings.py` (maps to `[retention]` in
    `settings.toml`; conservative defaults: jobs 24 h, interaction_log 90 d,
    idempotency keys 48 h, daily_spend 365 d, checkpoints 30 d, sweep every 3600 s).
  - Extended `ResidentWorkerThread.__init__` with optional `session_store`,
    `checkpoint_manager`, and retention-TTL params; added `_maybe_sweep()` / `_do_sweep()`
    called on every idle poll cycle.
  - `boot_agent` in `gateway/orchestrator.py` now passes `session_store`,
    `checkpoint_manager`, and `RetentionSettings` values to `ResidentWorkerThread`.
- **Avoid:** defining a cleanup method without also hooking it into the background
  maintenance loop — the method is effectively dead code and stores grow unbounded.
  When adding a new durable store, add its cleanup method AND wire it into
  `ResidentWorkerThread._do_sweep()` in the same PR.
- **Locked by:**
  `tests/test_retention_sweep.py` — 6 tests covering:
  `test_worker_idle_prunes_old_completed_jobs`,
  `test_inmemory_queue_also_swept`,
  `test_session_store_cleanup_idempotency_keys`,
  `test_session_store_cleanup_interaction_log`,
  `test_session_store_cleanup_daily_spend`,
  `test_checkpoint_manager_prune_old_snapshots`.

### BB-73 — `[observability]` config was dead; `setup_from_config`/`setup_metrics` never called at boot

- **Symptom:** Setting `[observability] enabled=true` in `settings.toml` had no effect —
  tracing and metrics stayed dormant regardless of config. `HACHI_TRACING=true` (env-var path)
  was the only way to activate tracing. `ObservabilitySettings.metrics_enabled=true` was also
  inert. The degraded mode (`enabled=true` + `bus_funnel=false`) had no warning.
- **Root cause:** `boot_agent` called `configure_from_env()` (env-var path only) at line ~2544
  but never read `ObservabilitySettings` from the loaded config dict or called
  `tracing.setup_from_config` / `metrics.setup_metrics`. The config objects existed and the
  functions existed; they were simply never wired together.
- **Fix:** Added `_apply_observability_settings(obs_cfg, events_cfg)` in
  `gateway/orchestrator.py` (called from `boot_agent` immediately after `_events_cfg` is
  parsed — config is already loaded, no double `load_config()` call). The helper calls
  `tracing.setup_from_config` and (conditionally) `metrics.setup_metrics`, and logs a
  `WARNING` when `enabled=true` + `bus_funnel=false` (degraded mode).
- **Avoid:** adding a new config section to `ObservabilitySettings` without also adding its
  activation call in `_apply_observability_settings`. The docstring in `settings.py` now
  correctly references `_apply_observability_settings` so the connection is documented.
- **Locked by:** `tests/test_obs_boot_config.py` — 8 tests covering enabled/disabled/
  metrics/sdk-absent/unknown-exporter/degraded-mode paths.

### BB-74 — `ObservabilitySettings` docstring falsely claimed the gateway called `setup_from_config`

- **Symptom:** `core/settings.py` docstring for `ObservabilitySettings` said "the gateway
  calls `setup_from_config` at boot" — a statement that was false (BB-73). Misleading
  documentation in the type that developers read first when understanding the config.
- **Root cause:** The docstring was written for an intended behavior that was never
  implemented.
- **Fix:** Updated the docstring to correctly reference `_apply_observability_settings`
  in `gateway.orchestrator` as the actual boot wiring point (`core/settings.py`).
- **Avoid:** writing docstrings that describe intended future behavior as present fact —
  especially on config classes where operators rely on the doc to understand the actual effect.
- **Locked by:** BB-73 tests (the behavior the docstring described is now real).

### BB-75 — Cookbook/security-guide documented CLI commands & flags that never existed

- **Symptom:** Copy-pasting from the user-facing docs errored. `hachi run --once "…"` and
  `echo … | hachi run --once` (cookbook §3, the entire "one-shot task from the CLI" recipe)
  → `No such option: --once`; `hachi run --resume <session-id>` → `Got unexpected extra
  argument(s)`; `hachi doctor --check google|voice` → `No such option: --check`;
  `hachi schedule delete <id>` → `No such command 'delete'`; `hachi secret set <name>`
  (og-security.md) → `No such command 'secret'`.
- **Root cause:** Doc drift. One-shot mode was documented but never implemented (no
  `--once`/one-shot path existed anywhere in `cli/`); `--resume` is a no-argument interactive
  picker, not an id-taking flag; `doctor` never had `--check`; the schedule subcommand is
  `remove`, not `delete`; the credential command is `hachi set` / `hachi secrets --set`,
  not `secret set`.
- **Fix:** Implemented `hachi run --once` (+ bare positional `hachi run "<task>"`, + piped
  stdin) via `_run_once()` in `cli/main.py` — boots an agent, runs one `chat` turn, raw-echoes
  ONLY the reply to stdout (so it pipes byte-clean), then exits. `--resume`/`--continue` now
  reject a stray positional with a guided error. Corrected the four stale doc references in
  `docs/guides/cookbook.md` and `docs/reference/og-security.md`.
- **Avoid:** documenting a CLI command/flag before it is registered — and renaming/removing
  one without grepping the reference docs. `--once` reads stdin only when piped
  (`not sys.stdin.isatty()`) so it never blocks on a TTY.
- **Locked by:** `tests/test_cli_run_once.py` (7 tests — one-shot behavior) and
  `tests/test_docs_cli_reference.py` (introspects the Typer app; asserts every `hachi <cmd>`
  in the reference docs resolves to a real command/subcommand).

### BB-76 — `<button>` with no base `background` rule renders the UA ButtonFace grey

- **Symptom:** The conversation-header session picker and the composer mic button rendered
  as cold `#F0F0F0` grey chips on Hachi's warm bone canvas (`#FBF7EF`) — visibly unthemed,
  as if they belonged to a different app. Reported by the user as "not blending to the theme".
- **Root cause:** Both used the codebase idiom `background: open ? 'var(--hachi-cream)' : undefined`
  in an inline `style`. React **omits** a property whose value is `undefined`, so the rest
  state declared no background at all and fell through to the User-Agent stylesheet, which
  paints `<button>` with `ButtonFace` (`#F0F0F0` on Windows/Chrome). The idiom itself is
  correct and deliberate — an inline background would out-specify the `.class:hover` rule and
  silently kill hover — but it *requires* the class to supply the rest-state background.
  `.hachi-session-title-btn` and `.hachi-composer-voice-btn` defined only `:hover`.
  `.hachi-rating-btn` is the correct precedent: it pairs `:hover` with `background: var(--hachi-canvas)`.
- **Fix:** Added the missing base `background` rules in `frontend/src/styles/interactive.css`.
  While there, gave the session-picker trigger a real state model — bare at rest (it reads as
  the page heading), `--hachi-cream` on hover, and on open it takes the dropdown panel's own
  skin (`--hachi-surface` + `--hachi-line` hairline) so trigger and panel read as one object.
  The open state is keyed off `[aria-expanded='true']` so the visual and a11y states cannot
  drift; it is ordered **after** `:hover` because the two have equal specificity (0,2,0).
- **Avoid:** never let an interactive `<button>` end up with zero `background` declarations.
  If the `.tsx` hands the rest state to CSS via `: undefined`, the class MUST declare a base
  `background` (use `transparent` for ghost buttons). Corollary: do **not** "fix" this by
  putting a literal background in the inline style — inline beats `:hover` and kills it.
- **Third instance:** `.hachi-leash-always-btn` (`routes/approvals.tsx`,
  `components/brand/LeashPrompt.tsx`) had the same defect — reproduced live by injecting a
  pending approval into the store, which rendered a raw grey "Always allow …" button beside
  the outlined Decline and solid-red Allow once. Fixed with the same base rule.
- **Scope boundary:** all three *reproduced* instances are fixed. A wider sweep found ~26
  further classes in `interactive.css` that define a `:hover` background with no base rule;
  most are fine because their `.tsx` sets an inline background — but that is the mirror-image
  hazard (an inline background out-specifies `:hover`, so those hover effects may be silently
  dead). Not audited here.
- **Locked by:** `frontend/tests/interactive-css.test.ts` — asserts the guarded classes
  declare a base background, plus two anti-vacuity guards (the stylesheet actually loaded;
  the parser can still detect the defect). Verified by reverting the CSS fix: the guard
  fails, then passes again once restored.
- **Testing gotcha (cost two gate failures):** that test lives outside `src/` deliberately.
  `frontend/tsconfig.json` scopes `include` to `src` and the project has **no `@types/node`**,
  so importing `node:fs` from a test under `src/` passes Vitest but breaks `tsc --noEmit`
  — failing both the `frontend typecheck` and `frontend build` gate steps. The obvious
  browser-side workaround is a trap: **Vitest stubs CSS imports to `''` even with `?raw`**
  (default `css: false`), so the file reads empty and every assertion passes *vacuously*.
  Node-side tests that read files belong in `frontend/tests/`, and any test that loads a
  fixture should assert the fixture is non-empty.

### BB-77 — Live approvals auto-declined in ~1 second after appearing

- **Symptom:** Any pending approval visible on Home or The leash vanished within one second and was logged as "timed out — Hachi stayed put", even when the gateway sent no expiry.
- **Root cause:** `adaptApproval()` (approvals.ts) correctly sets `expiresAt: 0` as a sentinel for "no server expiry." But `tickApprovals()` (store.ts) tested `a.expiresAt <= now`, and `0 <= Date.now()` is always true — so every live approval expired immediately. The shared 1-second interval in `useCountdown.ts` called `tickApprovals()` whenever any approval card was mounted.
- **Fix:** (1) `tickApprovals` returns early when `dataMode === 'live'` (belt-and-braces). (2) The expiry filter now guards `a.expiresAt > 0 && a.expiresAt <= now`; the keep-filter mirrors it. (3) The `ApprovalCard` in `routes/index.tsx` renders "no auto-decline — Hachi waits until you decide" when `expiresAt <= 0`, matching the existing copy in `routes/approvals.tsx`.
- **Avoid:** never use `expiresAt <= now` without first guarding `expiresAt > 0`; zero is the "no expiry" sentinel throughout the approval domain.
- **Locked by:** `frontend/src/lib/store/store.test.ts` — "tickApprovals — live sentinel and mock expiry (BB-77)".

### BB-78 — Permission removals and operator-tool toggles silently reverted on reload

- **Symptom:** Removing an allowed folder/command in Settings → Permissions, or toggling an operator tool, took effect in the UI but reverted to the previous state on the next hard reload. The Safety section header promised "Narrow it any time."
- **Root cause:** `removeFolder`, `removeCommand`, and `toggleOpTool` in `store.ts` only mutated local Zustand state — no gateway call was made. In contrast, the add paths (`addFolderLive`, `addCommandLive`) already called POST routes. The DELETE routes `DELETE /config/permissions/folders` and `DELETE /config/permissions/commands` did not exist in the backend.
- **Fix:** Added `remove_allowed_folder` / `remove_allowed_command` methods to `HachiSupportService` (cli/main.py), wired them through `GatewayControlPlane` (control_plane.py), and exposed them as `DELETE /config/permissions/folders` and `/commands` in fastapi_app.py. Added `removeFolderLive`, `removeCommandLive`, `toggleOpToolLive` to config.ts (store only mutates on 2xx; shows blocked toast on failure). Rewired PermissionsSection.tsx to use the Live functions. Regenerated OpenAPI schema (BB-4).
- **Avoid:** never wire a destructive UI action to a bare store mutation; always mirror the add-path pattern (Live function → gateway → store-on-2xx).
- **Locked by:** `frontend/src/lib/data/domains/config.test.ts` — "removeFolderLive/removeCommandLive/toggleOpToolLive — persist to gateway (BB-78)"; `tests/test_g1_wave1.py` — "test_delete_allowed_folder/command_*".

### BB-79 — "Browse the kennel → Add" dead-ends; consent dialog never appears

- **Symptom:** Clicking Add on any skill in the kennel browse panel had no effect — the browse panel stayed open and the consent dialog never appeared.
- **Root cause:** `askKennel` in store.ts set `kennelConsent: item` but did not clear `kennelBrowseOpen`. `KennelConsent.tsx` renders the browse panel when `kennelBrowseOpen` is true, so it always rendered the browse panel, never the consent view — even when a pending consent item was in state.
- **Fix:** `askKennel: (item) => set({ kennelConsent: item, kennelBrowseOpen: false })` — consent and browse are mutually exclusive; clearing the browse flag is the correct invariant.
- **Avoid:** consent and browse mode are mutually exclusive — setting one must always clear the other.
- **Locked by:** `frontend/src/components/overlays/KennelConsent.test.tsx` — "askKennel — closes browse panel before showing consent (BB-79)".

### BB-80 — Run terminal scroll-follow was inert; caret had no class (reduced-motion rule never fired)

- **Symptom:** The run output terminal did not auto-scroll to new lines during a live run. Additionally, the `[class*="hachi-caret"]` reduced-motion CSS rule never applied because the caret span had no class.
- **Root cause:** Two layered problems: (1) `run.tsx` wrapped `<Terminal>` in a div with `overflow: auto` and also passed `style={{ display: 'block', minHeight: '100%' }}` — this collapsed Terminal's internal flex layout so the output region never overflowed and the rAF pin was a no-op. (2) The blinking caret span was anonymous (`aria-hidden="true"` only), so `[class*="hachi-caret"]` in the `<style>` block never matched.
- **Fix:** Removed `overflow: auto` from the run wrapper div (it now passes `display: flex` to let Terminal own scrolling). Terminal's style changed to `height: 100%`. Added `className="hachi-caret"` to the caret span. Added `streaming?: boolean` prop (default true): when false, hides the caret and shows "run ended" copy. Added state-based following flag + "↓ Latest" pill. Added `role="log" aria-live="polite"` to the output region.
- **Avoid:** the outer scroll container must never be a wrapper that also sizes its child; Terminal must own its own overflow. And any CSS rule targeting `[class*="x"]` is dead without the class.
- **Locked by:** `frontend/src/components/ui/Terminal.test.tsx` — "Terminal — streaming prop controls caret (BB-80)".

### BB-81 — Authoring "Written to …" badge shown for the wrong target after switching

- **Symptom:** Apply a patch to `brain.og`, then click `settings.toml` — the "Written to ~/.hachi/skills/settings.toml · skill re-indexed" line and the Revert button appeared, claiming a patch was applied to a file that was never touched.
- **Root cause:** `authApplied` in the store was a global `boolean`. Switching `authSel` did not reset it, so any previously-applied target would show the badge on every subsequent target.
- **Fix:** Replaced `authApplied: boolean` with `authAppliedTarget: string | null`. `applyPatch()` sets it to the current `authSel`; `revertPatch()` clears it to `null`. In `authoring.tsx`, `const authApplied = authAppliedTarget === authSel` — a different selected target simply doesn't match, no reset needed.
- **Avoid:** never use a global boolean flag for "this action was performed" when the action is scoped to a selected item; track the item identity instead.
- **Locked by:** `frontend/src/lib/store/store.test.ts` — "applyPatch — per-target applied state (BB-81)".

### BB-82 — Setup wizard let users advance past the key step with no key

- **Symptom:** Clicking "Next" on the API-key step (step 1) advanced the wizard even when no key was entered and none was pre-configured server-side. "Skip" called `closeSetup()` (closing the wizard) rather than advancing to the next step. Re-opening the wizard always reset to step 0, losing any partial progress. The wizard never set a `completed` flag, so `openSetup()` had no way to distinguish "resume in progress" from "start over after finish."
- **Root cause:** `handleNext` called `setupNext()` unconditionally; the primary button had no `disabled` prop; `skipLabel` onClick was `closeSetup`; `openSetup()` always wrote `step: 0`; no `completed` field existed.
- **Fix:** Added `keyOk = setup.keyAlreadySet || keyValidation.status === 'valid'`. Primary button is disabled when `!keyOk` on KEY_STEP. Skip calls `setupNext()`. Added "Set up later" (calls `closeSetup()`) visible only on KEY_STEP. Skip hidden on KEY_STEP unless `keyAlreadySet`. Added `completed: boolean` to setup state; `setupNext()` sets it when finishing step ≥ 4. `openSetup()` preserves step when `step > 0 && !completed`.
- **Avoid:** never gate wizard advancement in the handler alone — the button itself must be disabled so the constraint is visible in the UI and testable without firing click events.
- **Locked by:** `frontend/src/components/overlays/SetupWizard.test.tsx` — "SetupWizard — key step gate (BB-82)".

### BB-83 — Toast queue capped at 1; arm-then-confirm had no cooldown protection

- **Symptom (T8a):** Only one toast could exist at a time; a 4th toast pushed the 1st out silently. `dismissToast()` was a bare `set({ toast: null })` with no id parameter — no way to dismiss a specific toast. The live region was inside the conditional `<Toast>` element, not the always-mounted wrapper, so screen readers missed announcements. Action button support and correct auto-dismiss per tone were absent.
- **Symptom (T8b):** "Always allow" on both approval cards had hand-rolled arm logic with no cooldown — a fast double-click within the same tick could confirm instantly. The arm pattern was duplicated across `approvals.tsx` and `LeashPrompt.tsx`.
- **Root cause (T8a):** Single `toast: Toast | null` state; `ToastHost` role on conditional child. **(T8b):** No shared primitive; `handleAlwaysAllowClick` checked armed state but not elapsed time since arming.
- **Fix:** `types.ts`: added `action?` and `ToastItem extends Toast { id: string }`. Store: `toasts: ToastItem[]` (cap 3, drop oldest); `showToast(msg, tone?, action?)` appends; `dismissToast(id)` by id. `ToastHost`: always-mounted wrapper gets `role="status" aria-live="polite"`; renders stack; timer on oldest toast. New `ConfirmButton` component: arm → cooldown check → confirm; auto-disarm after armWindowMs or blur. Applied to: SessionPicker (session delete), memory (fact delete + undo toast), integrations (Clear/Disconnect), LeashPrompt + approvals (Always allow).
- **Avoid:** never use a single-slot for toasts; never do arm-then-confirm without a cooldown delay; always put the live region on a permanently-mounted element.
- **Locked by:** `frontend/src/lib/store/store.test.ts` — "toast queue — append / cap / dismiss (BB-83)"; `frontend/src/components/ui/ConfirmButton.test.tsx` — "ConfirmButton — arm-then-confirm (BB-83)".

### BB-84 — Tool-protocol injection text rendered verbatim in chat history and Review

- **Symptom:** Conversation history re-loaded from the gateway showed raw injection text beneath the user's message (`[Attached image: hachi logo.png]\n(Use the view_image tool with path: …)`). Review screen rendered `##` headings and `**bold**` as literal characters.
- **Root cause:** `reconstructMessagesFromEvents` (chat.ts) and `loadSessionMessages` set the user message `text` directly from `payload.user_text` / `m.content`, which the gateway stores with the full prompt-injection block. `review.tsx` rendered the Hachi `agentOutput` field with `{ix.agentOutput}` (plain text node) and the user input with `whiteSpace: pre-wrap`.
- **Fix:** New exported pure function `extractInjectedAttachments(text)` strips `\n?[Attached (image|file): name]\n(Use the \w+ tool with path: …)` blocks (multiline, global), returns `{ text, attachments: { name }[] }`. Applied to `role === 'you'` in both adapter paths. Extracted attachments merged into the message's `attachments` array (displayed as chips by `MessageItem`). In `review.tsx`: user cell applies `extractInjectedAttachments`, Hachi cell renders via `<Markdown>` (imported from `../components/ui/Markdown`).
- **Avoid:** never pipe raw `user_text` from the gateway directly to a display layer — the gateway concatenates the prompt-injection preamble into the same field.
- **Locked by:** `frontend/src/lib/data/domains/chat.test.ts` — "extractInjectedAttachments — strip tool-protocol injection (BB-84)".

### BB-85 — Two approval card implementations disagreed: low/medium risk showed filled "Allow once" on approvals screen

- **Symptom:** `routes/index.tsx:ApprovalCard` and `routes/approvals.tsx:ApprovalCard` were independent implementations. The approvals screen made "Allow once" a solid filled brand-red button for low AND medium risk — the visually dominant choice was the one that bypasses the leash, violating Nielsen error-prevention. `LeashPrompt` (the design-correct card) was used in conversation/run but not on the dedicated approval screens.
- **Root cause:** Two separate `ApprovalCard` implementations instead of one shared component. `routes/approvals.tsx` rendered `background: var(--hachi-brand)` on Allow once for non-high-risk approvals.
- **Fix:** Extended `LeashPrompt` props (`riskPill`, `touches`, `countdown: string|null`, `countdownBar`, `headerTestId`, `actionsTestId`). Button weighting now in ONE place: low/medium both outlined, high = Decline solid trust. Deleted `fmtCountdown` from `LeashPrompt` (uses `useCountdown().label` from caller). Both `ApprovalCard` functions replaced with `<LeashPrompt />` composition. `hachi-leash-decline-high-btn` class added to `interactive.css` (resting background in class per BB-76).
- **Avoid:** never duplicate approval-card markup; the design contract for equal-weight decisions lives ONCE in `LeashPrompt`.
- **Locked by:** `frontend/src/components/brand/LeashPrompt.test.tsx` — "LeashPrompt — button weighting (T10 / BB-85)"; `frontend/src/routes/-index.ApprovalCard.test.tsx` — "Home ApprovalCard — action row layout" (structural invariant still holds via LeashPrompt's layout).

### BB-86 — Overflow approval count was non-interactive text with wrong terminology

- **Symptom:** When more than 3 approvals were pending, `routes/index.tsx` showed a `<div>` reading "scroll down in the Leash tab to see all" — `cursor: default`, no keyboard access, terminology "Leash tab" instead of the correct route `/approvals`. Impossible to navigate from keyboard.
- **Root cause:** The overflow count was rendered as a plain div with `cursor: default` instead of a navigable element. The copy referenced a "tab" that doesn't exist in the current navigation model.
- **Fix:** Replaced with a `<button className="hachi-more-approvals-link">` that calls `navigate({ to: '/approvals' })` on click. Label updated to `+ N more waiting on the leash →`. Resting `background: transparent` placed in the CSS class (not inline) per BB-76 so `:hover { text-decoration: underline }` can override it.
- **Avoid:** never render navigation-intent content as a `<div>` with `cursor: default`; never reference navigation locations by informal names.
- **Locked by:** `frontend/tests/interactive-css.test.ts` — "overflow link hover treatment (BB-86)" verifies the `:hover` underline rule exists in `interactive.css`.

### BB-87 — Audit timestamps fabricated at fetch time; history "when" frozen as "just now"

- **Symptom 1:** The operator audit log showed a fabricated timestamp (`new Date().toLocaleTimeString()` called at fetch time) for every historical gateway-sourced audit entry — every page reload re-dated the entire table to the current time.
- **Symptom 2:** Past-decisions history rows always showed "just now" regardless of how long ago the decision was made.
- **Root cause 1:** `frontend/src/lib/data/domains/approvals.ts` adapter called `new Date().toLocaleTimeString()` when adapting `PermissionAuditSnapshot` entries, which carry no timestamp field.
- **Root cause 2:** `store.ts:resolveApproval` and `tickApprovals` wrote the literal string `"just now"` into `HistoryItem.when` with no real timestamp alongside.
- **Fix:**
  - `approvals.ts`: gateway audit entries now get `ts: "—"` (no timestamp from server → render nothing).
  - `types.ts`: added `ts?: number` to `HistoryItem`.
  - `store.ts`: both call sites now write `ts: Date.now()` alongside `when: "just now"`.
  - `approvals.tsx`: history row renders `fmtRelative(item.ts)` when `ts` is present, else falls back to `item.when`.
  - Created `frontend/src/lib/ui/format.ts` with `fmtUSD`, `fmtTokens`, `fmtRelative`, `fmtDate`.
- **Avoid:** never call `new Date()` when adapting server data that has no timestamp field; always store a real `ts: Date.now()` epoch when creating in-memory time-sensitive records.
- **Locked by:** `frontend/src/lib/ui/format.test.ts` — covers all four format functions including the relative-time cases.

### BB-88 — Markdown parser infinite loop on pipe-delimited lines that lack a separator row

- **Symptom:** Vitest worker hung indefinitely (no timeout, no output) when a test rendered a `<Markdown>` component whose source contained a `|…|`-delimited line NOT followed by a `---`-style separator row.
- **Root cause:** `parseBlocks` in `Markdown.tsx` included `!TABLE_ROW_RE.test(rawLines[i])` in the paragraph-accumulation stop condition. A pipe-delimited line that failed the table-header+separator check fell through to the paragraph branch; the inner `while` exited immediately (stop condition matched line 0), produced an empty `lines` array (no block pushed), and `i` was never incremented — infinite spin.
- **Fix:** Removed `!TABLE_ROW_RE.test(rawLines[i])` from the paragraph stop condition. Pipe-delimited lines that don't form a valid table are now absorbed into paragraphs as plain text (the table-detection check at the top of the outer loop handles the valid-table path before the paragraph fallback is ever reached).
- **Avoid:** never add a line pattern to the paragraph stop condition unless that same pattern is also handled by an earlier branch that ALWAYS increments `i`; any stop condition that causes an empty `lines` result without advancing `i` produces an infinite outer loop.
- **Locked by:** `frontend/src/components/ui/Markdown.test.tsx` — "does not produce a table without a separator row" passes the failing input directly to the component.

### BB-89 — Sub-agent blocklist named a nonexistent tool; schedule mutators walked free

- **Symptom:** A sub-agent whose role `.og` declares no `::meta tools:` allowlist could call
  `create_schedule` and `delete_schedule`, creating or deleting the user's durable, recurring,
  autonomously-firing schedules. `BLOCKED_TOOL_NAMES` is documented as "the security boundary".
- **Root cause:** Constant-name vs tool-name mismatch. `schedule_tools.py` defines
  `SCHEDULE_TASK_SCHEMA` whose registered `"name"` is `create_schedule`, and
  `CANCEL_SCHEDULE_SCHEMA` whose name is `delete_schedule`. The blocklist was written from the
  constant names, so it blocked the string `"schedule_task"` (a no-op — no such tool) plus the two
  harmless read/alias tools, and missed both mutators. Nothing validated the strings against real
  tool names; `test_security_hardening.py` asserted `"schedule_task" in BLOCKED_TOOL_NAMES`,
  locking the bug in as the contract.
- **Fix:** Replaced `schedule_task` with `create_schedule`, added `delete_schedule`, declared the
  two genuinely-unregistered `core_*_agent_profile` entries in `_RESERVED_BLOCKED_NAMES`, and
  added an integrity test that AST-scans the source tree for tool-schema `name` literals and
  fails on any blocked name that matches none of them.
- **Avoid:** A tool name is the `"name"` field of the registered schema — never the Python
  constant, never the handler function's identifier. Any hand-maintained list of tool names needs
  a test that resolves every entry against a real schema, or it will rot silently and fail open.
- **Locked by:** `tests/test_delegation_blocklist_integrity.py` —
  `test_schedule_mutators_are_blocked_from_sub_agents`,
  `test_every_blocked_name_is_a_real_tool_name`

### BB-92 — `settings.example.toml` was invalid TOML; copying it silently discarded the user's whole config

- **Symptom:** a user copies `settings.example.toml` to `settings.toml` as the file's own
  header instructs, edits it, and *nothing* takes effect — no crash, no non-zero exit.
- **Root cause:** 11 lines used `key = null`. TOML has no null literal, so `tomllib` aborted
  at line 122. `_read_toml(primary=True)` (`core/config_loader.py:105`) catches
  `TOMLDecodeError`, logs `logger.error`, and returns `{}` — the process continues on
  built-in defaults with the entire file ignored. Nothing in CI had ever parsed the example.
- **Fix:** commented out the 11 assignments (matching the pre-existing line-47 precedent)
  rather than deleting them or substituting `0`/`""` — `None` means "no cap" and `0` is a
  live cap, so a value substitution would document behaviour Hachi does not have.
- **Avoid:** any file the docs tell a user to copy must be parsed by a test. And when
  asserting "documented keys are real keys", walk `model_fields` by hand — `ResolvedConfig`
  and all 20 nested section models set `extra="ignore"`, so `model_validate()` accepts
  anything and asserts nothing.
- **Locked by:** `tests/test_config_loader.py` — `test_settings_example_toml_parses`,
  `test_settings_example_toml_keys_are_known_config_fields`

### BB-93 — Routing validation never returned a cost warning (arity error swallowed since inception)

- **Symptom:** `POST` to the routing validate/apply endpoint always returned
  `warnings: []`, even for a config that pins `reasoning` to a model ~16× the price of
  `default_model` — exactly the BB-30 trap the guard was written to catch.
- **Root cause:** `core/routing/validate.py` called
  `policy.costly_capability_warnings()` with no arguments, but the method requires a
  positional `registry` (`core/routing/policy.py:56`). Every call raised `TypeError`
  into a bare `except Exception: pass`, so the warnings list was unconditionally empty.
  `registry` was already in scope — it is `validate_routing`'s first parameter.
  `gateway/orchestrator.py` called the same method correctly, so boot-time warnings
  worked and masked the gap.
- **Fix:** passed `registry` at the call site and replaced the bare swallow with
  `logger.warning(..., exc_info=True)`.
- **Avoid:** never wrap a *single* call in `except Exception: pass` — an arity error is
  indistinguishable from "no warnings" and survives forever. A test that constructs the
  result dataclass by hand does not test the function that builds it.
- **Locked by:** `tests/test_routing_validate.py` —
  `test_costly_capability_override_produces_a_warning`

### BB-91 — boot_agent defined emit_runtime_event twice; the governance path kept the pre-bus copy

- **Symptom:** LATENT — no confirmed user-visible symptom. With `[events] bus_funnel=true` (the
  default), every subsystem published runtime events through the `EventBus` **except** the
  permission/approval subsystem, whose emitter bypassed the bus entirely and therefore could never
  reach `ObservabilitySink` or any other bus subscriber. Two facts bound the present-day impact to
  ~zero and both are worth remembering: (a) `ObservabilitySink._SPAN_EVENTS` contains no
  `permission.*` name, so the sink ignored these events anyway — a second, independent gap; and
  (b) all six affected call sites sit in branches predicated on `on_permission_request`, which no
  production caller passes (see BB-90/T-01). The defect would have gone live the moment T-01
  made `RegistryApprover` reachable.
- **Root cause:** `gateway/orchestrator.py` `boot_agent` defined `emit_runtime_event` (and
  `relay_event`) once unconditionally, then **again** ~145 lines later inside
  `if _events_cfg.bus_funnel:` with `# noqa: F811`. Rebinding a local name creates a new function
  object; it does not update references already passed by value. Six sites passed the emitter by
  value **before** the rebind — `InteractivePermissionGate(event_emitter=…)` ×2, `HandlerApprover`
  ×2, `RegistryApprover` ×2 — and each stores it as `self._emit`. `AgentSession(...)` showed the
  split in one call expression: the positional `runtime_gate` held the old emitter while the
  `event_emitter=` keyword on the next line held the new one. The block's own comment claimed the
  funnel was "byte-identical by construction" while registering a third subscriber the direct path
  never had.
- **Fix:** One definition each, reading the `_event_bus` closure cell at call time (`if
  _event_bus is not None: publish(...) else: <direct path>`), and both redefinitions deleted along
  with their `# noqa: F811`. The cell is declared before the closures, so a reference captured 100
  lines earlier follows the bus once it is assigned. Stale comments corrected in
  `gateway/orchestrator.py` and `core/settings.py`.
- **Avoid:** Never re-`def` a name that has already been passed as an argument — branch inside the
  single definition on a closure cell instead. Treat `# noqa: F811` on a second definition of the
  same callable as a defect, not a style waiver.
- **Locked by:** `tests/test_emit_runtime_event_single_definition.py` —
  `test_boot_agent_defines_each_event_closure_once`,
  `test_late_bound_bus_reaches_a_reference_captured_before_assignment`

### BB-90 — `RegistryApprover` unreachable: every session silently got `PolicyApprover`

- **Symptom:** `GET /approvals` was permanently `{"pending": []}` and the Leash screen was structurally empty. Under `guided`/`sandbox` every gated action was denied instantly with "Connect the TUI or web companion to approve" — while the companion was connected. Under `unrestricted` every gated action was auto-approved with no request and no audit row.
- **Root cause:** Two compounding defects in `boot_agent` (`gateway/orchestrator.py`). (1) The `RegistryApprover` branch was `elif isinstance(runtime_gate, InteractivePermissionGate)`, but `runtime_gate` only becomes an `InteractivePermissionGate` when `on_permission_request` is truthy — and reaching the `elif` requires it to be `None`. Logically unreachable, and duplicated verbatim in the tier-reconcile block. (2) `on_permission_request` was passed by zero call sites in `src/`, so all five boot paths fell through to `PolicyApprover`. `permission.requested` therefore had no live emitter and `PendingApprovalRegistry` was never populated.
- **Fix:** Extracted `select_action_approver(...)` as the single selection rule and added an explicit keyword-only `approval_surface` parameter to `boot_agent`; `_InProcessGatewayRuntime._boot_session_agent` (`cli/main.py`) passes `"gateway"`. Both former `if/elif/else` sites now call the helper. Two test doubles (`tests/test_cold_start_resume.py`, `tests/test_gateway_runtime_validation.py`) that hard-coded `boot_agent`'s old positional/keyword signature needed the new keyword-only parameter added to stay in sync — their assertions were untouched.
- **Avoid:** never branch on a value derived from the same condition you already
  branched on — and never let a composition root choose a strategy on a signal no
  caller supplies. If a boot path needs to pick a behaviour, pass it explicitly and
  pin the choice with a test.
- **Locked by:** `tests/test_approver_selection.py` — `test_gateway_boot_path_requests_registry_approver`,
  `test_select_action_approver_maps_gateway_surface_to_registry_approver`,
  `test_handler_still_wins_over_surface`

### BB-94 — One SSE viewer disconnecting cancelled the whole session's turn

- **Symptom:** closing a browser tab, a proxy blip, or a second viewer navigating away stopped
  generation mid-turn for every other viewer of the same session. The transcript ended
  mid-sentence with nothing in the audit trail explaining why.
- **Root cause:** in `stream_session_events` (`gateway/fastapi_app.py`), the disconnect branch
  called `control_plane.interrupt(session_id)`. `request.is_disconnected()` is CONNECTION-local;
  `session_id` is a path parameter shared by every viewer, so a connection-scoped trigger fired a
  session-scoped cancel. Added as "P1-4" so the Stop button would kill the worker thread — but that
  requirement was already met by the dedicated, ownership-checked
  `POST /sessions/{session_id}/interrupt` route.
- **Fix:** dropped the `interrupt()` call from the disconnect branch; the `break` remains so the
  abandoned stream still closes. Turn cancellation is now an explicit user action only.
- **Avoid:** never trigger session-scoped side effects from a connection-scoped signal. Before
  calling `interrupt`/`close`/`cancel` with a `session_id`, ask whether the thing that triggered it
  is owned by ONE connection or by the session. (The mirror site in `_openai_event_stream` is
  correct precisely because its session is minted per request.) Separately: neither
  `starlette.testclient.TestClient` nor `httpx.ASGITransport` can exercise a mid-stream
  `request.is_disconnected()` — both run the whole ASGI app call to completion inside a blocking
  call before handing back a response, and their `receive()` only ever reports `http.disconnect`
  *after* the body is already complete. A test built on early-exiting a `TestClient.stream()`
  context manager passes whether or not the code under test is correct (verified: it took ~36s and
  exited via `max_idle_cycles`, never the disconnect branch). Drive the route coroutine directly
  with a hand-built `Request`/`receive()` instead when a disconnect must be observed mid-stream.
- **Locked by:** `tests/test_gateway_sse.py` — `test_stream_disconnect_does_not_interrupt_other_viewers`

### BB-96 — `file.extracted` was published into a throwaway EventBus and reached nobody

- **Symptom:** the `file.extracted` runtime event never arrived — no SSE delivery, no
  subscriber invocation — after an attachment finished text extraction. No error was
  logged, because the publish itself "succeeded".
- **Root cause:** `_emit_file_extracted` (`core/runtime/job_handlers.py`) did
  `from hachi.gateway.orchestrator import _shared` inside a try/except. `_shared` is not
  a module-level name in `orchestrator.py` (AST-verified: the only module-level assigns
  are `logger` and `_SWEEP_INTERVAL_SECONDS`) — it is a local inside `boot_agent`. The
  `ImportError` was swallowed, `bus` stayed `None`, and the code then constructed a
  fresh subscriber-less `EventBus()` and published into that. The correct accessor,
  `ProcessSharedServices.instance()`, already existed and was already used by
  `core/cost.py` and `gateway/fastapi_app.py`. A `# type: ignore[attr-defined]` on the
  bad import had silenced the type checker.
- **Fix:** switched to `ProcessSharedServices.instance().event_bus`, deleted the
  throwaway `EventBus()` fallback (a bus with no subscribers is a dropped event, not a
  degraded one), and turned every swallow into a `logger.warning` — including an
  explicit warning when there is no shared bus at all. `pyproject.toml`'s import-linter
  `ignore_imports` seam list was updated to match: the now-dead
  `hachi.core.runtime.job_handlers -> hachi.gateway.orchestrator` entry (import-linter
  fails on a stale, unmatched ignore) was replaced with
  `hachi.core.runtime.job_handlers -> hachi.gateway.shared_services`, next to the
  identical `core.cost` / `core.runtime.resume` seams.
- **Avoid:** never import a private name from another module's *function* scope and
  assume it is a module global — grep for a module-level binding first. And never
  "degrade" by constructing an empty sink: a publish into a subscriber-less bus is
  indistinguishable from success at the call site. When a `# type: ignore[attr-defined]`
  is needed to make an import type-check, the import is probably wrong.
- **Locked by:** `tests/test_file_extracted_event.py` —
  `test_file_extracted_reaches_the_shared_bus`,
  `test_file_extracted_warns_when_no_shared_bus`

### BB-97 — Client-fabricated rows in the permission audit log (BB-87 recurrence)

- **Symptom:** Allowing or declining an approval unshifted a locally-invented row — with a `new Date().toLocaleTimeString()` timestamp — into the same `auditLog` slice that `loadApprovals()` hydrates from `GET /permissions/audit`. Because real gateway rows deliberately render `ts: "—"` (they carry no server timestamp), the fabricated row was the ONLY row in the table with a timestamp, i.e. it looked more authoritative than the real ones. `declineAll` injected one per approval.
- **Root cause:** `resolveApproval` in `frontend/src/lib/store/store.ts` did three writes in one `set()`: filter `approvals` (correct), unshift `history` (correct — client-side "past decisions", no server counterpart), and unshift `auditLog` (wrong — server truth). BB-87 had already removed the fetch-time fabrication in the adapter (`approvals.ts` now renders the `ts: "—"` sentinel) but left this decision-time fabrication in the store untouched — the same class of bug BB-87's "Avoid" line warned about recurred one call site over, in the consumer of the slice rather than the producer.
- **Fix:** Deleted the `AuditEntry` construction and the `auditLog` write from `resolveApproval`, leaving a comment explaining why the obvious write is absent. `history` and the toast are unchanged, so the user still gets immediate feedback; the audit table now only ever refreshes from the gateway, on the next `loadApprovals()` (triggered by `postDecision`'s `invalidate("approvals")`).
- **Avoid:** a store slice hydrated from the server has exactly one writer — the loader. Never unshift a locally-constructed record into it, and never invent a timestamp for a row whose server counterpart has none. This is BB-87's recurrence: fixing a fabrication at one call site (the adapter) does not fix the invariant everywhere it applies — grep every writer of a server-hydrated slice, not just the one that first tripped the bug.
- **Locked by:** `frontend/src/lib/store/store.test.ts` — describe block "audit log is server truth — never client-fabricated (BB-87 recurrence)" ("resolving an approval does not write into the server-hydrated audit log", "declineAll does not inject one fabricated row per approval").

### BB-95 — Anthropic LLM calls had NO timeout (not 600s); the configured `timeout_s` was never read

- **Symptom:** an Anthropic chat turn could hang indefinitely — spinner forever, no error, a
  parked thread per hung call (`loop.py`/`resilience.py` are fully synchronous, so a blocked
  socket read blocks an OS thread). Setting `[routing.resilience] timeout_s` changed nothing.
- **Root cause:** two independent gaps. (1) `create_llm_client` (`core/llm.py:862`) omitted
  `timeout` at all six returns, so `AnthropicClient.__init__`'s own default of `None` was
  used. (2) That `None` was passed *explicitly* to `anthropic.Anthropic(timeout=...)`
  (`llm.py:273`), and in the installed `anthropic` **0.105.2** an explicit `None` is a
  **given** value: `_base_client.py:932` only substitutes `DEFAULT_TIMEOUT` when
  `not is_given(timeout)`, and `is_given(None)` is `True`. Verified by executing the
  installed package: `anthropic.Anthropic(api_key=..., timeout=None)._client.timeout` →
  `Timeout(timeout=None)` (wait forever), vs. omitting the kwarg entirely →
  `Timeout(connect=5.0, read=600, write=600, pool=600)`. So httpx received `Timeout(None)`,
  not the SDK's 600s default (an earlier review draft assumed the latter). The SDK's
  secondary non-streaming ceiling is gated on `self._client.timeout == DEFAULT_TIMEOUT`
  (`anthropic/resources/messages/messages.py:987`) and was therefore also disabled.
  `RetryPolicy.timeout_s` (`core/resilience.py:86`) and `ResilienceSettings.timeout_s`
  (`core/settings.py:170`) had **zero** readers — a word-boundary grep across `src/` returned
  only the two definitions and one docstring.
- **Fix:** `create_llm_client` takes a keyword-only `timeout_s: float = 60.0` (keyword-only so
  no existing positional call site shifts) and applies it to `AnthropicClient` and all four
  `OpenAICompatibleClient` returns; the `chatgpt-web` branch (`ChatGPTWebClient` — opt-in,
  `HACHI_UNOFFICIAL=1`-gated, different transport) is deliberately left alone.
  `ModelRouter._build_client`'s `create_llm_client` fallback (`core/routing/router.py:301`)
  now passes `timeout_s=self._resilience.timeout_s` (the float, never the `RetryPolicy`
  object — `core/llm.py` must stay free of a `core/resilience.py` import, which already
  imports `llm.py`, or the two modules cycle). `AnthropicClient.__init__`'s docstring — which
  claimed `None` meant "the SDK default (600s)" — corrected.
- **Follow-up fix — direct-construction call sites (closed):** two production call sites
  construct `AnthropicClient` directly, bypassing `create_llm_client` entirely, and passed no
  `timeout` — `core/sub_agent.py:685` (`AnthropicClient(model=model, max_tokens=4096)`, the
  sub-agent **delegation path**) and `tools/memory_tools.py:120` (`AnthropicClient()`, the
  background memory-consolidation job). Both silently inherited the class's own
  `timeout=None` default and could hang forever — closing this was the point of T-23 (an
  unattended sub-agent hanging is exactly the story it exists to prevent), so leaving these
  two sites unbounded would have shipped the fix half-done. Root-caused and closed two ways:
  (a) **root fix** — `AnthropicClient.__init__`'s own default changed from `timeout: float |
  None = None` to `timeout: float = 60.0` (`core/llm.py:257`), so *every* direct construction
  gets a backstop regardless of call site, present or future; the type was narrowed from
  `float | None` to `float` (matching `OpenAICompatibleClient`'s existing pattern) so a
  future explicit `timeout=None` is at least a visible, deliberate choice rather than a type-
  compatible default. This alone fixes `sub_agent.py:685` with no code change there. (b)
  **narrow fix** — `tools/memory_tools.py:120` additionally now passes an explicit
  `timeout=30.0`, matching `core/memory_vault.py:304`'s identical
  memory-consolidation-engine convention (both run on the durable job-queue worker, not the
  user-facing turn, so the tighter background bound is more consistent than the general 60s
  default). `core/memory_vault.py:304` itself remains untouched — it already worked.
- **Residual gap, still not fixed (genuinely deferred, adjacent to T-24):** `ModelRouter`'s
  *primary* build path, `provider.build_llm_client(desc.id)`
  (`core/providers/anthropic_provider.py:88`, `core/providers/openai_compat_provider.py:96`),
  calls `create_llm_client` without forwarding `timeout_s`, so it gets the 60s *default* (the
  infinite hang is closed there too) but not a non-default configured
  `[routing.resilience].timeout_s` — only the `router.py:301` fallback (taken when no
  `ProviderRegistry` is wired, or a provider adapter raises) receives the configured value.
- **Avoid:** when an SDK distinguishes "not given" from `None` (a `NotGiven` sentinel),
  passing `None` explicitly is NOT the same as omitting the argument. Verify against the
  installed package (CLAUDE.md Rule 4) — both a review and a corrections pass got this wrong
  from the docstring alone. And: a config field with zero readers is a lie; grep `-w` for
  every new settings field before trusting it is wired. And: a factory-level fix
  (`create_llm_client`) does not protect direct construction — grep the class name itself
  (`AnthropicClient(`) for bare call sites, not just the factory's call sites, before calling
  a "thread it through the factory" fix complete.
- **Locked by:** `tests/test_llm_env_resolution.py` —
  `test_create_llm_client_applies_timeout_to_anthropic_sdk_client`,
  `test_create_llm_client_applies_timeout_to_openai_compatible`,
  `test_anthropic_client_default_timeout_is_bounded_not_none`;
  `tests/test_routing_compat.py::TestSubAgentD2Regression::test_no_session_client_default_anthropic_has_bounded_timeout`;
  `tests/test_memory_tools_queue.py::test_smart_upsert_conflict_resolution_anthropic_client_has_bounded_timeout`

### BB-98 — Kennel "Add" reported success while installing nothing, for every user

- **Symptom:** `GET /skills/kennel` never listed a bundled pack, and clicking **Add** in the
  web UI showed "Added <pack> — it's ready to use" while no file was written. Requesting a
  pack id that does not exist also returned `{"ok": true, "installed": true}`.
- **Root cause:** three independent dead links in one feature. (1)
  `_InProcessGatewayRuntime.get_kennel` (`cli/main.py`) imported
  `hachi.og_engine.bundled_packs.list_bundled_packs` — wrong *package* (the module is
  `hachi.core.bundled_packs`) **and** a symbol that exists nowhere in `src/` (it was named only
  in that module's own stale docstring). The resulting `ModuleNotFoundError` was swallowed by
  `except Exception: pass`. (2) `add_skill_from_kennel` validated `kennel_id` as non-empty and
  then never used it again — no install call existed anywhere in the method — before returning
  a hardcoded `{"ok": True, "installed": True}` for *any* id, including ids that do not exist.
  (3) Its re-index call was `agent.skill_engine.reindex()`; `SkillEngine` has no `reindex`
  method (verified: no `def reindex` anywhere in `src/`), so that raised `AttributeError` into
  another `except Exception: pass`. No function in `hachi/core/bundled_packs.py` — a fully
  working, already-tested module (`tests/test_p2_12_agent_packs.py`) — had any production
  caller at all; it was a green orphan.
- **Fix:** re-pointed `get_kennel` at `list_bundled_manifests()`, mapping the frozen/slotted
  `OgPackManifest` (no `name`/`tags` fields) onto the wire dict via a new module-level
  `_pack_display_name` helper (`"crm-agent"` → `"Crm Agent"`) and `manifest.scope_ids()`.
  `add_skill_from_kennel` now calls the existing
  `install_bundled_pack(pack_id, get_hachi_paths().skills_dir, confirm=False)` (BB-5: not
  `paths.workspace_dir`) and raises `GatewayProtocolError` when it returns `None` for an
  unknown pack — an unknown pack can no longer report success. The re-index call was corrected
  to `skill_engine.load_directory(skills_dir)`, the real public re-index entry point
  (`og_engine/engine.py:540`). Both former `except Exception: pass` sites are now
  `_logger.warning(..., exc_info=True)`, including a distinct warning for the
  no-exception-but-empty case (manifest list empty).
- **Trap found while fixing it:** do not add a *local* `from hachi.core.paths import
  get_hachi_paths` inside a method that must stay test-patchable. `get_hachi_paths` is already
  a module-level name in `cli/main.py` (imported once at the top); tests patch it via
  `patch("hachi.cli.main.get_hachi_paths", ...)` (the pattern in `tests/test_backup_restore.py`),
  which rewrites that module attribute. A local re-import inside the method instead fetches the
  attribute straight off `hachi.core.paths` and silently ignores the patch — in a test this
  means `install_bundled_pack` would write into the *developer's real* `~/.hachi` data dir
  instead of `tmp_path`. Call the already-imported module-level name bare; do not shadow it.
- **Avoid:** a `try: from x import y / except Exception: pass` around a *feature's only entry
  point* is not graceful degradation — it is a permanently disabled feature that reports
  success. Grep that the module AND the symbol exist before trusting a bare swallow, and never
  return a hardcoded success literal from a handler that performed no work. A unit test that
  stubs the handler entirely (`_app(get_kennel=lambda: stub)`, `tests/test_g1_wave1.py`) proves
  nothing about wiring — it stayed green through all three defects.
- **Locked by:** `tests/test_kennel_install.py` — `test_get_kennel_lists_real_bundled_packs`,
  `test_add_skill_from_kennel_actually_installs_og_files`,
  `test_add_skill_from_kennel_rejects_unknown_pack`.

### BB-99 — `[memory]` recall/decay settings never reached the vault (M2 decay was inert)

- **Symptom:** `recall_distance_max`, `core_memory_max_entries`, and
  `decay_half_life_days` had no effect. Setting `decay_half_life_days = 30.0` changed
  nothing — the M2 recency-decay feature was inert for every user, silently, with no
  exception and no log line.
- **Root cause:** the only code that passed those settings lived in `boot_agent`'s
  `else` branch (`gateway/orchestrator.py`), reached only when `_shared.vault is
  None`. But `boot_agent` calls `ProcessSharedServices.get_or_create()` ~230 lines
  earlier, whose `_init` unconditionally built the vault via a settings-free
  `_provider.initialize(brain_dir=brain_dir)` — so on the default `chroma` backend
  the `else` branch was unreachable (`_shared.vault` was always already non-`None`).
  On the non-Chroma backends the `else` DID run but failed its
  `isinstance(_provider, ChromaMemoryProvider)` check and fell to the plain
  `initialize()` anyway. **Confirmed against source: neither of `boot_agent`'s two
  branches ever delivered the settings** — the review's fix target (the `if` at
  `orchestrator.py`) was one level removed from the actual defect, which was in
  `ProcessSharedServices._init` (`gateway/shared_services.py`). An in-code comment
  claimed the opposite ("so settings.toml actually controls retrieval behaviour").
  Compounding it, `_config_fingerprint` keyed only on `brain_dir|backend|graph`, so
  changing any of the three settings did not even produce a new singleton — fixing
  the delivery path alone would have worked on boot and silently stopped working on
  the first `settings.toml` hot-reload.
- **Fix:** passed the three settings into `_provider.initialize(...)` inside
  `ProcessSharedServices._init` (`shared_services.py:315-340`) — the site that
  builds the vault every session actually reuses — added the three raw config keys
  to `_config_fingerprint` (`shared_services.py:54-83`), corrected the false comment
  at `orchestrator.py:3059-3067`, and added a boot-time `logger.warning` per setting
  when the configured value and the live vault's value disagree
  (`orchestrator.py:3080-3105`). The warning reads three new read-only properties —
  `decay_half_life_days` / `recall_distance_max` / `core_memory_max_entries`
  (`memory_vault.py:624-634`) — added over what was previously `_`-prefixed-only
  state, so neither the warning nor a test needs to reach into private attributes.
- **Avoid:** when a setting is applied in a branch, prove the branch is reachable on
  the DEFAULT path before believing the feature works — a settings object that is
  parsed and then never passed anywhere is a dead feature; grep the parse site for a
  corresponding consumer. And when a config value affects what a process-level
  singleton *is* (not just how it behaves once built), it belongs in that
  singleton's cache-key fingerprint — otherwise a hot-reload silently keeps serving
  the stale instance built from whichever config booted first.
- **Locked by:** `tests/test_memory_settings_reach_vault.py` —
  `test_shared_vault_carries_configured_memory_settings`,
  `test_shared_services_non_chroma_backend_initializes_without_vault`,
  `test_fingerprint_changes_when_recall_settings_change`,
  `test_fingerprint_changes_for_each_of_the_three_settings_independently`,
  `test_hot_reload_with_tuned_settings_does_not_reuse_stale_singleton`.

### BB-100 — BB-40's `extract_facts` gate was never actually fixed; auto-memory stayed a silent no-op for keyring/`.env` users, and skips still reported as success

- **Symptom:** background fact extraction wrote nothing for any user whose Anthropic key
  lived in the OS keyring/secret store or a project `.env` file — while chat worked fine
  for the same user with the same key, because the chat path (`core/llm.py
  create_llm_client`) resolves through the secret store and `_resolve_api_key`, and the
  memory path did not. The jobs surface showed every one of those turns as a COMPLETED
  `extract_fact` job; nothing ever appeared under `jobs/failed`, and a skip was
  indistinguishable from a real extraction to anything outside the handler.
- **Root cause:** BB-40's cause #2 was documented as fixed and was not — a recurrence in
  the strict sense: the same bug, still present. BB-40's own Fix section claims
  `core/memory.py`'s `extract_facts()` gate was "changed from
  `if not os.environ.get("ANTHROPIC_API_KEY")` to `if llm_client is None`", but
  `git log -L 218,228:src/hachi/core/memory.py` returned exactly **one** commit
  (`04f2f90`, the original P1-5 feature commit that introduced the gate) — the lines were
  never touched again until this fix. The identical literal env-var check was also live
  in `core/memory_vault.py`'s `_get_llm_client()` (the client factory the durable job path
  uses whenever no `_llm_client_factory` is injected — i.e. every real production job), so
  even if `extract_facts` had correctly gated on `client is None`, the client it received
  was still `None` for any non-`ANTHROPIC_API_KEY`-exporting user. Compounding both:
  `handle_extract_fact` returns `"skipped:..."` strings instead of raising (deliberately,
  to avoid a restart retry loop — `job_handlers.py:154-157`), and `jobs/queue.py`'s
  success path mapped any non-raising handler outcome to a bare `job.completed` event with
  no field indicating a skip had occurred.
- **Fix:** `memory_vault._get_llm_client()` now resolves the key the same way
  `core/llm.py create_llm_client` does for the chat path — secret store
  (`create_default_secret_store().get("ANTHROPIC_API_KEY")`, verified against
  `core/onboarding.py persist_provider_api_key`'s storage convention) first, then
  `_resolve_api_key("ANTHROPIC_API_KEY")` (env var, then project `.env`) — and passes the
  resolved key to `AnthropicClient` explicitly instead of relying on the client's own
  bare `os.environ` fallback. `extract_facts()`'s redundant env-var gate
  (`core/memory.py`) is deleted and replaced with the `llm_client is None` check BB-40
  already claimed to have made. `jobs/queue.py`'s success path now emits an additional
  `job.skipped` event (new constant in `core/runtime_events.py`, alongside
  `JOB_COMPLETED`) carrying a `reason` field (the text after `"skipped:"`) whenever a
  handler's output starts with `"skipped:"` — alongside the unchanged `job.completed`
  event, so the durable status stays COMPLETED (no retry loop introduced) while the skip
  becomes machine-observable to anything subscribed to job events. The two
  `logger.warning` calls in `job_handlers.py` that said "(ANTHROPIC_API_KEY not set?)" now
  name all three resolution sources.
- **Trap found while fixing it:** the new `.env` fallback inside `_get_llm_client` made an
  *existing, previously-passing* test — `tests/test_slice22_wiring.py::
  TestHandlerCorrections::test_extract_fact_is_real_and_degrades_gracefully` — reach a
  **live** Anthropic API call using this repo's own dev-checkout `.env` key (confirmed:
  the call returned `"extracted:1"`, i.e. a real extraction against the real API). That
  test had only ever passed because the *old*, narrower env-var-only gate reliably
  returned `None` in any test process that didn't export `ANTHROPIC_API_KEY` — never
  because the test isolated itself from the ambient environment. Fixed by isolating that
  test the same way this task's own new tests do (`monkeypatch.chdir(tmp_path)` + a stub
  secret store + resetting the memoised `_llm_client_instance` global); its assertion is
  byte-for-byte unchanged.
- **Avoid:** when a bug-bank entry records a fix, verify the fix is actually in the code
  (`git log -L <start>,<end>:<file>`) before trusting the entry and moving on to the next
  bug — BB-40's cause #2 was diagnosed correctly and then never landed, and the entry read
  as fully resolved for weeks with a fabricated "Locked by" line reinforcing the false
  impression. Separately: before adding a `.env`/secret-store fallback to a function that
  existing *unmocked* tests already call with no factories/mocks, grep for those call
  sites first — a fallback that reads real local files by design (the "sanctioned"
  lazy-at-call-time shape per BB-2) will pick up a real `.env`/keyring credential on any
  developer machine that has one, silently turning a unit test into a live, billable
  network call. `tests/conftest.py`'s `HACHI_DISABLE_DOTENV_AUTOLOAD=1` only gates
  `cli.main`'s import-time merge — it does **not** gate `core/llm.py _resolve_api_key`'s
  own independent, per-call `.env` read, which stays live in every test process
  regardless of that flag.
- **Locked by:** `tests/test_auto_memory_key_resolution.py` —
  `test_memory_llm_client_resolves_key_from_project_dotenv`,
  `test_memory_llm_client_resolves_key_from_secret_store`,
  `test_memory_llm_client_returns_none_when_no_key_resolvable_anywhere`,
  `test_extract_facts_does_not_gate_on_the_env_var`,
  `test_extract_facts_skips_cleanly_when_llm_client_is_none`,
  `test_skipped_extraction_is_distinguishable_from_success`.

### BB-101 — `[eval_reports]` was documented, implemented, and rejected by the config schema

- **Symptom:** adding the documented `[eval_reports] enabled = true` to `settings.toml`
  did nothing — the weekly failure-report daemon never started — AND the loader logged
  "contains unknown top-level key(s): eval_reports". With `HACHI_CONFIG_STRICT=1` the
  same documented config raised `ValueError` and failed the boot outright.
- **Root cause:** `EvalReportSettings` existed and `boot_agent` read
  `config.get("eval_reports", {})`, but `ResolvedConfig` declared no `eval_reports`
  field. `model_config = ConfigDict(extra="ignore")` therefore dropped the section at
  `config_loader.load_effective_config`'s `ResolvedConfig.model_validate(merged)`, so the
  post-dump dict boot reads never contained it and `enabled` was always `False`. The same
  absence made `_known_config_keys()` (which derives from `ResolvedConfig.model_fields`)
  classify the user's key as unknown. `mcp_servers` carries a comment describing this
  exact mechanism from a previous occurrence.
- **Fix:** added `eval_reports: EvalReportSettings = Field(default_factory=EvalReportSettings)`
  to `ResolvedConfig` (`src/hachi/core/settings.py`) and documented the section — as a
  live, parsed `[eval_reports]` table with its real defaults, not a fully-commented block
  — in `settings.example.toml`, so `tests/test_config_loader.py::
  test_settings_example_toml_keys_are_known_config_fields` (T-02) actually exercises it.
  A fully-commented block would satisfy "still parses" but contributes no key to
  `tomllib.loads()`, so T-02's subset lock would never see it.
  **Update (T-07 carried-over debt, closed):** the boot-time `logger.info` at the
  daemon-start call site (`orchestrator.py`, the `if _eval_cfg.enabled:` gate right
  before `_start_eval_report_scheduler(...)`) has now been added, so a user can tell
  "I enabled it and it started" from "I enabled it and the config never arrived" —
  locked by `tests/test_eval_report_config.py::
  test_boot_agent_logs_when_eval_report_daemon_starts`.
- **Avoid:** a settings model is not a setting until it is a field on `ResolvedConfig`.
  With `extra="ignore"`, an unregistered section is dropped silently AND reported to the
  user as their typo. Whenever you add a `*Settings` model, add the `ResolvedConfig`
  field in the same commit and assert the round-trip in a test — and if you also
  document it in `settings.example.toml`, make sure the keys actually parse (not fully
  commented out), or the subset-test lock never sees them.
- **Locked by:** `tests/test_eval_report_config.py` —
  `test_eval_reports_section_survives_resolved_config`,
  `test_eval_reports_is_not_an_unknown_key`, and
  `test_all_settings_models_are_registered_on_resolved_config` (generalised: walks every
  `*Settings` model defined in `hachi.core.settings` and fails by name if it is not
  reachable from `ResolvedConfig`'s field graph, independent of whether the section was
  also added to `settings.example.toml` — so a future model that skips both still fails
  loudly here).

### BB-102 — A stalled stream defeated both the wall-clock deadline and the stop button

- **Symptom:** a turn whose provider accepted the request and then stopped delivering chunks
  hung indefinitely. The 600s wall-clock limit never fired, `[TIME LIMIT REACHED]` was never
  returned, and pressing Stop did nothing. From the UI the agent looked dead, not failed.
- **Root cause:** in `ExecutionLoop._stream_generate` (`core/loop.py:673`), both the cancel
  check and the wall-clock-deadline check sat AFTER the blocking `chunk = next(gen)` call. They
  were only reachable once another chunk arrived — the prior comment said so verbatim ("as soon
  as the next chunk arrives"). Nothing between creating the generator and calling `next()` on it
  could interrupt the read. The module is fully synchronous (`grep -c "async def" core/loop.py`
  → 0), so no asyncio timeout was available in place. BB-95 (this task's dependency) closed the
  transport-level gap for Anthropic, but a transport read timeout — on any provider — only
  bounds *inter-byte* silence. It does nothing against a server that stays connected and keeps
  the socket warm (SSE keep-alives, periodic whitespace) while producing no real content, which
  never trips a read timeout at all. That residual case is what this bug closes.
- **Fix:** the generator is now drained on a daemon worker thread into a `queue.Queue`; the loop
  thread waits on the queue with a bounded timeout (`_STREAM_POLL_INTERVAL_S = 0.1s`, capped
  against the time remaining to `self._wall_deadline`) and evaluates the cancel flag and
  wall-clock deadline on every wakeup — whether or not a chunk arrived — so neither check is
  ever downstream of the blocking read. The worker only ever enqueues one of `"chunk"` /
  `"done"` / `"error"`; `on_delta` and all loop/turn state are touched only by the loop thread,
  so a worker still blocked on a dead socket after the loop thread has raised and moved on
  cannot call back into a turn it has already abandoned. Exception types (`_StreamCancelled`,
  `_StreamTimedOut`, both unchanged) and `run()`'s handlers keep the partial-text semantics
  exactly as before — only the reachability of the checks changed.
- **Considered and rejected:** a watchdog thread that reaches into the provider transport to
  close the socket directly. It would genuinely unblock the read rather than abandoning it, but
  needs different code per client (the Anthropic SDK's stream context manager vs. `httpx`
  streaming) and breaks the `BaseLLMClient` abstraction.
- **Avoid:** a timeout/cancel check placed after a blocking call is not a timeout — it is a
  post-condition. Any "check the deadline every iteration" loop must be able to wake up WITHOUT
  the thing it is waiting for (a message, a chunk, a socket read). A transport-level timeout
  (BB-95) is the necessary first line of defence, not a substitute for this one.
- **Locked by:** `tests/test_loop_backstop_cancel.py` —
  `test_wall_deadline_fires_when_stream_stops_delivering_chunks`,
  `test_cancel_fires_when_stream_stops_delivering_chunks`.

### BB-103 — Google tools mounted twice; the approver-less mount won, denying every destructive action

- **Symptom:** With Google fully configured AND connected, every destructive Google action
  (`send_email`, `trash_file`, …) returned *"No approver connected for this session; destructive
  action blocked."* — permanently, for exactly the users who completed setup. This is an
  **availability** defect, not a security bypass — a missing approver denies, it does not permit.
- **Root cause:** Two live mount paths in `boot_agent`. The `TOOL_SPECS` entry
  (`gateway/orchestrator.py:3345`, `register_google_tools(..., approver=action_approver)`) mounted
  the hub with the session `action_approver`; the capability loop (`:3421-3454`) then mounted it
  again from the `google` `CapabilityDescriptor`'s factory, which takes no approver, so
  `GoogleIntegrationHub.__init__` set `client.approver = None`. `ToolRegistry.register` overwrites
  handlers last-wins (`core/loop.py:197`), so the approver-less handler survived;
  `ToolRegistry.get_schemas` de-dupes first-wins (`core/loop.py:330-337`), so the schema the model
  saw came from the *other* mount — the registry was internally inconsistent, schema from one
  mount, handler from another. `GoogleHTTPClient.require_approval` fails safe and denies when
  `self.approver is None` (`integrations/google/client.py:288-292`).
- **Fix:** Added `CapabilityDescriptor.mounts_tools` (default `True`; `False` on the `google`
  descriptor only, `gateway/orchestrator.py:361`). The capability loop now `continue`s past
  **both** the mount call and the `_register_capability_setup_stub` fallback for any
  `mounts_tools=False` descriptor, immediately after the `kind` check (`:3421-3431`). `TOOL_SPECS`
  is the single mount path and keeps the approver; `google` is still yielded by `iter_enabled()`
  so `install_optional_skill_pack()` and `status_snapshot()` are unchanged. `slack`/`notion` are
  unaffected (default `True`).
- **Avoid:** One integration, one mount path. When adding an integration, check both `TOOL_SPECS`
  and the capability registry before adding a second — nothing in `ToolRegistry` detects or warns
  about a double mount (that guard is T-36, deliberately sequenced *after* this fix, since landing
  it first would hard-crash boot for every configured Google user). Two audit claims about this
  finding were wrong and were verified false before this fix was written: the Anthropic API never
  sees duplicate schema names (`get_schemas` de-dupes first-wins), and a missing approver denies
  rather than permits — do not describe a "no approver" bug as an open door without checking
  `require_approval`'s actual fail-safe direction first.
- **Locked by:** `tests/test_google_double_mount.py` —
  `test_google_capability_does_not_mount_tools`,
  `test_google_destructive_action_is_permitted_once_mounted_only_via_tool_specs` (the second
  calls the real `GoogleHTTPClient.require_approval` end-to-end and asserts the actual allow/deny
  outcome, not a proxy for it — the pre-fix failure message is the literal production denial
  string, not a mocked stand-in).

### BB-104 — A routing-layer boot failure silently disabled routing (logged at DEBUG while success logged at INFO)

- **Symptom:** a user configured `[routing]`, and Hachi ran every turn on the single default
  model as if the section were absent. No error, no warning — at default log level, nothing at
  all.
- **Root cause:** `gateway/orchestrator.py`'s routing-boot `except Exception:` (guarding
  `ProviderRegistry` → `ModelRegistry` → `RoutingPolicy` → `ModelRouter` construction) caught
  every exception at `logger.debug` and set `_model_router = None`. The success path one branch up
  logs at `logger.info`, so a working boot was strictly louder than a broken one. The `try` also
  wrapped six `import` statements alongside the constructors, so an
  `ImportError`/`AttributeError`/typo anywhere in that block was indistinguishable from a genuine
  `[routing]` config problem.
- **Fix:** promoted the failure log to `logger.warning` with the exception type name and
  `exc_info=True`; added a `routing.boot_failed` runtime event alongside it (matching the existing
  `cost.routing_warning` pattern a few lines up in the same block); hoisted the six imports out of
  the `try` so they raise loudly instead of being swallowed. `_model_router = None` on failure is
  unchanged — the fallback itself was correct, only its silence was not.
- **Avoid:** a failure must never be logged at a lower level than the corresponding success. And
  never wrap `import` statements in the same `try` as the logic they support — it turns every
  packaging or rename error into a false "your config is bad".
- **Locked by:** `tests/test_routing_boot_failure.py` — `test_routing_boot_failure_logs_at_warning`,
  `test_routing_boot_failure_names_the_exception_type`,
  `test_routing_boot_failure_emits_a_runtime_event`,
  `test_routing_boot_imports_are_outside_the_try`. Structural (AST-based) assertions against the
  real `boot_agent` source, not a `caplog`-driven boot — booting a real `boot_agent()` in a test
  needs the whole boot sequence (permissions, skill-engine indexing, MCP servers, LLM client
  construction) mocked off the developer's real `~/.hachi`, which no test in this repo currently
  does (and T-26 itself forbids extracting a helper to make the block independently callable); see
  the module docstring for the full reasoning.

### BB-105 — In-context approvals always resolved `approvals[0]`; the SSE field names never matched

- **Symptom:** Clicking Allow/Deny on an in-context leash prompt resolved the wrong
  pending approval whenever more than one was pending, and the prompt itself always read
  "Permission requested" instead of naming the tool and target. Counter-intuitively, this
  was *also* wrong with exactly one approval pending — the click still resolved that lone
  approval, but only by accident (the fallback path, not because anything actually
  matched what the user clicked).
- **Root cause:** Two layered defects, and the architecture review's own diagnosis of
  this bug was wrong (see `docs/audit/2026-07-19/tasks/CORRECTIONS.md` §R2) — it blamed a
  missing `request_id` on the SSE wire. The id was always on the wire (all three backend
  emitters send it and SSE serialises the payload verbatim); the real defects were
  entirely frontend: (1) `chat.ts`'s `permission.requested` handler read
  `payload.request` / `.action` / `.why` — three keys the backend has never emitted; the
  wire carries `request_id` / `tool` / `target` / `reason`. All three fell back to their
  defaults, and `request_id` was discarded entirely. (2) Because `InContextLeash` had no
  id field, `allowLeashLive`/`denyLeashLive` resolved via `approveFirstMatching(ic.request)`,
  a fuzzy text match whose needle was therefore always the literal `"Permission
  requested"`. It matched nothing, so the `let target = approvals[0]` fallback fired
  unconditionally — the first pending approval was resolved every time, right only by
  coincidence when exactly one was pending.
- **Fix:** Added `requestId: string` to `InContextLeash` (`lib/data/types.ts`) and to the
  `runLeash` state shape (`lib/store/store.ts`); `chat.ts`'s `permission.requested` case
  now reads `request_id` / `tool` / `target` / `reason` off the real payload and composes
  `action` the same way `approvals.ts`'s `adaptApproval` does, so the two surfaces agree.
  Added `approveByRequestId` / `declineByRequestId` (`lib/data/domains/approvals.ts`),
  resolving through the existing `request_id → numeric id` map and reporting `false` for
  an id the map has never seen rather than minting one for it. Deleted
  `approveFirstMatching` and `denyFirstMatching` outright, along with the comment block
  that documented the wrong assumption ("that event carries no request_id"). Backend
  unchanged — this was purely a frontend field-name and resolution-strategy bug.
- **Avoid:** never resolve a server-side record by fuzzy-matching its display text —
  carry the server's identifier end to end. When adapting an event payload, verify the
  emitter's actual keys against source; a `typeof x === "string" ? x : fallback` guard
  silently converts a wrong field name into a plausible-looking default, and a fallback
  string that never varies is invisible in casual testing because it still "looks like"
  a value.
- **Locked by:** `frontend/src/lib/data/domains/approvals.test.ts` — "resolves the
  approval the user clicked, not approvals[0]", "does not fall back to the only pending
  approval when the leash's id does not match it", "maps the real SSE field names onto
  the in-context leash".

### BB-106 — Approval decisions failed silently: the card vanished while the gateway stayed blocked

- **Symptom:** Clicking Allow or Decline always removed the approval card and showed a
  success toast, even when the `POST /approvals/{id}` returned 404/422/500 or was never
  issued at all (unmapped `request_id`). The agent remained blocked until the 180s
  server-side timeout auto-denied it, so a user's *approve* could silently become a
  *deny* three minutes later, with nothing on screen explaining why.
- **Root cause:** `postDecision` in `frontend/src/lib/data/domains/approvals.ts` had two
  silent paths: a bare `catch {}` (justified by a comment claiming `client.ts` surfaced
  failures centrally — it only surfaces genuine *network* errors via `isNetworkError`,
  not decoded `ApiError` status codes) and an `if (requestId)` guard that skipped the
  POST entirely on an id-map miss. It returned `Promise<void>`, so callers had no way to
  distinguish success from either failure mode. `approve`/`allowAlways`/`decline` also
  ran the optimistic store mutation — which removes the card and fires a success toast —
  *before* awaiting the POST at all, so the UI had already committed to "it worked"
  before the network call even started.
- **Fix:** `postDecision` now returns `"ok" | "unknown-id" | "failed"` instead of
  `void`. The three wrappers (`approve`, `allowAlways`, `decline`) await it first and
  mutate the store only on `"ok"`, raising a `"blocked"`-tone toast otherwise (matching
  the existing house style at `store.ts`'s `runAlwaysAllow`/`icAlwaysAllow`, BB-78's
  rule). `declineAll` no longer calls the bulk optimistic store action; it resolves each
  id individually and clears from the store only the ones the gateway actually
  confirmed, toasting how many failed when any did. `invalidate("approvals")` still
  fires on every outcome, including failure. Chose the simpler of the two documented
  options for `isNetworkError`: a genuine network failure now also reports `"failed"`
  and toasts here (accepting slight duplication with the existing reconnect banner)
  rather than special-casing it to stay silent.
- **Avoid:** never `catch {}` a mutation the UI has already claimed succeeded. Mutate the
  store on a confirmed 2xx (BB-78's rule), and give a mutation function a return type
  that can express failure — a `Promise<void>` wrapped around a POST that can 404 is a
  guarantee some caller will treat "didn't throw past my try/catch" as "worked."
- **Locked by:** `frontend/src/lib/data/domains/approvals.test.ts` — "keeps the approval
  and warns when the decision POST fails", "does not silently drop the approval when the
  request_id is unmapped", "declineAll does not clear approvals whose POST failed".

### BB-107 — `getattr(x, "field", None)` against our own types silently disabled four features

- **Symptom:** four separate features were permanently inert while their call sites read
  as careful defensive code: `ProcessSharedServices.session_store` (declared nowhere —
  three readers, every "prefer shared store" branch dead, plus a leaked SQLite handle per
  message; owned by T-10), `MemoryManager.memory_dir` (does not exist — the gardener
  reader returned `[]` before touching the file; owned by T-08), `ResilientClient.
  max_tokens` (not exposed — the pre-flight token safety ceiling silently computed 0
  under routing; owned by T-27), and `AgentSession.reindex_skills` (defined nowhere —
  authoring saves never re-indexed and reported `reindexed_ms: 0`; this task). Two more
  turned up while building the checker that did not make the audit's original four:
  `MemoryManager._session_id` (read in `sync_turn()`, assigned nowhere in the repo — the
  fact-extraction job's `session_id` payload is always `""`) and `AgentSession.
  _session_store._current_started_at` (read once in `gateway/orchestrator.py`, assigned
  nowhere).
- **Root cause:** a three-argument `getattr` against a Hachi-owned collaborator converts
  "this attribute was never implemented" into a silently-taken default. `mypy` cannot
  flag it (the call is well-typed), `ruff` has no rule for it, and it reads as
  future-proofing — so it survives review indefinitely. The `reindex_skills` /
  `_session_id` cases are one hop worse: the object handed to `getattr` is a bare local
  (`agent = next(iter(self._agents.values()), None)`), not `self.<attr>` directly, so a
  checker that only recognises the literal `self`/`self.<attr>` shape misses its own
  headline example.
- **Fix:** added `scripts/check_getattr.py`, a stdlib-only AST rail that flags
  `getattr(self, "...", default)` / `getattr(self.<attr>, "...", default)` with a
  literal attribute name, AND `getattr(local, "...", default)` where `local` was bound,
  earlier in the same function, to a self-owned collaborator via at least one pure
  container/iterator hop (`next()`, `iter()`, `.values()`, subscripting, …) — the second
  form is required to catch `reindex_skills`. A zero-hop bare rename (`x = self.foo`) is
  deliberately excluded from that second form: it fired on `self._chroma_client`
  (chromadb) and `self._server` (`uvicorn.Server`) during development, both legitimate
  duck-typing over foreign SDK objects, before the "at least one real hop" rule was
  added. Every genuinely-existing hit was converted to a direct read (verified against
  the declaring class's real `__init__`, not guessed — two of the first attempts were
  wrong because the attribute is only assigned inside `run()`, not `__init__`, and a
  real test caught it); every genuinely-optional hit was allowlisted with a one-line,
  path:attr-keyed reason; the already-known and newly-found dead features were filed,
  not fixed, and allowlisted with a citation to the owning task. Wired into
  `scripts/preflight.py` as an advisory step next to `adr-lint`.
- **Avoid:** against a type WE own, a missing attribute is a bug — read it directly and
  let `AttributeError` be loud. Reserve defaulted `getattr` for foreign/SDK objects and
  genuinely optional protocol members, and comment why at each site. Before assuming an
  attribute is "always set in `__init__`, safe to convert", grep for `ClassName.__new__(`
  in `tests/` — several classes in this codebase (`ExecutionLoop`, `MemoryVault`,
  `SessionStore`, `AgentSession`, …) are deliberately constructed that way in tests to
  skip expensive I/O, and genuinely lack anything not set by `__init__` itself. `hasattr`
  guards on our own types are the same bug in different syntax (left for a future
  widening, noted in the checker's docstring).
- **Locked by:** `tests/test_check_getattr.py` —
  `test_src_tree_has_no_unallowlisted_violations`,
  `test_detects_defaulted_getattr_on_local_alias_of_self_collaborator`.

### BB-108 — Two session routes could not authorise, because their handlers took no `Request`

- **Symptom:** `GET /sessions/{session_id}/messages` returned a full transcript, and
  `POST /sessions/cleanup-empty` deleted empty session rows globally, without the
  per-session ownership check the other nine session routes perform. **Not exploitable
  as shipped** — both routes still require a valid bearer token via the router-level
  dependency (`fastapi_app.py:710`/`:713`), and exactly one token exists per process
  today, so there is no second principal to be protected from.
- **Root cause:** Structural, not a forgotten line. `get_session_messages(session_id,
  limit)` and `cleanup_empty_sessions()` declared no `request: Request` parameter, so
  neither could call `_require_auth(request)` and neither could obtain the caller digest
  that `_check_session_ownership(session_id, caller_hash)` needs. FastAPI discards a
  router-level dependency's return value, which is why the nine already-checked handlers
  re-call `_require_auth` themselves.
- **Fix:** Both handlers now take `request: Request` (positioned before defaulted
  params — Python parameter ordering, not FastAPI's, forces this). `get_session_messages`
  calls `_check_session_ownership(session_id, _require_auth(request))` before touching
  the transcript store (`fastapi_app.py:1069`, above the `session_transcript` call at
  `:1072`); `cleanup_empty_sessions` calls `_require_auth(request)` (`:1018`) with a
  comment documenting that the operation stays global by design — there is no
  `session_id` to scope it to, and inventing one was explicitly out of scope.
- **Verified, not assumed:** the exported OpenAPI schema (`app.openapi()`) is
  byte-identical before and after this change — `Request` is a FastAPI-reserved
  injectable type, never a schema parameter, matching the 8 pre-existing
  `request: Request` session handlers. No OpenAPI/frontend-type regeneration was needed
  for this task, despite the originating task spec assuming the added parameter would
  change the schema.
- **Avoid:** when a per-request check needs a value only a `Request` can supply, a
  handler without a `Request` parameter cannot be checked *at all* — the omission is
  invisible in a route-by-route review because there is no missing line to spot; audit
  signatures, not just bodies. Separately, and just as easy to get wrong in the test that
  locks this: `set_session_owner` silently no-ops for a session that is not "live"
  (absent from `control_plane._connections`), and a `None`/missing owner means *allow* —
  so a regression test that stamps a foreign owner and then asserts a 403 must first
  assert `get_session_owner() == <stamped digest>`, or it risks passing for the wrong
  reason (an ownerless session, same as every session after a restart) instead of
  exercising the check at all.
- **Locked by:** `tests/test_session_route_ownership.py` —
  `test_session_messages_denies_a_foreign_owner` (asserts the foreign-owner stamp landed,
  then asserts the live HTTP 403), `test_session_messages_still_serves_the_owner` (normal
  single-principal path unchanged).

### BB-109 — OpenAI facade shared one `"__default__"` session across all callers, unchecked

- **Symptom:** two unrelated OpenAI clients calling `POST /v1/chat/completions` (non-streaming) got
  each other's conversation history and each other's accumulated agent memory. No 403 was reachable
  for a caller who did not own the session.
- **Root cause:** two failures that compound. (1) The route called
  `control_plane.complete_chat(prompt, owner_token_hash=...)` without a `session_key`, so every
  caller resolved to the `"__default__"` default in `complete_chat`/`get_or_create_session`.
  (2) `_check_session_ownership` had 10 call sites in `fastapi_app.py` and NONE inside the facade —
  and `get_or_create_session` stamps `owner_token_hash` only when it CREATES a session, so every
  later caller reused a session owned by someone else with nothing comparing them. BB-48 fixed the
  *stamping* half in an earlier pass and reads as though the issue was closed; the *checking* half
  was never added.
- **Fix:** the non-streaming path now keys sessions `f"openai:{principal}:{conversation}"`
  (`principal` = the caller's token hash or `"anon"`; `conversation` = the client's OpenAI `user`
  field or a fresh per-request UUID), and `_check_session_ownership` runs BETWEEN
  `get_or_create_session` and `send_message` on both the non-streaming and streaming paths
  (`src/hachi/gateway/fastapi_app.py`, `openai_chat_completions` / `_openai_event_stream`).
  `OpenAIChatRequest` gained an explicit `user: str | None = None` field instead of riding on
  `extra="allow"`, so it is now declared in the OpenAPI schema (confirmed by diffing
  `app.openapi()` against the checked-in spec — the `user` field is the only change, no routes
  added or removed).
- **Avoid:** a `session_key` parameter with a shared literal default is a loaded gun — any call site
  that forgets it silently joins a global session. When a route resolves a session id, the ownership
  check is part of resolving it, not an optional extra — and it must run BEFORE the side effect,
  never after: `complete_chat` used to return the session id only once the turn had already run, so
  the fix splits it into `get_or_create_session` → `_check_session_ownership` → `send_message`.
- **Locked by:** `tests/test_gateway_openai_channels.py` —
  `test_openai_facade_does_not_share_one_default_session`,
  `test_openai_facade_rejects_foreign_session_owner`.

### BB-110 — OpenAI-facade streaming (and, after T-32, ephemeral non-streaming) requests leaked one booted session each, until the cap jammed permanently

- **Symptom:** after ~100 streaming `POST /v1/chat/completions` requests the gateway rejected ALL
  new sessions — including the web UI's — with "Maximum active sessions reached (100)",
  permanently, until the process was restarted. `GET /readiness` showed `active_connections`
  climbing monotonically and never falling. Once T-32 landed, every non-streaming request that
  omitted the OpenAI `user` field started minting its own per-request session too, and leaked
  identically.
- **Root cause:** the streaming path mints `session_key = f"openai-{chat_id}"` from a fresh UUID per
  request, so `get_or_create_session` always missed and booted a full agent — and `close_session`
  was never called anywhere in the facade. The cap in `GatewayControlPlane.create_session` is a hard
  raise with no LRU and no eviction, so the leak was terminal rather than degrading. Verified live
  while writing the regression test: exceeding a lowered cap mid-stream doesn't even surface as a
  clean error response — Starlette has already sent the `200` `http.response.start` event before the
  handler discovers the cap is full (`StreamingResponse.stream_response` sends the status before the
  first `body_iterator` chunk), so the failure comes out as an unhandled `RuntimeError` ("Caught
  handled exception, but response already started"), not a rewritable status code.
- **Fix:** `close_session(session_id)` is now called in `_openai_event_stream`'s existing `finally:`
  block, and around the non-streaming `send_message` call for sessions that used T-32's ephemeral
  (no-`user`) fallback — both off the event loop via `anyio.to_thread.run_sync`, guarded so a
  teardown error can never raise into a partially-sent response stream. Sessions keyed by a
  client-supplied `user` id are deliberately never closed here — that key exists precisely so the
  conversation persists across requests.
- **Avoid:** if a code path CREATES a session, that same path owns closing it — put the close in the
  `finally:` at the point of creation, not in a reaper. A hard cap with no eviction turns any session
  leak into a permanent outage, so "who closes this?" is part of writing the create, not a follow-up.
  Separately: once a `StreamingResponse` generator has yielded even once, the HTTP status is already
  on the wire, so a later exception from that generator cannot become a clean error response — a
  regression test that drives a session leak past a lowered cap mid-stream must expect (and assert
  on) the underlying exception rather than assume a same-status-family error response.
- **Locked by:** `tests/test_gateway_openai_channels.py` — `test_openai_streaming_closes_its_session`,
  `test_openai_streaming_does_not_approach_the_session_cap`,
  `test_openai_non_streaming_closes_its_ephemeral_session`,
  `test_openai_facade_user_keyed_session_persists_across_requests`.

### BB-111 — ToolRegistry.register silently clobbered duplicate tool names

- **Symptom:** Registering a tool name twice silently replaced the handler (last-wins) while the
  model kept seeing the first registration's schema (`get_schemas` de-dupes first-wins), so a
  tool's schema and its handler could come from two different mounts. `_schemas` also grew by one
  dead dict per duplicate, per boot. No exception, no log line, no test could detect it. The
  Google double-mount (see the BB-103 entry, fixed by T-35) shipped through this gap.
- **Root cause:** `core/loop.py` `register()` did `self._tools[name] = func` /
  `self._schemas.append(schema)` with no membership check — the only validation was that the
  schema had a non-empty `name`. Two subsystems independently worked around it instead of fixing
  it: `ToolRegistry.update_handler` exists solely for this (its docstring says so), and
  `_register_search_tools_for` hand-rolled idempotency for the same reason (checks `"web_search"
  in tool_registry._tools` before choosing `register` vs `update_handler`).
- **Fix:** `register()` now raises `ValueError` when the name is already present, and the message
  points at `unregister()` — the pre-existing public teardown API the MCP lifecycle already uses.
  Landed strictly after T-35 (BB-103), which removed the one double-mount that would otherwise
  have crashed boot for every fully-configured Google user — re-verified against source before
  this change landed: `CapabilityDescriptor.mounts_tools=False` on `google` (`gateway/
  orchestrator.py`) makes `register_google_tools(..., approver=action_approver)` (via `TOOL_SPECS`)
  the sole mount path, and the capability loop now skips `google` entirely.
- **Residual finding (reported, not fixed — out of file scope for this task):**
  `AgentSession.add_mcp_server_live` (`gateway/orchestrator.py:947-996`) calls
  `self.mcp_manager._register_server_tools(cfg.name, server, registry)` directly — the private,
  non-idempotent mount, with no `unregister` first — reachable from the CLI MCP-server-add command
  (`cli/main.py:1938-2010`) whose own comment documents that re-registering an *existing* server
  name is a supported case. This is a **live, not boot-time**, path: unreachable from `boot_agent`
  and untested against a real `ToolRegistry` (`tests/test_mcp_transport_and_config.py::TestHotReload`
  mocks both `registry` and `manager._register_server_tools`). Before this fix it silently
  duplicated a schema per re-add; after this fix, re-adding an MCP server under a name that is
  already live now raises `ValueError` instead. Confirmed by direct repro against real
  `MCPManager`/`ToolRegistry` objects. The already-idempotent sibling, `register_server_tools`
  (used by `set_mcp_tools_active`), unregisters before re-mounting and is unaffected — this is the
  pattern `add_mcp_server_live` should follow. Needs its own task; not fixed here because the fix
  lives in `gateway/orchestrator.py`, out of this task's scope.
- **Avoid:** A registry keyed by name must reject duplicates at write time. Anything that needs to
  re-mount calls `unregister()` first; anything that needs to swap only the callable calls
  `update_handler()`. Never re-`register()` to overwrite. When auditing a "make X strict" change,
  grep every call site of the function being tightened, not just the ones a doc named — the MCP
  hot-reload path above was found by tracing callers, not by the task spec.
- **Locked by:** `tests/test_tool_registry_duplicate_guard.py` — `test_duplicate_registration_raises`,
  `test_duplicate_registration_leaves_registry_unchanged`, `test_unregister_then_register_is_allowed`,
  `test_update_handler_still_works`.

### BB-112 — Tool groups lived in a hand-maintained orchestrator table, and it drifted

- **Symptom:** `use_tools("delegation")` unlocked `delegate_task` and `list_roles` but not
  `delegate_parallel_tasks`, which had silently landed in the `"integrations"` group instead. The
  same table also mapped `schedule_task`, a name no registered tool has ever used — the identical
  phantom name that produced BB-89's security hole in the sub-agent blocklist (same drift class,
  same file family, same week; T-34 fixed the blocklist, this fixes the grouping table).
- **Root cause:** Group membership was declared in `_GROUP_BY_TOOL_NAME` in
  `gateway/orchestrator.py`, hundreds of lines from every `register(...)` call, and consumed by a
  single `classify()` call with `default="integrations"`. A tool omitted from the table is not an
  error — it is silently absorbed by the default. Nothing validated the table's 27 hand-written
  names against real tool schemas: `delegate_parallel_tasks` was added to
  `delegation_tools.py` (`register_delegation_tools`) without a matching table entry, so
  `classify()` found it in neither `core_names` nor `group_by_name`, no prefix matched, and it fell
  through to `default="integrations"`. NOTE: `"integrations"` is a normal, activatable group
  (`available_groups()` includes it and `use_tools` accepts it) — the defect was *wrong group*,
  not *unreachable tool*; a nearby BB-32 comment saying "never activated" (about MCP tools, a
  different mechanism) does not apply here.
- **Fix:** Each non-core tool now declares `group="..."` at its own `register(...)` call
  (`classify()` rule 4 already left explicitly-tagged tools intact — this is not a new mechanism,
  just a wider use of one `use_tools`/per-skill-REST tools already relied on). `_GROUP_BY_TOOL_NAME`
  was deleted outright from `orchestrator.py` (not just corrected), and the production `classify()`
  call now passes `group_by_name={}`. `delegate_parallel_tasks` now carries `group="delegation"`.
  `_CORE_TOOL_NAMES` was deliberately kept unchanged — it is a central per-turn token-budget
  decision, not a per-tool fact, and has its own size-guard test
  (`test_tool_lazy_loading.py::test_core_tool_set_size_guard`). The Google `"google_"` prefix rule
  and the `google_tools.py` re-tag-by-privates poke were deliberately left in place (a convention
  rule can't drift the way a name table can; threading `group="google"` through nine
  `GoogleService.register_tools` implementations is a separate, larger task).
- **Verified, not assumed:** every real `register_*_tools` function was called directly (the same
  functions `TOOL_SPECS` calls at boot) and the resulting `ToolRegistry._groups` was diffed against
  the old table's 26 live entries — zero mismatches, plus the one intended change
  (`delegate_parallel_tasks: integrations → delegation`).
- **Residual finding (reported, not fixed — file is out of scope for this task):**
  `scripts/measure_turn.py:121,152` (`build_core_registry()`) imports `_GROUP_BY_TOOL_NAME` and
  passes it to `classify()`; deleting the table breaks that import and, transitively,
  `tests/test_measure_turn.py` (7 errors) and `tests/test_core_tool_cost_guard.py` (4 failures) —
  neither in this task's scoped test list. The fix is mechanical (drop the name from the import,
  pass `group_by_name={}` at its `classify()` call, mirroring `orchestrator.py`) but `scripts/` is
  owned by another workstream in this wave; needs a one-line follow-up there.
- **Avoid:** Metadata about a tool belongs at the tool's registration, not in a central table
  keyed by tool-name strings. If a table keyed by tool names is unavoidable, it needs a test that
  resolves every entry against a real schema — see the BB-89 entry for what this same rot costs
  when the table is a security boundary instead of a UX one. Before deleting a module-level name
  that other files import, grep the whole tree (not just `tests/`) for its usage — a script under
  `scripts/` can be a live, un-scoped consumer that a test-file-only search misses.
- **Locked by:** `tests/test_tool_groups_at_registration.py` —
  `test_all_delegation_tools_share_the_delegation_group`,
  `test_register_delegation_tools_wires_all_three_into_delegation`,
  `test_core_tool_names_all_reference_real_tools`.

### BB-113 — A dead gateway rendered as a healthy TUI cockpit full of fabricated governance data

- **Symptom:** With the gateway down or unreachable, `hachi run` showed a fully populated
  cockpit: a pending medium-risk approval to write `~/Documents/report.md` (with a working
  Allow button that would 404), a spend figure, and an audit log — none of it real. Nothing on
  screen indicated a problem, and the only trace was a `log.debug` line invisible at the
  default level.
- **Root cause:** `CockpitData` (`src/hachi/tui/cockpit/data.py`) documented and implemented
  "methods NEVER raise — they always return real or placeholder data." All 25 read methods
  wrapped the client call in `except Exception: log.debug(...)` and fell through to
  `placeholder_*()` from `hachi.client.gaps`, so a *failed live call* (`client is not None`,
  the call raised) was indistinguishable from *deliberate offline mode* (`client is None`).
  `decide_approval` was the lone honest method — it already returned `{}` on error instead of
  fabricating a success response.
- **Fix:** Split the two cases. `client is None` (deliberate offline) still returns
  placeholders — untouched, still pinned by `TestCockpitDataOffline`. `client is not None` and
  the call raised now records a reason via `_degrade(key, exc)`, logs at WARNING (all 25
  `log.debug` fallbacks moved to `log.warning`, including the 8 already-honest mutation
  methods for consistency — see BB-71), and returns an honest empty value (`[]`, `([], [])`,
  or a neutral model built explicitly from `client/models.py`, e.g. `TrustBarSnapshot` fields
  use `"—"` rather than `""` so `trust_bar.py`'s `snap.tier or "guided"` truthy-fallback can't
  quietly reintroduce a fabricated default); a later success clears the flag via
  `_clear_degraded(key)`. The four governance-facing views (`approvals`, `audit`, `resources`,
  `dashboard`) check `data.degraded(key)` and render an explicit "⚠ can't reach Hachi — …
  unknown, not empty" message — in a DANGER color, not the reassuring empty state's MUTED —
  instead of their normal empty/zeroed content.
- **Avoid:** A fallback must distinguish "no data" from "could not fetch." Never let a caught
  exception return the same value (or the same placeholder) as a successful-but-empty result,
  and never log a swallowed failure at DEBUG (BB-71). When a DEGRADED state is added, verify it
  actually renders differently from both the populated state *and* the reassuring empty state
  — a data-layer flag nobody reads, or an empty-text swap that keeps the exact same muted
  styling as "all clear," has not solved the problem.
- **Locked by:** `tests/test_cockpit_degraded.py` — `test_failed_live_call_returns_empty_not_placeholder`,
  `test_failed_live_call_records_a_degraded_reason`, `test_offline_mode_still_returns_placeholders`,
  `test_recovered_call_clears_the_degraded_flag`, plus four view-level render tests
  (`test_approvals_view_renders_degraded_not_fabricated_row` et al.) that assert on the actual
  rendered table rows / panel text, not just the facade's return value.

### BB-117 — Failover spend was booked against the primary model, not the fallback that served the turn

- **Symptom:** when a provider failed and a configured fallback served the turn, `/spend`
  and the per-model breakdown showed usage for the **primary** model — at the primary's
  price — and zero for the model that actually ran. Cost caps tripped early or late during
  exactly the failover windows where the numbers matter most.
- **Root cause:** `ResilientClient.__init__` captures `self.model` once from the primary
  (`core/resilience.py`, `__init__`) and `_call_with_chain` never updates it while walking
  the chain — even though it reads the fallback's model to *log* the failover a few lines
  below. `core/loop.py`'s cost-tracking read then attributed cost via
  `getattr(self._turn_client, "model", "unknown")`, which is always the (unchanged) primary
  regardless of who served the turn. The streaming happy path (`ResilientClient.generate_stream`)
  compounds this: it never goes through `_call_with_chain` at all, so a stamp added only
  there would have missed almost every real successful turn.
- **Fix:** added `served_model: str | None = None` to `LLMResponse` (`core/llm.py`,
  a pydantic `BaseModel` — a defaulted field touches zero of the 36 existing
  `LLMResponse(...)` construction sites), populated at all four production sites in
  `core/llm.py`: `AnthropicClient._parse_message` (from the provider's own reported
  `Message.model`, covering both the non-streaming and streaming Anthropic paths since both
  route through it), `OpenAICompatibleClient.generate` (from the response body's `"model"`
  field, falling back to the configured model when the endpoint omits it — both its
  empty-choices early return and its main return), and `OpenAICompatibleClient.generate_stream`
  (the configured model — the SSE chunks parsed there carry no per-chunk model field worth
  relying on, verified rather than assumed per CLAUDE.md Rule 4). Backfilled in
  `ResilientClient._call_with_chain` (stamps from whichever chain element actually produced
  the response, guarded by `isinstance(result, LLMResponse)` since the chain is generic) and
  on `generate_stream`'s streaming success path (stamps from `self._inner` when the client
  itself left it unset) — the fallback-degrade path already routes through
  `_call_with_chain`, so it inherits the same backfill for free. `core/loop.py`'s
  cost-tracking site now prefers `response.served_model`, falling back to the old
  `getattr(self._turn_client, "model", "unknown")` for any client that leaves the field unset.
  Left `core/eval/corpus.py` (26 golden fixtures) and `core/unofficial_providers.py` (5
  sites, opt-in) untouched — both are covered by the chain backfill and neither needed
  editing since the field is defaulted.
- **Avoid:** identity of *who served a call* belongs on the RESPONSE (per-call), never on a
  long-lived client attribute (shared, and stale the moment a chain fails over). Any
  `getattr(client, "model", ...)` on a decorated client is suspect. When a decorator has
  more than one code path back to the caller (streaming happy path vs. fallback-degrade vs.
  non-streaming chain), a fix added to only one path silently misses the others — enumerate
  every return path before calling a stamp "done".
- **Locked by:** `tests/test_served_model_attribution.py` (all four tests, incl.
  `test_cost_is_attributed_to_the_fallback_that_served_the_turn`, which asserts on
  `CostTracker.per_model_snapshot()` — the real observed behaviour, not just the field's
  value) and `tests/test_streaming.py`'s five `*_surfaces_served_model`/
  `*_falls_back_to_configured_model` tests pinning the three `llm.py` production sites.

### BB-118 — Enabling `[routing]` silently degraded the pre-flight token-safety ceiling to a post-hoc check

- **Symptom:** with routing enabled, a single runaway generation could cross the 2,000,000-token
  safety ceiling by its own full cost before anything noticed. With routing disabled the same
  ceiling refused the call up front. No error, no log — the guardrail just got weaker when a
  cost feature was switched on.
- **Root cause:** `ResilientClient.__init__` forwarded only `model` from the wrapped client
  (`core/resilience.py:347`); there is no `max_tokens` attribute and no `__getattr__`. The
  loop's pre-flight check reads `getattr(worst_case_next_call, "max_tokens", 0) or 0`
  (`core/loop.py:831`, `_safety_ceiling_exceeded`), so it got 0, making
  `projected == snap.total_tokens` — literally the post-hoc check the S1 docstring right above it
  says it replaced. With `router is None`, `loop.py:947` leaves `_turn_client` as the raw
  `AnthropicClient` (which has `max_tokens`, `llm.py:279`), so the bug was invisible in the
  default configuration. `tests/test_loop_backstop_cancel.py`'s
  `test_token_safety_ceiling_is_preflight_not_post_hoc` covered the ceiling with a fake that DID
  expose `max_tokens` directly — green while the routed path was inert.
- **Fix:** forward `max_tokens` explicitly beside `model` in `ResilientClient.__init__`
  (`core/resilience.py`). Deliberately NOT a blanket `__getattr__` — that would mask the next
  missing attribute the same way a defaulted `getattr` masked this one.
- **Avoid:** a decorator/wrapper class must forward every attribute its callers read, and a
  `getattr(obj, name, <default>)` against your OWN type is a code smell: it converts a
  missing attribute into a plausible value instead of an error. When wrapping, test through
  the wrapper, never against the bare inner object.
- **Locked by:** `tests/test_loop_backstop_cancel.py` —
  `test_token_safety_ceiling_is_preflight_through_a_resilient_client`

### BB-119 — Channel and schedule sessions were invisible to /sessions, audit and spend — and never governed at all

- **Symptom:** A Telegram/WhatsApp conversation, or a task fired by `hachi schedule run-due` /
  `run-now`, never appeared in `GET /sessions`, contributed nothing to the permission-audit view,
  and was absent from spend reporting — even with a gateway runtime live in the same process. A
  gated action taken from one of these sessions never reached the approval registry either: it
  silently auto-approved (`unrestricted`) or instantly denied (`guided`/`sandbox`) with no request
  ever created.
- **Root cause:** Four boot sites called `boot_agent()` directly instead of going through
  `_InProcessGatewayRuntime`'s session-registration bookkeeping: `GatewaySessionRunner`
  (constructed twice in `cli/commands/channel.py`, once for Telegram and once for WhatsApp) kept
  its own private `_agents` dict, and `cli/commands/schedule.py` called `boot_agent()` as a bare
  local at **both** `schedule_run_due` (`:169`) and `schedule_run_now` (`:205`) — the second site
  is easy to miss and the architecture review's own remediation list missed it entirely. Every
  gateway read model (`recent_sessions`, `permission_audit`, `subagent_reports`, `mcp_inventory`,
  ...) resolves through `_InProcessGatewayRuntime._agents`, so an agent in any other map — or in
  no map at all — is structurally unreachable. Independently, because `approval_surface="gateway"`
  (BB-90 / T-01) is only threaded through `_boot_session_agent`, none of these four sites ever
  selected `RegistryApprover` either — **not** an orphaned-approval bug (the review's framing);
  no approval was created on these paths at all.
- **Fix:** Added `_InProcessGatewayRuntime.boot_registered_session(client_id, surface)`
  (`cli/main.py`), which reuses `_boot_session_agent` (hence `approval_surface="gateway"`) and a
  new shared `_register_agent` helper (factored out of `register_connection`'s own bookkeeping, so
  the two registration paths cannot diverge). Channels pass it through the existing
  `GatewaySessionRunner(boot=…)` seam via a new `_governed_channel_boot` /
  `_build_channel_runner` pair in `cli/commands/channel.py`; both schedule sites call a new
  `_boot_scheduler_agent()` in `cli/commands/schedule.py`. When no gateway runtime is active in
  the process (`_ACTIVE_RUNTIME is None` — the common case for a standalone channel process or a
  cron-invoked `run-due`), both fall back to a direct `boot_agent()` and log a WARNING naming
  exactly what is degraded, rather than failing or staying silent. Interim pending the CF-2
  runtime extraction (T-41); `channels/runner.py` is unchanged — the `boot=` seam it already
  exposed was sufficient.
- **Avoid:** every entry point that creates an agent session must register it in the one session
  map the read models query — a second private map (or no map) makes the sessions invisible
  rather than merely unmanaged. And a governance property (which approver a session gets) rides
  on the SAME boot path as visibility — routing a session through the registered boot helper is
  what makes both true simultaneously; testing only "was a function called" would have missed
  either half.
- **Locked by:** `tests/test_governed_session_entry_points.py` —
  `test_channel_runner_boots_through_the_gateway_runtime`,
  `test_schedule_run_due_boots_through_the_gateway_runtime`,
  `test_schedule_run_now_boots_through_the_gateway_runtime` (plus WhatsApp and no-gateway-fallback
  coverage in the same file). Cross-references BB-90 (T-01), whose `approval_surface`/
  `select_action_approver` mechanism this task threads through the two non-web entry points.
- **Follow-up (same BB-N, caught by the full-suite gate, not the scoped run):** the new
  `schedule.py` / `channel.py` dependency on `hachi.cli.main._ACTIVE_RUNTIME` made
  `tests/test_schedule_run_now.py::test_run_now_known_schedule_success` and
  `::test_run_now_agent_error_exits` order-dependent — they passed in isolation but failed in the
  full suite (`2 failed, 4463 passed`). Root cause: `_ACTIVE_RUNTIME` is reassigned by *production*
  code (`cli/main.py`, `global _ACTIVE_RUNTIME; _ACTIVE_RUNTIME = runtime`, fired whenever a real
  `_InProcessGatewayRuntime` boots, e.g. via `_start_gateway_server`), not only by tests through
  `monkeypatch` — and `monkeypatch` only reverts its own `setattr` calls, so a test that boots a
  *real* runtime (`tests/test_cockpit_gaps.py` reproduces it directly) leaks the global into every
  later test in the same worker that reads it. Fixed with a third autouse fixture,
  `_isolate_active_gateway_runtime` in `tests/conftest.py`, matching the existing
  `_restore_os_environ` fixture's shape immediately above it (same bug class: a global mutated by
  production code, not just by monkeypatch-tracked tests) — snapshot/restore unconditionally so it
  corrects the leak regardless of how the global was mutated, and is a no-op for tests that already
  isolate it correctly via `monkeypatch`. **Avoid:** when a fix introduces a new module-global read
  from a shared/CLI-owned module, check whether that global is *also* mutated by production code
  (not just by tests) before assuming `monkeypatch`-based tests are sufficient protection — grep for
  every `global <name>` assignment site, not just the test call sites. **Locked by:**
  `tests/test_governed_session_entry_points.py` —
  `test_direct_mutation_of_active_runtime_does_not_leak_to_later_tests` /
  `test_active_runtime_is_clean_after_the_previous_test` (a declaration-order-dependent pair that
  pins the fixture's cross-test-boundary guarantee directly, independent of full-suite ordering).

### BB-120 — `add_mcp_server_live` re-add crashed under T-36's duplicate-registration guard

- **Symptom:** re-registering an already-live MCP server (a supported action —
  `cli/main.py`'s register-server path re-registers an existing server name in
  place, and `AgentSession.add_mcp_server_live` is called from a live session's
  `if agent is not None:` gate) raised `ValueError` instead of refreshing the
  server's tools.
- **Root cause:** `add_mcp_server_live` (`gateway/orchestrator.py`) called the
  private, non-idempotent `MCPManager._register_server_tools` directly, with no
  `unregister` first. T-36 (BB-111) had correctly added a duplicate-name guard to
  `ToolRegistry.register` to stop tool schemas silently duplicating on re-add —
  but that guard turned this same call path into a hard crash on re-add, because
  nothing unregistered the server's existing tools before re-mounting them.
- **Fix:** `add_mcp_server_live` now calls the public, idempotent
  `MCPManager.register_server_tools` (`orchestrator.py` ~:966), which unregisters
  any stale tools for the server name before re-mounting — so a re-add refreshes
  the server's tools in place instead of hitting `ToolRegistry.register`'s
  duplicate-name guard.
- **Avoid:** re-adding an already-live resource (MCP server, skill, etc.) is a
  supported user action, not an edge case — always route it through the
  idempotent public API (`register_server_tools`), never the private
  `_register_server_tools`/equivalent, or a duplicate-registration guard added
  for a different reason (T-36 was about duplicate tool schemas, not re-add)
  will turn a supported action into a crash.
- **Locked by:** `tests/test_mcp_lifecycle.py` —
  `TestAddMcpServerLiveReregister::test_readd_live_server_does_not_raise_and_has_no_duplicate_schema`.
  (Fix shipped in an earlier commit; this entry was owed and is being added
  alongside T-08/BB-126.)

### BB-121 — Two implemented provider kinds were rejected by the config schema

- **Symptom:** setting `[provider] kind = "gemini"` (documented at
  `docs/guides/configuration.md:385`, and the natural free-tier choice for a user with
  no Anthropic key) crashed config loading with a pydantic `literal_error`. `hachi
  doctor` separately reported `unknown provider kind: 'gemini'`. Same for
  `chatgpt-web`.
- **Root cause:** `core/llm.py create_llm_client` implements `kind == "gemini"` and
  `kind == "chatgpt-web"`, but `ProviderKind` in `core/settings.py` is a `Literal` that
  listed neither, so `ResolvedConfig.model_validate` rejected the value at
  `config_loader.load_effective_config`. `ConfigDict(extra="ignore")` does not help —
  this is an invalid *value* for a declared field, not an unknown key. The rejection was
  doubled because `core/config_validate._VALID_KINDS` derives from the same literal.
- **Fix:** added both kinds to `ProviderKind` (the `HACHI_UNOFFICIAL=1` ToS gate for
  `chatgpt-web` stays at the client-construction boundary in `core/llm.py`, which is a
  better place to enforce it than a parse error), documented `gemini` in
  `settings.example.toml`, and added a ToS note to the configuration guide. Locked with
  an AST-derived test that fails whenever `create_llm_client` grows a `kind == "..."`
  branch with no matching `ProviderKind` member.
- **Avoid:** a provider is not supported until the schema accepts it. When you add a
  branch to a factory keyed on a config value, extend the `Literal` in the same commit —
  and prefer a test that derives the expected set from the implementation over one that
  hardcodes it, or the two lists drift again.
- **Locked by:** `tests/test_provider_kinds.py` —
  `test_provider_kind_literal_covers_every_implemented_kind`

### BB-122 — Pending approvals carried no deadline; the enforced 180 s timeout was unreadable from outside the waiting thread

- **Symptom:** No client could tell how long an approval had left. `list_pending()`
  returned six fields, none temporal, and the `permission.requested` SSE event the same
  — so the gateway invented a 60 s `expiresAt` to fill the gap (later deleted by T-15)
  and the web UI hardcoded `expiresAt: 0` and hid the countdown entirely.
- **Root cause:** `_PendingEntry` (`core/approvals.py`) had three slots — request, event,
  decision — and no time. The timeout was a `RegistryApprover.__init__` argument
  consumed once as a stack-local inside `threading.Event.wait(timeout=…)`, so it existed
  only for the duration of the blocking call and was never recorded anywhere readable.
- **Fix:** `_PendingEntry` now records `created_at_ms` and `expires_at_ms` (wall clock,
  epoch ms — comparable to JS `Date.now()`), computed from a single `time.time()` call
  so the two can never drift apart. `PendingApprovalRegistry.create()` accepts an
  optional `timeout` (the value that will actually be enforced) and a new
  `_deadline_for()` accessor lets `RegistryApprover._build_and_park` read back the exact
  stored values — never a second clock read. `list_pending()` and the
  `permission.requested` payload surface both as `created_at` / `expires_at`, `None`
  when no timeout was supplied (an honest absence, not a fabricated default).
  `HandlerApprover` is unchanged — it never parks anything in the registry.
- **Avoid:** if a value governs behaviour, record it where the state lives — a policy
  that exists only as a stack-local inside a blocking call cannot be reported, and the
  gap will be filled with a fabrication. When two payloads must report the identical
  clock read (an entry and its SSE event), compute the timestamp once and read it back;
  never call `time.time()` twice for what is conceptually one moment.
- **Locked by:** `tests/test_action_approval.py` —
  `test_pending_entry_carries_the_deadline_that_is_actually_enforced`,
  `test_entry_parked_without_a_timeout_reports_no_deadline`

### BB-114 — The gateway's error envelope was absent from its own OpenAPI spec

- **Symptom:** `openapi/hachi-openapi.json` had no `ErrorResponse`, so generated clients had no
  typed error shape — while the gateway returned `{code, message, details}` from 7 exception
  handlers and 21 in-route returns (28 emissions total). The frontend hand-mirrors the envelope
  in `parseError` (`frontend/src/lib/data/client.ts:104-121`) instead of importing it.
- **Root cause:** FastAPI only emits a model into `components.schemas` when a route DECLARES it
  (`response_model=` or `responses=`). `grep -c "responses=" src/hachi/gateway/fastapi_app.py`
  returned 0 before the fix — not one route or router declared any error response, and
  `ErrorResponse` was only ever constructed inside function bodies and serialised into a bare
  `JSONResponse`.
- **Fix:** a shared `_ERROR_RESPONSES` mapping (400/401/403/404/409/410/429/500/503 — 422
  deliberately excluded, FastAPI's `RequestValidationError` handler already owns it) passed as
  `responses=` on each of the gateway's 19 `APIRouter`s (`system` gets a reduced mapping without
  401, since its public routes cannot return an auth error). Purely additive — no runtime
  behaviour changed (`raise HTTPException` stayed at 46 sites, `_error_response(` at 29).
- **Avoid:** returning a `JSONResponse` built from a Pydantic model does NOT put that model in
  the spec. If a shape is part of your contract, some route has to declare it. And when you lock
  a contract with a test, don't stop at "the schema has the right key names" — trigger the real
  response and validate the real body against the real model, so spec and runtime can't quietly
  drift apart again.
- **Known and NOT fixed (separate decision):** 46 `raise HTTPException` sites still return
  Starlette's `{"detail": ...}`, a different shape from the envelope. Unifying them is a breaking
  wire-format change affecting the frontend's `ApiError` decoding.
- **Locked by:** `tests/test_gateway_openapi_contract.py` —
  `test_openapi_declares_the_error_envelope`, which (beyond checking the schema/`$ref` shape)
  triggers a real 401 via `TestClient` and validates the actual response body against the real
  `ErrorResponse` model.

### BB-115 — The generated OpenAPI types were CI-gated but imported by nothing, because almost nothing was typed

- **Symptom:** `frontend/src/lib/data/schema.d.ts` (thousands of generated lines) had zero
  importers, while the real frontend↔gateway contract lived in 60 hand-written
  `interface Gateway*` blocks across 14 domain files. The `check:api` step regenerated and
  diff-gated a file no code read, so it could never catch a frontend/gateway disagreement — the
  exact regression class the repo had already suffered ("FE wired to nonexistent gateway
  routes"). `og.ts`'s hand-written `GatewayOgGraph` had already drifted from the real payload:
  it declared only `nodes`, while the backend always returns `{nodes, count}`.
- **Root cause:** only 9 of ~100 gateway operations declared a `response_model`. The other 91,
  including `GET /og/graph`, were annotated `-> Any` with no `response_model=`, so FastAPI
  published an EMPTY schema (`"application/json": unknown` in the generated TypeScript) — there
  was almost nothing a willing developer could have imported even if they'd tried.
- **Fix:** piloted on the smallest domain. Added `OgNodeModel`/`OgGraphResponse`
  (`src/hachi/gateway/fastapi_app.py`) and `response_model=OgGraphResponse` to `GET /og/graph`
  (typed-operation count 9 → 10), regenerated, and rewrote
  `frontend/src/lib/data/domains/og.ts` to derive its raw types from
  `components["schemas"][...]` instead of hand-mirroring them. The adapter layer into
  `types.ts` (`adaptOgNode`) is unchanged except for being exported so `og.test.ts` can call the
  real function instead of a re-implemented copy.
- **Avoid:** a route annotated `-> Any` with no `response_model=` publishes an EMPTY schema.
  Before claiming an OpenAPI document is a contract, count how many of its operations actually
  declare a response — and before adding a CI gate on a generated artifact, confirm something
  imports it. Separately: `response_model=` is a runtime filter, not just documentation — it
  fills in declared defaults for fields the underlying dict omits (here, a `count: int = 0`
  default would have turned the honest no-handler fallback `{"nodes": []}` into
  `{"nodes": [], "count": 0}` on the wire, breaking two pre-existing tests that pinned the old
  shape). `response_model_exclude_unset=True` fixes this the right way: a field is only emitted
  when the underlying dict actually set it, so the real payload (which always sets `count`)
  keeps reporting it while the honest fallback keeps its old, honest shape — verified against
  the installed FastAPI/pydantic source (`TypeAdapter.dump_python(..., exclude_unset=True)`),
  not assumed.
- **Locked by:** `tests/test_gateway_openapi_contract.py` —
  `test_og_graph_route_declares_a_response_schema`;
  `frontend/src/lib/data/domains/og.test.ts` — `adaptOgNode` tests (imports the real exported
  function, not a re-implemented copy).

### BB-116 — Four stacked retry layers turned one failed LLM call into up to 13-21 provider calls and up to ~9-12s of blocked thread

- **Symptom:** a transient provider failure took several seconds to surface as an error, during
  which a gateway worker thread was parked, and the provider received far more calls than a
  single retry policy should allow for a single turn. Whether an Anthropic 529 failed over to
  the configured fallback model or just burned loop retries against the same overloaded primary
  depended on which of two layers saw it first.
- **Root cause:** four independent retriers with no knowledge of each other — (1)
  `core/loop.py`'s `_stream_generate` (deleted by this fix), 3 attempts, backoff 1.0/2.0s; (2)
  `core/resilience.py`'s `resilient_call`, 3 attempts, backoff 0.5/1.0s (default); (3) the
  fallback chain (`ResilientClient._call_with_chain`) wrapping *each* element in (2); and (4) the
  `anthropic` SDK's own `DEFAULT_MAX_RETRIES = 2` (verified against the installed **0.105.2**
  package, `_constants.py:10` / `_client.py:156`), never overridden. Worse, (1) and (2) used
  unrelated definitions of "transient": `loop._RETRYABLE_STATUS` = `{408,409,429,500,502,503,
  504,529}` with message-substring fallback, vs `resilience._TRANSIENT_STATUS_CODES` =
  `{429,500,502,503,504}` with exception-class-name matching — so 408/409/529 retried at one
  layer and were terminal at the other. Streaming was the worst case:
  `ResilientClient.generate_stream` called the primary directly with no retry of its own, then
  degraded to `generate()` over the chain with `include_primary=False` on ANY failure.
  **Correction to the architecture review's own arithmetic (verify-by-measurement, not
  recomputation):** the review's naive "3 loop attempts × (1 + 3 + 3 chain attempts) = 21 calls,
  ~16s" (and this pack's corrected "~12s") assume every retry attempt actually executes. It
  doesn't hold once the (unrelated, pre-existing, untouched-by-this-task) circuit breaker enters
  the picture: `HealthRecorder` objects are reused across the loop's repeated top-level retries
  (not reset between them), so a fallback's `consecutive_failures` carries over and the breaker
  (`breaker_threshold=5` default) can open partway through, short-circuiting later attempts
  without calling the client at all. **Measured** (not recomputed) against
  `tests/test_resilience.py::test_streaming_failure_does_not_amplify_provider_calls`'s scenario:
  pre-fix **13 provider calls** (not 21) at `backoff_base_s=0.0`, and **9.01s** of real sleep (not
  12s or 16s) at the framework's real default backoff constants. Post-fix: **9 calls, 4.50s**
  (matches the target arithmetic exactly — the breaker never trips at that lower volume).
- **Fix:** resilience is the sole retry owner. One `is_transient()` (`core/resilience.py`, the
  union of both status sets `{408,409,429,500,502,503,504,529}` + class-name matching + the
  loop's old message-hint fallback for exceptions carrying neither) used by everything;
  `core/loop.py`'s `_is_retryable_llm_error`/`_RETRYABLE_STATUS`/`_RETRYABLE_HINTS`/
  `_MAX_LLM_RETRIES`/`_LLM_RETRY_BASE_DELAY` deleted; `_stream_generate` is now a single pass
  (drive the generator, propagate the exception — no retry). `ResilientClient.generate_stream`
  gained its own bounded pre-output retry (same `RetryPolicy`, same backoff formula as
  `resilient_call`), gated on `chunks_yielded == 0` — the exact H7 "never re-invoke a stream that
  already emitted output" rule, moved rather than lost. The `LLM_RETRY` runtime event is
  re-homed onto a new `ResilientClient.on_retry` sink (set by
  `ExecutionLoop._wire_retry_events` whenever `_turn_client` is a `ResilientClient`) fired from
  both `resilient_call`'s existing `on_failover` hook and the new streaming retry, so the
  UI-visible "retrying…" signal survives even though loop.py no longer retries anything itself.
  A **bare (non-resilient) `_turn_client` now gets zero retries** — this is intentional: wrap it
  in a `ResilientClient` to opt in (most production sessions do, via `ModelRouter`; a few direct
  paths — e.g. `core/sub_agent.py`'s delegation client — do not, and lose the old loop-level
  retry as a result; noted, not fixed, out of this task's scope).
  **SDK `max_retries` decision:** left at the SDK's default (`2`, i.e. up to 3 HTTP requests per
  resilience-level call) rather than forced to `0`. Overriding it lives in `core/llm.py`
  (`anthropic.Anthropic(...)` construction), outside this task's file scope, and — more
  importantly — several call sites construct `AnthropicClient`/`OpenAICompatibleClient` directly
  without ever wrapping them in a `ResilientClient` (`core/sub_agent.py`, `tools/memory_tools.py`);
  for those, the SDK's own retry is the *only* resilience they have, and forcing it off everywhere
  would have stripped it from paths this task cannot see, let alone fix. Documented in
  `core/resilience.py`'s module docstring.
  **BB-95 residual gap also closed:** `ModelRouter._build_client`'s primary path
  (`provider.build_llm_client(desc.id)`) now forwards `timeout_s=self._resilience.timeout_s`
  too (previously only the `create_llm_client` fallback did), and
  `AnthropicProvider`/`OpenAICompatProvider.build_llm_client` now pop+forward it. The separate,
  still-open half of BB-95 (`_build_client` also hardcodes `max_tokens=4096` regardless of the
  caller's configured budget) is **not** touched here — different bug (a token-budget guardrail,
  not a retry/timeout concern), and a different task tracks `max_tokens` forwarding (T-27,
  already landed, fixed `ResilientClient` forwarding `inner.max_tokens` to the loop's pre-flight
  ceiling — a related but distinct gap from `_build_client`'s hardcoded construction default).
- **Avoid:** exactly one component may own retry for a given operation. When adding a resilience
  wrapper around existing code, DELETE the retry it replaces in the same commit — and check the
  vendor SDK, which usually retries too. Separately: **a worked-example arithmetic in a design
  doc or review is a hypothesis, not a fact** — a pre-existing, unrelated mechanism (here, a
  circuit breaker whose state persists across the very retry attempts being counted) can make
  naive multiplicative call-count math wrong in either direction; measure the real number in the
  regression test, and if the theoretical and measured numbers disagree, trust the measurement
  and say so.
- **Locked by:** `tests/test_resilience.py` —
  `test_streaming_failure_does_not_amplify_provider_calls`,
  `test_one_transient_taxonomy_for_loop_and_resilience`;
  `tests/test_llm_retry_event.py` (new) —
  `test_llm_retry_event_fires_from_resilient_client_streaming_retry`,
  `test_llm_retry_event_fires_from_fallback_chain_retry`,
  `test_loop_module_has_no_second_retry_predicate`;
  `tests/test_loop_resilience.py::test_transient_llm_error_is_retried` (amended — see its
  docstring for why the amendment is a correction, not a weakening);
  `tests/test_routing_router.py::TestResilienceTimeoutForwarding::
  test_primary_provider_path_forwards_configured_timeout` (the BB-95 residual-gap half).

### BB-123 — Demo fixtures shipped in every production web bundle (untree-shakable by construction)

- **Symptom:** `npm run build` with `VITE_HACHI_USE_MOCKS` unset still emitted the entire
  394-line fixture corpus (`frontend/src/lib/data/fixtures.ts`) into the production
  bundle's main entry chunk — confirmed by building and running the new
  `scripts/check_bundle.py`, which reported 6 fixture-only markers (e.g. `"Your name is
  Sam. You go by Sam in messages"`, `"can't reach 127.0.0.1:5432"`) inside
  `dist/assets/index-*.js` before the fix; a raw grep also confirmed
  `priya@studio.co`/`"Sending email to Priya"` were present.
- **Root cause:** `frontend/src/lib/store/store.ts` statically imported `../data/fixtures`
  and referenced every binding unconditionally inside `domainDefaults()` (~30
  `USE_MOCKS ? fixture : liveDefault` ternaries). `USE_MOCKS` is a *computed* export
  (`String(import.meta.env.VITE_HACHI_USE_MOCKS).toLowerCase() === "true"`, `client.ts:30`),
  not a literal, so Rollup could never constant-fold the ternaries or prove the
  `USE_MOCKS ?` arms dead, and retained the whole module. Nothing in the gate inspected
  build output.
  Switching to a dynamic `import("../data/fixtures")` (still gated at runtime on the same
  `USE_MOCKS`) is **not sufficient on its own** — verified by building both ways. The
  culprit is specifically `String(x).toLowerCase()`: no minifier evaluates arbitrary
  `String.prototype` calls, even once Vite has replaced `import.meta.env.X` with a literal
  object, so Rollup still can't prove the `await import(...)` unreachable and still emits
  `fixtures.ts` as its own chunk (`fixtures-<hash>.js`) in *every* build — merely
  unreferenced from the entry HTML, not absent from `dist/assets/`. A guard that greps the
  whole `dist/assets/` directory (the only way to check "not in the shipped output" — there
  is no manifest of what a browser actually fetches to check against instead) still fails
  on that orphan chunk.
- **Fix:** Two layers, both required.
  1. `domainDefaults()` (`frontend/src/lib/store/store.ts`) now returns live defaults
     (`[]`/`0`/`null`) unconditionally; mock seeding moved to a new `seedMockFixtures()`,
     called once from `routes/__root.tsx` behind `dataMode === "mock"`.
  2. A build-time-foldable flag, `__HACHI_USE_MOCKS__`, injected via `vite.config.ts`'s
     `define` (computed once, in Node, at config-load time — sidestepping the
     `String.prototype` folding problem entirely) and declared in `vite-env.d.ts`.
     `seedMockFixtures()` checks it *first*, as an unconditional early return, so when
     mocks are off Rollup can prove everything after it (including the dynamic import)
     unreachable and drops `fixtures.ts` from the build **entirely** — no orphan chunk.
     The runtime `USE_MOCKS` check stays as a second, defensive guard. Verified
     empirically: building with `VITE_HACHI_USE_MOCKS` unset produces zero
     `fixtures-*.js` chunk and zero fixture-only marker hits anywhere under
     `dist/assets/`; building with it set to `true` reproduces the exact same chunk
     content, proving the seed path still works.
  Added `scripts/check_bundle.py` as a **blocking** preflight step
  (`"frontend bundle purity (no demo fixtures)"`, registered immediately after
  `"frontend build"`) that greps `frontend/dist/assets/*.js` for a small, explicit marker
  list and fails the gate on any hit; raises `FileNotFoundError` (never a silent pass) if
  `dist/assets` is missing or has no `.js` files.
- **Avoid:** (1) A `flag ? demoData : realData` ternary keeps `demoData` alive in the
  bundle unless `flag` folds to a build-time literal — assert on the *built artifact*,
  never trust the bundler. (2) A dynamic `import()` alone does not guarantee absence from
  `dist/` — it only guarantees the module becomes its own chunk; the chunk is still
  *emitted* unless the bundler can prove the import call itself unreachable, which needs a
  genuinely foldable condition (a `vite.config.ts` `define` literal, not a runtime string
  comparison). (3) When picking literal markers for a bundle-purity guard, verify each one
  is not *also* hardcoded somewhere the fix is deliberately not touching (here: 13
  `dataMode === "live"` mock forks in `store.ts` still hardcode
  `priya@studio.co`/`"Sending email to Priya"`/`"reader-1"`/`gmail.send(to=` inline — out
  of scope per T-22 §9, Wave-2 CF-16 work) — a marker sourced from untouched code makes a
  blocking guard permanently red.
- **Locked by:** `scripts/check_bundle.py` (preflight step
  `"frontend bundle purity (no demo fixtures)"`, `scripts/preflight.py`), pinned by
  `tests/test_bundle_guard.py` (`test_checker_flags_a_marker`,
  `test_checker_refuses_to_pass_on_a_missing_dist`,
  `test_checker_refuses_to_pass_on_an_empty_dist`, +3 more); and
  `frontend/src/lib/store/store.test.ts` —
  `seedMockFixtures — no-op guard when mocks are disabled`.

### BB-124 — Any settings write baked transient env overrides (incl. HACHI_PERMISSION_TIER) permanently into settings.toml

- **Symptom:** a permission tier (or model, or token cap) set via an environment variable
  or a project `.env` for one run became permanent — it survived unsetting the variable —
  after any unrelated settings change anywhere in the CLI, gateway, or even an LLM
  `set_agent_config` tool call. `settings.toml` also grew a wall of default sections
  (`[meta]`, `[memory]`, `[events]`, `[jobs]`, `[retention]`, …) the user never wrote.
  Reproduced directly: seed a file with only `[agent] name = "Hachi"`, set
  `HACHI_PERMISSION_TIER=unrestricted`, call `provider_admin.add_or_update_provider()` (an
  unrelated write) — `settings.toml` gained `[permissions]\ntier = "unrestricted"` plus
  every other section's defaults.
- **Root cause:** all 15 writers called `load_effective_config()` (defaults + file + env +
  CLI, `core/config_loader.py:267-312`) and handed the whole merged `ResolvedConfig` to
  `write_global_config()`, which `model_dump(exclude_none=True)`s it and replaces every
  top-level section wholesale with `doc[key] = value`. Nothing on that path tracked which
  layer a value came from. `HACHI_PERMISSION_TIER` enters via `_env_overrides`
  (`:248-249`), and `env_get` also reads a project `.env`, so no shell export was even
  required.
- **Fix:** writers now read the FILE layer only via a new public
  `read_global_config_file()` (a thin wrapper over the pre-existing `_read_toml`), mutate
  that plain dict (or, where a sub-value needs schema validation/defaulting — e.g.
  `ProviderSettings`/`RoutingSettings` construction in `provider_admin.py`, or per-field
  type coercion in `tools/config_tools.py`'s `_apply_config_patch` — a freshly-constructed
  model built from the caller's own input, never a re-validation of the merged/file dict),
  and write the dict back via a new `write_global_config_dict()`. The dict is never
  re-validated through `ResolvedConfig.model_validate()` for the WRITE payload — doing so
  re-inflates every default field and reproduces the bug. 12 of the 15 sites were
  converted: `channels/telegram_setup.py` (`save_allowlist`), `cli/main.py` (6 sites:
  `update_provider`, `update_permissions`, `update_memory`, `set_capability_enabled`,
  `update_limits`, `config-set`), `gateway/provider_admin.py` (all 3:
  `add_or_update_provider`, `remove_provider`, `set_routing`), `gateway/voice_endpoints.py`
  (`set_voice_config`), and `tools/config_tools.py`'s **injected** writer
  (`_make_set_agent_config`, mounted by `register_config_tools` — the LLM-facing
  `set_agent_config` tool). Two sites deliberately keep merged semantics — they are
  first-run bootstraps that *create* the file from defaults + setup answers, not unrelated
  writes: `gateway/workspace.py:142` (`ensure_workspace`, fires only when
  `settings.toml` doesn't exist yet) and `core/onboarding.py:533`
  (`OnboardingService.apply_setup`). Each carries a comment explaining why. `_CONFIG_LOCK`
  moved from `gateway/provider_admin.py` into `core/config_loader.py` and is now taken
  **inside** `write_global_config()`/`write_global_config_dict()`; `provider_admin.py`'s
  own three `with _CONFIG_LOCK:` blocks were removed (a plain `threading.Lock` is not
  reentrant — leaving both would self-deadlock the first provider write).
  **One site was a known, tracked gap, not an oversight — closed by the T-15 follow-up
  below:** `gateway/fastapi_app.py`'s Tavily-connector-configured-via-UI persist of
  `[search] provider = "tavily"` (inside `post_credentials`) was still on the old merged
  `write_global_config()` path — out of scope for this pass (file owned by a concurrent
  agent in the same wave).
- **Avoid:** never persist the output of a layered/merged config resolver. Read the layer
  you own, mutate it, write it back. A writer that reads `load_effective_config()` is a
  bug — including one wired into an agent-facing tool, which is easy to miss because it
  doesn't look like a "settings page."
- **Locked by:** `tests/test_config_write_file_layer.py` —
  `test_env_override_is_not_persisted_by_unrelated_write` (drives
  `provider_admin.add_or_update_provider`),
  `test_env_override_is_not_persisted_by_set_agent_config_tool` (drives the LLM-facing
  `set_agent_config` tool via `register_config_tools` + a real `ToolRegistry`), and
  `test_tavily_credentials_persist_does_not_leak_env_override` (T-15 follow-up, below —
  drives the real `POST /integrations/tavily/credentials` route). All three set
  `HACHI_PERMISSION_TIER` and assert on the bytes of `settings.toml` on disk, not on a
  returned object.
- **Follow-up fix (closed, T-15):** converted the Tavily persist inside
  `post_credentials`'s `_store_all` closure (`gateway/fastapi_app.py`) from
  `load_effective_config()` / `write_global_config()` to
  `read_global_config_file()` / `write_global_config_dict()`, mutating a plain
  `dict(doc.get("search") or {})` (so any other `[search]` keys already on disk — e.g.
  `max_calls_per_turn` — survive) and writing the dict back, same pattern as the other 12
  writers. Reproduced first, not assumed: with the old code, seeding `settings.toml` with
  only `[agent] name = "Hachi"`, setting `HACHI_PERMISSION_TIER=unrestricted`, then POSTing
  a Tavily API key through the real route baked the *entire* merged config — every
  section's defaults, including `tier = "unrestricted"` — into the file, the same shape as
  the original bug. Confirms the tracked gap was live, not theoretical.

### BB-125 — `ProcessSharedServices.session_store` was read in three places and declared in none (BB-42 recurrence)

- **Symptom:** every "prefer the process-shared SessionStore" branch was dead. The
  gateway's durable ingress-idempotency path (`POST /sessions/{id}/messages`, i.e.
  the hot path — once per message) opened a brand-new `SessionStore()` — full
  schema init/migrations — and never shut it down, holding a `sessions.db-wal`/
  `-shm` handle open for the life of the process. Windows gateways accumulated
  handles until restart. Two other readers (`cleanup_empty_sessions` and
  `core/cost.py`'s daily-spend ledger) had the identical dead branch.
- **Root cause:** `ProcessSharedServices` had no `session_store` field and nothing
  ever assigned one (`grep -rn "\.session_store\s*=" src/` → no hits before this
  fix), yet three sites read it: `gateway/fastapi_app.py` `_resolve_session_store`
  via `getattr(shared, "session_store", None)`, `gateway/fastapi_app.py`
  `cleanup_empty_sessions` via a `hasattr(_shared, "session_store")` guard, and
  `core/cost.py` `_get_shared_session_store` via another defaulted `getattr`. All
  three therefore always took the fallback. `boot_agent` already created the store
  and passed it to `BootDeps`, `register_session_tools`, `set_session_store`, and
  the idle sweep — but never attached it to `_shared`.
- **This is a BB-42 recurrence, one level down.** BB-42 was "imported a
  `_get_session_store` symbol that has never existed"; its own fix reintroduced
  the identical failure as "getattr an attribute that has never existed" — and the
  docstring describing BB-42 sat four lines above the new instance in
  `core/cost.py`, describing the bug class while the code below it committed it
  again.
- **A second, more severe failure class was found (not in the original CF-1
  finding) while implementing the literal "attach it and read it directly" fix:**
  naively assigning `_shared.session_store = session_store` at boot with no other
  change reintroduces, for the session store, exactly what BB-45 already fixed for
  the `Scheduler` and what the existing `vault.is_shared` flag already guards
  against for `MemoryVault` — `AgentSession.shutdown()` unconditionally called
  `self._session_store.shutdown()`. `cli/main.py`'s `_InProcessGatewayRuntime`
  keeps up to `HACHI_GATEWAY_MAX_SESSIONS` (100) concurrent `AgentSession` objects
  alive in one process and calls `.shutdown()` on perfectly ordinary events —
  closing a browser tab (`close_connection`), replacing a session on reboot
  (`_register_agent`), a provider-config-driven agent rebuild (`_reload_agents`) —
  not just full process exit. Once the store is genuinely shared, an unguarded
  close in any ONE of those silently poisons the sqlite3/WAL connection every
  sibling session, the ingress-idempotency hot path, and the daily-spend ledger
  depend on, for the rest of the process lifetime (all three readers degrade to
  "no shared store" rather than raising — so nothing would have surfaced this in
  production until spend/idempotency data quietly went missing).
- **Fix:** declared `session_store: Any = field(default=None)` on the dataclass;
  attached the store in `boot_agent` right after it is constructed; converted all
  three reads to direct attribute access (a future typo is now an
  `AttributeError`, not a silent `None`); removed `_resolve_session_store`'s
  fallback entirely (its three callers already treat `None` as "durable
  idempotency/interactions unavailable for this request" — confirmed by reading
  every call site before choosing this shape) and turned its blanket
  `except Exception: return None` into a `logger.warning`; kept
  `cleanup_empty_sessions`'s fallback (a maintenance route, not the hot path) but
  now close it in a `finally`, mirroring the shape `core/cost.py` already had
  right; and added an is-still-shared identity guard in `AgentSession.shutdown()`
  — mirroring the `vault.is_shared` guard a few lines above it in the same
  method — so a session only closes its own store once a newer session has
  already taken over the shared slot.
- **Avoid:** `getattr(x, "field", None)` / `hasattr(x, "field")` against one of OUR
  OWN types is not defensive — it converts "this attribute was never implemented"
  into a silently-taken fallback. Read declared attributes directly and let
  `AttributeError` be loud. When you fix a bug of this class, grep for the
  *shape* (getattr-default, hasattr-guard, wrong-symbol-import), not just the
  symbol — BB-42 came back through different syntax with the same effect. AND:
  before publishing a per-instance-constructed resource onto a process-wide
  singleton, audit every place that resource's *owning* object already closes it
  on its own teardown — an unguarded close on a newly-shared resource is a worse
  bug than the leak you just fixed (BB-45 hit this for the `Scheduler`; `vault`
  carries a dedicated `is_shared` flag for exactly this reason).
- **A note on regression-test design for this bug shape:** `ProcessSharedServices`
  is a plain (non-frozen, non-slotted) dataclass, so `shared.session_store = fake`
  succeeds as an ordinary instance attribute *whether or not the field is
  declared* — a test shaped "manually attach a fake store, assert the reader
  returns it" cannot fail against the pre-fix code, because the readers'
  `getattr(..., None)` already finds a manually-set attribute today. The
  properties that actually discriminate fixed from unfixed code are (1) class-level
  `__dataclass_fields__` membership, and (2) counting real construction/shutdown
  calls when nothing has been attached (the realistic default-boot state).
- **Locked by:** `tests/test_shared_session_store.py` —
  `test_shared_services_declares_session_store`,
  `test_resolve_session_store_does_not_leak_a_new_store_per_call`,
  `test_cleanup_empty_sessions_closes_its_fallback_store`,
  `test_agent_session_shutdown_does_not_close_the_still_shared_store` (regression
  proofs — verified failing before the fix); plus 3 correctness-lock tests in the
  same file for the "prefer an attached store" contract.

### BB-126 — MemoryGardener shipped with a writer, a reader, and no connection between them

- **Symptom:** M3 memory gardening (dedupe / supersede / expire over `memory/*.og`)
  never ran. `pending_diffs.json` was never written, and the "TUI Memory-Diff
  screen" named in the module docstring did not exist in either UI.
- **Root cause:** three independent dead layers. (1) `MemoryGardener.run()` — the
  only writer — had zero production callers; every construction site was in
  tests. (2) `AgentSession.snapshot_gardener_diffs` — the only reader — also had
  zero callers: no gateway route, no TUI screen, no CLI command. (3) Even when
  called, that reader did `getattr(self.memory, "memory_dir", None)` against a
  `MemoryManager`, which has no `memory_dir` attribute — so it returned `[]`
  before touching the file. The correct path is `get_hachi_paths().memory_dir`.
  **A fourth, previously-unreachable defect surfaced only once (1)–(3) were
  fixed:** the reader's dead code also referenced
  `GardeningResult._PENDING_DIFFS_FILE` — that constant is declared on
  `MemoryGardener`, not `GardeningResult` — so fixing the `getattr` alone would
  have traded a silent `[]` for an `AttributeError`. Nobody had ever gotten far
  enough to hit it before this task, and the audit's own §3.3 source read did not
  catch it either — proof that the getattr-masking bug wasn't just returning the
  wrong answer, it was hiding a second broken line behind it.
- **Fix:** Option A — wired up end-to-end (recorded in `docs/DECISIONS.md`):
  - **A1 (schedule):** `gateway/orchestrator.py`'s `_start_memory_gardener`
    mirrors `_start_eval_report_scheduler` exactly (daemon thread, sleep loop,
    `min(interval, 3600)` wake cap, try/except that logs, started from
    `boot_agent` behind a new opt-in `[memory_gardener]` section). The section's
    `MemoryGardenerSettings` model is registered on `ResolvedConfig` in the same
    commit (see BB-101 — the reflective
    `test_all_settings_models_are_registered_on_resolved_config` guard would
    otherwise fail by name).
  - **A2 (route):** `GET /memory/diffs` + a `GatewayControlPlane` handler + an
    `_InProcessGatewayRuntime.get_memory_gardener_diffs()` method make
    `snapshot_gardener_diffs` reachable for the first time; its `getattr` bug (and
    the `GardeningResult._PENDING_DIFFS_FILE` bug above) are both fixed in the
    same change. OpenAPI + frontend types regenerated (BB-4).
  - **A3 (surface + write-back):** the approve/reject path that did not exist at
    all before this task. `GardeningProposal` gained a stable, deterministic `id`
    (hash of its identifying fields) so a client can round-trip
    `GET /memory/diffs` → `POST /memory/diffs/{id}` without depending on list
    order. `MemoryGardener.decide()` applies an approval through the same
    append-only `::changelog` mechanism the auto-apply dedup path already used —
    never overwriting node content (the module's own governance rule) — or drops
    a rejection without touching any `.og` file; either way it re-persists
    `pending_diffs.json` so the decision survives the next `GET`. The web
    frontend's Memory screen gained a "Gardener" tab (list + Approve/Reject)
    wired through a new `memoryGardener.ts` domain module using the same
    non-optimistic POST-then-mutate pattern BB-106 established for approvals.
  - `GardeningResult.load`'s swallowed exception was promoted from
    `logger.debug` to `logger.warning(..., exc_info=True)` now that a corrupt
    `pending_diffs.json` silently degrades a live route, not just an unused one.
- **Avoid:** a `getattr(self.x, "field", None)` against one of our OWN types is
  not defensive coding — it converts "this attribute does not exist" into a
  silent empty result. Before shipping a read-model method, grep for its caller;
  a snapshot method with no caller is a dead feature, not future-proofing. AND:
  fixing a getattr-masking bug can uncover a SECOND bug that was hiding behind
  it (dead code is never type-checked by a passing test suite) — re-run the
  method's own tests immediately after unmasking it, don't assume the exposed
  code path was already correct just because it existed.
- **Locked by:** `tests/test_memory_gardener_wiring.py` (17 tests: config
  registration, A1 scheduler-on-interval + 3600s cap + boot_agent AST check, A2
  reader-fix + route end-to-end, A3 `MemoryGardener.decide()` + route
  approve/reject/404 + the debug→warning logging fix) and
  `frontend/src/lib/data/domains/memoryGardener.test.ts` (6 tests: adapter shape
  + the BB-106 non-optimistic decision pattern).

### BB-127 — ~4,100 green tests could not detect nine completely dead features

- **Symptom:** nine shipped features were inert in production while the full
  suite passed: the kennel installed nothing (BB-98), routing cost warnings
  were always empty (BB-93), three `[memory]` settings were ignored (BB-99),
  `[eval_reports]` was stripped from config and its own key reported back to
  the user as unknown (BB-101), MemoryGardener never ran (BB-126),
  `file.extracted` reached nobody (BB-96), the shared `session_store` was
  never assigned (BB-125), auto-memory was a no-op for non-env-var users
  (BB-100), and two implemented provider kinds were rejected by the schema
  (BB-121).
- **Root cause:** the suite verified **units**, never **wiring**. Every one of
  the nine had a green unit test — some had two (the kennel's
  `tests/test_p2_12_agent_packs.py`, which tested the pack module directly
  that no production code imported, and `tests/test_g1_wave1.py`, which
  stubbed the gateway handler). No test in the repo called the real
  `boot_agent`; the two that mentioned it (`tests/test_cold_start_resume.py`,
  `tests/test_gateway_runtime_validation.py`) both substituted a fake.
  Combined with the `except Exception: pass` / `getattr(x, "field", None)`
  idiom at feature boundaries, there was no mechanism — test or runtime — by
  which a dead feature could announce itself.
- **Fix:** added `tests/test_feature_liveness.py` — nine tests, one per
  formerly-dead feature, asserting a live object, a real file, or a real bus
  delivery (never a mock's recorded call) — and wired it into
  `scripts/preflight.py` as a **blocking** step ("feature liveness (boot
  smoke)"), placed right after "eval regression". Four tests (T-06/T-08/T-09/
  T-10) share ONE real `boot_agent()` boot into an isolated `tmp_path`
  profile (module-scoped fixture `live_session`); five (T-04/T-05/T-07/T-11/
  T-12) construct their own tiny isolated environment or are pure functions,
  because forcing all nine through one boot would not have made the suite
  more correct, only slower. Measured wall time ~8-13s — kept `in_quick=True`
  so the fast inner loop still catches the next dead feature. Every assertion
  was verified failing against its pre-fix shape: five (T-04, T-05, T-07,
  T-11, T-12) by an actual temporary revert-and-run (Edit the file back to
  the exact code quoted in the T-0x task spec's "Verified current state",
  run only that one test, observe the original error, restore, confirm
  `git diff` is empty); four (T-06, T-08, T-09, T-10) by a grounded argument
  citing the exact before/after code, because reverting them would have
  meant editing multiple files (including one this task was explicitly told
  not to touch) and re-paying the ~9s boot cost per attempt for no added
  certainty the argument does not already provide.
- **Avoid:** a unit test that constructs the subject itself, or stubs the
  handler under test, proves nothing about whether production reaches it.
  When you ship a feature, add one assertion that the COMPOSITION ROOT wires
  it — and prove the assertion fails without the wiring. "Tests pass" is not
  "the feature works". Feature-liveness is boot wiring, not loop mechanics:
  it belongs in a dedicated suite, NOT in the `hachi eval run` corpus, which
  scripts the LLM transport (`ScriptedLLMClient.generate` discards
  `**kwargs`) and asserts on turn trajectories — it structurally cannot see a
  prompt/feature regression.
  Two traps this suite's own construction hit, worth naming because the next
  boot-time test will hit them too: (1) **path isolation is not one
  `get_hachi_paths` patch.** `boot_agent()`'s call graph resolves paths
  through *at least six* independent module-level bindings
  (`hachi.gateway.orchestrator`, `hachi.gateway.workspace`,
  `hachi.core.session_store`, `hachi.core.secret_store`,
  `hachi.core.config_loader`, `hachi.og_engine.engine` — each does its own
  `from hachi.core.paths import get_hachi_paths` at module scope, freezing
  an independent binding at first import), plus a completely unrelated
  `PlatformDirs("hachi", "opengigantic")` call inside
  `hachi.jobs.queue._default_db_path()` that `get_hachi_paths()` has no say
  over at all. Patching only the one target a spec names and trusting that
  is sufficient is how a "hermetic" test writes into the developer's real
  `%LOCALAPPDATA%\hachi` — verify by scanning `sys.modules` for every
  live binding, not by hand-listing one. (2) **a module-scoped boot fixture
  and a function-scoped autouse singleton-clearer (`tests/conftest.py`'s
  `_reset_process_shared_services`) fight, and the autouse fixture wins even
  on the FIRST test** — pytest sets up the broader-scoped fixture first, so
  the clear lands after the boot but before every test body, including the
  one that triggered the boot. The booted objects stay alive (the fixture
  holds a live reference), but `ProcessSharedServices.instance()` returns
  `None` inside the test unless something re-registers the singleton under
  its fingerprint before reading it — do that explicitly per test that needs
  it; do not fight conftest by trying to make the fixture avoid the clear.
- **Locked by:** `tests/test_feature_liveness.py` — the whole file (nine
  tests: `test_liveness_t04_kennel_lists_and_installs_bundled_packs` …
  `test_liveness_t12_provider_kinds_schema_accepts_gemini_and_chatgpt_web`);
  `scripts/preflight.py` step "feature liveness (boot smoke)".

### BB-128 — `GET /approvals` bypassed the control plane and fabricated an `expiresAt` deadline the registry never recorded

- **Symptom:** Every pending approval came back with a camelCase `expiresAt = <fetch
  time> + 60s` — identical across all entries in a response, unrelated to when the
  request was actually created, and a window that also understated the real
  `RegistryApprover` timeout (180 s) by 3x. Cosmetic only: the frontend discarded the
  field (`approvals.ts` hardcodes `expiresAt: 0`), so nothing rendered from it, and it
  was **not** the cause of the BB-77 auto-decline (CORRECTIONS.md R1 refutes that link
  on two independent grounds — the frontend never reads the field, and BB-77's real
  cause, `0 <= Date.now()` always being true, was already fixed). Structurally,
  `GET /approvals` and `POST /approvals/{id}` were also the **only** routes in
  `fastapi_app.py` reaching past `GatewayControlPlane` straight into
  `core.approvals.get_approval_registry()` — every other route (100+ call sites) goes
  through `control_plane.*`.
- **Root cause:** `get_approvals()` injected a hardcoded 60 s TTL into any entry missing
  an `expiresAt` key, to satisfy a countdown the UI never consumed — computed **once per
  request** (`now + 60s`), not per pending entry, so it was a clock reading, not a
  deadline. Both approvals routes imported and called `get_approval_registry()` directly
  instead of going through the control-plane seam like everything else in the file.
- **Fix:** Added `pending_approvals()` / `resolve_approval(request_id, action,
  rationale="")` to `GatewayControlPlane` (`gateway/control_plane.py`), backed by two new
  `_InProcessGatewayRuntime` methods of the same names (`cli/main.py`) — the only places
  `get_approval_registry()` is now touched from the gateway edge. Wired unconditionally
  in `_build_gateway_control_plane` via `support.*` (the registry is a process singleton,
  not gated on a live `AgentSession` — mirrors `get_capabilities=support.capabilities`).
  `get_approvals()` now returns `{"pending": control_plane.pending_approvals()}`
  verbatim — no injection loop, no local `import datetime`. `post_approval_decision()`
  calls `control_plane.resolve_approval(request_id, payload.action, payload.rationale)`;
  `PermissionDecision` construction moved into `_InProcessGatewayRuntime.resolve_approval`,
  so `fastapi_app.py` no longer imports `get_approval_registry` or `PermissionDecision` at
  all. **Reconciled with T-16** (landed first): `PendingApprovalRegistry.list_pending()`
  already carries real `created_at` / `expires_at` (epoch ms, snake_case, `expires_at is
  None` when parked without a timeout) — the route now passes these through unchanged
  instead of colliding with them under a different (camelCase, fabricated) key. Per
  CORRECTIONS.md §2/R1, this is an honesty/layering cleanup, not a user-facing bug fix —
  the frontend behaviour is unchanged.
- **Avoid:** never synthesise a field the backing store does not hold — a fabricated
  timestamp reads as more authoritative than an honestly absent one. If a client needs a
  value the store doesn't track, add it to the store first (T-16), then route the read
  through the seam every other endpoint uses — don't let one router grow a private
  exception to "everything goes through `GatewayControlPlane`."
- **Test correction, not a weakening (CORRECTIONS.md "Four tests will legitimately fail",
  item 4):** `test_get_approvals_returns_pending_list_with_token` previously asserted
  `body["pending"] == pending`, where `pending` was the *same list object* the old route
  mutated in place to inject `expiresAt` — so the assertion passed *because* of the
  fabrication, not despite it (an aliasing trap: mutate-then-compare-to-the-same-object
  cannot detect the mutation). Routing through the control plane also moved the test's
  patch target — `hachi.gateway.fastapi_app.get_approval_registry` no longer exists, by
  design. Rewritten to inject via `GatewayControlPlane(list_pending_approvals=...)` and
  assert against a freshly-built literal, which would catch either an added or a dropped
  field. The five `POST /approvals/{id}` tests and the (previously unused and, on
  inspection, already-broken — its own `with patch(...):` block closed before the
  returned client was ever used) `_make_app_with_fake_registry` helper in the same class
  needed the identical patch-target migration; rewritten to inject `resolve_approval=`
  and assert on the `(request_id, action, rationale)` tuple the route forwards — a
  strictly more precise check of the route's actual contract with the control plane than
  the old test's dig into a `PermissionDecision` stand-in's attributes.
- **Locked by:** `tests/test_gateway_auth_default.py` —
  `test_get_approvals_does_not_fabricate_expires_at` (new; injects a stub via
  `GatewayControlPlane(list_pending_approvals=...)` shaped like T-16's real
  `list_pending()` output — 8 keys including `created_at`/`expires_at` — and asserts no
  `expiresAt` key is added and `created_at`/`expires_at` pass through unchanged) plus the
  corrected `test_get_approvals_returns_pending_list_with_token` and the five
  `test_post_approval_*` tests described above.

### BB-129 — The SSE/events read model silently clamped a stale cursor instead of signalling the gap

- **Symptom:** a client that reconnected with a cursor older than the 2,000-entry ring-buffer
  trim received a continuous-looking event stream that silently started from the oldest
  surviving event — everything between its cursor and that survivor (tool calls, approvals,
  assistant text) was simply missing, with no error, no warning, and nothing in the response
  that let the client tell "sequence 6..100 never existed" apart from "sequence 6..100 was
  dropped." A repo-wide grep for `gap|truncat|dropped|missed` found no gap-signalling mechanism
  anywhere in the codebase before this fix.
- **Root cause:** `RuntimeReadModelStore.read_events_since` (`core/runtime_read_models.py`)
  computed `base = self._events[0].sequence`, clamped `start = max(0, after - base + 1)` to
  avoid a wrong tail-slice wrap, then discarded `base`. Every hop after it —
  `AgentSession.snapshot_events_since` (`gateway/orchestrator.py`) →
  `_InProcessGatewayRuntime.read_events` (`cli/main.py`) →
  `GatewayControlPlane.read_session_events` (`gateway/control_plane.py`) →
  `SessionEventsResponse` (`gateway/fastapi_app.py`) — carried only the events, never the base.
  The clamp itself was correct (and stays); the silence around it was the bug.
- **Fix:** added `RuntimeReadModelStore.oldest_sequence()` (returns the oldest retained
  sequence, or 0 when empty) and threaded it through all five hops as a **separate scalar** —
  `AgentSession.snapshot_events_base()` → `_InProcessGatewayRuntime.read_events_base()` → a new
  `GatewayControlPlane(read_events_base=...)` constructor kwarg (defaults to `None`, emits
  `base_sequence: 0` when unwired so the many hand-built `GatewayControlPlane` tests keep
  working) → a new `base_sequence: int` field on `SessionEventsResponse`. The history-replay
  branch of `read_session_events` also emits `base_sequence: 0` explicitly — it has its own
  sequence space and no ring, so there is nothing meaningful to report, but the field must be
  present so the response shape is uniform. Client-side rule: a gap occurred iff
  `base_sequence > after + 1`. Also corrected the `stream_session_events` route docstring,
  which claimed the frontend uses `EventSource` (auto-reconnecting) — it actually uses
  `fetch` + `ReadableStream` (`frontend/src/lib/data/sse.ts`) and does **not** auto-reconnect;
  callers own retry/backoff.
- **Design note:** `read_events_since`'s return *type* was deliberately left untouched (still a
  bare `tuple[RuntimeEventSnapshot, ...]`) rather than turned into a `(base, events)` pair,
  because several call sites already index/compare it directly — a type change would have
  rippled through those call sites and broken the existing B4 tests structurally instead of
  just extending them. `oldest_sequence()` takes the store's lock separately from
  `read_events_since()`, so a trim can land between the two calls; the only consequence is a
  harmless false-positive gap (a spurious client refetch), never a false negative, so no
  cross-call atomicity was added.
- **Avoid:** when a read path silently repairs a caller's bad/stale input (a clamp, a
  default-fill, a truncation), the repair itself is fine but it MUST be reported back to the
  caller. Clamping in silence is a data-integrity bug, not a robustness feature.
- **Also noted, NOT fixed (out of scope — CORRECTIONS.md §3.7/§9):**
  `GatewayControlPlane._synthesize_history_events` replays stored transcripts in a *separate*
  sequence space starting at `seq = 0`, so a client that streams live, disconnects, then
  refetches once the session is no longer live gets colliding `id:` cursors between the live
  ring and history replay. Left alone per the task spec — a durable unified sequence space is
  a bigger design question than this fix.
- **Test correction, not a weakening (CORRECTIONS.md "Four tests will legitimately fail",
  item 2):** `tests/test_runtime_read_models.py`'s B4 trim-underflow contract
  (`test_read_events_since_stale_after_clamps_to_oldest_surviving` and
  `test_read_events_since_parity_with_scan_after_trim`) previously asserted only that the
  clamp happened. Every existing assertion in both was kept unchanged (the clamp is still
  correct); each gained one new assertion pinning `oldest_sequence()` against the same trimmed
  buffer, so the tests now also cover that the skip is *detectable*, not just survived.
  `tests/test_gateway_runtime_validation.py::test_runtime_event_buffer_caps_at_2000_and_handles_sparse_cursors`
  was amended the same way: its existing end-to-end assertions (`len == 2000`,
  `a_events[0].sequence == 106`) are untouched; it gained an assertion that
  `runtime.read_events_base(...)` reports the same `106`, proving the value survives the hop
  from store to runtime. That test's local `_StoreBackedAgent` fake (standing in for
  `AgentSession`) needed a `snapshot_events_base()` method added to match the widened real
  interface — a test-double update, not a contract change.
- **Locked by:** `tests/test_runtime_read_models.py` —
  `test_read_events_since_exposes_gap_after_trim` (new primary lock) and
  `test_oldest_sequence_reports_no_gap_for_cursor_within_bounds` (new; pins that an in-bounds
  cursor gets no false gap), plus the amended
  `test_read_events_since_stale_after_clamps_to_oldest_surviving` and
  `test_read_events_since_parity_with_scan_after_trim`;
  `tests/test_gateway_runtime_validation.py::test_runtime_event_buffer_caps_at_2000_and_handles_sparse_cursors`
  (amended); `tests/test_gateway_sse.py::test_session_events_response_carries_base_sequence`
  (new — proves the value reaches the actual JSON response body through the real ASGI app, for
  both a stale cursor (gap signalled) and an in-bounds cursor (no false gap)).

### BB-130 — Four contract types defined twice; health objects crossed the seam by structural luck

- **Symptom:** No visible failure — which is the point. `SearchResult`, `ExtractResult`, `SearchProvider` and `ProviderHealth` each existed as two unrelated classes (`core/providers/base.py` dataclasses vs `core/providers/search.py` pydantic models for the search three; `core/providers/base.py` vs `core/resilience.py` for `ProviderHealth`), and a `resilience.ProviderHealth` instance was already flowing through four slots annotated `providers.base.ProviderHealth` (`HealthRecorder.snapshot()` → `Provider.update_health` → `Provider.health` → `ProviderRegistry.health()` → `ModelRouter`, wired by `ModelRouter._get_recorder`'s `on_update` sink). It worked only because the field names matched.
- **Root cause:** `core/providers/base.py` (dataclasses) and `core/providers/search.py` (pydantic) were written for the same three search contracts in different initiatives; `core/resilience.py` grew its own `ProviderHealth` beside the one in `base.py` whose docstring already claimed the recorder produced it. The twins had drifted (`SearchResult.score: float = 0.0` vs `float | None = None`, `extra` present on one and not the other; `ProviderHealth.breaker: str` vs `Literal["closed", "open", "half_open"]`). None of the five files involved is in the blocking mypy gate (`mypy-strict-gate.ini`), so the mismatch was invisible to the only type-aware check that runs on every change.
- **Fix:** `search.py` made canonical for the three search contracts (`SearchResult`, `ExtractResult`, `SearchProvider`) — it is what the only concrete adapter (`tavily_provider.py`) and the search test suite already used. Deleted the twins from `base.py`; `providers/__init__.py` and `providers/registry.py:get_search` re-point at `search.py`; public `__all__` unchanged. For `ProviderHealth`, the spec's first-choice canonical location (`providers/base.py`) turned out to have a **real** circular import, not a hypothetical one: `providers/base.py` (and `providers/registry.py`, reached via the `providers` package `__init__`) import `hachi.core.routing.capability`, and importing *any* name from the `hachi.core.routing` package eagerly runs `routing/__init__.py` → `routing/router.py` → `core/resilience.py` — confirmed by `uv run pytest` failing collection with `ImportError: cannot import name 'HealthRecorder' from partially initialized module 'hachi.core.resilience'` the moment `resilience.py` imported `ProviderHealth` from `providers.base`. Per the task spec's own documented fallback, `ProviderHealth` now lives in a new dependency-free module, `core/provider_health.py` (stdlib-only: `dataclasses` + `typing`, adopting the `Literal[...]` breaker annotation), which both `resilience.py` and `providers/base.py` import directly; `base.py` re-exports it so `from hachi.core.providers.base import ProviderHealth` is unchanged for every existing consumer (`anthropic_provider.py`, `openai_compat_provider.py`, `registry.py`, three test files). `resilience.py` imports the new module directly rather than through `providers.base`, because merely touching the `providers` package (even just to re-export) re-triggers the same `__init__` cascade and recreates the cycle.
- **Avoid:** two classes with the same name and the same fields are not "compatible" — they are a fix that will land on one of them. When a docstring says "produced by X and consumed by Y", check that X and Y are talking about the same class. Separately: before making module A import module B to deduplicate a type, verify the reverse edge doesn't already exist *transitively through a package `__init__.py`* — `import X.Y.z` always runs `X/Y/__init__.py` first, so a dependency-free-looking leaf submodule can still import a whole package's eager side effects. A hypothetical cycle check is not a substitute for actually running the import.
- **Locked by:** `tests/test_twin_contract_types.py` — `test_health_recorder_produces_the_canonical_provider_health` (asserts `resilience.ProviderHealth is providers.base.ProviderHealth` and that `HealthRecorder.snapshot()` returns that exact class, by `is`, not field equality) and `test_contract_types_have_exactly_one_definition` (asserts `providers.SearchResult`/`ExtractResult`/`SearchProvider` are the `search.py` classes by `is`).

### BB-131 — `jobs.db` lived on a second platformdirs root, invisible to every path-based tool

- **Symptom:** `jobs.db` resolved under `%LOCALAPPDATA%\opengigantic\hachi\` while every
  other Hachi file lived under `%LOCALAPPDATA%\hachi\`. The orphan directory existed on
  every machine that had ever booted a session and contained exactly one file. Nothing
  built on `get_hachi_paths()` — `hachi backup`, `hachi restore`, `hachi doctor` — could
  see the durable job queue, including its dead-letter contents.
- **Root cause:** `jobs/queue.py::_default_db_path()` called
  `PlatformDirs("hachi", "opengigantic")` (and created the directory as a side effect)
  while `core/paths.py::get_hachi_paths()` used
  `PlatformDirs(appname="hachi", appauthor=False)`. `HachiPaths` had no `jobs_db`
  property at all, so the file could not even be named through the canonical API. The
  resolution was **duplicated inline** a third time in
  `cli/commands/jobs.py::_open_queue()` — a partial fix (only one of the two call
  sites updated) would have split the queue in two rather than fixing it.
- **Fix:** Added `HachiPaths.jobs_db` (`state_dir / "jobs.db"`, `core/paths.py`);
  pointed `_default_db_path()` (`jobs/queue.py`) at it via a new
  `_migrate_legacy_jobs_db()` that relocates the legacy DB and its `-wal`/`-shm`
  sidecars once — sidecars moved *before* the main `.db` file so `target.exists()`
  (the interlock a concurrent boot checks) never reports "done" on a partially-moved
  state — never overwrites an existing target, and logs at WARNING (not DEBUG) and
  falls back to a fresh DB at the new path rather than raising if the move fails.
  Deleted the duplicated resolution in `cli/commands/jobs.py::_open_queue()`; it now
  calls a bare `SQLiteJobQueue()` so both entry points share one code path.
- **Avoid:** resolve every user-data path through `get_hachi_paths()` — never
  construct `PlatformDirs` outside `core/paths.py` (or a documented, isolated
  migration helper that names the old location only). And when two entry points
  resolve the same file independently, changing only one of them splits the store:
  fix both in the same commit and pin it with a test that asserts each side
  independently equals the *canonical* path, not merely that the two sides equal
  each other — a mutual-equality check alone can pass "for free" when both sides
  already agree on the same wrong answer.
- **Locked by:** `tests/test_jobs_db_path_migration.py` —
  `test_cli_and_gateway_resolve_the_same_jobs_db`,
  `test_legacy_jobs_db_is_moved_to_the_canonical_path`,
  `test_migration_never_overwrites_an_existing_target`,
  `test_migration_failure_logs_warning_and_does_not_crash_boot`; and
  `tests/test_p1_09_paths.py::test_jobs_db_uses_platformdirs_state_dir`.

### BB-132 — `shutdown()` silently skipped cleanup for three attributes the constructor never set

- **Symptom:** `AgentSession.shutdown()` stopped the schedule runner, closed the sub-agent LLM
  client, and closed the browser controller — but read all three via
  `getattr(self, "_x", None)`. `boot_agent` wrote them onto the finished object instead of
  passing them to the constructor: the sub-agent-client/browser-controller stash sat right
  after `AgentSession(...)`, and the schedule-runner/scheduler stash sat ~190 lines later,
  after `ScheduleRunner.start()`. Any `AgentSession` not built by the real `boot_agent` path —
  every hand-built session in the test tree, and any future caller — skipped all three
  cleanups and reported a clean shutdown regardless.
- **Root cause:** Post-construction attribute stashing, ten writes in total (the source
  audit's own list named only four; the other six included the three that matter for
  cleanup). The lifecycle contract lived in inline comments at the assignment sites,
  thousands of lines from `shutdown()`, and the constructor signature did not express it.
  Three of the ten (`_sub_agent_client`, `_browser_controller`, `_schedule_runner`) were never
  declared in `__init__` at all — every read of them was necessarily defensive.
- **Fix:** Promoted all ten stashes to keyword-only `AgentSession.__init__` parameters, same
  defaults as the class body previously declared (`max_daily_cost_usd` passed as
  `max_daily_cost_usd or None` at the `boot_agent` call site to preserve the old
  `if max_daily_cost_usd:` falsy-collapse). Replaced the four defensive
  `getattr(self, "_x", None)` reads tied to `shutdown()` and title generation with direct
  attribute access. `_schedule_runner` could not simply move into the existing construction
  call: its `fire` callback closes over `session.chat`, and `ScheduleRunner.start()` ran ~190
  lines *after* the old construction site. Moved the `_fire_schedule` closure +
  `ScheduleRunner(...)` + the H3 single-thread-start guard ahead of `AgentSession(...)`
  instead — Python closures are late-binding (the callback resolves `session` when it *runs*,
  not when it is defined), and `ScheduleRunner`'s first tick never fires sooner than
  `tick_interval_seconds` (30s default) after `start()`, long after `session` is assigned a
  few lines later in the same function. `_schedule_runner.start()` still runs before the
  session is constructed, unchanged in relative order.
- **Deliberately left:** a fifth `getattr(self, "_schedule_runner", None)` read, in
  `run_schedule_now()` (pre-existing, added by BB-36), was left untouched.
  `tests/test_schedule_run_now_gateway.py::test_run_schedule_now_no_schedule_runner_returns_false`
  builds its session via `AgentSession.__new__`, specifically to assert this method degrades
  to `False` when `_schedule_runner` was never set at all; converting that read to direct
  attribute access raises `AttributeError` through that fixture, and the file was outside
  this fix's scope. Follow-up: align that fixture with the real constructor (mirroring the
  `test_agent_session_shutdown.py` fix here) and convert the read once it is.
- **Avoid:** if `shutdown()` (or any lifecycle method) depends on an attribute, that attribute
  is a constructor argument. `getattr(self, "_own_field", None)` against your own type is a
  smell — it means construction is incomplete by design. When a background thread's callback
  closes over the object under construction (a `ScheduleRunner`-shaped problem), prefer
  starting the thread before construction and relying on Python's late-binding closures over
  stashing the thread reference on afterwards — but verify the tick/callback interval
  actually gives the assignment time to land before relying on it; don't assume it.
- **Locked by:** `tests/test_agent_session_construction.py` —
  `test_constructor_establishes_every_shutdown_dependency`,
  `test_shutdown_stops_a_constructor_supplied_schedule_runner`,
  `test_daily_cap_promotion_preserves_falsy_collapse`,
  `test_all_ten_promoted_values_reach_their_attributes`,
  `test_constructor_defaults_match_the_pre_promotion_declared_defaults`.
  `tests/test_agent_session_shutdown.py::TestAgentSessionShutdown::test_shutdown_shuts_down_tool_pool`
  was strengthened to build its fixture through the real constructor instead of
  `AgentSession.__new__`, so it can now catch a dropped constructor argument.

### BB-133 — The `.og` spec omitted every executable tag the runtime acts on

- **Symptom:** `docs/reference/objectgraph-spec.md` contained ZERO occurrences of `::api`,
  `::tool`, `::config`, `role=env`, `role=integration` or `role=mcp` — and never documented
  the `role=` node attribute at all — while the parser executed all three block types
  (`og_engine/parser.py`, `block_type == "config"/"tool"/"api"`) and the gateway scanned
  every loaded `.og` for all three roles at session boot
  (`gateway/orchestrator.py`'s three `skill_engine.get_nodes_by_role("mcp"|"env"|"integration")`
  calls). An installed `.og` could ingest credentials, mount an HTTP-calling tool, and start
  an MCP subprocess, from a format whose spec said none of that was possible. `::api` was
  already load-bearing for 10 of the 36 shipped first-party skills, so the spec was not
  merely incomplete — it described a different format from the one that ships.
- **Root cause:** the spec tracks the *knowledge* format; the runtime tags accreted in code
  only, and nothing tied the two together. `scripts/lint_skills.py` (the one blocking-preflight
  guard that touches every shipped `.og` file) had no concept of "reserved" or "first-party"
  either, so a community pack declaring `role=mcp` would have linted clean.
- **Fix:** documented the executable surface in a new `docs/reference/objectgraph-spec.md`
  §4.7 "Runtime & Integration Tags (Internal)" — all six tokens named, each with a worked
  example (the real `currency.og` for `::api`; code-derived illustrative examples for the
  three roles, since none ship), and a security note anchored to the three
  `get_nodes_by_role` boot scans, cross-linked to T-56/T-58. Recorded the decision (pure
  Option B for all six, not the task doc's own "split" recommendation) in
  `docs/DECISIONS.md`. Added a `scripts/lint_skills.py` rule (`is_first_party_skill` +
  `check_reserved_tags`) that rejects `::config`/`::tool`/`::api` and
  `role=env`/`role=integration`/`role=mcp` in any `.og` outside `src/hachi/default_skills/`,
  while allowing them inside it — verified against a real throwaway community-style `.og`
  declaring `role=mcp`, which the CLI now rejects (exit 1) instead of linting clean. Added a
  conformance test that derives the parser's block types from the parser source (not a
  hardcoded copy) and requires each to appear in the spec, so a future
  `block_type == "..."` branch fails the test automatically until documented.
- **Avoid:** when a format is the product, its spec is not documentation — it is the
  contract a third-party reviewer audits against. Any new `block_type ==` branch or
  `get_nodes_by_role(...)` scan must land in the same PR as its spec section (§4.7) and,
  if it is meant to stay first-party-only, the same PR as its `scripts/lint_skills.py`
  reserved-tag entry — the conformance test enforces the spec half; nothing enforces the
  lint half automatically, so a reviewer must check it by hand.
- **Locked by:** `tests/test_p5_01_conformance.py` —
  `test_spec_documents_every_parser_block_type`, `test_spec_documents_every_executable_node_role`;
  `tests/test_lint_skills_reserved_tags.py` (7 tests, incl. the real CLI entry point
  rejecting a throwaway community `.og`).

### BB-134 — "Forget" did not forget: deleted facts resurfaced through .og graph recall

- **Symptom:** After `hachi memory forget <id>` (or the agent's `delete_memory` tool),
  the fact was re-injected into the very next turn's context via the graph-recall
  block. The CLI concealed it: `hachi memory search` builds a bare `MemoryVault()` with
  no graph index attached, so it showed only the Chroma view and reported the fact
  gone. The tool's own docstring and result string told the model the fact had been
  "removed from all backends".
- **Root cause:** Facts dual-write to `.og` files and ChromaDB
  (`core/memory.py::extract_facts`), but `MemoryVault.delete_fact` only removed the
  Chroma row — `core/memory_writer.py` had no delete/remove path of any kind. The
  per-turn `build_graph_recall_section` reads `.og` straight off disk via
  `load_memory_dir`'s `rglob("*.og")`. Two further defects made it worse: (a) the FTS5
  cleanup was a permanent no-op because `_delete_from_fts5` deleted
  `WHERE node_id = <chroma sha256>` while `MemoryIndex` stores
  `"<rel-path>:<node-name>"`, and `refresh()` rebuilds the table from `.og` anyway
  (so even a coincidental id match would not survive the next write); (b) that
  cleanup was skipped entirely when `memory.graph=false`, and
  `delete_user_fact`/`delete_agent_fact` never attempted it at all.
- **Fix:** Added `MemoryWriter.write_tombstone()` (`core/memory_writer.py`), which
  appends a node carrying `->[:supersedes=<name>]` — routed to the SAME `.og` file as
  the node it supersedes, resolved via a new `_find_node_file` helper — plus
  `->[:role=tombstone]` and `->[:reason=...]`; the tombstone references the forgotten
  node **by name only**, never by content, so it cannot itself become a new
  resurrection vector. Made all three vault delete methods
  (`delete_fact`/`delete_user_fact`/`delete_agent_fact`) share one tri-store helper
  (`MemoryVault._tri_store_delete`): capture the Chroma document text for the id
  *before* deleting the row, delete the Chroma row, then resolve every `.og` node
  whose `dense` equals that captured text (content is the only identifier the two
  stores share) and tombstone each — unconditionally, even with no graph index
  attached, so a later `memory.graph=true` flip does not resurrect the fact. Moved
  supersede suppression into `load_memory_dir` (`core/memory_graph.py`) so it is
  global rather than traversal-dependent (a tombstone that nothing seeds still
  suppresses its target); kept `expand_graph`'s own filter as a harmless second
  layer. Taught `MemoryIndex.refresh()` (`core/memory_index.py`) to skip
  supersede-suppressed nodes on every rebuild — detected via the lightweight
  `memory_graph.parse_memory_file` parser, since `og_engine`'s `OGNode` does not
  surface arbitrary `->[:key=value]` edges. Replaced the bypass
  `sqlite3.connect(getattr(self._graph_index, "_db_path", None))` in
  `MemoryVault._delete_from_fts5` with a call to a new public `MemoryIndex.delete_node(node_id)`,
  resolving the real composite id via a new shared `memory_index.compute_node_id()`
  (single source of truth for the id format, used by both `refresh()` and the vault —
  a duplicated copy of that format string is exactly how the original bug happened).
  Added an optional `memory_dir` constructor param + read-only property to
  `MemoryVault` (defaults to `get_hachi_paths().memory_dir`, the same directory
  `MemoryWriter`/`MemoryIndex` already use in production) so the vault can resolve
  its content-addressed `.og` lookups without depending on a graph index being
  attached. Corrected the `delete_memory` tool's docstring/result strings
  (`tools/memory_tools.py`) to describe what now actually happens instead of an
  unqualified "removed from all backends".
- **Avoid:** if a write goes to N stores, the delete must go to N stores — and a
  docstring claiming "all backends" is not evidence that it does. When two stores
  hold the same data under different identifiers, the delete path needs an explicit
  identifier-resolution step (here: content match, since only the content is shared
  verbatim between Chroma and `.og`), and the regression test must read back through
  the *production* read path (`build_graph_recall_section`), not through the store
  you just deleted from — a test that hand-seeds an index row under a synthetic id
  that happens to equal the delete key (as `tests/test_memory_reliability.py`'s FTS5
  fixture did) can pass while the real id-format mismatch goes uncaught.
- **Locked by:** `tests/test_memory_forget_is_durable.py` —
  `test_forgotten_fact_does_not_resurface_via_graph_recall` (the headline lock, red
  before the fix with `AttributeError: 'MemoryWriter' object has no attribute
  'write_tombstone'`), `test_tombstone_suppresses_node_even_when_unseeded`,
  `test_fts5_delete_uses_the_real_node_id`, plus coverage for the emitted `.og` text,
  the append-only guarantee, and the `memory.graph=false` case across all three vault
  delete methods. Also repaired `tests/test_memory_reliability.py::TestDeleteSpansAllBackends::test_delete_removes_from_fts5_when_graph_index_attached`,
  whose `MagicMock` + synthetic-same-id fixture predates this fix and would silently
  mask the real bug going forward — it now indexes a real `.og` node through a real
  `MemoryIndex` so the ids line up the way `refresh()` actually assigns them.

### BB-135 — T-47's router extraction turned `/voice/speak`'s deliberately-lazy `speak_stream` import into a module-level one, silently breaking a source-module monkeypatch

- **Symptom:** `tests/test_voice_endpoints.py::test_voice_speak_route_carries_proxy_buffering_headers`
  failed with `assert 503 == 200` — but **only** when it ran after another test in the
  same pytest process had already built a gateway app (e.g. `tests/test_authoring_backend.py`
  first, then this test). Run in isolation (or as the first test to call
  `create_gateway_app`), it passed, which made the failure look order-dependent/flaky
  rather than a real code defect.
- **Root cause:** before T-47, `create_gateway_app` did
  `from hachi.gateway.voice_endpoints import speak_stream as _voice_speak_stream` **inside**
  the factory function — a genuinely lazy import, re-executed (and re-bound) every time
  `create_gateway_app()` ran. T-47 lifted the `/voice/*` handlers into
  `src/hachi/gateway/routers/voice.py` and turned that into a **module-level** import,
  bound once when `hachi.gateway.routers.voice` is first imported (which happens via
  `hachi.gateway.routers.ALL_ROUTERS`, itself imported lazily inside
  `create_gateway_app` — but Python still only executes a module's top-level statements
  on its *first* import per process; every subsequent `create_gateway_app()` call in the
  same test session reuses the cached module and its already-bound name). The regression
  test does `monkeypatch.setattr(voice_endpoints, "speak_stream", _fake_speak_stream)`
  on the **source** module (`hachi.gateway.voice_endpoints`) right before calling
  `create_gateway_app()`, relying on the handler resolving `speak_stream` at call time.
  Once some earlier test had already imported `hachi.gateway.routers.voice` (there are
  ~30 other gateway tests that call `create_gateway_app`), the module-level alias
  `_voice_speak_stream` was frozen to the *original* function object; the later
  monkeypatch on `voice_endpoints.speak_stream` rebinds an attribute the router module
  no longer looks at, so the real (unconfigured) provider ran and returned 503.
- **Fix:** in `src/hachi/gateway/routers/voice.py`, replaced
  `from hachi.gateway.voice_endpoints import speak_stream as _voice_speak_stream` with
  `from hachi.gateway import voice_endpoints` (a module import) and changed the call
  site in `route_voice_speak` from `_voice_speak_stream(...)` to
  `voice_endpoints.speak_stream(...)`, so the attribute is looked up on the module
  object fresh on every request — the same lazy-resolution contract the pre-T-47
  closure had, restored without reintroducing a closure. Left the other three
  `/voice/*` handlers (`transcribe`, `config` GET/PUT) on their existing bare-name
  imports since no test exercises a source-module patch against them and the task
  was scoped to this one regression. Note for future router-extraction work: an
  explanatory addition to a route handler's **docstring** is picked up verbatim by
  FastAPI as the OpenAPI operation description, so it round-trips into
  `openapi/hachi-openapi.json` and `frontend/src/lib/data/schema.d.ts` — the fix
  narrative here lives in a plain `#` comment instead, to avoid failing the
  `openapi <-> frontend contract` preflight step with an unrelated diff.
- **Avoid:** when lifting a handler out of a closure that did a deliberately-lazy
  in-function import, keep the laziness — import the **module**, not a name from it,
  and reference `module.attr(...)` at call time. A bare `from x import y as _y` at
  module scope looks identical to the lazy version at a glance but binds exactly
  once per process, so it silently breaks any test (present or future) that
  monkeypatches `x.y` after that module has already been imported once elsewhere in
  the same suite — a failure mode that will not show up running the affected test
  file alone, only in full-suite / cross-file ordering.
- **Locked by:** `tests/test_voice_endpoints.py::test_voice_speak_route_carries_proxy_buffering_headers`
  (pre-existing test — this fix restores the behavior it already asserted; verified
  red with `503 == 200` when run after `tests/test_authoring_backend.py` in the same
  session, green after the fix in that same ordering).

### BB-136 — Session-ownership checks could never discriminate, and were off after every restart

- **Symptom:** The gateway appeared to enforce per-caller session isolation (token
  digests, 403 paths, owner-filtered lists) while enforcing nothing. Two session
  routes (`GET /sessions/{id}/messages`, `POST /sessions/cleanup-empty`) skipped the
  check entirely before T-62 closed that specific gap, and after any restart *every*
  persisted session was ownerless — which the policy defined as "allow."
- **Root cause:** Exactly one bearer token exists per process
  (`create_gateway_app(control_plane, auth_token, ...)`; compared once inside
  `require_auth`/`_require_auth`), so every authenticated caller hashed to the same
  digest and `_check_session_ownership`/`check_session_ownership` compared a value to
  itself. Ownership was stored as a key on the in-memory live-connection dict
  (`GatewayControlPlane._connections[session_id]["owner_token_hash"]`), and
  `set_session_owner` was a **silent no-op** when the session wasn't in
  `_connections`. `get_session_owner` returning `None` was documented as allow.
- **Fix:** ADR-0006 (`docs/architecture/adr/0006-single-tenant-authz.md`) settled the
  model: **delete the ownership layer, keep authentication.** Removed
  `GatewayControlPlane.set_session_owner`/`.get_session_owner` and the
  `owner_token_hash` connection key; `deps.check_session_ownership` (formerly
  `fastapi_app._check_session_ownership`) and `routers/runs.py`'s
  `_check_run_ownership` wrapper; every 403 call site across
  `routers/{sessions,runs,interactions,openai_compat}.py` (nine session routes, three
  run routes, two interaction routes) — including T-62's `/messages` check, which is
  now superseded; the owner-stamping on session creation and the two facades
  (`get_or_create_session`/`complete_chat`'s `owner_token_hash` parameter); and the
  inline owner-filters on `GET /sessions`, `GET /runs`, `GET /interactions`.
  `require_auth` (`gateway/deps.py`) no longer returns the sha256 digest — its
  `CallerHash` dependency alias is renamed `Authenticated` and now carries a plain
  `bool` (authenticated or not). Two unrelated features that had opportunistically
  reused the digest (BB-58's OpenAI-compat rate-limit key; T-32's facade
  session-key namespacing) were adapted to the boolean contract with a fixed
  `"authenticated"`/`"anonymous"` marker — under one token both were already single
  shared buckets per auth mode, not per-principal ones, so no observable behaviour
  changed. **Also found and removed:** `SessionStore.set_session_owner`/
  `.get_session_owner` and a persisted `owner_token_hash` column migration in
  `core/session_store.py` — a second, completely orphaned copy of this machinery,
  unreferenced by any caller since 2026-06-20, that the task's own reproduction
  steps (§3.2/§4, "grep ... returns nothing today") incorrectly claimed did not
  exist even against their own cited baseline commit.
- **Avoid:** never ship a security control that cannot fail. If a comparison can only
  ever compare a value to itself, it is documentation, not enforcement — and readers
  will trust it. Either give it a second principal to discriminate, or delete it and
  state the model plainly. When a dependency's return value exists to feed exactly
  one downstream consumer, deleting that consumer should also delete (or honestly
  retype) the value — a type still named `CallerHash` after the hash is gone is
  itself the next misleading artifact. And when a task's reproduction steps hand you
  a "you will see X" grep command, run it yourself before relying on it — this one
  was wrong even against the exact commit it claimed to be verified against.
- **Locked by:** `tests/test_session_authz_model.py` —
  `test_gateway_is_single_principal_by_decision` (structural: no ownership symbol
  survives anywhere under `hachi.gateway` or `core/session_store.py`; behavioural: the
  one authenticated principal reaches every session route, including `/messages`,
  with no owner gate).

### BB-137 — No SQLite store recorded a schema version; every migration had to be additive forever

- **Symptom:** No latent runtime failure — the cost was entirely future-tense. No
  database in Hachi could answer "what shape am I?". A downgraded build would open a
  newer schema and read it silently with the wrong assumptions, and no non-additive
  migration (rename, retype, split) had any safe path — the only way to grow a schema
  was to bolt another `ALTER TABLE ... ADD COLUMN` onto an already ad-hoc block.
- **Root cause:** No `schema_version` table or `PRAGMA user_version` existed anywhere —
  the only repo hits for the string were the unrelated obs telemetry envelope
  (`core/obs/envelope.py:88`). `core/migrations.py` provided `apply_migrations`, which
  does `ALTER TABLE ... ADD COLUMN` only and has **no version concept**; only 2 of the 5
  live stores (`files.db`, `runs.db`) called it, while `sessions.db` and `jobs.db`
  hand-rolled the identical `PRAGMA table_info` + `ALTER` algorithm inline and
  `memory_index.db` had no migration path at all (`CREATE ... IF NOT EXISTS` plus a full
  rebuild on every `refresh()`). A sixth declared store, `tui_state.db`
  (`HachiPaths.tui_state_db`), had zero consumers anywhere in the codebase.
- **Fix:** Added `SCHEMA_VERSION_TABLE`, `ensure_schema_version`, `set_schema_version`,
  and `SchemaTooNewError` to `core/migrations.py`, backed by a one-row `schema_version`
  table (`CHECK (id = 1)` enforces "exactly one row" in the schema itself). Every live
  store (`sessions.db`, `files.db`, `runs.db`, `jobs.db`, `memory_index.db`) now calls
  `ensure_schema_version(conn, current=1)` immediately after its
  `CREATE TABLE IF NOT EXISTS` block and before any `apply_migrations` call. An unstamped
  legacy DB is stamped version 1 (never the running build's `current` — that would make a
  future reader mistake "version 1" for "pristine"); a stored version higher than the
  build's raises `SchemaTooNewError` and every store lets it propagate.
  `memory_index.db` — a derived cache rebuilt wholesale from `.og` on every `refresh()` —
  rebuilds on ANY mismatch instead of raising, and is the one store where that shortcut is
  correct. Both ad-hoc `PRAGMA table_info` migration blocks
  (`core/session_store.py`, `jobs/queue.py`) were converted to `apply_migrations` calls,
  preserving their inline comments; `rg -n "PRAGMA table_info" src/` now returns exactly
  one hit (`apply_migrations` itself). The dead `HachiPaths.tui_state_db` property was
  deleted rather than versioned (nothing ever opened it), and `README.md` /
  `docs/architecture/03-tech-debt.md` updated to stop describing it as live.
- **Avoid:** stamp a version into a store the day you create it, not the day you first
  need to change its shape non-additively. Retrofitting versions onto databases already
  in users' hands means guessing what they contain — the only honest answer is "version
  1, meaning whatever shipped before we started counting" — and a regression test for
  "every live store has a schema_version table" should be built from the real store
  constructors, not a hardcoded filename list, so a sixth store added later without a
  stamp breaks the test instead of silently shipping unversioned.
- **Locked by:** `tests/test_migrations.py::TestSchemaVersion` (6 tests) —
  `test_every_live_sqlite_store_records_a_schema_version`,
  `test_unstamped_database_is_stamped_as_version_one`,
  `test_opening_a_future_version_database_raises`,
  `test_opening_sessions_db_at_a_future_version_raises`,
  `test_opening_runs_db_at_a_future_version_raises`,
  `test_memory_index_rebuilds_instead_of_raising_on_version_mismatch`.

### BB-138 — `.og` memory provenance always empty: `MemoryManager._session_id` was never assigned

- **Symptom:** No fact the agent extracted for itself ever carried an
  `->[:evidence=session:<id>]` edge in the `.og` memory graph, and every `event` node
  from every session appended to a single `memory/journal/unknown.og` instead of
  `journal/<session-id>.og`. Nothing errored. Session-close *episodes* routed
  correctly, which made the breakage invisible — the same directory held both correct
  and broken output.
- **Root cause:** `MemoryManager.sync_turn` read the session id with
  `getattr(self, "_session_id", "")` — the only occurrence of `_session_id` in the
  whole file, and a read. `__init__` never defined the attribute and no setter
  existed, so the `getattr` default always won and the extraction job was enqueued
  with `session_id=""`. That empty string flowed through `handle_extract_fact` →
  `extract_facts` → `MemoryNode(session_id="")` → `MemoryWriter.write_nodes`, where
  `_node_block` skips the `evidence` edge entirely when the id is falsy and `_route`
  falls back to `journal/unknown.og`.
- **Fix:** Declared `self._session_id: str = ""` in `MemoryManager.__init__`, added
  `set_session_id()` beside the existing `set_vault`/`set_memory_writer` setters,
  replaced the `getattr` with a direct attribute read in `sync_turn`, and had
  `AgentSession.set_session_store` propagate the real id via
  `self.memory.set_session_id(...)` right after `store.start_session()`.
- **Avoid:** never `getattr(self, "_own_attribute", default)` on your own class — it
  converts an `AttributeError` you would fix in minutes into a silent default you ship
  for a year. If an attribute is optional, declare it in `__init__` with its default.
- **Locked by:** `tests/test_memory_session_provenance.py` —
  `test_sync_turn_enqueues_job_with_real_session_id` (behavioural lock),
  `test_written_node_carries_evidence_session_edge` and
  `test_event_nodes_route_to_the_real_session_journal` (on-disk contract).

### BB-139 — Blanket `*.png` / `*.html` ignores hid 52.6 MB, two extras were no-ops, and the sdist swept in 60+ MB of stale worktrees

- **Symptom:** seven design-tool trees totalling 52,598,381 B sat at the repo root and
  appeared in NO git output — not `git status`, not `git status -uall`, not `git clean`.
  Separately, `pip install hachi-og[memory]` and `[local-models]` installed nothing (both
  extras' pins duplicated `dependencies` character-for-character), and a freshly built
  sdist swept `.claude/worktrees/` in wholesale — 60,643,104 B uncompressed (~80% of the
  archive), an ~18x sdist:wheel ratio.
- **Root cause:** `.gitignore` carried unanchored `*.png`/`*.jpg`/`*.jpeg`/`*.gif`/`*.html`
  rules plus `node_modules/`. The DIRECTORIES were never ignored — every FILE inside
  matched a global glob, so each tree looked empty to git; `!frontend/index.html` was the
  fossil of that same rule eating a needed file once before. The sdist leak's root cause is
  `hatchling`'s default sdist VCS-exclusion (verified against the installed package source,
  `hatchling/builders/config.py::vcs_exclusion_files`) reading only the repo-ROOT
  `.gitignore` via `locate_file(root, ".gitignore", boundary=".git")` — it never walks into
  nested `.gitignore` files, so `.claude/worktrees/` (excluded from git only via the nested
  `.claude/.gitignore:4`) stayed invisible to the build backend regardless of how long that
  nested rule had existed.
- **Fix:** named each of the seven design trees in `.gitignore` first, THEN narrowed the
  asset globs to repo-root-anchored (`/*.png`, `/*.html`, …) — that order matters, the
  reverse dumps 52 MB into `git status` at once; ignored `frontend/.tanstack/` (empty
  today, genuinely unignored); removed the two no-op extras (`memory`, `local-models`) and
  documented `observability` as a deliberate superset of `tracing`; added a minimal
  `[tool.hatch.build.targets.sdist] exclude = ["/.claude/worktrees"]`, taking a fresh sdist
  from 20,033,163 B / ~18x down to 3,940,545 B / ~3.55x (a normal ratio); fixed the root
  `hachi` POSIX shim (CRLF -> LF, `.venv/bin` vs `.venv/Scripts` layout detection, pinned
  via `.gitattributes`).
- **Avoid:** never put an unanchored asset glob in `.gitignore` — it does not filter noise,
  it decides what CAN be tracked, and fails silently (`git add` succeeds, `git status`
  stays clean, the file never lands). Scope every asset rule to a directory. To find what a
  repo is hiding, run `git check-ignore -v <a file inside the dir>`, not on the directory
  itself. And never assume a nested `.gitignore` protects a packaging build: a build
  backend's default VCS-awareness typically reads only the project-root ignore file — an
  exclusion declared in a subdirectory's own `.gitignore` is invisible to it and needs its
  own explicit build-target exclude.
- **Locked by:** `tests/test_repo_hygiene.py` — `test_no_blanket_asset_globs_in_gitignore`,
  `test_no_no_op_optional_dependencies`, `test_sdist_excludes_claude_worktrees`

### BB-140 — Resumed sessions silently lost their compacted context

- **Symptom:** A resumed long session had weaker context than the live session it
  continued. The compacted middle of the conversation was simply absent — the raw messages
  had been dropped from context and the checkpoint that replaced them was never stored. No
  error, no warning; the agent answered confidently from an incomplete picture.
- **Root cause:** `MemoryManager._sliding_window_compact` spliced a checkpoint `Message`
  into `_history`, and the background `_run_and_clear_summary_job` →
  `_apply_summary_to_history` mutated `msg.content` in the in-memory list only. Session
  persistence wrote exactly two rows per turn — `log_message(..., "user", ...)` and
  `"assistant"` (`gateway/orchestrator.py`) — so the checkpoint never reached `sessions.db`.
  `resume_from_session` additionally filtered anything outside `{"user", "assistant"}`, so
  persisting alone would not have been enough. A stale comment (`core/memory.py`) pointed
  readers at a `get_pending_summaries()` method that has never existed anywhere in the repo.
- **Fix:** `SessionStore` gains `CHECKPOINT_ROLE` (`"system"`), `log_checkpoint()` (returns
  the row id) and `update_message_content()` — the one narrow, single-column exception to
  the otherwise append-only `messages` table — plus a class-docstring sentence naming it the
  single durable transcript. `MemoryManager` gains `set_session_store()`, persists the
  checkpoint immediately via `_persist_checkpoint()` right after splicing it into `_history`
  (so a crash before the background job finishes still leaves an honest marker), and
  converges the persisted row via `_persist_checkpoint_update()` from
  `_apply_summary_to_history` — unconditionally, even when the in-memory sentinel scan finds
  nothing, because a later compaction may have already moved the window on.
  `AgentSession.set_session_store` now also wires `memory.set_session_store(...)`;
  `resume_from_session` widens its allow-set for `CHECKPOINT_ROLE` and re-seeds it as
  `Message(role="user", ...)` — the same disguise the live path uses — while deliberately
  keeping it out of `resumed_transcript` (the UI-rendered feed). The phantom
  `get_pending_summaries()` comment is rewritten, not deleted, to describe the real sentinel
  apply-back mechanism.
- **Avoid:** if state lives only in a Python list, it does not survive a restart — say so
  out loud in the code, and persist the marker *before* the background job that perfects it,
  not instead of it. Never leave a comment naming a method that does not exist: it sends the
  next reader hunting for a mechanism that was never built.
- **Locked by:** `tests/test_compaction_checkpoint_persistence.py` —
  `test_compaction_checkpoint_survives_a_session_round_trip`,
  `test_persisted_checkpoint_is_not_logged_as_a_user_message`,
  `test_background_summary_updates_the_persisted_checkpoint`,
  `test_resume_from_session_seeds_checkpoint_into_history`,
  `test_resume_from_session_excludes_checkpoint_from_resumed_transcript`.

### BB-143 — A ratchet nobody turns is not a ratchet

- **Symptom:** `[tool.mypy] strict = true` was declared repo-wide while hundreds of errors
  sat under it, and the blocking scoped gate (`mypy-strict-gate.ini`) covered 40 of 264
  source files under `core/`, `og_engine/`, `gateway/` (~15%). 89 files in that scope were
  already strict-clean and had simply never been added — coverage was frozen while the
  tree grew (132 -> 160 engine-layer files between the audit snapshot and this fix).
- **Root cause:** the gate's ratchet rule ("add a file once its error count reaches zero")
  lived only in the ini header, with no step in the PR flow that made anyone act on it.
- **Fix:** harvested the 89 already-clean files into the gate (40 -> 129 files, zero code
  changes), verified via the scoped gate itself (never a whole-tree mypy run, to avoid the
  stall that hit a prior attempt), and added a ratchet-floor test plus a CONTRIBUTING.md
  checklist line so the crank gets turned by default going forward.
- **Avoid:** a gate whose coverage is a hand-maintained list needs a mechanical floor test,
  or it silently stops growing. On this machine, also avoid: bare `python` on PATH is a
  global Python-Install-Manager shim, NOT the project `.venv` — it lacks `hachi` and
  `pytest-xdist`. Use `uv run python -m mypy` / `uv run python -m pytest`, not bare `python`.
- **Locked by:** `tests/test_p1_14_mypy_gate.py::test_strict_gate_covers_at_least_the_ratchet_floor`

### BB-144 — A deferred import does NOT escape import-linter

- **Symptom:** an engine->gateway import inside a function body, wrapped in try/except, was
  believed to "escape" the layering gate — leading to the conclusion that the gate had a
  hole and that moving an import into a function was a way to satisfy it.
- **Root cause:** grimp (import-linter's graph builder) reads the AST. A function-local or
  try-wrapped import is an edge in the graph exactly like a module-level one. The five
  `core`/`tools` -> `hachi.gateway` imports (onboarding, cost, resume, job_handlers,
  config_tools) were not escaping anything — they were explicitly whitelisted in
  `pyproject.toml`'s `[tool.importlinter]` `ignore_imports`.
- **Fix:** none needed in the linter. All five engine->gateway edges were declared debt;
  burned down by inverting each dependency — `core.onboarding` and `tools.config_tools`
  via engine-owned collaborators (`hachi.core.skill_packs.install_skill_pack`,
  `config_tools.get_skills_dir`), and `core.cost` / `core.runtime.resume` /
  `core.runtime.job_handlers` via a shared `Protocol` (`hachi.core.runtime.protocols.ProcessServices`)
  that the gateway satisfies structurally and registers a resolver for once, at import
  time, in `gateway/shared_services.py`.
- **Avoid:** never "fix" a layering violation by deferring the import — it changes nothing
  the linter sees and nothing about the runtime coupling. To prove an edge is really gone,
  assert in a FRESH subprocess that the module is absent from `sys.modules`. And remember
  the contracts are self-cleaning: an `ignore_imports` entry that matches nothing FAILS the
  run with `No matches for ignored import <edge>`, so deleting the entry is the completion
  signal for each seam.
- **Locked by:** `uv run lint-imports` (blocking in `scripts/preflight.py` and CI);
  `tests/test_engine_gateway_decoupling.py`

### BB-145 — `hachi backup` silently omitted most user data, including the entire .og memory graph

- **Symptom:** `hachi backup` printed a green "Backup created" panel and copied only
  four things: `sessions.db`, `chroma_vault/`, `settings.toml` and
  `device-permissions.og`. A user who backed up, wiped and restored lost their authored
  `.og` skills, `brain.og`, `runs.db`, `files.db`, `blobs/`, `checkpoints/`,
  `schedules.json`, `skill_usage.json`, `onboarding_state.json`, `secrets_metadata.json`,
  `secrets.enc` and the entire job queue. **Under `memory.backend = "fts5_only"` — where
  `data_dir/memory/**/*.og` is the CANONICAL fact store and no Chroma vault exists — the
  restore produced an agent that remembered nothing.** `hachi doctor` was equally blind:
  four directory-existence checks and no database probe.
- **Root cause:** `backup()`/`restore()` (`cli/main.py`) hardcoded a four-item list
  written before most stores existed, and nothing connected it to `core/paths.py` where
  stores are declared. `HachiPaths` exposed eighteen path properties and no way to
  enumerate them, so every consumer had to hand-maintain its own list — and this one was
  never updated.
- **Fix:** Added `HachiPaths.all_stores()` (`core/paths.py`) returning `StoreSpec` records
  (name, path, kind, derived, note) as the single source of truth. `backup`, `restore`
  and `doctor` (`cli/main.py`) all enumerate from it — no second hand-written list.
  Backup emits a `manifest.json` recording what was captured and what was deliberately
  skipped and why; restore reads it (falling back to plain on-disk presence for pre-fix
  backups) to report gaps, and never deletes a live store whose backup counterpart is
  absent. `sessions.db` keeps its online hot-backup; every other SQLite store (`jobs.db`,
  `runs.db`, `files.db`) is copied via `sqlite3.Connection.backup()` in both directions,
  clearing stale `-wal`/`-shm` sidecars first so a copy can never desync from its base
  file. `doctor --json` gained an additive `stores` map (presence, size, and — for
  SQLite — a successful-open probe) without removing any existing key.
- **Avoid:** a backup command must never hardcode what to copy. Enumerate from the same
  declaration the stores are created from, and add a test that fails when a new store is
  declared without being registered — otherwise "backup" degrades into "backup of
  whatever existed the day it was written", silently, with a success message.
- **Locked by:** `tests/test_backup_restore.py` —
  `test_full_round_trip_preserves_every_registered_store` (the round-trip lock),
  `test_backup_captures_the_og_memory_graph`,
  `test_every_hachi_path_is_registered_or_explicitly_exempt`,
  `test_backup_manifest_lists_every_store_captured_or_skipped_with_reason`.

### BB-146 — `memory.backend = "in_memory" / "fts5_only"` resolved a provider, then discarded it

- **Symptom:** Setting `memory.backend = "in_memory"` (or `"fts5_only"`) produced an
  agent whose memory tools all answered "Long-term memory is not available", with no
  error — while boot logged a successfully resolved and initialized provider.
- **Root cause:** `MemoryProvider`'s contract methods (`upsert`/`search`/`delete`) had
  zero call sites in `src/`. Boot called `initialize()` then immediately unwrapped:
  `vault = _provider.vault if isinstance(_provider, ChromaMemoryProvider) else None`
  (duplicated in `gateway/orchestrator.py` and `gateway/shared_services.py`) and used
  the raw `MemoryVault` for everything downstream. The two non-Chroma providers have
  no `.vault`, so the unwrap yielded `None` and every vault-facing call site
  short-circuited. The seam existed as a type, never as a call path.
- **Fix (T-49, option B — adopt, not delete):** Added `ProviderBackedVault`
  (`core/memory_provider.py`) — a facade whose `upsert_fact`/`semantic_search`/
  `delete_fact` (and the user/agent-profile equivalents) delegate to the wrapped
  provider's real `upsert()`/`search()`/`delete()`. Boot (`shared_services.py::_init`,
  `orchestrator.py::boot_agent`) now builds this facade instead of `None` for
  `in_memory`/`fts5_only`. Deep Chroma-only features (episodic memory, delegation
  search, the quarantine trail) stay honestly absent (`[]`), not faked.
- **Avoid:** an interface with no call sites is not a seam — it is a comment with a
  type annotation. Before adding an abstraction "for future backends," write the call
  site that would use it; if a `None`-fallback silently disables the feature the
  config claims to offer, that fallback IS the bug.
- **Locked by:** `tests/test_memory_provider.py` —
  `test_provider_backed_vault_upsert_routes_through_provider_upsert`,
  `test_provider_backed_vault_search_routes_through_provider_search`,
  `test_provider_backed_vault_delete_routes_through_provider_delete`,
  `test_provider_backed_vault_in_memory_write_then_read_round_trips`,
  `test_provider_backed_vault_fts5_only_write_then_read_round_trips`,
  `test_shared_services_in_memory_backend_no_longer_produces_a_none_vault`.

### BB-147 — A banned compat shim and two `.env` bypasses survived because nothing checked

- **Symptom:** `TOKOBOROS_*` env vars were still silently copied to `HACHI_*` on every
  `import hachi.cli.main`, long after the hard rename and despite an explicit
  repo-level ban. Separately, `HACHI_DISABLE_DOTENV_AUTOLOAD=1` did not stop core from
  reading a CWD `.env`: `core/llm.py::_resolve_api_key` and `core/oauth.py::_resolve_env`
  each read it directly, unconditionally — so a real repo-root `.env` (dev key) leaked
  into any test that booted a client without providing its own key, hiding the bypass.
- **Root cause:** Both rules were written in prose only. The tokoboros ban lived in
  CLAUDE.md with no gate step, so a later commit reintroduced the shim and CI stayed
  green. The dotenv gate was implemented as a local `if` inside
  `cli/main._load_dotenv_into_environ` rather than as a shared predicate, so
  `core/llm._resolve_api_key` and `core/oauth._resolve_env` — added later,
  independently — each grew their own ungated `Path.cwd() / ".env"` read.
- **Fix:** Deleted the shim (`cli/main.py`) and added `scripts/check_bans.py` as a
  BLOCKING preflight/CI step matching `TOKOBOROS_[A-Z0-9_]+` in `src/`+`scripts/`.
  Added `core.paths.dotenv_autoload_disabled()` as the single definition of the gate
  and applied it at all three read sites (`cli/main.py`, `core/llm.py`, `core/oauth.py`).
  Eight tests that were riding the ambient `.env` leak now declare their key explicitly
  (opt into `HACHI_DISABLE_DOTENV_AUTOLOAD=0` where the `.env` fallback IS the subject,
  or `setenv` the key directly where it was incidental plumbing — e.g. the cockpit-CRUD
  and routing-DefaultClient tests) — CI-safe, since `.env` is gitignored.
- **Avoid:** a ban that is not a gate step is advice, and it will be violated. When you
  write "do not do X" in CLAUDE.md, add the check in the same commit. And when a rule
  has a second reader, make the rule a shared predicate — never a local `if`. A test
  that boots a real client without setting a key may be silently riding an ambient
  `.env`; the fix is to make it declare the credential, not to keep the leak.
- **Locked by:** `tests/test_governance_bans.py::test_no_tokoboros_env_shim_in_src`;
  `tests/test_dotenv_hermeticity.py::test_llm_resolve_api_key_honours_disable_flag`,
  `test_oauth_resolve_env_honours_disable_flag`.

### BB-148 — A package-boundary move breaks import shapes a dotted-path grep can't see

- **Symptom:** Rewriting every `from hachi.core.memory_vault import X` site (T-48's
  move of core/memory*.py into core/memory/) passed `rg "hachi\.core\.memory_"` clean,
  but a full scoped test run still failed collection with
  `ImportError: cannot import name 'memory_vault' from 'hachi.core'`, plus separate
  ImportErrors for `_TOOL_RESULT_CLEAR_TOKENS`, `_MAX_ACTIVE_TOKENS`,
  `_is_substantive_fact`, `_CLEARED_TOOL_RESULT` — none of which matched the grep.
- **Root cause:** three shapes a dotted-path search misses: (1) `from hachi.core
  import memory_vault as _mv` — the old name sits after `import `, not glued to
  `core.` by a dot; (2) `from hachi.core import <private_name>` — reaching a
  formerly-flat module's private constant through the package's bare name, which
  only survives for the __init__.py's deliberately narrow re-export list; (3) a
  bare-string `sys.modules`/`Path()` reference used for cache-eviction or
  file-existence checks (`if "memory_vault" in k`, `Path(".../memory_vault.py")`)
  — neither raises; they silently stop matching, so the check they guard
  quietly no-ops forever (one was a real coverage loss: a `_TARGET_FILES` scanner
  that `continue`s on `not full.exists()`).
- **Fix:** grepped the submodule *basename* as a whole word (`\bmemory_vault\b`,
  not `hachi\.core\.memory_vault`) across the whole tree, classified every hit as
  prose / unrelated identifier / load-bearing, and fixed only the load-bearing
  ones. Finished with a full-tree `pytest --collect-only` (not just the
  grep-derived file list) as the real proof — 4601 tests, zero collection errors.
- **Avoid:** after any package-boundary move, `rg "pkg\.old_flat_name"` alone is
  not enough — also grep the bare basename as a whole word across the whole tree
  and read every hit. Watch for `from pkg import old_name as x`,
  `getattr`/`setattr`/`patch(pkg, "private_name")`, and any `sys.modules`/`Path`
  string keyed on the old name. Finish with a full-tree
  `pytest --collect-only -q` — cheap, and it catches every remaining import site
  regardless of which pattern missed it. Separately: `core/foo.py` -> `core/foo/`
  is a name collision, not a rename — create the package dir + `__init__.py`
  first, and delete `src/hachi/core/__pycache__/` before AND after the move if
  you hit a bizarre ImportError (a stale `foo.cpython-*.pyc` can shadow the new
  package in a dirty tree).
- **Locked by:** `tests/test_memory_package_boundary.py` + the full-tree
  `pytest --collect-only` pass this fix required to go green.

### BB-149 — A class-move's stale patch target isn't always a string, and a moved class can drag a same-file helper with it

- **Symptom:** After extracting `_InProcessGatewayRuntime` from `cli/main.py` to
  `gateway/in_process_runtime.py` (T-41), two problems surfaced that the census
  `grep -rhoE '"hachi\.cli\.main\.[a-zA-Z_]+"' tests/` does not catch: (1)
  `tests/test_memory_gardener_wiring.py` rebound the target with
  `cli_main.get_hachi_paths = lambda: fake_paths` plus a manual restore in a
  `finally:` — a bare attribute assignment, not `monkeypatch.setattr`/`patch()` —
  so 2 tests silently started reading the developer's real `~/.hachi` config
  after the move; (2) `ruff check` on the new module raised
  `F821 Undefined name '_pack_display_name'` — a private helper sitting
  immediately *after* the class in the old file, used by exactly one method
  (`get_kennel`), was outside the literal class span, and importing it back from
  `cli.main` would have reintroduced the exact gateway->cli inversion the move
  exists to remove.
- **Root cause:** (1) `mock.patch`/`monkeypatch.setattr` are not the only way a
  test rebinds a module attribute — a direct `module.name = value` with manual
  save/restore is equivalent and equally invisible to a string-literal grep.
  (2) A class's line span is not its full dependency surface: an adjacent,
  non-exported, single-consumer helper can be *logically* part of the class
  without being *textually* inside it.
- **Fix:** swept the tree for `alias\.[a-zA-Z_]+\s*=\s*(lambda|MagicMock)` against
  every alias `hachi.cli.main` is imported under (`cli_main`, `main_mod`) and
  re-pointed the hits; ran `ruff check` on the new module as an active F821 sweep
  (not just an unused-import check) and moved `_pack_display_name` alongside the
  class once the sweep proved it had no other consumer.
- **Avoid:** after moving a class between modules, grep the old target as BOTH a
  quoted patch string AND a bare `alias.name = value` assignment, for every alias
  the old home is imported under. Then run `ruff check` on the new module — `F821`
  names any function that sat next to the class rather than inside it. Verify each
  re-pointed patch by temporarily breaking the real implementation and confirming
  the test goes red: a green suite after a module move proves nothing, because a
  stale patch target is a silent no-op that lets the real function run against a
  tmp path that happens to work.
- **Locked by:** `tests/test_in_process_runtime_extraction.py::test_runtime_is_importable_from_gateway_without_cli`
  (proves the class needs nothing left behind in `cli.main`);
  `tests/test_memory_gardener_wiring.py::test_post_memory_diff_decision_approve_route_end_to_end`
  and `::test_post_memory_diff_decision_reject_route_drops_from_store` (re-pointed;
  verified red when reverted).

### BB-150 — Deleting `sys.modules` entries to force a re-import poisons every later test holding the old reference

- **Symptom:** `tests/test_in_process_runtime_extraction.py::test_runtime_is_importable_from_gateway_without_cli`
  passed alone, but made `tests/test_schedule_show.py` (6),
  `tests/test_schedule_run_now.py` (4) and `tests/test_schedule_pause_resume.py`
  (4) fail with CLI commands exiting 1 — only when run after it in the same
  session. Every one of those 14 passed in isolation. The full-suite gate was the
  only thing that caught it; scoped runs were green.
- **Root cause:** the test deleted every `hachi.cli*` entry from `sys.modules`
  (deliberately scoped to that prefix so it would never repeat BB-3's chromadb
  mistake) and re-imported the gateway module, which pulls `hachi.cli.main` back
  in as a *new* module object. Any test that already did
  `from hachi.cli.main import app` at import time keeps its name bound to the
  *orphaned* object while everything imported afterward gets the new one; the two
  diverge (Typer command registration, module-level singletons) and
  CliRunner-driven commands built on the stale object start failing. Scoping away
  from chromadb was necessary but not sufficient — `hachi.cli` is exactly what the
  CLI test files depend on, so deleting it is never safe in a shared interpreter,
  whatever the prefix.
- **Fix:** proved the property in a disposable subprocess instead —
  `subprocess.run([sys.executable, "-c", script], cwd=<repo root>, capture_output=True,
  timeout=60)` asserting `returncode == 0`, never `shell=True`. This interpreter's
  `sys.modules` is never mutated.
- **Avoid:** never delete `sys.modules` entries to answer "does importing X require
  Y?" inside the pytest process — the mutation outlives the test and corrupts
  whichever later test already bound a name to the deleted module, as an
  order-dependent failure far from its cause. Use a subprocess: hermetic, and a
  strictly *stronger* assertion, because it proves the import genuinely never pulls
  the dependency in rather than inspecting a dict the test just edited by hand.
  Note this trap shipped in T-41's own spec sketch (§7) — a spec proposing a
  `sys.modules` deletion is a defect in the spec.
- **Locked by:** `tests/test_in_process_runtime_extraction.py::test_runtime_is_importable_from_gateway_without_cli`
  (subprocess-based) — confirmed red when the new module was made to import
  `hachi.cli.main`, and confirmed order-independent against the three schedule test
  files with `-p no:randomly`.

### BB-151 — An import-linter exemption justified by a dependency that never existed, and the `getattr` readers deleting it would have silently broken

- **Symptom:** `pyproject.toml` exempted `hachi.core.runtime.resume -> hachi.cli.main`
  — the only `core -> cli` import in the repo — justified in its comment as *"cold-start
  resume needs the CLI's Typer app to rebuild a session's command context."* No Typer app
  is involved anywhere in that path. The real dependency was the `_ACTIVE_RUNTIME` module
  global plus two private attributes (`_agents`, `_agents_lock`) on the runtime it held.
  The `_ACTIVE_RUNTIME` slot's own docstring claimed it existed so resume could work
  *"without importing the CLI"* — while being the exact mechanism by which the CLI got
  imported. Both comments were wrong in the same direction, and a reader trusting either
  concludes the seam is load-bearing forever.
- **Root cause:** the exemption comment was written from intent rather than from the code,
  and was never re-checked as the code moved — T-41 relocated the very class the seam
  reaches for out of `cli/main.py`, and the comment still did not change.
- **Second, sharper trap:** T-42's own spec listed the two *writers* of `_ACTIVE_RUNTIME`
  and told the implementer to delete the slot. A whole-repo grep (not the task's file
  list) found two more *readers* — `cli/commands/channel.py` and
  `cli/commands/schedule.py`, whose governed-boot helpers did
  `getattr(_main_mod, "_ACTIVE_RUNTIME", None)` to get the full runtime for
  `boot_registered_session`. Deleting the global would have made both return `None`
  forever **with no exception and no failing test anywhere near the diff**, silently
  regressing governed channel/schedule boot to the pre-T-20 ungoverned path.
- **Fix:** a two-method `SessionRuntime` protocol (`get_live_session`, `rehydrate`) in
  `core/runtime/protocols.py`, injected from the edge via
  `set_active_session_runtime`/`get_active_session_runtime`; exemption deleted; both
  unlisted readers repointed at the getter.
- **Avoid:** an `ignore_imports` comment must name the *symbol* being reached for, not the
  module's reputation — and must be re-verified whenever the target moves. Exploit
  import-linter's default `unmatched_ignore_imports_alerting = ERROR`: remove the import
  and leave the exemption, and the linter *fails* with `No matches for ignored import …`.
  That failure is a free, unambiguous proof the import is gone; a contract-violation
  failure instead means you missed one. Separately, and more generally: **before deleting
  a module-level global, grep the WHOLE repo for it, never just the files a task brief
  names.** `getattr(module, "name", default)` degrades silently to the default once
  `name` is gone, so a reader outside your stated scope regresses without a single test
  failing near your change.
- **Locked by:** `tests/test_core_cli_seam.py::test_core_does_not_import_the_cli`;
  `tests/test_governed_session_entry_points.py` (channel/schedule governed-boot and
  isolation-guard tests, retargeted at the new seam).

### BB-152 — Terminal outcomes encoded as string prefixes: the loop's exit reason was unaddressable, and one sentinel was asserted both ways

- **Symptom:** Nothing could ask "why did this turn end?" as a value. `core/sub_agent.py`
  recovered "was this cut off?" with `result.startswith(INCOMPLETE_RESULT_PREFIX)`. The
  structured wait condition behind `[WORKFLOW SUSPENDED]` had already been "lost inside the
  return string" once and needed a side channel (`self._last_suspend`) bolted on to recover
  it — the design failing in-repo and being patched around. The prefixes are also literal
  text in the assistant's reply, so the eval corpus carries a case asserting they *don't*
  leak into clean output: a test defending against our own sentinel design.
- **Root cause:** `ExecutionLoop.run() -> str` smuggled five terminal outcomes (cancelled,
  time limit, budget limit, suspended, incomplete) back inside the assistant text, across
  **nine** return sites in `core/loop.py`, with a sixth emitter in another layer
  (`gateway/orchestrator.py`'s daily-cap check) hardcoding the `[BUDGET LIMIT REACHED]`
  literal a second time. A separate loop-local `AgentException` sat outside the
  `core/errors.py` `HachiError` taxonomy, giving the repo two exception roots.
- **Fix:** A frozen `TurnOutcome(status, text, reason, limit_seconds, max_iterations,
  suspend)` + `TurnStatus` enum. The nine sites build one, store it on `self._last_outcome`,
  and return `outcome.render()` — now the only place a prefix literal exists. `sub_agent`
  branches on the status field; `orchestrator` renders through the same object.
  `AgentException` reparented under `HachiError`. Rendered text is byte-identical, so the
  change is behaviour-preserving by construction.
- **Avoid — three traps, in order of how much time they cost:**
  1. **Never encode control-flow state in a human-readable return string.** It cannot carry
     structure, it leaks internal sentinels to users, and it turns every downstream branch
     into a substring match no type checker can verify. If a function must return both
     display text and an outcome, return an object.
  2. **"Preserve both, they differ" — check whether they differ in OUTPUT or only in
     construction.** The task brief forbade collapsing two pairs of near-duplicate sites
     because they supposedly produced different text. They did not: sites 1/5 were the same
     expression character-for-character, and sites 2/6 were differently-shaped f-string
     concatenations (`f"...exceeded " f"{s:.0f}s wall-clock limit.{suffix}"` vs
     `f"...exceeded {s:.0f}s " f"wall-clock limit." f"{...}"`) that evaluate to identical
     strings. Five render shapes, not nine. What *did* differ was the surrounding
     `on_event` / `memory.complete_task` calls — which are not part of the return value.
     **Diff the rendered output, not the source expression, before duplicating a branch.**
  3. **A sentinel can be asserted in both directions at once.** `core/eval/corpus.py`
     asserts `output_contains("[INCOMPLETE]")` in the `max_iterations_cap` case *and*
     `output_not_contains("[INCOMPLETE]")` in `no_internal_markers_in_clean_output`. A
     rename that satisfies every negative assertion still fails the positive one, and
     `hachi eval run` is a blocking preflight step whose failure reads like a model
     regression rather than a refactor. Grep a sentinel for **both** polarities before
     touching it.
- **Also:** a new `last_*` property does not have to grow the `scripts/check_getattr.py`
  allowlist. `_last_suspend` / `_last_tool_calls` use `getattr(self, ..., None)` because
  they are deliberately absent before the first `run()` (BB-107). `_last_outcome` has no
  such contract, so initialising it in `__init__` and reading it plainly keeps the
  suppression list from growing. Copy a pattern's *reason*, not its shape.
- **Locked by:** `tests/test_turn_outcome.py` —
  `test_turn_outcome_render_is_byte_identical_to_legacy_prefixes` (all five shapes,
  including the empty-text `"[CANCELLED]"` with no trailing space and the `:.0f` seconds
  formatting) and `test_turn_outcome_is_addressable_without_string_parsing`.

### BB-153 — A 77-callable constructor hid a second axis: which handlers need a live session

- **Symptom:** `GatewayControlPlane.__init__` took **77** parameters — 74 optional handler
  callables plus a readiness probe and two tunables — wired from one 95-line call in
  `cli/main.py`. All 74 defaulted to `None`, so a control plane missing half its wiring
  constructed fine and failed at the first request; 44 `_require_handler` call sites exist
  purely to compensate for a requirement the type system was never allowed to express.
- **Root cause:** Per-endpoint dependency inversion with no grouping. Every new endpoint added
  a parameter, a delegate method, and a wiring line, so the constructor grew monotonically.
- **The trap that matters — a flat parameter list can encode TWO axes, and only one is
  visible.** The 74 params looked like a single flat bag, but they carried two orthogonal
  facts: *which domain* an endpoint serves (guessable from the names) and *whether it needs a
  live agent session* (**not guessable from anything in the signature**). The second axis
  existed only as a per-parameter idiom at the single call site — `runtime.X if runtime else
  None` for 28 of them versus `support.X` unconditionally for the other 46 — where `runtime`
  is `None` before a session boots and `support` never is (BB-6). Grouping by the visible axis
  alone would have flattened the invisible one: five of the fourteen domain groups a
  domain-only decomposition produces are **internally mixed**, so collapsing each onto one
  object would have made live-only endpoints (`get_spend`, `get_skills`,
  `get_memory_gardener_diffs`) answer before a session existed, or made standalone ones
  (`get_spend_daily`, `get_kennel`, `decide_memory_gardener_diff`) start failing pre-session.
  Neither would fail a test near the diff.
- **Avoid:** Before grouping a large flat parameter list, **read the call site and check
  whether it encodes an axis the parameter names do not.** A conditional repeated across
  dozens of arguments is not boilerplate — it is a load-bearing distinction with no home in
  the type system. Give it one: here, ports whose name begins `Live` are nullable and need a
  session; every other port is always wired. The rule is now legible from `ports.py` alone.
- **Also:** port members must be named for the **provider's** methods, not the consumer's
  parameters. 19 of the 74 differ (`on_connect`→`register_connection`,
  `add_skill`→`add_skill_from_kennel`, `get_capabilities`→`capabilities`). Transcribing
  protocols from the constructor signature yields protocols the provider cannot satisfy — and
  `@runtime_checkable` only checks member *presence*, never signatures, so some of that
  surfaces late or not at all.
- **Method note:** the port map was generated from the builder programmatically and validated
  (every handler assigned exactly once, every declared source matching the real one) rather
  than hand-transcribed. That is what surfaced the five mixed groups; a by-hand reading of a
  95-line call had already missed them once.
- **Locked by:** `tests/test_control_plane_characterization.py` —
  `test_runtime_satisfies_every_port`, `test_control_plane_helper_matches_legacy_kwargs_form`,
  and the three behaviour pins `test_session_limit_is_enforced_at_the_control_plane` /
  `test_prompt_length_limit_is_enforced` / `test_failed_connect_releases_the_session_slot`.

### BB-154 — Inventorying call sites by text search miscounts in BOTH directions

- **Symptom:** Planning T-43's test migration, a text-search inventory of
  `GatewayControlPlane(...)` call sites reported **99 sites across 23 files**. The real figure
  was **94**, and the file *set* was wrong in both directions — two listed files needed zero
  conversions, two genuine consumers were missing from the list entirely.
- **Root cause — three distinct failure modes, and they do not cancel out:**
  1. **Prose false positives.** Docstrings and comments containing the constructor name
     (`` T-15: injected via ``GatewayControlPlane(list_pending_approvals=...)`` ``) match a
     source regex exactly like real code does.
  2. **Indirection false negatives.** A local wrapper — `def _cp(**kwargs): return
     GatewayControlPlane(**kwargs)`, called by ~20 test functions — is one textual hit that
     is really one *conversion site* with wide reach, and a fixture or wrapper that forwards
     `**kwargs` exposes no literal kwarg names to match on at all.
  3. **A wrong predicate.** The inventory classified a site as "must change" if it passed any
     parameter from the constructor's full parameter list — but `readiness_probe` was never
     one of the 74 handlers being retired. Files whose only argument was `readiness_probe`
     were flagged as needing work they did not need.
- **Avoid:** For any mechanical multi-file refactor, inventory call sites with `ast.parse` and
  an `ast.Call` walk, not text search — the AST cannot match a docstring, and it makes
  wrappers visible as ordinary calls. Then **state the classification predicate explicitly and
  check it against the actual change**, because (3) is not a tooling failure: a perfect parser
  fed the wrong question returns a confidently wrong answer.
- **Wider point:** T-43's own port map WAS generated and self-validated programmatically, which
  is what caught the five mixed domain groups a manual reading had already missed. The
  call-site inventory was the one artefact in that task done by hand — and it was the one that
  was wrong. Apply the same rigour to both halves of a refactor: the thing being changed and
  the set of places that must change with it.
- **Locked by:** nothing executable — this is a methodology entry. The corrected inventory is
  reflected in `tests/conftest.py::control_plane`'s docstring and T-43's ADR.

### BB-155 — A verification harness fails in both directions, and both look like results

- **Symptom (fail-open):** T-77's full gate was launched as
  `preflight.py > "$LOG" 2>&1; echo "PREFLIGHT_EXIT=$?" >> "$LOG"`. The task notification
  reported **"completed (exit code 0)"**. Preflight had never run: the scratchpad directory the
  log path pointed into had been removed mid-session, so the redirect failed before the command
  started. `echo` still exited 0. Grepping the (nonexistent) log for a failure verdict returned
  nothing — which reads exactly like "no failures".
- **Symptom (fail-closed):** an AST-based checker comparing `git show HEAD:file` against the
  worktree reported **21 prompt literals silently lost**, including strings in functions the
  diff never touched. Every one of them contained `—`, `…` or `≤`, and one output line carried a
  literal `U+FFFD`. Nothing was lost.
- **Root cause:**
  1. Redirection is evaluated by the shell *before* the command runs, so a bad log path turns
     "run the gate and record it" into "run nothing"; and the exit status you capture is the
     last command's, which is the `echo` you added to capture it.
  2. `subprocess.run(..., text=True)` decodes with the **locale** codepage on Windows (cp1252),
     not UTF-8. This repo's prose uses em dashes heavily, so every multi-byte character
     mojibakes and every string containing one compares unequal to its worktree twin.
- **Avoid:** `mkdir -p` the log directory and prove it writable (`: > "$LOG"`) in the same
  command that runs the gate; then **read the log's own verdict line**
  (`[OK] preflight passed clean` / `Summary: N passed, M failed`), never the exit code, and
  treat *no verdict line at all* as a failure rather than as silence. Pass
  `encoding="utf-8"` explicitly to every `subprocess.run` that reads repo content on Windows.
- **Wider point:** both failures were in the checking apparatus, not the code under test, and
  neither announced itself as a tooling fault — one produced a clean bill of health, the other a
  21-item catastrophe. Before believing a verification result that is surprisingly good *or*
  surprisingly bad, check the instrument. The tell for the second was that the "damage" fell
  precisely along a character-class boundary rather than along the diff. See also BB-1 (Windows
  tooling), BB-154 (a correct parser fed the wrong question), and T-41's note that a gate's exit
  code is not its verdict.

### BB-156 — Flattening a delegation drops the delegate's side effects, and the whole suite stays green

- **Symptom:** during T-66, `approveByRequestId()` was rewritten from a wrapper that delegated
  to `approve()` into a "thin wrapper" calling `postDecision()` directly and returning
  `outcome === "ok"`. Typecheck passed (both shapes return `Promise<boolean>`), all 253 frontend
  tests passed, and the diff read as a pure simplification. A failed decision from the
  in-context leash had nonetheless become completely invisible.
- **Root cause:** `approve()` does more than return — on a non-`"ok"` outcome it calls
  `showToast(DECISION_FAILED_MESSAGE, "blocked")`. The rewrite reimplemented the return value
  and nothing else. `chat.ts`'s `allowLeashLive()` clears the leash optimistically
  (`hydrate({ inContext: null })`) and then discards the boolean inside a `try/catch`, so that
  toast was the **only** surface a failed POST reached from that call site. Dropping it meant:
  leash disappears, no toast, no error, agent still blocked until the 180 s server-side timeout
  auto-denies — the exact silent-failure mode T-18 removed (BB-106), reintroduced at a call site
  T-18 never touched.
- **Why no test caught it:** the existing tests covered the *approvals screen* path (which still
  toasted, via `approve()`) and the *unknown-id* path (unchanged). Nothing covered the leash
  path's `"failed"` branch. A green suite was evidence about the paths under test, not about the
  path that changed.
- **Avoid:** when replacing `await f(x)` with an inlined equivalent, enumerate `f`'s side
  effects — store mutations, toasts, logging, cache invalidation — and account for each one
  explicitly. Never infer equivalence from a matching return type or from a green suite. Where
  the old and new paths differ *deliberately* (here `"failed"` toasts but `"unknown-id"`
  deliberately does not, matching the old map-miss branch), write the asymmetry into the doc
  comment so the next reader does not "fix" it.
- **Wider point:** this was found by reading the old and new control flow side by side and
  asking "what did the function I removed *do*, besides return?" — not by any automated check.
  A refactor that deletes a call site deletes everything that call site caused. See also BB-106
  (the original silent-decision failure) and BB-155 (the checking apparatus is itself fallible).
- **Locked by:** `frontend/src/lib/data/domains/approvals.test.ts` — 3 tests: failure toasts for
  both `approveByRequestId` and `declineByRequestId`, plus one pinning that `"unknown-id"` stays
  silent.

### BB-157 — A read fallback without a matching write fallback forks the state it was added to share

- **Symptom:** a user connects Google in the web UI; the agent picks the token up and works.
  Roughly an hour later — the first silent refresh — the web UI's copy freezes forever. The
  vault ends up holding two tokens for one grant, with two different `expires_at` values, and
  nothing reconciles them. Nothing appears broken while it happens: the agent keeps working, on
  its own copy, which is exactly why it stayed invisible.
- **Root cause:** two OAuth stacks with two vault-key prefixes — `core/oauth.py`'s `_key()` →
  `oauth.<p>` (the agent), `integrations/oauth_flow.py`'s `_token_key()` → `oauth2.<p>` (the web
  UI). BB-10 made the *read* cross the gap: `get_token` fell back to `oauth2.<p>`. But
  `save_token` had exactly one `self._store.set` and it always targeted `_key(provider)`, with no
  fallback. So `_refresh_google_token`'s write-back landed on `oauth.<p>` even when the token had
  just been read from `oauth2.<p>`. From the next read onward `oauth.<p>` wins the lookup and the
  other copy is never read again — and never refreshed either, because
  `oauth_flow._refresh_token` has **zero callers**.
- **Second face — `hachi google-auth --revoke`:** the CLI clears credentials with
  `oauth.save_token("google", {})` (`cli/main.py:1948`). For a web-UI-authenticated user that
  wrote `"{}"` to `oauth.<p>` and left the real token live at `oauth2.<p>` — the empty payload
  *shadowed* the grant (a truthy `"{}"` string stops the BB-10 fallback from firing) instead of
  clearing it. A credential the user explicitly asked to remove stayed on disk. The same one-key
  fix corrects this for free, with no change to `cli/main.py`.
- **Fix:** `OAuthManager._resolve_key(provider)` resolves the vault key once — prefers
  `oauth.<p>`, falls back to `oauth2.<p>` only when that is the only one present, and returns
  `oauth.<p>` when neither exists so a fresh authorisation lands on the agent prefix. `get_token`
  and `save_token` (including its `metadata["name"]`) both route through it. A write now lands on
  the key the read came from: one grant, one key.
- **What is verified, and what is not:** verified — after a refresh the key that held the token
  holds the new access token, and no second key is created. NOT claimed — that the two stacks are
  unified (that is T-57 half B, still open), nor that anything user-visible broke during the fork.
- **Avoid:** a read fallback without a matching write fallback forks the state it was added to
  share. If you add a compatibility read for a second storage key, fix the write in the same
  commit — otherwise the divergence appears on the first *mutation*, hours later, in a code path
  nobody is watching. Generally: when a getter and a setter disagree about where a thing lives,
  the getter's tolerance hides the setter's bug.
- **Locked by:** `tests/test_oauth.py::test_refresh_writes_back_to_the_key_it_read_from` and
  `::test_clearing_a_token_updates_the_key_that_actually_holds_it`. BB-10's sibling test
  `test_get_token_finds_web_ui_oauth2_prefix` still passes, unmodified.

### BB-158 — Moving code between files silently breaks location-keyed test and tooling assumptions

- **Symptom:** T-46 relocated 20 read-model methods (including `snapshot_gardener_diffs` and two
  defaulted-`getattr` call sites) from `AgentSession` (`gateway/orchestrator.py`) into a new facade
  (`gateway/session_read_models.py`), preserving every line of logic verbatim. Two independent,
  unrelated checks broke anyway.
  1. `tests/test_memory_gardener_wiring.py` monkeypatched `hachi.gateway.orchestrator.get_hachi_paths`
     to redirect a file read to a tmp directory. Once the read lived in a module that imports
     `get_hachi_paths` into its own namespace, the patch stopped taking effect — the moved code
     silently read the real machine's data directory and returned `[]`.
  2. `scripts/check_getattr.py`'s ALLOWLIST is keyed `"<path>:<attr>"` *deliberately* rather than by
     line number, so it survives unrelated edits. Relocation is exactly the case that key format does
     not survive: two already-allowlisted sites became "unallowlisted" purely by moving files, failing
     `tests/test_check_getattr.py` — a **blocking** preflight step, not an advisory one.
- **Root cause:** both mechanisms anchor to *which module the code lives in*, not to its text or
  behaviour. A verbatim code-motion refactor is invisible to behavioural tests — return values are
  byte-identical — and is precisely what location-keyed mechanisms are blind to.
- **Fix:** for (1) the **test** was fixed, not the production code. This repo already had the precedent
  three functions further down the same file: T-41 moved code out of `cli/main.py`, and those tests
  were updated to patch `hachi.gateway.in_process_runtime`'s binding. Both gardener tests now patch
  `hachi.gateway.session_read_models.get_hachi_paths`, same manual assign/restore style, assertions
  byte-identical, only the module object changed. An earlier draft instead had production code import
  `get_hachi_paths` **through** `orchestrator.py` (itself only a re-export of `core.paths`) via a
  deferred import, so the stale patch target would keep resolving — rejected. For (2), two
  `session_read_models.py` entries were added to the allowlist, and `orchestrator.py:_last_user_query`
  — whose last call site had just moved out — was deleted in the same change.
- **Avoid:** when you *relocate* code rather than rewrite it, grep the repo for the OLD module's dotted
  path combined with each moved symbol before calling the move "verbatim". Then fix it in both
  directions. Point the test or lint reference at the **new** location — never make new code reach
  backward through the old module to keep a stale patch target alive, which is a booby trap for the
  next reader who deletes the "pointless" indirection. And withdraw the grant at the **old** location:
  when a move takes the last flagged call site out of a file, the leftover allowlist entry is a silent
  pre-authorisation for that attribute to reappear there unnoticed. Same "the list only ever shrinks"
  discipline this repo already applies to the import-linter contracts.
- **Locked by:** `tests/test_memory_gardener_wiring.py::test_snapshot_gardener_diffs_no_longer_masked_by_getattr`
  and `::test_get_memory_diffs_route_returns_real_gardener_output_end_to_end`;
  `tests/test_check_getattr.py::test_src_tree_has_no_unallowlisted_violations` (35 allowlist entries:
  2 added, 1 removed as stale).

### BB-159 — Community pack installs bypassed the entire consent and integrity gate

- **Symptom:** `hachi skill pack install user/repo` fetched an archive from the internet and copied
  `.og` files into `<skills_dir>/packs/<name>/` with **no consent sheet, no confirmation prompt, no
  declared scopes and no content hashes** — while `hachi ogpack install`, writing to the *same*
  directory, required all four.
- **Root cause:** two independently-grown installers. `core/og_pack.py` (`pack.toml`) implemented
  scopes, `render_consent_sheet`, `typer.confirm(default=False)` and `verify_pack_checksums`;
  `core/community_packs.py` (`pack.json`) implemented none of them. Both resolved `skills_dir` the same
  way, both wrote to `skills_dir/"packs"`, and `SkillEngine.load_directory` globs `**/*.og` — so
  consented and unconsented skills loaded identically.
- **Fix:** `community_packs` demoted to a fetcher (`fetch_pack_to_dir`, writes nothing under
  `skills_dir`) plus a manifest synthesiser (`synthesise_pack_manifest`) that declares zero scopes and
  computes `sha256`s over the files actually fetched. `pack_install` now runs fetch → synthesise →
  `write_pack_toml` → render sheet → confirm-or-exit → `install_ogpack_from_dir`. The confirm-or-exit
  logic became one shared `confirm_pack_consent_or_exit` in `cli/commands/pack.py`, which
  `ogpack_install`, `ogpack_update` and the community path all call — previously two inline copies.
- **Avoid:** when two code paths write to the same destination, they must share the same gate. A
  security control guarding one of N paths to a shared resource is decoration. Assert on the **outcome
  on disk** after declining, not on whether a prompt function was called.
- **Locked by:** `tests/test_community_pack_consent.py` — `test_community_pack_install_refuses_without_consent`
  plus 7 more in the same file.

### BB-160 — `.installed.toml` hand-built with f-strings: a Windows path corrupts it and the pack silently vanishes from `pack list`

- **Symptom:** a pack installs successfully — files on disk, `verify_pack_checksums` passes — but
  `hachi pack list` / `list_ogpacks` does not show it. Reproduces on Windows for any locally-sourced
  pack, community or `.ogpack`, independent of the T-56 work that found it.
- **Root cause:** `og_pack._write_installed_meta` built `.installed.toml` with `f'source = "{source}"'`
  instead of a TOML serialiser. `source` is a filesystem path; on Windows it is backslash-separated,
  and TOML basic strings treat `\` as an escape introducer, so a real Windows path routinely produces
  invalid TOML. `_read_installed_meta`'s bare `except Exception: return {}` then swallows the
  `TOMLDecodeError`, turning "corrupt on write" into "this record never existed".
- **Fix:** `_write_installed_meta` now builds the document with `tomlkit` — already a declared
  dependency used by four other modules, and the same pattern as `config_loader.py`.
- **Avoid:** never hand-build TOML or JSON by interpolating platform-derived strings, paths above all;
  use the format's real serialiser. And a bare `except Exception: return {}` around a parse deserves a
  second look — it converts a write-side bug into a silently missing record, which is far harder to
  find than a crash.
- **Locked by:** `tests/test_community_pack_consent.py::test_community_pack_install_is_visible_to_ogpack_list`.

### BB-161 — The consent sheet silently dropped the scope id it was asking the user to approve

- **Symptom:** `hachi pack install` / `hachi pack update` rendered a consent sheet showing a scope's
  prose description ("Read Gmail messages and labels") but never its id. The terminal printed
  `+    Read Gmail messages and labels` where `+  [gmail.read]  Read Gmail messages and labels` had
  been written.
- **Root cause:** `render_consent_sheet` / `render_scope_diff` correctly embed scope ids in literal
  brackets. `console.print(sheet)` is a Rich `Console.print`, which parses `[...]` as a markup tag by
  default and **silently drops** an unrecognised one — there is no `gmail.read` style — rather than
  echoing it or erroring.
- **Fix:** `markup=False` at the two print call sites in `cli/commands/pack.py`. Both build plain text
  with no intentional Rich styling (verified: the renderers emit no style tags at all), so disabling
  markup there is unconditionally correct. `og_pack.py`'s renderers were not touched — they were
  already right.
- **Note on scope:** pre-existing, and not introduced by T-56. It moved into scope because T-56 made
  that same print the gate for community packs, which previously had no sheet at all — the bug did not
  change, its blast radius did. A consent screen that drops the identifier while keeping only the prose
  is a consent-integrity defect, not a display nit: the id is the precise thing being granted.
- **Avoid:** any programmatically built string passed to `Console.print()` needs `markup=False` (or
  `rich.markup.escape()`) unless it was deliberately authored with Rich tags. A silently dropped tag
  looks like working code in every test that asserts on the **returned** string — test what the user
  actually sees rendered.
- **Locked by:** `tests/test_community_pack_consent.py` — `test_pack_install_consent_sheet_shows_scope_id_not_just_description`,
  `test_pack_update_scope_diff_shows_scope_id_not_just_description`.

### BB-162 — A migration that picks its winner by key name instead of by data destroys the fresher credential

- **Symptom:** caught in review, before it shipped. T-57 half B's one-time sweep of the legacy
  `oauth.<p>` vault key onto the canonical `oauth2.<p>` key made the legacy copy authoritative
  **whenever both keys existed**, then deleted it after copying. In the reachable case where the user
  reconnects Google in the web UI while both keys exist, that copies a stale token over the one the
  user just consented to and deletes the evidence — unrecoverable.
- **Root cause:** the precedence rule reasoned from half A's resolver, which preferred `oauth.<p>`, and
  concluded the legacy copy is always the live one. True for a grant that forked *before* half A. False
  the moment `oauth_callback` runs: it calls `OAuthTokenStore.save()` **directly**, consulting no
  resolver, so canonical can just as easily be the fresher copy. The spec's own rule ("migrate only if
  canonical does not exist") avoided the destruction but had the mirror-image failure — it would hand
  the agent a stale token whenever the legacy copy was the live one.
- **Fix:** decide on the data, not the key name. `_safe_expires_at` (missing or unparseable → `0`),
  canonical wins only when strictly greater, ties go to legacy. The winner is written to the canonical
  key and **read back and compared** before the legacy key is deleted — credential data is never
  deleted without a verified copy elsewhere.
- **Avoid:** when a migration must choose between two copies of the same record, choose by a property
  *of the records* — a timestamp, a version — not by which name they are stored under. A name-based
  rule encodes an assumption about history that the next code path to write that key will violate. And
  a migration over credentials is one-way: verify the copy landed before deleting the original.
- **Locked by:** `tests/test_oauth.py` — `test_migrate_legacy_oauth_key_canonical_wins_when_newer`,
  `test_migrate_legacy_oauth_key_legacy_wins_when_newer`, `test_migrate_legacy_oauth_key_ties_go_to_legacy`.

### BB-163 — `chat.ts`'s live-session reset was hand-duplicated in four functions

- **Symptom:** no confirmed user-visible failure — this is the mechanism, caught before it fired. A
  fifth "start a fresh session" path, or an edit applied to three of the four existing ones, would
  leave a stale `_cursor` and replay the previous session's SSE events into the new session
  (cross-session transcript bleed), with none of the responsible state visible in the store or in
  devtools.
- **Root cause:** `frontend/src/lib/data/domains/chat.ts` kept 17 module-level mutables as a runtime
  kernel, and the ~10-line reset sequence was copied verbatim into four functions
  (`loadConversationHistory`'s "no sessions left" guard, `switchSessionLive`, `deleteSessionLive`,
  `newSessionLive`). Three copies were byte-identical; the fourth differed only in setting a target
  session id instead of null.
- **Fix:** extracted `sessionRef` / `streamBuffers` / `costMeter` — three store-free state objects with
  their own `reset()` — into `frontend/src/lib/data/domains/chat/`, plus one `resetLiveSession(nextSessionId)`
  that all four sites call. Each site keeps its own `hydrate(...)` call: the kernel never touches store
  state. `getKernelSnapshot()` makes the kernel inspectable for the first time. All 16 pre-existing
  exports keep their names and signatures; `store.ts` was not touched.
- **Avoid:** duplicated reset logic is a latent state bug with a scheduled arrival date. Once more than
  two call sites reset the same set of variables, that set is an object and the reset is its method —
  do it before the third copy, not after the fourth.
- **Locked by:** `frontend/src/lib/data/domains/chat.test.ts` — "every session-reset path leaves
  identical kernel state".

### BB-164 — A constructor that resolves environment state makes a "pure" library un-embeddable, and import-linter cannot see it

- **Symptom:** `SkillEngine()` raised in any process without a Hachi installation. The constructor
  called `get_hachi_paths()` before any `.og` file had been named. Tests could not isolate usage state
  without monkeypatching platform paths, and `get_stale_skills` / `get_usage_report` were untestable
  without a real home directory.
- **Root cause:** persistent usage tracking was added straight onto the class that owns parsing and
  retrieval, and its file path was resolved in `__init__` rather than injected. The class passed
  import-linter — `og_engine → core` is the one permitted direction — so the coupling was invisible to
  the architectural rail. It was a *constructor* dependency on environment state, not an import-layer
  violation.
- **Fix:** a defaulted keyword-only `usage_path: Path | None = None`, so all 20 `src/` and 62 `tests/`
  construction sites were untouched, and `get_hachi_paths()` is called only when it is `None`. Usage
  tracking and embedding rank were extracted to `og_engine/usage.py` and `og_engine/embedding.py`, with
  `SkillEngine`'s methods kept as thin delegations. `UsageTracker` still loads the file eagerly at
  construction — deliberately, to preserve pre-T-73 behaviour; what changed is where the path comes
  from, not when the read happens.
- **Avoid:** a constructor must not resolve environment state. Inject the path with a default so no
  call site changes, and let composition roots decide. import-linter cannot see this class of coupling:
  if a "pure" library cannot be constructed with its path resolver patched to raise, it is not pure.
  Related trap: `SkillEngine.with_role()` builds views via `__new__` + `__dict__.update`, so extracted
  collaborators are shared by reference — back-compat attribute access (`engine._usage`,
  `engine._usage_path`, `engine._emb_collection`, all poked directly by pre-existing tests) must be a
  **property** reading through the shared collaborator, not a plain attribute snapshotted at
  `with_role()` time, or a view stops seeing state recorded on the engine after it was created.
- **Locked by:** `tests/test_og_engine.py` — `test_engine_constructs_without_a_hachi_home`,
  `test_injected_usage_path_is_used_verbatim`, `test_with_role_view_shares_the_usage_tracker`.

### BB-165 — `boot_agent`'s numbered step comments misreported execution order

- **Symptom:** `boot_agent` carried steps numbered 0–10 plus 6b, 8b and 5.5. Step 5.5
  (system-prompt assembly) sat ~285 lines *below* step 8b (tool registration) and executed
  eleventh, not sixth. Two neighbouring comments were scar tissue from earlier reorderings
  ("prompt assembled after tool registration below", "Moved up so Vault can bind to it") that
  the numbers contradicted. Anyone decomposing the function by reading the numbers would build
  a stage graph that is wrong at exactly the point where it matters most.
- **Root cause:** blocks were reordered over time to satisfy real dependencies; the numbers were
  never updated. The comments recorded the *original intent* while the code recorded the *actual
  graph*, and nothing could detect the divergence — a comment cannot be executed.
- **Fix:** the 1,245-line function became 15 named stages under `src/hachi/gateway/boot/`, each
  taking typed inputs and returning a frozen dataclass, ordered by the dependency graph derived
  empirically rather than read off the comments. `build_prompt` now takes `register_tools`'s
  result as a parameter, so the downstream edge is a **type signature** instead of a coincidence
  of file position. The numbering is gone.
- **Avoid:** never encode execution order in a comment that a later edit can silently invalidate.
  If an ordering is load-bearing, express it as a data dependency in a signature — then moving
  the code in the wrong order is a type error rather than a stale comment. The same defect can
  hide in user-facing strings: this boot sequence still emits `"Building system prompt"` at
  progress step 7 of 11, four steps before the prompt is actually built (open, not fixed here).
- **Locked by:** `tests/test_boot_agent_stages.py` —
  `test_boot_emits_events_in_a_stable_order`, `test_prompt_reflects_registered_tool_count`,
  `test_booted_session_has_the_expected_wiring`

### BB-166 — Ten wall-clock tests forced the whole suite sequential; nothing enforced it

- **Symptom:** `uv run pytest -n auto` failed on `test_two_sessions_run_concurrently` and
  `test_parallel_execution_is_faster_than_sequential`, so the gate ran 4,600+ tests
  sequentially (~520 s) with nothing in the tooling stating that as policy.
- **Root cause:** eight tests asserted on `time.monotonic()` deltas (tightest: `elapsed < 0.05`)
  and two compared cross-thread timestamps. Under xdist, workers steal the CPU those assertions
  assume. Worse, sequential execution was enforced **by omission**: preflight simply never passed
  `-n`, while `pyproject.toml` shipped `pytest-xdist`, kept `--dist=loadfile` in `addopts`, and
  its own comment instructed readers to "Pass `-n <N>` or `-n auto` on the CLI" — the exact thing
  the preflight docstring forbade. A contributor following the config's own instructions broke
  the gate.
- **Fix:** pinned `-n 0` explicitly first (note `-p no:xdist` does **not** work — it disables the
  plugin and makes `--dist=loadfile` an unrecognized argument), then replaced every duration
  assertion with a `threading.Event` handoff, a `threading.Barrier`, or a background thread with
  a generous deadman-switch join; the two ordering tests became explicit handshakes over recorded
  markers rather than timestamps. **No production file needed a clock seam** — see Avoid. Then
  flipped preflight, CI and the `pyproject.toml` comment to `-n auto` (~120 s, ≈4×).
- **Avoid:** never assert wall-clock duration to prove "did not block" — assert the slow
  collaborator was never awaited (an Event the test controls), or force genuine concurrency with
  a `Barrier` so a serial implementation deadlocks instead of merely running slow. Reach for
  clock injection only when the code under test polls `time.monotonic()` itself; if its timeout
  is delegated to a stdlib blocking primitive (`Thread.join(timeout=…)`,
  `Condition.wait_for(timeout=…)`) there is no clock to swap and you should prove the *outcome*
  instead. When you must keep a real time assertion, prefer a **lower** bound: starvation only
  ever makes things slower, so `elapsed >= configured` is parallel-safe where `elapsed <` is not.
  And when a gate depends on a mode, name the mode in the command — a rule enforced by omission
  is not enforced.
- **Locked by:** `tests/test_test_suite_hygiene.py::test_no_new_wall_clock_duration_assertions`;
  CI's `test` job runs `-n auto` on all three platforms

### BB-167 — `hydrate` widened its own type and could overwrite store actions

- **Symptom:** any of ~20 domain loaders could write any of the frontend store's 75 state fields
  in one call, and a patch key colliding with an action name would replace that action's
  *function* with arbitrary data.
- **Root cause:** `frontend/src/lib/store/store.ts` was one flat 1,847-line store — 75 fields,
  100 actions, 22 unrelated concerns, no ownership boundaries. `hydrate` was **declared**
  `(patch: Partial<State>) => void` but **implemented** as `set(patch as Partial<State & Actions>)`.
  The cast existed only to satisfy zustand's setter and silently discarded the guarantee the
  signature made. State it precisely: not "untyped" — *typed, then widened, and unscoped*.
- **Fix:** split into typed slices under `lib/store/slices/`, one owner per concern, with
  per-domain hydrate functions and both casts removed. `useStore`'s public shape is unchanged and
  **no component file was touched**. The field *set* — not merely its count — was diffed before
  and after to prove nothing was dropped or duplicated.
- **Avoid:** a cast that widens a parameter past its declared type is a silent contract break —
  if a library's setter needs a wider type than your API promises, narrow at the boundary rather
  than widening the promise. And never refactor a store that four untested 1,200+-line screens
  depend on without landing render smoke tests **first**: the split is mechanical, but the failure
  mode is a blank screen that typechecks.
- **Locked by:** `frontend/src/lib/store/store.test.ts` — `hydrate cannot overwrite an action`;
  `frontend/src/routes/{integrations,work,memory,og}.test.tsx` (render smoke tests)

### BB-168 — Tests keyed to a code *location* break when the code moves — in three different ways

- **Symptom:** three separate refactors in this pack each broke a test that was not asserting
  anything wrong, only asserting it *at an address that had changed*: a monkeypatch target
  (BB-158), an AST-structural test anchored to an enclosing function (`test_routing_boot_failure.py`,
  which walks `boot_agent`'s source for a `try/except` shape), and an import-graph test anchored
  to a literal edge (`module-graph.test.ts`, pinning `store.ts → data/domains/chat`).
- **Root cause:** each keys on *where* code lives rather than *what it does*. File-level ownership
  analysis cannot see them: none of the three tests lives in the file being edited, and two reach
  into a function's source text, so grepping for imports of the moved symbol does not find them
  either.
- **Fix:** re-anchor to the new location; never weaken the assertion. `test_routing_boot_failure.py`
  moved its anchor from `boot_agent` to `boot.providers.build_routing` with **zero** assertion
  changes. `module-graph.test.ts` was re-pointed at the real, now-longer chain
  (`store.ts → slices/chat.ts → data/domains/chat.ts → store.ts`) — the cycle it guards never went
  away, it gained a hop.
- **Avoid:** before moving a function, grep the **whole** test suite for `ast.parse` /
  `inspect.getsource` against its name, and for monkeypatch targets naming its module — a
  file-ownership list will not contain them. Two such tests in this pack were missed by exactly
  that omission and were found only because the implementer distrusted the list. When one does
  break, the question is always "did the fact change, or only the address?" — if only the address,
  fix the anchor. Deleting or inverting the assertion destroys the guard: `module-graph.test.ts`
  exists *because* a comment once claimed there was no cycle while a cycle existed, so relaxing it
  to "no cycle" would have restored the original defect.
- **Locked by:** `tests/test_routing_boot_failure.py` (5 tests, unchanged assertions);
  `frontend/src/lib/module-graph.test.ts` (both tests in the `module graph` block)

<!--
When you fix a bug: add BB-N here with a one-line **Avoid:**, link the locking test,
and (if the constraint is subtle) drop a comment next to the code itself — the agent
gets far better attention on a comment beside the function than on a doc three
directories away.
-->

### BB-169 — `.og` files were a plaintext credential source for the legacy IntegrationRuntime

- **Symptom:** a `role=env` node's `::dense` block could carry `GMAIL_APP_PASSWORD=…` in cleartext.
  The boot path loads **every** `.og` under the skills dir, so any such node became a live
  credential and was handed to `smtplib.login()`. Setup failures went to `print()`, corrupting the
  Textual render and escaping the logging config entirely.
- **Root cause:** `core/integrations.py` predates the credential vault and never caught up.
  `load_env_node` parsed `KEY=VALUE` into `self._env: dict[str, str]`, `get_credential` returned it
  raw, and `_setup_smtp` consumed it directly — `grep "vault\|secret_store\|redact"` on the module
  returned **nothing**, while its sibling `UniversalRestAdapter`, in the same layer, already
  resolved secrets from `CredentialVault` at request time and redacted outbound params. Same repo,
  two eras, both live.
- **Fix:** `role=env` is no longer a credential source — `load_env_node` stores nothing and emits
  one `logger.warning` naming the file and the credential **key names** (never values) plus the
  `hachi secrets --set <key>` command to migrate each. SMTP credentials resolve through
  `CredentialVault.get_token(..., prompt_if_missing=False)`. `_setup_http` was deleted rather than
  ported: `UniversalRestAdapter` already covers declarative HTTP with a vault, an SSRF allowlist and
  outbound redaction. All five `print()` sites became logger calls.
- **Avoid:** when a newer subsystem in the same layer already solves a cross-cutting concern
  (secrets, redaction, logging), an older sibling solving it differently is a **defect, not a style
  difference** — grep the old module for the new module's safety primitives before assuming parity.
  Second, and more transferable: when the dependency a fix needs is constructed several stages
  *after* the code that needs it, prefer moving the **dependency** earlier over moving the
  **consumer** later. Moving the consumer changes observable ordering (here it would have moved a
  `boot.progress` event that `tests/test_boot_agent_stages.py` pins); moving a cheap dependency
  usually does not. **Verify "cheap" rather than asserting it** — `create_default_secret_store()`
  looks pure but calls `ensure_directories()`, which writes to disk. It is safe to hoist here for
  two specific reasons that had to be checked: the call is `mkdir(parents=True, exist_ok=True)`
  (idempotent), and `load_config()` already invoked it four stages earlier. "Side-effect-free"
  would have been the wrong justification for a correct move.
- **Locked by:** `tests/test_integration_runtime_credentials.py` —
  `test_role_env_node_is_not_a_credential_source`, `test_integration_runtime_emits_no_stdout`

### BB-170 — A test's outer timeout exactly matched its inner one, so load killed the wrong clock

- **Symptom:** the gate's `frontend vitest` step was green in isolation (262 passed / 33 files,
  ~23–25 s) but failed inside the full preflight, where the same step took ~55 s and the four route
  smoke tests died with the blunt `Error: Test timed out in 5000ms.` The failing subset varied run
  to run, which is what made it read as three flaky tests rather than one defect in four.
- **Root cause:** each test set a deliberately generous **inner** `findByRole(..., {timeout: 5000})`
  for a cold React + TanStack Router + loader mount — and vitest's **outer** per-test `testTimeout`
  was left at its default, which is **also 5000 ms**. The two numbers read as equal but are not:
  the outer clock starts at test entry, before `render()`; the inner clock starts later, at the
  `findByRole` call. So the outer budget has **zero headroom** over the budget it is supposed to
  bound, and under load it always wins the race — killing the test before the generous inner wait
  could resolve or fail informatively.
- **Fix:** an explicit per-test budget, `it("renders without crashing", async () => {…}, 15000)`,
  on all four files — 3× both the default and the inner wait. Scoped to the four call sites rather
  than a global `test.testTimeout`, which would have relaxed the hang-detector for the other ~258
  tests that do no such mount. No assertion, no inner timeout and no production source changed:
  the entire functional diff is `});` → `}, 15000);`, four times.
- **Avoid:** when a test configures an explicit `waitFor`/`findBy*` timeout, **check it against the
  runner's own default outer timeout.** If the inner value is equal to or greater than the outer
  one, the outer wins under any load and the inner generosity is silently inert. Give the outer
  real headroom rather than a matching number. Related process lesson: a flake that only appears
  under load will not reproduce in the isolated run you use to verify the fix — reproduce it
  *under real load first* (here: concurrently with `pytest -n auto`), or you are testing nothing.
  And do not trust the observed failing subset to delimit the bug: three of four tests failed in the
  gate, but all four failed under heavier load — the fourth's survival was timing luck, not a
  structural difference.
- **Locked by:** `frontend/src/routes/{memory,og,work,integrations}.test.tsx` — explicit 15000 ms
  `it(...)` budgets; reproduced red under concurrent `pytest -n auto` load pre-fix, green after
