# Hachi — Black-Box (End-User) Audit — v2 (re-verification)

> **QA Agent 1 — Black-Box Testing (re-run).** Audited as an end user across the **Web SPA** and **CLI**. The **Cockpit TUI** (`src/hachi/tui/cockpit/**`) is owned by a concurrent in-flight session and is **EXCLUDED** from scope/scoring per the audit brief. The old Textual TUI (`src/hachi/tui/app.py`) was **deleted**, so all prior TUI findings (BB-025…BB-027, BB-036) are now **OBSOLETE** (the surface they describe no longer exists).
> **Date:** 2026-06-18 (v2) · **Branch:** `main` · **Stance:** READ-ONLY. No source/test/config files were modified.
> **Method:** Ran the CLI live via `uv run hachi …` (reproduced crashes first-hand); traced the Web SPA route-by-route via the code path each user action triggers; cross-checked the gateway endpoints the SPA depends on; ran the backend test suite.

## Headline

The two prior **Critical** CLI crashes (BB-001, BB-002) that the audit lead's preliminary signal marked "**looks FIXED**" are **NOT fixed.** I reproduced both first-hand with `uv run`. `config-get` still crashes with a raw traceback on **every** invocation; `config-set` still fails on all three safety/budget keys (now with a caught error message instead of a raw traceback, but still 100% non-functional). The command bodies were rewritten cosmetically but still read/write the **wrong** attribute paths (`cfg.safety_tier` / `cfg.max_tokens` / `cfg.session_cost_cap_usd`) that do not exist on `ResolvedConfig`. The new tests added alongside the rewrite test the **gateway runtime** mutators (which use the correct `cfg.permissions.tier`), not the CLI commands — so the suite is green while the commands are broken. **`config-validate` works and reads `permissions.tier`/`agent.max_tokens` correctly, proving the data is reachable and BB-001/002 are pure code defects.**

On the Web SPA, the trust-critical fakes the v1 audit flagged are **almost entirely still present**: silent fixture fallback, fake "key validated", the simulated `/run`, the in-context leash no-op, fabricated success toasts, read-only "spend cap" fields, hardcoded consent permissions, the dev-only palette action. The few genuine fixes are narrow (the `/approvals` screen now POSTs; the authoring Apply/Revert buttons now POST — but to a gateway endpoint documented as a **"no-op stub … no patch pipeline yet"**, and against a patch the inert Generate button never produced). Accessibility and responsiveness are essentially unchanged from v1.

Backend test suite: **2361 passed, 15 skipped, 0 failed** (~4 min). Green — but blind to the CLI config crashes and to the entire frontend.

---

## Confirmed status of the master-report criticals that touch this domain

| Master ID | v1 verdict | **v2 verdict** | Evidence (current) |
|---|---|---|---|
| **BB-001** `config-get` crashes | Critical | **STILL-OPEN** (reproduced live) | `cli/main.py:2209` `"safety_tier": cfg.safety_tier` → `AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'` |
| **BB-002** `config-set` crashes on safety/budget keys | Critical | **STILL-OPEN / CHANGED** (reproduced live) | `cli/main.py:2258,2260,2262`; live: `Invalid value for safety_tier: "ResolvedConfig" object has no field "safety_tier"`. Raw traceback gone; still non-functional |
| **UX-001 / BB-006** fake "validated" key | Critical | **STILL-OPEN** | `SetupWizard.tsx:51` `keyValid = key.trim().length >= 12`; `:239` "Key looks good — validated." No `fetch`/validate call in the file |
| **UX-018 / BB-004** silent fixture fallback | Critical | **STILL-OPEN** | `domains/work.ts:201-203` `catch { setReconnecting(true) // leave fixtures in place }` (same in memory/og/skills/integrations/config/authoring/approvals/chat/spend); `__root.tsx:17` "degrades to seeded fixtures … never blanks the UI" |

All four "this looks fixed" / "P0" criticals in this domain are **still open.**

---

## Prior-finding verification table (every BB-* prior ID)

