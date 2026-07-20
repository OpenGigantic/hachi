#!/usr/bin/env python
"""check_bundle -- fail the gate if demo/mock fixture content ships in the production
web bundle (T-22, BB-123).

`frontend/src/lib/data/fixtures.ts` is ~400 lines of scripted demo data (a hardcoded
`priya@studio.co` recipient, a fabricated cost, sample sessions/skills/memory/etc.). It
exists for offline UI work in mock mode (`VITE_HACHI_USE_MOCKS=true`) and must never be
reachable from a real production build. The store isolates it behind a dynamic
`import("../data/fixtures")` taken only on the mock path (store.ts's
`seedMockFixtures()`), so a bundler that cannot prove the mock path dead still keeps the
fixture module out of the eagerly-loaded chunks -- but "should be tree-shaken" is a claim
about a bundler's behaviour, not a fact, so this script checks the *emitted files*
directly instead of trusting the build.

Scope note -- READ BEFORE TOUCHING THE MARKER LIST. The markers below are strings
verified (2026-07-20) to live ONLY inside fixtures.ts. They deliberately exclude demo
strings that are ALSO hardcoded inline inside store.ts's 13 `dataMode === "live"` mock
forks (e.g. `priya@studio.co`, "Sending email to Priya", "reader-1", `gmail.send(to=`).
T-22 explicitly leaves those 13 forks unedited (Wave-2 CF-16 store-slice work moves mock
behaviour behind the data layer), and store.ts itself is never dynamically imported, so
those particular strings are *expected* to remain in the production bundle until that
later task lands -- marking them here would make this a permanently-failing blocking gate
step. See docs/bug-bank.md BB-123 and
docs/audit/2026-07-19/tasks/T-22-web-fixtures-out-of-prod-bundle.md sec 5/9 for the full
reasoning (the task's own §7 example marker list included some of these and does not
actually go green under the in-scope fix -- verified, not assumed).

If you add a new demo string to fixtures.ts, add a marker for it here -- but only after
confirming (grep frontend/src/lib/store/store.ts) it is not ALSO duplicated inline in one
of the untouched mock forks.

Run standalone:  uv run python scripts/check_bundle.py
Registered in scripts/preflight.py as a BLOCKING step, immediately after "frontend build"
so it always runs against fresh output.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "frontend" / "dist"

# Demo-fixture markers. Each occurs, verbatim, in exactly one place in
# frontend/src/lib/data/fixtures.ts and nowhere else under frontend/src (including
# store.ts's mock forks -- see the module docstring above for why that distinction
# matters). Add a comment with the fixtures.ts anchor when you add a new one.
MARKERS: tuple[str, ...] = (
    "missing env var STRIPE_KEY",  # fixtures.ts: failed[0].error
    "can't reach 127.0.0.1:5432",  # fixtures.ts: mcpServers "postgres" entry
    "You're based in Lisbon",  # fixtures.ts: memGroups "About you" fact f2
    "no meetings on Fridays",  # fixtures.ts: proposed.to
    "A password that appeared in a pasted log",  # fixtures.ts: blocked[0]
    "Your name is Sam. You go by Sam in messages",  # fixtures.ts: memGroups fact f1
)


class Hit(NamedTuple):
    file: Path
    marker: str


def find_markers(dist_dir: Path) -> list[Hit]:
    """Scan every .js file under `dist_dir/assets/` for a demo-fixture marker.

    Raises FileNotFoundError if `dist_dir`'s `assets/` subdirectory is missing, or if it
    exists but contains no .js files. A missing/empty bundle means there is nothing real
    to check -- that must be a loud error, never a silent "0 hits" pass (T-22 sec 7).
    """
    assets_dir = dist_dir / "assets"
    if not assets_dir.is_dir():
        raise FileNotFoundError(
            f"{assets_dir} does not exist. Run `npm run --prefix frontend build` first -- "
            "a missing bundle must fail this check loudly, not pass vacuously."
        )
    js_files = sorted(assets_dir.rglob("*.js"))
    if not js_files:
        raise FileNotFoundError(
            f"{assets_dir} contains no .js files. Run `npm run --prefix frontend build` "
            "first -- an empty bundle must fail this check loudly, not pass vacuously."
        )

    hits: list[Hit] = []
    for path in js_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in MARKERS:
            if marker in text:
                hits.append(Hit(path, marker))
    return hits


def main() -> int:
    try:
        hits = find_markers(DIST_DIR)
    except FileNotFoundError as exc:
        print(f"check_bundle: FAIL -- {exc}")
        return 1

    if hits:
        print(
            f"check_bundle: FAIL -- {len(hits)} demo-fixture marker hit(s) in the "
            "production bundle:"
        )
        for hit in hits:
            try:
                rel = hit.file.relative_to(ROOT).as_posix()
            except ValueError:
                rel = str(hit.file)
            print(f"  - {rel} contains demo fixture marker '{hit.marker}'")
        print(
            "\n  Demo/mock fixture data must never reach a production bundle. "
            "frontend/src/lib/data/fixtures.ts must be reachable only via the dynamic "
            "import in store.ts's seedMockFixtures(), never a static import -- see "
            "docs/bug-bank.md BB-123."
        )
        return 1

    n_files = len(sorted((DIST_DIR / "assets").rglob("*.js")))
    rel_dist = DIST_DIR.relative_to(ROOT).as_posix()
    print(
        f"check_bundle: OK -- scanned {n_files} JS file(s) under {rel_dist}, "
        "no demo-fixture markers found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
