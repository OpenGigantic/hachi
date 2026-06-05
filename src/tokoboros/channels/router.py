"""Transport-agnostic routing from inbound channel messages to agent replies."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from tokoboros.channels.base import InboundMessage, OutboundMessage, SessionRunner

logger = logging.getLogger(__name__)


class ChannelRouter:
    """Route inbound messages to per-sender agent sessions and produce replies.

    Responsibilities:
      * **Session continuity** — every distinct ``(channel, sender_id)`` maps to a
        stable session key so a user's conversation has memory across messages.
      * **Access control** — an optional allowlist restricts who may talk to the
        agent (important: a chat bot is an open door otherwise).
      * **Isolation of failures** — an error from the agent becomes a friendly
        reply rather than crashing the adapter's receive loop.
    """

    def __init__(self, runner: SessionRunner, allowlist: Iterable[str] | None = None):
        self._runner = runner
        self._allowlist: set[str] | None = {str(s) for s in allowlist} if allowlist else None
        self._sessions: dict[tuple[str, str], str] = {}

    def session_key(self, channel: str, sender_id: str) -> str:
        """Return (creating if needed) the stable session key for a sender."""
        key = (channel, sender_id)
        if key not in self._sessions:
            self._sessions[key] = f"{channel}:{sender_id}"
        return self._sessions[key]

    def is_allowed(self, sender_id: str) -> bool:
        return self._allowlist is None or str(sender_id) in self._allowlist

    def dispatch(self, message: InboundMessage) -> OutboundMessage:
        """Handle one inbound message and return the reply to send back."""
        if not self.is_allowed(message.sender_id):
            logger.info("Blocked message from unauthorized sender %s", message.sender_id)
            return OutboundMessage(
                channel=message.channel,
                recipient_id=message.sender_id,
                text="Sorry — you're not authorized to use this assistant.",
            )

        session = self.session_key(message.channel, message.sender_id)
        try:
            reply = self._runner(session, message.text)
        except Exception as e:  # never let one bad turn kill the receive loop
            logger.exception("Agent run failed for %s", session)
            reply = f"⚠️ Sorry, something went wrong handling your message: {e}"

        return OutboundMessage(
            channel=message.channel,
            recipient_id=message.sender_id,
            text=reply,
        )
