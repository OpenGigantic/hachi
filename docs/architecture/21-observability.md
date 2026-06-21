# 21 — Observability (Langfuse-ready)

> Phase 2 / Gate B. Initiative **H**. The seam is clean; the work is **correlation + coverage**.
> Sequenced early (Phase 2.3) so B/C/D are traceable as they land. Seams are `file:line`.
> Related: ADR-0005.

## 1. Problem

Tracing is dormant (`HACHI_TRACING` off) with only 2 leaf spans (`loop.py:355,737`), no per-turn
root, and the tool thread-pool loses OTel context (`loop.py:856`). Tool/usage/sub-agent events
carry no `turn_id`/`session_id` (`loop.py:775,829,882`); the transport envelope has none
(`control_plane.py:418`). So no trace can be assembled across User→Session→Agent→Tool→Model→
Provider→Response. No metrics. Logging is silent outside the daemon.

## 2. Contract (injected, never littered)

```python
# core/obs/trace.py
@dataclass
class TraceContext:
    trace_id: str          # == turn_id (orchestrator.py:1141), promoted to the canonical id
    session_id: str
    principal: str         # token-hash today; real user id when auth grows
    surface: str           # cli|tui|web|webhook|schedule (orchestrator.py:1190)

def current() -> TraceContext | None: ...          # contextvars-backed
def bind(ctx: TraceContext): ...                   # context manager; sets contextvars + OTel baggage
```

Two injection seams, both additive (ADR-0005):
1. **Spans** via `tracing.span()` (`tracing.py:91`) at a few choke points — *not* scattered.
2. **An observability event sink** subscribed to the `EventBus` ([12](12-event-architecture.md))
   that mirrors runtime events into trace events. Business logic emits events (it already does);
   the sink turns them into spans/generations. **The sink only enqueues (Gate-C amendment A5)** —
   actual export runs through OTel's async `BatchSpanProcessor`, so a slow exporter (e.g. Langfuse
   over the network) can never block the turn thread.

## 3. Coverage to add

| Layer | Span / signal | Where |
|---|---|---|
| Turn (root) | `agent.turn` root span carrying `TraceContext` | `_chat_impl` (`orchestrator.py:1138`) |
| Thread propagation | re-attach OTel context inside the tool pool | `loop.py:856` (the critical fix) |
| Model | `llm.generate` + token/cost/model attrs | `loop.py:737,772` (attach `CostSnapshot`) |
| Provider | optional provider span + failover events | `ResilientClient` ([13](13-provider-architecture.md)) |
| Tool | `tool.call` (now nested under the root) | `loop.py:355` |
| Sub-agent | `agent.delegate` child span | `delegation_tools.py` |
| Events | every event stamped with `trace_id`/`session_id` | `runtime_events.py`, `loop.py:775,829,882` |

## 4. Exporter (the Langfuse decision)

`setup_tracing(exporter=…)` (`tracing.py:55`) already accepts any OTel `SpanExporter`, wired through
an async **`BatchSpanProcessor`** (Gate-C amendment A5) so emission never blocks the loop. Config
`[observability]` selects: `console` (default, dev), `otlp` (any OTLP endpoint), or `langfuse`
(OTLP→Langfuse). **No live Langfuse instance is assumed** — it is opt-in config. The redacting
event taxonomy (`runtime_events.py` secret-scrub) ensures no secrets reach the exporter; spans
inherit the same redaction.

## 5. Minimal metrics

A thin counter/timer facade (`core/obs/metrics.py`) recording turn count, tool-call count,
provider error-rate, route selections, and latency histograms — emitted via the same exporter
when OTel-metrics is enabled, else no-op. Deliberately minimal (counters + a few histograms), not
a full metrics platform.

## 6. Logging (fix the default-silent gap)

A shared `setup_logging()` (generalizing the daemon's handler, `daemon.py:113`) wired into CLI/
gateway/TUI entrypoints, level via `HACHI_LOG_LEVEL`, with `trace_id` bound into a `LoggerAdapter`
so logs correlate with traces.

## 7. What it touches

`core/obs/*` (new: trace context, sink, metrics); `core/tracing.py:55,76` (exporter selection);
`gateway/orchestrator.py:1138` (root span + bind); `core/loop.py:355,737,772,856` (propagation +
cost-on-span); `core/runtime_events.py` (id stamping); entrypoints (`setup_logging`);
`core/settings.py` (`[observability]`); `pyproject.toml` (langfuse/otlp optional extra).

## 8. What could break (and the guard)

- **Context propagation into threads** is subtle. *Guard:* a test asserts `tool.call` spans nest
  under `agent.turn` (parent/child), the exact gap today.
- **Overhead when enabled.** *Guard:* spans are no-ops when unconfigured (already true,
  `tracing.py:99`); enabling adds bounded attribute-setting; measured in a perf test.
- **Id unification** (`turn_id` vs the 500-handler `correlation_id`, `fastapi_app.py:480`).
  *Guard:* standardize on `trace_id`; keep the HTTP correlation id as a span attribute, documented.

## 9. Deferred (seam only, per §4a)

Full metrics/SLO backend + dashboards; trace sampling strategy; multi-user principal modeling
beyond the `principal` field; log aggregation shipping. The exporter and sink seams accommodate
each later.
