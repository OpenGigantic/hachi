"""Tests for validate_routing() — Phase 3 routing validation rules."""

from __future__ import annotations

from hachi.core.routing.capability import Capability
from hachi.core.routing.descriptor import ModelDescriptor, Pricing
from hachi.core.routing.policy import RoutingPolicy
from hachi.core.routing.registry import ModelRegistry
from hachi.core.routing.validate import RoutingValidationResult, validate_routing


def _reg(*model_ids: str) -> ModelRegistry:
    reg = ModelRegistry()
    for mid in model_ids:
        reg.register(
            ModelDescriptor(
                id=mid,
                provider="anthropic",
                capabilities=frozenset({Capability.REASONING}),
                context_window=200_000,
                max_output=8_192,
            )
        )
    return reg


def _priced_reg() -> ModelRegistry:
    """Two REASONING-capable models, priced ~16x apart (BB-30 shape)."""
    reg = ModelRegistry()
    for mid, price in (("cheap-model", 0.15), ("pricey-model", 2.50)):
        reg.register(
            ModelDescriptor(
                id=mid,
                provider="anthropic",
                capabilities=frozenset({Capability.REASONING}),
                context_window=200_000,
                max_output=8_192,
                pricing=Pricing(input_per_mtok=price, output_per_mtok=price * 4),
            )
        )
    return reg


class TestValidateRoutingRules:
    def test_empty_default_model_is_invalid(self) -> None:
        result = validate_routing(_reg("model-a"), RoutingPolicy(default_model=""), providers=None)
        assert not result.ok
        assert any("default_model" in i for i in result.issues)

    def test_whitespace_default_model_is_invalid(self) -> None:
        result = validate_routing(
            _reg("model-a"), RoutingPolicy(default_model="   "), providers=None
        )
        assert not result.ok

    def test_valid_default_model_is_ok(self) -> None:
        result = validate_routing(
            _reg("model-a"), RoutingPolicy(default_model="model-a"), providers=None
        )
        assert result.ok

    def test_unknown_model_default_still_passes_because_synthetic_descriptor(self) -> None:
        """Unknown default_model is allowed (A11 synthetic descriptor fills the gap)."""
        result = validate_routing(
            _reg("model-a"),
            RoutingPolicy(default_model="some-unknown-model"),
            providers=None,
        )
        assert result.ok

    def test_unknown_capability_key_is_invalid(self) -> None:
        # Use model_construct() to bypass pydantic's enum coercion so we can hand
        # validate_routing() a raw-string capability key and confirm it is caught.
        policy = RoutingPolicy.model_construct(
            default_model="model-a",
            capabilities={"not_a_capability": ["model-a"]},
        )
        result = validate_routing(_reg("model-a"), policy, providers=None)
        assert not result.ok
        assert any("not_a_capability" in i for i in result.issues)

    def test_all_known_capability_keys_are_valid(self) -> None:
        known = {c.value: ["model-a"] for c in Capability}
        policy = RoutingPolicy(default_model="model-a", capabilities=known)
        result = validate_routing(_reg("model-a"), policy, providers=None)
        assert result.ok

    def test_multiple_issues_accumulate(self) -> None:
        # model_construct() bypasses pydantic to inject both an empty default_model
        # and an unknown capability key in a single RoutingPolicy instance.
        policy = RoutingPolicy.model_construct(
            default_model="",
            capabilities={"bad_cap": ["model-a"]},
        )
        result = validate_routing(_reg("model-a"), policy, providers=None)
        assert not result.ok
        assert len(result.issues) >= 2

    def test_validation_result_has_both_fields(self) -> None:
        result = RoutingValidationResult(ok=True, issues=["a"], warnings=["w"])
        assert result.issues == ["a"]
        assert result.warnings == ["w"]

    def test_costly_capability_override_produces_a_warning(self) -> None:
        """BB-93: validate_routing() must actually invoke costly_capability_warnings()
        with the registry — a costly per-capability override must surface as a
        non-blocking warning, not be silently dropped by an arity TypeError."""
        policy = RoutingPolicy(
            default_model="cheap-model",
            capabilities={"reasoning": ["pricey-model"]},
        )
        result = validate_routing(_priced_reg(), policy, providers=None)

        assert result.ok, "a costly override is advisory, never blocking"
        assert result.warnings, (
            "BB-30 cost guard produced no warning — costly_capability_warnings() was "
            "never actually invoked by validate_routing()"
        )
        assert any("pricey-model" in w for w in result.warnings)
