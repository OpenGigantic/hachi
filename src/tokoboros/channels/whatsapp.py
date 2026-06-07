"""WhatsApp outbound sender + adapter.

Supports two providers (auto-detected from environment variables):

- **Meta Cloud API** (official WhatsApp Business API) — POST to
  ``https://graph.facebook.com/v18.0/{phone_number_id}/messages`` with a
  Bearer token.  Requires ``META_WHATSAPP_TOKEN`` and ``META_WHATSAPP_PHONE_ID``.

- **Twilio WhatsApp API** — POST to the Twilio Messages endpoint with HTTP
  Basic Auth.  Requires ``TWILIO_ACCOUNT_SID``, ``TWILIO_AUTH_TOKEN``, and
  ``TWILIO_WHATSAPP_FROM`` (e.g. ``whatsapp:+14155238886``).

Credentials are read from the environment at call time (never at import/boot).
The ``poster`` is injectable so tests run fully offline. Outbound URLs are
validated through ``net_guard`` (SSRF mitigation).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from typing import Any

from tokoboros.channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from tokoboros.core.net_guard import validate_outbound_url

# (url, json_payload, headers) -> (status_code, response_text)
Poster = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, str]]

# WhatsApp rejects messages longer than this; truncate so a long reply still posts.
MAX_TEXT = 4096

_META_API = "https://graph.facebook.com/v18.0/{phone_number_id}/messages"
_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"


class WhatsAppError(RuntimeError):
    """Raised when a WhatsApp send is misconfigured or rejected by the provider."""


def _clamp(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _default_poster(url: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, str]:
    import httpx

    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        # Twilio uses form-encoded bodies; Meta uses JSON — detect via Content-Type.
        if headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            resp = client.post(url, data=payload, headers=headers)
        else:
            resp = client.post(url, json=payload, headers=headers)
        return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# Pure request builders (unit-testable without a sender)
# ---------------------------------------------------------------------------


def build_meta_request(
    token: str, phone_number_id: str, to: str, text: str
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Build a Meta Cloud API send-text request (pure).

    Args:
        token: Meta WhatsApp Business bearer token.
        phone_number_id: The sender phone-number ID from Meta.
        to: Recipient phone number in E.164 format (e.g. ``"+447911123456"``).
        text: Message body.

    Returns:
        ``(url, payload, headers)`` ready to POST.
    """
    if not to:
        raise WhatsAppError("recipient phone number is required")
    url = _META_API.format(phone_number_id=phone_number_id)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+").replace(" ", ""),  # Meta expects no leading +
        "type": "text",
        "text": {"body": _clamp(text)},
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    return url, payload, headers


