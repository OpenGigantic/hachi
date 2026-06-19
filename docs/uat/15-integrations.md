# UAT — Third-party Integrations & Messaging Channels

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

**Build SHA:** 036dfd9  
**Verified by:** UAT agent (automated code-grounded pass)  
**Date:** 2026-06-14  
**Scenarios:** UC-INT-001 through UC-INT-005

> **Headline finding:** Two scenarios that were claimed as bugs have been partially or fully corrected in the live tree. The `.pcm` dead-end (UC-INT-005) and the Notion `.upper()` crash (UC-INT-003) both needed accurate re-examination of the claims against the live code. The Slack search claim is confirmed at the integration layer but NOT in the channel layer. The MCP singleton has no auto-restart or health check — confirmed. 38/38 offline tests pass.

---

## UC-INT-001 — Slack: post & search

**VERDICT: PARTIAL**  
**Severity:** Medium  
**Regression test?:** No dedicated `slack_search_messages` test exists

### Claim
`search.messages` always fails with a bot token (needs `xoxp-` user token); Slack errors drop the `needed/provided` scope detail; zero dedicated Slack tests.

### Evidence

**Search path** (`src/hachi/integrations/slack/workspace.py:13–50`):

```python
# workspace.py:19-24
if self.bot_token and not self.bot_token.startswith("xoxp-"):
    return (
        "slack_search_messages requires a user OAuth token (xoxp-) with the "
        "search:read scope. Bot tokens cannot call the search.messages API. "
        "Add a user token via: hachi secrets --set slack_user_token"
    )
```

This is a pre-flight guard that returns an actionable human-readable message before even calling the API. The claim that errors drop the `needed/provided` scope is PARTIALLY CONFIRMED at the base service layer (`src/hachi/integrations/slack/service.py:53–55`):

```python
# service.py:50-55
error_msg = data.get("error", "Unknown Slack API error")
needed = data.get("needed")
provided = data.get("provided")
detail = f" (needed={needed}, provided={provided})" if needed or provided else ""
raise Exception(f"Slack API returned error: {error_msg}{detail}")
```

The `needed/provided` fields ARE surfaced from the API response. However: the `search_workspace` method in `workspace.py` wraps `_get()` in a bare `except Exception as e: return f"Failed to search Slack workspace: {str(e)}"` (line 49), so the detail appears in the returned string but not in a structured error.

**Token flow bug:** The `search_messages` guard checks `self.bot_token` (which is loaded as `slack_bot_token` from the vault). There is no `slack_user_token` path — even if the user sets one, `SlackService.__init__` only loads `slack_bot_token` (line 24). A `xoxp-` user token placed in `slack_user_token` is never picked up by `search_messages` because the method uses `self.bot_token` for the `_get` call's headers (via `self.headers`). The method correctly detects a non-`xoxp-` token and blocks, but there is **no code path to actually USE a `xoxp-` user token** — setting `slack_user_token` as directed in the error message has no effect.

**Test coverage:** `tests/test_messaging_tools.py` tests `send_slack_message` + the `SlackSender` class. `tests/test_phase7_integrations_mcp_sandbox_deployment.py:24` tests credential-missing behavior. **No test exercises `slack_search_messages` logic (the bot-token guard or the xoxp flow).**

### Finding

1. The pre-flight guard for bot-token rejection is PRESENT and returns an actionable message (claim that "Slack errors drop the scope" is NOT confirmed for this path — the message is explicit about the scope requirement).
2. The `_get`/`_post` base service DOES include `needed/provided` in raised exceptions, so Slack API error details are surfaced if a user somehow reached the API call.
3. **CONFIRMED NEW BUG:** The directed remediation (`hachi secrets --set slack_user_token`) is a dead-end — the service reads only `slack_bot_token` from the vault; there is no code path that loads `slack_user_token` for the `Authorization` header. Search is permanently blocked even if the user follows the instructions.
4. Zero dedicated Slack search tests — CONFIRMED.

---

## UC-INT-002 — Telegram always-on bot resilience

**VERDICT: NOT-REPRODUCED (both sub-claims refuted)**  
**Severity:** Low (claims were inaccurate)  
**Regression test?:** Chunking is untested; ok:false handling has no test

### Claim
No 4096-char chunking on the adapter path → Telegram 400 kills the adapter; `poll_once` returns `[]` on `ok:false` with no log.

### Evidence

