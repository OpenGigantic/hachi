"""Optional OpenTelemetry tracing.

Emits spans for agent turns, LLM calls, and tool calls so operators can see where
time goes and how requests flow. It is **opt-in and degrades gracefully**: if the
OpenTelemetry SDK isn't installed (``pip install 'hachi-og[tracing]'``) or tracing
hasn't been configured, :func:`span` is a zero-cost no-op and the agent behaves
identically. Enable at runtime with ``HACHI_TRACING=true`` (console exporter),
or call :func:`setup_tracing` with your own exporter.

Implementation note: we hold our own ``TracerProvider`` + tracer rather than the
global OTel singleton, so configuration is re-entrant (tests can swap exporters)
and never collides with a host app that already set a global provider.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any

try:  # pragma: no cover - import guard
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - SDK not installed
    _OTEL_AVAILABLE = False

_tracer: Any = None
_configured = False


def is_available() -> bool:
    """True if the OpenTelemetry SDK is importable."""
    return _OTEL_AVAILABLE


def is_active() -> bool:
    """True if tracing has been configured and spans will be recorded."""
    return _tracer is not None


def setup_tracing(
    service_name: str = "hachi",
    exporter: Any = None,
    *,
    force: bool = False,
) -> bool:
    """Configure tracing. Idempotent unless ``force=True``.

    Args:
        service_name: ``service.name`` resource attribute.
        exporter: An OTel ``SpanExporter`` (e.g. console, OTLP, or in-memory for
            tests). When ``None``, a provider is created with no exporter (spans are
            produced but dropped) — pass an exporter to actually record them.
        force: Reconfigure even if already set up (used by tests to swap exporters).

    Returns:
        True if tracing is now active, False if the SDK is unavailable.
    """
    global _tracer, _configured
    if not _OTEL_AVAILABLE:
        return False
    if _configured and not force:
        return True
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    _tracer = provider.get_tracer("hachi")
    _configured = True
    return True


def configure_from_env() -> bool:
    """Auto-enable tracing when ``HACHI_TRACING`` is truthy (console exporter).

    Called during gateway boot. A no-op when the env flag is unset or the SDK is
    missing, so the default experience is unchanged.
    """
    if os.environ.get("HACHI_TRACING", "").lower() not in {"1", "true", "yes"}:
        return False
    if not _OTEL_AVAILABLE:
        return False
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    return setup_tracing(exporter=ConsoleSpanExporter())


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Record a span for the wrapped block. No-op if tracing isn't active.

    Args:
        name: Span name (e.g. ``"tool.call"``).
        **attributes: Span attributes (only str/bool/int/float are set).
    """
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if isinstance(value, str | bool | int | float):
                try:
                    current.set_attribute(key, value)
                except Exception:  # pragma: no cover - never let tracing break a turn
                    pass
        yield


def _reset_for_tests() -> None:
    """Clear tracing state so a test can reconfigure from scratch."""
    global _tracer, _configured
    _tracer = None
    _configured = False


__all__ = [
    "configure_from_env",
    "is_active",
    "is_available",
    "setup_tracing",
    "span",
]
