# Agent 5 — Performance & Scalability Audit (2026-06-20)

Repo: `D:\OpenGigantic\hachi` · Branch `main` · Auditor: Performance & Scalability

Scope: verify the two merged perf fixes (BM25 bisect, persistent job-queue connection)
and re-assess the remaining 10 prior perf findings (compaction, SessionStore batching,
MemoryVault/ChromaDB, AnthropicClient duplication, SSE poll, cache breakpoints, etc.).

---

## Findings table

| ID | Title | Severity | Verdict | File:line |
|----|-------|----------|---------|-----------|
| PERF-01 | BM25 prefix search O(log V) via bisect | High | **FIXED** | `og_engine/engine.py:436-471, 750-790` |
| PERF-02 | Job-queue persistent connection | High | **FIXED** | `jobs/queue.py:552-595` |
| PERF-03 | Sliding-window compaction SYNC LLM call in hot loop | High | **STILL-OPEN** | `core/memory.py:831-832, 950-1024` |
| PERF-04 | SessionStore commit-per-write | Medium | **STILL-OPEN** | `core/session_store.py:246-252` |
| PERF-05 | MemoryVault ChromaDB cold-start (5 serial collections) | High | **STILL-OPEN** | `core/memory_vault.py:121-148` |
| PERF-06 | MemoryVault.close() swallows / no lock | Medium | **STILL-OPEN (partial)** | `core/memory_vault.py:157-185` |
| PERF-07 | Multiple AnthropicClient instantiations per turn | Medium | **STILL-OPEN** | `core/memory.py:204,339,924,1012`; `tools/memory_tools.py:106` |
| PERF-08 | SSE 50 ms busy-poll | Medium | **PARTIAL** | `gateway/fastapi_app.py:515,709` |
| PERF-09 | Prompt-cache breakpoint churn | Low | **PARTIAL / by-design** | `core/llm.py:230-298` |
| PERF-10 | SkillEngine.record_use disk write per load | Low | **FIXED** | `og_engine/engine.py:1651-1686` |
| PERF-11 | Per-query prefix-set recomputation in BM25 scoring | Low | **NEW** | `og_engine/engine.py:751-764` |
| PERF-12 | `_find_safe_sliding_boundary` O(N²·B) scan | Low | **NEW** | `core/memory.py:842-903` |

Severity counts (current state): **High: 3** (1 fixed, 2 open) · **Medium: 4** (open/partial) ·
**Low: 5** (1 fixed, 2 partial, 2 new). Net open/regressed/new: **9**.

---

## PERF-01 — BM25 prefix search now O(log V) — FIXED (was HIGH-014)

**Root cause (prior):** `search()` did a full `O(V)` dict-key scan per query token to find
prefix matches, where V = vocabulary size. With N docs × T query-tokens this was O(T·V) per
search.

**Verification:** A sorted token list `_sorted_tokens` is maintained (`engine.py:371`),
marked dirty on `load_file` (`:695`), and lazily rebuilt (`_rebuild_sorted_tokens`, `:426`).
`_prefix_matched_qids` (`:436-471`) uses `bisect.bisect_left(tokens, qt[:_MIN_TOKEN_LEN])`
to enter the range, then iterates forward and **breaks once `tok >= qt`** — correct, because
any prefix of `qt` must sort `< qt`. Result set is identical to the naïve scan: the same
`len(tok) >= _MIN_TOKEN_LEN and qt.startswith(tok)` predicate is applied per candidate.

Complexity: entry is O(log V); the forward scan is bounded by the number of tokens sharing
the 2-char prefix (small, ≈ vocabulary fan-out), not full V. **The fix is real and
results-preserving.**

**Caveat (PERF-11):** the rebuild is amortised but the whole sorted list is re-sorted
(O(V log V)) on the *first* search after any `upsert_skill`/`import_skill_md` hot-load,
since each mutates `_token_index` and sets `_sorted_tokens_dirty`. Acceptable for boot
(one rebuild) but a chatty authoring session pays a re-sort per write→search cycle.

