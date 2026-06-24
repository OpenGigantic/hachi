"""Curated seed corpus of deterministic replay cases for ``hachi eval run``.

This is an intentional SEED — not exhaustive coverage.  Every case here
exercises a specific, meaningful structural property of the agent loop:
  - plain text reply with no tool calls,
  - single-tool call then summarise,
  - multi-step (two-tool) trajectory,
  - a no-error smoke test on a longer LLM script.

Grow this corpus as the product matures.  Add domain-specific cases (real tool
workflows, memory recall, budget checks, multi-turn patterns, etc.).  Each
``ReplayCase`` is a plain dataclass — JSON/YAML round-trips are trivial to add
when a file-based suite registry is needed.

Note on cost assertions
-----------------------
``ScriptedLLMClient`` has no real cost meter so ``TurnTrajectory.cost_usd`` is
always ``0.0`` for replay-based runs.  ``max_cost_usd`` assertions are therefore
intentionally ABSENT from this corpus — checking a threshold against a synthetic
``0.0`` value is meaningless.  Use a unit test with an explicit synthetic
``TurnTrajectory`` if you need to exercise the cost-assertion code path.
"""

from __future__ import annotations

from hachi.core.eval.replay import ReplayCase, ToolSpec
from hachi.core.eval.runner import EvalCase, no_error, output_contains, tool_called
from hachi.core.llm import LLMResponse, ToolCall

# ---------------------------------------------------------------------------
# Helper — build a trivial no-args tool callable
# ---------------------------------------------------------------------------


def _noop_tool(result: str = "ok") -> object:
    """Return a zero-argument callable that returns ``result``."""
    # The loop calls tools as func(**tool_call.input); the scripted inputs
    # here are always {} so the lambda receives no keyword arguments.
    return lambda **_kw: result


# ---------------------------------------------------------------------------
# DEFAULT_REPLAY_SUITE
# ---------------------------------------------------------------------------

DEFAULT_REPLAY_SUITE: list[ReplayCase] = [
    # ------------------------------------------------------------------
    # 1. Plain text reply — no tool calls; assert the scripted text comes
    #    through verbatim and the turn completes without error.
    # ------------------------------------------------------------------
    ReplayCase(
        case=EvalCase(
            name="plain_text_reply",
            user_input="Say the word 'pong' back to me.",
            assertions=[
                no_error(),
                output_contains("pong"),
            ],
        ),
        llm_script=[LLMResponse(content="pong")],
    ),
    # ------------------------------------------------------------------
    # 2. Single-tool call — the LLM script calls "fetch_summary" then
    #    emits a final text reply; assert tool fired + no error.
    # ------------------------------------------------------------------
    ReplayCase(
        case=EvalCase(
            name="single_tool_call",
            user_input="Fetch a summary for me.",
            assertions=[
                no_error(),
                tool_called("fetch_summary"),
                output_contains("Summary received"),
            ],
        ),
        llm_script=[
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc1", name="fetch_summary", input={})],
            ),
            LLMResponse(content="Summary received."),
        ],
        tools=[ToolSpec(name="fetch_summary", callable=_noop_tool("summary data"))],
    ),
    # ------------------------------------------------------------------
    # 3. Two-tool trajectory — LLM calls "step_one" then "step_two" in
    #    separate iterations then summarises; assert both tools ran.
    # ------------------------------------------------------------------
    ReplayCase(
        case=EvalCase(
            name="two_tool_trajectory",
            user_input="Run step_one then step_two.",
            assertions=[
                no_error(),
                tool_called("step_one"),
                tool_called("step_two"),
                output_contains("done"),
            ],
        ),
        llm_script=[
            # Iteration 1: call step_one
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc_a", name="step_one", input={})],
            ),
            # Iteration 2: call step_two
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc_b", name="step_two", input={})],
            ),
            # Iteration 3: final text
            LLMResponse(content="All done."),
        ],
        tools=[
            ToolSpec(name="step_one", callable=_noop_tool("step_one result")),
            ToolSpec(name="step_two", callable=_noop_tool("step_two result")),
        ],
    ),
    # ------------------------------------------------------------------
    # 4. Longer script smoke — three LLM turns finishing with a reply;
    #    primarily checks the loop handles a multi-turn script cleanly
    #    (no_error is the critical assertion here).
    # ------------------------------------------------------------------
    ReplayCase(
        case=EvalCase(
            name="multi_turn_smoke",
            user_input="Do a multi-step calculation.",
            assertions=[
                no_error(),
                output_contains("42"),
            ],
        ),
        llm_script=[
            LLMResponse(content="", tool_calls=[ToolCall(id="tc1", name="calc", input={})]),
            LLMResponse(content="", tool_calls=[ToolCall(id="tc2", name="calc", input={})]),
            LLMResponse(content="The answer is 42."),
        ],
        tools=[ToolSpec(name="calc", callable=_noop_tool("21"))],
    ),
    # ------------------------------------------------------------------
    # 5. No-tool output_contains check — ensures the scripted output
    #    substring matching works end-to-end without any tool involvement.
    # ------------------------------------------------------------------
    ReplayCase(
        case=EvalCase(
            name="output_contains_smoke",
            user_input="What is the capital of France?",
            assertions=[
                no_error(),
                output_contains("Paris"),
            ],
        ),
        llm_script=[LLMResponse(content="The capital of France is Paris.")],
    ),
]
