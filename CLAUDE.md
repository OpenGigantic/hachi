# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Requires Python 3.11+ and `uv`.

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest -q

# Run a single test
uv run pytest tests/test_module.py::TestClass::test_name -vv

# Lint / format / type-check
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src --ignore-missing-imports

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

# Export OpenAPI schema (required to keep frontend contract current)
uv run hachi gateway-openapi --output openapi/hachi-openapi.json
```

**Windows**: If editable-install lock breaks pytest, use:
```bash
PYTHONPATH=src uv run --with pytest --no-project pytest -q
```

Before finishing any change, run lint + type-check + tests.

## Architecture

Hachi is an autonomous agent engine. The core loop is:
**CLI → Orchestrator → ExecutionLoop → ToolRegistry → LLM (Anthropic)**

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
- **MemoryVault** — ChromaDB semantic store for long-lived facts (`core/memory_vault.py`), split into `core_memory`, `user_profile`, `agent_profile`, `delegation_log`

Memory writes go through the jobs queue (`jobs/queue.py`). The queue drains via a resident
worker thread; drain-at-turn-start is a fallback only.

### Permission gating

`device-permissions.og` declares filesystem roots, allowed/blocked shell commands, MCP server toggles, and integration scopes. `PermissionGate` wraps OS tools and enforces these at call time — not as runtime prompts.

### Sub-agent delegation

`delegate_task(role="...", task="...")` creates a scoped ToolRegistry, loads the matching `brain-<role>.og`, and runs a child `ExecutionLoop` in isolation. Results come back via `delegation_log`. This keeps the orchestrator's context clean.

### Runtime events

All significant lifecycle events are typed in `runtime_events.py`. The TUI and gateway both consume these via `RuntimeReadModels`. Event names follow `<domain>.<noun>.<verb>` (e.g. `tool.call.started`, `session.chat.completed`).

### Frontend contract

The `frontend/` app (TanStack Router SPA + React + zustand) generates TypeScript API
types into `frontend/src/lib/data/schema.d.ts` from `openapi/hachi-openapi.json` via
`openapi-typescript`. After any gateway route change, run:

```bash
uv run hachi gateway-openapi --output openapi/hachi-openapi.json
npm run --prefix frontend generate:api
```

CI enforces this is kept current via the `frontend-contract` job: it re-exports the
schema, runs `check:api` (regenerate + `git diff --exit-code` on `schema.d.ts`), and
`typecheck`. The hand-authored UI types live in `src/lib/data/types.ts`; `schema.d.ts`
is the generated transport contract (`paths` / `components["schemas"]`).

## Coding conventions

- Python 3.11+ only; line length 100 (Ruff); `select = ["E","F","I","W","B","UP"]`
- `pathlib.Path` for all filesystem paths, never raw strings
- Type hints + docstrings on all public functions and classes
- Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`

## Security guardrails

- No `subprocess` calls with `shell=True`
- No interactive credential prompts during Textual boot — resolve lazily at call time
- No mutation of shared loop state per turn — use `TurnContext` (frozen dataclass)
- Prefer queued background work (`jobs/queue.py`) over fire-and-forget threads

## CI pipeline

Six jobs on every push/PR: `test`, `security`, `lint`, `skill-lint`, `benchmark`, `frontend-contract`.
Lint and type-check are `continue-on-error` on main but strict on PRs.
The security job additionally runs `--fail-on-medium` on pull requests.
`skill-lint` runs `scripts/lint_skills.py` and blocks on any error in shipped `.og` files.