| ID | Title (abbrev) | v1 sev | **Status** | Current evidence |
|---|---|---|---|---|
| BB-001 | `config-get` raw-traceback crash | Critical | **STILL-OPEN** | `cli/main.py:2205-2213` (eager flat dict), crash at `:2209` reproduced via `uv run hachi config-get` |
| BB-002 | `config-set` crashes on 3 of 5 keys | Critical | **STILL-OPEN (CHANGED)** | `cli/main.py:2247-2272`; safety_tier/max_tokens/session_cost_cap_usd all fail live; only provider.model/base_url work |
| BB-003 | Web desktop-only, no responsive | High | **STILL-OPEN** | 3 `@media` total, all non-width (`colors.css:49`, `LoyalLoop.tsx:48`, `Terminal.tsx:126`); 0 `matchMedia`/`innerWidth`/`max-width`/`min-width` |
| BB-004 | Silent fixture fallback | High/Crit | **STILL-OPEN** | `domains/work.ts:201-203` et al.; no per-route error/loading/stale state |
| BB-005 | `/run` is a simulation | High | **STILL-OPEN** | `run.tsx:7-11` `TODO(live-run) … simulation … left intact`; `run.tsx:107-111` auto-boots fake run |
| BB-006 | Fake "key validated" | High/Crit | **STILL-OPEN** | `SetupWizard.tsx:51,239` |
| BB-007 | Modals lack dialog semantics/focus-trap/Esc | High | **STILL-OPEN** | `role="dialog"` = 1 (`LeashPrompt.tsx:62`); `aria-modal` = 0 across frontend |
| BB-008 | Clickable `<div>/<g>` not keyboard-operable | High | **STILL-OPEN** | `work.tsx:1030-1044`, `og.tsx:191-198,330-345`, `integrations.tsx:479-503`, `authoring.tsx:201-216`, `index.tsx:204-218`; `onKeyDown/tabIndex/role` ≈ 1 match in routes |
| BB-009 | Light-mode contrast / hardcoded pills | High | **STILL-OPEN** | `colors.css:12` `--hachi-muted #8A8580` on `#FBF7EF`/`#FFFDF8`; amber `#A8651A` inline in `index.tsx:31`, `work.tsx:31`, `conversation.tsx:32`, `approvals.tsx:39`, `run.tsx:36` |
| BB-010 | Authoring Generate inert / summary hardcoded | High | **STILL-OPEN (PARTIAL)** | Generate has no onClick (`authoring.tsx:455-478`); `"+3 −2 lines"` literal (`:528`); Apply/Revert POST (`:585,535`) but gateway is a **stub** (`fastapi_app.py:874,879`, `control_plane.py:501,506`) and Generate never makes a patch |
| BB-011 | Icon-only buttons unnamed | High | **PARTIAL** | `aria-label` now 8 (run Back, skills Edit, settings Remove). Still missing: Memory fact edit/delete (`memory.tsx:120,123`), Work close (`work.tsx:253-268`), wizard/consent close, conversation send/stop. `Icon` has no title/aria support |
| BB-012 | Two parallel pack systems | High | **STILL-OPEN** | `hachi pack` (.ogpack, `commands/pack.py`) vs `hachi skill pack` (GitHub/URL, `commands/skill.py`); both live in `--help` |
| BB-013 | `doctor` soft-pedals broken skills, no verdict | Medium | **STILL-OPEN** | Live: `skills_health: "30 file(s), 23 error(s), 41 warning(s)"`, `skills_parse_failures: []`, `permissions_tier: "unrestricted"`, single uniform panel, no PASS/FAIL |
| BB-014 | No loading state; fixtures swap to real | Medium | **STILL-OPEN** | No loading branch on any data route; loaders hydrate over fixtures silently (`bootstrap`/`live` pattern) |
| BB-015 | Memory shows fabricated recall unasked | Medium | **STILL-OPEN** | `memory.tsx:220-221` `recallQuery='what do you know about my repo?'`, `recallDone=true` → seeded recall renders on load |
| BB-016 | Destructive/bulk actions, no confirm | Medium | **STILL-OPEN** | delFact (`memory.tsx:592`), Decline-all (`approvals.tsx:514`), safety→Open (`settings.tsx`), remove folder/cmd/skill — all immediate, no confirm/undo |
| BB-017 | Inputs lack label association | Medium | **STILL-OPEN** | Home/Memory/Authoring/composer raw inputs placeholder-only; `Input.tsx:56` label has no `htmlFor`/`id` |
| BB-018 | Provider choice contradictory | Medium | **STILL-OPEN** | `quickstart --provider` = `anthropic\|openai-compatible\|ollama` (live); engine supports 7 kinds; banner advertises groq/together/deepseek/openrouter/gemini links (`main.py:1314-1321`); wizard hardcodes Anthropic |
| BB-019 | Palette dev action + fake recents + no kbd nav | Medium | **STILL-OPEN** | `CommandPalette.tsx:102` "Preview the reconnecting state"; `:105-109` fake recents; keydown only ⌘K/Esc, no arrow/Enter |
| BB-020 | Spend caps look editable, are static | Medium | **STILL-OPEN** | `settings.tsx:527-541` per-task literal `$1.00`; `:543-561` daily `{budgetStr}` — both static `<div>`s; no input, no Save |
| BB-021 | Skills CTA noops + hardcoded consent perms | Medium | **STILL-OPEN** | Browse noop (`skills.tsx:202`); operator edit noop (`skills.tsx:40`); `KennelConsent.tsx:13-17` hardcoded GitHub/Notion/Slack + generic fallback (`:26`) |
| BB-022 | Home hides approvals past 3 | Medium | **STILL-OPEN** | `index.tsx:582` `approvals.slice(0,3)`; header shows full count (`:576-578`); no "see more"/link to `/approvals` |
| BB-023 | No in-transcript "thinking" cue | Medium | **PARTIAL (Web only now)** | Web: `conversation.tsx` still mounts only the user bubble on send; no inline pending bubble. (TUI half OBSOLETE.) |
| BB-024 | No visible focus ring (outline:none) | Medium | **STILL-OPEN** | `base.css:38` zero-specificity `:where(...):focus-visible`; inline `outline:'none'` at `conversation.tsx:845`, `index.tsx:474`, `memory.tsx:146,329,391`, `authoring.tsx:451`, `SetupWizard.tsx:232`, `CommandPalette.tsx:158` |
| BB-025 | TUI cancelled-turn marker | Medium | **OBSOLETE** | Old Textual TUI deleted; cockpit excluded |
| BB-026 | TUI `?` help inoperative | Medium | **OBSOLETE** | ditto |
| BB-027 | TUI deny-rationale not enforced | Medium | **OBSOLETE** | ditto (`tui/widgets.py` deleted) |
| BB-028 | Settings permanent "unsaved changes" | Low | **PARTIAL (CHANGED)** | `settings.tsx:64` now starts `false`, but no handler clears it and there is no Save button → becomes permanent once any toggle flips it |
| BB-029 | Status meaning color-only / hardcoded hex | Low | **STILL-OPEN (PARTIAL)** | Pills carry text labels (better than pure color) but no `role`/`aria-label`; hex still inline (`index.tsx:31`, `work.tsx:31`, `approvals.tsx`, `integrations.tsx`); `StatusPill.tsx` primitive unused |
| BB-030 | Toasts pointer-events:none, not pinnable | Low | **STILL-OPEN** (assumed) | `ui/Toast.tsx` unchanged pattern (auto-dismiss, `role="status"`) — not re-deep-verified |
| BB-031 | Switch toggles unnamed in context | Low | **STILL-OPEN** | `Switch.tsx` has role/aria-checked but Settings passes no label (`settings.tsx:292-295,496-499`) |
| BB-032 | No virtualization; OG graph overlaps | Low | **STILL-OPEN** | Routes map full arrays; OG layout fixed-height even-division (`og.tsx`) |
| BB-033 | Windows-hostile `export` / broken "one command" | Low | **STILL-OPEN (PARTIAL)** | `onboarding.py:310` still `export {env}=sk-...`; banner now uses `secrets --set {env}=<your-key>` (`main.py:1332,1338`) but `--set` takes a bare KEY then prompts (`main.py:2462-2474`) and **hard-exits non-TTY** (`:2465-2467`) — the exact non-TTY context it's suggested in |
| BB-034 | Redundant REPL entry points + dual creds | Low | **STILL-OPEN** | `--repl` global (`main.py:163`), `run --repl` (`:1888`), `run-repl` (`:1944`); `reauth`→`CredentialVault` (`:1697`) vs `secrets`→`secret_store` (`:2449`) |
| BB-035 | `channel whatsapp` binds 0.0.0.0, empty verify token | Low | **STILL-OPEN** | `commands/channel.py:143` `host="0.0.0.0"  # noqa: S104`; `:186` `Verify token: (none)` no warning |
| BB-036 | TUI Esc-cancel invisible in Footer | Low | **OBSOLETE** | Old Textual TUI deleted |

