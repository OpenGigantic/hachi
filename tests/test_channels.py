"""Tests for the multi-channel routing core and the Telegram adapter parsing.

The router and Telegram update-parsing are pure, so they're tested without any
network or bot credentials.
"""

from __future__ import annotations

from tokoboros.channels import ChannelRouter, InboundMessage
from tokoboros.channels.telegram import latest_offset, parse_updates


def _echo_runner(session_key: str, text: str) -> str:
    return f"[{session_key}] {text}"


# ---- ChannelRouter ----


def test_dispatch_returns_reply_and_targets_sender() -> None:
    router = ChannelRouter(_echo_runner)
    out = router.dispatch(InboundMessage(channel="telegram", sender_id="42", text="hi"))
    assert out.channel == "telegram"
    assert out.recipient_id == "42"
    assert out.text == "[telegram:42] hi"


def test_session_continuity_per_sender() -> None:
    router = ChannelRouter(_echo_runner)
    k1 = router.session_key("telegram", "42")
    k2 = router.session_key("telegram", "42")
    k3 = router.session_key("telegram", "99")
    assert k1 == k2  # same sender -> same session
    assert k1 != k3  # different sender -> different session


def test_allowlist_blocks_unknown_senders() -> None:
    router = ChannelRouter(_echo_runner, allowlist=["42"])
    allowed = router.dispatch(InboundMessage(channel="telegram", sender_id="42", text="hi"))
    blocked = router.dispatch(InboundMessage(channel="telegram", sender_id="7", text="hi"))
    assert allowed.text == "[telegram:42] hi"
    assert "not authorized" in blocked.text


def test_runner_error_becomes_friendly_reply() -> None:
    def boom(_session: str, _text: str) -> str:
        raise RuntimeError("model exploded")

    router = ChannelRouter(boom)
    out = router.dispatch(InboundMessage(channel="telegram", sender_id="42", text="hi"))
    assert "something went wrong" in out.text
    assert "model exploded" in out.text


# ---- Telegram parsing ----


def test_parse_updates_extracts_text_messages() -> None:
    updates = [
        {
            "update_id": 10,
            "message": {
                "text": "hello bot",
                "chat": {"id": 555},
                "from": {"username": "ada"},
            },
        },
        {"update_id": 11, "message": {"chat": {"id": 555}, "sticker": {}}},  # no text -> skipped
    ]
    msgs = parse_updates(updates)
    assert len(msgs) == 1
    assert msgs[0].channel == "telegram"
    assert msgs[0].sender_id == "555"
    assert msgs[0].text == "hello bot"
    assert msgs[0].sender_name == "ada"


def test_latest_offset_advances_past_last_update() -> None:
    assert latest_offset([{"update_id": 10}, {"update_id": 11}], 0) == 12
    assert latest_offset([], 5) == 5  # empty batch leaves offset unchanged


def test_end_to_end_parse_then_dispatch() -> None:
    router = ChannelRouter(_echo_runner)
    updates = [
        {
            "update_id": 1,
            "message": {"text": "ping", "chat": {"id": 1}, "from": {"first_name": "X"}},
        }
    ]
    replies = [router.dispatch(m) for m in parse_updates(updates)]
    assert replies[0].text == "[telegram:1] ping"
