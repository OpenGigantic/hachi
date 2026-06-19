# QA Audit v2 — Performance & Scalability (Agent 5)

**Date:** 2026-06-18 (v2 re-verification) · **Scope:** frontend perf, backend perf, API latency, DB efficiency, caching (incl. Anthropic prompt-cache breakpoints), resource use, concurrency (event-loop blocking, thread-pool, locks), memory consumption, startup, large-scale readiness.
**Method:** Re-verification of every prior PERF-* finding against CURRENT code (read-only), plus real measurements where cheap. "Measured" = a number I produced this session and say so; "reasoned" = inferred from code structure. The Textual cockpit (`src/hachi/tui/cockpit/**`) is OFF-LIMITS (concurrent session) and excluded from scoring — noted at high level only where it consumes shared backend/client paths inefficiently.

**Prior report:** `docs/audit/2026-06-18/05-performance-scalability.md` (26 findings: Critical 2 · High 9 · Medium 11 · Low 4).

---

## Verdict at a glance

The two Critical findings — **PERF-001 (uncached message history / moat regression)** and **PERF-025 (unvirtualized chat transcript)** — are both **STILL-OPEN, byte-for-byte unchanged.** The remaining prior highs are mostly STILL-OPEN; two improved (**PERF-022 startup → PARTIAL**, big wins on Chroma/Textual laziness; **PERF-024 → FIXED**). The major churn since v1 — the new `src/hachi/client/**` REST+SSE module — is a **net performance positive** (pooled httpx client, true SSE streaming, no busy-poll). The new gateway web endpoints repeat the **same blocking-in-async + unbounded-fetch** anti-patterns the prior audit flagged.

**Counts this audit:** 6 NEW findings (0 Critical · 1 High · 3 Medium · 2 Low) on top of the prior set.
**Prior-finding status:** 1 FIXED · 2 PARTIAL · 23 STILL-OPEN (of 26).

---

## Measurements (what I actually ran)