**Prior BB tally:** of 36 prior findings, **4 are OBSOLETE** (deleted TUI). Of the remaining 32: **0 fully FIXED**, **~6 PARTIAL/CHANGED** (BB-010 buttons wired-to-stub, BB-011 some labels added, BB-023 TUI half gone, BB-028 flag logic changed, BB-029 labels present, BB-033 banner reworded), and **~26 STILL-OPEN**. Zero of this domain's prior findings are cleanly resolved.

---

## Detailed findings — re-verified prior items (selected, full evidence)

### BB-001 — `hachi config-get` still crashes with a raw traceback on every call · Critical · STILL-OPEN
- **Repro (live):** `uv run hachi config-get` · `uv run hachi config-get provider.model` · `uv run hachi config-get safety_tier` — all crash identically.
- **Actual:** Rich-rendered traceback ending `AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'`.
- **Root cause:** `cli/main.py:2205-2213` eagerly builds a flat dict that reads `cfg.safety_tier` (`:2209`), `cfg.max_tokens` (`:2210`), `cfg.session_cost_cap_usd` (`:2211`) **before** any per-key lookup, so even `config-get provider.model` crashes. `ResolvedConfig` (`core/settings.py:85-97`) exposes tier at `permissions.tier`, tokens at `agent.max_tokens`, and **has no `session_cost_cap_usd` field at all**. Verified via `grep` — `safety_tier`/`session_cost_cap_usd` appear nowhere in `settings.py`.
- **Positive control:** `uv run hachi config-validate` succeeds and prints `permission_tier  tier='guided'`, `max_tokens  max_tokens=8192` — i.e. the data is reachable through the correct paths. This is a pure code defect.
- **Why the green suite missed it:** the rewrite's tests (`tests/test_gateway_config_mutations.py`) exercise `_InProcessGatewayRuntime.update_*` (which correctly use `cfg.permissions.tier`), not the `config-get`/`config-set` CLI commands.
- **Impact:** A headline read-only inspection command is 100% broken and leaks a stack trace; breaks any CI that inspects config; reads as unstable to evaluators.

