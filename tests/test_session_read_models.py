"""Characterization tests for AgentSession's 20 UI read models (T-46).

T-46 moves `AgentSession`'s `snapshot_*` / `query_*` methods behind a new
`SessionReadModels` facade (`hachi.gateway.session_read_models`). This is a
pure, behaviour-preserving extraction -- there is no bug to reproduce, so per
CLAUDE.md Rule 2 applied to a refactor (see the T-46 spec, section 7), the
regression instrument is a *characterization* test: pin the exact observed
output of all 20 read models against REAL (or, where a subsystem would be too
heavy to build for real, a small hand-rolled but concrete-state) subsystems,
BEFORE the move, and prove the same test still passes AFTER the move.

These tests deliberately avoid `unittest.mock.MagicMock` for the subsystems
under test: a `MagicMock` attribute access silently returns another mock
instead of failing, so an assertion against a Mock's return value proves
nothing about real behaviour. Where a real production class is cheap to
construct (`SkillEngine`, `MCPManager`, `Scheduler`/`ScheduleStore`,
`SessionStore`, `MemoryVault`), this module builds one for real, isolated by
`tmp_path`. Where the real class is heavy and orthogonal to what a read model
touches (`ExecutionLoop`, `MemoryManager`), a minimal fake exposes only the
concrete attributes the read models actually read.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from hachi.core.cost import CostTracker
from hachi.core.llm import TokenUsage
from hachi.core.mcp_client import MCPManager
from hachi.core.memory.manager import TaskRecord
from hachi.core.memory.vault import MemoryVault
from hachi.core.runtime_read_models import RuntimeEvent, RuntimeReadModelStore
from hachi.core.session_store import SessionStore
from hachi.gateway.orchestrator import AgentSession
from hachi.jobs.scheduler import ScheduleKind, Scheduler, ScheduleStore
from hachi.og_engine.engine import SkillEngine
from hachi.og_engine.parser import OGNode

# The complete list of the 20 read models T-46 moves behind the facade
# (T-46 spec section 3.2). Order matches the spec's own table.
READ_MODELS = (
    "snapshot_session_state",
    "snapshot_usage",
    "snapshot_task_ledger",
    "snapshot_skills",
    "snapshot_gardener_diffs",
    "snapshot_og_explorer",
    "snapshot_permission_audit",
    "snapshot_schedules",
    "snapshot_mcp_inventory",
    "snapshot_active_jobs",
    "snapshot_failed_jobs",
    "snapshot_tool_calls",
    "snapshot_runtime_events",
    "snapshot_events_since",
    "snapshot_memory_facts",
    "snapshot_og_graph",
    "query_recent_sessions",
    "query_session_messages",
    "query_memory_recall",
    "query_subagent_reports",
)


class _FakeLoop:
    """Minimal stand-in for ExecutionLoop -- only what read models read.

    `ExecutionLoop` itself is a heavy engine object (registry, LLM client,
    turn machinery) that is orthogonal to the read models under test; only
    `base_system_prompt` (read once in `AgentSession.__init__`) and
    `cost_tracker` (read by `snapshot_usage` / `snapshot_session_state`) are
    ever touched by anything exercised here. `cost_tracker` is a REAL
    `CostTracker`, not faked, so its `.snapshot()` output is genuinely
    computed rather than asserted-against-itself.
    """

    def __init__(self, cost_tracker: CostTracker) -> None:
        self.base_system_prompt = "You are Hachi, a helpful agent."
        self.cost_tracker = cost_tracker


class _FakeMemory:
    """Minimal stand-in for MemoryManager -- concrete, known state.

    Only `active_skill`, `_last_user_query`, and `task_ledger` are read by
    any of the 20 read models; `set_session_id` / `set_session_store` are
    called by `AgentSession.set_session_store` (production code) and are
    no-ops here, mirroring what a real `MemoryManager` does with them for
    the purposes of this test.
    """

    def __init__(self) -> None:
        self.active_skill: str | None = "weather_skill"
        self._last_user_query = "what's the weather in Tokyo?"
        self.task_ledger = [
            TaskRecord(
                skill_name="weather_skill",
                user_query="what's the weather in Tokyo?",
                summary="Reported sunny, 22C.",
            ),
            TaskRecord(
                skill_name="notes_skill",
                user_query="save a note about the trip",
                summary="Saved a note to the journal.",
            ),
        ]
        self.session_id: str | None = None
        self.session_store: Any = None

    def set_session_id(self, session_id: str) -> None:
        self.session_id = session_id

    def set_session_store(self, store: Any, session_id: str) -> None:
        self.session_store = store


@pytest.fixture
def booted_session(tmp_path):
    """A REAL, constructor-built AgentSession wired to isolated-but-real subsystems.

    - `skill_engine`: a real `SkillEngine` with two real `OGNode`s poked into
      `_nodes` (the same technique `tests/test_skill_inventory_dedup.py`
      uses) -- exercises the genuine `get_skill_inventory`/`get_all_nodes`
      code paths, not a stand-in.
    - `mcp_manager`: a real `MCPManager` with one boot-failure entry poked
      into `_boot_failures` (avoids spawning a real subprocess for a live
      server while still exercising the genuine `get_inventory` code path).
    - `scheduler`: a real `Scheduler` over a real (tmp_path-backed)
      `ScheduleStore`, with one real schedule added via the public `add()` API.
    - `vault`: a real, tmp_path-backed `MemoryVault` (ChromaDB-backed) seeded
      with one fact per collection via the public upsert APIs -- BB-3 says
      never mock chromadb; this uses the real thing, isolated by directory.
    - `_session_store`: a real, tmp_path-backed `SessionStore` (SQLite),
      seeded with one session and two messages via the public API.
    - `loop`/`memory`: heavy/orthogonal engine objects, so minimal fakes with
      concrete, known field values stand in (see the fake classes above).
    """
    skill_engine = SkillEngine()
    node_a = OGNode(
        name="weather_skill",
        source_file=str(tmp_path / "weather.og"),
        role="skill",
        keywords=["weather", "forecast"],
        dense="Look up the weather for a city.",
    )
    node_b = OGNode(
        name="notes_skill",
        source_file=str(tmp_path / "notes.og"),
        role="skill",
        keywords=["notes", "journal"],
        requires=["weather_skill"],
        dense="Save a note to the journal.",
    )
    skill_engine._nodes["weather.og:weather_skill"] = node_a
    skill_engine._nodes["notes.og:notes_skill"] = node_b

    mcp_manager = MCPManager()
    mcp_manager._boot_failures["broken-server"] = "startup failed: ECONNREFUSED"

    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    scheduler = Scheduler(schedule_store)
    scheduler.add(
        name="daily-digest",
        task="Summarize today's notes",
        kind=ScheduleKind.ONCE,
        run_at="2030-01-01T00:00:00+00:00",
    )

    cost_tracker = CostTracker()
    cost_tracker.record("test-model", TokenUsage(input_tokens=1000, output_tokens=500))

    read_models = RuntimeReadModelStore()

    session = AgentSession(
        loop=_FakeLoop(cost_tracker),
        skill_engine=skill_engine,
        memory=_FakeMemory(),
        integrations=SimpleNamespace(),
        mcp_manager=mcp_manager,
        permission_gate=SimpleNamespace(),
        model_name="test-model",
        read_models=read_models,
        scheduler=scheduler,
    )

    vault = MemoryVault(brain_dir=str(tmp_path / "vault"), memory_dir=tmp_path / "memory")
    vault.upsert_fact("The workspace uses FastAPI 0.115.", category="workspace_state")
    vault.upsert_user_fact("User prefers dark mode.", category="preference")
    vault.upsert_agent_fact("Be concise.", category="directive")
    session.set_vault(vault)

    session_store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    session.set_session_store(session_store, model="test-model")
    session_store.log_message(session._session_id, role="user", content="hello agent")
    session_store.log_message(
        session._session_id, role="assistant", content="hello, how can I help?"
    )

    yield session

    vault.close()


def test_every_read_model_returns_the_same_shape_after_the_move(booted_session) -> None:
    """All 20 read models stay callable and keep their return TYPE.

    Cheap, broad net: catches a dropped method or a return-type change across
    the whole surface in one assertion. Two of the 20 (`snapshot_session_state`,
    `snapshot_mcp_inventory`) return a single frozen snapshot dataclass rather
    than a tuple/list/dict -- the T-46 spec's own sketch checks only
    `isinstance(result, (tuple, list, dict))`, which is too narrow and would
    misreport those two as "changed type" even though nothing changed; this
    test accepts both real shapes actually present in the API today.
    """
    assert len(READ_MODELS) == 20
    for name in READ_MODELS:
        method = getattr(booted_session, name, None)
        assert callable(method), f"read model disappeared: {name}"

        # These three take a REQUIRED positional arg with no default -- calling
        # them with zero arguments (as a naive "same shape" sweep might) raises
        # TypeError regardless of the refactor, so supply one.
        if name == "query_session_messages":
            result = method(booted_session._session_id)
        elif name == "query_memory_recall":
            result = method("FastAPI")
        elif name == "snapshot_events_since":
            result = method(after=0)
        else:
            result = method()

        is_collection = isinstance(result, (tuple, list, dict))
        is_snapshot_dataclass = dataclasses.is_dataclass(result) and not isinstance(result, type)
        assert is_collection or is_snapshot_dataclass, f"{name} changed return type: {type(result)}"


def test_store_derived_read_models_reflect_appended_events(booted_session) -> None:
    """The store-derived models must still read the SAME store instance.

    Guards the most likely wiring mistake: the facade constructing its own
    RuntimeReadModelStore instead of receiving the session's. Exercises two
    of the six purely store-derived methods (`snapshot_runtime_events`,
    `snapshot_events_since`) plus the store-backed half of the
    `query_subagent_reports` hybrid, so a facade that silently drops the
    session's real store cannot pass by only wiring one accessor correctly.
    """
    before_events = len(booted_session.snapshot_runtime_events(limit=1000))
    before_since = len(booted_session.snapshot_events_since(after=0, limit=1000))
    before_reports = len(booted_session.query_subagent_reports(top_k=1000))

    booted_session._read_models.append_event(
        RuntimeEvent(name="t46.probe", payload={"k": "v"}, timestamp="2026-07-19T00:00:00Z")
    )
    booted_session._read_models.append_event(
        RuntimeEvent(
            name="subagent.completed",
            payload={
                "role": "researcher",
                "status": "success",
                "duration": 12.5,
                "iterations": 3,
                "summary": "Found 3 relevant docs",
            },
            timestamp="2026-07-19T00:00:01Z",
        )
    )

    after_events = booted_session.snapshot_runtime_events(limit=1000)
    assert len(after_events) == before_events + 2
    assert any(e.name == "t46.probe" for e in after_events)

    after_since = booted_session.snapshot_events_since(after=0, limit=1000)
    assert len(after_since) == before_since + 2

    after_reports = booted_session.query_subagent_reports(top_k=1000)
    assert len(after_reports) == before_reports + 1
    assert any(
        r.role == "researcher" and r.summary == "Found 3 relevant docs" for r in after_reports
    )


def test_live_read_models_reflect_subsystem_state(booted_session) -> None:
    """The 12 live models must read the SESSION's subsystems, not fresh ones.

    Each assertion compares the read model's output against either a directly
    observed call on the SAME live subsystem object the session holds, or
    against the concrete value that was seeded into that subsystem -- never
    against a mock's return value.
    """
    # skill engine -- snapshot_skills / snapshot_og_explorer / snapshot_og_graph
    skills = booted_session.snapshot_skills()
    live_inventory = booted_session.skill_engine.get_skill_inventory()
    assert len(skills) == len(live_inventory) == 2
    assert {s.name for s in skills} == {"weather_skill", "notes_skill"}

    explorer = booted_session.snapshot_og_explorer()
    assert explorer["node_count"] == len(booted_session.skill_engine.get_all_nodes()) == 2

    graph = booted_session.snapshot_og_graph()
    assert graph["count"] == 2
    assert {n["name"] for n in graph["nodes"]} == {"weather_skill", "notes_skill"}

    # mcp_manager -- snapshot_mcp_inventory
    mcp = booted_session.snapshot_mcp_inventory()
    assert mcp.server_count == 1
    assert mcp.servers[0].name == "broken-server"
    assert mcp.servers[0].status == "failed"
    assert mcp.servers[0].err == "startup failed: ECONNREFUSED"

    # scheduler -- snapshot_schedules
    schedules = booted_session.snapshot_schedules()
    assert len(schedules) == 1
    assert schedules[0]["name"] == "daily-digest"

    # memory -- snapshot_task_ledger / snapshot_usage / snapshot_session_state
    ledger = booted_session.snapshot_task_ledger()
    assert [item.skill_name for item in ledger] == ["weather_skill", "notes_skill"]
    assert ledger[0].summary == "Reported sunny, 22C."

    usage = booted_session.snapshot_usage()
    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 500
    assert usage["total_tokens"] == 1500

    state = booted_session.snapshot_session_state()
    assert state.total_tokens == 1500
    assert state.active_skill == "weather_skill"
    assert state.session_id == booted_session._session_id

    # session store -- query_recent_sessions / query_session_messages
    recent = booted_session.query_recent_sessions()
    assert any(row["id"] == booted_session._session_id for row in recent)

    messages = booted_session.query_session_messages(booted_session._session_id)
    assert [m["content"] for m in messages] == ["hello agent", "hello, how can I help?"]

    # vault -- snapshot_memory_facts / query_memory_recall
    facts = booted_session.snapshot_memory_facts()
    all_texts = {f["text"] for group in facts["groups"] for f in group["facts"]}
    assert "The workspace uses FastAPI 0.115." in all_texts
    assert "User prefers dark mode." in all_texts
    assert "Be concise." in all_texts

    recall = booted_session.query_memory_recall("FastAPI 0.115")
    assert any("FastAPI" in r.content for r in recall)
