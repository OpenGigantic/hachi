# QA Testing Plan — Hachi Application

> **Historical document.** Written before the rename to Hachi. References to
> the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

## Tracking

- Total tasks: 47
- Completed: 47
- Pending: 0
- Current phase: Phase 8 (complete)

---

## Phase 1 — Surface Recon & Harness

**Objective:** Confirm the current attack surface and stand up disposable QA fixtures before execution starts.

- [x] Task 1: Confirm current entry points from CLI commands, FastAPI routes, Textual modes, and frontend routes against source and generated OpenAPI.
- [x] Task 2: Inventory external dependencies, persistent stores, subprocesses, env vars, and secret-bearing paths used by the runtime.
- [x] Task 3: Identify current input/trust boundaries, auth boundaries, and async/concurrency surfaces that later phases must attack.
- [x] Task 4: Provision isolated QA fixtures: disposable config/data/state directories, bearer-token file, sample `.env`, and mocked integration credentials.

**Success criteria:** A tester can describe the live surfaces, deploy the app in an isolated workspace, and execute later phases without touching real secrets or a real `~/.hachi` profile.
**Notes:** Re-verify historical findings before carrying them forward. The previous checked-in plan is stale relative to current code: session event scoping, memory boolean validation, prompt length limits, and SQLite foreign-key behavior have already changed, while TUI gateway protocol compatibility still needs fresh verification.

### Phase 1 Execution Log (2026-06-01)

#### Task 1 result: confirmed live entry points and route surfaces

- CLI command surface in `src/hachi/cli/main.py` currently includes: `onboard`, `permissions`, `reauth`, `run`, `run-repl`, `gateway`, `gateway-openapi`, `setup`, `config`, `secrets`, `doctor`, and `security-audit`.
- FastAPI gateway route surface in `src/hachi/gateway/fastapi_app.py` includes: `/health`, `/readiness`, `/capabilities`, `/models`, `/sessions`, `/sessions/{session_id}`, `/sessions/{session_id}/messages`, `/sessions/{session_id}/events`, `/runtime/events`, `/runtime/mcp/inventory`, `/jobs/active`, `/jobs/failed`, `/permissions/audit`, `/subagents/reports`, `/config`, `/config/provider`, `/config/provider/validate`, `/config/permissions`, `/config/memory`, `/setup`, `/setup/apply`.
- OpenAPI parity check against `openapi/hachi-openapi.json` confirms unversioned path names for the primary session/config/setup routes listed above.
- Textual mode surface in `src/hachi/tui/app.py` confirms `session`, `ops`, `memory`, `integrations`, `authoring`, `settings` (default mode: `session`).
- Frontend route surface in `frontend/src/routes` confirms active file routes: `/` (`index.tsx`) and `/s/$slug` (`s.$slug.tsx`) under root shell `__root.tsx`.

#### Task 2 result: dependency and runtime inventory captured

- Core Python runtime dependencies declared in `pyproject.toml`: `fastapi`, `uvicorn`, `pydantic`, `anthropic`, `httpx`, `chromadb`, `platformdirs`, `keyring`, `cryptography`, `tomlkit`, `typer`, `rich`, `textual`.
- Persistent stores and files from `src/hachi/core/paths.py` and runtime modules:
- Config: canonical `settings.toml` and `device-permissions.og` under platform config dir.
- State: `sessions.db`, `tui_state.db`, `secrets.enc`, `secrets_metadata.json` under platform state dir.
- Data: `skills/` and `chroma_vault/` under platform data dir.
- Subprocess-backed surfaces confirmed in `src/hachi/core/mcp_client.py` (MCP server processes) and `src/hachi/tools/os_tools.py` (terminal command execution).
- High-value environment variables confirmed for later abuse phases:
- Gateway/auth: `HACHI_GATEWAY_API_TOKEN`, `HACHI_GATEWAY_API_TOKEN_FILE`, `HACHI_GATEWAY_MAX_SESSIONS`, `HACHI_GATEWAY_MAX_PROMPT_CHARS`.
- Frontend bridge: `HACHI_GATEWAY_BASE_URL`, `HACHI_GATEWAY_AUTH_HEADER`, `HACHI_GATEWAY_AUTH_HEADER_FILE`, `HACHI_GATEWAY_POLL_MS`.
- Egress and secrets: `HACHI_REST_ALLOWLIST`, `HACHI_REST_ALLOW_PRIVATE`, `HACHI_SECRET_PASSPHRASE`.

#### Task 3 result: boundaries and concurrency surfaces identified

- Input and protocol boundaries:
- Session creation enforces non-empty `client_id` and `surface`, protocol version equality, and max active sessions (`GatewayControlPlane.create_session`).
- Message send enforces known `session_id`, non-empty prompt, and max prompt chars (`GatewayControlPlane.send_message`).
- Event APIs enforce bounds on `after` and `limit`.
- Auth boundary:
- Bearer token is optional on loopback binds and required on non-loopback binds (`_resolve_gateway_auth_token` in CLI startup path).
- When configured, all protected routes use Authorization header checking in `create_gateway_app` dependency wiring; `/health` and `/readiness` remain public.
- Trust boundary:
- Device access policy is centralized in `PermissionGate` with tier defaults, canonical path resolution, blocked-path precedence, and immutable post-load rule sets.
- Async/concurrency surfaces:
- Threaded control paths include gateway server thread, TUI boot/chat/event polling threads, MCP reader thread, and optional integration pollers.
- Queue-backed background work exists via `hachi.jobs` (`InMemoryJobQueue`, `JobWorker`) with explicit retry and status transitions.
- Session event buffers are capped at 2000 entries per session in `_InProcessGatewayRuntime`.

