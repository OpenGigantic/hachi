# Hachi — UX & Product Experience Audit (QA Agent 3)

**Reviewer lens:** Senior Product Manager · **Date:** 2026-06-18 · **Mode:** Read-only
**Surfaces reviewed:** Web SPA (`frontend/src/routes/*`, components, data layer), CLI
(`src/hachi/cli/main.py`, `core/onboarding.py`), README + COOKBOOK, product-intent docs
(`docs/strategy/03,06,07`, `docs/design/web-ui-design-brief.md`,
`docs/design/hachi-web-ui-requirements.md`).
**ICP anchor:** disillusioned self-host devs — "OpenClaw for people who got burned by OpenClaw."
Trust through control is the whole promise.

**What is genuinely strong (so findings are read in context):** the web SPA is well-built and
on-brand. Streaming, tool-call cards, and the in-context leash prompt are wired to live SSE
(`frontend/src/lib/data/domains/chat.ts:172` handles `text.stream.delta`,
`tool.call.started/completed`, `permission.requested`). The Approvals screen
(`frontend/src/routes/approvals.tsx`) is excellent: plain-language requests, a "why" clause, what-it-touches
file chips, a live countdown bar with "requests expire — Hachi stays put," and an honest "Runs without a
leash check" disclosure block. The leash/kennel/pack vocabulary is consistent across routes. Onboarding
(`SetupWizard.tsx`) is a calm 5-step stepper. The OG Explorer (`frontend/src/routes/og.tsx`) renders the
three-pass moat as a real data-viz with per-node savings. DATA_GAPS is empty — every domain is wired
(`frontend/src/lib/data/client.ts:43`).

---

## Summary

- **Total findings:** 26
- **Severity:** Critical 3 · High 9 · Medium 10 · Low 4
- Findings are grouped: Onboarding, Trust, Navigation/IA, Discoverability, Friction/Feedback,
  Cognitive-Load/Consistency, Accessibility, Delight, then a dedicated **Missing Product Capabilities**
  section and a **Trust-signals-for-the-ICP** subsection.

---

## Onboarding

### UX-001 — Web setup wizard "validates" the key by length only, then claims it is validated
- **Severity:** Critical · **Category:** Trust / Onboarding
- **Description:** `frontend/src/components/overlays/SetupWizard.tsx:51` sets
  `keyValid = setup.key.trim().length >= 12`, and Step 1 then renders a green check reading
  *"Key looks good — validated."* (`SetupWizard.tsx:236-240`). No network validation happens at that
  step; the real `applySetup()` only fires on Finish (`SetupWizard.tsx:73-85`). The design brief and CLI
  both promise live validation ("validated on the spot, with a clear ✓/⚠", requirements §5.0; the CLI
  *does* call `service.validate_provider()` live). The web UI shows a validated checkmark for any 12-char
  string.
- **User impact:** For the exact ICP that distrusts hype, a fake "validated" badge is a credibility
  landmine — a user pastes a wrong/expired key, sees a green check, finishes setup, then the first task
  fails. This is the precise "fails mid-conversation" pain the product claims to have solved.
- **Recommendation:** Wire Step 1 (or a "Validate" button) to the same gateway provider-validation the CLI
  uses; show ✓ only on a real 200, ⚠ with the actual error otherwise. Never label format-checking as
  "validated."
- **Effort:** M

### UX-002 — Web setup never lets the user choose a provider (Anthropic hard-coded) and never sets a spend cap
- **Severity:** High · **Category:** Onboarding / Missing-Capability
- **Description:** Step 1 shows Provider as a static, non-interactive field reading "Anthropic · Claude"
  (`SetupWizard.tsx:209-212`) — no Ollama/OpenAI-compatible/OpenRouter choice, despite provider-freedom
  being a core switch argument (strategy 03 §5 "→ From Claude Code: provider-free… incl. fully local").
  The wizard also never asks for a spend cap, even though "spend governed in the loop" is the #1 anti-bill-
  shock pitch. Settings later shows a hard-coded `$1.00` per-task cap as a read-only field
  (`frontend/src/routes/settings.tsx:540`).
- **User impact:** A local-first / cost-sensitive self-hoster (the literal target segment) cannot pick
  Ollama or set a budget during onboarding — the two things that would earn their trust on day one are
  absent from the make-or-break flow.
