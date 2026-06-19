# Hachi — Product Quality Audit (2026-06-18)

A complete product quality audit by 5 parallel QA agents (Black-Box, White-Box, UX/Product, Security/Reliability, Performance/Scalability), consolidated into a Product Owner analysis, engineering execution plan, verification strategy, and release recommendation.

**Headline:** Backend engine is mature; the web product is a high-fidelity prototype that fakes things a *trust* product must not fake. CI is red and CLI config crashes. None of it is deep — it's a focused ~2-sprint fix program.

- **Overall product health: 61/100**
- **Release readiness: 34/100 full product (NO-GO) · 68/100 engine+CLI (conditional GO after Sprint 1)**
- **153 raw findings → ~118 distinct: 10 Critical · 44 High · 66 Medium · 33 Low**

## Read in this order

| # | Doc | Phase |
|---|---|---|
| 00 | [Master Report](00-MASTER-REPORT.md) — exec summary, scores, findings by severity & category, gap analysis, risk matrix, prioritized fix list | 2 + 6 |
| 06 | [Product Owner Decisions](06-product-owner-decisions.md) — triage with rationale | 3 |
| 07 | [Execution Plan](07-execution-plan.md) — epics, fully-specified tasks, parallel agent assignment, dependency graph, sprint roadmap | 4 |
| 08 | [Verification Strategy](08-verification-strategy.md) — per-fix test/regression/integration/perf/security/UAT | 5 |
| 09 | [Release Readiness](09-release-readiness.md) — go/no-go gates and sequence | 6 |

## Raw QA agent reports (full evidence, file:line)

| # | Report | Findings |
|---|---|---|
| 01 | [Black-Box Testing](01-black-box.md) | 36 (2C/10H/15M/9L) |
| 02 | [White-Box Testing](02-white-box.md) | 35 (1C/7H/18M/9L) |
| 03 | [UX & Product](03-ux-product.md) | 26 (3C/9H/10M/4L) |
| 04 | [Security & Reliability](04-security-reliability.md) | 30 (2C/9H/12M/7L) |
| 05 | [Performance & Scalability](05-performance-scalability.md) | 26 (2C/9H/11M/4L) |

## The 10 critical blockers

1. `BB-001` `hachi config-get` crashes (CLI)
2. `BB-002` `hachi config-set` crashes on safety/budget keys (CLI)
3. `WB-001` `frontend-contract` CI job hard-fails every push (CI)
4. `UX-001/BB-006` fake API-key "validated" (trust)
5. `SEC-001` pack ZIP zip-slip (security)
6. `SEC-005` MCP server arbitrary exec at boot (security)
7. `UX-018/BB-004` silent fixture fallback shows fake data (trust)
8. `PERF-001` loaded skills re-billed every turn (cost + `.og` moat)
9. `PERF-025` unvirtualized chat transcript (perf)
10. `UX-009/UX-023` no governance status bar + no accessibility foundation (UX/a11y)

## Notes for implementing agents

- **Land [T2.1 (fix CI)](07-execution-plan.md) first** — until then "green" is meaningless.
- **`src/hachi/tui/**` is off-limits** — a concurrent session owns it. TUI-tagged findings are handed off separately.
- `docs/strategy/01-architecture-review.md` is **stale** — its top debt items are already fixed in the working tree. Don't action it (see [T10.3](07-execution-plan.md)).
- The verified anchor findings (CI job, zip-slip, CLI crashes) were reproduced first-hand by the audit lead; the rest are evidence-backed in reports 01–05.
