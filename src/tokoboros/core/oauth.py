"""
Universal OAuth Manager for Tokoboros

This module handles zero-friction "Sign in with..." experiences for users.
It spins up a temporary local web server, opens the browser for the user to grant permission,
and securely stores the resulting OAuth tokens locally.

No API keys required from the user.
"""

import os
import json
import time
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional
import httpx

# Tokens are stored securely in the user's home directory
TOKEN_DIR = os.path.expanduser("~/.tokoboros/tokens")

# For an open-source project, a public Client ID is standard practice for Desktop Apps.
# This would normally be the official "Tokoboros AI" Google App.
# For demonstration/testing, we use a placeholder or require one via environment for now,
# but the UX for the end user is completely abstracted.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "940160412387-fdjkc2l8r5ncl9cq3vgrqbf1lqib8hpi.apps.googleusercontent.com")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "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")

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Temporary local server to catch the OAuth redirect."""
    
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        if "code" in params:
            self.server.auth_code = params["code"][0]
            html = """
            <html><body style="font-family: sans-serif; text-align: center; margin-top: 50px;">
                <h1 style="color: #4CAF50;">Authentication Successful!</h1>
                <p>You have successfully connected your account to Tokoboros.</p>
                <p>You can close this window and return to the terminal.</p>
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
    
    def __init__(self):
        os.makedirs(TOKEN_DIR, exist_ok=True)
        
    def get_token(self, provider: str) -> Optional[Dict]:
        """Load a saved token from disk."""
        token_path = os.path.join(TOKEN_DIR, f"{provider}.json")
        if os.path.exists(token_path):
            with open(token_path, "r") as f:
                return json.load(f)
        return None
        
    def save_token(self, provider: str, token_data: Dict):
        """Save a token to disk securely."""
        token_path = os.path.join(TOKEN_DIR, f"{provider}.json")
        # Ensure file is only readable by the user
        with open(token_path, "w") as f:
            json.dump(token_data, f, indent=2)
        if os.name != 'nt':
            os.chmod(token_path, 0o600)
            
    def get_google_credentials(self, scopes: List[str]) -> str:
        """
        Gets a valid Google access token. 
        If none exists, it triggers the browser OAuth flow.
        If it's expired, it automatically refreshes it.
        """
        token_data = self.get_token("google")
        
        # Check if the requested scopes are a subset of the granted scopes
        granted_scopes = token_data.get("scope", "").split(" ") if token_data else []
        scopes_satisfied = all(scope in granted_scopes for scope in scopes)

        # If we have a token, it has all required scopes, and it's not expired, return it
        if token_data and scopes_satisfied and time.time() < token_data.get("expires_at", 0):
            return token_data["access_token"]
            
        # If we have a token, it has all scopes, but it's expired, refresh it
        if token_data and scopes_satisfied and "refresh_token" in token_data:
            print("  [oauth] Refreshing Google token silently...")
            return self._refresh_google_token(token_data)
            
        # Otherwise, we need to do the full browser flow (missing token or missing scopes)
        if token_data and not scopes_satisfied:
            print("  [oauth] Upgrading Google account permissions for new capabilities...")
        return self._run_google_browser_flow(scopes)
        
    def _run_google_browser_flow(self, scopes: List[str]) -> str:
        """Spins up local server, opens browser, gets code, trades for token."""
        print("  [oauth] Connecting to Google...")
        print("  [oauth] A browser window will open to complete authentication.")
        
        port = 8080
        redirect_uri = f"http://127.0.0.1:{port}"
        
        # 1. Start local server
        server = HTTPServer(('127.0.0.1', port), OAuthCallbackHandler)
        server.auth_code = None
        
        # 2. Open browser to Google Consent Screen
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={GOOGLE_CLIENT_ID}"
            f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
            "&response_type=code"
            f"&scope={urllib.parse.quote(' '.join(scopes))}"
            "&access_type=offline"
            "&prompt=consent"
        )
        webbrowser.open(auth_url)
        
        # 3. Wait for the redirect (blocks until the handler receives the request)
        server.handle_request()
        
        if not server.auth_code:
            raise Exception("Authentication failed or was cancelled by the user.")
            
        # 4. Trade the code for the actual tokens
        print("  [oauth] Authorization received. Securing tokens...")
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": server.auth_code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
        )
        
        token_response.raise_for_status()
        data = token_response.json()
        
        # Calculate absolute expiration time
        data["expires_at"] = time.time() + data.get("expires_in", 3600)
        
        # 5. Save securely
        self.save_token("google", data)
        print("  [oauth] ✓ Google account connected successfully!")
        
        return data["access_token"]
        
    def _refresh_google_token(self, old_token_data: Dict) -> str:
        """Trades a refresh token for a fresh access token."""
        refresh_token = old_token_data["refresh_token"]
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
        )
        
        token_response.raise_for_status()
        data = token_response.json()
        
        # Merge new data into old token to preserve scopes and other metadata
        for key, value in data.items():
            old_token_data[key] = value
            
        old_token_data["expires_at"] = time.time() + data.get("expires_in", 3600)
        self.save_token("google", old_token_data)
        
        return old_token_data["access_token"]
