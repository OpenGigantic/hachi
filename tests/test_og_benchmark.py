"""Token-savings benchmark for the ObjectGraph 3-pass model.

This is the proof behind the differentiator: answering a query via
search -> peek -> load (loading exactly one skill) must cost dramatically fewer
tokens than the naive "dump every skill into context" baseline. Runs against the
real bundled default_skills so the measurement reflects shipped content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import hachi.og_engine.engine as og_engine_mod
from hachi.og_engine.engine import SkillEngine, estimate_tokens

SAVINGS_THRESHOLD = 0.80


def _bundled_skills_dir() -> Path:
    # Derive the package root from a module with a real __file__ (the top-level
    # `hachi` package is a namespace package, so its __file__ is None).
    pkg_root = Path(og_engine_mod.__file__).resolve().parent.parent  # .../hachi
    return pkg_root / "default_skills"


def test_three_pass_beats_dump_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    skills_dir = _bundled_skills_dir()
    if not skills_dir.exists():
        pytest.skip("bundled default_skills directory not found")

    engine = SkillEngine()
    engine.load_directory(skills_dir)

    nodes = engine.get_all_nodes()
    if len(nodes) < 3:
        pytest.skip("not enough skill nodes to run a meaningful benchmark")

    # Baseline: every skill's full content concatenated into the prompt (the
    # markdown-dump approach the .og format is meant to replace).
    baseline_text = "\n".join(engine.resolve_node(n.name, blocks=["all"]) for n in nodes)
    baseline = estimate_tokens(baseline_text)
    assert baseline > 0

    # Pick a keyword from a visible (non-brain.og) skill node.
    query = next(
        (
            node.keywords[0]
            for node in nodes
            if node.keywords and engine._is_skill_node(node)
        ),
        None,
    )
    if query is None:
        pytest.skip("no searchable keyword found in bundled skill nodes")

    results = engine.search(query)
    assert results, f"expected at least one match for query {query!r}"
    candidates = [r["name"] for r in results][:5]

    pass1 = estimate_tokens(json.dumps(results[:5]))  # search summaries
    pass2 = estimate_tokens(json.dumps(engine.peek(candidates)))  # compressed peeks
    pass3 = estimate_tokens(engine.resolve_node(candidates[0]))  # load exactly one skill
    three_pass = pass1 + pass2 + pass3

    savings = 1 - (three_pass / baseline)

    with capsys.disabled():
        print(
            f"\n[og benchmark] nodes={len(nodes)} | baseline={baseline} tok | "
            f"3-pass={three_pass} tok (p1={pass1}, p2={pass2}, p3={pass3}) | "
            f"savings={savings:.1%}"
        )

    assert three_pass < baseline
    assert savings >= SAVINGS_THRESHOLD, (
        f"expected >= {SAVINGS_THRESHOLD:.0%} token savings, got {savings:.1%}"
    )