**Verdict: FIXED.** Expected improvement at 5,000 nodes (~15k vocab): per-search prefix
cost ~10⁴ → ~14 comparisons + small fan-out scan; multi-ms → sub-ms.

---

## PERF-02 — Job-queue persistent connection — FIXED (was HIGH-016)

**Root cause (prior):** every queue op did `sqlite3.connect()/close()`. The
`ResidentWorkerThread` polls every 0.5 s (`queue.py:846`) and `get_pending` ran each poll,
so ≥2 connect/close cycles per second even while idle.

**Verification:** `SQLiteJobQueue.__init__` opens one connection (`:557`,
`check_same_thread=False`) guarded by an `RLock` (`:556`). `_conn()` (`:579-594`) is a
context manager yielding the persistent connection under the lock, committing on success /
rolling back on exception — same transactional semantics as before, no per-call open/close.
`shutdown()` closes it (`:823-830`). RLock correctly allows the nested `enqueue → _conn`
acquisition.

**Verdict: FIXED.** Eliminates ~2 conn/s of syscall + page-cache churn; idle CPU drops and
write latency under contention improves (single warm connection vs cold WAL reopen).

**Minor note:** WAL is not explicitly enabled on the jobs DB (no `PRAGMA journal_mode=WAL`
in `_init_db`, unlike `session_store.py:53`). Under a single persistent connection this is
low-impact, but a reader (TUI `jobs/active`) opening a *separate* connection could see
default rollback-journal locking. Low severity.

---

## PERF-03 — Compaction SYNC LLM call in the hot loop — STILL-OPEN (was HIGH-015)

**Status nuance:** there are *two* compaction paths and only one was moved off the hot path.

1. **Skill-switch compaction** (`apply_pending_compaction`, `:453-506`) — invoked from
   `prepare_for_turn` (`:650`) which the loop calls each iteration (`loop.py:673`). This path
   builds the summary by **string truncation only** (`:477-483`), no LLM call. Good.

2. **Sliding-window compaction** (`_sliding_window_compact`, `:749-840`) — invoked from
   `add_message` (`:434-435`) whenever `len(history) > max_active_messages` (default 30).
   `add_message` is called **inside the turn** at `loop.py:350, 797, 900`. This path calls
   `_generate_structured_summary` (`:832`) which is **explicitly synchronous** — comment at
   `:831` says *"synchronous — we need the result now"* — and issues a blocking
   `AnthropicClient().generate()` (`:1012-1021`) with up to an 8 KB transcript.

**Impact under load:** on any turn that pushes history past 30 messages (long tool-heavy
sessions, sub-agent transcripts), the user-visible turn **stalls for a full extra LLM
round-trip** (typically 1–4 s, Sonnet-class latency, no timeout set on this client) before
the real model call even starts. This is squarely on the critical path and unbounded by a
timeout (unlike the fact-extraction clients which pass `timeout=30.0`). The fact-extraction
half of the same method *is* queued (`_flush_facts_to_vault` via job queue, `:808-824`), but
the structured summary is not.

**Optimization:** Move `_generate_structured_summary` to the job queue and insert a cheap
deterministic placeholder checkpoint synchronously (the fallback string at `:1024` already
exists), then swap in the LLM summary when the job completes — or compute the checkpoint
lazily. At minimum, pass `timeout=` so a slow API can't hang the turn.

**Expected improvement:** removes a 1–4 s blocking stall from ~every long turn; turn
latency on long sessions drops to the single real model round-trip.

**Verdict: STILL-OPEN.** (Prior remediation addressed only the skill-switch path / fact
flush, not the sliding-window structured summary.)

---

## PERF-04 — SessionStore commit-per-write — STILL-OPEN (was MED-001 / E5-T4)

**Root cause:** `log_message` (`:246-252`) does `INSERT … ; commit()` under the lock on
*every* user/assistant/tool message. With `synchronous=NORMAL` + WAL each commit still
forces a WAL frame write + fsync-equivalent checkpoint pressure. A tool-heavy turn logs
many messages, each its own transaction.

