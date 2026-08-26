"""T-44 stages: infrastructure objects tool registration needs, and the per-session
budget caps `register_tools` and `build_brain_loop` both read.

Moved verbatim from `boot_agent`'s old step "8. Build infrastructure objects..."
(`:3036-3051`), the `credential_vault`/`rest_adapter` pair (`:3201-3202`, moved
alongside tool-infra here rather than staying split across the vault block --
see `boot.memory`'s module docstring for why that reordering is safe), the
`LeaseManager` construction (`:3209`), and the `[limits]`/env-var budget-cap
parsing (`:3217-3232`).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from hachi.core.checkpoints import CheckpointManager
from hachi.core.credential_vault import CredentialVault
from hachi.core.lease_manager import LeaseManager
from hachi.core.loop import ToolRegistry
from hachi.core.rest_adapter import UniversalRestAdapter
from hachi.core.session_store import SessionStore
from hachi.jobs.scheduler import Scheduler

from .stages import LimitsStage, ToolInfraStage

GetOrCreateSharedScheduler = Callable[[Any, Any], Scheduler]
CoerceFloat = Callable[..., float]
CoerceInt = Callable[..., int]


def build_tool_infrastructure(
    *,
    shared: Any,
    credential_vault: CredentialVault,
    checkpoints_dir: Any,
    schedules_file: Any,
    get_or_create_shared_scheduler: GetOrCreateSharedScheduler,
) -> ToolInfraStage:
    """Moved verbatim from `:3036-3051` + `:3201-3202` + `:3209`.

    Args:
        shared: the `ProcessSharedServices` singleton (`SkillEngineStage.shared`).
        credential_vault: T-58 — previously constructed HERE via
            `CredentialVault(secret_store=secret_store)`; now built by the
            composition root BEFORE `boot.environment.build_integrations`
            runs (several stages earlier than this one) so
            `IntegrationRuntime` can also receive it, and passed in ready-
            made. This keeps exactly one `CredentialVault` construction in
            the whole boot path — this stage only builds the
            `UniversalRestAdapter` that wraps it now.
        checkpoints_dir: `get_hachi_paths().checkpoints_dir`.
        schedules_file: `get_hachi_paths().schedules_file`.
        get_or_create_shared_scheduler: orchestrator-local
            `_get_or_create_shared_scheduler`.
    """
    # These are created before the TOOL_SPECS table so BootDeps can reference
    # fully-constructed objects rather than lazy placeholders.
    tool_registry = ToolRegistry()
    # File-change checkpoints give the user an undo for agent edits (rollback_changes tool).
    checkpoint_manager = CheckpointManager(checkpoints_dir)
    # Proactive scheduling (cron/interval/once) — persisted across restarts.
    # H3: shared across sessions in this process (see _get_or_create_shared_scheduler).
    scheduler = get_or_create_shared_scheduler(shared, schedules_file)
    # Initialize Session Store (Phase 3: cross-session persistence)
    session_store = SessionStore()
    # T-10 / BB-42 recurrence: publish on the process singleton so the gateway's
    # ingress-idempotency hot path (fastapi_app._resolve_session_store) and
    # core/cost.py's daily-spend ledger reuse THIS store instead of opening (and
    # leaking) a fresh sqlite3/WAL connection per call. Overwrites whatever a
    # prior session in this process attached — see the is-still-shared guard in
    # AgentSession.shutdown(), which stops a session's own shutdown from closing
    # this out from under sibling sessions / the gateway.
    shared.session_store = session_store

    rest_adapter = UniversalRestAdapter(tool_registry, credential_vault)

    # P4-01: per-session write-target lease manager (prevents concurrent delegation conflicts).
    lease_manager = LeaseManager()

    return ToolInfraStage(
        tool_registry=tool_registry,
        checkpoint_manager=checkpoint_manager,
        scheduler=scheduler,
        session_store=session_store,
        credential_vault=credential_vault,
        rest_adapter=rest_adapter,
        lease_manager=lease_manager,
    )


def build_limits(
    config: dict[str, Any],
    *,
    coerce_float: CoerceFloat,
    coerce_int: CoerceInt,
) -> LimitsStage:
    """Moved verbatim from `:3217-3232`.

    Per-session safety caps (0 = unlimited): stop a runaway loop from draining
    the user's API budget. Read from the optional `[limits]` config section,
    else env vars.

    Args:
        coerce_float: orchestrator-local `_coerce_float`.
        coerce_int: orchestrator-local `_coerce_int`.
    """
    limits_cfg = config.get("limits", {}) if isinstance(config.get("limits"), dict) else {}
    max_daily_cost_usd = coerce_float(
        limits_cfg.get("max_daily_cost_usd"), os.environ.get("HACHI_MAX_DAILY_COST_USD")
    )
    max_session_cost_usd = coerce_float(
        limits_cfg.get("max_session_cost_usd"), os.environ.get("HACHI_MAX_SESSION_COST_USD")
    )
    max_session_tokens = coerce_int(
        limits_cfg.get("max_session_tokens"), os.environ.get("HACHI_MAX_SESSION_TOKENS")
    )
    max_turn_cost_usd = coerce_float(
        limits_cfg.get("max_turn_cost_usd"), os.environ.get("HACHI_MAX_TURN_COST_USD")
    )
    max_turn_tokens = coerce_int(
        limits_cfg.get("max_turn_tokens"), os.environ.get("HACHI_MAX_TURN_TOKENS")
    )
    return LimitsStage(
        max_daily_cost_usd=max_daily_cost_usd,
        max_session_cost_usd=max_session_cost_usd,
        max_session_tokens=max_session_tokens,
        max_turn_cost_usd=max_turn_cost_usd,
        max_turn_tokens=max_turn_tokens,
    )