- **Recommendation:** Add a provider picker (mirror CLI presets incl. local Ollama) to Step 1, and a
  "Spend limit" step or field ("ask before spending over $X / day") to the wizard.
- **Effort:** M

### UX-003 — Onboarding's "first magic moment" is a toggle, not a confirmed artifact
- **Severity:** Medium · **Category:** Onboarding / Delight
- **Description:** Step 4 offers a "Morning briefing" routine as a single Switch
  (`SetupWizard.tsx:417-458`); on Finish the wizard just closes. The strategy explicitly defines success as
  *"a scheduled artifact the user can see in Ops"* (UX redesign §7.3) and the make-or-break flow #1 ends
  with *"see it confirmed and 'on watch' on Home"* (requirements §6.1). There's no hand-off that lands the
  user on Home with the routine visibly "on watch."
- **User impact:** The user finishes setup with no evidence anything happened; time-to-first-magic is not
  reached — they get a closed modal, not a win.
- **Recommendation:** On Finish, route to Home, scroll to "On watch," and briefly highlight the newly
  created routine row. Confirm with a calm toast in the leash voice.
- **Effort:** S

### UX-004 — Two install paths in the README with conflicting first-run behavior; "5-minute" promise unquantified
- **Severity:** Medium · **Category:** Onboarding / Adoption-Blocker
- **Description:** README shows a curl/iex one-liner that "launches guided setup," plus a `pipx install
  hachi-og` → `hachi quickstart` path, while COOKBOOK leads with `uv tool install` → `hachi setup --tier
  lightning` and an env-var "skip the wizard" path. Three install verbs (`curl`, `pipx`, `uv tool`) and two
  different first commands (`quickstart` vs `setup`) with no "fastest route in 30s vs full setup in ~3 min"
  guidance. (Per README/COOKBOOK sub-audit.)
- **User impact:** A new user must choose between three onramps without knowing which is canonical;
  decision friction at the highest-drop-off moment.
- **Recommendation:** Pick one canonical path, label it, and offer exactly one "advanced/source" fallback.
  State the realistic time-to-first-message.
- **Effort:** S

### UX-005 — Bare `hachi` launches the app instead of showing help; `quickstart` is never suggested
- **Severity:** Medium · **Category:** Discoverability / Onboarding
- **Description:** `src/hachi/cli/main.py` configures the Typer app with `no_args_is_help=False,
  invoke_without_command=True`, so bare `hachi` boots the assistant. A first-timer who runs `hachi` to
  "see what it does" gets dropped into a session (or an auto-applied silent setup via
  `_apply_quick_setup_if_needed`) rather than a command list or a nudge toward `hachi quickstart`. (Per CLI
  sub-audit.)
- **User impact:** New users can't discover commands the obvious way; the silent auto-setup may pick a
  provider they didn't intend (e.g. Ollama from a stray env var) with no explanation.
- **Recommendation:** On first run with no prior config, print a one-screen welcome that points to
  `hachi quickstart` and lists the top 5 commands; keep direct-launch for configured users.
- **Effort:** S

---

## Trust (critical for the burned-self-hoster ICP)

### UX-006 — "Always allow" carries no confirmation and Decline never captures a reason
- **Severity:** High · **Category:** Trust
- **Description:** On both the Approvals screen and the LeashPrompt, "Always allow {scope}" fires
  immediately with no confirm step (`frontend/src/routes/approvals.tsx:322-339`,
  `LeashPrompt.tsx:245-271`). The requirements spec says declining "can take an optional reason"
  (requirements §5.4) — but `onDecline` is a bare call with no reason capture anywhere. "Always allow"
  is the single most consequential trust action (it permanently widens the leash) and it's the easiest
  button to hit by accident.
- **User impact:** One stray click permanently grants a standing permission with no friction and no audit
  rationale — the opposite of "you hold the leash," and exactly the kind of silent scope-creep that burned
  OpenClaw users.
- **Recommendation:** Add a lightweight confirm/undo for "Always allow" (toast with Undo, or a one-line
  "always allow X for {scope}?" confirm). Add an optional reason field to Decline that lands in the audit
  log.
- **Effort:** M

