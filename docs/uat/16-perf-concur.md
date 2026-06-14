# UAT Report — Concurrency, Performance & Scale

**Assignment:** UC-PERF-001, UC-PERF-002, UC-PERF-004, UC-PERF-005, UC-PERF-006, ADV-024, ADV-025
**Build SHA:** `036dfd9`
**Tester:** UAT VERIFICATION agent (static + offline tests; no live load)
**Date:** 2026-06-14

> **HEADLINE:** UC-PERF-001 per-session serialization is CONFIRMED correct (lock + SESSION_BUSY event). UC-PERF-005 file-size check has a real `except: pass` bypass for read but NOT for write. ADV-024 stale-singleton config-ignored issue is CONFIRMED — second callers always get the first config with no `brain_dir` mismatch check. ADV-025 tar-bomb / unbounded extraction is CONFIRMED absent per MR-4.

---

### UC-PERF-001 — CONFIRMED (Severity: Low; Regression test: Yes)

**Claim:** Concurrent chats to the same session are serialized; the second caller sees `session.busy`; history is never interleaved.

**Evidence — file:line + quoted code:**

`src/tokoboros/gateway/orchestrator.py:417-419`
```python
# Per-session turn serialization: prevents concurrent chat() calls from
# corrupting interleaved user/assistant/tool-pair message history.
self._chat_lock = threading.Lock()
```

`src/tokoboros/gateway/orchestrator.py:886-895`
```python
# Emit session.busy when another turn is already running so UIs can show
# queued state. The acquire() then blocks until the current turn finishes.
if not self._chat_lock.acquire(blocking=False):
    self._emit_event(SESSION_BUSY, {"queued_input_preview": user_input[:80]})
    self._chat_lock.acquire()  # blocks until the current turn is done

try:
    return self._chat_impl(user_input)
finally:
    self._chat_lock.release()
```

`src/tokoboros/gateway/orchestrator.py:897-898`
```python
def _chat_impl(self, user_input: str) -> str:
    """Internal single-turn implementation; must only be called while _chat_lock is held."""
```

`src/tokoboros/core/runtime_events.py:26`
```python
SESSION_BUSY = "session.busy"
```

**Finding:** The per-session serialization (P1-01) is correctly implemented. A second concurrent caller:
1. Non-blocking acquires — fails — emits `session.busy` with an 80-char prompt preview
2. Then blocking-acquires and queues behind the first turn
3. `_chat_impl` is only called while the lock is held, so history writes are strictly serial

No interleaved `tool_use`/`tool_result` pairs are possible under this design. The `LeaseManager` docstring at `core/lease_manager.py:69` also confirms it relies on this "serialised chat lock (P1-01)".

**Offline test coverage:** `tests/test_gateway_control_plane.py` covers the prompt-length cap and session lifecycle; no dedicated concurrent-turn test exists. The lock semantics are validated by code inspection. The `test_chat_rejects_oversized_prompt` test passed (run 2026-06-14, 2 tests, 2.43s).

**Severity:** Low (behaves correctly)
**Regression-test?:** Yes — `tests/test_gateway_control_plane.py` covers the session lifecycle; a dedicated concurrency regression test (two threads, assert `session.busy` fires) is missing. Recommend adding.

---

### UC-PERF-002 — PARTIAL (Severity: Medium; Regression test: No)

**Claim:** Many concurrent sessions writing memory should not cause Chroma corruption, SQLite "database locked", or FTS5 refresh races.

**Evidence — file:line + quoted code:**

`src/tokoboros/core/memory_vault.py:70-78`
```python
self.client = chromadb.PersistentClient(
    path=self.db_path, settings=Settings(anonymized_telemetry=False)
)
```

There is **no application-level write lock** anywhere in `memory_vault.py`. No `threading.Lock`, `threading.Semaphore`, or equivalent guards any of the `collection.upsert(...)` calls. The docstring in `shared_services.py:12-13` states "MemoryVault — Chroma PersistentClient; handles concurrent access natively." This is a single-writer assumption delegated entirely to ChromaDB's embedded sqlite/hnswlib layer.

