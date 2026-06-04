"""
Base Slack Service
Provides the core HTTP client wrapper and authentication headers for the Slack Web API.
"""

import httpx

from tokoboros.core.loop import ToolRegistry


class SlackService:
    """
    Base service for Slack API integrations.
    Handles authentication and provides a pre-configured HTTP client.
    """

    BASE_URL = "https://slack.com/api"

    def __init__(self):
        from tokoboros.core.credential_vault import CredentialVault

        self._vault = CredentialVault()
        # Avoid interactive stdin prompts during TUI boot. We resolve lazily per call.
        self.bot_token = self._vault.get_token("slack_bot_token", prompt_if_missing=False)

    def _ensure_token(self) -> None:
        if self.bot_token:
            return
        self.bot_token = self._vault.get_token("slack_bot_token", prompt_if_missing=False)
        if not self.bot_token:
            raise ValueError(
                "Missing Slack Bot Token. Add 'slack_bot_token' to ~/.tokoboros/vault.json."
            )

    @property
    def headers(self) -> dict:
        """Returns the required headers for Slack API requests."""
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _post(self, endpoint: str, json_data: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        response = httpx.post(url, headers=self.headers, json=json_data or {}, timeout=30.0)
        response.raise_for_status()

        data = response.json()
        if not data.get("ok"):
            error_msg = data.get("error", "Unknown Slack API error")
            raise Exception(f"Slack API returned error: {error_msg}")

        return data

    def _post_multipart(self, endpoint: str, data: dict = None, files: dict = None) -> dict:
        self._ensure_token()
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"

        # Don't use standard headers because httpx must set the Content-Type to
        # multipart/form-data with boundary
        multipart_headers = {"Authorization": f"Bearer {self.bot_token}"}

        response = httpx.post(
            url, headers=multipart_headers, data=data or {}, files=files or {}, timeout=60.0
        )
        response.raise_for_status()

        resp_data = response.json()
        if not resp_data.get("ok"):
            error_msg = resp_data.get("error", "Unknown Slack API error")
            raise Exception(f"Slack API returned error: {error_msg}")

        return resp_data

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        response = httpx.get(url, headers=self.headers, params=params, timeout=30.0)
        response.raise_for_status()

        data = response.json()
        if not data.get("ok"):
            error_msg = data.get("error", "Unknown Slack API error")
            raise Exception(f"Slack API returned error: {error_msg}")

        return data

    def register_tools(self, registry: ToolRegistry) -> None:
        """To be implemented by subclasses to register their specific tools."""
        pass
