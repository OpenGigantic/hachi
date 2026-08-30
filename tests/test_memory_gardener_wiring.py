"""Regression tests for T-08 (Option A — wire MemoryGardener up end-to-end).

Before this task: MemoryGardener.run() (the writer) had zero production callers;
AgentSession.snapshot_gardener_diffs (the reader) had zero callers AND was broken
(read memory_dir via a getattr that always defaulted to None); there was no route
and no write-back path for the pending_review proposals it produces. See
docs/audit/2026-07-19/tasks/T-08-memory-gardener.md and the BB-126 entry in
docs/bug-bank.md.

Three sections, matching the three independent pieces:
    A1 — the resident scheduler (gateway/orchestrator.py:_start_memory_gardener)
    A2 — GET /memory/diffs (the reader, now reachable)
    A3 — POST /memory/diffs/{id} (the write-back path, which did not exist at all)

Every test in this file asserts a REAL, observed property (a real file on disk,
a real HTTP response, a real background thread) — never a mock standing in for
the thing under test (see CLAUDE.md Rule 7 / the T-08 task's "treat every sketch
as a hypothesis" note).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hachi.core.memory.gardener import GardeningResult, MemoryGardener
from hachi.core.paths import HachiPaths
from hachi.core.settings import MemoryGardenerSettings, ResolvedConfig
from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app
from tests.conftest import control_plane


def _paths(tmp_path: Path) -> HachiPaths:
    """Mirror tests/test_eval_report_config.py's _paths() helper exactly."""
    return HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )


def _write_node(path: Path, name: str, dense: str = "") -> None:
    """Write a minimal .og file containing a single ::node with optional ::dense text."""
    path.write_text(f"::node[{name}]\n::dense\n{dense}\n", encoding="utf-8")


def _make_agent_session():
    """A minimal, REAL AgentSession — mocked collaborators, real gardener-diffs method.

    snapshot_gardener_diffs() (the method under test in the A2 section) touches
    none of loop/skill_engine/memory/integrations/mcp_manager/permission_gate after
    the BB-5 fix, so MagicMocks are safe stand-ins for everything AgentSession.__init__
    needs — but the AgentSession instance itself, and its snapshot_gardener_diffs
    method, are real and unmocked.
    """
    from hachi.gateway.orchestrator import AgentSession

    return AgentSession(
        loop=MagicMock(),
        skill_engine=MagicMock(),
        memory=MagicMock(),
        integrations=MagicMock(),
        mcp_manager=MagicMock(),
        permission_gate=MagicMock(),
        model_name="test-model",
    )


# ---------------------------------------------------------------------------
# Config registration — BB-101-class guard (T-08's explicit CRITICAL instruction)
# ---------------------------------------------------------------------------


def test_memory_gardener_section_survives_resolved_config(tmp_path: Path) -> None:
    """[memory_gardener] must reach the exact dict boot_agent's A1 gate reads.

    Mirrors test_eval_reports_section_survives_resolved_config (T-07/BB-101)
    exactly: settings.toml on disk -> load_effective_config() -> model_dump(mode=
    "python", exclude_none=True) (gateway.workspace.load_config()'s own call) ->
    MemoryGardenerSettings.model_validate(config.get("memory_gardener", {})) (the
    exact line boot_agent's T-08 A1 block runs). Proves the setting actually
    changes the flag that gates whether the daemon starts, not just that a schema
    field is declared. If ResolvedConfig has no `memory_gardener` field, this
    section is silently dropped by ConfigDict(extra="ignore") and this test fails.
    """
    from hachi.core.config_loader import load_effective_config

    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.global_config_toml.write_text(
        "[memory_gardener]\nenabled = true\ninterval_seconds = 3600\nsimilarity_threshold = 0.5\n",
        encoding="utf-8",
    )

    resolved = load_effective_config(paths=paths)
    dumped = resolved.model_dump(mode="python", exclude_none=True)
    assert "memory_gardener" in dumped, (
        "the [memory_gardener] section was dropped by ConfigDict(extra='ignore') — "
        "ResolvedConfig has no field for it (BB-101-class bug)"
    )

    gardener_cfg = MemoryGardenerSettings.model_validate(dumped.get("memory_gardener", {}))
    assert gardener_cfg.enabled is True, (
        "the user's [memory_gardener] enabled=true never reached the daemon's gate — "
        "boot_agent would see enabled=False and never start the scheduler"
    )
    assert gardener_cfg.interval_seconds == 3600
    assert gardener_cfg.similarity_threshold == 0.5


