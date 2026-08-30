"""Regression tests for T-68 — burn down the five gateway import-linter exemptions.

Each of these five engine-side call sites used to reach directly into
``hachi.gateway`` — module-level for ``core.onboarding`` (the fattest one),
function-local/lazy for the other four (``core.cost``, ``core.runtime.resume``,
``core.runtime.job_handlers``, ``tools.config_tools``). All five were frozen as
declared debt in ``pyproject.toml``'s ``[tool.importlinter]`` gateway contract.

A lazy/function-local import still couples the runtime to the gateway package
even though import-linter's grimp-based graph builder sees it just as well as
a module-level one (BB-144: a deferred import does NOT escape import-linter).
So the regression these tests guard against is "a fix that swaps a direct
import for an indirect or lazy one and keeps the runtime coupling" — not
merely "the linter is red".

Each of the five primary assertions runs the engine-side call in a FRESH
subprocess and asserts ``hachi.gateway`` is absent from ``sys.modules``
afterwards. This is load-bearing: by the time most of this suite runs,
``hachi.gateway.*`` is already imported into the test process by earlier
tests, so the same assertion in-process would pass for the wrong reason (see
the T-68 task spec, docs/audit/2026-07-19/tasks/T-68-import-linter-exemption-
burndown.md §7).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a brand-new interpreter and return the completed process."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


# ---------------------------------------------------------------------------
# (a) tools.config_tools -> gateway.workspace
# ---------------------------------------------------------------------------


def test_config_tools_resolves_skills_dir_without_gateway(tmp_path: Path) -> None:
    """list_mcp_servers() must work from an injected skills_dir with no gateway import.

    Regression: tools/config_tools.py reached into hachi.gateway.workspace at
    three call sites (add_mcp_server, remove_mcp_server, list_mcp_servers),
    frozen as a single import-linter exemption covering all three.
    """
    code = f"""
import json
import sys
from pathlib import Path

import hachi.tools.config_tools as m

fn = m._make_list_mcp_servers(skills_dir_provider=lambda: Path({str(tmp_path)!r}))
result = fn()
data = json.loads(result)
assert data == {{"servers": [], "count": 0}}, data
assert "hachi.gateway" not in sys.modules, sorted(
    k for k in sys.modules if k.startswith("hachi.gateway")
)
"""
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# (b) core.cost -> gateway.shared_services
# ---------------------------------------------------------------------------


def test_cost_shared_session_store_lookup_without_gateway() -> None:
    """_get_shared_session_store() must degrade to None with no gateway import.

    Regression: core/cost.py reached into hachi.gateway.shared_services
    (function-local, inside a try/except) to find the process-wide
    SessionStore singleton.
    """
    code = """
import sys

from hachi.core.cost import _get_shared_session_store

result = _get_shared_session_store()
assert result is None, f"expected None with no process services registered, got {result!r}"
assert "hachi.gateway" not in sys.modules, sorted(
    k for k in sys.modules if k.startswith("hachi.gateway")
)
"""
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# (c) core.runtime.resume -> gateway.shared_services
#
# NOTE: the SIBLING seam in this same module, `core.runtime.resume ->
# hachi.cli.main` (inside `_do_resume`), is deliberately untouched — it
# belongs to T-42, not this task.
# ---------------------------------------------------------------------------


def test_resume_process_bus_lookup_without_gateway() -> None:
    """_get_process_bus() must degrade to None with no gateway import.

    Regression: core/runtime/resume.py reached into
    hachi.gateway.shared_services (function-local, inside a try/except) to
    find the process-wide EventBus singleton.
    """
    code = """
import sys

from hachi.core.runtime.resume import _get_process_bus

result = _get_process_bus()
assert result is None, f"expected None with no process services registered, got {result!r}"
assert "hachi.gateway" not in sys.modules, sorted(
    k for k in sys.modules if k.startswith("hachi.gateway")
)
"""
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_resume_process_bus_returns_the_shared_bus(tmp_path: Path) -> None:
    """Positive-path lock: when a shared EventBus IS available, resume.py finds it.

    Correctness lock, mirroring tests/test_shared_session_store.py's pattern
    for cost.py's sibling seam. Does NOT independently fail before the T-68
    fix (the old direct import already reached the same singleton) — it
    guards against a "fix" that breaks the positive path while chasing the
    negative one above.
    """
    from hachi.core.event_bus import EventBus
    from hachi.core.runtime.resume import _get_process_bus
    from hachi.gateway.shared_services import ProcessSharedServices

    ProcessSharedServices.clear()
    shared = ProcessSharedServices.get_or_create(
        {"memory": {"backend": "in_memory"}},  # BB-3: no ChromaDB
        str(tmp_path),
        force_reinit=True,
    )
    bus = EventBus()
    shared.event_bus = bus
    try:
        assert _get_process_bus() is bus, (
            "resume.py's _get_process_bus did not reach the shared EventBus through "
            "the core.runtime.protocols seam"
        )
    finally:
        ProcessSharedServices.clear()


# ---------------------------------------------------------------------------
# (d) core.runtime.job_handlers -> gateway.shared_services
# ---------------------------------------------------------------------------


def test_job_handlers_emit_file_extracted_without_gateway() -> None:
    """_emit_file_extracted() must run cleanly (dropping the event) with no gateway import.

    Regression: core/runtime/job_handlers.py reached into
    hachi.gateway.shared_services (function-local, inside a try/except) to
    find the process-wide EventBus singleton. T-09 already fixed the
    correctness half (it used to reach a nonexistent `_shared` global); this
    guards the layering half.
    """
    code = """
import sys

from hachi.core.runtime.job_handlers import _emit_file_extracted

_emit_file_extracted("f-1", "a.txt", "text/plain", "f-1.txt")
assert "hachi.gateway" not in sys.modules, sorted(
    k for k in sys.modules if k.startswith("hachi.gateway")
)
"""
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# (e) core.onboarding -> gateway.workspace  (the only MODULE-LEVEL import)
# ---------------------------------------------------------------------------


def test_onboarding_module_import_does_not_pull_in_gateway() -> None:
    """Merely importing hachi.core.onboarding must not drag in hachi.gateway.

    Regression: core/onboarding.py did `from hachi.gateway.workspace import
    install_optional_skill_pack` at MODULE SCOPE — the only eager import of
    the five, meaning `import hachi.core.onboarding` alone dragged in the
    whole gateway package before any function was even called.
    """
    code = """
import sys

import hachi.core.onboarding  # noqa: F401

assert "hachi.gateway" not in sys.modules, sorted(
    k for k in sys.modules if k.startswith("hachi.gateway")
)
"""
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr
