# Hachi — Black-Box (End-User) Audit

> **QA Agent 1 — Black Box Testing.** Audited as an end user across the **Web SPA**, **CLI**, and **Textual TUI**.
> **Date:** 2026-06-18 · **Branch:** `main` · **Scope:** user-facing workflows, UX, accessibility, error/empty/loading states, keyboard nav, mobile responsiveness, onboarding, discoverability, visual consistency.
> **Stance:** This is a READ-ONLY audit. No source/test/config files were modified.

## Intro & how this differs from prior QA

The existing QA corpus (`docs/QA_BUG_REPORT.md`, `docs/UAT_RESULTS.md`, `docs/UAT_REMEDIATION.md`, `docs/UAT_BETA_TEST_PLAN.md`, `docs/uat/*.md`) is **security/correctness-heavy** and has been through a remediation campaign (`UAT_REMEDIATION.md`, 2026-06-14) that closed most of the P0 meta-risks (gateway auth, tarball traversal, approval gate, secret redaction, delegation fail-closed, memory-write loudness, schedule file-lock). I **re-verified** several of those during this pass and confirm two as genuinely fixed (TUI mid-stream Esc cancel; TUI tool-I/O redaction) — those are noted, not re-filed.

This audit instead targets the gap the prior passes left: the **lived end-user experience** of the three surfaces — empty/loading/error states, accessibility (ARIA, roles, labels, focus, contrast), keyboard operability, mobile responsiveness, onboarding honesty, dead controls, and trustworthiness of what the UI shows. The single largest theme: the Web SPA is a **faithfully-ported visual prototype** in which a number of surfaces are still simulated/inert and data silently falls back to fabricated fixtures — a serious trust problem for a product whose whole pitch is "you can trust what the agent is doing."

The rename from `tokoboros` → `hachi` is **clean** — grep across `src/` and the live CLI/TUI output found **zero** `tokoboros` leftovers.

---

## Findings

### BB-001 — `hachi config-get` crashes with a raw traceback on every invocation
- **Severity:** Critical · **Area:** CLI
- **Reproduction:**
  1. `uv run hachi config-get`
  2. `uv run hachi config-get provider.model`
  3. `uv run hachi config-get safety_tier`
- **Expected:** Per its own help ("Show one or all effective configuration values"), it prints config value(s).
- **Actual:** All three crash with a full Rich-rendered Python traceback ending in `AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'`. The flat dict is built eagerly before any per-key lookup, so even valid keys never resolve.
- **Evidence:** Verified live (captured output): `AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'` at `src/hachi/cli/main.py:2070` (`"safety_tier": cfg.safety_tier`). `ResolvedConfig` exposes tier at `permissions.tier`, tokens at `agent.max_tokens` (`src/hachi/core/settings.py:85-97`); `session_cost_cap_usd` is not modeled at all.
- **Business impact:** A headline read-only inspection command is 100% broken and leaks a stack trace — reads as unfinished/unstable to evaluators and breaks any CI that inspects config.
- **User impact:** No way to query config from the CLI; the traceback is alarming and unactionable for non-technical users.

### BB-002 — `hachi config-set` crashes for 3 of its 5 documented writable keys
- **Severity:** Critical · **Area:** CLI
- **Reproduction (code-evidence; not executed to avoid mutating state):**
  1. `uv run hachi config-set safety_tier guided`
  2. `uv run hachi config-set max_tokens 4096`
  3. `uv run hachi config-set session_cost_cap_usd 5`
- **Expected:** Each persists the value (the docstring + `else` branch advertise exactly these keys).
- **Actual:** Three assign to attributes that don't exist on `ResolvedConfig` → `AttributeError` (same root cause as BB-001, which is confirmed live on the same `safety_tier` attribute). Only `provider.model` / `provider.base_url` work.
- **Evidence:** `src/hachi/cli/main.py:2113-2123` (`cfg.safety_tier = value`, `cfg.max_tokens = int(value)`, `cfg.session_cost_cap_usd = float(value)`); docstring at `main.py:2102-2104,2126-2129`; model fields `src/hachi/core/settings.py:85-97`.
- **Business impact:** The primary scriptable config-mutation surface is broken for the safety tier and token/cost caps — the very controls the product positions as differentiators.
- **User impact:** Following the help text to change safety tier or budget caps yields a crash; the only working path is hand-editing `settings.toml`.

### BB-003 — Web SPA is desktop-only: zero width-based responsive logic; every screen overflows below ~900px
- **Severity:** High · **Area:** Web
- **Reproduction:**
  1. Open the app at any width < ~900px (phone, narrow window, split-screen).
  2. Load any screen.
- **Expected:** Layout reflows to a single column / collapsible nav on small viewports.
- **Actual:** No responsive code exists anywhere. The only `@media` rules in the entire frontend are `prefers-color-scheme` and two `prefers-reduced-motion` blocks — **no `max-width`/`min-width` query, no `window.innerWidth`, no `matchMedia`** (verified by grep). The shell is a fixed flex row with a 248px `<aside>` and `height:100vh; overflow:hidden` (`frontend/src/routes/__root.tsx:22-49`, `Sidebar.tsx:66-77`). Screens use hardcoded multi-column grids that never collapse: Memory `gridTemplateColumns:'1fr 1fr'` (`routes/memory.tsx:580`); Skills `'1fr 1fr'`/`'1fr 1fr 1fr'` (`routes/skills.tsx:272,299`); Home `repeat(auto-fill,minmax(420px,1fr))` + a hard `'1fr 1fr'` (`routes/index.tsx:611,645`); stat cards `repeat(4,1fr)` (`routes/og.tsx:928`, `work.tsx:556`); Authoring fixed `'260px minmax(0,1fr)'` (`routes/authoring.tsx:163`); Run a fixed 312px panel (`routes/run.tsx:242`); Work detail panel fixed 440px (`work.tsx:231`).
- **Evidence:** grep result: only 3 `@media` in `frontend/src` (`styles/tokens/colors.css:49`, `ui/LoyalLoop.tsx:48`, `ui/Terminal.tsx:126`), none width-based. Plus the line refs above.
- **Business impact:** Unusable on mobile/tablet; any mobile visit bounces. Directly contradicts a "check on what Hachi is doing from your phone" personal-agent positioning. (Prior `UC-MOB-001` assumed "responsive present" — that assumption does not hold for width.)
- **User impact:** Horizontal scrolling, clipped cards, two-up grids squeezed to slivers, unreachable buttons.

