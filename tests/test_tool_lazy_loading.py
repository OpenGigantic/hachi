"""Tests for per-turn lazy tool-loading: curated core + on-demand groups.

Invariant under test: only "core" tools are sent by default, optional groups are
unlocked mid-turn via use_tools()/load_skill(), and no tool is ever permanently
uncallable.
"""

from __future__ import annotations

from tokoboros.core.llm import LLMResponse, TokenUsage, ToolCall
from tokoboros.core.loop import ExecutionLoop, ToolRegistry
from tokoboros.core.sub_agent import SubAgentMemory
from tokoboros.tools.og_tools import USE_TOOLS_SCHEMA, _make_use_tools

_EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}


def _tool(name: str) -> dict:
    return {"name": name, "description": name, "input_schema": _EMPTY_SCHEMA}


def _grouped_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_tool("read_file"), lambda **_k: "ok")  # default -> core
    reg.register(USE_TOOLS_SCHEMA, _make_use_tools(reg), group="core")
    reg.register(_tool("load_skill"), lambda **_k: "loaded", group="core")
    reg.register(_tool("schedule_task"), lambda **_k: "scheduled", group="schedule")
    reg.register(_tool("python_sandbox"), lambda **_k: "ran", group="sandbox")
    reg.register(_tool("fetch_wiki_summary"), lambda **_k: "wiki", group="wiki")
    return reg


class _RecordingLLM:
    """Scripted LLM that records the tool names it was offered on each call."""

    model = "fake-model"

    def __init__(self, script: list[list[ToolCall]]) -> None:
        self._script = script
        self._i = 0
        self.tools_seen: list[set[str]] = []

    def generate(self, **kwargs) -> LLMResponse:
        self.tools_seen.append({t["name"] for t in (kwargs.get("tools") or [])})
        calls = self._script[self._i] if self._i < len(self._script) else []
        self._i += 1
        return LLMResponse(content="" if calls else "done", tool_calls=calls, usage=TokenUsage())

    def generate_stream(self, **kwargs):
        response = self.generate(**kwargs)
        return response
        yield  # makes this a generator function


# --- ToolRegistry unit tests ---------------------------------------------


def test_get_schemas_filters_by_active_groups() -> None:
    reg = _grouped_registry()
    all_names = {s["name"] for s in reg.get_schemas(None)}
    core = {s["name"] for s in reg.get_schemas(set())}
    assert {"read_file", "use_tools", "load_skill"} <= core
    assert "schedule_task" not in core and "python_sandbox" not in core
    sched = {s["name"] for s in reg.get_schemas({"schedule"})}
    assert "schedule_task" in sched and "python_sandbox" not in sched
    assert core < all_names  # strict subset — gating actually reduces the set


def test_classify_assigns_groups_and_preserves_explicit_stamps() -> None:
    reg = ToolRegistry()
    reg.register(_tool("read_file"), lambda **_k: "")  # register default -> core
    reg.register(_tool("schedule_task"), lambda **_k: "")  # default core, reclassified
    reg.register(_tool("fetch_wiki_summary"), lambda **_k: "", group="wiki")  # explicit
    reg.register(_tool("slack_post"), lambda **_k: "")  # unmapped -> default fallback
    reg.classify(
        core_names={"read_file"},
        group_by_name={"schedule_task": "schedule"},
        default="integrations",
    )
    assert reg._groups["read_file"] == "core"
    assert reg._groups["schedule_task"] == "schedule"
    assert reg._groups["fetch_wiki_summary"] == "wiki"  # explicit stamp preserved
    assert reg._groups["slack_post"] == "integrations"  # fallback applied (not stuck core)
    assert reg.available_groups() == {"core", "schedule", "wiki", "integrations"}


def test_no_tool_is_permanently_hidden() -> None:
    reg = _grouped_registry()
    all_names = {s["name"] for s in reg.get_schemas(None)}
    reachable = {s["name"] for s in reg.get_schemas(reg.available_groups() - {"core"})}
    assert reachable == all_names  # activating every group reveals everything


# --- ExecutionLoop integration tests -------------------------------------


def _loop(reg: ToolRegistry, llm: _RecordingLLM) -> ExecutionLoop:
    return ExecutionLoop(llm, reg, SubAgentMemory(), base_system_prompt="x")


def test_use_tools_unlocks_group_mid_turn() -> None:
    reg = _grouped_registry()
    llm = _RecordingLLM(
        [
            [ToolCall(id="a", name="use_tools", input={"group": "schedule"})],
            [ToolCall(id="b", name="schedule_task", input={})],
            [],
        ]
    )
    loop = _loop(reg, llm)
    loop.add_user_message("schedule something")
    loop.run(max_iterations=5)
    assert "schedule" in loop._active_groups
    assert "schedule_task" not in llm.tools_seen[0]  # gated before activation
    assert "schedule_task" in llm.tools_seen[1]  # available after activation


def test_load_skill_auto_activates_its_group() -> None:
    reg = _grouped_registry()
    llm = _RecordingLLM(
        [
            [ToolCall(id="a", name="load_skill", input={"name": "wiki"})],
            [],
        ]
    )
    loop = _loop(reg, llm)
    loop.add_user_message("look up sunflowers")
    loop.run(max_iterations=5)
    assert "wiki" in loop._active_groups
    assert "fetch_wiki_summary" not in llm.tools_seen[0]
    assert "fetch_wiki_summary" in llm.tools_seen[1]


def test_active_groups_reset_between_turns() -> None:
    reg = _grouped_registry()
    activate = _RecordingLLM(
        [[ToolCall(id="a", name="use_tools", input={"group": "schedule"})], []]
    )
    loop = _loop(reg, activate)
    loop.add_user_message("go")
    loop.run(max_iterations=5)
    assert "schedule" in loop._active_groups

    # New turn: groups must reset; the first call sees only core again.
    llm2 = _RecordingLLM([[]])
    loop.llm_client = llm2
    loop.add_user_message("go again")
    loop.run(max_iterations=5)
    assert loop._active_groups == set()
    assert "schedule_task" not in llm2.tools_seen[0]


# --- Orchestrator taxonomy guard -----------------------------------------


def test_core_tool_set_size_guard() -> None:
    from tokoboros.gateway.orchestrator import _CORE_TOOL_NAMES

    assert 18 <= len(_CORE_TOOL_NAMES) <= 24  # budget can't silently regress
    assert "use_tools" in _CORE_TOOL_NAMES
    assert "browse_web" in _CORE_TOOL_NAMES
