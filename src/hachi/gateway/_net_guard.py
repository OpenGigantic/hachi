"""Gateway-layer SSRF guard — thin wrapper around :mod:`hachi.core.net_guard`.

Exposes :func:`is_safe_outbound_url` as the canonical check for any gateway
code that needs to validate a user-supplied URL before making an outbound
request (provider test-connection, voice STT/TTS base_url config, etc.).

``allow_loopback=True`` is used for local providers such as Ollama, where
connecting to ``http://localhost:11434`` is the *expected* use case.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def is_safe_outbound_url(url: str, *, allow_loopback: bool = False) -> bool:
    """Return *True* when *url* is safe to send an outbound request to.

    Checks performed:
    - Scheme must be ``http`` or ``https``.
    - Host must be present.
    - Host resolves via DNS and every resolved address passes the IP checks:
      - :meth:`~ipaddress.IPv4Address.is_private` → rejected
      - :meth:`~ipaddress.IPv4Address.is_link_local` → rejected
      - :meth:`~ipaddress.IPv4Address.is_reserved` → rejected
      - :meth:`~ipaddress.IPv4Address.is_multicast` → rejected
      - :meth:`~ipaddress.IPv4Address.is_unspecified` → rejected
      - :meth:`~ipaddress.IPv4Address.is_loopback` → rejected unless
        *allow_loopback* is ``True``

    Args:
        url: The URL to validate.
        allow_loopback: When ``True``, loopback addresses (127.x.x.x, ``::1``,
            ``localhost``) are permitted.  Use for local providers (Ollama).

    Returns:
        ``True`` when the URL is safe; ``False`` otherwise.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False

    host = parsed.hostname
    if not host:
        return False

    # Resolve the hostname to its IP(s).
    try:
        infos = socket.getaddrinfo(host, parsed.port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        return False

    addresses: set[str] = {str(info[4][0]) for info in infos}
    if not addresses:
        return False

    for raw_ip in addresses:
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False

        # When allow_loopback is True, permit loopback addresses (127.x.x.x / ::1)
        # before any other check — loopback IPs also satisfy is_private on many
        # Python versions, so the loopback exemption must come first.
        if addr.is_loopback:
            if not allow_loopback:
                return False
            continue  # allowed — skip remaining checks for this address

        # P-04: this module duplicates the core predicate rather than importing
        # it (the module docstring calls it a "thin wrapper", but it imports
        # nothing from hachi.core.net_guard), so the same gap has to be closed
        # here independently. CGNAT / overlay addresses (100.64.0.0/10 —
        # Tailscale) are neither is_private nor is_global, so every negative
        # check below passed them. Require is_global positively.
        #
        # The negative list is kept because is_global alone would regress NAT64
        # (64:ff9b::/96), which is is_global but is_reserved and is blocked
        # today. Placed after the loopback short-circuit so the documented
        # allow_loopback=True path (local Ollama) still works.
        if not addr.is_global:
            return False

        if addr.is_private:
            return False
        if addr.is_link_local:
            return False
        if addr.is_reserved:
            return False
        if addr.is_multicast:
            return False
        if addr.is_unspecified:
            return False

    return True
