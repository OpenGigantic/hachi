# Hachi — Verification Strategy (Audit v2)

**Date:** 2026-06-18 · **Owner:** QA Lead
**Inputs:** [00 — Master Report](00-MASTER-REPORT.md) (esp. §6 fix list, §7 verification-integrity meta-finding) · [06 — PO Decisions](06-product-owner-decisions.md) · [09 — Release Readiness](09-release-readiness.md) gates A/B/C · sub-reports [01](01-black-box.md)–[05](05-performance-scalability.md).
**Status:** Planning / analysis only. This document changes no source. Where it says "add a test," that is a specification for the implementing agent.

> **Mapping note.** The Execution Plan (`07-execution-plan.md`) was not yet written when this strategy was authored. Every row below is therefore keyed to its **finding ID** (the stable identifier across all v2 docs). When 07 lands, each row maps 1:1 to the T-ID that owns the finding; until then, treat the finding ID as the join key. The PO disposition (`NOW` / `BEFORE-WEB`) from [06](06-product-owner-decisions.md) is carried in the matrix so the sprint owner is unambiguous.

---

## 0. The one rule this document exists to enforce

This audit happened because three independent "it works" signals were lying at once (Master §7):

1. **WB-001** — the `frontend-contract` CI job guarded a frontend that didn't exist, so it red-failed every push and the red was ignored as noise.
2. **WB-N07** — `tests/test_config_get_set.py` mocks a config shape the real object does not have, so 11 tests pass green while `hachi config-get` crashes 100% of the time.
3. **The frontend has zero test tooling** — no vitest, no testing-library, no integration smoke (confirmed first-hand: `frontend/package.json` has no `vitest`/`@testing-library`/`jsdom`/`msw`/`playwright`, and there are no `*.test.*` files under `frontend/src`).

**Overriding theme: no fix is "done" until a test reproduces the original failure (red before, green after) and that test runs in a CI that is green for real.** Treat every future "it's fixed" with one question: *what test reproduces the failure, and does it run in CI?*

---

## 1. Principles

### P1 — Red-before, green-after, every fix
Every fix ships with at least one test that **fails on the pre-fix code and passes on the post-fix code.** The author must demonstrate the red (capture the failing run) before landing the fix. A fix with only an after-the-fact assertion that happens to be green is not accepted — it cannot distinguish "fixed" from "never broken in this path." For bug fixes specifically, the test must encode the *user-observable* failure (a crash, a wrong number, a no-op), not an internal shape.

### P2 — Test the REAL surface, never a mock of a shape the real object lacks (the WB-N07 anti-pattern)
**WB-N07 is the named anti-pattern this whole document is built to prevent.** `tests/test_config_get_set.py::_fake_cfg()` returns:

```python
SimpleNamespace(provider=..., safety_tier="guided", max_tokens=8192, session_cost_cap_usd=5.0)
```

— i.e. `safety_tier` / `max_tokens` / `session_cost_cap_usd` as **flat top-level attributes** — and patches `hachi.cli.main.load_effective_config` to return it. But the real `ResolvedConfig` (`src/hachi/core/settings.py:85-97`) has **none of those as top-level fields**: the tier lives at `cfg.permissions.tier` (`settings.py:47`), tokens at `cfg.agent.max_tokens` (`settings.py:26`), and **`session_cost_cap_usd` does not exist anywhere in the model.** The mock satisfies exactly the access pattern the real object violates, so the suite is green while `hachi config-get` raises `AttributeError` at `cli/main.py:2209`.

Rules that follow:
- **No mock may have an attribute the production type lacks.** If a test builds a fake config/object, it must be built from the real type (`ResolvedConfig(...)`, `default_config()`, or a `load_effective_config()` against a tmp settings file) — or assert against the real type's field set so divergence fails the test.
- **Prefer the no-mock variant** for CLI commands: invoke the actual command against a real on-disk `settings.toml` in a `tmp_path`, so the real attribute paths are exercised end to end.
- Add a **structural guard** so this class can't silently regress: a test that asserts the set of attributes any `_fake_*` config exposes is a subset of `ResolvedConfig.model_fields` (+ documented nested paths). A fake that grows a phantom field fails immediately.

### P3 — CI must be green-for-real before any verification is trusted (WB-001)
Until "green" means something, nothing else is verifiable, so this lands **first** (PO Sprint 1 Task 1; Gate A G-A0). "Green for real" means:
- `frontend-contract` runs against the **actual shipped SPA** and its real scripts. *(State note: the current working tree has already moved here — `frontend/package.json` now defines `generate:api`/`check:api` with an `openapi-typescript` devDependency, and `.github/workflows/ci.yml:130-163` now runs `npm install` → `check:api` → `typecheck` with no phantom-file list. The white-box report described the **pre-remediation** snapshot. Verification therefore shifts from "make it real" to "prove it stays real and actually fails on drift" — see §5.)*
- The strict `ruff` + `mypy` gate is **blocking on `main`**, not `continue-on-error` (today `ci.yml:80,84` are `continue-on-error: true`; only the PR-only strict-subset gates at `:86-88` block).
- No job is permitted to be red-and-ignored. A red required check blocks merge, full stop.

### P4 — The frontend needs real test tooling (it has none)
Every "done" claim about a web journey is currently unfalsifiable. Before any frontend fix is verifiable, the SPA must gain: **vitest + @testing-library/react + jsdom**, a network boundary (**msw** or a thin typed gateway mock), and **one real integration smoke that boots the actual gateway and asserts the SPA reads live data, not fixtures.** Until that layer exists, frontend fixes are verified by code-reading only — which is exactly the condition that produced this audit. Full spec in §4.

### P5 — Trust fakes get a dedicated regression group
Because the brand is trust, the highest-value regression net is one that asserts **no surface renders fabricated data**: gateway-down shows error/empty (not fixtures), an invalid key never shows "validated," spend reflects cumulative usage exactly once, a memory edit survives reload, and no countdown is shown without a server expiry. This is its own suite (§6) so a future "looks fixed" fake can't slip back in unnoticed.

### P6 — Verification runs on this repo's real harness
All repro/verify commands target this repo on Windows and prefer module invocation over `.exe` shims (which are blocked here — see Memory: Windows test runner workaround). Concretely: `python -m pytest`, `python -m mypy`, and the CLI via `PYTHONPATH=src python -m hachi.cli.main <cmd>`. Full harness in §3.

---

## 2. Per-fix verification matrix

