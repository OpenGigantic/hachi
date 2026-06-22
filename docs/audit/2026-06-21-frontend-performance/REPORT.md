# Frontend Performance Audit — Hachi Web App

**Date:** 2026-06-21
**Scope:** `frontend/` (Vite + React 18 SPA)
**Branch:** `feat/phase4-platform-impl`
**Method:** Static read of the full render/streaming/state hot path + git-history diffing (HEAD vs working tree) + measured build & test runs + **live in-browser reproduction and instrumentation** (dev server + console-heartbeat + frame-time sampling against the real gateway).

---

## ⚠️ UPDATE — Confirmed root cause of the live crash (reproduced + fixed + verified)

Live instrumentation against the real gateway found the **actual** crash — and it was **none of the size/count issues below**. It is a classic infinite loop in the hand-rolled Markdown parser.

### PRIMARY ROOT CAUSE — `Markdown.parseInline` infinite loop (`components/ui/Markdown.tsx`)

`parseInline` recurses to parse the children of bold / italic / link spans. It used a **single module-level RegExp with the `g` flag** (`INLINE_RE`). A nested (recursive) call ran `INLINE_RE.exec()` on the inner string, which **reset the shared `lastIndex` to 0**. Back in the outer loop, `exec()` then restarted from 0 and **re-matched the same `**bold**` token forever → infinite loop → main thread frozen (RESULT_CODE_HUNG).**

- Triggered by **any** assistant reply containing `**bold**`, `*italic*`, `__/_`, or `[text](url)`. The render only switches to `<Markdown>` when a turn **finishes streaming**, so the freeze happens at end-of-turn.
- This is why it was query-dependent: a plain one-sentence answer (no emphasis) never hung; the "Summarise this week's pull requests" reply (bold-heavy numbered/bulleted list) **always** hung. All mock responses are plain text → never reproduced it.
- This is why the user's own cap/virtualization fixes didn't help — completely unrelated axis.

**Reproduction (isolated, deterministic):** feeding `"1. **GitHub** — your personal repos…"` through the exact `parseInline` logic ran **>200,000 iterations in 113 ms and never terminated**. The raw gateway stream for the query was confirmed normal (10 KB, 33 events, completes) — so the hang was 100% client-side, in the render after `session.chat.completed`.

**Fix:** give each `parseInline` call its **own** RegExp instance (`new RegExp(INLINE_RE_SOURCE, 'g')`) so recursion can't corrupt `lastIndex`; plus a zero-width-match guard. After the fix the same input terminates in **1 iteration**.

**Verification (end-to-end, live gateway):** the real "Summarise this week's pull requests" turn now completes (`streaming → false`, heartbeat keeps ticking) and renders the bold/numbered/bulleted reply correctly (screenshot captured) — the exact scenario that produced the "This page isn't responding" dialog. Regression tests added in `Markdown.test.tsx`.

### SECONDARY hardening shipped in the same pass (real, but not the crash)

These were found while hunting the crash and are genuine improvements (a *very* long reply could still degrade without them), so they're kept:

1. **Streaming render was O(size²).** The in-progress message re-rendered as one `<span>{fullText}</span>` every delta → the browser re-laid-out the whole message each flush. Fixed with `StreamingText` (append-only **memoized chunks**: only the last ~2 KB re-flows) + a **flush throttle** to ~20/s in `chat.ts`.
2. **Removed the message-list virtualizer.** The dynamic `@tanstack/react-virtual` `measureElement` ResizeObserver on the growing streaming row was a second freeze vector and is unnecessary given the 400-message cap. The transcript now renders as a plain memoized list (only the streaming row re-renders mid-turn). Side benefit: the conversation chunk dropped 48 KB → 24 KB.

**Files changed:** `components/ui/Markdown.tsx` (the fix), `routes/conversation.tsx` (de-virtualized list + chunked streaming text), `lib/data/domains/chat.ts` (flush throttle), `components/ui/Markdown.test.tsx` (new regression tests). `tsc`, `vitest` (8/8), and `vite build` all green.

