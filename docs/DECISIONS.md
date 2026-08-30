# DECISIONS.md

Engineering decision log for the 12-issue hardening pass on `feat/phase4-platform-impl`.
Each entry: **Found** (root cause / current behaviour) → **Decided/Changed** (approach + files) →
**Why** (rationale). Significant architectural decisions are flagged **[ADR]**.

Methodology: 8 parallel read-only audits (one per issue cluster) established root causes, then 8
file-disjoint implementation lanes applied fixes with explicit cross-file contracts. Audits and
fixes were delegated to Sonnet 4.6 subagents; orchestration, decisions, and verification by Opus.
"Minimal-safe" means: smallest correct fix now, with deeper refactors captured as `# TODO(issueN)`.

---

## ⚠️ Operational findings (require human action — NOT code-fixable)

1. **Live secrets in `.env`.** `D:\OpenGigantic\hachi\.env` holds real, plaintext production
   credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `GOOGLE_CLIENT_SECRET`, `NOTION_API_KEY`, Discord/Slack tokens, STT/TTS keys). The file is
   gitignored (verified) so it is not in git history, but these keys should be **rotated** because
   they have been stored in cleartext. We did not modify `.env` (it is the user's live secret store).

2. **Stale `.env` comment.** The header comment in `.env` claims "the app does NOT auto-load this
   file." That is now false — `cli/main.py::_load_dotenv_into_environ()` loads it at import time.
   Documented in `docs/guides/configuration.md`; the user should update/remove the misleading comment.

---

## Issue 1 — "Unknown session id" on page refresh

**Found.** The session id lives in two unbridged stores. `POST /sessions` registers a UUID in the
in-memory `GatewayControlPlane._connections` dict; `SessionStore.start_session()` separately writes
a (different-format) id to SQLite. On gateway restart `_connections` is wiped. Client-side,
`_liveSessionId` is a **module-level variable** (`frontend/.../chat.ts:27`) with no persistence — it
resets to `null` on browser refresh, and the zustand store does not persist `sessions`. The visible
error originates in `control_plane.py`: `session_state()` (~547) and `send_message()` (~341) gate on
`_connections` only and raise `GatewayProtocolError("Unknown session_id")` for a session from a prior
process. The events endpoint already tolerates dead sessions (falls back to SQLite history); only
`session_state`/`send_message` do not.

**Decided/Changed.**
- Backend (`control_plane.py`): `session_state()` falls back to SQLite-backed metadata instead of
  raising for a known-but-dead session. `send_message()` raises a **typed, recoverable** signal
  (`session_expired`) instead of a generic "Unknown session_id". `# TODO(issue1)` left for full
  server-side session re-boot-on-resume.
- Frontend (`chat.ts`): persist `_liveSessionId` to `sessionStorage` (tab-scoped) and rehydrate on
  load so a same-tab refresh reconnects. On `session_expired` → silently create a new session + show
  a friendly inline notice. On unrecognized/corrupted id → silently reset and create new, **no
  error surfaced**.

**Why.** [ADR] We **distinguish expired (expected) from corrupted (bug)** as the spec requires:
expired is a normal lifecycle event → friendly, non-alarming UX; a missing/garbled id is our bug →
recover silently rather than blame the user. Making `session_state` SQLite-tolerant mirrors the
already-correct events endpoint, so the fix is consistent with existing patterns and low-risk. Full
transparent server resume is deferred because it needs careful re-wiring of the orchestrator boot
path — the minimal fix already removes the user-visible failure.

---

## Issue 2 — Slow/delayed markdown & code-block rendering (FOUC)

**Found.** Not a parser or highlighter problem (the markdown component is a zero-dep synchronous
regex tokenizer; no lazy `import()`, no shiki/prism). Root cause is **(c) streaming buffering + (d)
remount**: `conversation.tsx` (~929) renders `<StreamingText>` (raw `white-space:pre-wrap`, zero
markdown) during the stream, then switches to `<Markdown>` only when streaming completes. That
switch **unmounts/remounts** the whole message subtree, so code blocks go from plain text to fully
styled in one repaint — the flash — plus a layout shift (inline spans → block `<pre>`/`<div>`).

**Decided/Changed.** Render `<Markdown text={msg.text} isStreaming={showCursor}/>` **always** (drop the
StreamingText branch for hachi messages). New `isStreaming` prop: keep synchronous per-flush parsing
(cheap; the existing `FLUSH_MIN_INTERVAL_MS=48` throttle bounds cadence and `useMemo(parseBlocks,
[text])` bounds re-parse), but for an **unclosed** fenced code block render the styled `<pre>`
container with un-highlighted text so chrome appears the instant ``` arrives. Stable block keys
across streaming→done = prop change, not remount. `min-height` on the message body prevents the
start/end collapse-expand shift.

**Why.** The original `StreamingText` optimization avoided O(size²) re-renders, but `React.memo` on
`MessageItem` already ensures only the last message re-renders per flush — so the perf win no longer
justifies the FOUC. Parsing during streaming is the correct trade now. Keeping the same component
across the transition is what actually kills the flash (no remount) and the layout shift.

---

## Issue 3 — Voice: speed, accuracy, reliability

**Found.** Latency is dominated by code-fixable issues, not the providers:
- **TTS waits for the full blob** before playback (`speakText` does `await res.blob()`); server
  returns `StreamingResponse(io.BytesIO(full_bytes))` — buffered, not streamed. ~1–2.5 s of silence.
- **STT batches** the full utterance (Whisper has no token streaming) — provider-limited.
- **VAD hangover** `SILENCE_HANGOVER_FRAMES=40` ≈ 667 ms tail on every utterance.
- **`httpx.Client` rebuilt per call** → TLS handshake each time (~100–300 ms).
- **ElevenLabs model hardcoded** `eleven_monolingual_v1` (not the low-latency turbo/flash model).
- **Mic stream + AudioContext rebuilt every utterance** (~150–200 ms + permission churn).

**Decided/Changed.** P1 stream TTS playback via `MediaSource`/`ReadableStream` (blob fallback) + server
streams httpx body where supported. P2 reuse MediaStream/AudioContext across utterances (rebuild only
the MediaRecorder). P3 hangover 40→25 + pre-speech gate (don't upload leading silence). P4 shared
lazy `httpx.Client` + 60 s timeout. P6 `config.voice.elevenlabs_model` (default `eleven_turbo_v2_5`)
+ configurable stt/tts model & base_url. Plus voice cost tracking (see Issue 9).

**Why.** [ADR] **Fix what's in our control; document what isn't.** Streaming TTS playback is the single
biggest perceived-latency win and is fully ours. STT streaming is genuinely provider-limited (Whisper
is batch-only) — documented in `docs/` with a mitigation path (Groq `whisper-large-v3-turbo` via
`stt_base_url`) rather than faked. VAD stays an energy detector (a WASM VAD like Silero is noted as a
follow-up) because retuning the threshold/hangover is low-risk and recovers most of the latency now.

---

## Issue 4 — MCP servers not appearing after registration

**Found.** Two compounding gaps. (1) Boot (`orchestrator._configure_mcp_servers`, ~1475) reads MCP
servers **only from `.og` `role=mcp` nodes**, never from the `[mcp_servers]` TOML that the
registration API writes — so an API-registered server is lost on restart and absent if no session is
live to hot-load it. (2) `MCPManager.get_inventory()` never populates an error field, so the UI has
no per-server `error`/`loading` state (only binary ready/stopped).

**Decided/Changed.** `_configure_mcp_servers` also loads `[mcp_servers]` TOML entries
(`MCPServerConfig.from_toml_entry`) at boot, de-duped against `.og`-defined servers. `get_inventory`
gains `err` (from a new `_last_error` on the connection) and a transient `loading`/`starting` status.
Frontend renders connected/loading/error chips and shows the error text.

**Why.** Registration already persisted to TOML correctly; the bug was purely that the boot path never
read it back — so the smallest correct fix is to make boot authoritative over both sources. Surfacing
`err` closes the "registered but silently dead" UX hole the spec calls out.

---

## Issue 5 — Tavily ignored; everything routes to Playwright

**Found.** `ProviderRegistry.from_config()` only instantiates `TavilySearchProvider` when
`config.search.provider == "tavily"` (default `None`). Storing the Tavily API key via the
integrations UI writes the key to the vault but **never sets `search.provider`** — so the provider is
never registered, `web_search`/`extract_url` tools are never added, and the LLM only ever has
`browse_web` (Playwright). There is no "routing" between them; the LLM simply uses the only web tool
present.

**Decided/Changed.** (1) `registry.from_config()` registers Tavily when `provider=="tavily"` **OR** a
Tavily key is resolvable (vault `tavily_api_key` or env `TAVILY_API_KEY`) — **key-presence is
sufficient**. (2) The credential-submit route also persists `[search] provider="tavily"` so it
survives restarts. (3) `_register_search_if_available` logs a clear warning when no provider is found
and `browse_web`/Playwright becomes the fallback; a `fallback_active` status field drives a UI notice.

**Why.** [ADR] **Configured-credential implies intent.** A user who pastes a Tavily key obviously wants
Tavily; requiring a separate hidden `provider` toggle was the trap. We make the key the activation
signal and make the Playwright fallback **explicit and visible** (log + UI) rather than silent, as the
spec demands.

---

## Issue 6 — `.env` credentials ignored; "Needs setup" shown wrongly

**Found (6a).** OAuth2 connectors compute "configured" via `is_configured()` which reads **only** the
canonical `HACHI_OAUTH_<NAME>_CLIENT_ID` env name. A user's `.env` with the bare `GOOGLE_CLIENT_ID`
(what the legacy Google path uses) therefore reads as unconfigured. apikey connectors (Telegram/
WhatsApp/Tavily) already check vault-or-env and work. `.env` *is* loaded (via
`cli/main.py::_load_dotenv_into_environ`), contradicting the stale `.env` comment.

**Decided/Changed.**
- **6a:** connector resolution checks the canonical name **and** documented legacy aliases (e.g.
  `GOOGLE_CLIENT_ID`/`_SECRET`); `connector_status()` now reports `source: "vault"|"env"|null` so the
  UI can show "(from environment)". Explicit env→connector→state mapping documented in
  `connector_registry.py`.
- **6b:** the existing generic `CredentialDialog` (masked fields) gains a per-integration **Test
  connection** button (`POST /integrations/{id}/credentials/test`), a "currently configured" masked
  hint, and an env-sourced tag. Secrets are never put in localStorage (verified: only UI-dismissal
  flags are).
- **6c:** CLI commands `hachi set K=v [K2=v2 ...]`, `hachi get K` (masked), `hachi list-keys`
  (status: vault/env/not-set), `hachi unset K`, validated against a `KNOWN_CREDENTIAL_KEYS` schema
  (warn on unknown). Backed by the existing `CredentialVault`. *(wave 2 — in `cli/main.py`)*
- **6d:** repo-wide scan found **zero `TOKOBOROS_` in tracked code/docs/CI/Docker**; the only
  occurrences are in the user's local `.env` (`STT_API_KEY`, `TTS_API_KEY`, 5× `SMTP_*`). So the
  rename is a no-op in code; we add a **startup back-compat shim** that copies any set `TOKOBOROS_*`
  to its `HACHI_*` equivalent (if unset) and logs a deprecation warning. *(wave 2)*

**Why.** [ADR] **6d shim despite the "no migration shims" memory.** A prior decision ripped out
back-compat shims and resolved never to reintroduce them. The current explicit instruction overrides
that, and this shim is narrow (env-var only, deprecation-warned, no persistent state) and directly
serves the user's own working `.env`. Tension noted here for reversibility. The alias approach for 6a
is preferred over forcing users to rename env vars because it makes the *common* `.env` (bare provider
names) "just work" while keeping the canonical `HACHI_OAUTH_*` names authoritative.

---

## Issue 7 — `settings.toml` validation & authority

**Found.** `settings.toml` **is** loaded and respected (4-layer merge in `config_loader.py`;
`permissions.tier` is a clean behaviour-controlling key for a test). But every model uses
`extra="ignore"`, so **unknown keys are silently dropped**; malformed TOML logs a warning and falls
back to defaults with **no abort**; there is **no file-permission check**; `settings.toml` is **not in
`.gitignore`** and there is **no `settings.example.toml`**; the `provider.headers` dict could harbor a
literal `Authorization` secret.

**Decided/Changed.** `config_loader` gains load-time validation: warn on unknown top-level/section keys
(diffed against `ResolvedConfig`), surface malformed primary-config clearly, and a **strict mode**
(`HACHI_CONFIG_STRICT=1`) that raises. A **world-writable check** (`mode & 0o002`) warns (aborts under
strict) on POSIX, best-effort on Windows. `settings.toml` + `**/.hachi/settings.toml` added to
`.gitignore`; new `settings.example.toml` documents every key (type/default/effect). Full key
reference in `docs/guides/configuration.md`.

**Why.** [ADR] **Validate loudly, fail safe by default.** Hard-aborting every existing install on an
unknown key would be hostile to current users, so the default is warn-and-continue with an opt-in
strict gate for CI/production. The agent already cannot write `permissions.tier` or credential keys
(blocked in `set_agent_config`), so "read-only authority" is enforced at the tool layer; the
world-writable check defends the remaining OS-level vector the spec names.

---

## Issue 8 — `.og` security model, governance, versioning

**Found (8a/8b).** `.og` files are parsed by `og_engine` with a 3-pass (index→dense→full) model;
malformed/missing files are handled gracefully (skipped + logged, sandbox fallback). The agent can
write `.og` via `save_skill`/`add_mcp_server` but only into the skills dir, never `~/.hachi/`
(blocked). **Crucially: permissions are engine-enforced** — `PermissionGate.check_command/check_path`
block actions *before* execution; only an advisory tier/folder **summary** is injected into the
prompt, **not** the full blocked-paths/commands policy. So the agent cannot read and "reason around"
the real policy. **No secrets found in any `.og` file.** Gaps: `upsert_skill`'s write is **non-atomic**,
and there is **no content version history** (only an in-file `::changelog` + semver).

**Decided/Changed.**
- **8b model:** documented tiered trust in `docs/reference/og-security.md` — **Tier 1** engine-enforced,
  never-agent-writable (`device-permissions.og`, `brain.og`); **Tier 2** advisory bundled skills;
  **Tier 3** agent-writable (`learned/*.og`, `mcp-*.og`, guard-scanned + linted on write).
  Recommendation: engine should refuse `upsert_skill` targeting core/system names.
- **8a/8b fix:** `upsert_skill` write made **atomic** (temp + `os.replace`).
- **8c versioning:** new `core/og_history.py` — **append-only** `<skills_dir>/.og-history/<name>.jsonl`
  (`{version, utc-timestamp, author: human|agent, summary, sha256, content}`), wired into
  `upsert_skill` (author=`agent`). CLI `hachi og rollback <name> --version N` (wave 2, records
  author=`human`). UI history view (wave 2 / tracked TODO).

**Why.** [ADR] **Permissions stay out of the prompt; history stays append-only and out of agent reach.**
The audit confirmed the engine-enforcement design is sound, so the work is to *formalize and document*
it rather than re-architect. Versioning is a sidecar JSONL (not git, not a rewrite of the `.og`) so it
is trivially append-only and cannot be edited through the agent's tool surface — satisfying "the
versioning system itself cannot be modified by the agent." Atomic write closes a real
corruption-on-crash risk found in the audit.

---

## Issue 9 — Cost tracking accuracy & completeness

**Found.** Only **5 of 16** model/API call sites track cost — the main LLM path is covered, but
**STT, TTS, the session titler, and Tavily are untracked**. The cost model correctly prices
input/output/cache-read/cache-write Anthropic tokens, but has **no voice pricing**. `get_spend`
collapses a multi-model session into a **single** entry (ignores `per_model_snapshot()`), reports
`inT=0` always, and there is **no per-modality** breakdown and **no "last used model"** in the
conversation UI.

**Decided/Changed.** `cost.py` gains `VOICE_PRICING` + `record_voice_usage(modality, model, units)`
feeding the **same daily ledger** (so `/spend/daily` includes voice). `session_titler.generate_title`
accepts a `cost_tracker` and records its usage. `runtime_events` turn/usage event gains `model` (+
token split); the orchestrator populates it. UI: per-model + per-modality + per-session + cumulative
breakdown in the Spend panel; a "via <model>" badge under the latest assistant message. `get_spend`
wired to `per_model_snapshot()` (wave 2 in `cli/main.py`).

**Why.** Voice (especially ElevenLabs) is materially expensive, so untracked voice was the biggest
correctness gap. Routing one capability to Opus and another to Haiku in the same session made the
single-entry breakdown actively misleading — surfacing `per_model_snapshot()` fixes that. The
"last used model" badge is the cheapest way to give users the confidence the spec asks for.

---

## Issue 10 — Memory audit & quality

**Found.** **Write:** every clean turn triggers LLM fact-extraction with only a 30-char gate (noise
risk); dedup is SHA-256-exact only (paraphrases proliferate), and a bare-fallback in
`memory_tools.py:137` bypasses the smart-consolidation dedup entirely. **Read:** hybrid — user/agent
profiles are **bulk-injected** every session (bounded at 25 entries each, acceptable), workspace
memory is **semantically queried per turn against real user input (good)**, but session-start uses a
**biased fixed query `"important context"`**. **Quality:** `_query_collection` fetches distances but
**never applies a similarity threshold** — top-k is returned regardless of relevance; `core_memory`
and `delegation_log` are **unbounded**.

**Decided/Changed.** Add a configurable **similarity threshold** in `_query_collection` (read distances,
drop low-similarity hits; conservative default). Add **`get_recent_facts()`** and replace the biased
`"important context"` probe with it (orchestrator lane). **Remove the dedup-bypass fallback**
(`memory_tools.py:137`) — log+skip on consolidation failure. Add a **write-quality filter** for trivial
extracted facts. Add a **soft cap + prune** for `core_memory`. Architecture written up in
`docs/guides/memory.md`.

**Why.** [ADR] **A relevance threshold is the highest-leverage fix** — injecting 5 unrelated facts every
turn both wastes context and degrades answers; returning *fewer, relevant* memories beats *k, always*.
The default threshold is deliberately conservative (favor recall) so we don't silently starve good
recalls; it's tunable. Replacing the fixed probe removes a systematic bias that surfaced whatever
happened to match the literal words "important context."

---

## Issue 11 — Model routing options all work

**Found.** Concrete model descriptors (opus-4/-4-8, sonnet-4, haiku-4/-4-5, gpt-4o/-mini) and the
real capability slots (reasoning, coding, summarization, title_generation, compaction, long_context,
vision) all resolve correctly. But **5 capability slots are stubs with no router impl** —
`embeddings`, `speech_to_text`, `text_to_speech`, `web_search`, `realtime` — and there is **no
"last used model" indicator** in the conversation UI.

**Decided/Changed.** Hide/disable the 5 stub capability slots in the routing UI (label "not
configurable yet") so users can't select a no-op route. Add the "last used model" badge (shared with
Issue 9 via the new `model` SSE field).

**Why.** Showing routing controls that silently fall through to the default model is a trust bug —
better to disable them honestly than to imply configurability that doesn't exist. The badge gives the
positive confirmation (which model actually answered) the spec wants.

---

## Issue 12 — Ghost / unnamed sessions

**Found.** `on_connect` calls `SessionStore.start_session()` **eagerly**, persisting an empty SQLite
row before any message exists — so failed boots, OpenAI-compat facade sessions, and
created-but-never-used sessions all leave "Unnamed" rows. `get_recent_sessions()` returns those
0-message rows. Deletion is actually **correct** (it removes from SQLite); sessions "reappear" only
because a new empty row is created on the next send before the first message commits.

**Decided/Changed.** First implemented as **lazy** row creation (defer the INSERT to the first
`log_message`). The full regression suite then showed lazy persistence broke real guarantees other
code/tests rely on: foreign-key rejection of unknown sessions, synchronous-commit visibility, and
per-session cost recording. **Revised to keep `start_session` eager** (row inserted + committed
immediately, FK integrity intact) and prevent ghosts at the *visibility* and *cleanup* layers instead:
`get_recent_sessions` hides sessions with no real signal (no messages AND no cost AND no title AND no
turns); `delete_empty_sessions()` (same predicate) + `POST /sessions/cleanup-empty` route + a "Clean up
empty sessions" UI action remove them on demand; the frontend also guards against rendering obviously
empty unnamed sessions and no longer shows the "no message history found" placeholder. The automatic
startup ghost-purge was **removed** — silently deleting message-less rows on every DB open is dangerous
(it deleted a legacy row a migration test still expected).

**Why.** [ADR] **Don't trade an integrity guarantee for a cosmetic one.** Lazy persistence removed FK
rejection (a safety property) and synchronous commit (relied on by cost recording and same-connection
reads). Hiding + explicit cleanup achieves the user-visible goal (no ghost sessions in the UI; one-click
cleanup) without weakening the store. The visibility predicate keys on "real signal" so a cost-bearing
or titled session is never mistaken for a ghost.

---

## Cross-cutting architectural decisions [ADR]

- **File-disjoint parallelism with contracts.** The backend hotspots (`orchestrator.py`,
  `fastapi_app.py`, `control_plane.py`) are touched by many issues, so they were assigned to a single
  owning lane; other lanes call newly-added functions by agreed signature (`delete_empty_sessions`,
  `record_voice_usage`, `get_recent_facts`, the `model` event field, `generate_title(cost_tracker=)`).
  This guarantees zero edit conflicts while preserving parallelism.
- **Cost discipline.** Audits and implementation ran on Sonnet 4.6 (not Opus) per the cost mandate;
  Opus was reserved for orchestration, decisions, and verification.
- **Verification gate.** No issue is "done" until: `ruff check`, `mypy src`, `pytest -q`, the frontend
  `vitest` + `vite build`, and OpenAPI `check:api` pass (any backend route change requires
  `gateway-openapi` re-export + `npm run generate:api`). Status tracked in the wave-2 reconciliation
  section below.

---

## Implementation reconciliation (what actually landed)

Delegated to 8 parallel Sonnet lanes (wave 1) + 1 CLI lane (wave 2), with cross-cutting glue and
verification by Opus. Per-lane landed changes and their own test counts:

| Lane | Key files | Tests |
|---|---|---|
| Conversation UI | `Markdown.tsx`, `conversation.tsx`, `chat.ts`, `store.ts` | 24 (Markdown) |
| Integrations/Spend UI | `integrations.tsx/.ts`, `SpendSection.tsx`, `ProviderSettings.tsx`, `types.ts` | 24 |
| Voice | `voice.py`, `speech.py`, `voice_endpoints.py`, `ContinuousVoice.tsx`, `voice.ts` | 9 + 4 |
| Memory | `memory_vault.py`, `memory.py`, `memory_tools.py` | 20 |
| Governance | `config_loader.py`, `og_engine/engine.py`, `og_history.py` (new), docs, `settings.example.toml`, `.gitignore` | 16 |
| Creds/env | `connector_registry.py`, `oauth_flow.py` | 17 |
| Gateway core | `orchestrator.py`, `fastapi_app.py`, `control_plane.py` | 16 |
| Supporting backend | `session_store.py`, `registry.py`, `mcp_client.py`, `settings.py`, `cost.py`, `session_titler.py`, `runtime_events.py` | 36 |
| CLI (wave 2) | `cli/main.py` | 32 |

**Cross-cutting glue done by Opus:**
- Memory config keys wired end-to-end: `MemorySettings.recall_distance_max` / `core_memory_max_entries`
  (`settings.py`) → `ChromaMemoryProvider.initialize(...)` (`memory_provider.py`) → vault construction
  (`orchestrator.py`). Without this the keys would be "read by nothing" (the anti-pattern the
  `LimitsSettings` docstring explicitly warns about).
- **Seam GAP-1** (Issue 5): `fallback_active` added to the `GET /integrations` Tavily entry (the UI reads
  it there; the lane had only put it on the POST-credentials response). `fastapi_app.py::get_integrations`.
- **Seam GAP-4** (Issue 6b): new `POST /integrations/{id}/credentials/test` route — bounded live probe
  (real ping for Tavily/Telegram, "stored; no live test" for others), never echoes secrets, never raises.
- **Seam GAP-3** (Issue 9): `get_spend` (`cli/main.py`) now emits the real per-model breakdown from
  `CostTracker.per_model_snapshot()` plus `modalities`/`session`/`cumulative_usd` (C-SPEND), keeping the
  legacy keys for the work tab. *Known limit:* per-session **voice** modality isn't split out — voice cost
  is recorded into the daily ledger total, not a per-session modality bucket. `TODO(issue9)`: modality-tagged ledger.
- **Seam GAP-2** (Issue 1): verified end-to-end — the gateway's `SessionExpiredError` → HTTP 410 with
  `code="session_expired"`, and `client.ts::parseError` lifts `body.code` into `ApiError.code`, which
  `chat.ts` matches. No change needed.
- `docs/guides/memory.md` authored; `settings.example.toml` extended with the new memory + `elevenlabs_model` keys.

**Cross-lane contract alignment (verified):** `session_expired` code (gateway↔chat.ts) ✓; `payload.model`
on `usage.updated` + `session.chat.completed` (A8 emits field, A7 populates, A1 consumes) ✓;
`POST /sessions/cleanup-empty` (A7 route ↔ A1 caller) ✓; `get_recent_facts` (A4 ↔ A7) ✓;
`record_voice_usage` (A8 ↔ A3) ✓; `delete_empty_sessions` (A8 ↔ A7) ✓; `source` field (A6 ↔ UI) ✓.

## Verification results

> **Bottom line:** `pytest -q` = **3359 passed / 0 failed / 17 skipped**; frontend `tsc` + 56 vitest +
> `vite build` green; `ruff check src tests` clean; the `mypy` strict-gate test passes; OpenAPI contract
> regenerated and re-typechecked clean.

- **Frontend:** `tsc --noEmit` clean; **56 vitest tests pass**; `vite build` succeeds (199 modules).
- **OpenAPI contract:** schema re-exported (`hachi gateway-openapi`), `schema.d.ts` regenerated
  (`generate:api`, +128 lines for the new routes/shapes), frontend **re-typechecks clean against the new
  schema**. (The regenerated `schema.d.ts` is an intentional, committable change — `check:api` "diff" is expected.)
- **Backend lint:** `ruff check src tests` — **all checks pass**.
- **Backend types:** `mypy src` baseline is a large pre-existing style-debt set (≈450, `no-untyped-def` /
  `type-arg` / `no-any-return`; mypy is `continue-on-error` on main for this reason). This session
  introduced **7 new errors** (in `og_history.py`, `fastapi_app.py`, `config_loader.py`, `cost.py`,
  `voice_endpoints.py`) — **all 7 fixed**, so the session is net-zero (slightly negative) on the mypy count.
- **Backend tests:** all **144** new-suite tests across the 8+1 lanes pass together (2 POSIX-only skips).
  Fixed one test-isolation bug in `test_memory_fixes.py` (the consolidation-failure tests patched
  `AnthropicClient` during job *capture* but executed the captured job *outside* the patch, so a
  shared client from an earlier test leaked in — now they execute under an active patch).
- **Full-suite regression triage (the important part):** the first full `pytest -q` run surfaced
  **17 failures** — and triage showed these were **real regressions**, not pre-existing env noise. The
  parallel lanes each passed their own isolated tests but broke pre-existing tests they couldn't see,
  in four clusters: (1) the `session_store` **lazy-row** change broke FK-integrity / synchronous-commit /
  session-cost / title-migration tests; (2) a speculative `claude-haiku-4-5` price override shadowed the
  family prefix the cost tests encode; (3) the OAuth **alias** + the local `.env` made three
  "not configured" tests non-hermetic (they cleared only the canonical env var, not the legacy alias);
  (4) a new unused `type: ignore` (`fastapi_app.py:840`) failed the strict **mypy-gate** test. **All four
  root-caused and fixed** — session_store reverted to eager + visibility/cleanup (see Issue 12);
  haiku-4-5 override removed (prefix-match to `claude-haiku-4` → `(1.0, 5.0)`); the three OAuth tests made
  hermetic by also clearing `GOOGLE_CLIENT_ID`; the gated `type: ignore` removed; and one new
  test rewritten to assert the eager design. Re-run of the 9 affected files + the 9 lane suites is
  **green (150 + 144 pass)**; a final full `pytest -q` was run to confirm the whole suite (~3359 tests)
  is clean.
- **Lesson:** parallel lanes must be regression-tested against the *whole* suite, not just their own new
  tests — isolated green is necessary but not sufficient. The first full run is what caught all 17.
- **Live integration proof:** exporting the OpenAPI schema imports the entire gateway cleanly (all lanes
  integrate), and the run shows the **`TOKOBOROS_*`→`HACHI_*` shim firing** on the real `.env` with the
  expected deprecation warnings (Issue 6d verified live).

## Tracked follow-ups (`TODO`)

- `TODO(issue1)`: full server-side session re-boot-on-resume (current fix makes `session_state` SQLite-tolerant + returns a typed `session_expired`; the frontend then resumes/creates cleanly).
- `TODO(issue9)`: modality-tagged daily ledger for a true per-session voice/text/image cost split.
- `TODO(issue10)`: cosine write-time dedup for paraphrases; bound/TTL `delegation_log`; LRU (not just oldest-first) eviction for `core_memory`.
- `TODO(issue8c)`: gateway route + UI view for `.og` version history (CLI `hachi og history`/`rollback` shipped; the append-only store + engine wiring shipped).
- Voice provider/hardware limits (documented, not code-fixable): Whisper is batch-only (mitigate via Groq `whisper-large-v3-turbo` through `stt_base_url`); OpenAI/ElevenLabs TTS buffer server-side until they expose `synthesize_stream`; energy-RMS VAD is noise-sensitive (a WASM VAD like Silero is the upgrade path).

---

## Live bug-fix round (2026-06-23, post-deploy) — user-reported in the running app

Diagnosed against the live gateway; root causes were deeper than the first pass and several
were **pre-existing latent bugs** the first round's fixes merely exposed. All verified live
(gateway booted, real session turns).

**Process gotcha (the meta-cause of "still not working"):** the running gateway is a long-lived
process — uncommitted on-disk fixes and `settings.toml` edits do **not** reach it until restart.
Confirmed via process start-time vs edit-time. Restarted the gateway after each backend change.
The Vite dev server (HMR) does pick up frontend edits live, but it was talking to a stale gateway.

### MCP "internet-search not reachable" (web search) — THREE stacked bugs
- **`[mcp_servers]` silently dropped by the config loader.** `workspace.load_config()` returns
  `ResolvedConfig.model_dump()`, and `ResolvedConfig` had **no `mcp_servers` field** (`extra="ignore"`),
  so every TOML-defined MCP server was stripped before boot ever saw it — TOML MCP servers could
  *never* have worked. Fix: added `mcp_servers: dict[str, dict[str, Any]]` to `ResolvedConfig`
  (`settings.py`). (Also removed A5's false "unknown key: mcp_servers" validator warning.)
- **HTTP transport sent the wrong `Accept` header → `406 Not Acceptable`.** MCP streamable-HTTP
  requires `Accept: application/json, text/event-stream`; `HttpTransport.start()` sent only
  `Content-Type`. The response also comes back as SSE (`data: {…}`) which the parser couldn't read,
  and the spec needs the `Mcp-Session-Id` echoed. Fixed all three in `mcp_client.py::HttpTransport`.
- **Permission gate blocked user-registered TOML servers.** The gate reads its tier from
  `device-permissions.og` (which was `guided`), *not* `settings.toml` (`unrestricted`) — a known
  split. Under `guided`, `is_mcp_server_allowed(name, trusted=False)` denied all of them. Fix:
  TOML `[mcp_servers]` are user-authored config → call the gate with `trusted=True` (unlike
  community-pack `.og` servers). `orchestrator._configure_mcp_servers`.
- **Result (verified):** all 4 servers boot `ready` — `internet-search` exposes `tavily_search`
  + 4 more (17 tools total). `err` now surfaced to the UI (`MCPServerSnapshot.err`).
- *Note:* the user has 3 identical Tavily MCP entries (`tavily`, `tavily-mcp`, `internet-search`) →
  15 duplicate tools; recommend keeping one. The native `[search] provider="tavily"` path also had a
  vault-key-name mismatch (`apikey.tavily.tavily_api_key` vs `tavily_api_key`) — fixed in `registry.py`.

### Model selection (gpt-4o-mini → spend showed haiku) — FOUR stacked bugs
- **The chat baked its model from `provider.model` (haiku), ignoring `routing.default_model`.**
  Fixed: when the router is active, `model_name = routing.default_model` so the session metadata +
  `/spend` reflect what actually runs (`orchestrator.py`). Plus a loud warning on fallback instead of
  silent haiku.
- **`set_routing` didn't reload live agents** → routing UI changes did nothing until restart. Added
  `self._reload_agents()` (`cli/main.py`).
- **Factory rejected provider kind `openai`** (`Unsupported provider kind: openai`) — the bundled
  gpt-4o/gpt-4o-mini descriptors use kind `openai`; the factory only knew `openai-compatible`. Added
  `openai` (maps to the OpenAI base URL) in `llm.py::create_llm_client`.
- **The configured provider's key never reached gpt-4o-mini.** `OpenAICompatProvider.models()`
  advertised descriptors with `provider="openai"`, but the provider's registry name is
  `"openai-compatible"`, so the router couldn't match them to *this* provider (which holds the key)
  and built a keyless generic client. Fixed: re-home advertised descriptors to `self.name`
  (`openai_compat_provider.py`). Also fixed a `gpt-4o-min` typo in the user's `settings.toml`.
- **Result (verified):** a live turn now runs on `gpt-4o-mini` (was haiku).

### Frontend: retry / cross-section leak / title / session list
- **Retry returned the *previous/unrelated* response + duplicated the user message.** `handleRetry`
  called `startTask` (which appends a fresh you/hachi pair), and — the real correctness bug — SSE
  events were applied to the global store with **no session scoping**, so a stale stream (or a
  query from another screen) rendered into the current chat (the "Aditya Dhanraj" leak). Fixes:
  new `store.retryLastMessage()` that resets the failed bubble in place (no duplicate); and a
  `_streamSessionId`/`session_id` guard at the top of `handleSseMessage` + `_cursor` reset on session
  change + single-stream abort (`chat.ts`). This is the highest-impact correctness fix of the round.
- **Title not instant** — there is no backend title event at all; the title only refreshed via
  `loadSessions` on mount. Added `loadSessions()` (immediate + delayed) on `session.chat.completed`.
- **Session list flickered between visible and empty** — `loadSessions` wiped the list to `[]` on any
  transient error, filtered out the active 0-message session, and never marked the live session
  active. Fixed all three (`chat.ts` + `conversation.tsx`).

### Verification (this round)
- Live gateway: 4 MCP servers `ready` (17 tools); turn runs on `gpt-4o-mini`; auth preserved across
  restart (persisted token).
- `ruff` clean on all changed files; OpenAPI re-exported + `schema.d.ts` regenerated + frontend
  `tsc` clean + 56 vitest pass; targeted backend suite **462 passed / 0 failed**; full `pytest -q`
  re-run for cross-cutting regressions.

---

## Reassessment P0 sweep (2026-06-24) — closing the idempotency deficit (AUDIT Theme A / P0-3)

The 2026-06-23 `AUDIT.md` was independently re-verified against HEAD `4063e41` (all 4 P0s + key
P1s still open by direct grep; HEAD's "live bug-fix round" was orthogonal to the architectural
backlog). Began executing the 30-day roadmap **trust-first**, starting with the audit's named
"single most corrosive gap": duplicate side effects on retry. One tested change at a time.

**Why this first.** North star is trust; an assistant that *acts on your behalf* must never do so
twice. Three independent auditors flagged the same hole — no idempotency anywhere a side effect
happens. Closed in three layers.

### P0-3a — outbound messaging dedup (`core/idempotency.py` + `tools/messaging_tools.py`)
- **Found:** the 4 `send_*` tools (Slack/Discord/WhatsApp/Telegram) had zero dedup — an LLM retry,
  the 4-worker tool pool, or a turn resume re-sends the identical message.
- **Changed:** new `IdempotencyGuard` (thread-safe, TTL, `begin`/`finish`/`abort`; mirrors the
  `lease_manager` house style but caches+replays the prior **success** result). Wired into all four
  tools. **Scope = one guard per session** (per `register_messaging_tools` call): covers every
  in-session retry path, but never suppresses a genuinely distinct send from another concurrent
  session. Only **successful** sends are deduped — a failed attempt `abort()`s its reservation so a
  real retry still goes through.
- **Gotcha caught by a test:** `IdempotencyGuard.__len__` makes an empty guard *falsy*, so the
  `guard or IdempotencyGuard()` idiom silently discarded an injected guard → fixed to an explicit
  `is None` check (commented so it isn't reintroduced).
- **Tests:** `test_idempotency.py` (11, incl. fake-clock TTL expiry + a 50-thread race proving
  exactly one `begin` wins) + 8 tool-layer dedup tests. Fixed a pre-existing `_telegram_send` mypy
  `no-any-return` while in the file.

### P0-3b — scheduler dedup (`jobs/scheduler.py`)
- **Found:** `Scheduler.add()` minted a fresh `uuid4` every call → a retried `create_schedule`
  produced a duplicate **recurring** job (fires forever).
- **Changed:** a windowed fingerprint scan (`_find_recent_duplicate`, default 60 s) — a retry with
  the same logical identity (name/task/kind/timing/delivery) inside the window returns the existing
  schedule instead of a duplicate. Scans the **live store** (the schedules dict *is* the dedup
  record — no parallel bookkeeping that can drift). Keys on persisted `created_at`, so dedup also
  holds across a restart between the two creates.
- **Tests:** 5 in `test_scheduler.py` (within-window, past-window, distinct-identity, window=0
  disable, and an across-restart case).

### P0-3c — ingress idempotency (`core/session_store.py` + `gateway/fastapi_app.py` + `frontend/chat.ts`)
- **Found:** `POST /sessions/{id}/messages` had no `Idempotency-Key` — a client retry / reconnect /
  double-click re-runs the **whole turn** (double spend + every side effect again).
- **Changed:** durable `idempotency_keys` table in SessionStore (`PRIMARY KEY (session_id, idem_key)`,
  `INSERT OR IGNORE` = first-write-wins, survives restart) + `get/save_idempotent_response`. The
  route reads `Idempotency-Key` (header preferred, `idempotency_key` body field fallback), replays
  the stored response on a hit, and persists **only on success** (`send_message` raises on failure →
  left retryable). Frontend keys each turn on the stable assistant-bubble id (`turn-${hachiMsgId}`);
  `retryLastMessage` already reuses that id, so a retry/double-submit replays server-side with zero
  extra state. `control_plane` untouched (uses injected callbacks, no store handle) — store resolved
  in the route via the existing `ProcessSharedServices` pattern (`_resolve_session_store`, patchable).
- **Tests:** `test_ingress_idempotency.py` (9: 5 durable-store incl. restart + first-write-wins +
  per-session scoping; 4 route incl. same-key-replays-without-rerunning via an `on_chat` call
  counter, distinct-keys, no-key, body-field fallback). OpenAPI re-exported + `schema.d.ts`
  regenerated; frontend `tsc` clean + 56 vitest pass.
- **Honest scope:** outbound + scheduler dedup are in-process/short-window (the realistic automatic
  retry paths); ingress is durable. Concurrent *in-flight* duplicates (same key, truly simultaneous)
  are suppressed for outbound (the guard's pending reservation) but for ingress two simultaneous
  first-time posts could both run before either stores — `INSERT OR IGNORE` still keeps the replay
  consistent. Provider-native idempotency keys (where a provider supports them) remain a future
  upgrade over content-hash dedup.

**Verification:** consolidated workstream run **155 passed / 0 failed**; `ruff` + `mypy` clean on all
changed modules; OpenAPI + frontend contract regenerated and consistent.

### P0-2 — `interaction_log`, the keystone of the eval loop (`session_store.py` + `loop.py` + `orchestrator.py`)
- **Found:** no durable per-interaction record existed — SessionStore persisted only flat `messages`
  rows, and per-turn cost was reconstructed by scanning the last 500 in-memory events (lost on
  restart). No row-level ground truth → no rating, no failure mining, no per-turn audit, no
  output-regression net. The audit's biggest world-class gap (eval maturity) was *unbuildable*
  without this.
- **Changed (three small pieces):**
  1. **Storage** — `interaction_log` table `{id, session_id, turn_id, ts, user_input, agent_output,
     tool_calls(JSON), memory_hits(JSON), model_used, token_cost, tokens, rating, notes}` +
     `log_interaction` / `get_interactions` / `set_interaction_rating`. `session_store.py` is in the
     **strict mypy gate** and stays clean. `rating`/`notes` are NULL until a future `/review`
     surface fills them (the write side, `set_interaction_rating`, already exists).
  2. **Per-turn tool capture** — `ExecutionLoop` now records `last_tool_calls` (names, in call order),
     reset at the top of each `run()` and appended in the sequential pre-pass (thread-safe, before the
     pool dispatch). Exposed as a copy via a property.
  3. **Write site** — extracted `AgentSession._write_interaction_log` (a testable seam) called once
     per turn from `_chat_impl_inner`, mapping in-scope values: recalled facts → `memory_hits`,
     `loop.last_tool_calls`, `self.model_name`, and **per-turn** cost/token deltas (added
     `_last_recorded_tokens` to mirror the existing `_last_recorded_cost_usd`). Best-effort — a
     logging failure never breaks a turn.
- **Tests (14):** 8 store (round-trip, JSON decode, per-session scoping, ordering+limit, rating
  set/preserve/unknown, restart-durability) + 3 loop (batch order, empty-before-run, reset-per-run via
  the `ScriptedLLMClient` harness) + 3 write-site (field mapping, no-op without store, swallows store
  errors). No real `boot_agent` turn harness exists in the suite, so the write site is covered via the
  extracted seam rather than a full boot.
- **Verification:** 57-test P0-2 regression green; `ruff` clean; **zero new mypy errors** (loop.py
  10→10, orchestrator.py 34→34, both pre-existing-advisory and not in the strict gate; session_store
  strict-gated and clean). Deferred to a later increment: the `/review` UI + thumbs endpoint + a
  trajectory-eval suite that *consume* this table (now unblocked).

### P0-1 — wiring the suspend producer (`loop.py` + `orchestrator.py`)
- **Found:** a dead marquee feature. `loop.py` caught `SuspendWorkflow`, ran
  `return "[WORKFLOW SUSPENDED] {reason}"`, and **discarded the structured wait condition**. No
  `RunState(WAITING)` was ever created, so the *entire* consumer subsystem — wake (`POST /runs/{id}/wake`),
  boot-recover (`_boot_recover_runs`), the 30 s timeout sweep, and `GET /runs` — had **zero inputs**
  and was permanently empty. The suspend tool's own docstring *claimed* "the gateway layer persists
  RunState(WAITING) + arms the registry" — that wiring did not exist. Tests asserted only the
  exception contract, so CI stayed green over a feature that did nothing.
- **Changed (the missing connection, both halves now meet):**
  1. `ExecutionLoop` now surfaces the structured suspend via `last_suspend` (the `SuspendWorkflow`
     itself), reset each `run()` and set in the suspend branch — mirroring the `last_tool_calls`
     pattern. The return string is unchanged (no contract break for existing callers).
  2. `AgentSession._persist_suspended_run_if_any(turn_id)` runs right after `loop.run()`: maps
     `wait_kind`→`WaitKind` (unknown → `USER`, the safe default), builds a `WaitCondition`, and
     `RunStateStore().upsert(RunState(status=WAITING, …))` + `get_wait_registry().arm(run_id, wait)`,
     then emits the existing `run.suspended` event. Best-effort (never breaks a turn); no-op on a
     normal turn. `resume_cursor=0` for now — a warm wake resumes the live in-memory session; precise
     cursor + cold-start replay remain the separately-tracked deferred resume work.
- **Tests (5):** 2 loop (`last_suspend` set with the right wait_kind/key on suspend via the
  `ScriptedLLMClient`; None on a normal turn) + 3 producer (`test_suspend_workflow.py`: creates a
  durable WAITING run **and** arms the registry so `resolve(key)` returns the run_id; unknown
  wait_kind → USER; no-op when not suspended). These assert the **durable side effect** the prior
  tests never did. 108-test producer+consumer regression green (`test_durable_store/waits/runtime_*`).
- **Verification:** `ruff` clean; **zero new mypy errors** (loop 10→10, orchestrator 34→34).

### P0-4 — making the Docker self-host recipe actually run (`frontend/` + `Dockerfile` + compose + CI)
- **Found:** `docker compose up` failed on first invocation. (1) `frontend` service built `./frontend`
  with `dockerfile: Dockerfile` — which **did not exist** → immediate build failure. (2) The backend
  runs non-root `USER hachi` (uid 10001) but volumes mounted to `/root/.config/hachi` etc. — paths the
  non-root user **cannot write**. (3) `test_docker_assets.py` only asserted the string `"8787"` was
  present, so CI was falsely green over all of it.
- **Changed:**
  - **frontend image (new):** `frontend/Dockerfile` (multi-stage — `node:22` build → `nginx:1.27`
    serve), `frontend/nginx.conf.template`, `frontend/docker-entrypoint.sh`, `frontend/.dockerignore`.
    nginx serves the built SPA on 5173 and reverse-proxies `/api/` → the gateway, **mirroring the dev
    Vite proxy**: trailing-slash `proxy_pass` strips the `/api` prefix, the bearer token is injected
    server-side (read from the mounted secret by the entrypoint → `envsubst` into the config, so the
    browser never holds it), and `proxy_buffering off` keeps SSE streaming. SPA `try_files` fallback
    for client-side routing.
  - **writable volumes:** switched the four backend volumes from `/root/...` bind mounts to **named
    volumes** targeting `/home/hachi/...` (the platformdirs paths for that user). The Dockerfile now
    pre-creates those XDG dirs `chown`ed to `hachi`, so each named volume **inherits hachi ownership**
    on first init and the non-root user can write. (A host bind mount would be created root-owned and
    re-break writes — hence named volumes, the standard non-root pattern.)
  - **honest tests + CI:** replaced the string-only `test_docker_assets.py` with 10 structural guards
    (frontend Dockerfile exists/builds/serves; volumes target `/home/hachi` not `/root`; named volumes
    declared; Dockerfile pre-creates+chowns the dirs; nginx proxies `/api` with prefix-strip + token +
    SSE; entrypoint reads the secret). Added a `docker-smoke` CI job (`docker compose config` +
    `docker compose build` of **both** images) so a missing/broken Dockerfile can't pass green again.
  - `.gitignore`: ignore `.hachi/` (holds the local gateway-token secret).
- **Verification:** `docker compose config` **valid** (volumes resolve to `/home/hachi`, both contexts
  build); compose + ci YAML parse-validated; 10 structural tests green. A live `docker compose build`
  could **not** be run locally — Docker Desktop's Linux engine went down mid-session — so the live
  build is verified by the new `docker-smoke` CI job (stable Linux Docker), exactly the audit's
  prescribed verification path. Honest gap: the running container has not been hit with a real browser
  request locally; the proxy/token/SSE wiring is asserted structurally + mirrors the proven dev proxy.

### Google Workspace config resolution (2026-06-24, user-reported: "configured in .env but does it work?")
- **Found (proven by reproduction):** `core/oauth.py` captured the OAuth client id/secret as
  **module-level globals at import time** — `GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")`.
  So a value present in `.env` but merged into the environment *after* this module was first imported
  (a different import order, a non-CLI entrypoint, or a `.env` in a directory the loader didn't read)
  was **never seen**: every Google tool failed "Missing GOOGLE_CLIENT_ID" despite being configured.
  Two compounding gaps: (a) `register_google_tools` gated the real-vs-stub decision on bare
  `os.environ`, and (b) `hachi setup` persists keys to `config_dir/.env`, but the startup loader only
  read `cwd/.env` — so a wizard-configured credential was written to disk and never loaded next run.
- **Changed (three layers, so "configure → it works" is reliably true):**
  1. **Lazy resolution** (`oauth.py`): replaced the import-time globals with `_resolve_env()` (read at
     call time: process env first, then `cwd/.env`, then `config_dir/.env`) behind `_google_client_id()`
     / `_google_client_secret()`; updated `_require_google_client_id` + the browser/refresh flows.
  2. **Consistent gate** (`google_tools.py`): `register_google_tools` now uses `_google_client_id()`
     (same resolver), so a credential configured in *any* `.env` location mounts the real hub, not the
     "not configured" stub.
  3. **Root cause** (`cli/main.py`): the startup loader now also merges `config_dir/.env` (where the
     setup wizard writes) into `os.environ` — `cwd/.env` still wins on conflicts. Fixes Google **and**
     every other wizard-configured credential (SMTP/Telegram/…) being silently ignored on the next run.
- **Not changed (by design):** the first-time consent still uses the local-server browser flow
  (`webbrowser.open` + loopback redirect). On a headless gateway the intended path is to run
  `hachi auth google` once (browser, consent, token persisted); the gateway then uses the saved token
  and refreshes silently. A first Google call in a headless context with no saved token still blocks
  ~120 s on the browser flow — noted as a follow-up (a non-interactive guard that returns
  "run `hachi auth google`" instead of hanging).
- **Tests (9):** 4 oauth regression (value set after import is resolved; env beats .env; .env
  fallback; dotenv parsing) + updated 4 patch sites to the resolver + 4 "no-credentials" stub tests
  now force the resolver to None (clearing only `os.environ` no longer suffices in a repo that ships a
  `.env`) + 1 new "mounts the real hub when the id is only in `.env`". 213-test Google/OAuth/credential/
  deployment regression green; `ruff` clean; **zero new mypy errors** (oauth 19→19, cli/main 121→121).

### Connector hardening (2026-06-24, "check if something is left") — Google write-tool idempotency + headless auth
Completeness sweep after the P0/Google commit (`4fc85b2`) surfaced two real residual items (the rest
of the backlog is the roadmapped P1 tier). Both closed:
- **In-turn idempotency for Google writes (Theme A completion).** Ingress idempotency covers full-turn
  *client* retries, and the messaging tools got per-call guards — but an LLM-retry / tool-pool
  re-dispatch *within* a turn could double-send an email or double-create an event. Added an **opt-in**
  guard to `GoogleHTTPClient.post` (`idempotency_key` param; refactored the body into `_post_raw`): a
  duplicate POST with the same key replays the prior JSON response. Per-client (= per-session) guard.
  **Only side-effecting leaf tools pass a key** — `gmail.send_email`, `gmail.reply_thread`,
  `calendar.create_event`, `calendar.create_recurring_event` — keyed on the **logical** identity
  (to/subject/body, thread/body, title/start/end[/RRULE]), NOT the serialized body (MIME boundaries +
  Meet `requestId` are random). Reads / batch / freeBusy pass no key and are never deduped. Failures
  `abort()` (stay retryable). The 4 test `FakeGoogleClient.post` stand-ins were updated to accept (and
  ignore) the kwarg. *Deliberately scoped to outbound/notifying ops; in-account creates (draft, task,
  contact, file upload) are reversible and can adopt the same one-line pattern later.*
- **Headless first-auth fail-fast.** A first Google call in the gateway with no saved token used to
  open a browser + block ~120 s on a dead loopback redirect. The `gateway`/`serve` server now
  `setdefault`s `HACHI_OAUTH_NONINTERACTIVE=1`; `_run_google_browser_flow` checks it and raises a clear
  "run `hachi auth google`" error immediately. `hachi auth google` runs in its own interactive process
  (flag unset) so the real consent still works. Gotcha caught: `test_cli_gateway_command` invokes the
  `gateway` command → sets the flag **process-wide** for the suite, so the existing browser-flow tests
  were made hermetic (`delenv`).
- **Tests (8):** `test_google_idempotency.py` (6: keyed-post replays/sends-once, no-key never dedups,
  failure retryable, distinct keys, + 2 `send_email` leaf-level dedup/distinct) + 1 headless-guard +
  hermetic fixes. Full Google/OAuth/gateway regression green; `ruff` clean; **zero new mypy errors**
  (oauth 19→19, cli/main 121→121; client.py actually 5→4).

### P1-2 — hot-path model tiering (`core/routing/classifier.py` + `core/loop.py`)

- **Found:** `ExecutionLoop.__init__` stores `self.router` (optional `ModelRouter`) but `self.router`
  is **never used** in the generate path. Both `_generate_with_retry` and `_stream_generate` always
  call `self.llm_client` — the fixed default client. Net effect: 100% of user turns use the default
  model with no complexity-based tiering and no cross-provider failover chain, even when a
  `ModelRouter` is wired in by `boot_agent`.

- **Changed:**

  1. **`src/hachi/core/routing/classifier.py` (new):** Pure `classify_turn_capability(user_input: str)
     -> Capability` function. No LLM calls. Rules (top-to-bottom, first match wins):
     - **LONG\_CONTEXT** — prompt ≥ 16 000 chars (~4 000 tokens) OR long-doc phrase detected
       ("entire file", "whole codebase", "all pages", "full document", etc.).
     - **CODING** — code fence (`` ``` ``) found OR ≥ 2 coding keywords present
       (`def `, `class `, `import `, `function`, `implement`, `refactor`, `debug`, `bug`, `error`,
       `exception`, `syntax`, `compile`, `build`, `test`, `unittest`, `pytest`, `lint`,
       `type hint`, `type annotation`).
     - **SUMMARIZATION** — `summarize`/`summarise`, `tl;dr`, `tldr`, `key points`, `main points`,
       or `abstract` detected.
     - **REASONING** — analytical phrase detected (`step by step`, `explain`, `analyse`/`analyze`,
       `compare`, `evaluate`, `pros and cons`, `trade-off`, `why`, `reasoning`, `cause`,
       `because`, `therefore`, `conclusion`) **and** prompt is ≥ 200 chars (short "why?" stays default).
     - **REASONING (default)** — all other turns (REASONING is the widest capability net:
       all Anthropic models support it so the router always has candidates).

  2. **`src/hachi/core/loop.py`:**
     - Added `self._turn_client: BaseLLMClient = llm_client` instance attribute (set in `__init__`,
       default = `llm_client` so router=None path is byte-for-byte unchanged).
     - Added `_resolve_turn_client(user_input)` method: calls `router.new_turn()` (clears the A12
       sticky cache), calls `classify_turn_capability(user_input)`, then `router.select(capability)`.
       On `NoRouteError` or any routing exception: logs at DEBUG and falls back to `llm_client`.
       Sets `self._turn_client` to the resolved/fallback client.
     - In `run()`, after `user_input_for_turn` is resolved: resets `self._turn_client = self.llm_client`
       then calls `self._resolve_turn_client(user_input_for_turn)` when `self.router is not None`.
     - Replaced `self.llm_client.generate` → `self._turn_client.generate` in `_generate_with_retry`.
     - Replaced `self.llm_client.generate_stream` → `self._turn_client.generate_stream` in
       `_stream_generate`.
     - Updated the model-name lookups (`_llm_model` for span + `model_name` for cost tracking) from
       `self.llm_client` → `self._turn_client` (accurate telemetry for the actual generating model).

- **Why:** ModelRouter already builds a `ResilientClient` fallback chain per `(capability,
  constraints)`. By routing the main turn through it we get: (a) cheaper models for simple turns,
  (b) reasoning-class models for multi-step/complex turns, (c) automatic cross-provider failover if
  the primary is down — all with zero additional LLM calls. The heuristic mis-routes are safely
  degraded (wrong tier, not wrong answer) and the back-compat guarantee means existing sessions with
  no router see zero behavior change.

- **Sticky-per-turn:** `router.new_turn()` is called once at the top of each `run()` call so the A12
  sticky-route cache is cleared and the turn re-selects independently. Within a single run() the
  `_resolve_turn_client` selection is stable; the router's own sticky cache handles repeated `select()`
  calls (e.g. from sub-tool routing within the same turn).

- **Verify (2026-06-24):**
  - `uv run pytest tests/test_hot_path_routing.py tests/test_e2e_loop.py tests/test_routing_compat.py
    tests/test_routing_router.py tests/test_routing_capability.py tests/test_routing_policy.py
    tests/test_provider_registry.py tests/test_routing_registry.py -q` → **119 passed / 0 failed**.
  - `uv run ruff check src/hachi/core/loop.py src/hachi/core/routing/classifier.py
    tests/test_hot_path_routing.py` → **All checks passed**.
  - `uv run ruff format --check` (same three files) → **already formatted / clean**.
  - `uv run mypy src/hachi/core/loop.py --ignore-missing-imports` → **10 errors (baseline = 10,
    no new errors introduced)**.
  - `uv run mypy src/hachi/gateway/orchestrator.py --ignore-missing-imports` → **34 errors (baseline =
    34, untouched)**.

---

### P1-3 — enforce daily spend cap

**Found.** `max_daily_cost_usd` was declared in `LimitSettings` (`core/settings.py:117`),
displayed in the CLI `/spend/daily` route (`cli/main.py:1441`), and stored in the daily
ledger via `SessionStore.record_daily_cost` — but never read back to block a turn.
`get_daily_spend` had exactly one caller (the display path). The per-session and per-turn
caps in `core/loop.py` were enforced (`_budget_exceeded` / `_turn_budget_exceeded`) but
the calendar-day cap was silently ignored.

**Changed.**
- `src/hachi/gateway/orchestrator.py` — three minimal edits:
  1. Added `_max_daily_cost_usd: float | None = None` instance attribute to
     `AgentSession.__init__` with a comment noting the P1-3 source.
  2. Added `AgentSession._daily_cap_exceeded() -> str | None` helper (before
     `_chat_impl_inner`): reads `_session_store.get_daily_spend(date.today().isoformat())`,
     compares against `_max_daily_cost_usd`, returns a reason string on breach or `None`.
     Wraps the read in `try/except` — a store failure returns `None` (safe fail-open).
  3. In `_chat_impl_inner` (after `_drain_job_queue`, before `_memory_snapshot` build):
     calls `_daily_cap_exceeded()`; if truthy, emits `BUDGET_EXCEEDED` event and returns
     `[BUDGET LIMIT REACHED] daily cost cap reached (~$X.XXXX >= $Y.YY)` — same format
     as `loop.py`'s per-session/per-turn refusals.
  4. In `boot_agent`: reads `max_daily_cost_usd` from `limits_cfg` /
     `HACHI_MAX_DAILY_COST_USD` env (same pattern as session/turn caps), assigns to
     `session._max_daily_cost_usd` after `set_session_store`.
  5. Added `BUDGET_EXCEEDED` import from `hachi.core.runtime_events`.
- `tests/test_daily_cap_enforcement.py` — new file; 12 focused tests covering all cases:
  cap not set / zero / store None (all return None), under cap, at cap, over cap, store
  read error (fail-open), date key alignment, and `_chat_impl_inner` refusal message +
  event emission.

**Why.** Minimum-correct fix: the check lives in the orchestrator (not the loop) because
that is where `_session_store` and the config-derived cap are both visible at turn start.
No loop state is mutated. Abort-only (no model-downgrade) per the P1-3 scope; downgrade
is a separate P1-2 finding. `BUDGET_EXCEEDED = "budget.exceeded"` was already in the
runtime event taxonomy — no new event invented.

**Verify.**
```
uv run pytest tests/test_daily_cap_enforcement.py tests/test_daily_spend_ledger.py -q
uv run ruff check src/hachi/gateway/orchestrator.py
uv run mypy src/hachi/gateway/orchestrator.py --ignore-missing-imports
```

---

### P1-4 — web Stop cancels the server-side turn

**Found.** `ExecutionLoop.cancel()` (a `threading.Event`) is correctly implemented and
tested. But it was unreachable from the gateway. The gateway only called
`turn_task.cancel()` (asyncio `CancelledError`) on client disconnect — which cancels the
*coroutine* waiting for the result but not the *worker thread* running the turn via
`anyio.to_thread.run_sync(...)`. The worker thread continued to completion, incurring
full LLM and tool costs after the user hit Stop. There was also no HTTP route to trigger
a mid-turn cancellation explicitly.

**Decided/Changed.**
1. **`GatewayControlPlane.__init__`** (`src/hachi/gateway/control_plane.py`) — added
   `on_interrupt: Callable[[str], dict[str, Any]] | None = None` parameter (stored as
   `self._on_interrupt`), mirroring the existing `on_chat`/`on_connect` callback pattern.
2. **`GatewayControlPlane.interrupt(session_id)`** — new public method: checks that the
   session is live (returns `{"interrupted": False}` if not), then calls `_on_interrupt`
   if wired (swallows handler exceptions so it never leaks a 5xx). Always returns
   `dict[str, Any]` with a single `"interrupted"` bool key.
3. **`_InProcessGatewayRuntime.interrupt_session(session_id)`** (`src/hachi/cli/main.py`)
   — new method: looks up the `AgentSession` by session_id under `_agents_lock`, calls
   `agent.loop.cancel()` (thread-safe), returns `{"interrupted": True/False}`.
4. **`_build_gateway_control_plane`** (`src/hachi/cli/main.py`) — wires
   `on_interrupt=runtime.interrupt_session if runtime else None` (one new line, matching
   the pattern of the 30+ other callbacks in that function).
5. **`POST /sessions/{session_id}/interrupt`** (`src/hachi/gateway/fastapi_app.py`) —
   new route; uses the same `_check_session_ownership` + `_require_auth` as every other
   `/sessions/{id}` route; returns `SessionInterruptResponse` (new `BaseModel`). Returns
   200 with `{"interrupted": false}` for non-live sessions (safe, never 404/500).
6. **SSE disconnect wiring** (`fastapi_app.py`) — both the primary SSE stream
   (`/sessions/{id}/stream`) and the OpenAI-compatible stream (`/v1/chat/completions`)
   now also call `control_plane.interrupt(session_id)` on `await request.is_disconnected()`
   in addition to the existing `turn_task.cancel()`.
7. **Frontend** (`frontend/src/lib/data/domains/chat.ts`) — `stopStreamingLive()` now
   fires `POST /sessions/{id}/interrupt` as a fire-and-forget (`catch(() => void 0)`)
   before calling `abortSse()`, so the Stop button explicitly stops the server-side
   worker thread even when SSE disconnect detection lags.
8. **OpenAPI** — `openapi/hachi-openapi.json` and `frontend/src/lib/data/schema.d.ts`
   regenerated to include the new route and `SessionInterruptResponse` schema.
9. **Tests** — `tests/test_gateway_interrupt.py` (new, 13 tests): control-plane unit
   tests (handler called, unknown-session safe, no-runtime safe, handler-exception safe),
   HTTP integration tests (200/403/401), ownership enforcement, and a real-loop smoke test
   verifying `_cancel_event` is set by `loop.cancel()`.

**Why.** The minimal correct path: `loop.cancel()` already existed and was fully tested;
the gap was purely a wiring gap. No cancellation logic was reimplemented. The callback
pattern matches the existing 30+ callbacks in `control_plane.py` exactly, keeping the
surface consistent. The SSE and frontend wiring are belt-and-suspenders: the browser
disconnect alone should trigger `is_disconnected()` → `interrupt`, but the explicit Stop
button call guarantees the signal arrives even on HTTP/1.1 where disconnect detection may
lag by one poll interval.

**Verify.**
```
uv run pytest tests/test_gateway_interrupt.py tests/test_gateway_control_plane.py tests/test_loop_backstop_cancel.py tests/test_session_ownership.py -q
uv run ruff check src/hachi/gateway/control_plane.py src/hachi/gateway/fastapi_app.py src/hachi/cli/main.py tests/test_gateway_interrupt.py
uv run ruff format --check src/hachi/gateway/control_plane.py src/hachi/gateway/fastapi_app.py src/hachi/cli/main.py tests/test_gateway_interrupt.py
uv run mypy src/hachi/gateway/fastapi_app.py --ignore-missing-imports   # must be 0
uv run mypy src/hachi/gateway/control_plane.py --ignore-missing-imports  # must be 0
npm run --prefix frontend typecheck   # must be 0
```

---

### P1-8 — MCP retry / circuit-breaker / reconnect

**Found.**  `MCPServerConnection.call_tool(...)` was single-shot: one transport call, no
retry, no circuit-breaker, no reconnect.  A flapping or mid-session-dead MCP server was
re-hit every turn at full blocking cost.  The `resilient_call` / `HealthRecorder` /
`RetryPolicy` machinery in `core/resilience.py` was already wired to LLM, Speech, and
Search providers — but never to MCP.  `errors.py` already defined `MCPError` and
`ToolError` (both unused: `grep "raise ToolError\|raise MCPError" src` → 0).  When an
MCP transport error occurred, `call_tool` returned an English string (`"Error: No response
from MCP server '...'"`) that the loop treated as a successful tool result
(`is_error=False`), hiding failures from the LLM and the operator view.

**Decided/Changed.**
1. **`src/hachi/core/mcp_client.py`** — Added:
   - Three new imports at top: `MCPError`, `HealthRecorder + RetryPolicy + resilient_call`.
   - `_MCPTimeoutError(TimeoutError)` — private exception for transport-dead/no-response
     cases; `TimeoutError` base ensures `resilience._is_transient` recognises it as
     retryable without coupling `MCPError` (which can be non-retryable protocol errors) into
     the transient-detection path.
   - Three new attributes in `MCPServerConnection.__init__`: `_retry_policy`, `_health_recorder`,
     `_reconnect_lock` — all scoped **per connection** so one dead server's breaker is invisible
     to peers.  Policy defaults: `max_attempts=3`, `backoff_base_s=0.25`, `breaker_threshold=5`,
     `breaker_cooldown_s=30.0`.
   - `call_tool(...)` replaced: fast-fails (raises `MCPError`) when the breaker is open;
     attempts one `_try_reconnect()` when the transport is dead before entering the retry loop;
     delegates to `resilient_call(_attempt, ...)` with `on_failover` logging; wraps non-MCPError
     exceptions in `MCPError`.
   - `_try_reconnect()` — serialised via `_reconnect_lock`; calls `transport.close()`,
     re-creates transport via `_make_transport()`, re-runs `_start_inner()` (the same sequence as
     `MCPServerConnection.start()`, minus the outer timed thread).
   - `_call_tool_once(...)` — extracted from the old `call_tool` body; raises
     `_MCPTimeoutError` on no-response (transient/retryable) and `MCPError` on JSON-RPC error
     responses (non-transient, not retried).
2. **`tests/test_mcp_resilience.py`** (new) — 16 tests covering all five required scenarios:
   transient retry + success, max-attempts exhaustion, breaker opens + fast-fails without
   touching transport, dead transport → exactly one reconnect, reconnect failure → MCPError,
   no reconnect when transport healthy, happy-path result unchanged, per-server isolation.
   Uses `FakeTransport` (no subprocess/network) and monkey-patched `_call_tool_once`.

**Secondary error-surfacing (DONE — partial).** Before this change, a tool failure
returned a success-looking string (`is_error=False`).  After this change, `call_tool`
raises `MCPError` on all failure paths.  The execution loop (`core/loop.py:401-402`)
already catches any `Exception` from `registry.execute(tool_call)` and returns
`(error_string, is_error=True, None)`, which the loop encodes as `{"is_error": True}` on
the `tool_result` block — visible to the LLM and the operator.  The full structured
`{ok, code, message, retryable}` envelope refactor (cross-cutting, all tool types) is
deferred as a follow-up.

**Why.**  Minimal-safe: reused the existing `resilient_call` + `HealthRecorder` +
`RetryPolicy` with zero changes to `resilience.py`.  Per-server isolation is automatic
because each `MCPServerConnection` owns its own recorder; no global state.  The
`_MCPTimeoutError(TimeoutError)` trick avoids modifying `resilience._is_transient`
(which is also used by LLM/Speech/Search providers) to avoid unintended side-effects.
The reconnect path reuses the existing `_make_transport()` + `_start_inner()` pair that
was already the established boot sequence.  The successful path is byte-for-byte identical
to the old `call_tool` (same return value, same truncation logic).

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_mcp_resilience.py tests/test_mcp_transport_and_config.py tests/test_mcp_client_timeout.py tests/test_mcp_injection_guard.py -q
uv run ruff check src/hachi/core/mcp_client.py tests/test_mcp_resilience.py
uv run ruff format --check src/hachi/core/mcp_client.py tests/test_mcp_resilience.py
uv run mypy src/hachi/core/mcp_client.py src/hachi/core/resilience.py src/hachi/core/errors.py --ignore-missing-imports   # 0 errors across all three
```

---

### P1-6 — apply compaction summary back into history

**Found.** `MemoryManager._run_and_clear_summary_job` generated a real structured
summary via `_generate_structured_summary(messages)` but discarded it — only returning
it as a job audit value. The placeholder string inserted by `_sliding_window_compact`
(`"[Context compacted asynchronously. Permanent facts are being flushed…]"`) stayed in
`_history` permanently. On provider failover or resume the model received the useless
placeholder instead of the real compacted context (Active Task / Completed Actions /
Key Decisions / Active State / Blocked).

**Changed.**

- `src/hachi/core/memory.py` — 3 logical edits:
  1. Added `import threading` to the import block.
  2. Added `self._history_lock: threading.Lock = threading.Lock()` in `__init__` with a
     comment explaining the concurrency contract.
  3. Wrapped `self._history = anchor + [checkpoint] + recent` in `_sliding_window_compact`
     with `with self._history_lock:` (tiny critical section — single list assignment).
  4. Rewrote `_run_and_clear_summary_job`: after generating the real summary it now calls
     `self._apply_summary_to_history(summary)` before returning.
  5. Added new method `_apply_summary_to_history(summary)`: acquires `_history_lock`,
     scans `_history` for the placeholder checkpoint by `_COMPACTION_PLACEHOLDER_SENTINEL`,
     replaces its `.content` **in place** with the real checkpoint text (same framing
     header), then releases the lock. If no placeholder is found (re-compacted in the
     meantime), logs at DEBUG and returns without side-effects.
  6. Added class-level constant `_COMPACTION_PLACEHOLDER_SENTINEL = "[Context compacted
     asynchronously."` so tests and the scanner share one canonical string.

- `tests/test_compaction_summary_applyback.py` — new file, 4 focused tests.

**Concurrency approach — why it is safe.**

`_history` is mutated by the main turn thread (in `_sliding_window_compact`,
`add_message`, etc.) and read-modified by the background worker thread (in
`_apply_summary_to_history`). A plain `threading.Lock` (non-reentrant) is sufficient:

- No caller holds `_history_lock` while calling back into `MemoryManager`, so
  there is no re-entrancy and therefore no deadlock risk.
- The lock is held for the duration of a single list assignment (`_sliding_window_compact`)
  or a short linear scan + one attribute set (`_apply_summary_to_history`) — never across
  the LLM network call (which takes seconds and runs outside the lock).
- Replacing `msg.content` on a `Message` object that is still referenced by `_history`
  is safe: Pydantic `BaseModel` fields are mutable; no list reordering occurs.
- If `_history` was rebuilt by a second compaction before `_apply_summary_to_history`
  runs, the scan finds no sentinel (because the new list was constructed with a fresh
  placeholder Message object that also contains the sentinel if an in-flight job is still
  pending — but `_compaction_in_flight` guard prevents a second job, so only one
  placeholder + one job exist at a time). In the rare case that the history was reset/
  cleared entirely between enqueue and execution, the sentinel scan returns no match
  and the method is a no-op.

**Why.**  The real structured summary (Active Task / Completed Actions / Key Decisions /
Active State / Blocked) is critical for model continuity across context compactions.
Without it, every resumed session or provider failover sees only the opaque placeholder
rather than the dense checkpoint the background job worked to produce.

**Verify.**
```
uv run pytest tests/test_compaction_summary_applyback.py tests/test_conversation_memory.py tests/test_memory_tools_queue.py tests/test_p2_02_job_queue.py tests/test_g3_compaction.py tests/test_memory_fixes.py -q
uv run ruff check src/hachi/core/memory.py tests/test_compaction_summary_applyback.py
uv run ruff format --check src/hachi/core/memory.py tests/test_compaction_summary_applyback.py
uv run mypy src/hachi/core/memory.py --ignore-missing-imports  # 16 errors before, 16 errors after
```

Results (2026-06-24): 4/4 new tests pass; 231/231 memory+compact+job tests pass;
ruff check + format clean; mypy 16→16 (no regression).

---

### P1-5 — durable fact-extraction

**Found.**
`MemoryManager.sync_turn()` (memory.py ~line 236) enqueued fact-extraction jobs with
`func=extraction_func, args=(combined,)` — a closure over the live `self` (vault, LLM client,
memory_writer). The SQLiteJobQueue `_recover_running()` logic (queue.py ~line 624) explicitly
discards orphaned func-only jobs on restart: any extraction job in-flight at process death is
silently dropped. `handle_extract_fact` in `job_handlers.py` existed as a stub seam and performed
no actual extraction. `JobContext.llm_client` and `JobContext.memory_writer` fields were absent.

**Changed.**
1. **`src/hachi/core/memory.py`** — Added module-level `extract_facts(text, *, vault, llm_client,
   memory_writer=None, session_id="") -> str` containing the shared extraction core (flat JSON-bucket
   path and M1 typed tool-call path). Both `_run_fact_extraction` and `_run_typed_extraction` now
   delegate to it. `sync_turn` with a queue now enqueues `Job(handler_name="extract_fact",
   payload={"session_id": str, "text": str, "typed": bool})` — no `func=`, no closures.

2. **`src/hachi/core/runtime/job_handlers.py`** — `JobContext` gains `llm_client` and
   `memory_writer` fields. `build_job_context` resolves: (a) vault via `get_memory_vault()`,
   (b) LLM client via `memory_vault._get_llm_client()` (the module-level singleton used by the
   vault itself), (c) MemoryWriter via `MemoryWriter(get_hachi_paths().memory_dir)` only when
   `typed=True`. All three accept injected factory callables for test isolation.
   `handle_extract_fact` is now real: resolves context, guards against None vault/client (returns
   `"skipped:..."` — never raises), calls `extract_facts`, catches LLM exceptions (returns
   `"error:..."` — never raises).

3. **`tests/test_durable_fact_extraction.py`** — 11 new tests: real extraction via injected fakes,
   durable enqueue assertion (handler_name not func=), JSON-serializable payload, graceful
   degradation on missing services (5 cases), end-to-end worker dispatch through registry.

4. **`tests/test_memory_tools_queue.py`** — Updated `test_sync_turn_rapid_enqueues_are_processed_once_each`
   to use a `JobRegistry` with an injected fake handler (previously monkey-patched `_run_fact_extraction`
   which the durable path no longer calls).

**How LLM client is resolved in the handler.**
`build_job_context` calls `memory_vault._get_llm_client()` — the same module-level shared
`AnthropicClient` singleton that vault LLM calls already use (MED-022). It is stateless and safe
to call from any worker process with `ANTHROPIC_API_KEY` set. If absent, returns `None` and the
handler returns `"skipped:no_llm_client"` without raising.

**Flush job (`_flush_facts_to_vault`) — deferred.**
`_flush_facts_to_vault` takes `messages: list[Message]` — live Pydantic objects. Serializing these
would require a full transcript schema with tool-use/tool-result block handling. The flush job runs
only from sliding-window compaction (rarely triggered), making the risk/reward unfavorable in this
sprint. It remains on `func=` (ephemeral in-process only) and is a follow-up item. A proper
conversion would introduce a `flush_facts` handler with a serialized transcript payload.

**No double-extraction guarantee.**
`sync_turn` with a queue enqueues exactly one job per turn (the `typed` bool selects the path,
not two separate jobs). Without a queue, extraction is skipped entirely. `_run_fact_extraction`
and `_run_typed_extraction` are only reachable via direct call (never from the queue path). The
handler calls `extract_facts` once inside a single try/except.

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_durable_fact_extraction.py tests/test_memory_tools_queue.py tests/test_p2_02_job_queue.py tests/test_conversation_memory.py tests/test_durable_store.py tests/test_durable_waits.py tests/test_job_queue.py tests/test_memory_fixes.py tests/test_memory_loud_failures.py tests/test_memory_reliability.py -q
uv run ruff check src/hachi/core/memory.py src/hachi/core/runtime/job_handlers.py tests/test_durable_fact_extraction.py tests/test_memory_tools_queue.py
uv run ruff format --check src/hachi/core/memory.py src/hachi/core/runtime/job_handlers.py tests/test_durable_fact_extraction.py tests/test_memory_tools_queue.py
uv run mypy src/hachi/core/memory.py src/hachi/core/runtime/job_handlers.py --ignore-missing-imports
```

Results (2026-06-24): 120/120 tests pass; ruff check + format clean; mypy before=18 errors (16+2),
after=15 (13+2) — net improvement of 3 (refactored methods removed some pre-existing errors). No regression.

---

### P1-9 — backup/restore CLI + Windows CI + GitHub connector

#### Part A — `hachi backup` / `hachi restore` CLI

**Found.** `SessionStore.backup(dest)` existed (core/session_store.py:862) with no callers and no
CLI entry point. The memory vault (ChromaDB at `paths.chroma_dir`) had no backup path at all. Users
had no supported way to protect or recover their session history or memory vault.

**Changed.**
- `src/hachi/cli/main.py`: Added `@app.command("backup")` and `@app.command("restore")`.
  - `hachi backup [--dest <dir>]`: creates `<dest>/hachi-backup-<YYYYMMDD-HHMMSS>/` containing
    (1) `sessions.db` via `SessionStore.backup()` (online hot-backup, safe with a running gateway),
    (2) `chroma_vault/` via `shutil.copytree`, (3) `settings.toml` + `device-permissions.og`.
    `.env` files are **explicitly excluded** (contain secrets) — the output states this clearly.
    Default dest: `<state_dir>/backups/`.
  - `hachi restore <src> [--yes]`: copies `sessions.db` back via `shutil.copy2` and replaces the
    live `chroma_vault/` via `shutil.rmtree` + `copytree`. Requires `--yes` or interactive
    confirmation; refuses cleanly on cancel. Exits 1 with a clear message if src is missing.
- `tests/test_backup_restore.py`: 11 tests covering backup creates files (sessions.db round-trip via
  real SQLite, chroma_vault dir copy, config files, secrets excluded, output mentions exclusion,
  default dest), and restore round-trips both artefacts and refuses without confirmation.

**Why.** The backup command gives users a point-in-time snapshot of session history + memory vault
with zero dependencies (no archive format, no external tools). Secrets are intentionally excluded
because `.env` contains plaintext credentials that belong in a secret manager, not a backup dir.
The restore guard (`--yes`) prevents accidental overwrite of live data.

#### Part B — Windows in CI

**Found.** `.github/workflows/ci.yml` `test` job matrix was `[ubuntu-latest, macos-latest]`.
The primary dev platform (Windows 11) was not covered, so Windows-specific regressions in path
handling, file encoding, or subprocess behaviour would not be caught in CI.

**Changed.**
- `.github/workflows/ci.yml`: Added `windows-latest` to the `test` job matrix. `fail-fast: false`
  was already set so a Windows failure does not mask Linux/macOS results. The `uv sync` and
  `uv run pytest -q` steps are cross-platform and require no shell changes.

**Why.** The Windows encoding fix at main.py boot (`sys.stdout.reconfigure`) and several
Windows-specific path guards already exist; CI should verify they stay working.

#### Part C — phantom GitHub connector removed

**Found.** `connector_registry.py` registered a full GitHub OAuth2 `ConnectorSpec` (id="github")
with authorize/token URLs and env-var mappings, but there is no `src/hachi/integrations/github/`
backend and no GitHub tool. A user who clicked "Connect GitHub" in the UI would complete the OAuth
flow and then have nothing to use it with — a dead UI affordance.

**Decision: REMOVED the registration** (clean removal was straightforward — no deep entanglement).

**Changed.**
- `src/hachi/integrations/connector_registry.py`: Removed the `github` `ConnectorDef` from
  `CONNECTORS` and the `"github"` entry from `OAUTH2_CLIENT_ID_ALIASES`. Updated the module
  docstring table to remove the github rows and added a NOTE explaining the intentional absence
  (add back once `src/hachi/integrations/github/` exists).
- `tests/test_oauth_integrations.py`: Updated `test_all_connectors_have_ids` to assert
  `"github" not in ids` (with explanatory comment) and lowered the minimum connector count from 5
  to 4. Replaced the github-specific `test_status_configured_when_creds_present_but_no_token` test
  with the same logic targeting `notion` (same OAuth2 pattern, actually backed by tools).

**Why.** Clean removal is better than a documented dead affordance: users don't see a "Connect
GitHub" button that leads nowhere, and the code surface is smaller. The secret-scanner tests
(`test_secret_scanner.py`, `test_secret_patterns_extended.py`) that detect GitHub PAT patterns
(`ghp_*`, `github_pat_*`) are unaffected — they test secret detection, not connector registration.

**Frontend note.** `grep -rn "github" frontend/` may reveal a hardcoded GitHub connector tile.
That is a follow-up (frontend change not tackled here — the backend registry is the source of
truth for the integrations list endpoint, so the tile will simply return 404 on connect attempts
until the frontend is updated or GitHub tools are implemented).

**Verify.**
```
uv run pytest tests/test_backup_restore.py tests/test_oauth_integrations.py -q
uv run ruff check src/hachi/cli/main.py src/hachi/integrations/connector_registry.py tests/test_backup_restore.py tests/test_oauth_integrations.py
uv run ruff format --check src/hachi/cli/main.py src/hachi/integrations/connector_registry.py tests/test_backup_restore.py tests/test_oauth_integrations.py
uv run mypy src/hachi/cli/main.py src/hachi/integrations/connector_registry.py --ignore-missing-imports
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml OK')"
```

Results (2026-06-24): 59/59 tests pass; ruff check + format clean; mypy main.py 122 errors
(baseline was already 122 — no regression); connector_registry.py 0 errors; ci.yml YAML valid.

---

### P1-7 — personalization substrate (UserProfileStore + CorrectionLog + save tool)

**Found.**
The `user_profile` ChromaDB collection (bounded to 25 entries via `MemoryVault.upsert_user_fact /
search_user_profile`) provided raw fact storage, but there was NO structured facade exposing
grouped read/write/correct/export semantics. Corrections were handled destructively (delete +
re-write) with no audit trail — a wrong fact silently vanished. The existing `core_save_user_profile`
tool accepted the old flat `{content, category}` list and the old vault category set
(name/role/preference/habit/…) without grouping into the four P1-7 schema buckets
(identity/preferences/routines/knowledge). No onboarding-wizard changes were in scope.

**Changed.**

- **`src/hachi/core/user_profile.py`** — new module; zero additions to existing files:
  - `PROFILE_CATEGORIES = frozenset({"identity","preferences","routines","knowledge"})` — the
    four P1-7 schema buckets.
  - `_resolve_read_category(vault_category)` — maps old vault categories (name→identity,
    preference→preferences, habit→routines, relationship→knowledge, …) so existing entries
    surface correctly under the new schema without a migration.
  - `CorrectionLog` — durable, append-only SQLite store backed by a `profile_corrections` table
    in the existing `sessions.db` file. Schema: `{id, ts, category, old_value, new_value, reason}`.
    There is NO UPDATE or DELETE path on rows — only INSERT. The table survives process restarts
    because it lives in the same WAL-mode SQLite file as the rest of the durable store. Thread
    safety via a reentrant lock.
  - `UserProfileStore` — structured facade over `MemoryVault`'s user_profile collection:
    - `get() -> dict[str, list]` — reads all user_collection entries and groups by P1-7 category.
    - `set(category, value) -> str` — validates category, writes via `_vault._guarded_write` to
      stamp the P1-7 category in metadata (bypassing `upsert_user_fact`'s legacy normalisation
      that would remap unknown categories to "preference").
    - `correct(old_value_or_id, new_value, *, reason="") -> dict` — non-destructive: (1) appends
      a CorrectionLog entry preserving old_value, (2) deletes the superseded fact from the vault,
      (3) writes the replacement. The old value is NEVER silently lost.
    - `export() -> dict` — returns `{profile: grouped, corrections: all log entries, stats: vault
      capacity}`.

- **`src/hachi/tools/memory_tools.py`** — minimal additions only:
  - `register_memory_tools` signature extended with optional `profile_store=None`; auto-builds a
    `UserProfileStore` from the live vault when omitted (backward-compat — existing callers pass
    zero new args).
  - `save_user_profile_structured(category, value) -> str` — new tool handler that calls
    `UserProfileStore.set` and returns a human-readable result string.
  - `core_save_user_profile_structured` registered with the LLM tool schema (category enum:
    identity|preferences|routines|knowledge, value string).

- **`tests/test_user_profile_store.py`** — new test file, 18 tests.

**CorrectionLog backing choice — SQLite table in sessions.db.**
Rationale: (1) SQLite is the existing house pattern for append-heavy audit data
(`interaction_log`, `idempotency_keys` — same file, same approach). (2) Corrections are a
structured log, not a semantic search target — a relational table is a better fit than ChromaDB.
(3) The file already exists on every install; no new dependency or DB file is introduced.
(4) Append-only is enforced by having no UPDATE/DELETE code path — only INSERT.

**Backward compatibility with existing user_profile writes.**
The `category` metadata field written by old code (name/role/preference/habit/…) is preserved
verbatim in ChromaDB; `UserProfileStore.get()` runs every entry through `_resolve_read_category`
at read time — no migration required, no re-write, no risk of data loss. New writes via
`UserProfileStore.set` stamp P1-7 category strings directly (identity/preferences/routines/knowledge).
The vault's existing `upsert_user_fact` and `memory_tools.py:save_user_profile` are untouched
and continue to write the old category strings; they and the new tool coexist without conflict.

**Deferred (out-of-scope follow-ups).**
- Onboarding wizard step / `SetupStep` flow change capturing the four P1-7 buckets during setup.
- Frontend / web UI for displaying and editing the structured profile.
- Implicit learning / PII-minimisation (P2-4 scope).
- Wrapping `memory_exporter.chroma_to_files` to include corrections in the `.og` export
  (profile.og currently exports raw Chroma entries, not the CorrectionLog).

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_user_profile_store.py tests/test_memory_tools_queue.py tests/test_conversation_memory.py -q
uv run ruff check src/hachi/core/user_profile.py src/hachi/tools/memory_tools.py tests/test_user_profile_store.py
uv run ruff format --check src/hachi/core/user_profile.py src/hachi/tools/memory_tools.py tests/test_user_profile_store.py
uv run mypy src/hachi/core/user_profile.py src/hachi/tools/memory_tools.py --ignore-missing-imports
```

---

### P1-1 — document the single-tenant constraint

**Found.**
`MemoryVault` accepts an `account_id` parameter that namespaces ChromaDB collections per account
(ADV-023 — all five collections get a `<account_id>_` prefix and a per-account subdirectory under
`chroma_vault/`). However, `account_id` is **never plumbed** from the authentication layer:
`boot_agent` constructs the vault with no `account_id` argument; the `TraceContext` `principal`
field in `orchestrator.py` is hardcoded to `"session"` (a placeholder). The result is that every
session on the same install shares the same `core_memory`, `user_profile`, `agent_profile`, and
`delegation_log` collections. Under the stated single-operator model this is safe — there is only
one trusted human behind all sessions. But the moment a second user/token/tenant is added, the two
principals would share one memory store → cross-user PII recall and profile contamination.

**Changed (action a only — documentation + comment seams; NO behavioral change).**

- **`README.md`**: Added a prominent "Security model / single-tenant" subsection (before the
  Configuration section) stating that Hachi is a single-operator product, that ChromaDB collections
  are shared per-install, that `account_id` namespacing exists but is unplumbed, and explicitly
  warning against adding a second principal without wiring `account_id` first.
- **`SECURITY.md`**: Added a "Trust model" section (before "Security Architecture Notes") with the
  same constraint, the explicit do-not-add-second-user warning, the two required wiring steps
  (`MemoryVault` constructor + `ProcessSharedServices` fingerprint), and a reference to the vault
  `account_id` seam (ADV-023).
- **`src/hachi/gateway/orchestrator.py`** (line ~1263): Extended the `principal="session"` comment
  with a SINGLE-TENANT block explaining the unplumbed state and what must be done before a second
  principal is added — so a future developer wiring multi-tenant sees the warning at the code.
- **`src/hachi/core/memory_vault.py`** (`__init__`, line ~302): Added a SINGLE-TENANT comment
  block before the `resolved_account` assignment — the exact construction site where `account_id`
  would be wired — with the same warning and cross-references.

**Why.**
The guard rails for the account_id seam exist in the vault but are invisible to a developer who
only reads the boot path. Documentation at the two seams (vault construction + TraceContext) makes
the constraint impossible to miss and prevents an accidental multi-user deployment.

**Action (b) — account_id plumbing — is intentionally DEFERRED** to the 12-month multi-tenant
roadmap track. Wiring requires: (1) authenticating the principal in the gateway request context,
(2) passing it through `boot_agent` → `MemoryVault.__init__`, and (3) updating
`ProcessSharedServices` to fingerprint per-account so each account gets an independent shared-vault
singleton. This is non-trivial and out of scope for the current hardening pass.

**Verify.**
```
uv run ruff check src/hachi/gateway/orchestrator.py src/hachi/core/memory_vault.py
uv run mypy src/hachi/gateway/orchestrator.py src/hachi/core/memory_vault.py --ignore-missing-imports
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_memory_vault.py -q
```

---

### P2-1 — token-based window management

**Found.**
`MemoryManager.prepare_for_turn` and `add_message` triggered sliding-window compaction only when
`len(self._history) > self._max_active_messages` (a message-COUNT check). A handful of very large
messages — e.g. a single 50 000-character tool output — could push estimated token usage well past
the provider context window BEFORE the count limit (30 messages) was reached. This produced a hard
"prompt too long" 400 from Anthropic instead of graceful compaction.

**Changed.**

*Files changed:*
- `src/hachi/core/memory.py` — token-budget implementation
- `tests/test_token_window_management.py` — 12 new tests (all green)

*Changes:*
1. **Module constant** `_MAX_ACTIVE_TOKENS = 24_000` added (line ~383). Rationale: Anthropic
   claude-3-* has a 200 000-token window; system prompt + tool schemas consume ~8 000 tokens;
   24 000 leaves ~168 000 for the in-flight turn — well under the hard limit with generous headroom.

2. **Constructor param** `max_active_tokens: int | None = None` added to `MemoryManager.__init__`.
   Resolution order (highest to lowest priority):
   - Explicit constructor arg
   - `HACHI_MAX_ACTIVE_TOKENS` environment variable
   - Module constant `_MAX_ACTIVE_TOKENS`

3. **Token trigger in `add_message`** (fires BEFORE the count trigger so large messages compact
   early): `if self.estimate_token_count() > self._max_active_tokens: self._sliding_window_compact(); return`

4. **Token trigger in `prepare_for_turn`** — catches histories loaded via `seed_history()` or
   direct `_history` manipulation that bypassed `add_message`. Fires AFTER `apply_pending_compaction`
   and sets `compacted=True` in the returned `IterationState`.

5. **`_sliding_window_compact` updated**:
   - Guard changed from `if len(self._history) <= self._max_active_messages` to
     `if not over_count and not over_tokens: return` (both triggers are checked).
   - Token-triggered runs use `keep_count = max(4, len(self._history) // 2 - 2)` (drop half)
     instead of the standard `max_active_messages - 2` to make a larger dent in one pass.
   - **Bounded post-compaction loop**: after each pass, if tokens are still over budget, `keep_count`
     is halved and the loop retries. The loop is capped at `_MAX_COMPACT_PASSES = 3` iterations;
     after the third pass it logs a WARNING and returns (the caller retries on the next turn). This
     prevents infinite compaction for pathological cases (e.g. a single retained message that is
     itself larger than the budget).
   - Added `-> None` return type annotation (fixes 2 pre-existing mypy `no-untyped-call` errors).

**Why.**
`estimate_token_count()` already existed and was already used for the lossless tool-result-clearing
path (`_TOOL_RESULT_CLEAR_TOKENS`). Reusing it costs a single O(N) pass per `add_message` call —
cheap given N is bounded by `_max_active_messages`. The change is additive and backwards-compatible:
the message-count trigger is kept as secondary defense-in-depth, all existing tool-pair-safety
logic (`_find_safe_sliding_boundary`), the async summary placeholder/apply-back (P1-6), and the
lossless tool-result clearing are fully preserved.

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest \
    tests/test_token_window_management.py \
    tests/test_conversation_memory.py \
    tests/test_compaction_summary_applyback.py \
    tests/test_memory_tools_queue.py \
    tests/test_g3_compaction.py -q
# → 37 passed

uv run ruff check src/hachi/core/memory.py tests/test_token_window_management.py
uv run ruff format --check src/hachi/core/memory.py tests/test_token_window_management.py
# → All checks passed / 2 files already formatted

uv run mypy src/hachi/core/memory.py --ignore-missing-imports
# → 11 errors (baseline was 13 — net improvement of 2)
```


---

### P2-7 — job-queue dead-letter + re-drive

**Found.**
`JobStatus` had no dead-letter state. When a job's retry count was exhausted (the `else` branch in
`JobWorker.execute_one` at ~line 473 of `queue.py`), it was set to `FAILED`. The
`cleanup_completed` method deleted jobs in `(COMPLETED, FAILED, CANCELLED)` after the retention
window, so a retry-exhausted durable job — e.g. a memory fact-extraction during an extended
provider outage — would be silently and permanently lost. Additionally the retry path called
`update_status(PENDING)` which for SQLiteJobQueue only updated the `status` column, NOT `attempt`
or `scheduled_for`, so the attempt counter in the DB never incremented and the job looped
forever in "attempt 1 < max_retries" without ever reaching the exhausted branch.

**Changed.**

*`src/hachi/jobs/queue.py`*
- Added `DEAD_LETTER = "dead_letter"` to `JobStatus` — semantically: retry-exhausted but handler
  exists; re-driveable.
- Retry path (`execute_one`): changed `update_status(PENDING)` call to `update_attempt()` for
  SQLiteJobQueue (falls back to `update_status` for InMemoryJobQueue). This persists `attempt` and
  `scheduled_for` to the DB so the counter actually increments between reads.
- Dead-letter routing: the retry-exhausted `else` branch (line ~473, `job.attempt >= max_retries`,
  handler exists) now sets `result.status = DEAD_LETTER` and emits `"job.dead_letter"` event
  instead of `FAILED`. Structurally-unexecutable path (line ~369, no callable and no handler in
  registry) stays terminal `FAILED` — retrying would just hit the same wall.
- `cleanup_completed` (both `InMemoryJobQueue` and `SQLiteJobQueue`): now only deletes
  `COMPLETED`, `FAILED`, and `CANCELLED`. `DEAD_LETTER` is explicitly preserved.
- `redrive_dead_letter() -> int`: added to both implementations and the abstract base `JobQueue`.
  Resets every `DEAD_LETTER` job to `PENDING` with `attempt=0`, clears `completed_at` and result
  columns, returns the count. SQLite implementation does a single `UPDATE … WHERE status =
  'dead_letter'` so it's atomic and durable.
- `update_status` in `SQLiteJobQueue`: clears `_func_table` entry on `DEAD_LETTER` (same as
  `FAILED`/`COMPLETED`/`CANCELLED`).

*`src/hachi/core/runtime_read_models.py`*
- `RuntimeReadModelStore.consume_event`: added `"job.dead_letter"` alongside `"job.failed"` in
  the failure-projection branch so dead-lettered jobs appear in `failed_jobs()` / the web UI
  "Needs attention" panel.

*`src/hachi/cli/commands/jobs.py`* (new file)
- `hachi jobs dead-letter`: lists current dead-letter jobs (table or JSON).
- `hachi jobs redrive`: calls `queue.redrive_dead_letter()`, prints count.
- Opens the production `SQLiteJobQueue` via a bare `SQLiteJobQueue()` (no explicit `db_path`),
  which resolves through `_default_db_path()` → `get_hachi_paths().jobs_db` (T-53 / BB-131).
  Previously this duplicated an inline `PlatformDirs("hachi", "opengigantic")` lookup — a
  second on-disk root invisible to every `get_hachi_paths()`-based tool; see `docs/bug-bank.md`.

*`src/hachi/cli/main.py`*
- Imports and registers `jobs_app` under `"jobs"`.

*`tests/test_job_queue.py`*
- Updated `test_failed_job_reaches_read_model_projection`: event name is now `"job.dead_letter"`
  (max_retries=1, handler exists → dead-letter, not terminal FAILED).

**Why.**
A retry-exhausted job represents a transient/resolvable failure (e.g. provider outage, ChromaDB
unavailable). Silently deleting it after cleanup discards durable work with no recovery path.
Dead-lettering preserves the job + its payload/error until an operator explicitly re-drives it
(`hachi jobs redrive`) or until the underlying problem is fixed. Structurally-unexecutable jobs
(no handler registered) remain terminal FAILED because re-driving them would just fail again.

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest \
    tests/test_job_dead_letter.py tests/test_p2_02_job_queue.py tests/test_job_queue.py -q
# → 41 passed

uv run ruff check src/hachi/jobs/queue.py src/hachi/cli/main.py \
    src/hachi/cli/commands/jobs.py src/hachi/core/runtime_read_models.py \
    tests/test_job_dead_letter.py tests/test_job_queue.py
# → All checks passed

uv run mypy src/hachi/jobs/queue.py --ignore-missing-imports
# → 3 errors (unchanged from baseline)

uv run mypy src/hachi/cli/main.py --ignore-missing-imports
# → 122 errors (unchanged from baseline)
```

---

### P2-5 — Behavioral tests for brain.og hard constraints

**Found.**
`tests/test_prompt_composer.py` only asserted structural shape (ordering, section headers,
which fragments appear in cacheable vs dynamic split) — it used a hardcoded `_BRAIN =
"STABLE_BRAIN_CONTENT"` fixture and never touched the shipped `brain.og`.  Deleting any
constraint line from `src/hachi/default_skills/brain.og` would not fail any test.

**Changed.**
Added `tests/test_brain_constraints.py` (11 new tests; 0 source file changes).

Two test layers:

*Source-level (7 tests)* — read the raw shipped brain.og and assert each guardrail is
present as text.  These fail the moment the line is deleted from the source.

*Composition-level (4 tests)* — render brain.og through the full production pipeline
(`parse_og_file` → `_render_brain_prompt` → `build_stable_base`) and assert each
constraint survives into the composed prompt string.  A composer bug that silently drops
a node type (e.g. skipping all `::warning` blocks) would be caught here even if the
source is intact.

Invariants guarded:

| Test | brain.og guardrail | Why it matters |
|---|---|---|
| `test_brain_og_no_hallucination_rule_present` + `test_no_hallucination_rule_survives_composition` | `::warning` in `execution-principles`: "NEVER hallucinate tool outputs" | Core honesty constraint — without it the agent may fabricate tool results |
| `test_brain_og_no_claim_without_tool_use_rule_present` | `::warning`: "NEVER claim you completed a task without actually using tools" | Grounding rule — prevents false success confirmations |
| `test_brain_og_destructive_command_confirmation_rule_present` + `test_destructive_command_rule_survives_composition` | `tool-philosophy::full`: "Never run destructive commands without explicit user confirmation" | Irreversible-action safeguard — rm -rf / format protection |
| `test_brain_og_execution_engine_identity_present` + `test_execution_engine_identity_survives_composition` | `identity::full`: "You are NOT a chatbot. You are an execution engine" | Identity anchor — prevents the agent drifting to passive advice-giving |
| `test_brain_og_skill_search_first_rule_present` | `tool-philosophy` + `skill-routing::full`: "ALWAYS check for relevant skills before improvising" | Core .og moat — agent must load skill blueprints before improvising |
| `test_brain_og_warning_blocks_are_hard_constraints_rule_present` | `skill-routing::full`: "::warning blocks, treat them as hard constraints — never violate them" | Meta-rule — makes all other warnings mandatory rather than advisory |
| `test_hard_constraints_section_present_in_composed_prompt` | `--- HARD CONSTRAINTS ---` header emitted by `_render_brain_prompt` Layer 2 | Guards the renderer's warning-aggregation loop specifically |

Matching strategy: case-insensitive substring search on a few distinctive keywords per
constraint (e.g. `"hallucinate" + "tool"`), not exact full-line matches, so trivial
rewording does not produce false failures while deletion still causes failure.

**Why.**
The audit (P2-5) identified that CI had no behavioral coverage of safety guardrails.  A
refactor or brand-rename PR that accidentally truncated a node, or a composer change that
dropped the warning-block rendering layer, would ship silently.

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest \
    tests/test_brain_constraints.py tests/test_prompt_composer.py -q
# → 26 passed

uv run ruff check tests/test_brain_constraints.py
# → All checks passed

uv run ruff format --check tests/test_brain_constraints.py
# → 1 file already formatted
```

---

### P2-11 — deep readiness probe + compose healthcheck

**Found.**
`_gateway_readiness_probe()` in `src/hachi/cli/main.py` (line ~2028) only checked that
two files exist (`global_config_toml`, `permissions_file`).  A gateway with no provider
API key, an unwritable session-store directory, or a dead job-worker thread still
reported `"ready": true`.  `docker-compose.yml` had no `healthcheck` for the `hachi`
service — Docker could not distinguish a broken gateway from a healthy one.

**Changed.**

| File | Change |
|------|--------|
| `src/hachi/cli/main.py` | `_gateway_readiness_probe` now returns `(bool, list[str], dict[str, str])` with five checks. Added `_GATEWAY_RESIDENT_WORKER` module-level slot; `_InProcessGatewayRuntime._boot_session_agent` populates it after agent boot so the probe can inspect worker liveness without coupling to the runtime instance. Added `ResidentWorkerThread` import. |
| `src/hachi/gateway/control_plane.py` | `readiness()` unpacks both 2-tuple (legacy) and 3-tuple probes; adds `checks` key to the returned dict. Constructor type annotation updated. |
| `src/hachi/gateway/fastapi_app.py` | `ReadinessResponse` Pydantic model gains `checks: dict[str, str]` field (default `{}`). |
| `docker-compose.yml` | Added `healthcheck` to the `hachi` service (30 s interval, 10 s timeout, 3 retries, 20 s start_period, `curl --fail http://localhost:8787/readiness`). `frontend.depends_on` now waits for `service_healthy`. |
| `tests/test_readiness_probe.py` | 12 new tests covering all five checks and the HTTP route. |

**Readiness checks performed** (all fast + side-effect-free; no live API calls):

1. `config_file` — global `settings.toml` exists.
2. `permissions_file` — `device-permissions.og` exists.
3. `provider_configured` — API key resolvable from env / keyring for the configured
   provider (skipped for keyless providers such as Ollama).
4. `session_store_writable` — `mkstemp` + immediate unlink in the sessions-DB parent
   dir confirms the directory is writable without persisting anything.
5. `job_worker` — if a `ResidentWorkerThread` has been registered, it must be alive
   (`.is_running` ↔ `thread.is_alive()`).  Pre-session (not yet registered) is
   reported as `"not started"` and is **not** a blocker.

**Structured result shape** (`GET /readiness`):

```json
{
  "status": "ready | not_ready",
  "ready": true,
  "blockers": [],
  "checks": {
    "config_file": "ok",
    "permissions_file": "ok",
    "provider_configured": "ok (ANTHROPIC_API_KEY)",
    "session_store_writable": "ok",
    "job_worker": "not started"
  },
  "active_connections": 0,
  "time": "2026-06-24T…"
}
```

HTTP 200 when ready; HTTP 503 when `ready` is false.  The endpoint is **unauthenticated**
so Docker / load-balancer probes do not need a bearer token.  No secrets are returned —
values are booleans or short human-readable reasons (no key material).

**Docker-compose healthcheck config:**

```yaml
healthcheck:
  test: ["CMD", "python", "-c",
         "import urllib.request; urllib.request.urlopen('http://localhost:8787/readiness', timeout=5)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 20s
```

**Why.**
AUDIT P2-11 identified that the readiness probe was cosmetic (file-exists only).  A
broken provider key or a crashed job-worker would silently report "ready", causing
clients and the Docker scheduler to route traffic to a non-functional gateway.  The
compose healthcheck ensures Docker marks the container `unhealthy` and prevents
`frontend` from starting before the gateway is genuinely ready.

The healthcheck originally used `curl`, but `python:3.12-slim` does not include `curl`
(the Dockerfile runs only `pip install` — no `apt-get install curl`).  Using `curl`
would cause the healthcheck to always exit with "curl: not found", permanently marking
the container unhealthy and preventing the `frontend` service (which has
`depends_on: hachi: condition: service_healthy`) from ever starting.  The replacement
uses `urllib.request.urlopen` from Python's stdlib: it raises on non-2xx responses
(including the 503 the readiness route returns when not ready) and on
connection-refused during startup, so the exit code semantics are identical to
`curl --fail`.

**Verify.**
```
cd /d/OpenGigantic/hachi
PYTHONPATH=src uv run --with pytest --no-project pytest \
    tests/test_readiness_probe.py tests/test_gateway_control_plane.py \
    tests/test_cli_gateway_command.py -q

uv run ruff check src/hachi/cli/main.py src/hachi/gateway/fastapi_app.py \
    src/hachi/gateway/control_plane.py tests/test_readiness_probe.py
uv run ruff format --check src/hachi/cli/main.py src/hachi/gateway/fastapi_app.py \
    src/hachi/gateway/control_plane.py tests/test_readiness_probe.py
uv run mypy src/hachi/gateway/fastapi_app.py src/hachi/cli/main.py \
    --ignore-missing-imports  # fastapi_app MUST be 0; cli/main stays at 122
python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('compose valid')"
```

### P2-10 — cold-start resume

**Found.**
`resume.py` `_do_resume()` tried `GatewayControlPlane.instance().get_session(session_id)`
— but `GatewayControlPlane` has no `instance()` singleton method and no `get_session()`
method, so the code NEVER reached the live-session path and always fell through to
`HonestResumeError("Replay not implemented for cold-start resume (slice 2.4 scope)")`.
A WAITING run woken after a gateway restart therefore always raised, even though all
the rehydration machinery (`resume_from_session`, `boot_agent(resume_session_id=…)`,
`_boot_session_agent`) was already present and functional.

**Changed.**

| File | Change |
|------|--------|
| `src/hachi/cli/main.py` | Added `_ACTIVE_RUNTIME: _InProcessGatewayRuntime \| None = None` module-level slot (alongside `_GATEWAY_RESIDENT_WORKER`). `_start_gateway_server` and the `gateway` command set it when a runtime is created. Added `rehydrate_session(session_id) → AgentSession \| None` method to `_InProcessGatewayRuntime`: boots a fresh agent via the REAL `_boot_session_agent(session_id, resume_session_id=session_id)` path and registers it in `_agents`. |
| `src/hachi/core/runtime/resume.py` | Rewrote `_do_resume`. Now reads `_ACTIVE_RUNTIME` from `hachi.cli.main` via `getattr()` at call time (so monkeypatching works). Hot path: session is in `_agents` → `agent.chat(inject_text)` directly. Cold path: session absent → `runtime.rehydrate_session(session_id)` → chat on the rehydrated agent. No-runtime path: `_ACTIVE_RUNTIME is None` → `HonestResumeError` (honest fail preserved). |
| `tests/test_cold_start_resume.py` | 5 new tests (see below). |

**Rehydration wire (how it connects to existing machinery).**
`resume.py._do_resume` calls `runtime.rehydrate_session(session_id)` in
`src/hachi/cli/main.py` (line ~329), which calls
`self._boot_session_agent(session_id, resume_session_id=session_id)` (line ~266),
which calls `boot_agent(resume_session_id=session_id)` in
`src/hachi/gateway/orchestrator.py` (~line 2128).  `boot_agent` creates an
`AgentSession`, then `AgentSession.resume_from_session(source_session_id, max_messages=40)`
(~line 571) loads the SQLite transcript via `SessionStore.get_session_messages` and
seeds it into the `MemoryManager`.  This is the SAME path used by
`_start_gateway_server(resume_session_id=…)` → `_pending_resume_session_id` →
`register_connection(resume_session_id=…)` for normal session-resume-on-connect.

**How the tests prove rehydration is REAL (not faked).**
The key assertion in `TestColdStartResume.test_cold_start_rehydrates_and_resumes_turn` is:
```python
assert agent.seen_resume_session_id == prior_sid
```
The `_fake_boot_agent` captures the `resume_session_id` argument that `boot_agent` was
called with.  `_ScriptedAgent.replayed_messages` is populated by calling
`session_store.get_session_messages(resume_session_id, …)` on the REAL store — so the
test also verifies that two transcript messages written before the run was created are
returned in order.  A fake session that bypassed `boot_agent` would have
`seen_resume_session_id=None` and `replayed_messages=[]`, failing both assertions.

**What still HonestResumeError-s.**
When `_ACTIVE_RUNTIME is None` (standalone `hachi run` CLI, `uv run hachi` without
`--runtime`, or any context where the gateway was never started), `_do_resume` raises
`HonestResumeError("_ACTIVE_RUNTIME is None …")`. Tested by
`TestNoRuntimeHonestFail.test_raises_honest_resume_error_when_no_runtime`. The run is
rolled back to WAITING (not FAILED) so the operator can wake it again after starting
the gateway.

**Idempotency preservation.**
`resume_run` guards `WAITING→RUNNING` at the top; all other statuses (RUNNING,
COMPLETED, CANCELLED, FAILED) return `"no-op: status is …"` immediately without
calling `_do_resume`. Tested by `TestIdempotency.test_double_wake_is_noop_for_completed`
and `test_double_wake_is_noop_for_running`.

**Verify.**
```
cd /d/OpenGigantic/hachi
PYTHONPATH=src uv run --with pytest --no-project pytest \
    tests/test_cold_start_resume.py tests/test_durable_store.py \
    tests/test_durable_waits.py tests/test_suspend_workflow.py \
    tests/test_gateway_runtime_validation.py -q

uv run ruff check src/hachi/core/runtime/resume.py src/hachi/cli/main.py \
    tests/test_cold_start_resume.py
uv run ruff format --check src/hachi/core/runtime/resume.py \
    src/hachi/cli/main.py tests/test_cold_start_resume.py
uv run mypy src/hachi/core/runtime/resume.py src/hachi/gateway/orchestrator.py \
    src/hachi/cli/main.py src/hachi/gateway/control_plane.py \
    --ignore-missing-imports
# resume.py/control_plane.py MUST be 0 errors; orchestrator stays at 34; cli/main stays at 122
```

---

### P2-10b — direct test for resume_from_session (real replay proof)

**Found:** `AgentSession.resume_from_session` (orchestrator.py ~571) had zero test coverage.
Searching `tests/` for `resume_from_session` returned no matches.  The method is the
substance of cold-start resume — it reads a prior session's transcript from SQLite,
filters to user/assistant rows, seeds them into `MemoryManager`, and caches the pairs
in `_resumed_transcript` for the UI.  Despite its criticality, no test ever called it
directly; the only adjacent coverage was `test_cold_start_resume.py` which stubs out
`boot_agent` entirely and never exercises the replay internals.

**Changed:** Added `tests/test_resume_from_session.py` — 18 tests across 6 classes,
no mocks on the memory or store path.

**Why / design choices:**

- Construction pattern copied verbatim from `tests/test_interaction_log.py` (line 121):
  `sess = AgentSession.__new__(AgentSession)` then set only `_session_store`, `memory`,
  and `_resumed_transcript`.  This avoids boot_agent and its 30+ required dependencies
  while still exercising the production code path.

- `MemoryManager()` is constructed with no arguments (bare defaults) — sufficient for
  `seed_history`, which only appends to `_history` and sets `_last_user_query`.

- Real `SessionStore(db_path=str(tmp_path / "…"))` with `start_session()` + `flush_sync()`
  before every read — matches the pattern already established in the session_store tests.

- History inspection via `mm.history` (the public `@property` at memory.py ~629) which
  returns `self._history` — the exact list extended by `seed_history`.

- `seed_history` nuances accommodated:
  1. It uses `extend()`, not prepend — so pre-existing history comes BEFORE resumed
     messages (locked in by `TestSeedHistoryNuances::test_seed_history_appends_not_prepends`).
  2. It scans reversed to set `_last_user_query` to the most-recent user content
     (locked in by `test_last_user_query_updated_after_resume`).
  3. Returns `len(messages)` so the count == number of user+assistant rows passed.

- `get_session_messages` returns oldest-first within its `limit=` cap, and the cap
  selects the N most-recent rows from the DB — so `max_messages=2` on a 4-row session
  yields the final pair (locked in by `TestMaxMessagesCap::test_max_messages_pulls_most_recent`).

**Verify:**
```
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_resume_from_session.py -q
# 18 passed in 4.43s

uv run ruff check tests/test_resume_from_session.py
# All checks passed!

uv run ruff format --check tests/test_resume_from_session.py
# 1 file already formatted
```

**Bug found:** None.  The implementation matches its documented contract exactly.
The empty-content guard (`content = row.get("content") or ""` + `not content` check)
correctly drops both None and `""` content; the tool-row filter is exact-set membership.

---

### P2-13 — perf/latency regression guard (recall + compaction)

**Found.** No CI guard existed to catch unbounded/excessive work in the two hot paths:
(1) `MemoryManager._sliding_window_compact` — the post-compaction tightening loop was
bounded at `_MAX_COMPACT_PASSES=3` in code but never covered by a regression test asserting
on the call-count bound; (2) `MemoryVault._query_collection` — always called with a bounded
`n_results=top_k` but no test verified the argument reached the ChromaDB collection; (3) lazy
ChromaDB init (`_chroma_client=None` after `__init__`) was covered in `test_g3_vault_lazy.py`
but not expressed as an explicit perf-regression assertion in a dedicated file.

**Changed.** New file `tests/test_perf_regression.py` — 13 deterministic structural tests, zero
wall-clock assertions (no flaky CI thresholds). Four test classes:

- **`TestCompactionPassBound`** (2 tests): spies on `_generate_structured_summary` via a call-
  through wrapper and asserts `call_count <= 3` even when `max_active_tokens=1` (every pass still
  over budget). Seam: `_MAX_COMPACT_PASSES = 3` local constant at `memory.py:1013`,
  `for _pass in range(_MAX_COMPACT_PASSES):` at `memory.py:1029`.

- **`TestCompactionPerTurnWorkBounded`** (2 tests): spies on `_sliding_window_compact` itself
  and asserts (a) adding one overflow message triggers at most one compaction call, and (b) 20
  sequential `add_message` calls trigger at most 20 compaction calls total (linear, not O(N²)).
  Seam: `add_message()` at `memory.py:638`, compaction trigger at `memory.py:659-665`.

- **`TestHistoryBoundedAfterManyAdds`** (2 tests): after 100 `add_message` calls, token
  estimate stays under `2 × budget` (generous tolerance for the post-append measurement window)
  and message count stays within `max_active_messages + 4`. Locks in the "cliff removed"
  behavior. No spy needed — asserts directly on `estimate_token_count()` and `len(_history)`.

- **`TestRecallBoundedTopK`** (4 tests): uses `_FakeCollection.query_calls` (recording spy
  baked into the fake) to assert `n_results == top_k` for `semantic_search(top_k=5)`,
  `search_user_profile(top_k=10)`, and a custom `top_k=3` call. Seam:
  `collection.query(n_results=top_k, ...)` at `memory_vault.py:774`. A fourth test asserts
  `n_results` is never `None` for any recorded call.

- **`TestVaultLazyInit`** (3 tests): asserts `_chroma_client is None` after `__init__`,
  all `_collections` slots are `None` after `__init__`, and `PersistentClient` is never called
  during `__init__`. Uses `patch.dict(sys.modules, {"chromadb": mock_chroma, ...})` — same
  pattern as `test_g3_vault_lazy.py`. Seam: `self._chroma_client: Any = None` at
  `memory_vault.py:335` and `_collections` dict at `memory_vault.py:339`.

**Why structural, not wall-clock.**
Wall-clock threshold tests are muted or skipped in CI within weeks (flaky on runner variance).
Structural assertions catch the same regressions: if someone removes the `_MAX_COMPACT_PASSES`
bound, `call_count <= 3` fails; if `query` is called without `n_results`, the recording spy
catches it; if `__init__` starts eagerly opening ChromaDB, `_chroma_client is None` fails.

**Spy technique.** Compaction tests wrap the REAL method via a nonlocal counter closure and
reassign `mm._generate_structured_summary = _counting_generate`, then call through
to `original_generate(messages)` so behavior is unchanged and the assertion reflects real
execution — not a reimplementation. Recall tests use a `_FakeCollection` that accumulates
`query_calls` on every `.query()` invocation, which pre-populates the stubbed vault's
`_collections` dict (same technique as `test_memory_fixes.py`).

**ChromaDB dependency.** Zero tests in this file require a real ChromaDB installation.
All vault tests use `MemoryVault.__new__` + pre-populated `_collections` dict. The lazy-init
tests use `patch.dict(sys.modules)` to inject a `MagicMock` chromadb module.

**Real perf bug found.** None. All three guards validated as already correct in source.

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_perf_regression.py -q
# 13 passed in 3.67s

PYTHONPATH=src uv run --with pytest --no-project pytest \
    tests/test_token_window_management.py tests/test_compaction_summary_applyback.py -q
# 16 passed — no shared-state collision

uv run ruff check tests/test_perf_regression.py   # All checks passed!
uv run ruff format --check tests/test_perf_regression.py  # 1 file already formatted
```

---

### Eval loop E1 — interaction read/rating API

**Found.** `SessionStore` already had `get_interactions()` and `set_interaction_rating()` (written as the P0-2 eval plumbing), but no gateway routes exposed them to the frontend or eval tooling. The `/review` UI and any trajectory-eval harness had no way to read rows or post ratings without going directly to SQLite.

**Changed.**
- `src/hachi/gateway/fastapi_app.py` — four new Pydantic models (`InteractionRecord`, `InteractionListResponse`, `RatingRequest`, `RatingResponse`) added above the `_lifespan` function (line ~340 area). New `interactions_router` (prefix `/interactions`, `protected_dependencies`) with two routes:
  - `GET /interactions` — query params `session_id` (optional) and `limit` (1–500, default 100). If `session_id` supplied: `_check_session_ownership` then `store.get_interactions(session_id=..., limit=...)`. Otherwise: fetch all rows then filter to sessions owned by the caller (mirrors `list_sessions`' `_owned` helper); no-auth returns everything. 503 if the store is unavailable.
  - `PUT /interactions/{interaction_id}/rating` — body `{rating: "up"|"down", notes?: str}`. Looks up the row's `session_id` first (via `get_interactions` scan), ownership-checks that session, then calls `store.set_interaction_rating`. Returns 404 if no row matched; 422 if `rating` is not `"up"` or `"down"` (Pydantic `Literal`).
  - `app.include_router(interactions_router)` appended to the existing `include_router` block (~line 2507).
- `tests/test_interactions_api.py` — 23 new tests across 5 classes; all use real `SessionStore(db_path=tmp_path)` seeded via `log_interaction`, with `patch("hachi.gateway.fastapi_app._resolve_session_store", ...)` to inject the isolated DB.
- `openapi/hachi-openapi.json` — regenerated; now contains `/interactions` and `/interactions/{interaction_id}/rating`.

**Why.** The write side (`log_interaction`) was complete but dark. This adds the read surface needed for the `/review` UI and eval tooling without any write-side coupling. Ownership gating mirrors the existing `list_sessions` / `get_session` pattern so no new security surface is introduced. `Literal["up","down"]` at the API layer constrains the free-text store column to a meaningful vocabulary without changing the store schema.

**Verify.**
```
uv run pytest tests/test_interactions_api.py -q
# 23 passed

uv run ruff check src/hachi/gateway/fastapi_app.py tests/test_interactions_api.py
# All checks passed!

uv run python -m mypy --config-file mypy-strict-gate.ini
# Success: no issues found in 36 source files

uv run hachi gateway-openapi --output openapi/hachi-openapi.json
# then: python -c "import json; data=json.load(open('openapi/hachi-openapi.json')); ..."
# ['/interactions', '/interactions/{interaction_id}/rating']
```

---

### Eval loop E2 — eval report + regression runner

**Found.** The interaction_log was populated (P0-2) and exposed via the HTTP API (E1), but there was no in-process analytics layer and no structural regression harness. Failure patterns (down-rated turns, tool names, per-model costs) had to be queried manually; there was no way to run a named regression case through the real agent loop and assert structural properties of the trajectory.

**Changed.**

- `src/hachi/core/eval/__init__.py` — package init, re-exports public API.
- `src/hachi/core/eval/report.py` — **REAL analytics; pure function, no I/O.** `build_failure_report(interactions, *, since) -> EvalReport` aggregates over `interaction_log` rows. `EvalReport` fields: `total_turns`, `rated_up`, `rated_down`, `unrated`, `failure_rate` (`down / (up+down)`, 0.0 when no rated turns), `avg_cost_per_turn` (excludes None-cost rows from denominator), `total_cost`, `errored_turns` (output empty/whitespace OR starts with `[error]`/`error:`/`exception:`/`traceback`/`[incomplete]`), `top_failing_tools` (tool→count from down-rated turns only, sorted desc), `per_model` (model→`PerModelStats{count,avg_cost,down_count}`), `recent_failures` (last 10 down-rated turns). Optional `since` ISO filter applied before all aggregation.
- `src/hachi/core/eval/runner.py` — **REAL execution; dependency-injected turn_fn.** Four assertion kinds: `ToolCalledAssertion`, `OutputContainsAssertion`, `NoErrorAssertion`, `MaxCostAssertion`. `run_case(case, turn_fn=...)` calls the injected `turn_fn` (real `ExecutionLoop` in tests, real `AgentSession` in production), evaluates all assertions against the real `TurnTrajectory`, and sets `passed=True` only when the failures list is empty — never hardcoded. `DEFAULT_SUITE` has 2 seed cases; docstring is explicit this is a starting scaffold.
- `src/hachi/cli/commands/eval.py` — `eval_app` Typer sub-app with two commands:
  - `hachi eval report [--json] [--since ISO] [--session SID] [--limit N]` — opens the real SessionStore (same path as `hachi session`), calls `get_interactions`, calls `build_failure_report`, renders a Rich table or JSON. Fully functional.
  - `hachi eval run [--suite PATH]` — honestly reports "not wired" rather than faking a pass (see "Why" below). Suite loading (`.py` files) is scaffolded; JSON format stub present.
- `src/hachi/cli/main.py` — one line added: `app.add_typer(eval_app, name="eval")`.
- `tests/test_eval_report.py` — 26 tests; pin exact aggregation math: rating counts, failure_rate fractions, cost sum/avg, errored_turns rule (empty + prefix), top_failing_tools counts and sort order, per_model stats, since filter inclusion/exclusion, recent_failures cap at 10.
- `tests/test_eval_runner.py` — 12 tests including the **anti-fake proof**: `test_unsatisfiable_assertion_fails_with_reason` passes `output_contains("NEVER_IN_OUTPUT")` against a scripted ExecutionLoop that returns "pong" — asserts `passed=False` with the needle in the failure reason. Any runner that hardcodes pass would break this test.

**Why.**

*Report*: analytics must be a pure function so it is testable without a live database; seeding 38 tests against known rows pins the real math.

*Runner*: dependency-injecting `turn_fn` keeps the runner framework-agnostic and makes the anti-fake guarantee testable without a live API key. The scripted `ExecutionLoop` in the test is the same class that runs in production — not a mock — so the test covers real assertion logic against real loop output.

*`eval run` not wired*: wiring `boot_agent` in the CLI command would require workspace init, LLM credentials, and skills loading — too fragile for a first-pass CLI and not guaranteed in CI. The runner itself is fully exercised in tests. When a real turn_fn is available, wire it in `eval.py` with `from hachi.gateway.orchestrator import boot_agent`.

*`errored_turns` rule*: conservative prefix list catches the three real failure modes (empty reply, crash traces, iteration-limit `[INCOMPLETE]`) without over-flagging short valid responses like "No." The rule is documented in the module docstring and tested.

**Verify.**
```
PYTHONPATH=src uv run --with pytest --no-project pytest tests/test_eval_report.py tests/test_eval_runner.py -q
# 38 passed in 4.68s

uv run ruff check src/hachi/core/eval/__init__.py src/hachi/core/eval/report.py \
    src/hachi/core/eval/runner.py src/hachi/cli/commands/eval.py \
    tests/test_eval_report.py tests/test_eval_runner.py
# All checks passed!

uv run mypy src/hachi/core/eval src/hachi/cli/commands/eval.py --ignore-missing-imports
# Success: no issues found in 4 source files

PYTHONPATH=src uv run --no-project python -c "from hachi.cli.main import app; app()" eval report --json
# {"total_turns": 25, "rated_up": 0, "rated_down": 0, "unrated": 25, "failure_rate": 0.0,
#  "avg_cost_per_turn": 0.0, "total_cost": 0.0, "errored_turns": 0,
#  "top_failing_tools": [], "per_model": {"gpt-4o-mini": {"count": 25, ...}},
#  "recent_failures": [], "since": null}
```

---

### Eval loop E3 — /review frontend screen

**Found.** The gateway already exposes `GET /interactions` and `PUT /interactions/{id}/rating` (committed in the P0 sweep on `reassess`), but the frontend had no screen to display or rate turns. The committed `schema.d.ts` was also stale — it predated these endpoints being added to `openapi/hachi-openapi.json`, so CI `frontend-contract` would have failed on next push.

**Changed.**

| File | Action |
|------|--------|
| `frontend/src/lib/data/schema.d.ts` | Re-generated via `npm run generate:api` — now includes `InteractionListResponse`, `InteractionRecord`, `RatingRequest`, `RatingResponse` |
| `frontend/src/lib/data/types.ts` | Added `AgentInteraction` UI type (`tool_calls`/`memory_hits` as `string[]`, not objects) |
| `frontend/src/lib/store/store.ts` | Added `interactions: AgentInteraction[]` to `State` interface, import, and `domainDefaults()` (defaults to `[]` in live mode) |
| `frontend/src/lib/data/domains/interactions.ts` | NEW — 3-layer domain loader: gateway shapes → `adapt()` → store `hydrate`. `loadInteractions()` GETs `/interactions`, `rateInteraction()` PUTs `/interactions/{id}/rating` then re-fetches. `_hasEverSucceeded` honesty pattern: first live failure clears to `[]` so fixture data is never shown as real. |
| `frontend/src/lib/data/domains/interactions.test.ts` | NEW — 27 vitest tests covering adapter mapping, rating normalization, timestamp humanization, null/undefined defensive coalescing, and RatingRequest body shape |
| `frontend/src/lib/data/useRouteLoaders.ts` | Added `/review` branch lazy-importing `loadInteractions()` |
| `frontend/src/routes/review.tsx` | NEW — `/review` route component: header bar + scrollable card list; each card shows user_input, agent_output, tool_calls/memory_hits as string chips, model/cost metadata, thumbs-up/down buttons, and a collapsible notes textarea. Calls `rateInteraction()` which round-trips to the gateway and re-fetches. |
| `frontend/src/components/shell/Sidebar.tsx` | Added `{ key:'review', route:'/review', label:'Review', icon:'eye' }` to `NAV_ITEMS` |
| `frontend/src/routeTree.gen.ts` | Updated to register `/review` route (normally auto-generated by Vite plugin on `npm run dev`; done manually here to unblock `tsc`) |

**Why.** The 3-layer pattern (gateway shapes → adapter → UI types → `hydrate`) is the established contract for all domain loaders. Following it exactly ensures the screen uses only authoritative gateway data in live mode and degrades gracefully to empty on failure. `rateInteraction` re-fetches after PUT so the rating shown always reflects server truth, not local optimism.

**Verify.**
```
cd frontend
npm run generate:api   # ✓ schema.d.ts updated (was stale — interactions endpoints missing)
npm run typecheck      # ✓ 0 errors
npm run test           # ✓ 82 tests pass (6 test files; +27 new from interactions.test.ts)
npm run check:api      # ✓ clean after schema.d.ts committed (diff is the previously-stale schema)
```

**Note on `check:api`:** The diff shown by `check:api` is the interactions endpoints being added to `schema.d.ts` — these were missing from the committed file because the prior P0 sweep added the gateway endpoints but didn't regenerate the schema. Committing the updated `schema.d.ts` makes `check:api` clean.

---

### E4 — Wire `hachi eval run` via record/replay corpus (2026-06-24)

**Gap.** `hachi eval run` previously printed "not wired in this release" and exited 0 unconditionally. The gap was honest but meant the command could never serve as a CI gate.

**What was built.**

| File | Role |
|------|------|
| `src/hachi/core/eval/replay.py` (NEW) | Production replay harness: `ScriptedLLMClient`, `ToolSpec`, `ReplayCase`, `make_replay_turn_fn`, `run_replay_suite` |
| `src/hachi/core/eval/corpus.py` (NEW) | Curated seed corpus: `DEFAULT_REPLAY_SUITE` with 5 cases (plain text, single-tool, two-tool trajectory, multi-turn smoke, output_contains smoke) |
| `src/hachi/cli/commands/eval.py` (EDITED) | `eval run` command rewired: loads `DEFAULT_REPLAY_SUITE`, calls `run_replay_suite`, prints a Rich scorecard, exits 1 on any failure |
| `tests/test_eval_replay.py` (NEW) | 20 tests covering `ScriptedLLMClient`, `make_replay_turn_fn`, `run_replay_suite`, the full corpus, the anti-fake property, and CLI exit codes |

**Anti-fake guarantee.**
1. `make_replay_turn_fn` builds a REAL `ExecutionLoop` (production class). The only stubbed component is `ScriptedLLMClient`, which replays recorded `LLMResponse` objects instead of calling the Anthropic API.
2. `run_replay_suite` delegates every case to the existing `run_case()` from `runner.py` — the audited anti-fake core. Pass/fail logic is NOT reimplemented.
3. CLI exits non-zero (`typer.Exit(code=1)`) when any case fails. This is proven by `test_eval_run_cli_exits_nonzero_on_failing_case`, which injects a suite with an unsatisfiable `output_contains("NEVER_PRESENT")` assertion and asserts `exit_code != 0`.

**Honest cost limitation.** `ScriptedLLMClient` has no real cost meter; `TurnTrajectory.cost_usd` is always `0.0` for replay runs. `max_cost_usd` assertions are intentionally absent from `DEFAULT_REPLAY_SUITE` — a threshold check against a synthetic `0.0` is meaningless. Document this in both `replay.py` and `corpus.py`.

**Verification output.**

```
uv run pytest tests/test_eval_replay.py tests/test_eval_runner.py -v
# 33 passed in 4.54s (17 new in test_eval_replay.py + 16 pre-existing in test_eval_runner.py)

uv run ruff check src/hachi/core/eval src/hachi/cli/commands/eval.py tests/test_eval_replay.py
# All checks passed!

uv run mypy src/hachi/core/eval src/hachi/cli/commands/eval.py --ignore-missing-imports
# Success: no issues found in 6 source files

uv run hachi eval run
# ───────────────────── hachi eval run — 5 case(s) ──────────────────────
#   PASS  plain_text_reply
#   PASS  single_tool_call
#   PASS  two_tool_trajectory
#   PASS  multi_turn_smoke
#   PASS  output_contains_smoke
# ───────────────────────────────────────────────────────────────────────
# 5 passed / 0 failed  out of 5 total
# Exit code: 0

# Failure path (suite with output_contains("NEVER_PRESENT") assertion):
# ───────────────────── hachi eval run — 1 case(s) ──────────────────────
#   FAIL  will_fail
#          ↳ output_contains('NEVER_PRESENT'): not found; output starts with 'pong'
# ───────────────────────────────────────────────────────────────────────
# 0 passed / 1 failed  out of 1 total
# Exit code: 1
```

---

### E5 — Weekly failure-report automation (2026-06-24)

**Gap.** The `interaction_log` + `build_failure_report()` already existed, but nothing
triggered a periodic report.  A user could only get a report by running `hachi eval report`
manually.  The audit named "weekly-failure-report automation" as the natural next increment
so Hachi self-reports failure-rate degradation over time without human prompting.

**Design: why NOT the agent-turn scheduler.**  `src/hachi/jobs/scheduler.py` fires agent
turns (`Schedule.task` → `session.chat()`).  A failure report is internal computation, not
an LLM turn — it needs no Anthropic API key, no tool loop, and must never contribute to
session cost.  Using the scheduler would model it as a user request, add noise to the
interaction_log, and block on the session lock during a live turn.

**Chosen pattern: mirror `_start_timeout_sweep`.**  `orchestrator._start_timeout_sweep` is
the established pattern for resident internal computation: a module-level function that
starts a daemon thread with a `while True: sleep(); try: work(); except: log_debug` loop.
`_start_eval_report_scheduler` follows exactly this shape:
- Thread named `hachi-eval-report`, daemon=True
- Sleeps `min(interval_seconds, 3600)` per tick (caps at 1 h so the process stays
  responsive even with a 1-week interval)
- Calls `maybe_generate_report(...)` inside try/except, logs at debug on failure, never
  raises

**Decoupled core (`src/hachi/core/eval/scheduled_report.py`).**  All logic is extracted
into a pure module with no orchestrator imports:
- `render_report_markdown(report)` — pure EvalReport → markdown
- `ReportArtifact` — typed result (md_path, json_path, report, generated_at)
- `generate_failure_report_artifact(store, *, out_dir, …)` — reads rows, builds report,
  writes both `eval-report-<stamp>.md` and `eval-report-<stamp>.json`
- `due_for_report(last_run_at, now, interval_seconds)` — pure predicate
- `read_last_report_at` / `write_last_report_at` — atomic marker (temp + os.replace)
- `maybe_generate_report(store, *, out_dir, marker_path, interval_seconds, limit, emit, now)`
  — the single testable unit the thread calls each wake

**Opt-in, default off.**  `EvalReportSettings(BaseModel)` in `src/hachi/core/settings.py`
with `enabled: bool = False`, `interval_seconds: int = 604800`, `limit: int = 1000`.
Boot wiring in `orchestrator.boot_agent` reads `config.get("eval_reports", {})` and calls
`_start_eval_report_scheduler` only when `enabled=True`.  Nothing runs unless the operator
adds `[eval_reports] enabled = true` to `settings.toml`.

**Event.**  `EVAL_REPORT_GENERATED = "eval.report.generated"` added to
`src/hachi/core/runtime_events.py`.  Payload carries `markdown_path`, `json_path`,
`total_turns`, `failure_rate`, `generated_at`.

**Output location.**  `get_hachi_paths().state_dir / "eval-reports"` — confirmed
`state_dir` is where `sessions.db`, `schedules.json`, and `checkpoints/` already live.
`workspace_dir` does not exist; `data_dir` is for persistent data not ephemeral reports.

**Manual entry point: `hachi eval snapshot`.**  New `@eval_app.command("snapshot")` in
`src/hachi/cli/commands/eval.py`.  Generates the artifact immediately, updates the weekly
marker (so the scheduler clock resets), and prints the written paths + totals.  Does not
touch `eval report` or `eval run`.

**Verification output.**

```
uv run pytest tests/test_eval_scheduled_report.py -q
# 37 passed in 1.45s

uv run pytest tests/test_eval_report.py tests/test_eval_runner.py tests/test_eval_replay.py -q
# 55 passed in 5.71s (no regression)

uv run ruff check src/hachi/core/eval src/hachi/core/runtime_events.py \
    src/hachi/gateway/orchestrator.py src/hachi/cli/commands/eval.py \
    tests/test_eval_scheduled_report.py
# All checks passed!

uv run mypy src/hachi/core/eval src/hachi/core/runtime_events.py \
    src/hachi/cli/commands/eval.py --ignore-missing-imports
# Success: no issues found in 8 source files

uv run mypy src/hachi/gateway/orchestrator.py --ignore-missing-imports
# Found 34 errors (all pre-existing; zero new errors from E5 symbols)

uv run hachi eval snapshot --out $TEMP/hachi-eval-test
# Snapshot written  turns=27  failure_rate=0.0%
#   md  → C:\Users\opsin\AppData\Local\Temp\hachi-eval-test\eval-report-20260624-172538.md
#   json→ C:\Users\opsin\AppData\Local\Temp\hachi-eval-test\eval-report-20260624-172538.json
```

**Honest residual limitation.**  The resident scheduler is intentionally dormant until
opted in.  Because `hachi gateway` boot_agent is the entry point, the scheduler is NOT
active in `hachi run` (TUI mode) — it fires only when the gateway is running.  A user who
never runs the gateway can still generate reports on demand via `hachi eval snapshot`.
The first automatic run fires at the first `min(interval, 3600)` tick after `enabled=True`
boot — not immediately on boot.  Both behaviours are intentional: the first-tick-delay
avoids a report storm on restart; gateway-only scope keeps the clean process model intact.

---

### COST-1 — OpenAI-compatible streaming usage (eval-flywheel finding) (2026-06-24)

**Symptom.**  Every `gpt-4o-mini` (and all other OpenAI-compat provider) turn appeared in
the `interaction_log` with `tokens=0` and `token_cost=0.0`.  The eval report showed 100%
of OpenAI-compat interactions with zero cost and zero tokens.

**Root cause.**  `OpenAICompatibleClient.generate_stream` set `payload["stream"] = True`
but never sent `stream_options: {"include_usage": true}`.  Per the OpenAI streaming spec,
usage is omitted from streamed responses unless that option is requested explicitly.  The
final-chunk usage-parse block (`if "usage" in data`) in the SSE loop therefore never fired,
`final_usage` stayed `TokenUsage()` (all zeros), and `cost_tracker.record(model, response.usage)`
recorded nothing.  The non-streaming `generate` path already parsed `usage` correctly — this
was streaming-only.

**Impact.**  Beyond cosmetic eval metrics:
1. Eval report cost/token metrics were always zero for all OpenAI-compat providers.
2. **The daily spend cap (P1-3) was silently inert for streaming OpenAI-compat turns** —
   cost never accrued so the cap never tripped.  This is a trust/safety regression for any
   non-Anthropic user relying on spend limits.
3. The frontend per-turn token/cost badge showed nothing for these providers.

**Fix.**  Added a `stream_usage: bool = True` parameter to `OpenAICompatibleClient.__init__`
stored as `self._stream_usage`.  In `generate_stream`, immediately after `payload["stream"] = True`,
added `if self._stream_usage: payload["stream_options"] = {"include_usage": True}`.
`create_llm_client` reads `provider_config.get("stream_usage", True)` and threads the value
through all four `OpenAICompatibleClient(...)` construction sites (ollama, gemini, named
presets, openai/openai-compatible).  The existing final-chunk parse path
(`if "usage" in data and data["usage"]`) already handled empty-choices + usage-field chunks
correctly — no change needed there.

**Opt-out rationale.**  Default `True` because every major hosted provider (OpenAI, Groq,
Together, DeepSeek, OpenRouter, Gemini-compat, recent Ollama/vLLM) supports `stream_options`.
The opt-out flag (`stream_usage: false` in provider config) exists only for the rare strict
local server that returns HTTP 400 on unknown request fields.  The cost of opting out is
losing usage metrics for that provider.

**Honest limitations.**
- Historical rows in the `interaction_log` already written with `tokens=0`/`cost=0.0` will
  NOT backfill — this is a forward-only fix.
- The `chatgpt-web` (unofficial) provider is inherently usage-less; its `generate_stream`
  is in `unofficial_providers.py` and is unaffected / not addressable.
- Cache-token fields (`cache_read_tokens`, `cache_creation_tokens`) are NOT parsed from the
  OpenAI streaming usage chunk (OpenAI does not return them in the same way Anthropic does);
  this is symmetric with the non-streaming `generate` path and left as a potential follow-up.

**Files changed.**
- `src/hachi/core/llm.py` — `__init__` (new `stream_usage` param + `_stream_usage` attr),
  `generate_stream` (two lines: opt-in block after `payload["stream"] = True`),
  `create_llm_client` (read `stream_usage` from config + thread to all 4 construction sites).
- `tests/test_streaming.py` — updated `_make_openai_client()` to set `_stream_usage = True`
  (mirrors `__init__` default; required because the helper uses `__new__`), added 4 new
  tests: `test_openai_stream_default_sends_stream_options`,
  `test_openai_stream_opt_out_omits_stream_options`,
  `test_openai_stream_usage_captured_from_final_chunk`,
  `test_openai_stream_no_usage_chunk_returns_zeros`.

**Verification output.**

```
uv run pytest tests/test_streaming.py -q
# 18 passed in 3.76s

uv run pytest tests/ -q -k "llm or openai or stream or usage or cost"
# 195 passed, 3584 deselected in 43.44s

uv run ruff check src/hachi/core/llm.py tests/test_streaming.py
# All checks passed!

uv run mypy src/hachi/core/llm.py --ignore-missing-imports
# Success: no issues found in 1 source file
```

### E6 — Eval regression corpus + precise assertions (2026-06-24)

**Loop invariants now protected by the corpus (14 cases):**
- Plain text reply passes through verbatim (output_contains)
- Single tool call fires + final reply appears (tool_called, output_contains)
- Two-tool trajectory across separate iterations (tool_called × 2)
- Multi-turn smoke — 3 LLM turns finishes without error
- output_contains baseline smoke (no tools)
- **Tool error recovery**: `_execute_single_tool` catches tool raises, feeds the error
  string back to the LLM as `is_error=True`; the TURN itself does not raise — `no_error()` passes.
- **Unregistered tool**: `ToolRegistry.execute()` returns the string
  `"Error: Tool '…' is not registered."` (no exception) — turn continues gracefully.
- **Max-iterations cap**: scripted LLM always calls a tool, never emits text — loop
  terminates at `max_iterations=10` returning `[INCOMPLETE]`; `tool_call_count(10)` confirms
  exactly 10 dispatches.
- **Empty/degraded response**: `LLMResponse(content="")` + no tool_calls → loop exits cleanly
  (`not assistant_content` branch at loop.py line 947); `no_error()` + `tool_call_count(0)` pass.
- **Tool with arguments**: `ToolCall.input` is unpacked as `**kwargs` into the callable — verified.
- **Parallel tool calls**: all `ToolCall`s in one `LLMResponse` are submitted to the thread
  pool concurrently (loop.py lines 1013-1017); both tools appear in `tool_calls`; order
  reflects the pre-pass iteration order.
- **Three-tool ordered trajectory**: `tool_called_in_order(["plan","execute","verify"])` +
  `tool_call_count(3)`.
- **Clean reply zero tools**: `tool_call_count(0)` + `output_not_contains("[error]")`.
- **No internal loop sentinel markers in clean output**: checks `[CANCELLED]`, `[TIME LIMIT
  REACHED]`, `[BUDGET LIMIT REACHED]`, `[WORKFLOW SUSPENDED]`, `[INCOMPLETE]` are absent.

**Three new assertion kinds added to `runner.py`:**
1. `ToolCallCountAssertion(count: int)` / `tool_call_count(n)` — `len(trajectory.tool_calls) == count`.
2. `OutputNotContainsAssertion(substr: str)` / `output_not_contains(s)` — `substr not in trajectory.output`.
3. `ToolOrderAssertion(names: tuple[str,...])` / `tool_called_in_order([...])` — `names` is
   an ordered subsequence of `trajectory.tool_calls` (not necessarily contiguous).

**Honest framing:**
- This is a loop-mechanics regression suite, not a model-quality suite.
- `ScriptedLLMClient` replaces only the LLM transport boundary; all ReAct loop, tool dispatch,
  and memory code is production `ExecutionLoop`.
- `cost_usd` is always `0.0` (no real cost meter); `max_cost_usd` assertions are absent.

**Surprising loop behaviors confirmed by reading source:**
- Parallel tool calls are supported in a single `LLMResponse` (the loop iterates all
  `response.tool_calls` twice — pre-pass for side-effects, then pool submit — so order
  in `last_tool_calls` is the pre-pass iteration order, which matches `ToolCall` list order).
- Tool errors are silently absorbed: `_execute_single_tool` returns a tuple, never re-raises
  tool exceptions as loop-level exceptions (only `SuspendWorkflow` propagates).
- An empty `LLMResponse` (no content, no tool_calls) is a CLEAN FINISH, not an error.
- `ToolRegistry.execute()` returns a string for unregistered tools — no exception path.
- No candidate loop bugs were found. All expected-pass cases passed on first run.

**Verification:**
```
uv run pytest tests/test_eval_replay.py tests/test_eval_runner.py tests/test_eval_assertions.py -q
# 64 passed in 5.19s

uv run ruff check src/hachi/core/eval tests/test_eval_replay.py tests/test_eval_runner.py tests/test_eval_assertions.py
# All checks passed!

uv run mypy src/hachi/core/eval --ignore-missing-imports
# Success: no issues found in 6 source files

uv run hachi eval run
# 14 passed / 0 failed  out of 14 total  (exit code 0)
```

---

### M1 — Episodic memory store (2026-06-25)

**Gap.**
Episode nodes were written to `memory/journal/<session>.og` at session close with a
near-useless 3×80-char summary derived from the last 3 history messages. The
`interaction_log` data — which captures actual `user_input`, `agent_output`, and
`tool_calls` per turn — was never used for recall. There was no ChromaDB episodic
collection, so past-session content was invisible to semantic recall; each session
started blind to everything that happened in previous sessions.

Additionally, `search_delegations` did not apply `_redact_results()` to its output,
breaking the invariant that all recall paths redact secrets (MED-009).

**Design (implemented exactly).**

1. **New ChromaDB collection** `episodic_memory` added to `_COLLECTION_SPECS` with lazy
   init (same pattern as `delegation_log`, `user_profile`, etc.). Existing `_FakeCollection`
   tests that bypass `__init__` are unaffected because they only touch collections they
   explicitly declare.

2. **Capture** — `build_episode_summary(interactions, *, max_chars=1500)` in `memory.py`:
   a pure, deterministic, zero-cost function (no LLM call). Produces a header line + a
   bulleted list of per-turn `user_input` (truncated to 120 chars) → `agent_output` head
   → tool names. Length-capped at `max_chars` with a graceful truncation note. Returns `""`
   for empty lists (caller skips the vault write). The vault upsert is done in the
   orchestrator `shutdown()` block alongside the existing `.og` episode write, NOT inside
   `MemoryManager.write_episode`, so `write_episode`'s existing tests stay green without
   modification.

3. **Recall** — `search_episodic(query, top_k)` on `MemoryVault`: delegates to
   `_query_collection("episodic_collection", ...)` and applies `_redact_results()`.
   Returns the same dict shape as `semantic_search` so the orchestrator can merge uniformly.
   Guarded: `top_k <= 0` returns `[]` immediately.

4. **Orchestrator wiring:**
   - *Turn start*: after existing semantic recall, calls `vault.search_episodic(user_input,
     top_k=self._episodic_top_k)` and merges into `recalled` (deduped by ID). Wrapped in
     try/except — never raises.
   - *Shutdown*: fetches `session_store.get_interactions(session_id, limit=50)`, reverses
     to chronological order, calls `build_episode_summary`, calls `vault.upsert_episode`
     with deterministic `session_id`-derived ID (upsert semantics, no duplicates). Wrapped
     in try/except — never breaks shutdown.
   - `_episodic_top_k` default 3, set in `__init__`, overridden by `boot_agent` from
     `_memory_settings.episodic_top_k`.

5. **Part D fix** — `search_delegations` now calls `_redact_results()` on its return path,
   matching `semantic_search` and `search_user_profile`.

**Settings** (`src/hachi/core/settings.py` — strict-gated):
`episodic_top_k: int = 3` added to `MemorySettings`. Set to 0 to disable episodic recall.

**Invariants preserved.**
- `_redact_results` applied on ALL recall outputs: episodic + delegations (Part D fix).
- `_assert_open()` + `_vault_lock` on every new collection-access method.
- Lazy collection init — `episodic_collection` is None until first access; `_FakeCollection`
  tests that set `_collections` manually to 5 keys are unaffected (they never touch
  `episodic_collection`).
- Episodic capture and recall both wrapped in try/except — never break a turn or shutdown.
- `get_recent_facts` and existing session_store.py unchanged.

**Files changed.**
| File | Change |
|------|--------|
| `src/hachi/core/memory_vault.py` | Add `episodic_memory` to `_COLLECTION_SPECS`; add `episodic_collection` lazy property; add `upsert_episode()` + `search_episodic()`; fix `search_delegations` to call `_redact_results()` |
| `src/hachi/core/memory.py` | Add `build_episode_summary()` pure function (module level) |
| `src/hachi/core/settings.py` | Add `episodic_top_k: int = 3` to `MemorySettings` |
| `src/hachi/gateway/orchestrator.py` | Add `self._episodic_top_k = 3` in `__init__`; wire episodic recall in turn start; wire episodic capture in `shutdown()`; set `session._episodic_top_k = _memory_settings.episodic_top_k` in `boot_agent` |
| `tests/test_episodic_memory.py` | New: 19 tests covering `build_episode_summary`, round-trip upsert/search, redaction, top_k=0 guard, idempotency, search_delegations redaction (Part D) |

**Capture-cadence limitation.** Episodes are captured exactly once per session, at shutdown.
For long-lived sessions (multi-hour conversations), the episode reflects the full
interaction_log at close time (up to 50 rows), which is correct. However, if a session
crashes without a clean shutdown, no episode is captured for that session — the `.og`
journal write has the same gap. A periodic in-session summarizer (triggered after every N
turns or on a time interval) is a documented follow-up.

**Verification.**
```
uv run pytest tests/test_episodic_memory.py tests/test_memory_fixes.py tests/test_memory_vault_close.py tests/test_memory_provider.py -q
# 61 passed in 8.92s

uv run ruff check src/hachi/core/memory_vault.py src/hachi/core/memory.py src/hachi/core/settings.py src/hachi/gateway/orchestrator.py tests/test_episodic_memory.py
# All checks passed!

uv run mypy src/hachi/core/memory_vault.py src/hachi/core/settings.py --ignore-missing-imports
# Success: no issues found in 2 source files

uv run python -m mypy --config-file mypy-strict-gate.ini
# Success: no issues found in 36 source files

uv run mypy src/hachi/gateway/orchestrator.py --ignore-missing-imports
# Found 34 errors (all pre-existing; zero new errors from M1)
```

---

## Memory decay (M2) — 2026-06-25

**Branch:** `reassess`

### Found / motivation

Semantic recall returns facts ranked purely by cosine distance (relevance). For a personal
agent vault that accumulates facts over months, this means a stale fact stored 6 months ago
can outrank a fresh fact stored yesterday if their embedding similarity differs by even a
small amount. This misranks recall whenever the user's context has evolved (new project,
new preference, new role). The fix must never break recall — it must only *re-order* an
already-quality-gated candidate set.

### Decided / changed

**Formula (multiplicative recency decay):** `score = distance / recency_decay` where
`recency_decay = 0.5 ** (age_days / half_life_days)`. Score is in the same direction as
distance (lower = better). At age 0 → score == distance (no penalty). At exactly
`half_life_days` → score == 2 × distance (the fact competes as if twice as far away).
The formula is parameter-free beyond the half-life value; it has no intercept, no
training data, and no risk of divergence.

**Off by default:** `decay_half_life_days: float | None = None` in `MemorySettings`.
When `None` or `<= 0`, `_query_collection` is byte-for-byte identical to pre-M2.

**Never resurrects filtered facts:** the `distance > effective_threshold` gate runs on
the RAW cosine distance and is completely unchanged. Decay only re-orders the survivors;
it cannot make a beyond-threshold candidate pass the gate.

**Over-fetch for larger reordering pool:** when decay is active, ChromaDB is queried for
`min(top_k × 4, 50)` candidates (constants `_DECAY_OVERFETCH=4`, `_DECAY_OVERFETCH_CAP=50`).
After the distance gate and decay sort, the result is sliced to `top_k`. This ensures
the freshest *relevant* fact can bubble up even if it wasn't in the top-k by raw distance.

**Never on user_profile:** `search_user_profile` passes `apply_decay=False`. Identity and
preference facts (name, role, timezone, habits) are timeless — a user's name stored two
years ago is not "stale". Only `semantic_search` (core_memory) and `search_episodic`
(episode summaries) get decay.

**`get_recent_facts` unaffected:** it uses `collection.get()` not `collection.query()`,
so it is a completely separate code path. Decay is not applied and contract C-RECENT
(unbiased recency access) is unchanged.

**`search_delegations` unaffected:** it has its own custom query path with where-clause
filtering and is not routed through `_query_collection`.

**Safety wrapping:** the decay sort is wrapped in `try/except Exception`; any bug in
`_decay_score` or the sort falls back to the undecayed relevance order and logs at DEBUG.
`_decay_score` itself is a total, exception-free pure function — unparseable/missing/empty
timestamps return `distance` unchanged; naive timestamps are assumed UTC (no tz-error);
extreme ages (underflow of `0.5**N`) are guarded by `max(recency_decay, 1e-9)`.

**Strict mypy gate:** `memory_provider.py` and `settings.py` are in `mypy-strict-gate.ini`.
Both remain 0 errors after M2. `memory_vault.py` is advisory — also clean.

### Files changed

| File | Change |
|------|--------|
| `src/hachi/core/memory_vault.py` | Add `_DECAY_OVERFETCH`, `_DECAY_OVERFETCH_CAP` constants; add `_decay_score()` module-level pure function; add `decay_half_life_days` param to `__init__`; add `apply_decay` param to `_query_collection` with over-fetch and try/except-wrapped re-rank; add `apply_decay=False` to `search_user_profile` call |
| `src/hachi/core/settings.py` | Add `decay_half_life_days: float | None = None` to `MemorySettings` |
| `src/hachi/core/memory_provider.py` | Add `decay_half_life_days: float | None = None` kwarg to `ChromaMemoryProvider.initialize()`; thread it into `_kwargs` |
| `src/hachi/gateway/orchestrator.py` | Pass `decay_half_life_days=_memory_settings.decay_half_life_days` in the `ChromaMemoryProvider.initialize()` call in `boot_agent` |
| `tests/test_memory_decay.py` | New: 20 tests covering `_decay_score` unit (age-0, half-life, unparseable, empty, Unknown-Date, distance-0, extreme-age, monotonicity, naive-ts, future-ts, half-life-0-guard), re-rank order flip via `_query_collection` and `semantic_search`, over-fetch+truncate, user_profile not-decayed, distance-gate-precedes-decay, default-off parity, C-RECENT contract |

**Config key to enable:** `memory.decay_half_life_days = 30.0` in `settings.toml`.
A value of 30 days means a one-month-old fact competes as if 2× more distant; a
three-month-old fact competes as if 8× more distant. Leave unset (the default) for
pure relevance order.

**Verification.**
```
python -m pytest tests/test_memory_decay.py tests/test_episodic_memory.py tests/test_memory_fixes.py tests/test_memory_vault_close.py tests/test_memory_reliability.py tests/test_memory_provider.py -q
# 100 passed

python -m mypy --config-file mypy-strict-gate.ini
# Success: no issues found in 36 source files

python -m ruff check src/hachi/core/memory_vault.py src/hachi/core/memory_provider.py src/hachi/core/settings.py src/hachi/gateway/orchestrator.py tests/test_memory_decay.py
# All checks passed!
```

---

## Container sandbox backend — S1 (2026-06-25)

**Found.**
`src/hachi/core/sandbox.py` contained a single concrete class (`SandboxExecutor`) with no
abstraction layer, making it impossible to slot in a hardened execution backend without
rewriting callers. `security-audit` emitted an unconditional `medium` finding
`sandbox.process_backend` regardless of the runtime configuration — even if an operator had
already deployed a hardened alternative. The module docstring marked container isolation as
"TODO".

**Decided/Changed.**

### `SandboxBackend` ABC  
Added `SandboxBackend(ABC)` with abstract `execute()`. Both concrete implementations
subclass it, exposing typed `name: str` and `is_hardened: bool` class attributes so
callers can branch without isinstance checks.

### Process backend (back-compat)
`SandboxExecutor` now subclasses `SandboxBackend`; it sets `name = "process"`,
`is_hardened = False`. **All existing methods and their signatures are byte-for-byte
preserved** (`__init__`, `_resolve_uv`, `_validate_dependencies`, `_generate_env_hash`,
`execute`). The body of `_validate_dependencies` delegates to the new module-level
`validate_dependencies()` function (so the container backend reuses the same logic);
the method itself is kept so `test_phase7_integrations_mcp_sandbox_deployment.py` can
still call `SandboxExecutor._validate_dependencies(executor)` directly.

### Container backend (`ContainerSandboxExecutor`)  
`name = "container"`, `is_hardened = True`. All config read from environment variables
with safe defaults:

| Env var | Default | Meaning |
|---|---|---|
| `HACHI_SANDBOX_IMAGE` | `python:3.12-slim` | Docker image |
| `HACHI_SANDBOX_MEMORY` | `512m` | `--memory` + `--memory-swap` (no swap) |
| `HACHI_SANDBOX_PIDS` | `256` | `--pids-limit` |
| `HACHI_SANDBOX_CPUS` | `1.0` | `--cpus` |
| `HACHI_SANDBOX_NETWORK` | `none` | `--network` |
| `HACHI_SANDBOX_USER` | `65534:65534` | `--user` (nobody:nogroup) |

Every `docker run` invocation is built as a list (never `shell=True`) and includes:
`--rm`, `--network none`, `--read-only`, `--cap-drop ALL`,
`--security-opt no-new-privileges`, `--user 65534:65534`,
`--memory 512m`, `--memory-swap 512m`, `--pids-limit 256`, `--cpus 1.0`,
`--tmpfs /tmp:rw,size=64m`, `-v <exec_dir>:/work:ro`, `-w /work`.

Deps + `--network none` → immediate `SandboxResult(success=False, exit_code=-1)` with
an explanatory message (suggest `HACHI_SANDBOX_NETWORK=bridge` or baking deps into the
image). Timeout → best-effort `docker kill` then `exit_code=124`.

### Fail-fast factory  
`create_sandbox_backend(backend=None, *, sandbox_root=None)` resolves the backend from
the `HACHI_SANDBOX_BACKEND` env var (default `"process"`). When `"container"` is
selected but Docker is absent: raises `OSError` immediately — **never silently downgrades
to the process backend**, as that would be a security surprise.

### Audit-honesty helper  
`sandbox_audit_finding(backend, docker_available) -> tuple | None`:
- `("container", True)` → `None` (hardened backend active, no finding emitted)  
- `("container", False)` → `("high", "sandbox.container_unavailable", …)`  
- Any other value → `("medium", "sandbox.process_backend", …)` (existing wording)  

`cli/main.py::security_audit` replaced the unconditional `add_finding(medium, sandbox.process_backend, …)`
with a call to this helper — the finding disappears when a working container backend is
configured, and escalates to HIGH when container is selected but Docker is missing.

### `sandbox_tools.py` update  
Lazy `_executor` list now typed as `list[SandboxBackend]`; initialises via
`create_sandbox_backend()`. Registration-time CRITICAL log is now conditional:
process backend → CRITICAL; container backend → WARNING. Per-invocation log matches the
active backend's `is_hardened` attribute.

### Tests  
`tests/test_sandbox_backend.py` — 37 new unit tests, no live Docker required:
factory routing (default/process/container/missing-docker/unknown/env-var), container
command flag assertions (`--rm`, `--network`, `--read-only`, `--cap-drop ALL`,
`--security-opt no-new-privileges`, `--user`, `--memory`, `--pids-limit`, `--cpus`,
`--tmpfs`, image, `python`), stdout/exit-code flow, timeout → 124,
deps+network=none short-circuits without calling docker-run,
`validate_dependencies` cases, `sandbox_audit_finding` matrix (8 cases),
`container_backend_available`, backward-compat `_validate_dependencies` method.

**Strict mypy gate:** `sandbox.py` is in `mypy-strict-gate.ini`; 0 errors after S1.
**Ruff:** all checks passed on all four changed files.

**Why.**  
Operators who deploy into Docker-capable environments now have a production-grade,
kernel-enforced isolation path without changing any call sites. The process backend
remains the default (zero behaviour change for existing deployments). The audit finding
is now an honest reflection of the runtime posture rather than a standing false-alarm.
The fail-fast factory prevents silent security regressions where a misconfigured
container backend would transparently fall back to the weaker process backend.

**Note on live container execution.** Unit tests mock `subprocess.run` and `shutil.which`.
A live end-to-end run requires Docker and should be exercised via an operator opt-in job
similar to the existing `docker-smoke` CI job (a follow-up, not part of S1 unit scope).

### Files changed

| File | Change |
|---|---|
| `src/hachi/core/sandbox.py` | `SandboxBackend` ABC; module-level `validate_dependencies()`; `SandboxExecutor` subclasses `SandboxBackend` (back-compat preserved); new `ContainerSandboxExecutor`; `container_backend_available()`; `create_sandbox_backend()` factory; `sandbox_audit_finding()` helper; updated module docstring |
| `src/hachi/tools/sandbox_tools.py` | `_executor` typed as `list[SandboxBackend]`; lazy init via `create_sandbox_backend()`; registration-time log conditional on backend; per-invocation log conditional on `is_hardened` |
| `src/hachi/cli/main.py` | Replace unconditional `add_finding(medium, sandbox.process_backend)` with `sandbox_audit_finding()` + conditional `add_finding(*_sb_finding)` |
| `tests/test_sandbox_backend.py` | New: 37 unit tests (factory, container command flags, timeout, deps+network, audit helper, availability probe, backward-compat method) |
| `DECISIONS.md` | This entry |

**Verification.**
```
python -m pytest tests/test_sandbox_backend.py tests/test_g4_security_depth.py tests/test_phase7_integrations_mcp_sandbox_deployment.py -p no:randomly -q
# 60 passed

python -m mypy --config-file mypy-strict-gate.ini
# Success: no issues found in 36 source files

python -m ruff check src/hachi/core/sandbox.py src/hachi/tools/sandbox_tools.py src/hachi/cli/main.py tests/test_sandbox_backend.py
# All checks passed!
```

---

## FLAKE — full-suite hermeticity + determinism (2026-06-25, commit `afe8138`)

Re-diagnosed from scratch on `reassess`; the `flake-wip` branch's fixes were superseded (it never
cracked layer 3 and its layer-4 fix was wrong). Full suite: **3867 passed / 21 skipped / 0 failed**,
deterministic, 4:40 (was a ~40-min crawl while leaked keys drove real LLM calls).

### Layer 1 — import-time `.env` autoload (secret leak + nondeterminism)
**Found.** `cli/main.py::_load_dotenv_into_environ()` runs at import time, merging `<cwd>/.env` +
config-dir `.env` into `os.environ`. During pytest collection this pulls the dev's real
`ANTHROPIC_API_KEY` etc. into the process, which (a) flips import-time `@skipif(not getenv(...))`
guards so live-gateway tests RUN — and `test_cockpit_chat_live` then makes **billed** LLM calls every
suite run — and (b) leaks secrets into the test process.
**Changed.** Guard the merge behind `HACHI_DISABLE_DOTENV_AUTOLOAD`; `tests/conftest.py` sets it
(`setdefault`, before any hachi import). Production default unchanged (autoload on). New
`tests/test_dotenv_hermeticity.py` regression-guards the flag + the conftest default.

### Layer 2 — `os.environ` leak past monkeypatch
**Found.** `_apply_tokoboros_shim()` writes `HACHI_*` straight into `os.environ`; a test that triggers
it (`test_shim_covers_all_renames`) leaks those keys because monkeypatch only undoes vars it set.
Order-dependent failures in later email/SMTP tests.
**Changed.** Autouse diff-based `_restore_os_environ` fixture in conftest — snapshot `dict(os.environ)`
before, restore after (remove added keys, restore changed/deleted). Diff-based (not `clear()`) so
concurrent in-process-gateway threads never observe an empty env mid-test.

### Layer 3 — chromadb native-extension "poison" (the real unsolved layer) **[ADR]**
**Found.** `patch.dict(sys.modules, {...})` snapshots sys.modules on enter and restores on exit,
**dropping any module first-imported inside the block**. `test_file_platform`'s
`test_pdf_without_pypdf_returns_none_from_find_extractor` runs `_build_extractor_chain()` inside a
`patch.dict(sys.modules, {"pypdf": None})` block; that chain lazily first-imports `chromadb`. On exit,
patch.dict wipes all `chromadb.*` entries it added — including the **PyO3 native submodule**. A native
extension *cannot be re-initialized once unloaded*: CPython raises `ImportError: cannot load module more
than once per process`. Every later `import chromadb` in the process then fails, and the guarded
`except ImportError` in `memory_vault` / `enable_embedding_rank` swallows it as "chromadb not installed"
→ vault/embedding tests silently fail. `flake-wip`'s `_restore_chromadb_modules` fixture *deleted real
chromadb submodules on teardown*, so it **caused** this failure rather than fixing it.
**Decided.** (a) Warm `import chromadb` + `import chromadb.config` once at `conftest.py` import, so
chromadb is present in *every* `patch.dict` enter-snapshot and survives the restore. (b) Fix the
offending `test_search_without_chromadb_returns_error` to use a `None`-sentinel restore — never install
a `MagicMock` stub (the original installed one when chromadb was absent, which poisoned
`from chromadb.config import Settings` with "chromadb is not a package"). (c) Do NOT add a
chromadb-restoring fixture — removing chromadb from sys.modules is the hazard, not the cure.
**Why.** Warming is a one-line, order-independent neutralizer for the whole class of
patch.dict-wipes-native-import bugs; it costs one import already paid by most runs.

### Layer 4 — "cockpit flakiness" is a boot timeout, not a key issue
**Found.** `test_cockpit_gaps::TestMemoryCRUD` (the S1 gate's 1 failure) fails at `_create_session` →
`POST /sessions transport error: timed out`, NOT at any memory assertion. 12/13 tests in the class pass
with **no** API key — memory CRUD needs no LLM. `POST /sessions` does heavy synchronous skill-engine +
ChromaDB vault init that exceeds `GatewayClient.DEFAULT_TIMEOUT = 15s` on a cold Windows process
(uncached imports) or under full-suite load (Nth PersistentClient).
**Changed.** `GatewayClient.__init__` gains a `timeout` arg; absent that, it reads
`HACHI_GATEWAY_CLIENT_TIMEOUT` (else the 15s default). conftest grants 90s for the test process.
Production default unchanged. Rejected `flake-wip`'s `@skipif(not ANTHROPIC_API_KEY)` on the whole
class — empirically wrong (would skip 12 passing keyless tests) and would hide real regressions.

### Files changed
`src/hachi/cli/main.py` (dotenv guard) · `src/hachi/client/gateway.py` (configurable timeout) ·
`tests/conftest.py` (flags + chromadb warm + env-restore fixture; removed harmful chromadb fixture) ·
`tests/test_file_platform.py` (sentinel restore) · `tests/test_dotenv_hermeticity.py` (new).

---

## Feature tier — EventBus / knowledge-graph / query-cache (2026-06-25)

Three user-greenlit features, each "activate dormant scaffolding" not greenfield.
Orchestrate-verify-gate via Sonnet 4.6 subagents; Opus designed, verified, committed.

### EventBus reactivity (`73199b7`) **[ADR]**
**Found.** A complete `EventBus` + a byte-identical funnel existed behind default-off
`[events] bus_funnel`; the reactive architecture (Initiative C) was dormant. Reactive
`file.uploaded→auto_extract` was double-gated (needed BOTH `bus_funnel` AND
`[jobs] durable_handlers`).
**Decided.** Default `bus_funnel` True (byte-identical proven by `test_slice22_wiring`).
DECOUPLE the reactive subscription from `durable_handlers`: subscribe whenever the bus
is on — async/durable when the JobRegistry is on, else sync-inline (file.uploaded is
emitted only by FilePipeline.ingest / POST /files, never the turn hot path, so inline is
safe and cannot orphan a job). Wire ObservabilitySink (no-op when tracing off). Promote
leaked literals to the existing typed constants (unifies the budget event name with the
daily-cap path). Did NOT default `durable_handlers` on (bigger subsystem; out of scope).
**Why.** Completes the migration to the bus as the event path with the full suite as the
byte-identical safety net, without forcing durable execution semantics on every user.

### Knowledge-graph memory (`0518255`)
**Found.** `memory_graph.py` (≤2-hop typed-edge expansion + supersession) was complete but
had NO caller. Crucially, the edge data already exists and is fresh: MemoryManager's typed
extraction writes nodes WITH `->[:key=value]` edges to `.og` files via MemoryWriter on the
normal write path — so graph recall reads them with ZERO new billed calls. (The vault→.og
*exporter* writes no edges, a red herring; the *typed writer* does.)
**Decided.** New `core/graph_recall.py::build_graph_recall_section` (pure helper), wired in
`orchestrator._chat_impl_inner` step 3b to APPEND a `[Related memory (graph)]` section to
the semantic recall block — additive (never replaces the floor), `[memory] graph_recall`
flag (default True), broad try/except (a recall enhancement must never break a turn),
degrades to "" without edges. Perf: process-LRU keyed `(memory_dir, max .og mtime_ns)` so
the rglob+parse runs only on actual file change — not every turn (stat-only probe).
**Why.** Structured graph traversal (project→tasks, causal chains, supersession) on top of
flat semantic recall, free and safe, gated by the full suite.

### Frontend query-cache (`1475a52`)
**Found.** Route loaders fired once per path per session (binary `_loaded` Set in
`useRouteLoaders`) and never refetched — data went stale until a full reload.
**Decided.** New `lib/data/cache.ts`: `loadCached(key, loader, {ttlMs, force})` with
stale-while-revalidate (fresh→no-op; stale→deduped loader via existing `dedupe.ts`; stamp
only on success so failures retry) + `invalidate`/`invalidateAll` + `DEFAULT_TTL_MS=30s` +
a `_setNow` test seam. Rewired `useRouteLoaders` to domain-keyed `loadCached` (approvals
10s, spend 60s); invalidate after memory/approval mutations. Frozen foundation
(client/sse/live/store) untouched; no gateway routes → OpenAPI contract unchanged.
**Why.** Revisits now refresh on a TTL while the store keeps showing prior data (SWR),
without adopting a heavy query lib inconsistent with the custom data layer.

### Gate results
EventBus 3887 pass / KG 3907 pass / query-cache vitest 94 pass — each 0 fail, ruff +
strict-mypy (36) clean, frontend typecheck + build green. All pushed to `reassess`.

---

## Regression guardrails — Phase 1 + Memory (2026-06-27) **[ADR]**

Adopted the "guardrails" plan's mechanical floor + memory layer to stop AI-introduced
regressions/drift. Phase 1 (the Gate + the Locks) + Memory; the Rails (Phase 2) and
role-agents (Phase 4) are deliberately deferred. Single session, no subagents.

**Found.** Hachi's checks existed only as 8 separate CI jobs — no single local gate, and nothing
*mechanically* stopped a bad diff landing. ruff + strict-mypy-gate were already green; full-tree
mypy and `ruff format` were NOT (113 files would reformat).

**Decided / built.**
- **The Gate — `scripts/preflight.py`** (`uv run python scripts/preflight.py [--quick]`): one
  command mirroring CI — ruff, strict-mypy-gate, skill-lint, eval, full pytest, frontend
  (typecheck/vitest/build/openapi-contract). Cross-platform Python runner (no Makefile).
  - **No global `ruff format --check` step** — the repo is deliberately not fully formatted (113
    files; a repo-wide reflow is forbidden, Rule 3). The auto-format hook keeps *touched* files clean.
  - **pytest runs sequentially, NOT `-n auto`** — parallel workers starve wall-clock timing tests
    (`test_two_sessions_run_concurrently`, notifier non-blocking) → flaky gate. Matches CI and the
    plan's "run tests sequentially to kill time-based flakiness."
  - **security-audit is advisory** (mirrors CI continue-on-error on main; 2 pre-existing HIGH
    config-posture findings remain — not regressions).
- **The Locks — `.claude/settings.json` + `.claude/hooks/*.py`** (stdlib Python, exec-form
  invocation → portable across PowerShell / Git Bash / Linux):
  - `bash_guard.py` (PreToolUse `Bash|PowerShell`): push-lock on main/master via a one-shot
    `.claude/.push-approved` sentinel; secret-scan on commits (high-confidence patterns + staged
    `.env`); anti-forge. **Matcher includes PowerShell** — `Bash`-only would let the agent bypass
    the push-lock on Windows (the primary shell).
  - `stop_guard.py` (Stop): blocks finishing while `ruff check` is red (fast); the *full* suite is
    preflight's job, not every Stop. Guards `stop_hook_active`.
  - `auto_format.py` (PostToolUse Write|Edit|MultiEdit): `ruff format`s the touched `.py`.
  - `session_start.py` (SessionStart): injects the gate command, branch, and the top
    `docs/bug-bank.md` **Avoid:** rules so a fresh agent starts warm.
- **Memory — `docs/bug-bank.md`** (per-bug autopsy + locking test), seeded BB-1..BB-9 from the old
  CLAUDE.md gotchas + history. The SessionStart hook injects the **Avoid:** lines each session.
- **Lean CLAUDE.md** — slimmed from 257 lines to the inviolable rules + pointers; detail
  *relocated* (gotchas→bug-bank, entry-points/frontend-contract→ARCHITECTURE.md, conventions→
  CONTRIBUTING.md). Nothing dropped.

**Verified.** All 5 hooks unit-checked live this session (15/15 driver checks: push-lock blocks
main + consumes the sentinel; secret-scan blocks AWS keys / staged `.env` and does NOT self-trip
on the guardrail files; stop-guard, auto-format, session-start). preflight green on every blocking
step (ruff / mypy-gate / skill-lint / eval; full pytest 3941 pass, and the 2 timing tests pass
once sequential). Caught + fixed en route: the PowerShell matcher hole; preflight's cp1252 console
crash; and **BB-2 was a stale absolute** — `.env` IS autoloaded at the CLI edge, gated by
`HACHI_DISABLE_DOTENV_AUTOLOAD` and locked by `test_dotenv_hermeticity.py` — corrected in the bug bank.

**Deferred (the plan's phasing).** Phase 2 Rails (vulture dead-code, deptry unused-deps, jscpd
duplication, ruff `PLR0913` max-params, import-linter boundaries — add to preflight advisory→
blocking once baselines are clean); Phase 3 formal `docs/adr/` + Mermaid/C4 diagrams; Phase 4
role-separated subagents + adversarial verification. Uncommitted on `main` at time of writing.

## Regression guardrails — Phase 2: the Rails (2026-06-27) **[ADR]**

Added the mechanical guards against the *architectural-drift* and *code-rot* pains, the layer
Phase 1 deferred. Three new dev-only tools (`import-linter`, `deptry`, `vulture`) in the `dev`
extra — never imported by the product — plus ruff complexity rules. Wired into both
`scripts/preflight.py` and CI's `lint` job so the gate and CI stay in lockstep. No `src` changes.

**Found (the real import graph, measured — not assumed).** The engine layers are *not* pure: `core`
and `tools` reach up into `gateway`/`cli` in 6 concrete seams (`core.onboarding→gateway.workspace`
top-level; `core.cost`/`core.runtime.job_handlers`/`core.runtime.resume`/`tools.config_tools`→
gateway lazily; `core.runtime.resume→cli.main`). `og_engine` *is* already pure. A naïve "domain must
not import gateway" rule would therefore be red on day one — the wrong move. Baselines: deptry 34
raw findings (all extras/dev/first-party noise + a few intentional), ruff complexity 178 at strict
defaults, vulture 8 at confidence ≥80.

**Decided / built.**
- **Architecture rail — `import-linter` (BLOCKING, green).** Three `forbidden` contracts in
  `pyproject [tool.importlinter]`: (1) `og_engine` is pure — depends on nothing but `core`; (2) the
  engine must not import the UI edges (`cli`, `tui`); (3) the engine must not import `gateway`. The 6
  pre-existing seams are frozen as commented `ignore_imports` — *documented debt to burn down, not a
  licence to add more*. The gateway contract sets `allow_indirect_imports = true` because the one
  indirect path (`core→cli.main→gateway.workspace`) is the cli seam already frozen by contract (2),
  and cli importing the gateway is correct edge→service layering. **Proven to bite:** a temporary
  direct `core.approvals→gateway.workspace` import flipped the contract to BROKEN; reverted.
  `unmatched_ignore_imports_alerting` stays at its ERROR default, so a stale ignore (a seam later
  removed) fails until deleted — the ratchet self-cleans.
- **Dependency rail — `deptry` (ADVISORY).** `[tool.deptry]` models Hachi's extras truthfully:
  `known_first_party=["benchmarks"]`, `optional_dependencies_dev_groups=["dev","security"]`, a
  `package_module_name_map` for distributions whose import name differs (docx/pptx/googleapiclient…),
  and `per_rule_ignores` freezing only **confirmed** false-positives (tzdata data-pkg; tavily-python
  via `importlib.import_module`; python-multipart via Starlette). This cut 34→**4 genuine findings**:
  `slack-sdk`, `notion-client`, `google-auth`, `google-api-python-client` are declared extras whose
  SDKs are imported **nowhere** in the repo (the integrations use httpx). Left UNfrozen on purpose —
  surfacing a real signal, not suppressing it; pending a call to drop the extras or keep them as
  intentional optional installs (then freeze + make deptry blocking).
- **Dead-code rail — `vulture` (ADVISORY).** `[tool.vulture]` at `min_confidence=80` (skips the noisy
  60% method/attribute guesses that false-positive on framework entry points) + decorator/name
  ignores. `exc_type/exc_val/exc_tb` ignored (protocol-required `__exit__` params). 6 findings remain
  for a human look — incl. an **"unsatisfiable ternary" at `og_engine/engine.py:638`** (possible
  latent bug; flagged, not silently "fixed" — a real fix needs a failing test first, per the
  regression law).
- **Complexity rail — ruff `PLR0911/0912/0913/0915` (ADVISORY).** Thresholds in
  `[tool.ruff.lint.pylint]` set *above* ruff's strict defaults (max-args 8, branches 15, returns 8,
  statements 60) so only egregious outliers count: 84 vs 178. Deliberately **NOT** added to the
  global `select` — the blocking `ruff check` stays green; preflight runs them as a separate
  `--statistics` step.

**Why advisory-first.** import-linter is green so it blocks today. The other three have non-zero
pre-existing baselines; making them blocking now would turn the gate red forever — the exact failure
mode the plan warns against. They are surfaced every run (like security-audit) and ratchet to
blocking once each baseline reaches zero.

**Verified.** `preflight --quick --no-frontend` green: 5 blocking pass (ruff, mypy-gate, skill-lint,
**import-linter**, eval), 4 advisory warn (security, deptry, vulture, complexity), 0 blocking fail.
Negative test confirmed import-linter detects a fresh cross-layer import. Full `pytest` re-run to
confirm no regression from the pyproject/tooling changes (no `src` touched).

**Deferred.** `jscpd` duplication (Node dep — the one cross-stack add; flagged as optional, not
built). Ratchet deptry/vulture/complexity advisory→blocking as baselines clear. Decide the 4 unused
SDK extras. Investigate the `og_engine/engine.py:638` ternary. Phase 3 (docs/adr + diagrams) /
Phase 4 (role subagents) still deferred. Uncommitted on `main` at time of writing.

## Regression guardrails — Phases 3 & 4: legibility + role subagents (2026-06-27) **[ADR]**

Closed out the guardrails plan's last two phases. No `src` changes — docs, a stdlib docs-lint, and
Claude Code subagent definitions. Phase 3 makes the architecture and its decisions *legible and
self-checking*; Phase 4 encodes the three disciplines a hook can't run as reusable subagents.

**Phase 3 — ADR convention + diagrams + a tiny enforcement.**
- **Two-tier ADR convention, reconciled.** Decisions already lived in two places: the flat
  `DECISIONS.md` log (this file) and numbered MADR ADRs under `docs/architecture/adr/`
  (`0001..0005`, from the architecture engagement). Rather than add a third home, `adr/README.md`
  now states the rule: **`DECISIONS.md` is the default log; a numbered ADR is for a major decision
  that needs alternatives analysis + a long-lived reference, linked from `DECISIONS.md`.** Added
  `adr/0000-template.md` (MADR shape matching the existing five) and an **Index** table.
- **The diagram is the contract.** Converted the Phase-2 ASCII layer block in `ARCHITECTURE.md` to
  a GitHub-rendered **Mermaid** diagram: the three layers (edges → service → engine), the *allowed*
  downward deps, and the **forbidden** upward edges (styled red) that `import-linter` blocks. The
  picture now matches the enforced contract instead of being a stale convention beside it.
- **adr-lint (`scripts/check_adrs.py`, ADVISORY).** A small stdlib check: every `NNNN-*.md` ADR is
  indexed in `adr/README.md` (no orphans), filenames are `NNNN-kebab.md` with unique numbers, and
  each ADR has a `Status:` line + Context/Decision/Consequences. Baseline is **clean** (5/5 pass),
  so it can ratchet to blocking — kept advisory for one cycle, consistent with the Phase-2 rails.
  Wired into `preflight` and CI's `lint` job. A docs convention with no check rots; this is the
  check. The ADR `Status:` lines are left as the engagement wrote them ("Proposed"); the index
  carries a "statuses may lag — `DECISIONS.md` is the outcome of record" caveat, because rewriting
  them is a deliberate edit, not a drive-by.

**Phase 4 — role subagents (`.claude/agents/`).** Three committed Claude Code subagents encoding
the rules a one-shot hook can't (the hooks *block*; these *do the work* the right way):
- **`regression-guard`** (sonnet, can edit) — the regression law as a ritual: failing test FIRST →
  minimal code fix → `docs/bug-bank.md` entry; full *sequential* pytest; the BB-1 Windows fallback.
- **`scope-sentinel`** (inherit, read-only) — reviews a working diff against the contract: scope
  creep, weakened/missing tests, new deps, layering violations, the security guardrails, tokoboros.
- **`gate-keeper`** (sonnet, read-only) — runs `scripts/preflight.py` and reports the REAL result
  (exact pytest counts; never an assumed green; never pushes; never forges the push sentinel).
- **Self-containment is the load-bearing constraint:** a subagent's body is its *entire* system
  prompt — it does NOT inherit CLAUDE.md, conversation history, or the main prompt. So each file
  **restates** its rules; that redundancy with CLAUDE.md is deliberate and must be maintained when a
  rule changes (`.claude/agents/README.md` documents this).

**Why this completes the plan.** Phase 1 = the gate + the locks + memory; Phase 2 = the static
rails; Phase 3 = the map/decisions made legible and self-checking; Phase 4 = the workflow roles.
Every pain (regressions / drift / bugs-returning / same-bug-thrice) now has a *mechanical* counter,
not just advice in a doc the model can rationalise away.

**Verified.** `preflight --quick` green: blocking checks pass (incl. import-linter), advisory rails
warn (now incl. adr-lint reporting OK). `check_adrs.py` exits 0 over the 5 existing ADRs + new
index. No `src` touched, so the full pytest suite (last full run: 3943 passed / 21 skipped / 0
failed) is unaffected.

**Deferred (unchanged).** `jscpd` duplication rail (the one Node-dep add — still flagged optional;
the user is weighing it). Ratchet deptry/vulture/complexity/adr-lint advisory→blocking as baselines
hold. Decide the 4 unused SDK extras. Investigate the `og_engine/engine.py:638` ternary. Uncommitted
on `main` at time of writing.

## B10 — Episodic recall offloaded from inline turn path (`perf/streaming-optimizations`, 2026-06-28)

**Context.** Prior to this change `_chat_impl_inner` ran two synchronous vault calls on every turn:
`semantic_search` (already in the background prefetch) **and** `search_episodic` (always inline,
never prefetched). The episodic call added measurable latency to the turn-start hot path, especially
on slower machines where ChromaDB I/O isn't cached.

**Decision.** Move `search_episodic` into `_run_prefetch_job` alongside `semantic_search`.
Both calls now run in the background job queue between turns (turns 2+). Turn 1 keeps both calls
inline because no prefetch result exists yet — this is the correct fallback and is tested.

**Tradeoff — previous-query staleness.** On turns 2+, episodic recall uses the *previous* turn's
query (enqueued at the end of turn N, consumed at the start of turn N+1). This means the episodic
context in turn N+1 is semantically matched to turn N's question, not turn N+1's. In practice
this is acceptable because:
- Episodic results are long-term memory (prior sessions), not turn-sensitive context.
- The 1-turn lag is almost always invisible; topics rarely pivot between consecutive turns.
- Turn 1 is always exact (inline fallback).

**What to watch for.** If users report that recalled episode summaries feel irrelevant on the first
message of a new topic (turn 2+), the root cause is this lag. Fix: run a second inline episodic call
when the semantic similarity between consecutive queries drops below a threshold — but that adds
complexity; defer until a real complaint surfaces.

**Implementation.** `src/hachi/gateway/orchestrator.py`:
- `_run_prefetch_job` (~line 1203): episodic block added after semantic block; id-keyed dedup.
- `_chat_impl_inner` (~line 1582): turn-1 inline fallback now handles both semantic + episodic with
  the same id-keyed dedup. The old always-synchronous standalone episodic block was removed.

**Locked by.** `tests/test_b10_episodic_recall_offload.py` (10 tests).


---

## Observability sink demoted from span-emitter to attribute-stamper (Phase 2, 2026-07-06)

**Context.** `ObservabilitySink` previously opened zero-duration marker spans for each of 9 bus
events (e.g. `tool.call.started` → a span named `"tool.call.started"`; `session.chat.started` →
`"agent.turn.start"`). This caused double-accounting with the real `agent.turn` root span opened in
`orchestrator._chat_impl` and created empty spans with no timing or child content.

**Decision.** The sink is now an **attribute stamper**: it calls `tracing.add_span_attributes()`
and `tracing.add_span_event()` to stamp correlation ids and well-known payload fields onto the
*currently active* span — never opening a new span. The waterfall is owned by `with span(...)`
wrappers at the work seams; the sink enriches them.

**Consequences.**
- `test_obs_sink.py::test_sink_mirrors_tool_call_started`, `::test_sink_carries_correlation_attributes`,
  `::test_sink_does_not_leak_secrets` were rewritten to assert the new attribute-stamping contract.
  This is an intended contract change, not a weakened test.
- When `events.bus_funnel=false`, direct spans still give the full waterfall; only sink-based
  attribute enrichment is unavailable (Phase 0 degraded-mode warning covers this).

**Locked by.** `tests/test_obs_sink.py` (10 tests, post-Phase-2 contract).

---

## T-31 — OpenAI-compat facade: keep and fix (2026-07-20) **[ADR]**

**Decision.** Option **(a) — keep and fix**, of the four options in
`docs/audit/2026-07-19/tasks/T-31-openai-facade-decision.md` §5. Decided by the product owner on
2026-07-20; recorded here per that task's §6 step 4, and the task's own header was updated to name
the chosen option per step 5.

**Why.** The facade is a real differentiator — "your agent, behind an OpenAI URL" — not a stray
endpoint. It is advertised in `README.md:197` and `:395` as the Open WebUI / LibreChat integration
path, documented in `ARCHITECTURE.md:161` and `docs/guides/configuration.md:435`, and pinned by 19
(now 25) tests plus bug-bank entries BB-48 and BB-58. T-31 §3.1 spells out what deleting it would
orphan — a README promise, three doc sites, the test file, two bug-bank locks — and that is not a
free cleanup; nothing indicated the feature should be dropped. Against that, the two
security-relevant defects (cross-principal session sharing; the session leak toward the 100-session
cap) were each independently estimated at ~half a day and turned out to be exactly that scale, so
"keep and fix" cost little more than the honest alternatives ((b) flag-gate, (c) demote to a
passthrough, (d) delete) while keeping the promise already made in three places.

**Executed as part of this decision — same session, two commits' worth of change:**
- [T-32](../audit/2026-07-19/tasks/T-32-facade-session-isolation.md) (defect 1 — cross-principal
  session sharing via a hardcoded `"__default__"` key and zero ownership check; **BB-109**). The
  non-streaming path now keys sessions `f"openai:{principal}:{conversation}"` (the caller's token
  hash, or `"anon"`, plus the client's OpenAI `user` field or a fresh per-request UUID), and
  `_check_session_ownership` runs on both paths, between session resolution and the turn.
- [T-33](../audit/2026-07-19/tasks/T-33-facade-session-lifecycle.md) (defect 2 — the streaming path
  (and, after T-32, the non-`user`-keyed non-streaming path) minted a session per request and never
  closed it, so the fixed 100-session cap filled permanently; **BB-110**). Both paths now close
  their ephemeral, per-request session in a guarded `finally:`; a `user`-keyed session is left open
  on purpose, because that key exists to let the conversation persist across requests.
- `src/hachi/gateway/fastapi_app.py`, `tests/test_gateway_openai_channels.py` (19 → 25 tests).

**What this decision leaves standing (deliberately, not by omission).**
- **Defect 3 — multi-turn history is discarded.** Explicitly deferred to a *separate design pass*
  (T-31 §9), not folded into T-32/T-33 and not spec'd as an ordinary task. The OpenAI protocol is
  stateless with client-owned history; Hachi is stateful with server-owned memory, and there is no
  obviously-right reconciliation — replay the client's history every turn (duplicate memory,
  unbounded cost), diff and append only what's new (fragile against a client that edits history),
  honour the system prompt but not prior turns (arbitrary), or keep ignoring history and simply stay
  honest about it (today's behaviour, minus the dishonesty already closed by T-32/T-33). Each answer
  is a different product; none of them is "just pass messages through". Still pinned by
  `test_openai_chat_requires_user_content` (a system-only request 400s).
- **Defect 4 — false metering and the echoed `model` both stand, unchanged.** T-32/T-33 did not
  touch either half of this defect — checked by diffing, not assumed (`model_name`/`usage`/
  `prompt_tokens` do not appear as added or removed lines anywhere in the T-32/T-33 diff). `usage`
  in the non-streaming response body is still hardcoded to
  `{"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}`; wiring real `CostTracker`
  usage remains a follow-up, explicitly out of scope for this pass (T-31 §5(a), T-32/T-33 §9). The
  `model` field still echoes `payload.model` rather than reporting the model that actually served
  the turn — it did not fall out of T-32/T-33's session-key/lifecycle changes, so per the batch
  instructions it was left alone and is reported here rather than silently dropped. BB-58's
  *validation* of the requested model (`:1613-1633`) is unchanged and was not touched, per T-31
  §9's explicit instruction not to regress it.

**Locked by.** The task's own header (updated); `docs/bug-bank.md` BB-109, BB-110;
`tests/test_gateway_openai_channels.py` (25 tests, all green).

## T-12 — `ProviderKind` schema: add both `gemini` and `chatgpt-web` (2026-07-20) **[ADR]**

**Decision.** Option **(a)** of the three in
`docs/audit/2026-07-19/tasks/T-12-provider-kinds-schema.md` §5 — add **both** `gemini` and
`chatgpt-web` to the `ProviderKind` Literal in `core/settings.py`, keeping the
`HACHI_UNOFFICIAL=1` runtime gate for `chatgpt-web` exactly as it stood at client
construction (`core/llm.py`). Decided in the task pack itself (recorded as the chosen option
before implementation, per the pack's instruction not to re-open it); recorded here per the
task's own §6 step 1.

**Why.** `create_llm_client` already implements both kinds; `ProviderKind` listed neither, so
`ResolvedConfig.model_validate` rejected the documented `settings.toml` value with a pydantic
`literal_error` and `hachi doctor` separately reported `unknown provider kind`
(`docs/guides/configuration.md:385` promises both). Rejected alternatives: **(b)** add only
`gemini` and delete `chatgpt-web` from the docs — honest, but permanently orphans a working,
tested factory branch with no config route in, trading one dishonesty (schema says
"supported", crashes) for another (documented nowhere, unreachable). **(c)** add both but make
`config_validate.validate_config` additionally require `HACHI_UNOFFICIAL` — more precise about
the risk, but reads the flag in two places (schema-adjacent validation and client
construction) that must be kept in sync, for a benefit the runtime gate already delivers.
**The runtime gate at `llm.py`'s `chatgpt-web` branch already raises a long, explicit ToS
warning at construction** — a user who sets `kind = "chatgpt-web"` without
`HACHI_UNOFFICIAL=1` gets that actionable error instead of an unexplained
`literal_error`, so the schema does not need to duplicate the enforcement. A schema's job is
to accept configurations the program can act on; refusing to *parse* a value the program
already implements is the same "two sources of truth" root cause as the rest of CF-1.

**What this decision leaves standing (deliberately).** The `chatgpt-web` factory branch is
unchanged and still gated; adding it to the Literal does not weaken
`HACHI_UNOFFICIAL` enforcement (pinned by
`test_chatgpt_web_schema_acceptance_does_not_weaken_tos_gate`). Moving `chatgpt-web` to an
opt-in packaging extra remains tracked separately in `docs/strategy/08-roadmap.md:41` /
`docs/strategy/01-architecture-review.md:260` — explicitly out of scope here. The ordering
dependency where the explicit `gemini` block in `create_llm_client` must stay *before* the
generic `_OPENAI_COMPAT_PRESETS` fall-through (§3.6 of the task) is commented in place at
`core/llm.py`, not restructured.

**Locked by.** `docs/bug-bank.md` BB-121; `tests/test_provider_kinds.py` (12 tests, all
green), incl. the AST-derived `test_provider_kind_literal_covers_every_implemented_kind`,
which fails whenever `create_llm_client` grows a `kind == "..."` branch with no matching
`ProviderKind` member.

## T-08 — MemoryGardener: wired up end-to-end (Option A) (2026-07-20/21) **[ADR]**

**Decision.** Option **A** of the two in
`docs/audit/2026-07-19/tasks/T-08-memory-gardener.md` §5 — wire the gardener up
end-to-end (schedule + route + review surface with a real write-back), not Option B
(delete the dead surface, keep the algorithm with a `STATUS: not wired` docstring). The
task doc's own §5 *recommends* Option B, but the decision to ship Option A instead was
made in the task pack itself before implementation started (recorded here per the
task's own step-6 instruction, not re-litigated).

**Why.** Recorded so a future reader does not mistake the task doc's Option-B
recommendation for what actually shipped. All three pieces landed together — the task's
own out-of-scope guard ("do not wire the writer and leave the reader broken") is exactly
the drift Option A forbids landing in parts:

- **A1 — schedule.** `gateway/orchestrator.py`'s `_start_memory_gardener` mirrors
  `_start_eval_report_scheduler` (the E5 precedent) verbatim: daemon thread, sleep loop,
  `min(interval_seconds, 3600)` wake cap, try/except that logs, started from
  `boot_agent` behind a new opt-in `[memory_gardener]` section
  (`MemoryGardenerSettings`, registered on `ResolvedConfig` in the same commit — the
  T-07/BB-101 reflective guard in `tests/test_eval_report_config.py` would otherwise
  fail by name).
- **A2 — route.** `GET /memory/diffs` makes `AgentSession.snapshot_gardener_diffs`
  reachable for the first time, via a new `GatewayControlPlane` handler and
  `_InProcessGatewayRuntime.get_memory_gardener_diffs()`. Fixed the reader's `getattr`
  bug (BB-5 class) **and** a second, previously-unreachable bug the getattr bug had been
  hiding (`GardeningResult._PENDING_DIFFS_FILE` — that constant lives on
  `MemoryGardener`, not `GardeningResult`). OpenAPI + frontend types regenerated (BB-4);
  `check:api` and `typecheck` both clean.
- **A3 — surface + write-back.** This is the piece the source task explicitly flagged as
  "where the real cost is," and it is where most of the new code lives.
  `GardeningProposal` gained a stable `id` (deterministic hash of its identifying
  fields) so `GET /memory/diffs` → `POST /memory/diffs/{id}` round-trips without
  depending on list order. `MemoryGardener.decide()` applies an approval through the
  same append-only `::changelog` mechanism `_apply_dedup` already used — never
  overwriting node content, per the module's own governance rule — or drops a rejection
  untouched; either way it re-persists `pending_diffs.json`. The web frontend's Memory
  screen gained a third "Gardener" tab (list + Approve/Reject), wired through a new
  `memoryGardener.ts` domain module that follows the same non-optimistic
  POST-then-mutate pattern BB-106 established for the Approvals screen — a failed
  decision leaves the card in place instead of pretending the `.og` files changed.

**What this decision leaves standing (deliberately).** No cross-run persistence of
rejected decisions: a rejected proposal can be re-detected (and re-offered) by a later
gardener tick, since nothing records "the user already said no to this specific pair"
outside the current `pending_diffs.json` snapshot. Approve/reject is scoped to a single
proposal at a time — no bulk actions. `SUPERSEDE_CONTRADICTION` is defensively handled
in `_apply_reviewed_proposal` but no detector currently emits it (only
`_find_similar_content`/`_find_expired`/`_find_dedup_same_name` run today). None of these
were asked for by the task and are not implied by "wire it up" — they are noted here so a
future reader does not mistake an absence for an oversight.

**Locked by.** `docs/bug-bank.md` BB-126; `tests/test_memory_gardener_wiring.py` (17
tests — config registration, A1 scheduler-on-interval + 3600s cap + boot_agent AST
check, A2 reader-fix + route end-to-end, A3 decide()/route approve+reject+404, and the
`GardeningResult.load` debug→warning fix); `frontend/src/lib/data/domains/
memoryGardener.test.ts` (6 tests, incl. the BB-106 non-optimistic pattern verified to
fail against an optimistic implementation).

## T-72 — `.og`'s executable surface: internal + lint, for all six tags (2026-07-21) **[ADR]**

**Decision.** `docs/audit/2026-07-19/tasks/T-72-og-spec-executable-surface.md` §5 poses
two options plus a "split" recommendation (Option A — document `::config`/`::tool`/`::api`
as public surface, since `::api` already ships in 10 first-party skills; Option B — mark
all six tokens internal and lint community packs against them). **The product owner chose
pure Option B for all six** — not the split. `::config`, `::tool`, `::api` (block types)
and `role=env`, `role=integration`, `role=mcp` (node roles) are now:

1. **Documented as existing**, in a new `docs/reference/objectgraph-spec.md` §4.7 "Runtime
   & Integration Tags (Internal)" — what each does, its keys, a worked example, and a
   security note anchored to the three `get_nodes_by_role("mcp"|"env"|"integration")` boot
   scans in `gateway/orchestrator.py`. This also documents, for the first time, that a
   `role=` node attribute exists at all (enumerating all five values it can take:
   `skill`, `native_integration`, `env`, `integration`, `mcp`) — the spec previously never
   mentioned it.
2. **Marked internal / first-party-only, not public API, no backward-compat commitment.**
3. **Mechanically enforced** — `scripts/lint_skills.py` (already a blocking preflight step)
   now rejects any of the six outside `src/hachi/default_skills/**`, and allows them inside
   it. A first-party skill using `::api` (10 of them do — `general/currency.og` and nine
   others) still lints clean; a synthetic community-style `.og` declaring `role=mcp` /
   `::config` / `::tool` / `::api` does not.

**Why pure B, not the split.** The split's own case for documenting `::api`/`::config`/
`::tool` as *public* was "`::api` is already de-facto public, so the spec is simply
wrong" — true, but "the spec should describe what ships" and "third parties may build
against this and we will support it" are different claims, and only the first is required
to close the gap this task exists to close (CF-19: the spec had zero occurrences of all six
tokens). Documenting existence already satisfies "a pack author can learn `::api` exists"
and "a reviewer can audit a pack against the spec" — the two harms named in the task's own
§2. It does not require accepting a compatibility commitment for `method`/`endpoint`/
`schema` today.

The decisive asymmetry is **reversibility**, which the task doc itself names as Option B's
argument: promoting an internal tag to public later is additive and non-breaking (nothing
that worked yesterday stops working); the reverse — retracting a tag from public back to
internal — is a breaking retraction of something third parties may already depend on.
Pre-v1, with no sandbox or install-time consent story yet ([T-56](../audit/2026-07-19/tasks/T-56-pack-consent-every-install-path.md)
is still open and cannot state what a pack is consenting *to* until this task landed), the
asymmetric-risk default is "closed until proven safe to open," not "open until proven
unsafe." Locking all six down uniformly is also simpler to reason about and to lint than a
per-tag split — one boundary (`default_skills/`), one rule, no tag-by-tag exceptions to
maintain as the parser grows. `::api`/`::config`/`::tool`'s *first-party* usage is
unaffected either way, so the split's main practical benefit (not disrupting the 10 shipped
skills) is already achieved by "internal + first-party-allowed" without also opening the
tag to community packs.

**What this does not do (deliberately, per the task's own out-of-scope guard).** It does
not change the parser or the runtime — `::api`/`::config`/`::tool` still parse and execute
identically for every node, first-party or not; the lint rule is a source-tree policy
check, not a runtime gate. It does not fix `role=env`'s plaintext-credential handling
(`core/integrations.py`'s `load_env_node` reads `KEY=VALUE` pairs straight out of a
`::dense` block) — that is [T-58](../audit/2026-07-19/tasks/T-58-declarative-integrations-consolidation.md)'s
subject; this task names the risk in the new spec section and stops. It does not build
install-path consent — that is T-56's subject; this task gives T-56 the vocabulary
("internal / reserved / first-party-only") to state what a pack would be consenting to.

**Locked by.** `docs/bug-bank.md` BB-133; `tests/test_p5_01_conformance.py` —
`test_spec_documents_every_parser_block_type` (derives the parser's block-dispatch tokens
from source via regex, not a hardcoded copy, so a new `block_type == "..."` branch fails
automatically) and `test_spec_documents_every_executable_node_role`;
`tests/test_lint_skills_reserved_tags.py` (7 tests — first-party `::api` allowed, community
`::api`/`role=mcp`/`role=integration`+`::config`+`::tool` rejected, a clean community pack
stays clean, the real CLI entry point rejects a throwaway community `.og`, and the no-arg
preflight invocation is unchanged).

## T-61 — Single-tenant authz: delete the C1 ownership machinery (2026-07-21) **[ADR]**

**Decision.** Option **(a) — delete**, of the three options in
`docs/audit/2026-07-19/tasks/T-61-single-tenant-adr.md` §5 (full reasoning:
[ADR-0006](architecture/adr/0006-single-tenant-authz.md)). Hachi is honestly single-principal —
the bearer token IS the identity, and possession of it is full authority over every session, run,
and interaction. Authentication (`require_auth`'s bearer comparison, the `HACHI_GATEWAY_NO_AUTH`
loopback-only opt-out, the startup guards) is untouched; only the per-session *ownership* layer
built on top of it is removed.

**Why.** CF-15 found the ownership system ("C1": sha256 token digests, an in-memory owner map, 403
branches, owner-filtered lists) could never discriminate between callers, because exactly one
bearer token exists per process — every authenticated caller hashes to the same digest, so
`_check_session_ownership` always compared a value to itself. It was also incomplete in ways that
would matter the moment a second principal appeared: the owner map lived only on
`GatewayControlPlane._connections` (wiped every restart), `set_session_owner` silently no-op'd for
a session not yet in that map, and a missing owner was documented policy as *allow* — so the
system's steady state across a process lifetime was "off." That combination — enough ceremony that
a reader trusts it, zero capability to enforce anything — is worse than having no control at all,
and it is exactly why [T-62](../audit/2026-07-19/tasks/T-62-session-messages-ownership-check.md)'s
missing check on two routes went unnoticed for as long as it did: an inconsistency in a decorative
control doesn't read as a bug. `CLAUDE.md` already states the product's stance — single-tenant is
load-bearing, multi-tenant is out of scope pending a decision — so "finish it" (option (b): a
persisted owner column, rotation-independent principal ids, a token-management surface) would have
been building toward multi-tenancy's front door, not fixing this defect.

**Executed.** Removed across `src/hachi/gateway/{deps,control_plane}.py` and
`src/hachi/gateway/routers/{sessions,runs,interactions,channels,openai_compat}.py`:
`GatewayControlPlane.set_session_owner`/`.get_session_owner` and the `owner_token_hash` connection
key; `deps.check_session_ownership` (formerly `fastapi_app._check_session_ownership`) and
`routers/runs.py`'s `_check_run_ownership` wrapper; every 403 call site (nine session routes, three
run routes, two interaction routes); the owner-stamping on session creation (`POST /sessions` and
the OpenAI-compat/channel-inbound facades via `get_or_create_session`/`complete_chat`'s
`owner_token_hash` parameter); the inline owner-filters on `GET /sessions`, `GET /runs`,
`GET /interactions`; and **the ownership check
[T-62](../audit/2026-07-19/tasks/T-62-session-messages-ownership-check.md) had just added to
`GET /sessions/{id}/messages`** in Wave 1 — deleting the machinery means that check goes too,
coherently, and T-62's own file now records the supersession. `require_auth` (`gateway/deps.py`)
no longer returns the sha256 digest — its only consumer was ownership — and now returns `bool`
(`True` when a configured token was validated, `False` in the two pre-existing no-enforcement
modes); its `CallerHash` dependency alias is renamed `Authenticated` to match. Two unrelated
features that had opportunistically reused the digest (BB-58's per-request OpenAI rate-limit key;
T-32's facade session-key namespacing) were adapted to the boolean contract with a fixed
`"authenticated"`/`"anonymous"` marker rather than deleted — under one token both were already
single shared buckets per auth mode, not per-principal ones, so no observable behaviour changed.

**Correction to the task spec's own evidence.** T-61 §3.2/§4 asserted
`grep -rn "owner" src/hachi/core/session_store.py` "returns nothing today," verified against
`main @ bd24afe`. That was wrong even against that exact commit (`git show bd24afe:...` still
shows it) — `SessionStore.set_session_owner`/`.get_session_owner` and a persisted
`owner_token_hash` column have existed there, completely unreferenced by any caller anywhere in
the codebase, since 2026-06-20 (a "sprint 1" security pass, `6653d1e`). The gateway's enforcement
was always in-memory-only exactly as CF-15 diagnosed regardless — this was a second, independently
orphaned "finish it" attempt nothing ever wired up, not evidence the two designs coexisted. Removed
alongside the gateway-side machinery (safe: no `sessions` query anywhere does `SELECT *`, so the
column was never serialized into a response) so the ADR's "no ownership symbol survives" claim is
true of the codebase, not just of the file the stale task spec happened to check.

**Reversibility.** Medium — see ADR-0006's Consequences. The deleted code is in git history and
the ADR records its exact shape and defects, but reversal should mean building option (b) properly
(persisted owners, rotation-independent identities, dependency-enforced uniform coverage,
fail-closed default) rather than restoring `set_session_owner` verbatim.

**Locked by.** `docs/bug-bank.md` BB-136; `tests/test_session_authz_model.py` —
`test_gateway_is_single_principal_by_decision` (structural: no ownership symbol survives anywhere
under `hachi.gateway` or in `core/session_store.py`; behavioural: the one authenticated principal
reaches every session route, including the `/messages` transcript endpoint, with no owner gate).
Pre-existing ownership-specific
tests in `test_session_ownership.py`, `test_session_route_ownership.py`, `test_gateway_interrupt.py`,
`test_gateway_router_extraction.py`, and `test_gateway_openai_channels.py` were rewritten (not
deleted) to assert the new contract — see the T-61 commit message for the itemized list.

## T-54 — Every SQLite store gains a `schema_version` table (2026-07-21) **[ADR]**

**Decision.** Implemented `docs/audit/2026-07-19/tasks/T-54-schema-version-tables.md` §5
as written — a **table**, not `PRAGMA user_version` (Decision 1); every existing,
unstamped database is adopted as **version 1**, never as the running build's `current`
(Decision 2); the dead `HachiPaths.tui_state_db` property is deleted outright rather than
versioned (Decision 3). `core/migrations.py` — already the shared home for
`apply_migrations`'s additive column helper — gains `SCHEMA_VERSION_TABLE`,
`ensure_schema_version(conn, *, current)`, `set_schema_version(conn, version)`, and a new
`SchemaTooNewError`:

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    id             INTEGER PRIMARY KEY CHECK (id = 1),   -- exactly one row, ever
    version        INTEGER NOT NULL,
    app_version    TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
```

All **five** live SQLite stores now call `ensure_schema_version` immediately after their
`CREATE TABLE IF NOT EXISTS` block and before any `apply_migrations` call: `sessions.db`
(`core/session_store.py::_migrate_schema`), `files.db`
(`core/files/store.py::FileMetaStore.__init__`), `runs.db`
(`core/runtime/store.py::_init_schema`), `jobs.db` (`jobs/queue.py::_init_db`), and
`memory_index.db` (`core/memory_index.py::_init_db`) — all stamped **current = 1**, since
T-54 is the version-1 baseline for every one of them. The two ad-hoc
`PRAGMA table_info` + `ALTER TABLE` blocks (`session_store.py`'s four-column
`sessions`/one-column `messages` migration, `jobs/queue.py`'s `owner_id` backfill) were
converted to `apply_migrations` calls, preserving every inline comment that explained
*why* a column exists (`C1`, `Bug 6`) rather than just *that* it does. `rg -n
"PRAGMA table_info" src/` now returns exactly one hit — `apply_migrations` itself.

**Fail-on-newer, with one deliberate exception.** `ensure_schema_version` raises
`SchemaTooNewError` when the stored version exceeds `current` — a database written by a
newer Hachi build must refuse to open silently, the whole reason this task exists (§2 of
the task doc: "no way to refuse to open one that is newer than the running build"). Every
store's constructor lets this propagate; none of the five catches it. The one exception is
`memory_index.db`: it is a **derived cache**, wholesale-rebuilt from `memory/*.og` on every
`refresh()` (`MemoryIndex.refresh()` unconditionally `DELETE`s and re-inserts — see the
task doc §3.5), so a version mismatch — in *either* direction, not only "newer than this
build" — has a remedy the other four stores do not: drop `mem_nodes`/`mem_fts`, recreate
them, re-stamp the current version, and let the next `refresh()` repopulate. This exception
is intentionally not generalised — `SchemaTooNewError`'s docstring and the four
non-cache stores' call sites both say so explicitly, because "just rebuild it" is exactly
the wrong instinct for `sessions.db`.

**`app_version` without importing the `cli` edge.** The `schema_version` row's
`app_version` column needed the installed package version, which `cli/main.py`'s
`--version` flag already resolves via `importlib.metadata.version("hachi-og")`. `core`
must not import `cli` (the enforced layering), so `core/migrations.py::_app_version()`
independently repeats the same stdlib `importlib.metadata` lookup rather than importing
the CLI's helper — a few duplicated lines, not a layering violation, with the same
best-effort fallback shape.

**`sessions.db`'s second schema owner.** `CorrectionLog` (`core/user_profile.py`) opens
its own connection to `sessions.db` and creates `profile_corrections` independently of
`SessionStore`. `SessionStore` is the sole owner of the `schema_version` row for this
file — `CorrectionLog` is unchanged and does not read or write it. Version 1 is defined as
"whatever `sessions.db` contains when either schema owner has run," which already includes
`profile_corrections`; `CorrectionLog` does not need its own stamp because it has no
independent migration path to version *against* (`_ensure_table` is, and remains, a bare
`CREATE TABLE IF NOT EXISTS`). `core/user_profile.py` is out of this task's file list and
was not touched.

**Why a table, not the pragma (Decision 1, restated).** `PRAGMA user_version` is a bare
integer: no room for a timestamp or the writing app's version, invisible to `sqlite3 .dump`,
and trivially clobbered by any code that happens to call `PRAGMA user_version = N` for an
unrelated reason. A table survives backup/restore visibly — which matters for
[T-55](../audit/2026-07-19/tasks/T-55-backup-completeness.md) — and a user debugging their
own data can `SELECT * FROM schema_version` with the stock `sqlite3` CLI, extending the
"owned data" promise to the metadata layer too.

**Why version 1, not schema archaeology (Decision 2, restated).** Probing an existing
database for the presence of `owner_token_hash` or similar to infer a "true" historical
version was considered and rejected: it is guesswork against real user data this task has
no access to, it is untestable, and it is unnecessary — `apply_migrations`'s additive-only
discipline already makes every historical shape of these tables forward-compatible. An
unstamped database is defined as version 1, full stop; a future reader must not mistake
`version = 1` for "freshly created" — it means "whatever shape shipped before T-54."

**What this decision leaves standing (deliberately).** No rollback/downgrade path —
`apply_migrations` remains additive-only, and fail-on-newer *is* the downgrade story, not a
gap in it. No storage consolidation (CF-13's longer-term "one state SQLite + Chroma +
blobs" recommendation is explicitly out of scope — this task makes that consolidation
*possible* by giving every store a version to migrate from, it does not start it). No
versioning of the JSON stores (`schedules.json`, `skill_usage.json`,
`onboarding_state.json`, `secrets_metadata.json`) or the three ChromaDB
`PersistentClient` roots — different mechanism, different task, noted in the source task
doc §9 and not relitigated here. `runs.db` stays on `data_dir` rather than moving to
`state_dir` with its SQLite siblings — [T-53](../audit/2026-07-19/tasks/T-53-jobs-db-path-unification.md)
already deferred that move deliberately; a one-line comment at
`core/runtime/store.py::_default_db_path` now says so explicitly, so the anomaly in the
task doc's own store inventory (§3.2) does not read as an oversight to the next person who
greps for it.

**Locked by.** `docs/bug-bank.md` BB-137; `tests/test_migrations.py` —
`TestSchemaVersion` (6 tests): `test_every_live_sqlite_store_records_a_schema_version`
(built from the real store constructors, not a hardcoded filename list, so a sixth
unversioned store added later breaks it), `test_unstamped_database_is_stamped_as_version_one`,
`test_opening_a_future_version_database_raises`,
`test_opening_sessions_db_at_a_future_version_raises` and
`test_opening_runs_db_at_a_future_version_raises` (§8's explicit acceptance criterion —
the raise must propagate through the real store constructor, not only through
`ensure_schema_version` called in isolation; verified RED by temporarily disabling the
`SessionStore` call site and confirming `DID NOT RAISE` before restoring it), and
`test_memory_index_rebuilds_instead_of_raising_on_version_mismatch` (asserts the stamped
version is corrected back to current on open — the one check that cannot pass "by
accident" from `refresh()`'s own unconditional rebuild, which would otherwise mask a
`MemoryIndex` that never wired the version check at all).

## T-74 — Repo hygiene sweep: named design trees, narrowed globs, sdist worktree leak, root shim (2026-07-25/26) **[ADR]**

**Decision — name before narrowing, always.** `.gitignore` carried unanchored globs that
silently hid seven design-tool trees (52,598,381 B) with zero signal. Fixed by naming each
tree by-name FIRST (`.ds-sync/`, `ds-bundle/`, `.design-bundle/`, `.design-sync/`,
`.stitch-review/`, `/Hachi Web UI/`), THEN narrowing the globs to repo-root-anchored
(`/*.png`, `/*.jpg`, `/*.jpeg`, `/*.gif`, `/*.html`) — reversing that order would dump
52.6 MB into `git status` in one step. `frontend/.tanstack/` (TanStack Router's cache dir,
empty and therefore invisible today) was also ignored by name.

**Decision — delete the two no-op extras, don't restructure base.** `memory` and
`local-models` duplicated `dependencies` character-for-character. Removed both rather than
moving `chromadb`/`httpx` out of base — that would be a breaking install-UX change, and
`httpx` is used far beyond local-model providers. `observability` is documented in-line as
a deliberate strict superset of `tracing`. `unofficial = []` is untouched (a deliberate ToS
gate, not a no-op).

**Finding — the sdist leak is real, not already fixed, and is a hatchling defaulting gap.**
A fresh `uv build` still swept `.claude/worktrees/` into the sdist (20,033,163 B sdist vs
1,111,246 B wheel, ~18x; worktrees alone were 60,643,104 B uncompressed, ~80% of the
archive). Root-caused against the installed `hatchling` source: its default sdist
VCS-exclusion reads only the repo-ROOT `.gitignore` and never walks into nested
`.gitignore` files, so the rule in `.claude/.gitignore` was invisible to it. Fixed with the
minimal targeted `[tool.hatch.build.targets.sdist] exclude = ["/.claude/worktrees"]` — no
broader allowlist written. Post-fix: 3,940,545 B sdist, ~3.55x, a normal ratio. Locked by
`test_sdist_excludes_claude_worktrees`, which drives a real `uv build --sdist` rather than
reimplementing hatchling's matcher; proven real by temporarily reverting the config and
watching it fail with 5,328 leaked files before restoring it.

**Decision — fix the root `hachi` shim rather than delete it.** `uv run hachi` and an
activated venv already work without it, so deletion was legitimate, but the fix is small.
Rewrote `hachi` to detect `.venv/bin/hachi` (POSIX) vs `.venv/Scripts/hachi` (Windows)
instead of hardcoding Windows, confirmed pure-LF byte content, and added `.gitattributes`
(`/hachi text eol=lf`) so line endings cannot regress. `hachi.bat` untouched. The tracked
mode of `hachi` was `100644` (non-executable) since its first commit; flipped to `100755`
via `git add --chmod=+x` at commit time so `./hachi` (direct invocation) works on a fresh
POSIX clone — `bash hachi` never needed the bit and already worked.

**Flagged for the user, not decided — `design-system/`'s disposition.** 65 tracked files,
last touched 2026-06-27; its 7 shared components with `frontend/src/components/ui/` (last
touched 2026-07-19) differ in every case, and the frontend carries 5 `.test.tsx` files the
design-system copy has none of — `frontend/` is the implementation source of truth. But
`design-system/` also holds `.prompt.md` authoring prompts, tokens, templates, and three
components (`Badge`, `Card`, `RunCard`) with no frontend equivalent — deleting it destroys
material that exists nowhere else. Per the task doc's explicit instruction, nothing under
`design-system/` was deleted, moved, or `git add`ed; the newly-visible untracked files
there (surfaced by the glob narrowing) were left untracked, pending a user decision (keep as
brand canon, move out of the product repo, or delete only the 9 duplicated components).

## T-49: Adopt the MemoryProvider seam (not delete it) (2026-07-26) **[ADR]**

**Context:** CF-12 found `MemoryProvider`'s ABC contract methods (`upsert`/`search`/
`delete`) had zero production callers — boot resolved a provider, called `initialize()`,
then unwrapped straight back to a raw `MemoryVault` (or `None` for the two non-Chroma
backends). The audit spec recommended deletion (option A, ~1 day) as the safer default,
but flagged adoption (option B, ~4 days) as legitimate if a real second backend was
wanted. Product decision: adopt.

**What changed:** `core/memory_provider.py` gained `ProviderBackedVault`, a facade that
funnels every read/write for `in_memory`/`fts5_only` through the provider's real
`upsert()`/`search()`/`delete()`. `gateway/shared_services.py` and `gateway/orchestrator.py`
build this facade instead of discarding the provider to `vault=None`. `core/user_profile.py`
gained a parallel provider-backed code path (its Chroma path deliberately bypasses
`upsert_user_fact`'s category normalisation via private vault internals that don't exist on
the facade; the facade doesn't normalise categories, so no bypass is needed there).
`attach_graph_index` was promoted to a no-op default hook on the `MemoryProvider` ABC (only
`FTS5MemoryProvider` overrides it meaningfully) so the facade forwards it directly rather
than via a defaulted `getattr` probe (BB-138).

**What this task did NOT do:** the full option-B shape (MemoryManager holding a provider
instead of a vault, `tools/memory_tools.py` calling `provider.upsert/search/delete`
directly, `set_vault()` -> `set_provider()`) was out of scope — it requires editing
`core/memory.py` and `tools/memory_tools.py` and is a materially larger, separate change.
What landed makes `in_memory`/`fts5_only` genuinely work end-to-end today (workspace facts,
user profile, agent profile, snapshots) through the real ABC contract; deep Chroma-only
features (episodic memory, delegation search, the MemoryGuard quarantine trail, a hard
capacity cap) are honestly unavailable on these backends rather than faked.

**Why this is the right boundary:** the ABC's 3-method contract genuinely does not cover the
~12-method vault surface `memory_tools.py`/`user_profile.py` use. Building full parity would
mean re-implementing most of `MemoryVault` against the in-memory/FTS5 providers — a
Chroma-shaped simulation with its own untested surface, not "adoption." Locking adoption to
what the ABC actually contracts for, and being explicit about what stays absent, keeps
`memory.backend`'s claims honest without inventing new unverified behaviour. `memory_provider.py`
is now load-bearing, which [T-48](../audit/2026-07-19/tasks/T-48-memory-package-boundary.md)
must preserve when it moves the module.

## T-48: Memory modules moved into a core/memory/ package (2026-07-26) **[ADR]**

**Decision:** the twelve flat `core/memory*.py` modules are now a `core/memory/` package
(`manager.py`, `vault.py`, `provider.py`, `writer.py`, `index.py`, `graph.py`,
`graph_recall.py`, `gardener.py`, `guard.py`, `importer.py`, `exporter.py`,
`conversation.py`). Pure churn — a 251/251-line 1:1 move, no behaviour change. The four
behavioural memory tasks (T-49..T-52) landed FIRST by design, precisely so this 197-import
rename could not invalidate their line anchors or make a semantic change indistinguishable
from a move in the diff.

**Narrow public surface (Decision 1):** `core/memory/__init__.py` re-exports exactly
`MemoryManager`, `extract_facts`, `build_episode_summary`. Everything else (private
constants like `_MAX_ACTIVE_TOKENS`, the writer/index/vault classes) is reached via its
explicit submodule (`hachi.core.memory.manager`, `.vault`, ...), not the bare package —
five test files that reached private names off the old flat module were redirected to
`.manager`.

**Import-linter unchanged:** the contracts name `hachi.core` as a whole and
`hachi.core.memory` is still inside it; `og_engine`'s import of `hachi.core.memory.guard`
stays legal under the "og_engine is pure" contract (which forbids gateway/cli/tui/tools,
not core). Re-verified 3 kept / 0 broken.

**memory_provider.py kept, not deleted:** T-49 adopted the MemoryProvider seam (it now
holds the load-bearing `ProviderBackedVault`), so it moved to `memory/provider.py` rather
than being deleted — the task doc's "T-49 may delete it" note predates that decision.

**Config repathed:** `mypy-strict-gate.ini` (9 entries from T-69's harvest),
`scripts/check_getattr.py`'s allowlist (6 keys), and `openapi/hachi-openapi.json` +
`frontend/src/lib/data/schema.d.ts` (BB-4 — one Pydantic model description echoed the moved
module path) were all regenerated/repathed. Lesson: a package move must repath every
by-path config and every generated artifact that named the old flat modules, or the gate
breaks on stale paths.

**Locked by:** `tests/test_memory_package_boundary.py`; docs/bug-bank.md BB-148.

## T-41: The in-process gateway backend now lives in the gateway, not the CLI (2026-07-26) **[ADR]**

**Decision:** `_InProcessGatewayRuntime` — the entire in-process gateway backend — moved
verbatim out of `src/hachi/cli/main.py` into `src/hachi/gateway/in_process_runtime.py`.
2,558 lines and 86 methods, relocated byte-for-byte. `cli/main.py` drops 5,858 -> 3,276
lines and is a CLI again. This is the keystone Wave-2 task: it unblocks T-42 (retire the
`core -> cli` seam) and T-43 (collapse the control-plane interfaces).

**Zero behaviour change, and that was the acceptance bar.** The moved span diffs
byte-identical against the pre-move original except one line, where `ruff` stripped
now-redundant quotes from `rehydrate_session`'s return annotation under
`from __future__ import annotations`. `cli/main.py` gained exactly five lines: the class
import, the `in_process_runtime as _runtime_mod` import, the rewritten worker read, and a
ruff-collapsed import. No renamed method, no changed signature, no "while I'm here" fix.
The class kept its leading underscore deliberately — renaming is a separable change that
would churn every test patch string a second time.

**The runtime keeps its module-level worker slot, read through the module object.**
`_GATEWAY_RESIDENT_WORKER` moved with the class; `cli/main.py`'s `_gateway_readiness_probe`
reads it as `_runtime_mod._GATEWAY_RESIDENT_WORKER`. A `from ... import
_GATEWAY_RESIDENT_WORKER` would snapshot `None` at import time and report the worker dead
forever — the single easiest way to break this task, so it has a dedicated regression test.

**`_pack_display_name` moved too, outside the task's stated file list.** The helper sat
just after the class, had exactly one consumer (`get_kennel`), and importing it back from
`cli.main` would have reintroduced the precise `gateway -> cli` inversion this task removes.
Accepted as additive and byte-identical (BB-149).

**Deliberately NOT done here:** `_ACTIVE_RUNTIME`, `_start_gateway_server`,
`_build_gateway_control_plane` and `_gateway_readiness_probe` stay in `cli/main.py`; the
`hachi.core.runtime.resume -> hachi.cli.main` import-linter exemption stays untouched
(T-42 owns it, and it is trivial once this has landed); the class was not split into the
13 domain groups its methods partition into (T-43). `config` / `models` / `permissions`
are Typer commands shadowed by locals inside the class and stay put.

**By-path config had to follow the file:** `scripts/check_getattr.py`'s allowlist keyed two
pre-existing, already-justified entries (`_og_engine_cache`, `reindex_skills`) to
`src/hachi/cli/main.py`; they were repathed, not re-suppressed. Same lesson as T-48 — a
module move breaks every config that names a file by path.

**Locked by:** `tests/test_in_process_runtime_extraction.py`; docs/bug-bank.md BB-149, BB-150.

## T-42: The `core -> cli` seam is closed, and the import-linter exemption list is now empty (2026-07-26) **[ADR]**

**Decision:** `core/runtime/resume.py` no longer imports `hachi.cli`. Cold-start resume now
depends on a `SessionRuntime` protocol owned by `core`, registered from the gateway edge.
The standing `ignore_imports` entry for `hachi.core.runtime.resume -> hachi.cli.main` is
**deleted**, so the "engine must not import the UI/entry edges" contract now holds with no
holes cut in it. This was the last genuine layering inversion in the repo.

**The protocol stays the size of the dependency.** Exactly two methods — `get_live_session`
and `rehydrate` — because the entire seam was two private attributes and one method call.
`get_live_session` takes `_agents_lock` internally so no caller ever sees it. `rehydrate`
wraps the existing `rehydrate_session`, which is retained for its other callers. Widening
this protocol would just recreate the handler bag that T-43 exists to undo.

**Registration, not import.** `set_active_session_runtime` / `get_active_session_runtime`
live in `core/runtime/protocols.py` beside T-68's `ProcessServices` seam — the same shape:
`core` declares the contract, the edge injects an implementation downward, and `core` never
names `hachi.gateway` or `hachi.cli`. `_ACTIVE_RUNTIME` in `cli/main.py` is deleted outright
rather than kept alongside; a parallel slot is how this seam grows back.

**Two readers outside the task's file list had to move with it.**
`cli/commands/channel.py` and `cli/commands/schedule.py` read the same global via
`getattr(_main_mod, "_ACTIVE_RUNTIME", None)` to obtain the *full* runtime for
`boot_registered_session`. Deleting the slot would have degraded both to `None` silently —
no exception, no nearby test failure — regressing governed channel/schedule boot to the
ungoverned path. Both now read the registered runtime, typed loosely at those two call
sites because they legitimately need the concrete class rather than the narrow protocol
(BB-151).

**The linter is the completion proof.** `import-linter` 2.12 defaults
`unmatched_ignore_imports_alerting` to `ERROR`, so an exemption that no longer matches a
real import *raises*. Removing the import while leaving the exemption produced
`No matches for ignored import hachi.core.runtime.resume -> hachi.cli.main`; deleting the
exemption then went green at 3 contracts kept / 0 broken. That is a stronger signal than a
passing test, and it is why the exemption comment being wrong (it claimed the seam needed
the Typer app; it never did) went undetected for so long.

**Locked by:** `tests/test_core_cli_seam.py`; docs/bug-bank.md BB-151.

## T-59: A turn's outcome is a value, not a substring (2026-07-26) **[ADR]**

**Context.** `ExecutionLoop.run()` returned `str`, so every terminal condition — user
cancellation, wall-clock timeout, budget exhaustion, workflow suspension, iteration-limit
cutoff — had to be smuggled back inside the assistant's text as a prefix. Nine return
sites, five prefixes, plus a sixth emitter in `gateway/orchestrator.py` hardcoding
`[BUDGET LIMIT REACHED]` independently. Meanwhile `core/errors.py` held a real eight-class
`HachiError` taxonomy that the loop used for nothing, and `loop.py` defined its own
`AgentException(Exception)` outside it — two roots.

**Decision: a `TurnOutcome` value object, not `HachiError` subclasses.** Terminal
conditions are *normal control flow*, not errors — a budget cap firing is the system
working. Raising would make `run()` throw on success-adjacent paths and force every caller
into a try/except, including the ones that legitimately want the partial text. It would
also mean smuggling the load-bearing partial text (what the user actually sees after a
cancel) through exception attributes, which is the same defect wearing a different hat.
`AgentException` is reparented under `HachiError` regardless, so the two-root problem goes
away even though the loop no longer needs the class.

**Decision: keep the wire format, render it from one place.** `TurnOutcome.render()`
reproduces the existing strings byte-for-byte and `run()` still returns `str`; the
structured value is exposed alongside via `ExecutionLoop.last_outcome`. This makes the
change behaviour-preserving, which is the only way to land a nine-site rewrite of the
hottest path in the product without churning the eval baseline. Deleting the user-visible
prefixes is a separate, deliberate decision with its own review — not a side effect of a
refactor.

**The "nine variants" were five.** The task brief forbade collapsing two pairs of
near-duplicate sites on the grounds that they produced different text. Verified against
source: they do not. Sites 1/5 (`[CANCELLED]`) are the same expression character-for-
character, and sites 2/6 (`[TIME LIMIT REACHED]`) are differently-shaped f-string
concatenations that evaluate to identical strings. The real difference is in the
`on_event` / `memory.complete_task` calls *surrounding* each return — `"Cancelled:"` vs
`"Cancelled (mid-stream):"` — which are not part of the return value and are left
untouched. So `render()` has five branches, and the prohibition applies to the
neighbouring calls, not the rendering. Diff the output, not the expression (BB-152).

**The eval corpus constrains the sentinel in both directions.** `[INCOMPLETE]` is asserted
present in the `max_iterations_cap` case and absent in `no_internal_markers_in_clean_output`.
A rename satisfying every `output_not_contains` still fails the `output_contains`, and
`hachi eval run` blocks preflight — where the failure would read as a model regression
rather than a refactor. Baseline was captured before dispatch and re-checked after:
14 passed / 0 failed, unchanged.

**`_last_outcome` is initialised in `__init__`, not via `getattr`.** The sibling
`_last_suspend` / `_last_tool_calls` use `getattr(self, ..., None)` because they carry a
deliberate "absent before the first `run()`" contract that a real test asserts (BB-107).
`_last_outcome` has no such contract, so a plain attribute read is correct and the
`scripts/check_getattr.py` allowlist does not grow. Copy a pattern's reason, not its shape.

**Scope held.** The `startswith` check in `sub_agent.py` is retained as a documented
one-release fallback rather than deleted; `INCOMPLETE_RESULT_PREFIX` and `_last_suspend`
both stay (the latter is read by `gateway/orchestrator.py`); `SuspendWorkflow` is *not*
reparented under `HachiError` because it signals a clean suspend, not an error; and the
wider `errors.py` migration — `ToolError`/`MCPError` opportunities exist throughout the
codebase — is explicitly left undone. Four files changed, all nine string-asserting test
files pass unmodified.

**Locked by:** `tests/test_turn_outcome.py`; docs/bug-bank.md BB-152.

## T-43 (round 1 of 2): Gateway ports are grouped by domain *and* by availability (2026-07-26) **[ADR]**

**Context.** `GatewayControlPlane.__init__` took 77 parameters — 74 optional handler
callables plus a readiness probe and two tunables — wired from a single 95-line call in
`cli/main.py`. Every new endpoint cost four edits (runtime method, constructor parameter,
delegate method, wiring line), and because all 74 defaulted to `None`, a half-wired control
plane constructed successfully and failed at the first request. The 44 `_require_handler`
guards exist only to compensate.

**Decision: eighteen ports, not fourteen — because there are two axes, not one.**
The task brief proposed fourteen domain protocols and asserted the wiring maps as
"`sessions=runtime`, everything else `=support`". That is false. Classifying all 77 kwargs
at the real builder shows 28 are `runtime`-gated (`runtime.X if runtime else None`, and
`runtime` is `None` before a session boots) and 46 are `support` (unconditional). That split
**cuts across domain lines**: Skills/kennel, Spend, Memory, Observability and
Session/transport each contain handlers of both kinds. Collapsing a mixed group onto one
object changes availability in one direction or the other, with no failing test nearby.

So the decomposition is **domain × availability**: eighteen ports, six of them prefixed
`Live*` (nullable, needs a session) and twelve always wired. The prefix puts the previously
invisible axis into the type name — that axis had existed only as a ternary repeated 74 times
in a builder function, which is why no one could tell from the signature which handlers a
working gateway requires (BB-153).

**Decision: ports are named for the provider, never the consumer.** 19 of the 74 members have
a runtime method name that differs from the constructor kwarg (`on_connect` →
`register_connection`, `add_skill` → `add_skill_from_kennel`). Renaming runtime methods to
match would churn the test patch strings T-41 had just re-pointed, so the protocols match the
runtime. `_InProcessGatewayRuntime` was verified to satisfy all 74 members with no changes.

**Decision: round 1 is additive; it deletes nothing.** The brief scoped test impact at two
files. It is 23 files and 99 call sites — every construction passing a handler kwarg breaks
when those kwargs go away. So the work splits: round 1 introduces `ports.py`, adds the 18
port parameters *alongside* all 74 originals, switches the production builder to ports, and
adds a `control_plane(**handlers)` test helper. Round 2 converts the 99 sites and retires the
old kwargs.

Resolution happens in `__init__` alone —
`self._on_chat = on_chat if on_chat is not None else (sessions.chat if sessions else None)` —
so **not one delegate body changes and no stateful method changes at all.** Everything from
`_require_handler` onward is byte-identical, which matters because about half of this class
is real logic, not façade: `create_session`'s connection accounting and failed-connect slot
release, the session and prompt limits, and the protocol-version check exist nowhere else.
The source review's claim that the class "contains almost no behavior" is refuted, and a
pass-through collapse planned on that premise would have deleted working code.

**Deliberately not done here:** the 14→3 port merge the review suggested (it produces
god-protocols and loses the mapping to the runtime's own method groups), making ports
required (that forces every partial-construction test to supply stubs in the same diff), and
deleting `_require_handler` (ports are still nullable, so it stays load-bearing).

**Locked by:** `tests/test_control_plane_characterization.py`; docs/bug-bank.md BB-153.

## T-43 (round 2 of 2): the 74 legacy handler kwargs are retired (2026-07-26) **[ADR]**

**What landed.** Every legacy call site moved to the `control_plane(**handlers)` helper, and
the 74 handler kwargs were deleted from `GatewayControlPlane.__init__`. The constructor is
now **21 parameters** — `readiness_probe`, 18 ports, and 2 tunables — down from 77. Handler
resolution simplified from the round-1 two-branch form to `self._on_chat = sessions.chat if
sessions else None`.

**The boundary held across both rounds.** Everything from `_require_handler` onward is
byte-identical to the **pre-T-43** baseline. A 77→21 parameter collapse and 94 rewritten call
sites changed no delegate body and no stateful method: `create_session`'s connection
accounting and failed-connect slot release, the session and prompt limits, and the
protocol-version check are provably untouched. That was the entire point of resolving in
`__init__` rather than rewriting the ~38 delegates to reach through ports.

**The compatibility knowledge lives in the tests, not the API.** `control_plane()` and its
74-entry legacy→port table stay in `tests/conftest.py`. That is deliberate: the mapping from
old kwarg names to ports is test convenience, and putting it there keeps it out of the
production constructor while making the 94-site conversion mechanical enough that **no
assertion changed** anywhere in the suite.

**One test outlived its purpose.** `test_control_plane_helper_matches_legacy_kwargs_form`
existed to prove the helper equivalent to the raw legacy-kwargs form. Retiring those kwargs
makes that comparison impossible — not by weakening the test, but by removing the thing it
compared against. It is renamed to `test_control_plane_helper_builds_a_working_control_plane`
and its duplicate construction collapsed, keeping every assertion: a real chat round-trip, a
real config read, and the unknown-kwarg guard. A test whose name promises something
untestable is worse than no test, and a renamed test with intact assertions is not a weakened
one — but the distinction is worth stating, because "the test's subject ceased to exist" is
exactly the argument a genuinely weakened test would also make.

**Ports stay nullable and `_require_handler` stays.** Making ports required is a separate
change; while they are optional, that guard is what turns a missing handler into a clean
`GatewayUnavailableError` rather than an `AttributeError`. `ports.py` is strict-mypy-clean but
deliberately not added to `mypy-strict-gate.ini` — T-69 owns that ratchet.

**Locked by:** `tests/test_control_plane_characterization.py`; docs/bug-bank.md BB-153, BB-154.

---

## T-77 — Prompt text gets an owner: 17 stray literals become 18 named, versioned templates (2026-08-18) **[ADR]**

**Found.** `core/prompt_composer.py`'s own docstring asserted an invariant the codebase
violated: *"no other module should concatenate system-prompt strings directly."* Eight auxiliary
one-shot LLM call sites did exactly that, carrying 17 inline prompt literals no test had ever
looked at. `core/memory/manager.py` held nine of them, across four extraction/summarisation
prompts that read as near-duplicates and are not — **each applies a different gate to the
model's response.** `_is_substantive_fact` (`manager.py:144`) runs on the flat extraction path's
three buckets (`:392`, `:400`, `:408`) and in `_flush_facts_to_vault` (`:1555`), but **not** on
the typed/tool path (`extract_facts` `:184`, branch at `:239`), which validates response *shape*
only — a three-character fact goes straight to `write_nodes`. `_generate_structured_summary`
(`:1592`) applies **no** validation at all: it returns `response.content.strip()` verbatim into
live conversation history, despite a prompt demanding five specific `##` sections.

**Decided.** `prompt_composer` gains a second, separately-testable responsibility beside its
existing assembly functions: a named, versioned template registry — `PROMPT_REGISTRY_VERSION`
(`:169`), a frozen `PromptTemplate` (`:175`), and `register_prompt`/`get_prompt`/`all_prompts`
(`:196`–`:211`). `get_prompt` raises `KeyError` on an unknown name and `render` raises on a
missing placeholder; neither has a silent default, because a silently-empty prompt is the worst
failure this module could have. All 17 Tier A literals moved in **verbatim**. `build_stable_base`
and `compose` are byte-identical and their 18 tests pass unchanged — that is the proof the change
stayed in its lane.

**Text moved; logic did not.** Every parse step, fence-strip, gate call, truncation and dedup
stayed in the same function in the same order. The flush path still concatenates its snippet at
the call site; `transcript[:8000]` still truncates where it did; the fence-strip asymmetry
between the flat and flush paths is preserved. The four memory prompts are four registry
entries — unmerged and unreworded — and `_is_substantive_fact` is still called at exactly four
sites and still absent from the typed path.

**Two decisions the task's own spec got wrong, resolved here.**

*Byte-equality has to be asserted on the rendered prompt, not on `.text`.* Five of the 17
literals are f-strings, which have no single original byte value — only a shape — and two of
those also contain literal JSON-example braces (`{"user_facts": [...]}`,
`{"delete_ids": [...]}`) that `str.format` would choke on. Those two escape their literal braces
as `{{`/`}}` in `.text` and render around them. Pinning `.text` would therefore be impossible
for some entries and meaningless for others; pinning the **rendered output** is uniform, and it
is what the model actually receives.

*Session titling's user prompt is not "already a module constant".* It is the one conditionally
assembled prompt in the set — `f"User: {user_message[:600]}"`, an `if`-guarded agent line, then
a fixed `"\n\nTitle:"` suffix — which makes it the most structurally awkward of the 17, not the
easiest. It splits into `session.title.user` + `session.title.user.agent_line` (18 entries for
17 literals) so no model-facing text is left inline, while the truncation slices and the
conditional stay at the call site (`session_titler.py:99`–`:104`) as logic.

**Why this, now.** The argument is entirely about timing. Today a prompt is a string literal
that moves freely because nothing depends on its exact bytes. Once prompt-injection hardening
starts depending on exact wording — delimiter conventions, untrusted-content fencing,
instruction-hierarchy markers — moving these stops being a refactor and becomes a security
review. This closes the gap while it is still cheap.

**The gate cannot see prompts, and that is the load-bearing fact.** `ScriptedLLMClient.generate`
(`core/eval/replay.py:97`) accepts `**kwargs` and discards them, so `system_prompt` and
`messages` are never read; `corpus.py` tests loop mechanics, not model decision quality. A grep
of `tests/` confirmed **no test asserted on any of these 17 literals** before this change. The
eval step is green whether these prompts are correct, reworded, or empty — so
`tests/test_prompt_registry.py` is not a supplementary check, it is the *only* one.

**Two gaps deliberately left open.** Adding `_is_substantive_fact` to the typed path, or any
validation to `_generate_structured_summary`, changes what gets written to memory and needs its
own regression test and review. Recorded, not fixed. `core/sub_agent.py`'s `PromptBuilder`
likewise remains a deliberate second composer: it reads parsed `OGFile` tags rather than
pre-rendered strings, and merging the two is its own design task.

**Locked by:** `tests/test_prompt_registry.py` (19 tests — 1 snapshot digest lock, 17
move-is-lossless byte pins, 1 completeness lock); `tests/test_prompt_composer.py` +
`tests/test_prompt_cache_ordering.py` (18 pre-existing tests, unchanged).

---

## T-66 — `Approval.id` is the gateway's `request_id`, and the guard outlives the map (2026-08-24) **[ADR]**

**Decision.** The frontend's `Approval.id` changes from a locally-minted `number` to the
gateway's own `request_id` string, and the module-level `Map<string, number>` bridging the two
id spaces is deleted. `HistoryItem.id` follows it to `string` — approval ids flow into history
rows via `resolveApproval`/`tickApprovals`, and it is a pure React list key (`key={h.id}`).
No `string | number` unions: a union propagates into every consumer and defeats the point.

**The spec was partly stale, again because of our own earlier work.** T-66 was anchored to
`main @ bd24afe`; Wave 1's `d82a48f` (T-17 + T-18) then landed in the same files. Three of the
spec's load-bearing claims were false by the time we ran it:

- §3.3's centrepiece — "the miss path is `if (requestId) { …POST… }` with **no `else`**" — was
  already fixed. T-18 added the `else`, set an `"unknown-id"` outcome, and surfaced a toast.
- §2's "Defect 2 — silent failure" was already closed, along with the optimistic-first store
  mutation.
- `approvals.test.ts`, listed as new, already existed with two T-18 tests in it.

Residual scope was Defect 1 only — the id model. This is the second consecutive task (see
T-77) whose file list had drifted under our own commits: re-derive anchors from the current
tree before dispatch and treat the pack's line numbers as hints, not facts.

**What the map actually was.** `approveByRequestId(requestId)` did a string → number → string
round-trip: `map.get(requestId)` → `uiId` → `approve(uiId)` → `requestIdForUiId(uiId)` (a linear
scan) → the same string it started with. The map existed solely to satisfy `id: number`.

**Why deleting it was safe.** The map's only population point was `adaptApproval`, called only
from `loadApprovals()` — the same population point as the store's `approvals` array. The map
was therefore a private mirror of the pending list. That is what let the `"unknown-id"` guard
survive: its oracle moved from `_idByRequestId.get(id)` to
`useStore.getState().approvals.some(a => a.id === id)`, which is behaviourally equivalent
rather than weaker, with no hydration-timing gap (the leash path is fed by the same
`loadApprovals()` call on the SSE event).

**Why the guard was kept at all.** T-66's framing — deleting the map removes the failure mode
rather than defending it — could be read as "the guard is now unnecessary". It is not.
Refusing to POST an id the gateway never showed us is load-bearing T-18/BB-106 behaviour,
independent of how many id spaces exist. `DecisionOutcome` keeps all three members, and T-18's
test kept its assertions byte-identical; only its id literal changed.

**Deliberately not done.** `approveByRequestId`/`declineByRequestId` stay separate exports even
though their signatures now coincide with `approve`/`decline`: the contracts differ
(`Promise<boolean>` for the chat leash vs `Promise<void>` + toast for the approvals screen).
Collapsing them is a second refactor. `chat.ts` needed no change.

**No bug-bank entry for the task itself** — it is a refactor; nothing returned a wrong answer
beforehand, and the spec explicitly warns against attributing the reported auto-decline symptom
to this code (CORRECTIONS.md traces that to BB-77). One entry was filed for a regression
introduced *during* the work and caught in review rather than by the suite: **BB-156**.

**Open, not fixed here (P1).** `routes/index.tsx` imports nothing from
`lib/data/domains/approvals` and wires the Home screen's "What needs you" Allow/Decline
directly to the raw store actions, so those clicks mutate local UI state and never POST to the
gateway — the card vanishes from every list while the agent stays blocked until the 180 s
timeout auto-denies. `approvals.tsx` (the Leash screen) does it correctly. This is the BB-106
class at a second call site T-18 never covered, latent only because `GET /approvals` returns
empty until T-01 lands. Not fixed inside T-66: the fix requires deciding error-surfacing
behaviour — T-18's design space — and the spec's §9 forbids a diff in which a type migration
and an error-handling redesign are indistinguishable. No bug-bank entry, because the bug is
still open.

**Locked by:** `frontend/src/lib/data/domains/approvals.test.ts` (10 tests — 6 pre-existing
T-17/T-18 tests unchanged, 1 characterization test pinning that the gateway's `request_id`
reaches the `POST /approvals/{id}` URL unchanged, 3 BB-156 regression tests).

---

## T-57 (half A of 2) — an OAuth refresh writes back to the key it read from (2026-08-24) **[ADR]**

**Decision.** `OAuthManager` resolves a provider's vault key once, through
`_resolve_key(provider)`, and both `get_token` and `save_token` use it. **Only half A of T-57
landed.** Half B — collapsing the two OAuth stacks onto one namespace and one set of primitives
— is deliberately deferred; the task spec itself recommends this split and says half A "can land
alone".

**Why split it.** The two halves have different risk profiles. Half A is ~50 lines in one file,
is a genuine bug fix under Rule 2, and stops credential state forking today. Half B is a week of
cross-module surgery on the agent's credential hot path. Shipping A first means the fork stops
even if B slips past a release.

**The spec was accurate this time — offset, not wrong.** T-57 was anchored to `main @ bd24afe`;
our own `e8b8de6` (T-67) later added 5 lines near the top of `core/oauth.py`. Every one of its
fifteen anchors was exactly **+5**, and every one still resolved to the right symbol. That makes
T-57 the first spec this wave whose claims all survived re-derivation — worth recording, because
the previous two (T-77, T-66) each had load-bearing claims that our own earlier commits had
falsified. Re-derive anyway: checking costs an hour, not checking costs a wrong diff.

**The defect (BB-157).** `get_token` read `oauth.<p>` and fell back to `oauth2.<p>` (BB-10).
`save_token` had exactly one `self._store.set` and always targeted `_key(provider)`. So the
refresh write-back landed on the *other* key from the one the token was read from — and since
`oauth_flow._refresh_token` has zero callers, nothing would ever refresh the abandoned copy.

**Consequences that were checked rather than assumed:**

- **`save_token` now reads before it writes.** Both production construction sites
  (`cli/main.py:1945`, `integrations/google/client.py:40`) use `create_default_secret_store()`,
  i.e. `CompositeSecretStore`, whose `get` swallows every exception and returns `None` — so the
  new read introduces no new failure path on the credential-save path. It costs 1–2 extra vault
  reads per save; on the degraded encrypted-file backend (keyring unavailable *and*
  `HACHI_SECRET_PASSPHRASE` set) each miss is a PBKDF2 derivation. Bounded, off the hot path, and
  accepted in exchange for the spec's simpler `-> str` resolver — deliberately not optimised into
  a value-returning resolver.
- **`metadata["name"]` follows the resolved key** — required, or vault listings name a key the
  value does not live under. Side effect: when the agent refreshes a web-UI-owned grant, that
  key's metadata labels change from `oauth_flow`'s `{provider, type: "oauth2"}` to core's
  `{provider, scope}`. No consumer reads `labels.type` (`credential_vault.list_token_metadata`
  reads name+backend; `in_process_runtime.list_secret_names` filters the `cred:` prefix; the CLI
  dumps raw JSON), so this is cosmetic — but it is a real difference, recorded here rather than
  discovered later.
- **A fresh `hachi google-auth` now overwrites the web-UI key** when that is where the grant
  lives, instead of minting a second copy. Checked for refresh-token loss: the authorize URL
  sends `access_type=offline&prompt=consent`, so Google always returns a refresh token and the
  replace loses nothing.
- **`--revoke` becomes correct** (see BB-157). Not special-cased, and `cli/main.py` was not
  touched — it falls out of the one-key invariant.

**Deliberately not done** — spec §9 forbids each of these, and each is the obvious adjacent move:
no unifying the two prefixes; no touching `oauth_flow.py`, including the dead `_refresh_token`
and `oauth_disconnect`'s single-key delete; no extending the BB-11 capability bridge in
`gateway/orchestrator.py`. Fixing `oauth_disconnect` to delete both keys would be dead defensive
code the moment half B lands.

**Open, not fixed here.** `OAuthTokenStore.is_connected` tests *presence*, not validity, so the
web UI's connector pill reads "connected" whenever the key exists. After a CLI `--revoke` the key
now holds `{}` — so the pill is still wrong, in a new way. This is a pre-existing wart the spec
documents at §3.6; the fix makes it reachable by one more path without creating it, and it lives
in `oauth_flow.py`, which half A may not touch. No bug-bank entry: the bug is open, and bug-bank
entries lock fixed bugs.

**Locked by:** `tests/test_oauth.py` — 14 tests: 12 pre-existing and unchanged, 1 regression test
for the write-back key, 1 characterization test for the clear path.

---

## Wave 1 of the parallel push — five tasks in one tree (2026-08-24) **[ADR]**

**Decision.** T-46, T-56, T-57 half B, T-65 and T-73 were implemented concurrently by five agents in a
single working tree, each holding an exclusive allow-list of files. This is the first multi-agent batch
since Batch A, and the constraint that shaped it is physical rather than procedural: **four of the
eleven remaining tasks (T-44, T-46, T-58, T-75) all rewrite `gateway/orchestrator.py`**, and T-70
relocates ~300 test files. Those cannot be parallelised at all, which is why finishing the remaining
pack takes waves rather than one fan-out. T-46 held the sole `orchestrator.py` lock for this wave.

**What the wave cost, and what it bought.** Running five at once removed the orchestrator's own
pre-dispatch spec derivation — the practice that caught the stale T-77 and T-66 specs and the T-66
regression. It was replaced with a weaker substitute: each agent was told to re-derive its own anchors
and stop if a spec claim proved false. That worked better than expected — **every one of the five specs
had at least one false claim**, and two contained test sketches that cannot pass by construction — but
it is a downgrade, and the compensating control moved entirely to post-hoc verification. Every
finding below was confirmed against the source before acceptance; three of the five needed a
round-trip. Two central instructions were changed for the batch: no agent could assign a bug-bank
number (five agents would all have taken BB-158), and each was told other agents were live in the tree
and to never act on `git status` output for files it did not create.

**Three bugs were found by agents verifying acceptance criteria, none predicted by any spec** — see
BB-160 (a Windows path corrupts `.installed.toml`, so an installed pack silently vanishes from
`pack list`), BB-162 (a migration tiebreak that destroys a freshly-consented credential) and BB-158
(a monkeypatch that stops working when the code it patches moves). That ratio is the argument for
acceptance criteria that name an observable outcome rather than a code shape.

---

## T-46 — `AgentSession`'s 20 read models move behind a facade (2026-08-24) **[ADR]**

**Decision.** The 20 `snapshot_*`/`query_*` bodies move verbatim to a new `SessionReadModels`
(`gateway/session_read_models.py`); `AgentSession` keeps 20 one-line forwarders. `orchestrator.py` is
+42/−196. `core/runtime_read_models.py` has zero diff, as the spec required.

**The judgement call that mattered.** The spec's illustrative facade captures `skill_engine`,
`memory`, `vault`, `session_store` and `loop` as flat constructor values. Three of those do not exist
at construction time: `self.vault = None` at `orchestrator.py:539`, with `set_vault()` and
`set_session_store()` assigning later — real sessions always call them *after* `__init__`. A literal
constructor capture would have frozen `vault`, `_session_store` and `_session_id` at `None` for the
life of the session, silently emptying `memory_facts`, `memory_recall`, `recent_sessions`,
`session_messages` and half of `subagent_reports` in every real boot — a dead feature reporting
success. Those three are passed as getter callables resolved per call, documented at length so nobody
"simplifies" them back.

**Spec claims that were false.** §3.5 calls `snapshot_gardener_diffs` dead with zero callers; T-08
(`f618d25`) had already wired it end-to-end through `GET /memory/diffs`. §7's sketch asserts
`isinstance(result, (tuple, list, dict))` for all 20, but `snapshot_session_state` and
`snapshot_mcp_inventory` return frozen dataclasses — copied literally it would report a false "type
changed" regression against unchanged code. A 21st method, `snapshot_events_base`, landed after the
spec's anchor; it was deliberately left on `AgentSession` to match the enumerated list rather than
widen scope unilaterally. **Open fast-follow:** move it, and retire the 20 forwarders.

**What went back.** Two location-keyed breakages, both now BB-158 — including a first attempt that had
production code import `get_hachi_paths` through `orchestrator.py` so a stale patch target would keep
resolving. The repo's own T-41 precedent, three functions below in the same test file, settles it: fix
the reference, not the code.

---

## T-56 — every pack install path goes through one consent gate (2026-08-24) **[ADR]**

**Decision (P1).** `hachi skill pack install user/repo` was fetching an archive from the internet and
writing `.og` files into the same `skills_dir/packs/` that `hachi ogpack install` guards with scopes, a
consent sheet, a default-No prompt and checksums. `community_packs` is now a fetcher plus a manifest
synthesiser that declares zero scopes and computes real hashes; the install path renders the sheet and
requires confirmation. One `confirm_pack_consent_or_exit` in `cli/commands/pack.py` now serves
`ogpack_install`, `ogpack_update` and the community path — previously two inline copies. See BB-159.

**Two adjacent bugs were fixed rather than filed.** BB-160 (`.installed.toml` hand-built with
f-strings; a Windows path corrupts it and the pack disappears from `pack list`) was found by verifying
an acceptance criterion, not by looking for it. BB-161 (Rich swallowing `[gmail.read]` from the consent
sheet) was pre-existing and initially classified as cosmetic; it was escalated because this task made
that same print the gate for community packs, which previously had no sheet at all. The bug did not
change — its blast radius did, and a consent screen that drops the identifier while keeping only the
prose is a consent-integrity defect.

**Deliberate:** the sheet still prints under `--json --yes`, routed to stderr, so the consent record is
always emitted on a channel that cannot corrupt machine-readable stdout. Two pre-existing tests in
`test_skill_packs.py` gained `--yes`, and one moved from `result.output` to `result.stdout` — neither
weakens anything: their subject was always "install succeeds and reports the pack name", never
"consent is not required", and the new behaviour is locked positively by
`test_community_pack_install_refuses_without_consent`. click 8.4.1 has no `mix_stderr` parameter, so
the two streams are genuinely separate.

**Honest residue:** `grep "typer.confirm" src/hachi/cli/commands/` shows one install-consent call site
plus three unrelated confirmations (`ogpack_remove`, `mcp.py`, `session.py`). The install gate is
unified; the grep is not literally singular.

---

## T-57 half B — one OAuth namespace, and one primitive that could not move (2026-08-24) **[ADR]**

**Decision.** `core/oauth.py` delegates storage to `integrations/oauth_flow.py`'s `OAuthTokenStore`;
`_key`/`_resolve_key` are deleted and every token lives under `oauth2.<p>`. A one-time
`_migrate_legacy_oauth_key` sweeps any surviving `oauth.<p>`. The dead `_refresh_token` (zero callers)
is deleted. `hachi.core → hachi.integrations` is permitted by all three import-linter contracts, and
`oauth_flow` does not import `core.oauth`, so there is no cycle.

**Step 9 was not implementable and the spec is wrong about it.** The spec calls for `_exchange_token`
to be deleted in favour of `oauth_flow._exchange_code`. `_exchange_code` takes no `code_verifier` — no
PKCE — and hardcodes `redirect_uri()`, the gateway's fixed callback. The loopback flow uses PKCE and an
ephemeral `http://127.0.0.1:<port>/callback`, so routing it through `_exchange_code` would send a
`redirect_uri` in the token exchange that does not match the authorize step and break every
`hachi google-auth` with `redirect_uri_mismatch`. `_exchange_token` keeps its `invalid_client` /
`redirect_uri_mismatch` / `invalid_grant` hints, and the loopback flow, `OAuthCallbackHandler` and
`_build_pkce` are untouched. **Open:** the exchange/refresh consolidation, which needs PKCE and a
caller-supplied redirect URI in the connector stack first.

**Two corrections made in review** — the migration tiebreak (BB-162) and a module-level
`from hachi.integrations.oauth_flow import …` that put five `mkdir` calls on `core.oauth`'s import
path, via that module's default-constructed singleton. Not a credential prompt and not a `.env` read,
so BB-2 was not literally broken, but `core.oauth` is imported far more widely than `oauth_flow`, and
"core resolves lazily at call time" is the rule that exists to prevent exactly this. Now a
function-level import at three sites; `OAuthTokenStore(secret_store=…)` with an explicit store never
calls the default factory, so per-call construction is free.

**Also open, found while reading:** `core/oauth.py` resolves only the legacy bare
`GOOGLE_CLIENT_ID`/`_SECRET`, while `ConnectorDef.client_id()` prefers the canonical
`HACHI_OAUTH_GOOGLE_CLIENT_ID`. A user who configures the documented canonical names sees the web UI
report Google as **configured** while `hachi google-auth` and every Google tool call fail with
"Missing GOOGLE_CLIENT_ID". Reproduced, not fixed — outside T-57's scope. No bug-bank entry: the bug is
open, and those entries lock fixed bugs.

---

## T-65 — `chat.ts`'s runtime kernel becomes three objects (2026-08-24) **[ADR]**

**Decision.** 17 module-level mutables become `sessionRef`, `streamBuffers` and `costMeter` under
`frontend/src/lib/data/domains/chat/`, and the reset sequence copied into four functions becomes one
`resetLiveSession(nextSessionId)`. Each call site keeps its own `hydrate(...)`: the kernel never
touches store state. `store.ts` and the auto-generated `schema.d.ts` have zero diff. All 16
pre-existing exports keep their names and signatures. See BB-163.

**Verified rather than assumed**, because this refactor is exactly the shape BB-156 warns about: all
ten statements the four reset blocks performed were traced into the new kernel, and the inlining of
`resetRunLineBuffer` into `resetBuffers` is behaviour-preserving because at HEAD `resetDeltaBuffer`
already called it as its sole caller. The diff contains zero lines mentioning `allowLeashLive` or
`denyLeashLive`, so the leash failure toast T-66 depends on is untouched.

**Spec claims that were false.** §5 and §12 both assert `switchSessionLive` "deliberately does not call
`hydrate({ messages: [] })`" — checked against the spec's own anchor commit, it always did; the real
difference between the four sites is how they patch the `sessions` array. §3.5 claims `chat.test.ts`
already has established mocking to reuse; it had none.

**Deliberate:** `sessionRef.eventsUnavailable` is not cleared on reset. The code's own comment says the
Set is intentionally page-lifetime-scoped, session ids are server-minted UUIDs, and none of the four
original reset blocks ever touched it — clearing it would be a behaviour change smuggled into a move.

---

## T-73 — the `.og` engine becomes constructible without a Hachi home (2026-08-24) **[ADR]**

**Decision.** `SkillEngine.__init__` gains keyword-only `usage_path`/`db_path` seams and calls
`get_hachi_paths()` only when they are `None`, so all 20 `src/` and 62 `tests/` construction sites are
untouched. Usage tracking and embedding rank move to `og_engine/usage.py` and `og_engine/embedding.py`
as collaborators, with `SkillEngine` keeping thin delegations — including for the private names
(`_load_usage`, `_save_usage`, `_embedding_search`) that existing tests poke directly. See BB-164.

**Why the rail could not see this.** `og_engine → core` is the one permitted direction, so
import-linter passed throughout. The coupling was a constructor dependency on *environment state*, not
an import-layer violation — a class of defect no layering contract detects.

**The `with_role()` trap.** Views are built with `__new__` + `__dict__.update`, so collaborators are
shared by reference. Every back-compat attribute is therefore a property reading through the shared
collaborator rather than a snapshotted value; otherwise a view stops seeing state recorded on the
engine after the view was created.

**Stopped deliberately before `SkillAuthor`**, which the spec sanctions verbatim at line 281:
"Stopping after (3) resolves the finding entirely." The reasons are real — `upsert_skill` must call
back into `SkillEngine.load_file()` to hot-load, and moving `validate_dense` creates a circular import
through `estimate_tokens`/`DENSE_TOKEN_BUDGET`, which `peek()` also uses. **Open fast-follow.**

**Spec defects found:** §3.3 lists `get_usage_counters`/`reset_usage_counters` as `UsageTracker`
methods, but their bodies operate on the unrelated M7 pass-depth counters — moving them would have
coupled the tracker to `search`/`peek`/`resolve_node` internals. §7's sketch inherits that conflation
and **cannot pass by construction**: `get_usage_counters()` returns a fixed
`{pass1_search, pass2_peek, pass3_resolve}` dict that can never contain a `"shared-skill"` key. It is
also non-hermetic — it reads the developer machine's real usage JSON, and failed `assert 2 == 1` from a
previous run's residue.

---

## Wave 2 of the parallel push — three tasks, and a limit that killed two of them (2026-08-25) **[ADR]**

**Decision.** T-44, T-63 and T-71 ran concurrently in one tree on exclusive file allow-lists.
The wave was shaped by the same physical constraint as wave 1: four of the remaining tasks all
rewrite `gateway/orchestrator.py`, so T-44 took the sole lock and T-58/T-75 wait. T-70 remains
exclusive and last.

**What actually happened.** A usage limit terminated T-63 and T-71 mid-flight. Neither left
damage, which is worth recording because it was not luck: the standing rule that **subagents own
no git** meant the worst case was an unfinished working tree, never a bad commit or a lost branch.
T-71 died inside a break-and-revert cycle — the single most dangerous moment for a killed agent,
because a deliberately-broken production file could have been left behind and committed. Its
revert had landed; I verified `mcp_client.py` at zero diff before doing anything else. **Check
that first when an agent dies, not last.**

T-63 turned out to be ~95% complete despite its last transcript line suggesting otherwise: an
agent's final message is not a progress indicator. The tree is the progress indicator.

**Three orchestrator errors worth recording, since the point of these entries is that the next
person makes different mistakes.** (1) My file-collision analysis was incomplete — two
AST-structural tests anchored to `boot_agent`'s body were not in T-44's allow-list and would have
broken silently; the implementer found them only by distrusting my list. (2) I sent T-71 a
break-check value (`timeout=5.0`) that could not go red, because a *lower* bound catches
"fires too early", not "too late" — the agent ran it, caught that it stayed green, and used `0.1`.
(3) My first round-trip on that test asked for a lower bound, which was the wrong fix on its own:
the real hole was a 1.0 s floor that the test's own configured value coincided with. Both were
caught by verification, which is the argument for verifying rather than for being right first.

---

## T-44 — `boot_agent` becomes 15 staged builders (2026-08-25) **[ADR]**

**Decision.** `boot_agent`'s 1,245-line body splits into 15 named stages under a new
`src/hachi/gateway/boot/` package, each taking explicit typed inputs and returning a frozen
`@dataclass(frozen=True, slots=True)`. `orchestrator.py` is +256/−802 (net −566); the function's
own body is 705 lines. Import direction is one-way and enforced by construction: `boot/*` never
imports `orchestrator` at runtime (all three `CapabilityRegistry` imports sit under
`if TYPE_CHECKING:`), and the orchestrator-local helpers a stage needs are passed as callables
rather than imported — which sidesteps circular-import risk entirely.

**The decomposition had to be derived, not read.** The step numbering lies (BB-165): step "5.5"
executes eleventh. The real edge is that prompt assembly is *downstream* of tool registration,
which the new signature now encodes. A second edge the numbering has no slot for at all:
`reconcile_permissions_with_config` reads both step 0's gate and step 1's config and rebuilds
step 0's output.

**Why the body is 705 lines and not the spec's ~80.** Two regions could not become stages, and
both were found by grepping the whole suite for `ast.parse`/`inspect.getsource` rather than
trusting the file list I supplied. The event-plumbing closures must stay lexically inside
`boot_agent` because they read a mutable `_event_bus` cell at call time — a naive extraction
passes every other test while silently breaking event routing the next time the bus rebinds. The
post-session tail must stay because a schedule-fire closure resolves `session` by late binding on
a name assigned later in the same function, and two AST-structural tests require
`_start_eval_report_scheduler` / `_start_memory_gardener` to be called directly from
`boot_agent`'s body. **A partial decomposition that is provably behaviour-identical beats a
complete one that is not.**

**Two deliberate reorderings, and how the riskier one was cleared.** `build_memory` now runs
before `build_tool_infrastructure` (originally the infra block sat physically between memory init
and the vault binding). The only mechanism that could make that observable is mutation of the
shared `ProcessSharedServices`: memory only *reads* `vault`/`graph_index`/`memory_writer`, infra
only *writes* `session_store`. Disjoint sets, and the writer is not the reader — so the swap is
unobservable. Verified independently rather than accepted.

**Spec claims that were false.** §3.3's "the tool count and group names go into the prompt"
implies both are tool-registration-downstream; only the group names are, since the numeric count
is fixed two stages earlier. Its §7 sketch calls a nonexistent
`session._tool_registry.list_tools()`. And T-26 §9 forbade extracting a `_build_routing()` helper
because "it will collide with CF-6 work" — CF-6 *is* T-44, so the deferral named its own
successor; that extraction landed here, with `test_routing_boot_failure.py`'s AST anchor moved and
**zero assertions changed**.

**Open, not fixed:** `BOOT_PROGRESS` reports `"Building system prompt"` at step 7 of 11, four
steps before the prompt is built — the same defect as the comments, in user-facing text. Out of
scope for a behaviour-preserving refactor, where "same events in the same sequence" was the
constraint. No bug-bank entry: those lock fixed bugs.

---

## T-63 — the 1,847-line store becomes 22 typed slices (2026-08-25) **[ADR]**

**Decision.** `frontend/src/lib/store/store.ts` drops to **271 lines** as a composition root over
22 slice files, one per concern, plus a shared type module. Both `as Partial<State & Actions>`
casts are gone (BB-167). `useStore`'s public shape is unchanged and **zero component files were
edited**. Four render smoke tests for the largest untested screens — `integrations` (2,187 lines),
`work` (1,472), `memory` (1,392), `og` (1,283) — landed *before* the first field moved, which was
the ordering the task insisted on and the reason a mechanical split did not become a week of
bisecting blank pages.

**The checksum that actually proves it.** The task asks that the slices' fields sum to 75. A count
is a weak check — it passes if one field is dropped and another duplicated. The set of field names
in `interface State` at HEAD was diffed against the union of the slice interfaces: **86 names on
each side, zero dropped, zero added.** A silently dropped field becomes `undefined` at runtime and
typechecks fine wherever it is read as optional, so set equality is the assertion worth making.

**A spec claim that was already fixed.** §3.3 and §9 both say the store statically imports 33
fixture symbols, "how the demo data reaches the production bundle". T-22/BB-123 fixed that before
this task existed — fixtures are dynamic-import-only, gate-enforced. It mattered: a slice that
re-introduced a static fixture import would pass typecheck and vitest and fail the bundle-purity
step. It did not.

**The cycle moved rather than closing.** `store.ts` no longer imports the chat domain directly;
three slices do, so the static cycle is now `store.ts → slices/chat.ts → data/domains/chat.ts →
store.ts` — one hop longer, still real. `module-graph.test.ts` was re-anchored to the full chain
and the chat.ts header comment corrected in the same change (BB-168). The production build was the
gate that mattered here — `tsc --noEmit` cannot see an initialisation-order cycle and Rollup can —
and it passes.

**One correction carried in.** A comment claiming live mode delegates "via dynamic import to avoid
a static circular dependency" was moved verbatim from `store.ts:1177`, where it had *already* been
false. Moving it verbatim was correct (Rule 3); leaving it false in a brand-new file was not, since
a reader of new code has no reason to distrust it.

**Deviation from the spec, recorded rather than hidden:** the task asks for one commit per slice.
Subagents own no git in this pack, so that was not achievable literally. The discipline it exists
to protect — split one slice at a time, typecheck and test after each — was followed; the
bisectability was not preserved.

---

## T-71 — the suite runs `-n auto`, and no production clock seam was needed (2026-08-25) **[ADR]**

**Decision.** Ten wall-clock/cross-thread-timestamp tests convert to `threading.Event` handoffs,
`threading.Barrier` coordination and deadman-switch joins; preflight, CI and `pyproject.toml`'s
comment all move to `-n auto` (~520 s → ~120 s, ≈4×). `tests/test_test_suite_hygiene.py` fails the
build if a new `elapsed <` / `wall_time <` assertion appears outside a documented allowlist. See
BB-166.

**The finding that generalises.** The task's premise was that three of these tests need an
injected clock in `src/`. **None did, and the production footprint is zero lines.**
`Thread.join(timeout=…)` and `Condition.wait_for(timeout=…)` are OS-level blocking waits with no
swappable clock, so they convert by proving the *outcome* — "returned without the hang ever being
released" — which is both clock-free and strictly stronger than the bound it replaced. The rule:
inject a clock only when the code under test polls `time.monotonic()` itself.

**Two bounds were loosened, and they are not the same case.** `test_obs_sink.py` went
`elapsed < 1.0` → `join(5.0)`: accepted, because `handle()` is pure attribute stamping with no
configured deadline, so both numbers are equally arbitrary hang-detectors and the tighter one
protected nothing. `test_mcp_client_timeout.py` went `elapsed < 2.0` → `join(10.0)`: sent back,
because that bound was pinning *the deadline fires at its configured value*, and a hardcoded 5 s
would have passed. It now carries a **lower** bound — parallel-safe by construction, since
starvation only makes things slower — plus a structural spy on
`Transport.receive(request_id, timeout_seconds)`.

**The spy exists because of a 1.0 s floor.** `MCPServerConnection` clamps with
`max(1.0, float(request_timeout_seconds))`, and the timing test configures exactly `1.0` — so on
its own it cannot distinguish "the configured value reached the transport" from "config is ignored
and the floor is always used". The spy configures `2.5`, off the floor. Its break-check proved
both halves: the spy went red (`recorded timeout_seconds=[1.0]`), *and* the sibling test stayed
green under the same break — the blind spot, demonstrated.

**One test is a deliberate, permanent holdout.** `test_loop_backstop_cancel.py`'s two assertions
exercise `ExecutionLoop`'s real deadline and cancel-during-stall behaviour with a 5× margin;
converting needs a clock seam in `core/loop.py`. It sits on `KNOWN_WALL_CLOCK` with a written
reason. **A documented allowlist entry, not an empty set, is the acceptance bar** — the spec's own
enumeration of "eight" sites was itself incomplete by two, which is how this entry was found.

**Open, not fixed:** that `max(1.0, …)` floor is applied identically at four constructors but
carries **no** comment, docstring, linked bug or spec reference anywhere — out of step with this
file's own convention of tagging constants (`UC-DEV-001`, `MED-004`, `LOW-014`, `BB-9`). It was
introduced unremarked in the commit that created the file and copy-pasted into three transports by
an unrelated split. A user configuring `request_timeout_seconds=0.5` silently gets `1.0`. Whether
that is protection or a bug needs a decision, not a guess.

**Resolved (2026-08-30).** It is protection; the silence was the bug. See *The MCP
request-timeout floor is protection, and it is now audible* at the end of this file, and
[BB-176](bug-bank.md).

---

## Wave 3 — one task, solo, because the remaining lane admits no parallelism (2026-08-26) **[ADR]**

**Decision.** T-58 ran alone. The three tasks left in the pack (T-58, T-75, T-70) have **no safe
concurrency between them**: T-75's anchors sit inside `boot_agent`, the same function T-58
restructures, and since subagents own no git two agents in one function overwrite each other rather
than conflict. T-70 relocates ~300 test files and collides with everything, so it stays last.

**The pre-dispatch verification pass paid for itself twice this wave, in opposite directions.**
T-58's spec was anchored to `main @ bd24afe`, 72 commits back, and **every one of its
`gateway/orchestrator.py` anchors was stale** — T-44 had moved the exact block it targets into
`boot/environment.py`. Checking that before dispatch is the difference between a brief that
describes the tree and one that sends an agent hunting line numbers that no longer exist. In the
same pass, T-75's spec turned out to have **barely decayed at all** — its `runtime_read_models.py`
anchors are still exact, because nothing in waves 1–2 touched that file. Decay is per-file, not
per-spec; assuming either uniformly is wrong.

**A prohibition in a spec can expire.** T-58 §9 says "do not update `docs/reference/objectgraph-spec.md`
— that is T-72's job." T-72 has since landed, and it wrote *into* the spec that `role=env` reads
plaintext credentials, citing "See T-58, which owns fixing this." So the prohibition, followed
literally, would have shipped documentation that is confidently and specifically wrong. Worse, the
conformance test guarding that section (`test_spec_documents_every_executable_node_role`) only
asserts the string `role=env` is *present* — it cannot detect that the sentence around it became
false. **When a spec forbids touching something, check whether the reason still holds**; a
dependency landing can invert the instruction without anyone editing it.

---

## T-58 — `role=env` stops being a credential source (2026-08-26) **[ADR]**

**Decision.** Option (a) of the task's own decision block: the plaintext path is deleted, not
fenced and not laundered. `load_env_node` stores nothing and emits one migration warning naming the
file and the credential **key names** — never the values — plus the exact `hachi secrets --set <key>`
command. `_setup_smtp` resolves through `CredentialVault.get_token(..., prompt_if_missing=False)`;
that keyword matters, because `setup_integration` runs during boot and this project forbids
interactive credential prompts there. `_setup_http` was deleted rather than ported, since
`UniversalRestAdapter` already covers declarative HTTP with a vault, an SSRF allowlist and outbound
redaction. All five `print()` calls became logger calls. See BB-169.

**The decision block resolved on evidence rather than judgement, because T-72 moved the ground.**
Option (c) — fence `role=env` against installed packs — existed to close a pack-supplied-credential
attack surface that `scripts/lint_skills.py` **already closes**: `env`/`integration`/`mcp` are now
reserved roles, rejected outside first-party skills. And no shipped `.og` file uses `role=env` at
all, so (a)'s breaking change cannot break a first-party skill. What the spec framed as a judgement
call had become a lookup.

**The ordering problem was real, and not the one the spec described.** The spec says move the vault
construction "from `:3108` to before `:2819`." Post-T-44 those lines do not exist, and the actual
constraint runs the other way: the vault needs a `secret_store`, built by `build_llm_provider` at
**stage 9**, while `build_integrations` is **stage 6**. Three shapes were available; two were
rejected for stated reasons. Moving `build_integrations` later would move a `boot.progress` event
that `test_boot_agent_stages.py` pins, requiring a baseline edit with no user-visible justification.
Threading the vault in after construction does not work at all, because `setup_integration` — SMTP
connection included — runs *synchronously inside* `build_integrations`. So `create_default_secret_store()`
and `CredentialVault(...)` were hoisted into the composition root, and the two downstream stages now
take them as parameters. Exactly one `CredentialVault` in the boot path.

**The hoist is safe, but not for the reason first given, and the distinction is the point.** The
implementation report justified it as moving "cheap, side-effect-free constructions."
`create_default_secret_store()` is **not** side-effect-free — it calls `ensure_directories()`, which
writes to disk. The move is safe for two narrower reasons that had to be checked rather than
assumed: `ensure_directories` is `mkdir(parents=True, exist_ok=True)`, and `load_config()` already
called it at stage 2, four stages earlier. A correct conclusion resting on a false premise is still
a defect in the reasoning, and it is the kind that survives review by sounding right.

**The strongest evidence is a test that did not change.** `test_boot_agent_stages.py` has **zero
diff**: T-44 built it specifically to catch stage reordering, and it stayed green with no edit to
`EXPECTED_BOOT_EVENTS` or `EXPECTED_BOOT_PROGRESS_STEPS`. That is mechanical proof the reorder is
invisible to the boot sequence, rather than an assertion anyone has to take on trust — which is
what a guard test is for.

**Open, not fixed:** `cli/main.py:760` and `:766` tell users to run
`hachi secrets --set <KEY>=<value>`, but `--set` takes a bare key and prompts for the value, so that
syntax creates a secret literally *named* `KEY=<value>`. T-58's new migration message uses the
correct form, which is how the discrepancy surfaced. Two user-facing strings teaching a syntax the
CLI mishandles. No bug-bank entry — those lock fixed bugs.

---

## The frontend gate was flaky, and the gate is where flakiness is least affordable (2026-08-26) **[ADR]**

**Decision.** The four route smoke tests T-63 added get explicit per-test budgets
(`it(…, 15000)`) rather than a suite-wide `test.testTimeout`. A global bump would relax the
hang-detector for ~258 tests that never do a cold React + Router + loader mount; the defect is
scoped to four call sites, so the fix is too. This mirrors T-71's `test_obs_sink.py` precedent —
a generous budget scoped and disclosed at the one site that needs it. See BB-170.

**This was our own defect, shipped one wave earlier.** T-63's tests were accepted on a single green
gate run. T-71, in the same wave, was held to three consecutive full-suite runs on a quiet tree
before its parallelism flip was accepted — and that asymmetry is exactly where this got through.
The standard was available; it was applied to the Python suite and not to the frontend one.

**Two findings worth more than the fix.** First, a load-dependent flake **will not reproduce in the
isolated run you use to verify the fix** — this one passed 262/262 alone, twice, both before and
after the change. Reproducing it required deliberately generating load (running the vitest step
concurrently with `pytest -n auto`), and any "fix" verified only in isolation here would have been
worthless. Second, **the observed failing subset does not delimit the bug**: three of four tests
failed in the gate, which read as three flaky tests. Under heavier load all four failed. The
fourth's survival was timing luck, and treating the observed set as the scope would have left a
known-defective test in place to resurface later.

---

## Open: the Python suite itself flakes under `-n auto`, and nothing is enforcing otherwise (2026-08-26)

**Not an ADR — an unresolved finding recorded so it is not rediscovered.** While generating load to
reproduce the frontend flake, the background `pytest -n auto` runs failed intermittently too, with
**a different test each time**: `test_add_core_memory_fact`, `test_capability_toggle_round_trip`,
`test_live_read_models_reflect_subsystem_state`, and on a later run
`test_cockpit_gaps.py::TestConfigActions::test_change_tier_cycle`.

Measured on the current tree: **four green full-suite runs (4643 passed / 21 skipped) to one red**
under normal conditions, and **two of two red** under deliberately heavy concurrent load. The
failures are not reproducible in isolation — `test_change_tier_cycle` passes alone (6.7 s) and its
entire file passes alone under `-n auto` (13/13). So this is **cross-file** interference:
`--dist=loadfile` keeps same-file tests on one worker, but different files racing on shared
filesystem/global state get no such protection, and the flaky names all touch config, capabilities,
read models or memory — process- or disk-level shared state.

This is the same *class* of problem T-71 solved for wall-clock assertions, by a different
mechanism, and T-71's flip to `-n auto` is what exposed it. `tests/test_test_suite_hygiene.py`
cannot catch it: it greps for duration assertions, and there are none here. **Roughly a one-in-five
red on an unrelated flake is corrosive to a gate specifically because it trains people to re-run
until green**, which is how a real failure gets waved through. The options are to find and fix the
shared-state races, or to revert the gate to `-n 0` and accept ~4× slower runs as the honest price.
That is a decision, not a guess, and it is not T-58's to make.

---

## The gate goes back to `-n 0`: three proven races, and why a partial fix would be worse (2026-08-26) **[ADR]**

**Decision.** The gate runs `pytest -q -n 0` again, in `scripts/preflight.py` and `.github/workflows/ci.yml`.
This resolves the open finding recorded directly above, which ended by saying the choice was a
decision and not T-58's to make. It has now been made, on evidence: an investigation reproduced the
flake on demand and proved **three independent race mechanisms** in source. All three are real
defects, none is a test-only artifact, and none is small. `addopts = "--dist=loadfile"` stays — it is
inert under `-n 0` and keeps same-file grouping for anyone who passes `-n 4` locally; removing it
would plant a fresh regression for the first developer who does.

**T-71's conversions stay — eight of them.** The count is worth stating precisely, because the
promise of this revert is that the hygiene work survives it. There were **ten wall-clock assertion
sites across eight files**; T-71 converted **eight sites in seven files** to injected clocks,
`threading.Event` and `threading.Barrier`, and deliberately left **two** in
`tests/test_loop_backstop_cancel.py`, which asserts a real production deadline at a 5× margin and is
the documented `KNOWN_WALL_CLOCK` holdout. Saying "ten converted" — as T-71's own text did, and as
this revert nearly repeated in six places — contradicts the holdout sentence printed beside it, and
an auditor who counts ten and finds eight stops trusting everything else on the page. The
conversions are correct whether or not the suite runs in parallel; the old versions were flaky under
load *serially* too, just less often. The parallelism flip is reverted; the hygiene work it was
bundled with is not, and `tests/test_test_suite_hygiene.py` still enforces the no-new-assertions rule.

**The three mechanisms.**

1. **Config writes race across processes** — `src/hachi/core/config_loader.py`. This reads as one
   defect and is actually **three**, which matters because the obvious fix cures only the least
   important of them:
   - **(a) `_CONFIG_LOCK` is a `threading.Lock`** (`:33`), so it serialises threads within one
     interpreter and **nothing at all between processes**; xdist workers are processes. Note that
     `src/hachi/gateway/provider_admin.py:12-16` asserts the opposite in production prose — "the
     load->mutate->write critical section for each writer is serialised by `_CONFIG_LOCK`" — which
     is true within a process, false across them, and a shipped module is relying on it.
   - **(b) The save path reapplies the caller's already-stale full document** (`:371-372`) over the
     one it just read fresh at `:365-366`. This is the **silent lost update**: it raises nothing, on
     every platform, regardless of how the temp file is named.
   - **(c) The write goes through a fixed-name `.tmp` sibling** (`:375`) shared by every writer,
     before `os.replace` (`:377`). This is the collision, and the only source of `WinError 32`.
   `src/hachi/core/atomic_io.py:56` (`tempfile.mkstemp()`) is the correct pattern **for (c) alone**.
   Adopting it and stopping there would leave the lost update and the cross-process gap fully
   intact while looking and feeling like a fix — which is exactly the partial repair this ADR argues
   against, blessed by our own citation. Pointing at a nearby correct pattern is only useful if you
   also say which fault it addresses.
2. **A five-second wall-clock startup deadline** — `src/hachi/gateway/control_plane.py:1474`:
   `deadline = time.time() + 5.0`, spun on while waiting for the `gateway-http` thread to come up.
   Under N-way CPU contention that thread does not get scheduled inside the budget.
3. **A chromadb write-then-read window** — `src/hachi/core/memory/vault.py:1025-1049`.
   `_query_collection` can query immediately after an upsert and hit
   `InternalError: ... Nothing found on disk` — that exact string is an observation from the
   investigation, not a message this repo emits, since the handler catches generically. It is caught
   by a broad `except Exception` (`:1039`) and **fails open, returning `[]`** (`:1049`), so this one
   never surfaces as an error. It silently returns the wrong answer, and a recall test downstream
   fails somewhere else entirely.

**Why not fix them instead.** The config repair alone is a multi-day change to a module with prior
incident history, and it is the only one of the three that a serial gate does not fully mask. More
to the point: **a partial fix would be worse than the revert.** Lowering a 1-in-5 flake to 1-in-30
does not restore trust in the gate; it makes the remaining failures rarer, harder to attribute, and
easier to wave through — while removing the pressure to finish the job. A visibly slow honest gate
beats a fast one that is wrong 3% of the time.

**The most important finding is not about the tests.** Mechanism 1 is a **production** defect that
the test suite merely exposed. A `threading.Lock` cannot span processes, so any two Hachi processes
writing config concurrently — a CLI invocation while the TUI is open, two terminals, a gateway plus
a `hachi` command — hit exactly the same hazard, with the silent-lost-update outcome being the
likely one on POSIX. The parallel test run did not create this bug; it was the first thing to
exercise it often enough to be noticed. Relatedly, and worth fixing on its own: **`tests/conftest.py`
has no filesystem isolation and `src/hachi/core/paths.py` has no path override**, so running the
suite writes to the developer's real Hachi install.

**A guard test's scope is part of its contract, and we accepted one that was too narrow.**
`tests/test_test_suite_hygiene.py` does `TESTS.rglob("test_*.py")` against its own directory — it
scans `tests/` and nothing else. So it can never see mechanism 2, a hard wall-clock deadline of
exactly the kind it exists to prohibit, sitting in `src/`. It was accepted as adequate cover for the
parallelism flip; it covers half the tree. That is the same failure mode as the frontend flake
recorded above — a standard applied unevenly rather than a standard that did not exist.

**What would have caught this earlier is arithmetic, not more load.** The flip was qualified on
three consecutive green full-suite runs. At the ~1-in-5 failure rate we later measured *on a quiet
machine*, three greens in a row is a **~51% outcome** (0.8³ = 0.512): the verification was a coin
flip, and it came up heads. Clearing a 1-in-5 flake at 95% confidence takes roughly **fourteen**
consecutive green runs. So the bar was not merely applied on too quiet a tree — three runs cannot
clear a low-rate flake on *any* tree. It is worth killing the tempting wrong lesson explicitly:
"verify under load" is not the rule, because `-n auto` manufactures N-way contention among its own
workers, so an idle host was never a low-contention run. That is precisely why these races fired
1-in-5 there rather than not at all. When a verification bar is a run count, do the probability
before trusting it.

---

## T-75 — runtime events get a durable table, and the deferral that named its own trigger (2026-08-27) **[ADR]**

Full record: [ADR-0008](architecture/adr/0008-durable-event-log.md). Autopsies:
[BB-171](bug-bank.md) (the log itself, and the boot-window orphan) and [BB-172](bug-bank.md) (the
singleton that outlived a repointed state dir). This entry covers what neither does: why the task was
taken now, and what it deliberately left open.

**Why now, and why this is not relitigating.** `docs/architecture/12-event-architecture.md` §8
deferred the durable event log and named the condition to revisit it: *"first multi-process
deployment or first 'replay events since T' requirement."* The second trigger fired —
`GET /sessions/{id}/events?after=N` shipped — and nobody went back to the deferral, so two lossy
substitutes were built instead: a 2,000-entry in-memory ring and a message-row reconstruction that
could emit four of ~30 event types. T-75 is that decision's own stated follow-through, not a reopened
decision. §8 now lists three deferrals instead of four.

**Two things the task spec got wrong, and one it did not decide.**

1. *The ADR number.* The spec said `0006-durable-event-log.md`; `0006` and `0007` were taken in the
   interim and `adr-lint` enforces unique numbers. It is `0008`.
2. *`paths.py` grew an enforced registry after the spec was written.* Adding an `events_db` property
   is half a change — `HachiPaths.all_stores()` is what `hachi backup`/`restore`/`doctor` enumerate,
   and `test_every_hachi_path_is_registered_or_explicitly_exempt` fails on an unregistered path. It
   is registered `derived=False`: an audit log is not rebuildable, and one that backup silently skips
   is not an *owned* audit trail. The two tempting ways to make that test pass — `derived=True`, or an
   exemption — are both test-weakening under Rule 2.
3. *Store lifetime.* The spec's step 11 (one store per booted agent) and step 12 (the history branch
   reads it) are **incompatible**: the history branch runs precisely when no agent is booted, so a
   per-agent store is unreachable from the path that most needs it, and the "replay fidelity equals
   live fidelity" criterion becomes unsatisfiable. The store is process-wide, reached through a third
   collaborator alongside the `_read_events`/`_read_events_base` pair already in
   `GatewayControlPlane.__init__`. BB-42 independently requires one store per process. **That
   decision was correct and it is also what caused BB-172** — a process-wide singleton is reachable
   from everywhere, including from tests that repoint the state directory underneath it. Both halves
   are worth remembering together.

**Redaction was a decision, not an implementation detail.** Persisting payloads converts a transient
in-memory exposure into a durable on-disk one, and `run.suspended` carries `wait_key` — which the
LLM-facing tool schema documents as a *"webhook token"*, i.e. the credential that resumes a suspended
run. Redaction happens at the persist boundary, so only the durable copy is scrubbed and live SSE/TUI
consumers are unchanged. It is **two layers and it is partial**: `redact_structure` matches a PEM
block plus twelve provider-token shapes and provably cannot see an internal token with no vendor
prefix, which is why `_SENSITIVE_PAYLOAD_KEYS` exists as additive defence for that blind spot. The
ADR states the limit explicitly rather than claiming payloads are redacted. **A partial guarantee
described as total is worse than no guarantee**, because the next reader stops checking.

**Process note: only the full gate found the real bug.** Five stages each ran targeted tests green —
including the two stages whose work was defective. The full suite found 39 failures across nine files
none of them had touched, and the mechanism was only isolated by stashing the change and measuring
the parent commit (4643 passed / 0 failed, against 39 failed with it). This is CLAUDE.md Rule 2's
"scoped runs miss Hachi's cross-module regressions" as an observation rather than a maxim, and it is
the second time in two waves that reading the **verdict line** rather than the exit code was
load-bearing: the failing gate exited `0` (BB-155).

**Deliberately left open, so nobody mistakes these for oversights.**

- **No retention or pruning.** The table is unbounded. How long an audit trail lives is a product
  decision, not an implementation detail; `RetentionSettings` was left unwired on purpose.
- **The two `_sequence` counters stay independent.** `EventBus`'s is a per-process fan-out ordinal;
  `RuntimeReadModelStore`'s is what clients page against. Reconciling them touches the async-dispatch
  path.
- **`_synthesize_history_events` stays.** Sessions recorded before this table can only be replayed
  through it; deleting it is silent data loss for existing users.
- **Sequences are monotonic within a session but not contiguous**, because `seq` is global and
  concurrent sessions interleave. Do not write a test asserting contiguity.
- **The type gate is narrower than it looks.** Adding `core/events/` took `mypy-strict-gate.ini` from
  129 to 131 files, but `control_plane.py`, `ports.py`, `orchestrator.py` and `in_process_runtime.py`
  — the whole gateway wiring surface this task touched — remain outside it. Expanding that allowlist
  is its own task; recorded here so the gap is known rather than assumed closed.
- **`core/mcp_client.py`'s `_cached_secret_store` has BB-172's exact shape** — a module-global caching
  a value derived from `get_hachi_paths()`, forever, with no reset fixture. It did not fire here
  (neither boot-isolation profile configures MCP servers), but it is the same landmine and it is
  still armed.
- **Tests that boot a real agent write to the developer's real Hachi install**, and `events.db` now
  joins `sessions.db`/`jobs.db`/`runs.db` in doing so. Pre-existing — `tests/conftest.py` has no
  filesystem isolation and `core/paths.py` has no path override — and worth fixing on its own terms.

---

## T-70 — `tests/` mirrors `src/hachi/`, and the three depth dependencies that had to die first (2026-08-30)

Autopsies: [BB-173](bug-bank.md) (the move and its three interlocking hazards) and
[BB-174](bug-bank.md) (the sweep that certified an anchor it could not see). This entry covers what
neither does: why the task was sequenced the way it was, what was deliberately *not* done, and the
one methodological failure worth generalising.

**The finding was findability, and only findability.** All 358 test files sat flat in `tests/` —
recursive and non-recursive counts identical, which is the proof there was no structure at all. The
cost is not aesthetic: an agent that cannot locate the existing tests for a module writes a new file
and ships a duplicate suite that drifts from the original. At agent-velocity authorship that is a
live failure mode, not a tidiness argument. 343 files now mirror `src/hachi/`; 16 stay at the root,
each with a written reason.

**Three commits, in a forced order, each independently gated.** The order is the whole design. A
bulk move breaks three things at once and *none of them fails loudly at the moment a file moves*, so
they had to be retired one at a time with a green gate between:

1. `ad7273c` — 46 `Path(__file__)` sites in ~40 files each encoded "I live exactly one level below
   the repo root." Hoisted `REPO_ROOT` / `FIXTURES` into `conftest.py`, which stays at the tests root
   at any depth.
2. `5e274be` — added `tests/__init__.py`, because pytest's default `prepend` mode keys modules on
   their **basename** and the first two same-named files in different directories abort collection.
   That change also breaks the suite's own bare `from conftest import` idiom, so the 23 files using
   it moved to `from tests.conftest import` in the same commit.
3. `52be3c7` — the move. Zero content edits.

**Why the move commit contains no content edits, stated as a rule rather than a preference.** With
content untouched, git matches all 343 as *exact* renames by blob hash, on a fast path that never
consults the similarity matrix or `diff.renameLimit`. `git log --follow` and a reviewable
`git show --stat` then come for free — verified on three spot-checked files, whose history traces
back through the move to `ec70e0a` and `eeb663d`. Edit one byte in one file and that file alone
drops into similarity scoring. Note also that the spec's instruction to use `git mv` "so git records
renames" rests on a false premise: **git does not record renames, it detects them at diff time**, so
plain `mv` plus staging produces a byte-identical commit.

**The methodological failure, which is the part worth generalising.** The spec's §3.5 certified "no
cross-test imports — every file is independently relocatable," from the sweep
`rg "^\s*(from|import)\s+test_" tests/`. That anchored pattern sees only the *bare* form. One real
cross-import exists in the *qualified* form — `from tests.test_delegation_blocklist_integrity import
_registered_tool_names`, reusing an AST tool-inventory scanner (T-34 / BB-89) — and moving that file
broke it mid-run. The anchor was "re-derived" against the newer HEAD by re-running the spec's own
regex, which faithfully reproduced the spec's blind spot and converted an unexamined assumption into
an apparently *measured* one. **Do not re-verify a claim with the instrument that produced it.** This
is BB-172's lesson — refuting the mechanisms you thought of is not evidence about the ones you did
not — recurring one wave later in a new disguise, which is why it is filed separately.

The same shape appeared twice more in this task and was caught both times only by varying the
instrument: three of the 23 bare-`conftest` imports sit inside *function bodies*, so they collect
clean and fail at run time — a `--collect-only` check, the fast and obvious instrument, calls them
green. And the claim that the isort reshuffle would recur across 358 moves was settled by copying an
affected file one directory deeper and running ruff (clean) rather than by argument.

**Deliberately left open, so nobody mistakes these for oversights.**

- **The 55 batch-named files keep their names.** `test_g3_turn_lock.py` tells you which audit wave
  produced it and nothing about what it protects, but a 55-file rename commit rewrites `git blame`'s
  entry point for 55 files at once and buys nothing that the same renames spread over months do not.
  Spec §5 part 2 is explicitly opportunistic: rename by subject **as you touch a file**.
- **`test_delegation_blocklist_integrity.py` is pinned at the root by a cross-import, not by its
  subject** (`tools/delegation_tools.py`). Unpinning it means editing the importing file — fine in a
  later commit, wrong in a pure-rename one. The reason is recorded inline in `ROOT_ALLOWED` so the
  next reader can act on it deliberately instead of rediscovering it.
- **Five real `src/hachi` packages got no test mirror** — `gateway/routers`, `integrations/notion`,
  `integrations/slack`, `core/files`, `tui/cockpit/views` — because no file's subject concentrated on
  any of them. That is a coverage observation this task deliberately did not act on.
- **~64 prose references to bare `tests/test_X.py` paths** in source comments and test docstrings are
  now stale. They break nothing, and fixing them inside the move would have buried a 343-rename diff
  under unreviewable churn.
- **`ROOT_ALLOWED` is a bucket that will rot if it is allowed to.** It holds 16 entries, each with a
  written reason. Growing it without one defeats the task; the layout test is what makes adding an
  entry a deliberate act.

---

## The MCP request-timeout floor is protection, and it is now audible (2026-08-30) **[ADR]**

**Decision.** Keep the 1.0 s `request_timeout_seconds` floor, and make every override of a configured
value visible. The T-71 entry above left this explicitly open — *"Whether that is protection or a bug
needs a decision, not a guess"* — and this is the guess being replaced by a decision. Mechanism and
autopsy: [BB-176](bug-bank.md).

**Why protection, not a bug.** Below a second, a per-request JSON-RPC timeout stops being a deadline and
becomes a way to fail every call: an MCP server's `tools/list` round trip alone — subprocess spawn or an
HTTP handshake, then the response — will not reliably fit inside it. Honouring `0.2` verbatim would not
give a user a fast agent; it would give them a server that never answers, diagnosed as a timeout. The
floor was right. Nothing about the *number* needed changing.

**What was wrong was the silence.** Four copy-pasted `max(1.0, …)` sites, no named constant, no comment,
no docstring, no linked reference — in a file that otherwise tags its constants (`UC-DEV-001`, `MED-004`,
`BB-9`). The floor *was* documented, precisely and correctly, in a T-71 **test** docstring, where it
explains why that test picks `2.5`. That is the wrong audience by construction: nobody configuring an MCP
server reads the tests for the transport. **A fact recorded where its audience will not look is not
documented.** It now lives in one named constant, one helper, four constructor docstrings, and a warning
that names the server and both values at the moment it fires.

**Config is deliberately not clamped, and that is the load-bearing half of the decision.**
`MCPServerConfig.to_toml_entry` still round-trips the user's `0.5` back to `settings.toml` verbatim. The
tempting alternative — clamp at parse time so everything downstream agrees — trades one dishonesty for a
worse one: the file would then misreport what the user *asked for*, not merely what runs, and it does so
by silently editing something they wrote. The rule this sets: **correct the behaviour, warn about the
difference, never rewrite the statement of intent.** The cost is that `settings.toml` and the runtime can
disagree; the warning is what makes that disagreement discoverable, and the config-field comment says so
where the field is defined.

**One config value must yield one number.** The same constructor derived two — a clamped copy for the
transport and a raw copy into `RetryPolicy(timeout_s=…)`. That field is *inert* on this path today
(`resilient_call` reads `max_attempts` and `backoff_base_s`, plus the breaker fields via
`HealthRecorder`, and never `timeout_s`; its only reader anywhere is `router.py`, on the LLM path's own
policy). Fixing an unread field looks like busywork and is not: "nothing reads it" is a fact about this
path *today*, not about the type, and a value that is wrong but unread is a bug with a delay fuse.
Verified by reading `resilient_call`'s body rather than grepping the field name — a grep finds the
declaration and its docstring and says nothing about readers.

**The general rule.** A silent clamp is a config lie. Code that overrides a configured value owes the
configurer one of three things: a warning when it overrides, the constraint stated in the docstring where
the parameter is documented, or a validation error at parse time. Silence is not one of them.

---

## Open: two CI checks on PR #144 are red by decision, and one had never run (2026-08-30)

PR #144 lands the wave-2 branch with two red checks, `security` and `benchmark`. Both were red before
this branch touched anything, neither is a regression, and both were left red on purpose.

**`security`.** Fails the "Strict security audit (fail on medium)" step on two high findings that both
describe fail-closed states — `GOOGLE_CLIENT_ID` unset ("should fail closed") and `HACHI_REST_ALLOWLIST`
empty ("secret-bearing REST calls are blocked") — plus one medium for the development-grade process
sandbox. The step is gated `if: github.event_name == 'pull_request'`; this repo develops on `main`
pushes, so the gate had never executed on this history. "Green on main" meant "never ran", not "passed".

Three ways to make it green, and why none was taken. Setting the two variables in the CI job supplies
exactly the configuration the audit exists to detect the absence of — green, and permanently blind.
Relaxing the strict step removes the only place the gate ever runs. Fixing the severity model so
*unconfigured-and-fail-closed* scores info/low while *misconfigured-and-exposed* stays high is the
defensible answer, and it is a change to security architecture, which CLAUDE.md says to flag rather than
auto-build. Flagged. See BB-179.

**`benchmark`.** M1 retrieval p@5 = 0.677 against a 0.8 threshold, 0 false positives, 31 queries. All ten
misses have one shape: the query resolves to the *integration* node and never reaches the *skill* node —
`'slack standup'` → `['slack']` where `daily_standup` was expected, `'email inbox'` → `[]`. Token savings
passed at 0.973. It fails identically on `main`. Greening it means genuinely improving skill-node recall,
which is a scoped product task rather than a CI repair; lowering `p5_threshold` below the measured value
is precisely what Rule 2 exists to prevent. Left red.

**What would change each.** `security`: a severity model that distinguishes an unconfigured fail-closed
state from an exposed one. `benchmark`: recall that reaches past integration names to the skill nodes
behind them.

**Correction (2026-08-30).** The paragraph that stood here called the 22 remaining test-job failures
"all environmental". That was wrong, and only the second half — that they were all present on `main` —
held up. On investigation, five of the six root causes were **production bugs**, not host problems:
BB-180 (a version-dependent `Path.stat` re-entry, plus two unguarded probes that crash on a genuinely
broken stat; a daemon filename allowlist missing versioned interpreters; a path folded mid-filename by
Rich), BB-181 (a bootstrap guard testing existence while an unrelated `mkdir` established existence),
BB-182 (a `@staticmethod` availability check blind to its injected dependency), and BB-183 (a composite
delete leaving a credential on disk while reporting success). Only the keyring test's own hermeticity
was a genuine test defect — and pulling on it is what exposed BB-183, which had never appeared in any
suite. All are now fixed and locked; see those entries in `docs/bug-bank.md`.

**The lesson worth keeping:** "fails only in CI" invited the environmental reading, and the reading was
comfortable because it implied nothing needed fixing. The runners were not misconfigured; they were the
only machines whose environment did not silently satisfy a precondition the code had come to depend on.

**Two structural gaps this surfaced, flagged and not acted on:** the suite is not sandboxed from the
developer's real installation — `get_hachi_paths()` resolves through `PlatformDirs` with no environment
override and no autouse fixture redirects it, so tests read and write the real `hachi` workspace, which
is precisely what hid BB-181. And CI tests **only** Python 3.11 while development happens on 3.14, with
3.12 and 3.13 claimed in the classifiers and exercised nowhere; every BB-180 bug lived in that gap.
