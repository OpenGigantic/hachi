"""LLM-callable outbound messaging tools (Slack / Discord).

Let the agent proactively post to Slack and Discord — e.g. "send the summary to #ops".
Credentials are resolved lazily from the environment at call time (no boot-time prompts),
or an explicit sender can be injected (tests). Every failure is returned as a readable
message rather than raised, so the agent can recover or report it.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from tokoboros.channels.discord import DiscordError, DiscordSender
from tokoboros.channels.slack import SlackError, SlackSender
from tokoboros.core.loop import ToolRegistry
from tokoboros.core.secret_scanner import redact_secrets


def _guard_outbound(text: str) -> tuple[str, str]:
    """Redact secrets from outbound text unless explicitly disabled.

    Returns ``(safe_text, note)`` — ``note`` is a non-empty suffix to append to the
    tool result when something was redacted, so the agent knows it happened. Set
    ``TOKOBOROS_ALLOW_OUTBOUND_SECRETS=true`` to opt out (power-user escape hatch).
    """
    if os.environ.get("TOKOBOROS_ALLOW_OUTBOUND_SECRETS", "").lower() in ("1", "true", "yes"):
        return text, ""
    safe, count = redact_secrets(text)
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


def register_messaging_tools(
    registry: ToolRegistry,
    *,
    slack_sender: SlackSender | None = None,
    discord_sender: DiscordSender | None = None,
) -> None:
    """Register outbound Slack/Discord tools.

    Senders are optional — when omitted, credentials are resolved from the environment at
    call time, so the tools register cleanly even before any token is configured (they return
    a friendly "not configured" message until then). Tests inject fake senders.
    """
    registry.register(SEND_SLACK_MESSAGE_SCHEMA, _make_send_slack(slack_sender))
    registry.register(SEND_DISCORD_MESSAGE_SCHEMA, _make_send_discord(discord_sender))
