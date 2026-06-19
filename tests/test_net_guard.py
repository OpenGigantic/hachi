"""Tests for outbound URL SSRF validation (uses literal IPs — no real DNS)."""

from __future__ import annotations

import pytest

from hachi.core.net_guard import ValidationResult, validate_outbound_url


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


# ---- HIGH-001: ValidationResult carries resolved IPs for DNS pinning ----


def test_returns_validation_result_instance() -> None:
    """validate_outbound_url always returns a ValidationResult."""
    result = validate_outbound_url("http://1.1.1.1/hook")
    assert isinstance(result, ValidationResult)


def test_successful_validation_carries_resolved_ips() -> None:
    """A passing validation for a literal IP URL exposes that IP in resolved_ips."""
    result = validate_outbound_url("http://1.1.1.1/hook")
    assert result.ok is True
    # 1.1.1.1 is a literal IP — getaddrinfo returns it directly.
    assert "1.1.1.1" in result.resolved_ips


def test_failed_validation_has_empty_resolved_ips() -> None:
    result = validate_outbound_url("http://127.0.0.1/admin")
    assert result.ok is False
    assert len(result.resolved_ips) == 0


def test_allow_private_has_empty_resolved_ips() -> None:
    """allow_private skips DNS — no IPs to pin, callers handle their own context."""
    result = validate_outbound_url("http://10.0.0.5/internal", allow_private=True)
    assert result.ok is True
    assert len(result.resolved_ips) == 0


def test_two_tuple_unpacking_still_works() -> None:
    """Legacy ``ok, reason = validate_outbound_url(…)`` call sites keep working."""
    ok, reason = validate_outbound_url("http://127.0.0.1/")
    assert ok is False
    assert "non-public" in reason or "localhost" in reason or "not allowed" in reason

    ok2, reason2 = validate_outbound_url("http://1.1.1.1/")
    assert ok2 is True
    assert reason2 == "ok"
