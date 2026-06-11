# QA: Email (SMTP) + Notifications

Scope: `src/tokoboros/tools/notification_tools.py`, `src/tokoboros/core/notifier.py`
Environment: Windows 11, Python 3.14.3, `.venv` + `PYTHONPATH=src` (not `uv run`). Tier `unrestricted`.
Method: live execution against the real code paths; SMTP creds loaded from repo-root `.env`.

## Summary

- **Live email: SENT successfully.** `send_email_digest` authenticated to Gmail (`smtp.gmail.com:587`, user `aditya12109970@gmail.com`, 16-char app password from `.env`) and delivered one labeled test email to the configured recipient in ~3s. **No 535 auth error** — the GLOBAL-5 credential caveat did NOT manifest; the stripped 16-char app password worked.
- Baseline unit tests: `tests/test_notification_tools.py` = 31 passed.
- Core happy paths work: digest buffering coalesces N notifications into one email; `from_env()` defaults (host/port/from) are correct; "not configured" and "no recipient" error paths are friendly (no crash).
- **6 bugs found.** Headline: a **High-severity PowerShell command-injection / content-corruption** flaw in the Windows desktop-notification path — `$(...)` subexpressions in the notification body are *executed* and `$env:` variables are expanded/leaked. Plus several "false success" reporting issues where the tool claims success when the underlying send failed or was never authenticated.

Severity counts: High 1, Medium 3, Low 2, Nit 0.

## Findings

### [High] Windows desktop notifications execute PowerShell subexpressions and corrupt `$`/backtick content (command injection)
- **Where:** `core/notifier.py` `DesktopNotifier._send_windows` (lines ~113-132), via `_safe_str` (`json.dumps`, line ~46-48). Reached by `notify_user` and by `DigestBuffer.flush`'s desktop toast.
- **Repro (live-verified, this machine):**
  ```python
  import subprocess, json
  body = r'Result: $(2+2) and $(Get-Date -Format yyyy)'
  script = '$msg = ' + json.dumps(body) + '; Write-Output $msg'
  subprocess.run(['powershell.exe','-NonInteractive','-NoProfile','-Command',script],
                 capture_output=True, text=True)
  # stdout -> 'Result: 4 and 2026'   (subexpressions EXECUTED)
  ```
  Second live repro with `body = r'Cost $50; var $env:PATH; tick \` here'` rendered as
  `Cost ; var <ENTIRE MACHINE %PATH% INJECTED>; tick  here` — `$50` expanded to empty,
  `$env:PATH` expanded to the full path env var, backtick consumed.
- **Root cause:** `json.dumps` emits a **double-quoted** string, and PowerShell performs variable expansion, subexpression evaluation `$(...)`, and backtick-escape processing *inside double-quoted strings*. JSON quoting is correct for `osascript`/AppleScript (literal double-quotes) but is NOT a valid escaping mechanism for an interpolating PowerShell double-quoted string.
- **Expected:** Notification body is displayed verbatim; no code is executed regardless of `$`, `$(...)`, backtick, or `;` in the text.
- **Actual:** Arbitrary PowerShell subexpressions in the body are executed in the user's session; `$`-prefixed tokens are expanded (info disclosure, e.g. `$env:*`); literal text is silently corrupted.
- **Impact:** (1) Correctness: any legitimate notification containing `$`, backtick, or `\`-sequences is mangled. (2) Security: notification body text frequently originates from untrusted/semi-trusted sources the agent summarizes (email contents, web/tool output). A body like `$(Invoke-WebRequest ...)` would execute. This is a real command-injection surface even though no `shell=True` is used — the injection is into the PowerShell *script string*, not the OS shell.
- **Suggested fix:** Do not interpolate user text into the `-Command` script. Pass title/body as PowerShell **parameters** via `-Command "& {param($t,$b) ...}" -args` or, better, send the script on **stdin** with `powershell -Command -` and reference `$args`/`[Console]::In`. Alternatively single-quote and double up embedded single quotes (`'` -> `''`) instead of using `json.dumps`. (Same hardening should be confirmed for `osascript`: AppleScript double-quoted literals do not expand `$`, so the macOS path is lower-risk, but a `\n` in the body via `json.dumps` would still produce a literal backslash-n rather than a newline — minor.)

