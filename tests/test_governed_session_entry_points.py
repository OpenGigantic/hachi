"""BB-119: channel and schedule sessions booted outside the governed gateway
runtime (T-20).

Four boot sites called ``boot_agent()`` directly instead of going through
``_InProcessGatewayRuntime``'s session-registration bookkeeping:
``GatewaySessionRunner`` (constructed twice in ``cli/commands/channel.py`` —
once for Telegram, once for WhatsApp) and ``cli/commands/schedule.py`` at
BOTH ``schedule_run_due`` and ``schedule_run_now``. Every gateway read model
(``recent_sessions``, ``permission_audit``, ``subagent_reports``, ...)
resolves through ``_InProcessGatewayRuntime._agents``, so a session booted
any other way is structurally invisible — not in ``/sessions``, not in the
audit view, not in spend.

Independently, because ``approval_surface="gateway"`` (BB-90 / T-01) was only
ever threaded through ``_boot_session_agent``, these four sites also never
selected ``RegistryApprover`` — a gated action taken by a channel or schedule
session never reached the approval registry at all (no orphaned-approval bug;
simply no approval, ever — see CORRECTIONS.md §1 / §C23).

These tests assert the REAL, OBSERVED outcome: the booted agent is actually
present in ``runtime._agents`` (reachable by every read model), AND a gated
action driven through that agent's approver actually populates
``PendingApprovalRegistry`` (reachable by ``GET /approvals``) — not merely
that some function was called with some keyword argument. A call-spy alone
would pass against a stub that registers nothing and governs nothing.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from hachi.core.approvals import PendingApprovalRegistry
from hachi.core.permissions import PermissionDecision
from hachi.jobs.scheduler import Schedule, Scheduler, ScheduleStore

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _drive_gated_action_to_registry(registry: PendingApprovalRegistry, approver: object) -> None:
    """Run one gated action through *approver* on a background thread and
    assert it actually parks a request in *registry* — the real end-to-end
    governance property this task exists to deliver — then resolve it so the
    thread exits cleanly.

    If *approver* is a ``PolicyApprover`` (what every one of these paths gets
    today, pre-fix) ``require()`` returns synchronously without ever touching
    *registry*, so ``pending`` stays empty and the assertion below fails —
    which is exactly the failure this test must produce before the fix.
    """
    results: list[tuple[bool, str]] = []

    def _run() -> None:
        results.append(approver.require("send_email", "Send via a non-web entry point"))  # type: ignore[attr-defined]

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 2.0
    pending: list[dict] = []
    while time.monotonic() < deadline:
        pending = registry.list_pending()
        if pending:
            break
        time.sleep(0.01)
    thread.join(timeout=2.0)

    assert pending, "gated action never reached the approval registry"
    # Resolve so nothing is left dangling (harmless if already timed out/gone).
    registry.resolve(
        pending[0]["request_id"],
        PermissionDecision(action="approve", rationale="test resolve"),
    )


def _make_due_schedule(tmp_path, name: str = "daily") -> tuple[Scheduler, Schedule]:
    """A Scheduler with exactly one schedule that is due right now."""
    scheduler = Scheduler(ScheduleStore(tmp_path / "schedules.json"))
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5)).isoformat()
    schedule = scheduler.add(name=name, task="say hi", kind="once", run_at=past)
    return scheduler, schedule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_registry(monkeypatch: pytest.MonkeyPatch) -> PendingApprovalRegistry:
    """A process-local PendingApprovalRegistry substituted for the module
    singleton so assertions cannot leak across tests (mirrors the pattern
    T-01's Known Traps documents: never assert against the real
    get_approval_registry() singleton in a test)."""
    registry = PendingApprovalRegistry()
    monkeypatch.setattr("hachi.gateway.orchestrator.get_approval_registry", lambda: registry)
    return registry


@pytest.fixture()
def live_runtime(
    monkeypatch: pytest.MonkeyPatch, test_registry: PendingApprovalRegistry
) -> Iterator[object]:
    """An _InProcessGatewayRuntime installed as the process-active runtime.

    boot_agent is stubbed to avoid a real MCP/SQLite/LLM boot, but the stub
    still calls the REAL, production select_action_approver (T-01) — so
    driving the returned agent's approver proves real governance, not merely
    that a keyword argument was forwarded to a mock.
    """
    from hachi.cli import main as cli_main
    from hachi.core.runtime.protocols import set_active_session_runtime
    from hachi.gateway import in_process_runtime as runtime_mod
    from hachi.gateway.orchestrator import select_action_approver

    def _fake_boot_agent(*args: object, **kwargs: object) -> MagicMock:
        approver = select_action_approver(
            on_permission_request=kwargs.get("on_permission_request"),
            approval_surface=kwargs.get("approval_surface"),
            tier="guided",
            emit=lambda _name, _payload: None,
        )
        agent = MagicMock()
        agent.action_approver = approver
        agent.chat.return_value = "ok"
        return agent

    # T-41: _boot_session_agent's bare `boot_agent(...)` call now resolves in
    # hachi.gateway.in_process_runtime's namespace, not cli.main's. T-42 moved
    # the active-runtime registration off cli.main entirely, onto the
    # core-owned protocols.set_active_session_runtime seam (channel.py and
    # schedule.py's governed-boot helpers read it back the same way).
    monkeypatch.setattr(runtime_mod, "boot_agent", _fake_boot_agent)
    runtime = cli_main._InProcessGatewayRuntime()
    set_active_session_runtime(runtime)
    yield runtime
    set_active_session_runtime(None)


@pytest.fixture()
def cheap_schedule_boot_agent(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Keep schedule.py's OWN ``boot_agent`` reference cheap regardless of
    which code path executes it: the bare pre-fix call, or the governed
    helper's no-gateway fallback branch. A scoped unit test must never invoke
    the real ``boot_agent()`` — it spawns MCP subprocesses, opens SQLite
    stores and builds a real LLM client.
    """
    from hachi.cli.commands import schedule as schedule_cmd

    stub = MagicMock()
    stub.chat.return_value = "ok"
    monkeypatch.setattr(schedule_cmd, "boot_agent", MagicMock(return_value=stub))
    return stub