Scope: the **11 P0 items** (Master §6 / README "the 11 must-fix") and the **P1 / BEFORE-WEB set** (Master §6, PO §1). Columns: Unit · Regression · Integration · Performance · Security · UAC (user-acceptance criteria). "—" = not applicable to that fix. Real paths throughout; new files marked *(new)*.

### 2A. P0 — Sprint 1 blockers (disposition: NOW)

#### P0-1 · WB-001 — `frontend-contract` CI guards the real SPA · *(CI/CD)*
| Col | Spec |
|---|---|
| **Unit** | n/a (CI-config change). Assert the job's steps resolve: `npm run --prefix frontend check:api` and `… typecheck` are real scripts in `frontend/package.json` (they now are). |
| **Regression** | The other five CI jobs (`test`, `security`, `lint`, `skill-lint`, `benchmark`) still run and pass; `frontend-contract` no longer hard-fails on "Missing script". |
| **Integration** | **Negative-control PR**: open a throwaway PR that edits a gateway route without regenerating the schema → `check:api`'s `git diff --exit-code -- src/lib/data/schema.d.ts` must go **red**. Revert; green. This proves the gate actually catches drift (D-1…D-4 class) rather than being decorative. Run `uv run hachi gateway-openapi --output openapi/hachi-openapi.json` then `npm run --prefix frontend check:api` locally to confirm clean. |
| **Performance** | — |
| **Security** | — |
| **UAC** | Every required check is green on a clean `main`; a contract-drifting PR is **blocked**; no required check is red-and-ignored. (Gate A G-A0.) |

#### P0-2 · WB-N07 — delete/repair the false-green config test · *(test integrity)*
| Col | Spec |
|---|---|
| **Unit** | Rewrite `tests/test_config_get_set.py` to **not** mock a phantom shape. Two acceptable forms: (a) build the fake from the real type — `cfg = default_config()` (or `ResolvedConfig(...)`) and exercise `cfg.permissions.tier` / `cfg.agent.max_tokens`; (b) **preferred** — drop the `load_effective_config` mock entirely and point the CLI at a real `settings.toml` in `tmp_path` (monkeypatch `HACHI_CONFIG_DIR`/paths). Either way the test must turn **red on today's `cli/main.py:2209-2211`** and green only after P0-3. |
| **Regression** | Keep coverage of the working paths (`provider.model`, `provider.base_url`) and the validation/exit-code branches. |
| **Integration** | Covered by P0-3's real-command test. |
| **Performance** | — |
| **Security** | — |
| **Security/structural** | Add a guard test *(new, e.g. `tests/test_config_mock_shape_guard.py`)*: assert that any config fake used in the config tests exposes only attributes present on `ResolvedConfig` (top-level) or its documented nested models — so a phantom field re-introduces a failure. This is the generalized WB-N07 tripwire. |
| **UAC** | The config tests fail on the unfixed command and pass on the fixed one; no test in the repo asserts against a config attribute `ResolvedConfig` does not have. (Gate A G-A0.) |

#### P0-3 · BB-001 / BB-002 / WB-N06 — `hachi config-get` / `config-set` crash on safety/budget keys · *(functional bug)*
| Col | Spec |
|---|---|
| **Unit** | In the rewritten `tests/test_config_get_set.py`, drive the **real command** via `CliRunner`/`PYTHONPATH=src python -m hachi.cli.main`: (1) `config-get` with no args exits 0 and prints `safety_tier`, `max_tokens`, `session_cost_cap_usd` mapped from `cfg.permissions.tier` / `cfg.agent.max_tokens` / the new cap field; (2) `config-get safety_tier` exits 0; (3) `config-set safety_tier guided` then re-read returns `guided` and persists; (4) `config-set max_tokens 4096` and `config-set session_cost_cap_usd 5` succeed and persist; (5) invalid tier / non-int still exit 1 with a message (no raw traceback). Map keys to real paths: `safety_tier→permissions.tier`, `max_tokens→agent.max_tokens`, and **model a real `session_cost_cap_usd` field** (e.g. new `AgentSettings.session_cost_cap_usd` or a `BudgetSettings`) — today it exists in neither the model nor the config. |
| **Regression** | `config-validate` still reads `permissions.tier`/`agent.max_tokens` (it already works — use it as the positive control); `provider.model`/`provider.base_url` set still works; `doctor`/`security-audit` verdict commands unaffected. |
| **Integration** | End-to-end on a tmp config dir: `config-set safety_tier sandbox` → new process `config-get safety_tier` reads `sandbox` → `config-validate` agrees → `permissions` reflects it. Proves the write lands in the real `settings.toml` schema, not a mock. |
| **Performance** | — |
| **Security** | The new `session_cost_cap_usd` must be the same value the spend cap actually enforces (ties to UX-008) — assert read-back equals what the budget guard consumes, so the cap shown is the cap applied. |
| **UAC** | All documented keys (`provider.model`, `provider.base_url`, `safety_tier`, `max_tokens`, `session_cost_cap_usd`) read and write from the CLI with **no traceback**; `hachi config-get` exits 0 on a fresh install. (Gate A G-A2.) |

#### P0-4 · SEC-001 / REL-010 — community-pack ZIP zip-slip + decompression-bomb caps · *(security)*
| Col | Spec |
|---|---|
| **Unit** | New tests in `tests/` for `core/community_packs.py::_extract_zip` (the unhardened path, `:243-264`), mirroring whatever covers the already-hardened tar path: (a) a ZIP whose member name contains `../` (and an absolute path, and a `..\\` Windows variant) is **rejected** and writes nothing outside `dest`; (b) a ZIP exceeding the member-count cap is rejected; (c) a ZIP whose declared/inflated cumulative size exceeds the size cap is rejected (bomb); (d) a benign ZIP still extracts correctly (no false positive). |
| **Regression** | Tar extraction (already hardened, `:188-240`) still works and stays guarded; legitimate `.ogpack` install round-trips. |
| **Integration** | `hachi pack install <crafted.ogpack>` (ZIP-backed) with a `../device-permissions.og` member → command refuses, exit non-zero, `~/.hachi/device-permissions.og` is untouched. |
| **Performance** | The size/count caps bound worst-case extraction time/space (REL-010); a bomb ZIP fails fast, not by exhausting disk. |
| **Security** | **The headline security test**: a zip with a `../` member is rejected (zip-slip) and a zip-bomb is rejected (caps). Assert no file is created outside the resolved `dest` for any crafted member. Tar/zip **parity** asserted: the same malicious member name fails identically on both paths. |
| **UAC** | A pack from the open web cannot write a single byte outside its install dir, regardless of member names or size. (Gate A G-A3.) |

