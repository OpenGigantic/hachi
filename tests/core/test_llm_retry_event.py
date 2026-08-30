"""T-24/BB-116 — the LLM_RETRY runtime event survives the retry-layer collapse.

Before this task, `core/loop.py`'s own (now-deleted) retry loop inside
`_stream_generate` emitted `LLM_RETRY` directly whenever IT retried a
pre-output streaming failure. Retries now live entirely inside
`ResilientClient` (`core/resilience.py`); this file locks that the
user-visible "retrying..." signal the UI (and any other `on_event`
consumer) depends on still fires -- via `ResilientClient.on_retry`, wired
up by `ExecutionLoop._wire_retry_events` and triggered by
`_emit_llm_retry`.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from hachi.core.llm import BaseLLMClient, LLMResponse
from hachi.core.loop import ExecutionLoop, ToolRegistry
from hachi.core.resilience import HealthRecorder, ResilientClient, RetryPolicy
from hachi.core.runtime_events import LLM_RETRY
from hachi.core.sub_agent import SubAgentMemory


class _FailsOnceThenSucceeds(BaseLLMClient):
    """Fails the first call with a transient (retryable) error, succeeds on the 2nd."""

    def __init__(self) -> None:
        self.model = "m"
        self.max_tokens = 4096
        self.calls = 0

    def _maybe_boom(self) -> None:
        if self.calls == 1:
            exc = RuntimeError("service unavailable")
            exc.status_code = 503  # type: ignore[attr-defined]
            raise exc

    def generate(self, *a: Any, **kw: Any) -> LLMResponse:
        self.calls += 1
        self._maybe_boom()
        return LLMResponse(content="ok")

    def generate_stream(self, *a: Any, **kw: Any) -> Generator[str, None, LLMResponse]:
        self.calls += 1
        self._maybe_boom()
        yield "ok"
        return LLMResponse(content="ok")


def test_llm_retry_event_fires_from_resilient_client_streaming_retry() -> None:
    """A pre-output primary retry (no fallback needed) still emits LLM_RETRY.

    Locks the re-homing described in T-24 step 5: LLM_RETRY now originates
    from ResilientClient's on_retry hook (fired inside its own pre-output
    streaming retry), not from a retry loop inside ExecutionLoop.
    """
    events: list[tuple[str, dict]] = []
    inner = _FailsOnceThenSucceeds()
    policy = RetryPolicy(max_attempts=3, backoff_base_s=0.0)
    client = ResilientClient(inner=inner, policy=policy, recorder=HealthRecorder("p", policy))

    loop = ExecutionLoop(
        client,
        ToolRegistry(),
        SubAgentMemory(),
        base_system_prompt="x",
        on_event=lambda et, d: events.append((et, d)),
        max_wall_seconds=0.0,
    )
    loop.add_user_message("go")
    result = loop.run(max_iterations=3)

    assert result == "ok"
    assert inner.calls == 2, f"expected exactly one retry (2 calls), got {inner.calls}"
    retry_events = [e for e in events if e[0] == LLM_RETRY]
    assert retry_events, "expected an LLM_RETRY event on the pre-output retry"
    assert retry_events[0][1]["attempt"] == 1


def test_llm_retry_event_fires_from_fallback_chain_retry() -> None:
    """A retry inside a FALLBACK's own resilient_call() attempt also emits LLM_RETRY.

    Exercises the other on_failover call site: _call_with_chain's
    resilient_call(..., on_failover=self.on_retry) for a chain element other
    than the primary (the primary streaming path is covered by the test
    above).
    """
    events: list[tuple[str, dict]] = []

    class _AlwaysFailsPrimary(BaseLLMClient):
        model = "primary"
        max_tokens = 4096

        def generate(self, *a: Any, **kw: Any) -> LLMResponse:
            raise RuntimeError("boom")  # pragma: no cover -- only generate_stream is driven

        def generate_stream(self, *a: Any, **kw: Any) -> Generator[str, None, LLMResponse]:
            exc = RuntimeError("service unavailable")
            exc.status_code = 503  # type: ignore[attr-defined]
            raise exc
            yield ""  # pragma: no cover -- unreachable, satisfies generator typing

    fallback = _FailsOnceThenSucceeds()
    policy = RetryPolicy(max_attempts=3, backoff_base_s=0.0)
    client = ResilientClient(
        inner=_AlwaysFailsPrimary(),
        policy=policy,
        recorder=HealthRecorder("primary", policy),
        fallbacks=[(fallback, HealthRecorder("fallback", policy))],
    )

    loop = ExecutionLoop(
        client,
        ToolRegistry(),
        SubAgentMemory(),
        base_system_prompt="x",
        on_event=lambda et, d: events.append((et, d)),
        max_wall_seconds=0.0,
    )
    loop.add_user_message("go")
    result = loop.run(max_iterations=3)

    assert result == "ok"
    assert fallback.calls == 2, f"expected the fallback to retry once, got {fallback.calls}"
    retry_events = [e for e in events if e[0] == LLM_RETRY]
    assert retry_events, "expected an LLM_RETRY event on the fallback's own retry"


def test_loop_module_has_no_second_retry_predicate() -> None:
    """Acceptance criterion: grep-equivalent for the deleted loop-level constants.

    `_RETRYABLE_STATUS`, `_RETRYABLE_HINTS`, `_MAX_LLM_RETRIES`,
    `_LLM_RETRY_BASE_DELAY`, and `_is_retryable_llm_error` must all be gone
    from core/loop.py -- resilience.is_transient is the sole predicate.
    """
    import hachi.core.loop as loop_mod

    for name in (
        "_RETRYABLE_STATUS",
        "_RETRYABLE_HINTS",
        "_MAX_LLM_RETRIES",
        "_LLM_RETRY_BASE_DELAY",
        "_is_retryable_llm_error",
    ):
        assert not hasattr(loop_mod, name), f"core/loop.py still defines {name}"
