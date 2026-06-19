# Verification Addendum — 2026-06-19 Audit

**Status:** Critical & key-high findings independently re-validated against current source.
**Method:** Three parallel verification agents read the actual code at every cited
`file:line`, returning per-claim verdicts (CONFIRMED / FALSE-POSITIVE / PARTIAL /
OVERSTATED) with quoted evidence. No code was modified during verification.

**Why this exists:** the original audit (`README.md`) is strong but was produced from a
single static-analysis pass. Before committing 4–6 weeks of parallel engineering to it,
the critical path was validated. Verification found **one critical false-positive and one
critical overstatement** — acting on them as written would have spent a sprint task and a
release gate on a non-bug. The corrected plan below supersedes the original Sprint 1 and
the release-gate list where they conflict.

---

## Headline corrections

| # | Finding | Original | Verified verdict | Action |
|---|---|---|---|---|
| 1 | **CRIT-004** — safety level frontend/backend mismatch "silently ignored" | Critical blocker + GATE-07 | **FALSE POSITIVE** | Remove from Sprint 1. A correct bidirectional mapping exists. Convert GATE-07 to a regression test. |
| 2 | **CRIT-001** — `create_gateway_app` auth bypass | Critical blocker + GATE-01 | **OVERSTATED** | Downgrade to Low / defense-in-depth. Not a reachable vuln in shipped code. Keep a fail-closed guard, but it is not a release blocker. |
| 3 | **HIGH-005** — division-by-zero crashes in work/index/og | High (3 files) | **PARTIAL** | Only `work.tsx` is a real crash → **FIXED this pass**. `index.tsx` is clamped, `og.tsx` is guarded — cosmetic `NaN%` only. |
| 4 | **HIGH-001** — DNS-rebinding SSRF + wildcard allowlist | High | **SPLIT** | TOCTOU rebinding **confirmed**; "wildcard allowlist" is a **false positive** (`net_guard.py` has no allowlist). Rescope the fix to the TOCTOU window only. |

---

## Per-claim verification detail

### CRIT-004 — Safety level mismatch — FALSE POSITIVE ❌

The string mismatch is real (`SafetyLevel = "guarded"|"standard"|"open"` in
`frontend/src/lib/data/types.ts:6`; backend `PermissionTier = "sandbox"|"guided"|"unrestricted"`
in `src/hachi/core/settings.py:11`), **but a correct bidirectional mapping bridges it**:

```ts
// frontend/src/lib/data/domains/config.ts:78-90
function adaptTier(tier?: string): SafetyLevel {      // gateway → UI
  if (tier === "sandbox") return "guarded";
  if (tier === "unrestricted") return "open";
  return "standard";                                  // "guided"
}
function safetyToTier(level: SafetyLevel) {            // UI → gateway
  if (level === "guarded") return "sandbox";
  if (level === "open") return "unrestricted";
  return "guided";                                     // "standard"
}
```

It is wired into **both** read (`adaptTier` in `loadConfig`, config.ts:121) and write
(`safetyToTier` in `setSafetyLive` → `PUT /config/permissions`, config.ts:158). Safety
changes are **not** silently ignored. **Disposition: REJECT the finding.** GATE-07 should
become a regression test that asserts the round-trip, not a fix task.

### CRIT-001 — Auth disabled when `create_gateway_app` has no token — OVERSTATED ⚠️

`_require_auth` does short-circuit to a no-op when `auth_token` is falsy
(`gateway/fastapi_app.py:331-336`), and `gateway-openapi` constructs the app without a token
(`cli/main.py:2044`). **But** that command only serializes `app.openapi()` to a file and
**never binds a socket or serves a request** (`cli/main.py:2045-2049`). The actual served
daemon always calls `_resolve_gateway_auth_token()` which generates/persists a loopback
token and **raises for non-loopback hosts without an explicit token**
(`cli/main.py:959, 992-999`). So this is a **latent contract footgun** (default
`auth_token=None` = silent no-auth), not a reachable network vulnerability.
**Disposition: KEEP as a small fail-closed hardening task (E1-T1), but DOWNGRADE from
Critical → Low and DROP it as a release blocker.** GATE-01 already holds for the served path.

### CRIT-002 — Authoring workflow non-functional — CONFIRMED ✅