### BB-002 — `hachi config-set` still fails on every safety/budget key · Critical · STILL-OPEN (CHANGED)
- **Repro (live):**
  - `uv run hachi config-set safety_tier guided` → `Invalid value for safety_tier: "ResolvedConfig" object has no field "safety_tier"`
  - `uv run hachi config-set max_tokens 4096` → `Invalid value for max_tokens: "ResolvedConfig" object has no field "max_tokens"`
  - `uv run hachi config-set session_cost_cap_usd 5` → `Invalid value for session_cost_cap_usd: "ResolvedConfig" object has no field "session_cost_cap_usd"`
  - `uv run hachi config-set provider.model claude-sonnet-4-5` → `Set provider.model = 'claude-sonnet-4-5'` (works; persisted — confirmed via `doctor`).
- **Change vs v1:** the assignment is now wrapped in `try/except (ValueError, TypeError)` (`cli/main.py:2270-2272`); pydantic raises `ValueError` for the unknown field, so the **raw traceback is gone** — but the command remains non-functional for the three keys it documents (`:2242-2243`). The `safety_tier` value-set validation (`:2253-2257`) runs *before* the failing assignment, so a valid tier still dies at `cfg.safety_tier = value` (`:2258`).
- **Evidence:** `cli/main.py:2258` (`cfg.safety_tier = value`), `:2260` (`cfg.max_tokens = int(value)`), `:2262` (`cfg.session_cost_cap_usd = float(value)`); fields absent in `core/settings.py:85-97`.
- **Impact:** The primary scriptable mutation surface for the safety tier and token/cost caps — the product's positioned differentiators — cannot be changed via the documented command; only `provider.model`/`provider.base_url` work. Only working path to change tier is hand-editing `settings.toml` (or `hachi permissions --tier`).

### BB-006 / UX-001 — SetupWizard still claims "validated" on a length check · High/Critical · STILL-OPEN
- **Trace:** SetupWizard Step 1 → paste any ≥12-char string. `SetupWizard.tsx:51` `const keyValid = setup.key.trim().length >= 12;` → green check + `:239` "Key looks good — validated." `grep` for `fetch`/`validate`/`POST` in the file: only the literal string at `:239`. A real validator exists (`lib/data/domains/config.ts validateProvider` → `POST /config/provider/validate`) but is never imported/called here.
- **Impact:** Onboarding's single most important step lies; a bogus key shows "validated", failures surface only later in chat with no link back.

### BB-004 / UX-018 — Loaders silently render fabricated fixtures on gateway failure · High/Critical · STILL-OPEN
- **Trace:** start SPA with gateway down → every domain loader `catch`es and calls `setReconnecting(true)` while leaving seeded fixtures rendered. `domains/work.ts:201-203` (`// leave fixtures in place`), plus identical blocks in `memory.ts`, `og.ts`, `skills.ts`, `integrations.ts`, `config.ts`, `authoring.ts`, `approvals.ts`, `chat.ts`, `spend.ts`. `__root.tsx:17` comment: "degrades to seeded fixtures on failure, so this never blanks the UI." The only signal is the global `ReconnectingBanner` pill; no route distinguishes live vs fabricated data, and there is no "sample data" marker.
- **Impact:** For a trust product, the worst class of bug — users make decisions (approve spend, trust "connected" integrations, read "memory facts") on invented data with no way to tell. On a cold gateway-down boot the entire UI is fake behind a transient pill.

