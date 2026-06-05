"""A small in-memory sliding-window rate limiter.

Used to protect the externally-reachable surfaces (channel inbound webhook,
OpenAI-compatible endpoint) from abuse and from runaway cost — each inbound
message can boot/keep an agent session, so a flood is expensive.

Pure and thread-safe; ``allow`` accepts an injectable ``now`` so it is fully
unit-testable without sleeping.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Allow at most ``max_requests`` per ``window_seconds`` per key."""

    def __init__(self, max_requests: int, window_seconds: float):
        self._max = max_requests
        self._window = float(window_seconds)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Record a request for ``key`` and return whether it is within the limit."""
        if self._max <= 0:
            return True  # 0 or negative = disabled
        current = now if now is not None else time.monotonic()
        cutoff = current - self._window
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(current)
            return True
