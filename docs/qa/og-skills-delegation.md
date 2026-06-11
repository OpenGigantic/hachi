# QA Report — ObjectGraph (.og) Engine, Skills, Delegation, Workflow & Presentation Tools

Date: 2026-06-11
Environment: Windows 11, Python 3.14.3, `./.venv/Scripts/python.exe` + `PYTHONPATH=src`, tier=unrestricted.
Scope: `src/tokoboros/og_engine/*`, `tools/og_tools.py`, `tools/delegation_tools.py`, `tools/workflow_tools.py`, `tools/presentation_tools.py`, `ObjectGraph_Complete_Specification.md`.

## Summary

The OG engine loads cleanly (43 nodes, matching `doctor`), the 3-pass model (search → peek → load) works, delegation is fully functional end-to-end (role resolution, isolated child loop, delegation_log persistence), workflow suspend works, and presentation generation both degrades gracefully when `python-pptx` is absent and builds valid decks when present.

However there is a **real, shipped data-corruption bug** in the parser: the `BLOCK_PATTERN` regex prefix-matches block headers without a word boundary, so the plural header `::assertions` (used by 17 of the shipped `.og` skill files) is parsed as `::assertion` + a leftover `s`, injecting a bogus assertion literally named `"s"` into every affected skill. This surfaces to the LLM in `load_skill` ("MANDATORY ASSERTIONS: - s") and to users in `skill assert` ("MANUAL  s … verify it yourself"). Lint does not catch it. A second Windows-only crash exists in `skill show` (UnicodeEncodeError on `≈`).

Severity counts: High 1, Medium 2, Low 2, Nit 1.

## Findings

### [High] `::assertions` (and other plural block headers) corrupt block content with a leftover `s` — affects 17 shipped skills
- **Where:** `src/tokoboros/og_engine/parser.py:301` (`BLOCK_PATTERN`), consumed in `_parse_node_body` at `parser.py:584` (`block_type == "assertion"`). Bug ships from `src/tokoboros/default_skills/` (17 files with `::assertions`).
- **Repro (live-verified):**
  ```
  PYTHONPATH=src python -c "from tokoboros.og_engine.parser import parse_og_file; \
    print(parse_og_file(r'C:\Users\opsin\AppData\Local\tokoboros\skills\general\currency.og').nodes[0].assertions)"
  # -> ['s', 'assert base != target "Base and target currencies must be different."']
  ```
  Source `currency.og` has exactly ONE assertion line (`assert base != target "..."`) under a `::assertions` header. The regex matches `::assertion` (11 chars) inside `::assertions`; block content extraction starts at `match.end()`, so the leftover `s\n` becomes the first "assertion".
  - Isolated proof: singular `::assertion` → `['assert x > 0 "..."]` (clean); plural `::assertions` → `['s', 'assert x > 0 "..."]`.
  - The defect is a regex prefix-match without a trailing word boundary. **Every** longer header that has a shorter alternation member as a prefix leaks a char: `::assertions`, `::notes`, `::examples`, `::tables`, `::lists`, `::configs`, `::tools`, `::references`, `::warnings`, `::codes` all leak `s`.
  - Downstream impact verified: `evaluate_assertions(node.assertions, ...)` reports the bogus `s` as a `manual` assertion; `load_skill` Pass-3 renders `- s` under "MANDATORY ASSERTIONS (run these before proceeding)"; CLI `skill assert core_currency` prints `MANUAL  s  (Not an auto-checkable 'assert <expr> "message"' form — verify it yourself.)`.
- **Scope of impact (live):** 17 of 23 skill nodes carry a bogus `s` assertion: core_currency, core_dictionary, core_finance, core_flight_radar, core_math_engine, core_network, core_qr_creator, core_translator, core_weather, core_wiki, notion_brain_dump, project_kickoff, weekly_review, daily_standup, dnd_coordinator, urgent_escalator, core_github.
- **Expected:** `::assertions` (or any block header) yields exactly the lines the author wrote; no synthetic `s` entry.
- **Actual:** A junk assertion `"s"` is injected, polluting the agent's context, the assertion evaluator, and the user-facing CLI. The LLM is told to "run these before proceeding" / "verify it yourself" for a meaningless `s`.
- **Impact:** Wrong/misleading skill metadata across most shipped skills; wasted tokens; potential agent confusion mid-task. It is a correctness defect in the core differentiator (the .og 3-pass model).
- **Suggested fix:** Add a word boundary to the block alternation, e.g. `r"^::(dense|assertion|steps|...|schema-def)\b(?:\[(.+?)\])?"`, AND decide on a canonical spelling: the spec (`ObjectGraph_Complete_Specification.md`) and the serializer (`serializer.py:101`) both use singular `::assertion`, but 17 shipped files use plural `::assertions`. Either accept `assertions?` in the regex or normalize the shipped files to singular. Note: `save_skill`/`upsert_skill` already writes singular and round-trips cleanly (verified), so the writer and the shipped data disagree.

