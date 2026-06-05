"""Tests for outbound URL SSRF validation (uses literal IPs — no real DNS)."""

from __future__ import annotations

import pytest

from tokoboros.core.net_guard import validate_outbound_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://evil/",
        "ftp://example.com/x",
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://[::1]/admin",
        "http://0.0.0.0/",
        "not-a-url",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    ok, _reason = validate_outbound_url(url)
    assert ok is False


@pytest.mark.parametrize("url", ["http://1.1.1.1/hook", "https://8.8.8.8/notify"])
def test_public_ip_urls_are_allowed(url: str) -> None:
    ok, reason = validate_outbound_url(url)
    assert ok is True, reason


def test_allow_private_bypass() -> None:
    # Explicit opt-in (e.g. a self-hosted internal webhook) skips the IP check.
    ok, _reason = validate_outbound_url("http://10.0.0.5/internal", allow_private=True)
    assert ok is True
