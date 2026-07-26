"""Tests for the gateway SSE streaming endpoint (C2)."""

from __future__ import annotations

import asyncio

from conftest import control_plane
from fastapi import Request
from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayEvent
from hachi.gateway.fastapi_app import create_gateway_app

_EVENTS = [
    GatewayEvent(
        sequence=1, event_type="tool.call.started", payload={"name": "read_file"}, time="t"
    ),
    GatewayEvent(sequence=2, event_type="text.stream.delta", payload={"text": "hi"}, time="t"),
]


def _read_events(_session_id: str, after: int, limit: int) -> list[GatewayEvent]:
    return [e for e in _EVENTS if e.sequence > after][:limit]


def _client_with_session() -> tuple[TestClient, str]:
    cp = control_plane(read_events=_read_events)
    app = create_gateway_app(cp)
    client = TestClient(app)
    resp = client.post(
        "/sessions",
        json={
            "client_id": "c1",
            "surface": "web",
            "protocol_version": "1.0",
            "capabilities": ["events"],
        },
    )
    assert resp.status_code == 200
    return client, resp.json()["session_id"]


def test_stream_pushes_buffered_events_then_closes() -> None:
    client, session_id = _client_with_session()
    with client.stream(
        "GET",
        f"/sessions/{session_id}/stream",
        params={"poll_interval": 0, "max_idle_cycles": 1},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = "".join(response.iter_text())

    # SSE framing + both events delivered, newest cursor advanced.
    assert ": connected" in body
    assert "event: tool.call.started" in body
    assert "read_file" in body
    assert "id: 2" in body


def test_stream_resumes_after_cursor() -> None:
    client, session_id = _client_with_session()
    with client.stream(
        "GET",
        f"/sessions/{session_id}/stream",
        params={"after": 1, "poll_interval": 0, "max_idle_cycles": 1},
    ) as response:
        body = "".join(response.iter_text())

    # Only the event after sequence 1 should appear.
    assert "text.stream.delta" in body
    assert "tool.call.started" not in body


def test_stream_unknown_session_is_rejected() -> None:
    cp = control_plane(read_events=_read_events)
    app = create_gateway_app(cp)
    with TestClient(app) as client:
        response = client.get("/sessions/does-not-exist/stream", params={"max_idle_cycles": 1})
        assert response.status_code == 400


def test_stream_response_carries_proxy_buffering_headers() -> None:
    """StreamingResponse must disable proxy buffering and cache on the SSE route (B1)."""
    client, session_id = _client_with_session()
    with client.stream(
        "GET",
        f"/sessions/{session_id}/stream",
        params={"poll_interval": 0, "max_idle_cycles": 1},
    ) as response:
        assert response.status_code == 200
        assert response.headers.get("x-accel-buffering") == "no"
        assert response.headers.get("cache-control") == "no-cache"
        # Consume the body so the context manager exits cleanly.
        response.read()


def test_stream_disconnect_does_not_interrupt_other_viewers() -> None:
    """T-29: an SSE disconnect is connection-scoped; it must not cancel the session turn.

    ``TestClient.stream()`` cannot exercise this: Starlette's ``TestClient`` (and
    ``httpx.ASGITransport``, its underlying transport) runs the whole ASGI app call
    to completion inside a blocking portal call *before* handing a response back to
    the caller, and its ``receive()`` only ever reports ``http.disconnect`` after
    the response is already complete. So ``request.is_disconnected()`` can never
    observe True while the handler's generator is still producing chunks through
    that harness — a test built on it would pass before AND after the fix, for the
    wrong reason (proven: it took ~36s and exited via ``max_idle_cycles``, never via
    the disconnect branch).

    Instead we drive the route coroutine directly with a hand-built ``Request``
    whose ``receive()`` reports a live connection on the first poll (so the handler
    reads its first batch of events, standing in for "viewer A read one frame") and
    a disconnect on every poll after that (standing in for the tab closing) — the
    same ASGI signal a real server delivers on a genuine socket close.
    """
    interrupted: list[str] = []

    def _on_interrupt(session_id: str) -> dict[str, object]:
        interrupted.append(session_id)
        return {"interrupted": True}

    cp = control_plane(read_events=_read_events, on_interrupt=_on_interrupt)
    app = create_gateway_app(cp)
    client = TestClient(app)
    session_id = client.post(
        "/sessions",
        json={
            "client_id": "viewer-a",
            "surface": "web",
            "protocol_version": "1.0",
            "capabilities": ["events"],
        },
    ).json()["session_id"]

    route = next(
        r for r in app.routes if getattr(r, "path", None) == "/sessions/{session_id}/stream"
    )

    calls = {"n": 0}

    async def _receive() -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/sessions/{session_id}/stream",
        "headers": [],
        "query_string": b"",
        "client": ("test", 0),
        "server": ("test", 80),
        "scheme": "http",
        "root_path": "",
    }
    request = Request(scope, receive=_receive)

    async def _drive() -> list[str]:
        # max_idle_cycles is high so the stream does NOT close on its own — the
        # only way out of the loop is the disconnect branch under test.
        #
        # T-47: calling the raw endpoint function bypasses FastAPI's dependency
        # injection (the whole reason this harness exists -- see the docstring
        # above), so `cp` -- now an explicit Depends-injected parameter instead
        # of a closure capture -- must be supplied by hand. ADR-0006 (T-61)
        # removed the per-session ownership check this handler used to run
        # (and the `caller_hash` parameter that fed it), so there is nothing
        # else to supply here beyond what FastAPI would have resolved for
        # this no-token app.
        response = await route.endpoint(
            session_id=session_id,
            request=request,
            after=0,
            poll_interval=0.0,
            max_idle_cycles=600,
            cp=cp,
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
        return chunks

    chunks = asyncio.run(_drive())

    # Sanity: the harness really did reach the generator and stream real frames
    # before the simulated disconnect cut it off — not an early error/no-op.
    assert any(": connected" in c for c in chunks)
    assert any("tool.call.started" in c for c in chunks)

    # Viewer B is still watching this session: the turn must not have been cancelled.
    assert interrupted == [], (
        "SSE disconnect interrupted the session-global turn; it is connection-scoped"
    )


def test_session_events_response_carries_base_sequence() -> None:
    """T-30: GET /sessions/{id}/events exposes base_sequence on the wire, so a
    reconnecting client can detect it read past a trim boundary instead of
    silently rendering a stream with a hole in it.

    Client-side gap rule (docs/audit/2026-07-19/tasks/T-30-sse-gap-signal.md §5):
    a gap occurred iff ``base_sequence > after + 1``. Driven through the real ASGI
    app with a hand-wired GatewayControlPlane (the pattern at :22-36 above), so
    this proves the value survives the hop from the runtime accessor into the
    actual JSON response body — not just that an internal counter clamped.
    """
    cp = control_plane(read_events=_read_events, read_events_base=lambda _session_id: 5)
    app = create_gateway_app(cp)
    with TestClient(app) as client:
        session_id = client.post(
            "/sessions",
            json={
                "client_id": "c-gap",
                "surface": "web",
                "protocol_version": "1.0",
                "capabilities": ["events"],
            },
        ).json()["session_id"]

        # A stale client reconnects with a cursor that predates the trim boundary
        # (base_sequence=5): it must be TOLD a gap occurred, not left to infer it.
        stale = client.get(f"/sessions/{session_id}/events", params={"after": 0, "limit": 100})
        assert stale.status_code == 200
        stale_body = stale.json()
        assert stale_body["base_sequence"] == 5
        assert stale_body["base_sequence"] > 0 + 1, "gap must be detectable on the wire"

        # A client whose cursor is already within bounds must NOT get a false gap.
        fresh = client.get(f"/sessions/{session_id}/events", params={"after": 10, "limit": 100})
        assert fresh.status_code == 200
        fresh_body = fresh.json()
        assert fresh_body["base_sequence"] == 5
        assert not (fresh_body["base_sequence"] > 10 + 1), "no false gap for an in-bounds cursor"
