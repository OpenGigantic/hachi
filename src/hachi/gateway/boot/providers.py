"""T-44 stages: the LLM provider (step 6) and the optional routing layer (step 6b).

Moved verbatim from `boot_agent:2899-3005`. `build_routing`'s try/except is the
one T-26/BB-104 hardened (promoted the failure log to WARNING, named the
exception type, added the `routing.boot_failed` event, hoisted the six routing
imports out of the guarded `try`) -- none of that behaviour changes here, only
its home. `tests/test_routing_boot_failure.py`'s structural (AST) assertions
were re-anchored to this function; see that file's own module docstring for why,
and this task's report for the full writeup (T-26 §9 forbade exactly this
extraction, deferring it to "a different task" -- this one).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from hachi.core.cost import CostTracker
from hachi.core.llm import create_llm_client
from hachi.core.sub_agent import DEFAULT_SUB_AGENT_MODEL

from .stages import LLMProviderStage, RoutingStage

logger = logging.getLogger(__name__)


def build_llm_provider(
    provider_config: dict[str, Any], *, max_tokens: int, secret_store: Any
) -> LLMProviderStage:
    """Moved verbatim from `:2899-2912` (old step 6).

    Args:
        secret_store: T-58 — previously created here via
            `create_default_secret_store()`; now built by the composition
            root BEFORE this stage runs (so a CredentialVault derived from it
            can be threaded into `boot.environment.build_integrations`,
            several stages earlier) and passed in. Still exactly one
            secret store for the whole boot — only its construction site
            moved.
    """
    client = create_llm_client(provider_config, secret_store=secret_store, max_tokens=max_tokens)

    # Sub-agents reuse the SAME resolved provider credentials as the main agent so
    # delegation never fails with "ANTHROPIC_API_KEY must be provided". For Anthropic
    # we drop to a cheaper model to keep delegation cost-aware; other providers keep
    # the user's configured model.
    sub_agent_provider = dict(provider_config)
    if str(provider_config.get("kind", "anthropic")).lower() == "anthropic":
        sub_agent_provider["model"] = DEFAULT_SUB_AGENT_MODEL
    sub_agent_client = create_llm_client(
        sub_agent_provider, secret_store=secret_store, max_tokens=4096
    )

    return LLMProviderStage(
        secret_store=secret_store,
        client=client,
        sub_agent_client=sub_agent_client,
        sub_agent_provider=sub_agent_provider,
    )


def build_routing(
    config: dict[str, Any],
    *,
    secret_store: Any,
    shared: Any,
    emit_runtime_event: Callable[[str, dict[str, Any]], None],
) -> RoutingStage:
    """Moved verbatim from `:2914-3005` (old step 6b + the cost_tracker derivation).

    `shared` is the `ProcessSharedServices` singleton (`SkillEngineStage.shared`)
    -- this stage may mutate its `routing_store` attribute exactly as the
    original did, the first time a routing layer boots in this process.
    """
    # BB-104: these six imports live OUTSIDE the try below (deliberately, still
    # function-local -- matching the original's own reasoning verbatim) so an
    # ImportError from a broken install / renamed module / typo raises loudly at
    # boot instead of being swallowed as "your [routing] config is bad". Only
    # the construction calls are guarded.
    from hachi.core.providers.registry import ProviderRegistry
    from hachi.core.resilience import RetryPolicy
    from hachi.core.routing.policy import RoutingPolicy
    from hachi.core.routing.registry import ModelRegistry
    from hachi.core.routing.router import ModelRouter
    from hachi.core.settings import ResolvedConfig

    _model_router: Any = None
    _provider_registry: Any = None
    # Pre-initialised (unlike the original's implicit "never bound if the try fails
    # early") only because this function must always return a RoutingStage: when
    # `_model_router` ends up None, `_routing_policy` is never read by any caller
    # (see build_brain_loop's `if routing.model_router is not None:` guard), exactly
    # mirroring the original's own invariant that `_routing_policy` was never read
    # unless `_model_router is not None` proved the try had already assigned it.
    _routing_policy: Any = None
    try:
        _resolved_cfg = ResolvedConfig.model_validate(config)
        _provider_registry = ProviderRegistry.from_config(_resolved_cfg, secret_store)
        _model_registry = ModelRegistry.build(_resolved_cfg, _provider_registry)
        _routing_policy = RoutingPolicy.from_config(_resolved_cfg)
        # Apply auto-policy if requested (amendment A1).
        if _routing_policy.auto:
            _routing_policy = _routing_policy.derive_auto_policy(_model_registry)
        _retry_policy = RetryPolicy.from_config(_resolved_cfg)
        _model_router = ModelRouter(
            registry=_model_registry,
            policy=_routing_policy,
            providers=_provider_registry,
            resilience=_retry_policy,
        )

        # Phase 3: ensure RoutingConfigStore exists on the shared singleton and holds
        # the boot snapshot so sessions can adopt future hot-reload updates at turn
        # boundaries without a restart.
        if getattr(shared, "routing_store", None) is None:
            from hachi.core.routing.snapshot import RoutingSnapshot
            from hachi.gateway.routing_store import RoutingConfigStore

            _rcs = RoutingConfigStore()
            _boot_snap = RoutingSnapshot(
                registry=_model_registry,
                policy=_routing_policy,
                resilience=_retry_policy,
                version=0,
                source="boot",
            )
            _rcs.init(_boot_snap)
            shared.routing_store = _rcs

        logger.info(
            "Routing boot: %d models registered; default=%s; auto=%s",
            len(_model_registry.all()),
            _routing_policy.default_model,
            _routing_policy.auto,
        )
        # Cost-governance guard (BB-30): surface the silent "every chat turn routed to
        # the priciest model" trap loudly at boot instead of letting it hide in the
        # routing table. Never changes routing — an explicit choice is respected.
        for _cost_warning in _routing_policy.costly_capability_warnings(_model_registry):
            logger.warning(_cost_warning)
            emit_runtime_event("cost.routing_warning", {"message": _cost_warning})
    except Exception as exc:
        # BB-104: promoted to WARNING (was DEBUG — strictly quieter than the success
        # path's INFO above) with the exception type named, plus a runtime event, so
        # the failure is as loud as the fallback it causes is significant.
        logger.warning(
            "Routing boot: failed to initialise routing layer (%s: %s); "
            "falling back to the direct client — [routing] configuration is NOT active",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        emit_runtime_event(
            "routing.boot_failed",
            {"error": f"{type(exc).__name__}: {exc}"},
        )
        _model_router = None

    # Shared per-session token/cost tracker — used by the main loop AND threaded
    # into delegated sub-agents so their spend is included in the session total.
    # Pass the model registry so registry-backed pricing is used (registry seeds from
    # cost.py values, so no drift).
    _registry_for_cost = _model_router._registry if _model_router is not None else None
    cost_tracker = CostTracker(registry=_registry_for_cost)

    return RoutingStage(
        model_router=_model_router,
        provider_registry=_provider_registry,
        routing_policy=_routing_policy,
        cost_tracker=cost_tracker,
    )