def test_memory_gardener_settings_reachable_from_resolved_config() -> None:
    """Direct, non-reflective proof that ResolvedConfig declares the field.

    tests/test_eval_report_config.py::test_all_settings_models_are_registered_on_
    resolved_config already generalises this reflectively (and now also covers
    MemoryGardenerSettings automatically) — this is the same guarantee asserted
    directly against this module, so a reader of THIS file does not have to go
    find the reflective test to see the contract.
    """
    assert "memory_gardener" in ResolvedConfig.model_fields
    default = ResolvedConfig()
    assert isinstance(default.memory_gardener, MemoryGardenerSettings)
    assert default.memory_gardener.enabled is False  # opt-in, dormant by default


# ---------------------------------------------------------------------------
# A1 — the resident scheduler
# ---------------------------------------------------------------------------


def test_start_memory_gardener_runs_on_configured_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1: the daemon actually runs the gardener on the configured interval.

    Injected clock (patches the real `time.sleep`), never a real sleep. The
    daemon loop does `import time as _time; _time.sleep(sleep_seconds)` — that
    binds to the SAME module object as `import time`, so patching `time.sleep`
    on the shared module intercepts it regardless of the local alias.

    Proves run() executed for REAL (not just that some function was invoked):
    two .og files share a node name, which MemoryGardener's dedup pass detects,
    auto-applies, and persists to pending_diffs.json — asserted from the file on
    disk, not a mock.
    """
    from hachi.gateway.orchestrator import _start_memory_gardener

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    node_a = memory_dir / "a.og"
    node_b = memory_dir / "b.og"
    _write_node(node_a, "shared_fact", "duplicate content")
    _write_node(node_b, "shared_fact", "duplicate content")
    # Force distinct mtimes so the dedup pass has a deterministic newest/older.
    import os

    now = time.time()
    os.utime(node_a, (now, now))
    os.utime(node_b, (now + 5, now + 5))

    calls = {"n": 0}
    tick_done = threading.Event()

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            return  # let the loop proceed straight through to the first tick
        # Second call means the first tick's MemoryGardener(...).run() has
        # already returned (loop structure: sleep() -> try: tick() except: pass
        # -> loop back -> sleep() again). Signal completion, then park this
        # daemon thread harmlessly forever instead of raising: pytest's
        # thread-exception hook flags ANY exception escaping a thread (including
        # SystemExit) as an unhandled-thread-exception warning, so parking is the
        # clean way to stop caring about the thread. Safe — it is daemon=True and
        # the test does not need it to actually terminate.
        tick_done.set()
        threading.Event().wait()

    monkeypatch.setattr(time, "sleep", fake_sleep)

    before_idents = {t.ident for t in threading.enumerate()}
    settings = MemoryGardenerSettings(enabled=True, interval_seconds=7)
    _start_memory_gardener(memory_dir, settings)

    new_threads = [
        t
        for t in threading.enumerate()
        if t.ident not in before_idents and t.name == "hachi-memory-gardener"
    ]
    assert len(new_threads) == 1, "expected exactly one new hachi-memory-gardener thread"

    assert tick_done.wait(timeout=5.0), "gardener tick did not complete within 5s"
    assert calls["n"] >= 2, "the daemon loop must call time.sleep before AND after its tick"

    diffs_path = memory_dir / "pending_diffs.json"
    assert diffs_path.exists(), "MemoryGardener.run() did not write pending_diffs.json on tick"
    on_disk = json.loads(diffs_path.read_text(encoding="utf-8"))
    assert len(on_disk["auto_applied"]) == 1
    assert on_disk["auto_applied"][0]["kind"] == "dedup_same_name"
    assert on_disk["auto_applied"][0]["name_a"] == "shared_fact"
    # Append-only governance: the older file got a ::changelog note, not an overwrite.
    assert "::changelog" in node_a.read_text(encoding="utf-8")


def test_start_memory_gardener_caps_sleep_at_one_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A week-long interval must not leave the daemon unkillable for an hour (mirrors
    _start_eval_report_scheduler's min(interval_seconds, 3600) cap)."""
    from hachi.gateway.orchestrator import _start_memory_gardener

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    captured: list[float] = []
    first_call = threading.Event()

    def spy_sleep(seconds: float) -> None:
        captured.append(seconds)
        first_call.set()
        # Park forever instead of raising — see the identical comment in
        # test_start_memory_gardener_runs_on_configured_interval for why.
        threading.Event().wait()

    monkeypatch.setattr(time, "sleep", spy_sleep)

    settings = MemoryGardenerSettings(enabled=True, interval_seconds=604800)  # 1 week
    _start_memory_gardener(memory_dir, settings)

    assert first_call.wait(timeout=5.0), "time.sleep was never called"
    assert captured[0] == 3600, f"expected the 3600s cap, got {captured[0]}"


