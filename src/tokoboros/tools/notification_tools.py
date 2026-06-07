"""LLM-callable notification tools (desktop pop-up + SMTP email digest).

Lets the agent proactively alert the user outside the chat window:
- ``notify_user``         — OS-native desktop notification (macOS / Linux / Windows).
- ``send_email_digest``   — SMTP email notification / digest.

Both tools degrade gracefully:
- Desktop: returns a friendly "not available" string if OS tools are absent.
- Email:   returns a "not configured" string if SMTP creds are unset.
"""

from __future__ import annotations

from collections.abc import Callable

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.notifier import DesktopNotifier, EmailNotifier

# ---------------------------------------------------------------------------
# Desktop notification tool
# ---------------------------------------------------------------------------

NOTIFY_USER_SCHEMA = {
    "name": "notify_user",
    "description": (
        "Send a desktop OS notification (pop-up/toast) to the user's screen. "
        "Use this to proactively alert the user about completed tasks, reminders, "
        "or time-sensitive events — even when they're not looking at the chat window. "
        "Works on macOS (osascript), Linux (notify-send), and Windows (PowerShell)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short notification headline (≤60 chars)."},
            "body": {
                "type": "string",
                "description": "Notification body text (one or two sentences).",
            },
            "urgency": {
                "type": "string",
                "enum": ["low", "normal", "critical"],
                "description": "Urgency level (Linux only; 'normal' on other platforms).",
            },
        },
        "required": ["title", "body"],
    },
}


def _make_notify_user(notifier: DesktopNotifier | None) -> Callable[[str, str, str], str]:
    """Build the notify_user tool function.

    Args:
        notifier: Injectable :class:`DesktopNotifier`; defaults to a live instance.
    """

    def notify_user(title: str, body: str, urgency: str = "normal") -> str:
        active: DesktopNotifier = notifier if notifier is not None else DesktopNotifier()
        result: str = active.send(title=title, body=body, urgency=urgency)
        return result

    return notify_user


# ---------------------------------------------------------------------------
# Email digest tool
# ---------------------------------------------------------------------------

SEND_EMAIL_DIGEST_SCHEMA = {
    "name": "send_email_digest",
    "description": (
        "Send an email notification or digest to the user via SMTP. "
        "Use this for longer summaries, daily/weekly digests, or when the user "
        "is away from their computer. "
        "Requires TOKOBOROS_SMTP_USER and TOKOBOROS_SMTP_PASSWORD to be configured. "
        "The default recipient is TOKOBOROS_SMTP_TO when `to` is omitted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Email subject line."},
            "body": {
                "type": "string",
                "description": "Email body (plain text; markdown is acceptable).",
            },
            "to": {
                "type": "string",
                "description": "Recipient email address. Falls back to TOKOBOROS_SMTP_TO.",
            },
        },
        "required": ["subject", "body"],
    },
}


def _make_send_email_digest(
    email_notifier: EmailNotifier | None,
) -> Callable[[str, str, str], str]:
    """Build the send_email_digest tool function.

    Args:
        email_notifier: Injectable :class:`EmailNotifier`; defaults to env-based config.
    """

    def send_email_digest(subject: str, body: str, to: str = "") -> str:
        active = email_notifier or EmailNotifier.from_env()
        if not active.is_configured():
            return (
                "Email notifications are not configured. "
                "Set TOKOBOROS_SMTP_USER and TOKOBOROS_SMTP_PASSWORD (and optionally "
                "TOKOBOROS_SMTP_HOST, TOKOBOROS_SMTP_PORT, TOKOBOROS_SMTP_FROM, "
                "TOKOBOROS_SMTP_TO), then try again."
            )
        try:
            result = active.send(subject=subject, body=body, to=to)
        except RuntimeError as exc:
            return f"Email notification failed: {exc}"
        except Exception as exc:
            return f"Email notification failed: {exc}"
        return f"Email sent to {result.get('to')}: {result.get('subject')!r}"

    return send_email_digest


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_notification_tools(
    registry: ToolRegistry,
    *,
    notifier: DesktopNotifier | None = None,
    email_notifier: EmailNotifier | None = None,
) -> None:
    """Register desktop-notification and email-digest tools.

    Args:
        registry: The agent's :class:`~tokoboros.core.loop.ToolRegistry`.
        notifier: Injectable :class:`DesktopNotifier`; defaults to live OS calls.
        email_notifier: Injectable :class:`EmailNotifier`; defaults to env-based config.
    """
    registry.register(NOTIFY_USER_SCHEMA, _make_notify_user(notifier))
    registry.register(SEND_EMAIL_DIGEST_SCHEMA, _make_send_email_digest(email_notifier))