### [Medium] Windows desktop send always reports "sent" — return code and stderr are ignored (false success)
- **Where:** `core/notifier.py` `_send_windows` / `_send_macos` / `_send_linux` — each returns `f"Desktop notification sent: {title!r}"` unconditionally after `self._runner(...)`; the `CompletedProcess.returncode`/`stderr` are never inspected, and `capture_output=True` discards them.
- **Repro (live-verified):** Inject a runner returning `CompletedProcess(cmd, 1, stderr='Exception calling ShowBalloonTip')` (non-zero, no raise). Result: `"Desktop notification sent: 'T'"`. Also, the real live `notify_user` call returned "sent" and took **8.34s** but on a headless/Focus-Assist-suppressed session there is no way to know whether a toast actually appeared.
- **Expected:** If PowerShell/notify-send exits non-zero or writes to stderr, the tool should report failure/uncertainty.
- **Actual:** Success is reported as long as the subprocess object is returned, even on rc!=0 with an error on stderr.
- **Impact:** `flush()` derives `sent_desktop` from `"sent" in desktop_result.lower()`, so a failed toast is counted as delivered; the agent tells the user a notification was shown when it may not have been. Note the deprecated `NotifyIcon.ShowBalloonTip` API can be silently suppressed by Windows notification settings even on rc=0.
- **Suggested fix:** Check `returncode == 0` (the default runner should pass `check=False` and inspect it) and surface stderr; consider migrating to a real toast API (WinRT / BurntToast) or honestly returning "notification dispatched (delivery not confirmed)".

### [Medium] `is_configured()` checks only SMTP_USER, not SMTP_PASSWORD — login silently skipped when password is empty
- **Where:** `core/notifier.py` `EmailNotifier.is_configured` (line ~185-187): `return bool(os.environ.get("TOKOBOROS_SMTP_USER"))`. Combined with `_default_email_sender` line ~157: `if user and password: smtp.login(...)`.
- **Repro (live-verified):** Set `TOKOBOROS_SMTP_USER` + `TOKOBOROS_SMTP_TO`, leave `TOKOBOROS_SMTP_PASSWORD` unset. `is_configured()` -> `True`; `send()` proceeds and calls the sender with `password=''`; the real sender then **skips `smtp.login()`** entirely and attempts an unauthenticated `sendmail`.
- **Expected:** With the schema/docstring stating both USER and PASSWORD are "required", a missing password should report "not configured" (the friendly tool message), not attempt an unauthenticated send.
- **Actual:** Tool treats the config as valid; on Gmail the unauthenticated `sendmail` is rejected by the server (surfaced as a generic "Email notification failed: ..."), so the *user-facing message is wrong about the cause* (looks like a transient send failure rather than "you forgot the password").
- **Impact:** Confusing diagnostics; on a permissive/open relay it would actually send unauthenticated mail. Misaligned with the documented contract.
- **Suggested fix:** `is_configured()` should require both: `bool(USER) and bool(PASSWORD)`. (Tighten the corresponding unit test `test_email_configured_with_smtp_user`, which currently asserts configured with only USER set.)

### [Medium] Digest flush hides email failure when the desktop toast "succeeds"
- **Where:** `tools/notification_tools.py` `_make_flush_notification_digest` (lines ~197-210). It returns success if *either* `sent_email` *or* `sent_desktop` is truthy and only surfaces `result["error"]` when **neither** fired.
- **Repro (live-verified):** Email sender raises `OSError('(535) auth failed')`; desktop runner returns rc=0. `flush()` -> `{'sent_email': False, 'sent_desktop': True, 'item_count': 2, 'error': '(535) auth failed'}`. Tool message -> `"Digest sent (2 item(s)) via desktop notification shown."` — the 535 error is **completely hidden**.
- **Expected:** When the email leg fails, the user/agent should be told the email did not go out (especially since email is the durable channel and the desktop toast is ephemeral).
- **Actual:** Partial failure is reported as full success; `error` is dropped.
- **Impact:** Agent believes a digest email was delivered when it was not — exactly the failure a 535 (the GLOBAL-5 credential caveat) would produce. Silent data loss of the notification.
- **Suggested fix:** Include per-channel status in the return string and always append `error` if non-empty, e.g. `"Digest: email FAILED (535 ...), desktop shown (2 items)."`

### [Low] `notify_user` blocks ~8s per call (synchronous `Start-Sleep -Milliseconds 4500`)
- **Where:** `core/notifier.py` `_send_windows` script includes `Start-Sleep -Milliseconds 4500` before `$n.Dispose()`; runner `timeout=15`.
- **Repro (live-verified):** A single live `notify_user(...)` call took **8.34s** wall time (PowerShell cold start + 4.5s sleep).
- **Expected:** A "send a toast" tool should return quickly; multi-second blocking stalls the ReAct loop, and `DigestBuffer.flush` (which calls desktop send inline) inherits the stall.
- **Actual:** Each Windows notification blocks the calling turn for ~5-8s.
- **Impact:** Latency / perceived hang; if an agent buffers+flushes frequently this compounds. Conflicts with the repo guideline to prefer queued background work over inline blocking.
- **Suggested fix:** Dispatch the toast without a foreground sleep (the balloon lifetime is controlled by `ShowBalloonTip(4000)`; the process can exit sooner), or run the notification on the jobs queue / a background thread.

### [Low] Non-numeric `TOKOBOROS_SMTP_PORT` surfaces a raw Python error instead of a config hint
- **Where:** `core/notifier.py` `send` line ~209: `port = int(os.environ.get("TOKOBOROS_SMTP_PORT", "587"))`.
- **Repro (live-verified):** Set `TOKOBOROS_SMTP_PORT=not_a_number`. Tool returns `"Email notification failed: invalid literal for int() with base 10: 'not_a_number'"`.
- **Expected:** A friendly "TOKOBOROS_SMTP_PORT must be an integer" style message (it is caught by the tool's broad `except Exception`, so no crash — just a poor message).
- **Actual:** Leaks the raw `int()` ValueError text. No crash (graceful enough), but unhelpful.
- **Impact:** Minor diagnostics quality.
- **Suggested fix:** Validate/parse the port with a clear error, defaulting to 587 on parse failure or raising a `RuntimeError("TOKOBOROS_SMTP_PORT must be an integer")`.

## What passed

- **Live email send (end-to-end):** `send_email_digest` -> Gmail STARTTLS on 587 -> delivered. Returned `"Email sent to aditya12109970@gmail.com: '[TOKOBOROS QA TEST] ...'"`. Credentials in `.env` are valid; no 535.
- **`EmailNotifier.from_env()` defaults:** host -> `smtp.gmail.com`, port -> `587` (int), `from` -> falls back to `SMTP_USER`. All correct.
- **"Not configured" path:** With `TOKOBOROS_SMTP_USER` unset, `send_email_digest` returns the friendly "Email notifications are not configured..." message (no crash).
- **"No recipient" path:** Configured but no `TO`/`to` -> friendly `"Email notification failed: No recipient address. Pass `to` or set TOKOBOROS_SMTP_TO."` (no crash).
- **Digest buffering (`buffer_notification` + `flush_notification_digest`):** Buffered 3 messages -> flushed into ONE email whose body contained all three timestamped lines; `item_count=3`; buffer cleared to 0; second flush correctly returns "Digest buffer is empty". Verified both with an injected `DigestBuffer` and via the shared module-level buffer (the agent-facing `buf=None` path).
- **`notify_user` live (Windows):** Did not error; returned "Desktop notification sent" (subject to the Medium false-success caveat above). No exception, no "not available".
- **`urgency` handling:** Only Linux uses it (`--urgency=...`); macOS/Windows ignore it as documented; invalid values default to `normal`. No bug.
- **Security baseline:** All desktop paths use `shell=False`; confirmed across macos/linux/windows that `shell=True` is never set.
- **Existing unit suite:** `tests/test_notification_tools.py` 31/31 passed.
