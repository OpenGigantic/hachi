<div align="center">

# 🐍 Tokoboros

**The Autonomous Agent Engine Powered by ObjectGraph (.og)**

*A multi-agent orchestration framework that cuts skill-loading token waste with a structured, queryable, agent-native file format — agents load only the skill nodes a task needs instead of dumping entire skill files into context.*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-purple.svg)](https://docs.astral.sh/ruff/)

</div>

---

## What is Tokoboros?

Tokoboros is an autonomous AI agent framework built from the ground up to solve the three biggest problems in modern agentic AI:

| Problem | How Tokoboros Solves It |
|---|---|
| **Token Waste** | ObjectGraph (.og) files enable partial, query-aware loading — agents read a compact `::index` routing table and pull only the skill nodes a task needs, instead of loading whole skill files |
| **Context Pollution** | A delegation architecture spawns isolated sub-agents, keeping the orchestrator's context window pristine |
| **Cost Explosion** | Native Anthropic prompt caching + structured file indexing means agents pay for the skill nodes they use, not whole files — on the bundled skills this is ~80–97% fewer skill-loading tokens, reproducible via `tests/test_og_benchmark.py` (CI enforces ≥80%) |

Unlike traditional agent frameworks that dump everything into the system prompt and pray, Tokoboros treats skill files as **queryable databases** and agents as **specialized workers** — each with their own clean context window, strict behavioral constraints, and deterministic validation.

---

## Features

- 🧠 **ObjectGraph (.og) Engine** — A structured, agent-native file format with 20+ semantic tags for structured, deterministic skill execution
- 🔀 **Multi-Agent Delegation** — Fire-and-forget sub-agents with isolated context windows for complex, multi-step tasks
- 💰 **Prompt Caching** — Native Anthropic cache breakpoints on system prompts and tool schemas to avoid re-paying for stable input tokens
- 🔐 **Permission Gating** — Declarative `device-permissions.og` manifest controlling filesystem, terminal, and MCP access
- 🔌 **Native Integrations** — Google Workspace (Gmail, Drive, Calendar), Notion, Slack — all via OAuth
- 🌐 **Universal REST Adapter** — Define any REST API in a `.og` file, and Tokoboros auto-generates the tool
- 🧩 **MCP Support** — Model Context Protocol servers for filesystem and external tools
- 📊 **Skill Lifecycle** — Usage tracking, stale detection, and analytics for all `.og` skills
- 🧬 **Long-Term Memory** — Semantic search vault with async prefetch and frozen session snapshots

---

## Quick Start

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

### Installation

```bash
# Clone the repository
git clone https://github.com/OpenGigantic/tokoboros.git
cd tokoboros

# Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install Tokoboros
pip install -e .
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
tokoboros run
```

On first run, Tokoboros will:
1. Create the `~/.tokoboros/` workspace directory
2. Launch the **Device Permissions Wizard** to configure access tiers
3. Copy default skill files to `~/.tokoboros/skills/`
4. Boot the agent and enter the interactive terminal

---

## Architecture

```
tokoboros/
├── src/tokoboros/
│   ├── cli/                  # CLI entry point (Typer)
│   │   └── main.py           # Boot sequence, REPL, and command handlers
│   │
│   ├── core/                 # Core engine modules
│   │   ├── llm.py            # LLM abstraction layer (Anthropic, with prompt caching)
│   │   ├── loop.py           # Autonomous ReAct execution loop
│   │   ├── memory.py         # Context window management & compaction
│   │   ├── memory_vault.py   # Long-term semantic memory (ChromaDB)
│   │   ├── permissions.py    # Device permission gating engine
│   │   ├── mcp_client.py     # Model Context Protocol client
│   │   ├── integrations.py   # Direct integration runtime (SMTP, REST)
│   │   ├── rest_adapter.py   # Universal REST API adapter
│   │   ├── oauth.py          # Google OAuth2 flow management
│   │   ├── session_store.py  # Cross-session persistence (SQLite)
│   │   └── credential_vault.py  # Secure credential storage
│   │
│   ├── og_engine/            # ObjectGraph (.og) parser & skill engine
│   │   ├── parser.py         # Full .og lexical parser (OGFile, OGNode, OGIndexEntry)
│   │   └── engine.py         # Skill indexing, search, and context resolution
│   │
│   ├── gateway/              # Session orchestration
│   │   ├── orchestrator.py   # Agent session factory & memory architecture
│   │   └── workspace.py      # ~/.tokoboros/ directory management
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
│   └── default_skills/       # Built-in .og skill files
│       ├── general/          # Everyday skills (email, QR codes, web apps)
│       ├── technical/        # Developer skills (code review, debugging)
│       ├── google/           # Google Workspace skills
│       ├── notion/           # Notion integration skills
│       └── slack/            # Slack integration skills
│
├── ObjectGraph_Complete_Specification.md   # Full .og format specification
├── pyproject.toml                        # Project metadata & dependencies
├── CONTRIBUTING.md                       # Contribution guidelines
└── LICENSE                               # MIT License
```

---

## The ObjectGraph (.og) Format

ObjectGraph is the core innovation behind Tokoboros. It is a **structured, queryable, agent-native file format** for AI agent skill files. The core bet: **context bloat is a format problem**, so the fix belongs in the file format itself.

`.og` uses a familiar, Markdown-like syntax with added `::` semantic blocks, so skills stay human-readable and are low-effort to port. The differentiators that make it a moat:

- **Node-level (sub-file) progressive disclosure** — an agent reads a skill's `::index` routing table (~30 tokens), then pulls only the `::dense` summaries and `::full` bodies for the nodes a task actually needs. Disclosure happens *below* the file boundary, not just at it.
- **Typed `requires` dependency edges** — `::edges` declare typed relationships (`requires`, `precedes`, …) so the engine can traverse and pull in prerequisite nodes deterministically instead of re-reading whole files.
- **Agent-authored, self-improving skills** — agents write and refine their own `.og` skills, and per-skill usage analytics drive a feedback loop that prunes dead weight and sharpens what earns its keep.

**Honest competitive context:** progressive disclosure itself is not unique — Anthropic Agent Skills do file-level metadata → body → bundled-files disclosure, and MCP filters which tools an agent sees. ObjectGraph's edge is going *below* the file level with typed, dependency-aware traversal plus a self-improving skill loop, rather than treating each skill file as one all-or-nothing unit.

### Why Not Markdown?

| | Markdown | ObjectGraph (.og) |
|---|---|---|
| **Reading Model** | Full file every time | 3-pass: Index → Dense → Full |
| **Token Cost (per query)** | Whole file every read | Only the nodes a task needs — ~80–97% fewer skill-loading tokens on the bundled skills, reproducible via `tests/test_og_benchmark.py` (CI enforces ≥80%) |
| **Query Routing** | None (read everything) | `::index` routing table |
| **Compression** | None | `::dense` keyword blocks |
| **Validation** | Prompt-dependent | `::assertion` blocks |
| **Multi-Agent RBAC** | None | `scope` attribute per node |
| **Dependency Tracking** | None | `::edges` with typed relationships |
| **Delta Loading** | Full re-read | `::changelog` diff entries |

### Example `.og` File

```
::meta
  title: Deployment Skill
  version: 1.0.0
  domain: deployment|devops
  scope: all

::index
  # node-id       | type    | scope  | conf | keywords
  install          | step    | all    | 0.99 | install, setup, pip
  deploy-prod      | step    | all    | 0.95 | deploy, production, release
  rollback         | step    | all    | 0.93 | rollback, revert, undo

::node[install]
->[:role=skill]
->[:keywords=install, setup, pip, python]
::dense
python3.11+|pip|virtualenv|requirements.txt|venv
::full
Ensure Python 3.11+ is installed. Create a virtual environment
and install dependencies from requirements.txt.
::steps
1. python -m venv .venv
2. source .venv/bin/activate
3. pip install -r requirements.txt
::assertion
  trigger: after[install]
  check: command('pip list') contains 'requests'
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

On first boot, Tokoboros creates `~/.tokoboros/` with the following layout:

```
~/.tokoboros/
├── brain.og                  # Core agent identity & behavioral rules
├── tokoboros.json             # Agent configuration (model, max_tokens)
├── device-permissions.og      # Filesystem & terminal access control
├── skills/                    # User's .og skill files
│   ├── general/
│   ├── technical/
│   └── google/
└── sessions/                  # Persistent session data
```

### Agent Configuration (`tokoboros.json`)

```json
{
    "agent": {
        "model": "claude-haiku-4-5",
        "temperature": 0.0,
        "max_tokens": 8192
    }
}
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

- [ ] **Multi-Agent Delegation** — `delegate_task` tool for spawning fire-and-forget sub-agents
- [ ] **Assertion Engine** — Deterministic `::assertion` execution in the ReAct loop
- [ ] **Edge Traversal** — Automatic `::edges` dependency resolution during skill loading
- [ ] **Delta Loading** — `::changelog`-based incremental skill updates
- [ ] **Agent Roles (RBAC)** — Scope-filtered `.og` views for orchestrator vs worker agents
- [ ] **Web UI** — Browser-based interface for non-terminal users
- [ ] **Plugin SDK** — Third-party tool development kit

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
