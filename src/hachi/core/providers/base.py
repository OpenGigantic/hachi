"""Provider Protocol contracts.

Design doc 13 §2 / Gate-C amendment A6: one adapter **class** may implement
multiple provider Protocols (structural typing via ``runtime_checkable``).

IMPORTANT: This module must NOT import any vendor SDK.  Vendor imports belong
in the concrete adapter modules (``anthropic_provider.py``, etc.).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from hachi.core.llm import BaseLLMClient

# T-60: re-exported (not defined here) for the existing public import path
# (``providers.base.ProviderHealth``). The class itself now lives in
# ``core/provider_health.py`` -- a dependency-free module -- because this
# module's ``routing.*`` imports below form a real circular import with
# ``core/resilience.py`` if ``ProviderHealth`` is defined here and
# ``resilience.py`` imports it from this module (see ``provider_health.py``'s
# docstring for the exact cycle).
from hachi.core.provider_health import ProviderHealth
from hachi.core.routing.capability import Capability
from hachi.core.routing.descriptor import ModelDescriptor


@runtime_checkable
class AIProvider(Protocol):
    """Vendor adapter for LLM providers.

    The ONLY place a vendor LLM SDK may be imported is in a concrete class that
    implements this protocol (or the grandfathered ``core/llm.py``).

    One class may implement ``AIProvider`` *and* other provider protocols
    simultaneously (amendment A6).
    """

    name: str

    def capabilities(self) -> set[Capability]:
        """Return the set of capabilities this provider supports."""
        ...

    def models(self) -> list[ModelDescriptor]:
        """Return the list of advertised model descriptors.

        Best-effort; may return ``[]`` when discovery is unavailable.
        """
        ...

    def build_llm_client(self, model_id: str, **opts: Any) -> BaseLLMClient:
        """Build a :class:`~hachi.core.llm.BaseLLMClient` for *model_id*.

        Args:
            model_id: The exact model identifier.
            **opts: Provider-specific options (e.g. ``max_tokens``, ``api_key``).
        """
        ...

    def health(self) -> ProviderHealth:
        """Return the current health snapshot for this provider."""
        ...


@runtime_checkable
class SpeechProvider(Protocol):
    """Vendor adapter for speech-to-text and text-to-speech providers (Initiative F)."""

    name: str

    def capabilities(self) -> set[Capability]:
        """Return ``{SPEECH_TO_TEXT}``, ``{TEXT_TO_SPEECH}``, or both."""
        ...

    def transcribe(self, audio: bytes, **opts: Any) -> str:
        """Convert raw audio bytes to a transcript string."""
        ...

    def synthesize(self, text: str, **opts: Any) -> bytes:
        """Convert text to audio bytes."""
        ...


@runtime_checkable
class StorageProvider(Protocol):
    """Vendor adapter for file / object storage (Initiative E, seam only)."""

    name: str

    def capabilities(self) -> set[Capability]:
        """Return storage-related capabilities."""
        ...


@runtime_checkable
class Embedder(Protocol):
    """Vendor adapter for embedding models."""

    name: str

    def embed(self, texts: list[str], **opts: Any) -> list[list[float]]:
        """Return dense embedding vectors for *texts*."""
        ...

    def capabilities(self) -> set[Capability]:
        """Must include :attr:`~hachi.core.routing.capability.Capability.EMBEDDINGS`."""
        ...
