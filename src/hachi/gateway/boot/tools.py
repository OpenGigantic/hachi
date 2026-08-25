"""T-44 stage: declarative tool registration (old step 8b), the largest single
block moved by this task.

Moved verbatim from `boot_agent:3234-3487` -- the `BootDeps` construction (the
old `_make_deps()` closure, inlined here since it had exactly one call site), the
`TOOL_SPECS` table, `ToolRegistrar().install(...)`, the native-capability
registration loop, `tool_registry.classify()`/`reclassify_by_prefix()`, and the
`domain_groups`/`skill_group_names` derivation `boot.assembly.build_prompt`
depends on.

Three `ToolSpec.register_fn` lambdas closed over `boot_agent` locals that were
NOT part of `BootDeps` -- exactly the closure hazard T-44 exists to eliminate,
found by grepping every lambda in this table for free variables (T-44 section
12's own warning) rather than assuming `BootDeps` was the complete picture:

* `google`  closed over `action_approver` (`perms.action_approver` here).
* `rest_apis` closed over `rest_adapter` (`infra.rest_adapter` here).
* `delegation` closed over the four `max_*` budget locals (`limits.*` here).

All three are now ordinary closures over THIS function's own parameters --
local, single-hop, and visible in the signature below, not a reach hundreds of
lines into an enclosing 1,245-line function.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hachi.core.approvals import ActionApprover
from hachi.core.integrations import IntegrationRuntime
from hachi.core.mcp_client import MCPManager
from hachi.gateway.tool_registration import BootDeps, ToolRegistrar, ToolSpec
from hachi.gateway.workspace import get_skills_dir
from hachi.jobs import JobQueue
from hachi.og_engine.engine import SkillEngine
from hachi.tools.browser_tools import register_browser_tools
from hachi.tools.config_tools import register_config_tools
from hachi.tools.delegation_tools import register_delegation_tools
from hachi.tools.file_tools import register_file_tools
from hachi.tools.google_tools import register_google_tools
from hachi.tools.memory_tools import register_memory_tools, register_session_tools
from hachi.tools.messaging_tools import register_messaging_tools
from hachi.tools.notification_tools import register_notification_tools
from hachi.tools.og_tools import USE_TOOLS_SCHEMA, _make_use_tools, register_og_tools
from hachi.tools.os_tools import register_os_tools
from hachi.tools.presentation_tools import register_presentation_tools
from hachi.tools.sandbox_tools import register_sandbox_tools
from hachi.tools.schedule_tools import register_schedule_tools
from hachi.tools.vision_tools import register_vision_tools
from hachi.tools.voice_tools import register_voice_tools
from hachi.tools.workflow_tools import register_workflow_tools

from .stages import (
    CapabilitiesStage,
    IntegrationsStage,
    LimitsStage,
    LLMProviderStage,
    MCPStage,
    MemoryStage,
    PermissionsStage,
    RoutingStage,
    SkillEngineStage,
    ToolInfraStage,
    ToolsStage,
)

if TYPE_CHECKING:
    from hachi.gateway.orchestrator import CapabilityRegistry

RegisterSearchIfAvailable = Callable[[BootDeps], Any]
RegisterCapabilitySetupStub = Callable[[Any, Any], None]


def register_tools(
    *,
    perms: PermissionsStage,
    skills: SkillEngineStage,
    integrations: IntegrationsStage,
    mcp: MCPStage,
    memory: MemoryStage,
    infra: ToolInfraStage,
    providers: LLMProviderStage,
    routing: RoutingStage,
    limits: LimitsStage,
    capabilities: CapabilitiesStage,
    provider_kind: str,
    job_queue: JobQueue,
    relay_event: Callable[..., None],
    emit: Callable[[str, dict[str, Any]], None],
    register_search_if_available: RegisterSearchIfAvailable,
    register_capability_setup_stub: RegisterCapabilitySetupStub,
    core_tool_names: frozenset[str],
) -> ToolsStage:
    """Old step 8b, moved verbatim -- see module docstring for the closure fixes.

    Args:
        register_search_if_available: orchestrator-local `_register_search_if_available`.
        register_capability_setup_stub: orchestrator-local `_register_capability_setup_stub`.
        core_tool_names: orchestrator-local `_CORE_TOOL_NAMES`.
    """
    tool_registry = infra.tool_registry
    gate = perms.runtime_gate
    action_approver: ActionApprover = perms.action_approver
    rest_adapter = infra.rest_adapter
    skill_engine: SkillEngine = skills.skill_engine
    mcp_manager: MCPManager = mcp.mcp_manager
    integration_runtime: IntegrationRuntime = integrations.integrations

    deps = BootDeps(
        gate=gate,
        skill_engine=skill_engine,
        checkpoint_manager=infra.checkpoint_manager,
        vault=memory.vault,
        job_queue=job_queue,
        session_store=infra.session_store,
        integrations=integration_runtime,
        mcp_manager=mcp_manager,
        scheduler=infra.scheduler,
        relay_event=relay_event,
        sub_agent_llm=providers.sub_agent_client,
        cost_tracker=routing.cost_tracker,
        provider_kind=provider_kind,
        secret_store=providers.secret_store,
        tool_registry=tool_registry,
        lease_manager=infra.lease_manager,
        router=routing.model_router,
        provider_registry=routing.provider_registry,
        session_llm=providers.client,
    )

    TOOL_SPECS: list[ToolSpec] = [
        ToolSpec(
            name="os",
            register_fn=lambda d: register_os_tools(
                d.tool_registry, d.gate, checkpoints=d.checkpoint_manager
            ),
        ),
        ToolSpec(
            name="og",
            register_fn=lambda d: register_og_tools(d.tool_registry, d.skill_engine),
        ),
        ToolSpec(
            # Lazy tool-loading meta-tool (core): lets the model unlock optional groups.
            name="use_tools",
            register_fn=lambda d: d.tool_registry.register(
                USE_TOOLS_SCHEMA, _make_use_tools(d.tool_registry), group="core"
            ),
        ),
        ToolSpec(name="workflow", register_fn=lambda d: register_workflow_tools(d.tool_registry)),
        ToolSpec(
            name="sandbox",
            register_fn=lambda d: register_sandbox_tools(d.tool_registry, d.gate),
        ),
        ToolSpec(
            # Headless browser automation (optional: needs `hachi-og[browser]` + a browser
            # binary). The controller is lazy — no browser launches until the agent first calls
            # browse_web. Return value is captured from registration_results["browser"].
            name="browser",
            register_fn=lambda d: register_browser_tools(d.tool_registry, d.gate),
        ),
        ToolSpec(
            # Outbound messaging (Slack/Discord/WhatsApp). Credentials resolve lazily at call
            # time, so these register cleanly even with no token set (return a "not configured"
            # hint until then).
            name="messaging",
            register_fn=lambda d: register_messaging_tools(d.tool_registry),
        ),
        ToolSpec(
            # Desktop push notifications + SMTP email digests. Zero config for desktop;
            # email requires HACHI_SMTP_* env vars (degrades gracefully if unset).
            name="notifications",
            register_fn=lambda d: register_notification_tools(d.tool_registry),
        ),
        ToolSpec(
            # Vision input — view local images.  Capability-routed (doc 20 §3):
            # session model with VISION → inline; else router.select(VISION) → routed;
            # else graceful text fallback.
            name="vision",
            register_fn=lambda d: register_vision_tools(
                d.tool_registry,
                d.gate,
                provider_kind=d.provider_kind,
                session_client=d.session_llm,
                router=d.router,
            ),
        ),
        ToolSpec(
            # Voice input — transcribe/synthesize audio.  Provider resolved via
            # ProviderRegistry.get_speech() (doc 19 §3); falls back to SpeechToText.from_env()
            # when registry is unavailable (back-compat).
            name="voice",
            register_fn=lambda d: register_voice_tools(
                d.tool_registry, d.gate, provider_registry=d.provider_registry
            ),
        ),
        ToolSpec(
            # Web search + URL extraction (Initiative A — Search Platform, doc 17).
            # Registered only when a SearchProvider is available in the ProviderRegistry.
            # Degrades gracefully to a no-op when no provider / key is configured.
            name="search",
            register_fn=lambda d: register_search_if_available(d),
        ),
        ToolSpec(
            # File / attachment platform (Initiative E, Slice 2.4).
            # attach_file, list_attachments, read_attachment, search_attachments.
            # Permission-gated like os_tools; extraction runs as durable job handler.
            name="files",
            register_fn=lambda d: register_file_tools(d.tool_registry, d.gate),
        ),
        ToolSpec(
            # Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Contacts, Meet, Tasks).
            # Only mounted when GOOGLE_CLIENT_ID is set; degrades to a friendly stub otherwise.
            # The session-scoped action_approver gates destructive leaf tools (send_email,
            # trash_file, etc.) through the appropriate human-approval path.
            name="google",
            register_fn=lambda d: register_google_tools(d.tool_registry, approver=action_approver),
        ),
        ToolSpec(
            # Presentation generation — build .pptx from natural language.
            name="presentation",
            register_fn=lambda d: register_presentation_tools(d.tool_registry, d.gate),
        ),
        ToolSpec(
            name="schedule",
            register_fn=lambda d: register_schedule_tools(d.tool_registry, d.scheduler),
        ),
        ToolSpec(
            # Register Universal REST APIs parsed from .og files.
            name="rest_apis",
            register_fn=lambda d: [
                rest_adapter.register_node_apis(node) for node in d.skill_engine.get_all_nodes()
            ],
        ),
        ToolSpec(
            name="memory",
            register_fn=lambda d: register_memory_tools(
                d.tool_registry, d.vault, job_queue=d.job_queue
            ),
        ),
        ToolSpec(
            name="session",
            register_fn=lambda d: register_session_tools(d.tool_registry, d.session_store),
        ),
        ToolSpec(
            name="integration_tools",
            register_fn=lambda d: d.integrations.register_tools(d.tool_registry),
        ),
        ToolSpec(
            name="mcp_tools",
            register_fn=lambda d: d.mcp_manager.register_tools(d.tool_registry),
        ),
        ToolSpec(
            # Agent-accessible configuration + MCP management tools.
            # Registered AFTER MCP manager so list_mcp_servers sees all nodes.
            # T-68: inject the gateway's fuller get_skills_dir (which also runs
            # the first-run workspace bootstrap) — tools/config_tools.py itself
            # must not import hachi.gateway, so its default collaborator is its
            # own thinner get_skills_dir (mkdir-only, no bootstrap).
            name="config",
            after={"mcp_tools"},
            register_fn=lambda d: register_config_tools(
                d.tool_registry, gate=d.gate, skills_dir_provider=get_skills_dir
            ),
        ),
        ToolSpec(
            # MUST be LAST — needs full parent registry.
            # Pass skill_engine so sub-agent OG tools are rebound to a role-scoped
            # engine view, preventing sub-agents from reading scope=orchestrator nodes.
            name="delegation",
            last=True,
            register_fn=lambda d: register_delegation_tools(
                d.tool_registry,
                d.gate,
                d.relay_event,
                d.vault,
                job_queue=d.job_queue,
                sub_agent_llm=d.sub_agent_llm,
                cost_tracker=d.cost_tracker,
                skill_engine=d.skill_engine,
                lease_manager=d.lease_manager,
                # S2: sub-agents inherit the parent session's configured budget caps
                # by default so a delegated task can never silently outspend them.
                max_cost_usd=limits.max_session_cost_usd,
                max_tokens=limits.max_session_tokens,
                max_cost_per_turn=limits.max_turn_cost_usd,
                max_tokens_per_turn=limits.max_turn_tokens,
            ),
        ),
    ]

    registrar = ToolRegistrar()
    registration_results = registrar.install(TOOL_SPECS, deps)
    browser_controller = registration_results.get("browser")

    # Register native capability tools lazily, with setup stubs for incomplete states.
    native_registration_count = 0
    capability_registry: CapabilityRegistry = capabilities.registry
    for descriptor in capability_registry.iter_enabled():
        if descriptor.kind != "native_integration":
            continue
        if not descriptor.mounts_tools:
            # BB-103: tools are mounted elsewhere (google -> TOOL_SPECS, which carries
            # the session approver). The descriptor still drives status_snapshot() and
            # install_optional_skill_pack() above — only the mount step is skipped, for
            # both the success and the not-available/not-configured stub branch, so a
            # configured-but-not-connected Google does not also get this loop's generic
            # stub competing with google_tools.py's own purpose-written one.
            continue
        if not descriptor.available or not descriptor.configured:
            register_capability_setup_stub(tool_registry, descriptor)
            continue

        try:
            instance = capability_registry.instantiate(descriptor.id)
        except Exception as exc:
            register_capability_setup_stub(tool_registry, descriptor)
            from hachi.core.runtime_events import MCP_SERVER_FAILED

            emit(
                MCP_SERVER_FAILED,
                {
                    "server": descriptor.id,
                    "reason": f"capability init failed: {exc}",
                },
            )
            continue

        if isinstance(instance, tuple):
            for sub_instance in instance:
                if hasattr(sub_instance, "register_tools"):
                    sub_instance.register_tools(tool_registry)
                    native_registration_count += 1
        elif hasattr(instance, "register_tools"):
            instance.register_tools(tool_registry)
            native_registration_count += 1

    # Classify tools into lazy-loading groups now that every tool is registered.
    # T-39/BB-112: non-core tools now declare group="..." at their own register(...)
    # call (mirroring the pre-existing per-skill REST tool / use_tools pattern), so
    # classify() has nothing left to assign via name lookup — group_by_name={} is
    # kept (not dropped from the signature) so a future dynamic table has somewhere
    # to plug in without a call-site change.
    tool_registry.classify(
        core_names=set(core_tool_names),
        group_by_name={},
        # Google integration tools all share the "google_" prefix → lazy "google" group.
        prefix_groups={"google_": "google"},
        default="integrations",
    )

    # BB-32: MCP tools land in "integrations" (never activated) because classify()
    # doesn't know MCP server names at call time.  Reclassify them into "mcp" so
    # activating that group exposes them to the LLM.
    _mcp_prefixes = {f"{name}_": "mcp" for name in mcp_manager._servers}
    has_live_mcp = bool(_mcp_prefixes)
    if has_live_mcp:
        tool_registry.reclassify_by_prefix(_mcp_prefixes)

    # Tell the model which optional domain groups it can unlock on demand. Skill
    # groups are omitted here — load_skill auto-activates those — so this lists only
    # the static domain groups (e.g. schedule, sandbox, browser, workflow).
    skill_group_names = frozenset(node.name for node in skill_engine.get_all_nodes())
    domain_groups = tuple(sorted(tool_registry.available_groups() - {"core"} - skill_group_names))

    return ToolsStage(
        tool_registry=tool_registry,
        browser_controller=browser_controller,
        native_registration_count=native_registration_count,
        domain_groups=domain_groups,
        skill_group_names=skill_group_names,
        has_live_mcp=has_live_mcp,
    )
