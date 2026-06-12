# Tokoboros — Open Bugs

**As of:** 2026-06-12 · **Commit:** `8d6155b` (post-fix) · **Suite:** 1300 passed, 0 failed.

This is the remaining fix list. Bugs found in the QA pass that have since been **fixed and re-verified are removed**. All credentials/integrations are wired and verified live (Telegram, Discord webhook + bot, Slack webhook, Notion, Gmail SMTP, OpenAI voice, full Google suite). Detailed per-area evidence (incl. the fixed history) remains under `docs/qa/`.

**Open: 3 Medium · 21 Low/Nit.** (H-6 High fixed & verified 2026-06-12 — see Notes.)

---

## 🟠 MEDIUM

### M-7 — Audio duration mis-reported as ~24.8 h for OpenAI-TTS WAVs
- **Where:** `src/tokoboros/core/voice.py:195` — `duration = wf.getnframes() / sample_rate`.
- **What:** OpenAI `tts-1` WAVs carry the `0xFFFFFFFF` streaming-length sentinel in the RIFF/`data` chunk, so `wave.getnframes()` returns `0x7FFFFFFF` → `get_audio_info`/`probe_audio_info` reports `89478.485 s` (24.86 h) for a ~1.3 s clip. (Tests miss it — they only build correct-header WAVs.)
- **Fix:** clamp `nframes` to the actual file size, or derive duration from `byte_rate` and the data-chunk byte length.

### M-11 — Gateway OpenAPI omits the auth scheme entirely
- **Where:** `src/tokoboros/gateway/fastapi_app.py` (`create_gateway_app`) + export at `src/tokoboros/cli/main.py:1408`.
- **What:** `_require_auth` is a plain `Depends`, so the exported schema has no `securitySchemes` and no documented `401` on any of the 25 paths — the schema is identical whether auth is on or off. Frontend/clients can't see that auth exists.
- **Fix:** install a custom `app.openapi()` that adds a bearer `securityScheme` + per-route `security` and documents 401s.

### GLOBAL-1 — Error messages point to a `secrets set` command / `vault.json` that don't exist
- **Where:** `src/tokoboros/core/credential_vault.py:38` ("Set it via `tokoboros secrets set …`"); `integrations/notion/service.py:42` + `integrations/slack/service.py:32` ("Add '…' to `~/.tokoboros/vault.json`"); actual `secrets` command at `cli/main.py:1796` has only `--list/--delete/--check`.
- **What:** A user who hits a missing vault credential is told to run a non-existent command / edit a non-existent file. Onboarding dead-end for Notion/Slack.
- **Fix:** add `tokoboros secrets set <key>` (prompt + `create_default_secret_store().set(...)`) and correct the message wording.

---

## ⚪ LOW / NIT

