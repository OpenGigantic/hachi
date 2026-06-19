# UAT Leakage Report — Secret/PII into Event Stream, SSE, Transcript, Web Companion

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

**UAT Agent:** UAT VERIFICATION agent
**Commit under test:** `036dfd9` (Enhance Gmail Integration and TUI Components)
**Date:** 2026-06-14
**Scenarios:** ADV-009, ADV-022, UC-AUTH-004
**Meta-risks covered:** MR-3 (event-stream leakage), MR-10 (memory at-rest encryption)

---

## ADV-009 — CONFIRMED (Critical)

> **Top finding: `redact_outbound` is never called on `tool.call.started` or `tool.call.completed` events. A fake `sk-ant-…` key passed as a tool arg or returned by a tool lands UNREDACTED in the event store, SSE stream, `/sessions/{id}/events`, and the TUI transcript. The preview cap was raised 500→2000 in commit `036dfd9` without adding redaction — the attack surface grew.**

### Claim

MR-3 asserts that secrets entered as tool arguments or returned as tool results are not redacted before entering the `tool.call.started` (raw `input`) or `tool.call.completed` (`result_preview`) runtime events, and therefore leak into every downstream surface.

### Evidence

**`src/hachi/core/runtime_events.py:59` — cap confirmed at 2000:**
```python
TOOL_RESULT_PREVIEW_MAX = 2000   # raised from 500 in commit 036dfd9, no redaction added
```

**`src/hachi/core/runtime_events.py:156-170` — `tool.call.started`, raw `input` emitted:**
```python
if event_type == "tool_call":
    tool_name = payload.get("name", "unknown")
    tool_input = payload.get("input", {})
    return [
        RuntimeEventEnvelope(
            _event(
                TOOL_CALL_STARTED,
                {
                    "name": tool_name,
                    "input": tool_input,   # <-- RAW, no redaction
                },
            ),
            ...
        )
    ]
```

**`src/hachi/core/runtime_events.py:172-186` — `tool.call.completed`, raw `result_preview`:**
```python
if event_type == "tool_result":
    tool_name = payload.get("name", "unknown")
    raw_result = str(payload.get("result", ""))
    return [
        RuntimeEventEnvelope(
            _event(
                TOOL_CALL_COMPLETED,
                {
                    "name": tool_name,
                    "result_preview": raw_result[:TOOL_RESULT_PREVIEW_MAX],  # <-- RAW, no redaction
                },
            ),
            ...
        )
    ]
```

**`src/hachi/core/loop.py:630` — `tool_call` event emitted with raw tool input:**
```python
self.on_event("tool_call", {"name": tool_call.name, "input": tool_call.input})
```

**`src/hachi/core/loop.py:683-688` — `tool_result` event emitted with raw result:**
```python
self.on_event(
    "tool_result",
    {
        "name": tool_call.name,
        "result": result_summary[:TOOL_RESULT_PREVIEW_MAX],
    },
)
```

**`redact_outbound` call sites (grep of entire `src/`):**
- `src/hachi/cli/commands/schedule.py:235-237` — webhook delivery only
- `src/hachi/integrations/slack/chat.py:9,16` — Slack outbound only
- `src/hachi/tools/messaging_tools.py:23,51` — Slack/Discord send only
- `src/hachi/core/secret_scanner.py:81` — definition

`redact_outbound` is **not imported or called** anywhere in `core/loop.py`, `core/runtime_events.py`, `gateway/fastapi_app.py`, or `core/runtime_read_models.py`.

**`src/hachi/gateway/orchestrator.py:1176-1184` — relay_event pipeline (no redaction injected):**
```python
def relay_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    envelopes = adapt_event(event_type, payload)
    for envelope in envelopes:
        event = envelope.primary
        read_models.append_event(event)   # stored unredacted
        if on_event:
            on_event(event.name, event.payload)   # forwarded unredacted to TUI/SSE
```

**`src/hachi/gateway/fastapi_app.py:373-381` — SSE stream emits event payload verbatim:**
```python
for event in events:
    cursor = event["sequence"]
    yield (
        f"id: {event['sequence']}\n"
        f"event: {event['event_type']}\n"
        f"data: {json.dumps(event)}\n\n"   # <-- payload not scrubbed
    )
```

**Offline repro (run against live working tree):**
```
PYTHONPATH=src python -c "
from hachi.core.runtime_events import adapt_event
secret = 'sk-ant-api03-' + 'A' * 40
ev1 = adapt_event('tool_call', {'name': 'run_terminal_command', 'input': {'command': f'echo {secret}'}})
print('in tool.call.started input:', secret in str(ev1[0].primary.payload))
# Output: in tool.call.started input: True

ev2 = adapt_event('tool_result', {'name': 'run_terminal_command', 'result': f'output: {secret}'})
print('in tool.call.completed result_preview:', secret in ev2[0].primary.payload['result_preview'])
# Output: in tool.call.completed result_preview: True
"
```
**Result (confirmed):** `True` on both vectors. Key appears verbatim in both event payloads.

