# Bug Bank — Hachi's regression memory

This file is the **memory layer that survives across sessions**. It is the direct
cure for *"a bug I already fixed came back"* and *"I fixed the same bug three times."*

Every non-trivial bug or hard-won, non-obvious constraint gets a short **autopsy**
here: the symptom, the root cause, the fix, and what now *locks* it (a regression
test or a mechanical guard). When an agent starts a session, the `SessionStart`
hook (`.claude/hooks/session_start.py`) injects the entries below into context, so
the warnings are loaded *before* planning — never relearned the hard way.

> **The regression law (CLAUDE.md):** for every bug you fix, first write a test
> that *fails* because of the bug, then fix the code, then confirm it goes green.
> That test is permanent. Add the autopsy here and link the test under **Locked by**.

## How to add an entry

Copy the template, give it the next `BB-N` number, keep it tight:

```markdown
### BB-N — Short title
- **Symptom:** what you observe (the failure, not the cause).
- **Root cause:** the actual mechanism.
- **Avoid:** the one-line rule a future agent must follow. ← the SessionStart hook surfaces this.
- **Locked by:** the regression test path, or the mechanical guard. "None yet — add when next touched" is an honest answer.
```

Keep **Avoid:** to a single imperative line: it is what gets injected at session start.

---

## Entries

### BB-1 — Windows pytest/mypy `.exe` shim fails with OS error 4551
- **Symptom:** `uv run pytest` or `uv run mypy` aborts on Windows with `OSError 4551`
  (editable-install lock) before collecting anything.
- **Root cause:** the generated `.exe` console-script shim contends with the
  editable-install lock on Windows.
- **Avoid:** on Windows, fall back to `python -m pytest` / `python -m mypy`, or
  `PYTHONPATH=src uv run --with pytest --no-project pytest -q`. `scripts/preflight.py`
  invokes tools through `uv run`, which usually sidesteps the shim.
- **Locked by:** mechanical — documented fallback; no test (environment-specific).

### BB-2 — `.env` autoload belongs at the CLI edge, never in the core import path
- **Symptom:** adding a top-level `.env` autoload to core/library modules turned the suite
  red across unrelated tests; credentials "sometimes" resolve depending on import/test order.
- **Root cause:** loading `.env` during a *library* import leaks ambient env into hermetic
  tests and couples units to global state. A real attempt to do this caused a cascade and was
  reverted.
- **Avoid:** keep `.env` loading at the **CLI entry edge only** —
  `cli/main.py::_load_dotenv_into_environ()` runs at CLI import but is gated by
  `HACHI_DISABLE_DOTENV_AUTOLOAD=1` (conftest sets it). Core code (`core/oauth.py`,
  `core/llm.py`, `tools/google_tools.py`) resolves credentials **lazily at call time** — never
  add a library/core import-time autoload.
- **Locked by:** `tests/test_dotenv_hermeticity.py` (the guard works + conftest defaults the
  flag to `"1"`); lazy call-time readers in core. Related: BB-8.

### BB-3 — `patch.dict(sys.modules)` over `chromadb` poisons later tests
- **Symptom:** a test that patches `sys.modules["chromadb"]` passes, then a *later*
  test dies with `cannot load module more than once` (PyO3 native extension).
- **Root cause:** `patch.dict(sys.modules)` wipes chromadb's compiled PyO3 extension;
  it cannot be re-imported in the same process. A "restore" fixture made it worse by
  forcing re-import.
- **Avoid:** never `patch.dict(sys.modules)` over `chromadb`. `conftest` **warms
  chromadb at import** so the native module loads exactly once — leave that warm-up in.
- **Locked by:** `tests/conftest.py` chromadb warm-up (deterministic suite, ~8× faster).

### BB-4 — OpenAPI ↔ frontend contract drift fails CI
- **Symptom:** the `frontend-contract` CI job fails on a `schema.d.ts` diff after a
  gateway route change.
- **Root cause:** the generated TypeScript transport contract
  (`frontend/src/lib/data/schema.d.ts`) is derived from `openapi/hachi-openapi.json`;
  changing a route without regenerating both leaves them out of sync.
- **Avoid:** after **any** gateway route change run
  `uv run hachi gateway-openapi --output openapi/hachi-openapi.json` **and**
  `npm run --prefix frontend generate:api`. `scripts/preflight.py` checks this.
- **Locked by:** CI `frontend-contract` job; `npm run --prefix frontend check:api`.

### BB-5 — `paths.workspace_dir` does not exist
- **Symptom:** `AttributeError` reaching for `paths.workspace_dir`.
- **Root cause:** the path accessor was never named that.
- **Avoid:** use `paths.skills_dir` (and the other `get_hachi_paths()` accessors:
  `config_dir`, `data_dir`, `state_dir`, `cache_dir`).
- **Locked by:** `src/hachi/core/paths.py` (covered by the strict mypy gate).

### BB-6 — `ProcessSharedServices` is `None` before a session boots
- **Symptom:** `AttributeError`/`NoneType` when touching shared services outside a
  live session (e.g. in a CLI path or a test that didn't boot one).
- **Root cause:** `ProcessSharedServices` is lazy — it does not exist until a session
  boots.
- **Avoid:** never assume shared services exist outside a live session; guard for
  `None` or boot a session first.
- **Locked by:** None yet — add a regression test when next touched.

### BB-7 — `hachi` ↔ `tokoboros` directory junction (double collection)
- **Symptom:** the suite appears to collect twice / a sibling `tokoboros` path shows
  the same files; `git core.hooksPath` may point into the junction.
- **Root cause:** `D:\OpenGigantic\hachi` and a sibling `tokoboros` path are the same
  files via a Windows directory junction.
- **Avoid:** rely on pytest `norecursedirs` (skips `tokoboros`) so the suite isn't
  collected twice. Don't "fix" paths by chasing the junction.
- **Locked by:** `pyproject.toml` `[tool.pytest.ini_options] norecursedirs`.

### BB-8 — OAuth client id read as an import-time global never saw `.env`
- **Symptom:** Google Workspace config looked correct but auth never picked up
  `GOOGLE_CLIENT_ID`; spend/integration silently inert.
- **Root cause:** the id was read into a module-level global at import, before any
  `.env` was loaded (and there is no global autoload — see BB-2).
- **Avoid:** read integration credentials **lazily at call time**, loading
  `config_dir/.env` inside the call — not as a top-level constant.
- **Locked by:** regression test for lazy OAuth credential resolution (added with the fix).

### BB-9 — OpenAI-compatible streaming reported `$0` cost → daily spend cap inert
- **Symptom:** per-turn cost/tokens were `$0` across many real turns on OpenAI-compatible
  providers; the daily spend cap never triggered.
- **Root cause:** `OpenAICompatibleClient.generate_stream` omitted
  `stream_options.include_usage`, so the provider streamed no usage and cost tracking
  saw nothing — the spend cap had no data to act on.
- **Avoid:** keep the default-on `stream_usage` flag; OpenAI-compatible streaming must
  request `include_usage` or the cost/limit machinery goes silently dark.
- **Locked by:** regression test asserting usage is requested + cost > 0 on a streamed
  OpenAI-compat turn (added with the fix).

<!--
When you fix a bug: add BB-N here with a one-line **Avoid:**, link the locking test,
and (if the constraint is subtle) drop a comment next to the code itself — the agent
gets far better attention on a comment beside the function than on a doc three
directories away.
-->
