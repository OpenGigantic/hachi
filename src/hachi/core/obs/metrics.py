"""Thin metrics facade — counters and histograms for Hachi's key signals.

Records turn count, tool-call count, provider error rate, route selections, and
latency histograms.  All calls are no-ops when OTel metrics is not configured
so there is zero overhead in the default (unconfigured) deployment.

Currently backed by OTel metrics when available; the implementation uses
module-level counters so a missing SDK produces ``None`` instruments and every
:func:`record_*` call short-circuits silently.

All instruments live under the meter name ``"hachi"``; attribute names follow
the ``hachi.<domain>.<key>`` convention.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_meter: Any = None
_turn_counter: Any = None
_tool_counter: Any = None
_error_counter: Any = None
_route_counter: Any = None
_turn_latency: Any = None
_tool_latency: Any = None


def setup_metrics(exporter: Any = None) -> bool:
    """Configure OTel metrics.  Idempotent; safe to call multiple times.

    Args:
        exporter: An OTel ``MetricExporter`` or ``None`` (instruments created
                  but not exported — useful for testing the call path without
                  a live backend).

    Returns:
        True if metrics are now active, False if the OTel SDK is unavailable.
    """
    global _meter, _turn_counter, _tool_counter, _error_counter
    global _route_counter, _turn_latency, _tool_latency
    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            PeriodicExportingMetricReader,
        )

        readers = []
        if exporter is not None:
            readers.append(PeriodicExportingMetricReader(exporter, export_interval_millis=15_000))
        else:
            # No exporter — create provider with no readers (instruments exist but data
            # is never flushed; useful to exercise the call path without output).
            pass

        provider = MeterProvider(metric_readers=readers)
        _meter = provider.get_meter("hachi")

        _turn_counter = _meter.create_counter(
            "hachi.turns.total",
            description="Number of agent turns completed.",
        )
        _tool_counter = _meter.create_counter(
            "hachi.tool_calls.total",
            description="Number of tool calls executed.",
        )
        _error_counter = _meter.create_counter(
            "hachi.provider_errors.total",
            description="Number of provider/LLM errors (retryable and fatal).",
        )
        _route_counter = _meter.create_counter(
            "hachi.route_selections.total",
            description="Number of model route selections made.",
        )
        _turn_latency = _meter.create_histogram(
            "hachi.turn.latency_ms",
            unit="ms",
            description="Wall-clock latency of a complete agent turn.",
        )
        _tool_latency = _meter.create_histogram(
            "hachi.tool.latency_ms",
            unit="ms",
            description="Wall-clock latency of a single tool call.",
        )
        return True
    except Exception:  # pragma: no cover — SDK not installed
        return False


def record_turn(surface: str = "unknown", *, latency_ms: float | None = None) -> None:
    """Increment the turn counter and optionally record latency.

    Args:
        surface: Request origin (``"cli"``, ``"web"``, etc.).
        latency_ms: Elapsed milliseconds for the turn, if available.
    """
    if _turn_counter is None:
        return
    try:
        _turn_counter.add(1, {"surface": surface})
        if latency_ms is not None and _turn_latency is not None:
            _turn_latency.record(latency_ms, {"surface": surface})
    except Exception:
        pass


def record_tool_call(
    tool_name: str, *, error: bool = False, latency_ms: float | None = None
) -> None:
    """Increment the tool-call counter.

    Args:
        tool_name:  Name of the tool that was called.
        error:      True when the tool raised an exception.
        latency_ms: Elapsed milliseconds for the call, if available.
    """
    if _tool_counter is None:
        return
    try:
        attrs = {"tool": tool_name, "error": str(error).lower()}
        _tool_counter.add(1, attrs)
        if latency_ms is not None and _tool_latency is not None:
            _tool_latency.record(latency_ms, attrs)
    except Exception:
        pass


def record_provider_error(model: str, error_kind: str = "unknown") -> None:
    """Increment the provider-error counter.

    Args:
        model:      Model id that produced the error.
        error_kind: Short classification (e.g. ``"rate_limit"``, ``"timeout"``).
    """
    if _error_counter is None:
        return
    try:
        _error_counter.add(1, {"model": model, "kind": error_kind})
    except Exception:
        pass


def record_route_selection(capability: str, model: str) -> None:
    """Increment the route-selection counter.

    Args:
        capability: The capability being routed (e.g. ``"REASONING"``, ``"VISION"``).
        model:      The model id selected.
    """
    if _route_counter is None:
        return
    try:
        _route_counter.add(1, {"capability": capability, "model": model})
    except Exception:
        pass


class TurnTimer:
    """Simple context manager to time a turn and call :func:`record_turn`.

    Usage::

        with TurnTimer(surface="web"):
            ...
    """

    def __init__(self, surface: str = "unknown") -> None:
        self.surface = surface
        self._start: float = 0.0

    def __enter__(self) -> TurnTimer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: object) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000.0
        record_turn(self.surface, latency_ms=elapsed_ms)


def _reset_for_tests() -> None:
    """Reset all metric instruments (tests only)."""
    global _meter, _turn_counter, _tool_counter, _error_counter
    global _route_counter, _turn_latency, _tool_latency
    _meter = None
    _turn_counter = None
    _tool_counter = None
    _error_counter = None
    _route_counter = None
    _turn_latency = None
    _tool_latency = None
