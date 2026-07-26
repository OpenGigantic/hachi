"""T-42 -- retire the ``core -> cli`` seam behind a ``SessionRuntime`` protocol.

Characterization tests for a pure refactor (CLAUDE.md Rule 2 applied to a
refactor; see docs/audit/2026-07-19/tasks/T-42-retire-core-cli-seam.md §7).
``core.runtime.resume`` used to import ``hachi.cli.main`` to reach a
process-wide ``_ACTIVE_RUNTIME`` global and two of its private attributes --
the only ``core -> cli`` import in the repo, previously kept legal by a
standing import-linter exemption (deleted by this same change). These tests
assert:

1. The real import graph of the shipped package no longer names the CLI from
   anywhere under the engine packages (a source-text check -- the same
   property ``import-linter``'s "the engine must not import the UI/entry
   edges" contract enforces, expressed so a developer sees it in ``pytest``
   output rather than only in the gate).
2. Real resume behaviour (hot path / cold path) still works through the new
   ``hachi.core.runtime.protocols.SessionRuntime`` injection point -- not a
   mock's return value.
"""

from __future__ import annotations

import re
from pathlib import Path

ENGINE_PACKAGES = ("core", "og_engine", "tools", "jobs", "integrations")

# Word-boundary aware: a plain substring check for "hachi.cli" would also
# match inside "hachi.client" (the gateway-facing SDK package under
# src/hachi/client/) since "hachi.cli" is a literal character-for-character
# prefix of "hachi.client". `\b` immediately after "cli" requires the next
# character to be non-identifier, so this matches "hachi.cli" / "hachi.cli.main"
# but not "hachi.client".
_CLI_IMPORT_RE = re.compile(r"(?:from|import)\s+hachi\.cli\b")


def test_core_does_not_import_the_cli() -> None:
    """The engine must not name hachi.cli. FAILS before T-42 (resume.py:126)
    with ``AssertionError: core still names the CLI: ['src/hachi/core/runtime/resume.py']``.

    Source-text assertion, not an import-graph one: it is the same check the
    import-linter contract makes, expressed so a developer sees it in `pytest`
    output rather than only in the gate.
    """
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src" / "hachi"
    offenders = [
        p.relative_to(repo_root).as_posix()
        for pkg in ENGINE_PACKAGES
        for p in (src_root / pkg).rglob("*.py")
        if _CLI_IMPORT_RE.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"core still names the CLI: {offenders}"


def test_resume_uses_the_injected_runtime() -> None:
    """Cold-start resume must resolve the runtime through the protocol seam.

    Characterization: asserts the SAME observable outcome as today (hot path
    returns the live session's reply directly; cold path rehydrates exactly
    once), through the new injection point.
    """
    from hachi.core.runtime.protocols import set_active_session_runtime

    class _LiveSession:
        def chat(self, text: str) -> str:
            return f"replied:{text}"

    class _FakeRuntime:
        def __init__(self) -> None:
            self.rehydrate_calls: list[str] = []

        def get_live_session(self, session_id: str) -> _LiveSession | None:
            return _LiveSession() if session_id == "live-1" else None

        def rehydrate(self, session_id: str) -> _LiveSession:
            self.rehydrate_calls.append(session_id)
            return _LiveSession()

    fake = _FakeRuntime()
    set_active_session_runtime(fake)
    try:
        from hachi.core.runtime.resume import _do_resume

        out = _do_resume("live-1", {"wait_result": {"ok": True}}, 0)
        assert out.startswith("replied:[RESUME]")
        assert fake.rehydrate_calls == []  # hot path: no rehydration

        out_cold = _do_resume("cold-1", {"wait_result": {"ok": True}}, 0)
        assert out_cold.startswith("replied:[RESUME]")
        assert fake.rehydrate_calls == ["cold-1"]  # cold path: rehydrated once
    finally:
        set_active_session_runtime(None)