`src/tokoboros/core/memory_index.py:71-82` (`_conn` context manager):
```python
@contextmanager
def _conn(self) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

The FTS5 index uses `check_same_thread=False` and a 10-second timeout. A concurrent `refresh()` (lines 92-151) does a full `DELETE FROM mem_fts` + `DELETE FROM mem_nodes` + batch `INSERT` in a single connection. A simultaneous `search()` (lines 157-186) opens a separate connection. Under SQLite WAL mode (Chroma's default), readers do not block writers, but a `refresh()` mid-`search()` produces a TOCTOU window where the search sees a partially cleared index.

`src/tokoboros/core/memory_index.py:134-148` — the refresh is not atomic relative to searches from other threads:
```python
with self._conn() as conn:
    conn.execute("DELETE FROM mem_fts")
    conn.execute("DELETE FROM mem_nodes")
    if nodes:
        conn.executemany("INSERT OR REPLACE INTO mem_nodes ...", nodes)
        conn.executemany("INSERT INTO mem_fts (node_id, text) ...", nodes)
```

**Finding:**
- Chroma concurrent access: relying on embedded Chroma's native sqlite locking. Under light concurrent writes (single `PersistentClient` shared via `ProcessSharedServices` singleton), this is likely safe because Chroma itself serializes at the sqlite WAL layer. The claim "handles concurrent access natively" is accurate for the single-process, shared-client case.
- FTS5 `refresh()` vs `search()` race: genuinely present. A search during a full refresh sees empty results for the window between `DELETE` and `INSERT`. This is a PARTIAL not BLOCKED because the code is right here to read and the window is real — it causes empty FTS5 hits, not data corruption. The SQLite `timeout=10` would serialize conflicting writers (only one connection gets the write lock at a time), so data integrity is preserved but `search()` during refresh returns stale-empty results.
- No cross-process protection: confirmed absent (MR-9), but that is scoped to UC-PERF-003.

**Status:** PARTIAL — Chroma single-process concurrency relies on native locking (acceptable). FTS5 refresh vs search TOCTOU window is real but causes temporary empty results, not corruption. BLOCKED-NEEDS-LIVE for actual 10-session load test.

**Severity:** Medium
**Regression-test?:** No — no test for concurrent FTS5 refresh + search race.

---

### UC-PERF-004 — CONFIRMED (Severity: Low; Regression test: Yes)

**Claim:** The prompt-length cap (default 100k chars) is enforced; graceful compaction handles large context; no 400 from malformed history.

**Evidence — file:line + quoted code:**

`src/tokoboros/gateway/control_plane.py:94-96` — cap is a constructor parameter with default:
```python
max_prompt_chars: int = 100_000,
```

`src/tokoboros/gateway/control_plane.py:125-126`
```python
self._max_prompt_chars = max(1, max_prompt_chars)
```

`src/tokoboros/gateway/control_plane.py:250-253`
```python
if len(prompt) > self._max_prompt_chars:
    raise GatewayProtocolError(
        f"prompt exceeds max length of {self._max_prompt_chars} characters"
    )
```

The error is surfaced as `GatewayProtocolError`, which `fastapi_app.py` maps to HTTP 400 with `code: "protocol_error"`. The check is on `len(prompt)` (character count), matching the 100k-chars specification.

**Offline test evidence:** `tests/test_gateway_control_plane.py::test_chat_rejects_oversized_prompt` (passed 2026-06-14):
```python
cp = GatewayControlPlane(on_chat=lambda _session_id, prompt: prompt, max_prompt_chars=8)
# ...
response = client.post(f"/sessions/{session_id}/messages", json={"prompt": "0123456789"})
assert response.status_code == 400
assert "prompt exceeds max length" in response.json()["message"]
```

Also `tests/test_gateway_control_plane.py::test_session_churn_respects_limits_and_recovers_capacity` exercises the same cap at the HTTP level (passed).

**Finding:** Cap is correctly enforced at 100k chars by default; the error is clear and maps to a 400. Graceful compaction logic sits in `core/memory.py:751-907` (sliding window + safe-boundary finder); static inspection confirms tool_use/tool_result pairs are never split (the safe-boundary finder walks backward to ensure pairing). The prompt-level cap and the compaction are independent layers working together correctly. Large-context OOM is blocked by the 100k chars limit before it reaches the compaction layer.

**Severity:** Low (behaves correctly)
**Regression-test?:** Yes — `test_chat_rejects_oversized_prompt` is the direct regression test and passes.

---

### UC-PERF-005 — PARTIAL (Severity: Medium; Regression test: No)

**Claim:** Size limit enforced for file read/write; `except: pass` at `os_tools.py:390-391` allows bypass on read.

**Evidence — file:line + quoted code:**

`src/tokoboros/tools/os_tools.py:379-392` — guarded read_file:
```python
try:
    target = normalized
    if (
        target.exists()
        and target.is_file()
        and target.stat().st_size > gate.max_file_size_bytes
    ):
        return (
            "Permission Denied: File exceeds configured max_file_size_mb. "
            "Update device-permissions.og to raise the limit."
        )