Generate button has no `onClick` and no `generatePatch` store action exists
(`authoring.tsx:455-478`, `store.ts`). Backend `authoring_apply` / `authoring_revert` are
explicit no-op stubs returning `{"ok": True}` (`cli/main.py:694-714`, with `# STUB` / `# TODO`),
echoed by `control_plane.py:501,507` ("no-op stub"). The UI "written to disk · re-indexed"
confirmation is cosmetic. **Disposition: KEEP as Critical (Large). Largest single fix.**

### CRIT-003 — Kennel "Browse" button noop — CONFIRMED ✅

`onClick={() => {/* … noop … */}}` empty body at `skills.tsx:205`. The working install path
is the per-card `addSkill(k.id)` button (`skills.tsx:144`); the header CTA is dead.
**Disposition: KEEP. Fix is Trivial→Medium** (wire to `KennelConsent` overlay / reveal the
"From the kennel" section).

### CRIT-005 — Python sandbox full host access — CONFIRMED (with nuance) ✅

`core/sandbox.py:163-176` runs LLM-supplied code via `uv run` with only a scrubbed env — no
namespace/seccomp/chroot, full host FS + network. Registered as LLM tool `python_sandbox`
(`tools/sandbox_tools.py:45-80`, `orchestrator.py:1711`). **Nuance the audit omitted:** the
tool raises `PermissionError` for `sandbox`/`guided` tiers — it is only reachable in the
**`unrestricted`** tier (`sandbox_tools.py:28-33`). Still real in that tier, and the tool
description ("secure sandbox") is materially misleading. **Disposition: KEEP. Interim fix
(env-gate + AST scan, E3-T2) is Medium; true isolation is Large.**

### CRIT-006 — No session ownership enforcement — CONFIRMED ✅

Session routes take only the path `session_id`; `_require_auth` returns `None` (no principal);
control-plane gates solely on `session_id in self._connections` and the stored record holds no
owner identity (`fastapi_app.py:443-461`, `control_plane.py:213-218, 269-271`). Grep for
`owner|principal|caller_id` in `gateway/` returns nothing. **Nuance:** severity is bounded by
single-tenant / single-shared-token / loopback deployment, but the access-control gap is real.
**Disposition: KEEP as Critical for any multi-user/exposed deployment (Medium).**

### CRIT-007 — Session resume drops context — CONFIRMED ✅

`cli/main.py:1860-1864`: `--resume`/`--continue` print a yellow warning and call
`_launch_cockpit()` fresh, no non-zero exit. A real resolver `_resolve_resume_target()` exists
at `:1793` but is **dead code**. **Disposition: KEEP (Medium). Note: the gateway
`_boot_session_agent(session_id)` also ignores `session_id` — fix must thread the id through.**

### CRIT-008 — No single-process `serve` — CONFIRMED ✅

No `serve` command exists in the CLI; `README.md:353-363` mandates two terminals
(`hachi gateway` + `npm run dev`). **Disposition: KEEP (Medium). `hachi serve` mounting
`frontend/dist` via `StaticFiles` is the fix.**

### HIGH-001 — SSRF — SPLIT (TOCTOU ✅ / wildcard ❌)

`net_guard.py:61-72` resolves via `getaddrinfo`, validates, returns `(True, "ok")` without
pinning the IP — the eventual TCP connect re-resolves (classic rebinding TOCTOU). **Confirmed.**
The "wildcard allowlist" half is a **false positive**: `net_guard.py` has no allowlist mechanism
at all (only a boolean `allow_private`). The auditor likely conflated it with the unrelated
exact-match **sender-id** allowlist in `fastapi_app.py:51-52`. **Disposition: KEEP TOCTOU fix
(Medium); DROP the wildcard clause.**

### HIGH-005 — Empty-data math crashes — PARTIAL → work.tsx FIXED ✅

- **`work.tsx` — CONFIRMED & reachable & FIXED.** On a brand-new user the live `/spend` loader
  hydrates empty arrays (`spend.ts:98-109`); `computeSpend` then hit `maxCum.toFixed(2)` on
  `undefined` (`work.tsx:161`) — a **hard `TypeError`/white-screen**, not just `NaN`. Fixed this
  pass by guarding every denominator (`barMax`, `lineDenom`, `maxCum`, `afterPct`, donut `frac`)
  and adding an empty-state in `SpendLens`.
