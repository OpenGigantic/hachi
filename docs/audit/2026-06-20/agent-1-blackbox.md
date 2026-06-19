# Agent 1 — Black Box Testing Report (2026-06-20)

End-user black-box audit of the Hachi web frontend and CLI, traced statically in
**LIVE mode** (`VITE_HACHI_USE_MOCKS=false`). Verifies whether the remediation PRs
(#116–129) actually wired the previously-dead flows end-to-end
(frontend handler → domain adapter → gateway endpoint → real effect), or whether
they are cosmetic. BE-1 (#128) is treated as NOT on `main`.

Severity tally: **Critical 2 · High 5 · Medium 4 · Low 3** (14 findings).

Overall: The "make the claims true" remediation is largely **honest but incomplete**.
Several flows were wired on the frontend against gateway endpoints **that do not exist
on `main`**, so they 404 silently or surface a generic error. The UI degrades
gracefully (no crashes, no fabricated data presented as real) — a genuine improvement
over the 2026-06-19 state — but multiple headline actions (Authoring Generate, MCP add,
Settings Save, in-context Always-allow, skill add/remove persistence) are non-functional
against a real gateway. Live Run remains a static card (now honestly labelled).

---

## BB-01 — Authoring "Generate patch" has no backend endpoint (404 in live mode)
- **Severity:** Critical
- **Verdict:** STILL-BROKEN (was CRIT-002; PR #118 wired FE + apply/revert only)
- **Repro:** Authoring screen → select a target → type a prompt → click **Generate**.
- **Expected:** Gateway generates a real diff for the `.og`/config target.
- **Actual:** `generateAuthoring` POSTs `/authoring/targets/{name}/generate`
  (`frontend/src/lib/data/domains/authoring.ts:151`). The gateway only registers
  `/targets`, `/targets/{name}`, `/targets/{name}/apply`, `/targets/{name}/revert`
  — there is **no `/generate` route** (`src/hachi/gateway/fastapi_app.py:894-912`).
  In live mode every Generate click 404s and renders the inline error
  ("API POST … → 404"). The diff/apply pipeline is a documented stub
  (`control_plane.authoring_apply`: "stub — no patch pipeline yet",
  `fastapi_app.py:906`).
- **Business impact:** The flagship "edit Hachi's own brain" differentiator is
  non-functional against a real server. Apply/Revert operate on a no-op stub.
- **User impact:** The primary CTA on the Authoring screen always errors.
- **Evidence:** `authoring.ts:134-165`; `fastapi_app.py:894-912`;
  `control_plane.py:500` (apply/revert stubs).

## BB-02 — Skill add/remove and kennel "Add" never persist (store-only)
- **Severity:** Critical
- **Verdict:** PARTIAL (was CRIT-003; PR #123/#124 wired the browse overlay only)
- **Repro:** Skills → **Browse the kennel** → pick a skill → **Add and allow**; or
  click **Add** on a kennel card; or click "not used in 90 days — remove?".
- **Expected:** The `.og` skill is installed/removed on disk and the gateway
  re-indexes; the change survives reload.
- **Actual:** `confirmKennel` (`store.ts:871`), `addSkill` (`store.ts:866`) and
  `removeSkill` (`store.ts:893`) are **pure local store mutations** with no gateway
  call. There is no `POST /skills` / `DELETE /skills/{id}` route — only
  `GET /skills` (`fastapi_app.py:854`). Worse, in live mode `kennel` is seeded
  empty (`store.ts:352`: `kennel: USE_MOCKS ? kennel : []`), so **"Browse the kennel"
  opens a dialog that always reads "No skills in the kennel yet."**
  (`KennelConsent.tsx:125-133`), and "From the kennel" never renders
  (`skills.tsx:361`). Any skill "added" vanishes on next `loadSkills()` refresh.
- **Business impact:** Skill management — the .og moat surface — is a mock-only demo.
- **User impact:** "Add"/"Remove" toast success but nothing is actually installed;
  the kennel browser is empty in production.
- **Evidence:** `store.ts:352,866-896`; `skills.tsx:175-231,361`;
  `KennelConsent.tsx:125-133`; `fastapi_app.py:854`.

## BB-03 — Settings "Save" calls a nonexistent endpoint → always "Save failed"
- **Severity:** High
- **Verdict:** REGRESSED / STILL-BROKEN (MED-017 cosmetic)
- **Repro:** Settings → change a budget cap → **Save changes**.
- **Expected:** Caps persist to config.
- **Actual:** `handleSave` PUTs `/config/limits`
  (`frontend/src/routes/settings.tsx:296`). No `/config/limits` route exists —
  the config router only exposes `GET ""`, `PUT /provider`, `POST /provider/validate`,
  `PUT /permissions`, `PUT /memory` (`fastapi_app.py:620-637`). In live mode Save
  404s, the catch fires, and the user always sees **"Save failed — please try again"**
  with the unsaved indicator stuck on.
- **Business impact:** No budget/cap setting can be saved from the web UI.
- **User impact:** Settings appear permanently broken.
- **Evidence:** `settings.tsx:292-308`; `fastapi_app.py:620-637`.

## BB-04 — Allowed folders / commands never persist to the gateway
- **Severity:** High
- **Verdict:** STILL-BROKEN (NEW relative to the dead-flow list)
- **Repro:** Settings → Permissions → **Add folder** / **Add command** (or remove one).
- **Expected:** The permission roots persist via `PUT /config/permissions`
  (which DOES exist, `fastapi_app.py:632`) and survive reload.
- **Actual:** `addFolderWithPath` / `addCommandWithText` / `removeFolder` /
  `removeCommand` only mutate local store slices (`store.ts:988-1009`); none call
  `apiSend` against `/config/permissions`. The endpoint exists but is never used by
  the UI. Changes vanish on reload and never reach `device-permissions.og`.
- **Business impact:** The core security-gating surface (the "leash") can be edited
  in the UI but the edits are illusory.
- **User impact:** Adding a folder/command appears to work (success toast) but the
  agent's actual permissions are unchanged.
- **Evidence:** `store.ts:988-1009`; `settings.tsx:319-334`; `fastapi_app.py:632`
  (unused endpoint).

## BB-05 — In-context "Always allow" hits a nonexistent endpoint
- **Severity:** High
- **Verdict:** PARTIAL (was HIGH-004; PR #124 wired the Leash *tab* only)
- **Repro:** Mid-conversation or mid-run, a leash prompt appears → click **Always allow**.
- **Expected:** A standing always-allow rule is saved server-side.
- **Actual:** `icAlwaysAllow` (`store.ts:1212`) and `runAlwaysAllow`
  (`store.ts:1421`) POST `/sessions/current/permissions/always-allow`. **No such
  route exists** (grep over `src/hachi/gateway/*.py` returns nothing). Both call
  sites comment "ignore 404", so the 404 is swallowed and only the local UI updates;
  the always-allow is never persisted, so Hachi will ask again next time.
  NOTE: the **Leash-tab** Always-allow (`approvals.ts:238` → `POST /approvals/{id}`
  with `action:"approve"`) DOES reach the gateway — the distinction is correctly
  built there. The gap is the in-context (chat/run) leash path.
- **Business impact:** "Always allow" silently fails to reduce future prompts when
  triggered from chat/run.
- **User impact:** User believes they've granted a standing permission; they haven't.
- **Evidence:** `store.ts:1212-1230,1421-1441`; no `always-allow` route in gateway;
  contrast `approvals.ts:229-241`.

## BB-06 — Home approval Allow/Decline never reaches the gateway
- **Severity:** High
- **Verdict:** PARTIAL (Leash tab fixed; Home card not wired)
- **Repro:** Home ("Today") → "What needs you" → **Allow once** / **Decline** on a card.
- **Expected:** Same gateway POST as the Leash tab (`POST /approvals/{request_id}`).
- **Actual:** Home's `ApprovalCard` calls the store-only `allowApproval` /
  `declineApproval` (`index.tsx:608-609`, `store.ts:690-701`), not the wired
  `approve` / `decline` domain mutations from `approvals.ts`. In live mode the
  decision updates the local UI but the gateway approval stays pending (the agent
  keeps waiting on its 180 s `threading.Event`). Home also has **no "Always allow"**
  button, so the three-way distinction the audit asked about is absent on this surface.
- **Business impact:** Approving from the home screen does not unblock the agent.
- **User impact:** User clicks Allow, the card disappears, but the task stays blocked.
- **Evidence:** `index.tsx:299-301,604-611`; `store.ts:690-701`; cf. wired path
  `approvals.ts:229-249`.

## BB-07 — MCP "Add server" hits a nonexistent endpoint
- **Severity:** High
- **Verdict:** PARTIAL (was HIGH-006; PR #121 wired FE form + validation only)
- **Repro:** Integrations → Add MCP server → fill name/command/args → submit.
- **Expected:** Server registered and appears in the inventory.
- **Actual:** `addMcpServer` POSTs `/runtime/mcp/register`
  (`frontend/src/lib/data/domains/integrations.ts:457`), explicitly documented as
  "does not yet exist in the Hachi gateway" (`integrations.ts:7-8,442-445`). Gateway
  only exposes `GET /runtime/mcp/inventory` (`fastapi_app.py:526`). In live mode the
  submit 404s; the dialog surfaces the ApiError. The client-side validation
  (`validateMcpServerInput`, `integrations.ts:386`) is solid and mirrors the Python
  shell-metacharacter guards, but there's nothing to submit to.
- **Business impact:** Cannot add MCP servers from the web UI.
- **User impact:** The add dialog always errors on submit against a real gateway.
- **Evidence:** `integrations.ts:386-470`; `fastapi_app.py:526-528`.

## BB-08 — Session "Resume" does not restore context
- **Severity:** Medium
- **Verdict:** PARTIAL (was HIGH-011; PR #122 made it navigate, not resume)
- **Repro:** Memory → Sessions → **Resume** on a past session.
- **Expected:** The chosen session's transcript/context is reloaded into a continuing
  conversation.
- **Actual:** `resume()` (`memory.tsx:326-333`) only `navigate({to:'/conversation'})`
  and shows an info toast ("context … is shown above for reference"). The code comment
  is honest: "The gateway has no dedicated resume endpoint — session history is
  display-only; … ensureSession() creates a fresh session." So clicking Resume on
  session A then sending a message starts a brand-new session B with no context from A.
  The button is labelled "Resume", implying continuation it cannot deliver.
- **Business impact:** No true session continuity in the web UI.
- **User impact:** "Resume" is misleading — prior context is silently dropped.
- **Evidence:** `memory.tsx:316-334`; `chat.ts` `ensureSession` (POST /sessions).
  NOTE: the CLI `hachi run --resume/--continue` (`cli/main.py:1903-1929`) does claim
  "full context reload" and is a separate, more complete path.

## BB-09 — Live Run is a static placeholder card (no streaming)
- **Severity:** Medium
- **Verdict:** STILL-BROKEN-BY-DESIGN (was HIGH-007 / F-015)
- **Repro:** Navigate to `/run` in live mode.
- **Expected:** Live sub-agent output / pack progress / step plan.
- **Actual:** `LiveRun` renders `LiveRunHonest` — a static card that says streaming
  "is only available when a backend run-streaming endpoint is connected" and redirects
  to `/conversation` (`run.tsx:82-219,748-754`). The full simulation only runs in mock
  mode. This is the deliberate "Option B (honest, no fabricated simulation)" decision
  (`run.tsx:7-14`). Honest, but the Run screen delivers nothing live; the entire
  plan/pack/terminal UI is mock-only.
- **Business impact:** The "watch Hachi work live" feature does not exist on this route.
- **User impact:** Clicking into a run from Home (`index.tsx:659-663`) lands on a
  dead-end card.
- **Evidence:** `run.tsx:7-14,82-219,748-758`.

## BB-10 — Stale / placeholder model list surfaced to users
- **Severity:** Medium
- **Verdict:** STILL-BROKEN (BE-1 model-list update unmerged)
- **Repro:** Setup wizard / model picker → choose a model; `hachi doctor`/setup model list.
- **Expected:** Current Anthropic model IDs.
- **Actual:** `list_models()` returns only
  `{"anthropic": ["claude-haiku-4-5", "claude-sonnet-4-5"]}`
  (`cli/main.py:501-503`), wired through to `list_models=support.list_models`
  (`cli/main.py:1164`) and consumed by `client.list_models()` at `cli/main.py:2426`.
  No Opus tier and no 4.x refresh — the BE-1 model-list change is unmerged on `main`.
- **Business impact:** Users cannot select the flagship/most-capable model from the UI.
- **User impact:** Limited, possibly outdated model choices.
- **Evidence:** `cli/main.py:501-503,1164,2426`.

## BB-11 — No single-process `serve` command (split gateway + TUI/UI)
- **Severity:** Medium
- **Verdict:** STILL-BROKEN (BE-1 `serve` unmerged)
- **Repro:** `hachi serve`.
- **Expected (per BE-1):** One command boots gateway + UI together.
- **Actual:** No `serve` command exists. The user must run `hachi gateway …` and then
  point `hachi cockpit`/the web frontend at it separately
  (`cli/main.py:1968` gateway; `cli/main.py:1932` cockpit). The cockpit/web UI both
  require a separately-started gateway and a hand-managed `HACHI_GATEWAY_TOKEN`.
- **Business impact:** High onboarding friction for the local/self-host ICP.
- **User impact:** Two-terminal, token-juggling setup to see a working product.
- **Evidence:** absence of `serve` in `cli/main.py` command list (lines 1476-2810);
  `cli/main.py:1932,1968`.

## BB-12 — Empty-state and division guards are present and correct
- **Severity:** Low (positive verification)
- **Verdict:** FIXED (PR #116/#117/#122)
- **Detail:** Verified guards across surfaces: budget div-by-zero guarded
  (`index.tsx:988-991` — `unlimited = budget <= 0`); work spend empty-state
  (`work.tsx:572-587`) and `workEmpty` (`work.tsx:976`); memory recall empty-state
  (`memory.tsx:313`); skills installed/kennel empty-states (`skills.tsx:330-344,361`);
  authoring no-targets empty-state (`authoring.tsx:312-336`); routines/finished
  empty rows (`index.tsx:713-786`). First-load failure clears seeded fixtures so
  fabricated data is never shown as real (`skills.ts:83-93`, `authoring.ts:76-87`,
  `operator.ts:175-185`, `approvals.ts:182-190`). No crashes or NaN found.

## BB-13 — Home first-run guidance + integration-filtered starters work
- **Severity:** Low (positive verification)
- **Verdict:** FIXED (was HIGH-019; PR #116)
- **Detail:** `isFirstRun = sessions.length === 0` renders `FirstRunPanel`
  with "Start your first task" + "Connect integrations" CTAs
  (`index.tsx:317-318,676-682,897-980`). Starters are filtered by connected
  integration id (`index.tsx:320-378`): Gmail/Calendar starters hidden until `i1`
  is connected; the integration-agnostic "Check this week's PRs" always shows.
  Behaves correctly.

## BB-14 — Keyboard nav, focus traps, and input dialogs are wired
- **Severity:** Low (positive verification, minor a11y gaps noted)
- **Verdict:** FIXED / PARTIAL
- **Detail:** Slash-menu keyboard nav (ArrowDown/ArrowUp/Enter, index reset on close)
  is implemented (`conversation.tsx:637-714`). Kennel browse and consent overlays use
  `useFocusTrap` + Escape-to-close and proper `role="dialog"`/`"alertdialog"` +
  `aria-modal` (`KennelConsent.tsx:35-43,71-76,216-242`). Add-folder/add-command use
  inline input dialogs with empty-input guards (`store.ts:988-1009`,
  `settings.tsx:323-334`). Minor a11y gaps: pervasive hover-only styling driven by JS
  `onMouseEnter/Leave` (no `:focus-visible` parity, e.g. every button in
  `index.tsx`/`skills.tsx`), and the auto-decline countdown
  ("auto-declines in {countdown}") is not announced via `aria-live`
  (`index.tsx:139-141`). No mobile/responsive breakpoints — layouts use fixed pixel
  grids (e.g. `gridTemplateColumns:'1fr 1fr 1fr'`, fixed 312/520px panels), so the UI
  is desktop-only.

---

## Verdict summary vs prior audit

| Prior finding | This audit | Status |
|---|---|---|
| CRIT-002 Authoring Generate/Apply/Revert | BB-01 | STILL-BROKEN (Generate 404; apply/revert are stubs) |
| CRIT-003 Skills browse + add | BB-02 | PARTIAL (overlay wired; add/remove store-only; kennel empty in live) |
| HIGH-011 Sessions Resume | BB-08 | PARTIAL (navigates, no context restore) |
| HIGH-006 MCP add dialog | BB-07 | PARTIAL (form/validation wired; endpoint missing) |
| HIGH-004 Always allow vs once | BB-05 / BB-06 | PARTIAL (Leash tab fixed; in-context + Home not) |
| HIGH-019 Home first-run + starters | BB-13 | FIXED |
| HIGH-007 / F-015 Live Run streaming | BB-09 | STILL-BROKEN (honest static card) |
| Empty-state / division guards | BB-12 | FIXED |
| addFolder/addCommand + settings unsaved + slash nav | BB-14 / BB-03 / BB-04 | Mixed (dialogs+nav FIXED; persistence broken) |

Root cause pattern: PR #118/#121/#124-era frontend work wired handlers against gateway
routes that were never added on `main` (`/authoring/.../generate`,
`/runtime/mcp/register`, `/sessions/current/permissions/always-allow`,
`/config/limits`), and several "mutations" (skill add/remove, folder/command edits,
Home approvals) remain local-store-only. The UI is honest about gaps and never crashes,
but the live product cannot complete these journeys.
