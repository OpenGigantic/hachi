"""BB-112: tool groups lived in a hand-maintained orchestrator table
(`_GROUP_BY_TOOL_NAME`) that drifted from the tools it was supposed to describe —
`delegate_parallel_tasks` fell into the "integrations" group instead of "delegation",
so `use_tools("delegation")` silently handed the model two of the three delegation
tools. Same drift class as BB-89 (T-34's sub-agent blocklist): a hand-maintained list
of tool-name strings, unchecked against reality, rots silently.

See docs/audit/2026-07-19/tasks/T-39-tool-groups-at-registration.md.
"""

from __future__ import annotations

from hachi.core.loop import ToolRegistry
from hachi.gateway.orchestrator import _CORE_TOOL_NAMES
from hachi.tools.delegation_tools import (
    DELEGATE_PARALLEL_TASKS_SCHEMA,
    DELEGATE_TASK_SCHEMA,
    LIST_ROLES_SCHEMA,
    register_delegation_tools,
)
from hachi.tools.og_tools import _make_use_tools

# Mirrors the production call at gateway/orchestrator.py's tool_registry.classify(...)
# after the fix: _GROUP_BY_TOOL_NAME is gone, so group_by_name is an empty literal.
_CLASSIFY_KWARGS = dict(
    core_names=set(_CORE_TOOL_NAMES),
    group_by_name={},
    prefix_groups={"google_": "google"},
    default="integrations",
)


def test_all_delegation_tools_share_the_delegation_group() -> None:
    reg = ToolRegistry()
    # Register with group= exactly as production does now (delegation_tools.py's
    # register_delegation_tools passes group="delegation" to all three calls).
    reg.register(DELEGATE_TASK_SCHEMA, lambda: "", group="delegation")
    reg.register(LIST_ROLES_SCHEMA, lambda: "", group="delegation")
    reg.register(DELEGATE_PARALLEL_TASKS_SCHEMA, lambda: "", group="delegation")

    reg.classify(**_CLASSIFY_KWARGS)

    for name in ("delegate_task", "list_roles", "delegate_parallel_tasks"):
        actual = reg._groups[name]
        assert actual == "delegation", f"{name} is in group {actual!r}, expected 'delegation'"


def test_register_delegation_tools_wires_all_three_into_delegation() -> None:
    """End-to-end version of the check above, tied directly to the real registration
    function rather than a hand-built registry: the REAL register_delegation_tools(...)
    call plus the REAL (post-T-39) classify() shape must expose all three delegation
    tools once "delegation" is active -- not just the two the deleted table knew about.

    This is the literal user-visible property: use_tools("delegation") must unlock
    every delegation tool, not a strict subset.
    """
    reg = ToolRegistry()
    register_delegation_tools(reg)
    reg.classify(**_CLASSIFY_KWARGS)

    for name in ("delegate_task", "list_roles", "delegate_parallel_tasks"):
        actual = reg._groups[name]
        assert actual == "delegation", f"{name} is in group {actual!r}, expected 'delegation'"

    activated = {s["name"] for s in reg.get_schemas(active_groups={"delegation"})}
    assert activated == {"delegate_task", "list_roles", "delegate_parallel_tasks"}, (
        f"use_tools('delegation') would only unlock {activated!r}"
    )

    use_tools = _make_use_tools(reg)
    assert use_tools("delegation") == (
        "Activated tool group 'delegation'. Its tools are now available to call."
    )


def test_core_tool_names_all_reference_real_tools() -> None:
    """The one surviving hand-written name table (_CORE_TOOL_NAMES) must resolve to a
    registered tool -- the analogue of T-34's BLOCKED_TOOL_NAMES integrity check
    (tests/test_delegation_blocklist_integrity.py, BB-89).

    _GROUP_BY_TOOL_NAME must not merely be *corrected*, it must be GONE: patching its
    two drifted entries without deleting the table would leave the identical
    hand-maintained-list trap armed for the next tool that gets renamed. Assert both
    halves: the table is deleted, and the table that survives (_CORE_TOOL_NAMES,
    kept deliberately -- see T-39 spec §9) has no drift of its own.
    """
    import hachi.gateway.orchestrator as orchestrator_module

    assert not hasattr(orchestrator_module, "_GROUP_BY_TOOL_NAME"), (
        "_GROUP_BY_TOOL_NAME still exists -- T-39 requires deleting the hand-maintained "
        "group table (each tool now declares group= at its own register() call), not "
        "just correcting its two drifted entries."
    )

    # Reuse the single AST inventory scanner rather than writing a second one (T-34, BB-89).
    from tests.test_delegation_blocklist_integrity import _registered_tool_names

    inventory = _registered_tool_names()
    assert "create_schedule" in inventory  # sanity-check the scanner before trusting it

    stale = sorted(n for n in _CORE_TOOL_NAMES if n not in inventory)
    assert stale == [], f"_CORE_TOOL_NAMES references names that match no registered tool: {stale}"
