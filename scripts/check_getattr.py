#!/usr/bin/env python
"""getattr-lint -- a defaulted getattr against one of OUR types is a bug, not a default.

CF-1: `getattr(x, "field", None)` converts "this attribute was never implemented" into a
silently-taken fallback. It masked at least four dead features in the 2026-07-19 audit
(ProcessSharedServices.session_store, MemoryManager.memory_dir, ResilientClient.max_tokens,
AgentSession.reindex_skills -- none of which existed).

Scope: three-argument `getattr()` with a LITERAL string attribute name, where the object
expression is one of:

  1. `self` or `self.<attr>` -- a collaborator this class was constructed with. Its type
     is known to the author, lives in this repo, and is changeable.
  2. A bare local name that, earlier in the same function, was bound to (1) via AT
     LEAST ONE pure container/iterator hop -- `next()`, `iter()`, `list()`, `tuple()`,
     `.values()`/`.items()`/`.keys()`, or subscripting. None of those can turn a
     self-owned collection into a foreign type, so an element pulled out of
     `self._agents.values()` is still a Hachi-owned collaborator, one hop removed.
     This is required to catch `cli/main.py`'s
     `agent = next(iter(self._agents.values()), None)` /
     `getattr(agent, "reindex_skills", None)` shape (CORRECTIONS.md N7) -- the
     motivating case for this checker, and the reason form (2) exists at all.

     A *zero*-hop bare rename (`x = self.foo`, nothing peeled off) is deliberately
     EXCLUDED from form (2): `self.<attr>` is frequently a foreign/third-party handle
     -- e.g. `self._chroma_client` (a chromadb client) or `self._server` (a
     `uvicorn.Server`) -- and a bare rename gives no extra evidence the aliased value
     is one of OUR types. Requiring >=1 real container hop is what keeps form (2)
     from flagging `getattr(client, "_server", None)` after `client =
     self._chroma_client`, which an earlier draft of this checker did (verified by
     running it: it fired on `core/memory_vault.py`'s undocumented Chroma
     `_server.stop()` probe and `gateway/control_plane.py`'s undocumented
     `uvicorn.Server.servers` probe -- both legitimate SDK duck-typing, exactly what
     form (2) must not catch).

Explicitly OUT of scope: a local bound via an arbitrary method call (e.g.
`response = self.client.messages.create(...)`), because that call can return any type,
foreign or not, and we have no static way to know. `getattr(response, "usage", None)`
against an `anthropic` response stays legitimate. Foreign/duck-typed objects reached
directly via locals (not derived from `self` at all) are likewise out of scope. See
ALLOWLIST notes and the module docstring in scripts/check_adrs.py for the
advisory-then-ratchet convention this rail follows.

Not handled (deliberately, see T-14 spec Sec 9): `hasattr()` guards on our own types are
the same bug in different syntax (e.g. `fastapi_app.py:1018`) but are left for a future
widening, noted here rather than silently swept in.

Advisory in the gate today. Ratchet to blocking once the allowlist stops growing.
Exits non-zero on any un-allowlisted occurrence. Stdlib only -- never imports the product.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "hachi"

# Pure "give me what's inside" operations: peeling these off a self-owned collection
# cannot change the element type away from what the collection declares, so we can see
# through them when tracing a local variable back to `self`/`self.<attr>`.
_UNWRAP_CALLS = frozenset({"next", "iter", "list", "tuple"})
_UNWRAP_ATTRS = frozenset({"values", "items", "keys"})

# Keyed on "<relative/path.py>:<attr>" -- NOT on line number, which drifts on every
# unrelated edit and would rot the allowlist. Every entry below was individually
# verified against the declaring class's real source (not guessed) -- see the
# T-14/BB-107 bug-bank entry for the method.
#
# Three kinds of entry live here, and the comment on each says which:
#   (c) genuinely optional -- a Protocol-typed collaborator with more than one
#       concrete implementation, a constructor-stash (CF-6), or a value that can
#       itself be None. Fine forever.
#   (a-unowned) the attribute VERIFIED to always exist -- this is really category
#       (a) and belongs as a direct read, but the file is gateway/orchestrator.py,
#       owned by a concurrent sibling agent this pass (not editable here). Flagged
#       in the T-14 report as a real, unfixed violation; remove this entry once
#       someone (in that file's own change) converts it to a direct read.
#   (b) a real, already-known dead feature this task is explicitly told not to fix
#       (T-14 spec Sec 5.5/Sec 9) -- allowlisted so this rail's own baseline is
#       green, NOT because the underlying bug is okay. Each cites the task or
#       bug-bank entry that owns the real fix and must be removed when it lands.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # === src/hachi/core/loop.py =================================================
        # (c) ExecutionLoop.memory is UNTYPED in __init__ (`memory,` -- no annotation)
        # and is documented/used polymorphically: MemoryManager for normal sessions,
        # `SubAgentMemory` for sub-agents (core/sub_agent.py -- see its own docstring:
        # "Legacy attributes kept for backward compatibility: get_context_header(),
        # ..., _last_user_query"), or any minimal ConversationMemory Protocol
        # implementation in tests. get_context_header/repair_tool_pairs/
        # estimate_token_count/clear_stale_tool_results are explicitly commented at
        # the call site as "not in the ConversationMemory Protocol" / "SubAgentMemory
        # ... skips all compaction housekeeping" -- genuinely absent on some
        # implementations, not merely defensive style.
        "src/hachi/core/loop.py:get_context_header",
        "src/hachi/core/loop.py:repair_tool_pairs",
        "src/hachi/core/loop.py:estimate_token_count",
        "src/hachi/core/loop.py:clear_stale_tool_results",
        "src/hachi/core/loop.py:_last_user_query",
        # (c) _last_tool_calls / _last_suspend are only ASSIGNED inside run(), never
        # in __init__ ("Returns an empty list before the first run" / "Reset at the
        # start of every run()" are the properties' own docstrings) -- genuinely
        # absent on a freshly constructed loop. Verified the hard way: an earlier
        # version of this fix converted these to direct reads and broke
        # tests/test_e2e_loop.py::test_last_tool_calls_empty_before_run with a real
        # AttributeError. Do not convert these two.
        "src/hachi/core/loop.py:_last_tool_calls",
        "src/hachi/core/loop.py:_last_suspend",
        # (c) .model on a BaseLLMClient-typed value: AnthropicClient/
        # OpenAICompatibleClient/ChatGPTWebClient all set self.model, but
        # ResilientClient (core/resilience.py, also a BaseLLMClient) does not -- it
        # wraps an inner client and has no __getattr__ delegation, so a
        # ResilientClient-typed self.llm_client/_turn_client genuinely lacks .model.
        "src/hachi/core/loop.py:model",
        # (b) T-27 (forward max_tokens): ResilientClient.max_tokens is not exposed,
        # so the pre-flight token safety ceiling silently computes 0 under routing.
        # Owned by T-27 -- remove this entry once it lands.
        "src/hachi/core/loop.py:max_tokens",
        # === src/hachi/core/resilience.py =============================================
        # (c) T-25/BB-117: same BaseLLMClient/.model reasoning as core/loop.py and
        # core/sub_agent.py above. ResilientClient.__init__ (a few lines above the
        # flagged site) already treats `inner.model` as optional --
        # `self.model: str = getattr(inner, "model", "unknown")` -- because
        # BaseLLMClient itself declares no `model` attribute; only concrete
        # subclasses (AnthropicClient/OpenAICompatibleClient/ChatGPTWebClient) set
        # it by convention. The generate_stream() served_model backfill reads
        # `self._inner.model` under the identical assumption to stamp who served a
        # successful stream (the path _call_with_chain's own backfill does not
        # cover); it must not be stricter than the constructor sitting right next
        # to it -- crashing mid-stream for something __init__ already tolerates
        # would be a robustness regression, not a fix.
        "src/hachi/core/resilience.py:model",
        # === src/hachi/core/memory/vault.py ===========================================
        # (c) MemoryVault is constructed via `MemoryVault.__new__(MemoryVault)` in
        # several tests to skip real ChromaDB I/O (tests/test_episodic_memory.py,
        # test_memory_decay.py, test_memory_fixes.py, test_perf_regression.py --
        # the last one's own comment: "MemoryVault.__new__ + pre-populated
        # _collections dict"), which genuinely never sets these unless the test
        # does so by hand. The decay read site's own comment says the same thing
        # explicitly: "vaults constructed without this attribute ... degrade
        # gracefully". Verified this is a *real*, exercised gap, not just a
        # hypothetical one -- do not convert these to direct reads.
        "src/hachi/core/memory/vault.py:_collections",
        "src/hachi/core/memory/vault.py:_decay_half_life_days",
        # _graph_index defaults to None (only set later via a setter) and the read
        # site is deliberately probing a *different* class's private field ("so we
        # do not need to add a new public API to MemoryIndex") -- getattr is doing
        # double duty (None-container guard + private-field probe); left as is.
        "src/hachi/core/memory/vault.py:_db_path",
        # === src/hachi/core/oauth.py =================================================
        # (c) self.server is a stdlib http.server.HTTPServer (foreign type); Hachi
        # attaches `oauth_state` onto it dynamically AFTER construction
        # (core/oauth.py: `server.oauth_state = state`, well after `HTTPServer(...)`)
        # -- a handler invoked without that patch having run (e.g. a bare test)
        # genuinely lacks it. Same constructor-stash shape as CF-6.
        "src/hachi/core/oauth.py:oauth_state",
        # === src/hachi/core/sub_agent.py =============================================
        # (c) same BaseLLMClient/.model reasoning as core/loop.py above.
        "src/hachi/core/sub_agent.py:model",
        # === src/hachi/core/session_store.py ==========================================
        # (c) SessionStore is constructed via `SessionStore.__new__(SessionStore)` in
        # tests/test_session_clean.py, which sets only _conn/_lock and then calls
        # shutdown() -- shutdown() reads _flush_timer directly, and (via
        # flush_sync() -> _flush_locked()) _uncommitted too. Verified: converting
        # either to a direct read breaks that test with a real AttributeError, so
        # this is a live, exercised gap, not a hypothetical one.
        "src/hachi/core/session_store.py:_uncommitted",
        "src/hachi/core/session_store.py:_flush_timer",
        # === src/hachi/jobs/queue.py ==================================================
        # (c) JobQueue is an ABC; update_attempt is defined only on SQLiteJobQueue,
        # not on the base class or InMemoryJobQueue -- genuinely absent on the
        # in-memory implementation used by some jobs/tests.
        "src/hachi/jobs/queue.py:update_attempt",
        # === src/hachi/core/memory/manager.py ==========================================
        # (c) _pending_compaction / _pending_old_skill are compaction-deferral state:
        # __init__ does not set either (verified: no assignment anywhere in
        # __init__); both are only ever written inside _sliding_window_compact's
        # defer-compaction branch, so a MemoryManager that has never deferred a
        # compaction genuinely lacks them by design.
        "src/hachi/core/memory/manager.py:_pending_compaction",
        "src/hachi/core/memory/manager.py:_pending_old_skill",
        # (b) NEW finding (not in the audit's original four): MemoryManager._session_id
        # is read at memory.py's sync_turn() but is never assigned ANYWHERE in the
        # repo (verified: no `self._session_id =` in memory.py, and no external
        # `.memory._session_id = ...` writer either) -- the fact-extraction job's
        # `session_id` payload field is therefore always "", which the handler
        # threads into extract_facts()'s typed-node journal routing. Filing as a
        # finding (bug-bank BB-107), not fixing: the right shape (constructor param?
        # a set_session_id() setter wired by AgentSession?) is a design choice, the
        # same reason T-14's spec leaves reindex_skills alone.
        "src/hachi/core/memory/manager.py:_session_id",
        # === src/hachi/gateway/in_process_runtime.py ==================================
        # (c) lazy-cache idiom: _og_engine_cache is populated on first use inside
        # _standalone_og_engine() (gateway/in_process_runtime.py) and deliberately
        # never pre-set in __init__ -- the getattr default IS the "not cached yet"
        # sentinel.
        "src/hachi/gateway/in_process_runtime.py:_og_engine_cache",
        # (b) T-14 itself (this task) / CORRECTIONS.md N7: AgentSession.reindex_skills
        # does not exist anywhere in the repo, so authoring saves never re-index and
        # `reindexed_ms` is always ~0. T-14's own spec (Sec 5.5, Sec 9) explicitly
        # forbids fixing this here -- deciding what re-indexing should do is a design
        # question T-04 Sec 9 leaves open. Filed as a finding (bug-bank BB-107);
        # remove this entry once someone implements AgentSession.reindex_skills.
        "src/hachi/gateway/in_process_runtime.py:reindex_skills",
        # === src/hachi/gateway/orchestrator.py (OFF LIMITS this pass -- owned by a
        # concurrent sibling agent; see T-14 report for the full un-owned-file list) ==
        # (c) CF-6: boot_agent stashes these onto the session AFTER construction, so
        # the attribute genuinely may not exist yet when read.
        "src/hachi/gateway/orchestrator.py:_sub_agent_client",
        "src/hachi/gateway/orchestrator.py:_schedule_runner",
        "src/hachi/gateway/orchestrator.py:_browser_controller",
        # (a-unowned) ExecutionLoop.cost_tracker/.router/.llm_client are all set
        # unconditionally in ExecutionLoop.__init__ (core/loop.py) -- VERIFIED to
        # always exist (possibly None-valued). .last_tool_calls/.last_suspend are
        # ExecutionLoop @property members, also always present. Every one of these
        # is a genuine, unfixed category-(a) violation; not converted here only
        # because gateway/orchestrator.py is off limits this pass.
        "src/hachi/gateway/orchestrator.py:cost_tracker",
        "src/hachi/gateway/orchestrator.py:router",
        "src/hachi/gateway/orchestrator.py:llm_client",
        "src/hachi/gateway/orchestrator.py:last_tool_calls",
        "src/hachi/gateway/orchestrator.py:last_suspend",
        # (b) T-08 (memory-gardener): MemoryManager.memory_dir is declared nowhere;
        # the gardener reader returns [] before touching the file. Owned by T-08 --
        # remove this entry once it lands and the attribute genuinely exists.
        "src/hachi/gateway/orchestrator.py:memory_dir",
        # (b) NEW finding: `_current_started_at` is read off self._session_store but
        # is never assigned anywhere in the repo -- verified zero matches for
        # `_current_started_at` outside this one read site, including in
        # core/session_store.py's SessionStore itself. Filing as a finding
        # (bug-bank BB-107), not fixing: unowned file this pass regardless.
        "src/hachi/gateway/orchestrator.py:_current_started_at",
        # === src/hachi/gateway/session_read_models.py (T-46 facade extraction) =====
        # (a) Two genuine, unfixed category-(a) violations, relocated verbatim by
        # T-46 -- not the same as being resolved. cost_tracker matches the
        # orchestrator.py:cost_tracker entry above (ExecutionLoop.cost_tracker
        # "VERIFIED to always exist"; still a live entry there via 3 unrelated call
        # sites). T-46 moved AgentSession.snapshot_session_state's and
        # .snapshot_usage's bodies here VERBATIM (now SessionReadModels.session_state
        # / .usage); its own spec (Sec 9) explicitly forbids "fixing" a
        # getattr/except guard while relocating it -- a relocation, not a getattr
        # burndown. Remove alongside orchestrator.py:cost_tracker once someone
        # converts these call sites to direct reads.
        "src/hachi/gateway/session_read_models.py:cost_tracker",
        "src/hachi/gateway/session_read_models.py:_last_user_query",
    }
)


def _is_self(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Name) and expr.id == "self"


def _self_attr_name(expr: ast.expr) -> str | None:
    """Return the attribute name if `expr` is exactly `self.<attr>`, else None."""
    if (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "self"
    ):
        return expr.attr
    return None


def _is_self_collaborator(expr: ast.expr) -> bool:
    """True if `expr` is a self-owned collaborator reached via AT LEAST ONE pure
    container/iterator hop off `self`/`self.<attr>`. A zero-hop bare rename
    (`x = self.foo`) is deliberately excluded -- see the module docstring's "form (2)"
    section for why (it is how a chromadb/uvicorn false positive crept in during
    development)."""
    hops = 0
    while isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name) and func.id in _UNWRAP_CALLS and expr.args:
            expr = expr.args[0]
            hops += 1
            continue
        if isinstance(func, ast.Attribute) and func.attr in _UNWRAP_ATTRS:
            expr = func.value
            hops += 1
            continue
        return False
    while isinstance(expr, ast.Subscript):
        expr = expr.value
        hops += 1
    if hops == 0:
        return False
    return _is_self(expr) or _self_attr_name(expr) is not None


def _own_scope_nodes(func: ast.AST) -> Iterator[ast.AST]:
    """Like ast.walk, but does not descend into a nested function/lambda -- those get
    their own local-variable scope and (for FunctionDef/AsyncFunctionDef) are walked
    separately by the caller, so nothing is double-visited or cross-contaminated."""
    stack = [func]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                and child is not func
            ):
                continue
            stack.append(child)


def _self_derived_locals(func: ast.AST) -> set[str]:
    """Local names in `func`'s own scope bound directly to a self-owned collaborator:
    a plain alias (`agent = self.foo`) or one pulled out of a self-owned container via
    a pass-through op, e.g. `agent = next(iter(self._agents.values()), None)`."""
    names: set[str] = set()
    for node in _own_scope_nodes(func):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_self_collaborator(node.value)
        ):
            names.add(node.targets[0].id)
        elif (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and _is_self_collaborator(node.iter)
        ):
            names.add(node.target.id)
    return names


def _getattr_hit(node: ast.AST) -> tuple[ast.expr, str, int] | None:
    """If `node` is a 3-arg getattr() call with a literal string attribute name,
    return (object-expr, attr-name, lineno); else None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    if node.func.id != "getattr" or len(node.args) != 3:
        return None
    target = node.args[1]
    if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
        return None  # dynamic attribute name -- out of scope
    return node.args[0], target.value, node.lineno