### BB-005 / UX-017 — `/run` "Watch live" is still a self-described simulation · High · STILL-OPEN
- **Trace:** `run.tsx:7-11` retains `TODO(live-run): … The run.tsx simulation (openRun queue + _runIv interval) is left intact until the live session/run mapping is defined`. `run.tsx:107-111` auto-boots a fake run on mount when none is active. Terminal lines, "the pack", steps, and the spend meter are driven by a store-side `setInterval` (`runTick`), not gateway SSE; a leash/approval prompt is synthesized mid-run. Nothing on screen flags it as simulated.
- **Impact:** Users watch invented activity and a fake cost meter believing it is their real task — contradicts "cost is a feature, never hidden."

### BB-010 / WB-008 — Authoring Generate inert; Apply/Revert wired to a stub · High · STILL-OPEN (PARTIAL)
- **Trace:** Generate button has only hover handlers, **no onClick** (`authoring.tsx:455-478`); patch summary is the literal `"+3 −2 lines"` (`:528`); the post-apply line is fabricated ("Written to … re-indexed in 0.3s", `:626`). Apply/Revert now call real handlers (`:585`, `:535`) that POST to `/authoring/targets/{name}/apply|revert` — **but the gateway endpoint is documented as a no-op stub**: `fastapi_app.py:874` "Apply a pending patch to the target (stub — no patch pipeline yet)", `control_plane.py:500-508` "(no-op stub)". Combined with the inert Generate (no patch is ever produced) and the diff being just the raw source file, the **end-to-end authoring flow does nothing for the user.**
- **Impact:** The hero "edit Hachi's own brain" feature is non-functional end to end; "Apply patch" toasts/acts against a phantom patch.

### WB-009 — Web spend meter double-counts cumulative usage · High · STILL-OPEN (first-hand confirmed)
- **Trace:** the engine emits the CostTracker's **cumulative snapshot** every LLM call: `core/loop.py:760` `self.on_event("usage", self.cost_tracker.snapshot().to_dict())`. The CLI treats these correctly as cumulative and computes per-turn deltas: `cli/main.py:720` ("cumulative counters … builds per-turn deltas"), `:750-755` (`delta = total - prev`). The web does the opposite — it **adds** each cumulative snapshot to the running total: `domains/chat.ts:237-238` `taskSpent: s.taskSpent + costUsd`, `spent: s.spent + costUsd`. So snapshots [0.01, 0.03, 0.06] show 0.06 in the CLI but 0.10 in the web. Spend is over-counted.
- **Impact:** The "cost is a feature / never hidden" promise shows inflated, wrong numbers — the moat visualization is incorrect.

### BB-012 — Two parallel pack systems · High · STILL-OPEN
- **Live:** `hachi pack --help` → install/list/remove/update/search/index for `.ogpack` archives. `hachi skill pack --help` → list/install/remove for "community skill packs" (GitHub/URL). Different sources, formats, and remove semantics; a pack installed via one is invisible to the other's `list`. (`commands/pack.py`, `commands/skill.py`.)

### BB-013 — `doctor` buries a broken skill library with no verdict · Medium · STILL-OPEN
- **Live `doctor`:** `skills_loaded: 53`, `skills_health: "30 file(s), 23 error(s), 41 warning(s)"`, **`skills_parse_failures: []`**, `permissions_tier: "unrestricted"` — all in one uniformly-styled panel with **no overall PASS/FAIL**. By contrast `security-audit` (`status: fail`, severity counts, exit code 2 on `--fail-on-high`) and `config-validate` (`Config OK` / non-zero) both emit verdicts. `doctor` should too.

### BB-018 — Provider vocabulary contradicts across surfaces · Medium · STILL-OPEN
- **Live:** `quickstart --provider` advertises only `anthropic | openai-compatible | ollama`; `setup --permission-tier` shows tiers but provider stays narrow. The engine supports 7 kinds (`core/settings.py:8-10`: anthropic, openai-compatible, openrouter, groq, together, deepseek, ollama) and the missing-key banner advertises get-a-key links for groq/together/deepseek/openrouter/gemini (`main.py:1314-1321`). The Web wizard hardcodes Anthropic. Free Gemini/Groq tiers (a selling point for the "burned by a paid host" ICP) are unreachable via guided setup.

