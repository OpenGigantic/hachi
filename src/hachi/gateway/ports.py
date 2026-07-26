"""Domain ports the gateway control plane consumes (T-43 round 1).

``GatewayControlPlane.__init__`` (``control_plane.py``) originally took 74
individual handler callables, one per endpoint. This module groups them into
18 ``typing.Protocol`` classes by domain, mirroring the method groups the
in-process runtime already exposes (T-41's ``_InProcessGatewayRuntime``).
Adding an endpoint should mean adding one member to one port here plus one
implementation, not a fourth wiring site.

The **``Live*`` vs everything-else split encodes a real availability axis
(BB-6), not an arbitrary domain boundary** — it is the single piece of
knowledge the old flat 74-callable signature destroyed, so it is repeated
here in full:

* **``Live*Port`` protocols** — ``LiveSessionPort``, ``LiveObservabilityPort``,
  ``LiveSchedulingPort``, ``LiveSkillsPort``, ``LiveMemoryPort``,
  ``LiveSpendPort`` (28 handlers total) — group the endpoints that need a
  *live agent session*. The gateway builder
  (``hachi.cli.main._build_gateway_control_plane``) wires these from a
  ``runtime`` object that is ``None`` until a session actually boots, so
  every member of a ``Live*Port`` is unavailable pre-session:
  ``GatewayControlPlane`` either raises
  :class:`~hachi.gateway.control_plane.GatewayUnavailableError` (via its
  ``_require_handler`` guard) or returns a safe empty default, depending on
  the endpoint.
* **Every other port** — ``ApprovalsPort``, ``CapabilityPort``, ``ConfigPort``,
  ``AuthoringPort``, ``KennelPort``, ``MemoryReviewPort``, ``DailySpendPort``,
  ``AllowlistPort``, ``MCPAdminPort``, ``ProviderRoutingPort``,
  ``SecretsPort``, ``SearchPort`` (46 handlers total) — group endpoints that
  operate standalone (filesystem / settings.toml / secret-store reads and
  writes) and never need a booted agent. The builder wires these from
  ``support``, a second runtime instance it constructs unconditionally, so
  they are always available, even before any session exists.

Collapsing a ``Live*`` handler onto a support-style port (or the reverse)
would silently change *when* an endpoint is available — either exposing a
session-dependent endpoint before any session exists, or making a
standalone endpoint depend on a live session it never needed. Preserving
this split exactly is the primary boundary of this task; do not merge ports
across it, and do not merge the 18 ports down to a handful of larger ones —
their separateness (including the four single-member ports) is what encodes
the split.

All ports are **structural** (``typing.Protocol``, ``@runtime_checkable``),
not ABCs — ``_InProcessGatewayRuntime``
(``hachi.gateway.in_process_runtime``) satisfies every one of them already,
without declaring or inheriting from any class in this module. Protocol
member names match the *runtime's* method names exactly, which is not
always the constructor kwarg name it replaces on ``GatewayControlPlane``:
19 of the 74 differ (e.g. the ``on_connect`` kwarg wires
``register_connection``). Each renamed member is marked with a
``# ctor kwarg was '...'`` comment. Do not rename methods on the runtime to
match a port member — ports match the runtime, never the reverse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from hachi.gateway.control_plane import ConnectRequest, GatewayEvent


# ======================================================================
# Runtime-gated ports (nullable) -- the builder passes `runtime`, which is
# None until a session boots. 28 handlers across 6 ports.
# ======================================================================


@runtime_checkable
class LiveSessionPort(Protocol):
    """Session lifecycle & transport -- 11 members."""

    # ctor kwarg was 'on_connect'
    def register_connection(self, session_id: str, request: ConnectRequest) -> None: ...

    # ctor kwarg was 'on_disconnect'
    def close_connection(self, session_id: str) -> None: ...

    # ctor kwarg was 'on_chat'
    def chat(self, session_id: str, prompt: str) -> str: ...

    def read_events(self, session_id: str, after: int, limit: int) -> list[GatewayEvent]: ...

    def read_events_base(self, session_id: str) -> int: ...

    # ctor kwarg was 'get_session_state'
    def session_state(self, session_id: str) -> dict[str, Any]: ...

    # ctor kwarg was 'get_recent_sessions'
    def recent_sessions(self, limit: int) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'get_session_messages'
    def session_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'delete_session'
    def delete_session_record(self, session_id: str) -> bool: ...

    # ctor kwarg was 'get_runtime_events'
    def runtime_events(self, session_id: str | None, limit: int) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'on_interrupt'
    def interrupt_session(self, session_id: str) -> dict[str, Any]: ...


@runtime_checkable
class LiveObservabilityPort(Protocol):
    """Runtime observability read-models -- 5 members."""

    # ctor kwarg was 'get_mcp_inventory'
    def mcp_inventory(self) -> dict[str, Any]: ...

    # ctor kwarg was 'get_active_jobs'
    def active_jobs(self) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'get_failed_jobs'
    def failed_jobs(self, limit: int) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'get_permission_audit'
    def permission_audit(self, limit: int) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'get_subagent_reports'
    def subagent_reports(self, query: str, top_k: int) -> list[dict[str, Any]]: ...


@runtime_checkable
class LiveSchedulingPort(Protocol):
    """Scheduling -- 4 members."""

    def list_schedules(self) -> list[dict[str, Any]]: ...

    def cancel_schedule(self, schedule_id: str) -> dict[str, Any]: ...

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> dict[str, Any]: ...

    def run_schedule_now(self, schedule_id: str) -> dict[str, Any]: ...


@runtime_checkable
class LiveSkillsPort(Protocol):
    """Skills / ObjectGraph inspection -- 2 members."""

    def get_skills(self) -> list[dict[str, Any]]: ...

    def get_og_graph(self) -> dict[str, Any]: ...


@runtime_checkable
class LiveMemoryPort(Protocol):
    """Memory (live-session-backed) -- 5 members."""

    def get_memory(self) -> dict[str, Any]: ...

    def get_memory_recall(self, query: str, top_k: int) -> list[dict[str, Any]]: ...

    def add_memory_fact(self, group: str, text: str) -> dict[str, Any]: ...

    def delete_memory_fact(self, fact_id: str) -> dict[str, Any]: ...

    def get_memory_gardener_diffs(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class LiveSpendPort(Protocol):
    """Live per-session spend analytics -- 1 member."""

    def get_spend(self, session_id: str | None) -> dict[str, Any]: ...


# ======================================================================
# Support ports (always wired) -- the builder passes `support`, which is
# never None. 46 handlers across 12 ports.
# ======================================================================


@runtime_checkable
class ApprovalsPort(Protocol):
    """Approvals inbox -- 2 members."""

    # ctor kwarg was 'list_pending_approvals'
    def pending_approvals(self) -> list[dict[str, Any]]: ...

    def resolve_approval(self, request_id: str, action: str, rationale: str) -> bool: ...


@runtime_checkable
class CapabilityPort(Protocol):
    """Capability toggles -- 2 members."""

    # ctor kwarg was 'get_capabilities'
    def capabilities(self) -> list[dict[str, Any]]: ...

    def set_capability_enabled(self, cap_id: str, enabled: bool) -> dict[str, Any]: ...


@runtime_checkable
class ConfigPort(Protocol):
    """Config & setup -- 9 members."""

    def get_config(self) -> dict[str, Any]: ...

    def update_provider(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def validate_provider(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_permissions(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_memory(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def list_models(self) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'get_setup_state'
    def setup_state(self) -> dict[str, Any]: ...

    def apply_setup(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_limits(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class AuthoringPort(Protocol):
    """Authoring targets -- 6 members."""

    def get_authoring_targets(self) -> list[dict[str, Any]]: ...

    def get_authoring_target(self, name: str) -> dict[str, Any]: ...

    def authoring_generate(self, name: str, prompt: str) -> dict[str, Any]: ...

    def authoring_apply(self, name: str) -> dict[str, Any]: ...

    def authoring_revert(self, name: str) -> dict[str, Any]: ...

    def authoring_save_source(self, name: str, content: str) -> dict[str, Any]: ...


@runtime_checkable
class KennelPort(Protocol):
    """Skills kennel -- 2 members."""

    def get_kennel(self) -> list[dict[str, Any]]: ...

    # ctor kwarg was 'add_skill'
    def add_skill_from_kennel(self, kennel_id: str) -> dict[str, Any]: ...


@runtime_checkable
class MemoryReviewPort(Protocol):
    """MemoryGardener review decisions -- 1 member."""

    def decide_memory_gardener_diff(self, proposal_id: str, action: str) -> dict[str, Any]: ...


@runtime_checkable
class DailySpendPort(Protocol):
    """Durable daily spend ledger -- 1 member."""

    def get_spend_daily(self) -> dict[str, Any]: ...


@runtime_checkable
class AllowlistPort(Protocol):
    """Permission allowlists -- 5 members."""

    def add_allowed_folder(self, folder_path: str) -> dict[str, Any]: ...

    def add_allowed_command(self, command: str) -> dict[str, Any]: ...

    def remove_allowed_folder(self, folder_path: str) -> dict[str, Any]: ...

    def remove_allowed_command(self, command: str) -> dict[str, Any]: ...

    def add_always_allow(self, session_id: str, request: str, action: str) -> dict[str, Any]: ...


@runtime_checkable
class MCPAdminPort(Protocol):
    """MCP server admin -- 3 members."""

    def register_mcp_server(
        self,
        name: str,
        transport: str = "stdio",
        command: str = "",
        args: str = "",
        env_vars: dict[str, str] | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def set_mcp_enabled(self, name: str, enabled: bool) -> dict[str, Any]: ...

    def delete_mcp_server(self, name: str) -> dict[str, Any]: ...


@runtime_checkable
class ProviderRoutingPort(Protocol):
    """Multi-provider admin & capability routing -- 11 members."""

    def list_providers(self) -> dict[str, Any]: ...

    def add_or_update_provider(self, name: str, spec: dict[str, Any]) -> dict[str, Any]: ...

    def remove_provider(self, name: str) -> dict[str, Any]: ...

    def get_routing(self) -> dict[str, Any]: ...

    def set_routing(
        self, default_model: str, capabilities: dict[str, list[str]]
    ) -> dict[str, Any]: ...

    def validate_routing(
        self, default_model: str, capabilities: dict[str, list[str]]
    ) -> dict[str, Any]: ...

    def rollback_routing(self) -> dict[str, Any]: ...

    def get_routing_audit(self, limit: int) -> list[dict[str, Any]]: ...

    def test_provider_connection(self, name: str) -> dict[str, Any]: ...

    def list_all_models(self) -> dict[str, Any]: ...

    def probe_provider(self, spec: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class SecretsPort(Protocol):
    """Credential manager (``cred:`` namespace) -- 3 members."""

    def store_secret(self, name: str, value: str) -> dict[str, Any]: ...

    def list_secret_names(self) -> dict[str, Any]: ...

    def delete_secret(self, name: str) -> dict[str, Any]: ...


@runtime_checkable
class SearchPort(Protocol):
    """Search-tool live reload -- 1 member."""

    def reload_search_tools(self) -> dict[str, Any]: ...
