"""Regression tests for scripts/check_getattr.py (T-14, BB-107).

CF-1: `getattr(x, "field", None)` against a Hachi-owned type converts "this attribute
was never implemented" into a silently-taken fallback. It masked at least four dead
features in the 2026-07-19 audit, including `AgentSession.reindex_skills`
(`cli/main.py:1313`, `:1389`) -- which does not exist anywhere in the repo.

These tests assert the checker's real output over real source text, including that it
does NOT fire on legitimate forms (two-arg getattr, a dynamic attribute name, a
defaulted getattr on a foreign object reached via a local). A detector with no
false-negative *and* no false-positive test is a detector nobody will trust.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_getattr import _violations, main  # noqa: E402


def test_detects_defaulted_getattr_on_self_attribute() -> None:
    src = 'class A:\n    def f(self):\n        return getattr(self.memory, "memory_dir", None)\n'
    assert _violations(ast.parse(src)) == [(3, "memory_dir")]


def test_ignores_two_arg_getattr_and_dynamic_names() -> None:
    cases = [
        'getattr(self.x, "attr")',  # 2-arg: raises on absence -- fine
        "getattr(self.x, name, None)",  # dynamic name -- out of scope
        'getattr(response, "usage", None)',  # foreign object via a local -- out of scope
    ]
    for src in cases:
        assert _violations(ast.parse(src)) == [], src


def test_detects_defaulted_getattr_on_local_alias_of_self_collaborator() -> None:
    """The `AgentSession.reindex_skills` shape: the object handed to `getattr` is a
    bare local, not `self.<attr>` directly -- but it was pulled straight out of a
    dict that IS a self-owned collaborator, via pure container access (`next`,
    `iter`, `.values()`) that cannot turn it into a foreign type. This is the
    call-site shape at `cli/main.py:1313`/`:1389` (`agent = next(iter(self._agents
    .values()), None)` then `getattr(agent, "reindex_skills", None)`), and the
    checker must catch it or it is not doing its job (see CORRECTIONS.md N7).
    """
    src = (
        "class Registry:\n"
        "    def apply(self):\n"
        "        with self._agents_lock:\n"
        "            agent = next(iter(self._agents.values()), None)\n"
        "        if agent is not None:\n"
        '            reindex = getattr(agent, "reindex_skills", None)\n'
        "            if callable(reindex):\n"
        "                reindex()\n"
    )
    assert _violations(ast.parse(src)) == [(6, "reindex_skills")]


def test_local_alias_detection_does_not_flag_unrelated_locals() -> None:
    """A local that is NOT derived from `self` (e.g. a foreign SDK response reached
    via an arbitrary method call, not a container read) must stay out of scope --
    otherwise the local-alias widening would swallow the same foreign-object cases
    §5.2 explicitly protects."""
    src = (
        "class Client:\n"
        "    def call(self):\n"
        "        response = self.client.messages.create()\n"
        '        return getattr(response, "usage", None)\n'
    )
    assert _violations(ast.parse(src)) == []


def test_src_tree_has_no_unallowlisted_violations() -> None:
    """The rail's real contract: the tree is clean, or every exception is documented."""
    assert main() == 0, "run `uv run python scripts/check_getattr.py` to see the offenders"