### BB-033 — Onboarding guidance is Windows-hostile and the "one command" refuses where shown · Low · STILL-OPEN (PARTIAL)
- `core/onboarding.py:310` still returns `export {env_name}=sk-...` — Unix-only; fails on the project's primary Windows/PowerShell platform.
- The boot banner was reworded to `hachi secrets --set {env}=<your-key>` (`main.py:1332,1338`) — but `secrets --set` takes a **bare KEY** then prompts for the value (`main.py:2468`), so `--set ANTHROPIC_API_KEY=sk-...` would store the secret under the literal name `ANTHROPIC_API_KEY=sk-...`. And `secrets --set` **hard-exits in non-TTY** ("requires an interactive terminal", `main.py:2465-2467`) — exactly the CI/Docker/piped context where the non-TTY branch (`:1338`) suggests it.

(BB-014, BB-015, BB-016, BB-017, BB-019, BB-020, BB-021, BB-022, BB-023, BB-024, BB-028, BB-029, BB-031, BB-032, BB-034, BB-035 — all re-verified per the table above; full v1 evidence still holds at the cited lines.)

---

## NEW findings (v2)

### BB-N01 — `hachi run --resume`/`--continue` (and global `-r`/`-c`) are advertised but silently no-op · High · NEW
- **Repro (code path):** `hachi --help` prominently lists `--resume -r` and `--continue -c`; `run --help` lists the same. Both the top-level callback (`main.py:155-174`) and `run` (`main.py:1891-1902`) route through `_launch_assistant`, which prints `"Session resume is not yet wired into the cockpit; starting a fresh session."` (`main.py:1842-1845`) and starts fresh.
- **Expected vs actual:** Flags advertised in primary help (resume was a real feature in the now-deleted TUI) → they reload a past session. Actual: they print a yellow notice and start a fresh session, discarding the user's intent.
- **Impact:** A documented, top-level continuity feature is dead; users invoking `hachi -c` to resume lose context silently. (Web Memory "Resume" is likewise a toast-only stub — see BB-N06 — so resume is broken on both surfaces.)

### BB-N02 — In-context leash Allow/Deny is a client-only no-op (no approval POST) · High · NEW (extends WB-007)
- **Trace:** `domains/chat.ts:395-406` `allowLeashLive()` only clears the in-context prompt optimistically; its own comment says *"In a full implementation, POST to an approval endpoint here … the in-context leash is informational in the current gateway schema."* `denyLeashLive()` (`:408-414`) just aborts the local stream. So the conversation-screen and `/run` leash buttons ("Allow / Always allow / Decline") change nothing server-side. (Distinct from the `/approvals` screen, which DOES now POST — see "genuine fixes".) The `LeashPrompt` `countdown` prop is never passed anywhere (`grep countdown=` → 0), so no in-context leash shows the auto-decline countdown.
- **Impact:** The headline "you hold the leash" control, shown inline during a task, does nothing — a governance control that looks real but isn't, the single worst class for this product.

### BB-N03 — Approvals auto-decline countdown is fabricated on live data · High · NEW
- **Trace:** `domains/approvals.ts:117-118` hardcodes `expiresAt: 0, totalMs: 0` for every live approval ("no server-sent expiry — countdown disabled"), yet `ApprovalCard` always renders an "auto-declines in {countdownLabel}" bar (`approvals.tsx:242-280`) and the screen copy promises requests "expire — Hachi stays put". On real data the label shows `0s` and the progress bar computes `0/0` (NaN%).
- **Impact:** A safety promise (timed auto-decline of risky actions) is visibly displayed but does not exist on live data — fabricated governance behavior.

### BB-N04 — Memory add/edit/delete and proposed-change approve/reject are local-only with fake success toasts, despite real gateway endpoints existing · High · NEW
- **Trace:** `domains/memory.ts:8-11` asserts "the gateway exposes READ-ONLY endpoints. There are no write endpoints for facts … remain local-only." This is **stale and wrong**: the gateway has `POST /memory/facts` (`fastapi_app.py:840-847` → `control_plane.add_memory_fact`, a real vault write) and `DELETE /memory/facts/{fact_id}` (`:849-855` → `delete_memory_fact`). The store toasts success anyway — `saveFact` "Saved — Hachi remembers it", `delFact` "Forgotten — Hachi removed that fact", `approveProposed/rejectProposed` "Updated — Hachi remembers the new version" — without POSTing. After the next `loadMemory`, the user's edits silently vanish.
- **Impact:** Fabricated-persistence: the user is told "Hachi remembers" a fact that was never saved, and existing real endpoints are left unwired. The memory-governance surface ("Hachi proposes an update") doesn't actually act.

