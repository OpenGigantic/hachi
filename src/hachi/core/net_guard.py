"""Outbound URL safety checks (SSRF mitigation).

A shared, dependency-light guard for any place the agent (or a user-supplied
config) can cause Hachi to make an outbound request — e.g. scheduled-task
webhook delivery. Rejects non-http(s) schemes and hosts that resolve to
private / loopback / link-local / reserved addresses (cloud metadata endpoints,
internal services, localhost, etc.).

DNS-pinning (TOCTOU mitigation)
--------------------------------
:func:`validate_outbound_url` resolves the hostname once and embeds the
validated IP set in the returned :class:`ValidationResult`. Callers that
proceed to make an HTTP connection should pin the resolved IPs to the
connection (e.g. pass them to httpx via ``transport`` or connect directly
to the pinned IP) rather than letting the HTTP library re-resolve the host
name — that second resolution is the classic DNS-rebinding window.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def _ip_is_public(raw_ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        return False
    # P-04: the negative list alone is insufficient. `is_private` is a closed
    # table (RFC1918, 127/8, 169.254/16, TEST-NETs, 240/4, …) that does NOT
    # include RFC 6598 shared address space — 100.64.0.0/10, which Tailscale and
    # other overlay networks hand out. Those addresses report
    # is_private=False *and* is_global=False, so every disjunct below was False
    # and they passed the guard.
    #
    # Requiring is_global positively closes it, including the IPv4-mapped form
    # (::ffff:100.64.0.1), which a range denylist would still miss because
    # `ipaddress.ip_network("100.64.0.0/10")` membership is False for mapped
    # addresses.
    #
    # The negative list is kept deliberately: `is_global` on its own would
    # *regress* NAT64 (64:ff9b::/96), which reports is_global=True but
    # is_reserved=True and is blocked today.
    return addr.is_global and not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


@dataclass(slots=True, frozen=True)
class ValidationResult:
    """Result of :func:`validate_outbound_url`.

    Attributes:
        ok: ``True`` when the URL passed all checks.
        reason: Human-readable explanation (``"ok"`` on success).
        resolved_ips: The set of IPs the hostname resolved to during
            validation. Empty when ``ok`` is ``False`` or when
            ``allow_private=True`` was used (no DNS check is performed
            in that case). Callers SHOULD connect to one of these IPs
            rather than re-resolving the hostname to close the
            DNS-rebinding TOCTOU window.
    """

    ok: bool
    reason: str
    resolved_ips: frozenset[str] = field(default_factory=frozenset)

    def __bool__(self) -> bool:  # lets ``if result:`` work like the old ``ok`` bool
        return self.ok

    # Keep the old 2-tuple unpacking interface so existing ``ok, reason = validate_outbound_url(…)``
    # call sites continue to work without changes.
    def __iter__(self) -> object:
        yield self.ok
        yield self.reason


def validate_outbound_url(url: str, *, allow_private: bool = False) -> ValidationResult:
    """Validate an outbound URL and return a :class:`ValidationResult`.

    Rejects non-http(s) schemes (blocks ``file://``, ``gopher://`` etc.) and,
    unless ``allow_private`` is set, any host that resolves to a non-public
    address. Every resolved address must be public (defends against a hostname
    that resolves to a mix of public + private IPs).

    The ``resolved_ips`` field of the returned result carries the IPs that were
    validated. Callers that proceed to open a TCP connection SHOULD connect to one
    of these IPs directly (e.g. via an httpx custom transport) instead of letting
    the HTTP library re-resolve the hostname — that second DNS lookup is the
    DNS-rebinding TOCTOU window.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:  # pragma: no cover - urlparse rarely raises
        return ValidationResult(ok=False, reason=f"unparseable URL: {exc}")

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return ValidationResult(
            ok=False,
            reason=f"scheme '{parsed.scheme}' is not allowed (use http or https)",
        )

    host = parsed.hostname
    if not host:
        return ValidationResult(ok=False, reason="URL has no host")

    if allow_private:
        # Caller opted in to private/internal destinations; skip IP check.
        # We still don't resolve here — unknown IPs aren't pinned, which is
        # intentional: the caller is already in a controlled network context.
        return ValidationResult(ok=True, reason="ok")

    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain"}:
        return ValidationResult(ok=False, reason="localhost is not allowed")

    try:
        infos = socket.getaddrinfo(host, parsed.port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        return ValidationResult(ok=False, reason=f"DNS resolution failed: {exc}")

    # info[4] is the sockaddr tuple; index 0 is always the IP address string.
    # Explicitly cast to str so mypy sees a homogeneous set[str] (the union type
    # from getaddrinfo's overloads includes int for IPv6 scope-id in position 3,
    # but position 0 is always the address string).
    addresses: set[str] = {str(info[4][0]) for info in infos}
    if not addresses:
        return ValidationResult(ok=False, reason="host did not resolve to any address")

    for addr in addresses:
        if not _ip_is_public(addr):
            return ValidationResult(
                ok=False,
                reason=f"host resolves to a non-public address ({addr})",
            )

    return ValidationResult(ok=True, reason="ok", resolved_ips=frozenset(addresses))
