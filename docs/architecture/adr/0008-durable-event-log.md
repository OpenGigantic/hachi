# ADR-0008 — Durable runtime-event log

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** T-75 (architecture-review Wave 2, Missed Opportunity #1 — see
  [T-75](../../audit/2026-07-19/tasks/T-75-durable-event-log.md))
- **Related:** [T-75](../../audit/2026-07-19/tasks/T-75-durable-event-log.md) (this task);
  [T-30](../../audit/2026-07-19/tasks/T-30-sse-gap-signal.md) (the interim `base_sequence` gap
  signal this makes structurally impossible rather than merely detectable);
  [T-54](../../audit/2026-07-19/tasks/T-54-schema-version-tables.md) (the schema-versioning
  mechanism this table is stamped with); [ADR-0003](0003-event-bus-over-emit-funnel.md) and
  [ADR-0004](0004-durable-run-state-on-sqlite-job-substrate.md) (read, not reopened — see
  Context; ADR-0004's `RunStateStore` is the pattern this store's shape copies verbatim);
  [12-event-architecture.md](../12-event-architecture.md) §8 (the deferral this ADR executes);
  BB-42 (why the store is process-wide, not per-request — see Decision)

## Context

`docs/architecture/12-event-architecture.md` §8 deferred a durable event log and named its own
trigger to build it:

> ## 8. Deferred (seam only, per §4a)
>
> Durable/persistent event log, replay, dead-letter queue, cross-process/distributed delivery.
> The bus interface accommodates all four later (persist in `publish`; replay by re-reading a
> persisted log; DLQ as a failed-handler-job sink) — none is built now because no consumer needs
> them on a single node. Trigger to build: first multi-process deployment or first "replay events
> since T" requirement.

The second trigger fired. `GET /sessions/{session_id}/events?after=N`
(`GatewayControlPlane.read_session_events`, `gateway/control_plane.py:486`) *is* a "replay events
since T" requirement, and it shipped — but the deferral was never revisited. In its absence, two
lossy substitutes were built instead:

1. **A 2,000-entry in-memory ring** (`RuntimeReadModelStore._events`, `core/runtime_read_models.py`)
   — a plain Python list, trimmed on every `append_event`. It does not survive a process restart,
   and (before this task) its sequence counter reset to `0` on every construction, so a client
   holding a pre-restart cursor would never see another event from the new process.
2. **Message-row synthesis** (`GatewayControlPlane._synthesize_history_events`,
   `control_plane.py:571`) — for a session with no live connection, reconstructs event envelopes
   from `SessionStore` transcript rows, in a second sequence space disconnected from the live
   ring's (`seq = 0`, fresh per call). It can emit exactly four event types —
   `session.turn.started`, `text.stream.delta`, `session.chat.completed`, `tool.call.completed` —
   all derived from message rows. `permission.requested` and `permission.decided`, the governance
   record, are not messages, so they existed live and vanished on replay. **That fidelity gap is
   what this ADR closes, not merely the durability gap.**

This ADR is not relitigating [ADR-0003](0003-event-bus-over-emit-funnel.md) or
[ADR-0004](0004-durable-run-state-on-sqlite-job-substrate.md) — `CLAUDE.md` forbids relitigating a
settled decision. It is executing §8's own stated follow-through: the trigger it named has fired,
and this is that trigger being honored.

## Decision

**We persist every non-ephemeral runtime event to a SQLite `events` table**, via a new
`EventStore` (`core/events/store.py`), threaded as an *optional* collaborator into
`RuntimeReadModelStore` (`core/runtime_read_models.py`). The in-memory ring is unchanged, as a hot
cache for the common "tail of a live session" query; `_synthesize_history_events` is unchanged, as
the fallback for sessions recorded before this table existed.

**The sequence authority.** `seq INTEGER PRIMARY KEY AUTOINCREMENT` (`core/events/store.py:36`) is
the durable sequence: **global, not per-session**, and monotonic **across restarts** — SQLite
persists the highest-ever-used rowid for the table in its own `sqlite_sequence` bookkeeping, which
is what makes a stale client cursor servable from disk after a real restart rather than merely
detectable. `RuntimeReadModelStore._sequence` is seeded from `EventStore.max_seq()` at
construction (`runtime_read_models.py:284`) instead of starting at `0`. `EventBus._sequence` is
untouched and remains a separate, per-process fan-out ordinal — see Consequences.