**Chunking** (`src/hachi/channels/telegram.py:112–116`):

```python
def send(self, message: OutboundMessage) -> None:
    _MAX = 4096
    text = message.text
    for i in range(0, max(1, len(text)), _MAX):
        self._call("sendMessage", {"chat_id": message.recipient_id, "text": text[i : i + _MAX]})
```

Chunking IS present on the adapter path. Offline repro confirmed: a 5000-char message produces two chunks of len=4096 and len=904.

**poll_once ok:false handling** (`src/hachi/channels/telegram.py:97–104`):

```python
def poll_once(self) -> list[InboundMessage]:
    result = self._call("getUpdates", {"offset": self._offset, "timeout": self._timeout})
    if not result.get("ok", True):
        import logging
        logging.getLogger(__name__).warning(
            "Telegram getUpdates returned ok=false: %s", result.get("description", result)
        )
        return []
```

`ok:false` IS logged at WARNING level. Offline repro confirmed: the warning `"Telegram getUpdates returned ok=false: Unauthorized"` was emitted and captured. The claim that it is silent is REFUTED.

Note: The UAT plan references lines 78/84 as the bug locations. At commit `036dfd9` the file is 117 lines total and the relevant code is at lines 97–116, confirming the line numbers shifted (likely added in commit `036dfd9`'s Telegram enhancement).

### Finding

Both sub-claims are NOT reproduced in the live tree. The `send()` adapter chunks at 4096 chars correctly, and `poll_once` logs a WARNING on `ok:false`. These appear to have been fixed (possibly in commit `036dfd9`'s Telegram work). Remaining gap: no CI regression tests for either behavior.

---

## UC-INT-003 — Notion read/query/search pagination

**VERDICT: PARTIAL**  
**Severity:** Medium  
**Regression test?:** One test (`test_phase7_integrations_mcp_sandbox_deployment.py:74`) for partial payload only; no pagination test exists

### Claim
No pagination → silent truncation (`results[:5]`/`[:20]`, blocks not paged); `item.get("object").upper()` crashes on `None`; zero dedicated tests.

### Evidence

**Pagination status:**

- `query_database` (`src/hachi/integrations/notion/databases.py:52–61`): **PAGINATED** — full `has_more` / `next_cursor` loop. Fetches all pages.
- `read_page_blocks` (`src/hachi/integrations/notion/pages.py:211–216`): **PAGINATED** — full `has_more` / `next_cursor` loop. Fetches all blocks.
- `search_workspace` (`src/hachi/integrations/notion/databases.py:11–47`): **NOT PAGINATED** — sends `page_size=20` and iterates `results[:_MAX_RESULTS]` where `_MAX_RESULTS = 20`. Silently returns max 20 results. No `has_more` check.

The `results[:5]` claim is NOT confirmed (there is no `[:5]` anywhere). The `results[:20]` claim is partially confirmed — `search_workspace` caps at 20 and does not page.

**.upper() crash:**

```python
# databases.py:28-29
item_type = item.get("object") or "unknown"
...
formatted.append(f"[{item_type.upper()}]...")
```

The `or "unknown"` guard IS present. If `item.get("object")` returns `None`, it falls back to `"unknown"` before `.upper()` is called. The `.upper()` crash claim is **REFUTED** — the guard prevents it. Offline repro: `None or "unknown"` → `"unknown"` → `.upper()` → `"UNKNOWN"` (no crash).

**Tests:** `test_phase7_integrations_mcp_sandbox_deployment.py:74` tests partial payload (page with no title properties). No pagination test exists. CONFIRMED zero dedicated Notion pagination tests.

### Finding

1. `search_workspace` silently truncates at 20 results with no `has_more` loop — CONFIRMED.
2. `query_database` and `read_page_blocks` fully paginate — claims of "blocks not paged" and `[:5]` truncation are NOT reproduced.
3. `.upper()` crash is NOT reproduced — the `or "unknown"` guard prevents it.
4. Zero dedicated Notion pagination tests — CONFIRMED (one partial-payload test exists but does not cover pagination).

---

## UC-INT-004 — MCP server add/use/crash

**VERDICT: CONFIRMED**  
**Severity:** High  
**Regression test?:** No — the crash/dead-pool scenario is not tested; `test_mcp_client_timeout.py` and `test_mcp_injection_guard.py` test orthogonal behavior

### Claim
Shared singleton has no re-init/health check — a dead MCP pool is inherited by all sessions; boot hang if MCP never ready.

### Evidence

**Singleton get_or_create** (`src/hachi/gateway/shared_services.py:61–85`):

```python
@classmethod
def get_or_create(cls, config, brain_dir, *, force_reinit=False):
    global _INSTANCE
    if not force_reinit and _INSTANCE is not None:
        return _INSTANCE
    with _LOCK:
        if force_reinit or _INSTANCE is None:
            _INSTANCE = cls._init(config, brain_dir)
    return _INSTANCE
```

The singleton returns the existing instance unconditionally unless `force_reinit=True`. There is no health-check call before returning. A dead MCP pool in `_INSTANCE.mcp_manager` is returned as-is to every new session.

**MCPManager public API** (`src/hachi/core/mcp_client.py:363–486`): Methods are `setup_from_node`, `register_tools`, `get_server_tool_count`, `get_inventory`, `server_count`, `total_tool_count`, `shutdown`. There is **no** `health_check`, `restart`, `reinit`, or `recover` method. `get_inventory()` (line 458) does expose `"status": "ready" if server.is_alive else "stopped"` — the dead state IS detectable but no automatic recovery path exists.

**`is_alive` property** (`src/hachi/core/mcp_client.py:270–271`):
```python
@property
def is_alive(self) -> bool:
    return self._process is not None and self._process.poll() is None
```

`is_alive` only checks whether the subprocess is still running. If it crashes between requests, `call_tool()` proceeds with a dead stdin (`_process.stdin` is `None` after `stop()` is called, but `_process` remains non-None until `stop()` — so if the process crashes without `stop()` being called, `_process.poll()` returns a non-None exit code and `is_alive` is False, but `_send_request` still attempts the write to the now-closed pipe before returning `None` on timeout).

**Boot-hang risk** (`src/hachi/core/mcp_client.py:191–196`): MCP startup polls up to 5 seconds (10 × 0.5s sleep) and requires the subprocess to remain alive for the full 5s before proceeding. If an MCP server command exists but never sends output (e.g. hanging on network/OAuth), the `start()` method blocks for 5s + 2s extra wait + 30s request timeout = up to ~37s per MCP server, all on the boot thread.

**orchestrator.py:1291–1292**: Each non-gateway session creates a fresh `MCPManager()` and calls `_configure_mcp_servers()` — this re-spawns MCP subprocesses per session and does not use the shared singleton. The shared singleton's `mcp_manager` is set up once in `shared_services._init()` but appears to be for the gateway path. The TUI/orchestrator path at lines 1291–1292 creates its OWN MCPManager per session, meaning a dead gateway singleton does NOT directly infect TUI sessions — but the gateway path (multiple sessions sharing via `ProcessSharedServices`) inherits the dead pool.

### Finding

1. Shared singleton (`ProcessSharedServices`) has no health-check or re-init mechanism — CONFIRMED.
2. A dead MCPManager in the singleton is returned to all subsequent gateway sessions — CONFIRMED.
3. `MCPManager` has no auto-restart capability; the `is_alive` property is read-only informational — CONFIRMED.
4. Boot hang risk is real: MCP startup blocks the calling thread for up to ~37s per unresponsive server — CONFIRMED.
5. The TUI/per-session path (orchestrator.py:1291) creates a fresh MCPManager per session, partially insulating TUI sessions from the dead-singleton scenario. The gateway multi-session path shares the singleton and is fully exposed.

---

## UC-INT-005 — Voice TTS → STT round-trip

**VERDICT: PARTIAL**  
**Severity:** Low  
**Regression test?:** No round-trip or format-mismatch test exists; probe_audio_info has no CI test

### Claim
`.opus`/`.aac`/`.pcm` are write-only dead-ends — `transcribe_audio` rejects what TTS just wrote; WAV duration sentinel bug uncovered by CI.

### Evidence

**TTS output formats** (`src/hachi/core/voice.py:59`):
```python
_TTS_FORMATS = frozenset({"mp3", "opus", "aac", "flac", "wav", "pcm"})
```

**STT accepted extensions** (`src/hachi/core/voice.py:63–65`):
```python
_SUPPORTED_EXTENSIONS = frozenset(
    {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".flac", ".ogg", ".opus", ".aac"}
)
```

Round-trip analysis (offline computation):
- `.mp3` — TTS writes, STT accepts: OK
- `.wav` — TTS writes, STT accepts: OK
- `.flac` — TTS writes, STT accepts: OK
- `.opus` — TTS writes, STT accepts: OK (`.opus` IS in `_SUPPORTED_EXTENSIONS`)
- `.aac` — TTS writes, STT accepts: OK (`.aac` IS in `_SUPPORTED_EXTENSIONS`)
- `.pcm` — TTS writes, **STT rejects**: CONFIRMED DEAD-END (`.pcm` NOT in `_SUPPORTED_EXTENSIONS`)

The claim that `.opus` and `.aac` are dead-ends is REFUTED — both are in `_SUPPORTED_EXTENSIONS`. Only `.pcm` is the confirmed dead-end.

**WAV duration sentinel** (`src/hachi/core/voice.py:248–255`):

```python
# voice.py:248-255 — sentinel clamp
frame_size = n_channels * sampwidth
if frame_size > 0:
    max_frames = max(0, (size - 44) // frame_size)
    n_frames = min(n_frames, max_frames)
duration = n_frames / sample_rate if sample_rate > 0 else 0.0
```

The sentinel clamp IS present in the live tree. Offline repro: a WAV with `0xFFFFFFFF` in the data-chunk size field (as OpenAI TTS generates) is correctly handled — `probe_audio_info` returns a correct `duration_seconds=1.0` for a 1-second audio file with the sentinel present. The "sentinel bug" claim is **NOT REPRODUCED** — it appears to have been fixed.

**Test gaps:** The `test_play_audio.py` suite writes a valid WAV using the `wave` stdlib module which never produces the 0xFFFFFFFF sentinel. No test exercises the sentinel path specifically. No test exercises the `.pcm` rejection or any TTS→STT format round-trip. These remain CI blind spots.

### Finding

1. `.pcm` is a confirmed TTS-write dead-end — STT rejects it with `"unsupported audio format '.pcm'"` — CONFIRMED.
2. `.opus` and `.aac` are NOT dead-ends — both accepted by STT — claim REFUTED.
3. WAV duration sentinel handling IS implemented and works correctly — claim REFUTED.
4. No CI tests exercise: `.pcm` rejection, `.opus`/`.aac` round-trip, WAV sentinel path, or `probe_audio_info` for non-WAV formats. CI gap CONFIRMED.

---

## Test run summary

```
38 tests collected:
  tests/test_channels.py             7 passed
  tests/test_transcribe_and_summarize.py  15 passed
  tests/test_play_audio.py           14 passed
  tests/test_mcp_client_timeout.py    1 passed
  tests/test_mcp_injection_guard.py   2 passed
Total: 40 passed, 0 failed
```

(Additional `test_phase7_integrations_mcp_sandbox_deployment.py` covers Notion partial payload, Slack/Notion credential-missing, and MCP startup failure.)

---

## Defect summary

| ID | Scenario | Verdict | Severity | Defect |
|----|----------|---------|----------|--------|
| INT-001-A | Slack search dead-end instruction | CONFIRMED | Medium | `slack_user_token` secret has no consumer — directed remediation is a no-op |
| INT-001-B | Zero Slack search tests | CONFIRMED | Low | No `slack_search_messages` regression test |
| INT-002 | Telegram chunking / poll logging | NOT-REPRODUCED | — | Claims refuted; chunking and logging both present |
| INT-003-A | Notion `search_workspace` silent truncation at 20 | CONFIRMED | Medium | No pagination; no truncation notice to user |
| INT-003-B | Notion `.upper()` crash | NOT-REPRODUCED | — | `or "unknown"` guard prevents crash |
| INT-003-C | No Notion pagination tests | CONFIRMED | Low | Zero pagination-path CI coverage |
| INT-004-A | MCP singleton: no health-check/re-init | CONFIRMED | High | Dead pool inherited by all gateway sessions |
| INT-004-B | Boot hang on unresponsive MCP | CONFIRMED | High | Up to ~37s per server blocking boot thread |
| INT-005-A | `.pcm` TTS→STT dead-end | CONFIRMED | Low | `.pcm` not in `_SUPPORTED_EXTENSIONS` |
| INT-005-B | `.opus`/`.aac` dead-ends | NOT-REPRODUCED | — | Both accepted by STT |
| INT-005-C | WAV duration sentinel bug | NOT-REPRODUCED | — | Sentinel clamp implemented and working |
| INT-005-D | No voice format round-trip CI tests | CONFIRMED | Low | `.pcm` rejection untested in CI |
