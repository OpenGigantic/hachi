"""
Google Service Interface

Defines the strict base class contract for all Google Workspace plugins.
Every module (Gmail, Drive, Docs) must inherit from this class to ensure
seamless mounting into the LLM ToolRegistry.
"""

from abc import ABC, abstractmethod

from tokoboros.core.loop import ToolRegistry
from tokoboros.integrations.google.client import GoogleHTTPClient


class GoogleService(ABC):
    """
    Abstract base class for a Google Workspace integration module.
    """

    def __init__(self, client: GoogleHTTPClient):
        """
        Injects the shared HTTP client so all services share the same OAuth token.
        """
        self.client = client

    @property
    @abstractmethod
    def required_scopes(self) -> list[str]:
        """
        Return a list of Google OAuth scopes required by this specific service.
        Example: ["https://www.googleapis.com/auth/gmail.modify"]
        """
        pass

    @abstractmethod
    def register_tools(self, registry: ToolRegistry) -> None:
        """
        Mount the tools provided by this service into the agent's brain.

        Args:
            registry: The central ToolRegistry of the agent.
        """
        pass