### BB-004 — Failed data loads silently render fabricated seed data as if it were real
- **Severity:** High · **Area:** Web / Cross-cutting
- **Reproduction:**
  1. Start the SPA while the gateway is down or returns an error.
  2. Open Work, Memory, OG explorer, Skills, Integrations, or Settings.
- **Expected:** A clear error/offline state ("Couldn't reach Hachi — retry"), or at minimum a visible "sample/stale data" marker.
- **Actual:** Every domain loader catches the error, calls `setReconnecting(true)`, and leaves seeded fixtures rendered; the screens have no error branch and keep reading store slices that still hold fixtures. The only signal is a small global "Reconnecting…" pill; the screen content gives no indication it is fake.
- **Evidence:** `frontend/src/lib/data/domains/work.ts:201-202`, `memory.ts:116-117`, `og.ts:55-56`, `skills.ts:77-78`, `integrations.ts:212-213`, `config.ts:121-123`. Loader-registry pattern in `frontend/src/lib/data/bootstrap.ts`. `__root.tsx:16-20` comment: "Each loader degrades to seeded fixtures on failure, so this never blanks the UI."
- **Business impact:** Users make decisions (approve spend, trust "connected" integrations, read "memory facts") based on invented data. For a trust/safety product this is a credibility and potential-safety problem.
- **User impact:** A user reads sample facts in Memory or sample runs in Work and believes they are real, with no way to tell.

### BB-005 — `/run` screen is a self-described simulation that fabricates live output, "pack" activity, and a running spend meter
- **Severity:** High · **Area:** Web
- **Reproduction:**
  1. From Home or Work, click a run / "Watch live" → lands on `/run`.
  2. Watch the streaming terminal lines, "the pack" members, steps, and "spent X of Y".
- **Expected:** Real run output, or an honest "not yet streaming" state.
- **Actual:** The file header documents it: the simulation (`openRun` queue + `_runIv` interval) is "left intact until the live session/run mapping is defined." Output is driven by a store-side timer, not gateway SSE; it even synthesizes a leash/approval prompt mid-run. Navigating to `/run` with no active run auto-boots a fake one from the first store run. Nothing on screen flags it as simulated.
- **Evidence:** `frontend/src/routes/run.tsx:1-12` (header comment), `run.tsx:107-111` (auto-boot fake run), `run.tsx:567-590` (synthesized leash prompt).
- **Business impact:** Users watch invented activity and a fake cost meter believing it's their real task — contradicts "cost is a feature, never hidden."
- **User impact:** False sense of what Hachi is doing/spending; a fabricated leash prompt may be approved/declined as if real.

### BB-006 — Onboarding (Web SetupWizard) falsely claims the API key is "validated"
- **Severity:** High · **Area:** Web
- **Reproduction:**
  1. Open the SetupWizard (first run, or command palette → "Re-run setup").
  2. On Step 1, paste any 12+ character string into "API key".
- **Expected:** Either real validation (a test call) or honest copy ("we'll verify when you first chat").
- **Actual:** Validity is purely a length check — `keyValid = setup.key.trim().length >= 12` — and the UI then shows a green check with "**Key looks good — validated.**" No provider call is made. A bogus 12-char string is reported as validated.
- **Evidence:** `frontend/src/components/overlays/SetupWizard.tsx:51` (`length >= 12`), `:236-240` ("Key looks good — validated.").
- **Business impact:** Onboarding tells users their key works when it may not; they hit failures only later, in chat, with no link back to the wizard.
- **User impact:** False reassurance during the single most important onboarding step; misleading "validated" claim.

### BB-007 — Web modals/dialogs lack dialog semantics, focus trap, and Esc-to-close (Work detail, SetupWizard, KennelConsent, `/run` leash)
- **Severity:** High · **Area:** Web (accessibility)
- **Reproduction:**
  1. Open the Work-detail slide-over (open any run). Press Esc; press Tab repeatedly.
  2. Open the SetupWizard. Press Esc; click the dimmed backdrop.
  3. Open KennelConsent (add a skill). Press Esc; Tab.
- **Expected:** `role="dialog"` + `aria-modal`, focus moved into and trapped within the dialog, Esc closes, backdrop click closes (consistently).
- **Actual:** None of these are dialogs. Work detail is plain `<div>`s with a click-catching backdrop, no `role`/`aria-modal`/Esc/focus management (`routes/work.tsx:213-238`). SetupWizard has a fixed backdrop but **no `role`, no Esc, and the backdrop does NOT close it** (the only `onClick` is on the panel with `stopPropagation`; there is no backdrop handler) — so the sole exit is the X/Skip (`SetupWizard.tsx:87-130`). KennelConsent has no `role`/Esc/focus trap (closes on backdrop only) (`KennelConsent.tsx:48-50`). The `/run` leash overlay is an absolute backdrop with no role/Esc (`run.tsx:567-590`). Only the in-conversation `LeashPrompt` correctly uses `role="dialog"` (`frontend/src/components/brand/LeashPrompt.tsx:62`) — the rest are inconsistent.
- **Evidence:** grep: `role="dialog"`/`aria-modal` appears **only** in `LeashPrompt.tsx:62` across the whole frontend. Plus the line refs above.
- **Business impact:** WCAG 2.4.3 / 4.1.2 failures on primary interaction surfaces incl. onboarding and a permission-grant dialog; procurement/legal a11y risk.
- **User impact:** Screen-reader users aren't told a dialog opened and can wander behind it; keyboard users can't dismiss with Esc; inconsistent backdrop behavior is confusing.

### BB-008 — Clickable `<div>`/`<g>` rows are not keyboard-focusable and have no role (Work, OG graph & tree, Integrations, Authoring, Home)
- **Severity:** High · **Area:** Web (accessibility)
- **Reproduction:**
  1. On the Work screen, Tab through the run list and try to open a run with Enter.
  2. Repeat on the OG explorer (graph nodes + tree), Integrations ("Add MCP server"), Authoring (target rows), Home (run cards).
