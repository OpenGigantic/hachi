# QA Report — Telegram Channel

Date: 2026-06-11
Tester: QA engineer (automated)
Env: Windows 11, Python 3.14.3, `./.venv/Scripts/python.exe`, `PYTHONPATH=src`, tier=unrestricted
Scope files:
- `src/tokoboros/channels/telegram.py`
- `src/tokoboros/channels/base.py`
- `src/tokoboros/tools/messaging_tools.py`
- `channel telegram` CLI in `src/tokoboros/cli/main.py` (~line 2335)
- Supporting: `src/tokoboros/channels/router.py` (allowlist filter)

## Summary

| Area | Result |
|------|--------|
| LIVE send ("Tokoboros QA test ✅" → chat 1674072270) | **FAIL — but environmental, not a code bug.** Telegram returned `HTTP 400 Bad Request: chat not found`. The user has never `/start`'d the bot (`getChat` 400 + `getUpdates` 0 pending), so the chat does not exist from `@toko67bot`'s view. The tool handled the failure gracefully. |
| `parse_updates` parser (text / edited / photo / sticker / `/command` / missing fields) | **PASS** — no crashes, correct extraction, non-text correctly skipped. |
| Allowlist filter (`--allow` → `ChannelRouter.is_allowed`/`dispatch`) | **PASS** — allowed vs disallowed ids handled correctly; consistent with `parse_updates` sender_id. |
| Error handling — empty/missing token | **PASS** — `ValueError` (adapter) / friendly message (tool). |
| Error handling — missing chat_id | **PASS** — friendly message. |
| Error handling — over-long (>4096) | **PARTIAL** — not chunked anywhere; tool catches the 400, but the **adapter path used by the live CLI bot does not** → crashes the bot loop. |
| Error handling — markdown/HTML special chars | **PASS** — no `parse_mode` set, text sent verbatim; safe, no formatting injection. |

Severity tallies: **High 1, Medium 2, Low 2, Nit 1** (6 findings).

Caveats:
- Per task rules, only **1** live Telegram send was performed. A follow-up diagnostic that would have made 4 more live sends was correctly blocked; root cause was instead confirmed with **read-only** `getMe` / `getChat` / `getUpdates` calls (these deliver nothing to the chat).
- Findings tagged **(live-verified)** were observed against the real Telegram API; **(code-reading)** were reproduced offline with mocks/inspection but not exercised against the live API to avoid extra sends.

---

## Findings

### [High] CLI Telegram bot loop has no per-iteration error handling — a single send/poll failure kills the whole bot
**Where:** `src/tokoboros/cli/main.py:2364-2375` (loop), with `src/tokoboros/channels/telegram.py:78-85` (`poll_once`/`send` propagate exceptions)
**Tag:** code-reading (offline-reproduced)

**Repro (offline):**
```python
adapter = TelegramAdapter("fake:token")
with patch("urllib.request.urlopen", side_effect=HTTPError("url",400,"Bad Request",{},io.BytesIO(b'...'))):
    adapter.send(OutboundMessage("telegram","1674072270","hi"))   # raises
    adapter.poll_once()                                           # raises
```
Output:
```
adapter.send: PROPAGATED HTTPError 400 -> would crash CLI poll loop
adapter.poll_once: PROPAGATED HTTPError 400 -> would crash CLI poll loop
channel_telegram wraps adapter.send in its own try/except inside the loop?: False
```

The CLI loop is:
```python
try:
    while True:
        for inbound in adapter.poll_once():
            ...
            adapter.send(router.dispatch(inbound))   # not guarded
        time.sleep(poll_interval)
except KeyboardInterrupt:
    ...
```
The only `try` guards `KeyboardInterrupt`. Any exception from `poll_once()` or `send()` — a transient network blip, a Telegram **409 Conflict** (two pollers), a reply that exceeds 4096 chars, a user who blocked the bot, a 429 rate-limit — propagates out of the loop and terminates the bot (then `finally: runner.shutdown()`).

**Expected:** A single bad message/poll should be logged and skipped; the bot stays up. (The sibling `channel whatsapp` command **does** wrap `adapter.send` in `try/except` at `main.py:2409-2412`, so this is also an internal inconsistency.)

**Actual:** Whole bot dies on the first transient error.

**Impact:** Availability. An "always-on" Telegram bot is one network hiccup, rate-limit, or over-long agent reply away from silently going offline.

