# QA Re-Verification — commit `8d6155b`

Re-ran the original repro for each bug against current `main` (commit `8d6155b`) on Windows 11,
`./.venv/Scripts/python.exe` (Python 3.14), `PYTHONPATH=src`. Live tokens sourced from repo-root
`.env`; tier `unrestricted`; vault holds `notion_api_key` / `slack_bot_token` / `oauth.google`.

## Summary

- **Full test suite:** `python -m pytest -q` → **1300 passed, 13 skipped** (0 failed). Baseline was 1280 passed; +20 net new tests, no regressions in the suite.
- **Verdict tally (16 bugs):** ✅ FIXED **15** · ⚠️ PARTIAL **1** · ❌ STILL BROKEN **0** · ◻️ CAN'T-VERIFY **0**.
- The single ⚠️ PARTIAL is **H-6**: the original parser corruption (bogus `"s"` block) is gone, but the fix over-corrected and now silently drops every `::assertions` (plural) block — used by 17 of 22 shipped skills, including `core_currency`.

## Verdicts

| Bug | Verdict | Evidence (real output / file:line of the fix) |
|-----|---------|-----------------------------------------------|
| **C-1** Notion `query_database` | ✅ FIXED | `NOTION_VERSION = "2022-06-28"` (service.py:18). Live: created temp DB under page `f949…`, added row "Row Alpha", `query_database()` → `- Row Alpha (ID: 37d6e122-…)`; DB archived (`archived: True`) for cleanup. |
| **C-2** PowerShell injection (`_send_windows`) | ✅ FIXED | notifier.py:148-180 + `_WINDOWS_NOTIFY_SCRIPT`. Title/body travel via env vars `TOKOBOROS_NOTIFY_TITLE/BODY`; `-Command` is a constant reading `$env:…`. Real PowerShell run: body `$(2+2)` → literal `$(2+2)` (not `4`); `$env:USERNAME` → literal (not expanded); backtick payload preserved. Live balloon fired, no execution. |
| **H-1** `session stats` Unicode crash | ✅ FIXED | cli/main.py:62-69 reconfigures stdout/stderr to utf-8/replace on win32. Console code page 437, no `PYTHONIOENCODING`: `session stats` printed `→`/`—` correctly, exit 0. Proof: raw python stdout = `cp1252/surrogateescape`; after importing cli.main = `utf-8/replace`. |
| **H-2** Telegram bot loop resilience | ✅ FIXED | cli/main.py:2378-2391 wraps each iteration in try/except (re-raises `KeyboardInterrupt`, swallows transient). Simulation: `poll_once()` raising `409 Conflict` did not kill the loop (4/4 iters ran, message delivered after recovery); a raising `dispatch` is isolated per-message. |
| **H-3** Slack redaction bypass | ✅ FIXED | chat.py `_guard_outbound` applied in both `send_message` (line 28) and `upload_file` initial_comment (line 45-46). Mocked transport: `sk-proj-…`+`xoxb-…` → `[REDACTED SECRET]` on the wire for both paths; return note "(2 secret(s) redacted…)". |
| **H-4** Missing-`uv` boot abort | ✅ FIXED | sandbox_tools.py:15-38 — `SandboxExecutor` built lazily in a 1-elem list on first call. With `_resolve_uv` patched to raise: `register_sandbox_tools` did NOT raise, `python_sandbox` mounted, a subsequent tool also mounted; tool call returned the OSError as a string (graceful). |
| **H-5** Gateway raw 500 | ✅ FIXED | fastapi_app.py:228-236 catch-all `_internal_error_handler`. Live gateway:8797 with a bad `ANTHROPIC_API_KEY`, chat → **HTTP 500, `content-type: application/json`**, body `{"code":"internal_error","message":"Error code: 401 - {…invalid x-api-key…}","details":null}` (not text/plain). |
| **H-6** OG parser corruption (`BLOCK_PATTERN`) | ⚠️ PARTIAL | parser.py:303 added `\b`. Original bug gone: no skill has a bogus `"s"` block (all 29 skills / 52 nodes scanned → NONE). BUT `\b` makes `::assertions` (plural) match **nothing**, so those blocks are now dropped: `core_currency.assertions == []`. 17 of 22 shipped skills author `::assertions`; only the 5 using singular `::assertion` parse (analyst/coder/devops/emailer). New defect introduced by the fix — see Regressions. |
| **H-7** Notion error body swallowed | ✅ FIXED | service.py:55-70 `_raise_for_status`. Live 404 on bogus page id → `Notion API error 404 — object_not_found: Could not find page with ID: … Make sure the relevant pages and databases are shared with your integration "toko".` Agent-facing tool surfaces the same. No generic MDN string. |
| **H-8** OpenAI key → Anthropic 401 | ✅ FIXED (code-side) | llm.py:451-455 forces `_resolve_api_key("ANTHROPIC_API_KEY")` for `kind=="anthropic"`. Live gateway:8796 one real chat → **HTTP 200, `{"response":"PONG"}"`**. Unit: with `api_key_env`/`api_key_ref="OPENAI_API_KEY"` + both keys set, resolved client still uses the `sk-ant-…` key. Active `tokoboros.json` has no provider block (defaults to `ANTHROPIC_API_KEY`); the change is a defensive guard against a corrupt ref. |
| **M-1** loop empty-response crash | ✅ FIXED | loop.py:487-493 — empty content + no tool_calls → `complete_task` + graceful return. Stub LLM returning `LLMResponse(content="", tool_calls=[])`: `run()` returned `''` after **1** LLM call (not 15), no "exceeded maximum iterations". |
| **M-2** `send_telegram_message` ignores result | ✅ FIXED | messaging_tools.py:199-202 inspects `ok`. Fake sender `ok:false` → `Telegram send failed: Bad Request: chat not found` (surfaces `description`); `ok:true` → success. |
| **M-3** NotionService raises on missing key | ✅ FIXED | service.py:24-30 / 36-40 wrap `get_token` in try/except ValueError. Construction with no key → `api_key = None`, no raise; `_ensure_api_key()` raises the friendly "Missing Notion API key…" lazily on use. |
| **M-9** `schedule show <prefix>` | ✅ FIXED | scheduler.py:256-260 `get()` resolves exact id, else unique prefix (None if ambiguous). Live: added schedule `22000510-…`, `schedule show 22000510` → full details, exit 0; removed for cleanup. |
| **M-10** Windows daemon unquoted path | ✅ FIXED | daemon.py:371-374 quotes tokens containing a space. With argv forced to include `C:\Program Files\Python313\python.exe`, the `schtasks /tr` value = `"C:\Program Files\Python313\python.exe" -m tokoboros gateway …` (spaced path quoted, other tokens not). |

