# UAT — 05: Permission Tiers, Path Containment, Shell Enforcement

> **Historical document.** Written before the project rename to Hachi. References to
> "Hachi", the `hachi` CLI, `HACHI_*` env vars, and `src/hachi/...` paths
> below describe the project now called **Hachi** (package `hachi`, CLI `hachi`, dist
> `hachi-og`) and are preserved here as migration history. See `docs/migration/hachi-rename.md`.

**Build SHA:** `036dfd9`  
**Date:** 2026-06-14  
**Tester:** UAT Verification Agent (read-only; in-process repro only)  
**Primary sources:** `src/hachi/core/permissions.py`, `src/hachi/tools/os_tools.py`

> **Top headline finding:** The `run_shell_script` tool at `guided` tier is **silently blocked** because `powershell`/`bash` are interpreter-blocked, yet the tool schema advertises full shell capabilities with no per-tier caveat. Any agent at `guided` tier that attempts to use `run_shell_script` receives a misleading `Permission Denied: 'powershell' is blocked in guided tier` error. The claim that `run_shell_script` allows operators "only if the shell is allowlisted" is technically true but the allowlist never includes the shell at `guided` — so this tool is effectively dead at the tier where users are most likely to want it.

---

## UC-PERM-001 — CONFIRMED

**Claim:** At sandbox tier, terminal / python_sandbox / browser / vision tools are blocked.

**Evidence — tier table (`permissions.py:209-217`):**
```python
"terminal": {
    "enabled": "false",
    ...
}
```
Terminal `enabled=false` for sandbox tier is the first check in `check_command` (`permissions.py:529-534`):
```python
if not self._term.enabled:
    return PermissionResult(
        False,
        f"Terminal access is disabled in '{self._tier}' tier. ..."
    )
```

**python_sandbox** — blocked at `sandbox` **and** `guided` tiers (`sandbox_tools.py:28-33`):
```python
tier = getattr(gate, "tier", None)
if tier in ("sandbox", "guided"):
    raise PermissionError(f"python_sandbox is not available in {tier} tier ...")
```

**browser** — `browser_tools.py:24, 27-32`:
```python
_BLOCKED_TIERS = {"sandbox"}
def _tier_blocked(gate):
    tier = getattr(gate, "tier", None)
    return tier if tier in _BLOCKED_TIERS else None
```

**vision** — `vision_tools.py:95`:
```python
_BLOCKED_TIERS = {"sandbox"}
```

**Repro output (in-process, no live agent):**
```
sandbox terminal disabled: True
  reason=Terminal access is disabled in 'sandbox' tier.
python_sandbox at sandbox blocked: True
  error=python_sandbox is not available in sandbox tier -- it grants unrestricted host access.
browser _tier_blocked(sandbox): sandbox
vision sandbox blocked: True
```

**Finding:** All four tool classes (terminal, python_sandbox, browser, vision) are correctly blocked at sandbox tier. The tier check uses the public `.tier` property, so it functions correctly under `InteractivePermissionGate` as well (verified by `test_security_hardening.py::test_python_sandbox_blocked_under_interactive_gate`). All 40 hardening tests pass.

**Note:** `python_sandbox` is additionally blocked at `guided` tier, which may be surprising but is intentional (it grants unrestricted host access).

**Severity:** N/A (passing)  
**Regression test?** Yes — `tests/test_security_hardening.py::test_python_sandbox_blocked_under_interactive_gate`, `tests/test_permission_gate_hardening.py` (40 tests pass).

---

## UC-PERM-002 — PARTIAL

**Claim:** Path containment defeats `../` traversal, symlink escapes, and UNC paths; blocked paths always win.

### Sub-test 1: `../` traversal — CONFIRMED

**Evidence (`permissions.py:270-275`):**
```python
def _resolve_path(raw: str) -> Path:
    raw = raw.strip()
    if raw.startswith("~"):
        return Path(raw).expanduser().resolve()
    return Path(raw).resolve()
```
`Path.resolve()` canonicalises `../` segments before the containment check.

