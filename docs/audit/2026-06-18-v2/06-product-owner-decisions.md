# Hachi — Product Owner Decisions (Audit v2)

**Date:** 2026-06-18 · **Owner:** Product Owner
**Input:** [00 — Master Report](00-MASTER-REPORT.md) + sub-reports 01–05.
**Disposition codes:** `NOW` = fix immediately (Sprint 1 / P0) · `BEFORE-WEB` = fix before any web-UI release (Sprint 2 / P1) · `NEXT` = next sprint (Sprint 3 / P2) · `BACKLOG` = later (Sprint 4 / P3) · `RISK-ACCEPT` = consciously not fixing for v1, documented · `REJECT` = will not do.

---

## 0. The overriding decision

**Verification integrity is the #1 priority, above every individual bug.** This audit exists only because a prior remediation was reported done and was not — and the tooling that should have caught that (CI, a unit test, frontend tests) was itself broken or fabricated. No engineering work in this plan is considered "done" until a test reproduces the original failure and that test runs in a CI that is green for real.

**Rationale:** Hachi sells trust. A team that cannot trust its own "green" cannot ship a trust product. Fixing this is cheap (one CI job + one honest test harness) and unblocks confidence in everything else. It is Sprint 1, Task 1.

---

## 1. Decisions by finding group

### NOW — Sprint 1 / P0 (fix immediately)

| Finding(s) | Decision | Rationale |
|---|---|---|
| WB-001 (CI broken) + WB-N07 (false-green test) + make lint/mypy blocking | **NOW** | Restores the ability to know if anything works. Everything else depends on it. Do first. |
| BB-001/002/WB-N06 (CLI config crash) | **NOW** | A documented, advertised core command crashes 100% of the time on the exact safety/budget keys the product is about. Tiny fix (wrong attribute paths). Must ship with a test that runs the real command. |
| SEC-001 (pack ZIP zip-slip) | **NOW** | Arbitrary file write from a web-installable artifact. Small, contained fix (mirror the tar hardening). |
| SEC-005 (MCP arbitrary exec at boot) | **NOW** | Web-installable config → process spawn with no tier gate. Highest-severity supply-chain path; must be consent-gated before any pack feature is promoted. |
| UX-001/BB-006 (fake "validated" key) | **NOW** | Pure trust lie; the real validator already exists one file away. Near-zero cost to fix. |
| UX-018/BB-004 (silent fixture fallback) | **NOW** | Showing fabricated healthy data to a "burned by OpenClaw" user is the single most brand-damaging behavior in the product. |
| PERF-001 (re-billed history / moat) | **NOW** | Directly inflates user spend and defeats the stated `.og` moat. Unchanged since v1. Medium effort, high payoff. |
| PERF-025/026 (unvirtualized transcript) | **NOW** | Long sessions lock the UI; this is the primary interaction surface. |
| UX-009 (no governance status bar) + UX-N01/WB-009 (spend double-count) | **NOW** | The product's whole proposition is *visible control*. The one number that proves it (spend) is both invisible and wrong. Fix together. |

### BEFORE-WEB — Sprint 2 / P1 (must be true before a web-UI release)

| Finding(s) | Decision | Rationale |
|---|---|---|
| BB-N02/WB-007/UX-006 (in-context leash no-op) | **BEFORE-WEB** (lead candidate to pull into P0) | The leash is the product. The inline prompt users actually hit must perform a real approval with countdown + decline reason. The standalone screen already POSTs, so the pattern exists. |
| BB-N04 (faked memory writes) | **BEFORE-WEB** | Endpoints already exist (`/memory/facts`); the UI just isn't calling them and is faking success. Cheap, high-trust. |
| WB-N03 (wrong-session memory routing) | **BEFORE-WEB** | Correctness bug: facts can leak across sessions. Must fix before multi-session web use. |
| BB-N03 (fabricated auto-decline countdown) | **BEFORE-WEB** | A displayed safety promise that doesn't exist. Fold into the leash work. |
| UX-017/BB-005 (simulated /run) | **BEFORE-WEB** | Either wire to real SSE or remove the screen — do not ship a simulation labeled "live." |
| UX-002/008 (spend cap settable + provider choice) | **BEFORE-WEB** | The #1 table-stakes capability (anti-bill-shock) and the core lock-in-escape argument. |
| SEC-002/003/004/007 (injection framing, SSRF ×2, pack signing) | **BEFORE-WEB** | Network/egress + supply-chain exposure unacceptable for an autonomous agent marketed on safety. |
| SEC-N01/SEC-013 (NO_AUTH blast radius / host gate) | **BEFORE-WEB** | The kill-switch now disables write routes too; must be loopback-gated. |
| REL-001 + PERF-N01/011 (sync approval wait; blocking async handlers) | **BEFORE-WEB** | Gateway stability under concurrent load; both are mechanical. |
| BB-007/008/009/011 (accessibility core) | **BEFORE-WEB** | Dev audience + legal exposure; focus-ring foundation already landed, finish the job. |
| WB-013 (silent except / fake uuid in fts5 provider) | **BEFORE-WEB** | Data-loss masking; finish the partial fix. |
| WB-004 + WB-002/003/005 + SEC-011 (compose/Dockerfile, release artifacts, dep-CVE/SAST) | **BEFORE-WEB** | Distribution + supply-chain hygiene for a self-host product. |
| PERF-019/015/012/022 (embedding pre-warm, store concurrency, /memory pagination, CLI imports) | **BEFORE-WEB** | Scale/latency hotspots; all bounded fixes. |