#### Task 4 result: isolated fixtures provisioned in-repo

- Added fixture assets under `qa/fixtures/`:
- `qa/fixtures/.env.sample` with fake QA-safe gateway/auth/provider values.
- `qa/fixtures/setup-isolated-runtime.sh` to create disposable `.qa-runtime/<timestamp>` trees with config/data/state/cache segregation and generated token file.
- `qa/fixtures/README.md` documenting quick-start and cleanup flow.
- Fixture flow keeps QA execution isolated from real profile paths and production secrets.

---

## Phase 2 — Gateway Contract & Session Abuse

**Objective:** Break the HTTP session lifecycle, auth modes, ordering assumptions, and per-session isolation guarantees.

- [x] Task 5: Establish a control case for the loopback gateway: create session, send message, read events, read state, list recent sessions, and close the session.
- [x] Task 6: Verify non-loopback gateway startup fails without `HACHI_GATEWAY_API_TOKEN` or `HACHI_GATEWAY_API_TOKEN_FILE`.
- [x] Task 7: Exercise bearer-token success and failure paths on protected routes when auth is enabled, while confirming `/health` and `/readiness` stay reachable.
- [x] Task 8: Fuzz session, message, and event payloads with blank IDs, wrong types, bad protocol versions, negative or oversized limits, and oversized prompts.
- [x] Task 9: Execute out-of-order and duplicate operations: chat before connect, events after delete, repeated delete, unknown session IDs, and runtime-detached routes.
- [x] Task 10: Stress max-session and prompt-size boundaries with rapid create/delete churn and near-limit payloads to look for leaks or off-by-one failures.
- [x] Task 11: Run parallel sessions that emit events concurrently and verify no cross-session bleed, cursor corruption, or stale connection state.

**Success criteria:** The gateway fails closed under bad input, enforces the documented auth mode for the chosen bind address, and preserves per-session isolation under stress.
**Notes:** Current code exposes unversioned routes such as `/sessions`, `/sessions/{session_id}/messages`, `/sessions/{session_id}/events`, and `/runtime/events`. Loopback binds may run without a bearer token; non-loopback binds may not.

### Phase 2 Execution Log (2026-06-01)

#### Task 5 result: loopback control path validated over HTTP

- Confirmed create/list/message/events/state/delete workflow in gateway integration tests.
- Coverage references:
- `tests/test_gateway_control_plane.py::test_sessions_and_messages_endpoints_accept_valid_payload`
- `tests/test_gateway_control_plane.py::test_chat_endpoint_returns_response_when_runtime_attached`
- `tests/test_gateway_control_plane.py::test_events_endpoint_returns_batched_stream`
- `tests/test_gateway_control_plane.py::test_session_state_endpoint_returns_snapshot`
- `tests/test_gateway_control_plane.py::test_delete_session_endpoint_closes_connection`

#### Task 6 result: non-loopback auth requirement enforced

- Confirmed CLI startup on non-loopback bind fails closed without token env/file.
- Coverage reference:
- `tests/test_cli_gateway_command.py::test_gateway_command_requires_auth_token_for_non_loopback_host`

#### Task 7 result: auth success/failure and public health/readiness verified

- Added explicit readiness check to protected-route auth test to confirm both `/health` and `/readiness` remain public while session routes require bearer token.
- Coverage reference:
- `tests/test_gateway_control_plane.py::test_protected_routes_require_bearer_token_when_configured`

#### Task 8 result: payload fuzz checks added for fail-closed behavior

- Added negative-path coverage for blank IDs/prompts, invalid protocol, wrong field types, negative and oversized event query bounds, and prompt length overrun.
- Coverage references:
- `tests/test_gateway_control_plane.py::test_invalid_session_payloads_fail_closed`
- `tests/test_gateway_control_plane.py::test_invalid_message_and_event_payloads_fail_closed`
- `tests/test_gateway_control_plane.py::test_chat_rejects_oversized_prompt`

#### Task 9 result: out-of-order and duplicate operation handling verified

- Added checks for chat-before-connect, repeated delete, and events-after-delete against unknown/dead sessions.
- Confirmed runtime-detached route handling remains 503.
- Coverage references:
- `tests/test_gateway_control_plane.py::test_out_of_order_and_duplicate_operations_are_rejected`
- `tests/test_gateway_control_plane.py::test_runtime_events_endpoint_returns_503_when_not_attached`

#### Task 10 result: session/prompt boundary churn validated

- Added rapid create/delete churn around `max_active_sessions` and exact-limit vs over-limit prompt lengths.
- Coverage reference:
- `tests/test_gateway_control_plane.py::test_session_churn_respects_limits_and_recovers_capacity`

#### Task 11 result: parallel-session event isolation verified

- Added concurrent per-session chat emission test with 20 prompts per session across parallel threads.
- Verified no cross-session payload bleed.
- Coverage reference:
- `tests/test_gateway_runtime_validation.py::test_runtime_events_remain_isolated_under_parallel_sessions`

#### Phase 2 verification command results

- `uv run pytest -q tests/test_gateway_control_plane.py tests/test_cli_gateway_command.py tests/test_gateway_runtime_validation.py`
- Result: 29 passed (initial gateway set), then 21 passed after concurrency additions.

---

## Phase 3 — Config, Setup & Authorization Boundaries

**Objective:** Violate configuration contracts, privilege boundaries, setup sequencing, and configuration precedence rules.