**Suggested fix:** Wrap the inner body in `try/except Exception` (log + continue), mirroring the WhatsApp dispatcher. Optionally also catch around `poll_once()` so a transient getUpdates failure backs off instead of exiting.

---

### [Medium] `send_telegram_message` reports success when Telegram returns `{"ok": false}` (HTTP 200)
**Where:** `src/tokoboros/tools/messaging_tools.py:196-203` (return value of `fn(...)` is discarded)
**Tag:** code-reading (offline-reproduced)

**Repro (offline):**
```python
def fake(token, chat_id, text): return {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
send = _make_send_telegram(fake)
os.environ["TELEGRAM_BOT_TOKEN"]="x"; os.environ["TELEGRAM_CHAT_ID"]="1674072270"
print(send("hi"))
```
Output:
```
'Sent Telegram message to chat 1674072270.'
```

The tool only catches `urllib.error.HTTPError`. Telegram returns **HTTP 200 with `{"ok": false, ...}`** for a class of errors (e.g. `409 Conflict`, `migrate_to_chat_id`, some validation responses). Because `fn(token, target, text)` is called but its return dict is never inspected, those map to a false "Sent" confirmation reported back to the agent/user.

**Expected:** When the response body has `ok == false`, surface `description` as a failure (e.g. `Telegram send failed: <description>`).

**Actual:** Reports `Sent Telegram message to chat <id>.` despite the message not being delivered.

**Impact:** Silent message loss. The agent believes a notification/summary was delivered when it was not — misleading for proactive alerts/reminders.

**Suggested fix:** Capture `result = fn(...)`; if `not result.get("ok", True)`, return `f"Telegram send failed: {result.get('description', 'unknown error')}"`.

---

### [Medium] Over-long messages (>4096 chars) are never chunked; only the *tool* path catches the resulting 400
**Where:** `src/tokoboros/channels/telegram.py:84-85` (`TelegramAdapter.send`) and `src/tokoboros/tools/messaging_tools.py:32-38` (`_telegram_send`)
**Tag:** code-reading (offline-reproduced) for chunking + adapter crash; tool-side 400 handling live-confirmed indirectly (real chat returned HTTP 400 and the tool produced a friendly string)

**Repro (offline inspection):**
```
TelegramAdapter.send mentions chunk/split/4096?: False
_telegram_send mentions chunk/split/4096?: False
```
A 5000-char message is sent as a single `sendMessage`. Telegram's hard limit is 4096 chars → API responds `400 Bad Request: message is too long`.

- **Tool path** (`send_telegram_message`): the 400 is caught by the `HTTPError` handler → `Telegram send failed: HTTP 400 — Bad Request` (acceptable degradation).
- **Adapter path** (the live CLI bot): `send` does **not** catch it (see High finding) → the bot crashes when an agent reply exceeds 4096 chars. This is a realistic trigger because agent replies are often long.

**Expected:** Long text is split into ≤4096-char chunks (Telegram clients/bots normally chunk), or at minimum the adapter degrades gracefully.

**Actual:** No chunking; adapter raises and kills the bot loop; tool reports a generic 400.

**Suggested fix:** Add a chunker (split on 4096-char boundaries, ideally on newlines) in `_telegram_send`/`TelegramAdapter.send`, and/or guard the CLI loop (High finding).

---

### [Low] Friendly HTTP-error message throws away Telegram's `description`, hiding the real cause
**Where:** `src/tokoboros/tools/messaging_tools.py:199-200`
**Tag:** live-verified

