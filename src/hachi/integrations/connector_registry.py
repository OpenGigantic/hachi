"""Connector registry: static metadata for all OAuth2 / API-key connectors.

Each entry drives:
  - The integrations list the frontend renders (GET /capabilities or GET /integrations).
  - The OAuth flow: authorize_url, token_url, scopes, env var names for client creds.
  - Status computation: ``configured`` (client creds present in env),
    ``connected`` (valid token stored), ``auth_type``.

Adding a new connector: add an entry to ``CONNECTORS`` — nothing else needs changing.

Env var naming convention for OAuth2 connectors:
    HACHI_OAUTH_{PROVIDER_UPPER}_CLIENT_ID
    HACHI_OAUTH_{PROVIDER_UPPER}_CLIENT_SECRET

Example for Google:
    HACHI_OAUTH_GOOGLE_CLIENT_ID=<your-client-id>
    HACHI_OAUTH_GOOGLE_CLIENT_SECRET=<your-client-secret>

The ``redirect_uri`` registered in the provider's OAuth app must be:
    <HACHI_OAUTH_REDIRECT_BASE>/integrations/oauth/callback
    Default base: http://localhost:8787
    Full default: http://localhost:8787/integrations/oauth/callback
"""

from __future__ import annotations

import os
from typing import Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

AuthType = Literal["oauth2", "apikey", "none"]


