# ADR-0005 — Observability via correlation-id stamping + injected exporter

- **Status:** Proposed (Gate B)
- **Date:** 2026-06-21
- **Related:** [21](../21-observability.md); composes with ADR-0003

## Context

Tracing is dormant with 2 leaf spans, no per-turn root, and thread-pool context loss
(`loop.py:856`); events carry no `turn_id`/`session_id` (`loop.py:775,829`); there is no metrics
or default logging. End-to-end traces (User→Session→Agent→Tool→Model→Provider→Response) cannot be
assembled. The brief (H) wants Langfuse-grade tracing, injected and not littered through business
logic.

## Decision

Make the existing per-turn `turn_id` the canonical **`trace_id`**, carried in a `TraceContext`
(contextvars + OTel baggage), **stamp it on every runtime event and span**, establish a per-turn
**root span** in `_chat_impl`, and **propagate OTel context into the tool thread-pool**. Inject
the exporter two ways, both additive: (1) `setup_tracing(exporter=…)` already accepts any OTel
`SpanExporter` (`tracing.py:55`), and (2) an **observability sink subscribed to the `EventBus`**
(ADR-0003) that turns runtime events into spans/generations. Exporter is config-selected
(`console` default / `otlp` / `langfuse`); **no live Langfuse instance is assumed**. Add a thin
no-op-by-default metrics facade and a shared `setup_logging()`.

## Alternatives considered

1. **Scatter `span()` calls through business logic.** Rejected — littering; the brief explicitly
   wants tracing cross-cutting and injected. The choke points (turn root, `registry.execute`,
   `BaseLLMClient`, the event sink) give coverage without scattering.
2. **Adopt the Langfuse SDK directly in the loop.** Rejected — couples business logic to a vendor;
   the OTel-exporter seam keeps Hachi provider-agnostic in observability too (a vendor exporter is
   one config value).
3. **New correlation id instead of reusing `turn_id`.** Rejected — `turn_id` already exists at the
   turn boundary (`orchestrator.py:1141`); reusing it avoids a second id (the existing 500-handler
   `correlation_id` stays a span attribute, not the trace key).

## Consequences

**Positive:** one trace spine across all layers; B/C/D become debuggable as they land (H sequenced
early); Langfuse is one config value; redaction inherited from the event taxonomy; the default-
silent logging gap closes. **Negative:** id-stamping touches many emit sites (additive, low-risk);
thread-context propagation is subtle (covered by a nesting test); minor enabled-overhead (no-op
when off). **Reversibility:** high — all changes are additive; disabling reverts to today.
