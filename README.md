<div align="center">

# Hachi

**The governed personal agent.**

Every capability it has — including the ones it teaches itself — is a typed, versioned,
permission-scoped `.og` file you can diff, lint and audit.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![code style: ruff](https://img.shields.io/badge/code%20style-ruff-purple.svg)](https://docs.astral.sh/ruff/)
[![.og token savings: 97.6%](https://img.shields.io/badge/.og%20token%20savings-97.6%25-brightgreen)](docs/reference/benchmark.md)

</div>

> **Pre-release.** Hachi is not published to PyPI yet and this repository is not public, so
> `pipx install hachi-og` and the `curl | bash` one-liner **do not work today**. Install from
> source below. The first public beta is `v0.1.0`; a `CHANGELOG.md` and a signed release will
> accompany it.

---

## What it is

Hachi is a local-first autonomous agent engine. It runs a ReAct loop against a model of your
choice, uses tools to do real work, remembers across sessions, and **enforces its own limits in
code** rather than in a system prompt.

Three ideas, in order of importance:

**1 · Governance is code, not vibes.** Every shell command, filesystem path, outbound URL and
memory write passes a guard that exists in source:
`core/permissions.py` (immutable, fail-safe gate), `core/net_guard.py` (SSRF),
`core/memory_guard.py` (prompt injection), `core/untrusted_content.py` (channel input).
Spend is capped **between LLM calls** by `core/loop.py`, which projects the next call's
worst-case cost before making it.

**2 · Capabilities are data.** A skill is not a blob of prompt text — it is a `.og` file: a
typed graph with a routing index, scopes, edges and executable assertions. That structure is
*why* governance is possible: you can lint it, diff it, version it, and check it before it runs.
The format has a [normative spec](spec/og-spec-v1.0.md), a [conformance suite](spec/conformance/),
and a [paper](https://arxiv.org/abs/2604.27820).

**3 · You own the box and the model.** Self-hosted, single-tenant, no telemetry. Anthropic,
OpenAI-compatible, OpenRouter, Groq, DeepSeek, Gemini, or fully local Ollama. It also speaks
the OpenAI-compatible protocol, so other tools can point at it.

---

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). `git` is only needed for the source
install.

```bash
git clone https://github.com/OpenGigantic/hachi && cd hachi
uv sync
uv run hachi quickstart      # pick a provider, validate your key, choose a safety tier
uv run hachi                 # start chatting (TUI)
```

`quickstart` checks your provider key **before** the first message instead of failing
mid-conversation. Prefer a plain shell? `uv run hachi run --once "what changed today?"`.

**Started without a key?** `uv run hachi doctor` tells you what is missing; `hachi models`
degrades to the configured model instead of crashing.

---

## What's in the box

| | |
|---|---|
| **Governance** | Permission tiers (`sandbox` / `guided` / `unrestricted`), call-time path and shell gating, SSRF guard, per-session **and** per-turn cost caps, daily spend ledger, rate limits |
| **Skills** | 31 bundled `.og` skills · BM25 + optional embedding retrieval over the node index · `save_skill` bumps semver and appends a changelog instead of overwriting · lint + `::assertion` checks before execution |
| **Memory** | ChromaDB vault with separate `core_memory` and bounded `user_profile` collections · episodic recall · knowledge graph · nightly append-only gardener that proposes rather than overwrites |
| **Surfaces** | Textual TUI ("Cockpit") · FastAPI gateway with SSE · React web UI · Telegram, Slack, Discord, WhatsApp · OpenAI-compatible `/v1/chat/completions` |
| **Tools** | 62 built-in: files, shell, browser automation, vision, voice, Google Workspace, Notion, Slack, MCP servers, scheduling, sub-agent delegation, and a universal REST adapter driven by `.og` |
| **Engineering** | 4,300+ tests, import-linter layer contracts, cross-platform CI (Linux/macOS/Windows), `preflight.py` gate, opt-in OpenTelemetry |

Full list: **[docs/guides/](docs/guides/)** — configuration, memory, cookbook, git sync

---

## The `.og` format, briefly

An agent does not read a document linearly — it retrieves. A `.og` file is built for that: a
routing index you can read for ~30 tokens, then only the node a task needs.

```
::meta
  title: Software Engineer Sub-Agent
  version: 1.0.0

::index
  # node-id            | type    | scope | conf | keywords
  identity              | concept | all   | 1.0  | persona, engineer
  execution-protocol    | concept | all   | 1.0  | rules, constraints

::node[execution-protocol]
->[:role=rule]
::dense
Never deviate from the assigned task. Read first, then act.
::steps
1. Read the files the task references
2. Execute with the minimum number of tool calls
::assertion
  trigger: after[write_file]
  check: file_exists == true
  on-fail: ->[:retry limit=1] current_step
::end
```

Measured on the bundled 31-file library: **97.6% fewer tokens** than dumping every skill into
the prompt, **given correct routing** — retrieval quality is benchmarked separately (P@5 = 1.00
over 54 nodes). Below ~30k tokens of total library content a cached dump-all is competitive;
`.og` is built for libraries that outgrow a single context window. Reproduce it yourself:
`uv run hachi skill benchmark`.

Spec: [`spec/og-spec-v1.0.md`](spec/og-spec-v1.0.md) · Conformance: [`spec/conformance/`](spec/conformance/) ·
Paper: [arXiv:2604.27820](https://arxiv.org/abs/2604.27820) *(first-party — our own paper)*

---

## Security model

- **Local-first, single-tenant.** The gateway binds loopback and requires a bearer token;
  `HACHI_GATEWAY_NO_AUTH=1` refuses to start on a non-loopback bind.
- **Fail-safe by default.** A missing `device-permissions.og` drops to the `sandbox` tier —
  it never fails open.
- **Credentials** live in the OS keyring; resolved lazily at call time, never at boot.
- **`security-audit`** inspects runtime posture and is a CI gate.

> **Known gap:** CI does not yet scan the *tree* for committed secrets — `security-audit`
> inspects the environment and resolved config only. Tracked, not yet wired.

Vulnerability reporting: [SECURITY.md](SECURITY.md).

---

## Documentation

| Start here | Deeper |
|---|---|
| [Configuration](docs/guides/configuration.md) — every env var and `settings.toml` key | [Architecture](ARCHITECTURE.md) — components, data flow, enforced layering |
| [Memory](docs/guides/memory.md) · [Cookbook](docs/guides/cookbook.md) | [Benchmark methodology](docs/reference/benchmark.md) |
| [`.og` format](docs/reference/objectgraph-spec.md) | [Decisions](docs/DECISIONS.md) · [Bug bank](docs/bug-bank.md) — 90+ regressions and what they taught |

`docs/bug-bank.md` and `docs/DECISIONS.md` are the two files that best explain how this project
thinks. They are kept current as a matter of policy.

---

## Contributing

The gate, in one command:

```bash
uv run python scripts/preflight.py          # ruff, mypy, skill lint, eval, full pytest, frontend
uv run python scripts/preflight.py --quick  # fast inner loop
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) first — it covers the first fifteen minutes, the
`import-linter` layering rail, and how `.og` contributions are linted. This project is developed
with AI agents; `CLAUDE.md`, `AGENTS.md` and `.claude/` are shipped on purpose, not by accident.

The lowest-friction contribution is a new `.og` skill: ~30 lines, no Python, linted in CI.

## License

MIT — see [LICENSE](LICENSE). The `.og` specification and the paper are separate works
(CC BY 4.0).