class ConnectorDef:
    """Static definition of a third-party connector.

    Args:
        id: Slug identifier, unique across the registry (e.g. ``"google"``).
        name: Human-readable display name.
        category: Grouping hint for the UI (e.g. ``"productivity"``, ``"messaging"``).
        auth_type: Authentication mechanism — ``"oauth2"``, ``"apikey"``, or ``"none"``.
        authorize_url: OAuth2 authorization endpoint URL (``oauth2`` only).
        token_url: OAuth2 token exchange endpoint URL (``oauth2`` only).
        revoke_url: Optional OAuth2 token revocation endpoint.
        default_scopes: Default OAuth2 scopes requested during authorization.
        client_id_env: Name of the env var holding the OAuth2 client ID.
        client_secret_env: Name of the env var holding the OAuth2 client secret.
        needs_offline: Whether to request offline access (refresh token).
            Defaults to ``True`` for oauth2 connectors.
        extra_auth_params: Optional extra query params for the authorize URL
            (e.g. ``{"prompt": "select_account"}``).
    """

    def __init__(
        self,
        *,
        id: str,
        name: str,
        category: str,
        auth_type: AuthType = "oauth2",
        authorize_url: str = "",
        token_url: str = "",
        revoke_url: str = "",
        default_scopes: list[str] | None = None,
        client_id_env: str = "",
        client_secret_env: str = "",
        needs_offline: bool = True,
        extra_auth_params: dict[str, str] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.category = category
        self.auth_type: AuthType = auth_type
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.revoke_url = revoke_url
        self.default_scopes: list[str] = default_scopes or []
        self.client_id_env = client_id_env
        self.client_secret_env = client_secret_env
        self.needs_offline = needs_offline
        self.extra_auth_params: dict[str, str] = extra_auth_params or {}

    def client_id(self) -> str | None:
        """Return the OAuth2 client ID from env, or None if not configured."""
        if not self.client_id_env:
            return None
        return os.environ.get(self.client_id_env) or None

    def client_secret(self) -> str | None:
        """Return the OAuth2 client secret from env, or None if not set."""
        if not self.client_secret_env:
            return None
        return os.environ.get(self.client_secret_env) or None

    def is_configured(self) -> bool:
        """Return True iff the minimum client credentials are present in env.

        For oauth2 connectors the client_id is the minimum requirement
        (some providers allow public clients without a secret).
        For non-oauth2 connectors always returns False (not applicable via OAuth flow).
        """
        if self.auth_type != "oauth2":
            return False
        return bool(self.client_id())


# ---------------------------------------------------------------------------
# Connector definitions
# ---------------------------------------------------------------------------

CONNECTORS: list[ConnectorDef] = [
    # --- Google (Gmail / Calendar / Drive unified client) ---
    ConnectorDef(
        id="google",
        name="Google",
        category="productivity",
        auth_type="oauth2",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        default_scopes=[
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive",
        ],
        client_id_env="HACHI_OAUTH_GOOGLE_CLIENT_ID",
        client_secret_env="HACHI_OAUTH_GOOGLE_CLIENT_SECRET",
        needs_offline=True,
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
    # --- Slack ---
    ConnectorDef(
        id="slack",
        name="Slack",
        category="messaging",
        auth_type="oauth2",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        revoke_url="https://slack.com/api/auth.revoke",
        default_scopes=[
            "channels:read",
            "channels:history",
            "chat:write",
            "users:read",
            "files:read",
        ],
        client_id_env="HACHI_OAUTH_SLACK_CLIENT_ID",
        client_secret_env="HACHI_OAUTH_SLACK_CLIENT_SECRET",
        needs_offline=False,  # Slack tokens don't expire; no refresh token needed
        extra_auth_params={},
    ),
    # --- Discord ---
    ConnectorDef(
        id="discord",
        name="Discord",
        category="messaging",
        auth_type="oauth2",
        authorize_url="https://discord.com/api/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        revoke_url="https://discord.com/api/oauth2/token/revoke",
        default_scopes=["identify", "guilds", "messages.read"],
        client_id_env="HACHI_OAUTH_DISCORD_CLIENT_ID",
        client_secret_env="HACHI_OAUTH_DISCORD_CLIENT_SECRET",
        needs_offline=True,
    ),
    # --- Notion ---
    ConnectorDef(
        id="notion",
        name="Notion",
        category="productivity",
        auth_type="oauth2",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        revoke_url="",  # Notion does not have a revoke endpoint
        default_scopes=[],  # Notion uses owner/workspace, not scope strings
        client_id_env="HACHI_OAUTH_NOTION_CLIENT_ID",
        client_secret_env="HACHI_OAUTH_NOTION_CLIENT_SECRET",
        needs_offline=False,  # Notion tokens are long-lived
        extra_auth_params={"owner": "user"},
    ),
    # --- GitHub ---
    ConnectorDef(
        id="github",
        name="GitHub",
        category="development",
        auth_type="oauth2",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        revoke_url="",  # GitHub revocation is done via API, not standard endpoint
        default_scopes=["repo", "read:user", "read:org"],
        client_id_env="HACHI_OAUTH_GITHUB_CLIENT_ID",
        client_secret_env="HACHI_OAUTH_GITHUB_CLIENT_SECRET",
        needs_offline=False,  # GitHub tokens are long-lived
        extra_auth_params={},
    ),
    # --- WhatsApp (Meta Business API) — not standard OAuth2; listed as apikey ---
    # Users must provision a permanent token via Meta Business Suite.
    # No OAuth2 flow is available for WhatsApp Business API.
    ConnectorDef(
        id="whatsapp",
        name="WhatsApp",
        category="messaging",
        auth_type="apikey",
        # No OAuth fields — apikey auth is not handled by this flow
    ),
]

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ConnectorDef] = {c.id: c for c in CONNECTORS}


def get_connector(connector_id: str) -> ConnectorDef | None:
    """Look up a connector by id, returning None if not found.

    Args:
        connector_id: The connector slug (e.g. ``"google"``).

    Returns:
        The :class:`ConnectorDef` if found, ``None`` otherwise.
    """
    return _REGISTRY.get(connector_id)


def list_connectors() -> list[ConnectorDef]:
    """Return all registered connectors in definition order."""
    return list(CONNECTORS)


def oauth2_connectors() -> list[ConnectorDef]:
    """Return only OAuth2-capable connectors."""
    return [c for c in CONNECTORS if c.auth_type == "oauth2"]
