"""Tests for the OpenAI-compatible facade and the generic channel inbound webhook."""

from __future__ import annotations

from conftest import control_plane as build_control_plane
from fastapi.testclient import TestClient

from hachi.gateway.control_plane import GatewayControlPlane
from hachi.gateway.fastapi_app import create_gateway_app


def _control_plane() -> GatewayControlPlane:
    return build_control_plane(on_chat=lambda _sid, prompt: f"echo:{prompt}")


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


def _control_plane_with_models(models: dict[str, list[str]]) -> GatewayControlPlane:
    """A control plane wired with a fixed list_all_models() allowlist."""
    return build_control_plane(
        on_chat=lambda _sid, prompt: f"echo:{prompt}",
        list_all_models=lambda: models,
    )


# ---------------------------------------------------------------------------
# BB-58 — requested `model` must be validated, not silently echoed/ignored
# ---------------------------------------------------------------------------


def test_openai_chat_rejects_unknown_model() -> None:
    """A model id absent from every configured provider's allowlist must be
    rejected with a clear error instead of silently falling back to a default.
    """
    cp = _control_plane_with_models({"__primary__": ["claude-haiku-4-5"]})
    app = create_gateway_app(cp)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "totally-not-a-real-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 400


def test_openai_chat_accepts_known_model() -> None:
    """A model id present in the allowlist must be accepted."""
    cp = _control_plane_with_models({"__primary__": ["claude-haiku-4-5", "gpt-4o"]})
    app = create_gateway_app(cp)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["model"] == "gpt-4o"


def test_openai_chat_no_allowlist_data_does_not_reject(monkeypatch) -> None:
    """When list_all_models is not wired (no data to validate against), any
    requested model must still be accepted — fail-open, matching every other
    'handler not attached' seam in this codebase.
    """
    app = create_gateway_app(_control_plane())
    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# BB-58 — rate limiter must be keyed per-principal, not one global bucket
# ---------------------------------------------------------------------------


def test_openai_chat_rate_limit_key_reflects_auth_state(monkeypatch) -> None:
    """The rate limiter must not use a shared, mode-blind hardcoded key.

    Before BB-58's fix, ``openai_limiter.allow("openai")`` used a single
    hardcoded literal key regardless of caller. BB-58's original fix keyed on
    the caller's bearer-token digest, described as "per-principal" — ADR-0006
    (T-61) subsequently established that framing was never accurate: Hachi is
    single-principal (exactly one bearer token per process), so that digest
    was already the same value for every authenticated request, discriminating
    only between auth states, not between callers. ``require_auth`` no longer
    returns a digest at all (it returns ``bool``); this test pins the
    surviving half of BB-58's fix — the key is still not the old hardcoded
    literal ``"openai"``, and it still differs between authenticated and
    no-auth requests.
    """
    monkeypatch.setenv("HACHI_OPENAI_RATE_LIMIT", "100")
    token_a = "token-caller-aaa"
    cp = _control_plane()

    calls: list[str] = []
    import hachi.gateway.fastapi_app as fastapi_app_module

    real_rate_limiter_cls = fastapi_app_module.RateLimiter

    class _SpyRateLimiter(real_rate_limiter_cls):
        def allow(self, key: str, now: float | None = None) -> bool:
            calls.append(key)
            return super().allow(key, now)

    monkeypatch.setattr(fastapi_app_module, "RateLimiter", _SpyRateLimiter)
    app = create_gateway_app(cp, auth_token=token_a)
    with TestClient(app) as client:
        authed = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
    assert authed.status_code == 200
    assert calls, "rate limiter was never invoked"
    assert calls[0] != "openai", "still using the pre-BB-58 hardcoded global-bucket key"
    assert calls[0] == "authenticated"

    # A no-auth-mode app (no token configured) must use a different key —
    # the two modes must not share one bucket.
    calls.clear()
    no_auth_app = create_gateway_app(_control_plane())
    with TestClient(no_auth_app) as no_auth_client:
        anon = no_auth_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert anon.status_code == 200
    assert calls[0] == "anonymous"
    assert calls[0] != "authenticated"


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


def _streaming_control_plane(*, max_active_sessions: int = 100) -> GatewayControlPlane:
    """A control plane that echoes prompts and exposes an event store."""
    from hachi.gateway.control_plane import GatewayEvent

    store: list[GatewayEvent] = []

    def on_chat(session_id: str, prompt: str) -> str:
        # Simulate text_delta events being emitted by the loop
        store.append(
            GatewayEvent(
                sequence=1,
                event_type="text.stream.delta",
                payload={"text": f"echo:{prompt}"},
                time="t",
            )
        )
        return f"echo:{prompt}"

    def read_events(session_id: str, after: int, limit: int) -> list[GatewayEvent]:
        return [e for e in store if e.sequence > after][:limit]

    return build_control_plane(
        on_chat=on_chat, read_events=read_events, max_active_sessions=max_active_sessions
    )


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


def test_openai_stream_carries_proxy_buffering_headers() -> None:
    """OpenAI-compat streaming response must disable proxy buffering (B1)."""
    app = create_gateway_app(_streaming_control_plane())
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("x-accel-buffering") == "no"
            assert resp.headers.get("cache-control") == "no-cache"
            resp.read()


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


# ---------------------------------------------------------------------------
# T-32 — the non-streaming facade must not share one "__default__" session
# across callers (BB-109). The per-session ownership check T-32 also added
# here was removed by ADR-0006 (T-61) along with the rest of that layer —
# see test_openai_facade_has_no_session_ownership_gate below.
# ---------------------------------------------------------------------------


