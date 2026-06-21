"""Tests for SuspendWorkflow + suspend handling in loop.py."""

from __future__ import annotations

import pytest

from hachi.core.loop import SuspendWorkflow
from hachi.tools.workflow_tools import register_workflow_tools

# ---------------------------------------------------------------------------
# SuspendWorkflow exception contract
# ---------------------------------------------------------------------------


class TestSuspendWorkflow:
    def test_basic_construction(self):
        exc = SuspendWorkflow(reason="waiting for approval")
        assert exc.reason == "waiting for approval"
        assert exc.wait_kind == "user"
        assert exc.wait_key == ""

    def test_full_construction(self):
        exc = SuspendWorkflow(
            reason="waiting for event",
            wait_kind="event",
            wait_key="session.*",
        )
        assert exc.reason == "waiting for event"
        assert exc.wait_kind == "event"
        assert exc.wait_key == "session.*"

    def test_is_exception(self):
        exc = SuspendWorkflow(reason="test")
        assert isinstance(exc, Exception)

    def test_str_is_reason(self):
        exc = SuspendWorkflow(reason="some reason")
        assert str(exc) == "some reason"

    def test_wait_kinds(self):
        for kind in ("user", "event", "time", "webhook"):
            exc = SuspendWorkflow(reason="r", wait_kind=kind, wait_key="k")
            assert exc.wait_kind == kind

    def test_raises_and_catches(self):
        with pytest.raises(SuspendWorkflow) as exc_info:
            raise SuspendWorkflow(reason="paused", wait_kind="user", wait_key="req-001")
        caught = exc_info.value
        assert caught.reason == "paused"
        assert caught.wait_kind == "user"
        assert caught.wait_key == "req-001"


# ---------------------------------------------------------------------------
# workflow_tools.py: register_workflow_tools
# ---------------------------------------------------------------------------


class MockRegistry:
    """Minimal ToolRegistry stub for testing register_workflow_tools."""

    def __init__(self) -> None:
        self.specs: list[dict] = []
        self.handlers: dict[str, object] = {}

    def register(self, spec: dict, handler: object) -> None:
        self.specs.append(spec)
        self.handlers[spec["name"]] = handler


class TestRegisterWorkflowTools:
    def test_registers_core_suspend_workflow(self):
        reg = MockRegistry()
        register_workflow_tools(reg)
        assert any(s["name"] == "core_suspend_workflow" for s in reg.specs)

    def test_tool_spec_has_required_fields(self):
        reg = MockRegistry()
        register_workflow_tools(reg)
        spec = next(s for s in reg.specs if s["name"] == "core_suspend_workflow")
        assert "description" in spec
        assert "input_schema" in spec
        schema = spec["input_schema"]
        assert "reason" in schema["properties"]
        assert "wait_kind" in schema["properties"]
        assert "wait_key" in schema["properties"]
        assert "reason" in schema["required"]

    def test_tool_spec_wait_kind_has_enum(self):
        reg = MockRegistry()
        register_workflow_tools(reg)
        spec = next(s for s in reg.specs if s["name"] == "core_suspend_workflow")
        kinds = spec["input_schema"]["properties"]["wait_kind"]["enum"]
        assert set(kinds) == {"user", "event", "time", "webhook"}

    def test_description_honest_no_auto_wake_promise(self):
        """Ensure the description no longer promises automatic wake-up."""
        reg = MockRegistry()
        register_workflow_tools(reg)
        spec = next(s for s in reg.specs if s["name"] == "core_suspend_workflow")
        desc = spec["description"].lower()
        # Old dishonest description contained "automatically wake up"
        assert "automatically wake up" not in desc

    def test_handler_raises_suspend_workflow(self):
        reg = MockRegistry()
        register_workflow_tools(reg)
        handler = reg.handlers["core_suspend_workflow"]

        with pytest.raises(SuspendWorkflow) as exc_info:
            handler(reason="need approval", wait_kind="user", wait_key="req-1")

        exc = exc_info.value
        assert exc.reason == "need approval"
        assert exc.wait_kind == "user"
        assert exc.wait_key == "req-1"

    def test_handler_defaults_for_wait_kind_and_key(self):
        reg = MockRegistry()
        register_workflow_tools(reg)
        handler = reg.handlers["core_suspend_workflow"]

        with pytest.raises(SuspendWorkflow) as exc_info:
            handler(reason="minimal")

        exc = exc_info.value
        assert exc.wait_kind == "user"
        assert exc.wait_key == ""

    def test_handler_event_kind(self):
        reg = MockRegistry()
        register_workflow_tools(reg)
        handler = reg.handlers["core_suspend_workflow"]

        with pytest.raises(SuspendWorkflow) as exc_info:
            handler(reason="waiting for event", wait_kind="event", wait_key="session.*")

        exc = exc_info.value
        assert exc.wait_kind == "event"
        assert exc.wait_key == "session.*"