### UX-007 — In-context leash prompt has no countdown; auto-decline can surprise the user mid-conversation
- **Severity:** High · **Category:** Trust / Feedback
- **Description:** `LeashPrompt` supports a `countdown` prop (`LeashPrompt.tsx:174-186`), but the
  Conversation screen renders it **without** one (`frontend/src/routes/conversation.tsx:761-768` passes no
  `countdown`), and the run screen likewise omits it (`frontend/src/routes/run.tsx:580-588`). The product
  rule is that every approval shows "a visible countdown… never let that be a surprise" (requirements §4.4).
  The standalone Approvals queue *does* show the countdown; the in-flow prompt does not.
- **User impact:** A user watching a task can have an inline request silently auto-decline (or expire)
  without warning — undermining the central promise that approvals are never a surprise.
- **Recommendation:** Pass a live `useCountdown` value into the in-context and run-screen LeashPrompts, same
  as the Approvals cards.
- **Effort:** S

### UX-008 — Settings provider has no Validate action and no key field; caps are read-only display
- **Severity:** High · **Category:** Trust / Missing-Capability
- **Description:** The Settings "AI provider & model" card has only a "Change provider" button that re-opens
  the wizard (`frontend/src/routes/settings.tsx:594-616`); there's no inline Validate (the design brief §9
  requires a Validate action that tests connectivity). "Spend & limits" renders per-task `$1.00` and the
  daily cap as static, non-editable boxes (`settings.tsx:521-563`) — there is no way to *set* a cap in the
  UI, and the README/COOKBOOK never document how either (per docs sub-audit). The "unsaved changes"
  indicator (`settings.tsx:153-157`) appears but there is **no Save button** anywhere on the page.
- **User impact:** The headline trust feature (enforceable budgets) is unconfigurable in the product, and a
  persistent "unsaved changes" badge with no Save control reads as broken — corrosive for a trust-first
  audience.
- **Recommendation:** Make caps editable with a Save/validate flow; add an inline provider Validate;
  resolve the "unsaved changes" badge with an explicit Save (or switch to autosave and drop the badge).
- **Effort:** M

### UX-009 — No global governance/status bar; spend, model, session id, and pending approvals live only in the left rail
- **Severity:** High · **Category:** Trust / IA
- **Description:** Both design specs make a persistent top governance bar non-negotiable — eye/state +
  model + live token+cost + session id + stream-status pill + counters (web-ui-design-brief §7.0;
  requirements §4.1 "always visible: state, spend vs budget, pending approvals"). The shipped shell
  (`frontend/src/routes/__root.tsx`, `frontend/src/components/shell/Sidebar.tsx`) puts governance only in
  the left sidebar (mascot + budget widget + approvals badge). There is **no model selector, no session id,
  no stream-status/connection pill** anywhere in the chrome; per-task spend appears only in the
  Conversation header (`conversation.tsx:668-676`).
- **User impact:** "Make the invisible visible" is the brand thesis; the single most important glance —
  what model, what session, am I connected, what's it costing right now — is not ambient. Users can't tell
  at a glance whether the local gateway is connected (only a transient reconnect pill appears on drop).
- **Recommendation:** Add the specified top status strip (model selector, live cost/tokens, session id,
  stream-status pill, counters) on every screen; keep the sidebar budget widget as secondary.
- **Effort:** M

### UX-010 — Memory deletion and fact edits are immediate with no confirmation or undo
- **Severity:** Medium · **Category:** Trust
- **Description:** On the Memory screen, the trash icon calls `delFact(g.id, fact.id)` directly with no
  confirm (`frontend/src/routes/memory.tsx:123-125`), and edits/saves persist immediately. For a screen
  whose entire job is "what does Hachi know about me — never a black box, inspect and correct"
  (requirements §5.5), destructive one-click deletion of a remembered fact has no safety net.
- **User impact:** Accidental permanent loss of memory the user may have wanted; erodes confidence in the
  memory surface as a safe place to inspect.
- **Recommendation:** Add an undo toast on delete (and ideally a soft-delete/restore for a session).
- **Effort:** S

---

## Navigation & Information Architecture

