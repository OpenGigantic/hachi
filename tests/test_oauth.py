from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from hachi.core.oauth import OAuthCallbackHandler, OAuthManager, _migrate_legacy_oauth_key


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
    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: None)

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
    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: "client-id")
    monkeypatch.setattr("hachi.core.oauth._google_client_secret", lambda: None)
    monkeypatch.delenv("HACHI_OAUTH_NONINTERACTIVE", raising=False)  # hermetic vs flag pollution
    monkeypatch.setattr(
        "hachi.core.oauth.HTTPServer",
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

    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: "client-id")
    monkeypatch.setattr("hachi.core.oauth._google_client_secret", lambda: None)
    monkeypatch.delenv("HACHI_OAUTH_NONINTERACTIVE", raising=False)  # hermetic vs flag pollution
    monkeypatch.setattr("hachi.core.oauth.HTTPServer", _FakeServer)
    monkeypatch.setattr("hachi.core.oauth.webbrowser.open", lambda *_a, **_k: True)

    manager = OAuthManager(secret_store=_MemorySecretStore())

    with pytest.raises(Exception, match="Authentication failed or was cancelled"):
        manager._run_google_browser_flow(["openid", "email"])


def test_google_token_refresh_failure_propagates(monkeypatch) -> None:
    class _FailingResponse:
        status_code = 400
        is_success = False
        text = '{"error": "invalid_grant"}'

        def json(self):
            return {"error": "invalid_grant", "error_description": "Token has been expired"}

    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: "client-id")
    monkeypatch.setattr("hachi.core.oauth._google_client_secret", lambda: None)
    monkeypatch.setattr("hachi.core.oauth.httpx.post", lambda *_a, **_k: _FailingResponse())

    manager = OAuthManager(secret_store=_MemorySecretStore())

    # The real Google error (invalid_grant) is now surfaced instead of a generic 400.
    with pytest.raises(RuntimeError, match="invalid_grant: Token has been expired"):
        manager._refresh_google_token({"refresh_token": "r1", "scope": "openid"})


# ---------------------------------------------------------------------------
# Lazy credential resolution (regression): a value configured AFTER import, or
# living in a project .env, must be picked up — the module-level capture didn't.
# ---------------------------------------------------------------------------


def test_client_id_set_after_import_is_resolved(monkeypatch) -> None:
    # The original bug: GOOGLE_CLIENT_ID was captured once at import, so a value set
    # later (e.g. .env merged at CLI startup, or a different import order) was never seen
    # and every Google tool failed "Missing GOOGLE_CLIENT_ID". Resolution is now lazy.
    from hachi.core import oauth

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "configured-late-123")
    assert oauth._google_client_id() == "configured-late-123"
    assert OAuthManager._require_google_client_id() == "configured-late-123"


def test_env_var_takes_precedence_over_dotenv(monkeypatch, tmp_path) -> None:
    from hachi.core import oauth

    (tmp_path / ".env").write_text("GOOGLE_CLIENT_ID=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "from-environ")
    assert oauth._resolve_env("GOOGLE_CLIENT_ID") == "from-environ"


def test_resolve_falls_back_to_project_dotenv(monkeypatch, tmp_path) -> None:
    from hachi.core import oauth

    # Not in the process environment, but present in the project .env → still resolved,
    # provided autoload isn't disabled. tests/conftest.py sets
    # HACHI_DISABLE_DOTENV_AUTOLOAD=1 for the whole suite (BB-2 hermeticity), so this
    # test — which is specifically exercising the enabled-fallback path — must opt back
    # in explicitly, exactly like tests/test_dotenv_hermeticity.py's
    # test_autoload_active_when_flag_zero does.
    monkeypatch.delenv("HACHI_TEST_ONLY_KEY", raising=False)
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "0")
    (tmp_path / ".env").write_text(
        '# a comment\nHACHI_TEST_ONLY_KEY="dotenv-value"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert oauth._resolve_env("HACHI_TEST_ONLY_KEY") == "dotenv-value"


def test_read_dotenv_value_ignores_comments_and_missing(tmp_path) -> None:
    from hachi.core import oauth

    p = tmp_path / ".env"
    p.write_text("# header\n\nFOO=bar\nBAZ = qux \n", encoding="utf-8")
    assert oauth._read_dotenv_value(p, "FOO") == "bar"
    assert oauth._read_dotenv_value(p, "BAZ") == "qux"
    assert oauth._read_dotenv_value(p, "MISSING") is None
    assert oauth._read_dotenv_value(tmp_path / "nope.env", "FOO") is None


def test_browser_flow_fails_fast_when_noninteractive(monkeypatch) -> None:
    # In a headless server (gateway/serve sets HACHI_OAUTH_NONINTERACTIVE), a first-time
    # Google auth must raise a clear "run hachi auth google" error immediately — never open
    # a browser or block on a local callback server.
    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: "client-id")
    monkeypatch.setenv("HACHI_OAUTH_NONINTERACTIVE", "1")
    monkeypatch.setattr(
        "hachi.core.oauth.HTTPServer",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("server must not start")),
    )
    monkeypatch.setattr(
        "hachi.core.oauth.webbrowser.open",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("browser must not open")),
    )
    manager = OAuthManager(secret_store=_MemorySecretStore())
    with pytest.raises(RuntimeError, match="hachi google-auth"):
        manager._run_google_browser_flow(["openid"])