**Residual (follow-up, not a crash):** streaming per-flush cost is still ~O(size) (the store concatenates the full string); realistic replies (≤50 KB) are smooth (≤20 ms/frame), extreme ones (>150 KB) get janky but no longer hang. Fully O(n) would mean storing streaming text as an append-only chunk array.

---

## 0. Framework reality check (correct the brief)

The brief assumes **Next.js**. This app is **not** Next.js. It is:

- **Vite 6** build, **React 18.3**, **TanStack Router** (file-based, `autoCodeSplitting: true`), **zustand 5**, **@tanstack/react-virtual 3**.
- Pure client SPA — **no SSR**, no server components, no `next.config`, no webpack.
- Transport is **SSE only** (fetch + ReadableStream) — **no WebSockets**.

This matters because several brief-implied risk areas (Next config, hydration, WebSocket reconnect storms, RSC waterfalls) **do not exist here**. The real risks are concentrated in three places: the **chat message list**, the **streaming write path**, and the **zustand store**.

---

## 1. Executive Summary

### The central finding

**Production runs committed `HEAD`. The reported P0 symptoms map precisely to defects in `HEAD`. A large, uncommitted working-tree change set already fixes the top P0s — but it is unverified, unlanded, and has known regressions.**

The working tree currently differs from `HEAD` by **+940 / −410 lines across the six hottest files** (`conversation.tsx` 516 lines, `chat.ts` 141, `store.ts` 90, `useCountdown.ts` 56, `Terminal.tsx` 38, `sse.ts` 35). Every line is performance work. This is in-flight remediation, not shipped code.

### Critical findings (root causes in production / `HEAD`)

| # | Defect (HEAD) | Symptom it produces | Status in working tree |
|---|---|---|---|
| P0-1 | **No cap on `messages[]`** | Memory grows unbounded → >3 GB, long-session instability | ✅ Fixed (cap 400) |
| P0-2 | **No list virtualization** — renders every message to DOM | DOM node explosion → RESULT_CODE_HUNG, freeze, crash | ✅ Fixed (`useVirtualizer`) |
| P0-3 | **Forced reflow every update** — reads `scrollHeight` on full list per `messages` change | Layout thrash → main-thread hang during streaming | ✅ Fixed (but introduced a regression — see P1-1) |
| P0-4 | **O(n²) history replay** — `findIndex` per text delta | Freeze on session resume/refresh | ✅ Fixed (O(n)) |
| P1-5 | **Unbounded history fetch** — no windowing | Memory spike on session load | ✅ Fixed (windowed) |
| P1-6 | **Per-card countdown intervals** — N `setInterval`s + N global store passes/sec | CPU burn + redundant renders with many approvals | ✅ Fixed (shared interval) |
| P2-7 | **Dynamic `import()` in hot actions** | Promise churn / latency on send/stop/allow | ✅ Fixed (static imports) |

### Root cause in one sentence

The chat transcript rendered and retained **every message, forever, as live DOM**, and the streaming path **forced a full-list layout on every update** — so memory and main-thread cost grew linearly (rendering) and quadratically (history replay) with conversation length, which is exactly the profile of "fine at first, then hangs and balloons to 3 GB over a long session."

### Expected improvements (working tree vs HEAD)

- DOM nodes for the transcript: **O(messages) → O(viewport)** (~16 mounted rows regardless of length).
- Retained message objects: **unbounded → ≤400**.
- History replay: **O(n²) → O(n)**.
- Streaming write cost: already RAF-batched in HEAD; unchanged (good).
- Idle CPU with K pending approvals: **K timers/sec → 1 timer/sec**.

### Measured today (working tree)

- `npm run build`: **clean**, 6.0 s, **initial JS 110.6 kB gzip**, routes code-split. Bundle is **not** a problem.
- `npm run test`: **4/4 pass** — but that is the *entire* frontend suite (2 files). The perf-critical logic is essentially untested (validation gap).
- `tsc --noEmit`: clean.

---

## 2. Phase 1 — Architecture Report

