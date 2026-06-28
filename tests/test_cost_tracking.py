"""Tests for per-session token accounting and cost estimation (B2)."""

from __future__ import annotations

from hachi.core.cost import CostTracker, pricing_for
from hachi.core.llm import LLMResponse, TokenUsage
from hachi.core.runtime_events import USAGE_UPDATED, adapt_event


def test_pricing_longest_prefix_match() -> None:
    # Dated model ids resolve to their family price.
    assert pricing_for("claude-opus-4-8-20260101") == (5.0, 25.0)
    assert pricing_for("claude-haiku-4-5") == (1.0, 5.0)
    assert pricing_for("gpt-4o-mini") == (0.15, 0.60)
    # Unknown models fall back to a default rather than crashing.
    assert pricing_for("some-unknown-model") == (1.0, 5.0)


def test_opus_4_family_priced_at_current_rate_not_legacy() -> None:
    # BB-27 regression: the Opus-4 family was mispriced at the legacy
    # Claude-3-Opus $15/$75 rate, inflating every Opus user's displayed cost 3x.
    # Current Opus (4.5+) is $5/$25 per MTok (Anthropic public pricing).
    assert pricing_for("claude-opus-4-8") == (5.0, 25.0)
    assert pricing_for("claude-opus-4-8-20260101") == (5.0, 25.0)
    assert pricing_for("claude-opus-4") == (5.0, 25.0)
    # Cheaper tiers must stay unchanged.
    assert pricing_for("claude-sonnet-4") == (3.0, 15.0)
    assert pricing_for("claude-haiku-4-5") == (1.0, 5.0)


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


def test_cost_tracker_uses_per_model_cache_multiplier_from_registry() -> None:
    # BB-28 regression: OpenAI cached input is ~50% off (0.5x), NOT Anthropic's
    # 90% off (0.1x). With a registry, record() must honor each model's
    # cache_read_mult so the OpenAI cached-token discount is priced correctly.
    from hachi.core.routing.registry import ModelRegistry

    reg = ModelRegistry.build(object(), None)
    tracker = CostTracker(registry=reg)
    # 1M cached-read tokens on gpt-4o ($2.50/M in): 0.5x => $1.25 (was $0.25 at 0.1x).
    tracker.record("gpt-4o", TokenUsage(input_tokens=0, cache_read_tokens=1_000_000))
    assert abs(tracker.snapshot().estimated_cost_usd - 1.25) < 1e-9
    # Anthropic still gets the 0.1x discount via its descriptor default.
    tracker2 = CostTracker(registry=reg)
    tracker2.record("claude-haiku-4-5", TokenUsage(input_tokens=0, cache_read_tokens=1_000_000))
    assert abs(tracker2.snapshot().estimated_cost_usd - 0.10) < 1e-9


def test_record_background_usage_writes_to_daily_ledger(tmp_path) -> None:
    # BB-31 regression: background work (fact extraction, durable titling) runs
    # outside the per-session CostTracker. Its spend must still hit the durable daily
    # ledger so /spend and the daily spend cap (which read this ledger) account for it.
    import datetime

    from hachi.core.cost import record_background_usage
    from hachi.core.session_store import SessionStore

    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    # haiku $1/M in, $5/M out: 1000 in + 100 out = $0.001 + $0.0005 = $0.0015
    cost = record_background_usage(
        "claude-haiku-4-5",
        TokenUsage(input_tokens=1000, output_tokens=100),
        store=store,
    )
    assert abs(cost - 0.0015) < 1e-9
    today = datetime.date.today().isoformat()
    assert abs(store.get_daily_spend(today) - 0.0015) < 1e-9


def test_extract_facts_records_background_spend(monkeypatch) -> None:
    # BB-31: extract_facts must report its LLM spend to the ledger via
    # record_background_usage (otherwise the extraction call is invisible spend).
    import hachi.core.memory as mem

    captured: dict[str, object] = {}

    def _fake_record(model: str, usage: TokenUsage, **_kw: object) -> float:
        captured["model"] = model
        captured["input_tokens"] = usage.input_tokens
        return 0.0

    monkeypatch.setattr("hachi.core.cost.record_background_usage", _fake_record)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeLLM:
        model = "claude-haiku-4-5"

        def generate(self, **_kwargs: object) -> LLMResponse:
            return LLMResponse(
                content='{"user_facts": [], "workspace_facts": [], "agent_facts": []}',
                usage=TokenUsage(input_tokens=1234, output_tokens=56),
            )

    class _FakeVault:
        def upsert_user_fact(self, *_a: object, **_k: object) -> None: ...
        def upsert_fact(self, *_a: object, **_k: object) -> None: ...
        def upsert_agent_fact(self, *_a: object, **_k: object) -> None: ...

    mem.extract_facts(
        "The user said their name is Aditya and they prefer Python.",
        vault=_FakeVault(),
        llm_client=_FakeLLM(),
    )
    assert captured.get("model") == "claude-haiku-4-5"
    assert captured.get("input_tokens") == 1234


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
