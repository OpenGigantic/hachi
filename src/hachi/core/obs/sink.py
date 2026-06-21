"""Observability event sink — mirrors runtime events into OTel spans.

The sink subscribes to the :class:`~hachi.core.event_bus.EventBus` as a sync
subscriber (``mode="sync", name="observability"``).  Per Gate-C amendment A5
the sink **only enqueues** into OTel; actual export runs through OTel's async
:class:`~opentelemetry.sdk.trace.export.BatchSpanProcessor` so a slow
exporter (e.g. Langfuse over the network) never blocks the turn thread.

The sink must NOT re-enter ``bus.publish()`` — it only records spans.

Secrets: the event taxonomy's redaction already strips secrets from event
payloads before they reach this sink; spans inherit that protection.
"""

from __future__ import annotations

import logging
from typing import Any

from hachi.core.runtime_events import RuntimeEvent

logger = logging.getLogger(__name__)

# Events that map directly to a child span.  The span name is the event name
# unless overridden in _SPAN_NAMES.
_SPAN_EVENTS: frozenset[str] = frozenset(
    {
        "tool.call.started",
        "tool.call.completed",
        "llm.generate",
        "session.chat.started",
        "session.chat.completed",
        "subagent.started",
        "subagent.completed",
        "provider.failover",
        "model.routed",
    }
)

_SPAN_NAMES: dict[str, str] = {
    "session.chat.started": "agent.turn.start",
    "session.chat.completed": "agent.turn.end",
}


class ObservabilitySink:
    """Subscribes to the EventBus and mirrors events into OTel spans.

    Designed to be registered once at boot:

    .. code-block:: python

        sink = ObservabilitySink()
        bus.subscribe("*", sink.handle, mode="sync", name="observability")

    The sink is safe to create even when OTel is not configured; in that case
    :meth:`handle` is a very cheap no-op (one function-call + ``is_active()``
    check).
    """

    def handle(self, event: RuntimeEvent) -> None:
        """Handle a single RuntimeEvent from the bus.

        Only enqueues a span into OTel's async pipeline; never blocks.
        Exceptions are caught and logged — never propagated into the bus.

        Args:
            event: The runtime event to mirror.
        """
        try:
            self._handle_safe(event)
        except Exception:  # pragma: no cover — defensive catch
            logger.debug(
                "ObservabilitySink: error mirroring event %r — skipped",
                event.name,
                exc_info=True,
            )

    def _handle_safe(self, event: RuntimeEvent) -> None:
        from hachi.core import tracing

        if not tracing.is_active():
            return

        # Only mirror events we have explicit mappings for; everything else is
        # captured via id-stamping on the event payload (not as a span).
        if event.name not in _SPAN_EVENTS:
            return

        span_name = _SPAN_NAMES.get(event.name, event.name)
        attrs: dict[str, Any] = {}

        # Carry correlation ids from the event payload (stamped upstream).
        for key in ("trace_id", "session_id", "turn_id"):
            val = event.payload.get(key)
            if isinstance(val, str):
                attrs[f"hachi.{key}"] = val

        # Carry well-known payload fields as span attributes.
        # Note: "name" is excluded here because it conflicts with the positional
        # ``name`` parameter of :func:`~hachi.core.tracing.span`; it is forwarded
        # under the ``tool_name`` key instead.
        for key in ("role", "model", "status", "duration", "iterations"):
            val = event.payload.get(key)
            if isinstance(val, str | bool | int | float):
                attrs[key] = val
        tool_name = event.payload.get("name")
        if isinstance(tool_name, str):
            attrs["tool_name"] = tool_name

        # The sink uses the tracing module's span() contextmanager but just
        # opens-and-immediately-closes it (enter + exit with no body) so only
        # the span metadata is recorded — we are not wrapping real work here.
        import contextlib

        with contextlib.suppress(Exception):
            with tracing.span(span_name, **attrs):
                pass
