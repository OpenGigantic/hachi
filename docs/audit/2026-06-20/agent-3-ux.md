# Agent 3 — UX & Product Experience Review (2026-06-20)

**Reviewer lens:** Product Manager. **Repo:** D:\OpenGigantic\hachi. **ICP:** disillusioned
self-host developers ("OpenClaw for people burned by OpenClaw"). **Moat:** the `.og` ObjectGraph
skill format.

This review verifies whether the UX/product remediations claimed in PRs #116–129 (post the
2026-06-19 audit) are real and effective, and surfaces remaining adoption/retention risks.

---

## Verdict at a glance

The frontend UX remediations are **largely real and well-executed** — the `.og` moat is now
explained at three entry points, brand vocabulary is glossed, first-run guidance exists, empty
states are guarded, and slash-command parity + keyboard nav landed. **But the single biggest
adoption blocker from the prior audit — the two-terminal setup (ONB-001/CRIT-008) — is STILL
OPEN: there is no `hachi serve` command anywhere in the codebase.** Session resume is also still
a stub. And the moat's *acquisition* flow (kennel browse → add skill) is now cosmetically wired
but remains **mock-only** with no live backend, so the differentiator is visible but not usable.

---

## Findings table

| ID | Title | Severity | Verdict | Screen / file |
|----|-------|----------|---------|---------------|
| UX-01 | Two-terminal setup persists — no `hachi serve` single-process path | Critical | STILL-OPEN | `cli/main.py`, `docker-compose.yml`, `README.md` |
| UX-02 | Kennel browse / add-skill is mock-only — moat acquisition unusable in live mode | High | PARTIAL | `skills.tsx`, `store.ts`, gateway |
| UX-03 | Session resume still prints "not yet wired" and starts fresh | High | STILL-OPEN | `cli/main.py:1860`, `memory.tsx` |
| UX-04 | Memory recall invisible at point of use (no "recalled N facts" in chat) | Medium | STILL-OPEN | `conversation.tsx` |
| UX-05 | Live Run screen is an honest static placeholder — no observability | High | PARTIAL | `run.tsx`, `work.tsx` |
| UX-06 | `.og` moat now explained in primary skill flow | High | FIXED | `skills.tsx`, `og.tsx`, `authoring.tsx` |
| UX-07 | Brand vocabulary (leash/kennel/pack) glossed on first encounter | High | FIXED | `index.tsx`, approvals, `run.tsx` |
| UX-08 | First-run guidance + integration-filtered starters on Home | High | FIXED | `index.tsx` |
| UX-09 | Slash-command parity + keyboard nav in web composer | Medium | FIXED | `conversation.tsx` |
| UX-10 | Memory screen split into Recall / Audit tabs (cognitive load) | Medium | FIXED | `memory.tsx` |
| UX-11 | Empty/loading/overflow states guarded across screens | Medium | FIXED | multiple |
| UX-12 | Authoring Generate wired; Apply/Revert call real adapters | Critical→ | PARTIAL | `authoring.tsx` |
| UX-13 | README leads with competitor anchoring + advertises broken flows | Low | STILL-OPEN | `README.md` |
| UX-14 | Lightning onboarding tier disables memory — fastest path kills retention hook | Medium | STILL-OPEN | onboarding service |
| UX-15 | No session selector/title in web Conversation screen | Medium | STILL-OPEN | `conversation.tsx` |
| UX-16 | Spend caps display-only; not editable from any UI | Medium | STILL-OPEN | `settings.tsx` |
| UX-17 | No push/browser notification for pending approvals — agent stalls silently | Medium | STILL-OPEN | store / gateway |
| UX-18 | Web frontend desktop-only — no mobile approval monitoring | Low | STILL-OPEN | all routes |
| UX-19 | "Applied" confirmation hardcodes "re-indexed in 0.3s" fabricated timing | Low | NEW | `authoring.tsx:868` |

**Severity counts:** Critical 1 (+1 partial) · High 5 · Medium 8 · Low 4 · (1 NEW).

---

## Critical & High findings (detail)

### UX-01 — Two-terminal setup persists; `hachi serve` does not exist (Critical, STILL-OPEN)