### BB-N05 — `connectIntegration` fabricates capability scopes and toasts "Connected" before/independent of the gateway result · Medium · NEW
- **Trace:** `store.ts connectIntegration` rewrites an integration's capability text to invented strings (e.g. Notion → "read & update pages"), clears its `risky` flag, and fires a "Connected — Hachi can reach it now" toast; `domains/integrations.ts` then reloads, but on failure only sets `reconnecting`. The fabricated scopes + success toast persist until a successful reload.
- **Impact:** A user sees "connected" + invented permission scopes for an integration that may not be connected — undercuts "you see exactly what it can do."

### BB-N06 — Web Memory "Resume" / routine create / dry-run fabricate success with no backend action · Medium · NEW
- **Trace:** Memory session `resume` is a toast-only no-op (`memory.tsx` — `showToast('Resuming … — picking up where you left off')`, no navigation/load/POST). Conversation "Create routine"/"Dry run" run the store's mock script and fabricate an on-watch confirmation ("On watch — runs weekdays at 8:00") regardless of input, with no live branch — so on a live gateway they show fabricated created-state.
- **Impact:** Multiple "it worked" confirmations for actions that did nothing — same fake-success class.

### BB-N07 — Settings "Add folder"/"Add command" insert literal placeholder values and toast success · Low · NEW
- **Trace:** `store.ts addFolder` pushes literal `"~/new-folder"` into `allowedFolders` and toasts "Added — edit the path in your config"; `addCommand` pushes literal `"cmd"`. There is no input to enter a real path/command and no gateway write (no permissions-folder endpoint in `config.ts`).
- **Impact:** The permission-editing UI appears to grant a filesystem root / shell command but inserts a meaningless placeholder that doesn't persist — misleading on a security surface.

### BB-N08 — Integrations "Add MCP server" affordance has no onClick at all · Medium · NEW (extends BB-008)
- **Trace:** `integrations.tsx:479-503` renders a clickable-styled row ("+ Add MCP server", `cursor:pointer`, hover tint) with `onMouseEnter/onMouseLeave` but **no `onClick`** and no role/tabIndex. It looks interactive and is a dead end (mouse and keyboard alike).
- **Impact:** The primary path to add an MCP server from the UI does nothing.

### BB-N09 — `skill list` shows duplicate skill names (global `.og` namespace collision, user-visible) · Low · NEW (black-box surfacing of WB-020)
- **Repro (live):** `uv run hachi skill list` lists `execution-protocol`, `identity`, and `report-format` **twice** each (confirmed via `uniq -d`). Same-named `.og` nodes from different files collide in the global namespace; the user sees apparent duplicates with no way to disambiguate which one is active.
- **Impact:** Discoverability/trust hit on the product's stated moat (skills); a user can't tell which of two identically-named skills is in effect.

### BB-N10 — Stale `.tokoboros` block-path persists in upgraded installs; `run`/`--repl` help still references the deleted "Textual TUI" · Low · NEW (caveated)
- **Trace (data):** live `hachi permissions` shows Blocked Folders `… .tokoboros …` — from the user's pre-existing `~/.hachi/device-permissions.og` (a pre-rename artifact). **The shipped default is clean** (`core/permissions.py:201-203` blocks `.ssh`/`.hachi`/AppData; `grep tokoboros src/` → 0). So this is an **upgrade/migration gap**, not a code regression: an upgraded user keeps a block-path (`.tokoboros`) that no longer protects the real config dir (`.hachi`), and there is no migration to fix it.
- **Trace (docs):** `run` help/docstring still says "Default: Textual TUI" / "instead of the Textual TUI" (`main.py:1889,1907-1908`), and `--repl` help (`:164`) too, though `hachi run` now launches the Cockpit and the Textual TUI was deleted.
- **Impact:** Minor trust/clarity drift; the stale block-path is a (low-severity) security-hygiene gap for upgraders.

> Note on `frontend/src/lib/data/store.ts` mock scripts (WB-011): still present, but now **guarded** in live mode for the chat path (`startTaskLive` returns before the script). The scripts remain embedded in live action methods, and the `/run` path has **no** such guard (BB-005). Treated as a code-health/PARTIAL item, folded into BB-005/WB-011 rather than re-filed.

---

## Genuine fixes / improvements observed (credit where due)