#### P0-5 · SEC-005 — MCP server install/exec tier-gated + explicitly confirmed · *(security)*
| Col | Spec |
|---|---|
| **Unit** | Tests for the boot path (`gateway/orchestrator.py:1239-1260`) + `core/mcp_client.py::setup_from_node`: (a) a config containing a `role=mcp` node does **not** spawn a subprocess at boot when the tier is below the required threshold and no explicit consent is recorded; (b) it spawns **only** when both the tier permits and consent is present; (c) the spawn argument vector is list-form (no `shell=True`) — assert the `Popen` call shape. Patch/spy `subprocess.Popen` to assert it is not called in the gated case. |
| **Regression** | The existing `filesystem_mcp` guard still works; an explicitly-consented, tier-appropriate MCP server still starts; `mcp list` empty-state unaffected. |
| **Integration** | Install a pack carrying a `role=mcp` node via `hachi pack install`, then boot the gateway/agent → **no process is spawned** without an explicit consent step; assert via a `Popen` spy / process check. |
| **Performance** | — |
| **Security** | **The headline security test (SEC-005)**: installing a pack with a `role=mcp` node does **not** spawn a process without explicit consent. Combine with P0-4: the chained exploit (`hachi pack install <evil>` → ZIP escape plants a `role=mcp` node → boot spawns `python -c "<exfil>"`) is blocked at both the extraction gate and the exec gate. |
| **UAC** | No web-sourced MCP command ever executes at boot without a tier check **and** an explicit user confirmation. (Gate A G-A4.) |

#### P0-6 · UX-001 / BB-006 — real API-key validation in the wizard · *(trust/web)*
| Col | Spec |
|---|---|
| **Unit** | *(new `frontend/src/.../SetupWizard.test.tsx`)*, on the §4 foundation: with the network boundary returning **200**, the Step-1 badge shows "validated" only after a real `validateProvider()` call to `POST /config/provider/validate`; with **401/network-error**, the badge shows a failure state and Finish is blocked. Assert the length-only `keyValid = key.trim().length >= 12` heuristic (`SetupWizard.tsx:51`) no longer drives the badge, and that a ≥12-char **garbage** key does **not** show "validated." |
| **Regression** | A genuinely valid key still completes the wizard; `POST /setup/apply` still fires on Finish (`SetupWizard.tsx:73-85`); other wizard steps unaffected. |
| **Integration** | *(gateway smoke, §4)* Wizard Step 1 with a real (test) gateway: a bad key surfaces a real validation failure; a good key proceeds. No "validated" without a 200. |
| **Performance** | — |
| **Security** | A malformed/empty/length-only key is never reported as validated (no false trust signal on the onboarding-critical step). |
| **UAC** | "Key looks good — validated" appears **only** after a real provider validation succeeds; failures are visible at Step 1 with a path to fix. (Gate B G-B2.) |

#### P0-7 · UX-018 / BB-004 — remove silent fixture fallback; honest loading/empty/error/stale states · *(trust/web)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)* For each of the ~10 domain loaders (`work.ts:201-203` pattern, + `memory/og/skills/integrations/config/authoring/approvals/chat/spend`): with the gateway boundary returning an error, the route renders an **error/empty** state — **not** seeded fixtures — and a retry affordance. Assert the rendered DOM contains the error/empty copy and does **not** contain known fixture sentinel values. |
| **Regression** | On a healthy gateway, real data renders; the global `ReconnectingBanner` still appears on a genuine drop; genuine empty states (Approvals/Work/Skills-installed/Integrations) still render their empty copy. |
| **Integration** | *(gateway smoke, §4)* **Boot the SPA with the gateway down** → the UI shows a disconnected/empty state, never a populated healthy-looking dashboard. Then bring the gateway up → data hydrates. This is the single most brand-critical integration assertion. |
| **Performance** | — |
| **Security** | No decision-bearing surface (spend, "connected" integrations, memory facts, approvals) ever shows invented data while disconnected. |
| **UAC** | With the gateway down, every data screen is honestly empty/error/disconnected; there is no path on which a user sees fabricated healthy data. (Gate B G-B1; anchors §6.) |

#### P0-8 · PERF-001 — cache message-history prefix; use all 4 Anthropic cache breakpoints · *(perf/cost/moat)*
| Col | Spec |
|---|---|
| **Unit** | Test `core/llm.py::_build_kwargs` (the `cache_control` placement, `:247-260`): assert `cache_control` markers are placed to cover the **system prefix, the tool list, and the message-history prefix** (today only the system prefix `:249` and the last tool `:258` are marked; `messages` carry none, `loop.py:704` sends history raw). Assert the count/positions of breakpoints match the intended 4 and that a stable history prefix is marked cacheable. |
| **Regression** | A single-turn request still works; tool-group unlock still produces a valid request (note PERF-002: group changes invalidate the prefix — assert the cache markers degrade gracefully, not crash). |
| **Integration** | A scripted 2-turn loop against a recording/stub LLM client: assert the second turn's request carries cache-read breakpoints over the prior history, and that the **billed input tokens on turn 2 drop** vs. the uncached baseline (read the usage the API returns, `llm.py:273-279`). |
| **Performance** | **PERF-001 metric:** assert `cache_control` is present on **all** intended message breakpoints, and that the **token-billed delta on turn 2** (fresh input tokens) is materially lower than turn 1's history size — i.e. the history prefix is cache-read, not re-billed. Target: turn-2 fresh-input tokens ≈ new turn's content only, not the whole transcript. |
| **Security** | — |
| **UAC** | A multi-turn session does not re-bill loaded `.og` skills + tool results at full input price every turn; the `.og` token-saving moat is realized rather than self-defeating. (Gate A G-A7.) |

