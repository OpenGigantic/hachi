# Hachi — Product Owner Decisions (Triage)

**Date:** 2026-06-18 · **Owner:** Product Owner
**Input:** [00 — Master Report](00-MASTER-REPORT.md) and sub-reports 01–05.

Each finding (or cluster) is assigned one disposition with rationale:

- **FIX NOW** — immediate; blocks the ability to develop/verify or is an active trust/security hole.
- **BEFORE RELEASE** — must ship resolved in the first public/beta release that includes that surface.
- **NEXT SPRINT** — important but not a release blocker.
- **BACKLOG** — real, lower priority; revisit when capacity allows.
- **REJECT / WON'T FIX (now)** — intentional, acceptable for the current threat model/ICP, or superseded.

> **Governing PO principle for this product:** Hachi's wedge is *trust* for burned self-hosters. Therefore "a control that looks real but does nothing" is triaged **at least as harshly as a functional bug** — it actively damages the wedge. Conversely, polish that doesn't touch trust, the desktop ICP, or the engine is cheerfully deferred.

---

## Disposition summary

| Disposition | Count (clusters) | Sprint |
|---|---|---|
| FIX NOW | 10 | Sprint 1 |
| BEFORE RELEASE | ~18 | Sprint 2 |
| NEXT SPRINT | ~16 | Sprint 3 |
| BACKLOG | ~14 | Sprint 4 / later |
| REJECT (now) | 4 | — |

---

## FIX NOW (Sprint 1 — release blockers)

| ID(s) | Decision | Rationale |
|---|---|---|
| **WB-001** CI contract job broken | FIX NOW | *Highest order of operations.* While CI is red we cannot prove any other fix is safe. This must land first so the rest of the program is verifiable. |
| **BB-001 / BB-002** CLI config crashes | FIX NOW | The crash is on the *safety tier and budget cap* keys — the exact governance controls our ICP cares about — and they raw-traceback. Reproduced live. Embarrassing and trust-killing for a CLI-first tool. |
| **SEC-001** pack zip-slip | FIX NOW | Verified arbitrary `.og` write outside dest from a web-fetched artifact. The tar path was already hardened; this is closing a known-class hole that was missed. Low effort, high impact. |
| **SEC-005** MCP arbitrary exec | FIX NOW | Web-installable config that launches arbitrary local commands at boot with no tier gate is the most severe single security finding. Must be gated + consent-confirmed before any pack/MCP feature is exposed. |
| **UX-001 / BB-006** fake key "validated" | FIX NOW | Telling a skeptical user their key is "validated" when we only counted characters is the precise betrayal our positioning warns against. Cheap to fix (call the real validation that already exists at Finish). |
| **UX-018 / BB-004** silent fixture fallback | FIX NOW | A transparency product that fabricates healthy data on backend failure is disqualifying. Must show honest error/loading/empty states. |
| **PERF-001** uncached history | FIX NOW | Directly inflates every user's bill *and* silently defeats the `.og` token-efficiency moat. Medium effort, very high impact on cost and narrative. |
| **PERF-025** unvirtualized transcript | FIX NOW | The chat view is the primary surface; it locks up on real-length conversations. Core-flow performance. |
| **UX-009** no governance status bar | FIX NOW | Both design specs call it non-negotiable; "make the invisible visible" is the thesis and the key glances (model/cost/connection) are absent. Foundational chrome that other trust work hangs off. |
| **UX-023 + BB-007/008/011** a11y foundation | FIX NOW | Keyboard/focus/dialog semantics are a hard requirement for a developer audience and a compliance floor. The *foundation* (focus rings, dialog role+trap, control labels) is Sprint 1; full WCAG sweep continues into Sprint 3. |

---

## BEFORE RELEASE (Sprint 2 — high)

| ID(s) | Decision | Rationale |
|---|---|---|
| **WB-007 / UX-006 / UX-007** leash no-op | BEFORE RELEASE | A permission product whose allow/deny buttons don't call the backend is the single most damaging "hollow control." Must POST to `/approvals/{id}`, add countdown + decline-reason. |
| **BB-005 / UX-017** simulated /run | BEFORE RELEASE | Either wire to real SSE or remove the screen. Shipping a scripted "live" view as if real repeats the trust violation. |
| **BB-010 / WB-008 / UX-014** authoring/kennel stubs | BEFORE RELEASE | "Apply" must either do the thing or not claim success. Acceptable interim: hide/disable with an honest "coming soon" rather than fake success. |
| **WB-009** spend double-count | BEFORE RELEASE | Spend accuracy is the anti-bill-shock promise; an inflated meter is both wrong and alarming. |
| **UX-002 / UX-008** spend cap + provider choice | BEFORE RELEASE | The two headline differentiators (budget control, provider freedom) are unconfigurable. At minimum: a working spend-cap field that enforces, and provider selection in setup. |
| **SEC-003 / SEC-004** SSRF (http_request, browser) | BEFORE RELEASE | An autonomous agent that can be steered to hit internal hosts/metadata endpoints is a real, demonstrable risk. Route both through `net_guard` incl. post-redirect. |
| **SEC-002** unframed injection | BEFORE RELEASE | Framing must be unconditional, not detection-gated, given the autonomous tool-execution threat model. |
| **SEC-007** unsigned packs | BEFORE RELEASE | Pair with SEC-001/005 to close the pack supply chain as one unit before any pack discovery/install UX is promoted. |
| **REL-001 / PERF-011** blocking the event loop | BEFORE RELEASE | Approval waits + blocking read handlers on the asyncio loop will brown-out the gateway under modest concurrency. Move to threads / async properly. |
| **WB-013** silent except-swallow | BEFORE RELEASE | Returning a fake uuid on a failed memory write hides data loss — unacceptable for a memory product. Fail loud / surface. |
| **WB-004 / WB-002/003/005** infra & release | BEFORE RELEASE | Self-hosters install via Docker; a compose file pointing at a non-existent Dockerfile and a PyPI-only release with a root container block the actual install path. |
| **SEC-011** no dep-CVE/SAST in CI | BEFORE RELEASE | Add `pip-audit`/`npm audit` + a SAST step; cheap, and a credible security posture is part of the pitch. |
| **PERF-005/019/015/012/022** perf hotspots | BEFORE RELEASE | SSE push (not 50ms poll), embedding pre-warm, SessionStore reader concurrency, `/memory` pagination, CLI lazy imports — all S/M, all on hot paths. |
| **BB-009** sub-AA contrast | BEFORE RELEASE | Token-level fix; lands with the a11y sweep. |
| **BB-003** desktop-only responsive | **NEXT SPRINT (downgraded)** | *PO override:* the lead ICP is self-host devs on desktop and the user explicitly deferred mobile apps. A "don't break below ~900px" tablet pass is nice-to-have, full responsive is not a launch blocker. Moved to Sprint 3. |

