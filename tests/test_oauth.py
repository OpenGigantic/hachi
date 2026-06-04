from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from tokoboros.core.oauth import OAuthCallbackHandler, OAuthManager


class _MemorySecretStore:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._items.get(name)

    def set(self, name: str, value: str, metadata=None) -> None:
        self._items[name] = value

    def delete(self, name: str) -> None:
        self._items.pop(name, None)

    def list_metadata(self):
        return []

    def healthcheck(self):
        return {"status": "ok"}


def test_google_oauth_requires_client_id(monkeypatch) -> None:
    monkeypatch.setattr("tokoboros.core.oauth.GOOGLE_CLIENT_ID", None)

    with pytest.raises(RuntimeError, match="Missing GOOGLE_CLIENT_ID"):
        OAuthManager._require_google_client_id()


def test_oauth_callback_rejects_state_mismatch() -> None:
    handler = OAuthCallbackHandler.__new__(OAuthCallbackHandler)
    handler.path = "/?code=abc123&state=unexpected"
    handler.server = SimpleNamespace(oauth_state="expected", auth_code="should-be-cleared")
    handler.wfile = io.BytesIO()
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    OAuthCallbackHandler.do_GET(handler)

    assert handler.server.auth_code is None
    body = handler.wfile.getvalue().decode("utf-8")
    assert "State validation failed" in body


def test_google_browser_flow_fails_when_callback_port_8080_is_in_use(monkeypatch) -> None:
    monkeypatch.setattr("tokoboros.core.oauth.GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        "tokoboros.core.oauth.HTTPServer",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("Address already in use")),
    )

    manager = OAuthManager(secret_store=_MemorySecretStore())

    with pytest.raises(OSError, match="Address already in use"):
        manager._run_google_browser_flow(["openid"])


def test_google_browser_flow_fails_closed_when_consent_is_cancelled(monkeypatch) -> None:
    class _FakeServer:
        def __init__(self, *_args, **_kwargs) -> None:
            self.auth_code = None
            self.oauth_state = "test-state"
            self.server_address = ("127.0.0.1", 0)
            self.timeout = None

        def handle_request(self) -> None:
            self.auth_code = None

    monkeypatch.setattr("tokoboros.core.oauth.GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr("tokoboros.core.oauth.HTTPServer", _FakeServer)
    monkeypatch.setattr("tokoboros.core.oauth.webbrowser.open", lambda *_a, **_k: True)

    manager = OAuthManager(secret_store=_MemorySecretStore())

    with pytest.raises(Exception, match="Authentication failed or was cancelled"):
        manager._run_google_browser_flow(["openid", "email"])


def test_google_token_refresh_failure_propagates(monkeypatch) -> None:
    class _FailingResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError("refresh failed")

        def json(self):
            return {}

    monkeypatch.setattr("tokoboros.core.oauth.GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr("tokoboros.core.oauth.httpx.post", lambda *_a, **_k: _FailingResponse())

    manager = OAuthManager(secret_store=_MemorySecretStore())

    with pytest.raises(RuntimeError, match="refresh failed"):
        manager._refresh_google_token({"refresh_token": "r1", "scope": "openid"})