#### P0-9 · PERF-025 / PERF-026 — virtualize transcript + throttle SSE deltas + memoize items/Markdown · *(perf/web)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)*: (a) `appendTextDelta` (`chat.ts:134-146`) no longer remaps the whole `messages` array per delta (assert referential stability of untouched messages, or that the streaming message is updated without re-creating siblings); (b) `Markdown.tsx` is memoized — `parseBlocks(text)` is not re-run when props are unchanged (assert via a spy/`React.memo`); (c) `MessageItem` (`conversation.tsx:464`) is memoized. |
| **Regression** | Streaming still renders incrementally and correctly; final rendered transcript matches the streamed content; scroll-to-bottom behavior intact. |
| **Integration** | *(component/render test)* Render a transcript and stream deltas; assert no full re-render storm (render-count bound) and visible correctness. |
| **Performance** | **PERF-025 metric:** render **N = 5000 messages** and assert the visible/committed work stays under a frame budget (e.g. only a windowed subset is in the DOM; a streaming delta commits in < ~16 ms of main-thread work in the test harness, or the average render-count stays bounded). Long transcripts must not lock the UI. |
| **Security** | — |
| **UAC** | A long session (thousands of messages) stays interactive while streaming; the primary interaction surface does not freeze. (Gate B contributes to perf; PO Sprint-1 NOW.) |

#### P0-10 · UX-009 — governance status bar (model / session / live-cost / connection) · *(UX/trust chrome)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)*: the status bar renders model, session id, live tokens+cost, and a connection pill on every route (mounted in `__root.tsx`). Assert the connection pill reflects gateway state (connected/disconnected) and the live-cost value is wired to the (corrected, P0-11) spend field. Surfaces the `liveTokens` data currently parsed-then-discarded (`chat.ts:228-242`, UX-N02). |
| **Regression** | Existing routes still render their content under the new chrome; no layout regression on Sidebar+Outlet. |
| **Integration** | *(gateway smoke, §4)* With the gateway up, the pill shows connected and cost ticks during a turn; with it down, the pill shows disconnected (pairs with P0-7). |
| **Performance** | The cost/token readout updates without triggering the PERF-025 whole-array remap (consume a narrow selector, not `s.messages`). |
| **Security** | The connection pill never shows "connected" when the gateway is unreachable (honest chrome; pairs with the no-fabrication rule). |
| **UAC** | On every route the user can see, at a glance: active model, session id, live cost+tokens, and whether the local gateway is connected. (Gate B G-B8.) |