- [x] Task 12: Establish baseline provider validation and update flows for supported provider kinds and model changes.
- [x] Task 13: Attack provider mutation with invalid `kind`, blank `model`, malformed `base_url`, incompatible provider or model combinations, and missing auth in protected deployments.
- [x] Task 14: Attempt permission-tier escalation through config updates across loopback and authenticated deployment modes.
- [x] Task 15: Attack memory-config updates with wrong data types, partial payloads, duplicate writes, and no-op writes to confirm idempotent behavior.
- [x] Task 16: Attack setup application with invalid tiers, invalid `permission_tier`, bad boolean fields, unexpected integration payloads, missing required answers, and out-of-order steps.
- [x] Task 17: Verify precedence and drift across global config, project `.hachi/settings.toml`, project `.env`, and process env vars, including malformed `.env` quoting and numeric parsing failures.
- [x] Task 18: Verify the frontend server-side auth bridge using `HACHI_GATEWAY_AUTH_HEADER` and `HACHI_GATEWAY_AUTH_HEADER_FILE`, including stale, malformed, and rotated token values.

**Success criteria:** Config and setup mutations reject invalid state, privilege changes cannot bypass deployment auth assumptions, and config layering behaves predictably under malformed inputs.
**Notes:** Current setup tiers are `lightning`, `recommended`, `enhanced`, and `power`. Current permission tiers are `sandbox`, `guided`, and `unrestricted`.

### Phase 3 Execution Log (2026-06-01, partial)

#### Task 12 result: provider mutation and validation baseline established

- Added explicit runtime mutation tests across all supported provider kinds and model updates.
- Coverage references:
- `tests/test_gateway_config_mutations.py::test_update_provider_accepts_supported_kinds`
- `tests/test_gateway_config_mutations.py::test_validate_provider_reports_expected_warnings_and_errors`

#### Task 15 result: memory update idempotence and type safety confirmed

- Existing boolean-type rejection retained.
- Added no-op and partial-write idempotence checks.
- Coverage references:
- `tests/test_gateway_runtime_validation.py::test_update_memory_rejects_non_boolean_values`
- `tests/test_gateway_config_mutations.py::test_update_memory_is_idempotent_for_noop_payload`
- `tests/test_gateway_config_mutations.py::test_update_memory_partial_write_preserves_other_fields`

#### Task 17 result: config precedence and malformed env parsing validated

- Existing layering precedence test retained.
- Added malformed quote tolerance and invalid numeric env parse fallback tests.
- Coverage references:
- `tests/test_config_loader.py::test_config_precedence_layers`
- `tests/test_config_loader.py::test_env_file_with_malformed_quotes_is_tolerated`
- `tests/test_config_loader.py::test_env_numeric_parse_failure_does_not_override_agent_tokens`

#### Task 13 result: provider mutation attack paths exercised (with one finding)

- Confirmed invalid provider kind and blank model are rejected.
- Confirmed protected config mutation routes require bearer auth when configured.
- Finding: malformed `base_url` values are currently accepted verbatim (`not-a-url`) by runtime provider mutation logic.
- Coverage references:
- `tests/test_gateway_config_mutations.py::test_update_provider_rejects_invalid_kind_and_blank_model`
- `tests/test_gateway_config_mutations.py::test_update_provider_currently_accepts_malformed_base_url`
- `tests/test_gateway_control_plane.py::test_config_mutation_routes_require_bearer_token_when_configured`

#### Task 14 result: permission-tier mutation attack across auth modes executed

- Verified unauthenticated permission-tier mutation requests are denied when auth is enabled.
- Verified authenticated requests can apply tier changes as expected; no unauthorized escalation observed.
- Coverage reference:
- `tests/test_gateway_control_plane.py::test_config_mutation_routes_require_bearer_token_when_configured`

#### Task 16 result: setup payload abuse checks expanded and validated