### Finding

A fake `sk-ant-api03-AAA...` token inserted as a tool argument or returned by a tool result is present **unredacted** in:
1. The `tool.call.started` event payload (`input` field)
2. The `tool.call.completed` event payload (`result_preview` field, up to 2000 chars)
3. The in-memory `RuntimeReadModelStore` event log (via `append_event`)
4. The HTTP polling endpoint `GET /sessions/{id}/events` (reads from the store)
5. The SSE stream `GET /sessions/{id}/stream` (serialises events via `json.dumps`)
6. The TUI transcript (collapsible tool cards receive the same `result_preview`)
7. The SQLite `messages` table (`session_store.log_message` persists the full assistant response, which includes tool call content via the `loop.run()` return value path)

`redact_outbound` is exclusively wired to outbound messaging (Slack, Discord, scheduled webhook delivery). It has no presence in the event pipeline.

**Exit criterion #6 check:** No test asserts that `tool.call.started` or `tool.call.completed` event payloads are free of secrets. `tests/test_runtime_events.py` only checks that the event *name* is correct — it passes a clean `result: "ok"` and does not probe for credential scrubbing. There is zero regression coverage for event-stream redaction.

### Severity

**Critical** — secrets placed in tool args (e.g. `set_config provider.api_key sk-ant-…`) or returned by tools (e.g. `read_file` on a `.env`) are durably logged and exposed over the unauthenticated (by default — MR-2) gateway HTTP+SSE surface.

### Regression test?

**Yes — needed.** Suggested name: `test_event_stream_redacts_secrets`. Should assert that calling `adapt_event("tool_call", {"name": "t", "input": {"k": "sk-ant-api03-" + "A"*40}})` does NOT produce an envelope whose `primary.payload` contains the raw key; and same for `adapt_event("tool_result", ...)`.

---

## ADV-022 — PARTIAL (High)

> **Top finding: The web companion event feed renders full unredacted `event.payload` via `JSON.stringify`; the memory browser renders vault content verbatim. No frontend-side scrubbing exists. The approvals inbox is lower-risk (shows only tool name + rationale). Full confirmation requires a live gateway — code-path analysis is conclusive for the event feed and memory browser.**

### Claim

ADV-022 asks whether the web companion's memory browser, approvals inbox, and event feed render sensitive information unscrubbed.

### Evidence

**`frontend/src/components/screens/logs-screen.tsx:137` — event feed renders full payload:**
```tsx
<pre className="mt-1 overflow-auto text-[11px] text-[#c7dcf7]">
  {JSON.stringify(event.payload, null, 2)}
</pre>
```
The event feed calls `getGatewayEventStreamQuery` which polls `/sessions/{id}/events` and renders every `event.payload` through `JSON.stringify` — no scrubbing, no field-level redaction. Since the upstream payload already contains unredacted secrets (per ADV-009), they flow directly into the rendered HTML.

**`frontend/src/components/screens/memory-browser-screen.tsx:22,34` — vault content rendered verbatim:**
```tsx
const summary = String(entry.summary ?? JSON.stringify(entry, null, 2));
// ...
<p className="text-sm text-muted-foreground leading-relaxed line-clamp-4">{summary}</p>
```
The memory browser calls `getGatewaySubagentReports` (the subagent delegation log endpoint) and renders the `summary` field of each entry as plain text with no scrubbing. If a delegation log entry contains a secret in its summary (e.g. because a sub-agent processed a `.env` file), it displays unscrubbed.

**`frontend/src/components/screens/approvals-inbox-screen.tsx:47-50` — approvals inbox (lower risk):**
```tsx
const tool = String(entry.tool ?? entry.action ?? "unknown action");
const decision = String(entry.decision ?? "unknown");
const rationale = String(entry.rationale ?? "");
```
The approvals inbox renders `tool`, `decision`, and `rationale` from the permission audit log. This is structurally lower risk — the permission audit log records the tool name and user-provided rationale, not tool args or results. However, `rationale` is a free-text field and could contain sensitive content if an agent or user includes it there.

### Finding

**Event feed (confirmed by code):** Renders `event.payload` as raw JSON with no filtering. Since ADV-009 confirms secrets appear unredacted in event payloads, any secret in a tool arg or result will be visible in the web companion event feed to any browser-origin that can reach the gateway (which is unauthenticated by default — MR-2, UC-AUTH-001).

**Memory browser (confirmed by code):** Renders vault `summary` fields verbatim. Leakage depends on what the subagent log contains — if it processed secret material, it will appear here.

**Approvals inbox (lower risk, code-confirmed):** Only renders tool name, decision, timestamp, and free-text rationale. Tool args are not exposed here.

**Live confirmation:** All three components fetch from the live gateway. Scenario is PARTIAL because confirming *actual* secret content in memory/approvals requires a running gateway session — code analysis is sufficient to show no scrubbing is applied to what is fetched.