**Process-wide, not per-agent.** `get_event_store()` (`core/events/__init__.py`) is a module-level
singleton (double-checked locking, mirroring `get_wait_registry()`'s shape exactly). Two
independent reasons: first, `GatewayControlPlane`'s not-live history branch (`read_session_events`,
via `read_events_durable` in `gateway/in_process_runtime.py:321`) is *by construction* the path
for a session with no booted agent — a per-agent store would be unreachable from exactly the read
path that needs durable replay the most. Second, BB-42 independently requires one store per
process: a store opened per call/request holds `*.db-wal`/`*.db-shm` handles open indefinitely on
Windows, and an events table is the highest-frequency writer in the process.

**The write hook is `RuntimeReadModelStore.append_event`, not `EventBus.publish`.**
`[events] bus_funnel` (`core/settings.py:205`, `EventsSettings.bus_funnel: bool = True`) is a
real, live toggle — `boot_agent` (`gateway/orchestrator.py:2560`) still defines a legacy
direct-call path for `emit_runtime_event`/`relay_event` that runs when `bus_funnel=false`, and the
bus path (`cfg.events_cfg.bus_funnel` gate at `orchestrator.py:2745`) funnels into the *same*
`append_event` call when it's on. `append_event` is the one choke point both configurations
traverse; persisting in `publish` would silently stop persisting the moment someone set
`bus_funnel=false` — a dead feature that reports success.

**A deny-list, not an allow-list.** `_EPHEMERAL_EVENTS = frozenset({"text.stream.delta",
"thinking.step"})` (`core/events/store.py:65`), applied inside `EventStore.append` — one choke
point, not duplicated at call sites. A deny-list fails *safe*: a newly added event name is
persisted by default. An allow-list fails *silent*.

**Redaction at the persist boundary — two layers, and neither one alone is a complete
guarantee.** `EventStore._redact_payload` (`core/events/store.py:271-301`) runs inside `append`,
at the store boundary, so only the durable copy is affected — the in-memory `RuntimeEvent`, the
TUI transcript, and the gateway SSE stream are all unchanged by this method.
1. `redact_structure` (`core/secret_scanner.py:88`) recursively scrubs every string leaf matching
   one of the **13 vendor-prefix patterns** in `_PATTERNS` (`secret_scanner.py:23-41`: a PEM
   private-key block, plus 12 provider-token shapes — Anthropic, OpenAI, AWS, GitHub ×2, Slack,
   Google ×2, Stripe, SendGrid, npm, JWT). This covers the realistic leak vector for the six event
   names that embed raw `str(exception)` text (`llm.retry`, `job.retry`, `job.dead_letter`,
   `job.failed`, `mcp.server.failed`, `routing.boot_failed`), since provider SDKs routinely echo
   the failing request — auth headers included — into exception text.
2. `_SENSITIVE_PAYLOAD_KEYS = frozenset({"wait_key"})` (`core/events/store.py:77`) is additive,
   name-based defence for a **provable blind spot** in layer 1: `run.suspended`'s `wait_key`
   (`orchestrator.py:1457`, `tools/workflow_tools.py:56`) is the token used to resume a suspended
   run, and it carries no recognizable vendor prefix — no pattern in layer 1 will ever match it.
   Layer 2 replaces that value outright by key name, regardless of shape.

**Payloads are not, in general, guaranteed redacted.** Layer 1 is a pattern matcher over 13 known
shapes, not a secret detector; layer 2 covers exactly one named key, found by reading the code,
not by any general mechanism. A future event whose payload embeds a credential in neither a
matched vendor shape nor a key in `_SENSITIVE_PAYLOAD_KEYS` is written to disk unredacted. This is
the honest scope of what shipped, not a claim that the persist boundary is a complete secret
filter.

**Stage D — the boot-window orphan, and why the fix is a per-instance placeholder plus a rebind,
not a shared tag.** `RuntimeReadModelStore` is constructed inside `boot_agent`
(`orchestrator.py:2617`, `read_models = RuntimeReadModelStore(get_event_store())`) *before* the
real session id exists: `SessionStore.start_session()` assigns it later, inside
`AgentSession.set_session_store()` (`orchestrator.py:654-673`). The store therefore starts writing
under *some* tag well before that real id is known — all 11 `boot.progress` events
(`orchestrator.py`, `"total": 11`) are emitted in this window.

