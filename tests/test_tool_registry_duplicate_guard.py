"""BB-111: ToolRegistry.register silently overwrote handlers and appended duplicate
schemas, so a tool's schema could come from one mount and its handler from another."""

from __future__ import annotations

import pytest

from hachi.core.loop import ToolRegistry

_SCHEMA = {"name": "demo", "input_schema": {"type": "object", "properties": {}}}


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register(_SCHEMA, lambda: "first")
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_SCHEMA, lambda: "second")


def test_duplicate_registration_leaves_registry_unchanged() -> None:
    """A rejected registration must not half-apply."""
    reg = ToolRegistry()
    reg.register(_SCHEMA, lambda: "first", group="core")

    with pytest.raises(ValueError):
        reg.register(_SCHEMA, lambda: "second", group="integrations")

    assert reg._tools["demo"]() == "first"
    assert len(reg._schemas) == 1
    assert reg._groups["demo"] == "core"


def test_unregister_then_register_is_allowed() -> None:
    """The sanctioned re-mount path (used by the MCP lifecycle) still works."""
    reg = ToolRegistry()
    reg.register(_SCHEMA, lambda: "first")
    assert reg.unregister(["demo"]) == 1
    reg.register(_SCHEMA, lambda: "second")
    assert reg._tools["demo"]() == "second"
    assert len(reg._schemas) == 1


def test_update_handler_still_works() -> None:
    """update_handler is the sanctioned callable swap — 3 production call sites."""
    reg = ToolRegistry()
    reg.register(_SCHEMA, lambda: "first")
    assert reg.update_handler("demo", lambda: "second") is True
    assert reg._tools["demo"]() == "second"
    assert len(reg._schemas) == 1