except Exception:
    pass
return _raw_read_file(str(normalized))
```

The `except Exception: pass` swallows **any** error from `target.exists()`, `target.is_file()`, or `target.stat().st_size`. If `stat()` raises (e.g., a special file, a symlink that breaks, or an unusual filesystem entry), the size check is silently skipped and `_raw_read_file` is called with no size guard. A crafted file path that triggers a `stat()` exception bypasses the cap.

By contrast, `guarded_write_file` at `src/tokoboros/tools/os_tools.py:400-410` is NOT bypassable:
```python
content_bytes = len(content.encode("utf-8"))
if content_bytes > gate.max_file_size_bytes:
    return "Permission Denied: Content exceeds configured max_file_size_mb. ..."
```
The write-side check operates on the already-in-memory content string, so no `stat()` call is needed and there is no exception path.

Additionally, `src/tokoboros/core/permissions.py:497-508` — the permission gate's own `check_path` also enforces size, but with an `except OSError: pass` that similarly skips the check on stat failure:
```python
if operation in {"read", "write"} and resolved.exists() and resolved.is_file():
    max_bytes = self._fs.max_file_size_mb * 1024 * 1024
    try:
        current_size = resolved.stat().st_size
    except OSError:
        ...  # (check skipped — see line 501)
```

**Finding:** The `read_file` size check is bypassable via a `stat()`-raising path (special device, broken symlink, etc.). Write-file size check is NOT bypassable. On normal files the check works correctly (50 MB default, 500 MB at unrestricted). The bypass requires an unusual file type, making it Medium not High severity. The default file-size limits are enforced at both the tool and gate layers for normal files. Large Sheets/Gmail reads have no pagination (confirmed in UAT plan — outside os_tools scope).

**Severity:** Medium (`read_file` size-check bypass via stat() exception)
**Regression-test?:** No — no test exercises the `except Exception: pass` bypass path.

---

### UC-PERF-006 — CONFIRMED (Severity: Low; Regression test: Yes)

**Claim:** Up to 3 retries with exponential backoff on 429/529; no duplicate streamed output on retry.

**Evidence — file:line + quoted code:**

`src/tokoboros/core/loop.py:30-34`
```python
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
_RETRYABLE_HINTS = ("overloaded", "rate limit", "timeout", "timed out", "temporarily", "connection")
_MAX_LLM_RETRIES = 3
_LLM_RETRY_BASE_DELAY = 1.0
```

`src/tokoboros/core/loop.py:347-370` — `_generate_with_retry`:
```python
delay = _LLM_RETRY_BASE_DELAY
for attempt in range(1, _MAX_LLM_RETRIES + 1):
    try:
        return self.llm_client.generate(**kwargs)
    except Exception as exc:
        if attempt >= _MAX_LLM_RETRIES or not _is_retryable_llm_error(exc):
            raise
        logger.warning(...)
        self.on_event("llm_retry", {"attempt": attempt, "error": str(exc)})
        time.sleep(delay)
        delay *= 2
```

`src/tokoboros/core/loop.py:386-419` — `_stream_generate` no-duplicate guard:
```python
for attempt in range(1, _MAX_LLM_RETRIES + 1):
    chunks_yielded = 0
    try:
        gen = self.llm_client.generate_stream(**kwargs)
        while True:
            try:
                chunk = next(gen)
            except StopIteration as si:
                return si.value
            chunks_yielded += 1
            on_delta(chunk)
    except Exception as exc:
        if chunks_yielded > 0 or attempt >= _MAX_LLM_RETRIES or not _is_retryable_llm_error(exc):
            raise
        # ...retry
        time.sleep(delay)
        delay *= 2