- **Expected:** Each interactive row is a `<button>` or `role="button"` + `tabIndex={0}` + Enter/Space handler.
- **Actual:** Bare `<div onClick>` / `<g onClick>` with `cursor:pointer`, no `role`, no `tabIndex`, no `onKeyDown`. Keyboard/AT users cannot open runs, navigate the OG graph, add an MCP server, select authoring targets, or open Home run cards.
- **Evidence:** `routes/work.tsx:1030-1044`; `routes/og.tsx:192-198` (graph `<g>`), `og.tsx:330-345` (tree rows); `routes/integrations.tsx:481-498`; `routes/authoring.tsx:201-205`; `routes/index.tsx:204-207`.
- **Business impact:** WCAG 2.1.1 keyboard-operability failure across the app's core navigation rows.
- **User impact:** Keyboard-only and screen-reader users are locked out of opening runs and exploring skills/the OG graph.

### BB-009 — Light-mode secondary text and status pills fail WCAG AA contrast
- **Severity:** High · **Area:** Web / Cross-cutting (accessibility)
- **Reproduction:**
  1. In light mode, read any secondary/muted text (sub-labels, descriptions, timestamps — pervasive at 12–13px) and any amber status pill label.
- **Expected:** ≥ 4.5:1 for normal text (these are < 18px), ≥ 3:1 for large.
- **Actual (measured):** `--hachi-muted #8A8580` on canvas `#FBF7EF` = **3.42:1**; on surface = 3.59:1; on cream `#F3E9D2` = **3.03:1** — all below AA for the small sizes used. Amber pill text `#A8651A` on working-tint `#FBEBD6` = **3.95:1** (fails). (Dark mode is fine: muted = 5.6–6.2:1.)
- **Evidence:** tokens `frontend/src/styles/tokens/colors.css:12-13,27`; muted text used everywhere (e.g. `routes/conversation.tsx:719`, sub-labels throughout); amber hex `#A8651A` hardcoded in pill maps (`routes/index.tsx:31-52`, `work.tsx:31-46`, `conversation.tsx:30-34`). Contrast ratios computed via WCAG relative-luminance.
- **Business impact:** WCAG 1.4.3 failure on the most common text style; affects readability for low-vision users and in bright environments.
- **User impact:** Secondary text and "working/needs-care" pills are hard to read in light mode (the default).

### BB-010 — Authoring "Generate" button is inert; patch summary is hardcoded, yet Apply works
- **Severity:** High · **Area:** Web
- **Reproduction:**
  1. On Authoring, edit the prompt and click **Generate**.
  2. Observe the "+3 −2 lines" summary, then click **Apply patch**.
- **Expected:** Generate produces a patch (or shows progress/error); line counts reflect the real diff.
- **Actual:** The Generate button has only hover handlers — **no `onClick`** — so it does nothing. The summary is the literal string `"+3 −2 lines"` regardless of input. Apply/Reject/Revert DO call real handlers, so a user can "Apply patch" against a diff they never generated.
- **Evidence:** `routes/authoring.tsx:455-478` (Generate, no onClick), `authoring.tsx:528` (literal "+3 −2 lines").
- **Business impact:** A headline feature ("edit Hachi's own brain") is non-functional in the UI and can apply phantom changes.
- **User impact:** The primary action appears broken; the diff count never changes; Apply may mutate skills unexpectedly.

### BB-011 — Web icon-only buttons lack accessible names (close, fact edit/delete)
- **Severity:** High · **Area:** Web (accessibility)
- **Reproduction:**
  1. With a screen reader, focus the Work-detail close button and a Memory fact's edit/delete icons.
- **Expected:** Each announces a name ("Close", "Edit fact", "Delete fact").
- **Actual:** Several icon-only `<button>`s render only an `<Icon>` with no `aria-label` and no text child. The `Icon` component takes `size`, not `title`/`aria-label`, so it contributes no accessible text. The destructive **delete-fact** control is among the unlabeled ones.
- **Evidence:** Work close `routes/work.tsx:253-268`; Memory `GhostIconBtn` `routes/memory.tsx:31-47` used at `memory.tsx:120-125`. Inconsistent with labeled examples (`run.tsx:276` `aria-label="Back"`, `skills.tsx:53`) — grep shows only 4 `aria-label` uses across all routes.
- **Business impact:** WCAG 4.1.2 failure on a destructive action (delete a memory fact) — high-stakes to mislabel.
- **User impact:** AT users can't tell what close/edit/delete do; risk of deleting a fact unknowingly.

### BB-012 — Two parallel, near-identically-named skill-pack systems (`hachi pack` vs `hachi skill pack`)
- **Severity:** High · **Area:** CLI
- **Reproduction:**
  1. `uv run hachi pack --help` → install/list/remove/update/search/index for `.ogpack` packs.
  2. `uv run hachi skill pack --help` → list/install/remove for "community skill packs" (GitHub/URL).
- **Expected:** One coherent way to install skill packs.
- **Actual:** Two separate command trees with different formats, sources, and remove semantics. `hachi skill list`'s empty-state even points at `hachi skill pack install user/repo`, while `hachi pack` is a wholly separate system; a pack installed via one is invisible to the other's `list`.
- **Evidence:** `src/hachi/cli/commands/pack.py:14` (`ogpack_app`); `src/hachi/cli/commands/skill.py:18-19,58` (`skill pack` + empty-state hint). Both confirmed in live `--help`.
- **Business impact:** Discoverability/trust hit on the product's stated moat (skills); conflicting docs.
- **User impact:** "Which packs command is real?"; installs that appear to vanish.

### BB-013 — `hachi doctor` soft-pedals a broken bundled skill library and has no PASS/FAIL verdict
- **Severity:** Medium · **Area:** CLI
- **Reproduction:** `uv run hachi doctor`
- **Expected:** A first-run diagnostic clearly flags that the shipped skills don't pass their own linter, with a severity cue.
- **Actual (live output):** `"skills_loaded": 53`, `"skills_health": "30 file(s), 23 error(s), 41 warning(s)"`, but `"skills_parse_failures": []` — so 23 lint errors are buried in a JSON blob inside a uniformly **green** panel, and `parse_failures: []` makes it look like nothing failed. Unlike `config-validate`/`security-audit`, `doctor` emits no overall verdict.
- **Evidence:** Live `doctor` output (captured): `permissions_tier: "unrestricted"`, `skills_health: "30 file(s), 23 error(s), 41 warning(s)"`. Rendering at `src/hachi/cli/main.py:2378-2443` (single green Panel regardless of counts).
- **Business impact:** The product ships a skill library that fails its own linter while the health command implies "all good" — undermines the "skills are the moat" pitch at first inspection.
- **User impact:** Users can't tell from `doctor` that something is wrong.

