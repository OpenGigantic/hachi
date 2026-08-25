"""T-44 stages: the system prompt and the execution loop -- the two stages that
prove T-44 section 3.3's central claim.

Moved verbatim from `boot_agent`'s old step "5.5 Assemble the stable base system
prompt" (`:3489-3497`) and step "9. Instantiate the Brain Loop" (`:3515-3558`).

`build_prompt` takes `ToolsStage` (the `register_tools` result) as an explicit
input -- not a "step 5" ingredients bundle alone -- because `tool_groups` does
not exist until every `ToolSpec` has registered and `tool_registry.classify()`
has run. That is the one dependency edge the old numbering ("5.5" sits right
after "5") actively lied about: the physical file already had this stage
running *after* tool registration (at `:3489`, ~285 lines below `:3204`'s "8b.");
this module just makes that a type signature instead of a coincidence of where
someone left the code.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from hachi.core.loop import ExecutionLoop
from hachi.core.prompt_composer import build_stable_base

from .stages import (
    BrainLoopStage,
    LimitsStage,
    MemoryStage,
    PromptIngredientsStage,
    PromptStage,
    RoutingStage,
    ToolsStage,
)

logger = logging.getLogger(__name__)


def build_prompt(
    *,
    brain_prompt: str,
    skill_count: int,
    ingredients: PromptIngredientsStage,
    tools: ToolsStage,
) -> PromptStage:
    """Old step 5.5 (`:3489-3497`), moved verbatim.

    Args:
        tools: the `register_tools` result -- `tools.domain_groups` is the
            genuinely tool-registration-downstream input (see module docstring).
    """
    system_prompt = build_stable_base(
        brain_text=brain_prompt,
        skill_count=skill_count,
        integration_count=ingredients.total_tools,
        permission_block=ingredients.permission_block,
        capability_block=ingredients.capability_block,
        tool_groups=list(tools.domain_groups) if tools.domain_groups else None,
    )
    return PromptStage(system_prompt=system_prompt)


def build_brain_loop(
    *,
    client: Any,
    routing: RoutingStage,
    tools: ToolsStage,
    memory: MemoryStage,
    prompt: PromptStage,
    relay_event: Callable[..., None],
    skill_engine: Any,
    limits: LimitsStage,
    routing_store: Any,
    model_name: str,
) -> BrainLoopStage:
    """Old step 9 (`:3515-3558`), moved verbatim.

    Args:
        routing_store: `getattr(shared, "routing_store", None)`.
        model_name: the model name resolved so far (from `ConfigStage`) --
            echoed back updated if routing resolves a different default model
            (see `BrainLoopStage`'s docstring).
    """
    # (max_daily_cost_usd / max_session_cost_usd / max_session_tokens / max_turn_cost_usd /
    # max_turn_tokens were computed earlier, before _make_deps, so S2 could thread them
    # into register_delegation_tools as well.)
    # When the router is available, default_client() returns the configured model's
    # resilient client; the loop's llm_client is unchanged (back-compat).
    _loop_client = client
    if routing.model_router is not None:
        try:
            _loop_client = routing.model_router.default_client()
            # Issue 11/9: routing succeeded — report the routed default model as the
            # session model so the "last used model" badge and /spend reflect what
            # actually runs (previously these reported provider.model = the primary).
            model_name = routing.routing_policy.default_model or model_name
        except Exception as _route_exc:
            # The routed default model's provider is not usable (missing API key,
            # unknown/misspelled model id, etc.). Fall back to the primary client but
            # do NOT misreport the model — keep model_name as the primary provider's
            # model and warn loudly so the user learns their selection didn't take effect.
            logger.warning(
                "Routing: requested default model %r is not usable (%s) — falling back "
                "to %r. Configure that provider's API key or fix the model id in "
                "[routing]/[providers].",
                getattr(routing.routing_policy, "default_model", "?"),
                _route_exc,
                model_name,
            )
            _loop_client = client

    loop = ExecutionLoop(
        llm_client=_loop_client,
        registry=tools.tool_registry,
        memory=memory.memory,
        base_system_prompt=prompt.system_prompt,
        on_event=relay_event,
        skill_engine=skill_engine,
        cost_tracker=routing.cost_tracker,
        max_cost_usd=limits.max_session_cost_usd,
        max_tokens=limits.max_session_tokens,
        max_cost_per_turn=limits.max_turn_cost_usd,
        max_tokens_per_turn=limits.max_turn_tokens,
        router=routing.model_router,
        routing_store=routing_store,
    )

    # BB-32: if live MCP servers were present at boot, persist "mcp" as an
    # always-on group so MCP tools are visible to the LLM every turn without
    # requiring a use_tools call.
    if tools.has_live_mcp:
        loop._persistent_groups.add("mcp")

    return BrainLoopStage(loop=loop, model_name=model_name)