def test_boot_agent_calls_start_memory_gardener_and_logs_at_info() -> None:
    """Structural (AST) check mirroring test_boot_agent_logs_when_eval_report_daemon_
    starts (T-07): boot_agent's `if _gardener_cfg.enabled:` gate must call
    _start_memory_gardener(...) AND logger.info(...) at the call site, so a user can
    tell "I enabled it and it started" from "I enabled it and the config never
    arrived" (the daemon's own start log only fires once its thread is already
    running) — the same BB-101 debt T-07 closed for eval_reports.
    """
    import ast
    import inspect

    import hachi.gateway.orchestrator as orch

    tree = ast.parse(inspect.getsource(orch))
    boot_agent = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "boot_agent"
    )
    gate = next(
        (
            node
            for node in ast.walk(boot_agent)
            if isinstance(node, ast.If)
            and any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_start_memory_gardener"
                for n in ast.walk(node)
            )
        ),
        None,
    )
    assert gate is not None, "boot_agent no longer calls _start_memory_gardener(...)"

    logs_at_boot = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "info"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "logger"
        for n in ast.walk(gate)
    )
    assert logs_at_boot, (
        "boot_agent must logger.info(...) at the [memory_gardener] enabled gate when "
        "the daemon starts (T-08 A1 acceptance criteria / BB-101 debt)"
    )


# ---------------------------------------------------------------------------
# A2 — GET /memory/diffs (the reader, now reachable)
# ---------------------------------------------------------------------------


def test_snapshot_gardener_diffs_no_longer_masked_by_getattr(tmp_path: Path) -> None:
    """BB-5: AgentSession.snapshot_gardener_diffs must resolve memory_dir via
    get_hachi_paths(), not a getattr(self.memory, ...) that always defaults to None.

    Directly exercises the previously-broken method (not the route) against a REAL
    pending_diffs.json on disk.
    """
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    node_a = memory_dir / "a.og"
    node_b = memory_dir / "b.og"
    _write_node(node_a, "topic_alpha", "loves long hikes in the mountains on weekends")
    _write_node(node_b, "topic_beta", "loves long hikes in the mountains on weekends")
    MemoryGardener(memory_dir).run()  # writes a real MERGE_SIMILAR pending_review entry

    fake_paths = HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path,  # HachiPaths.memory_dir == data_dir / "memory"
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )
    assert fake_paths.memory_dir == memory_dir

    session = _make_agent_session()
    # session.memory is a MagicMock with no real `memory_dir` attribute — accessing
    # `.memory_dir` on it returns ANOTHER auto-spec'd MagicMock (never None), so if
    # snapshot_gardener_diffs still used getattr(self.memory, "memory_dir", None) the
    # method would try to build a Path from a MagicMock and blow up (or, if the old
    # getattr default path were hit, silently return []). Either way this proves the
    # fix no longer routes through self.memory at all.

    import hachi.gateway.session_read_models as read_models_mod

    # T-46: AgentSession.snapshot_gardener_diffs's body now resolves get_hachi_paths
    # in hachi.gateway.session_read_models's namespace (the SessionReadModels facade
    # it was moved into), not gateway.orchestrator's.
    original_get_paths = read_models_mod.get_hachi_paths
    read_models_mod.get_hachi_paths = lambda: fake_paths
    try:
        diffs = session.snapshot_gardener_diffs()
    finally:
        read_models_mod.get_hachi_paths = original_get_paths

    assert len(diffs) == 1, f"expected 1 pending proposal, got {diffs}"
    assert diffs[0]["kind"] == "merge_similar"
    assert {diffs[0]["name_a"], diffs[0]["name_b"]} == {"topic_alpha", "topic_beta"}