# ---------------------------------------------------------------------------
# Channels — construction sites cli/commands/channel.py:114 (telegram) and
# :162 (whatsapp). The seam itself (channels/runner.py:20 / :29) is untouched.
# ---------------------------------------------------------------------------


def test_channel_runner_boots_through_the_gateway_runtime(
    live_runtime: object, test_registry: PendingApprovalRegistry
) -> None:
    """A Telegram session must be visible to /sessions and the audit view,
    and a gated action it takes must reach the SAME approval registry a web
    session's would.

    Regression: GatewaySessionRunner booted into its OWN private _agents
    dict via a bare boot_agent() call, so channel conversations were
    invisible to every gateway read model and never got a RegistryApprover.
    """
    from hachi.cli.commands import channel as channel_cmd

    runner = channel_cmd._build_channel_runner("telegram")
    runner("tg:12345", "hello")  # drives _agent_for -> boot

    assert len(live_runtime._agents) == 1  # type: ignore[attr-defined]
    agent = next(iter(live_runtime._agents.values()))  # type: ignore[attr-defined]
    _drive_gated_action_to_registry(test_registry, agent.action_approver)


def test_whatsapp_runner_boots_through_the_gateway_runtime(live_runtime: object) -> None:
    """The WhatsApp construction site (channel.py:162) is a SEPARATE call
    from the Telegram one (:114) — pin it independently so a fix that only
    touches one of the two sites is still caught.
    """
    from hachi.cli.commands import channel as channel_cmd

    runner = channel_cmd._build_channel_runner("whatsapp")
    runner("wa:+15551234567", "hello")

    assert len(live_runtime._agents) == 1  # type: ignore[attr-defined]


