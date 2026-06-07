"""LLM-callable outbound messaging tools (Slack / Discord / WhatsApp).

Let the agent proactively post to messaging platforms — e.g. "send the summary to #ops"
or "send this update to +447911123456 on WhatsApp". Credentials are resolved lazily from
the environment at call time (no boot-time prompts), or an explicit sender can be injected
(tests). Every failure is returned as a readable message rather than raised, so the agent
can recover or report it.
"""

from __future__ import annotations

from collections.abc import Callable

from tokoboros.channels.discord import DiscordError, DiscordSender
from tokoboros.channels.slack import SlackError, SlackSender
from tokoboros.channels.whatsapp import WhatsAppError, WhatsAppSender
from tokoboros.core.loop import ToolRegistry
from tokoboros.core.secret_scanner import redact_outbound


def _guard_outbound(text: str) -> tuple[str, str]:
    """Redact secrets from outbound text via the shared outbound-redaction policy.

    Returns ``(safe_text, note)`` — ``note`` is a non-empty suffix to append to the
    tool result when something was redacted, so the agent knows it happened. The opt-out
    (``TOKOBOROS_ALLOW_OUTBOUND_SECRETS=true``) is honored inside ``redact_outbound``.
    """
    safe, count = redact_outbound(text)
    note = f" ({count} secret(s) redacted before sending for your safety.)" if count else ""
    return safe, note


def _make_send_slack(sender: SlackSender | None) -> Callable[[str, str], str]:
    def send_slack_message(text: str, channel: str = "") -> str:
        text, guard_note = _guard_outbound(text)
        active = sender or SlackSender.from_env()
        if not active.is_configured():
            return (
                "Slack is not configured. Set SLACK_BOT_TOKEN (and pass a channel) or "
                "SLACK_WEBHOOK_URL, then try again."
            )
        try:
            result = active.send(text=text, channel=channel)
        except SlackError as exc:
            return f"Slack send failed: {exc}"
        except Exception as exc:
            return f"Slack send failed: {exc}"
        return f"Sent message to Slack ({result.get('target')})." + guard_note

    return send_slack_message


def _make_send_discord(sender: DiscordSender | None) -> Callable[[str, str], str]:
    def send_discord_message(content: str, channel: str = "") -> str:
        content, guard_note = _guard_outbound(content)
        active = sender or DiscordSender.from_env()
        if not active.is_configured():
            return (
                "Discord is not configured. Set DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN "
                "(and pass a channel id), then try again."
            )
        try:
            result = active.send(content=content, channel=channel)
        except DiscordError as exc:
            return f"Discord send failed: {exc}"
        except Exception as exc:
            return f"Discord send failed: {exc}"
        return f"Sent message to Discord ({result.get('target')})." + guard_note

    return send_discord_message


SEND_SLACK_MESSAGE_SCHEMA = {
    "name": "send_slack_message",
    "description": (
        "Post a message to Slack. Use this to proactively notify a channel or user — e.g. send a "
        "summary, alert, or reply. With a bot token you must pass a `channel` (like '#ops' or a "
        "channel id); with an incoming webhook the channel is fixed and `channel` is ignored."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The message text to post."},
            "channel": {
                "type": "string",
                "description": "Target channel ('#name' or id). Required with a bot token.",
            },
        },
        "required": ["text"],
    },
}

SEND_DISCORD_MESSAGE_SCHEMA = {
    "name": "send_discord_message",
    "description": (
        "Post a message to Discord. Use this to proactively notify a channel — e.g. send a "
        "summary, alert, or reply. With a webhook the channel is fixed; with a bot token pass the "
        "target `channel` id. Messages longer than 2000 characters are truncated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The message content to post."},
            "channel": {
                "type": "string",
                "description": "Channel id; required for bot tokens, ignored for webhooks.",
            },
        },
        "required": ["content"],
    },
}


def _make_send_whatsapp(sender: WhatsAppSender | None) -> Callable[[str, str], str]:
    def send_whatsapp_message(text: str, to: str = "") -> str:
        text, guard_note = _guard_outbound(text)
        active = sender or WhatsAppSender.from_env()
        if not active.is_configured():
            return (
                "WhatsApp is not configured. Set META_WHATSAPP_TOKEN + META_WHATSAPP_PHONE_ID "
                "(Meta Cloud API), or TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + "
                "TWILIO_WHATSAPP_FROM (Twilio), then try again."
            )
        if not to:
            return "A recipient phone number (`to`) is required to send a WhatsApp message."
        try:
            result = active.send(to=to, text=text)
        except WhatsAppError as exc:
            return f"WhatsApp send failed: {exc}"
        except Exception as exc:
            return f"WhatsApp send failed: {exc}"
        provider = result.get("provider")
        return f"Sent WhatsApp message to {result.get('to')} via {provider}." + guard_note

    return send_whatsapp_message


SEND_WHATSAPP_MESSAGE_SCHEMA = {
    "name": "send_whatsapp_message",
    "description": (
        "Send a WhatsApp message to a phone number. Use this to proactively notify someone "
        "on WhatsApp — e.g. send a summary, reminder, or alert. Requires a recipient phone "
        "number in E.164 format (e.g. '+447911123456'). Supports Meta Cloud API or Twilio."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The message text to send."},
            "to": {
                "type": "string",
                "description": "Recipient phone number in E.164 format (e.g. '+447911123456').",
            },
        },
        "required": ["text", "to"],
    },
}


def register_messaging_tools(
    registry: ToolRegistry,
    *,
    slack_sender: SlackSender | None = None,
    discord_sender: DiscordSender | None = None,
    whatsapp_sender: WhatsAppSender | None = None,
) -> None:
    """Register outbound Slack/Discord/WhatsApp tools.

    Senders are optional — when omitted, credentials are resolved from the environment at
    call time, so the tools register cleanly even before any token is configured (they return
    a friendly "not configured" message until then). Tests inject fake senders.
    """
    registry.register(SEND_SLACK_MESSAGE_SCHEMA, _make_send_slack(slack_sender))
    registry.register(SEND_DISCORD_MESSAGE_SCHEMA, _make_send_discord(discord_sender))
    registry.register(SEND_WHATSAPP_MESSAGE_SCHEMA, _make_send_whatsapp(whatsapp_sender))
