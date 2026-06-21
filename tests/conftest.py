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