**Problem.** A grep across `src/hachi/` for `serve` returns *zero* command definitions. The
registered Typer commands are: `run`, `cockpit`, `run-repl`, `gateway`, `gateway-status`,
`gateway-openapi`, `quickstart`, `setup`, plus sub-apps (`schedule`, `channel`, `skill`, `mcp`,
`memory`, `session`, `daemon`, `pack`). `README.md` still documents the web UI as:

```
# Terminal 1: gateway
uv run hachi gateway --host 127.0.0.1 --port 8787 --runtime
# Terminal 2: frontend dev server
cd frontend && npm install && npm run dev
```

`docker-compose.yml` likewise runs two services (`hachi` gateway + `frontend` Vite on :5173).
The prior audit's E1-T5 acceptance criterion ("`uv run hachi serve` starts within 10 s, opens
browser, no second terminal, no Node required") is unmet. BE-1 (#128) — the single-process
`serve`/onboarding PR — is confirmed **not landed**.

**Impact.** This is the #1 adoption blocker for the exact ICP Hachi targets. A disillusioned
self-host dev evaluating Hachi against OpenClaw's single-command setup hits a 15–25 min,
Node-requiring, two-terminal wall before first value. The README's own "Quickstart (one-liner
install)" promise is contradicted three sections later by the two-terminal web instructions.
Note: the TUI path (`hachi run`) *does* boot an in-process gateway (`_launch_cockpit`), so the
**terminal** experience is single-process — but the **web** experience (the one screenshotted in
marketing and the brand design) is not.

**Recommendation.** Ship `hachi serve`: start the gateway on loopback with an auto-generated
token, serve the pre-built frontend bundle as static files from the same process, and open the
browser. Gate it so Node is not required at runtime. Until then, update the README to stop
implying a one-command web experience.

### UX-02 — Kennel browse / add-skill is mock-only (High, PARTIAL)

**Problem.** CRIT-003's "Browse the kennel" noop is *cosmetically* fixed: `skills.tsx:275`
wires the button to `openKennelBrowse`, and `KennelCard` calls `addSkill`. But in live mode the
kennel is empty — `store.ts:352`: `kennel: USE_MOCKS ? kennel : []` — and `confirmKennel`
(`store.ts:871`) only pushes an item into local React state with a success toast. There is **no
`POST /skills/add` call and no kennel-listing gateway endpoint** (`fastapi_app.py` exposes only
`GET /skills` returning installed skills via `control_plane.skills()`). The skills domain adapter
(`domains/skills.ts`) has a loader for installed skills but no "available skills" fetch.

**Impact.** The `.og` skill library is Hachi's stated moat and primary growth vector (vs.
OpenClaw's marketplace). A live-mode user clicks "Browse the kennel," sees an empty overlay, and
even if items existed, "Add" would not persist anything to disk. The moat's acquisition funnel is
broken at the entry point — the prior audit's E4-T3 is not delivered end-to-end.

**Recommendation.** Implement a kennel-listing endpoint (can seed from `bundled_packs.py`),
wire `confirmKennel` to `POST /skills/add` with disk write + skill-engine re-index, and surface
the consent/scope sheet. This is the highest-leverage product gap after `hachi serve`.

### UX-03 — Session resume still a stub (High, STILL-OPEN)

**Problem.** `cli/main.py:1860-1864`: when `--resume`/`--continue` is passed, the cockpit prints
`"Session resume is not yet wired into the cockpit; starting a fresh session."` and continues.
`_resolve_resume_target` (line 1793) computes the target session id correctly, but
`_launch_assistant` discards it. On the web side, `memory.tsx:326` "Resume" navigates to
`/conversation` and shows an *info* toast admitting context is "shown above for reference" only —
no context injection. CRIT-007 / HIGH-011 remediations are cosmetic, not functional.

**Impact.** Documented `hachi -c`/`-r` flags silently lose state. A user who relies on
"pick up where you left off" (a literal Memory-screen header) duplicates work. Trust erosion on a
flagship retention feature.

**Recommendation.** Load the resolved session's history from `SessionStore` into the gateway's
session at boot; remove the yellow warning when resume succeeds; non-zero exit on bad id.

### UX-05 — Live Run screen is an honest placeholder (High, PARTIAL)