- **`/approvals` screen now performs real allow/deny/allow-once** — `domains/approvals.ts:185-245` POSTs to `/approvals/{request_id}` with `{action, rationale}`; wired in `approvals.tsx`. (The **in-context** leash is still a no-op — BB-N02 — and Decline still captures no reason, and the countdown is fabricated — BB-N03.)
- **Authoring Apply/Revert buttons now POST** to real routes (the gateway side is still a stub — BB-010).
- **`config-set provider.model` / `provider.base_url` work** and persist.
- **`security-audit`** is exemplary: structured JSON, severity counts, correct exit codes (`--fail-on-high` → exit 2). `config-validate` likewise emits a clear verdict. `doctor` should adopt the same pattern (BB-013).
- **`mcp list`** has a clean empty state with a next-step hint.
- **Shipped permissions default is rename-clean** (no `tokoboros` in code).
- **Backend test suite is green** (2361 passed, 15 skipped, 0 failed).
- Some icon buttons gained `aria-label` (BB-011 PARTIAL).

---

## Methodology — ran vs read

**Ran (live, captured output):**
- `uv run hachi --help`, `config-get` (+ `provider.model`, `safety_tier`, `--json`), `config-set` (safety_tier/max_tokens/session_cost_cap_usd/provider.model), `config-validate`, `config`, `doctor`, `security-audit --json [--fail-on-high]`, `permissions`, `mcp list`, `memory --help`, `pack --help`, `skill pack --help`, `skill list`, `quickstart --help`, `setup --help`. Reproduced BB-001/BB-002 first-hand; confirmed BB-013/BB-018/BB-035 from live output; observed BB-N09 (duplicate skills) and BB-N10 (`.tokoboros`) live.
- `uv run python -m pytest -q` → **2361 passed, 15 skipped, 0 failed** in 238.83s.

**Read (code as the rendered experience):**
- CLI: `cli/main.py` (config-get/set, run/cockpit/run-repl, banner, secrets, doctor), `cli/commands/channel.py`, `core/onboarding.py`, `core/settings.py`, `core/permissions.py`, `core/config_loader.py`, `tests/test_gateway_config_mutations.py`.
- Web SPA (route-by-route + grep sweeps): `__root.tsx`, `routes/{index,work,approvals,conversation,memory,og,skills,settings,integrations,run,authoring}.tsx`, overlays (`SetupWizard`, `CommandPalette`, `KennelConsent`, `ReconnectingBanner`), primitives (`Input`, `Switch`, `Icon`, `Toast`, `StatusPill`, `LeashPrompt`), `lib/data/domains/*.ts`, `lib/data/store.ts`, `styles/tokens/{colors,base}.css`.
- Gateway (to verify the contract behind the SPA): `gateway/fastapi_app.py` (route table), `gateway/control_plane.py` (handler stubs), `core/loop.py` (usage emission).

**Excluded:** the Cockpit TUI (`src/hachi/tui/cockpit/**`) — in-flight, owned by a concurrent session — and therefore `hachi run`/`hachi cockpit` runtime behavior (the command launches the cockpit; only its stale help text and the resume no-op are filed, both at the CLI layer).

**Side effect disclosed:** `config-set provider.model claude-sonnet-4-5` (run to confirm the working path) persisted a model-name change to the local `settings.toml`. No other state was mutated.

---

## Severity tally (v2, this domain)

| Severity | New this pass | Prior STILL-OPEN/PARTIAL carried |
|---|---|---|
| **Critical** | 0 new | BB-001, BB-002 (both STILL-OPEN); BB-004, BB-006 (STILL-OPEN, master-Critical) |
| **High** | BB-N01, BB-N02, BB-N03, BB-N04 | BB-003, BB-005, BB-007, BB-008, BB-009, BB-010, BB-011, BB-012, WB-009 |
| **Medium** | BB-N05, BB-N06, BB-N08 | BB-013, BB-014, BB-015, BB-016, BB-017, BB-018, BB-019, BB-020, BB-021, BB-022, BB-023, BB-024 |
| **Low** | BB-N07, BB-N09, BB-N10 | BB-028, BB-029, BB-030, BB-031, BB-032, BB-033, BB-034, BB-035 |

**Bottom line:** the v1 picture is largely intact. The Critical CLI crashes (BB-001/002) the audit lead believed fixed are **not**; the Web SPA's trust-eroding fakes are overwhelmingly **still open**, and three new High-severity fakes were found (in-context leash no-op, fabricated auto-decline countdown, fake memory persistence). The one bright spot is the `/approvals` screen now genuinely POSTs. A green backend test suite gives **false** assurance for this domain: it covers neither the broken CLI commands nor any frontend behavior.