### Findings
- SPA shell in `routes/__root.tsx`: `Sidebar` + `<Outlet/>` + `GovernanceBar` + `GlobalOverlays`. Clean separation; overlays self-gate on store flags.
- One global zustand store (`lib/store/store.ts`, ~1680 lines) holding ~50 slices. Persists **only** `operator` + `theme` (good — no transcript in localStorage).
- Data layer: `client.ts` (fetch wrapper), `sse.ts` (stream reader), `domains/*.ts` (per-domain loaders that `hydrate()` the store), `live.ts`/`bootstrap.ts` (loader registry run once on mount).
- Routing: file-based, **per-route code splitting on** (confirmed in bundle output).

### High-risk areas (ranked)
1. **Chat list render path** (`routes/conversation.tsx`) — highest blast radius.
2. **Streaming write path** (`domains/chat.ts` → `store.hydrate`).
3. **Global store growth** (`store.ts` array slices).

### Performance risks
- Streaming mutates `messages[]` ~per animation frame; any non-memoized list child re-renders the whole transcript. (Mitigated in working tree via `memo(MessageItem)`.)

### Memory risks
- `messages[]`, `runLines[]`, `history[]`, `auditLog[]` are append-style arrays. Caps now exist on the first two; `history`/`auditLog` remain uncapped (P2-5 below).

---

## 3. Phase 2 — Memory Analysis Report

### Critical leaks (in HEAD / production)
- **Unbounded `messages[]`** (P0-1). Each turn appends 2 messages permanently; with cards + markdown each can be multi-KB. No eviction. → primary contributor to >3 GB.
- **Live DOM retention** (P0-2). Without virtualization, every message is a retained DOM subtree (+ React fiber + style objects). DOM is the dominant heap consumer in chat apps.

### Potential leaks (present in working tree — still open)
- **`history[]` unbounded** — `resolveApproval` and `tickApprovals` prepend with `[entry, ...s.history]`; the live approvals loader hydrates `approvals`+`auditLog` but **not** `history`, so it grows for the page lifetime. Slow but real. (P2-5)
- **`auditLog[]`** grows between live refreshes (reset on each operator/approvals fetch, so bounded in practice in live mode; unbounded in mock mode).
- **OAuth popup abandonment** (`domains/integrations.ts:354`) — if the user navigates away with the popup open, the 500 ms `pollInterval` + `window 'message'` listener live forever and the promise never settles. (P2-7)

### Ruled out (with evidence)
- **SSE stream leak:** `sse.ts` has idle-timeout, caller-abort `removeEventListener` in `finally`, `reader.cancel()` + `releaseLock()`. Clean.
- **`events[]`/`toolCalls[]`/`auditLog[]` from SSE:** the SSE handler never appends to these; loaders **replace** them wholesale from a fetch snapshot (`operator.ts:172`, `approvals.ts:179`). Bounded.
- **WebSockets:** none.
- **Reconnect storm:** the reconnect path only flips a `reconnecting` flag; no retry/poll loop.
- **localStorage growth:** only `operator`+`theme` persisted.

### Estimated memory savings (HEAD → working tree)
Long session (~2,000 messages): **~1.5–3 GB → ~150–400 MB** (estimated; dominated by removing live-DOM retention and capping the array). Requires heap-snapshot validation.

---

## 4. Phase 3 — Render Performance Report

### Components with highest render cost
- `Conversation` re-renders ~per frame during streaming (it subscribes to `messages`). Acceptable **because** children are isolated:
  - `MessageItem` is `React.memo` → only the changed (last) message re-renders.
  - `StreamingCursor` is `memo` + CSS animation → blink doesn't re-render siblings.
  - During streaming the last message renders as a **plain `<span>`**, not `<Markdown>` → no regex re-parse per frame. This is the ChatGPT-style pattern, done correctly.

### Rerender sources
- `isStreaming` is passed to every `MessageItem`; it flips true→false **once per turn**, re-rendering all rows once (acceptable). It is stable *during* a stream, so memo holds for all but the last row. Correct.
- `GovernanceBar` uses fine-grained selectors (`spent`, `budget`, …) which change a few times per turn (`usage.updated`), not per token. Fine.

