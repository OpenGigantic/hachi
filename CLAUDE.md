# CLAUDE.md — Hachi Working Agreement

This file defines how you (Claude Code) must work in **Hachi**. It provides both the
project map and the non-negotiable rules. These rules exist to prevent regressions,
context drift, and loss of working state. Follow them on **every** task. Where they
conflict with your default behavior, these rules win.

> If a rule below makes a task impossible, STOP and ask. Do not silently work around it.

---

## Project context

- **What this is:** Hachi — an autonomous agent engine. Skills are `ObjectGraph` (`.og`)
  files, not markdown. A ReAct loop drives tool-calling against an Anthropic (or
  OpenAI-compatible / Ollama) LLM, with a permission gate ("the leash") around every OS
  tool. Surfaces: a Typer CLI, a Textual TUI, a FastAPI gateway (REST + SSE), and a React SPA.
- **Stack:** Python 3.11+ managed by `uv` (hatchling build). Key libs (see `pyproject.toml`
  for pins): `anthropic`, `fastapi`/`starlette`/`uvicorn`, `pydantic` v2, `chromadb`,
  `typer`, `rich`, `textual`, `keyring`, `cryptography`. Frontend (`frontend/`): Vite +
  React + TypeScript + TanStack Router + zustand.
- **Core loop:** **CLI → Orchestrator → ExecutionLoop → ToolRegistry → LLM (Anthropic)**

## Commands — this is your verification gate

Requires Python 3.11+ and `uv`.

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests (the full suite is the contract — see Rule 2)
uv run pytest -q

# Run a single test
uv run pytest tests/test_module.py::TestClass::test_name -vv

# Lint / format / type-check
uv run ruff check src tests
uv run ruff format src tests          # see Rule 3: do NOT run repo-wide format casually
uv run mypy src                       # strict mode is on in pyproject.toml

# Run the app (Textual TUI)
uv run hachi run

# Run legacy Rich REPL
uv run hachi run --repl

# Initial setup wizard
uv run hachi setup --tier lightning

# Diagnostics and security audit
uv run hachi doctor
uv run hachi security-audit --json --fail-on-high

# Start standalone gateway server
uv run hachi gateway --host 127.0.0.1 --port 8787 --runtime