#### P0-11 · UX-N01 / WB-009 — fix web spend double-count (set cumulative, don't add) · *(trust/correctness)*
| Col | Spec |
|---|---|
| **Unit** | *(new `frontend/src/.../chat.test.ts`)*: feed the `usage.updated` handler (`chat.ts:236-239`) a sequence of **cumulative** snapshots `[0.01, 0.03, 0.06]` and assert the store ends at `0.06` (set-to-latest with a per-turn baseline), **not** `0.10` (today's `s.spent + costUsd`). Mirror the CLI's correct delta logic (`cli/main.py:720,750-753`). |
| **Regression** | Across two turns, the per-turn baseline resets correctly so a new turn's spend adds to the session total once; the backend `/spend` endpoint (already correct, `cli/main.py:751-771`) remains the authoritative aggregate. |
| **Integration** | *(gateway smoke, §4)* Run a real streaming turn; assert the SPA's live spend equals the `/spend` endpoint's reported total for that turn (reconciliation), not a super-linear inflation. |
| **Performance** | — |
| **Security** | — |
| **UAC** | The spend number shown during/after a turn matches the provider-accurate cumulative usage exactly once; a skeptical self-hoster reconciling against their provider dashboard sees agreement. (Gate B G-B6; anchors §6.) |

### 2B. P1 — Sprint 2 / BEFORE-WEB

#### P1-1 · BB-N02 / WB-007 / UX-006 — in-context leash → real approval + countdown + decline reason · *(trust/web)* — *PO: lead candidate to pull into P0*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx` / `chat.test.ts`)*: `allowLeashLive()` / `denyLeashLive()` (`chat.ts:395-414`) **POST to `/approvals/{request_id}`** with `{action, rationale}` (mirror the working standalone path `approvals.ts:185-233`). Assert the consumer reads the **real** SSE keys `request_id/operation/tool/target/reason` (drift D-1; today it reads `payload.request/action/why` → all `undefined`). Assert "Always allow" requires a confirm; Decline collects a reason. |
| **Regression** | The standalone `/approvals` screen still POSTs and works; the local stream still aborts on deny. |
| **Integration** | *(gateway smoke, §4)* Trigger a real permission request mid-turn → click Allow in the **in-context** prompt → the backend approval resolves and the turn proceeds; click Deny → it's declined server-side with the captured reason. |
| **Performance** | — |
| **Security** | The headline governance control performs a real server-side decision — assert the gateway received the approval, not just a UI dismissal. |
| **UAC** | The leash the user meets mid-task actually approves/denies on the backend, with a real countdown and a decline reason. (Gate B G-B3.) |

#### P1-2 · BB-N03 — remove fabricated auto-decline countdown on live approvals · *(trust/web)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)*: when `expiresAt`/`totalMs` are absent/0 (no server expiry, `approvals.ts:117-118`), `ApprovalCard` (`approvals.tsx:242-280`) renders **no** countdown bar (no "auto-declines in 0s", no `0/0` NaN%). When a server expiry **is** present, the countdown renders and counts down. |
| **Regression** | Real server-sent expiries still display a correct countdown. |
| **Integration** | *(gateway smoke)* Live approvals with no server timer show no countdown promise. |
| **Performance** | — |
| **Security** | **A safety promise is never displayed without a backing server timer** (anchors §6). |
| **UAC** | No "auto-declines in {time}" is shown unless the server actually enforces that expiry. (Gate B G-B3.) |

#### P1-3 · BB-N04 — wire memory add/edit/delete/approve to `/memory/facts`; survive reload · *(trust/web)*
| Col | Spec |
|---|---|
| **Unit** | *(new `memory.test.ts`/`.tsx`)*: `saveFact`/`delFact`/`approveProposed`/`rejectProposed` (`store.ts`, guided by stale `memory.ts:8-11`) call `POST /memory/facts` and `DELETE /memory/facts/{id}` (which exist: `fastapi_app.py:840-855`). Assert the success toast fires **only after** a successful response; assert no toast on failure. |
| **Regression** | Read/recall still works; blocked/quarantined memory copy unaffected. |
| **Integration** | *(gateway smoke, §4)* Add a fact → **reload** → the fact is still present (the explicit BB-N04 failure: edits vanish on reload today). Delete a fact → reload → gone. |
| **Performance** | — |
| **Security** | A "Hachi remembers it" toast is never shown for a write that didn't persist. |
| **UAC** | Memory edits made in the UI survive a reload and reflect the gateway's vault state. (Gate B G-B4.) |

#### P1-4 · WB-N03 — fix wrong-session memory/skills/og routing · *(correctness/reliability)*
| Col | Spec |
|---|---|
| **Unit** | Tests for the web handlers that today resolve `next(iter(self._agents.values()))` (`cli/main.py:563,571,583,604,851`): `get_skills/get_memory/get_memory_recall/get_og_graph/add_memory_fact/delete_memory_fact` honor a `session_id` param and resolve the **specific** agent (as `get_spend` already does, `:730-733`). |
| **Regression** | Single-session behavior unchanged; `get_spend`'s existing session targeting still works. |
| **Integration** | Boot the gateway with **two** sessions; write a memory fact targeting session A → assert it lands in A's vault and is **not** recalled from B. (The concurrency landmine: facts written to / recalled from the wrong session.) |
| **Performance** | — |
| **Security** | Cross-session leakage is impossible — a fact written for one session is never returned to another. |
| **UAC** | In a multi-session deployment, memory/skills/recall always operate on the caller's session. (Gate B G-B4.) |

#### P1-5 · UX-017 / BB-005 — real `/run` SSE or remove the screen · *(trust/web)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)*: if kept, `/run` consumes `GET /sessions/{id}/stream` (real SSE), not the scripted `_runQueue`/`setInterval(runTick)` (`run.tsx:7-12`, `store.ts:1299-1312`). If removed, assert the route/entry points are gone and "Watch live" does not navigate to a simulation. |
| **Regression** | Work "Watch live" routing still works to the real destination (or is removed cleanly). |
| **Integration** | *(gateway smoke)* "Watch live" shows real streamed activity and a real spend meter, or nothing labeled "live." |
| **Performance** | Reuses the virtualized transcript work (P0-9) if it renders streamed lines. |
| **Security** | No fake cost meter is ever presented as the user's real task spend. |
| **UAC** | Nothing labeled "live" is a simulation. (Gate B G-B5.) |

#### P1-6 · UX-002 / UX-008 — settable+enforced spend cap; provider choice at setup · *(product gap)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)*: Settings cap field is **editable** with a Save that persists (today read-only `$1.00`, `settings.tsx:540`, no Save, sticky `unsaved` badge `:64`); the wizard offers a provider picker incl. Ollama/OpenAI-compatible and a spend-limit step (today hardcoded Anthropic `SetupWizard.tsx:209`, `setup.ts:88-91`). |
| **Regression** | Safety/provider persistence that already works (`config.ts:136-168`) is unaffected; the orphan `unsaved` badge resolves on save. |
| **Integration** | *(gateway smoke + CLI cross-check)* Set a cap in the UI → it persists and equals the `session_cost_cap_usd` the engine enforces (ties to P0-3 and the budget guard). Pick a non-Anthropic provider → it round-trips to config. |
| **Performance** | — |
| **Security** | The cap shown is the cap enforced (no decorative limit); enforcement is asserted, not just persistence. |
| **UAC** | A user can set and enforce a spend cap from the UI and choose their provider at setup. (Gate B G-B6/G-B7.) |

#### P1-7 · SEC-002 — unconditional untrusted-content framing · *(security)*
| Col | Spec |
|---|---|
| **Unit** | `core/untrusted_content.py` (`:42-46`): assert untrusted content is **always** framed; `scan()` is used only to **escalate** severity, never to skip framing. A scanner-evading benign-scored payload is still wrapped. Red on today's `if is_safe: return content, False`. |
| **Regression** | Already-flagged content still framed; non-untrusted content paths unaffected. |
| **Integration** | A crafted "safe-scored" injection reaches the model **framed**; assert the framing wrapper is present in the assembled message. |
| **Performance** | — |
| **Security** | **Scanner-evading injection cannot reach the model unframed** (SEC-002). |
| **UAC** | All untrusted external content is framed regardless of scanner verdict. (Gate B G-B10.) |

#### P1-8 · SEC-003 — SSRF guard on dynamic `http_request` tool · *(security)*
| Col | Spec |
|---|---|
| **Unit** | `core/integrations.py` (`:244-270`): assert the tool calls `validate_outbound_url`/`net_guard` before request; uses a **no-redirect** opener; does **not** resend the auth header across hosts. Red today (raw `urllib.request.urlopen`, follows 3xx, leaks header). |
| **Regression** | Legitimate allowed outbound requests still work. |
| **Integration** | A request to a link-local/metadata address (e.g. `169.254.169.254`) or an internal host is **blocked**; a 3xx to an internal host is not followed. |
| **Performance** | — |
| **Security** | **SSRF blocked**: no internal/metadata reach, no auth-header leak on redirect (SEC-003). |
| **UAC** | The dynamic HTTP tool cannot be steered to internal services or leak credentials. (Gate B G-B10.) |

#### P1-9 · SEC-004 — `browse_web`/Playwright per-hop redirect guard · *(security)*
| Col | Spec |
|---|---|
| **Unit** | `tools/browser_tools.py:49` / `core/browser.py:193`: assert each navigation/redirect hop is revalidated against `net_guard`, not just the initial URL. |
| **Regression** | Normal external browsing still works. |
| **Integration** | A page that 3xx-redirects to an internal host is **blocked at the redirect**, not loaded. |
| **Performance** | — |
| **Security** | **Redirect-based SSRF blocked** (SEC-004). |
| **UAC** | Browser navigation cannot be redirected into internal hosts. (Gate B G-B10.) |

#### P1-10 · SEC-007 — enforce pack signature/hash on install · *(security)*
| Col | Spec |
|---|---|
| **Unit** | `core/community_packs.py::install_pack` (`:299-389`) calls `verify_index_entry_sha256` (`pack_index.py:217-225`); an **empty** hash is **not** treated as valid (today it returns `True` on empty); a mismatched hash is rejected; `--unsafe` is the only opt-out. |
| **Regression** | A correctly-hashed pack installs; the verifier is actually invoked (today it exists but is never called). |
| **Integration** | `hachi pack install` of a tampered pack (hash mismatch) is refused; `--unsafe` explicitly bypasses with a warning. |
| **Performance** | — |
| **Security** | **Supply-chain integrity**: no unsigned/unhashed pack installs silently (SEC-007). Combine with P0-4/P0-5 for the full pack-trust story. |
| **UAC** | Pack installs are integrity-checked by default. (Gate B G-B10.) |

#### P1-11 · SEC-N01 / SEC-013 / SEC-014 — loopback-gate NO_AUTH; auth-enabled-no-token is a hard error · *(security)*
| Col | Spec |
|---|---|
| **Unit** | `gateway/fastapi_app.py` (`:243-250,299-304`): assert `HACHI_GATEWAY_NO_AUTH=1` combined with a **non-loopback** bind **refuses to start** (or refuses to disable auth); assert "auth enabled + falsy token" is a **hard error**, not a silent `return` (closes SEC-014). |
| **Regression** | Loopback dev with NO_AUTH still works; normal token auth unaffected; constant-time compare intact (`:307`). |
| **Integration** | Start the gateway with NO_AUTH on `0.0.0.0` → it refuses; the now-numerous mutation routes (`PUT /capabilities/{id}/enabled`, `POST/DELETE /memory/facts`, `PUT /config/*`, `POST /setup/apply`) are never exposed unauthenticated on a reachable interface. |
| **Performance** | — |
| **Security** | **The kill-switch can't silently expose config/memory/capability writes off-loopback** (SEC-N01). |
| **UAC** | No configuration accidentally serves write routes without auth on a network-reachable interface. (Gate B G-B10.) |

#### P1-12 · REL-001 / REL-N03 — approval wait off the event loop; cap pending approvals · *(reliability)*
| Col | Spec |
|---|---|
| **Unit** | `core/approvals.py` (`:309-357,89-98`): assert the 180s wait does not park an anyio worker thread (async wait or fail-fast to pending), and that `create` caps concurrent pending approvals. |
| **Regression** | A normal single approval still resolves; timeout still defaults sanely. |
| **Integration** | Simulate an approval flood while hitting `/health` and an SSE stream concurrently → `/health` stays responsive (no thread-pool exhaustion); the streaming facade path (REL-N03, `fastapi_app.py:629-637`) is covered too. |
| **Performance** | **Concurrency metric:** under K concurrent approval-triggering turns, `/health` p99 stays within budget and worker threads are not all parked. |
| **Security** | — |
| **UAC** | An approval flood cannot hang the gateway for other clients. (Gate B G-B11.) |

#### P1-13 · PERF-N01 / PERF-011 / REL-N01 — offload blocking work out of `async def` read handlers · *(perf/reliability)*
| Col | Spec |
|---|---|
| **Unit** | For each handler (`get_skills:822`, `get_memory:827`, `get_memory_recall:832`, `get_og_graph:857`, `get_authoring_targets:862`, `get_spend:882`, `get_capabilities:386`): assert it either is a plain `def` (Starlette threadpool) or wraps the control-plane call in `anyio.to_thread.run_sync` (match `create_session:398`). A unit can assert the handler doesn't call the blocking method directly on the loop (spy/inspect). |
| **Regression** | Each endpoint returns the same payload; happy-path tests in `test_gateway_web_endpoints.py` still pass. |
| **Integration** | Hit a slow `/memory`/`/memory/recall` (cold Chroma) while an SSE keepalive runs → the keepalive and `/health` are not stalled. |
| **Performance** | **Event-loop metric:** a slow read handler does not block concurrent requests; measure that `/health` latency during a heavy `/og/graph` stays bounded. |
| **Security** | — |
| **UAC** | A single heavy read no longer freezes the whole gateway. (Gate B G-B11.) |

#### P1-14 · BB-007 / BB-008 / BB-009 / BB-011 — accessibility core · *(accessibility)*
| Col | Spec |
|---|---|
| **Unit** | *(new `*.test.tsx`)*: clickable `<div>`/`<g>` rows become `button`/`role+tabIndex+keydown` and are keyboard-operable (`work.tsx:1030-1044`, `og.tsx:191-198`, `integrations.tsx` AddMcpRow); modals get `role="dialog"`+`aria-modal`+focus-trap+Esc (SetupWizard, KennelConsent, Work detail; today only `LeashPrompt` has `role="dialog"`); icon-only buttons get `aria-label` (`memory.tsx:120,123`, Work close, send/stop). Focus-ring foundation already landed (`base.css:38-42`). |
| **Regression** | Mouse interaction still works; existing labeled buttons unaffected. |
| **Integration** | Keyboard-only walkthrough of a primary journey (open a row, operate a modal, send a message) without a mouse. |
| **Performance** | — |
| **Security** | — |
| **a11y** | **`axe` (jest-axe / @axe-core/playwright) run on shipped screens has no critical violations** (Gate B G-B9); AA contrast fixed (`colors.css:12` muted-on-light, hardcoded amber pill). |
| **UAC** | Every interactive element is keyboard-operable and screen-reader-named; no critical `axe` violations on shipped screens. (Gate B G-B9.) |

#### P1-15 · WB-013 / REL-N04 — stop fake-uuid on memory write failure (fts5 provider) · *(reliability/data integrity)*
| Col | Spec |
|---|---|
| **Unit** | `core/memory_provider.py::Fts5OnlyMemoryProvider.upsert` (`:230-239`): on a write failure it **raises or returns a sentinel**, never `str(uuid.uuid4())`. Red today (`except Exception: pass; return uuid4()`). The default vault path already fails loudly (`memory_vault.py:239-265`) — keep that as the positive control. |
| **Regression** | A successful write still returns the real id; the new `/memory/facts` handler (already correct, `cli/main.py:858-861`) unaffected. |
| **Integration** | With `memory.backend = "fts5_only"`, force a write failure → the caller observes failure, not a fake success id. |
| **Performance** | — |
| **Security** | No silent data loss masked by a fabricated id. |
| **UAC** | A failed memory write is never reported as success. (Gate B G-B-class; PO BEFORE-WEB.) |

#### P1-16 · WB-004 / WB-002 / WB-003 / WB-005 / SEC-011 / SEC-012 — distribution + supply-chain hygiene · *(CI/CD/infra)*
| Col | Spec |
|---|---|
| **Unit** | n/a (infra). Assert `docker-compose.yml:21-24` no longer references a non-existent `frontend/Dockerfile` (either add the file or remove the service); Dockerfile declares a non-root `USER` and a configurable host (`Dockerfile`, today root + `0.0.0.0`); Python version aligned (Dockerfile `3.12` vs CI `3.11`). |
| **Regression** | `docker compose` builds/starts; existing backend image still runs. |
| **Integration** | Build the image in CI; run a container as non-root that answers `/health`; `pip-audit`/`uv pip audit` + `npm audit` + `bandit` run as CI steps. |
| **Performance** | — |
| **Security** | **`pip-audit` (or equivalent) is clean — no high CVEs in shipped deps (SEC-011 / Gate A G-A5)**; container runs non-root; SAST runs. |
| **UAC** | Image builds, runs non-root on a configurable host; CI fails on a known high CVE or a SAST finding. (Gate A G-A5/G-A6, Gate B G-B-class.) |

#### P1-17 · PERF-019 / PERF-015 / PERF-012 / PERF-022 — embedding pre-warm, store concurrency, `/memory` pagination, CLI imports · *(perf)*
| Col | Spec |
|---|---|
| **Unit** | (PERF-015) `core/session_store.py` uses thread-local connections, not one global lock (`:49-51`) — assert concurrent reads aren't serialized. (PERF-012) `snapshot_memory_facts`/`/memory` paginate and bound `coll.get()` (`orchestrator.py:741-760`, today no limit). (PERF-019) Chroma embedding is pinned/pre-warmed (`memory_vault.py:121-148`, today default ONNX, 0 `embedding_function` refs). (PERF-022) assert `import hachi.cli.main` does **not** eagerly import `anthropic`/`uvicorn` (defer them). |
| **Regression** | Recall/search still returns correct results; CLI commands still work after lazy-import. |
| **Integration** | `--help` cold-start time drops (PERF-022: ~3s of the ~4.6s is avoidable); `/memory` on a large vault returns a bounded page. |
| **Performance** | **Metrics:** `/memory` response is paginated (bounded N); concurrent SessionStore reads scale; `import hachi.cli.main` excludes anthropic/uvicorn (assert via `sys.modules` diff, the method the perf agent used); embedding cold-start is pre-warmed/pinned (no hidden network download at first use). |
| **Security** | — |
| **UAC** | Multi-session, large-vault, many-skill usage is responsive; CLI starts fast. (Gate A G-A-perf / Gate B G-B11 / Gate C.) |

---

## 3. Reproduction harness specifics (this repo, Windows)

`.exe` shims are blocked in this environment (os err 4551 — see Memory: Windows test runner workaround). Prefer module invocation. `.env` is **not** auto-loaded — pass keys explicitly or set the env var in-shell.

**Tests / type-check (PowerShell or bash):**
```bash
# Full suite
PYTHONPATH=src python -m pytest -q
# Single file / test
PYTHONPATH=src python -m pytest tests/test_config_get_set.py -vv
PYTHONPATH=src python -m pytest tests/test_config_get_set.py::TestConfigGet::test_no_key_lists_all -vv
# If the editable-install lock breaks pytest (Windows), the no-project form:
PYTHONPATH=src python -m pytest -q   # (uv run --with pytest --no-project pytest -q  if uv is healthy)
# Type-check
PYTHONPATH=src python -m mypy src --ignore-missing-imports
PYTHONPATH=src python -m mypy --config-file mypy-strict-gate.ini
# Lint
python -m ruff check src tests
```

**Running the CLI (two equivalent forms; CI uses the first):**
```bash
PYTHONPATH=src python -m hachi.cli.main <cmd>
PYTHONPATH=src python -c "from hachi.cli.main import app; app()" -- <args>
```

**The config crash — exact repro (P0-3 / BB-001 / WB-N06):**
```bash
PYTHONPATH=src python -m hachi.cli.main config-get
# Expected (pre-fix): AttributeError: 'ResolvedConfig' object has no attribute 'safety_tier'
#   raised eagerly at cli/main.py:2209 — crashes even for `config-get provider.model`.
PYTHONPATH=src python -m hachi.cli.main config-set safety_tier guided
# Expected (pre-fix): Invalid value for safety_tier: "ResolvedConfig" object has no field "safety_tier"
```
Real attribute paths the fix must target (from `src/hachi/core/settings.py:85-97`):
`safety_tier → cfg.permissions.tier` (`:47`); `max_tokens → cfg.agent.max_tokens` (`:26`); `session_cost_cap_usd` → **does not exist; must be modeled** (e.g. add to `AgentSettings` or a new `BudgetSettings`). Positive control that already reads the data correctly: `PYTHONPATH=src python -m hachi.cli.main config-validate`.

**OpenAPI contract loop (P0-1 / WB-001):**
```bash
PYTHONPATH=src python -m hachi.cli.main gateway-openapi --output openapi/hachi-openapi.json
npm install --prefix frontend
npm run --prefix frontend check:api      # generate schema.d.ts + git diff --exit-code
npm run --prefix frontend typecheck
```

**Gateway up for integration (P1 routes / smoke):**
```bash
PYTHONPATH=src python -m hachi.cli.main gateway --host 127.0.0.1 --port 8787 --runtime
# then drive routes with the bearer token printed on first run (~/.hachi/gateway-token).
```

**Security audit / skill-lint (parity with CI jobs):**
```bash
PYTHONPATH=src python -m hachi.cli.main security-audit --json --fail-on-high
PYTHONPATH=src python scripts/lint_skills.py
```

---

## 4. Frontend test foundation (it has none today)

**Current state (confirmed first-hand):** no `vitest`, `@testing-library/*`, `jsdom`, `msw`, or `playwright` in `frontend/package.json`; zero `*.test.*` files under `frontend/src`. So every web "done" claim is currently verified by code-reading only.

**Add (devDependencies + scripts):**
- **vitest** + **@testing-library/react** + **@testing-library/user-event** + **jsdom** (or happy-dom) — unit/component tests with a real DOM.
- **msw** (Mock Service Worker) **or** a thin typed gateway mock built from `openapi/hachi-openapi.json` — the network boundary. Prefer msw so handlers return real-shaped payloads and error/empty responses can be simulated per-test.
- **jest-axe** (or `@axe-core/playwright`) — the a11y assertion engine for P1-14 / Gate B G-B9.
- **@playwright/test** (optional but recommended) — for the one real integration smoke that boots the gateway.
- Scripts: `"test": "vitest run"`, `"test:watch": "vitest"`, `"test:smoke": "playwright test"` (or a vitest integration project). Wire `test` into the `frontend-contract` CI job (or a new `frontend-test` job) — Gate B G-B12.

**One real integration smoke (non-negotiable):** boot the **actual** gateway (`hachi gateway --host 127.0.0.1 --port 8787 --runtime` with a token), point the SPA at it, and assert the SPA reads **live** data — not fixtures. The single most important assertion: **with the gateway down, no screen shows fabricated healthy data; with it up, real data hydrates.** This is the structural cure for UX-018/WB-001-class "looks done but isn't."

**Cover these screens first (trust-critical, in priority order):**
1. **SetupWizard validation** (P0-6 / UX-001) — bad key never shows "validated"; real `validateProvider` call.
2. **Fixture-fallback states** (P0-7 / UX-018) — gateway-down renders error/empty, not seeded fixtures, on every domain loader.
3. **Spend math** (P0-11 / UX-N01) — cumulative `usage.updated` counted once; reconciles with `/spend`.
4. **In-context leash POST** (P1-1 / BB-N02) — allow/deny actually POST `/approvals/{request_id}` with the real keys; countdown only with a server expiry (P1-2 / BB-N03).
5. **Memory persistence** (P1-3 / BB-N04) — add/edit/delete call `/memory/facts` and survive reload.
6. Then: status bar (P0-10), `/run` real-or-removed (P1-5), spend cap settable (P1-6), accessibility/axe (P1-14).

---

## 5. CI gates (must be green and blocking after the fixes)

Tie to [09 — Release Readiness](09-release-readiness.md) Gate A (engine/CLI) and Gate B (web). After Sprint 1, the following must be **green and blocking** (no `continue-on-error`):

| Gate | Job / check | Blocking requirement | Tied to |
|---|---|---|---|
| A G-A0 | `test` | Full `pytest` green on Linux + macOS, incl. the **real** config-command test (P0-2/3) | WB-N07, BB-001/002 |
| A G-A0 | `lint` (ruff + mypy) | **Blocking on `main`** — remove `continue-on-error: true` (`ci.yml:80,84`); strict gate stays blocking on PRs | WB-002 |
| A G-A0 | `frontend-contract` | Real `check:api` (schema regen + `git diff --exit-code`) + `typecheck`; **a contract-drifting PR goes red** (proven via the negative-control PR in P0-1) | WB-001 |
| A G-A3/4 | `test` | Pack zip-slip/bomb + MCP-consent tests present and green | SEC-001, SEC-005 |
| A G-A5 | `security` (new steps) | `pip-audit`/`uv pip audit` (+ `npm audit`, `bandit`) — **no known high CVEs / SAST findings** | SEC-011 |
| A G-A7 | `test` | PERF-001 cache-breakpoint test green | PERF-001 |
| B G-B9 | `frontend-test` (new) | `axe` a11y check on shipped screens — **no critical violations** | BB-007/008/009/011 |
| B G-B12 | `frontend-test` (new) | vitest component tests **+ one gateway-integration smoke** green | §4, UX-018/001/N01/leash/memory |

**Notes:** the `security` job today runs only Hachi's own auditor (`ci.yml:53-58`) — add real dependency-CVE + SAST steps (SEC-011). The `lint` job's `ruff`/`mypy` steps are `continue-on-error: true` on `main` (`:80,84`) — make them blocking (WB-002). Keep `skill-lint` (`:107-108`) and `benchmark` (`:127-128`) as-is (already blocking/meaningful).

---

## 6. Regression suite for the trust fakes (dedicated group)

A standing suite — `tests/test_trust_no_fabrication.py` (backend reconciliations) + `frontend/src/.../trust.test.tsx` (UI) — whose sole job is to assert **no surface renders fabricated data.** This is the net that stops a future "looks fixed" fake from returning. Every assertion maps to a finding and a Gate-B item:

| # | Assertion | Anchored finding | Where |
|---|---|---|---|
| T-1 | **Gateway-down → error/empty state, not fixtures.** With the network boundary erroring, no domain route renders seeded fixture sentinels; an error/empty + retry state is shown. | UX-018 / BB-004 | FE component + smoke (§4) |
| T-2 | **Invalid key → no "validated."** A length-only/garbage key never produces the "validated" badge; only a real 200 does. | UX-001 / BB-006 | FE `SetupWizard.test.tsx` |
| T-3 | **Spend reflects cumulative usage exactly once.** Cumulative snapshots `[0.01,0.03,0.06]` → store shows `0.06`, not `0.10`; reconciles with `/spend`. | UX-N01 / WB-009 | FE `chat.test.ts` + smoke |
| T-4 | **Memory edit survives reload.** A fact added in the UI is still present after reload (it actually POSTed). | BB-N04 | FE `memory` smoke |
| T-5 | **No countdown without a server expiry.** With `expiresAt/totalMs` absent, no "auto-declines in {time}" bar renders. | BB-N03 | FE `approvals.test.tsx` |
| T-6 | **In-context leash performs a real decision.** Allow/Deny POST `/approvals/{request_id}`; a UI dismissal alone is not "approved." | BB-N02 / WB-007 | FE + gateway smoke |
| T-7 | **No fabricated success toasts.** Connect-integration, routine-create, "watch live", folder/command-add, authoring-apply do not toast success without a backing gateway action (or are honestly disabled until wired). | BB-N05/N06/N07, UX-017, WB-N04 | FE + handler tests |
| T-8 | **No fake id on write failure.** `Fts5OnlyMemoryProvider.upsert` never returns a synthesized uuid on failure. | WB-013 / REL-N04 | BE `tests/` |
| T-9 | **Connection pill honesty.** The status bar never shows "connected" when the gateway is unreachable. | UX-009 (chrome) | FE smoke |

**Design rule for this suite:** each test must **fail on the current (faking) code** — it is, by construction, a red-before/green-after set. If any T-row passes against today's tree, the test is wrong (it's mocking the fake), not the product.