### Optimization opportunities (open)
- **`Markdown` is not memoized** (P2-4). It re-parses (regex `parseInline`/`parseBlocks`) on every mount. With virtualization, scrolling remounts off-screen rows → re-parse on scroll. Wrap in `React.memo` (pure fn of `text`) and/or cache parses.

---

## 5. Phase 4 — Large List & Chat Analysis

### Virtualization
- **HEAD: absent** (`useVirtualizer` count = 0; rendered `messages.map(...)`).
- **Working tree: present** — `@tanstack/react-virtual` `useVirtualizer`, `overscan: 8`, `getItemKey` by message id, `measureElement` for dynamic heights. Correct.

### Streaming render
- Only the active streaming message re-renders (memoized siblings). ✅ Target met.
- **No syntax highlighter / no markdown lib** → no code-block highlight bottleneck exists (the brief's concern doesn't apply). Code fences currently render as plain paragraphs (feature gap, not perf).

### Open chat issues
- **P1-1 — streaming auto-follow broken** (regression introduced by the virtualization rewrite). The follow effect keys on `[messageVirtualizer, messages.length, streaming]`; during a stream the text grows but `messages.length` is constant, so `scrollToIndex` never fires and the streaming text scrolls out of view. HEAD followed correctly (via `scrollTop = scrollHeight`). Must restore follow without reintroducing the reflow.
- **P1-2 — live-run terminal is not batched.** `driveRunSlice` calls `store.hydrate({ runLines })` **synchronously per `text.stream.delta`**; on `/run`, `Terminal` re-maps up to 400 line-divs each token. The conversation path is RAF-batched; this mirror path is not.
- **P1-3 — 400-cap vs "50,000 messages" target.** The cap trims to the last 400 (safe memory, but no deep scrollback). True 50k support needs either backend-windowed paging or full history held outside the reactive store with a measured-size virtualization cache. This is a **product/perf decision to make explicitly**, not a bug.

---

## 6. Phase 5 — Bundle Analysis Report (measured)

### Current bundle (`vite build`, gzip)
- **Entry `index.js`: 110.6 kB gz** (349 kB raw) — React + ReactDOM + Router + zustand + virtual + shell.
- Largest route chunks: `conversation` 13.7 kB, `integrations` 10.4 kB, `work` 8.2 kB, `og` 5.2 kB. All lazy.
- CSS: 2.7 kB gz. `index.html`: 0.3 kB.

### Largest dependencies
- The framework baseline (React/Router) dominates the entry chunk. **No heavy libs** (no moment/lodash/markdown/highlighter — Markdown is hand-rolled, zero-dep).

### Potential savings / recommendations
- **Initial JS is already < the 300 kB target.** Bundle work is **low priority.**
- Optional: `manualChunks` to split `react`/`react-dom` into a long-cached vendor chunk; enable Brotli at the edge. Marginal.

---

## 7. Phase 6 — Browser Performance Report

### Layout issues
- **HEAD (production):** the auto-scroll effect (`scrollRef.current.scrollTop = scrollRef.current.scrollHeight` on `[messages, streaming]`) **forced a synchronous reflow of the entire mounted message list on every streaming update** — the layout-thrash source of RESULT_CODE_HUNG. **Fixed** in working tree (virtualizer scroll), but see P1-1.
- Remaining `scrollHeight`/`clientHeight` reads are now in scroll handlers (event-driven, not per-frame) and the composer auto-resize (`[composer]` only). Acceptable.

### Paint issues
- Two CSS keyframe animations (caret blink, reconnect pulse) are opacity-only and isolated. Fine.

### DOM issues
- **HEAD:** DOM node count = O(total messages) → unbounded. **Working tree:** O(viewport). This is the single biggest browser-stability win.

---

## 8. Phase 7 — State Management Report