def build_twilio_request(
    account_sid: str, auth_token: str, from_number: str, to: str, text: str
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Build a Twilio WhatsApp send-message request (pure).

    Args:
        account_sid: Twilio Account SID.
        auth_token: Twilio Auth Token.
        from_number: Twilio WhatsApp sender (e.g. ``"whatsapp:+14155238886"``).
        to: Recipient WhatsApp number (e.g. ``"whatsapp:+447911123456"``).
        text: Message body.

    Returns:
        ``(url, payload, headers)`` ready to POST.
    """
    if not to:
        raise WhatsAppError("recipient phone number is required")
    url = _TWILIO_API.format(account_sid=account_sid)
    # Twilio uses form-encoded bodies, but we handle it as a JSON-keyed dict
    # for consistency — the sender converts to form data internally.
    payload = {
        "From": from_number if from_number.startswith("whatsapp:") else f"whatsapp:{from_number}",
        "To": to if to.startswith("whatsapp:") else f"whatsapp:{to}",
        "Body": _clamp(text),
    }
    creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return url, payload, headers


# ---------------------------------------------------------------------------
# WhatsAppSender
# ---------------------------------------------------------------------------


class WhatsAppSender:
    """Send WhatsApp messages via the Meta Cloud API or the Twilio API.

    Mirrors the :class:`~tokoboros.channels.slack.SlackSender` and
    :class:`~tokoboros.channels.discord.DiscordSender` design:

    - Credentials are resolved at call time, never at import/boot.
    - An injectable ``poster`` makes offline tests trivial.
    - Uses :func:`~tokoboros.core.net_guard.validate_outbound_url` for SSRF.
    """

    def __init__(
        self,
        *,
        # Meta credentials
        meta_token: str | None = None,
        meta_phone_number_id: str | None = None,
        # Twilio credentials
        twilio_account_sid: str | None = None,
        twilio_auth_token: str | None = None,
        twilio_from: str | None = None,
        # Shared
        poster: Poster | None = None,
        allow_private: bool = False,
    ) -> None:
        self._meta_token = meta_token or None
        self._meta_phone_id = meta_phone_number_id or None
        self._twilio_sid = twilio_account_sid or None
        self._twilio_token = twilio_auth_token or None
        self._twilio_from = twilio_from or None
        self._poster = poster or _default_poster
        self._allow_private = allow_private

    @classmethod
    def from_env(cls, poster: Poster | None = None) -> WhatsAppSender:
        """Build from environment variables (Meta first, Twilio fallback)."""
        return cls(
            meta_token=os.environ.get("META_WHATSAPP_TOKEN"),
            meta_phone_number_id=os.environ.get("META_WHATSAPP_PHONE_ID"),
            twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN"),
            twilio_from=os.environ.get("TWILIO_WHATSAPP_FROM"),
            poster=poster,
            allow_private=os.environ.get("TOKOBOROS_REST_ALLOW_PRIVATE", "").strip().lower()
            in {"1", "true", "yes"},
        )

    def is_configured(self) -> bool:
        """Return True when at least one provider has sufficient credentials."""
        meta_ok = bool(self._meta_token and self._meta_phone_id)
        twilio_ok = bool(self._twilio_sid and self._twilio_token and self._twilio_from)
        return meta_ok or twilio_ok

    @property
    def _active_provider(self) -> str:
        """Return the name of the first configured provider."""
        if self._meta_token and self._meta_phone_id:
            return "meta"
        if self._twilio_sid and self._twilio_token and self._twilio_from:
            return "twilio"
        return "none"

    def send(self, *, to: str, text: str) -> dict[str, Any]:
        """Send a WhatsApp message.  Raises :class:`WhatsAppError` on failure.

        Args:
            to: Recipient phone number (E.164 for Meta; ``whatsapp:+NNN`` or
                bare number for Twilio — the prefix is added automatically).
            text: Message body (truncated to :data:`MAX_TEXT` characters).

        Returns:
            A dict with ``provider`` and ``to``.
        """
        if not self.is_configured():
            raise WhatsAppError(
                "WhatsApp is not configured. Set META_WHATSAPP_TOKEN + "
                "META_WHATSAPP_PHONE_ID (Meta Cloud API), or "
                "TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_WHATSAPP_FROM (Twilio)."
            )

        provider = self._active_provider

        if provider == "meta":
            assert self._meta_token and self._meta_phone_id  # guarded by is_configured
            url, payload, headers = build_meta_request(
                self._meta_token, self._meta_phone_id, to, text
            )
        else:
            assert self._twilio_sid and self._twilio_token and self._twilio_from
            url, payload, headers = build_twilio_request(
                self._twilio_sid, self._twilio_token, self._twilio_from, to, text
            )

        ok, reason = validate_outbound_url(url, allow_private=self._allow_private)
        if not ok:
            raise WhatsAppError(f"blocked WhatsApp endpoint: {reason}")

        # The poster detects application/x-www-form-urlencoded and sends as form
        # data (data=payload) rather than JSON (json=payload) for Twilio.
        status, body = self._poster(url, payload, headers)

        if status not in (200, 201):
            raise WhatsAppError(
                f"WhatsApp send failed (HTTP {status}): {body[:200]}"
            )

        return {"provider": provider, "to": to}


# ---------------------------------------------------------------------------
# ChannelAdapter (send-only; inbound arrives via the generic webhook)
# ---------------------------------------------------------------------------


class WhatsAppAdapter(ChannelAdapter):
    """Outbound WhatsApp adapter for the ChannelRouter.

    Inbound messages from WhatsApp arrive through the generic
    ``POST /channels/{channel}/inbound`` webhook — no polling required.
    This adapter only handles outbound sends.
    """

    def __init__(self, sender: WhatsAppSender | None = None) -> None:
        self._sender = sender or WhatsAppSender.from_env()

    def poll_once(self) -> list[InboundMessage]:
        """Inbound handled by generic webhook; always returns empty."""
        return []

    def send(self, message: OutboundMessage) -> None:
        """Send a WhatsApp message to ``message.recipient_id``."""
        if not self._sender.is_configured():
            raise WhatsAppError(
                "WhatsApp is not configured — set META_WHATSAPP_TOKEN + "
                "META_WHATSAPP_PHONE_ID or Twilio credentials."
            )
        self._sender.send(to=message.recipient_id, text=message.text)
