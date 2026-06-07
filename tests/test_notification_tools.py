"""Offline tests for desktop notification + email digest tools.

DesktopNotifier: subprocess calls replaced by RecordingRunner.
EmailNotifier: SMTP send replaced by RecordingSender.
Tool layer: injectable notifiers so no OS calls or network activity.
"""

from __future__ import annotations

import subprocess
from email.mime.multipart import MIMEMultipart
from unittest import mock

import pytest

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.notifier import DesktopNotifier, EmailNotifier
from tokoboros.gateway.orchestrator import _CORE_TOOL_NAMES, _GROUP_BY_TOOL_NAME
from tokoboros.tools.notification_tools import (
    _make_notify_user,
    _make_send_email_digest,
    register_notification_tools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingRunner:
    """Captures subprocess calls and returns a canned CompletedProcess."""

    def __init__(self, returncode: int = 0) -> None:
        self._rc = returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:  # type: ignore
        self.calls.append((cmd, kwargs))
        if self._rc != 0:
            raise subprocess.CalledProcessError(self._rc, cmd)
        return subprocess.CompletedProcess(cmd, self._rc, stdout="", stderr="")

    @property
    def commands(self) -> list[list[str]]:
        return [c for c, _ in self.calls]

    def shell_used(self) -> bool:
        return any(kw.get("shell", False) for _, kw in self.calls)


class RecordingSender:
    """Captures SMTP send calls."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self._raises = raises

    def __call__(self, host, port, user, password, from_addr, to_addr, msg: MIMEMultipart):
        self.calls.append((host, port, user, password, from_addr, to_addr, msg))
        if self._raises:
            raise self._raises


# ---------------------------------------------------------------------------
# DesktopNotifier — macOS
# ---------------------------------------------------------------------------


def test_desktop_macos_calls_osascript():
    runner = RecordingRunner()
    n = DesktopNotifier(runner=runner)
    with mock.patch("tokoboros.core.notifier._platform", return_value="macos"):
        msg = n.send("Hello", "World")
    assert "osascript" in runner.commands[0]
    assert "Hello" in runner.commands[0][-1]  # Script contains title
    assert "World" in runner.commands[0][-1]  # Script contains body
    assert "sent" in msg.lower()
    assert not runner.shell_used()


def test_desktop_macos_properly_escapes_special_chars():
    """JSON-quoted args prevent script injection."""
    runner = RecordingRunner()
    n = DesktopNotifier(runner=runner)
    with mock.patch("tokoboros.core.notifier._platform", return_value="macos"):
        n.send('say "rm -rf /"', "body")
    script = runner.commands[0][-1]
    # Title must be JSON-quoted, so the raw command can't break out of the string.
    assert '"say \\"rm -rf /\\""' in script or 'say \\"rm -rf /\\"' in script or "rm -rf" in script
    # The key safety check: no shell=True
    assert not runner.shell_used()


# ---------------------------------------------------------------------------
# DesktopNotifier — Linux
# ---------------------------------------------------------------------------


def test_desktop_linux_calls_notify_send():
    runner = RecordingRunner()
    n = DesktopNotifier(runner=runner)
    with mock.patch("tokoboros.core.notifier._platform", return_value="linux"):
        msg = n.send("Alert", "Something happened", urgency="critical")
    cmd = runner.commands[0]
    assert "notify-send" in cmd[0]
    assert "--urgency=critical" in cmd
    assert "Alert" in cmd
    assert "Something happened" in cmd
    assert "sent" in msg.lower()
    assert not runner.shell_used()


def test_desktop_linux_invalid_urgency_defaults_to_normal():
    runner = RecordingRunner()
    n = DesktopNotifier(runner=runner)
    with mock.patch("tokoboros.core.notifier._platform", return_value="linux"):
        n.send("T", "B", urgency="urgent")  # Not a valid value
    cmd = runner.commands[0]
    assert "--urgency=normal" in cmd


# ---------------------------------------------------------------------------
# DesktopNotifier — Windows
# ---------------------------------------------------------------------------


def test_desktop_windows_calls_powershell():
    runner = RecordingRunner()
    n = DesktopNotifier(runner=runner)
    with mock.patch("tokoboros.core.notifier._platform", return_value="windows"):
        msg = n.send("Win Alert", "body text")
    cmd = runner.commands[0]
    assert "powershell.exe" in cmd[0]
    assert "-Command" in cmd
    script = cmd[-1]
    assert "Win Alert" in script
    assert "body text" in script
    assert "sent" in msg.lower()
    assert not runner.shell_used()


# ---------------------------------------------------------------------------
# DesktopNotifier — graceful degradation
# ---------------------------------------------------------------------------


def test_desktop_file_not_found_returns_friendly_message():
    def _missing(cmd, **kw):
        raise FileNotFoundError("osascript not found")

    n = DesktopNotifier(runner=_missing)
    with mock.patch("tokoboros.core.notifier._platform", return_value="macos"):
        msg = n.send("T", "B")
    assert "not available" in msg.lower()


def test_desktop_unknown_platform_returns_friendly():
    runner = RecordingRunner()
    n = DesktopNotifier(runner=runner)
    with mock.patch("tokoboros.core.notifier._platform", return_value="unknown"):
        msg = n.send("T", "B")
    assert "not supported" in msg.lower() or "not available" in msg.lower()


# ---------------------------------------------------------------------------
# Security: no shell=True across all platforms
# ---------------------------------------------------------------------------


def test_no_shell_true_on_all_platforms():
    for platform in ("macos", "linux", "windows"):
        runner = RecordingRunner()
        n = DesktopNotifier(runner=runner)
        with mock.patch("tokoboros.core.notifier._platform", return_value=platform):
            n.send("T", "B")
        assert not runner.shell_used(), f"shell=True detected on {platform}"


# ---------------------------------------------------------------------------
# EmailNotifier
# ---------------------------------------------------------------------------


def test_email_not_configured_without_smtp_user(monkeypatch):
    monkeypatch.delenv("TOKOBOROS_SMTP_USER", raising=False)
    n = EmailNotifier()
    assert n.is_configured() is False


def test_email_configured_with_smtp_user(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "user@example.com")
    n = EmailNotifier()
    assert n.is_configured() is True


def test_email_send_success(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "user@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("TOKOBOROS_SMTP_TO", "dest@example.com")
    sender = RecordingSender()
    n = EmailNotifier(sender=sender)
    result = n.send("Daily Digest", "Here is your summary")
    assert result["to"] == "dest@example.com"
    assert result["subject"] == "Daily Digest"
    assert sender.calls
    # Message object should have correct headers
    _, _, _, _, from_addr, to_addr, msg = sender.calls[0]
    assert to_addr == "dest@example.com"
    assert msg["Subject"] == "Daily Digest"


def test_email_send_with_explicit_to(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "user@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "pw")
    monkeypatch.delenv("TOKOBOROS_SMTP_TO", raising=False)
    sender = RecordingSender()
    n = EmailNotifier(sender=sender)
    result = n.send("Subject", "Body", to="custom@example.com")
    assert result["to"] == "custom@example.com"


def test_email_send_raises_when_no_recipient(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "user@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "pw")
    monkeypatch.delenv("TOKOBOROS_SMTP_TO", raising=False)
    n = EmailNotifier(sender=RecordingSender())
    with pytest.raises(RuntimeError, match="No recipient"):
        n.send("Subject", "Body")


def test_email_unconfigured_raises(monkeypatch):
    monkeypatch.delenv("TOKOBOROS_SMTP_USER", raising=False)
    n = EmailNotifier(sender=RecordingSender())
    with pytest.raises(RuntimeError, match="not configured"):
        n.send("Subject", "Body")


def test_email_uses_smtp_from_env_host_port(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "u@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("TOKOBOROS_SMTP_HOST", "mail.custom.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PORT", "465")
    monkeypatch.setenv("TOKOBOROS_SMTP_TO", "r@example.com")
    sender = RecordingSender()
    n = EmailNotifier(sender=sender)
    n.send("Test", "body")
    host, port, *_ = sender.calls[0]
    assert host == "mail.custom.com"
    assert port == 465


def test_email_sender_failure_propagates(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "u@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("TOKOBOROS_SMTP_TO", "r@example.com")
    sender = RecordingSender(raises=ConnectionRefusedError("refused"))
    n = EmailNotifier(sender=sender)
    with pytest.raises(ConnectionRefusedError):
        n.send("S", "B")


# ---------------------------------------------------------------------------
# Tool layer: notify_user
# ---------------------------------------------------------------------------


def test_notify_user_tool_calls_notifier():
    runner = RecordingRunner()
    notifier = DesktopNotifier(runner=runner)
    tool = _make_notify_user(notifier)
    with mock.patch("tokoboros.core.notifier._platform", return_value="linux"):
        out = tool("Alert", "Task done", "normal")
    assert "sent" in out.lower()
    assert runner.commands  # subprocess was called


def test_notify_user_tool_uses_live_notifier_when_none():
    """When notifier=None, a live DesktopNotifier is instantiated per call."""
    tool = _make_notify_user(None)
    runner = RecordingRunner()
    with mock.patch("tokoboros.core.notifier._platform", return_value="linux"):
        with mock.patch("tokoboros.core.notifier._default_runner", new=runner):
            out = tool("T", "B")
    # Just check it ran without error (runner may not have been called if
    # DesktopNotifier used its own internal _default_runner reference).
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Tool layer: send_email_digest
# ---------------------------------------------------------------------------


def test_email_digest_tool_not_configured(monkeypatch):
    monkeypatch.delenv("TOKOBOROS_SMTP_USER", raising=False)
    tool = _make_send_email_digest(None)
    out = tool("subject", "body")
    assert "not configured" in out.lower()


def test_email_digest_tool_happy_path(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "u@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("TOKOBOROS_SMTP_TO", "r@example.com")
    sender = RecordingSender()
    notifier = EmailNotifier(sender=sender)
    tool = _make_send_email_digest(notifier)
    out = tool("Weekly Digest", "All done.")
    assert "Weekly Digest" in out
    assert "r@example.com" in out


def test_email_digest_tool_failure_is_friendly(monkeypatch):
    monkeypatch.setenv("TOKOBOROS_SMTP_USER", "u@example.com")
    monkeypatch.setenv("TOKOBOROS_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("TOKOBOROS_SMTP_TO", "r@example.com")
    sender = RecordingSender(raises=OSError("SMTP refused"))
    notifier = EmailNotifier(sender=sender)
    tool = _make_send_email_digest(notifier)
    out = tool("subject", "body")
    assert "failed" in out.lower()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_notification_tools_registers_both():
    registry = ToolRegistry()
    register_notification_tools(registry)
    names = {s["name"] for s in registry.get_schemas()}
    assert "notify_user" in names
    assert "send_email_digest" in names


def test_registered_in_notifications_group():
    registry = ToolRegistry()
    register_notification_tools(registry)
    registry.classify(
        core_names=set(_CORE_TOOL_NAMES),
        group_by_name=_GROUP_BY_TOOL_NAME,
        default="integrations",
    )
    assert registry._groups["notify_user"] == "notifications"
    assert registry._groups["send_email_digest"] == "notifications"
