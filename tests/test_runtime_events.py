from tokoboros.core.runtime_events import TOOL_CALL_COMPLETED, TOOL_CALL_STARTED, adapt_event


def test_tool_call_adapts_without_permission_side_effects() -> None:
    envelopes = adapt_event("tool_call", {"name": "read_file", "input": {"path": "x"}})

    assert len(envelopes) == 1
    assert envelopes[0].primary.name == TOOL_CALL_STARTED


def test_tool_result_adapts_without_permission_side_effects() -> None:
    envelopes = adapt_event("tool_result", {"name": "read_file", "result": "ok"})

    assert len(envelopes) == 1
    assert envelopes[0].primary.name == TOOL_CALL_COMPLETED