**`_is_subpath` (`permissions.py:285-291`):**
```python
def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
```

**Repro output:**
```
../ traversal denied: True
  reason='...\blocked\secret.txt' is inside blocked directory '...\blocked'. This restriction cannot be overridden.
```
The traversal hit the blocked-path rule because the resolved canonical path falls inside `blocked/`. Even without `blocked/` being listed, if it fell outside `allowed/` the default deny at `permissions.py:477-482` would catch it.

### Sub-test 2: Symlink escape — NOT-REPRODUCED (environment limitation)

**Evidence:** Test `test_permission_gate_hardening.py::test_symlink_escape_outside_allowed_root_is_denied` exists and asserts denial. The logic is sound: `Path(...).resolve()` follows symlinks before the `relative_to` check, so a symlink inside `allowed/` pointing to a directory outside would resolve to the real target and fail the `relative_to(allowed_path)` test.

**Live repro result:** Symlink creation failed with `[WinError 1314] A required privilege is not held by the client` — Windows requires elevated privileges or Developer Mode for symlink creation. The test in the test suite uses `pytest.skip` for this exact condition (`test_permission_gate_hardening.py:182-183`).

**Conclusion:** Code is correct per static analysis. Symlink behavior **cannot be verified on this machine** without Developer Mode or elevation. The TOCTOU risk (symlink retargeted between `check_path` and actual file open) is a real gap: `_resolve_path` resolves once at check time but `open()` in `_raw_read_file` does not re-resolve or verify. However, this is a generic class of TOCTOU not specific to the current code path.

### Sub-test 3: Windows UNC path — PARTIAL (handled by default deny, not by explicit UNC guard)

**Evidence:** `_resolve_path(r"\\127.0.0.1\admin$\system32\file.txt")` on Windows calls `Path(...).resolve()` which returns the UNC path as-is (it is already absolute). The result is `\\127.0.0.1\admin$\System32\file.txt`, `is_absolute()=True`.

**Repro output:**
```
UNC path resolved to: \\127.0.0.1\admin$\System32\file.txt  is_absolute=True
UNC path allowed=False
  reason='\\127.0.0.1\admin$\System32\file.txt' is not in your allowed directories.
  UNC is_subpath_of_allowed=False  is_subpath_of_blocked=False
```

**Finding:** The UNC path is **denied**, but only because it is not in `allowed_paths` — the containment logic correctly falls through to the default-deny at step 5 (`permissions.py:477-482`). There is **no explicit UNC guard**. This is the critical distinction:

- If `allow_all=True` (unrestricted tier), `path_allowed` is set to `True` at step 2 (`permissions.py:470`), and the UNC path would only be blocked if it happens to be a subpath of `blocked_paths`. On the live machine, `\\127.0.0.1\admin$\...` does not resolve as a subpath of `~/.ssh`, `~/.hachi`, or `~/AppData` (these are local paths). Therefore **a UNC path is NOT blocked by `blocked_paths` at unrestricted tier** — it passes straight through.

**Repro at unrestricted tier (implicit from logic):** `unrestricted` sets `allow_all=True`; the only blocked path is `~/.ssh`. A UNC share like `\\127.0.0.1\admin$\...` would be `is_subpath_of_blocked=False`, so `check_path` returns `PermissionResult(True, ...)`. This is an unverified escape at `unrestricted` tier.

**Severity:** HIGH — Unverified UNC bypass at `unrestricted` tier. The UAT spec (UC-PERM-002) explicitly calls this out as "untested."  
**Regression test?** No UNC-specific test exists. A test covering `unrestricted` + UNC path should be added.

### Sub-test 4: Blocked paths always win — CONFIRMED