# Export OpenAPI schema + regenerate the frontend contract (after ANY gateway route change)
uv run hachi gateway-openapi --output openapi/hachi-openapi.json
npm run --prefix frontend generate:api
```

**Windows**: if an editable-install lock or `.exe` shim breaks pytest (OS error 4551), use:
```bash
PYTHONPATH=src uv run --with pytest --no-project pytest -q
```
or invoke modules directly (`python -m pytest`, `python -m mypy`).

"Looks done" is **not** done. A task is complete only when the relevant commands above
pass. Run them yourself and report the actual result. If you cannot run a command, say so
explicitly — never assume it passes.

---

## Architecture

### Key entry points

| Path | Role |
|------|------|
| `src/hachi/cli/main.py` | Typer CLI + in-process gateway runtime |
| `src/hachi/gateway/orchestrator.py` | Session boot: wires workspace, tools, LLM, memory |
| `src/hachi/core/loop.py` | ReAct execution loop — drives tool calling |
| `src/hachi/core/turn_context.py` | Frozen per-turn snapshot (no shared mutable state) |
| `src/hachi/og_engine/` | ObjectGraph (`.og`) parser and skill indexer |
| `src/hachi/tools/` | All LLM-callable tool implementations |
| `src/hachi/tui/app.py` | Textual UI |
| `src/hachi/gateway/fastapi_app.py` | FastAPI control plane (REST + SSE) |
| `src/hachi/core/runtime_events.py` | Typed event contract consumed by TUI and gateway |

### ObjectGraph (`.og`) format

Skills are `.og` files, not markdown. Agents read them in 3 passes:
- **Pass 1** — `::index` routing table (~30 tokens), picks relevant nodes
- **Pass 2** — `::dense` summaries for targeted nodes
- **Pass 3** — `::full` content only when needed

This cuts token usage vs. loading entire skill files. The spec is in `ObjectGraph_Complete_Specification.md`.

### Memory (3-layer split)

- **Active context** — in-context message history managed by `MemoryManager`; compacted
  asynchronously (deferred/conditional via the jobs queue, not inline per turn)
- **SessionStore** — SQLite transcript per session (`core/session_store.py`)
- **MemoryVault** — ChromaDB semantic store for long-lived facts (`core/memory_vault.py`),
  split into `core_memory`, `user_profile`, `agent_profile`, `delegation_log`

Memory writes go through the jobs queue (`jobs/queue.py`). The queue drains via a resident
worker thread; drain-at-turn-start is a fallback only.

### Permission gating

`device-permissions.og` declares filesystem roots, allowed/blocked shell commands, MCP
server toggles, and integration scopes. `PermissionGate` wraps OS tools and enforces these
at call time — not as runtime prompts.

### Sub-agent delegation

`delegate_task(role="...", task="...")` creates a scoped ToolRegistry, loads the matching
`brain-<role>.og`, and runs a child `ExecutionLoop` in isolation. Results come back via
`delegation_log`. This keeps the orchestrator's context clean.

### Runtime events

All significant lifecycle events are typed in `runtime_events.py`. The TUI and gateway both
consume these via `RuntimeReadModels`. Event names follow `<domain>.<noun>.<verb>`
(e.g. `tool.call.started`, `session.chat.completed`).

### Frontend contract

The `frontend/` app generates TypeScript API types into
`frontend/src/lib/data/schema.d.ts` from `openapi/hachi-openapi.json` via
`openapi-typescript`. After any gateway route change, run the two commands under
**Commands** above. CI enforces this via the `frontend-contract` job: it re-exports the
schema, runs `check:api` (regenerate + `git diff --exit-code` on `schema.d.ts`), and
`typecheck`. The hand-authored UI types live in `src/lib/data/types.ts`; `schema.d.ts` is
the generated transport contract (`paths` / `components["schemas"]`).

---

## Non-negotiable rules

### 1. Git is the safety net — use it
- The working tree often carries uncommitted WIP. Before any non-trivial change, report
  what's already uncommitted so your work isn't conflated with it.
- Commit after every meaningful, working unit of work, with a Conventional-Commit message
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`). Only commit when the user asks, or when
  it's the natural close of a unit you were told to land.
- **Never** amend, rebase, force-push, or `git reset --hard` away existing commits without
  explicit permission.
- If something breaks and you can't fix it within **2 attempts**, STOP. Report what broke
  and which commit was last known-good. Do not keep patching on top of a broken state.

### 2. Tests are a contract — do not cheat them
- **Never** modify, delete, weaken, comment out, or skip an existing test to make it pass.
  If a test fails, fix the **code**, not the test. If you believe a test is genuinely
  wrong, STOP and ask.
- For **every bug you fix**: first write a failing test that reproduces it, then make it
  pass.
- Tests must assert **real, observable behavior** (actual return values, real DB/SSE/API
  results) — not your assumptions about intended behavior.
- **Run the FULL `uv run pytest -q` suite before claiming done.** Hachi has thousands of
  tests and heavy cross-module coupling; isolated/scoped runs and parallel subagent lanes
  repeatedly miss real regressions that only the full suite surfaces. Report the actual
  pass/fail counts.

### 3. Stay in scope — don't touch what you weren't asked to
- Change only what the task requires. Do not refactor, rename, reformat, or "improve"
  unrelated code.
- **Never run a repo-wide `ruff format`** — it reflows ~100 files and buries your real
  diff. Format only the files you touched.
- **Never** delete existing functionality, edge-case handling, validation, or comments
  unless explicitly told to. Flag it instead.
- **Do not reintroduce `tokoboros` back-compat / migration shims.** The rename to Hachi
  deliberately ripped them out; tracked code/docs must stay tokoboros-free.

### 4. Don't guess library APIs
- If unsure a method, import, or parameter exists, check the installed package source or
  real docs before using it — especially for `anthropic`, `chromadb`, `textual`, and
  `fastapi`, whose APIs shift between versions. Verify against the pins in `pyproject.toml`.

