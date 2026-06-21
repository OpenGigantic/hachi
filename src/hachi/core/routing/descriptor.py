"""Model descriptor and the bundled defaults table.

``ModelDescriptor`` carries everything the router needs to evaluate a model candidate:
capabilities, context window, output budget, pricing, and feature flags.

``BUNDLED_MODEL_DEFAULTS`` is the single curated data table — the only place model ids
and capabilities live as data.  Pricing numbers are migrated verbatim from
``core/cost.py:19`` (MODEL_PRICING) so the guard test (registry pricing == old dict)
passes without drift.
"""

from __future__ import annotations

from pydantic import BaseModel

from hachi.core.routing.capability import Capability


class Pricing(BaseModel):
    """USD per 1,000,000 tokens for one model.

    ``cache_read_mult`` and ``cache_write_mult`` are multipliers relative to the
    base ``input_per_mtok`` price — mirrors the Anthropic-style accounting already
    in ``cost.py``.
    """

    input_per_mtok: float
    output_per_mtok: float
    cache_read_mult: float = 0.1
    cache_write_mult: float = 1.25


class ModelDescriptor(BaseModel):
    """Immutable description of a single model variant.

    Registered in :class:`~hachi.core.routing.registry.ModelRegistry`; consumed by
    :class:`~hachi.core.routing.router.ModelRouter` for capability filtering and
    ordering.

    Args:
        id: Canonical model identifier (e.g. ``"claude-opus-4-8"``).
        provider: Key matching a registered :class:`~hachi.core.providers.base.AIProvider`
            (e.g. ``"anthropic"``).
        capabilities: Frozen set of :class:`~hachi.core.routing.capability.Capability`
            values the model supports.
        context_window: Maximum input tokens the model accepts.
        max_output: Maximum tokens the model may generate.
        pricing: Per-token cost.  ``None`` means cost is unknown; the router may still
            pick the model unless a hard cost constraint is set.
        supports_tools: Whether the model supports tool/function calling.
        supports_streaming: Whether the model supports streaming generation.
        tier_rank: Lower number = higher quality.  Used for ``prefer="quality"`` ordering
            when multiple models share a capability.  Defaults to 50 (mid-tier).
    """

    id: str
    provider: str
    capabilities: frozenset[Capability]
    context_window: int
    max_output: int
    pricing: Pricing | None = None
    supports_tools: bool = True
    supports_streaming: bool = True
    tier_rank: int = 50  # lower = higher quality; used for quality ordering


# ---------------------------------------------------------------------------
# Bundled defaults table
# Pricing values are verbatim from cost.py:19 MODEL_PRICING so the registry
# guard test in tests/test_routing_registry.py passes without drift.
# ---------------------------------------------------------------------------

BUNDLED_MODEL_DEFAULTS: list[ModelDescriptor] = [
    # ------ Anthropic Claude family ------
    ModelDescriptor(
        id="claude-opus-4",
        provider="anthropic",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.VISION,
                Capability.LONG_CONTEXT,
                Capability.SUMMARIZATION,
                Capability.COMPACTION,
            }
        ),
        context_window=200_000,
        max_output=64_000,
        pricing=Pricing(input_per_mtok=15.0, output_per_mtok=75.0),
        tier_rank=10,
    ),
    ModelDescriptor(
        id="claude-opus-4-8",
        provider="anthropic",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.VISION,
                Capability.LONG_CONTEXT,
                Capability.SUMMARIZATION,
                Capability.COMPACTION,
            }
        ),
        context_window=200_000,
        max_output=64_000,
        pricing=Pricing(input_per_mtok=15.0, output_per_mtok=75.0),
        tier_rank=10,
    ),
    ModelDescriptor(
        id="claude-sonnet-4",
        provider="anthropic",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.VISION,
                Capability.SUMMARIZATION,
                Capability.COMPACTION,
                Capability.LONG_CONTEXT,
            }
        ),
        context_window=200_000,
        max_output=64_000,
        pricing=Pricing(input_per_mtok=3.0, output_per_mtok=15.0),
        tier_rank=20,
    ),
    ModelDescriptor(
        id="claude-haiku-4",
        provider="anthropic",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.TITLE_GENERATION,
                Capability.COMPACTION,
                Capability.SUMMARIZATION,
            }
        ),
        context_window=200_000,
        max_output=8_192,
        pricing=Pricing(input_per_mtok=1.0, output_per_mtok=5.0),
        tier_rank=30,
    ),
    ModelDescriptor(
        id="claude-haiku-4-5",
        provider="anthropic",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.TITLE_GENERATION,
                Capability.COMPACTION,
                Capability.SUMMARIZATION,
            }
        ),
        context_window=200_000,
        max_output=8_192,
        pricing=Pricing(input_per_mtok=1.0, output_per_mtok=5.0),
        tier_rank=30,
    ),
    # ------ OpenAI / OpenAI-compatible ------
    ModelDescriptor(
        id="gpt-4o",
        provider="openai",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.VISION,
                Capability.SUMMARIZATION,
            }
        ),
        context_window=128_000,
        max_output=16_384,
        pricing=Pricing(input_per_mtok=2.50, output_per_mtok=10.0),
        tier_rank=15,
    ),
    ModelDescriptor(
        id="gpt-4o-mini",
        provider="openai",
        capabilities=frozenset(
            {
                Capability.REASONING,
                Capability.CODING,
                Capability.TITLE_GENERATION,
                Capability.COMPACTION,
                Capability.SUMMARIZATION,
            }
        ),
        context_window=128_000,
        max_output=16_384,
        pricing=Pricing(input_per_mtok=0.15, output_per_mtok=0.60),
        tier_rank=35,
    ),
]
