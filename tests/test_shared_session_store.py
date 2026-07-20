"""Regression tests for T-10 — ``ProcessSharedServices.session_store``.

BB-42 recurrence: three call sites read ``ProcessSharedServices.session_store``
(the gateway's ingress-idempotency hot path in ``fastapi_app._resolve_session_store``,
the ``POST /sessions/cleanup-empty`` route, and ``core/cost.py``'s daily-spend
ledger) but the field was declared nowhere and never assigned anywhere in
``src/``, so every "prefer the shared store" branch was dead and the hot path
(called on every message) opened — and never closed — a fresh ``SessionStore()``
per call.

A note on test design (read before "fixing" a test that looks redundant):
``ProcessSharedServices`` is a plain (non-frozen, non-slotted) dataclass, so
``shared.session_store = fake`` succeeds as an ordinary instance attribute
*whether or not the field is declared* — ``hasattr``/``getattr`` cannot tell
the difference (verified empirically while writing this file). That means a
test shaped "manually attach a fake store, then assert the reader returns it"
can NOT fail against the pre-fix code, because the readers' ``getattr(...,
None)`` already finds a manually-set attribute today. The task's own §7
sketch is built around exactly that shape and does not reproduce as a failing
test (see the ``_returns_the_shared_instance`` tests below, kept as
correctness locks — they pass on both sides of the fix and are not the
regression proof).

The two properties that DO discriminate real (pre-fix) from fixed code, and
that this file leans on for the actual regression proof, are:
  1. ``"session_store" in ProcessSharedServices.__dataclass_fields__`` — the
     field is declared nowhere today; a class-level introspection check, not
     an instance attribute check, is what a manual assignment cannot fake.
  2. Counting real ``SessionStore()``/fallback construction when NOTHING has
     been attached (the realistic default-boot state) — today's fallback
     branches unconditionally manufacture a fresh, never-closed store on
     every call; the fix must not.
"""

from __future__ import annotations

from typing import Any

import pytest

from hachi.gateway.shared_services import ProcessSharedServices


class _FakeStore:
    """Stands in for SessionStore — identity is all these tests care about."""

    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def end_session(self, session_id: str) -> None:  # touched by AgentSession.shutdown
        pass

    def delete_empty_sessions(self) -> int:  # touched by cleanup_empty_sessions
        return 0


class _CountingSessionStore:
    """Drop-in replacement for ``hachi.core.session_store.SessionStore`` that
    counts how many times a fresh instance would have been constructed, and
    how many times one was shut down — used to prove (or disprove) a leak
    without ever touching a real sqlite file.
    """

    created = 0
    shutdown_calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).created += 1

    def shutdown(self) -> None:
        type(self).shutdown_calls += 1

    def delete_empty_sessions(self) -> int:
        return 3

    @classmethod
    def reset(cls) -> None:
        cls.created = 0
        cls.shutdown_calls = 0


@pytest.fixture
def shared(tmp_path):
    ProcessSharedServices.clear()
    s = ProcessSharedServices.get_or_create(
        {"memory": {"backend": "in_memory"}},  # no ChromaDB — see BB-3
        str(tmp_path),
        force_reinit=True,
    )
    yield s
    ProcessSharedServices.clear()


# ---------------------------------------------------------------------------
# 1. The field must exist — THE regression proof for the "declared nowhere"
#    half of the bug. Genuinely fails before the fix, passes after.
# ---------------------------------------------------------------------------


def test_shared_services_declares_session_store() -> None:
    assert "session_store" in ProcessSharedServices.__dataclass_fields__, (
        "three call sites read ProcessSharedServices.session_store; it is declared nowhere"
    )


# ---------------------------------------------------------------------------
# 2. fastapi_app._resolve_session_store — correctness locks (identity when a
#    store IS attached; see the module docstring for why these do not
#    themselves fail pre-fix) plus the genuine leak-regression proof.
# ---------------------------------------------------------------------------


def test_resolve_session_store_returns_the_shared_instance(shared) -> None:
    """Correctness lock: when a store IS attached, the resolver must prefer it.

    Does not independently fail before the fix (see module docstring) — locked
    jointly with ``test_shared_services_declares_session_store`` above.
    """
    from hachi.gateway.fastapi_app import _resolve_session_store

    store = _FakeStore()
    shared.session_store = store
    assert _resolve_session_store() is store, (
        "the resolver built a fresh SessionStore instead of reusing the shared one"
    )


def test_resolve_session_store_does_not_leak_a_new_store_per_call(monkeypatch) -> None:
    """THE leak regression proof (CF-1 / the hot-path bug this task exists for).

    Before boot_agent has attached anything to ``_shared`` (the realistic state
    every gateway message hit), the resolver must not manufacture a brand-new,
    never-closed ``SessionStore()`` on every call. Patches the class the
    resolver's local ``from hachi.core.session_store import SessionStore``
    re-resolves on every invocation, so this proves construction COUNT without
    touching a real sqlite file.
    """
    from hachi.gateway.fastapi_app import _resolve_session_store

    ProcessSharedServices.clear()  # nothing shared in this process
    _CountingSessionStore.reset()
    monkeypatch.setattr("hachi.core.session_store.SessionStore", _CountingSessionStore)

    _resolve_session_store()
    _resolve_session_store()

    assert _CountingSessionStore.created == 0, (
        f"resolver opened {_CountingSessionStore.created} fresh SessionStore "
        "connection(s) across 2 calls instead of skipping/reusing — this is the "
        "per-message sqlite3/WAL leak CF-1 describes"
    )


