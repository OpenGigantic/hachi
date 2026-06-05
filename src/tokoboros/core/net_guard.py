"""Outbound URL safety checks (SSRF mitigation).

A shared, dependency-light guard for any place the agent (or a user-supplied
config) can cause Tokoboros to make an outbound request — e.g. scheduled-task
webhook delivery. Rejects non-http(s) schemes and hosts that resolve to
private / loopback / link-local / reserved addresses (cloud metadata endpoints,
internal services, localhost, etc.).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def _ip_is_public(raw_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_outbound_url(url: str, *, allow_private: bool = False) -> tuple[bool, str]:
    """Validate an outbound URL. Returns ``(ok, reason)``.

    Rejects non-http(s) schemes (blocks ``file://``, ``gopher://`` etc.) and,
    unless ``allow_private`` is set, any host that resolves to a non-public
    address. Every resolved address must be public (defends against a hostname
    that resolves to a mix of public + private IPs).
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:  # pragma: no cover - urlparse rarely raises
        return False, f"unparseable URL: {exc}"

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"scheme '{parsed.scheme}' is not allowed (use http or https)"

    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    if allow_private:
        return True, "ok"

    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain"}:
        return False, "localhost is not allowed"

    try:
        infos = socket.getaddrinfo(host, parsed.port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        return False, f"DNS resolution failed: {exc}"

    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False, "host did not resolve to any address"

    for addr in addresses:
        if not _ip_is_public(addr):
            return False, f"host resolves to a non-public address ({addr})"

    return True, "ok"
