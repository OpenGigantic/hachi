# Architecture

Tokoboros is a local-first autonomous agent engine. This document describes the
major components and how they interact. For deep analysis and design rationale see
`docs/strategy/00-recon.md` through `docs/strategy/09-execution-plan.md`.

---

## Overview

```
User input
    │
    ▼
┌─────────────────────────────────────────────────┐
│  Entry points (CLI / TUI / Gateway / Channels)  │
│  src/tokoboros/cli/main.py                      │
│  src/tokoboros/tui/app.py                       │
│  src/tokoboros/gateway/fastapi_app.py           │
└──────────────────────┬──────────────────────────┘
                       │ session.chat()
                       ▼
          ┌────────────────────────┐
          │  Orchestrator          │  src/tokoboros/gateway/orchestrator.py
          │  (session boot, wiring)│
          └────────────┬───────────┘
                       │ run()
                       ▼
          ┌────────────────────────┐
          │  ExecutionLoop (ReAct) │  src/tokoboros/core/loop.py
          │  tool calls ↔ LLM     │
          └────────────┬───────────┘
              ┌────────┴────────┐
              ▼                 ▼
    ┌──────────────┐   ┌─────────────────┐
    │ ToolRegistry │   │ BaseLLMClient   │  src/tokoboros/core/llm.py
    │ (tools./)    │   │ (Anthropic API) │
    └──────────────┘   └─────────────────┘
```

---

## Core loop (`src/tokoboros/core/loop.py`)

The loop implements the **ReAct** (reason + act) pattern:

1. Build a `TurnContext` — a frozen snapshot of the current turn state.
2. Call the LLM with the full message history + tool specs.
3. For each tool call in the response: validate via `PermissionGate`, dispatch to the
   matching `ToolSpec`, append the result.
4. If the response is a final text message, emit `session.chat.completed` and return.
5. If the iteration limit is reached, return a partial summary with `status=incomplete`.

**No shared mutable state per turn.** `TurnContext` is a frozen dataclass; all
accumulation happens in local lists that are flushed to the shared history only on
a clean turn exit.

---

## ObjectGraph (.og) skill system (`src/tokoboros/og_engine/`)

Skills are `.og` files, not markdown. Each file is a graph of typed nodes. The agent
reads skills in three ranked passes, stopping as soon as it has what it needs:

| Pass | Content | Cost |
|------|---------|------|
| **Pass 1 — index** | `::index` routing table (~30 tok / file) | Very cheap |
| **Pass 2 — dense** | `::dense` summaries for matching candidates | Cheap |
| **Pass 3 — full** | `::full` content for the single best node | Exact skill size |

Key components:

- **`SkillEngine`** (`engine.py`) — indexes `.og` files; provides `search()` (BM25),
  `peek()` (Pass 2), `resolve_node()` (Pass 3), and persistent usage tracking
  (`state_dir/skill_usage.json` via `get_tokoboros_paths()`).
- **`parse_og_file()`** (`parser.py`) — pure-Python parser; `.og` is data, never code.
- **`lint_og_file()`** (`engine.py`) — structural linter; `missing-index` and
  `dense-over-budget` are errors for skill-role nodes, blocking in CI.
- **Qualified node IDs** — nodes are indexed as `<file-stem>:<node>` to prevent
  silent shadowing between user, community, and built-in skill files.

Default skill library: `src/tokoboros/default_skills/**/*.og`

---

## Memory (`src/tokoboros/core/`)

Three-layer split:

| Layer | Location | Purpose |
|-------|----------|---------|
| **Active context** | In-memory message history | Managed per-turn by `MemoryManager`; compacted asynchronously via the jobs queue |
| **SessionStore** | `state_dir/sessions.db` (SQLite) | Per-session transcript; every turn appended |
| **MemoryVault** | `state_dir/chroma_vault/` (ChromaDB) | Long-lived semantic facts, split into `core_memory`, `user_profile`, `agent_profile`, `delegation_log` |

Memory writes go through the jobs queue (`src/tokoboros/jobs/queue.py`) — never
inline during the turn. `MemoryGuard` quarantines writes from untrusted content.
Failed writes surface in the `jobs/failed` read-model (visible in the TUI badge).

---

## Permission gating (`src/tokoboros/tools/permissions.py`)

`device-permissions.og` (in the platformdirs config dir) declares:
- Allowed filesystem roots (reads and writes)
- Allowed / blocked shell commands
- MCP server toggles
- Integration scopes

`PermissionGate` wraps every OS-touching tool call and checks the gate **at call
time**, not as a prompt. Denials emit `tool.call.denied` runtime events.

---

## Sub-agent delegation (`src/tokoboros/core/sub_agent.py`)

`delegate_task(role="...", task="...")` creates a scoped `ToolRegistry`, loads the
matching `brain-<role>.og` via `RoleResolver`, and runs a child `ExecutionLoop` in
isolation. Results return via `delegation_log` in `MemoryVault`.

`RoleResolver` looks up role files in order:
1. `<platformdirs data_dir>/skills/roles/<role>.og` (user-defined overrides)
2. Legacy `~/.tokoboros/skills/roles/<role>.og` (read shim, for migration)
3. `src/tokoboros/default_skills/roles/<role>.og` (built-in)

---

## Runtime events (`src/tokoboros/core/runtime_events.py`)

All significant lifecycle moments emit typed events following `<domain>.<noun>.<verb>`
(e.g. `tool.call.started`, `session.chat.completed`, `jobs.write.failed`).
The TUI and gateway consume these via `RuntimeReadModels` — no direct coupling
between the loop and the display layer.

---

## Gateway / REST API (`src/tokoboros/gateway/fastapi_app.py`)

FastAPI control plane exposing:
- `POST /sessions` — create a session (lazy-boots the agent)
- `POST /sessions/{id}/chat` — send a message; returns SSE stream or JSON
- `GET  /sessions/{id}/events` — SSE event stream
- `GET  /health` — liveness probe
- `POST /v1/chat/completions` — OpenAI-compat facade

Start standalone: `uv run tokoboros gateway --host 127.0.0.1 --port 8787 --runtime`

---

## Platform paths

All user data goes through `get_tokoboros_paths()` (`src/tokoboros/core/paths.py`),
which returns platformdirs-based paths:

| Directory | Contents |
|-----------|---------|
| `config_dir` | `settings.toml`, `device-permissions.og`, `brain.og` |
| `data_dir`   | `skills/` (user skill library) |
| `state_dir`  | `sessions.db`, `skill_usage.json`, `schedules.json`, `checkpoints/` |
| `cache_dir`  | Transient caches |

On first boot with an existing `~/.tokoboros/` workspace, `import_legacy_workspace()`
migrates settings and skill files to the new locations (one-time, non-destructive).

---

## CI pipeline (`.github/workflows/ci.yml`)

| Job | What it checks |
|-----|---------------|
| `test` | `pytest -q` — full test suite |
| `security` | `tokoboros security-audit --fail-on-high`; PRs add `--fail-on-medium` |
| `lint` | `ruff check src tests`; `mypy src` (continue-on-error on main, strict scope TBD) |
| `skill-lint` | `python scripts/lint_skills.py` — 0 errors across `default_skills/` |
| `benchmark` | `tokoboros skill benchmark --json --threshold 0.80` — savings ≥ 80% |
