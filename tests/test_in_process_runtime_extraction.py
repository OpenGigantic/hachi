"""Characterization tests for T-41 — extracting ``_InProcessGatewayRuntime`` out of
``hachi.cli.main`` into ``hachi.gateway.in_process_runtime``.

This is a pure refactor: there is no bug to reproduce, so these tests characterize
the *current* behaviour so the move is provably behaviour-neutral (CLAUDE.md Rule 2,
applied to a refactor — see docs/audit/2026-07-19/tasks/T-41-extract-in-process-runtime.md
§7). All three pass before AND after the move — that is correct — except
``test_runtime_is_importable_from_gateway_without_cli``, which must FAIL before the
move with ``ModuleNotFoundError: No module named 'hachi.gateway.in_process_runtime'``
and pass after.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Generated with:
#   awk 'NR>=216 && NR<=2773' src/hachi/cli/main.py \
#     | grep -n "^    def " | awk -F'[:(]' '{print $1+215": "$2}' | sed 's/    def //'
# against the working tree at the time this task ran (86 methods — the spec's
# original count of 79 is stale; 7 methods were added since: _register_agent,
# boot_registered_session, read_events_base, pending_approvals, resolve_approval,
# get_memory_gardener_diffs, decide_memory_gardener_diff). Pasted literally, not
# derived from the class under test — a derived set would assert nothing.
EXPECTED_METHODS = {
    "__init__",
    "_boot_session_agent",
    "_get_agent",
    "_register_agent",
    "register_connection",
    "boot_registered_session",
    "close_connection",
    "rehydrate_session",
    "_reload_agents",
    "chat",
    "interrupt_session",
    "read_events",
    "read_events_base",
    "session_state",
    "recent_sessions",
    "session_messages",
    "delete_session_record",
    "runtime_events",
    "mcp_inventory",
    "active_jobs",
    "failed_jobs",
    "permission_audit",
    "pending_approvals",
    "resolve_approval",
    "subagent_reports",
    "list_schedules",
    "cancel_schedule",
    "toggle_schedule",
    "run_schedule_now",
    "capabilities",
    "get_config",
    "update_provider",
    "validate_provider",
    "update_permissions",
    "update_memory",
    "list_models",
    "setup_state",
    "apply_setup",
    "shutdown",
    "get_skills",
    "get_memory",
    "get_memory_recall",
    "get_memory_gardener_diffs",
    "decide_memory_gardener_diff",
    "get_og_graph",
    "_standalone_og_engine",
    "get_authoring_targets",
    "_resolve_authoring_target_path",
    "get_authoring_target",
    "authoring_generate",
    "authoring_apply",
    "authoring_save_source",
    "authoring_revert",
    "get_spend",
    "get_spend_daily",
    "set_capability_enabled",
    "add_memory_fact",
    "update_limits",
    "add_allowed_folder",
    "add_allowed_command",
    "remove_allowed_folder",
    "remove_allowed_command",
    "add_always_allow",
    "register_mcp_server",
    "_mcp_live_server_names",
    "_mcp_node_names",
    "set_mcp_enabled",
    "delete_mcp_server",
    "get_kennel",
    "add_skill_from_kennel",
    "delete_memory_fact",
    "store_secret",
    "list_secret_names",
    "delete_secret",
    "reload_search_tools",
    "list_providers",
    "add_or_update_provider",
    "remove_provider",
    "get_routing",
    "set_routing",
    "validate_routing",
    "rollback_routing",
    "get_routing_audit",
    "test_provider_connection",
    "list_all_models",
    "probe_provider",
}


def test_runtime_exposes_the_full_method_surface() -> None:
    """Lock the 86-method surface so the move cannot silently drop one."""
    from hachi.gateway.in_process_runtime import _InProcessGatewayRuntime

    actual = {
        name
        for name in vars(_InProcessGatewayRuntime)
        if callable(getattr(_InProcessGatewayRuntime, name))
    }
    assert EXPECTED_METHODS <= actual, f"methods lost in the move: {EXPECTED_METHODS - actual}"
    assert len(EXPECTED_METHODS) == 86


def test_runtime_is_importable_from_gateway_without_cli() -> None:
    """The gateway backend must not need the Typer module to import.

    FAILS before T-41 with ModuleNotFoundError: No module named
    'hachi.gateway.in_process_runtime'.

    BB-150: this test used to delete ``hachi.cli*`` from THIS interpreter's
    ``sys.modules`` and assert on the dict afterward (scoped to the ``hachi.cli``
    prefix so it never touched chromadb — BB-3). That still poisoned the shared
    interpreter: any test running later in the same session that already holds a
    reference bound at import time (e.g. ``tests/test_schedule_show.py``'s
    ``from hachi.cli.main import app``) keeps pointing at the now-orphaned module
    object, while the deleted entry silently comes back as a *different* fresh
    module object on the next import — CliRunner-driven CLI commands then fail
    with exit code 1, purely from suite ordering. Import the new module in a
    disposable subprocess instead: hermetic (this interpreter's sys.modules is
    never mutated) and a strictly stronger assertion than the dict check it
    replaces — it proves the import genuinely never pulls the CLI in, rather
    than inspecting a dict this test just edited by hand. No ``shell=True``.
    """
    script = (
        "import sys\n"
        "import hachi.gateway.in_process_runtime\n"
        "assert 'hachi.cli.main' not in sys.modules, ("
        "'hachi.gateway.in_process_runtime pulled in hachi.cli.main')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"subprocess exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_resident_worker_slot_is_visible_to_readiness_probe(monkeypatch) -> None:
    """The probe must observe a LATE write to the worker slot across the module seam.

    Guards §5 decision 2: a ``from hachi.gateway.in_process_runtime import
    _GATEWAY_RESIDENT_WORKER`` in cli/main would snapshot ``None`` at import time
    and never see a later write — the probe would then report the worker as
    "not started" forever, no matter what is actually running. Reading through the
    module object (``_runtime_mod._GATEWAY_RESIDENT_WORKER``) observes the write.
    """
    from hachi.gateway import in_process_runtime as runtime_mod

    alive_worker = MagicMock()
    alive_worker.is_running = True
    monkeypatch.setattr(runtime_mod, "_GATEWAY_RESIDENT_WORKER", alive_worker, raising=False)

    from hachi.cli.main import _gateway_readiness_probe

    _ready, blockers, checks = _gateway_readiness_probe()

    assert "job_worker" not in blockers
    assert checks["job_worker"] == "ok"
