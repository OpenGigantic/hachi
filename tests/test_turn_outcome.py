"""T-59 — TurnOutcome value object regression test.

``ExecutionLoop.run()`` used to encode five terminal outcomes (cancelled, time
limit, budget limit, suspended, incomplete) as string prefixes on the returned
text across nine return sites (``core/loop.py``). Callers had no way to
recover *why* a turn ended without ``str.startswith()``-parsing the reply
(e.g. ``core/sub_agent.py:737``'s ``result.startswith(INCOMPLETE_RESULT_PREFIX)``),
and the structured wait condition for ``[WORKFLOW SUSPENDED]`` needed a side
channel (``self._last_suspend``) to avoid being "lost inside the return
string".

``TurnOutcome`` (+ ``TurnStatus``) makes the outcome a value: ``render()``
reproduces the exact legacy strings byte-for-byte (this is a
behaviour-preserving refactor — see the eval corpus and the nine
string-asserting test files this must not disturb), while
``ExecutionLoop.last_outcome`` exposes the structured value alongside the
still-``str``-returning ``run()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from hachi.core.llm import LLMResponse, Message, ToolCall
from hachi.core.loop import ExecutionLoop
from hachi.core.memory import MemoryManager

# ---------------------------------------------------------------------------
# Harness — copied from tests/test_iteration_limit.py's _make_loop() rather
# than invented here, per the regression-law instruction to reuse an existing
# driveable-loop fixture.
# ---------------------------------------------------------------------------


def _make_loop(max_iterations: int = 1) -> tuple[ExecutionLoop, list]:
    """Return a minimal ExecutionLoop that always calls tools (never finishes),
    forcing it to exhaust max_iterations. Also returns the events list."""
    events: list[tuple[str, dict]] = []

    memory = MemoryManager(max_active_messages=50)

    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.execute.return_value = "tool result"

    llm_client = MagicMock()

    loop = ExecutionLoop(
        llm_client=llm_client,
        registry=registry,
        memory=memory,
        base_system_prompt="system",
        on_event=lambda name, payload=None: events.append((name, payload or {})),
    )

    # Make _stream_generate return a tool_use response so the loop keeps iterating.
    # run() now uses streaming generation and no longer calls _generate_with_retry.
    fake_tool_call = ToolCall(id="tc1", name="dummy_tool", input={})
    fake_response = LLMResponse(
        content="partial work done",
        tool_calls=[fake_tool_call],
        stop_reason="tool_use",
        usage={},
    )
    loop._stream_generate = MagicMock(return_value=fake_response)

    # Prime the memory with a user message
    memory.add_message(Message(role="user", content="do something long"))

    return loop, events


def test_turn_outcome_render_is_byte_identical_to_legacy_prefixes() -> None:
    """render() must reproduce the exact strings callers and evals depend on.

    This is the safety net for the nine-site conversion: the eval corpus and
    nine test files assert on these literals, so any drift is a red gate.

    There are exactly FIVE render shapes (not nine) — the "near-duplicate"
    pairs (iteration-boundary vs mid-stream cancel; iteration-boundary vs
    ``_StreamTimedOut`` time-limit) render byte-identical text even though the
    surrounding ``on_event``/``memory.complete_task`` calls around them differ.
    """
    from hachi.core.loop import TurnOutcome, TurnStatus

    # CANCELLED — loop.py sites at the iteration boundary and mid-stream both
    # produce `f"[CANCELLED] {text}" if text else "[CANCELLED]"`. The
    # empty-text case has NO trailing space; a naive
    # `f"[CANCELLED] {text}".strip()` would not reproduce this for other
    # inputs (e.g. text with meaningful trailing whitespace).
    assert TurnOutcome(TurnStatus.CANCELLED, text="").render() == "[CANCELLED]"
    assert TurnOutcome(TurnStatus.CANCELLED, text="hi").render() == "[CANCELLED] hi"
    assert (
        TurnOutcome(TurnStatus.CANCELLED, text="partial work done").render()
        == "[CANCELLED] partial work done"
    )

    # TIME_LIMIT — both the iteration-boundary and _StreamTimedOut mid-stream
    # sites resolve to this same shape once formatted, despite being built
    # from differently-shaped f-string concatenations in the source.
    assert TurnOutcome(TurnStatus.TIME_LIMIT, text="", limit_seconds=30.0).render() == (
        "[TIME LIMIT REACHED] Turn exceeded 30s wall-clock limit."
    )
    assert TurnOutcome(TurnStatus.TIME_LIMIT, text="partial work", limit_seconds=30.0).render() == (
        "[TIME LIMIT REACHED] Turn exceeded 30s wall-clock limit. Partial: partial work"
    )
    # :.0f formatting — a non-integer wall-clock seconds value still renders
    # without decimals, exactly like the original f"{self.max_wall_seconds:.0f}s".
    assert TurnOutcome(TurnStatus.TIME_LIMIT, text="", limit_seconds=0.05).render() == (
        "[TIME LIMIT REACHED] Turn exceeded 0s wall-clock limit."
    )

    # BUDGET_LIMIT — shared by three loop.py sites (session budget, safety
    # ceiling, per-turn budget) AND gateway/orchestrator.py's daily-cap emitter.
    assert (
        TurnOutcome(TurnStatus.BUDGET_LIMIT, reason="cap $1.00 reached").render()
        == "[BUDGET LIMIT REACHED] cap $1.00 reached"
    )

    # SUSPENDED.
    assert TurnOutcome(TurnStatus.SUSPENDED, reason="waiting on x").render() == (
        "[WORKFLOW SUSPENDED] waiting on x"
    )

    # INCOMPLETE — must still literally contain "[INCOMPLETE]" (the eval
    # corpus's max_iterations_cap case has a POSITIVE output_contains
    # assertion on this, not just the five output_not_contains calls in the
    # no_internal_markers_in_clean_output case).
    rendered_incomplete = TurnOutcome(TurnStatus.INCOMPLETE, max_iterations=15).render()
    assert rendered_incomplete == (
        "[INCOMPLETE] The task reached the 15-iteration limit and could not be "
        "fully completed. Reply 'continue' to pick up where it left off."
    )
    assert "[INCOMPLETE]" in rendered_incomplete


def test_turn_outcome_is_addressable_without_string_parsing() -> None:
    """A turn that hits the iteration limit must report it as a value.

    Regression: ExecutionLoop.run() encoded five terminal outcomes as prefixes
    on the returned text across nine return sites; the only way to recover the
    outcome was `result.startswith("[INCOMPLETE]")` (core/sub_agent.py:737).
    """
    from hachi.core.loop import TurnStatus

    loop, _events = _make_loop(max_iterations=1)
    result = loop.run(max_iterations=1)

    outcome = loop.last_outcome
    assert outcome is not None, "run() must expose a structured TurnOutcome"
    assert outcome.status is TurnStatus.INCOMPLETE
    # And the legacy string contract still holds for existing callers.
    assert result.startswith("[INCOMPLETE]")
