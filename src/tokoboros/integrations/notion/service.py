"""
Base Notion Service
Provides the core HTTP client wrapper and authentication headers for the Notion API.
"""
import os
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
        vault = CredentialVault()
        try:
            self.api_key = vault.get_token("notion_api_key", prompt_if_missing=True)
        except ValueError:
            self.api_key = None
            print("Warning: NOTION_API_KEY was not provided. Notion tools will fail.")

    @property
    def headers(self) -> dict:
        """Returns the required headers for Notion API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json"
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
