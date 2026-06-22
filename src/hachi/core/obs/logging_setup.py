"""Shared logging bootstrap for CLI / gateway / TUI entry points.

Generalises the daemon's ``setup_rotating_log`` into a one-call setup that:
- Sets the root logger level from ``HACHI_LOG_LEVEL`` (default ``WARNING``).
- Adds a :class:`logging.StreamHandler` with a concise format when no handlers
  are yet configured (so running without a config file is not completely silent).
- Optionally binds the current :class:`~hachi.core.obs.trace.TraceContext`
  into a :class:`logging.LoggerAdapter` so log records carry ``trace_id`` for
  correlation with OTel spans.

Usage in entry points::

    from hachi.core.obs.logging_setup import setup_logging

    logger = setup_logging(__name__)
    # or, to correlate with the active TraceContext:
    logger = traced_logger(logging.getLogger(__name__))
"""

from __future__ import annotations

import logging
import os
from typing import Any


def setup_logging(name: str | None = None, *, level: int | None = None) -> logging.Logger:
    """Bootstrap the root logger and return a logger for *name*.

    Idempotent: if the root logger already has handlers this only adjusts the
    level.

    Args:
        name:  Logger name (typically ``__name__`` of the caller).  ``None``
               returns the root logger.
        level: Override the log level.  When ``None``, reads ``HACHI_LOG_LEVEL``
               (``WARNING`` by default).

    Returns:
        A configured :class:`logging.Logger`.
    """
    effective_level = level if level is not None else _level_from_env()
    root = logging.getLogger()

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(effective_level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)

    if root.level == logging.NOTSET or root.level > effective_level:
        root.setLevel(effective_level)

    return logging.getLogger(name)


def traced_logger(logger: logging.Logger) -> logging.LoggerAdapter[logging.Logger]:
    """Wrap *logger* in an adapter that injects ``trace_id`` from the active TraceContext.

    When no :class:`~hachi.core.obs.trace.TraceContext` is bound the adapter
    omits the ``trace_id`` key so log records produced outside a turn remain
    unchanged.

    Args:
        logger: Base :class:`logging.Logger` to wrap.

    Returns:
        A :class:`logging.LoggerAdapter` with dynamic ``trace_id`` injection.
    """
    return _TracedAdapter(logger, extra={})


def _level_from_env() -> int:
    """Resolve the log level from ``HACHI_LOG_LEVEL`` (default ``WARNING``)."""
    raw = os.environ.get("HACHI_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, raw, None)
    if isinstance(level, int):
        return level
    return logging.WARNING


class _TracedAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """LoggerAdapter that dynamically injects ``trace_id`` from the active TraceContext."""

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        try:
            from hachi.core.obs.trace import current as _current

            tc = _current()
            if tc is not None:
                extra = dict(kwargs.get("extra") or {})
                extra.setdefault("trace_id", tc.trace_id)
                kwargs = dict(kwargs)
                kwargs["extra"] = extra
        except Exception:  # pragma: no cover — obs not available
            pass
        return msg, kwargs