**Confirmed UNMERGED:** `git log -- core/session_store.py` shows the last touch is `#113`;
no batching commit landed. The branch `fix/be-1-gateway-core` (BE-1 / PR #128, DRAFT)
exists but is not merged, matching the reported FK-test regression. Every state-mutating
method in the file still calls `self._conn.commit()` inline (15 call sites).

**Why the naive batch broke FK tests:** deferring the `sessions` INSERT (or `messages`
INSERT) while `PRAGMA foreign_keys=ON` (`:52`) means a `messages` row can reference a
not-yet-committed `session_id`, or tests that read immediately after a write see nothing.
**Correct fix:** keep `start_session` synchronous (so the FK parent exists), and batch only
`log_message` — accumulate inserts and `commit()` on a size/time threshold (e.g. every 16
rows or 250 ms) plus a flush in `end_session`/`shutdown`. This preserves FK integrity
(parent already committed) while collapsing N commits into ⌈N/16⌉.

**Impact under load:** at high message rates, commit fsync dominates `log_message` latency;
since it runs in the calling thread (`:235` docstring), it adds per-message latency to the
turn even with WAL.

**Verdict: STILL-OPEN.** Expected improvement: ~10–16× fewer transactions on tool-heavy
turns; removes commit fsync from the per-message path.

---

## PERF-05 — ChromaDB cold-start: 5 serial `get_or_create_collection` — STILL-OPEN (was HIGH-021 / MED-023)

**Root cause:** `MemoryVault.__init__` (`:121-148`) creates a `PersistentClient` then calls
`get_or_create_collection` **five times serially** (`core_memory`, `user_profile`,
`agent_profile`, `delegation_log`, `memory_quarantine`). Each `get_or_create` on a cold
Chroma triggers SQLite metadata I/O and, for HNSW collections, index load. ChromaDB import
itself (`:118`) pulls a heavy dependency graph (onnxruntime/tokenizers for the default
embedder) on first construction.

**Impact:** this is a dominant chunk of **startup time** and is on the boot path before the
first turn can run. SkillEngine's optional embedding index (`engine.py:843-882`) opens a
*sixth* Chroma collection when `HACHI_EMBEDDING_RANK=1`. None of this is lazy: the vault is
constructed eagerly at session boot.

**Optimization:**
- Lazily create the rarely-hit collections (`delegation_log`, `memory_quarantine`,
  `agent_profile`) on first use rather than at boot — only `core_memory` + `user_profile`
  are needed for the first turn (snapshot injection).
- Defer the `import chromadb` (`:118`) — it is already deferred elsewhere in the codebase
  (e.g. `engine.py:853`), so the vault should follow the same pattern and import inside the
  method, gated on first real need.
- Consider a warmup job on the queue so the first user turn doesn't pay HNSW load.

**Verdict: STILL-OPEN.** Expected improvement: defers ~3 of 5 collection opens + the
chromadb import out of cold boot; meaningfully cuts time-to-first-turn.

---

## PERF-06 — MemoryVault.close() — STILL-OPEN (partial) (was part of HIGH-021)

`close()` (`:157-185`) wraps everything in a bare `except Exception: pass` (`:184`), so a
failed WAL flush is silent. There is no lock around close vs concurrent
`upsert_*`/`semantic_search` from background jobs (fact-extraction runs on the worker thread
and touches `self.collection`). If shutdown races a queued `_flush_facts_to_vault`, the
collection refs are set to `None` (`:175-183`) while another thread may still call
`.upsert()`, producing an `AttributeError` that the job pipeline then surfaces as a
spurious `job.failed`. Severity Medium (shutdown-window race, not steady-state).

**Optimization:** guard `close()` and the upsert/query paths with a shared lock or a
`_closed` flag checked before collection use; stop the resident worker *before* closing the
vault in the shutdown sequence. **Verdict: STILL-OPEN.**

---

## PERF-07 — Multiple AnthropicClient instantiations per turn — STILL-OPEN (was MED-022)

**Root cause:** `core/memory.py` constructs a fresh `AnthropicClient` in four places
(`:204` fact-extraction, `:339` typed-extraction, `:924` flush-facts, `:1012` structured
summary) and `tools/memory_tools.py:106` does too. Each constructs a new
`anthropic.Anthropic(...)` (`llm.py:226`), which builds a new `httpx` connection pool — so
every background memory op opens fresh TCP/TLS instead of reusing the session's pooled
client. The orchestrator *does* cache one client on the loop (`fastapi_app.py:1594`,
accessed via `self.loop.llm_client`), but the memory subsystem ignores it.

**Impact:** per background job, a new TLS handshake to api.anthropic.com (~100–300 ms added
latency + no keep-alive reuse). On the structured-summary path (PERF-03) this compounds the
hot-loop stall. At scale (many concurrent sessions) it multiplies open connections and
defeats httpx pooling.

**Optimization:** inject the cached `llm_client` (or a dedicated cheap-model client) into
`MemoryManager` once at boot and reuse it for all four call sites; same for the memory tool.
The extraction prompts intentionally want a cheap model — pass a single shared
haiku-configured client rather than re-instantiating.

**Verdict: STILL-OPEN.** Expected improvement: removes 1 TLS handshake per background memory
op and per memory-tool call; connection reuse across jobs.

---

## PERF-08 — SSE 50 ms busy-poll — PARTIAL (was MED-002)

Two SSE loops poll on a fixed `await asyncio.sleep(0.05)`:
`fastapi_app.py:515` (runtime events) and `:709` (chat completion chunks). The runtime-event
loop is adaptive in spirit — it sleeps `0.05` only when events just arrived and falls back to
`poll_interval` when idle (`:515`) and bounds itself with `max_idle_cycles` — so an idle
stream is cheap. **But** both still busy-poll the control plane at 20 Hz while a turn is
*actively streaming*, and the chat loop (`:709`) is an unconditional 50 ms poll. With many
concurrent SSE clients this is 20 wakeups/sec/client against `read_session_events`, each
taking the control-plane lock.

**Optimization:** push-based delivery — have the control plane expose an `asyncio.Event` /
`asyncio.Queue` per session that the producer sets on new events, so the SSE coroutine
`await`s instead of polling. This removes fixed-interval wakeups and cuts streaming latency
(currently up to 50 ms of added per-chunk lag).

**Verdict: PARTIAL.** Idle cost is mitigated; active-stream busy-poll and the chat-chunk
50 ms poll remain. Medium severity at multi-client scale.

---

## PERF-09 — Prompt-cache breakpoint churn — PARTIAL / largely by-design (was LOW-016)

`_build_kwargs` (`llm.py:259-298`) places ephemeral `cache_control` on: system block,
last tool, and the **last ≤2 messages** (`_cache_message_indices`, `:230-243`). Because the
breakpoint moves to the new tail every turn, each turn writes a *new* cache segment
(`cache_creation` tokens) for the just-appended messages while reading the stable prefix.
This is the intended Anthropic incremental-caching pattern (4-breakpoint budget: system +
tools + 2 trailing), and `TokenUsage` correctly tracks `cache_creation` vs `cache_read`
(`:315-316`). The only residual churn: marking *two* trailing messages each turn can write a
slightly larger delta than necessary, and there's no cache on the `dynamic_system` block
(`:289-290`, intentionally — it changes per turn).

**Verdict: PARTIAL / acceptable.** The design is correct; the cost is the inherent
cache-write of new tail content, not a bug. Low priority. Optionally cache only the single
last message to shrink per-turn `cache_creation`.

---

## PERF-10 — SkillEngine.record_use disk write per load — FIXED (was LOW-015)

`record_use` (`:1651-1679`) is now **debounced**: writes flush at most once per
`_usage_flush_interval` (30 s) via a monotonic-clock check under `_usage_lock`
(`:1665-1679`), with `flush_usage()` (`:1681-1686`) for shutdown/tests. The first call after
boot still flushes so one-shot runs persist. Previously every `load_skill` rewrote the whole
JSON file. **Verdict: FIXED.** Expected improvement: O(loads) disk writes → O(loads / 30 s),
removing JSON serialize + write from the per-load path.

---

## PERF-11 — BM25 scoring recomputes prefix sets per query — NEW (Low)

In `search()` the candidate-gathering loop calls `_prefix_matched_qids(qt)` (`:755`), then
the code **recomputes the same sets again** into `prefix_qids_per_token` (`:762-764`). That
is two bisect scans per query token. The second computation memoizes for the scoring loop
(good), but the first pass at `:755` could reuse it. Minor; only matters for many-token
queries. **Fix:** compute `prefix_qids_per_token` once before the candidate loop and reuse.
**Verdict: NEW (Low).**

---

## PERF-12 — `_find_safe_sliding_boundary` is O(N²·B) — NEW (Low)

`_find_safe_sliding_boundary` (`memory.py:842-903`) walks the cut point backward one index
at a time, and `_is_safe(cut)` rescans the *entire* retained slice `history[cut:]` **twice**
(two full passes building `seen_tool_use_ids` then checking results). Worst case this is
O(N²) over messages × O(B) blocks. History is capped at `max_active_messages` (30), so the
absolute cost is small today, but the double-scan-per-step is wasteful and runs inside the
already-hot `_sliding_window_compact`. **Fix:** single backward pass tracking open tool_use
ids incrementally. **Verdict: NEW (Low).**

---

## Ranked top bottlenecks (current state)

1. **PERF-03 — sliding-window structured summary on the hot path (High).** A blocking,
   un-timeout'd LLM round-trip injected into long turns. Biggest user-visible latency hit;
   should be queued. *(~1–4 s removed per long turn.)*
2. **PERF-05 — ChromaDB cold-start, 5 serial collections + eager chromadb import (High).**
   Dominant slice of time-to-first-turn. Lazy/deferred creation + deferred import.
   *(Cuts ~3 collection opens + heavy import out of boot.)*
3. **PERF-07 — AnthropicClient re-instantiation in memory subsystem (Medium).** New TLS pool
   per background op; compounds PERF-03. Inject the cached client.
4. **PERF-04 — SessionStore commit-per-write (Medium).** Batch `log_message` only (keep
   session INSERT sync to preserve FKs). ~10–16× fewer commits on tool-heavy turns.
5. **PERF-08 — SSE active-stream 50 ms busy-poll (Medium).** Move to push/`asyncio.Event`;
   removes 20 Hz/client wakeups and up to 50 ms per-chunk lag.
6. **PERF-06 / PERF-11 / PERF-12 / PERF-09 (Low).** Shutdown-race hardening, duplicate
   bisect scan, O(N²) boundary scan, optional cache-breakpoint trim.

## Expected impact after optimization

- **Time-to-first-turn:** PERF-05 lazy collections + deferred chromadb import remove the
  largest cold-start cost; boot becomes dominated by config/secret resolution rather than
  Chroma index load.
- **Long-session turn latency:** PERF-03 (queue the summary) + PERF-07 (reuse client)
  remove a 1–4 s blocking stall plus a TLS handshake from any turn that crosses the
  sliding-window threshold — the single highest-value fix.
- **Write-path throughput:** PERF-04 batching collapses per-message commits; PERF-02
  (already merged) keeps the jobs DB warm.
- **Multi-client streaming:** PERF-08 push delivery removes fixed-interval wakeups, lowering
  CPU under many concurrent SSE clients and trimming per-chunk latency.
- **Search at scale:** PERF-01/PERF-10 (merged) are confirmed real — 5,000-node search stays
  sub-millisecond and skill loads no longer thrash the usage JSON.

## Verification of merged fixes — summary

- **BM25 O(log V) (PERF-01):** confirmed correct *and* results-identical to the naive scan
  (bisect entry + `tok >= qt` break + same `startswith` predicate). Real fix.
- **Job-queue persistent connection (PERF-02):** confirmed single connection in `__init__`,
  RLock-guarded `_conn()` with commit/rollback, closed in `shutdown()`. Real fix.
  (Minor: jobs DB lacks explicit WAL pragma.)