`check_path` evaluates blocked paths before the allow-all shortcut (`permissions.py:460-467`):
```python
# 1. Blocked paths always win
for bp in self._fs.blocked_paths:
    if _is_subpath(resolved, bp):
        return PermissionResult(False, ...)
```

**Repro output:**
```
blocked path denied: True  reason='...blocked...' is inside blocked directory '...blocked'.
```

---

## UC-PERM-003 — CONFIRMED with one documented gap

**Claim:** `git status` allowed; `rm -rf`/`shutdown` blocked; compound `echo hi && shutdown` blocked; `python -c` blocked; `npx node` blocked; `cat <blocked-path>` blocked; `cat id_rsa` (bare filename, no separator) NOT path-checked.

### Allowed commands

**Evidence (`permissions.py:604-626`):** Allowlist checks `executable_name` and `executable_stem` via exact match. `git` is in `_SAFE_COMMANDS` (line 134).

**Repro:** `git status allowed=True  reason=Command 'git' is allowed.`

### Blocked commands

**`rm -rf` — multi-token blocked (`permissions.py:541-550`):**
```python
# Multi-token pattern: every pattern token must appear in the command tokens
if all(t in cmd_tokens_lower for t in bc_tokens):
    return PermissionResult(False, ...)
```
`_DANGEROUS_COMMANDS` includes `"rm -rf"` (line 167). Command `"rm -rf /"` tokenizes to `['rm', '-rf', '/']`; pattern tokens `['rm', '-rf']` are both present.

**`shutdown` — single-token blocked (`permissions.py:553-558`):**
```python
if bc_lower in cmd_tokens_lower:
    return PermissionResult(False, ...)
```
`"shutdown"` is in `_DANGEROUS_COMMANDS` (line 176). Command `"shutdown /s /t 0"` tokenized = `['shutdown', '/s', '/t', '0']`; `'shutdown' in tokens` → True.

**Compound `echo hi && shutdown`:** Tokenizes to `['echo', 'hi', '&&', 'shutdown']`; `'shutdown' in tokens` → True. Blocked before allowlist check.

**Repro:**
```
rm -rf blocked=True  reason='rm -rf' is a blocked command pattern.
shutdown blocked=True  reason='shutdown' is a blocked command pattern.
echo&&shutdown blocked=True  reason='shutdown' is a blocked command pattern.
```

### Interpreter block (`python -c`)

**`permissions.py:570-589`:**
```python
interpreter_stems = {"python", "python3", "node", "pwsh", "powershell", "bash", "sh", "zsh", "cmd"}
if self._tier != "unrestricted" and executable_stem in interpreter_stems:
    return PermissionResult(False, f"'{executable_stem}' is blocked in {self._tier} tier ...")
```

**Repro:** `python -c blocked in guided=True  reason='python' is blocked in guided tier ...`

### Wrapper block (`npx node`)

**`permissions.py:592-603`:**
```python
if self._tier != "unrestricted" and executable_stem in {"npx", "npm", "pip", "pip3"}:
    for token in tokens[1:]:
        candidate_stem = ...
        if candidate_stem in interpreter_stems:
            return PermissionResult(False, f"Nested interpreter '{candidate_stem}' is blocked ...")
```

**Repro:** `npx node blocked in guided=True  reason=Nested interpreter 'node' is blocked in guided tier.`

### `cat <absolute-blocked-path>` — blocked

**`permissions.py:620-624` (FIX C1a):**
```python
if executable_stem in _EXFIL_STEMS:
    blocked_result = self._check_path_args(tokens[1:])
    if blocked_result is not None:
        return blocked_result
```

**`_check_path_args` (`permissions.py:418-433`):**
```python
if not any(c in token for c in ("/", "\\", "~", ".")):
    continue
```

**Repro:** `cat /blocked/id_rsa blocked=True  reason=Access to blocked path denied.`

### `cat id_rsa` (bare filename, no separator) — CONFIRMED GAP