**Repro (live, read-only confirmation of the same 400):**
```
[getChat {'chat_id': '1674072270'}] HTTPError 400 Bad Request:
  {"ok":false,"error_code":400,"description":"Bad Request: chat not found"}
```
The live send produced:
```
'Telegram send failed: HTTP 400 — Bad Request'
```
The handler uses `exc.code` and `exc.reason` but never reads `exc.read()`, so the actionable `description` ("chat not found") is dropped. The user sees only "Bad Request" and cannot tell *why* (wrong chat id? user never `/start`'d? bot blocked?).

**Expected:** Include the body description, e.g. `Telegram send failed: 400 — Bad Request: chat not found`.

**Actual:** Generic `HTTP 400 — Bad Request`.

**Impact:** Poor diagnosability of the single most common Telegram failure mode (this exact case is what blocked the live send in this very test run).

**Suggested fix:** In the `HTTPError` branch, attempt `desc = json.loads(exc.read()).get("description")` and append it when present.

---

### [Low] `poll_once` silently returns `[]` on an API-level `ok:false` (e.g. 409 Conflict), with no warning
**Where:** `src/tokoboros/channels/telegram.py:78-82`
**Tag:** code-reading (offline-reproduced)

**Repro (offline):** mock `urlopen` to return `{"ok":false,"error_code":409,"description":"Conflict: terminated by other getUpdates request"}`:
```
poll_once on ok:false(409): returned [] (silently empty — error hidden)
```
`poll_once` does `result.get("result", []) or []` and never checks `result.get("ok")`. A `409 Conflict` (a second poller / leftover webhook) makes the bot appear alive while receiving nothing, with no log line.

**Expected:** Log a warning when `ok` is false so the operator can see the bot is wedged.

**Actual:** Looks healthy, receives nothing, no signal.

**Impact:** Hard-to-diagnose "bot is up but deaf" state.

**Suggested fix:** `if not result.get("ok", True): logger.warning("getUpdates failed: %s", result.get("description"))`.

---

### [Nit] `parse_updates` drops photo/document **captions** and uses `chat.id` (not `from.id`) as `sender_id`
**Where:** `src/tokoboros/channels/telegram.py:33-44`
**Tag:** code-reading (offline-verified, by design)

**Observed (offline):** a photo update with `"caption": "a photo caption"` and a sticker are both skipped (only `text` is read), so caption-only messages are invisible to the agent. Output from the representative-JSON run:
```
Parsed 5 messages (no crash):
  sender_id='555' text='hello bot'      sender_name='ada'
  sender_id='555' text='edited text'    sender_name='Ada'   # edited_message handled
  sender_id='777' text='/start now'     sender_name='Bob'   # /command handled
  sender_id=''    text='no chat field'  sender_name=''       # missing chat -> empty sender_id
  sender_id='888' text='no from field'  sender_name=''
# photo + sticker correctly skipped; channel_post / callback_query ignored
```
Two minor notes (not bugs, behavior is documented as text-only and "chat ids"):
1. Caption-only media messages are silently ignored. Fine for a text-only contract, but a user sending "summarize this 📷 + caption" gets no response.
2. `sender_id = chat.id`. In **private** chats `chat.id == user.id`, so the `--allow` chat-id allowlist works as intended (verified: a parsed message with `chat.id=1674072270` is allowed by an allowlist of `1674072270`). In **group** chats `chat.id` is the group id, so the allowlist gates the whole group, not individuals — acceptable but worth documenting. Also, a message missing `chat` yields `sender_id=""` yet is still dispatched (empty-sender turn).

**Suggested fix (optional):** Fall back to `caption` when `text` is absent; document the group-vs-private allowlist semantics.

---

## What passed

- **Token validation (adapter):** `TelegramAdapter("")` and `TelegramAdapter(None)` both raise `ValueError("Telegram bot token is required")` (`telegram.py:62`). *(offline)*
- **Tool config guards:** `send_telegram_message` returns a clear "Telegram is not configured…" with no token, and "No Telegram chat_id provided…" with a token but no chat. *(offline)*
- **`parse_updates` robustness:** text, edited_message (with text), and `/command` extracted correctly; photo, sticker, `channel_post`, and `callback_query` skipped; `None`/non-dict/`text=None`/int-text payloads handled without crashing. *(offline)*
- **`latest_offset`:** advances past the max `update_id` (+1), leaves the offset unchanged on an empty batch, and falls back to `current` on a missing `update_id`. *(offline)*
- **Allowlist filter:** `ChannelRouter.is_allowed` and `dispatch` correctly allow listed ids (`1674072270`, `42`) and block others (`999` → "Sorry — you're not authorized to use this assistant."); int sender ids are normalized to str and match. The allowlist key matches the `sender_id` that `parse_updates` produces. *(offline)*
- **Special-character safety:** no `parse_mode` is set on either send path, so Markdown/HTML metacharacters (`_ * [ ] ( ) ~ \` > # + - = | { } . !`, `<b>`, `&`) are transmitted verbatim with no escaping needed and no risk of a Markdown-parse 400 or formatting injection. *(offline)*
- **Token liveness:** `getMe` returns HTTP 200 for `@toko67bot` (`id 8823662070`), confirming the configured `TELEGRAM_BOT_TOKEN` is valid — so the live-send 400 is purely the missing-chat precondition, not a credential/encoding problem. *(live-verified, read-only)*
- **Existing suite:** `tests/test_channels.py` — 7 passed.
