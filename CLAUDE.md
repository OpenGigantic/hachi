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
uv run tokoboros run

# Run legacy Rich REPL
uv run tokoboros run --repl

# Initial setup wizard
uv run tokoboros setup --tier lightning

# Diagnostics and security audit
uv run tokoboros doctor
uv run tokoboros security-audit --json --fail-on-high

# Start standalone gateway server
uv run tokoboros gateway --host 127.0.0.1 --port 8787 --runtime

# Export OpenAPI schema (required to keep frontend contract current)
uv run tokoboros gateway-openapi --output openapi/tokoboros-openapi.json
```

**Windows**: If editable-install lock breaks pytest, use:
```bash
PYTHONPATH=src uv run --with pytest --no-project pytest -q
```

Before finishing any change, run lint + type-check + tests.

## Architecture

Tokoboros is an autonomous agent engine. The core loop is:
**CLI → Orchestrator → ExecutionLoop → ToolRegistry → LLM (Anthropic)**

### Key entry points

| Path | Role |
|------|------|
| `src/tokoboros/cli/main.py` | Typer CLI + in-process gateway runtime |
| `src/tokoboros/gateway/orchestrator.py` | Session boot: wires workspace, tools, LLM, memory |
| `src/tokoboros/core/loop.py` | ReAct execution loop — drives tool calling |
| `src/tokoboros/core/turn_context.py` | Frozen per-turn snapshot (no shared mutable state) |
| `src/tokoboros/og_engine/` | ObjectGraph (`.og`) parser and skill indexer |
| `src/tokoboros/tools/` | All LLM-callable tool implementations |
| `src/tokoboros/tui/app.py` | Textual UI |
| `src/tokoboros/gateway/fastapi_app.py` | FastAPI control plane (REST + SSE) |
| `src/tokoboros/core/runtime_events.py` | Typed event contract consumed by TUI and gateway |

### ObjectGraph (`.og`) format

Skills are `.og` files, not markdown. Agents read them in 3 passes:
- **Pass 1** — `::index` routing table (~30 tokens), picks relevant nodes
- **Pass 2** — `::dense` summaries for targeted nodes
- **Pass 3** — `::full` content only when needed

This cuts token usage vs. loading entire skill files. The spec is in `ObjectGraph_Complete_Specification.md`.

### Memory (3-layer split)

- **Active context** — in-context message history managed by `MemoryManager`, compacted per turn
- **SessionStore** — SQLite transcript per session (`core/session_store.py`)
- **MemoryVault** — ChromaDB semantic store for long-lived facts (`core/memory_vault.py`), split into `core_memory`, `user_profile`, `agent_profile`, `delegation_log`

Memory sync happens asynchronously via the jobs queue (`jobs/queue.py`), not inline during the turn.

### Permission gating

`device-permissions.og` declares filesystem roots, allowed/blocked shell commands, MCP server toggles, and integration scopes. `PermissionGate` wraps OS tools and enforces these at call time — not as runtime prompts.

### Sub-agent delegation

`delegate_task(role="...", task="...")` creates a scoped ToolRegistry, loads the matching `brain-<role>.og`, and runs a child `ExecutionLoop` in isolation. Results come back via `delegation_log`. This keeps the orchestrator's context clean.

### Runtime events

All significant lifecycle events are typed in `runtime_events.py`. The TUI and gateway both consume these via `RuntimeReadModels`. Event names follow `<domain>.<noun>.<verb>` (e.g. `tool.call.started`, `session.chat.completed`).

### Frontend contract

The `frontend/` SvelteKit app generates API types from `openapi/tokoboros-openapi.json`. Run `uv run tokoboros gateway-openapi --output openapi/tokoboros-openapi.json` after any gateway route change; CI enforces this via the `frontend-contract` job.

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

Four jobs on every push/PR: `test`, `security`, `lint`, `frontend-contract`. Lint and type-check are `continue-on-error` on main but strict on PRs. The security job additionally runs `--fail-on-medium` on pull requests.