An earlier version of this fix (Stage B) late-bound the id via `set_session_id()` but tagged every
pre-bind write with the shared literal `""`. Every row written before the rebind — all 11
`boot.progress` events — was durably written yet permanently unreachable through the real session
id, because `read_since`'s `WHERE session_id = ?` filter (`core/events/store.py:202`) is an exact
match, not a prefix. **Stage D fixed this.** `RuntimeReadModelStore.__init__`
(`runtime_read_models.py:235`) now mints a **unique per-instance placeholder**,
`f"{_BOOT_PLACEHOLDER_PREFIX}{uuid.uuid4().hex}"` (`_BOOT_PLACEHOLDER_PREFIX = "__boot__"`,
`runtime_read_models.py:229`), whenever it is constructed with an `event_store` and no explicit
`session_id`. Once `set_session_store()` learns the real id, it calls `set_session_id()`
(`runtime_read_models.py:287`), which reassigns `self._session_id` and calls the new
`EventStore.rebind_session(old_session_id, new_session_id)` (`core/events/store.py:218`) —
`UPDATE events SET session_id = ? WHERE session_id = ?`, matched by **exact equality**, the same
discipline `read_since` already uses. `set_session_id` is a no-op when the id is unchanged, so it
is safe to call more than once (`set_session_store` is not guaranteed to run exactly once).

The placeholder must be per-instance, not a shared constant — in particular, not the empty string
`""` writes were tagged with before Stage D. The "process-wide, not per-agent" decision above
means several sessions share **one** `EventStore`. If every not-yet-bound
`RuntimeReadModelStore` in the process tagged its boot-window writes with the same shared value,
`rebind_session`'s exact-equality `UPDATE ... WHERE session_id = <shared value>` would rebind
*every other* concurrent session's still-unbound boot rows into whichever session's
`set_session_id` happened to run first — silently reassigning one session's `boot.progress`
history into another session's replay. A unique-per-instance placeholder is the only tag
`rebind_session` can retag safely by exact equality; a shared one is the bug this mechanism exists
to avoid, not a valid way to call it.

## Alternatives considered

1. **Persist in `EventBus.publish` instead of `append_event`**, as `12-event-architecture.md` §8
   itself once suggested. Rejected — `bus_funnel` defaults `True` but is a real, live toggle with
   a legacy direct-call path still reachable; persisting in `publish` would silently stop
   persisting under `bus_funnel=false`, the exact "dead feature reports success" pattern this
   audit exists to catch.
2. **A per-agent `EventStore`** (one instance per booted `AgentSession`, the same lifecycle as
   `RuntimeReadModelStore` itself). Rejected — the one read path that most needs durable replay,
   `GatewayControlPlane`'s not-live history branch, is defined by there being *no* booted agent
   for that session; a per-agent store is unreachable from exactly that path. BB-42 also
   independently rules out a store opened per request.
3. **An allow-list of event names to persist.** Rejected in favor of a deny-list — an allow-list
   fails silently the day a new event type is added and nobody remembers to add it to the list; a
   deny-list fails safe, with the burden of an explicit decision falling on the two names
   deliberately excluded, not on every future one included.
4. **Field-stripping instead of `redact_structure`'s pattern matcher** (maintain only a list of
   known-dangerous keys like `wait_key`, and drop the general pattern layer). Rejected — this
   would miss the realistic leak vector entirely: credentials that provider SDKs echo into
   free-form `str(exception)` text for six event names, which no fixed key name could target.
   `_SENSITIVE_PAYLOAD_KEYS` is kept *additive* to `redact_structure`, for the pattern matcher's
   own narrow, provable blind spot — not a substitute for it.
5. **(Stage D) Tag every boot-window write with one shared constant** (the empty string, or any
   other single fixed placeholder), and rebind by that same shared value once each session's real
   id is known. Rejected — the store is process-wide (Decision, "process-wide, not per-agent"), so
   several sessions' `RuntimeReadModelStore` instances would all tag their boot-window rows with
   the identical value; the first `set_session_id()` call to run would rebind *every* concurrent
   session's still-unbound boot rows into itself via `rebind_session`'s exact-equality `UPDATE`,
   not just its own. This is the bug Stage D closes, not an alternative to it.