### Problems
- One large store is fine for this app size; the issue was never structure, it was **unbounded array slices feeding an unvirtualized view**.
- `history[]`/`auditLog[]` lack caps (P2-5).
- Module-level timers (`_iv`, `_tt`, `_rc`, `_runIv`) are correctly cleared via `clearIv`/`clearRunIv`/guarded `clearTimeout`. Mock-mode `stream()` setInterval is cleared on completion/stop. OK.

### Recommended architecture
- Keep the single store. Add caps to all append slices. Keep transcript text out of any future persisted partition. For 50k-scrollback, introduce a non-reactive history buffer + backend paging rather than enlarging the reactive array.

---

## 9. P0 Issues (root cause · impact · fix · before/after · status)

> All P0s below are **already fixed in the working tree**. They are documented so the fixes can be *verified and landed*, and so a regression check exists.

### P0-1 — Unbounded `messages[]`
- **Root cause:** `store.ts` appended messages with no eviction.
- **Impact:** unbounded heap; primary >3 GB driver.
- **Before:** `messages: s.messages.map(...)` / `messages: [...s.messages, you, hachi]`
- **After:** `const CHAT_MESSAGES_MAX = 400; capMessages()` applied in `setText`, `hydrate`, `startTask`.
- **Expected gain:** retained messages bounded to ≤400. **Status: fixed (verify).**

### P0-2 — No virtualization
- **Root cause:** `conversation.tsx` rendered `messages.map((m) => <MessageItem/>)`.
- **Impact:** DOM node count = O(messages) → hang/crash on long chats.
- **After:** `useVirtualizer({ count, getScrollElement, estimateSize, getItemKey, overscan: 8 })` + absolute-positioned rows + `measureElement`.
- **Expected gain:** ~16 mounted rows regardless of length. **Status: fixed (verify; see P1-1).**

### P0-3 — Forced reflow on every streaming update
- **Before:** `useEffect(() => { scrollRef.current.scrollTop = scrollRef.current.scrollHeight }, [messages, streaming])` over a fully-mounted list.
- **Impact:** full-list layout per update → main-thread hang.
- **After:** virtualizer `scrollToIndex`, gated by a `followOutputRef` computed in the scroll handler.
- **Status: fixed — but introduced P1-1 (no longer follows during stream).**

### P0-4 — O(n²) history replay
- **Before:** `reconstructMessagesFromEvents` did `reconstructed.findIndex((m) => m.id === currentHachiId)` for **every** `text.stream.delta`.
- **Impact:** O(n²) on resume; freeze loading a long session.
- **After:** track `currentHachiIndex` directly → O(n).
- **Status: fixed (verify).**

---

## 10. P1 Issues (open — these are the real next-actions)

### P1-1 — Streaming auto-follow regression *(introduced by the P0-3 fix)*
- **Why:** follow effect depends on `messages.length`, which is constant during a stream; growing text never triggers a scroll.
- **Fix:** drive auto-scroll off the streaming message's text length (or virtualizer total size) while `followOutputRef.current` is true; keep it RAF/measure-based to avoid reintroducing the reflow.
- **Impact:** streaming output stays pinned to the bottom (ChatGPT behaviour). **Effort: S.**

### P1-2 — Live-run terminal not RAF-batched
- **Why:** `driveRunSlice` writes `runLines` to the store on every text delta; `Terminal` re-maps ≤400 divs per token on `/run`.
- **Fix:** batch `runLines` updates through the existing RAF buffer (or a coalescing timer); optionally `memo` the line row.
- **Impact:** removes per-token O(lines) render during live runs. **Effort: S–M.**

### P1-3 — 400-message cap vs 50k-scrollback target
- **Decision needed:** accept last-400 (current, memory-safe) **or** implement backend-windowed paging + non-reactive history buffer for true deep scrollback.
- **Impact:** product capability vs memory. **Effort: M–L (if pursued).**

---

## 11. P2 Issues (open — lower priority)