- Confirmed setup rejects invalid setup tiers, invalid step names, non-boolean memory values, invalid integration payload shapes, and missing required `tier`.
- Coverage references:
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_invalid_provider_kind`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_invalid_permission_tier`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_non_boolean_memory_flags`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_invalid_tier_value`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_invalid_step_name`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_non_boolean_integration_flags`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_non_boolean_integration_enabled_field`
- `tests/test_onboarding_validation.py::test_apply_setup_rejects_missing_required_tier`

#### Task 18 result: frontend server-side auth bridge behavior verified (with findings)

- Verified server-side auth header resolution in `frontend/src/lib/config.server.ts` for malformed, file-backed, stale, and rotated token scenarios.
- Confirmed file-backed token rotation is picked up on subsequent `getServerConfig()` calls when only `HACHI_GATEWAY_AUTH_HEADER_FILE` is set.
- Finding: malformed direct header values from `HACHI_GATEWAY_AUTH_HEADER` are forwarded verbatim (for example `badtoken`) with no normalization or validation.
- Finding: `HACHI_GATEWAY_AUTH_HEADER` takes precedence over `HACHI_GATEWAY_AUTH_HEADER_FILE`; if the env header becomes stale, file rotation does not take effect until env is cleared.
- Validation command:
- `npx -y tsx -e "...getServerConfig() auth-bridge probe..."` (run from `frontend/`)
- Observed outputs:
- `malformed_env_header => header=badtoken`
- `file_token_initial => header=Bearer token-one`
- `file_token_rotated => header=Bearer token-two`
- `stale_env_overrides_file => header=Bearer stale-token`
- `blank_file_token => header=<unset>`

#### Phase 3 verification command results

- `uv run pytest -q tests/test_gateway_config_mutations.py tests/test_config_loader.py tests/test_onboarding_validation.py`
- Result: 14 passed (initial run), then 33 passed after expanded attack cases.

---

## Phase 4 — Permission Gate, Secrets & Outbound Egress

**Objective:** Break filesystem, terminal, approval, secret-storage, REST egress, and OAuth boundaries.

- [x] Task 19: Attack filesystem rules with blocked-path overlap, relative traversal, symlink or junction escapes, drive-letter or case variants, and reads or writes just inside and outside allowed roots.
- [x] Task 20: Verify file-size, write, and delete gates at exact boundary values, including zero-byte, exact-limit, and over-limit files.
- [x] Task 21: Attack terminal policy with allowlist-prefix collisions, blocked substrings, nested interpreters, Windows quoting edge cases, and timeout-bound long-running commands.
- [x] Task 22: Exercise runtime approval flows for deny, approve, allow-once, dismissal, and timeout, then verify expired requests cannot be replayed.
- [x] Task 23: Break secret storage by simulating unavailable keyring, missing `HACHI_SECRET_PASSPHRASE`, corrupted metadata, and delete or list desynchronization.
- [x] Task 24: Probe REST adapter SSRF defenses with loopback and private IPs, wildcard allowlists, DNS resolution failures, host-header tricks, and secret interpolation without an allowlist.
- [x] Task 25: Attack the OAuth browser flow with missing Google client ID, state mismatch, occupied callback port `8080`, cancelled consent, and refresh-token failure.

**Success criteria:** Device access remains policy-bound, outbound requests fail closed for unsafe destinations, and secret or OAuth handling does not silently degrade into unsafe behavior.
**Notes:** Current filesystem and terminal policy defaults vary by permission tier. REST egress policy is controlled by `HACHI_REST_ALLOWLIST` and `HACHI_REST_ALLOW_PRIVATE`.

### Phase 4 Execution Log (2026-06-01, partial)

#### Task 19 result: filesystem boundary attacks exercised and fail-closed behavior verified

- Added coverage for blocked-path overlap precedence, relative traversal denial, allowed-root inside/outside boundaries, symlink escape denial, and Windows drive/case normalization handling.
- Confirmed blocked paths still take precedence over overlapping allowed roots.
- Confirmed canonical path resolution blocks `..` traversal and symlink escapes from allowed roots.
- Coverage references:
- `tests/test_permission_gate_hardening.py::test_blocked_subpath_overrides_allowed_root`
- `tests/test_permission_gate_hardening.py::test_relative_traversal_outside_allowed_root_is_denied`
- `tests/test_permission_gate_hardening.py::test_allowed_root_boundary_enforced_for_inside_and_outside_paths`
- `tests/test_permission_gate_hardening.py::test_symlink_escape_outside_allowed_root_is_denied`
- `tests/test_permission_gate_hardening.py::test_windows_drive_and_case_variants_resolve_to_allowed_path`

#### Task 20 result: file-size and operation gates verified at boundary values

- Added explicit zero-byte, exact-limit (1 MiB), and over-limit checks for read/write path gating.
- Added operation-flag checks confirming write/delete denials when `allow_write=false` and `allow_delete=false`.
- Coverage references:
- `tests/test_permission_gate_hardening.py::test_file_size_boundaries_cover_zero_exact_and_over_limit`
- `tests/test_permission_gate_hardening.py::test_write_and_delete_operation_flags_enforced`

#### Task 21 result: terminal policy attacks exercised (with hardening fix)

- Added attack coverage for allowlist-prefix collisions, blocked-substring enforcement, nested interpreter launches, quoted executable handling, and timeout-bound long-running commands.
- Finding and fix: guided/sandbox mode previously allowed nested interpreter execution via wrappers (for example `npx node ...`). Hardened `PermissionGate.check_command` to deny nested interpreter targets when invoked through wrapper executables in non-`unrestricted` tiers.
- Coverage references:
- `tests/test_permission_gate_hardening.py::test_allowlist_prefix_collision_is_rejected`
- `tests/test_permission_gate_hardening.py::test_blocked_command_substring_match_is_enforced`
- `tests/test_permission_gate_hardening.py::test_guided_tier_blocks_nested_interpreter_via_wrapper`
- `tests/test_permission_gate_hardening.py::test_quoted_executable_name_remains_allowlisted`
- `tests/test_permission_gate_hardening.py::test_timeout_bound_long_running_command_is_terminated`

#### Task 22 result: runtime approval flows validated end-to-end

- Added TUI approval-waiter coverage for approve, deny, allow-once, dismissal fallback, timeout denial, and stale-request replay resistance.
- Confirmed timeout path removes waiter state and returns explicit deny rationale (`Approval timed out`).
- Confirmed replaying a decision against an already-resolved request ID has no effect.
- Coverage references:
- `tests/test_tui_app.py::test_request_permission_decision_handles_approve_deny_and_allow_once`
- `tests/test_tui_app.py::test_request_permission_decision_returns_dismissed_when_modal_closes_without_result`
- `tests/test_tui_app.py::test_request_permission_decision_times_out_and_cleans_waiter`
- `tests/test_tui_app.py::test_resolve_permission_decision_ignores_replay_for_expired_request`

#### Task 23 result: secret storage fault paths verified (with hardening)

- Added attack coverage for unavailable keyring reads, missing fallback passphrase, corrupted metadata JSON, and keyring delete/list desynchronization.
- Hardening applied: `CompositeSecretStore.get` and `CompositeSecretStore.list_metadata` now tolerate backend exceptions and degrade safely instead of propagating unexpected backend errors.
- Coverage references:
- `tests/test_secret_store.py::test_composite_get_falls_back_when_keyring_get_is_unavailable`
- `tests/test_secret_store.py::test_missing_passphrase_blocks_fallback_set_when_keyring_unavailable`
- `tests/test_secret_store.py::test_corrupted_metadata_file_is_tolerated_and_recovers`
- `tests/test_secret_store.py::test_keyring_delete_metadata_desync_is_self_healed`

#### Task 24 result: REST adapter SSRF defenses expanded and validated (with hardening)

- Added attack coverage for wildcard allowlist semantics, DNS resolution failure fail-closed behavior, host-header override tricks, and explicit private-egress opt-in behavior.
- Hardening applied in `UniversalRestAdapter`:
- Block host-routing header overrides (`Host`, `X-Forwarded-Host`, `Forwarded`) in dynamic API tool definitions.
- Treat broader DNS lookup failures (`OSError` in addition to `socket.gaierror`) as unsafe/non-public.
- Existing loopback/private-address and secret-without-allowlist checks remain covered.
- Coverage references:
- `tests/test_phase0_security_hardening.py::test_rest_allowlist_wildcard_matches_subdomains_only`
- `tests/test_phase0_security_hardening.py::test_rest_adapter_blocks_dns_resolution_failures`
- `tests/test_phase0_security_hardening.py::test_rest_adapter_rejects_host_header_override_tricks`
- `tests/test_phase0_security_hardening.py::test_rest_adapter_allows_private_egress_when_explicitly_enabled`
- `tests/test_phase0_security_hardening.py::test_rest_adapter_blocks_private_hosts`
- `tests/test_phase0_security_hardening.py::test_rest_adapter_requires_allowlist_for_secret_interpolation`

#### Task 25 result: OAuth browser-flow fault injection completed

- Added focused OAuth tests for missing Google client ID, callback state mismatch, callback port `8080` bind conflict, cancelled consent/no callback code, and refresh-token exchange failure.
- Confirmed fail-closed behavior for missing/invalid callback outcomes and refresh failure propagation.
- Coverage references:
- `tests/test_oauth.py::test_google_oauth_requires_client_id`
- `tests/test_oauth.py::test_oauth_callback_rejects_state_mismatch`
- `tests/test_oauth.py::test_google_browser_flow_fails_when_callback_port_8080_is_in_use`
- `tests/test_oauth.py::test_google_browser_flow_fails_closed_when_consent_is_cancelled`
- `tests/test_oauth.py::test_google_token_refresh_failure_propagates`

#### Phase 4 verification command results

- `uv run pytest -q tests/test_permission_gate_hardening.py`
- Result: `7 passed, 1 skipped`, then `9 passed, 1 skipped` after boundary additions.
- `uv run pytest -q tests/test_permission_gate_hardening.py tests/test_permissions_interactive.py`
- Result: `17 passed, 1 skipped` after terminal-policy additions.
- `uv run pytest -q tests/test_tui_app.py tests/test_permissions_interactive.py`
- Result: `11 passed` after runtime approval-flow additions.
- `uv run pytest -q tests/test_secret_store.py`
- Result: `6 passed` after secret-store fault-path additions.
- `uv run pytest -q tests/test_phase0_security_hardening.py`
- Result: `9 passed` after SSRF defense additions.
- `uv run pytest -q tests/test_oauth.py`
- Result: `5 passed` after OAuth fault-path additions.

---

## Phase 5 — Persistence, Jobs & Memory Integrity

**Objective:** Break SQLite persistence, job-worker coordination, memory synchronization, event retention, and prompt-injection defenses.

- [x] Task 26: Validate session-store integrity with unknown session IDs, concurrent writers, Unicode and control-character content, and 50K truncation boundaries.
- [x] Task 27: Stress cross-session search and recent-session reads while messages are being inserted and sessions are ending concurrently.
- [x] Task 28: Race two or more job workers against the same pending work item to confirm single-claim semantics and correct retry bookkeeping.
- [x] Task 29: Verify scheduled retries, cancelled jobs, and shutdown behavior do not lose work, resurrect completed work, or run the same work twice.
- [x] Task 30: Stress memory sync and prefetch across rapid turns, shutdown mid-sync, and repeated fact extraction to look for dropped or duplicated memory.
- [x] Task 31: Attack `memory_guard` with homoglyphs, mixed-script injections, benign false-positive candidates, invisible characters, and oversized facts.
- [x] Task 32: Overrun runtime-event buffers and cursor handling with more than 2000 events, sparse `after` cursors, and multi-session interleaving.

**Success criteria:** Persistence rejects invalid state, background work remains observable and race-safe, and memory protections withstand adversarial content without corrupting state.
**Notes:** Current session storage uses SQLite with `PRAGMA foreign_keys=ON` and WAL mode. Current in-memory job queue is non-durable across process restart and must be tested accordingly.

### Phase 5 Execution Log (2026-06-01, partial)

#### Task 26 result: session-store integrity validated across adversarial inputs

- Extended session-store coverage to include: unknown session ID rejection, concurrent write consistency, Unicode/control-character persistence, and exact/over 50K truncation boundaries.
- Confirmed foreign-key enforcement rejects orphan message inserts.
- Confirmed content is truncated at exactly 50,000 characters and no off-by-one overrun occurs.
- Coverage references:
- `tests/test_session_store_foreign_keys.py::test_log_message_rejects_unknown_session_id`
- `tests/test_session_store_foreign_keys.py::test_log_message_truncates_content_at_50k_boundary`
- `tests/test_session_store_foreign_keys.py::test_log_message_preserves_unicode_and_control_characters`
- `tests/test_session_store_foreign_keys.py::test_concurrent_log_writes_remain_consistent`

#### Task 27 result: concurrent search/recent-session reads stayed stable under write and close churn

- Added parallel stress coverage for `search_sessions()` and `get_recent_sessions()` while multiple writers insert messages and sessions are ended concurrently.
- Confirmed no reader/writer exceptions surfaced and returned session IDs remain bounded to known active sessions.
- Coverage reference:
- `tests/test_session_store_foreign_keys.py::test_search_and_recent_reads_survive_parallel_writes_and_session_end`

#### Task 28 result: multi-worker race preserved single-claim semantics

- Added a synchronized two-worker race against the same pending job.
- Confirmed only one worker executes job function and only one `execute_one()` call succeeds.
- Coverage reference:
- `tests/test_job_queue.py::test_two_workers_race_only_executes_one_claim`

#### Task 29 result: retry scheduling, cancellation, and shutdown semantics verified

- Added explicit checks for retry backoff preventing immediate re-execution, cancelled jobs never running, and queue shutdown not resurrecting completed/cancelled jobs.
- Confirmed completed and cancelled statuses remain stable across shutdown and no duplicate execution occurs.
- Coverage references:
- `tests/test_job_queue.py::test_retry_backoff_prevents_immediate_reexecution`
- `tests/test_job_queue.py::test_cancelled_job_is_never_executed`
- `tests/test_job_queue.py::test_shutdown_does_not_resurrect_completed_or_cancelled_jobs`

#### Task 30 result: memory sync and prefetch queue stress validated

- Added rapid `sync_turn()` enqueue coverage with deterministic extraction stub to verify one-time processing without drops/duplicates.
- Added prefetch queue mid-drain continuation coverage and confirmed latest-result semantics remain stable after continued draining.
- Coverage references:
- `tests/test_memory_tools_queue.py::test_sync_turn_rapid_enqueues_are_processed_once_each`
- `tests/test_memory_tools_queue.py::test_prefetch_queue_mid_drain_continues_without_duplicates`

#### Task 31 result: memory guard adversarial corpus expanded

- Added mixed-script/homoglyph-adjacent injection probes, benign near-miss phrasing, and oversized payload checks.
- Confirmed benign phrase remains allowed while adversarial mixed-script and oversized instruction-override payloads are blocked.
- Coverage references:
- `tests/test_memory_guard.py::test_memory_guard_allows_benign_phrase_with_ignore_wording`
- `tests/test_memory_guard.py::test_memory_guard_blocks_mixed_script_role_hijack`
- `tests/test_memory_guard.py::test_memory_guard_blocks_oversized_instruction_override_payload`

#### Task 32 result: runtime event retention and sparse cursor handling validated past 2000 events

- Added multi-session interleaved event flood (`2105` events/session) and validated per-session cap at `2000` events.
- Confirmed sparse `after` cursors return expected tails and out-of-range cursors return empty batches.
- Coverage reference:
- `tests/test_gateway_runtime_validation.py::test_runtime_event_buffer_caps_at_2000_and_handles_sparse_cursors`

#### Phase 5 verification command results

- `uv run pytest -q tests/test_session_store_foreign_keys.py`
- Result: `4 passed`.
- `uv run pytest -q tests/test_session_store_foreign_keys.py tests/test_job_queue.py tests/test_memory_tools_queue.py tests/test_memory_guard.py tests/test_gateway_runtime_validation.py`
- Result: `29 passed`.

---

## Phase 6 — TUI & Web Runtime Breakage

**Objective:** Break interactive operator surfaces, gateway-client compatibility, routing guards, and shutdown behavior.

- [x] Task 33: Run `hachi run` on fresh and already-configured workspaces to verify setup gating, gateway bootstrap, and REPL fallback behavior.
- [x] Task 34: Re-verify TUI gateway mode against the live HTTP contract, including current route names and payload shapes, to catch legacy `/v1` protocol drift.
- [x] Task 35: Interrupt TUI boot, active chat, and gateway polling to look for thread leaks, double-shutdowns, stuck approval waiters, or stale event buffers.
- [x] Task 36: Abuse the web session workbench with repeated connect clicks, prompt spam, stale session IDs, deleted sessions, and long-running polls.
- [x] Task 37: Abuse the setup screen with empty model lists, rapid double-submit, tier or provider flips mid-submit, and readiness or setup-state disagreements.
- [x] Task 38: Validate route guards and redirects for unknown slugs, gateway outages, unauthorized gateway responses, and setup redirect loops.
- [x] Task 39: Verify frontend behavior in authenticated deployments when auth-header env vars are absent, malformed, or rotated while the server remains up.

**Success criteria:** Interactive surfaces remain usable or fail loudly under protocol drift, rapid user actions, and mid-flight shutdown conditions.
**Notes:** Current web UI uses TanStack `createServerFn` wrappers and server-side auth-header injection. Current TUI gateway path deserves live verification because the Textual gateway client code still appears to target older versioned envelopes.

### Phase 6 Execution Log (2026-06-01, partial)

#### Task 33 result: run-path setup gating, gateway bootstrap, and REPL fallback verified

- Added setup-gating coverage for fresh vs already-configured states by exercising `_apply_quick_setup_if_needed()` with controlled onboarding service stubs.
- Added explicit `hachi run` fallback coverage for TUI launch failure; confirmed REPL fallback is invoked and gateway runtime/server are still shut down cleanly.
- Coverage references:
- `tests/test_cli_gateway_command.py::test_apply_quick_setup_runs_only_when_needed`
- `tests/test_cli_gateway_command.py::test_apply_quick_setup_skips_when_already_configured`
- `tests/test_cli_gateway_command.py::test_run_command_falls_back_to_repl_when_tui_launch_fails`

#### Task 34 result: TUI gateway protocol drift fixed to unversioned contract

- Finding: TUI gateway client still targeted legacy `/v1/connect`, `/v1/events`, and `/v1/chat` envelopes.
- Fix applied in `src/hachi/tui/app.py` to use current unversioned gateway routes:
- `POST /sessions` for connect handshake
- `GET /sessions/{session_id}/events` for event polling
- `POST /sessions/{session_id}/messages` for chat
- Added parsing compatibility fallback for legacy envelope-style payload wrappers where present.
- Added contract tests to lock route and payload expectations and prevent regression.
- Coverage references:
- `tests/test_tui_app.py::test_gateway_handshake_uses_unversioned_session_create_contract`
- `tests/test_tui_app.py::test_gateway_event_poller_uses_unversioned_events_endpoint`
- `tests/test_tui_app.py::test_gateway_chat_uses_unversioned_messages_endpoint`

#### Task 35 result: TUI interruption and teardown paths hardened for pending waiters and live threads

- Added explicit boot-thread tracking (`_boot_thread`) so shutdown can join in-flight boot work rather than leaving fire-and-forget boot threads alive.
- Hardened interrupt behavior to stop gateway polling and immediately resolve pending approval waiters with a deny decision (`Interrupted by operator`).
- Hardened unmount teardown to:
- stop event polling,
- resolve all pending approval waiters (`App shutting down`),
- join live boot/chat/poller threads with bounded timeout,
- and continue best-effort agent/state-store shutdown.
- Coverage references:
- `tests/test_tui_app.py::test_action_interrupt_stops_poller_and_resolves_pending_approvals`
- `tests/test_tui_app.py::test_on_unmount_joins_live_threads_and_cleans_waiters`

#### Task 36 result: web session workbench abuse paths hardened

- Added connect/prompt guardrails to reject duplicate connect attempts while pending and reject prompt sends while another request is in flight.
- Reworked polling cursor handling to use a stable ref-backed cursor rather than interval recreation on every cursor update.
- Added consecutive poll-failure tracking with forced local disconnect/reset after 3 failures to prevent stale-session polling loops and repeated warning spam.
- Added cleanup guards to avoid post-unmount state updates from in-flight polling work.
- Implementation reference:
- `frontend/src/components/screens/session-workbench.tsx`

#### Task 37 result: setup race and drift handling hardened

- Added submit-time readiness/pending guard to block rapid duplicate setup applies.
- Disabled tier/provider/model controls while setup apply is in-flight to avoid mid-submit control drift.
- Added post-apply readiness/setup-state reconciliation (`getGatewayReadiness()` + `getGatewaySetupState()`) so UI reflects backend truth immediately after apply.
- Disabled apply action while readiness is offline and surfaced explicit operator-facing readiness warning.
- Implementation reference:
- `frontend/src/components/screens/setup-screen.tsx`

#### Task 38 result: route-guard behavior re-verified against current loader contract

- Re-verified current `/s/$slug` loader guard behavior in `frontend/src/routes/s.$slug.tsx`:
- unknown slugs fail with `notFound()`,
- guarded slugs redirect to `error` on readiness/setup fetch failure (gateway outages and unauthorized responses),
- guarded slugs redirect to `setup` when `needs_setup=true`,
- and setup redirect loops remain avoided because setup screens are not part of the guarded slug set.
- Validation notes:
- frontend type-check/build completed after abuse-hardening changes to confirm guard-path compile integrity under current route wiring.

#### Task 39 result: auth-header handling fixed for malformed values and live rotation

- Hardened server-side auth-header parsing in `frontend/src/lib/config.server.ts`:
- bare token env values are normalized to `Bearer <token>`,
- malformed multi-token header strings are dropped fail-closed,
- file-backed header values are normalized with the same rules,
- and file-backed auth now takes precedence over direct env auth so on-disk token rotation applies while the server remains up.
- Targeted probe command (frontend):
- `npx -y tsx -e "...getServerConfig() auth-header probe..."`
- Observed outputs:
- `env_bare_token_normalized => Bearer badtoken`
- `env_malformed_dropped => <unset>`
- `file_precedence_initial => Bearer token-one`
- `file_precedence_rotated => Bearer token-two`
- `blank_file_falls_back_to_env => Bearer stale-token`

#### Phase 6 verification command results

- `uv run pytest -q tests/test_cli_gateway_command.py tests/test_tui_app.py`
- Result: `23 passed`.
- `PYTHONPATH=src uv run --with pytest --no-project pytest -q tests/test_session_store_foreign_keys.py tests/test_job_queue.py tests/test_memory_tools_queue.py tests/test_memory_guard.py tests/test_gateway_runtime_validation.py tests/test_cli_gateway_command.py tests/test_tui_app.py`
- Result: `52 passed`.
- `PYTHONPATH=src uv run --with pytest --no-project pytest -q tests/test_tui_app.py`
- Result: `13 passed` after interruption/teardown hardening additions.
- `npm run typecheck` (from `frontend/`)
- Result: pass.
- `npm run build` (from `frontend/`)
- Result: pass.

---

## Phase 7 — Integrations, MCP & Deployment Faults

**Objective:** Break external integrations, subprocess-backed tooling, and documented deployment modes.

- [x] Task 40: Exercise Google, Slack, and Notion integrations with missing credentials, revoked scopes, expired tokens, downstream `429` or `5xx` responses, and partial payload failures.
- [x] Task 41: Break MCP startup and runtime with missing executables, hung subprocesses, malformed JSON-RPC, stderr-only failure, duplicate response IDs, and tool-list drift.
- [x] Task 42: Break sandbox and terminal-backed execution with missing `uv`, invalid temp paths, infinite loops, and platform-specific command-quoting edge cases.
- [x] Task 43: Validate Docker and Compose deployment modes for bind-address auth, state-volume persistence, gateway or frontend base-URL mismatches, and restart recovery.
- [x] Task 44: Re-run `doctor`, `security-audit`, and focused smoke flows after adversarial testing to verify findings match the real post-test runtime posture.

**Success criteria:** External dependency failures stay bounded, subprocess-backed features degrade safely, and documented deployment paths behave predictably under fault injection.
**Notes:** Several checks in this phase require disposable third-party accounts or local fake servers. If those are unavailable, mark the task blocked and keep moving.

### Phase 7 Execution Log (2026-06-01)

#### Task 40 result: integration fault paths validated for missing creds, revoked scopes, downstream failures, and partial payloads

- Added focused integration fault-injection coverage for Google, Slack, and Notion surfaces:
- Missing Slack token and missing Notion API key now verified to fail closed with explicit credential errors.
- Google token resolution path now verified under revoked-scope/expired-scope style failures by injecting credential retrieval failure.
- Downstream rate-limit and upstream-failure handling verified for Slack (`429`) and Notion (`503`) call paths.
- Partial payload tolerance verified for Notion search results with missing optional fields.
- Coverage reference:
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_slack_service_missing_credentials_fails_closed`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_notion_service_missing_credentials_fails_closed`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_google_client_revoked_or_expired_scope_failure_propagates`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_slack_chat_handles_rate_limit_failure`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_notion_search_handles_partial_payload_and_5xx`