def test_get_memory_diffs_route_returns_real_gardener_output_end_to_end(
    tmp_path: Path,
) -> None:
    """A2: GET /memory/diffs returns REAL gardener diffs end-to-end.

    Real MemoryGardener.run() writes pending_diffs.json to a tmp memory dir; the
    real (BB-5-fixed) AgentSession.snapshot_gardener_diffs() reads it back; wired
    through the real _InProcessGatewayRuntime.get_memory_gardener_diffs() and
    GatewayControlPlane, served by the real FastAPI route via TestClient. Asserted
    against BOTH the file on disk and the HTTP response — never a stub/mock
    standing in for the gardener or the reader.
    """
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    node_a = memory_dir / "a.og"
    node_b = memory_dir / "b.og"
    _write_node(node_a, "topic_alpha", "loves long hikes in the mountains on weekends")
    _write_node(node_b, "topic_beta", "loves long hikes in the mountains on weekends")
    MemoryGardener(memory_dir).run()

    fake_paths = HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )

    import hachi.gateway.session_read_models as read_models_mod
    from hachi.cli.main import _InProcessGatewayRuntime

    # T-46: AgentSession.snapshot_gardener_diffs's body now resolves get_hachi_paths
    # in hachi.gateway.session_read_models's namespace (the SessionReadModels facade
    # it was moved into), not gateway.orchestrator's.
    original_get_paths = read_models_mod.get_hachi_paths
    read_models_mod.get_hachi_paths = lambda: fake_paths
    try:
        runtime = _InProcessGatewayRuntime()
        session = _make_agent_session()
        with runtime._agents_lock:
            runtime._agents["test-session"] = session

        cp = control_plane(get_memory_gardener_diffs=runtime.get_memory_gardener_diffs)
        app = create_gateway_app(cp)

        with TestClient(app) as client:
            response = client.get("/memory/diffs")
    finally:
        read_models_mod.get_hachi_paths = original_get_paths

    assert response.status_code == 200
    data = response.json()
    assert len(data["pending_review"]) == 1
    proposal = data["pending_review"][0]
    assert proposal["kind"] == "merge_similar"
    assert {proposal["name_a"], proposal["name_b"]} == {"topic_alpha", "topic_beta"}

    on_disk = json.loads((memory_dir / "pending_diffs.json").read_text(encoding="utf-8"))
    assert on_disk["pending_review"][0]["id"] == proposal["id"]


def test_get_memory_diffs_route_empty_when_no_agent_booted() -> None:
    """Mirrors get_memory()/get_skills(): an unwired/no-session state is an empty
    list (HTTP 200), not an error — consistent with the sibling /memory routes."""
    cp = GatewayControlPlane()  # get_memory_gardener_diffs not wired
    app = create_gateway_app(cp)

    with TestClient(app) as client:
        response = client.get("/memory/diffs")
    assert response.status_code == 200
    assert response.json() == {"pending_review": []}


# ---------------------------------------------------------------------------
# A3 — POST /memory/diffs/{id}: the approve/reject write-back path
#
# This path did not exist at all before T-08 (GardeningProposal had no accept/
# reject action; run() only ever auto-applied DEDUP_SAME_NAME). Every assertion
# below is against the real persisted GardeningResult / real .og file content —
# never a mock of the decision.
# ---------------------------------------------------------------------------