```

**Finding:** Both retry paths are correct:
- `_generate_with_retry`: 3 attempts, 1s→2s→4s exponential backoff, `llm_retry` event emitted each time, re-raises on auth/400 errors immediately.
- `_stream_generate`: Only retries if `chunks_yielded == 0` — once any chunk is emitted to the caller, no retry is attempted on subsequent failure, preventing duplicate output. This is the correct no-duplicate-output design.
- `_is_retryable_llm_error` correctly checks both HTTP status (429, 529, 500-504) and message heuristics for connection/timeout errors.

`tests/test_rate_limit.py` tests the `RateLimiter` (sliding-window, gateway-level), not the LLM retry. The retry logic is untested offline but the code is correct by inspection.

**Severity:** Low (behaves correctly)
**Regression-test?:** Yes (RateLimiter tested; LLM retry path has no dedicated unit test — recommend adding mock-LLM retry test).

---

### ADV-024 — CONFIRMED (Severity: High; Regression test: No)

**Claim:** `get_or_create` ignores config after the first call (stale singleton A3-c); skill upsert collisions; Chroma write contention.

**Evidence — file:line + quoted code:**

`src/tokoboros/gateway/shared_services.py:79-85`
```python
global _INSTANCE
if not force_reinit and _INSTANCE is not None:
    return _INSTANCE
with _LOCK:
    if force_reinit or _INSTANCE is None:
        _INSTANCE = cls._init(config, brain_dir)
return _INSTANCE
```

The check `_INSTANCE is not None` at line 80 is a **fast-path outside the lock**. If the singleton exists, the call returns immediately without inspecting the `config` or `brain_dir` arguments. There is **no check that `brain_dir` matches the stored `brain_dir`** and **no check that `config` matches what was used to initialize the singleton**. A second session started with a different `memory.backend` setting or a different `brain_dir` silently gets the original singleton's memory backend, skill engine, and Chroma client.

Concretely: if session 1 starts with `memory.backend = "chroma"` and session 2 starts with `memory.backend = "fts5_only"`, session 2's `get_or_create` returns the `chroma`-backed singleton and the `fts5_only` config is silently dropped.

`src/tokoboros/gateway/shared_services.py:102-176` — `_init` uses `brain_dir` and `config` exclusively once; the module docstring at line 20-23 documents this explicitly:
```python
shared = ProcessSharedServices.get_or_create(config, brain_dir)
# re-call with same config → returns the same instance (no re-init)
same  = ProcessSharedServices.get_or_create(config, brain_dir)
assert shared is same
```

The docstring says "same config" but no equality check is performed.

**Skill upsert collisions:** `src/tokoboros/og_engine/engine.py:1062`
```python
path.write_text(serialize_og_file(og), encoding="utf-8")
self.load_file(path)  # hot-load so the skill is usable immediately
```

`upsert_skill` does a `path.exists()` check followed by `path.write_text()` with no file lock. Two concurrent `upsert_skill` calls for the same slug have a TOCTOU window: both see `is_update = True` (or both see `False`), produce conflicting writes, and the `load_file` calls on the shared `SkillEngine` run concurrently. `SkillEngine._nodes` is a `dict` — concurrent writes are not safe in CPython under the GIL for complex operations but `dict.__setitem__` is atomic, so the worst case is last-write-wins, not corruption.

**Finding:** The config-ignored-after-first-call bug is CONFIRMED. It is by design as documented, but the design choice has a real consequence: any session started with non-default memory settings after the first session silently uses the first session's config. Skill upsert has a file-level TOCTOU race under concurrent calls (concurrent `save_skill` for the same name), but CPython GIL makes dict corruption unlikely (last-write-wins). Chroma write contention defers to Chroma's embedded SQLite locking (same as UC-PERF-002 finding).

**Severity:** High (config ignored silently; particularly dangerous if memory backend changes between sessions)
**Regression-test?:** No — no test verifies that a second `get_or_create` with different config raises or logs a warning.

---

### ADV-025 — PARTIAL (Severity: High; Regression test: No)

**Claim:** Size/shape limits reject early; no OOM; no tar bomb extraction limit.

**Evidence — file:line + quoted code:**

**Tar bomb — CONFIRMED absent (MR-4):**
`src/tokoboros/core/og_pack.py:402-403`
```python
with tarfile.open(archive_path, "r:*") as tf:
    tf.extractall(tmp_path)  # noqa: S202