def _violations(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    # Pass 1: `getattr(self, ...)` / `getattr(self.<attr>, ...)` directly -- scope-
    # independent, matches anywhere in the tree.
    for node in ast.walk(tree):
        hit = _getattr_hit(node)
        if hit is None:
            continue
        obj, attr, lineno = hit
        if _is_self(obj) or _self_attr_name(obj) is not None:
            out.append((lineno, attr))

    # Pass 2: `getattr(local, ...)` where `local` was bound, earlier in the same
    # function, to a self-owned collaborator (see _is_self_collaborator). Disjoint
    # from pass 1 by construction: pass 1 only matches ast.Attribute/bare-"self"
    # objects, pass 2 only matches ast.Name objects whose id isn't "self".
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        self_locals = _self_derived_locals(func)
        if not self_locals:
            continue
        for node in _own_scope_nodes(func):
            hit = _getattr_hit(node)
            if hit is None:
                continue
            obj, attr, lineno = hit
            if isinstance(obj, ast.Name) and obj.id in self_locals:
                out.append((lineno, attr))

    return out


def main() -> int:
    if not SRC.is_dir():
        print(f"check_getattr: no source directory at {SRC} -- skipping.")
        return 0

    total = 0
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        # utf-8-sig: transparently strips a leading BOM (e.g. src/hachi/client/models.py)
        # instead of feeding ast.parse a stray U+FEFF and crashing the whole rail.
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        for lineno, attr in _violations(tree):
            total += 1
            key = f"{rel}:{attr}"
            if key in ALLOWLIST:
                continue
            problems.append(
                f'{rel}:{lineno}: getattr(..., "{attr}", <default>) reaches into one of '
                f"our own types -- a missing attribute is a bug, read it directly and let "
                f'AttributeError be loud. If it is genuinely optional, add "{key}" to '
                f"ALLOWLIST in scripts/check_getattr.py with a one-line reason."
            )

    if problems:
        print(
            f"check_getattr: FAIL -- {len(problems)} un-allowlisted defaulted getattr(s) "
            f"against our own types ({total} total hit(s), {len(ALLOWLIST)} allowlisted):"
        )
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"check_getattr: OK -- {total} defaulted getattr(s) against our own types, "
        f"all allowlisted with a reason ({len(ALLOWLIST)} allowlist entries)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
