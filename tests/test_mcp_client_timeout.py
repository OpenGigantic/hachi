from __future__ import annotations

import threading
import time

from hachi.core.mcp_client import MCPServerConnection


class _FakeStdin:
    def write(self, _payload: bytes) -> None:
        return None

    def flush(self) -> None:
        return None


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = object()

    def poll(self):  # pragma: no cover - used for interface compatibility only
        return None


def test_send_request_times_out_without_stdout_responses() -> None:
    """When nothing ever arrives on stdout, _send_request() must give up and
    return None once request_timeout_seconds has genuinely elapsed — not
    before, and not hang forever.

    T-71: `request_timeout_seconds=1.0` makes `_send_request()` genuinely wait
    for about a second — a real `threading.Condition.wait_for(timeout=...)`
    (see StdioTransport.receive in mcp_client.py), an OS-level blocking wait
    with no injectable clock to swap in.

    UPPER bound (not hanging forever): a tight `elapsed < 2.0` (the original
    2x margin) starves under `-n auto`, so this is now a generous deadman-
    switch join instead — it only catches an infinite wait.

    LOWER bound (`elapsed >= 1.0`, below): kept as a real wall-clock
    assertion, not a proxy, because it is parallel-safe by construction —
    xdist starvation only ever makes things slower, never faster, so this
    cannot spuriously fail under load the way an upper bound can. It is also
    the assertion that actually catches the regression this test exists for:
    a deadline that still fires, but at the wrong value (e.g. a hardcoded
    timeout that silently ignores `request_timeout_seconds`) — the deadman
    switch alone would not notice that. Do NOT "helpfully" drop this or flip
    its comparison direction to a LESS-than bound — the hygiene test's
    PATTERN (tests/test_test_suite_hygiene.py) flags an `assert` on
    `elapsed`/`wall_time` only when it is immediately followed by a
    less-than comparison, so this greater-or-equal lower bound is
    deliberately exempt and needs no KNOWN_WALL_CLOCK entry.
    """
    conn = MCPServerConnection("silent", "python", request_timeout_seconds=1.0)
    conn._process = _FakeProcess()  # type: ignore[assignment]

    result: list[dict | None] = []
    elapsed = 0.0

    def _call() -> None:
        nonlocal elapsed
        started = time.monotonic()
        result.append(conn._send_request("tools/list", {}))
        elapsed = time.monotonic() - started

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=10.0)

    assert not t.is_alive(), (
        "_send_request() did not return within a generous bound — "
        "the request_timeout_seconds deadline may not be firing"
    )
    assert result == [None]
    assert elapsed >= 1.0, (
        "returned before request_timeout_seconds elapsed — is the deadline "
        f"wired to the configured value? elapsed={elapsed:.3f}s"
    )


def test_send_request_passes_the_configured_timeout_to_the_transport() -> None:
    """The configured request_timeout_seconds must reach Transport.receive()'s
    explicit `timeout_seconds` parameter — not a default, and not merely the
    1.0s floor that MCPServerConnection clamps to (`max(1.0, ...)`).

    T-71: `Transport.receive(request_id, timeout_seconds)` takes the timeout
    as an explicit parameter (see the `MCPTransport` ABC in mcp_client.py), so
    this is directly observable with a spy — no clock, no wall-clock bound, no
    wait at all.

    request_timeout_seconds=2.5 is deliberately off the floor: the sibling
    `test_send_request_times_out_without_stdout_responses` configures exactly
    1.0, so on its own it cannot distinguish "the configured value reached
    the transport" from "the code ignores config and always uses the floor".
    2.5 can only appear here if the value genuinely round-tripped through
    MCPServerConnection.__init__ and _send_request.
    """
    conn = MCPServerConnection("silent", "python", request_timeout_seconds=2.5)
    conn._process = _FakeProcess()  # type: ignore[assignment]

    recorded: list[float] = []

    def _spy_receive(request_id, timeout_seconds):  # noqa: ARG001
        recorded.append(timeout_seconds)
        return None  # canned response — the spy never blocks, no real wait

    conn._transport.receive = _spy_receive  # type: ignore[method-assign]

    conn._send_request("tools/list", {})

    assert recorded == [2.5], (
        "request_timeout_seconds=2.5 did not reach Transport.receive() — "
        f"recorded timeout_seconds={recorded}"
    )