6. **(Stage D) Buffer boot-window events in memory and flush them once the real session id is
   known**, rather than writing them immediately under a placeholder. Rejected — a crash during
   boot, which is precisely the window `boot.progress` events exist to help diagnose, would lose
   every buffered event, defeating the reason for persisting them durably at all.

## Consequences

**Positive:**
- `events.db` is registered in `HachiPaths.all_stores()` (`core/paths.py:194-203`) with
  `derived=False`, so `hachi backup`/`restore`/`doctor` all cover it through the existing
  `StoreSpec` enumeration (T-55/BB-145) with no separate wiring. An audit trail excluded from
  backup would not be an owned audit trail.
- Replay fidelity now matches live fidelity for the case this task set out to fix: `permission.decided`
  — and every other non-ephemeral event type — is returned by the durable replay path, where
  message-row synthesis could never represent it
  (`tests/test_event_store_durability.py::test_replay_fidelity_matches_live_fidelity`).
- A cursor older than the 2,000-entry ring tail is served from disk rather than silently clamped
  (`tests/test_event_store_durability.py::test_stale_cursor_is_served_not_clamped`).
- The boot-window orphan is closed: all 11 `boot.progress` events, and anything else emitted
  before `set_session_store()` runs, are reachable through the real session id once
  `set_session_id()` rebinds them
  (`tests/test_event_store_durability.py::test_events_emitted_before_the_session_id_is_known_are_not_orphaned`,
  `::test_rebind_does_not_capture_another_sessions_boot_events`;
  `tests/test_event_store.py::test_rebind_retags_rows_and_returns_the_moved_count`,
  `::test_rebind_is_an_exact_equality_match_not_a_prefix`).

**Negative / cost:**
- `_synthesize_history_events` (`control_plane.py:571`) is retained permanently, as the fallback
  for every session recorded before this table existed — a second, parallel replay code path now
  lives in the codebase indefinitely by design, not by oversight.
- `base_sequence` (T-30) stays in the response shape but, once a durable store is attached, always
  reports `0` (`runtime_read_models.py:554`, `oldest_sequence`) — it stops indicating a gap that is
  now structurally impossible, rather than being removed, so the response shape stays uniform
  across the live/durable/synthesized branches.
- Sequence numbers are monotonic **within** a session but not **contiguous** — `seq` is one global
  counter shared by every concurrent session, so any one session's numbers will show gaps wherever
  another session's events interleaved.
- The table is unbounded. Retention/pruning is deliberately **not** part of this decision:
  `RetentionSettings` (`orchestrator.py:3157`) exists and is loaded elsewhere but was not wired to
  this table on purpose — how long an audit trail should live is a product decision, not an
  implementation detail.
- The two `_sequence` counters (`EventBus._sequence` vs. `RuntimeReadModelStore._sequence`) remain
  independent, by decision, not by omission — reconciling them touches the async-dispatch path and
  is explicitly out of scope here.
- Write volume: one `INSERT` per non-ephemeral event, on the agent's worker thread, inside the
  same lock as the in-memory ring append. Stage D adds a second write on every `set_session_id()`
  call when a durable store is attached — an `UPDATE` across however many boot-window rows
  accumulated (bounded — at most the 11 `boot.progress` events plus whatever else fires before the
  rebind).
- The redaction guarantee is partial, not total (see Decision) — a payload that embeds a
  credential in neither a matched vendor shape nor a key in `_SENSITIVE_PAYLOAD_KEYS` is written
  to disk unredacted.

**Reversibility:** high for the wiring, low for data already written. `event_store: EventStore |
None = None` keeps `RuntimeReadModelStore` byte-identical to pre-T-75 behaviour when absent — the
backward-compatible default the task spec required, since the constructor was called with no
arguments in 19 places across 6 test files before this change — and the read path falls back to
the ring / `_synthesize_history_events` when no store is attached, so un-wiring durability at the
single `boot_agent` call site is a one-line change. What is not reversible is the audit history
already accumulated in `events.db` by the time such a change is made; this ADR establishes no
export or migration mechanism for recovering it if the table is later abandoned.