## Regressions or new issues

1. **H-6 over-correction (new defect introduced by the fix).** Adding `\b` to `BLOCK_PATTERN` (parser.py:303)
   fixed the `::assertions → ::assertion + leftover "s"` corruption, but made the regex require the
   **singular** `::assertion` exactly. Skills authored with the **plural** `::assertions` (17 of the 22
   shipped skills that declare assertions, including `core_currency`, `wiki`, `github`, all Slack/Notion/
   general skills) now have their assertion block matched by nothing and **silently dropped**.
   - Proof: `parse_og_file("…/currency.og").nodes[0].assertions == []`, and `skill assert core_currency
     --param base=USD --param target=USD` returns `{"ok": true, "results": []}` — the `base != target`
     rule is never evaluated. Old regex captured `::assertion` (+ corrupt `s`); new regex returns `None`.
   - Suggested follow-up: accept the plural form, e.g. `…|assertions?|…` (or normalize the keyword), so
     both `::assertion` and `::assertions` parse into the `assertions` list. This restores assertion
     content for the majority of shipped skills without reintroducing the `"s"` corruption.

2. **No other regressions observed.** Full pytest suite is green (1300 passed / 13 skipped). No `src/` or
   `tests/` files were modified during this pass (only this report was added; a pre-existing, unrelated
   working-tree edit to `docs/qa/google.md` from a separate Google-bug re-verification was left untouched).

### Notes / caveats
- **H-8** is verified code-side and via a live 200 chat turn, but the *original* "OpenAI key handed to
  Anthropic" only manifests when `settings.toml`/config sets `api_key_ref`/`api_key_env` to an OpenAI
  ref. The active config (`~/.tokoboros/tokoboros.json`) carries no provider block, so it already
  defaulted to `ANTHROPIC_API_KEY`; the committed change is the durable guard. No live `settings.toml`
  correction was found to be needed.
- **C-2 / H-1** Unicode rendering: the `—` shown as `�` in some captured console lines is a terminal
  code-page artifact of cp437, not a defect — the underlying strings are correct (and that is exactly
  what H-1's reconfigure handles gracefully).
- Live-send budget respected: 1 Slack send was mocked (no real send), 1 desktop balloon (C-2),
  0 Telegram sends (simulated), Notion artifacts created were archived.
