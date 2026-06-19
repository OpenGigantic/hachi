# Hachi — UX & Product Experience Audit v2 (QA Agent 3)

**Reviewer lens:** Senior Product Manager · **Date:** 2026-06-18 (v2 re-verification) · **Mode:** Read-only
**Primary surface:** Web SPA (`frontend/`, uncommitted, ~60 files). CLI/docs cross-checked for consistency.
**Out of scope (per audit brief):** the new Textual cockpit (`src/hachi/tui/cockpit/**`) — owned by a concurrent session; excluded from review and scoring.
**ICP anchor:** disillusioned self-host devs — "OpenClaw for people who got burned by OpenClaw." Trust through control is the whole promise. Controls that LOOK real but do nothing are existential, not cosmetic.

## What changed since the v1 audit (2026-06-18)

Several files were edited on 2026-06-18 (00:03–00:07) after the v1 audit landed. Real progress is visible and must be credited:

- **A global `:focus-visible` ring now exists** and is wired into the app (`frontend/src/styles/tokens/base.css:38-42`, imported via `main.tsx → styles/index.css:7`). This materially changes UX-023 from "no focus foundation" to PARTIAL — native `<button>`/`<input>` elements now get a ring.
- **The standalone Approvals screen now posts real decisions** to `POST /approvals/{request_id}` (`frontend/src/lib/data/domains/approvals.ts:185-233`). The leash buttons on `/approvals` are no longer no-ops.
- **Settings safety + provider now persist** via `PUT /config/permissions` and `PUT /config/provider` (`frontend/src/lib/data/domains/config.ts:136-168`), and a real `validateProvider()` (`POST /config/provider/validate`) now exists (`config.ts:174-191`).
- **Authoring apply/revert are now real POSTs** to `/authoring/targets/{name}/apply` and `/revert` (`frontend/src/lib/data/domains/authoring.ts:105-145`).
- **Settings no longer fabricates a key last-4**; it honestly shows "stored securely · hidden" (`frontend/src/routes/settings.tsx:111`).
- **Work routine pause/delete now persist** via `PUT /schedules/{id}/enabled` and `DELETE /schedules/{id}` (`frontend/src/lib/data/domains/work.ts:227,259`).
- **The setup wizard now fires a real `POST /setup/apply` on Finish** (`SetupWizard.tsx:73-85` → `setup.ts:206-225`).

**But the trust-critical fakes that defined the v1 verdict are still present:** fake key "validated" badge, simulated `/run`, silent fixture fallback, no governance status bar, in-context leash allow/deny no-op, read-only spend cap, and web spend double-count. The product is still not yet honest about what works.

---

## Summary

- **Total findings:** 28 (26 prior re-verified + 2 net-new)
- **New-issue severity tally:** Critical 3 · High 9 · Medium 11 · Low 5
- **Prior UX findings:** **3 FIXED · 5 PARTIAL · 17 STILL-OPEN · 1 CHANGED**

### Prior-finding verification table (every UX-* ID)