# ---------------------------------------------------------------------------
# BB-10 regressions: web-UI token key mismatch + wrong CLI command in error
# ---------------------------------------------------------------------------


def test_get_token_finds_web_ui_oauth2_prefix(monkeypatch) -> None:
    """OAuthManager.get_token("google") must find tokens stored by the web UI
    under the "oauth2." prefix, not only those stored under "oauth." prefix.

    Root cause: web UI writes "oauth2.google"; agent reads with _key() which
    returns "oauth.google" — the token was never found, forcing re-auth on
    every agent turn even after the user had authenticated via the web UI.
    """
    store = _MemorySecretStore()
    # Simulate what the web UI (oauth_flow.py, _TOKEN_KEY_PREFIX="oauth2.") stores:
    import json

    token_data = {"access_token": "ya29.web-ui-token", "scope": "openid email"}
    store.set("oauth2.google", json.dumps(token_data))

    manager = OAuthManager(secret_store=store)
    # Under the bug, this returned None; with the fix it returns the token.
    result = manager.get_token("google")
    assert result is not None, (
        "get_token('google') returned None even though the token was stored under "
        "'oauth2.google' (the web-UI key prefix). The agent must fall back to the "
        "'oauth2.' prefix when nothing is stored under 'oauth.'."
    )
    assert result["access_token"] == "ya29.web-ui-token"


def test_refresh_writes_back_to_the_key_it_read_from(monkeypatch) -> None:
    """A refresh must update the key the token was loaded from.

    Regression: get_token() fell back to the web-UI prefix "oauth2.<p>" (BB-10)
    but save_token() always wrote "oauth.<p>". After the first silent refresh of a
    web-UI-authenticated token the two vault keys held two different tokens with
    two different expiries, and nothing ever reconciled them (BB-157).
    """
    import json
    import time

    store = _MemorySecretStore()
    stale = {
        "access_token": "ya29.web-ui-token",
        "refresh_token": "1//refresh",
        "scope": "openid email",
        "expires_at": time.time() - 3600,  # well past the 60s skew buffer
    }
    store.set("oauth2.google", json.dumps(stale))

    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: "cid")
    monkeypatch.setattr("hachi.core.oauth._google_client_secret", lambda: None)
    monkeypatch.setattr(
        OAuthManager,
        "_exchange_token",
        staticmethod(lambda payload: {"access_token": "ya29.refreshed", "expires_in": 3600}),
    )

    manager = OAuthManager(secret_store=store)
    token = manager.get_google_credentials(["openid", "email"])
    assert token == "ya29.refreshed"

    refreshed = json.loads(store.get("oauth2.google") or "{}")
    assert refreshed.get("access_token") == "ya29.refreshed", (
        "token under 'oauth2.google' is stale: the refresh wrote to a different key, "
        "forking credential state between the agent and the web UI."
    )
    # And it must not have created a second, divergent copy.
    assert store.get("oauth.google") is None, (
        "refresh created a second copy under 'oauth.google'; one grant must live "
        "under exactly one vault key."
    )


def test_clearing_a_token_updates_the_key_that_actually_holds_it() -> None:
    """Characterizes `--revoke` (cli/main.py's `oauth.save_token("google", {})`).

    Before the _resolve_key fix, save_token() always wrote "oauth.<p>" regardless of
    where the live grant lived, so revoking a web-UI-authenticated token wrote "{}" to
    "oauth.google" while the real token stayed live at "oauth2.google" — the empty
    payload *shadowed* the grant instead of clearing it (a truthy "{}" string blocks
    the BB-10 fallback in get_token). After the fix, save_token() resolves the same
    key get_token() would read, so the clear lands on the key that actually holds the
    grant and no second key is created.
    """
    import json

    store = _MemorySecretStore()
    store.set("oauth2.google", json.dumps({"access_token": "ya29.web-ui-token"}))

    manager = OAuthManager(secret_store=store)
    manager.save_token("google", {})

    assert store.get("oauth2.google") == "{}"
    assert store.get("oauth.google") is None


