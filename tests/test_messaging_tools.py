"""Tests for outbound Slack/Discord senders, adapters, and LLM tools.

Fully offline: HTTP is replaced by a recording fake poster, so request building, response
interpretation, SSRF webhook rejection, and the tool/adapter layers are all exercised
without touching the network.
"""

from __future__ import annotations

import json

import pytest

from tokoboros.channels.base import OutboundMessage
from tokoboros.channels.discord import (
    MAX_CONTENT,
    DiscordAdapter,
    DiscordError,
    DiscordSender,
)
from tokoboros.channels.discord import (
    build_bot_request as d_bot_request,
)
from tokoboros.channels.slack import (
    SlackAdapter,
    SlackError,
    SlackSender,
)
from tokoboros.channels.slack import (
    build_bot_request as s_bot_request,
)
from tokoboros.core.loop import ToolRegistry
from tokoboros.tools.messaging_tools import register_messaging_tools


class RecordingPoster:
    """Captures the last (url, payload, headers) and returns a canned (status, body)."""

    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status = status
        self.body = body
        self.calls: list[tuple] = []

    def __call__(self, url, payload, headers):
        self.calls.append((url, payload, headers))
        return self.status, self.body


def _never_called(url, payload, headers):  # poster that asserts it is not invoked
    raise AssertionError("poster should not be called")


# -- pure request builders --------------------------------------------------


def test_slack_bot_request_shape():
    url, payload, headers = s_bot_request("xoxb-1", "#ops", "hi")
    assert url.endswith("/chat.postMessage")
    assert payload == {"channel": "#ops", "text": "hi"}
    assert headers["Authorization"] == "Bearer xoxb-1"


def test_slack_bot_request_requires_channel():
    with pytest.raises(SlackError):
        s_bot_request("xoxb-1", "", "hi")


def test_discord_bot_request_requires_channel():
    with pytest.raises(DiscordError):
        d_bot_request("tok", "", "hi")


def test_discord_truncates_overlong_content():
    url, payload, _ = d_bot_request("tok", "123", "x" * (MAX_CONTENT + 500))
    assert len(payload["content"]) == MAX_CONTENT
    assert payload["content"].endswith("…")


# -- SlackSender ------------------------------------------------------------


def test_slack_bot_send_success():
    poster = RecordingPoster(200, json.dumps({"ok": True, "channel": "C1"}))
    sender = SlackSender(bot_token="xoxb-1", poster=poster)
    result = sender.send(text="hello", channel="#ops")
    assert result["status"] == "sent"
    assert poster.calls and poster.calls[0][1] == {"channel": "#ops", "text": "hello"}


def test_slack_bot_send_logical_error_raises():
    poster = RecordingPoster(200, json.dumps({"ok": False, "error": "channel_not_found"}))
    sender = SlackSender(bot_token="xoxb-1", poster=poster)
    with pytest.raises(SlackError) as exc:
        sender.send(text="hello", channel="#nope")
    assert "channel_not_found" in str(exc.value)


def test_slack_webhook_send_success():
    poster = RecordingPoster(200, "ok")
    sender = SlackSender(webhook_url="https://hooks.slack.com/services/T/B/X", poster=poster)
    result = sender.send(text="hello")
    assert result["target"] == "webhook"
    assert poster.calls[0][1] == {"text": "hello"}


def test_slack_webhook_ssrf_blocked():
    sender = SlackSender(webhook_url="http://127.0.0.1/hook", poster=_never_called)
    with pytest.raises(SlackError) as exc:
        sender.send(text="hello")
    assert "blocked webhook URL" in str(exc.value)


def test_slack_not_configured_raises():
    sender = SlackSender(poster=_never_called)
    assert not sender.is_configured()
    with pytest.raises(SlackError):
        sender.send(text="hello")


def test_slack_empty_text_raises():
    sender = SlackSender(bot_token="xoxb-1", poster=_never_called)
    with pytest.raises(SlackError):
        sender.send(text="   ", channel="#ops")


