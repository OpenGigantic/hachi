"""BB-89: the sub-agent blocklist named a tool that does not exist, so the two
tools that mutate the schedule store were never blocked."""

from __future__ import annotations

import ast
import pathlib

from hachi.core.loop import ToolRegistry
from hachi.jobs.scheduler import Scheduler, ScheduleStore
from hachi.tools.delegation_tools import (
    _RESERVED_BLOCKED_NAMES,
    BLOCKED_TOOL_NAMES,
    _build_scoped_registry,
)
from hachi.tools.schedule_tools import register_schedule_tools

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "hachi"


def _registered_tool_names() -> set[str]:
    """Every tool-schema `name` literal in the source tree.

    A tool schema is a dict literal with a string `"name"` and either a literal
    `"input_schema"` key or a `**` unpack (the `_CANCEL_ALIAS_SCHEMA` form).
    """
    names: set[str] = set()
    for path in _SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            literal_keys = {
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            has_unpack = any(k is None for k in node.keys)
            if "name" not in literal_keys or not ("input_schema" in literal_keys or has_unpack):
                continue
            # ast.Dict's grammar is `Dict(expr?* keys, expr* values)`: keys and
            # values are always parallel, equal-length lists (a `**` unpack shows
            # up as key=None with its expression in the matching values slot).
            # strict=True turns that real invariant into a loud assertion instead
            # of a silent mispairing if it's ever violated.
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "name"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    names.add(value.value)
    return names


def test_schedule_mutators_are_blocked_from_sub_agents(tmp_path) -> None:
    parent = ToolRegistry()
    register_schedule_tools(parent, Scheduler(ScheduleStore(tmp_path / "schedules.json")))
    assert "create_schedule" in parent._tools  # guard: the parent really mounts them

    # No tool_patterns == the backward-compatible default role (all non-blocked pass).
    scoped = _build_scoped_registry(parent)

    leaked = sorted(scoped._tools)
    assert leaked == [], f"sub-agent can still reach: {leaked}"


def test_every_blocked_name_is_a_real_tool_name() -> None:
    inventory = _registered_tool_names()
    # Sanity-check the scanner itself before trusting its verdict.
    assert "create_schedule" in inventory
    assert "cancel_schedule" in inventory  # the {**BASE, "name": ...} alias form

    stale = sorted(
        name
        for name in BLOCKED_TOOL_NAMES
        if name not in inventory and name not in _RESERVED_BLOCKED_NAMES
    )
    assert stale == [], (
        f"BLOCKED_TOOL_NAMES contains names that match no registered tool: {stale}. "
        "Either the tool was renamed (fix the entry) or it is deliberately reserved "
        "(add it to _RESERVED_BLOCKED_NAMES with a comment)."
    )
