# Hachi — Product Quality Audit v2 / Re-Verification (2026-06-18)

A second-pass audit by 5 parallel QA agents (Black-Box, White-Box, UX/Product, Security/Reliability, Performance/Scalability), each tasked to **re-verify every finding from the [v1 audit](../2026-06-18/)** against the current working tree and to hunt new issues in the surfaces that changed since (hard rename, new `src/hachi/client/`, new gateway endpoints, full `frontend/` SPA, deleted old TUI). Consolidated into a Product Owner analysis, execution plan, verification strategy, and release recommendation.

**Headline:** Between v1 and v2, **~0 of the critical issues were actually fixed — they were dressed up.** The CLI config command was rewritten and still crashes; a new unit test mocks a non-existent config shape so it passes green over the crash; the frontend-contract CI job pointed at a frontend that doesn't exist — and was being hot-patched *during this audit* yet still can't go green because the SPA is uncommitted (see the "audit-window note" in [00](00-MASTER-REPORT.md)). The engine remains mature and the fixes remain shallow — but the **verification system itself cannot currently be trusted**, which for a *trust* product is the most important finding.

- **Overall product health: ~60/100** (v1: 61 — flat, because remediation was cosmetic)
- **Release readiness: 32/100 full product (NO-GO) · 64/100 engine+CLI (conditional GO after Sprint 1)**
- **Re-verified ~149 prior findings: 1 cleanly FIXED · ~10 PARTIAL · ~135 STILL-OPEN · +13 new findings**

## Read in this order

| # | Doc | Phase |
|---|---|---|
| 00 | [Master Report](00-MASTER-REPORT.md) — exec summary, verification delta, findings by severity & category, gap analysis, risk matrix, prioritized fix list, the verification-integrity meta-finding | 2 + 6 |
| 06 | [Product Owner Decisions](06-product-owner-decisions.md) — disposition (NOW/BEFORE-WEB/NEXT/BACKLOG/RISK-ACCEPT/REJECT) with rationale | 3 |
| 07 | [Execution Plan](07-execution-plan.md) — epics, fully-specified tasks, parallel agent assignment, dependency graph, sprint roadmap | 4 |
| 08 | [Verification Strategy](08-verification-strategy.md) — per-fix test/regression/integration/perf/security/UAT | 5 |
| 09 | [Release Readiness](09-release-readiness.md) — go/no-go gates and sequence | 6 |

## Raw QA agent reports (full evidence, file:line)

| # | Report |
|---|---|
| 01 | [Black-Box Testing](01-black-box.md) |
| 02 | [White-Box Testing](02-white-box.md) |
| 03 | [UX & Product](03-ux-product.md) |
| 04 | [Security & Reliability](04-security-reliability.md) |
| 05 | [Performance & Scalability](05-performance-scalability.md) |

## The 11 must-fix items (Sprint 1 / P0)

1. `WB-001` make `frontend-contract` CI green on a clean checkout — commit the SPA + a current `schema.d.ts` (scripts were fixed mid-audit; the SPA is still untracked) — **do first**
2. `WB-N07` delete/repair the false-green config test; make a test run the real command
3. `BB-001/002/WB-N06` `hachi config-get/set` crash on safety/budget keys (reproduced live)
4. `SEC-001` community-pack ZIP zip-slip
5. `SEC-005` MCP server arbitrary exec at boot
6. `UX-001/BB-006` fake API-key "validated"
7. `UX-018/BB-004` silent fixture fallback (fake healthy data)
8. `PERF-001` re-billed message history (cost + `.og` moat)
9. `PERF-025` unvirtualized chat transcript
10. `UX-009` no governance status bar
11. `UX-N01/WB-009` web spend double-count

## Notes for implementing agents

- **Land the CI + test-honesty work (WB-001, WB-N07) before anything else** — until then "green" is meaningless and we will repeat the v1→v2 "looks fixed" failure.
- **`src/hachi/tui/cockpit/**` is off-limits** — a concurrent session owns it. Excluded from scope/scoring.
- `docs/strategy/01-architecture-review.md` is **stale** — its top debt items are already fixed. Do not action it.
- The new `src/hachi/client/**` is clean and well-built; the new gateway write-routes carry bearer auth + Pydantic validation. Build on them — the gaps are in the SPA wiring and the supply-chain/perf hotspots, not in these.
- Every "fixed" claim must be backed by a test that reproduces the original failure and runs in CI.
