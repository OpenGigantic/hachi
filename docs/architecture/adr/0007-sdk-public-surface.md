# ADR-0007 — `hachi.sdk` is the supported public surface

- **Status:** Accepted
- **Date:** 2026-07-21
- **Deciders:** T-76 (architecture-review Wave 2, Missed Opportunity #4)
- **Related:** [docs/guides/sdk.md](../../guides/sdk.md); `tests/test_p3_06_sdk_v0.py`

## Context

`src/hachi/sdk/__init__.py` (113 lines, one file, no other modules in the package) re-exports 12
symbols from `hachi.core` and `hachi.gateway` and claims in its own docstring to be a "stable
public surface for third-party pack authors", "independently semver'd", and pinnable as
`hachi-og[sdk]`.

Verification (2026-07-19 architecture review, confirmed against `main` @ `bd24afe`) found the
claim was unbacked in four concrete ways:

1. **No `sdk` extra existed.** `pyproject.toml`'s `[project.optional-dependencies]` defined
   `dev`, `memory`, `local-models`, `search`, `browser`, `files`, `tracing`, `observability`,
   `presentations`, `unofficial`, `security` — no `sdk`. `pip install hachi-og[sdk]` as
   documented would warn about an unknown extra and install nothing extra.
2. **No `py.typed` marker existed anywhere in the repository**, so `hachi.sdk` — despite being
   fully annotated (`from __future__ import annotations`, typed signatures throughout) — was
   PEP 561-invisible: any downstream `mypy`/`pyright` run reported "missing library stubs or
   py.typed marker" instead of type-checking against it.
3. **`SDK_VERSION` had no mechanical link to anything.** It was a bare `"0.1.0"` literal,
   coincidentally equal to `pyproject.toml`'s `version = "0.1.0"`, with no test tying it to the
   shape of the surface it is supposed to version.
4. **The contract test (`tests/test_p3_06_sdk_v0.py`) checked only one direction.** Both
   surface tests asserted that every documented symbol is *present* in `hachi.sdk.__all__`;
   nothing asserted the reverse. Adding an internal symbol (e.g. the dead `_MIN_APP_VERSION`
   constant) to `__all__` passed the full 11-test suite, despite the file's own docstring
   claiming the suite validates "the stable surface does NOT export any private/internal-only
   symbols."

Two further defects in the same file: `_MIN_APP_VERSION` was assigned once and read nowhere
(dead); and `is_compatible()`'s docstring promised "a simple major-match + minor-GTE rule" while
its implementation was a plain 3-tuple `>=` comparison — no major-match requirement, and it
raised `ValueError`/`IndexError` on malformed input instead of returning `False`.

Outside its own source and test, `hachi.sdk` appeared in exactly four places repo-wide: an
`import-linter` `forbidden_modules` entry, two mentions in a strategy planning doc, and one
roadmap row marking it "done." `README.md`, `ARCHITECTURE.md`, and `docs/DECISIONS.md` had zero
mentions. No ADR existed for it. A "public surface for third-party pack authors" that no
author-facing document mentioned.

## Decision

Bless the existing 12-symbol surface rather than redesign it. Make each promise the docstring
already made either **mechanically enforced** or **removed** — nothing is left as prose:

1. **Typed.** `src/hachi/sdk/py.typed` (empty marker file) ships in the wheel — verified by
   building the wheel and inspecting its contents, not by assuming the whole-tree
   `[tool.hatch.build.targets.wheel]` include covers it.
2. **Installable as documented.** No `sdk` extra was added. Every symbol `hachi.sdk` re-exports
   depends only on packages already in `[project.dependencies]` (FastAPI, tomlkit, pydantic,
   ...) — an extra with nothing to install would be theatre. The docstring's `hachi-og[sdk]`
   claim was deleted and replaced with the true instruction: `pip install hachi-og`.
3. **Surface pinned bidirectionally.** `tests/test_p3_06_sdk_v0.py::test_sdk_all_contains_nothing_beyond_stable_symbols`
   asserts `set(sdk.__all__) == set(_STABLE_SYMBOLS)` — equality, not one-way membership. Adding
   or removing a symbol now fails the suite.
4. **Version tied to the surface.** `test_sdk_version_matches_recorded_surface` pins
   `(SDK_VERSION, sorted(__all__))` to a checked-in `_SURFACE_BY_VERSION` fingerprint. Widening
   or narrowing `__all__` without bumping `SDK_VERSION` (and recording the new surface) fails
   the suite. `SDK_VERSION` stays deliberately independent of `pyproject.toml`'s package
   `version` — collapsing the two would delete the "independently semver'd" property rather than
   enforce it; this ADR is the record of that being a decision, not an oversight.
5. **Documented where a consumer would look.** [`docs/guides/sdk.md`](../../guides/sdk.md) lists
   all 12 symbols, the stability policy, a runnable compatibility-check example, and the
   `sdk`/`client` distinction below. `README.md` and `docs/README.md`'s guide index link it.

`is_compatible()` was corrected to match its own docstring: major versions must match exactly,
the running minor must be `>=` the required minor, patch is not considered, and a malformed
argument returns `False` instead of raising. The three pre-existing `is_compatible` tests
(`test_is_compatible_with_same_version`, `_older_version`, `_future_version`) were re-verified
against the corrected implementation and pass unchanged — all three compare against SDK_VERSION
`0.1.0`, so no case exercises the new major-match branch differently than before. The dead
`_MIN_APP_VERSION` constant was deleted.

**`hachi.sdk` and `hachi.client` remain SEPARATE and are not to be merged.** `sdk/` (113 lines,
1 file) is an in-process re-export facade for pack authors, importing `hachi.core` /
`hachi.gateway` by Python import. `client/` (2,488 lines, 5 files) is a synchronous REST + SSE
consumer built for the Textual TUI, talks to the gateway over `httpx`, and imports nothing from
`hachi.*`. They look like duplication and are not — see the `sdk/` vs `client/` comparison table
in [docs/guides/sdk.md](../../guides/sdk.md) for the full detail.

## Alternatives considered

1. **Add an `sdk` extra anyway, even though it would be empty.** Rejected — an extra that exists
   only to make a docstring's `pip install hachi-og[sdk]` line technically resolve, while
   installing nothing beyond the base package, is a false signal of an optional dependency that
   does not exist. The honest fix is to state the true install command.
2. **Tie `SDK_VERSION` directly to `importlib.metadata.version("hachi-og")`.** Rejected — this
   would silently delete the "independently semver'd" property the docstring promises (the SDK's
   minor version could no longer diverge from the app's release cadence). The chosen fix (pin
   the *surface*, not the package version) enforces the same underlying goal — "the version means
   something" — without collapsing two properties that are meant to be different axes.
3. **Redesign the 12-symbol surface while blessing it** (add/remove symbols, decouple `ToolSpec`
   from `hachi.gateway`). Rejected — blessing an existing surface and redesigning one are
   different tasks with different risk. The 12 symbols are unchanged by this decision; a symbol
   deemed missing should be proposed and added deliberately later, which is exactly what the
   now-bidirectional test makes possible.
4. **Merge `sdk/` and `client/` into one public package.** Rejected — see Decision above; they
   serve different audiences (in-process pack authors vs. an HTTP-only TUI client) and share no
   code today.
5. **Add an `import-linter` contract constraining `sdk`/`client` as a `service` layer.**
   Rejected for this task — the commented three-layer model in `pyproject.toml` is real debt
   worth enforcing, but doing so here risks failing the gate for reasons unrelated to blessing
   the surface. Tracked separately (import-linter exemption burn-down).

## Consequences

**Positive:** the surface can no longer widen or narrow silently — adding or removing a symbol
requires a version bump and a test update in the same commit; downstream pack authors get real
type checking against `hachi.sdk` via `py.typed`; the install instructions are true; a consumer
looking at `README.md` or `docs/README.md` now finds the SDK.

**Negative / cost:** `is_compatible()`'s corrected major-match rule is a real (if currently
inert) behavior change — under a future `SDK_VERSION = "1.0.0"`, `is_compatible("0.1.0")` would
return `False` where the old implementation returned `True`. This is the intended correction
(matching the rule the docstring always claimed), not a regression, but any pack author who
depended on the old lenient behavior across a major bump would need to re-pin. `_SURFACE_BY_VERSION`
is a second place (alongside `SDK_VERSION` itself) that must be updated on every deliberate
surface change — a small but real maintenance tax, which is the point: it converts an accidental
widening into a deliberate, reviewable one.

**Reversibility:** high. Every change here is additive or corrective (a marker file, two new
tests, a docstring correction, a dead constant removed); none of it constrains the *shape* of a
future redesign of the surface, only how deliberately that redesign must happen.

**Deliberately NOT decided here:** whether `client/models.py` (which mirrors
`frontend/src/lib/data/types.ts`, not the gateway) should be generated from the OpenAPI schema.
That is a four-layer contract problem tracked with the OpenAPI pilot work
([T-38](../../audit/2026-07-19/tasks/T-38-openapi-pilot-og-domain.md)). Also noted, not fixed:
`docs/strategy/09-execution-plan.md:466` marks `doctor --plugin` complete; grepping `src/hachi/`
for `--plugin` / `plugin_pkg` / `def plugin` finds nothing — it does not exist. This is a
roadmap-honesty defect (the same shape as CF-1 in the 2026-07-19 review), recorded here, not
fixed here.