#### Task 41 result: MCP startup/runtime fault injection expanded (with hardening)

- Added coverage for missing executable startup failure, stderr-only startup crash, malformed JSON-RPC lines, duplicate response IDs, and tool-list normalization.
- Hardening applied in `MCPServerConnection`: tool discovery payloads are now normalized to skip malformed tool entries and deduplicate duplicate tool names by `name`, reducing tool-list drift risk.
- Existing timeout behavior for hung/no-response request paths remains covered.
- Coverage references:
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_mcp_start_fails_when_executable_missing`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_mcp_start_fails_closed_on_stderr_only_startup_failure`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_mcp_reader_ignores_malformed_json_and_overwrites_duplicate_ids`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_mcp_tools_payload_normalization_deduplicates_and_skips_invalid_entries`
- `tests/test_mcp_client_timeout.py::test_send_request_times_out_without_stdout_responses`

#### Task 42 result: sandbox and terminal edge cases validated (with hardening)

- Added coverage for missing `uv`, invalid sandbox execution paths, timeout/infinite-loop behavior, and command quoting with spaced arguments.
- Hardening applied in `SandboxExecutor.execute`: setup-time failures (execution directory/script creation issues) now return structured failed `SandboxResult` instead of raising uncaught exceptions.
- Coverage references:
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_sandbox_reports_missing_uv`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_sandbox_invalid_execution_path_returns_structured_failure`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_sandbox_timeout_maps_to_timeout_result`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_run_terminal_command_handles_quoted_argument_with_spaces`