def test_openai_facade_does_not_share_one_default_session() -> None:
    """T-32: two facade callers must not land in the same server-side session."""
    seen: list[str] = []

    def _on_chat(session_id: str, prompt: str) -> str:
        seen.append(session_id)
        return f"echo:{prompt}"

    cp = build_control_plane(on_chat=_on_chat)
    app = create_gateway_app(cp)
    with TestClient(app) as client:
        for text in ("first caller", "second caller"):
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": text}]},
            )
            assert resp.status_code == 200

    assert len(set(seen)) == 2, f"both callers shared one session: {seen}"


def test_openai_facade_has_no_session_ownership_gate() -> None:
    """ADR-0006 (T-61): the facade has no per-session ownership check to perform.

    T-32 originally added this test to prove the facade performed the same
    ownership check the other session-scoped routes did, by re-stamping a
    live session's owner to a foreign digest via ``set_session_owner`` and
    expecting 403. That machinery no longer exists. Under the single-
    principal model the bearer token IS the identity, so the same
    authenticated caller reusing a `user`-keyed conversation must always
    succeed, with nothing gating repeated access.
    """
    _TOKEN_A = "token-a"

    cp = build_control_plane(on_chat=lambda _sid, prompt: f"echo:{prompt}")
    app = create_gateway_app(cp, auth_token=_TOKEN_A)

    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {_TOKEN_A}"}
        body = {"user": "conversation-1", "messages": [{"role": "user", "content": "hi"}]}

        first = client.post("/v1/chat/completions", json=body, headers=headers)
        assert first.status_code == 200

        second = client.post("/v1/chat/completions", json=body, headers=headers)
        assert second.status_code == 200


# ---------------------------------------------------------------------------
# T-33 — the facade must close the ephemeral (per-request) sessions it mints,
# or the fixed 100-session cap fills permanently (BB-110).
# ---------------------------------------------------------------------------


def test_openai_non_streaming_closes_its_ephemeral_session() -> None:
    """T-33: a non-streaming request with no client-supplied `user` mints a
    per-request session (T-32's fallback) and must release it, exactly like
    the streaming path does.
    """
    cp = _control_plane()
    app = create_gateway_app(cp)

    with TestClient(app) as client:
        baseline = client.get("/readiness").json()["active_connections"]
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        after = client.get("/readiness").json()["active_connections"]

    assert after == baseline, f"non-streaming request leaked a session ({baseline} -> {after})"


def test_openai_streaming_closes_its_session() -> None:
    """T-33: a facade session is minted per streaming request and must be released."""
    cp = _streaming_control_plane()
    app = create_gateway_app(cp)

    with TestClient(app) as client:
        baseline = client.get("/readiness").json()["active_connections"]

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            assert response.status_code == 200
            "".join(response.iter_text())  # drain to completion so `finally:` runs

        after = client.get("/readiness").json()["active_connections"]

    assert after == baseline, f"streaming request leaked a session ({baseline} -> {after})"


def test_openai_streaming_does_not_approach_the_session_cap() -> None:
    """T-33: N sequential streaming requests must not accumulate sessions."""
    cp = _streaming_control_plane(max_active_sessions=3)
    app = create_gateway_app(cp)

    with TestClient(app) as client:
        for i in range(6):
            try:
                with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json={"stream": True, "messages": [{"role": "user", "content": f"turn {i}"}]},
                ) as response:
                    assert response.status_code == 200, f"request {i} failed — sessions leaked"
                    "".join(response.iter_text())
            except RuntimeError as exc:
                # A leaked-session cap breach raises mid-stream rather than
                # returning a clean non-200 (the HTTP response line has already
                # gone out as 200 by the time the handler discovers the cap is
                # full — Starlette cannot rewrite a status code after
                # `http.response.start` was sent). Either failure mode proves
                # the same thing: earlier requests were never released.
                raise AssertionError(
                    f"request {i} blew up once the (lowered) session cap was hit "
                    f"mid-stream — sessions from earlier requests were never closed: {exc}"
                ) from exc

        assert client.get("/readiness").json()["active_connections"] == 0


def test_openai_facade_user_keyed_session_persists_across_requests() -> None:
    """T-33: a client-supplied `user` id must keep its (non-streaming) session
    alive for continuity — only the per-request ephemeral (no-`user`) fallback
    is closed. Only the non-streaming path threads `user` into the session key
    (T-32); the streaming path always mints a fresh per-request session.
    """
    seen: list[str] = []

    def _on_chat(session_id: str, prompt: str) -> str:
        seen.append(session_id)
        return f"echo:{prompt}"

    cp = build_control_plane(on_chat=_on_chat)
    app = create_gateway_app(cp)
    body = {"user": "conversation-1", "messages": [{"role": "user", "content": "hi"}]}

    with TestClient(app) as client:
        baseline = client.get("/readiness").json()["active_connections"]
        first = client.post("/v1/chat/completions", json=body)
        assert first.status_code == 200
        mid = client.get("/readiness").json()["active_connections"]
        second = client.post("/v1/chat/completions", json=body)
        assert second.status_code == 200
        after = client.get("/readiness").json()["active_connections"]

    assert len(set(seen)) == 1, "user-keyed session was not reused across requests"
    assert mid == baseline + 1, "user-keyed session was not created"
    assert after == mid, "user-keyed session was closed instead of kept alive"
