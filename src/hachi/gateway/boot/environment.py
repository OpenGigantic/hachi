"""T-44 stages: capabilities, the skill engine, direct integrations, MCP, and the
early "stable-base ingredients" collection.

Moved verbatim from `boot_agent`'s old steps 2-5 (`:2790-2887`). These four
stages are genuinely independent of tool registration (step 8b) despite two of
them being numbered "5" and sitting physically ABOVE the tool-registration block
-- see `collect_prompt_ingredients`'s docstring and the T-44 report's dependency
graph for the one field (`tool_groups`) that is NOT available yet at this point.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hachi.core.credential_vault import CredentialVault
from hachi.core.integrations import IntegrationRuntime
from hachi.core.mcp_client import MCPManager
from hachi.gateway.workspace import get_skills_dir
from hachi.og_engine.engine import SkillEngine

from .stages import (
    CapabilitiesStage,
    IntegrationsStage,
    MCPStage,
    PromptIngredientsStage,
    SkillEngineStage,
)

if TYPE_CHECKING:
    from hachi.gateway.orchestrator import CapabilityRegistry

BuildCapabilityRegistry = Callable[[dict[str, Any]], "CapabilityRegistry"]
InstallOptionalSkillPack = Callable[[str], Any]
ConfigureMCPServers = Callable[..., None]


def build_capabilities(
    config: dict[str, Any],
    *,
    build_registry: BuildCapabilityRegistry,
    install_pack: InstallOptionalSkillPack,
) -> CapabilitiesStage:
    """Moved verbatim from `:2790-2793`.

    Args:
        build_registry: orchestrator-local `_build_capability_registry`.
        install_pack: `hachi.gateway.workspace.install_optional_skill_pack`.
    """
    registry = build_registry(config)
    enabled_capabilities = [item.id for item in registry.iter_enabled()]
    for capability_id in enabled_capabilities:
        install_pack(capability_id)
    return CapabilitiesStage(registry=registry)


def build_skill_engine(
    config: dict[str, Any],
    *,
    data_dir: str,
    event_bus: Any,
) -> SkillEngineStage:
    """Moved verbatim from `:2808-2824` (old step 2).

    Args:
        data_dir: `str(get_hachi_paths().data_dir)`.
        event_bus: the boot's `_event_bus` (still `None` unless `[events]
            bus_funnel=true` -- that decision is made by the event-plumbing
            block, which stays inline in `boot_agent`; see the package
            docstring).
    """
    from hachi.gateway.shared_services import ProcessSharedServices

    shared = ProcessSharedServices.get_or_create(config, data_dir)
    # Slice 2.2: publish the EventBus on the shared singleton so resume.py and
    # the observability sink (slice 2.3) can find it via ProcessSharedServices.instance().
    if event_bus is not None:
        shared.event_bus = event_bus

    if shared.skill_engine is not None:
        skill_engine = shared.skill_engine
    else:
        # Fallback: create a fresh engine (should not happen after the first boot).
        skill_engine = SkillEngine(agent_role="orchestrator")
        skills_dir = get_skills_dir()
        skill_engine.load_directory(skills_dir)

    skill_count = skill_engine.node_count
    return SkillEngineStage(skill_engine=skill_engine, skill_count=skill_count, shared=shared)


def build_integrations(
    skill_engine: SkillEngine, *, vault: CredentialVault | None = None
) -> IntegrationsStage:
    """Moved verbatim from `:2836-2845` (old step 3).

    Args:
        vault: T-58 — the CredentialVault so role=integration/type=smtp nodes
            can resolve `auth_email_key`/`auth_password_key` from it instead
            of a role=env node's plaintext `::dense` block (see
            `hachi.core.integrations.IntegrationRuntime`). The composition
            root (`orchestrator.py`) now constructs this vault BEFORE this
            stage runs — three stages earlier than it used to be built —
            specifically so it can be threaded in here; see the construction
            site's comment for why.
    """
    integrations = IntegrationRuntime(vault=vault)
    integration_count = 0

    for node in skill_engine.get_nodes_by_role("env"):
        integrations.load_env_node(node)

    for node in skill_engine.get_nodes_by_role("integration"):
        if integrations.setup_integration(node):
            integration_count += 1

    return IntegrationsStage(integrations=integrations, integration_count=integration_count)


def build_mcp(
    skill_engine: SkillEngine,
    *,
    gate: Any,
    emit: Callable[[str, dict[str, Any]], None],
    config: dict[str, Any],
    configure: ConfigureMCPServers,
) -> MCPStage:
    """Moved verbatim from `:2859-2861` (old step 4).

    Args:
        configure: orchestrator-local `_configure_mcp_servers`.
    """
    # Issue 4: pass the raw config dict so TOML [mcp_servers] entries are also started.
    mcp_manager = MCPManager()
    configure(skill_engine, mcp_manager, gate, emit, config=config)
    mcp_tool_count = mcp_manager.total_tool_count
    return MCPStage(mcp_manager=mcp_manager, mcp_tool_count=mcp_tool_count)


def collect_prompt_ingredients(
    *,
    integration_count: int,
    mcp_tool_count: int,
    runtime_gate: Any,
    capability_registry: CapabilityRegistry,
) -> PromptIngredientsStage:
    """Moved verbatim from `:2883-2887` (old step 5, "Collect stable-base ingredients").

    Despite the "5", every input here is already available immediately after
    `IntegrationsStage`/`MCPStage` -- none of it depends on tool registration.
    See `PromptIngredientsStage`'s docstring and the T-44 report.
    """
    total_tools = integration_count + mcp_tool_count
    permission_block = runtime_gate.get_system_prompt_injection()
    capability_snapshot = capability_registry.status_snapshot()
    capability_block = str(capability_snapshot) if capability_snapshot else ""
    return PromptIngredientsStage(
        total_tools=total_tools,
        permission_block=permission_block,
        capability_snapshot=capability_snapshot,
        capability_block=capability_block,
    )