def test_noninteractive_error_mentions_correct_cli_command(monkeypatch) -> None:
    """When HACHI_OAUTH_NONINTERACTIVE=1 and no saved token exists, the error
    message must direct the user to `hachi google-auth`, not `hachi auth google`
    (which does not exist as a CLI command).
    """
    monkeypatch.setattr("hachi.core.oauth._google_client_id", lambda: "client-id")
    monkeypatch.setenv("HACHI_OAUTH_NONINTERACTIVE", "1")
    monkeypatch.setattr(
        "hachi.core.oauth.HTTPServer",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("server must not start")),
    )
    monkeypatch.setattr(
        "hachi.core.oauth.webbrowser.open",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("browser must not open")),
    )
    manager = OAuthManager(secret_store=_MemorySecretStore())
    with pytest.raises(RuntimeError) as exc_info:
        manager._run_google_browser_flow(["openid"])
    msg = str(exc_info.value)
    assert "hachi google-auth" in msg, (
        f"Error message should reference 'hachi google-auth' (the real CLI command) "
        f"but got: {msg!r}"
    )
    assert "hachi auth google" not in msg, (
        f"Error message must NOT reference the non-existent 'hachi auth google' command, "
        f"but got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# T-57 half B: storage now delegates to OAuthTokenStore (integrations/oauth_flow.py)
# — the single "oauth2.<provider>" namespace. A one-time migration sweeps any
# surviving legacy "oauth.<provider>" key (written by this module before T-57)
# onto the canonical key: copy first, verify the copy landed, only then delete —
# credential data is never deleted without a verified copy existing elsewhere.
# ---------------------------------------------------------------------------


def test_migrate_legacy_oauth_key_copies_and_deletes_legacy_only_token() -> None:
    """A provider authorised only via the old agent prefix ("oauth.<p>", before
    T-57 half B) must be swept onto the canonical "oauth2.<p>" key — copy first,
    verify, then delete — so it is not silently lost once nothing reads the
    legacy prefix any more.
    """
    store = _MemorySecretStore()
    store.set("oauth.google", json.dumps({"access_token": "ya29.agent-only"}))

    _migrate_legacy_oauth_key(store, "google")

    migrated = json.loads(store.get("oauth2.google") or "{}")
    assert migrated.get("access_token") == "ya29.agent-only"
    assert store.get("oauth.google") is None


def test_migrate_legacy_oauth_key_noop_when_no_legacy_key() -> None:
    """Nothing to migrate when only the canonical key (or neither) exists — the
    canonical copy is left untouched and no legacy key is fabricated."""
    store = _MemorySecretStore()
    store.set("oauth2.google", json.dumps({"access_token": "ya29.web-ui"}))

    _migrate_legacy_oauth_key(store, "google")

    assert json.loads(store.get("oauth2.google") or "{}")["access_token"] == "ya29.web-ui"
    assert store.get("oauth.google") is None

    empty_store = _MemorySecretStore()
    _migrate_legacy_oauth_key(empty_store, "google")
    assert empty_store.get("oauth2.google") is None
    assert empty_store.get("oauth.google") is None


def test_migrate_legacy_oauth_key_legacy_wins_when_newer() -> None:
    """If both keys hold tokens and the legacy "oauth.<p>" copy has the later
    `expires_at` (a grant that forked before half A landed: half A's
    `_resolve_key` always preferred the legacy key once populated, so every
    refresh since kept it current while "oauth2.<p>" stayed frozen at
    whatever it held the moment of the fork), the migration must keep the
    fresher legacy value — overwriting the stale canonical copy — then
    delete the legacy key.
    """
    store = _MemorySecretStore()
    store.set(
        "oauth2.google",
        json.dumps({"access_token": "ya29.stale-web-ui", "expires_at": 1000.0}),
    )
    store.set(
        "oauth.google",
        json.dumps({"access_token": "ya29.live-agent-copy", "expires_at": 5000.0}),
    )

    _migrate_legacy_oauth_key(store, "google")

    migrated = json.loads(store.get("oauth2.google") or "{}")
    assert migrated.get("access_token") == "ya29.live-agent-copy", (
        "migration must keep the fresher (later-expiring) legacy copy, not the stale web-UI one"
    )
    assert store.get("oauth.google") is None


def test_migrate_legacy_oauth_key_canonical_wins_when_newer() -> None:
    """If both keys hold tokens but the canonical "oauth2.<p>" copy has the
    later `expires_at`, it must survive untouched.

    Regression: a stale legacy "oauth.<p>" key can outlive a *fresh*
    reconnect. `oauth_callback` (integrations/oauth_flow.py) writes a
    brand-new token straight to "oauth2.<p>" via `OAuthTokenStore.save()`
    without consulting any resolver — e.g. the user reconnects Google in the
    web UI after the agent's copy had already forked under BB-157. Preferring
    the legacy key unconditionally (as if the key name alone decided
    freshness) would silently overwrite the token the user just consented to
    with the stale one, then delete the only other copy — destroying it
    unrecoverably. Precedence must be decided on the data (`expires_at`), not
    the key name.
    """
    store = _MemorySecretStore()
    store.set(
        "oauth2.google",
        json.dumps({"access_token": "ya29.fresh-reconnect", "expires_at": 9999.0}),
    )
    store.set(
        "oauth.google",
        json.dumps({"access_token": "ya29.stale-agent-copy", "expires_at": 1000.0}),
    )

    _migrate_legacy_oauth_key(store, "google")

    migrated = json.loads(store.get("oauth2.google") or "{}")
    assert migrated.get("access_token") == "ya29.fresh-reconnect", (
        "the fresher canonical token was overwritten by a stale legacy copy — a token the "
        "user just consented to was destroyed"
    )
    # The stale legacy key is still cleaned up even though it lost.
    assert store.get("oauth.google") is None


def test_migrate_legacy_oauth_key_ties_go_to_legacy() -> None:
    """When neither token carries a usable `expires_at` (both missing, so both
    count as 0 — a tie), the legacy copy wins. This preserves half A's own
    resolver preference for the common case where neither side has enough
    information to prove freshness.
    """
    store = _MemorySecretStore()
    store.set("oauth2.google", json.dumps({"access_token": "ya29.stale-web-ui"}))
    store.set("oauth.google", json.dumps({"access_token": "ya29.live-agent-copy"}))

    _migrate_legacy_oauth_key(store, "google")

    migrated = json.loads(store.get("oauth2.google") or "{}")
    assert migrated.get("access_token") == "ya29.live-agent-copy"
    assert store.get("oauth.google") is None


def test_migrate_legacy_oauth_key_leaves_corrupt_legacy_value_alone() -> None:
    """A legacy value that does not parse as a token dict must not be deleted —
    there is nothing verified to have copied it to, so destroying the only
    copy would be a silent credential loss."""
    store = _MemorySecretStore()
    store.set("oauth.google", "not-valid-json{{{")

    _migrate_legacy_oauth_key(store, "google")

    assert store.get("oauth.google") == "not-valid-json{{{"
    assert store.get("oauth2.google") is None


def test_migrate_legacy_oauth_key_does_not_delete_when_copy_unverified() -> None:
    """Credential data: never delete without a verified copy. If a write to the
    canonical key silently doesn't land, the legacy key must survive so the
    token is not lost — simulated with a store whose set() is a no-op."""

    class _WriteDroppingStore(_MemorySecretStore):
        def set(self, name: str, value: str, metadata=None) -> None:
            pass  # simulate a write that silently does not persist

    store = _WriteDroppingStore()
    # Seed directly (set() is broken by design above) so the legacy value is
    # actually present for the migration to read.
    store._items["oauth.google"] = json.dumps({"access_token": "ya29.only-copy"})

    _migrate_legacy_oauth_key(store, "google")

    assert store.get("oauth.google") is not None, (
        "legacy key was deleted even though the copy to 'oauth2.google' never "
        "verifiably landed — this would have destroyed the only copy of the grant."
    )


def test_get_token_migrates_a_legacy_only_token_transparently() -> None:
    """OAuthManager.get_token must find — and migrate — a token that only
    exists under the pre-T-57 legacy "oauth.<p>" prefix: not just return it,
    but move it onto the canonical key so every subsequent read/write is
    unambiguous.
    """
    store = _MemorySecretStore()
    store.set("oauth.google", json.dumps({"access_token": "ya29.legacy-only"}))

    manager = OAuthManager(secret_store=store)
    result = manager.get_token("google")

    assert result is not None
    assert result["access_token"] == "ya29.legacy-only"
    assert json.loads(store.get("oauth2.google") or "{}")["access_token"] == "ya29.legacy-only"
    assert store.get("oauth.google") is None


def test_revoke_without_a_prior_read_still_clears_a_legacy_only_token() -> None:
    """`hachi google-auth --revoke` clears credentials via save_token() directly
    (cli/main.py), without ever calling get_token() first. A token that has
    only ever lived under the legacy "oauth.<p>" prefix must still be the one
    --revoke clears — save_token() must migrate a surviving legacy key before
    writing, not rely on get_token() having already done it.
    """
    store = _MemorySecretStore()
    store.set("oauth.google", json.dumps({"access_token": "ya29.legacy-only"}))

    manager = OAuthManager(secret_store=store)
    manager.save_token("google", {})

    assert store.get("oauth.google") is None, (
        "the legacy copy of the grant survived a --revoke that never called get_token() first"
    )
    assert store.get("oauth2.google") == "{}"
