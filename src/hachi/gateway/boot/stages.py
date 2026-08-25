"""Frozen result types for the `hachi.gateway.boot` staged builders (T-44).

Matches the repo's existing idiom for typed boot/read snapshots
(`core/runtime_read_models.py` uses `@dataclass(frozen=True, slots=True)` for all
14 of its snapshot types). A frozen field can still hold a reference to a mutable
collaborator (e.g. `ToolsStage.tool_registry` is a live `ToolRegistry` that a
LATER stage's caller may go on to mutate further) -- frozen means the *binding*
cannot be reassigned, not that every referenced object is itself immutable. That
is deliberate and matches the spec's own sketch (T-44 section 5).

Nothing in this module imports from `hachi.gateway.orchestrator` at runtime --
see the package docstring for why that direction is one-way. The one type this
module needs that is not defined in `hachi.core`/`hachi.jobs`/`hachi.tools`
(`CapabilityRegistry`) is imported under `TYPE_CHECKING` only, so mypy sees it but
nothing actually executes the import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hachi.core.approvals import ActionApprover
from hachi.core.checkpoints import CheckpointManager
from hachi.core.cost import CostTracker
from hachi.core.credential_vault import CredentialVault
from hachi.core.integrations import IntegrationRuntime
from hachi.core.loop import ExecutionLoop, ToolRegistry
from hachi.core.mcp_client import MCPManager
from hachi.core.memory import MemoryManager
from hachi.core.permissions import PermissionGate
from hachi.core.rest_adapter import UniversalRestAdapter
from hachi.core.session_store import SessionStore
from hachi.core.settings import EventsSettings, JobsSettings, MemorySettings, ObservabilitySettings
from hachi.jobs.scheduler import Scheduler
from hachi.og_engine.engine import SkillEngine

if TYPE_CHECKING:
    from hachi.gateway.orchestrator import CapabilityRegistry


@dataclass(frozen=True, slots=True)
class PermissionsStage:
    """Step 0's output: the device-permission gate and the session approver.

    `runtime_gate` is `gate` itself when no `on_permission_request` handler is
    connected, or an `InteractivePermissionGate` wrapping it otherwise -- callers
    that need to distinguish the two should check `isinstance`.
    """

    gate: PermissionGate
    runtime_gate: Any  # PermissionGate | InteractivePermissionGate
    action_approver: ActionApprover


@dataclass(frozen=True, slots=True)
class ConfigStage:
    """Step 1's output: the loaded config, brain prompt, and its parsed sub-sections.

    `provider_config` / `model_name` / `max_tokens` are derived from `config` here
    (not re-derived by every later stage) so there is one place that reads the
    `[provider]`/`[agent]` fallback chain.
    """

    config: dict[str, Any]
    brain_prompt: str
    provider_config: dict[str, Any]
    model_name: str
    max_tokens: int
    events_cfg: EventsSettings
    jobs_cfg: JobsSettings
    obs_cfg: ObservabilitySettings


@dataclass(frozen=True, slots=True)
class CapabilitiesStage:
    """The optional native-capability registry (Google Workspace, etc.) and its
    already-installed bundled skill packs."""

    registry: CapabilityRegistry


@dataclass(frozen=True, slots=True)
class SkillEngineStage:
    """Step 2's output: the (possibly process-shared) skill engine.

    `shared` is the `ProcessSharedServices` singleton this boot resolved or
    created -- later stages read/write several of its other attributes (vault,
    memory_writer, graph_index, routing_store, session_store), so it is threaded
    through explicitly rather than re-resolved by each one.
    """

    skill_engine: SkillEngine
    skill_count: int
    shared: Any  # hachi.gateway.shared_services.ProcessSharedServices


@dataclass(frozen=True, slots=True)
class IntegrationsStage:
    """Step 3's output: direct SMTP/HTTP integrations loaded from `.og` env/integration nodes."""

    integrations: IntegrationRuntime
    integration_count: int


