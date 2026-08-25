"""T-44 stage: device permissions and the session-scoped action approver.

Moved verbatim from `boot_agent`'s old step "0. Load Device Permissions" and its
later tier-drift reconcile block (the old comment "Bug 7 boot-reconcile"). Those
two blocks are ~35 lines apart in the original file but operate on the exact same
three values (`gate`, `runtime_gate`, `action_approver`), so they are modelled as
two calls over one mutable-in-transit stage rather than being forced into a
single "step 0" stage that would need `config` before `config` exists.

`select_action_approver` and the closures `on_permission_request`/`emit` are NOT
imported here -- they are orchestrator-local (the first) or genuinely
caller-supplied (the rest), so `boot_agent` passes them in explicitly. See the
package docstring for why this direction is deliberate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hachi.core.approvals import ActionApprover
from hachi.core.permissions import (
    InteractivePermissionGate,
    PermissionDecision,
    PermissionGate,
    PermissionRequest,
)

from .stages import PermissionsStage

logger = logging.getLogger(__name__)

SelectApprover = Callable[..., ActionApprover]


def build_permissions(
    *,
    permissions_file: Path,
    on_permission_request: Callable[[PermissionRequest], PermissionDecision] | None,
    approval_surface: str | None,
    emit: Callable[[str, dict[str, Any]], None],
    select_approver: SelectApprover,
) -> PermissionsStage:
    """Old step 0 (`:2671-2690`), moved verbatim.

    Args:
        permissions_file: Path to `device-permissions.og`.
        on_permission_request: TUI-style approval handler, if connected.
        approval_surface: Forwarded to `select_approver` unchanged.
        emit: The boot's `emit_runtime_event` (still defined inline in
            `boot_agent` -- see the package docstring).
        select_approver: `hachi.gateway.orchestrator.select_action_approver`,
            passed in by `boot_agent`.
    """
    gate = PermissionGate(permissions_file)
    runtime_gate: Any = gate

    if on_permission_request:
        runtime_gate = InteractivePermissionGate(
            base_gate=gate,
            approval_handler=on_permission_request,
            event_emitter=emit,
        )

    # Build the session-scoped ActionApprover for destructive tool actions.
    # Selection priority is documented on select_action_approver() itself.
    action_approver: ActionApprover = select_approver(
        on_permission_request=on_permission_request,
        approval_surface=approval_surface,
        tier=gate.tier,
        emit=emit,
    )

    return PermissionsStage(gate=gate, runtime_gate=runtime_gate, action_approver=action_approver)


def reconcile_permissions_with_config(
    perms: PermissionsStage,
    *,
    config: dict[str, Any],
    permissions_file: Path,
    on_permission_request: Callable[[PermissionRequest], PermissionDecision] | None,
    approval_surface: str | None,
    emit: Callable[[str, dict[str, Any]], None],
    select_approver: SelectApprover,
) -> PermissionsStage:
    """Old "Bug 7 boot-reconcile" block (`:2706-2734`), moved verbatim.

    If `settings.toml`'s `[permissions].tier` drifts from the tier the `.og`
    gate reports (e.g. `update_permissions()` wrote the `.og` but a crash
    prevented the in-process gate from being rebuilt), rebuild the gate +
    approver to match config before any tool registration reads permissions.

    Preserves the original's exact reassign-in-place semantics on the (narrow,
    pre-existing) partial-failure path: if `PermissionGate(...)` succeeds but a
    later construction in the same `try` raises, the already-reassigned local
    is kept and only the not-yet-reached ones fall back to *this call's* input
    -- exactly as the original did with `boot_agent`'s own locals, now scoped to
    this function's locals instead. Not a behaviour this task introduces; only
    relocated.
    """
    gate = perms.gate
    runtime_gate = perms.runtime_gate
    action_approver = perms.action_approver

    _config_tier = config.get("permissions", {}).get("tier", "")
    if _config_tier and _config_tier != gate.tier:
        try:
            gate = PermissionGate(permissions_file)
            if on_permission_request:
                runtime_gate = InteractivePermissionGate(
                    base_gate=gate,
                    approval_handler=on_permission_request,
                    event_emitter=emit,
                )
            else:
                runtime_gate = gate
            action_approver = select_approver(
                on_permission_request=on_permission_request,
                approval_surface=approval_surface,
                tier=gate.tier,
                emit=emit,
            )
            logger.info(
                "boot_agent: rebuilt gate to tier=%r (was %r per .og)",
                gate.tier,
                _config_tier,
            )
        except Exception:
            logger.warning("boot_agent: gate reconcile failed", exc_info=True)

    return PermissionsStage(gate=gate, runtime_gate=runtime_gate, action_approver=action_approver)
