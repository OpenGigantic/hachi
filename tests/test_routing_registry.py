"""Tests for ModelRegistry and pricing parity guard (Slice 2.1).

GATE tests:
  - Pricing parity: registry pricing == old MODEL_PRICING for existing families.
  - /models regression: configured model appears in registry.all().
"""

from __future__ import annotations

from hachi.core.cost import MODEL_PRICING
from hachi.core.routing.capability import Capability
from hachi.core.routing.descriptor import ModelDescriptor, Pricing
from hachi.core.routing.registry import ModelRegistry

# ---------------------------------------------------------------------------
# Pricing parity guard (doc 14 §7)
# ---------------------------------------------------------------------------


class TestPricingParity:
    """Assert registry pricing == old MODEL_PRICING for the 5 existing families."""

    def _registry_price(self, registry: ModelRegistry, prefix: str) -> tuple[float, float] | None:
        pricing = registry.pricing_for_model(prefix)
        if pricing is None:
            return None
        return (pricing.input_per_mtok, pricing.output_per_mtok)

    def test_claude_opus_4_parity(self) -> None:
        reg = ModelRegistry.build(object(), None)
        old = MODEL_PRICING["claude-opus-4"]
        new = self._registry_price(reg, "claude-opus-4")
        assert new == old, f"claude-opus-4 pricing mismatch: registry={new} vs cost.py={old}"

    def test_claude_sonnet_4_parity(self) -> None:
        reg = ModelRegistry.build(object(), None)
        old = MODEL_PRICING["claude-sonnet-4"]
        new = self._registry_price(reg, "claude-sonnet-4")
        assert new == old, f"claude-sonnet-4 pricing mismatch: registry={new} vs cost.py={old}"

    def test_claude_haiku_4_parity(self) -> None:
        reg = ModelRegistry.build(object(), None)
        old = MODEL_PRICING["claude-haiku-4"]
        new = self._registry_price(reg, "claude-haiku-4")
        assert new == old, f"claude-haiku-4 pricing mismatch: registry={new} vs cost.py={old}"

    def test_gpt_4o_parity(self) -> None:
        reg = ModelRegistry.build(object(), None)
        old = MODEL_PRICING["gpt-4o"]
        new = self._registry_price(reg, "gpt-4o")
        assert new == old, f"gpt-4o pricing mismatch: registry={new} vs cost.py={old}"

    def test_gpt_4o_mini_parity(self) -> None:
        reg = ModelRegistry.build(object(), None)
        old = MODEL_PRICING["gpt-4o-mini"]
        new = self._registry_price(reg, "gpt-4o-mini")
        assert new == old, f"gpt-4o-mini pricing mismatch: registry={new} vs cost.py={old}"

    def test_dated_variant_resolves_via_prefix(self) -> None:
        """claude-opus-4-8 (dated variant) should resolve to claude-opus-4 price."""
        reg = ModelRegistry.build(object(), None)
        old = MODEL_PRICING["claude-opus-4"]
        new = self._registry_price(reg, "claude-opus-4-8-20260101")
        assert new == old


# ---------------------------------------------------------------------------
# ModelRegistry basics
# ---------------------------------------------------------------------------


class TestModelRegistry:
    def _build(self) -> ModelRegistry:
        return ModelRegistry.build(object(), None)

    def test_all_returns_bundled_defaults(self) -> None:
        reg = self._build()
        ids = [d.id for d in reg.all()]
        assert "claude-opus-4-8" in ids
        assert "claude-haiku-4-5" in ids
        assert "gpt-4o" in ids

    def test_get_known_model(self) -> None:
        reg = self._build()
        desc = reg.get("claude-haiku-4-5")
        assert desc is not None
        assert Capability.REASONING in desc.capabilities

    def test_get_unknown_model_returns_none(self) -> None:
        reg = self._build()
        assert reg.get("nonexistent-model-xyz") is None

    def test_by_capability_reasoning(self) -> None:
        reg = self._build()
        models = reg.by_capability(Capability.REASONING)
        assert len(models) > 0
        for d in models:
            assert Capability.REASONING in d.capabilities

    def test_by_capability_vision(self) -> None:
        reg = self._build()
        models = reg.by_capability(Capability.VISION)
        ids = [d.id for d in models]
        assert "claude-opus-4-8" in ids or "gpt-4o" in ids

    def test_register_overrides_bundled(self) -> None:
        reg = self._build()
        custom = ModelDescriptor(
            id="claude-haiku-4-5",
            provider="anthropic",
            capabilities=frozenset({Capability.CODING}),
            context_window=100,
            max_output=100,
            pricing=Pricing(input_per_mtok=999.0, output_per_mtok=999.0),
        )
        reg.register(custom)
        desc = reg.get("claude-haiku-4-5")
        assert desc is not None
        assert desc.pricing is not None
        assert desc.pricing.input_per_mtok == 999.0

    def test_unknown_model_pricing_returns_none(self) -> None:
        reg = self._build()
        result = reg.pricing_for_model("completely-unknown-model-xyz")
        assert result is None

    def test_configured_model_in_all(self) -> None:
        """/models regression: the configured running model appears in registry."""
        reg = self._build()
        # The typical default is claude-haiku-4-5.
        ids = [d.id for d in reg.all()]
        assert "claude-haiku-4-5" in ids