### 5. Be honest about state
- If you're stuck, blocked, or unsure, say so plainly. Never claim success you haven't
  verified — particularly on Windows, where the test/type-check runners are finicky.
- Don't over-engineer: no speculative abstractions, defensive layers, or tests for cases
  that can't happen. Solve the stated problem.

---

## Coding conventions

- Python 3.11+ only; line length 100 (Ruff); `select = ["E","F","I","W","B","UP"]`
- `pathlib.Path` for all filesystem paths, never raw strings
- Type hints + docstrings on all public functions and classes (mypy `strict = true`)
- Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`

## Security guardrails

- No `subprocess` calls with `shell=True`
- No interactive credential prompts during Textual boot — resolve lazily at call time
- No mutation of shared loop state per turn — use `TurnContext` (frozen dataclass)
- Prefer queued background work (`jobs/queue.py`) over fire-and-forget threads

---

## Definition of done — run this checklist before reporting back
1. `uv run ruff check src tests` clean (format only the files you touched).
2. `uv run mypy src` clean (strict).
3. `uv run pytest -q` — **full suite** green; report actual pass/fail counts.
4. If you changed a gateway route: regenerated `openapi/hachi-openapi.json` **and**
   `npm run --prefix frontend generate:api`, and the frontend typechecks.
5. Summarized the change in 2–3 lines (what changed and why).
6. Committed with a clear message — or stated it's ready to commit.
7. Listed anything skipped, risky, or deliberately left out of scope.

## CI pipeline

Six jobs on every push/PR: `test`, `security`, `lint`, `skill-lint`, `benchmark`,
`frontend-contract`. Lint and type-check are `continue-on-error` on main but strict on PRs.
The security job additionally runs `--fail-on-medium` on pull requests. `skill-lint` runs
`scripts/lint_skills.py` and blocks on any error in shipped `.og` files.

---

## Known gotchas / recurring bugs (project memory)
<!--
This is the memory layer that survives across sessions. Whenever a bug recurs or you learn
a non-obvious constraint, ADD AN ENTRY HERE so it isn't relearned the hard way.
-->

- **Windows test runner:** `uv run pytest`/`mypy` `.exe` shims can fail with OS error 4551
  (editable-install lock). Fall back to `PYTHONPATH=src uv run --with pytest --no-project
  pytest -q` or `python -m pytest` / `python -m mypy`.
- **No import-time `.env` autoload.** Hachi does **not** load `.env` globally at import
  (attempting it caused a test-hermeticity cascade and was reverted). Resolve credentials
  lazily at call time via the keyring vault / `config_dir`. Don't add a top-level autoload.
- **OpenAPI ↔ frontend contract:** any change to a gateway route requires regenerating
  `openapi/hachi-openapi.json` **and** `npm run --prefix frontend generate:api`, or the
  `frontend-contract` CI job fails on the `schema.d.ts` diff.
- **Don't `patch.dict(sys.modules)` over `chromadb`.** It wipes the PyO3 native extension
  ("cannot load module more than once") and poisons later tests. `conftest` warms chromadb
  at import for this reason — leave it.
- **Config paths:** `paths.workspace_dir` does not exist — use `paths.skills_dir`.
- **`ProcessSharedServices` is lazy:** it is `None` before a session boots; don't assume
  shared services exist outside a live session.
- **Directory junction:** `D:\OpenGigantic\hachi` and a sibling `tokoboros` path point at
  the same files; pytest's `norecursedirs` skips `tokoboros` so the suite isn't collected
  twice.

## Out of scope — do not build unless explicitly asked
- **Multi-tenant** support (single-tenant is currently load-bearing) — flag for a decision.
- **Encryption-at-rest** and other security-architecture changes — flag, don't auto-build.
- **Desktop / mobile apps** — deferred by the user until the product is feature-rich and stable.
- **New runtime dependencies** — propose first; don't add to `pyproject.toml` unprompted.
- **`tokoboros` compatibility shims** — explicitly removed; do not reintroduce.