### BB-014 — Web SPA shows no loading state anywhere; fixtures render then silently swap to real data
- **Severity:** Medium · **Area:** Web
- **Reproduction:**
  1. Open any data screen (Work / Memory / OG / Skills / Integrations / Settings) on first load.
  2. Watch content while the `loadX()` `useEffect` runs.
- **Expected:** A skeleton/spinner/"Loading…" while the first fetch is in flight.
- **Actual:** No screen has a loading branch. Each mounts, renders the current store value (the seed fixtures), then values silently flip when the fetch resolves.
- **Evidence:** `routes/memory.tsx:216`, `work.tsx:818-821`, `og.tsx:824`, `skills.tsx:166`, `integrations.tsx:516-518`, `settings.tsx:58`, `approvals.tsx:463` (all `useEffect(() => { void loadX() }, [])` with no loading flag).
- **Business impact:** Perceived instability; spend/token numbers visibly jump on load, undermining confidence.
- **User impact:** Content shifts under the user; briefly-shown numbers differ from the real ones a beat later.

### BB-015 — Memory screen shows fabricated "recall" results for a question the user never asked
- **Severity:** Medium · **Area:** Web
- **Reproduction:**
  1. Open Memory. The recall box is pre-filled with `"what do you know about my repo?"`.
  2. Read the "Closest matches" block — populated on load.
  3. Start typing a new query.
- **Expected:** No results until the user runs a recall.
- **Actual:** `recallDone` is initialized `true`, so the seeded recall fixture renders immediately as answers to a fabricated question; typing flips `recallDone=false`, hiding them until Recall is clicked.
- **Evidence:** `routes/memory.tsx:221` (`recallDone` initial `true`); recall fixture rendered in the matches block.
- **Business impact:** Reinforces BB-004 — users see invented "recall hits" as real memory.
- **User impact:** Confusing: answers appear for a question they didn't ask, then vanish on typing.

### BB-016 — Several destructive/bulk actions fire with no confirmation and no undo (Web)
- **Severity:** Medium · **Area:** Web
- **Reproduction:**
  1. Memory → trash icon on a fact. 2. Approvals → "Decline all". 3. Settings → switch safety to "Open", or remove a folder/command. 4. Skills → "not used in 90 days — remove?".
- **Expected:** Confirmation for destructive/bulk/irreversible actions (esp. "Decline all" and deleting a fact), or a toast with Undo.
- **Actual:** All execute immediately. Notably "Decline all" has no confirm; the safety "Open" warning text appears only *after* you select it; fact delete and skill remove are single-click; no undo path exists.
- **Evidence:** `routes/memory.tsx:592` (delFact); `routes/approvals.tsx:514` (declineAll, no confirm); `routes/settings.tsx:67-71,239-256,362-378,429-445`; `routes/skills.tsx:81`.
- **Business impact:** Accidental bulk-decline or memory deletion is unrecoverable from the UI; loosening a security control (safety level) is a single misclick.
- **User impact:** One stray click can decline everything pending, delete remembered facts, or drop Hachi to "act without asking".

### BB-017 — Web inputs/textareas/selects have no associated label (core text-entry surfaces)
- **Severity:** Medium · **Area:** Web (accessibility)
- **Reproduction:**
  1. With a screen reader, focus the Home "Ask Hachi…" field, Memory "Search memory", Memory recall, Authoring prompt, or the conversation composer.
- **Expected:** Each input has `<label htmlFor>` or `aria-label`.
- **Actual:** All rely on placeholder text only (placeholders are not accessible names and vanish on input). The shared `Input` primitive supports a `label`, but these screens use raw `<input>`/`<textarea>`. Note the conversation composer `<textarea>` also has no `aria-label` (`routes/conversation.tsx:829-850`). Even the `Input` primitive's `label` is a sibling `<label>` with **no `htmlFor`/`id`** association (`frontend/src/components/ui/Input.tsx:56-57`), so it isn't programmatically tied to the field either.
- **Evidence:** `routes/index.tsx:460-463`, `routes/memory.tsx:318-321,373-379,131-135`, `routes/authoring.tsx:437-453`, `routes/conversation.tsx:829-850`, `components/ui/Input.tsx:56-57`.
- **Business impact:** WCAG 3.3.2 / 4.1.2 on the app's core inputs.
- **User impact:** Screen-reader users hear "edit text" with no context once typing begins.

### BB-018 — Onboarding provider choice is contradictory and under-sells real capability (CLI + Web)
- **Severity:** Medium · **Area:** Cross-cutting (CLI + Web)
- **Reproduction:**
  1. `uv run hachi quickstart --help` / `setup --help` → only `anthropic | openai-compatible | ollama`.
  2. Web SetupWizard Step 1 → hardcoded "Anthropic · Claude" only.
  3. Trigger a missing-key boot banner → it lists get-a-key links for groq/together/deepseek/openrouter/gemini.
- **Expected:** One consistent provider vocabulary across boot banner, CLI setup, and Web wizard.
- **Actual:** The onboarding layer actually supports nine provider kinds (`openrouter, groq, together, deepseek, gemini, chatgpt-web` beyond the three), and the boot banner advertises Groq/Gemini/etc. get-a-key links — but neither the CLI `setup`/`quickstart` nor the Web wizard lets the user pick them. Free Gemini/Groq tiers (a selling point for the "burned by a paid host" ICP) are unreachable via guided setup.
- **Evidence:** `src/hachi/cli/main.py:1403-1404,1427` (3 choices), `main.py:453` (validator), `main.py:1237-1245` (banner links); `src/hachi/core/onboarding.py:52-62,71-84` (9 kinds); `frontend/src/components/overlays/SetupWizard.tsx:206-212` (hardcoded Anthropic).
- **Business impact:** Under-sells real capability and ships contradictory onboarding docs.
- **User impact:** Users who want a free/alternative provider can't pick it through the guided flows.

