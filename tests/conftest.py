"""Shared pytest fixtures for the Hachi test suite.

Autouse fixtures here reset process-level singletons between tests so that
tests that boot an in-process gateway (test_cockpit_gaps.py,
test_cockpit_live_refresh.py, test_cockpit_chat_live.py, etc.) do not leave
a stale ProcessSharedServices instance (and an open ChromaDB PersistentClient)
that corrupts later vault/ChromaDB tests.

Root cause of the shared-state leak
-------------------------------------
``ProcessSharedServices._INSTANCES`` is a module-level dict.  Every call to
``ProcessSharedServices.get_or_create()`` stores the new singleton there.
Tests that call ``_start_gateway_server()`` boot a real vault-backed shared
service. After the test's ``runtime.shutdown()`` + ``server.stop()`` the
``ProcessSharedServices`` instance (and its embedded ChromaDB PersistentClient)
remain in ``_INSTANCES``. When enough such tests run before the vault tests, the
accumulation of open PersistentClient handles on Windows (ChromaDB 1.5.x /
SQLite WAL mode) causes later vault tests to see "vault unavailable" errors or
incorrect state.

Fix
---
An autouse session-scoped fixture drains the ``_INSTANCES`` dict via
``shutdown_all()`` once after the entire test session to flush Chroma WAL files.
A second autouse function-scoped fixture calls ``ProcessSharedServices.clear()``
(no vault close — avoids double-close on vaults that tests manage themselves)
**before each test** so every test that calls ``get_or_create()`` or
``instance()`` starts with a clean slate. The vault tests are unaffected because
they construct ``MemoryVault`` directly with ``tmp_path`` and never go through
``ProcessSharedServices``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

# Test hermeticity: hachi.cli.main merges <cwd>/.env and the config-dir .env into
# os.environ at IMPORT time. During a full pytest run that pulls the developer's
# real provider keys (ANTHROPIC_API_KEY, etc.) into the process, which flips
# import-time skipif guards on live-gateway tests (flaky, order-dependent
# failures) and leaks real secrets into the test process. Disable the autoload
# before any test module imports hachi.cli.main. Export
# HACHI_DISABLE_DOTENV_AUTOLOAD=0 to opt back in for a local debugging run.
os.environ.setdefault("HACHI_DISABLE_DOTENV_AUTOLOAD", "1")

# Cockpit/live-gateway tests boot a real in-process uvicorn + orchestrator and POST
# /sessions, which does heavy synchronous skill-engine + ChromaDB vault init. On a
# cold Windows process (uncached imports) or under full-suite load (the Nth
# PersistentClient in one process), that can exceed GatewayClient's 15s default and
# raise a transport timeout — the deterministic-looking "cockpit flakiness". Grant a
# generous client timeout for the test process so session creation reliably
# completes. Production keeps the 15s default (this only sets the env override).
os.environ.setdefault("HACHI_GATEWAY_CLIENT_TIMEOUT", "90")

# Warm the chromadb native (Rust/PyO3) extension once at session start. chromadb's
# native module CANNOT be re-initialized once unloaded — CPython raises
# "cannot load module more than once per process". Several tests use
# patch.dict(sys.modules, {...}) to simulate a missing dependency; patch.dict
# snapshots sys.modules on enter and restores it on exit, DROPPING any module that
# was first-imported INSIDE the block. When chromadb is first-imported lazily inside
# such a block (e.g. via the file-extractor chain in test_file_platform), the exit
# wipes its native submodules and every later `import chromadb` in the process fails
# — silently degrading vault/embedding tests to "chromadb not installed". Importing
# it here guarantees chromadb is in every patch.dict enter-snapshot, so it survives
# the restore. No-op if chromadb is not installed in this environment.
try:
    import chromadb  # noqa: F401
    import chromadb.config  # noqa: F401
except Exception:  # pragma: no cover - chromadb is optional in some CI envs
    pass

import pytest


@pytest.fixture(autouse=True)
def _reset_process_shared_services() -> None:  # type: ignore[return]
    """Clear the ProcessSharedServices singleton cache before each test.

    Runs before the test body (setup phase). Does NOT close open vaults — that
    is left to the individual test's teardown (runtime.shutdown() + server.stop())
    or to the session-level atexit handler registered by shared_services.py.
    """
    try:
        from hachi.gateway.shared_services import ProcessSharedServices

        ProcessSharedServices.clear()
    except ImportError:
        # Package not yet importable (e.g. missing dependency in partial CI env).
        pass
    yield  # run the test
    # No post-test cleanup here — _flush_shared_services_at_session_end handles WAL flush.


@pytest.fixture(autouse=True)
def _restore_os_environ():  # type: ignore[no-untyped-def]
    """Snapshot os.environ before each test and restore it after.

    Tests occasionally mutate os.environ directly (or trigger production code
    that does — e.g. the tokoboros→hachi rename shim writes HACHI_* vars), and
    pytest's monkeypatch only undoes vars IT set. Without this, a leaked var
    (e.g. HACHI_SMTP_PORT='value-for-...') pollutes later tests and makes the
    full-suite gate order-dependent. Diff-based restore (no clear()) avoids a
    window where concurrent in-process-gateway threads observe an empty env.
    """
    snapshot = dict(os.environ)
    yield
    # Remove keys the test added that weren't present before.
    for key in list(os.environ.keys()):
        if key not in snapshot:
            del os.environ[key]
    # Restore keys that were changed or deleted during the test.
    for key, value in snapshot.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _isolate_active_gateway_runtime():  # type: ignore[no-untyped-def]
    """Snapshot the registered ``SessionRuntime`` before each test and restore
    it after (T-20 / BB-119 follow-up; retargeted by T-42).

    The active runtime is a process-wide slot (``hachi.core.runtime.protocols``
    ``set_active_session_runtime`` / ``get_active_session_runtime``) assigned
    by PRODUCTION code (``cli/main.py``'s two boot sites, when a real
    ``_InProcessGatewayRuntime`` boots via ``_start_gateway_server`` or the
    ``gateway`` command) — not only by tests. Same shape of bug as
    ``_restore_os_environ`` above: ``monkeypatch`` only reverts its OWN
    ``setattr`` calls, so a test that boots a REAL runtime (rather than
    monkeypatching) leaks it into every later test in the same worker that
    reads it — which ``cli/commands/schedule.py``'s and
    ``cli/commands/channel.py``'s governed-boot helpers both do. A plain
    unconditional restore (not a diff of recorded changes) fixes the leak
    regardless of *how* the slot was mutated, and is a no-op for the tests
    that already isolate it correctly via ``monkeypatch`` (this fixture's
    teardown runs after monkeypatch's own undo, restoring the same value it
    already put back).

    Before T-42 this snapshotted/restored ``hachi.cli.main._ACTIVE_RUNTIME``
    directly; that module global no longer exists (core must not import the
    CLI — see ``tests/test_core_cli_seam.py``), so this fixture now reads and
    writes through the same core-owned getter/setter production code uses.
    """
    try:
        from hachi.core.runtime import protocols as _session_runtime_protocols
    except ImportError:
        # Package not yet importable (e.g. missing dependency in partial CI env).
        yield
        return
    before = _session_runtime_protocols.get_active_session_runtime()
    yield
    _session_runtime_protocols.set_active_session_runtime(before)


@pytest.fixture(autouse=True, scope="session")
def _flush_shared_services_at_session_end() -> None:  # type: ignore[return]
    """Flush ChromaDB WAL files once after all tests complete.

    shutdown_all() closes every MemoryVault in _INSTANCES and then clears the
    dict — the same action the atexit handler would take at process exit. Running
    it explicitly at session end ensures the WAL is flushed before pytest exits,
    which prevents SQLite "database disk image is malformed" errors on the next
    run.
    """
    yield  # run all tests first
    try:
        from hachi.gateway.shared_services import ProcessSharedServices

        ProcessSharedServices.shutdown_all()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# T-43 round 1 -- GatewayControlPlane test helper.
#
# Maps every legacy handler kwarg name (pre-T-43's flat 74-callable
# constructor) to the (port param name, port method name) that now carries
# it (see src/hachi/gateway/ports.py). 19 entries have a method name that
# differs from the kwarg name -- e.g. 'on_connect' -> ('sessions',
# 'register_connection') -- mirroring the `# ctor kwarg was '...'` markers
# in ports.py itself.
# ---------------------------------------------------------------------------
_LEGACY_HANDLER_PORTS: dict[str, tuple[str, str]] = {
    "on_connect": ("sessions", "register_connection"),
    "on_disconnect": ("sessions", "close_connection"),
    "on_chat": ("sessions", "chat"),
    "read_events": ("sessions", "read_events"),
    "read_events_base": ("sessions", "read_events_base"),
    "get_session_state": ("sessions", "session_state"),
    "get_recent_sessions": ("sessions", "recent_sessions"),
    "get_session_messages": ("sessions", "session_messages"),
    "delete_session": ("sessions", "delete_session_record"),
    "get_runtime_events": ("sessions", "runtime_events"),
    "on_interrupt": ("sessions", "interrupt_session"),
    "get_mcp_inventory": ("observability", "mcp_inventory"),
    "get_active_jobs": ("observability", "active_jobs"),
    "get_failed_jobs": ("observability", "failed_jobs"),
    "get_permission_audit": ("observability", "permission_audit"),
    "get_subagent_reports": ("observability", "subagent_reports"),
    "list_schedules": ("scheduling", "list_schedules"),
    "cancel_schedule": ("scheduling", "cancel_schedule"),
    "toggle_schedule": ("scheduling", "toggle_schedule"),
    "run_schedule_now": ("scheduling", "run_schedule_now"),
    "get_skills": ("skills", "get_skills"),
    "get_og_graph": ("skills", "get_og_graph"),
    "get_memory": ("memory", "get_memory"),
    "get_memory_recall": ("memory", "get_memory_recall"),
    "add_memory_fact": ("memory", "add_memory_fact"),
    "delete_memory_fact": ("memory", "delete_memory_fact"),
    "get_memory_gardener_diffs": ("memory", "get_memory_gardener_diffs"),
    "get_spend": ("spend", "get_spend"),
    "list_pending_approvals": ("approvals", "pending_approvals"),
    "resolve_approval": ("approvals", "resolve_approval"),
    "get_capabilities": ("capabilities", "capabilities"),
    "set_capability_enabled": ("capabilities", "set_capability_enabled"),
    "get_config": ("config", "get_config"),
    "update_provider": ("config", "update_provider"),
    "validate_provider": ("config", "validate_provider"),
    "update_permissions": ("config", "update_permissions"),
    "update_memory": ("config", "update_memory"),
    "list_models": ("config", "list_models"),
    "get_setup_state": ("config", "setup_state"),
    "apply_setup": ("config", "apply_setup"),
    "update_limits": ("config", "update_limits"),
    "get_authoring_targets": ("authoring", "get_authoring_targets"),
    "get_authoring_target": ("authoring", "get_authoring_target"),
    "authoring_generate": ("authoring", "authoring_generate"),
    "authoring_apply": ("authoring", "authoring_apply"),
    "authoring_revert": ("authoring", "authoring_revert"),
    "authoring_save_source": ("authoring", "authoring_save_source"),
    "get_kennel": ("kennel", "get_kennel"),
    "add_skill": ("kennel", "add_skill_from_kennel"),
    "decide_memory_gardener_diff": ("memory_review", "decide_memory_gardener_diff"),
    "get_spend_daily": ("daily_spend", "get_spend_daily"),
    "add_allowed_folder": ("allowlists", "add_allowed_folder"),
    "add_allowed_command": ("allowlists", "add_allowed_command"),
    "remove_allowed_folder": ("allowlists", "remove_allowed_folder"),
    "remove_allowed_command": ("allowlists", "remove_allowed_command"),
    "add_always_allow": ("allowlists", "add_always_allow"),
    "register_mcp_server": ("mcp_admin", "register_mcp_server"),
    "set_mcp_enabled": ("mcp_admin", "set_mcp_enabled"),
    "delete_mcp_server": ("mcp_admin", "delete_mcp_server"),
    "list_providers": ("providers", "list_providers"),
    "add_or_update_provider": ("providers", "add_or_update_provider"),
    "remove_provider": ("providers", "remove_provider"),
    "get_routing": ("providers", "get_routing"),
    "set_routing": ("providers", "set_routing"),
    "validate_routing": ("providers", "validate_routing"),
    "rollback_routing": ("providers", "rollback_routing"),
    "get_routing_audit": ("providers", "get_routing_audit"),
    "test_provider_connection": ("providers", "test_provider_connection"),
    "list_all_models": ("providers", "list_all_models"),
    "probe_provider": ("providers", "probe_provider"),
    "store_secret": ("secrets", "store_secret"),
    "list_secret_names": ("secrets", "list_secret_names"),
    "delete_secret": ("secrets", "delete_secret"),
    "reload_search_tools": ("search", "reload_search_tools"),
}

# Constructor params that are NOT handlers grouped into a port -- forwarded
# to GatewayControlPlane(...) unchanged.
_CONTROL_PLANE_PASSTHROUGH_KWARGS = frozenset(
    {"readiness_probe", "max_active_sessions", "max_prompt_chars"}
)

# Reverse index: port param name -> every method name that port declares
# (see hachi.gateway.ports). Built from _LEGACY_HANDLER_PORTS so it can never
# drift out of sync with the table above.
_PORT_MEMBERS: dict[str, tuple[str, ...]] = {}
for _kwarg_name, (_port_param_name, _method_name) in _LEGACY_HANDLER_PORTS.items():
    _PORT_MEMBERS.setdefault(_port_param_name, []).append(_method_name)  # type: ignore[arg-type]
_PORT_MEMBERS = {name: tuple(members) for name, members in _PORT_MEMBERS.items()}


def control_plane(**handlers: Any):  # type: ignore[no-untyped-def]
    """Build a ``GatewayControlPlane`` from legacy handler kwargs, via ports.

    Test-only helper (T-43 round 1). Accepts exactly the keyword names the
    pre-T-43 flat constructor did (``on_connect``, ``get_config``,
    ``max_active_sessions``, ...), groups the handler ones into T-43's 18
    port objects, and constructs the control plane through the new
    ``sessions=``/``config=``/etc. port parameters -- never the individual
    handler kwargs directly.

    Each port is a ``types.SimpleNamespace``: the ports in
    ``hachi.gateway.ports`` are structural ``typing.Protocol`` classes, so
    any object exposing the right attributes satisfies one -- no stub
    classes needed. A port is omitted entirely (stays ``None`` on the
    control plane) when none of its member kwargs were supplied, matching
    the old "handler unwired -> None -> ``_require_handler`` raises"
    behaviour.

    A touched port's namespace always carries EVERY member that port
    declares, not just the ones this call supplied -- unsupplied members
    default to ``None``. ``GatewayControlPlane.__init__`` resolves each
    handler as ``port.method if port else None``: it reads ``port.method``
    directly (no ``getattr`` default), so a port object must never be
    missing an attribute it declares -- only have it set to ``None`` -- or
    the missing attribute raises ``AttributeError`` instead of a clean
    ``_require_handler`` failure.

    Round 2 converted the 94 existing ``GatewayControlPlane(...)`` call
    sites in this test suite to call this helper instead of passing handler
    kwargs directly.
    """
    from hachi.gateway.control_plane import GatewayControlPlane

    unknown = set(handlers) - set(_LEGACY_HANDLER_PORTS) - _CONTROL_PLANE_PASSTHROUGH_KWARGS
    if unknown:
        raise TypeError(f"control_plane() got unknown handler kwarg(s): {sorted(unknown)}")

    port_overrides: dict[str, dict[str, Any]] = {}
    for kwarg_name, value in handlers.items():
        if kwarg_name in _CONTROL_PLANE_PASSTHROUGH_KWARGS:
            continue
        port_param_name, method_name = _LEGACY_HANDLER_PORTS[kwarg_name]
        port_overrides.setdefault(port_param_name, {})[method_name] = value

    ports = {
        port_param_name: SimpleNamespace(
            **{member: overrides.get(member) for member in _PORT_MEMBERS[port_param_name]}
        )
        for port_param_name, overrides in port_overrides.items()
    }
    passthrough = {k: v for k, v in handlers.items() if k in _CONTROL_PLANE_PASSTHROUGH_KWARGS}

    return GatewayControlPlane(**ports, **passthrough)
