"""
Universal OAuth Manager for Tokoboros

This module handles zero-friction "Sign in with..." experiences for users.
It spins up a temporary local web server, opens the browser for the user to grant permission,
and securely stores the resulting OAuth tokens locally.

No API keys required from the user.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from tokoboros.core.secret_store import SecretStore, create_default_secret_store

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Temporary local server to catch the OAuth redirect."""

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        expected_state = getattr(self.server, "oauth_state", None)
        received_state = params.get("state", [None])[0]

        if "code" in params and expected_state and expected_state == received_state:
            self.server.auth_code = params["code"][0]
            html = """
            <html><body style="font-family: sans-serif; text-align: center; margin-top: 50px;">
                <h1 style="color: #4CAF50;">Authentication Successful!</h1>
                <p>You have successfully connected your account to Tokoboros.</p>
                <p>You can close this window and return to the terminal.</p>
            </body></html>
            """
            self.wfile.write(html.encode("utf-8"))
        elif "code" in params and expected_state and expected_state != received_state:
            self.server.auth_code = None
            html = """
            <html><body style="font-family: sans-serif; text-align: center; margin-top: 50px;">
                <h1 style="color: #F44336;">Authentication Failed</h1>
                <p>State validation failed. Please try again.</p>
            </body></html>
            """
            self.wfile.write(html.encode("utf-8"))
        else:
            self.server.auth_code = None
            html = """
            <html><body style="font-family: sans-serif; text-align: center; margin-top: 50px;">
                <h1 style="color: #F44336;">Authentication Failed</h1>
                <p>No authorization code received. Please try again.</p>
            </body></html>
            """
            self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress logging to keep the terminal clean
        pass


class OAuthManager:
    """Manages OAuth tokens and the browser-based authentication flow."""

    def __init__(self, secret_store: SecretStore | None = None):
        self._store = secret_store or create_default_secret_store()

    @staticmethod
    def _key(provider: str) -> str:
        return f"oauth.{provider}"

    @staticmethod
    def _require_google_client_id() -> str:
        if not GOOGLE_CLIENT_ID:
            raise RuntimeError(
                "Missing GOOGLE_CLIENT_ID. Configure Google OAuth before using Google integrations."
            )
        return GOOGLE_CLIENT_ID

    @staticmethod
    def _build_pkce() -> dict[str, str]:
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
            .decode("utf-8")
            .rstrip("=")
        )
        return {"verifier": verifier, "challenge": challenge, "method": "S256"}

    def get_token(self, provider: str) -> dict | None:
        """Load a saved token from secure storage."""
        raw = self._store.get(self._key(provider))
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return None
        return None

    def save_token(self, provider: str, token_data: dict):
        """Save a token to secure storage with metadata."""
        metadata = {
            "name": self._key(provider),
            "labels": {
                "provider": provider,
                "scope": str(token_data.get("scope", "")),
            },
        }
        self._store.set(self._key(provider), json.dumps(token_data), metadata=metadata)

    def get_google_credentials(self, scopes: list[str]) -> str:
        """
        Gets a valid Google access token.
        If none exists, it triggers the browser OAuth flow.
        If it's expired, it automatically refreshes it.
        """
        token_data = self.get_token("google")

        # Check if the requested scopes are a subset of the granted scopes
        granted_scopes = token_data.get("scope", "").split(" ") if token_data else []
        scopes_satisfied = all(scope in granted_scopes for scope in scopes)

        # If we have a token, it has all required scopes, and it's not expired
        # (with a 60s clock-skew buffer), return it.
        if token_data and scopes_satisfied and time.time() < token_data.get("expires_at", 0) - 60:
            return token_data["access_token"]

        # If we have a token, it has all scopes, but it's expired, refresh it.
        if token_data and scopes_satisfied and "refresh_token" in token_data:
            print("  [oauth] Refreshing Google token silently...")
            try:
                return self._refresh_google_token(token_data)
            except Exception as exc:
                # Refresh token revoked/expired (e.g. invalid_grant) — fall back to
                # the full browser flow instead of failing every Google tool call.
                print(f"  [oauth] Token refresh failed ({exc}); re-authenticating...")

        # Otherwise, we need to do the full browser flow (missing token or missing scopes)
        if token_data and not scopes_satisfied:
            print("  [oauth] Upgrading Google account permissions for new capabilities...")
        return self._run_google_browser_flow(scopes)

    def _run_google_browser_flow(self, scopes: list[str]) -> str:
        """Spins up local server, opens browser, gets code, trades for token."""
        print("  [oauth] Connecting to Google...")
        print("  [oauth] A browser window will open to complete authentication.")

        client_id = self._require_google_client_id()
        pkce = self._build_pkce()
        state = secrets.token_urlsafe(24)

        # 1. Start local server on an ephemeral port to avoid conflicts
        server = HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
        actual_port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{actual_port}/callback"
        server.auth_code = None
        server.oauth_state = state

        # 2. Open browser to Google Consent Screen
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={client_id}"
            f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
            "&response_type=code"
            f"&scope={urllib.parse.quote(' '.join(scopes))}"
            "&access_type=offline"
            "&prompt=consent"
            f"&state={urllib.parse.quote(state)}"
            f"&code_challenge={urllib.parse.quote(pkce['challenge'])}"
            f"&code_challenge_method={pkce['method']}"
        )
        webbrowser.open(auth_url)

        # 3. Wait for the redirect — 2-minute timeout to avoid blocking forever
        server.timeout = 120
        server.handle_request()

        if server.auth_code is None:
            raise TimeoutError(
                "Authentication failed or was cancelled (timed out waiting for consent)."
            )

        # 4. Trade the code for the actual tokens
        print("  [oauth] Authorization received. Securing tokens...")
        token_payload = {
            "code": server.auth_code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": pkce["verifier"],
        }
        if GOOGLE_CLIENT_SECRET:
            token_payload["client_secret"] = GOOGLE_CLIENT_SECRET

        token_response = httpx.post("https://oauth2.googleapis.com/token", data=token_payload)

        token_response.raise_for_status()
        data = token_response.json()

        # Calculate absolute expiration time
        data["expires_at"] = time.time() + data.get("expires_in", 3600)

        # 5. Save securely
        self.save_token("google", data)
        print("  [oauth] ✓ Google account connected successfully!")

        return data["access_token"]

    def _refresh_google_token(self, old_token_data: dict) -> str:
        """Trades a refresh token for a fresh access token."""
        refresh_token = old_token_data["refresh_token"]
        client_id = self._require_google_client_id()
        payload = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if GOOGLE_CLIENT_SECRET:
            payload["client_secret"] = GOOGLE_CLIENT_SECRET

        token_response = httpx.post("https://oauth2.googleapis.com/token", data=payload)

        token_response.raise_for_status()
        data = token_response.json()

        # Merge new data into old token to preserve scopes and other metadata
        for key, value in data.items():
            old_token_data[key] = value

        old_token_data["expires_at"] = time.time() + data.get("expires_in", 3600)
        self.save_token("google", old_token_data)

        return old_token_data["access_token"]
