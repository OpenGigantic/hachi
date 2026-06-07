"""Cross-platform notification backends for Tokoboros.

Provides two lightweight, zero-extra-dependency notification paths:

- :class:`DesktopNotifier` — sends an OS-native pop-up/toast via platform tools:
  macOS → ``osascript``, Linux → ``notify-send``, Windows → PowerShell balloon tip.
  No credentials required.  Injectable ``runner`` makes it fully offline-testable.

- :class:`EmailNotifier` — sends email via SMTP (stdlib ``smtplib``).  Credentials
  are resolved lazily from environment variables at call time (never at import/boot).
  Injectable ``sender`` replaces the network call in tests.

Both backends degrade gracefully: when not available or not configured they return
a human-readable "not configured / not available" string rather than raising.
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
from collections.abc import Callable
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

# ---------------------------------------------------------------------------
# Desktop notifier
# ---------------------------------------------------------------------------

# subprocess.run compatible: (cmd: list[str], **kwargs) -> CompletedProcess-like
_Runner = Callable[..., Any]


def _default_runner(cmd: list[str], **kwargs: Any) -> Any:
    """Run a subprocess command (shell=False always)."""
    import subprocess

    return subprocess.run(cmd, shell=False, **kwargs)  # noqa: S603


def _safe_str(value: str) -> str:
    """JSON-encode a string so it is safe to embed in osascript/PowerShell arguments."""
    return json.dumps(value)


class DesktopNotifier:
    """Send desktop OS-native notifications (macOS / Linux / Windows).

    No credentials or configuration required — uses built-in OS tools.

    Args:
        runner: Callable with the same signature as :func:`subprocess.run`.
            Injected in tests so no actual notification is sent.
    """

    def __init__(self, runner: _Runner | None = None) -> None:
        self._runner = runner or _default_runner

    def send(self, title: str, body: str, urgency: str = "normal") -> str:
        """Send a desktop notification.

        Args:
            title: Short notification title (headline).
            body: Notification body text.
            urgency: One of ``"low"``, ``"normal"``, ``"critical"`` (Linux only;
                silently ignored on other platforms).

        Returns:
            Human-readable status string.
        """
        platform = _platform()
        try:
            if platform == "macos":
                return self._send_macos(title, body)
            if platform == "linux":
                return self._send_linux(title, body, urgency)
            if platform == "windows":
                return self._send_windows(title, body)
        except FileNotFoundError as exc:
            return (
                f"Desktop notification not available: {exc}. "
                "On Linux, install 'libnotify-bin' (notify-send). "
                "On macOS, osascript should be present by default."
            )
        except Exception as exc:
            return f"Desktop notification failed: {exc}"
        return "Desktop notifications are not supported on this platform."

    # ------------------------------------------------------------------
    # Platform implementations
    # ------------------------------------------------------------------

    def _send_macos(self, title: str, body: str) -> str:
        # json.dumps adds proper AppleScript-safe quoting (double quotes + escapes).
        script = f"display notification {_safe_str(body)} with title {_safe_str(title)}"
        self._runner(["osascript", "-e", script], capture_output=True, timeout=10)
        return f"Desktop notification sent: {title!r}"

    def _send_linux(self, title: str, body: str, urgency: str) -> str:
        valid_urgency = urgency if urgency in ("low", "normal", "critical") else "normal"
        self._runner(
            ["notify-send", f"--urgency={valid_urgency}", title, body],
            capture_output=True,
            timeout=10,
        )
        return f"Desktop notification sent: {title!r}"

    def _send_windows(self, title: str, body: str) -> str:
        # Use PowerShell WinForms balloon tip — no BurntToast required.
        # json.dumps produces double-quoted strings PS understands.
        script = (
            "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            f"$n.BalloonTipTitle = {_safe_str(title)}; "
            f"$n.BalloonTipText = {_safe_str(body)}; "
            "$n.Visible = $True; "
            "$n.ShowBalloonTip(4000); "
            "Start-Sleep -Milliseconds 4500; "
            "$n.Dispose()"
        )
        self._runner(
            ["powershell.exe", "-NonInteractive", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=15,
        )
        return f"Desktop notification sent: {title!r}"


# ---------------------------------------------------------------------------
# Email notifier (SMTP)
# ---------------------------------------------------------------------------

# Injectable sender: (host, port, user, password, from_addr, to_addr, msg) -> None
EmailSender = Callable[[str, int, str, str, str, str, MIMEMultipart], None]


def _default_email_sender(
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    to_addr: str,
    msg: MIMEMultipart,
) -> None:
    """Send email via SMTP with STARTTLS (stdlib, no extra deps)."""
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(from_addr, to_addr, msg.as_string())


class EmailNotifier:
    """Send email notifications via SMTP.

    Credentials are resolved lazily from environment variables at call time:
    - ``TOKOBOROS_SMTP_HOST``   (default: ``smtp.gmail.com``)
    - ``TOKOBOROS_SMTP_PORT``   (default: ``587``)
    - ``TOKOBOROS_SMTP_USER``   (required for auth)
    - ``TOKOBOROS_SMTP_PASSWORD`` (required for auth)
    - ``TOKOBOROS_SMTP_FROM``   (defaults to SMTP_USER)
    - ``TOKOBOROS_SMTP_TO``     (default recipient when ``to`` is empty)

    Args:
        sender: Injectable SMTP sender for tests (replaces the real network call).
    """

    def __init__(self, sender: EmailSender | None = None) -> None:
        self._sender = sender or _default_email_sender

    @classmethod
    def from_env(cls, sender: EmailSender | None = None) -> EmailNotifier:
        """Convenience constructor — explicitly reads env at call time."""
        return cls(sender=sender)

    def is_configured(self) -> bool:
        """Return True when SMTP credentials are available."""
        return bool(os.environ.get("TOKOBOROS_SMTP_USER"))

    def send(self, subject: str, body: str, to: str = "") -> dict[str, str]:
        """Send an email notification.

        Args:
            subject: Email subject line.
            body: Email body (plain text).
            to: Recipient address; falls back to ``TOKOBOROS_SMTP_TO`` when empty.

        Returns:
            ``{"to": addr, "subject": subject}`` on success.

        Raises:
            RuntimeError: When not configured or the send fails.
        """
        if not self.is_configured():
            raise RuntimeError(
                "Email notifications are not configured. "
                "Set TOKOBOROS_SMTP_USER and TOKOBOROS_SMTP_PASSWORD."
            )
        host = os.environ.get("TOKOBOROS_SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("TOKOBOROS_SMTP_PORT", "587"))
        user = os.environ.get("TOKOBOROS_SMTP_USER", "")
        password = os.environ.get("TOKOBOROS_SMTP_PASSWORD", "")
        from_addr = os.environ.get("TOKOBOROS_SMTP_FROM") or user
        to_addr = to or os.environ.get("TOKOBOROS_SMTP_TO", "")

        if not to_addr:
            raise RuntimeError("No recipient address. Pass `to` or set TOKOBOROS_SMTP_TO.")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.attach(MIMEText(body, "plain"))

        self._sender(host, port, user, password, from_addr, to_addr, msg)
        return {"to": to_addr, "subject": subject}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return "unknown"