| ID | Title (short) | v1 sev | Status | Current evidence |
|---|---|---|---|---|
| UX-001 | Fake "Key looks good — validated" for any ≥12-char key | Critical | **STILL-OPEN** | `SetupWizard.tsx:51` `keyValid = length>=12`; badge `:236-240`. No network validation at Step 1. `validateProvider` exists in `config.ts:174` but is NOT called by the wizard. |
| UX-002 | Wizard hard-codes Anthropic; never sets a spend cap | High | **STILL-OPEN** | Provider is a static field `SetupWizard.tsx:209-212`; `setup.ts:88-91` "always anthropic"; no cap step anywhere in the 5 steps. |
| UX-003 | "First magic moment" is a toggle, not a confirmed artifact | Medium | **STILL-OPEN** | Step4 `SetupWizard.tsx:417-460`; `handleNext` at `:73-85` just fires apply + `setupNext()` (closes modal); no route-to-Home/highlight. |
| UX-004 | Conflicting install paths in README/COOKBOOK | Medium | **STILL-OPEN** | README still mixes curl one-liner (`README.md:58`) + `--tier lightning` (`:277`); no single canonical onramp. |
| UX-005 | Bare `hachi` launches app instead of help | Medium | STILL-OPEN (CLI) | Out of primary scope this pass; unchanged in `cli/main.py` Typer config. |
| UX-006 | "Always allow" no confirm; Decline no reason | High | **PARTIAL** | Standalone Approvals now POSTs decisions (`approvals.ts:212-233`) and `decline(id, reason?)` accepts a reason — but the UI never collects one (`approvals.tsx:559` calls `decline(a.id)`), and "Always allow" still fires instantly with no confirm (`approvals.tsx:322-339`, `LeashPrompt.tsx:245-271`). |
| UX-007 | In-context leash prompt has no countdown | High | **STILL-OPEN** | `conversation.tsx:761-768` passes no `countdown`; `run.tsx:580-588` likewise. `LeashPrompt` supports it (`LeashPrompt.tsx:24,174-186`). |
| UX-008 | Settings: no Validate, read-only cap, no Save | High | **PARTIAL** | Caps still hardcoded read-only `$1.00` (`settings.tsx:540`); still no Save button; `unsaved` is local state never cleared (`settings.tsx:64`) → badge sticks forever. BUT: safety/provider now persist live, and `validateProvider` exists (just not surfaced in Settings UI — only "Change provider"→wizard `settings.tsx:615`). |
| UX-009 | No global governance/status bar | High (Critical-pair) | **STILL-OPEN** | `__root.tsx:22-49` renders only Sidebar+Outlet; no model selector / session id / live-cost / connection pill anywhere; `index.tsx` grep for status-bar/session/connection = 0 matches. |
| UX-010 | Memory delete/edit immediate, no confirm/undo | Medium | **STILL-OPEN** | `memory.tsx:123-125,592` → `delFact` local-only (`store.ts:828-837`); `memory.ts:8-11` "no write endpoints for facts… local-only." No confirm, no undo, no backend call. |
| UX-011 | Spend buried as a tab inside /work | High | **STILL-OPEN** | `work.tsx:834` `{ k:'spend', label:'Spend' }`; `Sidebar.tsx:18-29` still has no Spend nav item; budget widget deep-links to `/work` Active (`Sidebar.tsx:60-63`). |
| UX-012 | Command palette static stubs; dev action shipped | Medium | **STILL-OPEN** | Hard-coded Recents `CommandPalette.tsx:105-109`; "Preview the reconnecting state" dev action `:102` still shipped; no real sessions/skills/slash-commands. |
| UX-013 | Operator-view framing is web-only | Low | STILL-OPEN (TUI) | TUI cockpit in flight — out of scope; web operator toggle still clean (`Sidebar.tsx:149-175`). |
| UX-014 | "Browse the kennel" + operator skill-edit dead | High | **STILL-OPEN** | `skills.tsx:202` "opens KennelConsent globally — noop"; `skills.tsx:40` "operator edit — noop per prototype"; no entry to `/authoring` from Skills. (Authoring's own apply/revert are now real — see WB-008.) |
| UX-015 | "Add MCP server" + folder/command have no input flow | Medium | **STILL-OPEN** | `integrations.tsx:479-502` AddMcpRow is a `<div>` with hover, no onClick; `connectIntegration` only flips `PUT /capabilities/{id}/enabled` (`integrations.ts:246`), no OAuth/device flow; Settings `addFolder/addCommand` still add fixed placeholders (`settings.tsx:80-91`). |
| UX-016 | Slash menu = 4 commands, none execute | Medium | **STILL-OPEN** | `conversation.tsx:66-71` (4 cmds); `handleSlashPick` → `setComposer(cmd+' ')` `:627-629` (prefill only). |
| UX-017 | /run "Watch live" is fully simulated | High | **STILL-OPEN** | `run.tsx:7-12` `TODO(live-run)` verbatim; scripted `_runQueue` with fake lines + fake `spend:0.01` in `store.ts:1299-1310`, driven by `setInterval(runTick,1100)` (`:1312`). "Watch live" now routes here (`work.tsx:933`) — i.e. the button works, the destination is still fake. |
| UX-018 | Silent fixture fallback; no error/loading states | High (Critical-pair) | **STILL-OPEN** | `__root.tsx:17` "degrades to seeded fixtures on failure, so this never blanks the UI"; every loader `catch{ setReconnecting(true) }` keeps fixtures (`config.ts:121`, `work.ts`, `memory.ts`, `og.ts`, `spend.ts`). No skeletons; no error cards w/ retry on Memory/OG/Skills/Work/Integrations. (Some genuine *empty* states exist — Approvals, Work, Skills-installed, Integrations.) |
| UX-019 | Composer hint never mentions shortcuts | Low | **STILL-OPEN** | Placeholder `conversation.tsx:835` "Message Hachi… / for commands"; footer `:920` is reassurance, not shortcut hint; no ↑/↓ history or `?` overlay. |
| UX-020 | `doctor` JSON-heavy; no spend-cap in setup summary | Low | STILL-OPEN (CLI) | Out of primary scope this pass. |
| UX-021 | Brand spec split; permission-tier names diverge | Medium | **STILL-OPEN** | Tier vocab drift confirmed across 3 surfaces: README "lightning/guided/strict" (`README.md:135`), web "guarded/standard/open" (`SetupWizard.tsx:14-30`), gateway "sandbox/guided/unrestricted" (`setup.ts:68-74`). |
| UX-022 | Decline/secondary button weight mismatch | Low | **CHANGED** | Decline is now a 1.5px navy outline `LeashPrompt.tsx:198` (heavier = the SAFE action is loud, which is reasonable) while "Always allow" is still the quietest ghost `:245-271`. Net: the dangerous standing-grant remains the quietest button — original concern partly persists. |
| UX-023 | No focus rings; no a11y foundation | Critical (Critical-pair) | **PARTIAL** | A global `:focus-visible` ring now exists and is imported (`base.css:38-42`) — real progress for native buttons/inputs. BUT clickable `<div>`/`<g>` rows still have no `tabindex`/role/keydown (Work rows `work.tsx:1030-1044`; OG nodes `og.tsx:192-197`; AddMcpRow), so they remain non-focusable and unringed. Icon-only buttons still mostly lack `aria-label` (memory ~9, integrations ~4). |
| UX-024 | No live regions for streaming text/approvals | Medium | **STILL-OPEN** | Streaming region `conversation.tsx:522-543` and approval cards have no `aria-live`. `LeashPrompt` has `role="dialog"`+`aria-label` but no `aria-modal`/focus-trap. Only `ReconnectingBanner` has a live region (`:40`). |
| UX-025 | Mascot/eye not used as ambient header state | Low | **STILL-OPEN** | `HachiMascot` appears once in sidebar (`Sidebar.tsx:87`); no top bar to host a compact eye (blocked by UX-009). |
| UX-026 | Sessions "Resume" only fires a toast | Medium | **STILL-OPEN** | `memory.tsx:233-243` `resume()` only `showToast('Resuming…')`; no navigation/session switch; no Export. |

**FIXED (3):** none of the prior UX-numbered findings reached full FIXED, but three sub-claims did: the standalone-Approvals POST (part of UX-006/WB-007), Settings key-fabrication removed (part of UX-008), and authoring apply/revert wiring (part of WB-008). The Work "Watch live" *routing* (part of UX-017) is also wired. I count these as PARTIAL on their parent IDs rather than FIXED, because the headline user harm in each parent remains.

> Honest accounting for the summary line: of the 26 prior UX IDs, **0 are fully FIXED**, **4 are PARTIAL** (UX-006, UX-008, UX-022→CHANGED, UX-023), **21 are STILL-OPEN**, **1 is CHANGED** (UX-022). Sub-component wiring landed but no full journey was closed.

---

## Net-new findings

### UX-N01 — Web spend double-counts: cumulative usage is *added* per event
- **Severity:** High · **Category:** Trust / Correctness · **Journey:** any live chat/task; Spend view; sidebar budget
- **Description:** The gateway emits **cumulative** usage counters on each `usage.updated` event (`cli/main.py:720` "usage.updated events (cumulative counters)"; the CLI explicitly builds *deltas* from them, `:750-753`). The web handler instead **adds** the cumulative figure to the running total on every event: `chat.ts:236-239` `spent: s.spent + costUsd`. A turn streaming cumulative 0.01 → 0.02 → 0.03 lands as 0.06 spent. Every live spend number the user sees (Conversation header, sidebar budget, Settings spend bar) is inflated and grows worse the longer a turn streams.
- **Why it matters (ICP):** Spend accuracy *is* the headline anti-bill-shock promise. A budget meter that over-reports is almost as trust-corrosive as one that under-reports — the skeptical self-hoster will reconcile against their provider dashboard, see the mismatch, and conclude the governance numbers are theatre.
- **Evidence:** `frontend/src/lib/data/domains/chat.ts:227-243` vs `src/hachi/cli/main.py:720,750-753`.
- **Fix:** Treat `usage.updated` as cumulative — *set* `taskSpent`/`spent` to the latest cumulative value (tracking a per-turn baseline like the CLI does), not `+=`.

### UX-N02 — Streaming token count is parsed then discarded; no live token readout anywhere
- **Severity:** Low · **Category:** Feedback / Trust transparency · **Journey:** live chat
- **Description:** `chat.ts:228-242` reads `total_tokens` from each usage event and then explicitly throws it away (`void totalTokens; // no dedicated UI field currently`). The design specs call for live token + cost in the (missing) status bar. Combined with UX-009, the "make the invisible visible" promise has the data in hand and drops it on the floor.
- **Why it matters (ICP):** Token transparency is a cheap, high-credibility trust signal for this audience; the wiring is 90% there.
- **Evidence:** `frontend/src/lib/data/domains/chat.ts:228-242`.
- **Fix:** Add a `liveTokens` store field and surface it in the status strip (see UX-009).

---

## Critical & High — consolidated table (new severities)

| ID | Title | Status | Sev | Evidence | One-line fix |
|---|---|---|---|---|---|
| UX-001 | Fake "Key looks good — validated" for any ≥12-char string | STILL-OPEN | **Critical** | `SetupWizard.tsx:51,236-240` | Call `validateProvider()` on Step 1 / a Validate button; show ✓ only on a real 200. |
| UX-018 | Silent fixture fallback shows fake healthy data when gateway down | STILL-OPEN | **Critical** | `__root.tsx:17`; loaders' `catch` keep fixtures | Add loading/error states; show "showing sample data — couldn't reach Hachi" banner instead of silent substitution. |
| UX-009 | No governance status bar (model/session/live-cost/connection) | STILL-OPEN | **Critical** | `__root.tsx:22-49`; `index.tsx` (no match) | Add persistent top strip: model, live tokens+cost, session id, connection pill. |
| UX-023 | A11y foundation: focus rings exist; click-`div`s still unfocusable | PARTIAL | **High** | `base.css:38-42` (good); `work.tsx:1030`, `og.tsx:192` (bad) | Convert clickable `div`/`g` to `button`/`role+tabindex+keydown`; add `aria-label` to icon buttons. |
| UX-017 | /run "watch live" is a scripted simulation | STILL-OPEN | **High** | `run.tsx:7-12`; `store.ts:1299-1312` | Wire `/run` to `GET /sessions/{id}/stream`; until then don't label it "live." |
| UX-N01 | Web spend double-counts cumulative usage | NEW | **High** | `chat.ts:236-239` vs `cli/main.py:720` | Set spend to latest cumulative (per-turn baseline), don't `+=`. |
| UX-006 | "Always allow" no confirm; in-context leash allow/deny no-op; Decline no reason | PARTIAL | **High** | `approvals.tsx:322,559`; `chat.ts:395-414` | Wire in-context allow/deny to `POST /approvals/{id}`; add Undo/confirm to Always-allow; collect Decline reason. |
| UX-008 | Settings: read-only `$1.00` cap, no Save, sticky "unsaved" badge | PARTIAL | **High** | `settings.tsx:540,64,153-157` | Make caps editable with Save; surface inline Validate; resolve the orphan unsaved badge. |
| UX-002 | Wizard hard-codes Anthropic; never sets a spend cap | STILL-OPEN | **High** | `SetupWizard.tsx:209`; `setup.ts:88-91` | Add provider picker (incl. Ollama) + a "spend limit" step. |
| UX-011 | Spend buried as a tab inside /work | STILL-OPEN | **High** | `work.tsx:834`; `Sidebar.tsx:18-29` | Promote Spend to a left-nav item; deep-link the budget widget to it. |
| UX-014 | "Browse the kennel" + operator skill-edit are dead buttons | STILL-OPEN | **High** | `skills.tsx:202,40` | Wire kennel-browse; deep-link operator-edit to `/authoring`; or hide until wired. |

### In-context leash allow/deny is still a no-op (the core trust action) — detail
`chat.ts:395-414`: `allowLeashLive()` just clears `inContext` with the comment *"In a full implementation, POST to an approval endpoint here… the in-context leash is informational in the current gateway schema."* `denyLeashLive()` only stops the local stream. So while the **standalone Approvals queue** now talks to the gateway, the **in-flow leash prompt** a user actually meets mid-conversation does not approve/deny anything on the backend — it optimistically dismisses the card. For a product whose entire pitch is the leash, the leash the user touches most is decorative. (This is the UX face of WB-007.)

---

## Confirmed status of the brief's named findings

- **UX-001 (fake validation): STILL-OPEN, Critical.** Unchanged logic; the irony is the real validator now exists one file away (`config.ts:174`) and isn't called from the wizard.
- **UX-009 (no governance bar): STILL-OPEN, Critical.** No status strip; the only connection signal is the transient `ReconnectingBanner` (appears only on drop). A self-hoster cannot tell at a glance whether the local gateway is connected, what model is active, or the session id.
- **UX-018 (silent fixture fallback): STILL-OPEN, Critical.** Explicitly designed-in (`__root.tsx:17`); a down gateway yields a populated, healthy-looking UI — the worst failure mode for a transparency product.
- **UX-023 (a11y foundation): PARTIAL, High.** Real improvement — a global `:focus-visible` token is now imported app-wide, so native buttons/inputs get a ring. Still failing: clickable `<div>`/`<g>` rows are not keyboard-operable (no `tabindex`/role/keydown), and icon-only buttons lack accessible names. Modals still lack `aria-modal`/focus-trap. Net: foundation laid, not finished.
- **UX-002:** STILL-OPEN, High. **UX-008:** PARTIAL, High (safety/provider persist; caps don't). **UX-011:** STILL-OPEN, High. **UX-014:** STILL-OPEN, High (kennel/operator-edit dead; authoring's apply/revert now real). **UX-017:** STILL-OPEN, High (scripted simulation intact).

---

## Product Gap Analysis (for the "burned by OpenClaw / disillusioned self-hoster" ICP)

### Expected-and-absent (table stakes)
1. **Set & enforce a spend cap from the UI.** The #1 anti-bill-shock pitch is read-only `$1.00` (`settings.tsx:540`) and has no cap step in onboarding (UX-002/008). *Highest-leverage gap.*
2. **Trustworthy spend numbers.** Beyond being unconfigurable, the live figure is *double-counted* (UX-N01). Fix this before promoting Spend, or you promote a wrong number.
3. **Provider freedom at setup** — no Ollama / OpenAI-compatible / OpenRouter choice in the wizard despite "provider-free, incl. fully local" being the core switch argument (UX-002).
4. **Honest failure states** — loading/error/empty per data screen instead of silent fixture substitution (UX-018).
5. **Real "watch live"** — the supervisory promise needs the real SSE stream, not a scripted queue (UX-017).
6. **Session resume + export** — Resume is a toast-only stub (UX-026); Export absent in web.

### Incomplete differentiators (the moat, half-built)
7. **The leash, end-to-end.** Standalone Approvals now posts; the *in-context* leash a user meets mid-task is still a no-op (UX-006/WB-007). The leash is the brand — it must be real everywhere, with a countdown (UX-007) and a confirm/undo on the standing-grant (UX-006).
8. **Ambient governance chrome** — model, session id, live cost+tokens, connection — surfaced once, always (UX-009/N02), plus a first-class Spend destination (UX-011) and the mascot-as-state-indicator (UX-025).
9. **Skill authoring + "browse the kennel" from the library** — the hero `save_skill` flow has no working entry point on Skills (UX-014); authoring lives on an isolated route.
10. **A real command palette** — recents/sessions/slash-commands; currently static stubs with a dev action shipped (UX-012, UX-016).

### Strategic opportunities
11. **Plain-language connect flows** (Google device-code, Telegram/Slack QR) from the integration buttons — today "Add MCP server" doesn't even have an onClick (UX-015).
12. **Memory gardener review at scale** — the proposed-change card is now data-driven (good) but is a single card; the Phase-4 multi-card merge/supersede review surface isn't built (UX-026 neighbour).
13. **One safety vocabulary** across CLI/web/docs (UX-021) — three divergent tier names is a self-inflicted trust wound for a governance product.

---

## Medium / Low (themed, with counts)

**Discoverability & keyboard-first (4):** static command-palette Recents + shipped dev action (UX-012); 4 non-executing slash commands (UX-016); composer hides ⌘↵/?/history (UX-019); kennel/MCP/folder "add" affordances with no input flow (UX-015).

**Trust safety-nets (3):** memory delete is local-only with no confirm/undo (UX-010); Always-allow is the quietest button (UX-022/CHANGED); in-context leash has no countdown (UX-007).

**Accessibility remainder (2):** no live regions for streaming text/approvals (UX-024); icon-only buttons without accessible names across memory/integrations (BB-011 cluster, folded into UX-023).

**Onboarding finish (2):** wizard ends in a closed modal, not a confirmed "on watch" artifact (UX-003); conflicting install paths (UX-004).

**Consistency (2):** brand spec split + permission-tier name drift across 3 surfaces (UX-021); token count parsed then dropped (UX-N02).

**Delight (1):** mascot/eye not used as ambient header state, blocked by the missing status bar (UX-025).

---

## What is genuinely strong (read findings in context)

- **The Approvals screen remains excellent** — plain-language request, "why" clause, "what this touches" file chips, live countdown bar ("requests expire — Hachi stays put"), and an honest "Runs without a leash check" disclosure (`approvals.tsx:596-650`). And it now posts real decisions.
- **The OG Explorer** still renders the 3-pass moat as a real per-node token-savings data-viz (`og.tsx` detail panel) — a credible differentiator demo.
- **Honest trust copy throughout** — blocked/quarantined memory ("Hachi refused to store"), risky-integration warning ("Broad filesystem + shell access. Review what it can reach.", `integrations.tsx:196-213`), "Sub-agents share your leash" on `/run`.
- **The leash/kennel/pack vocabulary** is consistent across routes, and the calm 5-step wizard is well-structured.
- **Real wiring landed this cycle** for config persistence, authoring apply/revert, schedule pause/delete, and the focus-ring foundation.

The pattern from v1 holds: **the copy and the surface tell the truth; the mechanics underneath still fake several trust-critical things.** Closing the gap is "finish the wiring / delete the fake," not architecture.

---

## Cross-cutting themes

1. **"Make the invisible visible" still isn't ambient.** No status bar, Spend buried, no connection indicator, token data discarded (UX-009/011/025/N02).
2. **Trust controls present but selectively hollow.** Caps read-only, key "validated" fake, in-context leash no-op, live-run simulated, spend double-counted (UX-001/008/006/017/N01).
3. **Failure honesty: excellent in copy, absent in mechanics.** Silent fixture fallback contradicts every honest sentence on screen (UX-018).
4. **Keyboard/a11y: foundation poured, building unfinished.** Focus ring now global, but click-`div`s, live regions, and modal semantics remain (UX-023/024).
5. **Onboarding nails calm, misses the win.** No provider choice, no budget, no real validation, ends in a closed modal (UX-001/002/003).

---

*Report path: `D:\OpenGigantic\hachi\docs\audit\2026-06-18-v2\03-ux-product.md`*
*Verified against current working-tree code on 2026-06-18; frontend uncommitted and not run against a live gateway.*