# -- DiscordSender ----------------------------------------------------------


def test_discord_webhook_send_success():
    poster = RecordingPoster(204, "")
    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/1/abc", poster=poster)
    result = sender.send(content="hello")
    assert result["target"] == "webhook"
    assert poster.calls[0][1] == {"content": "hello"}


def test_discord_bot_send_success():
    poster = RecordingPoster(200, "{}")
    sender = DiscordSender(bot_token="bot-tok", poster=poster)
    result = sender.send(content="hello", channel="999")
    assert result["status"] == "sent"
    assert "/channels/999/messages" in poster.calls[0][0]


def test_discord_webhook_ssrf_blocked():
    sender = DiscordSender(webhook_url="http://localhost/hook", poster=_never_called)
    with pytest.raises(DiscordError) as exc:
        sender.send(content="hello")
    assert "blocked webhook URL" in str(exc.value)


def test_discord_http_error_raises():
    poster = RecordingPoster(403, "Forbidden")
    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/1/abc", poster=poster)
    with pytest.raises(DiscordError) as exc:
        sender.send(content="hello")
    assert "403" in str(exc.value)


def test_discord_not_configured_raises():
    sender = DiscordSender(poster=_never_called)
    with pytest.raises(DiscordError):
        sender.send(content="hello")


# -- adapters ---------------------------------------------------------------


def test_slack_adapter_send_delegates():
    poster = RecordingPoster(200, json.dumps({"ok": True}))
    adapter = SlackAdapter(SlackSender(bot_token="xoxb-1", poster=poster))
    adapter.send(OutboundMessage(channel="slack", recipient_id="#ops", text="hi"))
    assert poster.calls[0][1] == {"channel": "#ops", "text": "hi"}
    assert adapter.poll_once() == []


def test_discord_adapter_send_delegates():
    poster = RecordingPoster(204, "")
    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/1/a", poster=poster)
    adapter = DiscordAdapter(sender)
    adapter.send(OutboundMessage(channel="discord", recipient_id="", text="hi"))
    assert poster.calls[0][1] == {"content": "hi"}
    assert adapter.poll_once() == []


# -- LLM tools --------------------------------------------------------------


def test_register_messaging_tools_registers_both():
    registry = ToolRegistry()
    register_messaging_tools(registry)
    names = {s["name"] for s in registry.get_schemas()}
    assert {"send_slack_message", "send_discord_message"} <= names


def test_send_slack_tool_happy_path():
    registry = ToolRegistry()
    poster = RecordingPoster(200, json.dumps({"ok": True}))
    register_messaging_tools(registry, slack_sender=SlackSender(bot_token="xoxb-1", poster=poster))
    out = registry._tools["send_slack_message"]("hello", "#ops")
    assert "Sent message to Slack" in out


def test_send_discord_tool_happy_path():
    registry = ToolRegistry()
    poster = RecordingPoster(204, "")
    sender = DiscordSender(webhook_url="https://discord.com/api/webhooks/1/a", poster=poster)
    register_messaging_tools(registry, discord_sender=sender)
    out = registry._tools["send_discord_message"]("hello")
    assert "Sent message to Discord" in out


def test_send_slack_tool_not_configured(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    registry = ToolRegistry()
    register_messaging_tools(registry)  # no injected sender -> resolves from (empty) env
    out = registry._tools["send_slack_message"]("hello", "#ops")
    assert "not configured" in out


def test_send_slack_tool_reports_failure():
    registry = ToolRegistry()
    poster = RecordingPoster(200, json.dumps({"ok": False, "error": "not_in_channel"}))
    register_messaging_tools(registry, slack_sender=SlackSender(bot_token="xoxb-1", poster=poster))
    out = registry._tools["send_slack_message"]("hello", "#ops")
    assert "Slack send failed" in out
    assert "not_in_channel" in out
