from __future__ import annotations

import threading
import time

import pytest

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
    1.0s floor that MCPServerConnection clamps to (`_clamp_request_timeout_seconds`
    / `_MIN_REQUEST_TIMEOUT_SECONDS` in mcp_client.py).

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


def test_sub_floor_request_timeout_reaches_retry_policy_clamped() -> None:
    """A sub-floor ``request_timeout_seconds`` must reach ``RetryPolicy.timeout_s``
    as the CLAMPED number, not the raw one.

    BB-176: ``MCPServerConnection.__init__`` already clamped its own
    ``_request_timeout_seconds`` (the value the transport actually uses — see the
    two tests above) to the 1.0s floor, but passed the RAW, un-clamped
    ``request_timeout_seconds`` *parameter* straight into
    ``RetryPolicy(timeout_s=...)`` — one constructor deriving two numbers from one
    config value and clamping only one of them.

    ``RetryPolicy.timeout_s`` is inert on the MCP path today: ``call_tool`` drives
    ``resilient_call(fn, self._retry_policy, self._health_recorder, ...)``
    (mcp_client.py) and ``resilient_call``'s body (core/resilience.py) reads only
    ``policy.max_attempts`` / ``policy.backoff_base_s`` directly, plus
    ``policy.breaker_threshold`` / ``policy.breaker_cooldown_s`` indirectly via
    ``HealthRecorder`` — never ``policy.timeout_s``. The only reader of
    ``RetryPolicy.timeout_s`` anywhere in ``hachi`` is
    ``core/routing/router.py`` (``ModelRouter``'s OWN, unrelated
    ``[routing.resilience]``-sourced policy, built from ``ResilienceSettings`` in
    ``core/settings.py`` — never touched by mcp_client.py). So this is a latent
    trap that would fire the day ``timeout_s`` becomes enforced on this path, not
    a live behavioural bug — but a single config value should still yield a
    single number everywhere it is read, so it is fixed here regardless.
    """
    conn = MCPServerConnection("silent", "python", request_timeout_seconds=0.2)

    assert conn._request_timeout_seconds == 1.0
    assert conn._retry_policy.timeout_s == 1.0, (
        "RetryPolicy.timeout_s must be the CLAMPED value, not the raw one — got "
        f"{conn._retry_policy.timeout_s!r} for request_timeout_seconds=0.2"
    )


def test_sub_floor_request_timeout_warns_naming_server_and_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Clamping a sub-floor ``request_timeout_seconds`` must be logged — once —
    naming the server and both the raw and clamped values.

    BB-176: ``[mcp_servers.NAME]`` TOML (``MCPServerConfig.from_toml_entry``) and
    an ``.og`` ``role=mcp`` node's ``::config`` both accept any float for
    ``request_timeout_seconds`` with no validation; a value below the 1.0s
    runtime floor used to be silently overridden with no signal anywhere — not a
    warning, not the docstring, nothing — that the configured value was not what
    actually ran. (``MCPServerConfig.to_toml_entry`` also round-trips the
    unclamped value back to ``settings.toml`` verbatim, so the file can say ``0.5``
    forever; that is deliberately NOT changed by this fix — config stays the
    user's stated intent, so the honesty fix belongs at runtime, not in the file.)

    Exactly ONE warning, not two: ``MCPServerConnection.__init__`` clamps first
    and ``_make_transport`` passes the ALREADY-clamped
    ``self._request_timeout_seconds`` down to the transport constructor, so the
    transport's own clamp must see a value at the floor and stay silent — a naive
    fix (clamp-and-warn independently in all four constructors) would double-warn
    on this, the common, construction path.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="hachi.core.mcp_client"):
        MCPServerConnection("silent", "python", request_timeout_seconds=0.5)

    warnings = [
        r
        for r in caplog.records
        if "request_timeout_seconds" in r.message and "silent" in r.message
    ]
    assert len(warnings) == 1, (
        f"expected exactly one clamp warning, got {len(warnings)}: "
        f"{[r.message for r in caplog.records]}"
    )
    message = warnings[0].message
    assert "silent" in message, f"warning does not name the server: {message!r}"
    assert "0.5" in message, f"warning does not name the raw value: {message!r}"
    assert "1.0" in message, f"warning does not name the clamped value: {message!r}"


def test_at_floor_request_timeout_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """A ``request_timeout_seconds`` AT the floor must not warn — only a value
    genuinely BELOW the floor is a clamp.

    Guards against a cheap, wrong "fix" for BB-176 that logs on every
    construction regardless of whether clamping actually happened.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="hachi.core.mcp_client"):
        MCPServerConnection("silent", "python", request_timeout_seconds=1.0)

    warnings = [r for r in caplog.records if "request_timeout_seconds" in r.message]
    assert warnings == [], (
        f"a value at the floor must not warn, got: {[r.message for r in warnings]}"
    )
