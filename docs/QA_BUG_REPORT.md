# Tokoboros — QA Bug Report

**Date:** 2026-06-11
**Branch:** `fix/session-bugs-and-schedule-tools`
**Env:** Windows 11, Python 3.14.3, permissions tier = `unrestricted`
**Method:** 10 agents, one per feature area, exercised the code **live** (real credentials) + via unit/integration tests + CLI. Each area's full detail is under `docs/qa/`; this file is the consolidated, fix-it-one-by-one list.

**Pytest baseline:** ✅ **1280 passed, 13 skipped, 0 failed** (skips are environmental: Playwright/pptx/launchd/symlinks). The regression suite is green — every bug below is something the suite does **not** catch.

> Severity: **Critical** (data loss / security / total breakage) · **High** (feature broken) · **Medium** (broken edge / bad UX) · **Low** (minor) · **Nit** (cosmetic).
> Each finding is tagged **[live]** (reproduced against the running system) or **[code]** (found by reading, repro'd synthetically).

---

## 0. Action items for YOU (credentials & config — not code bugs)

1. **Telegram — send `/start` to your bot.** Token is valid (`@toko67bot`) but chat `1674072270` returns *"chat not found"* — you've never opened a chat with the bot, so it cannot message you. Message `@toko67bot` → `/start`, then the live send works.
2. **Slack — the bot token is webhook-scoped only.** `xoxb-…` carries just `incoming-webhook`, so all six Slack Web-API tools fail `missing_scope`. Add scopes `chat:write, channels:read, groups:read, reactions:write, files:write` (+ a `xoxp-` **user** token for `search:read`). The webhook works.
3. **Discord — add the bot to your server.** Webhook works; bot-mode send to channel `1514470972953067682` returns `403 Missing Access` — the **Toko** bot isn't in that guild.
4. **Google — DONE & tested live** (account `opsingh861@gmail.com`; token refreshes against project `art-gallery-404021`). Two follow-ups for full coverage: **(a)** enable **Drive, Docs, Sheets, and People** APIs in Cloud project `art-gallery-404021` (#418537082803) — they're disabled, so those services 403 *"API not enabled"*; **(b)** `find_free_time` needs `calendar.readonly` (or `calendar`) added — only `calendar.events` is requested (see M-12).
5. **(Optional) WhatsApp** — no Meta/Twilio creds; only not-configured + parser paths were tested.
6. **Local `settings.toml` is mis-keyed** → see **H-8** (gateway LLM 401). `provider.api_key_ref = "OPENAI_API_KEY"` while `kind = anthropic`. Bad legacy-import default *and* a code bug.

---

## 1. Systemic themes (each shows up in multiple areas — fix once, fix many)

- **T1 · Windows Unicode console crashes.** `session stats` (`→`), `skill show` (`≈`), and `schedule list` (em-dash) all `UnicodeEncodeError`/mojibake on the default cp1252 console. Root: the Rich `Console` at `cli/main.py:60` has no `errors="replace"` / forced UTF-8. One fix covers all CLI commands. (H-1, OG-Med, Infra-Nit, Google-Low)
- **T2 · Upstream API error bodies are swallowed.** Notion, Telegram, Slack, **and Google** tools discard the provider's JSON error and surface a generic HTTP string. The agent loses the actionable reason (`object_not_found: share the page…`, `chat not found`, `needed: <scope>`, Google's `error.message`) and cannot self-correct. (H-7, H-9, Telegram-Low, Slack-Low)
- **T3 · Eager construction aborts boot / crashes direct use.** A missing `uv` (sandbox) or a wrapped vault key (Notion) raises at *registration/`__init__`* time instead of degrading — one missing optional dependency can stop the whole tool registry mounting. (H-4, Notion-Med)
- **T4 · "False success" reporting.** Desktop notify, digest flush, and `send_telegram_message` report success while the underlying send failed (rc≠0, `ok:false`, email 535) → silent loss. (Email-Med×2, Telegram-Med)

---

## 2. CRITICAL

### [Critical][live] C-1 — `notion_query_database` is 100 % broken under the pinned API version
- **Where:** `src/tokoboros/integrations/notion/service.py:18` (`NOTION_VERSION = "2025-09-03"`) + the `databases/{id}/query` call.
- **Detail:** Notion API `2025-09-03` **removed** `POST /v1/databases/{id}/query`. Proven against a real shared DB: tool → 400; raw query → `400 invalid_request_url` on `2025-09-03` but **200** on `2022-06-28`; the replacement `data_sources/{ds_id}/query` → 200. (All other Notion ops work under 2025-09-03 — only query broke.)
- **Fix:** pin `NOTION_VERSION = "2022-06-28"`, **or** migrate query to `data_sources/{data_source_id}/query` (and update the "UUID of database" tool description).
- Detail: [notion.md](qa/notion.md)

### [Critical][live] C-2 — PowerShell command injection in desktop notifications (security)
- **Where:** `src/tokoboros/tools/notification_tools.py` / `core/notifier.py` `_send_windows`. *(Agent rated High; elevated — arbitrary code execution from untrusted text.)*
- **Detail:** The notification **body** was interpolated into a double-quoted PowerShell string; PS expands `$`, `$(...)`, backticks inside double quotes → body executes. Verified live: `$(2+2)`→`4`; `$env:PATH` leaked into the toast. An agent surfacing external content (email/Slack/Telegram) via `notify_user`/digest → **RCE**.
- **Fix:** pass body out-of-band (env var / here-string / `-EncodedCommand`), never interpolate.
- **✅ STATUS: FIX APPLIED & KEPT** (your decision). Background fix-task passed title/body via `$env:` vars instead of interpolation (`core/notifier.py`, +3 tests; `test_notification_tools.py` **34/34 green**). Unstaged in your tree — review & stage when ready.
- Detail: [email-notifications.md](qa/email-notifications.md)

---

## 3. HIGH

### [High][live] H-1 — `tokoboros session stats` crashes on Windows (T1)
`UnicodeEncodeError: 'charmap' can't encode '→'` at `cli/main.py:4128`. `--json`/`PYTHONIOENCODING=utf-8` dodge it. Fix the shared console (`cli/main.py:60`). → [core-engine.md](qa/core-engine.md)

### [High][live] H-2 — One error kills the whole Telegram bot
`channel telegram` poll loop (`cli/main.py:2364`) has **no per-iteration try/except**; a network blip / `409 Conflict` / over-long reply stops the bot. The sibling WhatsApp command guards this. → [telegram.md](qa/telegram.md)

### [High][live] H-3 — Slack integration tools bypass outbound-secret redaction (security)
`slack_send_message` / `slack_upload_file` write text straight to the wire, skipping the `redact_outbound` guard that `messaging_tools.send_slack_message` applies. Same agent registry → a secret the agent read can be exfiltrated via the unguarded path. → [messaging-channels.md](qa/messaging-channels.md)

### [High][code] H-4 — Missing `uv` aborts the entire tool-registration boot (T3)
`SandboxExecutor()` is constructed eagerly at registration (`tools/sandbox_tools.py:15`), raising `OSError` before its own handler; `ToolRegistrar.install` has no per-spec isolation → **everything after `sandbox` never mounts**. → [infra-tools.md](qa/infra-tools.md)

### [High][live] H-5 — Gateway returns raw `text/plain` 500 when the LLM raises
All three chat surfaces (`/sessions/{id}/messages`, `/v1/chat/completions`, `/channels/{c}/inbound`) emit a bare `500` outside the `ErrorResponse` envelope — no catch-all `Exception` handler (only 400/422/503). → [gateway-openapi.md](qa/gateway-openapi.md)

### [High][live] H-6 — Parser corrupts 17 of 23 shipped skills
`BLOCK_PATTERN` (`og_engine/parser.py:301`) prefix-matches headers with no word boundary, so plural `::assertions` parses as `::assertion` + a stray block named `"s"`. Ships from `default_skills/`, surfaces to the LLM (`load_skill`) and users (`skill assert`); same defect leaks `s` for `::notes/::examples/::tables/::configs`. `skill lint` doesn't flag it. Fix: add `\b`. → [og-skills-delegation.md](qa/og-skills-delegation.md)

### [High][live] H-7 — Notion tool errors discard the API body, killing self-correction (T2)
On 404 the agent sees *"Client error '404'… check MDN"* instead of `object_not_found: … share the page with your integration "toko"`. → [notion.md](qa/notion.md)

### [High][live] H-8 — Gateway hands the Anthropic client an OpenAI key → 401 on every turn
`create_llm_client` resolves `provider.api_key_ref` first; `settings.toml:10` has `api_key_ref="OPENAI_API_KEY"` (kind=anthropic) → `sk-proj-…` goes to Anthropic → `401 invalid x-api-key` → the bare 500 in H-5. Valid env `ANTHROPIC_API_KEY` never consulted. Also blocked `schedule run-now`. Fix: prefer the kind-appropriate key / validate ref↔kind; repair the legacy default. → [gateway-openapi.md](qa/gateway-openapi.md)

### [High][live] H-9 — Google tool errors discard the API body (T2, same family as H-7)
`integrations/google/client.py:79-85` routes only **403** to the friendly handler; 400/401/404/etc. hit a bare `raise_for_status()` → the agent sees `Client error 'NNN'… developer.mozilla.org/…` instead of Google's real `error.message` (live: Google said *"Missing time zone definition for start time."* / *"Not Found"*, Tokoboros showed only the MDN link). Affects every Google service. → [google.md](qa/google.md)

---

## 4. MEDIUM

| ID | Area | Where | Issue | Fix |
|----|------|-------|-------|-----|
| M-1 | Core | `core/loop.py:498→:597` | Empty/truncated LLM response (e.g. `max_tokens`) falls through to a misleading `"exceeded maximum iterations"` and crashes the turn (no try/except in `AgentSession.chat`). | Treat empty+no-tool-calls as a graceful stop. |
| M-2 | Telegram | `tools/messaging_tools.py:196` | `send_telegram_message` ignores the result → HTTP-200 `{"ok":false}` reported as "Sent" (T4). | Inspect `ok`; surface `description`. |
| M-3 | Notion | `integrations/notion/service.py:25,30` | `NotionService.__init__` **raises** on a missing key (vault `get_token` raises, not None), defeating lazy design; friendly message is dead code. | Catch → defer to call time. |
| M-4 | Email | `tools/notification_tools.py` (`_send_windows`) | Desktop notify ignores rc/stderr → rc=1 still returns "sent" (T4). | Check return code. |
| M-5 | Email | `core/notifier.py` `is_configured()` | Checks only `SMTP_USER`, not `SMTP_PASSWORD` → True with no password, then silently skips `smtp.login()`. | Require both. |
| M-6 | Email | `tools/notification_tools.py` `flush_notification_digest` | Reports success if *either* email or desktop fired → an email `535` hidden behind "desktop shown" (T4). | Report per-channel outcomes. |
| M-7 | Voice | `core/voice.py` (`get_audio_info`) | OpenAI `tts-1` WAV uses the `0xFFFFFFFF` streaming sentinel → `wave.getnframes()` → reports **24.8 h** for a ~1.35 s clip. | Clamp `nframes` to file size / derive from `byte_rate`. |
| M-8 | Infra | `tools/schedule_tools.py` (`schedule list`) | Rich parses `[interval, enabled]` as a style tag → kind + paused/active state invisible. | Escape Rich markup. |
| M-9 | Infra | `jobs/scheduler.py` `Scheduler.get` | `schedule show <prefix>` advertises prefix lookup but does exact-match only → valid prefix → "not found". | Implement prefix match. |
| M-10 | Infra | `daemon.py` (Windows `install`) | Unquoted `schtasks /tr` → any space in the Python path yields a broken task. | Quote the program path. |
| M-11 | Gateway | `gateway/fastapi_app.py` | OpenAPI omits the auth scheme (`_require_auth` is a plain `Depends`) → no `securitySchemes`, no documented 401s across 25 paths. | Use a security dependency / document it. |
| M-12 | Google | `integrations/google/calendar.py` (scopes) | `find_free_time` 100% 403s — `/freeBusy` needs `calendar`/`calendar.readonly`, but only `calendar.events` is declared. | Add the read scope. |
| M-13 | Google | `integrations/google/calendar.py` `_dt_field` | `create_recurring_event` 100% 400s — omits a named `timeZone` when the time has an offset/`Z`; recurring events require one (replay with `timeZone:"UTC"` → 200). | Always set `timeZone`. |

## 5. LOW / NIT (full detail in the area files)

| Area | Items |
|------|-------|
| Core | **L** duplicated `repair_tool_pairs()` block `loop.py:435-451`; **L** `frozen+slots` dataclasses raise `TypeError` not `FrozenInstanceError` for undeclared attrs (CPython 3.14 quirk); **Nit** MemoryVault guard didn't block a prompt-injection probe (informational). |
| Telegram | **L** friendly error drops Telegram's `description` (`messaging_tools.py:199`, T2); **L** `poll_once` silently returns `[]` on `ok:false` (409); **Nit** no chunking → adapter path crashes on >4096-char replies. |
| Messaging | **L** `slack_search_messages` can't work with a bot token (needs `xoxp-`); **L** Slack errors drop the actionable `needed` scope; **Nit** webhook-scope failures are graceful (caveat). |
| Notion | **L** no pagination (read/query/search truncate >100/20/5); **L** `item["object"].upper()` unguarded in search; **Nit** "UUID of database" descriptions mislead post-migration. |
| Email | **L** `notify_user` blocks ~8 s (sync `Start-Sleep 4500` + PS cold start) inline; **L** bad `SMTP_PORT` surfaces a raw `int()` ValueError. |
| Voice | **L** format mismatch: `speak_text` advertises `.opus/.aac/.pcm` but `transcribe_audio` rejects them (`_SUPPORTED_EXTENSIONS` lists `.ogg`) → advertised round-trip dead-ends (mp3/wav/flac fine). |
| Infra | **L** no daemon dry-run/preview mode; **Nit** em-dash mojibake on Windows console (T1). |
| Gateway | **L** inconsistent error shapes (`ErrorResponse` vs `{"detail":…}` vs `text/plain`); **L** `_api_sessions` not pruned on `close_session` (leak); **Nit** `GET /` → 404. |
| OG | **L** malformed `.og` files silently dropped (log only); **L** ~52/86 lint warnings are benign noise; **Nit** `generate_presentation` accepts out-of-enum themes silently. |
| Google | **L** `plan_my_day` date header falls back to raw ISO on Windows (`%-d` is glibc-only, T1); **L** inconsistent error contract (some tools raise, siblings catch-return); **L (code)** Meet uses a fire-and-forget daemon thread — violates the repo's "no fire-and-forget threads" guardrail; **L** `search_drive`/contacts listing have no pagination; **Nit** `_raise_forbidden` prints a blank API name for absolute-URL services. |

---

## 6. Global findings (from credential setup)

- **[Medium] GLOBAL-1** — Error messages tell users to run `tokoboros secrets set <key>` and edit `~/.tokoboros/vault.json`, but **neither exists** (`secrets` has only `--list/--delete/--check`; no `vault.json` reader). Onboarding dead-end for every vault integration. `credential_vault.py:38`, `integrations/{notion,slack}/service.py`.
- **[Medium] GLOBAL-2** — `.env` is never loaded into `os.environ`; channel/messaging/notification creds in `.env` are silently ignored (only provider keys read, via `core/llm.py:_read_dotenv`). Fix: load `.env` at CLI start, or document loudly.
- **[High → Resolved] GLOBAL-3** — Was: Google unusable (client id/secret absent → stub only, no refresh). **Resolved:** creds supplied for project `art-gallery-404021`; cached token refreshes and Gmail/Calendar/Tasks verified live. Product gap still worth fixing — persist client id/secret with the token, and have `doctor` warn when a token exists but client creds don't.
- **[Low] GLOBAL-5** — SMTP user given without a domain; assumed `@gmail.com` + stripped app-password spaces. **Resolved:** the live email sent with no 535 — assumption correct.

---

## 7. Per-area detail index

| Area | File | Sev counts | Live result |
|------|------|-----------|-------------|
| Core engine / memory / sessions / permissions | [core-engine.md](qa/core-engine.md) | H1 M1 L2 N1 | pytest 1280✅; loop/memory/perms/caps verified |
| Telegram | [telegram.md](qa/telegram.md) | H1 M2 L2 N1 | send blocked (no `/start`); parsing/allowlist ✅ |
| Discord / Slack / WhatsApp | [messaging-channels.md](qa/messaging-channels.md) | M1 L2 N1 | Discord+Slack webhook ✅; bot paths scope/guild-blocked |
| Notion | [notion.md](qa/notion.md) | C1 H1 M1 L2 N1 | CRUD ✅; query_database broken (C-1) |
| Email + notifications | [email-notifications.md](qa/email-notifications.md) | C1(sec) M3 L2 | live email ✅; PS injection (C-2, fix applied) |
| Voice + Vision | [voice-vision.md](qa/voice-vision.md) | M1 L1 | TTS↔STT round-trip ✅; vision+SSRF ✅ |
| Browser / Sandbox / Scheduler / Daemon | [infra-tools.md](qa/infra-tools.md) | H1 M3 L2 N1 | sandbox+scheduler ✅; browser graceful (no chromium) |
| Gateway + OpenAPI | [gateway-openapi.md](qa/gateway-openapi.md) | H1 M2 L2 N1 | boots+serves✅; OpenAPI in sync✅; LLM 401 (H-8) |
| .og engine / skills / delegation / workflow / pptx | [og-skills-delegation.md](qa/og-skills-delegation.md) | H1 M2 L2 N1 | delegation+pptx✅; parser corrupts skills (H-6) |
| Google Workspace | [google.md](qa/google.md) | H1 M2 L3 N1 | Auth/Gmail/Calendar-CRUD/Tasks ✅; free-time+recurring ❌; Drive/Docs/Sheets/Contacts ⛔ (APIs disabled); Meet ⛔ (no Workspace) |

**Totals: 2 Critical · 9 High · 13 Medium · ~20 Low · ~8 Nit**, plus 4 global findings (1 resolved). Regression suite green. All 10 areas tested (C-2 fix applied & kept).
