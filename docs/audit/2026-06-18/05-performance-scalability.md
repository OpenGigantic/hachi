# QA Audit — Performance & Scalability (Agent 5)

**Date:** 2026-06-18 · **Scope:** frontend perf, backend perf, API latency, DB efficiency, caching, resource use, concurrency, memory consumption, startup, large-scale readiness.
**Method:** Static analysis + code reading (read-only). No load tests run. Findings are grounded in code read this session. "Measured" = a number from a committed benchmark/doc; "reasoned" = inferred from code structure.

**Totals:** 26 findings — Critical 2 · High 9 · Medium 11 · Low 4.

Severity = user-perceived latency or scale impact, not security/correctness (those are other agents' lanes; only flagged here when they also gate scale).

---

## LLM / Token efficiency

### PERF-001 — Loaded skills & tool results re-billed at full input price every turn (no message-history caching)
- **Severity:** Critical · **Area:** LLM-Token / Caching
- **Bottleneck:** `src/hachi/core/llm.py:247-260` `_build_kwargs`. Only the **system prefix** gets `cache_control` (line 249) and the **last tool** schema gets one (line 258). Message history (`messages`) carries none. `loop.py:704` sends `self.memory.history` raw each iteration.
- **Root cause:** Anthropic prompt caching supports up to **4 cache breakpoints**; only **2** are used, and neither is on the conversation prefix. When a skill is loaded via `load_skill`, its `::full` text enters *message history* (confirmed by `docs/strategy/02-object-graph-eval.md:23`), so on every subsequent LLM call in the session it is re-sent as **fresh uncached input** at 1.0× price, not 0.1× cache-read. Same for large tool results that remain in history until cleared.
- **Optimization:** Add a `cache_control: ephemeral` breakpoint on the last *stable* message block (i.e. the most recent message that won't change next turn — typically the previous assistant/tool block). Anthropic caches the whole prefix up to that breakpoint, so a long loaded skill becomes a cache-read (0.1×) on turns 2..N. This is the single biggest per-turn token win for any session that loads a skill or accumulates tool output.
- **Expected impact:** For a multi-turn session that loads one ~400-token skill and keeps it resident, ~90% reduction in the re-billed cost of that block across the session (1.0×→0.1×); larger for big tool outputs. Directly undercuts the `.og` lazy-loading value prop, which today *spends back* its savings by re-billing the loaded block.
- **Effort:** M (history is mutated in place by compaction; the breakpoint must track a stable boundary and be re-pinned after compaction).

### PERF-002 — Tool schemas re-sent every iteration at full price (only the last one is cached)
- **Severity:** Medium · **Area:** LLM-Token / Caching
- **Bottleneck:** `loop.py:706` `self.registry.get_schemas(self._active_groups)` builds the tool array every iteration; `llm.py:254-260` caches only `tools_copy[-1]`.
- **Root cause:** ~58 `registry.register()` calls ship by default (reasoned from grep of `tools/`); core set ~29 tools. Each JSON schema is ~50–300 tokens, so the resident schema payload is roughly **1.5k–5k tokens per call**. Caching only the last tool means the *prefix* of the tools array is cached by Anthropic (tools sit before messages), which is actually fine **as long as the tool set is byte-identical across calls** — but `get_schemas` filtering by `_active_groups` means **the moment a new tool group unlocks mid-session (e.g. load_skill auto-unlocks its `::api` group), the tools array changes and the whole tool+system cache prefix is invalidated and rewritten at 1.25×.**
- **Optimization:** (a) Confirm tool ordering is stable (sorted) so cache hits survive; (b) consider sending the *full* tool set with a stable order and relying on the model to ignore unused tools only if token budget allows — or accept that group unlock costs one cache-write. Document the cache-invalidation-on-unlock cost. Lower priority than PERF-001.
- **Expected impact:** Avoids surprise cache-rewrite (1.25× on full system+tools prefix) on the first turn after each skill/group unlock.
- **Effort:** S (verify ordering) / M (restructure).

### PERF-003 — `.og` 3-pass token claim is real but narrow; retrieval misroutes spend the savings
- **Severity:** Medium · **Area:** LLM-Token / Scalability
- **Bottleneck:** Benchmark (`docs/BENCHMARK.md`) reports **97.6% token savings vs dump-all** on the 53-node pack — *measured*, CI-gated ≥80%. But `docs/strategy/02-object-graph-eval.md` §2.3 (prior in-repo eval) shows retrieval P@1 ≈ 0.23–0.77 on paraphrased queries and the engine has **no semantic ranking** (`engine.py:360-372` bidirectional substring scan).
- **Root cause:** The benchmark query is the first indexed keyword (oracle); on realistic paraphrases the keyword engine routes to the wrong node, and the 3-pass protocol then peeks/loads noise — net token *cost*, not saving. Also (§3.1b) below ~30k library tokens, **cached dump-all is ~3× cheaper** than 3-pass because of the 3 extra round-trips + PERF-001 re-billing. The shipped library is ~9k tokens — *below* the crossover.
- **Optimization:** The savings claim holds **only** for libraries ≳30k tokens AND when retrieval lands correctly. For the current bundled pack the 3-pass model is likely *more* expensive per task than cached dump-all (see strategy doc worked examples). Either (a) add the hybrid BM25+embedding retrieval already scoped in that doc (ChromaDB dep is present), or (b) for small libraries, default to a cached dump and switch to 3-pass past a size threshold. Fixing PERF-001 also materially improves the 3-pass economics.
- **Expected impact:** Avoids the headline benchmark being attackable; makes per-task cost actually win at the scale the format targets.
- **Effort:** L (retrieval v2) — but verdict/documentation is S.

### PERF-004 — Token counting is free (good); estimator is len/4 (acceptable)
- **Severity:** Low (positive finding) · **Area:** LLM-Token
- **Detail:** `cost.py:80-101` records usage straight from the API response (`llm.py:273-279`), including cache read/write multipliers (0.1× / 1.25×). No count-tokens network call on the hot path. The `len/4` estimator (`memory.py` `estimate_token_count`) is used only for *internal* compaction/clear decisions, not billing. This is the right design; no change needed.
- **Effort:** —

---

## Concurrency

### PERF-005 — SSE stream endpoints poll a synchronous, lock-taking read on a 50 ms loop inside the async event loop
- **Severity:** High · **Area:** Concurrency / Backend / API-latency
- **Bottleneck:** `fastapi_app.py:436-469` (`/sessions/{id}/stream`) and `:620-664` (`/v1/chat/completions` stream). Both are `async def` generators that call the **synchronous** `control_plane.read_session_events(...)` every `0.05s` while a turn is active.
- **Root cause:** `read_session_events` (`control_plane.py:338-368`) acquires `self._connections_lock`, then calls `_read_events` → `RuntimeReadModels.read_events_since` which acquires its own `_lock` and **linearly scans up to 2000 events** (PERF-006). This blocking, lock-contended work runs *directly on the asyncio event loop* (no `run_in_threadpool`/`to_thread`). With many concurrent SSE clients each spinning at ~20 polls/s, the loop spends its time in synchronous list scans under locks, starving all other requests.
- **Optimization:** (a) Wrap the read in `await anyio.to_thread.run_sync(...)`, or (b) replace polling with an `asyncio.Queue`/`Condition` pushed by `append_event` so the generator awaits new events instead of busy-polling. (b) is the real fix and removes the 50 ms spin entirely.
- **Expected impact:** Removes O(clients × 20/s × n_events) wasted scans and unblocks the event loop; large improvement under multi-client streaming.
- **Effort:** M.

### PERF-006 — `read_events_since` is an O(n) linear scan per poll over a 2000-element list
- **Severity:** Medium · **Area:** Concurrency / Scalability
- **Bottleneck:** `runtime_read_models.py:422-429` — `fresh = [snap for snap in self._events if snap.sequence > after]` scans the entire (up to 2000) list on every call; `append_event` trims via full slice copy `self._events = self._events[-2000:]` (line 246-247).
- **Root cause:** Sequences are strictly monotonic (line 238), so the cutoff index is findable in O(log n) via `bisect`, but the code does a full scan. Combined with PERF-005's 50 ms polling this is the per-poll hot path. The slice-trim on every append past 2000 is also O(n) churn.
- **Optimization:** Use `collections.deque(maxlen=2000)` for O(1) appends with auto-eviction, and `bisect_right` on a parallel sequence list (or binary-search since sequences are sorted) to slice from `after`.
- **Expected impact:** Per-poll cost drops from O(2000) to O(log n + result size); removes append churn.
- **Effort:** S.

### PERF-007 — Per-session ThreadPoolExecutor (4 workers each) — N sessions × 4 threads
- **Severity:** Medium · **Area:** Concurrency / Scalability / Memory
- **Bottleneck:** `loop.py:320-323` each `ExecutionLoop` creates its own `ThreadPoolExecutor(max_workers=4)`. With the documented 100-session process cap that is up to **400 tool-pool threads** plus the resident job worker per process, plus per-session MCP server sets.
- **Root cause:** Tool parallelism is correct and bounded per loop, but pools are not shared across sessions. Thread stacks (~512KB–1MB each) and scheduler overhead scale linearly with session count.
- **Optimization:** Share a single bounded process-wide tool pool (e.g. 16–32 workers) keyed via `ProcessSharedServices`, or create the per-session pool lazily on first parallel batch and tear it down on idle. Confirm `_tool_pool.shutdown` (line 424) is always called on session close to avoid leaks.
- **Expected impact:** Caps thread/memory growth under many concurrent sessions; the 100-session cap becomes far cheaper.
- **Effort:** M.

### PERF-008 — Per-session `chat()` serialized by a blocking `threading.Lock`; correct, but a head-of-line latency wall
- **Severity:** Low (correct by design) · **Area:** Concurrency
- **Detail:** `orchestrator.py` `_chat_lock` serializes turns per session (confirmed in `docs/uat/16-perf-concur.md` UC-PERF-001) and emits `session.busy`. The HTTP route `post_message` is correctly a sync `def` (`fastapi_app.py:403`) so it runs in Starlette's threadpool, not the event loop — so the lock does **not** block the loop (contrary to a naive reading). The only cost is intended: a second caller to the *same* session blocks until the first turn (potentially 10–60s) finishes. Acceptable. Note Starlette's default threadpool is 40 workers — with >40 concurrent in-flight turns, additional `post_message` calls queue at the threadpool, an upstream throttle worth documenting.
- **Effort:** — (doc only).

### PERF-009 — FTS5 memory-index `refresh()` is a full DELETE+rebuild, racing concurrent `search()`
- **Severity:** Medium · **Area:** Memory-Vault / DB / Concurrency
- **Bottleneck:** `memory_index.py:134-148` — `refresh()` does `DELETE FROM mem_fts; DELETE FROM mem_nodes;` then re-inserts **all** nodes in one connection. A concurrent `search()` (separate connection, line 172) during that window sees an empty index (TOCTOU; confirmed UC-PERF-002 PARTIAL).
- **Root cause:** Full rebuild instead of incremental upsert; no transaction isolation visible to readers (WAL lets readers proceed but they read the mid-rebuild state once the writer commits the deletes). Also re-parses every `memory/*.og` file on each refresh (`rglob` + parse all, lines 97-132) — O(all memory files) cost regardless of what changed.
- **Optimization:** Incremental refresh keyed on file mtime (only re-index changed files); wrap delete+insert so readers never observe the empty state (single transaction is already used, but the empty-result window is between the writer's commit and re-insert if split — keep delete+insert atomic, which it is here, so the real cost is the full re-parse). Add mtime-based skip.
- **Expected impact:** Avoids re-parsing the whole memory graph on every refresh; eliminates empty-result window at scale.
- **Effort:** M.

### PERF-010 — Sub-agent delegation duplicates runtime objects per child loop
- **Severity:** Medium · **Area:** Concurrency / Memory / Scalability
- **Bottleneck:** `delegate_task` spins a scoped `ToolRegistry` + child `ExecutionLoop` (per CLAUDE.md), each with its own `_tool_pool` (PERF-007) and tool-schema build. Parallel delegation (`tests/test_parallel_delegation.py`) multiplies this.
- **Root cause:** Isolation is the goal (clean context) but heavy objects (registry schemas, pools) are rebuilt per child.
- **Optimization:** Reuse the shared tool pool; build child tool schemas by filtering the parent's cached `_schemas` rather than re-registering.
- **Expected impact:** Lower memory + faster delegation spin-up under parallel fan-out.
- **Effort:** M.

---

## Backend / API latency

### PERF-011 — Read endpoints are `async def` but call blocking control-plane work directly (event-loop stalls)
- **Severity:** High · **Area:** Backend / API-latency / Concurrency
- **Bottleneck:** `fastapi_app.py` — `get_memory` (:815), `get_og_graph` (:828), `get_skills` (:810), `get_capabilities` (~:374) are `async def` and call synchronous `control_plane.*` methods that do real work (Chroma scans, token-estimate loops) without `to_thread`. Contrast `create_session` which *does* use `await anyio.to_thread.run_sync`.
- **Root cause:** Inconsistent offloading. Any blocking call inside an `async def` handler runs on the event loop and stalls every other in-flight request for its duration.
- **Optimization:** Make these handlers `def` (so Starlette runs them in the threadpool) **or** wrap the call in `await anyio.to_thread.run_sync(...)`. The `def`-vs-`async def` choice for `post_message` (PERF-008) is already correct — apply the same discipline to the read endpoints.
- **Expected impact:** Prevents single slow snapshot from blocking the whole gateway; removes a class of tail-latency spikes.
- **Effort:** S.

### PERF-012 — `/memory` fetches ALL documents from 3 Chroma collections every request, no pagination/cache
- **Severity:** High · **Area:** Memory-Vault / Scalability / API-latency
- **Bottleneck:** `orchestrator.py:741-760` `snapshot_memory_facts` loops `core_memory`, `user_profile`, `agent_profile` and calls `coll.get()` (no limit) on each — pulls the entire collection into memory and serializes it.
- **Root cause:** `core_memory` is explicitly **unbounded** (`memory_vault.py:58-59`). At 10k+ facts every `/memory` call materializes all of them; the web UI may poll this. `user_profile`/`agent_profile` are capped at 25 so they're fine; `core_memory` is the risk.
- **Optimization:** Paginate `core_memory` (limit + offset/cursor) and lazy-load; cache the snapshot server-side with a short TTL invalidated on write. Add `Cache-Control` headers.
- **Expected impact:** `/memory` latency goes from O(all facts) to O(page); avoids large-vault OOM/timeout.
- **Effort:** M.

### PERF-013 — `/og/graph` recomputes per-pass token estimates over all nodes on every request, no cache
- **Severity:** Medium · **Area:** Caching / Backend
- **Bottleneck:** `orchestrator.py:~802-827` `snapshot_og_graph` iterates all nodes, calling `estimate_tokens()` 3×/node (index/dense/full) on each request; served by an unoffloaded `async def` (PERF-011).
- **Root cause:** Skill graph is static within a session but recomputed per call; the UI graph view can re-fetch frequently.
- **Optimization:** Compute once at boot (or on skill upsert) and cache; invalidate on `load_file`/`upsert_skill`. Add `ETag`.
- **Expected impact:** Turns a 100+-node × 3-pass scan per request into a cache read.
- **Effort:** S.

### PERF-014 — No HTTP caching headers on static-ish snapshot endpoints
- **Severity:** Low · **Area:** Caching
- **Bottleneck:** `/skills`, `/og/graph`, `/capabilities`, `/authoring/targets` (`fastapi_app.py:810-840`) return no `Cache-Control`/`ETag`, so clients (and the SPA) can't conditionally GET.
- **Optimization:** Add `Cache-Control: max-age=15–60` or `ETag` to data that changes only on explicit mutation.
- **Expected impact:** Fewer redundant recomputations under a chatty UI.
- **Effort:** S.

---

## Database

### PERF-015 — SessionStore funnels ALL reads and writes through one connection + one global lock
- **Severity:** High · **Area:** DB / Concurrency / Scalability
- **Bottleneck:** `session_store.py:49-51` — a **single** `sqlite3.connect(..., check_same_thread=False)` guarded by **one** `threading.Lock` (`self._lock`) wrapping every method. WAL+`busy_timeout=5000` are set, but the application-level lock means **reads serialize behind writes** even though WAL would allow concurrent readers.
- **Root cause:** Single shared connection forces the global lock for thread-safety, defeating WAL's reader/writer concurrency. Every `log_message` (called after each user input and assistant response) takes the lock and commits — under many concurrent sessions this is a contention point and a write-amplification source.
- **Optimization:** Use a connection-per-thread (or a small pool) so WAL readers run concurrently; reserve a lock only for writes, or drop the lock entirely with thread-local connections. Batch `log_message` commits (the resident job worker already exists for deferred writes).
- **Expected impact:** Concurrent session reads (resume, search, recent) stop blocking on unrelated writes; lower turn-tail latency at scale.
- **Effort:** M.

### PERF-016 — Cross-session search is `LIKE %query%` — full table scan, unindexable
- **Severity:** Medium · **Area:** DB / Scalability
- **Bottleneck:** `session_store.py:273-281` — `WHERE m.content LIKE ?` with leading wildcard. The code comment at `:85` even notes the content index was removed because "leading-wildcard LIKE cannot use a B-tree index."
- **Root cause:** Substring search over all messages = O(total messages). The docstring scopes this to "~1000s of sessions"; beyond that it degrades linearly. An FTS5 table over `messages.content` already exists as a pattern elsewhere (`memory_index.py`) but isn't applied to the session transcript.
- **Optimization:** Add an FTS5 virtual table mirroring `messages.content` (porter tokenizer) and route `search_sessions` through `MATCH` + `bm25`. Reuses the exact pattern in `memory_index.py`.
- **Expected impact:** Sub-linear search; usable at 100k+ messages.
- **Effort:** M.

### PERF-017 — SQLiteJobQueue opens and closes a fresh connection per operation
- **Severity:** Medium · **Area:** DB / Concurrency
- **Bottleneck:** `jobs/queue.py:566-577` `_conn()` does `sqlite3.connect(...)` + `close()` on **every** enqueue/get_pending/update_status. The resident worker polls `get_pending` every `poll_interval=0.5s` (`:825`), so the queue connects/disconnects at least twice a second forever, plus once per enqueue and per status transition.
- **Root cause:** Connect/close per call avoids cross-thread sharing but adds per-call open + WAL setup cost and file-handle churn. No PRAGMA WAL/busy_timeout is set on these short-lived connections, so contention with other writers risks immediate "database is locked".
- **Optimization:** Hold a persistent connection (like SessionStore) guarded by the existing `_lock`, or a thread-local connection in the worker thread; set `journal_mode=WAL` + `busy_timeout`. The 0.5s poll is also a busy-ish idle loop — fine, but the connect-per-poll is the waste.
- **Expected impact:** Removes ~2 connect/close cycles per second of idle overhead + per-job churn; fewer lock errors.
- **Effort:** S.

### PERF-018 — `delete_sessions_before` builds an unbounded `IN (?,…)` placeholder list
- **Severity:** Low · **Area:** DB
- **Bottleneck:** `session_store.py:414-423` — collects all matching ids then `DELETE … WHERE id IN (<n placeholders>)`. SQLite caps bound variables at 999 (default `SQLITE_MAX_VARIABLE_NUMBER`); a retention purge over >999 old sessions raises `too many SQL variables`.
- **Optimization:** Delete with a correlated subquery (`WHERE started_at < ?`) directly, or chunk the id list into batches of ≤900.
- **Expected impact:** Retention cleanup works at scale instead of erroring.
- **Effort:** S.

---

## Memory-Vault / Memory consumption

### PERF-019 — ChromaDB uses the default embedding model — heavy cold-start + per-query CPU
- **Severity:** High · **Area:** Memory-Vault / Startup
- **Bottleneck:** `memory_vault.py:121-148` — `PersistentClient` + `get_or_create_collection` with **no `embedding_function`** passed (grep for `embedding_function`/`SentenceTransformer`/`DefaultEmbeddingFunction` = **0 matches** in `src`). Chroma therefore uses its default `all-MiniLM-L6-v2` ONNX model, which it **downloads on first use** (~80MB) and loads into memory.
- **Root cause:** First `semantic_search`/`upsert` triggers model download + ONNX runtime init — a multi-second (or network-dependent) stall, and the model resides in RAM for the process. Every `query()`/`upsert()` runs CPU inference to embed text. This is the dominant memory-recall latency and a silent first-run network dependency (breaks "offline mode" claims until cached).
- **Optimization:** (a) Pin and pre-warm the embedding model at boot in the background job queue so the first user query isn't cold; (b) make the model explicit/configurable and bundle/cache it for offline; (c) document the RAM cost. For large vaults, the per-query embed + HNSW search is the real recall cost — consider the FTS5-only path (`memory.graph`) as the default lightweight tier.
- **Expected impact:** Removes a multi-second first-recall stall and a hidden network dependency; clarifies RAM footprint per process.
- **Effort:** M.

### PERF-020 — `core_memory` is unbounded; recall and `/memory` cost grow without limit
- **Severity:** Medium · **Area:** Memory-Vault / Scalability
- **Bottleneck:** `memory_vault.py:58-59` documents core_memory as "Unbounded." HNSW index size, RAM, and query latency all grow with fact count; `/memory` reads all of it (PERF-012); `get_delegation_stats` (`:701-712`) and similar `.get()`-all patterns load entire collections to compute aggregates.
- **Root cause:** No eviction/TTL/summarization policy on workspace facts; aggregates fetch all rows rather than using counts/filters.
- **Optimization:** Add a soft cap + LRU/age-based compaction (summarize old facts), and compute stats with Chroma `where`/`count` rather than fetching every document.
- **Expected impact:** Bounds long-lived RAM and recall latency; large vaults stay responsive.
- **Effort:** M.

### PERF-021 — `delete_fact` opens a second SQLite connection to FTS5 on every delete
- **Severity:** Low · **Area:** Memory-Vault / DB
- **Bottleneck:** `memory_vault.py:198-223` `_delete_from_fts5` connects+closes a fresh sqlite connection per fact deletion (reaching into `MemoryIndex._db_path`).
- **Optimization:** Route deletes through a `MemoryIndex` method that reuses its connection rather than per-delete connect/close.
- **Effort:** S.

---

## Startup

### PERF-022 — CLI eagerly imports the entire runtime (TUI, orchestrator, Anthropic, Chroma) at module load
- **Severity:** High · **Area:** Startup
- **Bottleneck:** `cli/main.py:28-64` imports `run_tui` (Textual), `boot_agent`/`AgentSession` (orchestrator), control plane, workspace, plus all command modules at top level. `orchestrator` transitively imports the LLM client, ChromaDB, and skill engine. So **every** invocation — `hachi --help`, `hachi doctor`, `hachi skill benchmark` — pays the full import cost of Textual + Anthropic SDK + ChromaDB.
- **Root cause:** No lazy import boundary between the Typer command surface and the heavy runtime. Textual and chromadb are large imports (hundreds of ms each, cold).
- **Optimization:** Move heavy imports inside the command functions that need them (Typer supports this cleanly). `--help` and lightweight subcommands should not import the TUI or Chroma.
- **Expected impact:** CLI startup (especially `--help`/`doctor`/non-agent commands) drops from seconds to sub-second; better UX and faster CI.
- **Effort:** M (mechanical but touches many imports).

### PERF-023 — Skill engine eagerly parses every `.og` file at boot
- **Severity:** Medium · **Area:** Startup / Scalability
- **Bottleneck:** `og_engine/engine.py:465-477` `load_directory` globs `**/*.og` and `load_file`s each one synchronously at boot; called per session boot. Parses + indexes all nodes into `_nodes` + keyword maps.
- **Root cause:** Eager full-library parse. Fine for the 53-node pack (~9k tokens, fast), but the project's *own* roadmap targets 200–1000 skill libraries; at that scale boot parse becomes a multi-second per-session cost (and is duplicated per session unless shared via `ProcessSharedServices`).
- **Optimization:** The 3-pass design only needs the `::index`/`::dense` at boot — defer `::full` parsing to resolve-time (lazy node body load). Share the parsed engine across sessions (singleton already partially does this). Index incrementally on file mtime.
- **Expected impact:** Boot scales sub-linearly with library size; large packs don't blow up per-session boot.
- **Effort:** M.

### PERF-024 — `ProcessSharedServices` singleton ignores config after first init (stale heavy objects)
- **Severity:** Medium · **Area:** Startup / Scalability
- **Bottleneck:** `shared_services.py:79-85` returns the existing singleton without comparing `config`/`brain_dir` (confirmed ADV-024, `docs/uat/16-perf-concur.md`). Positive perf effect: heavy objects (Chroma client, skill engine, MCP manager) are reused across sessions — good. Negative: a session with different memory settings silently gets the first session's backend.
- **Root cause:** Fast-path reuse with no key check.
- **Optimization:** Key the singleton on `(brain_dir, memory.backend, account_id)`; reuse only on match, else build a second instance (or warn). Preserves the reuse win while fixing correctness.
- **Expected impact:** Keeps the shared-services memory/latency win without the silent-config bug.
- **Effort:** S.

---

## Frontend (untracked `frontend/`)

### PERF-025 — Chat transcript renders every message unvirtualized; whole `messages` array re-rendered on each SSE delta
- **Severity:** Critical · **Area:** Frontend
- **Bottleneck:** `frontend/src/routes/conversation.tsx:746-754` maps over all `messages` with no virtualization (no `react-window`/`@tanstack/react-virtual` in `package.json`). Each `text.stream.delta` SSE event calls `appendTextDelta` (`lib/data/domains/chat.ts:134-166`) which does `msgs.map(m => …)` — a **full array remap** that replaces the `messages` reference, so `useStore(s => s.messages)` re-renders the **entire** message list on every token.
- **Root cause:** (a) no list virtualization; (b) SSE deltas mutate the whole array (new reference) many times/second; (c) `MessageItem`/`ActionCardRow`/`RoutineCard` (`conversation.tsx:89,248,457`) are not `React.memo`'d and use inline style objects, so even with narrow selectors every item re-renders.
- **Optimization:** (1) Virtualize the transcript (`@tanstack/react-virtual`); (2) batch/throttle `text.stream.delta` (e.g. coalesce per animation frame / 50ms) before hydrating; (3) update only the streaming message (keyed map / per-message store slice) instead of remapping the array; (4) `React.memo` the item components + hoist inline styles to module constants.
- **Expected impact:** Long sessions (100s–1000s of messages) go from janky/locking to smooth; streaming stops triggering N full-list re-renders per second. Largest frontend win.
- **Effort:** M.

### PERF-026 — `Markdown` re-parses full message text on every render (not memoized)
- **Severity:** Medium · **Area:** Frontend
- **Bottleneck:** `frontend/src/components/ui/Markdown.tsx:286-302` — `parseBlocks(text)` (regex-based block + inline parse) runs on **every** render with no `React.memo` and no `useMemo`. During streaming, the streaming message re-renders per delta and re-parses the *entire accumulated* text each time → O(n²) over the message as it grows.
- **Root cause:** No memoization keyed on `text`.
- **Optimization:** `export default React.memo(Markdown)` and `useMemo(() => parseBlocks(text), [text])`. Combined with PERF-025's delta throttling this removes the quadratic re-parse.
- **Expected impact:** Eliminates a CPU hot spot on long streamed assistant messages.
- **Effort:** S.

**Frontend positives (confirmed):** dependencies are lean (`package.json`: react, react-dom, `@tanstack/react-router`, zustand — no heavy chart/markdown/date/icon libs); `vite.config.ts` has `autoCodeSplitting: true` so routes are split; zustand selectors are mostly narrow (per-field, not whole-store). No bundle analyzer configured (minor).

---

## Top optimization opportunities — ranked by impact / effort

| # | ID | Title | Severity | Impact | Effort |
|---|----|-------|----------|--------|--------|
| 1 | PERF-001 | Cache message-history prefix (stop re-billing loaded skills/tool results) | Critical | Very High | M |
| 2 | PERF-025 | Virtualize transcript + throttle SSE deltas + memo items | Critical | Very High | M |
| 3 | PERF-011 | Offload blocking work out of `async def` read handlers | High | High | S |
| 4 | PERF-006 | `deque` + bisect for event polling (drop O(n) scan) | Medium | High | S |
| 5 | PERF-005 | Push-based SSE instead of 50 ms poll of locked sync read | High | High | M |
| 6 | PERF-019 | Pre-warm / pin Chroma embedding model (cold-start + offline) | High | High | M |
| 7 | PERF-022 | Lazy heavy imports in CLI (sub-second `--help`/`doctor`) | High | High | M |
| 8 | PERF-012 | Paginate + cache `/memory` (unbounded core_memory) | High | High | M |

---

## Large-scale readiness verdict

**Not yet ready for large-scale usage (many sessions / large transcripts / large vault / many skills) without targeted work — but the foundations are sound.** The per-turn LLM economics are quietly undermined by PERF-001: loaded skills and tool results sit in uncached message history and are re-billed at full price every turn, which means the headline `.og` token-saving claim (PERF-003) holds only above ~30k-token libraries *and* only when the keyword retriever lands correctly — neither is true for the shipped 9k-token pack, where cached dump-all is cheaper. At scale the gateway has three compounding pressure points: SSE streams busy-poll a synchronous, lock-taking, O(n) event read on the asyncio loop (PERF-005/006/011); the SessionStore serializes all reads behind one lock/connection and searches via unindexable `LIKE` (PERF-015/016); and memory is unbounded with a heavy default embedding model that cold-starts and grows RAM/latency with fact count (PERF-019/020). The frontend will lock up on long transcripts because it neither virtualizes the message list nor throttles streaming deltas (PERF-025/026). Startup is heavier than it should be because the CLI eagerly imports the entire runtime (PERF-022) and every session re-parses the full skill library (PERF-023). None of these are architectural dead-ends: concurrency primitives are correct (per-session lock, bounded tool pools, durable job queue with WAL+busy_timeout on SessionStore), the cost tracker is free and accurate, and the shared-services singleton already amortizes heavy objects. The fixes are mostly S/M effort and concentrated in ~8 hotspots; landing the top-8 table would move the verdict from "single-user / small-team" to "comfortably multi-session with large transcripts and a growing vault."