### BB-019 — CommandPalette ships a dev-only "Preview the reconnecting state" action and fake "Recent" items, and has no keyboard list navigation
- **Severity:** Medium · **Area:** Web
- **Reproduction:**
  1. Press ⌘K/Ctrl+K. Read the "Do" and "Recent" groups.
  2. Type a query; try to move the selection with arrow keys and press Enter.
- **Expected:** Production-only actions; real recents; arrow-key navigation + Enter to run the highlighted item.
- **Actual:** The "Do" group includes "**Preview the reconnecting state**" (calls `simulateReconnect`) — a debug affordance in the shipping palette. "Recent" is three hardcoded fake entries ("Reply to Priya about Q3 deck", …). There is **no arrow-key navigation or Enter-to-activate** — the palette only filters; you must Tab through results and click/Space. (It does correctly do Esc-to-close and focus-on-open.)
- **Evidence:** `frontend/src/components/overlays/CommandPalette.tsx:102` (Preview reconnecting), `:105-109` (fake recents), `:54-70` (only ⌘K + Esc handled; no Arrow/Enter on the list).
- **Business impact:** A dev toggle in the production command surface looks unfinished; the palette (the app's primary keyboard accelerator) isn't actually keyboard-drivable end-to-end.
- **User impact:** Confusing fake "recents"; power users can't run a palette command without reaching for the mouse.

### BB-020 — Settings spend caps look editable but are read-only static text
- **Severity:** Medium · **Area:** Web
- **Reproduction:** On Settings → Spend & limits, click the "$1.00" Per-task cap and the daily-cap field.
- **Expected:** Editable inputs, or a clearly non-interactive display.
- **Actual:** Both are `<div>`s styled exactly like input boxes (bordered, mono, 42px tall) but contain static text — Per-task is the literal `$1.00`, Daily shows `{budgetStr}` — with no input and no handler. They look like form fields but can't be changed; the per-task cap ignores actual config.
- **Evidence:** `routes/settings.tsx:527-541` (Per-task `$1.00`), `settings.tsx:547-561` (Daily).
- **Business impact:** Users believe they can set spend limits (a core "cost is a feature" promise) but can't.
- **User impact:** Clicking the cap fields does nothing.

### BB-021 — Skills "Browse the kennel" CTA and operator "edit" are inert no-ops; KennelConsent permissions are hardcoded fiction for most skills
- **Severity:** Medium · **Area:** Web
- **Reproduction:**
  1. On Skills (The library), click **Browse the kennel** (header). 2. As operator, click a skill's pencil/edit icon. 3. Add a skill not named GitHub/Notion/Slack and read its consent permission list.
- **Expected:** Browse opens discovery; edit opens an editor; the consent dialog reflects the skill's real scopes.
- **Actual:** Browse has an empty `onClick` (`/* noop */`); operator edit has an empty `onClick` (`/* noop per prototype */`). The KennelConsent "What it will be allowed to do" list is a hardcoded map with only GitHub/Notion/Slack; any other skill falls back to the single generic line "Use this capability within the leash", under a "Add and allow" button that grants real access.
- **Evidence:** `routes/skills.tsx:202` (Browse noop), `skills.tsx:40` (edit noop); `frontend/src/components/overlays/KennelConsent.tsx:13-17,26` (hardcoded `KENNEL_PERMS` + generic fallback).
- **Business impact:** The primary "add capabilities" CTA is dead; a permission-consent dialog that doesn't reflect actual scopes undercuts the "you see exactly what it can do" promise.
- **User impact:** The most obvious button does nothing; users approve a skill without an accurate scope list.

### BB-022 — Home "What needs you" hides approvals beyond the first 3 with no overflow link
- **Severity:** Medium · **Area:** Web
- **Reproduction:**
  1. Accumulate 4+ pending approvals. 2. Open Home (Today) → "What needs you".
- **Expected:** A count plus a way to see the rest ("+N more — view all" / link to The leash).
- **Actual:** Home renders `approvals.slice(0, 3)` with no "see more" and no link to `/approvals`, while the header says e.g. "5 things waiting on the leash". The other 2 are invisible from here.
- **Evidence:** `routes/index.tsx:576-578` (count), `index.tsx:582` (`slice(0,3)`).
- **Business impact:** Time-sensitive approvals auto-decline on a countdown; silently hiding them risks missed actions.
- **User impact:** "5 waiting" but only 3 shown and no path to the rest, which can expire unseen.

### BB-023 — No in-transcript "thinking" feedback between submit and first token (Web + TUI)
- **Severity:** Medium · **Area:** Cross-cutting (Web + TUI)
- **Reproduction:**
  1. Send a prompt that takes a while to first token (slow model or long first tool call).
  2. Watch the transcript area (not the header).
- **Expected:** An inline pending bubble/spinner confirming work is underway.
- **Actual (Web):** After send, the only transcript change is the user bubble; nothing appears until the first stream delta or card. The header text flips to "Hachi is working" but there's no in-thread cue. **(TUI, in flight):** same — the user bubble mounts, then nothing until the first `text.stream.delta` bubble or `tool.call.started` card; the only "working" cue is the small animated header glyph.
- **Evidence:** Web: `routes/conversation.tsx:609-610,746-754` (no pending bubble). TUI: `src/hachi/tui/app.py:2605-2639` (submit mounts only the user bubble), `app.py:1552-1577`.
- **Business impact:** Perceived unresponsiveness/hangs on slow first-token latency; users may re-submit or quit.
- **User impact:** Uncertainty about whether the prompt was accepted, especially before the first character.

### BB-024 — Web SPA has no visible focus indicator on most interactive elements (zero-specificity focus ring is overridden by inline `outline:'none'`)
- **Severity:** Medium · **Area:** Web (accessibility)
- **Reproduction:**
  1. Tab through the conversation composer, the Memory/Home search inputs, and route-level buttons.
- **Expected:** A clearly visible focus outline on every focusable control.
- **Actual:** The base focus ring is defined with a zero-specificity `:where(...)` selector (`base.css:38`), so any element with an inline `outline:'none'` overrides it — which the route-level bare `<input>`/`<textarea>` do (e.g. composer `outline:'none'`, `conversation.tsx:845`). Clickable `<div>` rows (BB-008) aren't focusable at all, so they never show focus. The shared `Button` sets no outline and does inherit the ring (good) — but most controls in the routes are inline-styled, not the primitive.
- **Evidence:** `frontend/src/styles/tokens/base.css:38-42` (`:where(...):focus-visible`); `routes/conversation.tsx:845` (`outline:'none'`); raw inputs across `memory.tsx`/`index.tsx`.
- **Business impact:** WCAG 2.4.7 (focus visible) failure on core inputs; keyboard users lose track of focus.
- **User impact:** Keyboard navigators can't see where they are.

### BB-025 — TUI: cancelled turn leaves a partial reply with no "cancelled" marker  [TUI in flight]
- **Severity:** Medium · **Area:** TUI
- **Reproduction:**
  1. Send a prompt that streams a reply. 2. Mid-stream, press Esc (or Ctrl+C). 3. Read the transcript.
- **Expected:** A clear inline "cancelled" indication so the partial reply isn't mistaken for complete.
- **Actual:** The loop returns `"[CANCELLED] {partial}"`, but `_on_chat_result` skips rendering the return value whenever any text already streamed (`_turn_streamed_any`), and the `session.cancelled` event is emitted but not handled by the session screen. The only feedback is a transient toast. The partial bubble is left looking like a finished answer. (Note: the underlying Esc→`loop.cancel()` mid-stream wiring is correctly fixed — see "Re-verified fixed" below.)
- **Evidence:** `src/hachi/tui/app.py:2671-2683`, `app.py:1552-1632`; `src/hachi/core/loop.py:624-631,724-735`; `src/hachi/core/runtime_events.py:123-134`.
- **Business impact:** Users may act on a truncated/abandoned answer believing it complete; erodes the "you hold the leash" control story.
- **User impact:** Confusion over whether the agent stopped and whether the visible text is the full response.

### BB-026 — TUI: `?` help shortcut is inoperative while the composer has focus (contradicts its own placeholder)  [TUI in flight]
- **Severity:** Medium · **Area:** TUI
- **Reproduction:**
  1. Launch the TUI (composer gets focus by default; placeholder reads "… ? for help").
  2. Type `?`.
- **Expected:** `?` opens the help overlay.
- **Actual:** The App-level `question_mark` binding is not `priority=True`, so a focused Textual `Input` consumes `?` as literal text instead of opening the help modal. Help is only reachable via `/help` or by moving focus off the composer — yet the placeholder tells users `?` works.
- **Evidence:** `src/hachi/tui/app.py:1360` (placeholder), `app.py:1988` (non-priority binding), `app.py:1298-1333` (composer key routing).
- **Business impact:** The main keyboard-help discoverability affordance is broken at initial focus.
- **User impact:** New users press `?`, get a stray character, conclude help is broken.

### BB-027 — TUI: leash/approval modal advertises rationale as "required for deny" but never enforces it  [TUI in flight]
- **Severity:** Medium · **Area:** TUI
- **Reproduction:**
  1. Trigger a gated operation so the approval modal appears. 2. Leave rationale blank. 3. Click deny (or press Esc).
- **Expected:** Either the field is genuinely required for deny, or the placeholder shouldn't claim it is.
- **Actual:** Placeholder reads "Rationale (required for deny, optional otherwise)", but both the deny button and Esc substitute `"Denied from modal"` when empty. The "required" claim is false, and the audit trail records boilerplate instead of a real reason.
- **Evidence:** `src/hachi/tui/widgets.py:547-550,563-576`.
- **Business impact:** Misleading microcopy in a security-critical dialog; weakened denial audit trail.
- **User impact:** Users who read "required" expect to be stopped; the deny goes through with a meaningless rationale.

### BB-028 — Web Settings shows a permanent, misleading "unsaved changes" with no Save button
- **Severity:** Low · **Area:** Web
- **Reproduction:** On Settings, toggle any switch or change safety, then look at the header and hunt for "save".
- **Expected:** Auto-save with a transient confirmation, or an explicit Save that clears the flag.
- **Actual:** Every handler sets `setUnsaved(true)` but nothing ever clears it, and there is no Save button — yet changes are persisted live. So the "unsaved changes" label is permanent and contradicts reality.
- **Evidence:** `routes/settings.tsx:67-95` (sets unsaved, never false), `settings.tsx:153-157` (the label).
- **Business impact:** Mixed signal about whether security settings are saved.
- **User impact:** A perpetual "unsaved changes" warning; users hunt for a non-existent Save button.

### BB-029 — Web status/risk meaning relies on color alone (no non-color cue) and re-implements pills with hardcoded hex instead of the design-system primitive
- **Severity:** Low · **Area:** Web (accessibility + consistency)
- **Reproduction:** View status/risk pills in grayscale or as a color-blind user (Home approval risk, Work status, Integrations chips, approvals risk).
- **Expected:** Status distinguishable without hue; consistent token usage.
- **Actual:** Pills pair a label with a small colored dot; the dot is the only differentiator between "needs care" (amber) and "high risk" (red). Label colors are hardcoded hex (`#A8651A`, `#2C7A50`, `#9E2D21`) repeated inline across screens, instead of the existing `StatusPill` primitive with tokenized colors — so the design system isn't actually used.
- **Evidence:** inline pill maps at `routes/index.tsx:31-52`, `work.tsx:31-46`, `approvals.tsx:29-47`, `integrations.tsx:27-66`; unused primitive `frontend/src/components/ui/StatusPill.tsx`.
- **Business impact:** WCAG 1.4.1 (use of color) concern on safety-critical risk signaling; hardcoded hex defeats theming correctness.
- **User impact:** Color-blind/low-vision users can't reliably tell "needs care" from "high risk".

### BB-030 — Web toasts are `pointer-events:none`, auto-dismiss only, and not pinnable; live-region announcement is fragile
- **Severity:** Low · **Area:** Web (accessibility)
- **Reproduction:** Trigger a toast (e.g. resume a session in Memory).
- **Expected:** Important messages are dismissible/re-readable, and reliably announced.
- **Actual:** `Toast` is `pointer-events:none` with no close affordance, so it can't be clicked/pinned and vanishes on its own. It does set `role="status" aria-live="polite"` (correct), but because the node is mounted only when shown rather than living in a persistent region, some screen readers miss the late-inserted live region. (The `ReconnectingBanner` correctly uses a persistent `role="status"` pill — `ReconnectingBanner.tsx:40`.)
- **Evidence:** `frontend/src/components/ui/Toast.tsx:43` (`pointer-events:none`), `:55` (`role="status"`).
- **Business impact:** Transient confirmations/errors can be missed by AT users.
- **User impact:** Screen-reader users may not hear confirmations; sighted users can't re-read a toast.

### BB-031 — Web Switch toggles have no accessible name in context (security-relevant permission toggles)
- **Severity:** Low · **Area:** Web (accessibility)
- **Reproduction:** With a screen reader, focus a Settings "Ask before…" toggle or an operator tool toggle.
- **Expected:** The control announces the rule it governs ("Sending email — switch, off").
- **Actual:** `Switch` renders `role="switch"`/`aria-checked` correctly, but Settings passes no `label` and no `aria-label`/`aria-labelledby` linking the adjacent rule text; the visible label is an unassociated sibling `<div>`. The switch announces only "switch, on/off".
- **Evidence:** `frontend/src/components/ui/Switch.tsx:60-73` (correct roles), used unlabeled at `routes/settings.tsx:292-295,496-499`.
- **Business impact:** WCAG 4.1.2 — security toggles ("ask before deleting", tool scopes) have no accessible name.
- **User impact:** AT users can't tell which permission a toggle controls.

### BB-032 — Long lists have no virtualization/pagination; OG graph overlaps into an unreadable cluster at scale
- **Severity:** Low · **Area:** Web
- **Reproduction:** With many facts/skills/sessions/runs or a large `.og` graph, open Memory, OG explorer, Work history, or the audit log.
- **Expected:** Virtualized/paginated lists; an OG graph that handles many nodes.
- **Actual:** Every list maps the full array with no windowing or "load more". The OG graph lays nodes out by even vertical division within a fixed ~380px column and scales node radius with content size, so beyond a handful of nodes the circles overlap into an unreadable cluster.
- **Evidence:** `routes/memory.tsx:564-600,636,685,763`; `work.tsx:745,771,1027`; `approvals.tsx:672,720`; `integrations.tsx:723`; OG layout `routes/og.tsx:25-37` (+ `GRAPH_H`).
- **Business impact:** Performance/legibility degrade with real data volumes; the OG visualization (a positioned moat feature) breaks at scale.
- **User impact:** Janky scrolling on large stores; an overlapping, illegible OG graph for any non-trivial skill library.

### BB-033 — CLI onboarding guidance is Windows-hostile (`export VAR=…`) and the suggested fix command refuses in the context it's shown
- **Severity:** Low · **Area:** CLI
- **Reproduction:**
  1. Trigger a missing-key state for a remote provider (or read the TTY boot banner).
  2. In a non-TTY context (CI/Docker/piped), note the banner's "Fix (one command): `hachi secrets --set <ENV>=<your-key>`".
  3. Run `uv run hachi secrets --set ANTHROPIC_API_KEY` with stdin not a TTY.
- **Expected:** Cross-platform guidance (the code explicitly claims to avoid Unix-only `export`), and a "one command" that works where it's shown.
- **Actual:** `OnboardingService.validate_provider` returns `export {env}=sk-...` guidance and the TTY banner adds `{env}=<your-key>` — but on the documented Windows/PowerShell platform `export` doesn't exist. Separately, `secrets --set` hard-exits in non-TTY ("requires an interactive terminal"), which is exactly the context the non-TTY banner suggests it; and `--set` takes only the KEY (then prompts for the value), so the banner's `KEY=<value>` form would store the secret under a literal `KEY=value` name.
- **Evidence:** `src/hachi/core/onboarding.py:308-312` (`export`); `src/hachi/cli/main.py:1255-1262` (banner), `main.py:2323-2335` (`--set` TTY guard + value prompt). (Partially re-confirms prior **GLOBAL-1**, which is otherwise resolved — `secrets --set` now exists and `vault.json` references are gone.)
- **Business impact:** CI/Docker onboarding dead-ends; inconsistent with the stated cross-platform goal on the project's own primary platform.
- **User impact:** Windows users copy `export …` and it fails; CI users get a refusal from the suggested command.

### BB-034 — CLI: redundant REPL entry points and two unrelated credential systems
- **Severity:** Low · **Area:** CLI
- **Reproduction:**
  1. `uv run hachi --help` shows global `--repl`; `run` also has `--repl`; there's also `run-repl`.
  2. Compare `hachi reauth` with `hachi secrets`.
- **Expected:** One obvious REPL entry; a coherent "where are my credentials" story.
- **Actual:** Three overlapping REPL affordances (`hachi --repl`, `hachi run --repl`, `hachi run-repl`) all funnel to `_run_repl`. Separately, `reauth` clears a `CredentialVault` while `secrets`/`google-auth`/provider keys use a different `secret_store` backend; clearing via one won't affect tokens in the other, and neither command references the other ("I cleared my creds but it still uses the old token").
- **Evidence:** `src/hachi/cli/main.py:162-166,1775-1807` (REPL trio); `main.py:1621-1634` (`reauth`→`CredentialVault`) vs `main.py:2176,2310,2357` (`secret_store`).
- **Business impact:** CLI surface clutter + two credential subsystems are a support/clarity burden.
- **User impact:** Confusion about the canonical REPL entry and about which command actually clears a given token.

### BB-035 — `hachi channel whatsapp` binds `0.0.0.0` by default with an empty verify token
- **Severity:** Low · **Area:** CLI
- **Reproduction:** Read `hachi channel whatsapp` defaults.
- **Expected:** Conservative, clearly-flagged network-exposing defaults (as `gateway` does with `127.0.0.1`).
- **Actual:** `channel whatsapp` defaults `--host 0.0.0.0` (with an explicit `# noqa: S104`), exposing a webhook receiver on all interfaces, and the startup banner prints `Verify token: (none)` without warning that an empty verify token is insecure.
- **Evidence:** `src/hachi/cli/commands/channel.py:143` (`host="0.0.0.0"`), `:179,183-188` (banner `(none)`).
- **Business impact:** Default-open bind is a soft security smell for a privacy-positioned product.
- **User impact:** Users may unknowingly expose a webhook receiver on their LAN.

### BB-036 — TUI: Esc-cancel is invisible in the Footer; thematic Footer labels don't match destination titles  [TUI in flight]
- **Severity:** Low · **Area:** TUI
- **Reproduction:**
  1. During a turn, look at the Footer for a way to stop. 2. Note the labels for Ctrl+2 / Ctrl+9.
- **Expected:** The cancel affordance is surfaced during a turn; mode labels match the screens.
- **Actual:** Esc-to-cancel lives only in `SessionScreen.on_key`, not in `BINDINGS`, so it never appears in the Footer (it's documented only inside the hard-to-reach `?` help modal — see BB-026). Footer mode labels are thematic ("the pack" for Ops, "the kennel" for OG Explorer) while the screens' own titles say "Ops"/"OG Explorer".
- **Evidence:** `src/hachi/tui/app.py:1954-1989` (BINDINGS, "the pack"/"the kennel"; Esc absent), `app.py:1634-1645` (Esc outside BINDINGS), `widgets.py:579-592`.
- **Business impact:** Reduced discoverability of the headline interrupt capability; thematic labels add a learn-the-jargon tax.
- **User impact:** Users in a long turn don't know Esc stops it; "the pack"/"the kennel" don't obviously map to Ops/OG Explorer.

---

## Re-verified PRIOR findings (not re-filed)

- **UC-CORE-005 (Esc stops in-flight LLM call) — CONFIRMED FIXED.** `SessionScreen.on_key` → `action_interrupt` → `agent.loop.cancel()` sets a thread-safe `_cancel_event`; the streaming generator checks it after every chunk and raises mid-stream (`src/hachi/tui/app.py:1634-1645,3152-3163`; `src/hachi/core/loop.py:412-420,453-456,624`). (But the *surfacing* of the cancel is weak — see BB-025.)
- **MR-3 / ADV-009 (TUI renders unredacted tool I/O) — CONFIRMED FIXED.** Tool inputs/results are redacted via `redact_structure`/`redact_secrets` before truncation, unconditionally, in `runtime_events.adapt_event()` (`src/hachi/core/runtime_events.py:166,183-192`); the in-process agent routes all events through `adapt_event` before the TUI sink.
- **GLOBAL-1 (secrets `set` / `vault.json` dead-end) — MOSTLY RESOLVED.** `secrets --set` now exists and the missing-key banner references it (no more `vault.json`). Residual gaps captured in **BB-033**.
- **tokoboros leftovers — NONE.** Zero matches in `src/` or live CLI/TUI output.

---

## Methodology — what I ran vs. what I read

**Ran (live, captured output):**
- `uv run hachi --help` — full 28-command surface.
- `uv run hachi doctor` — captured JSON; confirms `permissions_tier: "unrestricted"`, `skills_health: 30 file(s), 23 error(s), 41 warning(s)`, `provider_api_key: "ok"` (basis for BB-013).
- `uv run hachi config-get` — **reproduced the Critical crash first-hand** (`AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'`, basis for BB-001/BB-002).
- (Via CLI sub-agent, additionally observed) `secrets --help`, `config`, `security-audit`, `config-validate`, `models`, and various `--help` screens.
- WCAG contrast ratios computed numerically for BB-009 from the palette hex values.
- grep sweeps to establish absence: `@media` (3 total, none width-based → BB-003), `role="dialog"`/`aria-modal` (1 total → BB-007), `aria-label` in routes (4 total → BB-011), `focus-visible`/`:focus` (only the zero-specificity base rule → BB-024), `tokoboros` (0).

**Read (code, as the rendered experience):**
- Web SPA: `__root.tsx`, `Sidebar.tsx`, all route files (`index/work/approvals/conversation/memory/og/skills/settings/integrations/run/authoring`), overlays (`SetupWizard`, `CommandPalette`, `KennelConsent`, `ReconnectingBanner`, `ToastHost`), UI primitives (`Button`, `Input`, `Switch`, `Toast`), `lib/data/bootstrap.ts` + domain loaders, and the `styles/` tokens.
- CLI: `cli/main.py` and all `cli/commands/*.py`, plus `core/onboarding.py`.
- TUI: `tui/app.py`, `widgets.py`, `theme.py`, `art.py`, `formatters.py` (marked **[TUI in flight]** — another session is actively editing).

The CLI's console-script and direct `python -c` import path fail in this environment (`ModuleNotFoundError: No module named 'keyring'`), so live CLI runs were done via `uv run hachi …` (which resolves deps). TUI was not executed (needs a real terminal) — assessed from code.

---

## Severity tally

| Severity | Count | IDs |
|---|---|---|
| **Critical** | 2 | BB-001, BB-002 |
| **High** | 10 | BB-003, BB-004, BB-005, BB-006, BB-007, BB-008, BB-009, BB-010, BB-011, BB-012 |
| **Medium** | 15 | BB-013, BB-014, BB-015, BB-016, BB-017, BB-018, BB-019, BB-020, BB-021, BB-022, BB-023, BB-024, BB-025, BB-026, BB-027 |
| **Low** | 9 | BB-028, BB-029, BB-030, BB-031, BB-032, BB-033, BB-034, BB-035, BB-036 |

> **Total: 36 findings — 2 Critical, 10 High, 15 Medium, 9 Low.**

### Cross-cutting themes
1. **The Web SPA is a high-fidelity prototype, not a finished product:** simulated `/run`, inert Generate/Browse/edit controls, hardcoded consent permissions, fake "Recent" palette items, a dev-only palette action, read-only "spend cap" fields. (BB-005, BB-010, BB-019, BB-020, BB-021)
2. **Silent fixture fallback erodes trust:** failed loads show fabricated data with no error/loading/stale marker, and pre-run "recall" results appear unasked — dangerous for a product whose pitch is trusting the agent. (BB-004, BB-014, BB-015)
3. **Accessibility is systemically unaddressed on Web:** no dialog semantics/focus traps, clickable non-focusable `<div>`s, unlabeled icon buttons/inputs/switches, color-only status, sub-AA contrast, no reliable focus ring. (BB-007, BB-008, BB-009, BB-011, BB-017, BB-024, BB-029, BB-031)
4. **Desktop-only:** zero width-based responsiveness anywhere; the app is unusable on phones/tablets/narrow windows. (BB-003)
5. **CLI config commands are broken and onboarding is inconsistent:** `config-get`/`config-set` crash on the key safety/budget controls; provider choices contradict across boot banner / CLI setup / Web wizard; Windows-hostile `export` guidance. (BB-001, BB-002, BB-013, BB-018, BB-033)
