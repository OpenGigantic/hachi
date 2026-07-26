# Contributing to Hachi

Thank you for your interest in contributing to Hachi! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- An Anthropic API key (for running the agent)

### Getting Started

```bash
# Clone the repository
git clone https://github.com/OpenGigantic/hachi.git
cd hachi

# Create/update local environment and include development dependencies
uv sync --extra dev
```

### Managing Dependencies

```bash
# Add a runtime dependency
uv add <package>

# Add a development-only dependency
uv add --dev <package>
```

### Running Tests

```bash
uv run pytest
```

### Linting & Formatting

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src --ignore-missing-imports
```

### Preflight — the one-command gate

Before opening a PR, run the full local gate (it mirrors CI):

```bash
uv run python scripts/preflight.py          # full
uv run python scripts/preflight.py --quick  # fast inner loop (skips full pytest + FE build)
```

It must pass clean. The *enforced* guardrails (Claude Code hooks: push-lock,
secret-scan, auto-format, red-tests-block-stop) live in `.claude/`; see `CLAUDE.md`
and `.claude/hooks/README.md`.

**The Rails** (static-analysis layer): `import-linter` enforces the module layering
([ARCHITECTURE.md](ARCHITECTURE.md#enforced-layering-the-architectural-rail)) and is
**blocking** — if your change adds an import across a forbidden boundary (e.g. the engine
importing the `gateway`), the gate fails. Either route the call through an existing seam
or, if a genuinely new boundary crossing is justified, add a commented `ignore_imports`
entry in `pyproject.toml` `[tool.importlinter]` and say why in the PR. `deptry` (unused/
missing deps), `vulture` (dead code), and the ruff complexity counts are **advisory** for
now — fix what you can; they don't block.

**Architecture decisions.** Settled decisions go in [`DECISIONS.md`](docs/DECISIONS.md); a major
architectural choice that warrants alternatives analysis and a long-lived reference gets a
numbered ADR under [`docs/architecture/adr/`](docs/architecture/adr/) — copy `0000-template.md`
and add it to the index (the advisory `adr-lint` check verifies both). See the
[ADR README](docs/architecture/adr/README.md) for the two-tier convention.

## Project Structure

Please refer to the [Architecture section](README.md#architecture) in the README for a detailed breakdown of the codebase.

## Contribution Guidelines

### Branches

- `main` — Stable, production-ready code
- `dev` — Active development branch
- Feature branches: `feat/<feature-name>`
- Bug fixes: `fix/<bug-description>`

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add delegate_task tool for sub-agent spawning
fix: resolve output loop in execution engine
docs: update README with architecture diagram
refactor: migrate parser to OGFile container model
```

### Pull Request Process

1. Fork the repository and create your branch from `dev`.
2. Write tests for any new functionality.
3. Ensure all tests pass and linting is clean.
4. Update documentation if you changed any public APIs.
5. Open a pull request with a clear description of the changes.
6. If a file you touched now has zero mypy errors, add it to `mypy-strict-gate.ini`.

### Code Style

- Follow PEP 8 with a 100-character line limit (enforced by Ruff).
- All public functions and classes **must** have docstrings.
- Use type hints on all function signatures.
- Use `pathlib.Path` for filesystem paths, never raw strings.
- Keep modules focused — one responsibility per file.

### `.og` File Contributions

If you are contributing new `.og` skill files:

1. Use the full ObjectGraph tag specification (`::meta`, `::index`, `::dense`, `::full`, `::steps`, `::assertion`, etc.).
2. Every skill node must have `keywords` for search indexing.
3. Include `::assertion` blocks where applicable for self-validating execution.
4. Follow the [ObjectGraph Specification](docs/reference/objectgraph-spec.md) for tag reference.

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests.
- Include reproduction steps, expected behavior, and actual behavior.
- For security vulnerabilities, email the maintainers directly — do not open a public issue.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
