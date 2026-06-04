# AGENTS.md

This file helps coding agents become productive in this repository quickly.

## Scope

- Applies to the whole repository.
- Prefer concise, minimal changes that preserve existing public behavior unless the task asks for behavior changes.

## Quick Start Commands

- Install runtime deps: `uv sync`
- Install dev deps: `uv sync --extra dev`
- Run tests: `uv run pytest -q`
- Lint: `uv run ruff check src tests`
- Format: `uv run ruff format src tests`
- Type-check: `uv run mypy src --ignore-missing-imports`
- Run app (TUI): `uv run tokoboros run`
- Run app (REPL fallback): `uv run tokoboros run --repl`
- Setup wizard: `uv run tokoboros setup --tier lightning`
- Show config: `uv run tokoboros config --paths`
- Manage secrets: `uv run tokoboros secrets --list|--check|--delete <name>`
- Diagnostics: `uv run tokoboros doctor`
- Security audit: `uv run tokoboros security-audit --json|--fail-on-high|--max-findings N`

## Architecture Map

- Entry point: `src/tokoboros/cli/main.py`
- Session orchestration and boot: `src/tokoboros/gateway/orchestrator.py`
- Main loop/tool execution: `src/tokoboros/core/loop.py`
- Immutable per-turn context: `src/tokoboros/core/turn_context.py`
- Skill parsing/execution: `src/tokoboros/og_engine/parser.py` and `src/tokoboros/og_engine/engine.py`
- Tool implementations: `src/tokoboros/tools/`
- Textual UI: `src/tokoboros/tui/app.py`
- Runtime event contract: `src/tokoboros/core/runtime_events.py` and `on_event(event_type: str, data: dict)`

## Coding Conventions

- Python 3.11+ only.
- Line length is 100 (Ruff).
- Add type hints and docstrings for public functions/classes.
- Keep modules focused on a single responsibility.
- Use `pathlib.Path` for filesystem paths.
- Follow Conventional Commits for commit messages.

## Testing Expectations

- Add or update tests under `tests/` for behavior changes.
- Prefer focused unit tests with `tmp_path` and `monkeypatch` where useful.
- Run lint + type-check + tests before finishing.

## Security And Runtime Guardrails

- Do not introduce new `subprocess` calls with `shell=True`.
- Avoid interactive credential prompts during Textual boot; resolve credentials lazily at call time.
- Do not mutate shared loop prompt state per turn; use `TurnContext` patterns.
- Prefer queued background work patterns over fire-and-forget threads; use `src/tokoboros/jobs/queue.py` for durable work when possible.

## Windows Notes

- If editable-install executable lock breaks `uv run pytest`, use:
  - `PYTHONPATH=src uv run --with pytest --no-project pytest -q`

## Source Docs (Link, Do Not Duplicate)

- Project overview: [README.md](README.md)
- Contribution process and standards: [CONTRIBUTING.md](CONTRIBUTING.md)
- Deep architecture and operations: [KNOWLEDGE_TRANSFER.md](KNOWLEDGE_TRANSFER.md)
- Current roadmap and risk register: [plan.md](plan.md)
- ObjectGraph spec: [ObjectGraph_Complete_Specification.md](ObjectGraph_Complete_Specification.md)
- CI pipeline: [.github/workflows/ci.yml](.github/workflows/ci.yml)
