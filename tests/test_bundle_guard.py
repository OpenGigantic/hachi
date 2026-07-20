"""Regression tests for scripts/check_bundle.py (T-22, BB-123).

`frontend/src/lib/store/store.ts` used to statically import the 394-line demo-fixture
corpus (`frontend/src/lib/data/fixtures.ts`) and reference every binding unconditionally
inside `domainDefaults()` (~30 `USE_MOCKS ? fixture : liveDefault` ternaries). `USE_MOCKS`
is a *computed* boolean, not a literal, so Rollup could never prove those ternary arms
dead -- every production build (VITE_HACHI_USE_MOCKS unset) shipped the whole fixture
corpus, including a hardcoded `priya@studio.co` recipient and a fabricated $0.02 cost, to
every user. Confirmed against a real build: see the `check_bundle: FAIL` output captured
in docs/bug-bank.md BB-123.

This is the regression lock for the *checker itself* (not the store.ts fix -- there is no
way to express "a string is absent from a built JS bundle" as a vitest/pytest assertion
against source, so the real lock is scripts/preflight.py's blocking "frontend bundle
purity" step running scripts/check_bundle.py against a fresh build). These tests pin that
the checker actually detects a known marker and, just as importantly, refuses to report a
silent pass when there is no real bundle to check -- a guard that vacuously passes on a
missing/stale/empty `frontend/dist` is worse than no guard at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_bundle import MARKERS, find_markers


def test_checker_flags_a_marker(tmp_path: Path) -> None:
    """The guard must actually detect demo fiction, not pass vacuously."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-deadbeef.js").write_text(
        'const a="can\'t reach 127.0.0.1:5432";', encoding="utf-8"
    )

    hits = find_markers(tmp_path)

    assert hits, "guard did not flag a known demo-fixture marker"
    assert hits[0].marker == "can't reach 127.0.0.1:5432"
    assert hits[0].file == assets / "index-deadbeef.js"


def test_checker_flags_every_marker_present_across_files(tmp_path: Path) -> None:
    """Multiple markers, multiple files -- every hit must be reported, not just the first."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.js").write_text("x = 'missing env var STRIPE_KEY';", encoding="utf-8")
    (assets / "b.js").write_text('y = "no meetings on Fridays";', encoding="utf-8")
    (assets / "clean.js").write_text("console.log('hello world');", encoding="utf-8")

    hits = find_markers(tmp_path)

    assert {h.marker for h in hits} == {"missing env var STRIPE_KEY", "no meetings on Fridays"}
    assert {h.file.name for h in hits} == {"a.js", "b.js"}


def test_checker_reports_zero_hits_on_a_clean_bundle(tmp_path: Path) -> None:
    """A real bundle with no demo strings must pass -- the guard is not trigger-happy."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text(
        "console.log('a perfectly ordinary production bundle');", encoding="utf-8"
    )

    assert find_markers(tmp_path) == []


def test_checker_refuses_to_pass_on_a_missing_dist(tmp_path: Path) -> None:
    """A missing bundle must be an error, not a silent pass."""
    with pytest.raises(FileNotFoundError):
        find_markers(tmp_path / "does-not-exist")


def test_checker_refuses_to_pass_on_an_empty_dist(tmp_path: Path) -> None:
    """A dist/assets/ that exists but holds no .js files must also error, not pass --
    e.g. a stale `frontend/dist` left over from `npm run clean` mid-build, or an assets
    directory that only ever received the CSS bundle."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-ClxQ994S.css").write_text("body { margin: 0 }", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        find_markers(tmp_path)


def test_marker_list_is_not_a_no_op() -> None:
    """The rail rots into theater if MARKERS is ever emptied out -- guard the guard."""
    assert len(MARKERS) > 0
    assert all(isinstance(m, str) and m for m in MARKERS)
