"""T-25/BB-117 — spend must be attributed to the model that actually SERVED a
turn, not the primary client's configured `model` attribute.

``ResilientClient.model`` is captured once from the primary at construction
(``resilience.py``) and never updated as the fallback chain walks past it, so
the loop's cost-tracking read (``getattr(self._turn_client, "model", ...)``)
booked failover spend against the primary -- at the primary's price -- even
though the primary served zero tokens for that turn.

Covers:
  - Direct ResilientClient.generate(): served_model reflects the fallback,
    and CostTracker attribution follows it (the real observed behaviour that
    matters, not just the field's value).
  - Direct ResilientClient.generate_stream(): the happy-path stamp (streaming
    bypasses _call_with_chain entirely -- a stamp added only there would miss
    almost every real successful turn).
  - Direct ResilientClient.generate_stream() failing over mid-stream-setup to
    the fallback chain: served_model still reflects who actually served it.
  - End-to-end through a real ExecutionLoop turn: pins loop.py's read site,
    not just the resilience-layer plumbing in isolation.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from hachi.core.cost import CostTracker
from hachi.core.llm import BaseLLMClient, LLMResponse, Message, TokenUsage
from hachi.core.loop import ExecutionLoop, ToolRegistry
from hachi.core.resilience import HealthRecorder, ResilientClient, RetryPolicy
from hachi.core.sub_agent import SubAgentMemory

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

_EMPTY_SCHEMA = {"type": "object", "properties": {}}


def _noop_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        {"name": "noop", "description": "noop", "input_schema": _EMPTY_SCHEMA},
        lambda **_kw: "ok",
    )
    return registry


class _Boom(Exception):
    """A transient provider failure (503 -- retryable under the shared taxonomy)."""

    status_code = 503


class _FakeLLM(BaseLLMClient):
    """Configurable fake client: can fail generate() and/or generate_stream()."""

    def __init__(
        self,
        model: str,
        *,
        fail_generate: bool = False,
        fail_stream: bool = False,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._fail_generate = fail_generate
        self._fail_stream = fail_stream
        self.generate_calls = 0
        self.stream_calls = 0

    def generate(self, messages: list[Message], **kw: Any) -> LLMResponse:
        self.generate_calls += 1
        if self._fail_generate:
            raise _Boom("down")
        return LLMResponse(
            content=f"ok-{self.model}", usage=TokenUsage(input_tokens=10, output_tokens=5)
        )

    def generate_stream(  # type: ignore[override]
        self, messages: list[Message], **kw: Any
    ) -> Generator[str, None, LLMResponse]:
        self.stream_calls += 1
        if self._fail_stream:
            raise _Boom("down")
        yield f"ok-{self.model}"
        return LLMResponse(
            content=f"ok-{self.model}", usage=TokenUsage(input_tokens=10, output_tokens=5)
        )

    def list_models(self) -> list[str]:
        return [self.model]


def _policy(**kw: Any) -> RetryPolicy:
    return RetryPolicy(max_attempts=1, backoff_base_s=0.0, **kw)


# ---------------------------------------------------------------------------
# 1. Direct ResilientClient.generate() -- non-streaming failover
# ---------------------------------------------------------------------------


def test_cost_is_attributed_to_the_fallback_that_served_the_turn() -> None:
    """When the primary fails and a fallback serves the turn, spend must be booked
    against the FALLBACK -- the real observed behaviour (CostTracker), not just
    the served_model field in isolation.
    """
    policy = _policy()
    primary = _FakeLLM("expensive-primary", fail_generate=True)
    fallback = _FakeLLM("cheap-fallback")

    rc = ResilientClient(
        inner=primary,
        policy=policy,
        recorder=HealthRecorder("a", policy),
        fallbacks=[(fallback, HealthRecorder("b", policy))],
    )

    response = rc.generate([Message(role="user", content="hi")])
    assert response.served_model == "cheap-fallback"

    # The behaviour that matters: the books.
    tracker = CostTracker()
    model_name = response.served_model or getattr(rc, "model", "unknown")
    tracker.record(model_name, response.usage)

    per_model = tracker.per_model_snapshot()
    assert "cheap-fallback" in per_model
    assert "expensive-primary" not in per_model, "spend booked against a model that served nothing"


# ---------------------------------------------------------------------------
# 2. Direct ResilientClient.generate_stream() -- happy-path stamp
# ---------------------------------------------------------------------------


def test_served_model_stamped_on_streaming_success_path() -> None:
    """generate_stream() bypasses _call_with_chain entirely on the happy path
    (resilience.py) -- a stamp added only inside _call_with_chain would leave
    every successful streaming turn (almost every real turn) unstamped.
    """
    policy = _policy()
    primary = _FakeLLM("primary-model")
    rc = ResilientClient(inner=primary, policy=policy, recorder=HealthRecorder("a", policy))

    gen = rc.generate_stream([Message(role="user", content="hi")])
    chunks: list[str] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as si:
        response = si.value

    assert chunks == ["ok-primary-model"]
    assert response.served_model == "primary-model"


# ---------------------------------------------------------------------------
# 3. Direct ResilientClient.generate_stream() -- fails over to the chain
# ---------------------------------------------------------------------------


def test_served_model_stamped_when_streaming_fails_over_to_fallback() -> None:
    """A pre-stream primary failure degrades to generate() over the fallback
    chain (resilience.py); served_model must reflect the FALLBACK, not the
    primary that never produced any output.
    """
    policy = _policy()
    primary = _FakeLLM("expensive-primary", fail_stream=True)
    fallback = _FakeLLM("cheap-fallback")

    rc = ResilientClient(
        inner=primary,
        policy=policy,
        recorder=HealthRecorder("a", policy),
        fallbacks=[(fallback, HealthRecorder("b", policy))],
    )

    gen = rc.generate_stream([Message(role="user", content="hi")])
    chunks: list[str] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as si:
        response = si.value

    assert chunks == ["ok-cheap-fallback"]
    assert response.served_model == "cheap-fallback"


# ---------------------------------------------------------------------------
# 4. End-to-end through a real ExecutionLoop turn -- pins loop.py's read site
# ---------------------------------------------------------------------------


def test_end_to_end_loop_attributes_cost_to_the_fallback_after_failover() -> None:
    """A full ExecutionLoop.run() turn whose primary fails (pre-stream,
    transient) and whose fallback serves the turn must record spend against
    the FALLBACK in the loop's own CostTracker -- this is what actually pins
    loop.py's read site (getattr(self._turn_client, "model", ...) previously),
    not just the resilience-layer plumbing exercised in isolation above.
    """
    tracker = CostTracker()
    policy = _policy()
    primary = _FakeLLM("expensive-primary", fail_stream=True)
    fallback = _FakeLLM("cheap-fallback")

    rc = ResilientClient(
        inner=primary,
        policy=policy,
        recorder=HealthRecorder("a", policy),
        fallbacks=[(fallback, HealthRecorder("b", policy))],
    )

    loop = ExecutionLoop(
        rc,
        _noop_registry(),
        SubAgentMemory(),
        base_system_prompt="x",
        cost_tracker=tracker,
        max_wall_seconds=0.0,
    )
    loop.add_user_message("hi")
    result = loop.run(max_iterations=5)

    assert "ok-cheap-fallback" in result

    per_model = tracker.per_model_snapshot()
    assert "cheap-fallback" in per_model, f"expected fallback in {list(per_model)}"
    assert "expensive-primary" not in per_model, (
        f"spend booked against the primary, which served nothing: {list(per_model)}"
    )