### [Medium] `skill show` crashes with UnicodeEncodeError on Windows (cp1252 console)
- **Where:** `src/tokoboros/cli/main.py:2742` — `console.print(f"  [dim]full load ≈ {result['full_load_tokens']} tokens …")` (the `≈`, U+2248).
- **Repro (live-verified):**
  ```
  PYTHONPATH=src python -c "from tokoboros.cli.main import app; app()" skill show core_currency
  # -> prints header + blocks, then:
  # UnicodeEncodeError: 'charmap' codec can't encode character '≈' in position 10: character maps to <undefined>
  ```
  Default `sys.stdout.encoding` on this box is `cp1252`. Setting `PYTHONIOENCODING=utf-8` makes the command succeed, confirming it is an encoding issue, not a missing key.
- **Expected:** `skill show <name>` prints the Pass-2 view and exits 0 on a stock Windows console.
- **Actual:** Partial output then a Rich traceback + non-zero exit; the command is effectively broken for Windows users in the default terminal. (The header line's `·`/`�` bullet is also mangled but does not crash.)
- **Impact:** A documented CLI subcommand is unusable on default Windows terminals.
- **Suggested fix:** Force a UTF-8-capable Rich `Console` (e.g. construct `Console(...)` with a UTF-8 file / `errors="replace"`) or reconfigure stdout to UTF-8 at CLI startup, or replace `≈`/`·` with ASCII (`~`, `-`). A UTF-8 console fix would also clean up the mangled bullets seen in `skill list`/`skill search`.

### [Medium] Lint does not flag corrupted / malformed assertions
- **Where:** `src/tokoboros/og_engine/engine.py` lint (`lint_og_file` / `_lint_result`), surfaced via `tokoboros skill lint`.
- **Repro (live-verified):** `skill lint <skills dir> --json` over 30 files reports 0 errors / 86 warnings; categories: `dense-over-budget` (29), `no-keywords` (27), `missing-index` (25), `missing-dense` (5), `empty-node` (3, info). No issue references the bogus `s` assertion in the 17 affected files. Matches `doctor` ("30 file(s), 0 error(s), 86 warning(s)").
- **Expected:** A linter that "gates community skill packs in CI" should flag a one-character / non-`assert …` assertion line (a clear malformation) at least as a warning.
- **Actual:** The corruption passes lint silently, so CI would never catch the High finding above.
- **Impact:** The safety net that is supposed to keep skills correct misses a real correctness defect.
- **Suggested fix:** Add an `assertion-malformed` lint rule (e.g. flag assertion lines that are < N chars or don't start with `assert`). Once the parser regex is fixed this rule still guards against authoring mistakes.

### [Low] Most `no-keywords` / `missing-index` lint warnings are benign noise (persona/rule nodes)
- **Where:** `skill lint` output; brain.og + role .og files.
- **Repro (live-verified):** Of the 27 `no-keywords` warnings, ALL are on `role=persona`/`role=rule` nodes (e.g. `identity`, `delegation-framework`, `skill-routing`) and role personas — which are loaded directly into prompts, not surfaced via `search_skills`. Every `role=skill` node (23/23) HAS keywords and is searchable.
- **Expected:** Warnings should be actionable; non-searchable node types shouldn't be told they "won't surface in search_skills".
- **Actual:** ~52 of 86 warnings (no-keywords + missing-index) are effectively false positives for persona/rule nodes, diluting the signal of the genuinely useful `dense-over-budget` warnings.
- **Impact:** Low — cosmetic / signal-to-noise; could mask real issues in a large pack.
- **Suggested fix:** Suppress `no-keywords`/`missing-index` for `role in {persona, rule, native_integration}` (or downgrade to info), since those are never search targets.

### [Low] Malformed `.og` files are silently dropped from the library
- **Where:** `src/tokoboros/og_engine/engine.py:341-343` (`load_file` catch-all).
- **Repro (live-verified):** A file with an invalid `::api` JSON schema raises `ValueError` in the parser; `load_file` catches it and logs `WARNING: Failed to parse …`, so a good sibling file still loads but the bad skill simply does not exist. No surfaced error to the user beyond a log line.
- **Expected:** Resilience is good (one bad file must not crash boot — verified it doesn't), but a silently-missing skill is hard to diagnose.
- **Actual:** Skill vanishes with only a log warning; `list_skills`/`search_skills` show no trace and no "N files failed to load" signal.
- **Impact:** Low — debuggability. A user whose skill has a typo gets no feedback in the TUI/CLI.
- **Suggested fix:** Track parse failures on the engine and expose a count (e.g. in `doctor`/`skill lint` summary: "2 file(s) failed to parse").

### [Nit] `generate_presentation` accepts out-of-enum `theme` values silently
- **Where:** `src/tokoboros/tools/presentation_tools.py` (`_build_pptx` → `_THEMES.get(theme_name, _THEMES["default"])`).
- **Repro (live-verified, with python-pptx temporarily installed):** `theme="bogus_theme"` builds a deck using the default palette and returns success, even though the schema declares `enum: [default, dark, light, professional]`.
- **Expected/Actual:** Minor — silently falling back to default is arguably fine; just noting the schema enum is advisory and unvalidated.
- **Impact:** Negligible.
- **Suggested fix:** Optionally validate `theme` against `_THEMES` and note the fallback in the return string.

## What passed

- **OG engine load (live-verified):** `SkillEngine().load_directory(~/AppData/Local/tokoboros/skills)` → `node_count == 43` (matches doctor). `get_nodes_by_role`: skill=23, rule=16, native_integration=3, persona=1.
- **3-pass model (live-verified):** Pass 1 `search("currency convert")` → routes to `core_currency`; Pass 2 `peek(["core_currency"])` → dense + block manifest (no heavy content); Pass 3 `resolve_node("core_currency")` → full steps/assertions. `::index` routing tables populated (e.g. brain.og 7 nodes / 7 index entries).
- **device-permissions.og + brain.og parsing (live-verified):** `~/.tokoboros/device-permissions.og` (4 nodes), `~/.tokoboros/brain.og` (7 nodes), `~/AppData/Local/tokoboros/skills/brain.og` (7 nodes) all parse without crash. No spec-violation crashes.
- **`with_role` scoping (live-verified):** `engine.with_role("coder")` hides orchestrator-scoped nodes (43 → 37), the mechanism sub-agents rely on.
- **Delegation, full loop (live-verified, claude-haiku-4-5):**
  - `list_roles` → `["analyst","coder","devops","emailer","researcher"]`; all 5 resolve to `~/.tokoboros/skills/roles/<role>.og`. (Note: roles live at `roles/<role>.og`, NOT `brain-<role>.og` — the task's hint was outdated; resolution works.)
  - `delegate_task(role="researcher", task="…reply PONG…")` → isolated child `ExecutionLoop` ran (1 step, ~1s) and returned a structured `[Sub-Agent Report]` with `Status: success / Result: PONG`. `delegation_start`/`thinking`/`text_chunk`/`delegation_complete` events all fired.
  - delegation_log persistence: with a real `MemoryVault`, the result was written and retrieved via `vault.search_delegations("researcher")`.
  - Missing role (`role="wizard_of_oz"`) degrades gracefully → `Status: error` report listing all 4 searched paths; no crash.
- **`save_skill`/`upsert_skill` round-trip (live-verified):** writes singular `::assertion`, reloads with clean assertions (no bogus `s`).
- **Resilient load (live-verified):** a malformed `.og` (bad `::api` JSON) is skipped while a good sibling loads.
- **Workflow tools (live-verified):** `register_workflow_tools` mounts `core_suspend_workflow`; calling it raises `SuspendWorkflow(reason=…)` as designed.
- **Presentation tools (live-verified, both paths):**
  - Degradation: with python-pptx absent, `generate_presentation(...)` returns the friendly message "python-pptx is not installed. Run `uv sync --extra presentations` …" — no ImportError.
  - Build: with python-pptx installed, a 1-content-slide deck produced a valid 2-slide `.pptx` (~34 KB, reopened with `Presentation()`); all 4 themes render; empty `slides=[]` returns "Error: at least one slide is required." (Environment restored afterward — pptx uninstalled.)
- **CLI `skill` subcommands (live-verified):** `list`, `search`, `deps`, `changelog`, `traverse`, `lint`, `assert` all run. (`show` is the lone crasher — see Medium finding.)
