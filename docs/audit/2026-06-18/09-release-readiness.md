# Hachi — Release Readiness Recommendation

**Date:** 2026-06-18 · **Owner:** Product Owner + Principal Architect
**Decision:** **NO-GO for a full-product (web UI) public/beta release today. CONDITIONAL-GO for a backend + CLI engine release after Sprint 1.**

---

## Verdict at a glance

| Release scope | Score /100 | Recommendation |
|---|---|---|
| **Backend + CLI engine** (no web UI) | 68 | **Conditional GO** after Sprint-1 backend items (CLI config crashes, pack supply-chain criticals) |
| **Full product incl. web UI** | 34 | **NO-GO** until Sprint 1 + the Sprint-2 trust/security items land |
| **TUI** | n/a | Excluded — owned by a concurrent session; re-audit after it lands |

---

## Why NO-GO for the web product (the short version)

Hachi's positioning is *trust for people burned by autonomous agents.* The web SPA currently:
- shows **fabricated healthy data** when the gateway is down (UX-018),
- tells users their API key is **"validated"** when it only counted characters (UX-001),
- ships a **simulated** "watch live" run as if it were real (UX-017),
- has **leash allow/deny buttons that never call the backend** (WB-007),
- **toasts "applied"** for actions that write nothing (WB-008).

For a governance product, controls that *look* real but do nothing are worse than honestly-absent ones. Shipping this to the skeptical ICP would actively burn the wedge. Additionally, **CI is red** (WB-001) and **`hachi config-get/set` crash on the budget/safety keys** (BB-001/002), so we cannot currently prove a build is safe or let users configure the very controls we tout.

The good news: **none of these are deep.** They are "delete the fake / wire the call / fix the gate." The engine underneath is mature.

---

## Release gates (must be true to flip each verdict)

### Gate A — Engine/CLI release (target: end of Sprint 1)
- [ ] **G-A1** CI green on `main` (T2.1).
- [ ] **G-A2** `hachi config-get/set` work for all documented keys, no traceback (T1.1/T1.2).
- [ ] **G-A3** Pack ZIP zip-slip + size limits closed; tar/zip parity (T6.1).
- [ ] **G-A4** MCP server install/exec is consent-gated; no web-sourced auto-exec (T6.2).
- [ ] **G-A5** `pip-audit` clean (no known high CVEs in shipped deps) (part of T2.3).
- [ ] **G-A6** Full `pytest` green; mypy-strict + ruff clean on shipped modules.
- [ ] **G-A7** Docker image builds, runs **non-root**, binds a configurable host; `SECURITY.md` linked (T2.2/T2.4/T10.2).

### Gate B — Full product (web UI) release (target: end of Sprint 2)
*All of Gate A, plus:*
- [ ] **G-B1** No UI surface renders fabricated data; honest loading/empty/error/disconnected states everywhere (T3.1).
- [ ] **G-B2** API-key validation is real; no fake "validated" (T3.2).
- [ ] **G-B3** Leash allow/deny perform real backend approvals with countdown + decline reason (T3.3 + T7.1).
- [ ] **G-B4** `/run` shows the real SSE stream or the screen is removed (T3.4).
- [ ] **G-B5** Every interactive control is real or visibly disabled — zero fake-success toasts (T3.6).
- [ ] **G-B6** Spend is accurate (no double-count), discoverable (nav item), and the cap is settable + enforced from the UI (T3.5/T4.2/T4.3).
- [ ] **G-B7** Provider choice available at setup, consistent across CLI/web/docs (T4.4/T10.1).
- [ ] **G-B8** Governance status bar (model/session/cost/connection) on every route (T4.1).
- [ ] **G-B9** Accessibility: keyboard operable, focus-visible, dialog semantics, labeled controls, AA contrast; no critical `axe` violations on shipped screens (T5.1–T5.5).
- [ ] **G-B10** SSRF guards on `http_request` + browser redirects; unconditional injection framing; pack signing enforced (T6.3/T6.4/T6.5).
- [ ] **G-B11** Gateway stays responsive under approval-flood/multi-session load (T7.1/T8.3/T8.4).
- [ ] **G-B12** Frontend has CI-run tests incl. a gateway-integration smoke (T9.1) — *strongly recommended; minimum bar is the integration tests in [08 §4](08-verification-strategy.md).*

### Gate C — Scale / GA hardening (target: Sprint 3+)
- [ ] Durable orchestrator jobs (T7.3); SessionStore reader concurrency + FTS search (T8.6/T8.7); embedding pre-warm (T8.5); full a11y AA + CI a11y gate (T5.6); dep-CVE/SAST blocking (T2.3); responsive/tablet pass.

---

## Risk-accepted for v1 (documented, not blocking)

| Item | Why acceptable now | Condition |
|---|---|---|
| Plaintext memory/transcripts at rest (SEC-010) | Local single-user threat model | **Must be documented** (T10.5); encrypted-vault opt-in on roadmap |
| Desktop-only / no mobile responsive (BB-003) | Lead ICP is desktop self-hosters; mobile apps explicitly deferred | Tablet "don't break <900px" pass in Sprint 3 |
| Monolith files (`cli/main.py` etc.) (WB-017) | No user impact | Refactor opportunistically |
| Non-durable func-jobs (WB-019) | Current jobs are regenerable (prefetch/title/sync) | Fix in Sprint 3 (T7.3) |

---

## Recommended release sequence

1. **Internal/dogfood build now** — for the team and the concurrent TUI session, with a banner stating known-simulated surfaces. *Not for external users.*
2. **Engine/CLI `0.1.0` (Gate A)** — ship the CLI + gateway to technical early adopters who live in the terminal. This is the strongest, most honest surface and matches the ICP.
3. **Private web beta (Gate B)** — invite-only, after the trust/security/perf items land, with the integration test layer in place.
4. **Public beta / GA (Gate C)** — after scale hardening + frontend test coverage.

---

## One-paragraph executive recommendation

**Do not ship the web UI yet — ship the engine.** Hachi's backend is genuinely strong and its security remediation has landed; the failure is that the web product pretends to do things it doesn't, on top of a red CI and crashing config commands. Fixing this is a focused, ~2-sprint program of deleting simulations, wiring real calls, closing the pack supply chain, and adding an accessibility + governance-visibility layer — not a rewrite. Land Sprint 1 (CI, CLI, the two critical security holes, the cost/transcript performance fixes, and the "stop faking data" pass) to release a credible **engine + CLI** to the exact developer audience we target, then complete Sprint 2 to earn a **web beta**. Releasing the web UI before Gate B would spend trust we cannot easily win back from a "burned by OpenClaw" audience.

---

*Back to [00 — Master Report](00-MASTER-REPORT.md).*