---

## NEXT SPRINT (Sprint 3 — medium)

| ID(s) | Decision | Rationale |
|---|---|---|
| **WB-019** non-durable func-jobs | NEXT SPRINT | Real durability gap, but current jobs (prefetch/title/sync) are regenerable; loss is degraded-not-broken. Fix the side-table soon. |
| **PERF-006** O(n) event poll | NEXT SPRINT | Scales poorly but fine at current event volumes; `deque`+bisect is a quick win. |
| **WB-012** per-turn semantic search in hot path | NEXT SPRINT | Couples perf + cache-busting; needs care to not regress retrieval quality. |
| **WB-016** no central Settings / 110 env reads | NEXT SPRINT | Foundational debt; worth a typed `Settings` model before more config surface is added. |
| **WB-011** mock scripts in live store | NEXT SPRINT | Remove ~400 lines of demo scripts from production store paths; risk of leaking into real sessions. |
| **PERF-016** unindexable search LIKE | NEXT SPRINT | Add FTS5 / index; matters as transcripts grow. |
| **SEC-006** webhook fail-open | NEXT SPRINT | Gateway path missed the router fix; fail-closed on empty allowlist. |
| **SEC-013** NO_AUTH ignores host | NEXT SPRINT | Gate the escape hatch to loopback-only. |
| **a11y completion** (live regions, full sweep, palette) | NEXT SPRINT | After the Sprint-1 foundation. |
| **Frontend test foundation** (Vitest/RTL + Playwright smoke + wire to CI) | NEXT SPRINT | Prereq for trusting future frontend changes; can't retrofit everything at once. |
| **Docs consistency** (provider/tier naming, link SECURITY.md, fix stale design brief, retire arch-review doc as historical) | NEXT SPRINT | Low-risk, improves trust + onboarding. |

---

## BACKLOG (Sprint 4 / later)

| ID(s) | Decision | Rationale |
|---|---|---|
| **BB-003** full responsive/mobile | BACKLOG (after tablet pass) | Desktop ICP; user deferred mobile. |
| **SEC-010** encryption at rest | BACKLOG + document now | Local single-user threat model makes plaintext acceptable for v1 **if documented**. Add an opt-in encrypted vault later. |
| **WB-017** monolith refactors (`cli/main.py`, etc.) | BACKLOG | Pure maintainability; no user impact. Split opportunistically as files are touched. |
| **WB-028** per-session runtime boot at scale | BACKLOG | Only bites at high session counts beyond current target. |
| **BB-012** two pack systems | BACKLOG | Consolidate `hachi pack` and `hachi skill pack` to remove confusion; not urgent. |
| **WB-020** global `.og` namespace | BACKLOG | Add namespacing/collision warnings; low incidence today. |
| Brand/copy cleanup, Pillow/dep floor verification, provider-preset polish | BACKLOG | Housekeeping. |

---

## REJECT / WON'T FIX (now) — with rationale

| Item | Decision | Rationale |
|---|---|---|
| Dev-only "Preview reconnecting" action in CommandPalette | REJECT as a bug | Legitimate dev affordance. Action: gate behind `import.meta.env.DEV` if not already; not a defect. |
| `docs/strategy/01-architecture-review.md` debt items (TD-1/2/4/13, M-11, 500-leak) | REJECT (already fixed) | White-Box + Security agents confirmed these are resolved in the working tree. **Action: mark the doc "historical — superseded" so it stops generating phantom work.** Do not assign. |
| All **TUI** findings | DEFER (not rejected) | A concurrent session owns `src/hachi/tui/`. Do not assign here; hand the TUI-tagged findings to that session. Re-audit TUI after it lands. |
| "Make `/run` real-time" *and* "remove `/run`" as competing asks | RESOLVE, don't do both | Decision: **wire `/run` to real SSE** (reuse the conversation stream infra) rather than delete — it's a differentiated surface. Tracked under UX-017. |
| Plaintext `.env` cwd-relative loading as a "bug" | REJECT severity, keep as polish | Works as designed for the local-dev path; harden the resolution order (BACKLOG) but it's not a defect. |

---

*Decisions flow into [07 — Execution Plan](07-execution-plan.md) (epics, tasks, parallel assignment, sprint roadmap).*