**Problem.** `run.tsx` now renders `LiveRunHonest` in live mode — a card explaining run
streaming "is available in the conversation view" with an "Open conversation" button. This is an
*honest* improvement over the prior silent static card (no longer misleading), but
sub-agent/pack observability — a differentiator shown richly in mock mode (`MockRunSimulation`
with plan, pack, live terminal) — remains absent in live mode because the gateway has no
run-streaming endpoint.

**Impact.** "Watch live" is a core trust signal for an autonomous agent. Users cannot observe
sub-agent orchestration, step progress, or live cost in the real product. The gap between the
polished mock and live reality persists, now labeled honestly.

**Recommendation.** Map `runtime_events.py` typed events to a run-scoped SSE endpoint and drive
the existing (already-built) `MockRunSimulation` layout from it.

### UX-06 — `.og` moat now explained in the primary flow (High, FIXED)

The moat is surfaced at three entry points, all dismissible + `localStorage`-persisted:
- `skills.tsx` `OgExplainerPanel` — three-pass reading explanation.
- `og.tsx` — explainer banner + a "Why?" tooltip + a three-pass card + a token-savings bar
  showing `−N% tokens` and `label = index-pass tokens · size = full size`.
- `authoring.tsx` — `.og` banner with a "Why?" tooltip describing Pass 1/2/3.

This is the strongest remediation in the set and directly addresses the prior "moat is invisible"
finding (HIGH-020 / E6-T1). Caveat: the `authoring.tsx` banner claims "up to 70%" while the
README/benchmark claims "97.6%" — the conservative in-product number is defensible, but the two
should be reconciled for consistency.

### UX-07 — Brand vocabulary glossed (High, FIXED)

`index.tsx` uses "waiting on the leash" with a leash icon; `run.tsx` has a "Sub-agents share your
leash" disclaimer and a "The pack" section with a paw icon; LeashPrompt is used inline. Brand
terms now carry inline meaning rather than appearing cold. (E6-T7.)

### UX-08 — First-run guidance + filtered starters (High, FIXED)

`index.tsx` `FirstRunPanel` renders when `sessions.length === 0` with "Start your first task" and
"Connect integrations" CTAs. Starter chips are filtered by connected integrations
(`connectedIntegrationIds`), so a Gmail starter no longer appears without Gmail connected
(HIGH-019 / LOW-005 / E4-T6). Solid.

---

## Medium findings (detail)

### UX-04 — Memory recall invisible at point of use (Medium, STILL-OPEN)

`conversation.tsx` contains no "recalled N facts" / "remembered" indicator — grep for
recall/remembered/fact in the conversation route returns only the slash-menu items. The Memory
*screen* now has a clean Recall tab (UX-10), but the prior MED-030 finding — that recall is
invisible *inside the conversation turn* — is unaddressed. The user cannot tell when long-term
memory influenced an answer. Hermes' integrated FTS5 recall is explicitly cited as ahead; this is
where that gap shows.

**Recommendation.** Emit a `memory.recall.surfaced` runtime event and render a compact
"recalled N facts" chip above the assistant message, expandable to show sources.

### UX-10 — Memory split into Recall / Audit tabs (Medium, FIXED)

`memory.tsx` now uses a `TabBar` (Recall vs Audit & History), directly addressing MED-019's
"six unrelated mental models" overload. `recallDone` starts `false` (HIGH-010 fixed — fixture
data no longer shown pre-query), recall has a loading state (LOW-008), and the example query is
removed (LOW-006). Clean.

### UX-11 — Empty/loading/overflow states guarded (Medium, FIXED)

Verified: `index.tsx` `BudgetSummary` guards `budget <= 0` ("unlimited"), approvals slice shows
"+N more" (LOW-010), Home shows empty states. `skills.tsx` has installed empty-state and only
renders "From the kennel" when non-empty (LOW-007). `authoring.tsx` has a no-targets empty state
(LOW-009) and derives diff counts instead of the old hardcoded "+3 −2" (LOW-003). `memory.tsx`
guards every section. These were the bulk of the Low UX backlog and are genuinely done.

### UX-14 — Lightning tier disables memory (Medium, STILL-OPEN)