def test_channel_runner_falls_back_loudly_when_no_gateway_is_active(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A standalone `hachi channel telegram` process (no gateway runtime in
    this process) must still work, but must say exactly what it lost
    (compare BB-71: a router fallback swallowed at DEBUG)."""
    from hachi.cli.commands import channel as channel_cmd
    from hachi.core.runtime.protocols import set_active_session_runtime

    set_active_session_runtime(None)
    fake_agent = MagicMock()
    fake_agent.chat.return_value = "hi back"
    monkeypatch.setattr("hachi.gateway.orchestrator.boot_agent", MagicMock(return_value=fake_agent))

    runner = channel_cmd._build_channel_runner("telegram")
    with caplog.at_level(logging.WARNING):
        result = runner("tg:999", "hello")

    assert result == "hi back"  # degraded but still functional
    assert any(r.levelname == "WARNING" for r in caplog.records), (
        "no-gateway fallback must log a WARNING naming the degradation, not stay silent"
    )


# ---------------------------------------------------------------------------
# Schedules — cli/commands/schedule.py, TWO sites: schedule_run_due (:169)
# and schedule_run_now (:205). The architecture review missed the second one
# entirely (CORRECTIONS.md §C23).
# ---------------------------------------------------------------------------


def test_schedule_run_due_boots_through_the_gateway_runtime(
    live_runtime: object,
    test_registry: PendingApprovalRegistry,
    cheap_schedule_boot_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """schedule_run_due (schedule.py:169) is the FIRST boot site.

    Regression: `agent = boot_agent()` booted a bare local the gateway never
    saw, so a cron-fired task was invisible and its actions were never
    governed.
    """
    from hachi.cli.commands import schedule as schedule_cmd

    scheduler, _schedule = _make_due_schedule(tmp_path)
    monkeypatch.setattr(schedule_cmd, "_get_scheduler", lambda: scheduler)

    schedule_cmd.schedule_run_due()

    assert len(live_runtime._agents) == 1  # type: ignore[attr-defined]
    agent = next(iter(live_runtime._agents.values()))  # type: ignore[attr-defined]
    _drive_gated_action_to_registry(test_registry, agent.action_approver)


def test_schedule_run_now_boots_through_the_gateway_runtime(
    live_runtime: object,
    test_registry: PendingApprovalRegistry,
    cheap_schedule_boot_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The SECOND schedule boot site (schedule_run_now, schedule.py:205) is
    easy to miss — the architecture review missed it entirely. Pin it
    independently of run_due so a fix that only redirects one site is still
    caught.
    """
    from hachi.cli.commands import schedule as schedule_cmd

    scheduler, schedule = _make_due_schedule(tmp_path, name="one-off")
    monkeypatch.setattr(schedule_cmd, "_get_scheduler", lambda: scheduler)

    schedule_cmd.schedule_run_now(schedule.schedule_id)

    assert len(live_runtime._agents) == 1  # type: ignore[attr-defined]
    agent = next(iter(live_runtime._agents.values()))  # type: ignore[attr-defined]
    _drive_gated_action_to_registry(test_registry, agent.action_approver)


def test_schedule_falls_back_loudly_when_no_gateway_is_active(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    """`hachi schedule run-due` under cron (no gateway live in that process)
    is the COMMON case, not an edge case — it must still fire schedules, but
    must say what it lost, at WARNING not DEBUG (compare BB-71)."""
    from hachi.cli.commands import schedule as schedule_cmd
    from hachi.core.runtime.protocols import set_active_session_runtime

    set_active_session_runtime(None)
    fake_agent = MagicMock()
    fake_agent.chat.return_value = "done"
    monkeypatch.setattr(schedule_cmd, "boot_agent", MagicMock(return_value=fake_agent))

    scheduler, _schedule = _make_due_schedule(tmp_path)
    monkeypatch.setattr(schedule_cmd, "_get_scheduler", lambda: scheduler)

    with caplog.at_level(logging.WARNING):
        schedule_cmd.schedule_run_due()

    assert any(r.levelname == "WARNING" for r in caplog.records), (
        "no-gateway fallback must log a WARNING naming the degradation, not stay silent"
    )
    fake_agent.chat.assert_called_once()


# ---------------------------------------------------------------------------
# Test-isolation guard for the active-session-runtime dependency this task
# introduced into schedule.py / channel.py (BB-119 follow-up; retargeted by
# T-42 from a cli.main global onto the core-owned protocols seam).
#
# The active runtime is assigned by PRODUCTION code (cli/main.py's two boot
# sites, via `hachi.core.runtime.protocols.set_active_session_runtime`) as a
# plain function call, not only by tests via monkeypatch. monkeypatch only
# reverts its OWN setattr calls, so a test that boots a real runtime (rather
# than monkeypatching an attribute) leaks it into every later test in the
# same worker that reads it -- which is exactly what made
# tests/test_schedule_run_now.py order-dependent in the full suite.
# tests/conftest.py's `_isolate_active_gateway_runtime` autouse fixture fixes
# this; the two tests below pin that guarantee directly, independent of
# full-suite ordering luck.
#
# These two tests MUST run in this declaration order for the guard to mean
# anything (test A pollutes; test B checks the pollution is gone). This file
# is never split across xdist workers mid-file (pyproject.toml's
# `--dist=loadfile`), and this suite does not use a random-order plugin, so
# plain top-to-bottom declaration order is deterministic here.
# ---------------------------------------------------------------------------


def test_direct_mutation_of_active_runtime_does_not_leak_to_later_tests() -> None:
    """Simulate exactly what production code does at cli/main.py's two writer
    call sites: a DIRECT call to `set_active_session_runtime`, deliberately
    NOT reverted within this test (production code doesn't revert it either;
    only the autouse isolation fixture in tests/conftest.py does).
    """
    from hachi.core.runtime.protocols import (
        get_active_session_runtime,
        set_active_session_runtime,
    )

    assert get_active_session_runtime() is None, (
        "test-isolation precondition failed -- the active session runtime was "
        "already polluted before this test ran, so the guard below would not "
        "be testing what it claims to"
    )
    set_active_session_runtime(object())  # type: ignore[arg-type]  # direct call -- not reverted here


def test_active_runtime_is_clean_after_the_previous_test() -> None:
    """Runs immediately after the direct-mutation test above. Passes only if
    the `_isolate_active_gateway_runtime` autouse fixture in tests/conftest.py
    restored the active session runtime to its pre-test value (None) between
    the two tests -- proving the leak that broke test_schedule_run_now.py
    cannot recur regardless of which test boots (or simulates booting) a
    runtime.
    """
    from hachi.core.runtime.protocols import get_active_session_runtime

    assert get_active_session_runtime() is None, (
        "the active session runtime leaked across a test boundary -- the "
        "_isolate_active_gateway_runtime autouse fixture in tests/conftest.py "
        "did not restore it (or was removed)"
    )
