"""Tests for the OpenAI-compatible facade and the generic channel inbound webhook."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app


def _control_plane() -> GatewayControlPlane:
    return GatewayControlPlane(on_chat=lambda _sid, prompt: f"echo:{prompt}")


def test_openai_chat_completions_returns_openai_shape() -> None:
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "echo:hello"}
    assert body["choices"][0]["finish_reason"] == "stop"


def test_openai_chat_handles_content_blocks() -> None:
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        blocks = [{"type": "text", "text": "hi there"}]
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": blocks}]},
        )
    assert resp.json()["choices"][0]["message"]["content"] == "echo:hi there"


def test_openai_chat_requires_user_content() -> None:
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "system", "content": "you are helpful"}]},
        )
    assert resp.status_code == 400


def test_openai_chat_is_rate_limited(monkeypatch) -> None:
    monkeypatch.setenv("HACHI_OPENAI_RATE_LIMIT", "1")
    app = create_gateway_app(_control_plane())
    body = {"messages": [{"role": "user", "content": "hi"}]}
    with TestClient(app) as client:
        first = client.post("/v1/chat/completions", json=body)
        second = client.post("/v1/chat/completions", json=body)
    assert first.status_code == 200
    assert second.status_code == 429


def test_openai_models_list() -> None:
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1


def test_channel_inbound_routes_to_agent() -> None:
    # H10: inbound text is now wrapped in guard_untrusted before dispatch, so the
    # echo reply contains the data envelope, not the raw text.
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        resp = client.post("/channels/slack/inbound", json={"sender_id": "U1", "text": "ping"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "slack"
    assert body["recipient_id"] == "U1"
    # The reply reflects the guarded text that reached on_chat; it must contain
    # the original message somewhere inside the envelope.
    assert "ping" in body["reply"]


def test_channel_inbound_is_rate_limited(monkeypatch) -> None:
    monkeypatch.setenv("HACHI_CHANNEL_RATE_LIMIT", "2")
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        body = {"sender_id": "U1", "text": "hi"}
        assert client.post("/channels/slack/inbound", json=body).status_code == 200
        assert client.post("/channels/slack/inbound", json=body).status_code == 200
        assert client.post("/channels/slack/inbound", json=body).status_code == 429


def test_channel_inbound_allowlist_blocks_unknown_sender(monkeypatch) -> None:
    monkeypatch.setenv("HACHI_CHANNEL_ALLOWLIST", "slack:U1")
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        allowed = client.post("/channels/slack/inbound", json={"sender_id": "U1", "text": "hi"})
        blocked = client.post("/channels/slack/inbound", json={"sender_id": "U2", "text": "hi"})
    assert allowed.status_code == 200
    assert blocked.status_code == 403


def test_complete_chat_reuses_session_per_key() -> None:
    cp = _control_plane()
    first = cp.complete_chat("a", session_key="k1")
    second = cp.complete_chat("b", session_key="k1")
    assert first["session_id"] == second["session_id"]  # same key -> same session
    other = cp.complete_chat("c", session_key="k2")
    assert other["session_id"] != first["session_id"]  # different key -> different session


def test_get_or_create_session_returns_stable_id() -> None:
    """get_or_create_session returns the same id for a given key and a new one for a new key."""
    cp = _control_plane()
    id1 = cp.get_or_create_session("alpha")
    id2 = cp.get_or_create_session("alpha")
    assert id1 == id2
    id3 = cp.get_or_create_session("beta")
    assert id3 != id1


# ---------------------------------------------------------------------------
# OpenAI streaming (P2-01d)
# ---------------------------------------------------------------------------


def _streaming_control_plane() -> GatewayControlPlane:
    """A control plane that echoes prompts and exposes an event store."""
    from hachi.gateway.control_plane import GatewayEvent

    store: list[GatewayEvent] = []

    def on_chat(session_id: str, prompt: str) -> str:
        # Simulate text_delta events being emitted by the loop
        store.append(
            GatewayEvent(
                sequence=1, event_type="text.stream.delta",
                payload={"text": f"echo:{prompt}"}, time="t",
            )
        )
        return f"echo:{prompt}"

    def read_events(session_id: str, after: int, limit: int) -> list[GatewayEvent]:
        return [e for e in store if e.sequence > after][:limit]

    return GatewayControlPlane(on_chat=on_chat, read_events=read_events)


def test_openai_stream_returns_event_stream_content_type() -> None:
    """stream=True must return media type text/event-stream."""
    app = create_gateway_app(_streaming_control_plane())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            body = "".join(resp.iter_text())

    assert "data: [DONE]" in body
    assert "chat.completion.chunk" in body
    assert "finish_reason" in body


def test_openai_stream_includes_role_chunk() -> None:
    """The first SSE data chunk must carry ``role: assistant``."""
    app = create_gateway_app(_streaming_control_plane())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
        ) as resp:
            lines = list(resp.iter_lines())

    data_lines = [ln for ln in lines if ln.startswith("data: ") and ln != "data: [DONE]"]
    assert data_lines, "no data lines in SSE stream"
    import json as _json
    first_chunk = _json.loads(data_lines[0][6:])
    assert first_chunk["choices"][0]["delta"].get("role") == "assistant"


def test_openai_stream_forwards_text_delta_events() -> None:
    """text.stream.delta events in the session's event store appear as content deltas."""
    app = create_gateway_app(_streaming_control_plane())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "ping"}], "stream": True},
        ) as resp:
            body = "".join(resp.iter_text())

    assert "echo:ping" in body


def test_openai_non_stream_unchanged() -> None:
    """When stream is False or absent, the response is the standard JSON shape."""
    app = create_gateway_app(_streaming_control_plane())
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "echo:hi"