- **`index.tsx` — OVERSTATED.** `BudgetSummary` is clamped by `Math.min(100, …)` (`index.tsx:852`);
  worst case is a cosmetic `NaN%` in the `0/0` case. Not a crash.
- **`og.tsx` — FALSE POSITIVE for "crash".** The only division-by-`dn.full` path is guarded by a
  `dn &&` render gate (`og.tsx:1045`); empty data yields a cosmetic `NaN%` header stat at worst.

**Disposition: work.tsx DONE. Re-scope E1-T2 to the cosmetic `NaN%` guards in index/og as Low.**

### HIGH-008 — Stale hardcoded model list — CONFIRMED (scoped) ✅

`cli/main.py:503` gateway `list_models` = `["claude-haiku-4-5", "claude-sonnet-4-5"]` — missing
`claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-6`. **Nuance:** this affects only the
**gateway/web-UI** endpoint; the `hachi models` **CLI** command uses live provider discovery
(`cli/main.py:2426`). The configured default (`claude-haiku-4-5`) **is** present.
**Disposition: KEEP (Trivial). 21 test files reference these strings — run the full suite after
editing. Prefer delegating to live discovery like the CLI already does.**

### HIGH-013 — Sub-agent model override ignored — CONFIRMED (scoped) ✅

`sub_agent.py:578` `llm_client or self._create_client(model)`; a non-None client is always
injected and, for Anthropic, force-pinned to `claude-haiku-4-5`
(`orchestrator.py:1530-1535`). `delegate_task` has no `model` parameter or schema field
(`delegation_tools.py:399, 69-105`). **Nuance:** "always haiku" is strictly true only for the
**Anthropic** provider; other providers reuse the configured model. **Disposition: KEEP
(Medium). Matches the existing backlog note in memory.**

---

## Corrected critical-blocker count

| | Original | Verified |
|---|---|---|
| Critical blockers | 8 | **6 confirmed** (CRIT-002, -003, -005, -006, -007, -008) |
| Reclassified out | — | CRIT-004 → **rejected** (false positive); CRIT-001 → **Low** (overstated) |
| Promoted in | — | HIGH-005/work.tsx behaved as a critical-severity new-user crash → **fixed this pass** |

The composite **44/100 NO-GO** verdict still stands — the six confirmed blockers (authoring
dead, kennel dead, no session ownership, resume broken, no single-process onboarding, sandbox
exposure in unrestricted tier) are individually release-blocking. The score is not materially
moved by the two corrections, but the **Sprint 1 work-list and the release-gate list are**.

---

## Corrected Sprint 1 & release gates

**Sprint 1 changes:**
- ❌ **Remove** the CRIT-004 safety-mapping task — no defect.
- ⬇️ **Downgrade** E1-T1 (auth) to a small defense-in-depth hardening; it is no longer a gate.
- ✅ **E1-T2 (work.tsx) — DONE** this pass (guards + empty-state). Remaining index/og `NaN%`
  guards drop to Low/Sprint 4.
- ➡️ **Keep** E1-T3 (model list, run full suite), E1-T5 (`hachi serve`), E1-T6 (resume),
  E2-T1/T2/T3 (test integrity).

**Release-gate changes:**
- **GATE-07** (safety round-trip): already satisfied by the existing mapping → convert to a
  *regression test*, not a fix-blocking gate.
- **GATE-01** (auth 401): already holds for the served daemon → keep as a regression assertion;
  the E1-T1 change is defense-in-depth, not the thing that makes the gate pass.
- **GATE-02** (no-crash on empty data): the `work.tsx` portion is now fixed; the live-stack
  acceptance test remains the sign-off.

---

## Fix applied during this verification pass

| Finding | File | Change | Verified |
|---|---|---|---|
| HIGH-005 (work.tsx) | `frontend/src/routes/work.tsx` | Guard all `computeSpend` denominators (`barMax`, `lineDenom`, `maxCum`, `afterPct`, donut `frac`); add empty-state to `SpendLens` | `npm run typecheck` clean. Live-mode GATE-02 acceptance test still recommended. |

All other findings remain for the parallel engineering execution plan in `README.md`,
amended by the corrections above.