| # | Area | Where | Issue | Fix |
|---|------|-------|-------|-----|
| 1 | Telegram | `channels/telegram.py:78` | `poll_once` returns `[]` on `ok:false` (e.g. 409 Conflict) with no signal — bot looks idle while silently failing. | Check `ok`; log/raise on false. |
| 2 | Telegram | `channels/telegram.py:84` | No 4096-char chunking on the adapter send path → Telegram 400 on long replies. | Split into ≤4096 chunks. |
| 3 | Slack | `integrations/slack/workspace.py:13` | `slack_search_messages` calls `search.messages` with a bot token — can never work (needs a `xoxp-` user token). | Support a user token, or drop/flag the tool. |
| 4 | Slack | `integrations/slack/service.py:51,71,83` | API errors surface only `error`, dropping the actionable `needed`/`provided` scope. | Include `needed`/`provided`. |
| 5 | Notion | `integrations/notion/databases.py:25,60`; `pages.py` | No pagination — read/query/search silently truncate (`results[:5]`/`[:20]`, block reads not paged). | Follow `next_cursor`/`has_more`. |
| 6 | Notion | `integrations/notion/databases.py:27,42` | `item.get("object").upper()` unguarded against `None` (mitigated by surrounding try/except). | Guard `None` before `.upper()`. |
| 7 | Email | `core/notifier.py:77` | `notify_user` blocks ~8 s inline (`Start-Sleep -Milliseconds 4500` + PS cold start). | Make non-blocking / shorten. |
| 8 | Email | `core/notifier.py:259` | `int(TOKOBOROS_SMTP_PORT)` unguarded → raw `invalid literal for int()` surfaced to user. | Validate with a friendly message. |
| 9 | Voice | `tools/voice_tools.py:105` vs `core/voice.py:63` | `speak_text` advertises `.opus/.aac/.pcm`, but `_SUPPORTED_EXTENSIONS` lacks them → `transcribe_audio` rejects what TTS just wrote. | Align the advertised + supported sets. |
| 10 | Infra | `daemon.py` (`DaemonInstaller`) | No dry-run/preview — `install()` writes the unit + runs commands immediately. | Add `--dry-run` that prints the unit. |
| 11 | Gateway | `gateway/fastapi_app.py:202` | `_require_auth` raises `HTTPException(detail=…)` → `{"detail":…}`, inconsistent with the `ErrorResponse{code,message,details}` envelope used elsewhere. | Raise the `ErrorResponse` shape. |
| 12 | Gateway | `gateway/control_plane.py:284` | `_api_sessions` not pruned on `close_session` (bounded, self-healing leak). | Pop the `session_key` mapping too. |
| 13 | Gateway | `gateway/fastapi_app.py` (routes) | `GET /` → 404 (only `/health`,`/readiness`,`/capabilities`,`/models` exist). | Add a root info/redirect route. |
| 14 | OG | `og_engine/engine.py:341` | Malformed `.og` files are silently dropped (log warning only) — no surfacing in `doctor`/`lint`. | Report skipped files in `doctor`/`lint`. |
| 15 | OG | `tools/presentation_tools.py:145` | `generate_presentation` silently falls back to default for an out-of-enum `theme`. | Validate `theme`, error on unknown. |
| 16 | Google | `integrations/google/meet.py:168` | Meet uses a fire-and-forget `daemon=True` thread — violates the repo "no fire-and-forget threads" guardrail. | Route through `jobs/queue.py`. |
| 17 | Google | `integrations/google/client.py:123` | `_raise_forbidden`: `url.split("/")[1]` is `""` for absolute-URL services → "the **·** API is not enabled". | Parse the service name from the host. |
| 18 | Google | `integrations/google/planning.py:146` | `plan_my_day` uses glibc-only `%-d` → falls back to raw ISO date on Windows. | Use a portable date format. |
| 19 | Google | `integrations/google/drive.py` | Inconsistent error contract — `search_drive/create_folder/move_file` let client errors propagate while siblings catch-and-return strings. | Pick one contract. |
| 20 | Google | `integrations/google/drive.py:14`; `contacts.py:21,99` | No pagination on `search_drive`/contacts listing. | Follow `nextPageToken`. |
| 21 | Core | `core/turn_context.py:21` | `frozen+slots` dataclasses raise `TypeError` (not `FrozenInstanceError`) for *undeclared* attrs — CPython 3.14 quirk; field mutation still raises correctly. | Low priority; note in code. |

---

### Notes
- **H-6** (OG parser dropping plural `::assertions`) — **fixed & verified 2026-06-12.** `parser.py` now consumes an optional trailing `s` outside the capture group (`s?\b`), so `::assertions/::notes/::examples/::tables/::configs` parse to the canonical singular block type. Re-verified: 34/34 plural-assertion skills parse, 0 bogus `"s"`, suite still 1300 green. *(Change is in the working tree, unstaged — ready for you to review & commit.)*
- **M-12** (`find_free_time`) is now **resolved** — the code fix needed a one-time Google re-consent for the `calendar.readonly` scope, which has been granted; freeBusy returns 200.
- **Slack Web-API tools** are blocked only by token scopes (not a code bug) — see the chat reply for the exact scope list to enable + reinstall.