### Severity

**High** — the event feed + memory browser are direct read surfaces on top of ADV-009's leakage. With the default unauthenticated gateway (MR-2), any local page can retrieve the event stream and observe unredacted secrets.

### Regression test?

**Yes — needed.** The event feed rendering test should assert that `result_preview` containing a known secret pattern does not appear in the rendered output (would require scrubbing at either the gateway event serialisation layer or in the frontend component).

---

## UC-AUTH-004 — NOT-REPRODUCED (Low)

> **Top finding: The gateway exception handler at `fastapi_app.py:241-262` correctly suppresses exception details from the client response for unhandled exceptions (including bad-API-key 401s from the provider). The key does not appear in the `500` JSON body. Server-side logging does log `exc` string representation, which may include the key if the Anthropic SDK embeds it in the exception message — this is a residual server-log risk, not a client-leak.**

### Claim

UC-AUTH-004 asserts that an invalid `ANTHROPIC_API_KEY` fails with a clean error and does not leak the key to the HTTP client or in logged output.

### Evidence

**`src/hachi/gateway/fastapi_app.py:241-262` — catch-all exception handler:**
```python
@app.exception_handler(Exception)
async def _internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # ... logs server-side only —
    # never returned to the client to avoid leaking internal paths or secrets.
    correlation_id = str(uuid.uuid4())
    _logging.getLogger(__name__).error(
        "Internal error [%s]: %s\n%s",
        correlation_id,
        exc,                        # exc.__str__() logged server-side
        _traceback.format_exc(),    # full traceback logged server-side
    )
    return _error_response(
        status_code=500,
        code="internal_error",
        message="An unexpected error occurred.",
        details={"correlation_id": correlation_id},
    )
```

**Client response (static analysis):** `_error_response` at `fastapi_app.py:172-178` constructs a `JSONResponse` from `ErrorResponse(code=..., message=..., details=...)`. The `message` is the hard-coded string `"An unexpected error occurred."` — not `str(exc)`. The correlation ID is a random UUID. No exception fields, no traceback, no key.

**More specific handlers (lines 220-228):** `GatewayProtocolError` and `GatewayUnavailableError` use `message=str(exc)` — if either of those exception types surfaces a message that includes a key string, it would leak. However, these types are for protocol/availability conditions, not for Anthropic API auth failures.

**Server-side logging risk:** `_logging.getLogger(__name__).error("... %s\n%s", exc, traceback)` passes `str(exc)` to the log. If the Anthropic SDK's 401 exception includes the provided API key in `__str__()` (e.g. in a "received key: sk-ant-…" message), the key would appear in server logs. This cannot be confirmed without a live Anthropic SDK call — **BLOCKED-NEEDS-LIVE for the server-log sub-case**.

**No test for UC-AUTH-004:** There is no test in the suite that injects a bad key into the gateway and asserts the HTTP response is key-free.

### Finding

The HTTP client response for a bad-API-key error is clean by construction — the handler never serialises `str(exc)` into the response body. The client receives only `{"code": "internal_error", "message": "An unexpected error occurred.", "details": {"correlation_id": "<uuid>"}}`.

Residual risk: server-side logs receive `str(exc)` and the full traceback. Whether the Anthropic SDK includes the key value in the exception message requires live testing (BLOCKED-NEEDS-LIVE).

No regression test exists asserting that the HTTP response for a bad key is key-free.

### Severity

**Low (client-side confirmed clean)** / **Medium (server-log sub-case, BLOCKED-NEEDS-LIVE)**

### Regression test?

**Yes — useful.** Suggested name: `test_bad_api_key_response_is_clean`. Should mock the LLM to raise an auth error, send a chat request to the test app, and assert the 500 response body contains no `sk-` substring and includes a `correlation_id`.

---

## Summary

| Scenario | Verdict | Severity | Regression test needed |
|----------|---------|----------|------------------------|
| ADV-009 | CONFIRMED | Critical | Yes — `test_event_stream_redacts_secrets` |
| ADV-022 | PARTIAL | High | Yes — frontend event feed scrubbing |
| UC-AUTH-004 | NOT-REPRODUCED (client) / BLOCKED-NEEDS-LIVE (server log) | Low/Medium | Yes — `test_bad_api_key_response_is_clean` |

### Key systemic finding

`redact_outbound` is a point solution for three outbound messaging surfaces (Slack, Discord, scheduled webhooks). It is completely absent from the event pipeline (`runtime_events.py`, `loop.py`, `runtime_read_models.py`, `fastapi_app.py`). Commit `036dfd9` raised `TOOL_RESULT_PREVIEW_MAX` from 500 to 2000 — doubling the maximum exposure per event — without adding redaction. The fix must be applied at `adapt_event()` in `runtime_events.py` (the single chokepoint for both `tool_call` and `tool_result` event types) so every downstream surface (TUI, SSE, REST, SQLite read model, web companion event feed) benefits from one change.
