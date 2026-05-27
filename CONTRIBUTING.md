# Contributing to Tokoboros

Thank you for your interest in contributing to Tokoboros! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) or pip
- An Anthropic API key (for running the agent)

### Getting Started

```bash
# Clone the repository
git clone https://github.com/AetherMind-Labs/tokoboros.git
cd tokoboros

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

### Linting & Formatting

```bash
ruff check src/
ruff format src/
mypy src/tokoboros/
```

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

### Code Style

- Follow PEP 8 with a 100-character line limit (enforced by Ruff).
- All public functions and classes **must** have docstrings.
- Use type hints on all function signatures.
- Keep modules focused — one responsibility per file.

### `.og` File Contributions

If you are contributing new `.og` skill files:

1. Use the full ObjectGraph tag specification (`::meta`, `::index`, `::dense`, `::full`, `::steps`, `::assertion`, etc.).
2. Every skill node must have `keywords` for search indexing.
3. Include `::assertion` blocks where applicable for self-validating execution.
4. Follow the [GraphMark Specification](GraphMark_Complete_Specification.md) for tag reference.

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests.
- Include reproduction steps, expected behavior, and actual behavior.
- For security vulnerabilities, email the maintainers directly — do not open a public issue.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
