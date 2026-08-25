"""BB-104: a routing-layer boot failure was invisible at default log level.

``gateway/orchestrator.py``'s routing-boot ``try/except`` (behind ``[routing]``) caught
*every* exception raised while building ``ProviderRegistry -> ModelRegistry ->
RoutingPolicy -> ModelRouter`` at ``logger.debug``, while the SUCCESS path one branch up
logs at ``logger.info`` — a working boot was strictly louder than a broken one. The
``try`` also wrapped six ``import`` statements, so an ``ImportError``/``AttributeError``/
typo in that block was indistinguishable from a genuine ``[routing]`` config problem.

See docs/audit/2026-07-19/tasks/T-26-routing-boot-failure-visible.md.

T-44 relocation note (read before touching this file)
-------------------------------------------------------
T-26 section 9 forbade extracting a ``_build_routing()`` helper, in these words:
"Extracting a helper is exactly the drift this section exists to prevent: it is a
different task, with a different review burden, and it will collide with CF-6
work." CF-6 is T-44 (decompose ``boot_agent`` into staged builders) — the deferral
named its own successor. T-44 landed that exact extraction:
``hachi.gateway.boot.providers.build_routing`` now holds the try/except this file
locks down, called from ``boot_agent`` rather than inlined in it. The routing-boot
block moved VERBATIM (T-44's own constraint) — same guarded span, same imports
hoisted above the ``try``, same log levels, same event. Only the enclosing function
changed, from ``boot_agent`` to ``build_routing``, so every assertion below now
anchors on THAT function instead. This is the BB-158 pattern ("point the test at
the new location, not backward through the old module") applied to a structural
(AST) test rather than a monkeypatch target — nothing here asserts anything
different than it did before the move.

Harness note (read before touching this file)
-----------------------------------------------
T-26 section 7 asks for a ``caplog``-based test that boots a session with a broken
``[routing]`` section. That requires calling the real ``boot_agent()``, which runs
device-permission loading, real ``.og`` skill-engine indexing via the process-wide
``ProcessSharedServices`` singleton, MCP-server startup, and LLM-client construction
*before* it ever reaches the routing-boot block — and would need every one of those
redirected off the developer's real ``~/.hachi`` to be safe to run in CI. No test in
this repo calls the real ``boot_agent()`` today (the only two hits for ``"boot_agent("``
in ``tests/`` — ``test_cold_start_resume.py`` and ``test_gateway_runtime_validation.py``
— both replace it with a fake via ``monkeypatch``), and (pre-T-44) extracting a helper
to make the block independently callable was explicitly out of scope.

So this file takes the spec's own pre-authorised fallback ("if a full boot is
impractical, assert the two properties that do not require it"): an honest,
*structural* (AST-based, not raw-grep) assertion against the REAL source of
``build_routing``, in the same spirit as
``tests/test_emit_runtime_event_single_definition.py``'s
``test_boot_agent_defines_each_event_closure_once``. Every assertion below reads the
live ``except`` handler via ``ast`` (not a hand-copied mirror), so it cannot drift
silently from what the file actually does, and it fails today for the exact reason
the bug exists.
"""

from __future__ import annotations

import ast
import inspect

import hachi.gateway.boot.providers as boot_providers

_ROUTING_IMPORT_MODULES: frozenset[str] = frozenset(
    {
        "hachi.core.providers.registry",
        "hachi.core.resilience",
        "hachi.core.routing.policy",
        "hachi.core.routing.registry",
        "hachi.core.routing.router",
        "hachi.core.settings",
    }
)


def _boot_agent_node() -> ast.FunctionDef:
    """Despite the name (kept so the diff against every call site below is minimal),
    this now returns ``build_routing`` in ``hachi.gateway.boot.providers`` — the
    function the routing-boot try/except lives in after T-44. See the module
    docstring's "T-44 relocation note"."""
    tree = ast.parse(inspect.getsource(boot_providers))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_routing":
            return node
    raise AssertionError("build_routing not found in hachi.gateway.boot.providers")


def _routing_boot_try() -> ast.Try:
    """The one ``try`` in ``build_routing`` whose body constructs a ``ModelRouter(...)``.

    Anchored on the ``ModelRouter(`` call rather than a line number, per the spec's
    own warning that anchors drift — this stays correct even if unrelated code moves.
    """
    func = _boot_agent_node()
    matches = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Try)
        and any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ModelRouter"
            for n in ast.walk(node)
        )
    ]
    assert len(matches) == 1, (
        f"expected exactly one routing-boot try/except in boot_agent, found {len(matches)} — "
        "the anchor (a ModelRouter(...) call inside a try) is no longer unique"
    )
    return matches[0]