### NEXT — Sprint 3 / P2

WB-019 (durable func-jobs) · WB-N01 (`client/models.py` encoding) · WB-011 (mock scripts in live store) · PERF-006 (event deque) · PERF-N02 (`/og/graph` cache) · WB-012 (per-turn semantic-search) · WB-016 (central Settings model) · PERF-016 (FTS search) · SEC-006 (webhook fail-closed) · REL-N02 (limiter reaping) · **frontend test foundation** · docs consistency. *Rationale: real quality/scale debt, no user-facing trust harm, safe to schedule once P0/P1 land.*

### BACKLOG — Sprint 4 / P3

Responsive/mobile (BB-003) · encryption at rest (SEC-010) · monolith refactors (WB-016/017) · pack-system consolidation (BB-012) · brand/doc cleanup · provider/tier naming unification · SSE parser + path-escape hardening (PERF-N05/SEC-N02). *Rationale: enhancements and opportunistic cleanup.*

---

## 2. RISK-ACCEPT (documented, not blocking v1)

| Item | Why acceptable for v1 | Condition |
|---|---|---|
| Desktop-only / no mobile (BB-003) | Lead ICP is desktop self-hosters; native apps explicitly deferred by the founder | Tablet "don't break < 900px" pass in Sprint 3; full responsive Sprint 4 |
| Plaintext memory/transcripts at rest (SEC-010) | Local single-user threat model | **Must be documented**; encrypted-vault opt-in on roadmap |
| Monolith files (`cli/main.py` 2,886 LOC) (WB-016) | No user impact | Refactor opportunistically; freeze further growth |
| Non-durable func-jobs (WB-019) | Current jobs (prefetch/title/sync) are regenerable | Fix Sprint 3 before any job carries irreplaceable work |
| `.og` corpus near token break-even (PERF risk 5) | Moat still positive once PERF-001 lands | Track corpus size; set a budget ceiling per pack |

---

## 3. REJECT

| Item | Decision | Rationale |
|---|---|---|
| Action `docs/strategy/01-architecture-review.md` debt items | **REJECT** | Confirmed stale in v1 (concurrent-chat corruption, no streaming, sequential tools already fixed). Do not let agents action it. Mark the doc stale instead. |
| Rewrite the backend / re-architect the engine | **REJECT** | The engine is the strongest asset (78/100). Every critical is "wire it / delete the fake / fix the gate," not a redesign. A rewrite would destroy value and time. |
| Build native desktop/mobile apps now | **REJECT (defer)** | Founder-deferred until feature-rich and stable. Not in this program. |
| Ship the web UI to external users before Gate B | **REJECT** | Spending trust we cannot win back from a skeptical ICP. Engine/CLI first. |

---

## 4. Sequencing rationale (why this order)

1. **CI + test honesty first** — otherwise we are flying blind and will repeat exactly the v1→v2 "looks fixed" failure.
2. **Engine/CLI before web** — the CLI + gateway is the strongest, most honest surface and matches the ICP (terminal-native self-hosters). It can ship after Sprint 1.
3. **"Stop faking" before "add features"** — every removal of a fake (validated key, fixture fallback, fake spend, faked memory, simulated run) is higher ROI for this brand than any new capability, and most are cheaper.
4. **Security criticals are non-negotiable for an autonomous agent** — zip-slip and boot-time arbitrary exec gate the pack ecosystem entirely.

*Continue to [07 — Execution Plan](07-execution-plan.md).*
