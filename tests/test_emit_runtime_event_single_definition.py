"""BB-91: boot_agent defined emit_runtime_event twice. The six call sites that pass
it BY VALUE (all in the permission/approval subsystem) captured the first
definition and never followed the EventBus rebind."""

from __future__ import annotations

import ast
import inspect

import hachi.gateway.orchestrator as orch
from hachi.core.event_bus import EventBus
from hachi.core.runtime_events import RuntimeEvent


def _defs_named(func_name: str, target: str) -> list[int]:
    """Line numbers of every `def <target>` nested directly in `<func_name>`."""
    tree = ast.parse(inspect.getsource(orch))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return [
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.FunctionDef) and child.name == target
            ]
    raise AssertionError(f"{func_name} not found in {orch.__name__}")


def test_boot_agent_defines_each_event_closure_once() -> None:
    for target in ("emit_runtime_event", "relay_event"):
        lines = _defs_named("boot_agent", target)
        assert len(lines) == 1, (
            f"boot_agent defines {target!r} {len(lines)} times (lines {lines}) — "
            "expected exactly 1. A second definition creates a new function object and "
            "strands every reference already passed by value."
        )


def test_late_bound_bus_reaches_a_reference_captured_before_assignment() -> None:
    """The closure-cell contract the fix depends on, against the real EventBus."""
    seen: list[str] = []

    def build():
        bus = None

        def emit(name: str) -> None:
            event = RuntimeEvent(name=name, payload={}, timestamp="2026-07-19T00:00:00Z")
            if bus is not None:
                bus.publish(event)
            else:
                seen.append(f"direct:{name}")

        captured = emit  # mirrors orchestrator.py:2636/2648/2651
        bus = EventBus()  # mirrors orchestrator.py:2730
        bus.subscribe("*", lambda e: seen.append(f"bus:{e.name}"), mode="sync", name="probe")
        return captured

    build()("permission.requested")
    assert seen == ["bus:permission.requested"], (
        f"reference captured before the bus existed did not publish through it: {seen}"
    )
