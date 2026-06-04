"""
Base Notion Service
Provides the core HTTP client wrapper and authentication headers for the Notion API.
"""

import httpx

from tokoboros.core.loop import ToolRegistry


class NotionService:
    """
    Base service for Notion API integrations.
    Handles authentication and provides a pre-configured HTTP client.
    """

    BASE_URL = "https://api.notion.com/v1"
    NOTION_VERSION = "2025-09-03"

    def __init__(self):
        from tokoboros.core.credential_vault import CredentialVault

        self._vault = CredentialVault()
        # Avoid interactive stdin prompts during TUI boot. We resolve lazily per call.
        self.api_key = self._vault.get_token("notion_api_key", prompt_if_missing=False)

    def _ensure_api_key(self) -> None:
        if self.api_key:
            return
        self.api_key = self._vault.get_token("notion_api_key", prompt_if_missing=False)
        if not self.api_key:
            raise ValueError(
                "Missing Notion API key. Add 'notion_api_key' to ~/.tokoboros/vault.json."
            )

    @property
    def headers(self) -> dict:
        """Returns the required headers for Notion API requests."""
        self._ensure_api_key()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        response = httpx.get(url, headers=self.headers, params=params, timeout=30.0)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, json_data: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        response = httpx.post(url, headers=self.headers, json=json_data or {}, timeout=30.0)
        response.raise_for_status()
        return response.json()

    def _patch(self, endpoint: str, json_data: dict = None) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        response = httpx.patch(url, headers=self.headers, json=json_data or {}, timeout=30.0)
        response.raise_for_status()
        return response.json()

    def register_tools(self, registry: ToolRegistry) -> None:
        """To be implemented by subclasses to register their specific tools."""
        pass
