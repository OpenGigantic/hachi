"""
Memory Provider Interface

Abstract base class that defines the contract for all memory backends.
Enables swapping ChromaDB for alternative backends (Qdrant, Pinecone, Honcho)
without touching core agent code.

The default implementation (ChromaMemoryProvider) wraps the existing MemoryVault,
ensuring zero behavioral change while enabling future extensibility.

Lifecycle hooks:
    initialize()     — called once at boot
    upsert()         — store a fact
    search()         — semantic retrieval
    delete()         — remove a fact
    sync_turn()      — called after every completed turn (optional)
    on_session_end() — called at shutdown (optional)
    shutdown()       — cleanup resources (optional)
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryProvider(ABC):
    """Abstract memory backend interface.

    Subclass this to create custom memory backends. Only upsert, search,
    delete, and initialize are required — all lifecycle hooks have no-op
    defaults so backends only implement what they need.
    """

    @abstractmethod
    def initialize(self, brain_dir: str) -> None:
        """Initialize the backend storage.

        Args:
            brain_dir: The base directory for persistent storage.
        """

    @abstractmethod
    def upsert(self, content: str, category: str = "workspace_state") -> str:
        """Store an atomic fact. Returns the fact ID or an error string.

        Args:
            content: The fact text to persist.
            category: Organizational tag for filtering.

        Returns:
            The unique fact ID, or a 'BLOCKED:<reason>' string on rejection.
        """

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Semantic similarity search.

        Args:
            query: Natural language query to match against stored facts.
            top_k: Maximum number of results to return.

        Returns:
            Ranked list of fact dicts with id, content, category, timestamp, raw_content.
        """

    @abstractmethod
    def delete(self, fact_id: str) -> bool:
        """Remove a fact by ID.

        Args:
            fact_id: The unique identifier of the fact to remove.

        Returns:
            True if deletion succeeded.
        """

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Optional: called after every completed turn for background extraction."""
        pass

    def on_session_end(self, messages: list) -> None:
        """Optional: called at shutdown for end-of-session processing."""
        pass

    def shutdown(self) -> None:
        """Optional: release resources."""
        pass


class ChromaMemoryProvider(MemoryProvider):
    """Default provider that wraps the existing MemoryVault.

    This adapter ensures backward compatibility — the entire Phase 1-3
    memory system works through this provider without any behavioral change.
    """

    def __init__(self, vault=None):
        """Initialize with an optional pre-existing vault.

        Args:
            vault: An existing MemoryVault instance. If None, one will be
                   created during initialize().
        """
        self._vault = vault

    @property
    def vault(self):
        """Direct access to the underlying MemoryVault for tools that need it."""
        return self._vault

    def initialize(self, brain_dir: str) -> None:
        """Create a MemoryVault if one wasn't provided at construction time."""
        if self._vault is None:
            from tokoboros.core.memory_vault import MemoryVault
            self._vault = MemoryVault(brain_dir=brain_dir)

    def upsert(self, content: str, category: str = "workspace_state") -> str:
        """Delegate to MemoryVault.upsert_fact()."""
        return self._vault.upsert_fact(content, category)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Delegate to MemoryVault.semantic_search()."""
        return self._vault.semantic_search(query, top_k)

    def delete(self, fact_id: str) -> bool:
        """Delegate to MemoryVault.delete_fact()."""
        return self._vault.delete_fact(fact_id)

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """No-op — sync_turn is handled by MemoryManager.sync_turn() already."""
        pass

    def shutdown(self) -> None:
        """ChromaDB PersistentClient handles its own cleanup."""
        pass