@dataclass(frozen=True, slots=True)
class MCPStage:
    """Step 4's output: the configured MCP manager and how many tools it exposes."""

    mcp_manager: MCPManager
    mcp_tool_count: int


@dataclass(frozen=True, slots=True)
class PromptIngredientsStage:
    """Step 5's output ("collect stable-base ingredients").

    Despite living at old step "5", every one of these is available immediately
    after `IntegrationsStage`/`MCPStage` -- NONE of them actually depend on tool
    registration (see the T-44 report's "false claim" writeup: the prompt's
    numeric tool count is fixed here, before `register_tools` ever runs; only the
    tool GROUP NAMES `PromptStage` embeds are genuinely downstream of it).
    """

    total_tools: int
    permission_block: str
    capability_snapshot: Any
    capability_block: str


@dataclass(frozen=True, slots=True)
class LLMProviderStage:
    """Step 6's output: the resolved secret store and the main + sub-agent LLM clients."""

    secret_store: Any
    client: Any
    sub_agent_client: Any
    sub_agent_provider: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RoutingStage:
    """Step 6b's output: the optional routing layer, or the back-compat fallback.

    `model_router` / `provider_registry` / `routing_policy` are all `None` when
    `[routing]` is absent or fails to build (the try/except's documented
    fallback -- see BB-104 / T-26). `cost_tracker` is always real: it is
    constructed from `model_router`'s registry when routing built, or with no
    registry otherwise -- CostTracker itself tolerates both.
    """

    model_router: Any
    provider_registry: Any
    routing_policy: Any
    cost_tracker: CostTracker


@dataclass(frozen=True, slots=True)
class MemoryStage:
    """Step 7's output: the memory manager and the vault/graph collaborators bound to it."""

    memory: MemoryManager
    vault: Any  # MemoryVault | ProviderBackedVault | None
    memory_settings: MemorySettings
    graph_index: Any
    is_fts5_only: bool


@dataclass(frozen=True, slots=True)
class ToolInfraStage:
    """Step 8's output: the infrastructure objects tool registration needs to exist first."""

    tool_registry: ToolRegistry
    checkpoint_manager: CheckpointManager
    scheduler: Scheduler
    session_store: SessionStore
    credential_vault: CredentialVault
    rest_adapter: UniversalRestAdapter
    lease_manager: Any


@dataclass(frozen=True, slots=True)
class LimitsStage:
    """Per-session safety caps (0/None = unlimited), read once and shared by
    `register_tools` (delegation sub-agent budgets) and `build_brain_loop` (the
    session's own budget) -- see T-44's dependency-graph report for why this is
    its own stage rather than being computed twice."""

    max_daily_cost_usd: float
    max_session_cost_usd: float
    max_session_tokens: int
    max_turn_cost_usd: float
    max_turn_tokens: int


@dataclass(frozen=True, slots=True)
class ToolsStage:
    """Step 8b's output: every tool registered, classified into lazy-load groups.

    `domain_groups` and `skill_group_names` are what `PromptStage` depends on --
    they do not exist until every `ToolSpec` has run and `classify()` /
    `reclassify_by_prefix()` have finished. This is the field that makes
    `build_prompt` genuinely downstream of `register_tools` (T-44 section 3.3).
    """

    tool_registry: ToolRegistry
    browser_controller: Any
    native_registration_count: int
    domain_groups: tuple[str, ...]
    skill_group_names: frozenset[str]
    has_live_mcp: bool


@dataclass(frozen=True, slots=True)
class PromptStage:
    """Step 5.5's output -- physically the 12th block in the file, not the 6th.
    See T-44 section 3.3: the number lied about when this runs."""

    system_prompt: str


@dataclass(frozen=True, slots=True)
class BrainLoopStage:
    """Step 9's output. `model_name` is echoed back (not just `loop`) because a
    successful routing resolution overwrites it with the routed default model --
    see the T-44 report's dependency-graph table for this edge, which the
    original code expressed only as a reassignment of a shared local."""

    loop: ExecutionLoop
    model_name: str
