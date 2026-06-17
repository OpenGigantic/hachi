"""Per-session token accounting and cost estimation.

Accumulates :class:`~hachi.core.llm.TokenUsage` across a session and
estimates spend from a per-model price table. Pure and thread-safe, so it can be
unit-tested without an agent and read from any thread (the loop records, the TUI
reads). Prices are USD per 1M tokens and are best-effort estimates — they are
meant to give the user live feedback on spend, not billing-grade accuracy.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from hachi.core.llm import TokenUsage

# USD per 1,000,000 tokens, as (input, output). Prefix match on the model id so
# dated variants (e.g. "claude-opus-4-8-20260101") resolve to the family price.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
}
_DEFAULT_PRICING = (1.0, 5.0)

# Anthropic prompt-cache multipliers relative to the base input price.
_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25


def pricing_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD-per-Mtoken for a model id (longest prefix wins)."""
    model = (model or "").lower()
    best: tuple[float, float] | None = None
    best_len = -1
    for prefix, price in MODEL_PRICING.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best if best is not None else _DEFAULT_PRICING


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    """An immutable view of accumulated usage and estimated spend."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    call_count: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "call_count": self.call_count,
        }


class CostTracker:
    """Accumulate token usage and estimate cost across a session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._input = 0
        self._output = 0
        self._cache_read = 0
        self._cache_creation = 0
        self._cost = 0.0
        self._calls = 0

    def record(self, model: str, usage: TokenUsage) -> None:
        """Add one generation's usage, pricing it against ``model``."""
        in_price, out_price = pricing_for(model)
        # The Anthropic API reports input_tokens, cache_read_input_tokens and
        # cache_creation_input_tokens as SEPARATE, mutually-exclusive counters
        # (input_tokens is the fresh, non-cached portion). So they are additive:
        # price fresh input at the base rate, cache reads cheaper, cache writes pricier.
        cost = (
            usage.input_tokens * in_price
            + usage.cache_read_tokens * in_price * _CACHE_READ_MULTIPLIER
            + usage.cache_creation_tokens * in_price * _CACHE_WRITE_MULTIPLIER
            + usage.output_tokens * out_price
        ) / 1_000_000

        with self._lock:
            self._input += usage.input_tokens
            self._output += usage.output_tokens
            self._cache_read += usage.cache_read_tokens
            self._cache_creation += usage.cache_creation_tokens
            self._cost += cost
            self._calls += 1

    def snapshot(self) -> CostSnapshot:
        with self._lock:
            return CostSnapshot(
                input_tokens=self._input,
                output_tokens=self._output,
                cache_read_tokens=self._cache_read,
                cache_creation_tokens=self._cache_creation,
                total_tokens=self._input + self._output + self._cache_read + self._cache_creation,
                estimated_cost_usd=self._cost,
                call_count=self._calls,
            )