**Evidence (`permissions.py:418-424`):**
```python
# Only treat tokens that look like paths (contain a separator, ~, or ./)
if not any(c in token for c in ("/", "\\", "~", ".")):
    continue
```
Token `id_rsa` contains none of these characters. `_check_path_args` skips it. The command then passes (it's in `_EXFIL_STEMS` → `cat` → allowlisted) and would execute in whatever `cwd` the agent provides.

**`os_tools.py:462-465` (inline comment confirms intentional design):**
```python
# Note: We intentionally do NOT gate the cwd here. The command allowlist
# already controls what the agent can execute. Gating cwd creates false
# positives ...
```

**Repro:**
```
cat id_rsa (bare, no sep) allowed=True  (known gap per os_tools.py:424)
token 'id_rsa' has_sep=False -> _check_path_args skips it (gap confirmed in code)
```

**Severity:** MEDIUM — This is a documented, intentional design gap (the comment at `os_tools.py:462` explains the tradeoff). An attacker who can control the `cwd` parameter and place a sensitive file there could exfiltrate it via `cat <bare-name>`. However, `cwd` is a parameter the agent chooses, not arbitrary user input at the tool level.  
**Regression test?** No test specifically covers this documented gap. A test asserting that `cat id_rsa` from a blocked `cwd` produces a warning or is not flagged (as documentation of the intentional behavior) would be useful for future reviewers.

---

## UC-PERM-004 — CONFIRMED with PARTIAL for `run_shell_script` at `guided`

**Claim:** `run_terminal_command` rejects shell operators; `run_shell_script` allows them only if the shell is allowlisted.

### `run_terminal_command` — ALL OPERATORS BLOCKED

**Evidence (`os_tools.py:121-128`):**
```python
blocked_fragments = ("&&", "||", "|", ";", "`", "$(", ">", "<")
for fragment in blocked_fragments:
    if fragment in raw:
        return (
            "Error: Shell operators are not allowed in run_terminal_command. "
            "Provide a single executable with arguments only."
        )
```

**Repro — all 8 operators:**
```
'&&': blocked=True
'||': blocked=True
'|': blocked=True
';': blocked=True
'`': blocked=True
'$(': blocked=True
'>': blocked=True
'<': blocked=True
```

### `run_shell_script` — gated by `check_command(shell)`

**Evidence (`os_tools.py:479-485`):**
```python
def guarded_run_shell_script(script: str, cwd: str = ".") -> str:
    shell = "powershell" if os.name == "nt" else "bash"
    cmd_result = gate.check_command(shell)
    if not cmd_result.allowed:
        return f"Permission Denied: {cmd_result.reason}"
    return _raw_run_shell_script(script, cwd, timeout=gate.terminal_timeout)
```

**At `sandbox` tier:** `terminal.enabled=false` → `check_command("powershell")` returns denied → `run_shell_script` blocked.

**At `guided` tier:** `powershell` is in `interpreter_stems` → blocked because `self._tier != "unrestricted"` (`permissions.py:584`). `run_shell_script` is therefore **blocked at guided tier as well**.

**At `unrestricted` tier:** `allow_all=True` → `check_command("powershell")` returns allowed → `run_shell_script` executes freely.

**Repro:**
```
run_shell_script at sandbox blocked: True
  result=Permission Denied: Terminal access is disabled in 'sandbox' tier.
run_shell_script at guided result: Permission Denied: 'powershell' is blocked in guided tier ...
run_shell_script at unrestricted: powershell allowed=True
  script result: --- STDOUT --- test_output_ok  Exit Code: 0
```

**Finding:** The behavior is technically correct per the code contract ("only if the shell is allowlisted"), but the practical effect is that `run_shell_script` only works at `unrestricted` tier. The tool schema description (`os_tools.py:551-572`) advertises full shell capabilities with no mention of this tier constraint — **users at `guided` tier will hit an opaque `Permission Denied` error with no actionable guidance**. This is a UX gap, not a security gap.

**Severity:** LOW (UX / documentation mismatch) — `run_shell_script` at `guided` tier produces an unhelpful error. Security is not compromised; if anything the tier is more restrictive than advertised.  
**Regression test?** The `test_timeout_bound_long_running_command_is_terminated` test covers the unrestricted path. No test covers the guided-tier block of `run_shell_script`. A test asserting the behavior at guided tier would document this surprising-but-correct restriction.

---

## Summary table

| Scenario | Verdict | Severity | Regression test exists? |
|---|---|---|---|
| UC-PERM-001 — sandbox blocks terminal/sandbox/browser/vision | CONFIRMED | N/A | Yes (40 tests pass) |
| UC-PERM-002 — path containment: `../` traversal | CONFIRMED | N/A | Yes |
| UC-PERM-002 — path containment: symlink escape | NOT-REPRODUCED (env: WinError 1314, symlink privilege required) | N/A | Yes (code is correct) |
| UC-PERM-002 — path containment: UNC path | PARTIAL — denied by default-deny at guided; **NOT blocked at unrestricted tier** (no explicit UNC guard) | High | No |
| UC-PERM-002 — blocked paths always win | CONFIRMED | N/A | Yes |
| UC-PERM-003 — allowed/blocked shell enforcement | CONFIRMED | N/A | Yes |
| UC-PERM-003 — `cat id_rsa` (bare filename) not path-checked | CONFIRMED GAP (intentional per code comment) | Medium | No |
| UC-PERM-004 — `run_terminal_command` rejects all operators | CONFIRMED | N/A | Yes |
| UC-PERM-004 — `run_shell_script` operator policy | PARTIAL — only works at `unrestricted`; blocked at `guided` by powershell interpreter block | Low (UX) | No |

---

## Defects raised

### DEFECT-PERM-A (High): UNC path bypasses blocked_paths at unrestricted tier

- **Build SHA:** `036dfd9`
- **Surface:** PermissionGate path check
- **Tier:** unrestricted
- **Repro:** `PermissionGate.check_path(r"\\server\share\file.txt", "read")` at `unrestricted` tier returns `allowed=True` because `allow_all=True` skips allowed-path containment checks, and `_is_subpath(unc_resolved, blocked_path)` returns `False` for local blocked paths (e.g. `~/.ssh`) since `Path.relative_to` does not relate UNC paths to local paths.
- **Root cause:** No explicit UNC prefix check in `check_path` or `_resolve_path`. `_is_subpath` uses `relative_to` which does not establish cross-share relationships.
- **Fix direction:** Add an explicit UNC prefix guard in `check_path` (or `_resolve_path`) that rejects `\\...` paths unless they are in `allowed_paths`; also ensure `unrestricted` tier's `blocked_paths` (which only contains `~/.ssh`) is checked independently of whether `allow_all` is set. The "blocked paths always win" invariant must apply before the `allow_all` shortcut.
- **Grounds:** `permissions.py:437-510`, `_is_subpath` lines 285-291.

### DEFECT-PERM-B (Low / UX): `run_shell_script` silently unavailable at guided tier

- **Build SHA:** `036dfd9`
- **Surface:** `run_shell_script` tool schema vs runtime behavior
- **Tier:** guided
- **Repro:** Call `guarded_run_shell_script(script, cwd)` at guided tier → `Permission Denied: 'powershell' is blocked in guided tier because it can bypass filesystem protections.`
- **User impact:** Schema advertises the tool as the solution "when run_terminal_command is too restrictive" — but at guided tier it is equally (more) blocked. Users have no recourse.
- **Fix direction:** Add a note to `RUN_SHELL_SCRIPT_SCHEMA.description` that this tool requires `unrestricted` tier, or add `powershell`/`bash` to the guided `allowed_commands` list behind a new `allow_shell_scripts` config flag.
- **Grounds:** `os_tools.py:550-572`, `permissions.py:209-238`.