Per prior MED-029, the fastest onboarding tier skips memory setup — meaning the path most new
users take disables the core retention mechanic (and renders the whole Memory screen empty). Not
addressed. Recommendation: enable semantic memory by default even on Lightning; make *prompting*
for keys lazy, not memory itself.

### UX-15 / UX-16 / UX-17 — Session selector, editable spend caps, approval notifications
All three remain STILL-OPEN per the prior audit (LOW-020, MED-018, MED-024). Spend caps are
display-only despite "hard cost caps" being a headline differentiator vs. OpenClaw — a user
cannot *set* the cap from any UI, only view it. No notification path means a backgrounded agent
stalls silently on an approval the user never sees.

---

## Low / New findings

- **UX-13 (Low, STILL-OPEN).** README subtitle "OpenClaw-class autonomy" anchors to a competitor
  at first impression (LOW-019), and the README advertises flows that are stubs in live mode
  (kennel, resume, web one-command). Honest-marketing risk for an ICP that is *specifically*
  burned-out skeptics — overclaiming is the fastest way to lose them.
- **UX-18 (Low, STILL-OPEN).** Desktop-only frontend; no responsive layout for mobile approval
  monitoring (LOW-025). For an always-on personal agent, glancing at "what needs you" from a phone
  is a natural expectation.
- **UX-19 (Low, NEW).** `authoring.tsx:868` hardcodes a post-apply confirmation
  "Written to ~/.hachi/skills/{authSel} · re-indexed in 0.3s". The "0.3s" is fabricated timing and
  the path is assumed regardless of target kind (brain/config live elsewhere). Minor trust leak;
  show the real path + drop the invented duration.

---

## Missing Product Capabilities

Features users expect but absent; incomplete differentiators; strategic opportunities.

1. **`hachi serve` single-process web launch** (blocker). The web experience requires two
   terminals + Node; the brand/marketing surface is the web UI. This is the gap between the
   one-command promise and reality.
2. **Live kennel / skill marketplace backend.** Listing endpoint + persistent add + scope-consent
   sheet. The moat's growth vector is non-functional in live mode.
3. **Memory recall at point of use.** "Recalled N facts" chip in the conversation turn — the
   single clearest way to make the long-term-memory differentiator *felt*, and to close the
   stated Hermes gap.
4. **Live run/sub-agent observability.** Run-scoped SSE driving the already-built run UI. "Watch
   the agent work" is table stakes for autonomous-agent trust.
5. **Functional session resume** across CLI and web — restore context, not just navigate.
6. **Editable spend caps from the UI.** Hard cost caps are a headline claim vs. OpenClaw but are
   read-only in Settings; let users set per-task/daily caps.
7. **Approval notifications** (browser/web push + responsive mobile view) so a backgrounded agent
   doesn't stall unseen.
8. **Session selector + titles** in the web Conversation screen (currently a single anonymous
   thread).
9. **Memory-on-by-default for Lightning tier** so the fastest path doesn't kill retention.
10. **Authoring end-to-end honesty** — confirm Generate→Apply→Revert actually round-trips to disk
    in live mode (UX-12 is PARTIAL; verify the gateway handlers are no longer stubs and surface
    real paths/timing).
11. **Discoverable multi-agent delegation** — `/delegate` is now in the slash menu (good), but
    there's still no explanation of what the pack is or when to use it outside the run screen.

---

## Assessment

The 2026-06-19 frontend UX backlog was worked seriously and honestly: the `.og` moat is now
explained at every relevant entry point, brand vocabulary is glossed, first-run guidance exists,
and the long tail of empty/loading/overflow bugs is genuinely fixed — this is a real step up in
polish and trust. However, the two findings that most determine whether the target ICP ever
reaches first value — the **two-terminal setup (no `hachi serve`)** and a **live, persistent
kennel acquisition flow** — are unfixed; the former is fully open and the latter is cosmetic-only.
Combined with still-stubbed session resume and invisible in-conversation recall, the product
still demos far better than it lives: a self-host skeptic who installs it today will find the
moat *explained* but not yet *usable*, behind a setup wall the README promised wasn't there.

**Net UX/product readiness: not release-ready for the web surface.** Ship `hachi serve` and a live
kennel backend before any public web demo; the rest of the merged remediations are sound.
