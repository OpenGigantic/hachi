"""Tests for the OpenAI-compatible facade and the generic channel inbound webhook."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tokoboros.gateway.control_plane import GatewayControlPlane
from tokoboros.gateway.fastapi_app import create_gateway_app


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
    monkeypatch.setenv("TOKOBOROS_OPENAI_RATE_LIMIT", "1")
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
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        resp = client.post("/channels/slack/inbound", json={"sender_id": "U1", "text": "ping"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "slack"
    assert body["recipient_id"] == "U1"
    assert body["reply"] == "echo:ping"


def test_channel_inbound_is_rate_limited(monkeypatch) -> None:
    monkeypatch.setenv("TOKOBOROS_CHANNEL_RATE_LIMIT", "2")
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        body = {"sender_id": "U1", "text": "hi"}
        assert client.post("/channels/slack/inbound", json=body).status_code == 200
        assert client.post("/channels/slack/inbound", json=body).status_code == 200
        assert client.post("/channels/slack/inbound", json=body).status_code == 429


def test_channel_inbound_allowlist_blocks_unknown_sender(monkeypatch) -> None:
    monkeypatch.setenv("TOKOBOROS_CHANNEL_ALLOWLIST", "slack:U1")
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