Environment caveat: measured on a **Python 3.14** interpreter (the repo targets 3.11 via `uv`; `uv`'s editable install was not available, so I `pip install`ed `keyring`, `tomlkit`, `chromadb`, `anthropic`, etc. into a bare interpreter). Numbers are **warm-pyc**, single-machine. Treat absolute values as indicative; the **relative** costs and the **which-modules-load** facts are the load-bearing results and are interpreter-version-independent.

| Measurement | Command | Result |
|---|---|---|
| `import hachi.cli.main` warm import time | `python -c "import time;t=…;import hachi.cli.main;…"` ×3 | **4.13 s, 4.13 s, 4.15 s** |
| `hachi --help` full process (spawn+import+render) | `subprocess.run(['python','-c', app() with argv=--help])` ×3 | **4.63 s, 4.71 s, 4.87 s** |
| `import anthropic` (warm, isolated) | `python -c "import anthropic"` | **3.0 s** |
| `import chromadb` (warm, isolated) | `python -c "import chromadb"` | **4.0 s** |
| `import uvicorn` (warm, isolated) | — | **0.60 s** |
| `import textual` (warm, isolated) | — | **0.59 s** |
| **Which heavy modules `import hachi.cli.main` pulls** | diff `sys.modules` before/after | **LOADED:** anthropic, uvicorn, cryptography, keyring, httpx, pydantic, tomlkit. **LAZY (not loaded):** chromadb, fastapi, starlette, textual, onnxruntime, tokenizers, numpy |
| `-X importtime` top contributors | `python -X importtime -c "import hachi.cli.main"` | `hachi.cli.main` cum **5.5 s** (cold, incl. compile); `orchestrator` 4.73 s → `cost` 4.01 s → `llm` 4.01 s → `anthropic` 2.48 s; `secret_store` 1.53 s → `cryptography…_rust` 1.29 s; `control_plane` pulls `uvicorn` |
| Shipped `.og` library size | `find src/hachi -name '*.og' | wc` + byte count | **36 files, ~95 KB, ~24k est tokens** (prior audit cited ~9k / 53 nodes — grown ~2.5×) |
| `embedding_function` usage in `src` | `grep -rE "embedding_function|SentenceTransformer|DefaultEmbeddingFunction|all-MiniLM"` | **0 matches** (Chroma still uses default ONNX MiniLM) |
| Default registry tool count basis | grep `registry.register(` in orchestrator | confirms tools are registered dynamically; schema array rebuilt per iteration (PERF-002) |

**Static-analysis verifications** (read this session, file:line cited per finding below): `llm.py` `_build_kwargs`, `loop.py:704-706` & `:322-324`, `fastapi_app.py` (all handlers), `control_plane.py:342-372`, `session_store.py` (all methods), `runtime_read_models.py:236-429`, `orchestrator.py:720-849`, `memory_vault.py:121-148,209-223`, `memory_index.py:92-151`, `shared_services.py:44-249`, `jobs/queue.py:566-577`, `og_engine/engine.py:168,465-509`, `client/gateway.py`, `client/sse.py`, `frontend/src/lib/data/domains/chat.ts`, `frontend/src/routes/conversation.tsx`, `frontend/src/components/ui/Markdown.tsx`, `frontend/package.json`.

---

## Prior-finding verification table

| ID | Title (short) | Prior sev | Status | Current evidence |
|----|---------------|-----------|--------|------------------|
| PERF-001 | Message history re-billed (only 2/4 cache breakpoints) | Critical | **STILL-OPEN** | `llm.py:247-260` — `cache_control` only on system prefix (`:249`) + last tool (`:258`); `messages` carry none. `loop.py:704` sends `self.memory.history` raw. |
| PERF-002 | Tool schemas re-sent; only last cached | Medium | **STILL-OPEN** | `loop.py:706` rebuilds `get_schemas(self._active_groups)` per iter; `llm.py:254-260` caches `tools_copy[-1]` only; group-unlock still invalidates prefix. |
| PERF-003 | `.og` savings claim narrow; retrieval misroutes | Medium | **STILL-OPEN (CHANGED context)** | `docs/BENCHMARK.md` still 97.6% (oracle keyword); engine still substring/BM25, no semantic default. Library grew to ~24k tokens — now **near** the ~30k crossover the prior audit named. |
| PERF-004 | Token counting free; len/4 estimator | Low (pos) | **STILL-OPEN (still good)** | `llm.py:273-279` reads usage from API incl. cache read/write; no count-tokens hot-path call. No change needed. |
| PERF-005 | SSE busy-polls sync lock-taking read on loop | High | **PARTIAL** | `fastapi_app.py:457` still calls sync `control_plane.read_session_events` directly on the loop (no `to_thread`). **But** poll cadence is now adaptive: `await asyncio.sleep(0.05 if events else poll_interval)` (`:483`), `poll_interval` default **1.0s** (`:436`). Idle spin dropped 20×; active-stream still 50 ms of sync locked scans. |
| PERF-006 | `read_events_since` O(n) scan per poll | Medium | **STILL-OPEN** | `runtime_read_models.py:428` `[snap for snap in self._events if snap.sequence > after]`; `:246-247` `self._events = self._events[-2000:]` slice churn. |
| PERF-007 | Per-session ThreadPoolExecutor(4) | Medium | **STILL-OPEN** | `loop.py:322-324` each loop builds its own pool; comment confirms not shared. Shared-services shares engine/MCP/vault but NOT the tool pool. |
| PERF-008 | Per-session `chat()` lock (correct by design) | Low | **STILL-OPEN (still correct)** | `fastapi_app.py:416` `post_message` is sync `def` → Starlette threadpool, not loop. Acceptable; Starlette 40-worker ceiling still worth documenting. |
| PERF-009 | FTS5 `refresh()` full DELETE+rebuild, re-parses all | Medium | **STILL-OPEN** | `memory_index.py:97-148` `rglob("*.og")` + parse-all, then `DELETE FROM mem_fts/mem_nodes` + full re-insert; no mtime skip. |
| PERF-010 | Sub-agent delegation duplicates runtime objects | Medium | **STILL-OPEN** | `delegate_task` still builds scoped `ToolRegistry` + child `ExecutionLoop` (→ its own PERF-007 pool + schema rebuild). |
| PERF-011 | Read endpoints `async def` doing blocking work | High | **STILL-OPEN (read paths)** | `fastapi_app.py` `get_skills:822`, `get_memory:827`, `get_og_graph:857`, `get_capabilities:386` are `async def` calling sync control-plane work with no `to_thread`. (Contrast: `create_session:398`, OpenAI `:579`, channel `:741` now DO offload — pattern applied inconsistently.) |
| PERF-012 | `/memory` fetches ALL docs, no pagination/cache | High | **STILL-OPEN** | `orchestrator.py:741-760` `snapshot_memory_facts` loops collections, `coll.get()` (`:744`) **no limit** on unbounded `core_memory` (`memory_vault.py:58-59`). |
| PERF-013 | `/og/graph` recomputes token estimates per request | Medium | **STILL-OPEN** | `orchestrator.py:780-849` iterates all nodes, `estimate_tokens()` ≥3×/node each request; served by unoffloaded `async def`. |
| PERF-014 | No HTTP caching headers on snapshot endpoints | Low | **STILL-OPEN** | `fastapi_app.py:822-885` — `/skills`, `/og/graph`, `/capabilities`, `/authoring/targets`, `/spend` return plain JSON, no `Cache-Control`/`ETag`. |
| PERF-015 | SessionStore single conn + global lock | High | **STILL-OPEN** | `session_store.py:49-51` one `sqlite3.connect(check_same_thread=False)` + one `threading.Lock`; every read method (`:319,331,342,362`) wraps `with self._lock`, defeating WAL reader concurrency. |
| PERF-016 | Cross-session search leading-wildcard LIKE | Medium | **STILL-OPEN** | `session_store.py:277-280` `WHERE m.content LIKE ?` with `f"%{query}%"`; comment `:85` confirms B-tree index removed. |
| PERF-017 | JobQueue connect/close per operation | Medium | **STILL-OPEN** | `jobs/queue.py:566-577` `_conn()` `sqlite3.connect(...)` + `close()` per call; no WAL/busy_timeout PRAGMA. Worker polls `get_pending` every 0.5s → ≥2 connect/close per second idle. |
| PERF-018 | `delete_sessions_before` unbounded IN(...) | Low | **STILL-OPEN** | `session_store.py:415-423` `placeholders = ",".join("?"*len(ids))` → 999-var cap risk on large purge. |
| PERF-019 | Chroma default ONNX embedding cold-start + net dep | High | **STILL-OPEN** | `memory_vault.py:121-148` `PersistentClient` + `get_or_create_collection` with NO `embedding_function`; grep = 0 matches. Measured: `import chromadb` alone **4.0s**; first embed downloads MiniLM (~80MB). |
| PERF-020 | `core_memory` unbounded; recall/`/memory` grow | Medium | **STILL-OPEN** | `memory_vault.py:58-59` "Unbounded"; aggregates still `.get()`-all (e.g. `snapshot_memory_facts`). |
| PERF-021 | `delete_fact` opens 2nd SQLite conn per delete | Low | **STILL-OPEN** | `memory_vault.py:215-221` `_delete_from_fts5` `sqlite3.connect`+`close()` per fact. |
| PERF-022 | CLI eagerly imports full runtime | High | **PARTIAL (improved)** | Chroma/FastAPI/Textual/onnx/numpy now **lazy** (measured: NOT in `sys.modules` after `import hachi.cli.main`). **But** `anthropic` (3.0s), `uvicorn` (0.6s), cryptography, keyring still eager via `schedule→orchestrator→cost→llm→anthropic` and `control_plane→uvicorn`. `--help` measured **~4.6s**, ~3s of it avoidable. |
| PERF-023 | Skill engine eagerly parses every `.og` at boot | Medium | **PARTIAL** | Per-session duplication FIXED: `shared_services.py:209-216` shares one `SkillEngine` per fingerprint/process. Eager full-body parse STILL-OPEN: `engine.py:476-477` `glob("**/*.og")`+`load_file` each; scales with library size (now ~24k tokens). |
| PERF-024 | Shared-services singleton ignores config | Medium | **FIXED** | `shared_services.py:44-59` keys instances by `_config_fingerprint(brain_dir, memory.backend, memory.graph)`; `get_or_create` (`:88-149`) returns per-fingerprint instance + MCP health check. Reuse win kept, silent-config bug gone. |
| PERF-025 | Chat transcript unvirtualized; array remapped per delta | Critical | **STILL-OPEN** | `conversation.tsx:746-754` `messages.map(...)` no virtualization; `:570` `useStore((s)=>s.messages)`; `chat.ts:134-146` `appendTextDelta` does `msgs.map(...)` → new array ref per `text.stream.delta`. `package.json` has NO virtualization lib. |
| PERF-026 | `Markdown` re-parses full text every render | Medium | **STILL-OPEN** | `Markdown.tsx` — no `React.memo`, no `useMemo` (grep = 0); `parseBlocks(text)` runs every render. `MessageItem` (`conversation.tsx:464`) is a plain function, not memoized → all items re-render per delta. |

---

## NEW findings (this audit)

### PERF-N01 — New gateway web endpoints repeat the blocking-`async def` pattern on heavy reads
- **Severity:** High · **Area:** Backend / API-latency / Concurrency
- **Root cause:** The web-UI routers added since v1 (`fastapi_app.py:812-885`) — `get_skills` (`:822`), `get_memory` (`:827`), `get_memory_recall` (`:832`), `get_og_graph` (`:857`), `get_authoring_targets` (`:862`), `get_spend` (`:882`), plus `get_capabilities` (`:386`) — are all `async def` that call synchronous `control_plane.*` methods which do real work: `og_graph()` → `snapshot_og_graph` iterates every node ×3 token-estimates; `memory()` → `snapshot_memory_facts` pulls **all** of unbounded `core_memory`; `skills()` walks the skill index. None use `anyio.to_thread.run_sync`. This is the *same* PERF-011 class, now multiplied across ~7 new endpoints.
- **Impact:** Each of these handlers blocks the single asyncio event loop for its full duration. One large-vault `/memory` call (PERF-012) or a 200-node `/og/graph` (PERF-013) stalls **every** concurrent request — including active SSE keepalives and `/health`. The cockpit's 3s auto-refresh (`tui/cockpit/screen.py:121`, OFF-LIMITS — noted only) and any chatty web client will hit these repeatedly, amplifying tail latency.
- **Optimization:** Make these handlers `def` (Starlette runs `def` in its threadpool) OR wrap each control-plane call in `await anyio.to_thread.run_sync(...)`. Apply the discipline already used by `create_session`/`post_message`.
- **Expected impact:** Removes a whole class of event-loop stalls; tail latency under concurrency drops sharply. (S)
- **Complexity:** S.

### PERF-N02 — `og_graph()` precomputes a `requires`/referenced-name set on every request (O(N·E)), uncached
- **Severity:** Medium · **Area:** Backend / Caching
- **Root cause:** `orchestrator.py:794-849` builds `referenced_names` by scanning every node's `requires` on each call, then for every node concatenates and `estimate_tokens()`-es index/dense/full blocks. All of this is recomputed per request even though the parsed skill graph is static within a process (shared `SkillEngine`). Extends PERF-013 with the extra full-graph dependency pass and the heavy-block string concatenation.
- **Impact:** O(nodes × deps + nodes × content-bytes) CPU per `/og/graph` hit, on the event loop (compounds PERF-N01). At the roadmap's 200–1000 node target this becomes a multi-hundred-ms synchronous stall per request.
- **Optimization:** Compute the graph payload once on skill-engine init (or on `load_file`/`upsert_skill`) and cache it on the shared services object; invalidate on mutation. Add an `ETag`/`Cache-Control` (ties to PERF-014).
- **Expected impact:** `/og/graph` becomes a dict return; cost amortized to once-per-library-change. (S)
- **Complexity:** S.

### PERF-N03 — `complete_chat` / OpenAI streaming facade re-reads the event log from sequence 0 to find the cursor
- **Severity:** Medium · **Area:** API-latency / Concurrency
- **Root cause:** `fastapi_app.py:606` snapshots the cursor with `read_session_events(session_id, after=0, limit=10000)` then `max(...sequence...)`. Because `read_events_since` is an O(n) full scan (PERF-006) over up to 2000 events, every OpenAI-compatible streaming request pays an O(2000) scan **just to discover the starting cursor**, before the turn even begins — and then the stream loop polls the same sync read every 50 ms (`:677`).
- **Impact:** Adds a fixed O(n) scan to the start of each `/v1/chat/completions` stream, plus the per-poll PERF-006 cost during streaming. Wasteful at scale; redundant with the monotonic sequence already tracked by the read model.
- **Optimization:** Expose a cheap "current max sequence" accessor on the read model (it already holds `self._sequence`) and call that instead of fetching 10000 events; combine with the PERF-006 `deque`+`bisect` fix so even the poll loop is O(log n).
- **Expected impact:** Removes a 2000-element scan per streaming request start and shrinks per-poll cost. (S)
- **Complexity:** S.

### PERF-N04 — `mcp_inventory()` client adapter is O(servers × tools) rebuild on every poll (minor)
- **Severity:** Low · **Area:** Backend / Client
- **Root cause:** `client/gateway.py:263-273` rebuilds the full `(servers, tools)` lists from the raw inventory on each call; the cockpit refresh (OFF-LIMITS) and any UI calling `/runtime/mcp/inventory` will re-materialize this every cycle. The gateway side returns the inventory uncached too.
- **Impact:** Small in absolute terms (inventories are tens of items), but it's pure rework on a polled path with no caching/ETag.
- **Optimization:** Cache the inventory server-side with a short TTL invalidated on MCP pool change; add `ETag`.
- **Expected impact:** Avoids repeated reconstruction on chatty pollers. (S)
- **Complexity:** S.

### PERF-N05 — SSE frame parser uses repeated front-slicing of the buffer (O(n²) worst case)
- **Severity:** Low · **Area:** Client / Frontend-parity
- **Root cause:** `client/sse.py:83-86` consumes lines via `buffer = buffer[nl_pos+1:]` in a `while "\n" in buffer` loop, re-slicing (and re-`index`-ing) the remaining buffer each line. For normal small SSE frames this is fine; for a single very large `data:` payload (e.g. a big tool result preview pushed as one frame) it degrades toward O(n²). The same shape exists in the TS parser it mirrors.
- **Impact:** Negligible for typical token deltas; a pathological large single frame could spike CPU in the consumer thread.
- **Optimization:** Split on a found newline index using a running offset, or use `bytearray`/`memoryview` and `splitlines(keepends=True)` on flushed segments.
- **Expected impact:** Bounds parse cost to O(n) regardless of frame size. (S)
- **Complexity:** S.

### PERF-N06 — `_resolve_api_key` re-reads & re-parses `.env` from cwd on every LLM client build (minor)
- **Severity:** Low · **Area:** Startup / Backend
- **Root cause:** `llm.py:39-53` falls back to `_read_dotenv(Path.cwd()/".env")` which opens and line-parses the file each time a client is created, when the env var is unset. Sub-agent delegation and multi-session boot can build many clients.
- **Impact:** Small per-build file read + parse; also a subtle cwd-dependency (the project already flags cwd-relative `.env` as a config smell in WB-025).
- **Optimization:** Resolve once at process start into a cached mapping; reuse.
- **Expected impact:** Removes redundant file I/O on the client-construction path. (S)
- **Complexity:** S.

---

## What got better since v1 (positives, confirmed)

- **NEW `src/hachi/client/**` is a performance-positive addition.** `GatewayClient` (`gateway.py:84-96`) lazily creates **one** `httpx.Client` and reuses it for all calls (real connection pooling) — no per-call client creation. The SSE consumer (`sse.py:219-232`) uses true `http_client.stream(... timeout=None)` and yields chunks as they arrive (`_iter_chunks`, `:244-248`) — **no busy-polling, no unbounded buffering** beyond a single frame. Cancellable via `threading.Event`, context-managed cleanup. This is the right design.
- **PERF-024 fixed** (config-fingerprinted singleton) — keeps the heavy-object reuse win without the silent-wrong-backend bug.
- **PERF-022 materially improved** — the two biggest cold-start offenders (ChromaDB ~4s, Textual ~0.6s) are now lazy; `import hachi.cli.main` no longer pulls them.
- **PERF-005 idle cost cut ~20×** — adaptive `0.05 if events else poll_interval(=1.0)` sleep means idle streams no longer spin at 20 Hz.
- **Frontend deps still lean** (`package.json`: react/react-dom/@tanstack-router/zustand only — no heavy chart/markdown/date/icon libs); `autoCodeSplitting` on; selectors mostly narrow (the `s.messages` whole-array selector is the exception that drives PERF-025).

## Cockpit note (OFF-LIMITS — high level only)

`src/hachi/tui/cockpit/data.py` correctly reuses the shared pooled `GatewayClient` and falls back to placeholders on `GatewayError` (good). However `tui/cockpit/screen.py:121` runs a **3.0s `set_interval` auto-refresh** that drives the data facade across multiple gateway endpoints — including the uncached/unbounded `/memory` (PERF-012) and `/og/graph` (PERF-013) and the blocking read handlers (PERF-N01). Net effect: the cockpit **amplifies** the server-side read-path costs at 3s cadence per connected client. The fix belongs server-side (PERF-N01/N02/012/013), not in the cockpit.

---

## Top optimization opportunities — ranked by impact / effort

| # | ID | Title | Severity | Status | Effort |
|---|----|-------|----------|--------|--------|
| 1 | PERF-001 | Cache message-history prefix (stop re-billing skills/tool results) | Critical | STILL-OPEN | M |
| 2 | PERF-025 | Virtualize transcript + throttle SSE deltas + memo items | Critical | STILL-OPEN | M |
| 3 | PERF-011 / PERF-N01 | Offload blocking work out of `async def` read handlers (≈8 endpoints) | High | STILL-OPEN | S |
| 4 | PERF-006 | `deque(maxlen)` + bisect for event polling (also fixes PERF-N03) | Medium | STILL-OPEN | S |
| 5 | PERF-022 | Finish CLI lazy-import (defer `anthropic`+`uvicorn`) — ~3s off `--help` | High | PARTIAL | M |
| 6 | PERF-019 | Pre-warm / pin Chroma embedding model (4s cold-start + offline) | High | STILL-OPEN | M |
| 7 | PERF-012 | Paginate + cache `/memory` (unbounded core_memory) | High | STILL-OPEN | M |
| 8 | PERF-015 | Thread-local SessionStore connections (restore WAL reader concurrency) | High | STILL-OPEN | M |
| 9 | PERF-013 / PERF-N02 | Cache `/og/graph` payload at engine init | Medium | STILL-OPEN | S |
| 10 | PERF-026 | `React.memo(Markdown)` + `useMemo(parseBlocks)` | Medium | STILL-OPEN | S |

---

## Large-scale readiness verdict

**Still not ready for large-scale usage (many sessions / large transcripts / large vault / many skills) without the same targeted work the v1 audit named — and the two Critical items have not moved.** The per-turn LLM economics remain quietly self-defeating: loaded `.og` skills and tool results sit in uncached message history and are re-billed at full input price every turn (PERF-001), so the headline token-saving claim (PERF-003) holds only above ~30k-token libraries *and* only when the keyword retriever lands — and the shipped library has now grown to ~24k tokens, right at the crossover where cached dump-all is competitive. The gateway's read surface expanded (7+ new web endpoints) but inherited the v1 anti-patterns: blocking work on the asyncio loop (PERF-011/N01), an O(n) event scan per poll and per stream-start (PERF-006/N03), an unbounded `/memory` fetch (PERF-012), and uncached per-request graph/token recomputation (PERF-013/N02). The data layer is unchanged: SessionStore serializes all reads behind one lock/connection (PERF-015) and searches via unindexable `LIKE` (PERF-016); memory is unbounded with a heavy default ONNX embedding that cold-starts at a measured ~4s and carries a hidden network dependency (PERF-019/020). The frontend will still lock up on long transcripts — no virtualization, full-array remap per token, unmemoized markdown (PERF-025/026).

**What improved is real and worth banking:** the new client module is well-built (pooled, streaming, no busy-poll); the shared-services singleton bug is fixed (PERF-024); and CLI startup shed its two biggest imports (Chroma, Textual) — though `--help` still measures ~4.6s because `anthropic` (~3s) and `uvicorn` (~0.6s) are still eager. None of the open items are architectural dead-ends; they remain ~10 S/M-effort hotspots. Landing the top-10 table — especially the two Criticals and the cheap `to_thread`/`deque`/cache trio — would move the verdict from "single-user / small-team" to "comfortably multi-session with large transcripts and a growing vault."
