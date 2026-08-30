"""T-44 — characterization tests for `boot_agent`'s staged decomposition.

This is CLAUDE.md Rule 2 (the regression law) applied to a pure refactor: there is
no bug, so the instrument is a characterization test that pins the OBSERVABLE
output of a real boot -- the event sequence, the prompt's dependency on the
tool-registration result, and the collaborator wiring on the returned session --
*before* `boot_agent`'s body is decomposed into `hachi.gateway.boot` stages, so the
decomposition is provably behaviour-neutral. See
docs/audit/2026-07-19/tasks/T-44-boot-agent-staged-builders.md section 7.

Isolation pattern mirrors ``tests/test_feature_liveness.py``'s ``live_session``
fixture (T-13) -- boots a REAL ``AgentSession`` into an isolated ``tmp_path``
profile so this module exercises the actual composition root, not a mock of it.
Re-implemented here (not imported) because tests do not import fixtures across
files in this codebase's style, and because this module's needs are narrower:
one real boot, shared read-only across all three characterization tests, with an
`on_event` recorder attached from the very first line of `boot_agent` so the
event-order test sees the true sequence.

``EXPECTED_BOOT_EVENTS`` / ``EXPECTED_BOOT_PROGRESS_STEPS`` below were captured
from one real, isolated boot against the pre-T-44 `boot_agent` (verified by
direct inspection of every ``emit_runtime_event(BOOT_PROGRESS, ...)`` call site
in `orchestrator.py`, then cross-checked by actually running the boot) -- not
hand-derived from reading the numbered step comments, which T-44 section 3.3
proves lie about execution order.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any
from unittest.mock import patch

import pytest

from hachi.core.paths import HachiPaths
from hachi.core.paths import get_hachi_paths as _REAL_GET_HACHI_PATHS

# ---------------------------------------------------------------------------
# Boot-time path isolation -- see tests/test_feature_liveness.py's module
# docstring for the full rationale. A profile-scoped boot must never touch the
# developer's real %LOCALAPPDATA%\hachi.
# ---------------------------------------------------------------------------


def _patch_every_get_hachi_paths_binding(fake_paths: HachiPaths) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    patched_module_ids: set[int] = set()
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            bound = getattr(module, "get_hachi_paths", None)
        except Exception:
            continue
        if bound is _REAL_GET_HACHI_PATHS and id(module) not in patched_module_ids:
            patched_module_ids.add(id(module))
            stack.enter_context(patch.object(module, "get_hachi_paths", return_value=fake_paths))
    return stack


# ollama: key-free, no network at client construction (matches test_feature_liveness.py
# T-13 §3.4a). in_memory: no ChromaDB PersistentClient -- keeps this boot fast and sidesteps
# BB-3 (never patch.dict(sys.modules) over chromadb; a real boot never needs to).
_PROFILE_TOML = """
[provider]
kind = "ollama"
model = "llama3"

[memory]
backend = "in_memory"

[events]
bus_funnel = true

[eval_reports]
enabled = false

