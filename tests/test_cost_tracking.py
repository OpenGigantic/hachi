"""Tests for per-session token accounting and cost estimation (B2)."""

from __future__ import annotations

from hachi.core.cost import CostTracker, pricing_for
from hachi.core.llm import LLMResponse, TokenUsage
from hachi.core.runtime_events import USAGE_UPDATED, adapt_event


def test_pricing_longest_prefix_match() -> None:
    # Dated model ids resolve to their family price.
    assert pricing_for("claude-opus-4-8-20260101") == (15.0, 75.0)
    assert pricing_for("claude-haiku-4-5") == (1.0, 5.0)
    assert pricing_for("gpt-4o-mini") == (0.15, 0.60)
    # Unknown models fall back to a default rather than crashing.
    assert pricing_for("some-unknown-model") == (1.0, 5.0)


def test_llm_response_usage_defaults_to_zero() -> None:
    resp = LLMResponse(content="hi")
    assert resp.usage.input_tokens == 0
    assert resp.usage.output_tokens == 0


def test_cost_tracker_accumulates_tokens_and_cost() -> None:
    tracker = CostTracker()
    # 1M input + 1M output on haiku ($1/$5 per M) = $6.00 exactly.
    tracker.record("claude-haiku-4-5", TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000))
    snap = tracker.snapshot()
    assert snap.input_tokens == 1_000_000
    assert snap.output_tokens == 1_000_000
    assert snap.total_tokens == 2_000_000
    assert snap.call_count == 1
    assert abs(snap.estimated_cost_usd - 6.0) < 1e-9


def test_cost_tracker_discounts_cached_input() -> None:
    tracker = CostTracker()
    # All input served from cache (fresh input_tokens=0): priced at 0.1x the rate.
    tracker.record(
        "claude-haiku-4-5",
        TokenUsage(input_tokens=0, cache_read_tokens=1_000_000),
    )
    snap = tracker.snapshot()
    # 1M cached input * $1/M * 0.1 = $0.10
    assert abs(snap.estimated_cost_usd - 0.10) < 1e-9
    assert snap.cache_read_tokens == 1_000_000
    assert snap.total_tokens == 1_000_000  # cache tokens count toward total


def test_cost_tracker_cache_tokens_are_additive_not_subtracted() -> None:
    tracker = CostTracker()
    # 1M fresh input + 1M cache-read on haiku ($1/M in): 1M*1 + 1M*1*0.1 = $1.10.
    tracker.record(
        "claude-haiku-4-5",
        TokenUsage(input_tokens=1_000_000, cache_read_tokens=1_000_000),
    )
    snap = tracker.snapshot()
    assert abs(snap.estimated_cost_usd - 1.10) < 1e-9
    assert snap.total_tokens == 2_000_000


def test_cost_tracker_sums_across_calls() -> None:
    tracker = CostTracker()
    tracker.record("claude-haiku-4-5", TokenUsage(input_tokens=100, output_tokens=50))
    tracker.record("claude-haiku-4-5", TokenUsage(input_tokens=200, output_tokens=10))
    snap = tracker.snapshot()
    assert snap.input_tokens == 300
    assert snap.output_tokens == 60
    assert snap.call_count == 2


def test_snapshot_to_dict_is_serializable() -> None:
    tracker = CostTracker()
    tracker.record("gpt-4o", TokenUsage(input_tokens=10, output_tokens=5))
    data = tracker.snapshot().to_dict()
    assert set(data) >= {"total_tokens", "estimated_cost_usd", "call_count", "cache_read_tokens"}
    assert data["total_tokens"] == 15


def test_usage_event_adapts_to_stable_taxonomy() -> None:
    envelopes = adapt_event("usage", {"total_tokens": 42, "estimated_cost_usd": 0.01})
    assert len(envelopes) == 1
    assert envelopes[0].primary.name == USAGE_UPDATED
    assert envelopes[0].primary.payload["total_tokens"] == 42
    assert "usage" in envelopes[0].legacy_aliases