- **P2-4 — Memoize `Markdown`** (re-parses on every mount; remounts on scroll). `React.memo` + parse cache. *Effort S.*
- **P2-5 — Cap `history[]` / `auditLog[]`** (e.g. last 200) to stop slow long-session growth. *Effort S.*
- **P2-6 — Tune virtualizer `estimateSize`** (fixed 120 px is poor for card/markdown rows → scroll-anchor jump on jump-to-bottom); cache measured sizes. *Effort S.*
- **P2-7 — OAuth popup abandonment leak** (`integrations.ts`): add a timeout/`AbortSignal` so the poll + listener can't outlive navigation. *Effort S.*
- **P2-8 — Test coverage** for cap, virtualization, O(n) replay, shared interval. *Effort M.*

---

## 12. Not a problem (ruled out, with evidence)

- Bundle size (110 kB gz initial, code-split). 
- WebSocket leaks (none exist).
- SSE leaks (full cleanup present).
- Reconnect storms (no retry loop).
- Store growth from SSE (`events`/`toolCalls`/`auditLog` are replaced, not appended).
- localStorage bloat (only operator+theme).
- Syntax-highlighter bottleneck (no highlighter).

---

## 13. Performance Benchmark Table

> `Current` = committed `HEAD` (production). `Expected` = working tree once landed. Long-session = ~2,000 messages. Memory figures are **estimates pending heap-snapshot validation**; bundle is **measured**.

| Metric | Current (HEAD/prod) | Target | Expected (working tree) |
|---|---|---|---|
| Memory (long session) | ~1.5–3 GB+ (reported) | < 1 GB | ~150–400 MB |
| Transcript DOM nodes | O(all messages), unbounded | O(viewport) | ~16 rows + overscan |
| Retained message objects | unbounded | bounded | ≤ 400 |
| History replay (n events) | O(n²) | O(n) | O(n) |
| Idle CPU, K approvals | K timers/s + K store passes | 1 timer/s | 1 timer/s |
| Main thread during stream | full-list reflow/update (hang) | < 50 ms/interaction | isolated last-row update |
| FPS during stream | drops/stalls (reported) | 60 | ~60 (needs profiling) |
| Initial JS (gzip) | ~110 kB (measured) | < 300 kB | ~110 kB |
| FCP / LCP | needs Lighthouse | < 1.5 s / < 2.5 s | likely within target (small bundle) |

---

## 14. Parallel Execution Plan (workstreams)

> The top P0s are already implemented in the working tree, so the plan front-loads **verification** before new work.

- **Agent 1 — Memory Leak Elimination:** cap `history`/`auditLog` (P2-5); fix OAuth popup leak (P2-7); heap-snapshot the long-session scenario to validate the cap+virtualization win.
- **Agent 2 — React Render Optimization:** memoize `Markdown` (P2-4); audit selector stability; React DevTools Profiler trace of a streaming turn.
- **Agent 3 — Chat Performance:** **P1-1 auto-follow fix** + **P1-2 run-terminal batching** + **P2-6 estimateSize**. (Highest user-visible value.)
- **Agent 4 — Bundle:** low priority; optional vendor `manualChunks` + Brotli. Verify no route-chunk regressions.
- **Agent 5 — State Management:** enforce caps on all append slices; document the 50k-scrollback decision (P1-3).
- **Agent 6 — Browser Performance:** confirm no remaining per-frame `scrollHeight`/`getBoundingClientRect` reads; Performance-panel trace for forced reflows.
- **Agent 7 — Benchmarking & Validation:** Lighthouse (FCP/LCP), scripted long-session memory harness, and **regression tests** for cap/virtualization/O(n)/shared-interval (P2-8).

---

## 15. Recommended sequencing

1. **Verify & land the in-flight working-tree fixes** (P0-1…P0-4, P1-5/6) — they are correct in approach; gate on the regression suite from Agent 7.
2. **Fix the two regressions/gaps the rewrite left:** P1-1 (auto-follow) and P1-2 (run-terminal batching).
3. **Sweep the P2s** (Markdown memo, array caps, popup leak, estimateSize).
4. **Decide P1-3** (deep scrollback) as a product call.
5. **Validate** with Lighthouse + heap snapshots + new tests; record real numbers back into §13.
