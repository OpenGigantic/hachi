"""AgentSession's constructor must establish everything shutdown() depends on (T-45).

Regression coverage: ``boot_agent`` used to construct an ``AgentSession`` and then write
ten private attributes onto the finished object rather than passing them to the
constructor. Three of those -- ``_sub_agent_client``, ``_browser_controller``, and
``_schedule_runner`` -- were never declared in ``__init__`` at all, so ``shutdown()``
(and a couple of other readers) reached them via ``getattr(self, "_x", None)`` and
silently skipped cleanup on any session not built by the real ``boot_agent`` path.

These tests build a REAL ``AgentSession`` through ``AgentSession(...)`` (never
``AgentSession.__new__``) so a dropped constructor argument shows up as a real
``AttributeError``/``TypeError``, not a silently-passing mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from hachi.gateway.orchestrator import AgentSession


def _constructed_session(**kwargs) -> AgentSession:
    """Build a real, constructor-built AgentSession -- collaborators are mocked, but
    the AgentSession instance itself goes through the genuine __init__ path."""
    return AgentSession(
        loop=MagicMock(),
        skill_engine=MagicMock(),
        memory=MagicMock(),
        integrations=MagicMock(),
        mcp_manager=MagicMock(),
        permission_gate=MagicMock(),
        model_name="test-model",
        **kwargs,
    )


# The three attributes shutdown() (and a couple of other readers) depend on, that
# boot_agent used to stash onto the finished object instead of passing to __init__.
SHUTDOWN_DEPENDENCIES = ("_sub_agent_client", "_browser_controller", "_schedule_runner")


def test_constructor_establishes_every_shutdown_dependency() -> None:
    """shutdown() depends on three attributes the constructor never sets.

    FAILS before T-45: they exist only because boot_agent writes them
    afterwards, so shutdown() reads them via getattr(..., None) and
    silently skips cleanup on any constructor-built session.
    """
    session = _constructed_session()
    missing = [a for a in SHUTDOWN_DEPENDENCIES if not hasattr(session, a)]
    assert missing == [], f"constructor did not establish: {missing}"


def test_shutdown_stops_a_constructor_supplied_schedule_runner() -> None:
    """The leak CF-6's four-item list would have left behind (T-45 SS3.3).

    FAILS before T-45 with TypeError: unexpected keyword 'schedule_runner'.
    """
    runner = MagicMock()
    session = _constructed_session(schedule_runner=runner)
    session.shutdown()
    runner.stop.assert_called_once()


def test_daily_cap_promotion_preserves_falsy_collapse() -> None:
    """A 0.0 cap must remain representable as 'unlimited', as the `if` at the old
    stash site made it (T-45 SS3.5). The constructor itself is a plain passthrough
    (`self._max_daily_cost_usd = max_daily_cost_usd`); the falsy-collapse is
    boot_agent's job at the call site (`max_daily_cost_usd or None`), so both None
    and 0.0 are acceptable here -- what matters is a real cap still comes through.
    """
    assert _constructed_session(max_daily_cost_usd=None)._max_daily_cost_usd is None
    assert _constructed_session(max_daily_cost_usd=0.0)._max_daily_cost_usd in (None, 0.0)
    assert _constructed_session(max_daily_cost_usd=5.0)._max_daily_cost_usd == 5.0


def test_all_ten_promoted_values_reach_their_attributes() -> None:
    """Every promoted stash must land on the matching attribute, not a neighbour's.

    Zero-behaviour-change guard (T-45 SS5 point 7): distinct sentinel values per
    parameter, so a swapped assignment (e.g. episodic_top_k written into
    graph_recall) would fail this test even though both attributes "exist".
    """
    sub_agent_client = MagicMock(name="sub_agent_client")
    browser_controller = MagicMock(name="browser_controller")
    schedule_runner = MagicMock(name="schedule_runner")
    scheduler = MagicMock(name="scheduler")
    provider_registry = MagicMock(name="provider_registry")
    secret_store = MagicMock(name="secret_store")

    session = _constructed_session(
        max_daily_cost_usd=12.5,
        sub_agent_client=sub_agent_client,
        browser_controller=browser_controller,
        schedule_runner=schedule_runner,
        scheduler=scheduler,
        episodic_top_k=7,
        graph_recall=False,
        durable_title=True,
        provider_registry=provider_registry,
        secret_store=secret_store,
    )

    assert session._max_daily_cost_usd == 12.5
    assert session._sub_agent_client is sub_agent_client
    assert session._browser_controller is browser_controller
    assert session._schedule_runner is schedule_runner
    assert session._scheduler is scheduler
    assert session._episodic_top_k == 7
    assert session._graph_recall is False
    assert session._durable_title is True
    assert session._provider_registry is provider_registry
    assert session._secret_store is secret_store


def test_constructor_defaults_match_the_pre_promotion_declared_defaults() -> None:
    """Omitting all ten kwargs must reproduce today's declared __init__ defaults
    (T-45 SS3.2) -- this is what makes the promotion 'zero behaviour change' for
    every existing hand-built AgentSession in the test tree that never passes them.
    """
    session = _constructed_session()

    assert session._max_daily_cost_usd is None
    assert session._sub_agent_client is None
    assert session._browser_controller is None
    assert session._schedule_runner is None
    assert session._scheduler is None
    assert session._episodic_top_k == 3
    assert session._graph_recall is True
    assert session._durable_title is False
    assert session._provider_registry is None
    assert session._secret_store is None