---

## 7. Summary

- **11 P0 fixes** (Master §6) each have a full matrix row (§2A: P0-1…P0-11).
- **17 P1 / BEFORE-WEB fixes** each have a full matrix row (§2B: P1-1…P1-17), covering the leash, faked memory, wrong-session routing, fabricated countdown, simulated `/run`, spend-cap/provider, the four security items + NO_AUTH gate, approval-wait reliability, blocking-handler offload, accessibility, fts5 fake-id, distribution/CVE hygiene, and the perf hotspots.
- **= 28 fixes with a complete test/regression/integration/perf/security/UAC matrix row**, plus a 9-assertion trust-fabrication regression suite (§6) and an 8-row CI gate table (§5).
- **Frontend test foundation:** add **vitest + @testing-library/react + jsdom + msw (network boundary) + jest-axe**, plus **one Playwright/vitest integration smoke that boots the real gateway** and asserts the SPA reads live data (gateway-down → honest empty, never fixtures). Cover trust-critical screens first: SetupWizard validation, fixture-fallback, spend math, in-context leash POST, memory persistence. Wire it as a blocking `frontend-test` CI job (Gate B G-B12).

*Back to [00 — Master Report](00-MASTER-REPORT.md) · forward to [09 — Release Readiness](09-release-readiness.md).*
