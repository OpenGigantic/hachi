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
   Documented in `docs/settings.md`; the user should update/remove the misleading comment.

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
reference in `docs/settings.md`.

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
- **8b model:** documented tiered trust in `docs/og-security.md` — **Tier 1** engine-enforced,
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
`docs/memory.md`.

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
- `docs/memory.md` authored; `settings.example.toml` extended with the new memory + `elevenlabs_model` keys.

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