class TestMemoryGardenerDecide:
    """MemoryGardener.decide() — the core write-back logic, independent of HTTP."""

    def _seed(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        node_a = memory_dir / "a.og"
        node_b = memory_dir / "b.og"
        _write_node(node_a, "topic_alpha", "loves long hikes in the mountains on weekends")
        _write_node(node_b, "topic_beta", "loves long hikes in the mountains on weekends")
        return memory_dir, node_a, node_b

    def test_approve_applies_append_only_and_persists(self, tmp_path: Path) -> None:
        memory_dir, node_a, node_b = self._seed(tmp_path)
        gardener = MemoryGardener(memory_dir)
        result = gardener.run()
        assert len(result.pending_review) == 1
        proposal_id = result.pending_review[0].id
        original_b_text = node_b.read_text(encoding="utf-8")
        original_a_text = node_a.read_text(encoding="utf-8")

        decided = gardener.decide(proposal_id, approve=True)

        assert decided.auto_applied is True
        # Append-only governance: original content survives verbatim; something
        # new was appended, never an overwrite.
        new_b_text = node_b.read_text(encoding="utf-8")
        assert new_b_text.startswith(original_b_text)
        assert new_b_text != original_b_text
        assert "::changelog" in new_b_text
        assert node_a.read_text(encoding="utf-8") == original_a_text  # untouched

        # Observed in the store: reloading from disk shows the decision, not a
        # return value we merely trust.
        reloaded = gardener.load_pending()
        assert reloaded.pending_review == []
        assert any(p.id == proposal_id for p in reloaded.auto_applied)

    def test_reject_drops_without_touching_any_og_file(self, tmp_path: Path) -> None:
        memory_dir, node_a, node_b = self._seed(tmp_path)
        gardener = MemoryGardener(memory_dir)
        result = gardener.run()
        proposal_id = result.pending_review[0].id
        original_a_text = node_a.read_text(encoding="utf-8")
        original_b_text = node_b.read_text(encoding="utf-8")

        decided = gardener.decide(proposal_id, approve=False)

        assert decided.auto_applied is False
        assert node_a.read_text(encoding="utf-8") == original_a_text
        assert node_b.read_text(encoding="utf-8") == original_b_text

        reloaded = gardener.load_pending()
        assert reloaded.pending_review == [], "rejected proposal must be dropped from the store"
        assert reloaded.auto_applied == [], "a reject must not be recorded as applied"

    def test_decide_unknown_id_raises_keyerror(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        gardener = MemoryGardener(memory_dir)
        gardener.run()  # writes an empty pending_diffs.json (no .og files yet)
        with pytest.raises(KeyError):
            gardener.decide("does-not-exist", approve=True)

    def test_decide_expire_stale_annotates_the_expired_node_itself(self, tmp_path: Path) -> None:
        """EXPIRE_STALE has no name_b/file_path_b — approve must annotate file_path_a."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        node = memory_dir / "stale.og"
        node.write_text(
            "::node[old_fact]\n::dense\nstale\n->[:valid_until=2000-01-01T00:00:00Z]\n",
            encoding="utf-8",
        )
        gardener = MemoryGardener(memory_dir)
        result = gardener.run()
        expired = [p for p in result.pending_review if p.kind == "expire_stale"]
        assert len(expired) == 1
        original_text = node.read_text(encoding="utf-8")

        decided = gardener.decide(expired[0].id, approve=True)

        assert decided.auto_applied is True
        new_text = node.read_text(encoding="utf-8")
        assert new_text.startswith(original_text)
        assert "expired_confirmed=true" in new_text


def test_post_memory_diff_decision_approve_route_end_to_end(tmp_path: Path) -> None:
    """A3 route: POST /memory/diffs/{id} {"action": "approve"} applies the proposal
    and persists it — observed in the real store (the JSON file + the .og file),
    not a mock, driven through the real FastAPI route."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    node_a = memory_dir / "a.og"
    node_b = memory_dir / "b.og"
    _write_node(node_a, "topic_alpha", "loves long hikes in the mountains on weekends")
    _write_node(node_b, "topic_beta", "loves long hikes in the mountains on weekends")
    result = MemoryGardener(memory_dir).run()
    proposal_id = result.pending_review[0].id

    fake_paths = HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )

    import hachi.gateway.in_process_runtime as runtime_mod
    from hachi.cli.main import _InProcessGatewayRuntime

    # T-41: _InProcessGatewayRuntime's methods resolve get_hachi_paths in
    # hachi.gateway.in_process_runtime's namespace now, not cli.main's.
    original_get_paths = runtime_mod.get_hachi_paths
    runtime_mod.get_hachi_paths = lambda: fake_paths
    try:
        runtime = _InProcessGatewayRuntime()
        cp = control_plane(decide_memory_gardener_diff=runtime.decide_memory_gardener_diff)
        app = create_gateway_app(cp)

        with TestClient(app) as client:
            response = client.post(f"/memory/diffs/{proposal_id}", json={"action": "approve"})
    finally:
        runtime_mod.get_hachi_paths = original_get_paths

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["action"] == "approve"
    assert data["proposal"]["auto_applied"] is True

    on_disk = json.loads((memory_dir / "pending_diffs.json").read_text(encoding="utf-8"))
    assert on_disk["pending_review"] == []
    assert any(p["id"] == proposal_id for p in on_disk["auto_applied"])
    assert "::changelog" in node_b.read_text(encoding="utf-8")


def test_post_memory_diff_decision_reject_route_drops_from_store(tmp_path: Path) -> None:
    """A3 route: POST /memory/diffs/{id} {"action": "reject"} drops the proposal —
    observed in the real store, and no .og file is touched."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    node_a = memory_dir / "a.og"
    node_b = memory_dir / "b.og"
    _write_node(node_a, "topic_alpha", "loves long hikes in the mountains on weekends")
    _write_node(node_b, "topic_beta", "loves long hikes in the mountains on weekends")
    original_b_text = node_b.read_text(encoding="utf-8")
    result = MemoryGardener(memory_dir).run()
    proposal_id = result.pending_review[0].id

    fake_paths = HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )

    import hachi.gateway.in_process_runtime as runtime_mod
    from hachi.cli.main import _InProcessGatewayRuntime

    # T-41: _InProcessGatewayRuntime's methods resolve get_hachi_paths in
    # hachi.gateway.in_process_runtime's namespace now, not cli.main's.
    original_get_paths = runtime_mod.get_hachi_paths
    runtime_mod.get_hachi_paths = lambda: fake_paths
    try:
        runtime = _InProcessGatewayRuntime()
        cp = control_plane(decide_memory_gardener_diff=runtime.decide_memory_gardener_diff)
        app = create_gateway_app(cp)

        with TestClient(app) as client:
            response = client.post(f"/memory/diffs/{proposal_id}", json={"action": "reject"})
    finally:
        runtime_mod.get_hachi_paths = original_get_paths

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["proposal"]["auto_applied"] is False

    on_disk = json.loads((memory_dir / "pending_diffs.json").read_text(encoding="utf-8"))
    assert on_disk["pending_review"] == []
    assert on_disk["auto_applied"] == []
    assert node_b.read_text(encoding="utf-8") == original_b_text, "reject must not touch .og files"


def test_post_memory_diff_decision_unknown_id_returns_404(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    MemoryGardener(memory_dir).run()  # writes an empty pending_diffs.json

    fake_paths = HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path,
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )

    import hachi.gateway.in_process_runtime as runtime_mod
    from hachi.cli.main import _InProcessGatewayRuntime

    # T-41: _InProcessGatewayRuntime's methods resolve get_hachi_paths in
    # hachi.gateway.in_process_runtime's namespace now, not cli.main's.
    original_get_paths = runtime_mod.get_hachi_paths
    runtime_mod.get_hachi_paths = lambda: fake_paths
    try:
        runtime = _InProcessGatewayRuntime()
        cp = control_plane(decide_memory_gardener_diff=runtime.decide_memory_gardener_diff)
        app = create_gateway_app(cp)

        with TestClient(app) as client:
            response = client.post("/memory/diffs/does-not-exist", json={"action": "approve"})
    finally:
        runtime_mod.get_hachi_paths = original_get_paths

    assert response.status_code == 404


def test_post_memory_diff_decision_returns_503_when_not_attached() -> None:
    cp = GatewayControlPlane()  # decide_memory_gardener_diff not wired
    app = create_gateway_app(cp)

    with TestClient(app) as client:
        response = client.post("/memory/diffs/anything", json={"action": "approve"})
    assert response.status_code == 503


def test_gardening_result_load_failure_logs_at_warning_not_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Acceptance criteria: GardeningResult.load's swallow must be loud (WARNING)
    now that it backs a live route, not silent (DEBUG, the pre-T-08 level)."""
    import logging

    bad_file = tmp_path / "pending_diffs.json"
    bad_file.write_text("{ not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="hachi.core.memory.gardener"):
        result = GardeningResult.load(bad_file)

    assert result.pending_review == []
    assert any(
        "GardeningResult.load failed" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    ), "corrupt pending_diffs.json must log at WARNING, not be swallowed at DEBUG"