# ---------------------------------------------------------------------------
# 3. core/cost.py._get_shared_session_store — correctness lock (see note above;
#    the underlying "declared nowhere" bug is proven by test 1).
# ---------------------------------------------------------------------------


def test_get_shared_session_store_returns_the_shared_instance(shared) -> None:
    from hachi.core.cost import _get_shared_session_store

    store = _FakeStore()
    shared.session_store = store
    assert _get_shared_session_store() is store, (
        "core/cost.py rebuilt/looked past the shared store instead of reusing it "
        "(BB-42 recurrence — see the docstring on _get_shared_session_store)"
    )


# ---------------------------------------------------------------------------
# 4. POST /sessions/cleanup-empty — its fallback SessionStore() must be closed.
#    Genuine regression proof: today's code never calls .shutdown() on it.
# ---------------------------------------------------------------------------


def test_cleanup_empty_sessions_closes_its_fallback_store(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from hachi.gateway.control_plane import GatewayControlPlane
    from hachi.gateway.fastapi_app import create_gateway_app

    ProcessSharedServices.clear()  # nothing shared in this process
    _CountingSessionStore.reset()
    monkeypatch.setattr("hachi.core.session_store.SessionStore", _CountingSessionStore)

    cp = GatewayControlPlane()
    with TestClient(create_gateway_app(cp)) as client:
        resp = client.post("/sessions/cleanup-empty")

    assert resp.status_code == 200
    assert resp.json()["removed"] == 3
    assert _CountingSessionStore.shutdown_calls == 1, (
        "cleanup_empty_sessions's fallback SessionStore was opened but never shut "
        "down — its sqlite3/WAL connection leaks for the rest of the process"
    )


# ---------------------------------------------------------------------------
# 5. AgentSession.shutdown() must not close a store the process is still
#    sharing. Discovered while implementing T-10 (not in the task's own §7
#    sketch): the literal fix — attach boot_agent's SessionStore to
#    `_shared.session_store` and do nothing else — reintroduces the exact
#    failure class BB-42 already fixed for `vault` (see the `H-VAULT`
#    guard/comment in AgentSession.shutdown, a few lines above the
#    session-store block). `cli/main.py`'s `_InProcessGatewayRuntime` keeps up
#    to `HACHI_GATEWAY_MAX_SESSIONS` (100) concurrent `AgentSession` objects
#    alive in one process and calls `agent.shutdown()` on ordinary, frequent
#    events — closing a browser tab (`close_connection`), replacing a session
#    on reboot (`_register_agent`), a provider-config-driven agent rebuild
#    (`_reload_agents`) — not just full process exit. Once `session_store` is
#    genuinely shared, an unguarded `self._session_store.shutdown()` in any ONE
#    of those closes the sqlite3/WAL connection the ingress-idempotency hot
#    path, `core/cost.py`'s daily-spend ledger, and every sibling session
#    depend on, for the rest of the process lifetime — silently, since all
#    three readers degrade to "no shared store" on the next call rather than
#    raising. Both tests below are genuine regression proofs (verified failing
#    before the guard was added).
# ---------------------------------------------------------------------------


def _minimal_agent_session(session_store: object):
    """Build a minimal AgentSession touching only what shutdown() reads.

    Mirrors ``tests/test_agent_session_shutdown.py``'s ``_minimal_agent_session()``.
    """
    from unittest.mock import MagicMock

    from hachi.gateway.orchestrator import AgentSession

    agent = AgentSession.__new__(AgentSession)
    agent.loop = MagicMock()
    agent.loop.llm_client = None
    agent.memory = MagicMock()
    agent.memory.history = []
    agent.memory.vault = None
    agent.vault = None
    agent.integrations = MagicMock()
    agent.mcp_manager = MagicMock()
    agent.mcp_manager.server_count = 0
    agent._schedule_runner = None
    agent._resident_worker = None
    agent._job_worker = None
    agent._session_id = "sess-1"
    agent._session_store = session_store
    agent._sub_agent_client = None
    agent._browser_controller = None
    agent._event_emitter = lambda *_a, **_kw: None
    return agent


def test_agent_session_shutdown_does_not_close_the_still_shared_store(shared) -> None:
    """Ending ONE session must not poison the store other sessions/the gateway share."""
    store = _FakeStore()
    shared.session_store = store  # this store is still the process-shared one
    agent = _minimal_agent_session(store)

    agent.shutdown()

    assert store.shutdown_calls == 0, (
        "AgentSession.shutdown() closed a SessionStore that ProcessSharedServices "
        "is still sharing with the rest of the process"
    )


def test_agent_session_shutdown_closes_a_store_no_longer_shared(shared) -> None:
    """A session whose store has already been superseded by a newer boot IS closed.

    Guards against overcorrecting to "never close a session's own store" —
    otherwise the per-session-store leak this task fixes on the gateway's
    utility call sites would just reappear as a leak of every superseded
    session's own store.
    """
    old_store = _FakeStore()
    new_store = _FakeStore()
    shared.session_store = new_store  # a newer session already took over the shared slot
    agent = _minimal_agent_session(old_store)

    agent.shutdown()

    assert old_store.shutdown_calls == 1
