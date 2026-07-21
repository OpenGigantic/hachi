"""Provider adapters and the ProviderRegistry.

New vendor SDK imports MUST live only in the adapter modules under this package
(import-boundary rule, design doc 13 §4a / Gate-C A7).  ``core/llm.py`` and
``core/voice.py`` are grandfathered exceptions.
"""

from hachi.core.providers.base import (
    AIProvider,
    Embedder,
    SpeechProvider,
    StorageProvider,
)
from hachi.core.providers.registry import ProviderRegistry
from hachi.core.providers.search import ExtractResult, SearchProvider, SearchResult

__all__ = [
    "AIProvider",
    "SpeechProvider",
    "SearchProvider",
    "StorageProvider",
    "Embedder",
    "SearchResult",
    "ExtractResult",
    "ProviderRegistry",
]