### UX-011 — "Spend" is a hidden tab inside /work, not a first-class destination
- **Severity:** High · **Category:** IA / Discoverability
- **Description:** Both design specs treat Spend as a top-level area (UX redesign §4 five-area IA;
  web-ui-design-brief screen map #3 "Spend" with its own nav slot). In the build it's a tab inside Work
  (`frontend/src/routes/work.tsx:829-835`, the `spend` filter) with no nav entry
  (`Sidebar.tsx:18-29` has no spend item). The sidebar budget widget links to `/work`
  (`Sidebar.tsx:60-63`), landing on the Active tab — the user must then notice and click the Spend tab.
- **User impact:** The screen that "proves the token ouroboros" — the core differentiator visualization —
  is undiscoverable. Cost transparency, the headline trust feature, is buried two clicks deep with no
  signpost.
- **Recommendation:** Promote Spend to a left-nav item (and/or make the budget widget deep-link straight
  to the Spend tab). Reconcile the IA with the documented five areas.
- **Effort:** S

### UX-012 — Command palette is mostly static stubs: "Recent" is hard-coded, actions don't reflect real state
- **Severity:** Medium · **Category:** Discoverability / Consistency
- **Description:** `frontend/src/components/overlays/CommandPalette.tsx` lists a fixed "Recent" group
  ("Reply to Priya about Q3 deck", "Morning briefing routine", "Memory: about you" — lines 105-109) that
  never changes, and a "Preview the reconnecting state" dev action shipped to users (line 102). It searches
  only nav + a static action list; it does not surface real sessions, real skills, or the slash commands
  the brief requires ("the web equivalent of… every slash command", brief §7.0; requirements §5.9 "open a
  recent conversation/skill/memory").
- **User impact:** The keyboard-first promise rings hollow — the palette can't actually jump to a real
  recent conversation or run a `/command`; a debug action is exposed to end users.
- **Recommendation:** Drive "Recent" from real session/recall data; add slash-command entries; remove the
  reconnect-preview dev action from production.
- **Effort:** M

### UX-013 — The TUI "operator-view" / two-audience adaptivity is web-only; CLI/TUI lacks the companion framing
- **Severity:** Low · **Category:** Consistency
- **Description:** The product principle is "one adaptive product — calm by default, operator depth on
  demand" with an operator-view toggle (requirements §2). The web SPA implements this cleanly
  (`Sidebar.tsx:149-175` operator switch gating folders/commands/audit/activity). The CLI/TUI (in flight,
  separate session) has no equivalent companion-vs-operator framing surfaced in onboarding. **TUI in
  flight** — flag for the cockpit rebuild.
- **User impact:** A companion-grade user dropped into the TUI sees operator density immediately.
- **Recommendation:** Carry the operator-view concept into the TUI cockpit; default to plain.
- **Effort:** M

---

## Discoverability

### UX-014 — "Browse the kennel" and operator skill-edit are dead buttons
- **Severity:** High · **Category:** Discoverability / Missing-Capability
- **Description:** On the Skills ("library") screen, the prominent "Browse the kennel" header button is a
  no-op (`frontend/src/routes/skills.tsx:202` — comment: "opens KennelConsent globally — noop") and the
  operator pencil/edit button is also a no-op (`skills.tsx:40` "operator edit — noop per prototype"). The
  only working add path is the per-kennel-card "Add" button. Skill *authoring* — the hero feature in the
  strategy ("make `save_skill` the hero feature", strategy 03 §6 C2) — has no entry point from this screen
  (it lives only on the separate /authoring route).
- **User impact:** The two most inviting buttons on the differentiator screen do nothing; users conclude
  the kennel/skill-authoring story is vaporware.
- **Recommendation:** Wire "Browse the kennel" to a kennel browse view/modal; wire operator-edit to deep-
  link into /authoring for that skill; or hide non-functional buttons until wired.
- **Effort:** M

### UX-015 — "Add MCP server" and "Add folder/command" are affordances with no input flow
- **Severity:** Medium · **Category:** Discoverability / Friction
- **Description:** Integrations shows an "Add MCP server" row that is a styled `div` with hover but no
  onClick (`frontend/src/routes/integrations.tsx:479-503`). Settings "+ folder" / "+ command" call
  `addFolder()` / `addCommand()` (`settings.tsx:381-398`, `448-465`) which — per the store — add fixed
  placeholder entries rather than prompting for a path/command. Re-authorise/Connect on integration cards
  call `connectIntegration(id)` with no OAuth flow surfaced.
- **User impact:** Operators can't actually add an MCP server or grant a specific folder/command through the
  UI; the controls imply capability that isn't there.
- **Recommendation:** Provide an input dialog for folder/command/MCP-server entry; surface a real connect
  flow (or device-code/QR per the brief) for integrations.
- **Effort:** M

### UX-016 — Slash-command menu offers 4 commands; the documented set is ~20 and none execute
- **Severity:** Medium · **Category:** Discoverability / Consistency
- **Description:** The Conversation composer slash menu lists only `/routine`, `/dry-run`, `/remember`,
  `/budget` (`frontend/src/routes/conversation.tsx:66-71`), and picking one just prefills the composer text
  (`handleSlashPick` → `setComposer(cmd + ' ')`, line 627-629) — it doesn't run anything. The design brief
  enumerates ~20 slash commands each needing a UI affordance (brief §8.1: `/help /clear /new /compact
  /sessions /model /search /memory /plan /deep-research /speak …`).
- **User impact:** Power users expecting parity with the CLI/TUI slash vocabulary find a tiny, non-executing
  subset; inconsistent across surfaces.
- **Recommendation:** Expand the menu to the documented command set, wire them to real handlers, and surface
  them in the command palette too.
- **Effort:** M

---

## Friction & Feedback

### UX-017 — /run live view is fully simulated; "Watch live" doesn't show the real run
- **Severity:** High · **Category:** Feedback / Adoption-Blocker
- **Description:** `frontend/src/routes/run.tsx` carries an explicit `TODO(live-run)` (lines 7-12): the run
  output, pack, steps, and spend are driven by a local `openRun` queue + interval timer, *not* the SSE
  stream used in chat. "Watch live" from Work (`work.tsx:930-934`) and "Watch a live run" from the palette
  open this simulation. So the one screen dedicated to "watch a worker you're responsible for"
  (requirements §5.3 "watch live") shows fabricated activity.
- **User impact:** For an audience whose mindset is "I'm supervising a worker I'm responsible for," a fake
  live view is a serious trust break the moment they realize the terminal output isn't their actual run.
- **Recommendation:** Wire /run to the same `GET /sessions/{id}/stream` SSE used in chat; map `openRun` to a
  real session id. Until then, don't present it as "live."
- **Effort:** L

### UX-018 — No empty/error/loading states on most data screens; loaders silently fall back to seeded fixtures
- **Severity:** High · **Category:** Feedback / Trust
- **Description:** Every domain loader degrades to seeded fixtures on failure (`__root.tsx:18-20`
  "degrades to seeded fixtures on failure, so this never blanks the UI"; per-domain `loadX()` swallow
  errors). Combined with no skeleton/error UI on Memory, OG, Skills, Integrations, Work, the screens show
  plausible *fake* data when the gateway is down or a call fails, with no indication. The design brief makes
  loading/empty/error/reconnecting states mandatory for every data screen (brief §8 "states every data
  screen must design"; requirements §4.6). Only Approvals, Skills-installed, and Integrations have a true
  empty state; none have a visible error state.
- **User impact:** A self-hoster debugging a broken gateway sees a populated, healthy-looking UI — the most
  dangerous failure mode for a transparency-first product. They can't distinguish real data from fallback.
- **Recommendation:** Add explicit loading skeletons and error cards (real message + retry) per screen; when
  a loader hits the fixture fallback, surface a non-blocking "showing sample data — couldn't reach Hachi"
  banner rather than silently substituting.
- **Effort:** M

### UX-019 — Composer footer hint never mentions keyboard shortcuts; ⌘↵ / ? / history not discoverable
- **Severity:** Low · **Category:** Discoverability / Feedback
- **Description:** The Conversation composer placeholder is "Message Hachi… / for commands" and the footer
  hint is a reassurance line, not a shortcut hint (`conversation.tsx:835`, `912-921`). The design brief
  specifies the placeholder "Send a prompt · `/` for commands · `⌘↵` to send · `?` for help" and an input-
  history affordance (brief §8.1). Enter-to-send is implemented but `↑/↓` history and `?` help are not.
- **User impact:** Keyboard-first users don't learn the shortcuts; minor but compounds the "keyboard-first"
  gap with the palette.
- **Recommendation:** Use the specified placeholder; implement `↑/↓` composer history and a `?` shortcut
  overlay.
- **Effort:** S

### UX-020 — `doctor` output is JSON-heavy for human eyes; spend caps absent from CLI setup confirmation
- **Severity:** Low · **Category:** Feedback / Friction
- **Description:** Per the CLI sub-audit, `hachi doctor` renders a JSON-dense panel that humans must scan,
  and the `quickstart` confirmation panel explains the permission tier but never surfaces the active spend
  cap or a "here's what Hachi can/can't do" summary at the end. Permission tiers *are* explained in plain
  language at tier-choice time (`main.py` quickstart) — that part is good.
- **User impact:** The diagnostics meant to build confidence read as machine output; the post-setup moment
  doesn't reinforce the governance story.
- **Recommendation:** Give `doctor` a human-readable default (keep `--json`); add a "what Hachi can do now +
  your spend cap" summary to the quickstart finish panel.
- **Effort:** S

---

## Cognitive Load & Consistency

### UX-021 — Brand identity is split across two living specs (retired teal/ouroboros vs. live hound/Torii-red/leash)
- **Severity:** Medium · **Category:** Consistency
- **Description:** `docs/design/web-ui-design-brief.md` describes a teal-on-navy "ouroboros eye" brand and
  defines Hachi as "token + ouroboros," while `docs/design/hachi-web-ui-requirements.md` (which supersedes
  it for web) defines the live hound / Torii-red / "leash" identity and names Hachi after the loyal Akita.
  The shipped SPA correctly implements the hound/Torii-red system (`HachiMascot.tsx`, `--hachi-brand`
  `#D64933`). The older brief is still presented as the design+system prompt and its brand section is stale.
  Permission-tier names also diverge across docs (README "lightning/guided/strict" vs COOKBOOK
  "sandbox/standard/lightning" vs SPA "guarded/standard/open").
- **User impact:** Contributors/designers reading the canonical brief build to a retired identity; tier-name
  drift confuses users configuring safety.
- **Recommendation:** Add a prominent "superseded — see requirements doc" banner to the old brief (or retire
  it), and standardize one safety-tier vocabulary across CLI, web, and docs.
- **Effort:** S

### UX-022 — Inconsistent decline/secondary button color reads as navy-on-navy, breaking the calm hierarchy
- **Severity:** Low · **Category:** Consistency / Cognitive-Load
- **Description:** Across cards the "Decline" action is a navy (`--hachi-trust`) outline button
  (`approvals.tsx:284-301`, `LeashPrompt.tsx:190-216`), while "Always allow" is a muted ghost. Decline (a
  safe, common action) is visually heavier than Always-allow (the dangerous one). The brand rule is "calm by
  default, loud only when it matters" — destructive/standing-grant actions should not be the quietest.
- **User impact:** Visual weight doesn't match consequence; users may misread which action is safe.
- **Recommendation:** Rebalance: make Decline neutral-secondary, give "Always allow" a subtle but clearly-
  distinct treatment, and reserve the loud primary for the single expected action per view.
- **Effort:** S

---

## Accessibility

### UX-023 — No visible focus rings; hand-rolled inline-style buttons are hover-only — fails the WCAG-AA hard requirement
- **Severity:** Critical · **Category:** Accessibility / Adoption-Blocker
- **Description:** Across the SPA nearly every control is a hand-styled `<button>`/`<div>` with inline
  `onMouseEnter/onMouseLeave` hover states and **no `:focus-visible` styling** (e.g.
  `index.tsx` ApprovalCard buttons, `conversation.tsx` composer/send, `settings.tsx`, `work.tsx`,
  `og.tsx`). A repo-wide search finds only ~27 focus/aria occurrences across 18 files and just 2 focus rules
  in `frontend/src/styles/tokens/base.css`. The design brief makes this a hard requirement: "Full keyboard
  operability with visible `focus-visible` rings… WCAG 2.1 AA minimum" (brief §6; requirements §4.5
  keyboard-friendly throughout). Clickable `div`s (Work list rows `work.tsx:1030`, OG nodes, "Add MCP" row)
  are not keyboard-focusable at all.
- **User impact:** Keyboard and screen-reader users cannot see focus or operate large parts of the UI —
  directly contradicts the "keyboard-first, mouse optional" promise and is an accessibility/compliance
  blocker.
- **Recommendation:** Add a global `:focus-visible` ring token applied to all interactive elements; convert
  clickable `div`s to `<button>`/`role+tabindex+keydown`; audit tab order. Consider routing controls through
  the existing `components/ui/Button.tsx` instead of bespoke inline buttons.
- **Effort:** M

### UX-024 — Live regions / SR announcements largely absent for streaming text and approvals
- **Severity:** Medium · **Category:** Accessibility
- **Description:** The brief requires live regions to announce streaming agent text, toasts, and approval
  prompts (brief §6, §8). The reconnecting banner has `role="status" aria-live="polite"`
  (`ReconnectingBanner.tsx:40`) and LeashPrompt has `role="dialog"`, but the streaming Conversation message
  region (`conversation.tsx:522-543`) and the approval cards have no `aria-live`, so screen-reader users get
  no announcement as agent text streams in or when an approval appears.
- **User impact:** Blind/low-vision users can't follow the agent's streaming response or know an approval is
  waiting — the core interaction is inaccessible.
- **Recommendation:** Wrap the streaming agent text in an `aria-live="polite"` region; announce new
  approvals via an assertive live region; ensure toasts are announced.
- **Effort:** M

---

## Delight

### UX-025 — The signature mascot/eye state indicator isn't used as ambient agent-state feedback in the header
- **Severity:** Low · **Category:** Delight / Feedback
- **Description:** `HachiMascot.tsx` is a lovely 5-state pixel sprite (on-watch/working/done/blocked/resting
  with breathe + blink + loyal-loop). It appears once, large, in the sidebar (`Sidebar.tsx:87`). The brand
  thesis is that this mark is "the brand's heartbeat" reflecting live state in the top bar (brief §3, §7.0
  eye-state table). Because there's no top status bar (UX-009), the single most distinctive brand-as-feedback
  element isn't where the eye lands during work.
- **User impact:** Missed delight + missed ambient state signal; the agent's "working/blocked" state isn't
  glanceable while the user reads the transcript.
- **Recommendation:** Place a compact mascot/eye in the (to-be-added) top status strip, driven by live
  state; keep the large sidebar version for the home/empty moments.
- **Effort:** S

### UX-026 — Sessions "Resume" only fires a toast; resuming a past session isn't actually wired in the web UI
- **Severity:** Medium · **Category:** Feedback / Missing-Capability
- **Description:** On Memory, the "Resume" button on a past session just calls
  `showToast('Resuming "…" — picking up where you left off')` (`frontend/src/routes/memory.tsx:239-243`)
  with no navigation or session switch. Session resume is a named capability (brief screen #4 "sessions list
  (resume/export)"; OpenCode's "session list + resume as a primary surface" is a steal-pattern, UX redesign
  §2).
- **User impact:** Users believe they resumed a session but nothing happens — a broken core workflow that a
  long-running-agent user will hit early.
- **Recommendation:** Wire Resume to load the session transcript and navigate to Conversation; add Export.
- **Effort:** M

---

## Missing Product Capabilities

Features users will expect (or the strategy promises) that are absent or non-functional today:

- **Set/enforce a spend cap from the UI.** "Spend governed in the loop" is the #1 anti-OpenClaw pitch, yet
  there is no editable cap in Settings (read-only `$1.00`) and no cap step in onboarding (UX-002, UX-008);
  COOKBOOK never documents how to set one. The single most important trust feature is unconfigurable in
  product.
- **Provider freedom at setup.** No Ollama / OpenAI-compatible / OpenRouter choice in the web wizard despite
  "provider-free, incl. fully local" being the core Claude-Code switch argument (UX-002).
- **Real live-run / "watch live."** The /run screen is a simulation (UX-017); the supervisory promise needs
  the real SSE stream.
- **Working skill authoring + kennel browse from the library.** Hero feature `save_skill` has no entry from
  Skills; "Browse the kennel" is a no-op (UX-014). Authoring exists only as an isolated /authoring route.
- **Session resume + export.** Resume is a toast-only stub (UX-026); export is undocumented/absent in web.
- **A real command palette (recents, sessions, slash commands).** Currently static stubs (UX-012, UX-016) —
  undercuts the keyboard-first identity.
- **Gardener / "proposed memory change" review at scale.** A single hard-coded `proposed` card exists
  (`memory.tsx:485`); the Phase-4 gardener-diff review surface (multiple reviewable merge/supersede cards) is
  the governance answer to Hermes' "Curator overwrites edits" and isn't really built.
- **Plain-language connect flows (Google device-code / Telegram QR).** Integration connect buttons don't
  surface a real OAuth/device flow (UX-015); the brief's "no terminal mention" companion connect is missing.

## Trust signals for the disillusioned-self-hoster ICP

This is the make-or-break audience. What's present, what's missing:

**Present and good:**
- Honest "Runs without a leash check" disclosure on Approvals (`approvals.tsx:596-650`) — exactly the right
  candor.
- "What this touches" file chips + policy + "why" on each approval (`approvals.tsx:181-240`).
- Blocked/quarantined memory section ("Hachi refused to store", `memory.tsx:615-662`).
- Risky-integration warning ("Broad filesystem + shell access. Review what it can reach.",
  `integrations.tsx:196-213`).
- "Sub-agents share your leash" reassurance on /run (`run.tsx:507-512`).
- CLI: live provider validation, plain-language error panels, `security-audit` + `doctor`, permission-tier
  explanations at setup (per CLI sub-audit).

**Missing / undermining trust (ordered by damage):**
1. **Fake "validated" key badge** in web setup (UX-001) — a credibility landmine for a skeptical audience.
2. **No editable spend cap** anywhere in the UI (UX-002/008) — the headline governance feature is
   unconfigurable; COOKBOOK doesn't document it.
3. **Silent fixture fallback** when the gateway is down (UX-018) — a transparency product showing fake
   healthy data is the worst possible failure mode.
4. **Simulated "live" run** (UX-017) — fake supervisory feedback.
5. **No connection/stream-status indicator** in chrome (UX-009) — a self-hoster can't tell if the local
   gateway is actually connected.
6. **"Always allow" with no confirm, Decline with no reason** (UX-006) — frictionless standing-grant
   scope-creep is exactly what burned OpenClaw users.
7. **SECURITY.md exists but is unlinked** from README/COOKBOOK (per docs sub-audit) — a security-conscious
   user can't find the disclosure policy.
8. **No "what Hachi can/can't do" plain-language summary** post-setup (UX-020) — the governance story isn't
   reinforced at the moment trust is formed.

---

## Cross-cutting product themes

1. **"Make the invisible visible" is the brand thesis, but the most important glances aren't ambient.**
   No top governance/status bar, Spend buried in a tab, no connection indicator, no model/session id in
   chrome (UX-009, UX-011, UX-025). The transparency promise is realized inside individual screens but not
   as the always-on chrome both specs require.

2. **Trust controls are present as UI but hollow underneath.** Caps are read-only, "validated" is fake,
   resume/kennel/MCP-add/skill-edit are stubs, live-run is simulated (UX-001/002/008/014/015/017/026). For
   the *governance* positioning, controls that look real but do nothing are worse than honestly-absent ones.

3. **Failure honesty is excellent in copy but absent in mechanics.** The product *says* the right honest
   things (unchecked-actions list, blocked memory, real errors in spec) yet silently substitutes fixture
   data on gateway failure and has no error states (UX-018). The honesty needs to extend from microcopy to
   the data layer.

4. **Keyboard-first and accessible are claimed as hard requirements but not met.** Missing focus rings,
   click-only `div`s, a stub palette, missing slash commands and live regions (UX-012/016/019/023/024) — a
   developer audience will notice immediately, and it's a compliance blocker.

5. **Onboarding nearly nails time-to-first-magic but stops one step short.** The wizard is calm and well-
   structured, but it doesn't choose a provider, set a budget, validate for real, or land the user on a
   confirmed "on watch" artifact (UX-001/002/003) — the make-or-break flow ends in a closed modal instead of
   a visible win.

---

*Report path: `D:\OpenGigantic\hachi\docs\audit\2026-06-18\03-ux-product.md`*
