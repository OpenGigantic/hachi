# QA Report — Infra Tooling (Browser / Sandbox / Scheduler / Daemon)

Date: 2026-06-11
Environment: Windows 11, `./.venv/Scripts/python.exe` (Python 3.14.3), `PYTHONPATH=src`, tier `unrestricted`.
Method: live execution against the real modules unless marked *code-reading*. Playwright is **not installed** in this env; `uv` **is** on PATH (`C:\Users\opsin\.local\bin\uv.EXE`).

## Summary

Counts: **High 1, Medium 3, Low 2, Nit 1** (no Critical).

Headline issues:
- **High** — A missing `uv` aborts the *entire* tool-registration boot, not just the sandbox tool, because `SandboxExecutor()` is constructed eagerly at registration time (raises `OSError` before the tool's own graceful `except OSError` can run). Live-verified the cascade: only `os` registers, `schedule`/`memory`/etc. never mount.
- **Medium** — `tokoboros schedule list` silently drops the kind and enabled/disabled state from its output because the Rich markup `[{kind}, {state}]` is parsed as a style tag. You cannot tell from `list` whether a schedule is paused.
- **Medium** — `tokoboros schedule show <prefix>` advertises "ID (or unique prefix)" but only does an exact dict lookup; a valid unique prefix returns "Schedule not found".
- **Medium** — Windows daemon install builds an unquoted `schtasks /tr` command; any space in the Python path (e.g. `C:\Program Files\...`, `C:\Users\First Last\...`) yields a broken scheduled task.
- **Low** — No real dry-run mode for the daemon; `--help` and `status` are fine but there is no user-facing way to preview the generated service definition without registering.

What worked end-to-end: **Browser** graceful degradation + SSRF guard (excellent), **Sandbox** exec/isolation/dep-validation/tier-gating, **Scheduler** create/list/cancel/persist/fire-advance/cron-correctness/validation. **Daemon** inspected only (no registration); help + status behave, service-definition builders produce correct macOS/Linux output.

---

## Findings

### [High] Missing `uv` aborts the whole tool-registration boot (not just the sandbox tool)
**Where:** `src/tokoboros/tools/sandbox_tools.py:15` (`executor = SandboxExecutor()`) → `src/tokoboros/core/sandbox.py:72` (`self._uv_path = self._resolve_uv()`, raises `OSError`); driven by `src/tokoboros/gateway/tool_registration.py:76-78` (`spec.register_fn(deps)` with no per-spec try/except).
**Repro (live-verified):**
```python
# uv hidden via mock; run register_sandbox_tools through ToolRegistrar.install
with mock.patch("shutil.which", return_value=None), mock.patch.object(sb.Path, "exists", return_value=False):
    ToolRegistrar().install([os_spec, sandbox_spec, schedule_spec, memory_spec], deps)
# => BOOT ABORTED: OSError -> CRITICAL: The 'uv' package manager was not found ...
#    Tools registered BEFORE crash: ['os']   (schedule, memory never registered)
```
Standalone confirmation: `register_sandbox_tools(ToolRegistry(), None)` with uv hidden raises `OSError` at *registration* time.
**Expected:** A missing `uv` should degrade only `python_sandbox` (the tool already has `except OSError: return str(e)` for exactly this), leaving every other tool — and agent boot — intact.
**Actual:** `SandboxExecutor()` is built eagerly when the tool is *registered*, so `_resolve_uv()` raises before the tool function (and its `except OSError`) is ever reachable. `ToolRegistrar.install` does not isolate per-spec failures, so the orchestrator boot fails and all tools ordered after `sandbox` never register.
**Impact:** On any host without `uv`, the agent fails to start (or starts crippled, depending on call site) instead of just lacking one optional tool. Contradicts the repo's lazy-resolution guardrail and makes the tool's own `except OSError` dead code.
**Suggested fix:** Defer executor construction to first call (build `SandboxExecutor()` lazily inside `run_python_sandbox`, after the gate check), or wrap each `spec.register_fn(deps)` in `tool_registration.install` with a try/except that logs and continues. The browser tool already models the correct lazy pattern.

### [Medium] `schedule list` hides kind and enabled/disabled state (Rich markup swallows `[kind, state]`)
**Where:** `src/tokoboros/cli/main.py:2148-2149` — `f"[bold]{s.name}[/bold] [{s.kind.value}, {state}] id={s.schedule_id}\n..."`.
**Repro (live-verified):**
```
$ tokoboros schedule list
qa-int  id=2f393a4c-...
  task: ping
  next_run: ...  runs: 0
```
The intended `[interval, enabled]` segment is absent. Direct Rich repro: `Console(record=True).print("[bold]qa-int[/bold] [interval, enabled] id=abc")` exports to `'qa-int  id=abc\n'` — the bracketed text is consumed as a style tag. A *paused* schedule renders identically to an enabled one in `list`.
**Expected:** Output shows the kind and whether the schedule is enabled/paused.
**Actual:** Both are invisible; user cannot distinguish paused vs active schedules without `schedule show`.
**Impact:** Misleading status display; a user who paused a schedule sees no indication it is paused. (`schedule show` is unaffected — it prints fields on separate non-bracketed lines.)
**Suggested fix:** Escape the brackets or restyle, e.g. `f"[bold]{s.name}[/bold] (\\[{s.kind.value}, {state}]) id=..."` or use `console.print(..., markup=False)` / Rich `escape()`. Simplest: render as `f"{s.name}  kind={s.kind.value} state={state} id={s.schedule_id}"`.

### [Medium] `schedule show` (and pause/resume/remove) prefix lookup does not work despite being advertised
**Where:** `src/tokoboros/cli/main.py:2154-2163` (help text: "Schedule ID (or unique prefix) to inspect") → `Scheduler.get` at `src/tokoboros/jobs/scheduler.py:255-256` (`return self._schedules.get(schedule_id)` — exact key only).
**Repro (live-verified):**
```
$ tokoboros schedule show 2f393a4c          # a valid unique prefix of a real id
Schedule not found: '2f393a4c'   (exit 1)
$ tokoboros schedule show 2f393a4c-bb8f-4019-a222-63c122231450   # full id works
{...full json...}
```
**Expected:** A unique id prefix resolves the schedule, per the documented argument help.
**Actual:** Only the full UUID matches; any prefix fails.
**Impact:** Documented convenience is broken; users must paste full UUIDs. Low functional harm but a real doc-vs-behavior mismatch (and the same exact-match limitation applies to pause/resume/remove, none of which do prefix resolution either).
**Suggested fix:** Either implement prefix matching in `Scheduler.get` (match when exactly one schedule_id `startswith(arg)`, error on ambiguous) or remove the "or unique prefix" claim from the help/docstring.

### [Medium] Windows daemon install builds an unquoted `schtasks /tr` — breaks on spaced Python paths
**Where:** `src/tokoboros/daemon.py:365-377` (`_install_windows`: `tr = " ".join(argv)` then `["schtasks","/create","/tn",_TASK_NAME,"/tr",tr,...]`). *Code-reading + synthetic repro; not triggered in this env because the venv path has no spaces.*
**Repro (synthetic, no real task created — fake runner):**
```python
# locate_executable patched to a spaced path
with mock.patch.object(d,"locate_executable",return_value=Path(r"C:\Program Files\Python314\python.exe")):
    DaemonInstaller(cfg, runner=fake).install()
# /tr value -> 'C:\Program Files\Python314\python.exe -m tokoboros gateway --host 127.0.0.1 --port 8787 --runtime'
# schtasks parses image as 'C:\Program' with arg 'Files\Python314\python.exe'
```
In *this* env the path is space-free (`D:\OpenGigantic\tokoboros\.venv\Scripts\python.exe`) so it happens to work.
**Expected:** The task's executable should be quoted so schtasks treats the whole path as the image, regardless of spaces — e.g. `/tr "\"C:\Program Files\...\python.exe\" -m tokoboros gateway ..."`.
**Actual:** The path is concatenated unquoted into the `/tr` string. On the very common Windows install locations (`C:\Program Files\...`, user folders with spaces), the created task launches the wrong/nonexistent image and the gateway never starts on logon.
**Impact:** `tokoboros daemon install` silently produces a broken always-on service for a large fraction of Windows users. The code comment ("schtasks handles it") is incorrect.
**Suggested fix:** Quote argv[0] (and any arg containing a space) inside the `/tr` string, e.g. build `tr = subprocess.list2cmdline(argv)` or manually wrap each token containing whitespace in double quotes. (`list2cmdline` is the standard Windows quoting helper.) The same latent risk applies to the systemd `ExecStart` space-join in `build_systemd_unit`, though it is the conventional unit format and Linux venv paths rarely contain spaces.

### [Low] No real dry-run / preview mode for the daemon
**Where:** `src/tokoboros/cli/main.py:4331-4402` (daemon subcommand group). *Code-reading + live help inspection.*
**Repro:** `tokoboros daemon --help` lists only `install/uninstall/start/stop/status`. There is no `--dry-run` flag and no "print the plist/unit/schtasks command without registering" path. The injectable `runner` on `DaemonInstaller` exists for unit tests but is not surfaced to the CLI.
**Expected:** A way to preview the generated service definition / command before committing (useful and safe on a shared machine).
**Actual:** The only non-destructive options are `status` (queries the live service) and calling the internal builders from Python. To see what would be installed, a user must read source.
**Impact:** Minor; reduces operator confidence and makes safe inspection harder. (`status` does behave well — returns "not installed (exit 1)" cleanly when absent.)
**Suggested fix:** Add `tokoboros daemon install --dry-run` that prints `_build_argv`, the platform service-file content, and the exact command that would run, then exits without invoking the runner.

### [Low] `schedule run-now` / `run-due` cannot be end-to-end verified here due to LLM auth (env), but failure handling is correct
**Where:** `src/tokoboros/cli/main.py:2262-2296` (`run-now`). *Live-attempted.*
**Repro (live):** `tokoboros schedule run-now <id>` boots the agent successfully and reaches `agent.chat(task)`, then the Anthropic call returns `401 invalid x-api-key` (the `.env` key is not valid for the custom `ANTHROPIC_BASE_URL` in this env). The command caught the error, printed `Error running schedule: ...`, exited 1, and — correctly — did **not** advance the schedule (`run_count` stayed 0, `last_run` null).
**Expected:** With valid creds the task fires and the result is delivered; on failure the schedule is not marked fired.
**Actual:** Failure path is correct and graceful. The success path could not be confirmed because no working Anthropic credentials were available in this environment.
**Impact:** None observed in scheduler code; flagged only so the LLM-call leg is marked unverified. Not a product bug — environment/credential limitation.
**Suggested fix:** None for the scheduler. (Worth noting for future QA: provide a working key/base-url pair to exercise the fire-and-deliver leg.)

### [Nit] Rich em-dash renders as `�` on the Windows console
**Where:** CLI output, e.g. `src/tokoboros/cli/main.py:2134` (`Scheduled '…' (kind) — id …`). *Live-observed.*
**Repro:** `tokoboros schedule add ...` prints `Scheduled 'qa-int' (interval) � id ...` — the em-dash is mojibake under the default cp1252 console code page.
**Expected:** Clean dash.
**Actual:** Replacement char. Purely cosmetic.
**Impact:** Cosmetic only.
**Suggested fix:** Use an ASCII `-`/`--`, or ensure the Rich console is configured for UTF-8 on Windows.

---

## What passed

**Browser (`browser_tools.py`, `core/browser.py`, `core/net_guard.py`) — live:**
- Playwright absent → `browse_web` / `browser_act` / `browser_screenshot` all return a clean `"Browser unavailable: Browser automation needs Playwright. Install it once with: ..."` message; **no crash**, no exception leaks. Controller is lazy (no thread/launch until first call).
- SSRF guard `validate_outbound_url` is robust. Blocked: `localhost`, `127.0.0.1(:port)`, `0.0.0.0`, `169.254.169.254` (cloud metadata), `192.168.x`, `10.x`, `[::1]`, `file://`, `gopher://`, decimal/hex-encoded IPs (`2130706433`, `0x7f000001` → DNS fail), and DNS-rebinding-style public names that resolve to loopback (`127.0.0.1.nip.io`, `localtest.me`). Allowed: `http(s)://example.com`. `browse_web` correctly applies the guard before navigating (returns `Blocked URL: ...`).
- Tier gating: browsing blocked only in `sandbox` tier ("Permission Denied"); `guided`/`unrestricted`/no-gate pass the gate.

**Sandbox (`sandbox_tools.py`, `core/sandbox.py`) — live:**
- Trivial exec works: `print('sum=', sum([1..5]))` → `STDOUT: sum= 15`, exit 0, isolated dir `~/.tokoboros/sandbox/exec_<hash>`.
- Isolation: host env var `TOKO_SECRET_LEAK_TEST` is **not** visible inside the sandbox (`LEAK= None`) — minimal env is enforced.
- Dependency install via `uv` works (`cowsay` installed in ~100ms, ran correctly).
- Dependency-name validation rejects shell metacharacters and paths (`requests; rm -rf /`, `../evil`, `/abs/pkg`, `pkg$(whoami)`, `a&&b` all raise `ValueError`).
- Tier gating: `python_sandbox` blocked (PermissionError) in `sandbox` and `guided`; allowed in `unrestricted`/no-gate. Uses the public `tier` property (the documented fix for the interactive-gate `_tier` bug holds).

**Scheduler (`schedule_tools.py`, `jobs/scheduler.py`, `jobs/queue.py`) — live:**
- Create once / interval / cron via the tool layer and the CLI; all three persist to `C:\Users\opsin\AppData\Local\tokoboros\schedules.json` with full fields. `next_run` computed correctly (once = run_at; cron = next match; interval = created_at + interval).
- `list` / `show` / `show --json` / `pause` / `resume` / `remove` all function (see findings for the `list` display + prefix issues).
- Validation returns clean errors for: out-of-range cron field (`99 ...`), wrong field count (`* * *`), missing cron, zero/negative/missing interval, missing/invalid `once` datetime, unknown kind — both at the tool layer (error JSON) and CLI (exit 2).
- Fire mechanics: `tick()` fires due schedules, advances `next_run`, increments `run_count`; re-tick does not double-fire; a `fire` callback that raises does **not** advance (stays due, retried), other schedules unaffected; ONCE retires (enabled=False, next_run=None) after firing.
- Cron matcher correctness: DOW `0`/`7` both match Sunday; `1-5` excludes Sunday / includes Monday; `*/15` steps correctly; `cron_next` returns the right next slot; `*/0` rejected.
- CLI `schedule --help` lists all subcommands; flag validation (exactly one of `--cron/--every/--at`) works.
- Cleanup: all QA schedules removed and the original `schedules.json` restored from backup.

**Daemon (`daemon.py`) — inspect-only (nothing registered):**
- `daemon --help` and bare `daemon` (no_args_is_help) show the subcommand list correctly.
- `daemon status` shells out read-only (`schtasks /query`) and reports `not installed (exit 1)` gracefully (catches `CalledProcessError`).
- Service-definition builders produce correct output: launchd plist uses a proper `ProgramArguments` array (space-safe); systemd unit has valid `[Unit]/[Service]/[Install]` with `Restart=on-failure`. `_build_argv` correctly chooses `python -m tokoboros` for the venv-python fallback. No `shell=True` anywhere; `runner` kwargs are `check=True, text=True, capture_output=False`.
- No real scheduled task / service was installed at any point.
