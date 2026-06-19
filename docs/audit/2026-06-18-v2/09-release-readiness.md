# Hachi — Release Readiness Recommendation (Audit v2)

**Date:** 2026-06-18 · **Owner:** Product Owner + Principal Architect
**Decision:** **NO-GO for a full-product (web UI) release. CONDITIONAL-GO for a backend + CLI engine release after Sprint 1** — with one new hard gate this pass: *prove the green is real.*

---

## Verdict at a glance

| Release scope | v1 | **v2** | Recommendation |
|---|---|---|---|
| **Backend + CLI engine** (no web UI) | 68 | **64** | **Conditional GO** after Sprint 1 (CLI config crash, false-green test, pack supply-chain criticals, CI green-for-real) |
| **Full product incl. web UI** | 34 | **32** | **NO-GO** until Sprint 1 + the Sprint-2 trust/security items land |
| **Cockpit TUI** | n/a | n/a | Excluded — owned by a concurrent session; re-audit after it lands |

The engine score dipped 4 points **not because the engine got worse** but because v2 confirmed that the CLI config commands crash (v1 had them as suspected-fixed) and that a test is hiding it. Confidence, not capability, is what fell.

---

## Why NO-GO for the web product

Hachi's positioning is *trust for people burned by autonomous agents.* The web SPA currently, when traced through its own code paths:
- shows **fabricated healthy data** when the gateway is down (UX-018),
- tells users their API key is **"validated"** when it only counted characters (UX-001),
- displays a **wrong, double-counted** spend number (UX-N01/WB-009),
- shows a **fabricated "auto-declines in 30s"** countdown that is not backed by any server timer (BB-N03),
- has an **in-context leash allow/deny that never calls the backend** (BB-N02/WB-007),
- **toasts "Hachi remembers it"** for memory edits that are never persisted — though the endpoints exist (BB-N04),
- ships a **simulated** "watch live" run (UX-017).

For a governance product, controls that *look* real but do nothing are worse than honestly-absent ones. Additionally **CI is red** (WB-001), a **unit test is green over a 100%-crashing command** (WB-N07), and the **frontend has no tests at all** — so we cannot prove a build is safe. The engine underneath is mature; the failure is honesty of the surface and of the verification system.

---

## Release gates

### Gate A — Engine/CLI release (target: end of Sprint 1)
- [ ] **G-A0 (NEW)** CI is **green for real**: `frontend-contract` points at the real SPA (WB-001); the config false-green test is deleted/repaired and a test runs the *actual* `config-get/set` command (WB-N07); strict ruff + mypy gate is **blocking** on `main`.
- [ ] **G-A1** Full `pytest` green on Linux + macOS; mypy-strict + ruff clean on shipped modules.
- [ ] **G-A2** `hachi config-get/set` work for all documented keys, no traceback, with a real cost-cap field modeled (BB-001/002).
- [ ] **G-A3** Pack ZIP zip-slip + size/count caps closed; tar/zip parity (SEC-001).
- [ ] **G-A4** MCP server install/exec is tier-gated + explicitly confirmed; no web-sourced auto-exec (SEC-005).
- [ ] **G-A5** `pip-audit` (or equivalent) clean — no known high CVEs in shipped deps (SEC-011).
- [ ] **G-A6** Docker image builds, runs **non-root**, binds a configurable host; `SECURITY.md` linked from README (WB-002/004, SEC-012).
- [ ] **G-A7** PERF-001 landed (history prefix cached; all 4 cache breakpoints) — cost claim restored.

### Gate B — Full product (web UI) release (target: end of Sprint 2)
*All of Gate A, plus:*
- [ ] **G-B1** No UI surface renders fabricated data; honest loading/empty/error/disconnected states everywhere (UX-018).
- [ ] **G-B2** API-key validation is real; no fake "validated" (UX-001).
- [ ] **G-B3** Leash allow/deny perform real backend approvals **from the in-context prompt**, with a real countdown + decline reason; "Always allow" confirms (BB-N02/BB-N03/WB-007/UX-006).
- [ ] **G-B4** Memory add/edit/delete/approve call `/memory/facts` and survive reload; correct session targeting (BB-N04/WB-N03).
- [ ] **G-B5** `/run` shows the real SSE stream or the screen is removed (UX-017).
- [ ] **G-B6** Spend is accurate (no double-count), discoverable (nav item), and the cap is settable + enforced from the UI (UX-N01/UX-008/UX-011).
- [ ] **G-B7** Provider choice available at setup, consistent across CLI/web/docs (UX-002).
- [ ] **G-B8** Governance status bar (model/session/cost/connection) on every route (UX-009).
- [ ] **G-B9** Accessibility: keyboard-operable rows, focus-visible (done), dialog semantics, labeled controls, AA contrast; no critical `axe` violations on shipped screens (BB-007/008/009/011).
- [ ] **G-B10** SSRF guards on `http_request` + browser redirects; unconditional injection framing; pack signing enforced; NO_AUTH loopback-gated (SEC-002/003/004/007/N01).
- [ ] **G-B11** Gateway stays responsive under approval-flood/multi-session load — approval wait off the event loop; read handlers in `to_thread` (REL-001/PERF-N01/011).
- [ ] **G-B12** Frontend has CI-run tests incl. a gateway-integration smoke (see [08](08-verification-strategy.md)).

### Gate C — Scale / GA hardening (target: Sprint 3+)
- [ ] Durable orchestrator jobs (WB-019); SessionStore reader concurrency + FTS search (PERF-015/016); embedding pre-warm (PERF-019); full a11y AA + CI a11y gate; dep-CVE/SAST blocking (SEC-011); responsive/tablet pass; `.og` corpus budget ceiling.

---

## Recommended release sequence

1. **Internal/dogfood build now** — for the team and the concurrent TUI session, behind a banner naming the known-simulated surfaces. *Not for external users.*
2. **Engine/CLI `0.1.0` (Gate A)** — ship the CLI + gateway to terminal-native early adopters. Strongest, most honest surface; matches the ICP.
3. **Private web beta (Gate B)** — invite-only, after the trust/security/perf items + the frontend integration test layer land.
4. **Public beta / GA (Gate C)** — after scale hardening + accessibility AA + dep-CVE gating.

---

## One-paragraph executive recommendation

**Do not ship the web UI yet — and do not trust "green" until Sprint 1 makes it real.** The backend engine is genuinely strong, but this second audit found that the prior remediation was cosmetic: the CLI config command still crashes, a test was written that passes over the crash, and the frontend-contract CI job guards a frontend that doesn't exist. On top of that, the web surface fakes validation, spend, memory persistence, and the safety countdown, and the in-context leash — the heart of the product — is a no-op. None of this is deep; it is a focused ~2-sprint program of *restoring honest verification first*, then deleting simulations, wiring real calls, closing the pack supply chain, and adding the governance/accessibility layer. Land Sprint 1 to release a credible **engine + CLI**; complete Sprint 2 to earn a **web beta**. Shipping the web UI before Gate B would spend trust this audience does not give back.

---

*Back to [00 — Master Report](00-MASTER-REPORT.md).*