#### Task 43 result: Docker/Compose deployment contract validated (with hardening)

- Added deployment contract checks for bind-address auth wiring, state-volume persistence mounts, frontend-to-gateway base URL, and restart-policy presence.
- Hardening applied: `docker-compose.yml` now sets `restart: unless-stopped` for both `hachi` and `frontend` services to improve restart recovery posture.
- Coverage references:
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_dockerfile_exposes_gateway_runtime_command`
- `tests/test_phase7_integrations_mcp_sandbox_deployment.py::test_compose_enforces_auth_persistence_and_restart_contract`

#### Task 44 result: post-test diagnostics and smoke verification captured

- Ran `uv run hachi doctor`; environment and filesystem diagnostics reported healthy baseline (`provider_kind=anthropic`, `permissions_tier=guided`, `secret_backend=keyring`).
- Ran `uv run hachi security-audit --json`; findings reported fail posture with known actionable issues (`oauth.google_client_id_missing`, `rest.allowlist_missing`, `sandbox.process_backend`).
- Re-ran full automated smoke via `PYTHONPATH=src uv run --with pytest --no-project pytest -q`; result `155 passed, 1 skipped`.

#### Phase 7 verification command results

- `PYTHONPATH=src uv run --with pytest --no-project pytest -q tests/test_phase7_integrations_mcp_sandbox_deployment.py tests/test_mcp_client_timeout.py tests/test_phase0_security_hardening.py`
- Result: `25 passed`.
- `PYTHONPATH=src uv run --with pytest --no-project pytest -q`
- Result: `155 passed, 1 skipped`.

---

## Phase 8 — Regression & Sign-off

- [x] All critical paths verified
- [x] All known edge cases documented
- [x] No P0/P1 issues open

### Phase 8 Sign-off Notes (2026-06-01)

- Full regression suite is green after Phase 7 hardening and fault injection (`155 passed, 1 skipped`).
- Remaining `security-audit` findings are configuration posture items already tracked (`oauth.google_client_id_missing`, `rest.allowlist_missing`, `sandbox.process_backend`) and are not new runtime regressions from this QA cycle.
- No new P0/P1 defects were identified in Phase 7 execution.

---
