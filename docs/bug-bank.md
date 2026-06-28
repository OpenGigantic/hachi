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
- **Avoid:** on Windows, fall back to `python -m pytest` / `python -m mypy`, or
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

<!--
When you fix a bug: add BB-N here with a one-line **Avoid:**, link the locking test,
and (if the constraint is subtle) drop a comment next to the code itself — the agent
gets far better attention on a comment beside the function than on a doc three
directories away.
-->