[memory_gardener]
enabled = false
"""

# Captured from a real, isolated boot against pre-T-44 orchestrator.py (permissions
# step through boot.complete). Every "boot.progress" carries a "step" field in its
# payload -- asserted separately below via EXPECTED_BOOT_PROGRESS_STEPS so a stage
# reordering that swaps two same-named events cannot pass silently.
EXPECTED_BOOT_EVENTS: list[str] = [
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.progress",
    "boot.complete",
]

EXPECTED_BOOT_PROGRESS_STEPS: list[str] = [
    "permissions",
    "config",
    "skills",
    "integrations",
    "mcp",
    "native_integrations",
    "prompt",
    "llm",
    "memory",
    "tools",
    "loop",
]


@pytest.fixture(scope="module")
def shared_boot(tmp_path_factory: pytest.TempPathFactory):
    """One real `boot_agent()` call, shared read-only by all tests in this module.

    All three characterization tests in this file observe facts about the SAME
    boot (the event sequence recorded from t=0, and the resulting session's
    prompt/wiring) rather than each paying for -- and each potentially
    re-deriving a slightly different -- independent boot.
    """
    root = tmp_path_factory.mktemp("boot-stages")
    config_dir = root / "config"
    data_dir = root / "data"
    cache_dir = root / "cache"
    state_dir = root / "state"
    for directory in (config_dir, data_dir, cache_dir, state_dir):
        directory.mkdir(parents=True, exist_ok=True)

    paths = HachiPaths(
        config_dir=config_dir, data_dir=data_dir, cache_dir=cache_dir, state_dir=state_dir
    )
    paths.skills_dir.mkdir(parents=True, exist_ok=True)
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
    paths.global_config_toml.write_text(_PROFILE_TOML, encoding="utf-8")

    stack = _patch_every_get_hachi_paths_binding(paths)
    # SQLiteJobQueue's default db path comes from an UNRELATED PlatformDirs(...) call
    # in hachi.jobs.queue that get_hachi_paths() has no say over (test_feature_liveness.py
    # hits the same gap) -- patched separately so boot_agent's bare SQLiteJobQueue()
    # cannot write into the developer's real AppData.
    stack.enter_context(
        patch("hachi.jobs.queue._default_db_path", return_value=state_dir / "jobs.db")
    )

    seen: list[tuple[str, dict[str, Any]]] = []
    try:
        from hachi.gateway.orchestrator import boot_agent

        session = boot_agent(on_event=lambda name, payload: seen.append((name, payload)))
    except Exception:
        stack.close()
        raise

    try:
        yield session, seen
    finally:
        try:
            session.shutdown()
        except Exception:
            pass
        stack.close()


def test_boot_emits_events_in_a_stable_order(shared_boot: Any) -> None:
    """Pin the boot event sequence so a stage reordering cannot pass silently.

    This is the single highest-value guard in T-44: stage extraction that changes
    WHEN an event fires -- or that fires the wrong one at a given point -- is
    exactly the failure mode a staged decomposition risks. Compared against the
    literal baseline recorded from a real run (see module docstring), not a
    guess, and not derived from the code under test.
    """
    _session, seen = shared_boot

    names = [name for name, _payload in seen]
    assert names == EXPECTED_BOOT_EVENTS, (
        f"boot event NAME sequence changed.\nexpected: {EXPECTED_BOOT_EVENTS}\ngot:      {names}"
    )

    progress_steps = [payload.get("step") for name, payload in seen if name == "boot.progress"]
    assert progress_steps == EXPECTED_BOOT_PROGRESS_STEPS, (
        "boot.progress STEP LABEL order changed -- a stage fired its progress marker "
        f"at the wrong point.\nexpected: {EXPECTED_BOOT_PROGRESS_STEPS}\ngot:      {progress_steps}"
    )


def test_prompt_reflects_registered_tool_count(shared_boot: Any) -> None:
    """The system prompt's tool-group list is built FROM the tool registry -- T-44 section 3.3.

    T-44's own worked example (spec section 7) asserts a *numeric* tool count
    appears in the prompt. That does not hold here: the prompt's numeric
    "integration_count" is `integration_count + mcp_tool_count`, both fixed
    before tool registration ever runs (verified by reading orchestrator.py and
    confirmed against a real boot) -- see this task's report for the "false
    claim" writeup. The genuinely tool-registration-DOWNSTREAM datum the prompt
    embeds is the sorted **domain tool GROUP NAME list** (`tool_groups=`), which
    `build_stable_base` only receives after every ToolSpec has registered and
    `tool_registry.classify()`/`reclassify_by_prefix()` have run. If a
    decomposition moves prompt assembly upstream of tool registration (following
    the misleading "5.5" numbering), this list goes stale/empty and this test
    catches it.
    """
    session, _seen = shared_boot

    registry = session.loop.registry
    skill_group_names = {node.name for node in session.skill_engine.get_all_nodes()}
    domain_groups = sorted(registry.available_groups() - {"core"} - skill_group_names)
    prompt = session.loop.base_system_prompt

    assert domain_groups, (
        "no domain tool groups were registered against this profile -- the fixture's "
        "premise (tool registration produces group names the prompt embeds) cannot be "
        "exercised without at least one; if this is a genuine profile regression, do not "
        "weaken this assertion -- fix the profile"
    )
    missing = [group for group in domain_groups if group not in prompt]
    assert not missing, (
        f"tool group(s) {missing!r} are registered on the live tool_registry but do not "
        "appear in the system prompt -- prompt assembly ran BEFORE tool registration (or "
        "against a stale group list), which is exactly the T-44 section 3.3 numbering trap"
    )


def test_booted_session_has_the_expected_wiring(shared_boot: Any) -> None:
    """Every collaborator boot_agent is supposed to attach is actually attached.

    T-45 already promoted these from post-construction stashes to constructor
    arguments (see AgentSession.__init__ and tests/test_agent_session_construction.py,
    which covers the CONSTRUCTOR mechanism itself). This test covers a different
    fact: that a real `boot_agent()` call supplies a genuine, non-None collaborator
    for each -- i.e. that decomposing `boot_agent`'s BODY into stages does not
    silently drop a value on the way to the constructor call. `hasattr` alone
    would not catch that (the constructor's own defaults already guarantee the
    attribute exists), so this asserts identity/non-None-ness instead.
    """
    session, _seen = shared_boot

    for attr in (
        "_sub_agent_client",
        "_browser_controller",
        "_provider_registry",
        "_secret_store",
        "_schedule_runner",
        "_scheduler",
    ):
        assert hasattr(session, attr), f"boot dropped {attr}"
        value = getattr(session, attr)
        assert value is not None, (
            f"boot_agent attached {attr}=None -- a real boot against this profile must "
            "produce a genuine collaborator, not the constructor's own unset default"
        )
