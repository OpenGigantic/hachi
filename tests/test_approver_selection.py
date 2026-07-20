"""BB-90: the RegistryApprover branch in boot_agent's approver selection was a
logical contradiction (elif isinstance(runtime_gate, InteractivePermissionGate)
can only be true when on_permission_request is truthy, but reaching the elif
requires it to be None) and, independently, on_permission_request was passed by
zero call sites in src/ — so every session silently got PolicyApprover. These
tests pin the fix: an explicit `approval_surface` selection signal, asserted at
both the boot-path call site and the selection helper itself."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_gateway_boot_path_requests_registry_approver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gateway boot path must ask boot_agent for the registry-backed approver.

    Regression: the RegistryApprover branch in boot_agent was unreachable AND no
    caller ever selected it, so every gateway session silently got PolicyApprover.
    """
    from hachi.cli import main as cli_main

    captured: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(cli_main, "boot_agent", _spy)

    runtime = cli_main._InProcessGatewayRuntime()
    runtime._boot_session_agent("sess-1")

    assert captured.get("approval_surface") == "gateway"


@pytest.mark.parametrize(
    ("surface", "expected"),
    [("gateway", "RegistryApprover"), (None, "PolicyApprover")],
)
def test_select_action_approver_maps_gateway_surface_to_registry_approver(
    surface: str | None, expected: str
) -> None:
    """Selection is total and explicit: gateway -> registry, anything else -> policy."""
    from hachi.gateway.orchestrator import select_action_approver

    approver = select_action_approver(
        on_permission_request=None,
        approval_surface=surface,
        tier="guided",
        emit=lambda _name, _payload: None,
    )
    assert type(approver).__name__ == expected


def test_handler_still_wins_over_surface() -> None:
    """A supplied handler keeps priority — the CLI/TUI contract is unchanged."""
    from hachi.core.permissions import PermissionDecision
    from hachi.gateway.orchestrator import select_action_approver

    approver = select_action_approver(
        on_permission_request=lambda _req: PermissionDecision(action="deny"),
        approval_surface="gateway",
        tier="guided",
        emit=lambda _name, _payload: None,
    )
    assert type(approver).__name__ == "HandlerApprover"
