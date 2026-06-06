<div align="center">

# 🐍 Tokoboros

**The Autonomous Agent Engine Powered by ObjectGraph (.og)**

_A multi-agent orchestration framework that cuts skill-loading token costs by loading only the skill nodes a task needs — via a structured, queryable, agent-native file format._

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-purple.svg)](https://docs.astral.sh/ruff/)

</div>

---

## What is Tokoboros?

Tokoboros is an autonomous AI agent framework built from the ground up to solve the three biggest problems in modern agentic AI:

| Problem               | How Tokoboros Solves It                                                                                                       |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **Token Waste**       | ObjectGraph (.og) files enable partial, query-aware loading — an agent reads a skill's compact index (~30 tokens) and pulls only the node it needs, instead of the whole file |
| **Context Pollution** | A delegation architecture spawns isolated sub-agents, keeping the orchestrator's context window pristine                      |
| **Cost Explosion**    | Native Anthropic prompt caching (cached input billed at a fraction of the base rate) plus query-aware skill loading keep per-turn token spend low |

Unlike traditional agent frameworks that dump everything into the system prompt and pray, Tokoboros treats skill files as **queryable databases** and agents as **specialized workers** — each with their own clean context window, strict behavioral constraints, and deterministic validation.

---

## Quickstart (one-liner install)

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/OpenGigantic/tokoboros/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/OpenGigantic/tokoboros/main/install.ps1 | iex
```

The installer bootstraps [`uv`](https://docs.astral.sh/uv/) (which manages Python for you), fetches Tokoboros, installs dependencies, and launches the guided setup. No prior Python setup required.

Already have the source? Just run the conversational setup:

```bash
uv run tokoboros quickstart   # pick a provider, we validate your API key up front, choose a safety level
uv run tokoboros run          # start chatting
```

`quickstart` checks your provider API key **before** the first message (instead of failing mid-conversation) — the most common first-run snag for new users.

---

## Features

- 🧠 **ObjectGraph (.og) Engine** — A structured, agent-native file format with 20+ semantic tags for structured, deterministic skill execution
- 🖥️ **Textual Operator Console** — Rich local UI with transcript, runtime timeline, setup flow, and operations views
- 🌐 **Typed Gateway API** — FastAPI control plane for sessions, events, runtime read models, config, and setup
- ⚛️ **Web Frontend Workspace** — TanStack Start frontend wired to gateway endpoints with generated OpenAPI client types
- 🔀 **Multi-Agent Delegation** — Sub-agents with isolated context windows and searchable delegation reports
- 💰 **Prompt Caching** — Native Anthropic cache breakpoints on system prompts and tool schemas (cached input is billed at a fraction of the base rate)
- 🔐 **Permission Gating** — Declarative `device-permissions.og` manifest controlling filesystem, terminal, and MCP access
- 🧱 **Immutable Turn Context** — Per-turn execution state is frozen via `TurnContext` to avoid shared prompt mutation
- 🗂️ **Job Queue Foundations** — Runtime jobs are tracked with retry/backoff and surfaced in gateway read models
- 🔌 **Native Integrations** — Google Workspace (Gmail, Drive, Calendar), Notion, Slack — all via OAuth
- 🌐 **Universal REST Adapter** — Define any REST API in a `.og` file, and Tokoboros auto-generates the tool
- 🕸️ **Browser Automation** — A real headless browser (`browse_web`, `browser_act`, `browser_screenshot`) for live web research and multi-step flows; SSRF-guarded and permission-gated (optional: `pip install 'tokoboros[browser]'` + `playwright install chromium`)
- 👁️ **Vision Input** — `view_image` lets the agent look at a screenshot/photo/diagram/chart from a **local file or an http(s) URL** (PNG/JPEG/GIF/WebP) and reason about it; permission-gated and SSRF-guarded (Anthropic provider)
- 🧯 **Built-in Guardrails** — Outbound Slack/Discord messages and REST requests are scrubbed of leaked credentials (API keys, tokens, private keys redacted before they leave the machine); untrusted **web, REST, and MCP** content is spotlighted so injected instructions are treated as data, not commands
- 🧩 **MCP Support** — Model Context Protocol servers for filesystem and external tools
- 📊 **Skill Lifecycle** — Usage tracking, stale detection, and analytics for all `.og` skills
- 🧬 **Long-Term Memory** — Semantic search vault with async prefetch and frozen session snapshots
- 🛡️ **Security Audit Command** — Built-in `security-audit` CLI checks for risky local runtime posture
- ⚡ **3-Pass Selective Loading** — Agents read a skill's index → dense summary → full content on demand, loading only what a task needs instead of dumping whole skill files (reproducible benchmark: `tests/test_og_benchmark.py`, CI-enforced ≥80% savings)
- 🕸️ **Typed Skill Traversal** — Resolve a skill's prerequisite chain via its `requires` edges (`skill_dependencies`), verify its `::assertion` preconditions before acting (`check_skill_assertions`), and delta-check whether it changed since last load (`skill_changelog`) — all without loading heavy content
- ✍️ **Autonomous Skill Authoring** — The agent writes & refines its own `.og` skills (`save_skill`); import the wider ecosystem with `tokoboros skill import` (SKILL.md → `.og`)
- ⏰ **Proactive Scheduling** — `tokoboros schedule add` runs tasks on cron/interval/once with delivery to chat or a webhook
- 💬 **Multi-Channel + OpenAI-Compatible API** — Reach the agent from Telegram or any platform via a generic inbound webhook, or point Open WebUI / LibreChat at the `/v1/chat/completions` endpoint
- 📣 **Outbound Slack & Discord** — The agent posts proactively (`send_slack_message`, `send_discord_message`) via bot token or webhook; SSRF-guarded, credentials resolved lazily at call time
- 🔄 **Model Freedom** — Anthropic, OpenAI-compatible, **OpenRouter** (200+ models), and local **Ollama** — no lock-in
- 💵 **Cost Controls** — Live token/cost tracking plus per-session budget caps and per-sender rate limits
- ↩️ **Edit Undo** — File writes are snapshotted; `rollback_changes` restores the previous version
- 🚀 **Frictionless Setup** — One-liner install (`install.sh` / `install.ps1`) + conversational `tokoboros quickstart`, or self-host with Docker

> See **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** for the full environment-variable and settings reference.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- An [Anthropic API key](https://console.anthropic.com/)
- Node.js 20+ (optional, for `frontend/` local development)

### Installation

```bash
# Clone the repository
git clone https://github.com/OpenGigantic/tokoboros.git
cd tokoboros

# Create/update the local environment and install the package
uv sync
```

### Configuration

```bash
# Set your Anthropic API key
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Run

```bash
uv run tokoboros run
```

By default, `tokoboros run` starts the gateway-backed Textual operator console.
Use `uv run tokoboros run --repl` to launch the legacy Rich REPL instead.

On first run, Tokoboros will:

1. Create the `~/.tokoboros/` workspace directory
2. Apply quick-start setup defaults and prepare provider/permission configuration
3. Copy default skill files to `~/.tokoboros/skills/`
4. Launch the Textual operator console, or resume setup if configuration is incomplete

### Common Commands

```bash
# Launch Textual UI (default)
uv run tokoboros run

# Launch legacy REPL
uv run tokoboros run --repl
uv run tokoboros run-repl

# Run setup flow without starting a session
uv run tokoboros setup --tier lightning

# Inspect config and canonical paths
uv run tokoboros config --paths

# Manage secret storage metadata and health
uv run tokoboros secrets --list
uv run tokoboros secrets --check

# Environment and security diagnostics
uv run tokoboros doctor
uv run tokoboros security-audit --json --fail-on-high

# Start standalone gateway server
uv run tokoboros gateway --host 127.0.0.1 --port 8787 --runtime

# Export OpenAPI contract for clients
uv run tokoboros gateway-openapi --output openapi/tokoboros-openapi.json

# Fastest first-run setup (conversational; validates your API key up front)
uv run tokoboros quickstart

# Skills: import SKILL.md, list, export, lint, usage report
uv run tokoboros skill import ./my-skill.md
uv run tokoboros skill list
uv run tokoboros skill export deploy_to_prod -o deploy.og
uv run tokoboros skill lint ./my-skills/      # structure + ::dense token-budget check (--strict for CI)
uv run tokoboros skill report                 # which skills earn their keep vs. dead weight

# Proactive scheduling (cron / interval / one-shot)
uv run tokoboros schedule add "daily-brief" "Summarize my unread email" --cron "0 9 * * 1-5"
uv run tokoboros schedule list
uv run tokoboros schedule run-due

# Talk to the agent from Telegram (needs TELEGRAM_BOT_TOKEN)
uv run tokoboros channel telegram
```

### Gateway and Web Frontend

Tokoboros now ships a typed HTTP gateway and a separate frontend workspace.

```bash
# Terminal 1: gateway
uv run tokoboros gateway --host 127.0.0.1 --port 8787 --runtime

# Terminal 2: frontend dev server
cd frontend
npm install
npm run dev
```

Gateway base URL: `http://127.0.0.1:8787`

Useful endpoints:

- `GET /health`
- `GET /readiness`
- `POST /sessions`
- `POST /sessions/{session_id}/messages`
- `GET /sessions/{session_id}/events`
- `GET /sessions/{session_id}/stream` — Server-Sent Events stream (replaces polling)
- `GET /runtime/events`
- `GET /config`
- `GET /setup`
- `POST /v1/chat/completions` — OpenAI-compatible (point Open WebUI / LibreChat here)
- `POST /channels/{channel}/inbound` — generic inbound webhook for any chat platform

### Docker Compose

The repository includes a two-service local stack:

- `tokoboros` (Python gateway service on `:8787`)
- `frontend` (web UI on `:5173`)

```bash
docker compose up --build
```

---

## Architecture

```
tokoboros/
├── src/tokoboros/
│   ├── cli/                  # CLI entry point (Typer)
│   │   └── main.py           # Run modes, gateway server commands, diagnostics
│   │
│   ├── core/                 # Core engine modules
│   │   ├── llm.py            # LLM abstraction layer (Anthropic, with prompt caching)
│   │   ├── loop.py           # Autonomous ReAct execution loop
│   │   ├── turn_context.py   # Immutable per-turn execution context
│   │   ├── memory.py         # Context window management, compaction, and sync jobs
│   │   ├── memory_vault.py   # Long-term semantic memory (ChromaDB)
│   │   ├── memory_guard.py   # Layered inbound memory threat scanning
│   │   ├── permissions.py    # Device permission gating engine
│   │   ├── mcp_client.py     # Model Context Protocol client
│   │   ├── integrations.py   # Direct integration runtime (SMTP, REST)
│   │   ├── rest_adapter.py   # Universal REST API adapter
│   │   ├── oauth.py          # Google OAuth2 flow management
│   │   ├── session_store.py  # Cross-session persistence (SQLite)
│   │   ├── runtime_events.py # Runtime event taxonomy
│   │   ├── runtime_read_models.py # Typed read models for TUI/gateway views
│   │   └── credential_vault.py  # Secure credential storage
│   │
│   ├── jobs/
│   │   └── queue.py          # Queue/worker primitives with retry/backoff
│   │
│   ├── og_engine/            # ObjectGraph (.og) parser & skill engine
│   │   ├── parser.py         # Full .og lexical parser (OGFile, OGNode, OGIndexEntry)
│   │   └── engine.py         # Skill indexing, search, and context resolution
│   │
│   ├── gateway/              # Typed control plane + orchestration
│   │   ├── control_plane.py  # Session protocol and gateway runtime interfaces
│   │   ├── fastapi_app.py    # HTTP routes and OpenAPI surface
│   │   ├── orchestrator.py   # Agent session factory + runtime wiring
│   │   └── workspace.py      # ~/.tokoboros/ directory and skill bootstrap
│   │
│   ├── integrations/         # Native platform integrations
│   │   ├── google/           # Gmail, Drive, Calendar
│   │   ├── notion/           # Notion pages & databases
│   │   └── slack/            # Slack messaging & workspace
│   │
│   ├── tools/                # LLM-callable tool definitions
│   │   ├── os_tools.py       # File I/O & terminal commands (permission-gated)
│   │   ├── og_tools.py       # search_skills, load_skill
│   │   ├── memory_tools.py   # Long-term memory tools
│   │   └── workflow_tools.py # Workflow control tools
│   │
│   └── tui/
│       └── app.py            # Textual operator console
│
├── src/tokoboros/default_skills/         # Built-in .og skill files
│   ├── general/                          # Everyday skills (email, QR codes, web apps)
│   ├── technical/                        # Developer skills (code review, debugging)
│   ├── google/                           # Google Workspace skills
│   ├── notion/                           # Notion integration skills
│   └── slack/                            # Slack integration skills
│
├── frontend/                              # TanStack Start web frontend
├── openapi/
│   └── tokoboros-openapi.json            # Generated gateway OpenAPI contract
├── docker-compose.yml                    # Gateway + frontend local stack
├── Dockerfile                            # Gateway service image
├── AGENTS.md                             # Contributor guidance for coding agents
├── ObjectGraph_Complete_Specification.md   # Full .og format specification
├── pyproject.toml                          # Project metadata & dependencies
├── CONTRIBUTING.md                         # Contribution guidelines
└── LICENSE                                 # MIT License
```

---

## The ObjectGraph (.og) Format

ObjectGraph is the core innovation behind Tokoboros. It is a **structured, queryable, agent-native file format** for AI agent skill files. It uses a familiar, Markdown-like syntax with added `::` semantic blocks, so skills stay human-readable and are low-effort to author and port.

### Why Not Markdown?

|                            | Markdown               | ObjectGraph (.og)                  |
| -------------------------- | ---------------------- | ---------------------------------- |
| **Reading Model**          | Full file every time   | 3-pass: Index → Dense → Full       |
| **Token Cost (per query)** | Whole file every time  | Only the node(s) needed¹           |
| **Query Routing**          | None (read everything) | `::index` routing table            |
| **Compression**            | None                   | `::dense` keyword blocks           |
| **Validation**             | Prompt-dependent       | `::assertion` blocks               |
| **Multi-Agent RBAC**       | None                   | `scope` attribute per node         |
| **Dependency Tracking**    | None                   | `::edges` with typed relationships |
| **Delta Loading**          | Full re-read           | `::changelog` diff entries         |

¹ On the bundled skill set, loading just the node a task needs costs substantially fewer skill-loading tokens than dumping every skill into context — reproducible via [`tests/test_og_benchmark.py`](tests/test_og_benchmark.py) (CI-enforced at ≥80% savings).

**How is this different from Anthropic Agent Skills or MCP?** Progressive disclosure on its own isn't unique — Anthropic Agent Skills load skill metadata first and the full `SKILL.md` only when it's relevant, and MCP can filter which tools are exposed. ObjectGraph goes _below the file level_: it routes and loads individual **nodes** within a skill, follows typed `::edges` (`->[:requires]`) to pull only the dependencies a task actually needs, and pairs this with agent-authored, self-improving skills and usage analytics. The bet is that context bloat is a **format** problem, not only a retrieval one.

### Example `.og` File

```
::meta
  title: Deployment Skill
  version: 1.0.0
  domain: deployment|devops
  scope: all

::index
  # node-id       | type    | scope  | conf | keywords
  install          | step    | all    | 0.99 | install, setup, uv
  deploy-prod      | step    | all    | 0.95 | deploy, production, release
  rollback         | step    | all    | 0.93 | rollback, revert, undo

::node[install]
->[:role=skill]
->[:keywords=install, setup, uv, python]
::dense
python3.11+|uv|sync|project-env|dependencies
::full
Ensure Python 3.11+ and uv are installed. Sync dependencies
and package metadata into the local project environment.
::steps
1. uv sync
2. uv run python --version
3. uv run tokoboros --help
::assertion
  trigger: after[install]
  check: command('uv run python -c "import typer"') exits 0
  on-fail: ->[:retry] install
  on-pass: ->[:proceed] deploy-prod
::edges
  ->[:precedes] deploy-prod
  ->[:requires] concept-virtualenv
```

For the complete specification, see [ObjectGraph_Complete_Specification.md](ObjectGraph_Complete_Specification.md).

---

## Configuration

### Workspace Structure

Tokoboros now resolves paths with `platformdirs` (config/data/state), while still importing from legacy `~/.tokoboros/` when needed.

Print your active paths at any time:

```bash
uv run tokoboros config --paths
```

Key path roles:

```
config_dir/
├── settings.toml              # Main resolved config (provider, agent, permissions, memory)
├── brain.og                   # Core agent identity & behavioral rules
└── device-permissions.og      # Filesystem & terminal access policy

data_dir/
├── skills/                    # User and installed .og skill files
└── chroma_vault/              # Semantic memory store

state_dir/
├── sessions.db                # Session/message persistence
├── tui_state.db               # Textual UI state
└── onboarding_state.json      # Setup progress
```

### Agent Configuration (`settings.toml`)

```toml
[provider]
kind = "anthropic"
model = "claude-haiku-4-5"
api_key_env = "ANTHROPIC_API_KEY"

[agent]
temperature = 0.0
max_tokens = 8192

[permissions]
tier = "guided"

[memory]
enable_session_history = true
enable_semantic_memory = true
```

### Device Permissions (`device-permissions.og`)

Control what the agent can access on your device:

```
::node[terminal]
->[:role=permission]
::config
enabled=true
allowed_commands=*
blocked_commands=rm -rf, format, shutdown, taskkill
timeout=120
```

---

## Roadmap

- [x] **Textual-First Runtime** — `tokoboros run` now launches the Textual operator console by default
- [x] **Gateway Control Plane** — Typed FastAPI surface for sessions, events, config, setup, and runtime observability
- [x] **OpenAPI + Frontend Contract** — Exported schema and generated TypeScript client integration
- [x] **Immutable TurnContext** — Per-turn prompt/context freezing to avoid shared mutable prompt state
- [x] **Security Audit Command + CI Integration** — Built-in posture checks wired into CI workflows
- [ ] **Queue Durability Upgrade** — Move from in-memory queue backend to persisted backend
- [ ] **Container-Grade Sandbox** — Production isolation backend with stronger boundary guarantees
- [ ] **Gateway Auth and Pairing** — Add auth tokens/device pairing for remote-safe multi-surface operation
- [ ] **Expanded Scheduler and Automations** — First-class scheduled job orchestration and delivery
- [ ] **Skill Compiler/Lint Pipeline** — Typed skill validation, diagnostics, and publish workflow

---

## Contributing

We welcome contributions! Please read our [Contributing Guide](CONTRIBUTING.md) for details on:

- Development setup
- Code style and commit conventions
- How to write `.og` skill files
- Pull request process

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">
  <sub>Built with 🧬 by the Tokoboros Project</sub>
</div>