def _logger_call_levels(nodes: list[ast.AST]) -> set[str]:
    """Every ``logger.<level>(...)`` call level name found within *nodes*."""
    levels: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "logger"
            ):
                levels.add(n.func.attr)
    return levels


def _import_from_modules(nodes: list[ast.AST]) -> set[str]:
    mods: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module)
    return mods


# ---------------------------------------------------------------------------
# The asymmetry itself: success logs at INFO, failure must not log quieter
# than WARNING (before the fix it logged at DEBUG — strictly quieter than the
# adjacent success path's INFO).
# ---------------------------------------------------------------------------


def test_routing_boot_failure_logs_at_warning() -> None:
    """A routing-layer boot failure must be visible at default log level.

    Before the fix, the except arm called ``logger.debug(...)`` while the success
    path one branch up calls ``logger.info(...)`` — a working boot was louder than a
    broken one, and a user whose ``[routing]`` config failed to build got a silently
    different (more expensive) product with no signal at default log level.
    """
    try_node = _routing_boot_try()
    assert len(try_node.handlers) == 1, "expected exactly one except handler"
    handler = try_node.handlers[0]

    levels = _logger_call_levels([handler])
    assert "debug" not in levels, (
        "routing-boot failure must not log at DEBUG — the adjacent success path logs "
        "at INFO, so a broken boot logging quieter than that hides the failure entirely "
        f"(found levels={levels})"
    )
    assert levels & {"warning", "error", "critical"}, (
        f"routing-boot failure must log at WARNING or louder; found levels={levels}"
    )


def test_routing_boot_failure_names_the_exception_type() -> None:
    """The failure message must include the exception TYPE name.

    That is what distinguishes "your [routing] config names a missing provider" from
    "ModelRegistry moved and the import is broken" — the pre-fix message could not
    express either, since it never touched the exception at all.
    """
    handler = _routing_boot_try().handlers[0]
    calls_type = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "type"
        for n in ast.walk(handler)
    )
    assert calls_type, (
        "the except handler must reference type(exc).__name__ (or equivalent) so the "
        "WARNING message names the exception type"
    )


def test_routing_boot_failure_emits_a_runtime_event() -> None:
    """A ``routing.boot_failed`` runtime event must accompany the log line.

    Mirrors the existing ``cost.routing_warning`` pattern a few lines up in the same
    try block (``logger.warning`` + ``emit_runtime_event`` together) so the frontend
    and any other subscriber can react, not just a human reading logs.
    """
    handler = _routing_boot_try().handlers[0]
    calls_emit = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "emit_runtime_event"
        for n in ast.walk(handler)
    )
    assert calls_emit, "routing-boot failure must call emit_runtime_event(...)"


def test_routing_boot_imports_are_outside_the_try() -> None:
    """An ImportError from a broken install must not masquerade as a config problem.

    Before the fix, six ``import`` statements sat inside the guarded ``try`` alongside
    the actual construction calls, so a partial install, a renamed module, or a typo
    in any of them presented identically to "your [routing] config is bad".
    """
    func = _boot_agent_node()
    try_node = _routing_boot_try()

    all_imports = _import_from_modules([func])
    for module in _ROUTING_IMPORT_MODULES:
        assert module in all_imports, (
            f"expected boot_agent to still import from {module!r} somewhere — "
            "it must not have been deleted, only relocated"
        )

    # try_node.body is the try's own body only (excludes handlers/orelse/finalbody).
    try_imports = _import_from_modules(list(try_node.body))
    overlap = try_imports & _ROUTING_IMPORT_MODULES
    assert not overlap, (
        f"these routing imports are still inside the guarded try: {sorted(overlap)} — "
        "an ImportError from a broken install/rename would be swallowed as a routing "
        "config problem instead of raising loudly at boot"
    )


def test_success_path_still_logs_at_info() -> None:
    """Guard the fixture itself: if the success path's level ever changes, the whole
    "success is louder than failure" comparison this bug is about goes stale silently.
    """
    try_node = _routing_boot_try()
    levels = _logger_call_levels(list(try_node.body))
    assert "info" in levels, (
        "the routing-boot success path must still log at INFO — this test file's "
        "premise (success was louder than failure) depends on it"
    )
