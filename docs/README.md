# Hachi Documentation

The map of `docs/`. Canonical project files (`README.md`, `ARCHITECTURE.md`, `CLAUDE.md`,
`AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`) stay at the repository root by
convention; everything else lives here.

**Naming convention:** canonical running logs keep UPPERCASE names (`DECISIONS.md`,
`AUDIT.md`, `bug-bank.md`); topic guides and reference docs use kebab-case.

## Guides — how to use & operate Hachi

- [guides/configuration.md](guides/configuration.md) — `settings.toml` keys, the authority model, every environment variable, Windows secret-setting, and the background daemon
- [guides/cookbook.md](guides/cookbook.md) — copy-paste recipes for common tasks
- [guides/memory.md](guides/memory.md) — how Hachi remembers across turns and sessions
- [guides/git-sync.md](guides/git-sync.md) — keep the memory graph in sync across devices
- [guides/releasing.md](guides/releasing.md) — cut and publish a release

## Reference — specifications & measured results

- [reference/objectgraph-spec.md](reference/objectgraph-spec.md) — the complete `.og` format whitepaper (rationale, format reference, transpiler & reader guide)
- [reference/benchmark.md](reference/benchmark.md) — `.og` token-efficiency benchmark + methodology
- [reference/og-security.md](reference/og-security.md) — `.og` tiered trust model

> The machine-checked **normative** format spec and its conformance fixtures live in
> [`spec/`](../spec/) at the repo root (`spec/og-spec-v1.0.md` + `spec/conformance/`).

## Decisions & review

- [DECISIONS.md](DECISIONS.md) — the running decision log (default home for decisions; newest at the bottom)
- [architecture/adr/README.md](architecture/adr/README.md) — numbered ADRs for major architectural decisions
- [AUDIT.md](AUDIT.md) — the architecture review board (living document)
- [bug-bank.md](bug-bank.md) — regression autopsies; check before debugging

## Architecture & strategy

- [architecture/README.md](architecture/README.md) — design docs (01–29) and ADRs
- [strategy/00-index.md](strategy/00-index.md) — product strategy, evaluation, and roadmap (00–09)
- [design/](design/) — TUI and web UI design specs
- [web/backend-api-gaps.md](web/backend-api-gaps.md) — frontend ↔ gateway API notes

## Brand & history

- [brand/](brand/) — marks, app icon, and design tokens (`hachi-token.css`)
- [history/](history/) — archived pre-rename documents (roadmap + TUI product brief)