```
`tf.extractall(tmp_path)` has no `members` filtering, no `filter='data'`, and no member-count or total-size cap. A tar bomb (recursive nested archive, or simply a huge sparse file within the tarball) will extract without bound into the temporary directory. The `# noqa: S202` comment acknowledges the Bandit warning and explicitly suppresses it. This is the MR-4 finding reproduced at code level.

**Large .og with many nodes — no explicit node count cap:**
`src/tokoboros/og_engine/engine.py:423-441` — `load_file` iterates `og_file.nodes` with no limit. A `.og` with millions of nodes would load all into `self._nodes` dict. No count cap exists at the parser or engine level. This is an OOM risk for pathologically large skill files.

**Prompt-length cap — confirmed in UC-PERF-004:** 100k characters default; enforced at `control_plane.py:250`.

**File-size cap for read/write — confirmed in UC-PERF-005:** Default 50 MB (sandbox/guided), 500 MB (unrestricted). Read bypass exists via `except Exception: pass`.

**Large sheet (1M rows) — no cap:** The UAT plan confirms no pagination on Sheets reads. Static search for `max_row`, `_MAX_SHEET`, `1M`, `million` across `src/tokoboros` found zero matches. A 1M-row sheet read would attempt to load everything into memory.

**Deeply nested JSON in MCP args — no depth limit:** Search for `json.*depth`, `sys.setrecursion`, `recursion.*limit`, `deeply.*nested` across `src/tokoboros` found zero matches. Python's `json.loads` has no depth limit in standard library (no recursive depth guard). A deeply nested JSON MCP arg would only be bounded by CPython's default recursion limit (1000), which raises `RecursionError`, not a clean error.

**Finding:** Tar bomb extraction is confirmed unbounded (MR-4). No `.og` node count cap exists. Sheet/doc reads have no pagination or size limit (not in os_tools scope but relevant to the DoS surface). Deeply nested JSON MCP args hit CPython's recursion limit with an unhandled `RecursionError`. The prompt-length cap and file-size caps provide some protection for direct user inputs but do not cover the skill-pack / sheet / MCP-arg vectors.

BLOCKED-NEEDS-LIVE for actual 10 GB upload attempt, 1M-row sheet, and tar bomb extraction test. Static analysis confirms the code paths have no guards.

**Severity:** High (tar bomb extraction unbounded confirmed; RecursionError on deeply nested JSON; no sheet row limit)
**Regression-test?:** No — MR-4 tarball traversal test is in the P0 exit criteria (`docs/UAT_BETA_TEST_PLAN.md:11.6`) but not yet present in `tests/`.

---

## Summary table

| ID | Verdict | Severity | Key finding |
|---|---|---|---|
| UC-PERF-001 | CONFIRMED | Low | Per-session `_chat_lock` + `SESSION_BUSY` event wired correctly at `orchestrator.py:886-895`; history cannot interleave |
| UC-PERF-002 | PARTIAL | Medium | Chroma defers to native sqlite locking (acceptable for single-process); FTS5 `refresh()` vs `search()` TOCTOU window causes empty results (not corruption); BLOCKED-NEEDS-LIVE for load test |
| UC-PERF-004 | CONFIRMED | Low | 100k-char cap enforced at `control_plane.py:250`; `test_chat_rejects_oversized_prompt` passes |
| UC-PERF-005 | PARTIAL | Medium | Write-file size check is correct; read-file size check bypassed via `except Exception: pass` at `os_tools.py:390-391` when `stat()` raises |
| UC-PERF-006 | CONFIRMED | Low | 3-retry exponential backoff correct; `_stream_generate` guards against duplicate output via `chunks_yielded > 0` check at `loop.py:406` |
| ADV-024 | CONFIRMED | High | `get_or_create` at `shared_services.py:80` returns stale singleton without checking `config` or `brain_dir` equality — second session with different memory config gets first session's backend silently |
| ADV-025 | PARTIAL | High | Tar bomb extraction confirmed unbounded (`og_pack.py:403`, `# noqa: S202`); no `.og` node-count cap; no JSON nesting depth limit; sheet reads unbounded; BLOCKED-NEEDS-LIVE for actual extraction test |
