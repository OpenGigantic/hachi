"""In-process bridge from gateway API calls to the AgentSession runtime.

Extracted verbatim from ``hachi.cli.main`` (T-41). Behaviour is unchanged;
only import paths were rewritten.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict
from datetime import date as _date
from pathlib import Path

from hachi.core.approvals import get_approval_registry
from hachi.core.config_loader import (
    load_effective_config,
    read_global_config_file,
    write_global_config_dict,
)
from hachi.core.events import get_event_store
from hachi.core.onboarding import (
    OnboardingService,
    looks_like_env_var_name,
    persist_provider_api_key,
)
from hachi.core.paths import get_hachi_paths
from hachi.core.permissions import PermissionDecision
from hachi.core.secret_scanner import redact_secrets
from hachi.core.secret_store import create_default_secret_store
from hachi.core.session_store import SessionStore
from hachi.gateway.control_plane import ConnectRequest, GatewayEvent, GatewayProtocolError
from hachi.gateway.orchestrator import AgentSession, boot_agent
from hachi.gateway.workspace import get_permissions_file
from hachi.jobs import ResidentWorkerThread, ScheduleStore

_logger = logging.getLogger(__name__)

#: Module-level slot for the ResidentWorkerThread started by the in-process
#: gateway runtime.  Set by :class:`_InProcessGatewayRuntime` once an agent
#: session is booted; inspected by :func:`_gateway_readiness_probe`.
_GATEWAY_RESIDENT_WORKER: ResidentWorkerThread | None = None


class _InProcessGatewayRuntime:
    """Bridge gateway API calls to the in-process AgentSession runtime."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentSession] = {}
        self._agents_lock = threading.Lock()
        # G3-T3 / REL-01 / HIGH-003: per-session chat serialization.
        # Concurrent chat() calls for the SAME session race on shared agent state
        # and ChromaDB writes.  A per-session lock serializes them while leaving
        # DIFFERENT sessions fully concurrent.
        # _chat_locks is guarded by _agents_lock for creation/deletion.
        self._chat_locks: dict[str, threading.Lock] = {}
        # G1-T7: set by _start_gateway_server so the first session boot resumes it.
        self._pending_resume_session_id: str | None = None
        # G1-T2 / CRIT-002: authoring pending-patch state (per-instance).
        self._pending_proposals: dict[str, str] = {}
        self._revert_snapshots: dict[str, str] = {}
        self._authoring_lock = threading.Lock()

    def _boot_session_agent(
        self, session_id: str, resume_session_id: str | None = None
    ) -> AgentSession:
        """Boot a fresh agent, optionally resuming a prior session's message history.

        Args:
            session_id: The new gateway session being created.
            resume_session_id: If set, the prior SessionStore session whose transcript
                to replay into the new agent's memory before the first turn.

        Returns:
            A booted :class:`AgentSession` ready to receive user input.
        """
        agent = boot_agent(resume_session_id=resume_session_id, approval_surface="gateway")
        # Expose the resident worker to the module-level readiness probe so it
        # can check worker liveness without coupling probe ↔ runtime instance.
        global _GATEWAY_RESIDENT_WORKER  # noqa: PLW0603
        worker = getattr(agent, "_resident_worker", None)
        if worker is not None:
            _GATEWAY_RESIDENT_WORKER = worker
        return agent

    def _get_agent(self, session_id: str) -> AgentSession:
        with self._agents_lock:
            agent = self._agents.get(session_id)
        if agent is None:
            raise GatewayProtocolError("Unknown session_id")
        return agent

    def _register_agent(self, session_id: str, agent: AgentSession) -> None:
        """Insert *agent* into the session map under *session_id*.

        Shared bookkeeping for :meth:`register_connection` and
        :meth:`boot_registered_session` (T-20) so the two registration paths
        cannot diverge: every entry point that boots an agent must land it in
        the ONE map every read model (``recent_sessions``,
        ``permission_audit``, ``subagent_reports``, ...) queries, or the
        session is invisible rather than merely unmanaged (BB-119).
        """
        with self._agents_lock:
            replaced_agent = self._agents.get(session_id)
            self._agents[session_id] = agent
            # Ensure a per-session chat lock exists (idempotent for re-registrations).
            if session_id not in self._chat_locks:
                self._chat_locks[session_id] = threading.Lock()

        if replaced_agent is not None:
            try:
                replaced_agent.shutdown()
            except Exception:
                pass

    def register_connection(
        self,
        session_id: str,
        _req: ConnectRequest,
        resume_session_id: str | None = None,
    ) -> None:
        """Boot an agent for the new gateway session.

        Args:
            session_id: The new gateway session id.
            _req: The connect request envelope (unused beyond compat).
            resume_session_id: When set, replay this prior session's transcript into
                the new agent's memory so context carries over (G1-T7).
        """
        # G1-T7: consume the pending resume id on the first connection (one-shot).
        if resume_session_id is None:
            with self._agents_lock:
                pending = self._pending_resume_session_id
                if pending and not self._agents:
                    self._pending_resume_session_id = None
                    resume_session_id = pending
        agent = self._boot_session_agent(session_id, resume_session_id=resume_session_id)
        self._register_agent(session_id, agent)

    def boot_registered_session(self, *, client_id: str, surface: str) -> AgentSession:
        """Boot an agent registered in the gateway's session map (channels, schedules).

        T-20 interim path (documented, pending the CF-2 runtime extraction —
        see T-41): channel and schedule entry points call this instead of
        ``boot_agent()`` directly, so their sessions are registered the same
        way a web session is — reachable by every read model that iterates
        ``_agents`` (``recent_sessions``, ``permission_audit``,
        ``subagent_reports``, ``mcp_inventory``, ...) — and, because this
        reuses :meth:`_boot_session_agent` (which already passes
        ``approval_surface="gateway"``), gated with the same
        ``RegistryApprover`` as any other governed session (BB-119; see
        BB-90 / T-01 for the approver-selection fix this builds on).

        Args:
            client_id: Identifies the calling client (e.g. ``"telegram"``,
                ``"whatsapp"``, ``"scheduler"``). Folded into the generated
                session id for traceability; no ``ConnectRequest`` /
                control-plane session record is created — see
                ``docs/audit/2026-07-19/tasks/T-20-govern-channel-schedule-sessions.md``
                §5 for the scoping decision.
            surface: The non-web entry surface booting this session (e.g.
                ``"channel"``, ``"schedule"``). Folded into the generated
                session id.

        Returns:
            A booted, registered :class:`AgentSession`.
        """
        session_id = f"{surface}:{client_id}:{uuid.uuid4().hex[:12]}"
        agent = self._boot_session_agent(session_id)
        self._register_agent(session_id, agent)
        return agent

    def close_connection(self, session_id: str) -> None:
        with self._agents_lock:
            agent = self._agents.pop(session_id, None)
            # Drop the per-session chat lock to avoid an unbounded leak.
            self._chat_locks.pop(session_id, None)
        if agent is not None:
            try:
                agent.shutdown()
            except Exception:
                pass

    def rehydrate_session(self, session_id: str) -> AgentSession | None:
        """Boot a fresh session that replays *session_id*'s transcript (cold-start resume).

        Called by :func:`~hachi.core.runtime.resume._do_resume` when the live-session
        map has no entry for *session_id* (gateway restarted).  Boots a fresh
        :class:`AgentSession` with ``resume_session_id=session_id`` so the prior
        SQLite transcript is replayed into the new agent's memory before the first
        turn — exactly the same path taken by a normal session-resume at connect time.

        The new session is registered under the SAME *session_id* so that subsequent
        resume_run calls (and any concurrent gateway requests) find it in the map.

        Returns:
            The freshly booted and registered :class:`AgentSession`, or ``None``
            when boot fails (error is logged; caller should fallback to HonestResumeError).
        """
        try:
            agent = self._boot_session_agent(session_id, resume_session_id=session_id)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "rehydrate_session: failed to boot agent for session %r",
                session_id,
                exc_info=True,
            )
            return None
        with self._agents_lock:
            self._agents[session_id] = agent
            if session_id not in self._chat_locks:
                self._chat_locks[session_id] = threading.Lock()
        return agent

    def get_live_session(self, session_id: str) -> AgentSession | None:
        """Return the live in-memory session for *session_id*, or ``None``.

        T-42: the adapter method for
        :class:`~hachi.core.runtime.protocols.SessionRuntime` — the caller
        (cold-start resume in ``core``) must never see ``_agents_lock``
        directly; this method owns the locking itself.
        """
        with self._agents_lock:
            return self._agents.get(session_id)

    def rehydrate(self, session_id: str) -> AgentSession | None:
        """Alias for :meth:`rehydrate_session` for the ``SessionRuntime`` protocol.

        T-42: ``rehydrate_session`` is kept as-is (unaliased) because it has
        other callers; this is purely the narrower name the protocol declares.
        """
        return self.rehydrate_session(session_id)

    def _reload_agents(self) -> None:
        """Reboot cached session agents so provider/credential changes apply live.

        Provider config (model, base URL) and the resolved API key are baked into
        the LLM client at agent boot, so a running session keeps using whatever it
        started with. After a config mutation we rebuild each cached agent in
        place — re-reading the freshly written config + process env — and retire
        the old one. No-op when no sessions are connected (e.g. the standalone
        gateway started without ``--runtime``).
        """
        with self._agents_lock:
            session_ids = list(self._agents.keys())
        for session_id in session_ids:
            try:
                fresh = self._boot_session_agent(session_id)
            except Exception:
                continue  # keep the existing agent if a reboot fails
            with self._agents_lock:
                old = self._agents.get(session_id)
                self._agents[session_id] = fresh
            if old is not None and old is not fresh:
                try:
                    old.shutdown()
                except Exception:
                    pass

    def chat(self, session_id: str, prompt: str) -> str:
        """Send *prompt* to the session's agent and return the reply.

        Concurrent calls for the **same** session are serialized via a per-session
        lock so agent state and ChromaDB writes are never interleaved.  Calls for
        **different** sessions proceed concurrently (each session has its own lock).
        """
        agent = self._get_agent(session_id)
        with self._agents_lock:
            session_lock = self._chat_locks.get(session_id)
        if session_lock is None:
            # Defensive: lock was never created (e.g. agent registered via a code
            # path that predates this change).  Fall through without serialization
            # rather than raising — behaviour is unchanged from before this fix.
            return agent.chat(prompt)
        with session_lock:
            return agent.chat(prompt)

    def interrupt_session(self, session_id: str) -> dict:
        """Signal the live session's execution loop to stop at its next iteration.

        P1-4: sets the ``threading.Event`` inside ``ExecutionLoop`` so the loop exits
        cleanly at the top of the next iteration (after the current LLM chunk / tool call
        finishes).  Thread-safe; safe to call even when no turn is in progress.

        Args:
            session_id: The gateway session to interrupt.

        Returns:
            ``{"interrupted": True}`` when the signal was delivered;
            ``{"interrupted": False}`` when the session is not found.
        """
        with self._agents_lock:
            agent = self._agents.get(session_id)
        if agent is None:
            return {"interrupted": False}
        try:
            agent.loop.cancel()
            return {"interrupted": True}
        except Exception:
            return {"interrupted": False}

    def read_events(self, session_id: str, after: int, limit: int) -> list[GatewayEvent]:
        snapshots = self._get_agent(session_id).snapshot_events_since(after, limit)
        return [
            GatewayEvent(
                sequence=snap.sequence,
                event_type=snap.name,
                payload=snap.payload,
                time=snap.timestamp,
            )
            for snap in snapshots
        ]

    def read_events_base(self, session_id: str) -> int:
        """Oldest sequence still retained for ``session_id`` (T-30 gap signal)."""
        return self._get_agent(session_id).snapshot_events_base()

    def read_events_durable(self, session_id: str, after: int, limit: int) -> list[GatewayEvent]:
        """Durable replay for a session with no live agent (T-75 Stage B).

        Reads the process-wide :class:`~hachi.core.events.EventStore` directly
        -- deliberately **without** going through :meth:`_get_agent`, since a
        not-live session (the only caller of this method,
        ``GatewayControlPlane``'s history branch) has none. ``session_id`` here
        is the SessionStore-persisted id (the same one ``session_messages`` /
        ``_synthesize_history_events`` already require -- both filter
        ``SessionStore`` rows by it), **not** the ephemeral gateway connection
        id ``register_connection``/``close_connection`` key by (that one is
        minted per-connect in ``control_plane.py``'s ``create_session`` and
        never reaches ``SessionStore``). The two id spaces are independent by
        construction; this method's caller only ever holds the former once a
        session is no longer live.
        """
        records = get_event_store().read_since(session_id, after, limit)
        return [
            GatewayEvent(
                sequence=record.sequence,
                event_type=record.name,
                payload=record.payload,
                time=record.timestamp,
            )
            for record in records
        ]

    def session_state(self, session_id: str) -> dict:
        return asdict(self._get_agent(session_id).snapshot_session_state())

    def recent_sessions(self, limit: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is not None:
            return [dict(item) for item in agent.query_recent_sessions(limit=limit)]
        # BB-13: no agent has booted yet (e.g. a fresh server start, before the
        # first message). Read persisted sessions straight from the SessionStore
        # so the conversation dropdown isn't empty on boot — the agent path and
        # this path both delegate to SessionStore.get_recent_sessions, so the
        # returned shape is identical. (Previously this returned [] when
        # _agents was empty, hiding every prior conversation until a turn ran.)
        store = SessionStore(db_path=str(get_hachi_paths().sessions_db))
        try:
            return [dict(item) for item in store.get_recent_sessions(limit=limit)]
        finally:
            store.shutdown()

    def session_messages(self, session_id: str, limit: int = 500) -> list[dict]:
        """Stored transcript for a (possibly historical) session — powers resume."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is not None:
            return [dict(item) for item in agent.query_session_messages(session_id, limit=limit)]
        # BB-34: no agent booted yet (cold gateway start before the first turn).
        # Mirror the BB-13 fallback used by recent_sessions(): read directly from
        # the durable SessionStore so past-session histories load on the picker.
        store = SessionStore(db_path=str(get_hachi_paths().sessions_db))
        try:
            return [dict(item) for item in store.get_session_messages(session_id, limit=limit)]
        finally:
            store.shutdown()

    def delete_session_record(self, session_id: str) -> bool:
        """Permanently delete a persisted session from the store. True if it existed."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return False
        return bool(agent.delete_session(session_id))

    def runtime_events(self, session_id: str | None, limit: int) -> list[dict]:
        if session_id:
            agent = self._get_agent(session_id)
            events = agent.snapshot_runtime_events(limit=limit)
            return [asdict(event) for event in events]

        all_events: list[dict] = []
        with self._agents_lock:
            agents = list(self._agents.values())
        for agent in agents:
            all_events.extend(asdict(event) for event in agent.snapshot_runtime_events(limit=limit))
        all_events.sort(key=lambda item: str(item.get("timestamp", "")))
        return all_events[-limit:]

    def mcp_inventory(self) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"server_count": 0, "total_tool_count": 0, "servers": []}
        return asdict(agent.snapshot_mcp_inventory())

    def active_jobs(self) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(job) for job in agent.snapshot_active_jobs()]

    def failed_jobs(self, limit: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(job) for job in agent.snapshot_failed_jobs(limit=limit)]

    def permission_audit(self, limit: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(item) for item in agent.snapshot_permission_audit(limit=limit)]

    def pending_approvals(self) -> list[dict]:
        """T-15: the only place get_approval_registry() is touched from the gateway
        edge (alongside :meth:`resolve_approval`). Runtime-independent — the
        registry is a process singleton, not tied to any booted AgentSession —
        so this is wired unconditionally in ``_build_gateway_control_plane``.
        """
        return get_approval_registry().list_pending()

    def resolve_approval(self, request_id: str, action: str, rationale: str = "") -> bool:
        """T-15: resolve a pending approval via the process-wide registry."""
        return get_approval_registry().resolve(
            request_id, PermissionDecision(action=action, rationale=rationale)
        )

    def subagent_reports(self, query: str, top_k: int) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        reports = agent.query_subagent_reports(query=query, top_k=top_k)
        return [asdict(item) for item in reports]

    def list_schedules(self) -> list[dict]:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is not None:
            return agent.snapshot_schedules()
        # No active session — read from disk so GET /schedules works before
        # the user opens the chat (or after a gateway restart with no live session).
        return [s.to_dict() for s in ScheduleStore(get_hachi_paths().schedules_file).load()]

    def cancel_schedule(self, schedule_id: str) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"ok": False, "detail": "no active session"}
        ok = agent.cancel_schedule(schedule_id)
        return {"ok": ok, "schedule_id": schedule_id}

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"ok": False, "detail": "no active session"}
        ok = agent.toggle_schedule_enabled(schedule_id, enabled=enabled)
        return {"ok": ok, "schedule_id": schedule_id, "enabled": enabled}

    def run_schedule_now(self, schedule_id: str) -> dict:
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"ok": False, "detail": "no active session"}
        ok = agent.run_schedule_now(schedule_id)
        return {"ok": ok, "schedule_id": schedule_id}

    def capabilities(self) -> list[dict]:
        config = load_effective_config()
        # Pull OAuth status for each capability from the connector registry.
        # Import is deferred to avoid circular imports at module load time.
        try:
            from hachi.integrations.oauth_flow import connector_status as _connector_status
        except Exception:
            _connector_status = None  # noqa: F841

        items: list[dict] = []
        for capability_id, state in config.capabilities.items():
            entry: dict = {
                "id": capability_id,
                "kind": "native_integration",
                "enabled": bool(state.enabled),
                "configured": bool(state.configured),
                "secret_ref": state.secret_ref,
                # OAuth status fields — present when the connector is in the registry
                "auth_type": "none",
                "connected": False,
                "status": "disconnected",
            }
            if _connector_status is not None:
                try:
                    oauth = _connector_status(capability_id)
                    entry["auth_type"] = oauth["auth_type"]
                    entry["connected"] = oauth["connected"]
                    # Override "configured" with the registry's view (env var present)
                    # only when the connector is OAuth2; otherwise keep the config value.
                    if oauth["auth_type"] == "oauth2":
                        entry["configured"] = oauth["configured"]
                    entry["status"] = oauth["status"]
                except Exception:
                    pass
            items.append(entry)
        return items

    def get_config(self) -> dict:
        # BB-57: GET /config must never echo secret-shaped fields — mirror the
        # masking GET /config/providers already applies via provider_admin._mask_provider
        # (drops api_key_ref/api_key_env/base_url, masks header values) instead of
        # dumping the raw ResolvedConfig, which leaked env-var names, base URLs, and
        # raw header values (e.g. Authorization bearer tokens) verbatim.
        from hachi.gateway.provider_admin import _mask_provider

        config = load_effective_config()
        raw = config.model_dump(mode="python", exclude_none=True)

        raw["provider"] = _mask_provider("__primary__", config.provider)
        raw["providers"] = {
            name: _mask_provider(name, ps) for name, ps in (config.providers or {}).items()
        }

        capabilities_block = raw.get("capabilities")
        if isinstance(capabilities_block, dict):
            for entry in capabilities_block.values():
                if isinstance(entry, dict) and entry.get("secret_ref"):
                    entry["secret_ref"] = "***"

        # Ensure GET /config always includes a ``limits`` block with the frontend-facing
        # keys ``per_task_cap_usd`` / ``daily_cap_usd`` so caps saved via PUT /config/limits
        # round-trip on reload.
        # Internally: max_turn_cost_usd = per-task cap, max_daily_cost_usd = daily cap.
        limits_block = raw.setdefault("limits", {})
        limits_block["per_task_cap_usd"] = getattr(config.limits, "max_turn_cost_usd", None)
        limits_block["daily_cap_usd"] = getattr(config.limits, "max_daily_cost_usd", None)
        return raw

    def update_provider(self, payload: dict) -> dict:
        provider = payload.get("provider", payload)
        if not isinstance(provider, dict):
            raise GatewayProtocolError("provider payload must be an object")

        # BB-124: read the FILE layer only — load_effective_config() merges in
        # env/CLI/defaults, and persisting THAT view would bake a transient
        # override (e.g. HACHI_PERMISSION_TIER) permanently into settings.toml.
        doc = read_global_config_file()
        provider_doc: dict = dict(doc.get("provider") or {})
        current_provider_kind = str(provider_doc.get("kind") or "anthropic").strip()
        current_provider_model = str(provider_doc.get("model") or "claude-haiku-4-5").strip()
        kind = provider.get("kind")
        model = provider.get("model")
        base_url = provider.get("base_url")
        api_key = provider.get("api_key")
        api_key_ref = provider.get("api_key_ref")
        api_key_env = provider.get("api_key_env")

        if kind is not None:
            from typing import get_args

            from hachi.core.settings import ProviderKind

            kind_text = str(kind)
            _valid_kinds = set(get_args(ProviderKind))
            if kind_text not in _valid_kinds:
                raise GatewayProtocolError(
                    f"provider.kind must be one of: {', '.join(sorted(_valid_kinds))}"
                )
            provider_doc["kind"] = kind_text

        if model is not None:
            model_text = str(model).strip()
            if not model_text:
                raise GatewayProtocolError("provider.model cannot be empty")
            from hachi.core.onboarding import resolve_provider_model

            provider_doc["model"] = resolve_provider_model(
                str(provider_doc.get("kind") or "anthropic"),
                model_text,
                previous_kind=current_provider_kind,
                previous_model=current_provider_model,
            )
        elif (
            kind is not None
            and str(provider_doc.get("kind") or "").strip() != current_provider_kind
        ):
            from hachi.core.onboarding import resolve_provider_model

            provider_doc["model"] = resolve_provider_model(
                str(provider_doc.get("kind") or "anthropic"),
                None,
                previous_kind=current_provider_kind,
                previous_model=current_provider_model,
            )

        if base_url is not None:
            provider_doc["base_url"] = str(base_url).strip() or None

        # API key handling — persist raw secrets to the secret store + process
        # env (never the config file); record only the env-var NAME. A raw key may
        # arrive via `api_key` (preferred) or, for backward compatibility, as a
        # non-env-name `api_key_ref`.
        raw_api_key = api_key
        if not raw_api_key and api_key_ref and not looks_like_env_var_name(str(api_key_ref)):
            raw_api_key = api_key_ref
            api_key_ref = None

        if api_key_env is not None:
            provider_doc["api_key_env"] = str(api_key_env).strip() or None

        if raw_api_key:
            env_name = persist_provider_api_key(
                str(provider_doc.get("kind") or "anthropic"),
                str(raw_api_key),
                api_key_env=provider_doc.get("api_key_env"),
                secret_store=create_default_secret_store(),
            )
            if env_name:
                provider_doc["api_key_ref"] = env_name
                provider_doc["api_key_env"] = env_name
        elif api_key_ref is not None:
            provider_doc["api_key_ref"] = str(api_key_ref).strip() or None

        doc["provider"] = provider_doc
        write_global_config_dict(doc)
        # Provider/credential changes are baked into the LLM client at boot, so
        # reboot any live session to apply them without a manual restart.
        self._reload_agents()
        # The response echoes "the effective config after this change" — same
        # API contract as before this fix. Building it from `doc` (validated,
        # never re-persisted) rather than a merged env-aware read keeps this
        # purely a display concern; only the FILE write is file-layer-only.
        from hachi.core.settings import ResolvedConfig

        return ResolvedConfig.model_validate(doc).model_dump(mode="python", exclude_none=True)

    def validate_provider(self, payload: dict) -> dict:
        provider = payload.get("provider", payload)
        if not isinstance(provider, dict):
            raise GatewayProtocolError("provider payload must be an object")

        kind = str(provider.get("kind") or "").strip()
        model = str(provider.get("model") or "").strip()
        api_key_ref = str(provider.get("api_key_ref") or "").strip()
        api_key_env = str(provider.get("api_key_env") or "").strip()
        errors: list[str] = []
        warnings: list[str] = []

        from typing import get_args

        from hachi.core.settings import ProviderKind

        _valid_kinds = set(get_args(ProviderKind))
        if kind not in _valid_kinds:
            errors.append(f"provider.kind must be one of: {', '.join(sorted(_valid_kinds))}")
        if not model:
            errors.append("provider.model is required")

        if kind in {"anthropic", "openai-compatible"} and not (api_key_ref or api_key_env):
            warnings.append("api_key_ref or api_key_env should be set for remote providers")

        if kind == "ollama" and not str(provider.get("base_url") or "").strip():
            warnings.append("base_url is recommended for ollama providers")

        return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}

    def update_permissions(self, payload: dict) -> dict:
        permissions = payload.get("permissions", payload)
        if not isinstance(permissions, dict):
            raise GatewayProtocolError("permissions payload must be an object")

        tier = str(permissions.get("tier") or "").strip()
        if tier and tier not in {"sandbox", "guided", "unrestricted"}:
            raise GatewayProtocolError("permissions.tier must be sandbox, guided, or unrestricted")

        ask_before = permissions.get("ask_before")
        if ask_before is not None and not isinstance(ask_before, dict):
            raise GatewayProtocolError("permissions.ask_before must be an object")

        # BB-124: read the FILE layer only — see update_provider() above.
        doc = read_global_config_file()
        permissions_doc: dict = dict(doc.get("permissions") or {})
        if tier:
            permissions_doc["tier"] = tier
        if isinstance(ask_before, dict):
            # Only the recognised "Ask before…" rule keys may be set; each value
            # must be a boolean. This persists the Settings panel toggles so they
            # survive a reload (the rule labels live in the frontend). Validate
            # against the SCHEMA's own field names — never a merged instance.
            from hachi.core.settings import AskBeforeSettings

            valid_keys = set(AskBeforeSettings.model_fields.keys())
            ask_before_doc: dict = dict(permissions_doc.get("ask_before") or {})
            for key, value in ask_before.items():
                if key not in valid_keys:
                    raise GatewayProtocolError(
                        f"permissions.ask_before.{key} is not a recognised rule"
                    )
                if not isinstance(value, bool):
                    raise GatewayProtocolError(f"permissions.ask_before.{key} must be a boolean")
                ask_before_doc[key] = value
            permissions_doc["ask_before"] = ask_before_doc

        doc["permissions"] = permissions_doc
        write_global_config_dict(doc)

        # Bug 7: write_global_config_dict persists the tier to settings.toml, but
        # PermissionGate reads device-permissions.og (not settings.toml).  A tier
        # change must therefore regenerate the .og so that (a) fresh boots pick it up
        # and (b) boot_agent's reconcile step can detect the drift.
        if tier:
            _perm_file = get_permissions_file()
            try:
                from hachi.core.permissions import PermissionGate, generate_permissions_og

                # Preserve any custom allowed_paths the user added manually.
                _custom_paths: list[str] | None = None
                if _perm_file.exists():
                    try:
                        _gate = PermissionGate(_perm_file)
                        if not _gate._fs.allow_all and _gate._fs.allowed_paths:
                            _custom_paths = [str(p) for p in _gate._fs.allowed_paths]
                    except Exception:
                        pass
                _og_content = generate_permissions_og(tier, _custom_paths)
                _tmp = _perm_file.with_suffix(".og.tmp")
                _tmp.write_text(_og_content, encoding="utf-8")
                import os as _os

                _os.replace(_tmp, _perm_file)
            except Exception:
                _logger.warning(
                    "update_permissions: failed to regenerate device-permissions.og", exc_info=True
                )

        from hachi.core.settings import ResolvedConfig

        return ResolvedConfig.model_validate(doc).model_dump(mode="python", exclude_none=True)

    def update_memory(self, payload: dict) -> dict:
        memory = payload.get("memory", payload)
        if not isinstance(memory, dict):
            raise GatewayProtocolError("memory payload must be an object")

        # BB-124: read the FILE layer only — see update_provider() above.
        doc = read_global_config_file()
        memory_doc: dict = dict(doc.get("memory") or {})
        if "enable_session_history" in memory:
            value = memory["enable_session_history"]
            if not isinstance(value, bool):
                raise GatewayProtocolError("memory.enable_session_history must be a boolean")
            memory_doc["enable_session_history"] = value
        if "enable_semantic_memory" in memory:
            value = memory["enable_semantic_memory"]
            if not isinstance(value, bool):
                raise GatewayProtocolError("memory.enable_semantic_memory must be a boolean")
            memory_doc["enable_semantic_memory"] = value

        doc["memory"] = memory_doc
        write_global_config_dict(doc)

        from hachi.core.settings import ResolvedConfig

        return ResolvedConfig.model_validate(doc).model_dump(mode="python", exclude_none=True)

    def list_models(self) -> list[dict]:
        """Return provider-specific model lists using live discovery when possible.

        Attempts live discovery via the same path as the ``hachi models`` CLI command.
        Falls back to a current static list when the provider API is unreachable or
        no API key is configured.  The configured default model is always present.

        HIGH-008: static fallback list is kept current (includes claude-opus-4-8,
        claude-sonnet-4-6, claude-haiku-4-5).
        """
        # Current static fallback lists per provider kind.
        _STATIC_FALLBACK: dict[str, list[str]] = {
            "anthropic": [
                "claude-haiku-4-5",
                "claude-sonnet-4-6",
                "claude-opus-4-8",
            ],
            "openai-compatible": ["gpt-4.1-mini", "gpt-4.1"],
            "ollama": ["llama3.1:8b", "qwen2.5-coder:7b"],
        }
        config = load_effective_config()
        kind = config.provider.kind
        current = config.provider.model
        allowed = list(getattr(config.provider, "allowed_models", []) or [])

        # Attempt live discovery — same path as the ``hachi models`` CLI command.
        discovered: list[str] = []
        try:
            from hachi.core.llm import create_llm_client, resolve_available_models

            provider_config = {
                "kind": kind,
                "model": current,
                "api_key_ref": getattr(config.provider, "api_key_ref", None),
                "api_key_env": getattr(config.provider, "api_key_env", None),
                "base_url": getattr(config.provider, "base_url", None),
            }
            client = create_llm_client(provider_config, secret_store=create_default_secret_store())
            try:
                discovered = client.list_models()
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        except Exception:
            # Offline, missing API key, or import error — fall through to static list.
            pass

        if discovered:
            from hachi.core.llm import resolve_available_models

            model_ids = resolve_available_models(discovered, allowed, current)
        else:
            # Static fallback: use allowlist if configured, else provider defaults.
            fallback = list(allowed) if allowed else _STATIC_FALLBACK.get(kind, [current])
            model_ids = list(fallback)
            if current and current not in model_ids:
                model_ids.insert(0, current)

        return [
            {
                "id": model,
                "label": model,
                "provider": kind,
                "recommended": model == current,
            }
            for model in model_ids
        ]

    def setup_state(self) -> dict:
        service = OnboardingService()
        state = service.resume_state()
        return {
            "state": state.model_dump(mode="python", exclude_none=True),
            "needs_setup": service.needs_setup(),
            "environment": service.detect_environment(),
        }

    def apply_setup(self, payload: dict) -> dict:
        plan_payload = payload.get("plan", payload)
        if not isinstance(plan_payload, dict):
            raise GatewayProtocolError("setup payload must be an object")

        service = OnboardingService()
        try:
            plan = service.apply_setup(plan_payload)
        except ValueError as exc:
            raise GatewayProtocolError(str(exc)) from exc
        # Apply provider/credential changes from the wizard to any live session.
        self._reload_agents()
        return {
            "ok": True,
            "plan": plan.model_dump(mode="python", exclude_none=True),
            "state": service.resume_state().model_dump(mode="python", exclude_none=True),
        }

    def shutdown(self) -> None:
        with self._agents_lock:
            agents = list(self._agents.values())
            self._agents.clear()
        for agent in agents:
            try:
                agent.shutdown()
            except Exception:
                pass

    # ==========================================
    # Web endpoint handlers — added for web UI (#1–#6)
    # ==========================================

    def get_skills(self) -> list[dict]:
        """Return the .og skill inventory (route: GET /skills)."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return [asdict(s) for s in agent.snapshot_skills()]

    def get_memory(self) -> dict:
        """Return grouped memory facts for the Memory screen (route: GET /memory)."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {"groups": [], "blocked": [], "proposed": None}
        return agent.snapshot_memory_facts()

    def get_memory_recall(self, query: str, top_k: int = 8) -> list[dict]:
        """Return semantic recall hits (route: GET /memory/recall).

        Calls vault.semantic_search directly to preserve the similarity score,
        which query_memory_recall (on AgentSession) drops when building its snapshot.
        """
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None or agent.vault is None:
            return []
        try:
            rows = agent.vault.semantic_search(query, top_k=top_k)
        except Exception:
            return []
        results: list[dict] = []
        for row in rows:
            fact = row.get("raw_content") or row.get("content", "")
            # G4-T2 (SEC-03): redact secrets at the API boundary before returning to the
            # client.  _redact_results() inside semantic_search() already applies the
            # vault-local patterns; redact_secrets() adds the full secret_scanner pattern
            # set (GitHub, Stripe, npm, JWT, …) as a defence-in-depth layer.
            fact, _ = redact_secrets(str(fact))
            src = row.get("category", "")
            # The row uses key "score" (from RRF merge) or "distance" (from Chroma).
            # Tolerate absence of both keys.
            raw_score = row.get("score") or row.get("distance")
            score = str(round(float(raw_score), 2)) if raw_score is not None else ""
            results.append({"fact": fact, "score": score, "src": src})
        return results

    def get_memory_gardener_diffs(self) -> list[dict]:
        """Return pending MemoryGardener proposals (route: GET /memory/diffs — T-08 A2).

        Makes AgentSession.snapshot_gardener_diffs() reachable. Mirrors get_memory()/
        get_skills(): returns an empty list before any session has booted rather than
        erroring, since this route backs a passive UI list.
        """
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        return agent.snapshot_gardener_diffs()

    def decide_memory_gardener_diff(self, proposal_id: str, action: str) -> dict:
        """Approve or reject a pending gardener proposal (route: POST /memory/diffs/{id}).

        T-08 A3: the write-back path that did not exist before this task. Operates
        directly on pending_diffs.json under get_hachi_paths().memory_dir —
        independent of any live agent session, since MemoryGardener.run() (A1)
        always writes there regardless of which (if any) session is currently
        booted, so a decision made while no chat session is open still lands.

        Args:
            proposal_id: A GardeningProposal.id from a prior GET /memory/diffs response.
            action:      "approve" (apply via an append-only ::changelog note and
                         record under auto_applied) or "reject" (drop without
                         touching any .og file).

        Returns:
            ``{"ok": True, "id": proposal_id, "action": action, "proposal": {...}}``.

        Raises:
            fastapi.HTTPException: 404 when proposal_id is not a currently pending
            proposal (already decided, or superseded by a newer gardener run).
        """
        from dataclasses import asdict

        from fastapi import HTTPException as _HTTPException

        from hachi.core.memory.gardener import MemoryGardener

        gardener = MemoryGardener(get_hachi_paths().memory_dir)
        try:
            decided = gardener.decide(proposal_id, approve=(action == "approve"))
        except KeyError as exc:
            raise _HTTPException(
                status_code=404, detail=f"Unknown gardener proposal id {proposal_id!r}"
            ) from exc
        return {"ok": True, "id": proposal_id, "action": action, "proposal": asdict(decided)}

    def get_og_graph(self) -> dict:
        """Return the ObjectGraph node graph (route: GET /og/graph).

        When an active agent session exists, its skill engine (which includes
        workspace skills) is used.  Before any session is started, falls back to
        the process-shared skill engine populated by
        :class:`~hachi.gateway.shared_services.ProcessSharedServices`, which
        loads the bundled default skills at gateway boot time.  This ensures the
        OG Explorer shows data even before the first conversation is opened.
        """
        from hachi.gateway.orchestrator import og_graph_from_engine

        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is not None:
            return agent.snapshot_og_graph()

        # No active session yet. Prefer the process-shared engine if a prior
        # session already initialised it; otherwise build a standalone engine
        # from the bundled default skills so the OG Explorer is populated before
        # the first conversation is opened (the shared services are created
        # lazily on first session boot, not at gateway startup).
        from hachi.gateway.shared_services import ProcessSharedServices

        shared = ProcessSharedServices.instance()
        if shared is not None and shared.skill_engine is not None:
            graph = og_graph_from_engine(shared.skill_engine)
            if graph.get("count"):
                return graph

        engine = self._standalone_og_engine()
        if engine is not None:
            return og_graph_from_engine(engine)
        return {"nodes": [], "count": 0}

    def _standalone_og_engine(self) -> object | None:
        """Build (once, cached) a SkillEngine from the bundled default skills.

        The process-shared services are initialised lazily on first session
        boot, so before any conversation exists ``ProcessSharedServices.instance``
        is ``None``. This standalone engine lets the OG Explorer show the skill
        graph immediately after gateway startup.
        """
        cached = getattr(self, "_og_engine_cache", None)
        if cached is not None:
            return cached
        try:
            from hachi.core.paths import get_hachi_paths
            from hachi.og_engine.engine import SkillEngine

            engine = SkillEngine(agent_role="orchestrator")
            default_skills_dir = Path(__file__).parent.parent / "default_skills"
            if default_skills_dir.exists():
                engine.load_directory(default_skills_dir)
            try:
                engine.load_directory(get_hachi_paths().skills_dir)
            except OSError:
                logging.getLogger(__name__).debug(
                    "standalone OG engine: workspace load skipped", exc_info=True
                )
            self._og_engine_cache = engine
            return engine
        except Exception:
            logging.getLogger(__name__).warning("standalone OG engine build failed", exc_info=True)
            return None

    def get_authoring_targets(self) -> list[dict]:
        """List editable .og and config targets from the workspace (route: GET /authoring/targets).

        Reads config-dir files independently of a live agent session.
        """
        paths = get_hachi_paths()
        targets: list[dict] = []

        # Collect .og skill files from the skills directory.
        skills_dir = paths.skills_dir
        if skills_dir.exists():
            for og_file in sorted(skills_dir.glob("*.og")):
                kind = "brain" if og_file.name.startswith("brain-") else "skill"
                rel = og_file.relative_to(skills_dir).as_posix()
                targets.append({"name": og_file.name, "path": rel, "kind": kind, "dirty": False})

        # Include the permissions file (device-permissions.og).
        perm_file = paths.permissions_file
        if perm_file.exists():
            targets.append(
                {
                    "name": perm_file.name,
                    "path": perm_file.name,
                    "kind": "config",
                    "dirty": False,
                }
            )

        # Include the global config toml.
        config_toml = paths.global_config_toml
        if config_toml.exists():
            targets.append(
                {
                    "name": config_toml.name,
                    "path": config_toml.name,
                    "kind": "config",
                    "dirty": False,
                }
            )

        return targets

    def _resolve_authoring_target_path(self, name: str) -> Path:
        """Resolve a target name to a filesystem path, enforcing security constraints.

        SECURITY: reject any name containing ``/``, ``\\``, or ``..`` to prevent
        path-traversal attacks. Only names present in the allowed target list are
        accepted.

        Args:
            name: The target name (e.g. ``"brain-core.og"``).

        Returns:
            Absolute :class:`~pathlib.Path` to the file.

        Raises:
            GatewayProtocolError: When the name is unsafe or not in the allowed set.
        """
        if "/" in name or "\\" in name or ".." in name:
            raise GatewayProtocolError(f"Invalid target name '{name}'")

        allowed_targets = self.get_authoring_targets()
        allowed_names = {t["name"] for t in allowed_targets}
        if name not in allowed_names:
            raise GatewayProtocolError(f"Target '{name}' not found in allowed set")

        # Re-derive the actual path from the known safe locations.
        paths = get_hachi_paths()
        skill_path = paths.skills_dir / name
        if skill_path.exists():
            return skill_path
        perm_path = paths.permissions_file
        if perm_path.name == name:
            return perm_path
        config_path = paths.global_config_toml
        if config_path.name == name:
            return config_path
        raise GatewayProtocolError(f"Target '{name}' could not be resolved")

    def get_authoring_target(self, name: str) -> dict:
        """Return the source text for a single authoring target (GET /authoring/targets/{name})."""
        file_path = self._resolve_authoring_target_path(name)
        source = file_path.read_text(encoding="utf-8")
        return {"name": name, "source": source}

    # ---- Authoring generate/apply/revert (G1-T2 / CRIT-002) ----
    # Pending proposed content is keyed by target name and stored in two dicts:
    #   _pending_proposals : name -> proposed text (set by generate; consumed by apply)
    #   _revert_snapshots  : name -> prior text    (set by apply;    consumed by revert)
    # Both are protected by _authoring_lock so concurrent requests are serialised.
    # (Instance variables initialised in __init__.)

    def authoring_generate(self, name: str, prompt: str) -> dict:
        """Generate a proposed patch for the named target via the configured LLM.

        Calls the LLM with the current file content + user prompt and returns a
        real unified diff (``[["" | "add" | "del", line], ...]``).  The proposed
        content is stored in ``_pending_proposals`` so that a subsequent
        ``authoring_apply`` can write it without the client re-sending it.

        The LLM call is synchronous and should be run off the event loop by the
        FastAPI route via ``anyio.to_thread.run_sync``.

        Args:
            name: Validated target name (path-traversal already rejected by
                  ``_resolve_authoring_target_path``).
            prompt: Natural-language description of the desired change.

        Returns:
            ``{"diff": [["" | "add" | "del", line], ...]}``

        Raises:
            GatewayProtocolError: If the target cannot be resolved.
            GatewayUnavailableError: If no API key / LLM is available.
        """
        import difflib

        from hachi.core.llm import Message, create_llm_client
        from hachi.core.prompt_composer import get_prompt
        from hachi.gateway.control_plane import GatewayUnavailableError

        file_path = self._resolve_authoring_target_path(name)
        original = file_path.read_text(encoding="utf-8")

        # Build provider config from current effective config.
        config = load_effective_config()
        provider_config = {
            "kind": str(config.provider.kind),
            "model": str(config.provider.model),
            "api_key_ref": getattr(config.provider, "api_key_ref", None),
            "api_key_env": getattr(config.provider, "api_key_env", None),
            "base_url": getattr(config.provider, "base_url", None),
        }

        try:
            client = create_llm_client(provider_config, secret_store=create_default_secret_store())
        except ValueError as exc:
            raise GatewayUnavailableError(
                f"Cannot generate patch: LLM client unavailable — {exc}. "
                "Check your API key configuration."
            ) from exc

        system = get_prompt("authoring.patch.system").text
        user_msg = get_prompt("authoring.patch.user").render(
            name=name, original=original, prompt=prompt
        )

        try:
            response = client.generate(
                messages=[Message(role="user", content=user_msg)],
                system_prompt=system,
            )
        except Exception as exc:
            raise GatewayUnavailableError(
                f"LLM generate call failed: {exc}. Check your API key and model configuration."
            ) from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        proposed = response.content.strip()
        if not proposed:
            raise GatewayUnavailableError("LLM returned an empty response; cannot produce a diff.")

        # Store pending proposal so apply() can write it.
        with self._authoring_lock:
            self._pending_proposals[name] = proposed

        # Build unified diff as list of ["" | "add" | "del", line] pairs.
        orig_lines = original.splitlines()
        prop_lines = proposed.splitlines()
        diff_lines: list[tuple[str, str]] = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, orig_lines, prop_lines, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                for line in orig_lines[i1:i2]:
                    diff_lines.append(("", line))
            elif tag in ("replace", "delete"):
                for line in orig_lines[i1:i2]:
                    diff_lines.append(("del", line))
                if tag == "replace":
                    for line in prop_lines[j1:j2]:
                        diff_lines.append(("add", line))
            elif tag == "insert":
                for line in prop_lines[j1:j2]:
                    diff_lines.append(("add", line))

        return {"diff": diff_lines}

    def authoring_apply(self, name: str) -> dict:
        """Apply the pending proposed content for the named target to disk.

        Performs an atomic write (temp file → replace) inside the skills root and
        saves the prior content as a revert snapshot.  Triggers a skill-engine
        re-index when the target is a ``.og`` file.

        Args:
            name: Validated target name.

        Returns:
            ``{"ok": True, "reindexed_ms": <elapsed_ms>}``

        Raises:
            GatewayProtocolError: If there is no pending proposal for this target.
        """
        import time

        with self._authoring_lock:
            proposed = self._pending_proposals.get(name)

        if proposed is None:
            raise GatewayProtocolError(f"No pending proposal for '{name}'. Call /generate first.")

        file_path = self._resolve_authoring_target_path(name)
        prior = file_path.read_text(encoding="utf-8")

        # Atomic write: write to a sibling temp file then replace.
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        try:
            tmp_path.write_text(proposed, encoding="utf-8")
            tmp_path.replace(file_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        # Store revert snapshot AFTER successful write.
        with self._authoring_lock:
            self._revert_snapshots[name] = prior
            self._pending_proposals.pop(name, None)

        # Trigger skill-engine re-index for .og files.
        reindexed_ms = 0
        if name.endswith(".og"):
            t0 = time.monotonic()
            try:
                with self._agents_lock:
                    agent = next(iter(self._agents.values()), None)
                if agent is not None:
                    reindex = getattr(agent, "reindex_skills", None)
                    if callable(reindex):
                        reindex()
            except Exception:
                pass
            reindexed_ms = int((time.monotonic() - t0) * 1000)

        return {"ok": True, "reindexed_ms": reindexed_ms}

    def authoring_save_source(self, name: str, content: str) -> dict:
        """Persist manually edited .og content for the named target (CONTRACT-AUTHORING-SAVE).

        Validates ``name`` (path-traversal guard), optionally parses the content as
        a valid ObjectGraph (.og files only), and writes it atomically.

        Args:
            name:    Validated target name (e.g. ``"brain-core.og"``).
            content: Full file content to persist.

        Returns:
            ``{"ok": True, "name": name}``

        Raises:
            GatewayProtocolError: When the name is invalid/unknown, or the content
                is empty.
            GatewayUnavailableError: When the content fails OG parsing for .og files.
        """
        if not content.strip():
            raise GatewayProtocolError("content cannot be empty")

        file_path = self._resolve_authoring_target_path(name)

        # For .og files, validate that the content parses before overwriting.
        if name.endswith(".og"):
            import tempfile as _tempfile

            from hachi.gateway.control_plane import GatewayUnavailableError as _Unavail
            from hachi.og_engine.parser import parse_og_file as _parse_og

            _tmp_path: str | None = None
            try:
                with _tempfile.NamedTemporaryFile(
                    mode="w", suffix=".og", delete=False, encoding="utf-8"
                ) as _tmp:
                    _tmp_path = _tmp.name
                    _tmp.write(content)
                _parse_og(_tmp_path)
            except _Unavail:
                raise  # re-raise without wrapping
            except Exception as exc:
                raise _Unavail(
                    f"Content does not parse as a valid ObjectGraph (.og) file: {exc}. "
                    "Fix the syntax and try again."
                ) from exc
            finally:
                if _tmp_path is not None:
                    try:
                        Path(_tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass

        # Atomic write: write to sibling temp file then replace.
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(file_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        # Trigger skill-engine re-index for .og files.
        if name.endswith(".og"):
            try:
                with self._agents_lock:
                    agent = next(iter(self._agents.values()), None)
                if agent is not None:
                    reindex = getattr(agent, "reindex_skills", None)
                    if callable(reindex):
                        reindex()
            except Exception:
                pass

        return {"ok": True, "name": name}

    def authoring_revert(self, name: str) -> dict:
        """Restore the revert snapshot for the named target (undoes the last apply).

        Also discards any pending un-applied proposal for this target.

        Args:
            name: Validated target name.

        Returns:
            ``{"ok": True}``

        Raises:
            GatewayProtocolError: If there is nothing to revert.
        """
        with self._authoring_lock:
            snapshot = self._revert_snapshots.get(name)
            # Also discard any un-applied pending proposal.
            self._pending_proposals.pop(name, None)

        if snapshot is None:
            raise GatewayProtocolError(
                f"Nothing to revert for '{name}'. No prior apply snapshot exists."
            )

        file_path = self._resolve_authoring_target_path(name)
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        try:
            tmp_path.write_text(snapshot, encoding="utf-8")
            tmp_path.replace(file_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        with self._authoring_lock:
            self._revert_snapshots.pop(name, None)

        return {"ok": True}

    def get_spend(self, session_id: str | None = None) -> dict:
        """Return per-turn token/cost rows, compaction events, and model totals.

        Best-effort aggregation: scans the last 500 runtime events for
        ``usage.updated`` events (cumulative counters) and builds per-turn deltas.
        Compaction events (``memory.compaction``) are surfaced as ``tidies``.
        ``models`` is a single entry from the current config + snapshot_usage().

        NOTE: Per-turn input/output token split is not separately tracked in the
        runtime event payload today. The ``inT`` field is always 0 and ``out``
        carries the full token delta. Update when the event payload is enriched.
        """
        # Pick the agent: prefer explicit session_id, fall back to most-recent.
        with self._agents_lock:
            if session_id and session_id in self._agents:
                agent = self._agents[session_id]
            else:
                agent = next(iter(self._agents.values()), None)

        if agent is None:
            return {"turns": [], "tidies": [], "models": []}

        events = agent.snapshot_runtime_events(limit=500)

        turns: list[dict] = []
        tidies: list[dict] = []
        prev_total_tokens: float = 0.0
        prev_total_cost: float = 0.0
        turn_idx = 0

        for event in events:
            event_name = event.name
            payload = event.payload or {}

            # Per-turn rows from usage.updated events (cumulative → delta).
            if event_name == "usage.updated":
                total_tokens = float(payload.get("total_tokens", 0) or 0)
                total_cost = float(payload.get("estimated_cost_usd", 0) or 0)

                delta_tokens = total_tokens - prev_total_tokens
                delta_cost = total_cost - prev_total_cost

                if delta_tokens > 0 or delta_cost > 0:
                    turn_idx += 1
                    # NOTE: Per-turn input/output split not available; out = delta.
                    turns.append(
                        {
                            "n": turn_idx,
                            "inT": 0,
                            "out": int(delta_tokens),
                            "cost": round(delta_cost, 4),
                        }
                    )

                prev_total_tokens = total_tokens
                prev_total_cost = total_cost

            # Compaction events → tidy rows.
            # Real event name from runtime_events.py: "memory.compaction" (COMPACTION).
            if "memory" in event_name and ("compact" in event_name or "tidy" in event_name):
                ts = event.timestamp or ""
                # Use HH:MM portion of ISO timestamp if available.
                if len(ts) >= 16:
                    ts = ts[11:16]
                tidies.append(
                    {
                        "ts": ts,
                        "before": int(payload.get("before_tokens", 0) or 0),
                        "after": int(payload.get("after_tokens", 0) or 0),
                    }
                )

        # Issue 9: real per-model breakdown from the cost tracker (a session that
        # routed to e.g. opus + haiku now shows BOTH, instead of one collapsed entry).
        # Each row carries both the legacy keys (name/tokens/cost — read by the work
        # tab) and the C-SPEND keys (model/cost_usd/input_tokens/output_tokens — read
        # by the Spend settings breakdown), so both consumers are satisfied.
        config = load_effective_config()
        usage = agent.snapshot_usage()
        # AgentSession.loop is always set in __init__ -- read it directly (BB-107).
        # cost_tracker stays getattr-defaulted: ExecutionLoop.cost_tracker always
        # exists too, but this keeps the read consistent with the rest of the
        # codebase's cost_tracker call sites (see check_getattr.py ALLOWLIST).
        tracker = getattr(agent.loop, "cost_tracker", None)
        models: list[dict] = []
        if tracker is not None and hasattr(tracker, "per_model_snapshot"):
            try:
                for mid, snap in tracker.per_model_snapshot().items():
                    _cost = round(float(snap.estimated_cost_usd), 6)
                    models.append(
                        {
                            "name": mid,
                            "model": mid,
                            "tokens": int(snap.input_tokens + snap.output_tokens),
                            "input_tokens": int(snap.input_tokens),
                            "output_tokens": int(snap.output_tokens),
                            "cost": _cost,
                            "cost_usd": _cost,
                            "color": "",  # Client assigns color.
                        }
                    )
            except Exception:
                models = []
        if not models and usage:
            # Fallback: single aggregate entry (no per-model attribution available yet).
            _cost = round(float(usage.get("estimated_cost_usd", 0) or 0), 6)
            models.append(
                {
                    "name": config.provider.model,
                    "model": config.provider.model,
                    "tokens": int(usage.get("total_tokens", 0) or 0),
                    "input_tokens": int(usage.get("input_tokens", 0) or 0),
                    "output_tokens": int(usage.get("output_tokens", 0) or 0),
                    "cost": _cost,
                    "cost_usd": _cost,
                    "color": "",
                }
            )

        # C-SPEND: per-modality / per-session / cumulative breakdown for the Spend UI.
        # Text cost = sum of per-model LLM costs. Voice cost is recorded into the daily
        # ledger (cost.record_voice_usage) but is not yet attributed per-session, so the
        # per-session modality split lists text only. TODO(issue9): modality-tagged ledger.
        text_cost = round(sum(float(m["cost_usd"]) for m in models), 6)
        session_total = (
            round(float(usage.get("estimated_cost_usd", 0) or 0), 6) if usage else text_cost
        )
        cumulative_usd = session_total
        try:
            _daily = self.get_spend_daily()
            cumulative_usd = round(float(_daily.get("spent_usd", session_total) or 0), 6)
        except Exception:
            pass

        # R10: per-feature cost attribution.
        by_feature: dict[str, object] = {}
        if tracker is not None and hasattr(tracker, "per_feature_snapshot"):
            try:
                by_feature = tracker.per_feature_snapshot()  # type: ignore[assignment]
            except Exception:
                pass

        return {
            "turns": turns,
            "tidies": tidies,
            "models": models,
            "by_feature": by_feature,
            "modalities": {"text": text_cost},
            "session": {
                "session_id": session_id or "",
                "cost_usd": session_total,
                "turns": len(turns),
            },
            "cumulative_usd": cumulative_usd,
        }

    def get_spend_daily(self) -> dict:
        """Return today's accumulated spend plus configured caps (GET /spend/daily).

        Reads the daily_spend ledger from SQLite directly — no live agent needed.
        Works immediately after a restart so the frontend can hydrate ``spent`` on load.

        Returns:
            Dict with:
                ``spent_usd``     — cumulative spend for today (local calendar date).
                ``daily_cap_usd`` — configured daily cap or ``None`` if unlimited.
                ``task_cap_usd``  — configured per-task (turn) cap or ``None`` if unlimited.
                ``date``          — today's ISO date string (e.g. ``"2026-06-22"``).
        """
        today = _date.today().isoformat()
        store = SessionStore()
        try:
            spent = store.get_daily_spend(today)
        finally:
            store.shutdown()
        config = load_effective_config()
        return {
            "spent_usd": spent,
            "daily_cap_usd": config.limits.max_daily_cost_usd,
            "task_cap_usd": config.limits.max_turn_cost_usd,
            "date": today,
        }

    def set_capability_enabled(self, cap_id: str, enabled: bool) -> dict:
        """Toggle a capability on/off and persist to global config (PUT /capabilities/{id}/enabled).

        Mirrors update_permissions: load config, mutate, write, return result.
        """
        # BB-124: read the FILE layer only — see update_provider() above. The
        # set of VALID capability ids still comes from the schema defaults
        # (capabilities are never env-overridden) unioned with whatever the
        # file already has — matching what load_effective_config() always
        # exposed (built-ins are seeded by default_config(), not the file).
        from hachi.core.settings import default_config

        doc = read_global_config_file()
        capabilities_doc: dict = dict(doc.get("capabilities") or {})
        known_ids = set(default_config().capabilities.keys()) | set(capabilities_doc.keys())
        if cap_id not in known_ids:
            raise GatewayProtocolError(f"unknown capability '{cap_id}'")

        entry: dict = dict(capabilities_doc.get(cap_id) or {})
        entry["enabled"] = bool(enabled)
        capabilities_doc[cap_id] = entry
        doc["capabilities"] = capabilities_doc
        write_global_config_dict(doc)
        return {
            "id": cap_id,
            "enabled": bool(enabled),
            "configured": bool(entry.get("configured", False)),
        }

    # ------ Memory CRUD ------

    _VAULT_GROUP_MAP: dict[str, str] = {
        "core_memory": "upsert_fact",
        "user_profile": "upsert_user_fact",
        "agent_profile": "upsert_agent_fact",
    }

    def add_memory_fact(self, group: str, text: str) -> dict:
        """Add a fact to the specified vault collection (POST /memory/facts).

        Maps ``group`` to the appropriate upsert method on MemoryVault:
          - ``core_memory``   → vault.upsert_fact
          - ``user_profile``  → vault.upsert_user_fact
          - ``agent_profile`` → vault.upsert_agent_fact

        Args:
            group: Collection name — one of core_memory, user_profile, agent_profile.
            text:  Plain-text fact to persist.

        Returns:
            Dict with ``id`` (the content-hash fact id, possibly prefixed with
            ``BLOCKED:`` or ``LIMIT_REACHED`` when the vault rejects the write).
        """
        if not text.strip():
            raise GatewayProtocolError("text cannot be empty")
        if group not in self._VAULT_GROUP_MAP:
            raise GatewayProtocolError(
                f"group must be one of: {', '.join(sorted(self._VAULT_GROUP_MAP))}"
            )
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None or agent.vault is None:
            return {"id": "", "error": "no active session or vault unavailable"}
        method_name = self._VAULT_GROUP_MAP[group]
        upsert_fn = getattr(agent.vault, method_name, None)
        if not callable(upsert_fn):
            return {"id": "", "error": "vault method unavailable"}
        try:
            fact_id: str = upsert_fn(text.strip())
        except Exception as exc:
            return {"id": "", "error": f"vault write failed: {exc}"}
        return {"id": fact_id}

    # ==========================================
    # G1 — new handler implementations
    # ==========================================

    def update_limits(self, payload: dict) -> dict:
        """Persist per-task and daily spend caps to global config (PUT /config/limits).

        Reads the existing config, updates limits.max_turn_cost_usd (frontend:
        ``per_task_cap_usd``) and limits.max_daily_cost_usd (frontend: ``daily_cap_usd``),
        writes back, and returns the updated config dump.

        Args:
            payload: Dict with optional ``per_task_cap_usd`` and ``daily_cap_usd`` (float, USD).

        Returns:
            Updated config dict.
        """
        per_task = payload.get("per_task_cap_usd")
        daily = payload.get("daily_cap_usd")

        # BB-124: read the FILE layer only — see update_provider() above.
        doc = read_global_config_file()
        limits_doc: dict = dict(doc.get("limits") or {})
        if per_task is not None:
            try:
                val = float(per_task)
                limits_doc["max_turn_cost_usd"] = val if val > 0 else None
            except (TypeError, ValueError):
                raise GatewayProtocolError("per_task_cap_usd must be a number") from None
        if daily is not None:
            try:
                val = float(daily)
                # max_daily_cost_usd is the rolling calendar-day cap; distinct from
                # max_session_cost_usd which caps a single conversation session.
                limits_doc["max_daily_cost_usd"] = val if val > 0 else None
            except (TypeError, ValueError):
                raise GatewayProtocolError("daily_cap_usd must be a number") from None

        doc["limits"] = limits_doc
        write_global_config_dict(doc)

        from hachi.core.settings import ResolvedConfig

        return ResolvedConfig.model_validate(doc).model_dump(mode="python", exclude_none=True)

    def add_allowed_folder(self, folder_path: str) -> dict:
        """Append a folder path to device-permissions.og allowed_paths.

        Mutates the on-disk device-permissions.og file using the same approach
        as the ``hachi permissions --allow`` CLI command.

        Args:
            folder_path: The path to add. Will be expanded and resolved.

        Returns:
            Dict with ok=True and the resolved path.
        """
        from pathlib import Path as _Path

        path = folder_path.strip()
        if not path:
            raise GatewayProtocolError("path cannot be empty")

        permissions_file = get_permissions_file()
        if not permissions_file.exists():
            raise GatewayProtocolError("device-permissions.og not found — run `hachi setup` first")

        resolved = str(_Path(path).expanduser().resolve())
        content = permissions_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        modified = False
        for i, line in enumerate(lines):
            if line.strip().startswith("allowed_paths="):
                current_value = line.split("=", 1)[1].strip()
                existing = [t.strip() for t in current_value.split(",") if t.strip()]
                if resolved not in existing:
                    existing.append(resolved)
                    lines[i] = f"allowed_paths={', '.join(existing)}"
                    modified = True
                break

        if modified:
            permissions_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {"ok": True, "path": resolved, "modified": modified}

    def add_allowed_command(self, command: str) -> dict:
        """Append a command to device-permissions.og allowed_commands.

        Mutates the on-disk device-permissions.og file.

        Args:
            command: The command name to add.

        Returns:
            Dict with ok=True and the command.
        """
        cmd = command.strip()
        if not cmd:
            raise GatewayProtocolError("command cannot be empty")

        permissions_file = get_permissions_file()
        if not permissions_file.exists():
            raise GatewayProtocolError("device-permissions.og not found — run `hachi setup` first")

        content = permissions_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        modified = False
        for i, line in enumerate(lines):
            if line.strip().startswith("allowed_commands="):
                current_value = line.split("=", 1)[1].strip()
                existing = [t.strip() for t in current_value.split(",") if t.strip()]
                if cmd not in existing:
                    existing.append(cmd)
                    lines[i] = f"allowed_commands={', '.join(existing)}"
                    modified = True
                break

        if modified:
            permissions_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {"ok": True, "command": cmd, "modified": modified}

    def remove_allowed_folder(self, folder_path: str) -> dict:
        """Remove a folder path from device-permissions.og allowed_paths."""
        from pathlib import Path as _Path

        path = folder_path.strip()
        if not path:
            raise GatewayProtocolError("path cannot be empty")

        permissions_file = get_permissions_file()
        if not permissions_file.exists():
            raise GatewayProtocolError("device-permissions.og not found — run `hachi setup` first")

        resolved = str(_Path(path).expanduser().resolve())
        content = permissions_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        modified = False
        for i, line in enumerate(lines):
            if line.strip().startswith("allowed_paths="):
                current_value = line.split("=", 1)[1].strip()
                existing = [t.strip() for t in current_value.split(",") if t.strip()]
                # Accept both the raw path and the resolved path for removal.
                filtered = [p for p in existing if p != path and p != resolved]
                if len(filtered) != len(existing):
                    lines[i] = f"allowed_paths={', '.join(filtered)}"
                    modified = True
                break

        if modified:
            permissions_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {"ok": True, "path": path, "modified": modified}

    def remove_allowed_command(self, command: str) -> dict:
        """Remove a command from device-permissions.og allowed_commands."""
        cmd = command.strip()
        if not cmd:
            raise GatewayProtocolError("command cannot be empty")

        permissions_file = get_permissions_file()
        if not permissions_file.exists():
            raise GatewayProtocolError("device-permissions.og not found — run `hachi setup` first")

        content = permissions_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        modified = False
        for i, line in enumerate(lines):
            if line.strip().startswith("allowed_commands="):
                current_value = line.split("=", 1)[1].strip()
                existing = [t.strip() for t in current_value.split(",") if t.strip()]
                filtered = [c for c in existing if c != cmd]
                if len(filtered) != len(existing):
                    lines[i] = f"allowed_commands={', '.join(filtered)}"
                    modified = True
                break

        if modified:
            permissions_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {"ok": True, "command": cmd, "modified": modified}

    def add_always_allow(self, session_id: str, request: str, action: str) -> dict:
        """Persist a standing always-allow rule to the global config.

        Writes the rule to a TOML config section so it survives gateway restart.
        The rule is a (request, action) pair; on boot the PermissionGate checks
        this list before prompting.

        Args:
            session_id: The session making the request ("current" = any active session).
            request:    The resource/tool being requested (e.g. a filesystem path).
            action:     The operation (e.g. "read", "write", "execute").

        Returns:
            Dict with ok=True and the persisted rule.
        """
        import tomlkit as _tomlkit

        from hachi.core.paths import get_hachi_paths as _get_paths

        req = request.strip()
        act = action.strip()
        if not req or not act:
            raise GatewayProtocolError("request and action cannot be empty")

        paths = _get_paths()
        config_toml = paths.global_config_toml

        if config_toml.exists():
            doc = _tomlkit.parse(config_toml.read_text(encoding="utf-8"))
        else:
            doc = _tomlkit.document()

        # Store rules under [always_allow] as a list of {request, action} tables.
        if "always_allow" not in doc:
            doc["always_allow"] = _tomlkit.array()  # type: ignore[assignment]

        rules = list(doc.get("always_allow") or [])
        # Idempotent: don't add duplicates.
        for rule in rules:
            if isinstance(rule, dict) and rule.get("request") == req and rule.get("action") == act:
                return {"ok": True, "request": req, "action": act, "already_existed": True}

        new_rule: dict = {"request": req, "action": act}
        rules.append(new_rule)
        doc["always_allow"] = rules  # type: ignore[assignment]

        tmp = config_toml.with_name(f"{config_toml.name}.tmp")
        tmp.write_text(_tomlkit.dumps(doc), encoding="utf-8")
        import os as _os

        _os.replace(tmp, config_toml)

        return {"ok": True, "request": req, "action": act, "already_existed": False}

    def register_mcp_server(
        self,
        name: str,
        transport: str = "stdio",
        command: str = "",
        args: str = "",
        env_vars: dict[str, str] | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Register a new MCP server and bring it online live (POST /runtime/mcp/register).

        Persists the server definition under ``[mcp_servers.NAME]`` in settings.toml
        (so it survives gateway restarts) and then attempts a hot-reload: the server
        is connected immediately and its tools are added to the live ToolRegistry so
        that ``GET /runtime/mcp/inventory`` reflects the new server without a restart.

        Args:
            name:      Slug identifier for the server.
            transport: ``"stdio"``, ``"sse"``, or ``"http"``.
            command:   Launcher command (npx / uvx / python / node / uv / python3); stdio only.
            args:      Space-separated package / script args; stdio only.
            env_vars:  Optional plain (non-secret) environment variables.
            url:       Remote server URL; sse / http only.
            headers:   Optional HTTP request headers; sse / http only.

        Returns:
            Dict with name, transport, message, and live_status.
        """
        import tomlkit as _tomlkit

        from hachi.core.mcp_client import MCPServerConfig as _MCPServerConfig
        from hachi.core.paths import get_hachi_paths as _get_paths

        env_vars = env_vars or {}
        headers = headers or {}

        # --- 1. Persist to settings.toml ---
        paths = _get_paths()
        config_toml = paths.global_config_toml

        if config_toml.exists():
            doc = _tomlkit.parse(config_toml.read_text(encoding="utf-8"))
        else:
            doc = _tomlkit.document()

        if "mcp_servers" not in doc:
            doc.add("mcp_servers", _tomlkit.table())  # type: ignore[arg-type]

        cfg = _MCPServerConfig(
            name=name,
            transport=transport,  # type: ignore[arg-type]
            command=command,
            args=[a for a in args.split() if a] if args.strip() else [],
            env=env_vars,
            url=url,
            headers=headers,
        )
        server_entry = cfg.to_toml_entry()

        # Edit ONLY the targeted server's sub-table, preserving lifecycle fields we
        # don't model in MCPServerConfig (notably `enabled`, written by
        # set_mcp_enabled). A blind ``mcp_section[name] = server_entry`` would rewrite
        # the whole entry from the in-memory config and silently drop `enabled` on every
        # re-registration (BB-19). On re-register: overwrite the keys this config owns,
        # prune stale managed keys (e.g. a stdio `command` after switching to http), and
        # leave every other key untouched.
        mcp_section = doc["mcp_servers"]
        existing = mcp_section.get(name)  # type: ignore[union-attr]
        if existing is None:
            mcp_section[name] = server_entry  # type: ignore[index]
        else:
            managed = _MCPServerConfig.TOML_MANAGED_KEYS
            for stale in [k for k in existing if k in managed and k not in server_entry]:
                del existing[stale]
            for key, value in server_entry.items():
                existing[key] = value

        import os as _os

        tmp = config_toml.with_name(f"{config_toml.name}.tmp")
        tmp.write_text(_tomlkit.dumps(doc), encoding="utf-8")
        _os.replace(tmp, config_toml)

        # --- 2. Hot-reload: connect the server live in the running agent ---
        live_status = "pending_restart"
        live_tool_count = 0
        live_error: str | None = None

        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)

        if agent is not None:
            try:
                result = agent.add_mcp_server_live(cfg, agent.loop.registry)
                if result.get("ok"):
                    live_status = "ready"
                    live_tool_count = int(result.get("tool_count", 0))
                else:
                    live_status = "failed"
                    live_error = str(result.get("error", "startup failed"))
            except Exception as exc:
                live_status = "failed"
                live_error = str(exc)

        base_msg = f"MCP server '{name}' registered (transport={transport})."
        if live_status == "ready":
            msg = f"{base_msg} Connected live — {live_tool_count} tool(s) available immediately."
        elif live_status == "failed":
            msg = (
                f"{base_msg} Live connect failed ({live_error}); "
                "server will be retried on next gateway restart."
            )
        else:
            msg = f"{base_msg} Restart gateway to activate (no active agent session)."

        return {
            "name": name,
            "transport": transport,
            "message": msg,
            "live_status": live_status,
            "tool_count": live_tool_count,
        }

    def _mcp_live_server_names(self) -> set[str]:
        """Names of MCP servers currently connected in any live agent."""
        names: set[str] = set()
        with self._agents_lock:
            agents = list(self._agents.values())
        for agent in agents:
            mgr = getattr(agent, "mcp_manager", None)
            if mgr is not None:
                names.update(getattr(mgr, "_servers", {}).keys())
        return names

    def _mcp_node_names(self) -> set[str]:
        """Names of all role=mcp .og nodes known to a live agent's skill engine."""
        names: set[str] = set()
        with self._agents_lock:
            agents = list(self._agents.values())
        for agent in agents:
            engine = getattr(agent, "skill_engine", None)
            if engine is None:
                continue
            try:
                for node in engine.get_nodes_by_role("mcp"):
                    names.add(node.name)
            except Exception:
                continue
        return names

    def set_mcp_enabled(self, name: str, enabled: bool) -> dict:
        """Enable/disable an MCP server: persist the flag and apply it live.

        Persists ``enabled`` under ``[mcp_servers.NAME]`` in settings.toml
        (creating the entry for .og-declared servers so the boot path can honour
        it) and applies the change to every live agent's tool registry without a
        gateway restart.  Raises ``HTTPException(404)`` only when the server is
        unknown across all sources (live connection, settings.toml, .og nodes).

        Args:
            name:    Server name as it appears in ``GET /runtime/mcp/inventory``.
            enabled: ``True`` to enable, ``False`` to disable.

        Returns:
            Dict with ``ok``, ``name``, ``enabled``, and ``applied_live``.
        """
        import os as _os

        import tomlkit as _tomlkit
        from fastapi import HTTPException as _HTTPException

        from hachi.core.paths import get_hachi_paths as _get_paths

        config_toml = _get_paths().global_config_toml
        if config_toml.exists():
            doc = _tomlkit.parse(config_toml.read_text(encoding="utf-8"))
        else:
            doc = _tomlkit.document()
        if "mcp_servers" not in doc:
            doc.add("mcp_servers", _tomlkit.table())
        mcp_section = doc["mcp_servers"]

        known = (
            name in mcp_section
            or name in self._mcp_live_server_names()
            or name in self._mcp_node_names()
        )
        if not known:
            raise _HTTPException(status_code=404, detail=f"MCP server {name!r} not found")

        # Persist the flag (create a minimal entry for .og-only servers so boot
        # can honour it).
        if name in mcp_section:
            mcp_section[name]["enabled"] = enabled
        else:
            entry = _tomlkit.table()
            entry["enabled"] = enabled
            mcp_section[name] = entry

        tmp = config_toml.with_name(f"{config_toml.name}.tmp")
        tmp.write_text(_tomlkit.dumps(doc), encoding="utf-8")
        _os.replace(tmp, config_toml)

        # Apply live to every agent (tools appear/disappear without a restart).
        applied_live = False
        with self._agents_lock:
            agents = list(self._agents.values())
        for agent in agents:
            if hasattr(agent, "set_mcp_tools_active"):
                try:
                    res = agent.set_mcp_tools_active(name, enabled)
                    applied_live = applied_live or bool(res.get("live"))
                except Exception:
                    _logger.warning(
                        "set_mcp_enabled: live apply failed for %r", name, exc_info=True
                    )

        return {"ok": True, "name": name, "enabled": enabled, "applied_live": applied_live}

    def delete_mcp_server(self, name: str) -> dict:
        """Delete an MCP server everywhere it lives and tear it down live.

        Idempotent: deleting an unknown / already-deleted server returns
        ``{"ok": True, "already_absent": True}`` (HTTP 200) rather than 404, so a
        repeated delete from the UI never surfaces an error.

        Removes the live connection + its tools, the ``[mcp_servers.NAME]``
        settings.toml entry, and a user-authored ``mcp-<name>.og`` file.  For a
        bundled / non-deletable .og server, writes an ``enabled = false``
        tombstone so it does not return on the next boot.

        Args:
            name: Server name as it appears in ``GET /runtime/mcp/inventory``.

        Returns:
            Dict describing what was removed.
        """
        import os as _os

        import tomlkit as _tomlkit

        from hachi.core.paths import get_hachi_paths as _get_paths

        # 1. Tear down the live connection + tools so the inventory drops it now.
        removed_live = False
        with self._agents_lock:
            agents = list(self._agents.values())
        for agent in agents:
            if hasattr(agent, "remove_mcp_server_live"):
                try:
                    res = agent.remove_mcp_server_live(name)
                    removed_live = removed_live or bool(res.get("removed_live"))
                except Exception:
                    _logger.warning(
                        "delete_mcp_server: live teardown failed for %r", name, exc_info=True
                    )

        # 2. Remove the settings.toml entry (unless it is already a tombstone).
        config_toml = _get_paths().global_config_toml
        if config_toml.exists():
            doc = _tomlkit.parse(config_toml.read_text(encoding="utf-8"))
        else:
            doc = _tomlkit.document()
        mcp_section = doc.get("mcp_servers", {})
        removed_toml = False
        was_tombstone = False
        if name in mcp_section:
            entry = mcp_section[name]
            is_tombstone = (
                entry.get("enabled") is False and not entry.get("command") and not entry.get("url")
            )
            if is_tombstone:
                was_tombstone = True  # already deleted earlier — keep the tombstone
            else:
                del mcp_section[name]
                removed_toml = True

        # 3. Remove a user-authored mcp-<name>.og file (created via add_mcp_server).
        removed_og = False
        try:
            from hachi.gateway.workspace import get_skills_dir

            skills_dir = get_skills_dir()
            og_file = skills_dir / f"mcp-{name}.og"
            if og_file.exists():
                og_file.unlink()
                removed_og = True
                from hachi.tools.config_tools import _invalidate_skill_engine_cache

                _invalidate_skill_engine_cache(skills_dir)
        except Exception:
            _logger.warning("delete_mcp_server: failed to remove mcp-%s.og", name, exc_info=True)

        # 4. Bundled / non-deletable .og server → tombstone so it stays gone.
        tombstoned = False
        if removed_live and not removed_toml and not removed_og and not was_tombstone:
            if "mcp_servers" not in doc:
                doc.add("mcp_servers", _tomlkit.table())
            tombstone = _tomlkit.table()
            tombstone["enabled"] = False
            doc["mcp_servers"][name] = tombstone
            tombstoned = True

        if removed_toml or tombstoned:
            tmp = config_toml.with_name(f"{config_toml.name}.tmp")
            tmp.write_text(_tomlkit.dumps(doc), encoding="utf-8")
            _os.replace(tmp, config_toml)

        found = removed_live or removed_toml or removed_og or was_tombstone or tombstoned
        return {
            "ok": True,
            "name": name,
            "removed_live": removed_live,
            "removed_toml": removed_toml,
            "removed_og": removed_og,
            "tombstoned": tombstoned,
            "already_absent": not found,
        }

    def get_kennel(self) -> list[dict]:
        """Return the skill kennel catalogue (GET /skills/kennel).

        Returns bundled community packs when available, otherwise falls back to
        the agent's installed skill index so the browse panel is never empty.
        """
        try:
            from hachi.core.bundled_packs import list_bundled_manifests

            manifests = list_bundled_manifests()
            if manifests:
                return [
                    {
                        "id": m.id,
                        "name": _pack_display_name(m.id),
                        "description": m.description,
                        "version": m.semver,
                        "tags": sorted(m.scope_ids()),
                    }
                    for m in manifests
                ]
            _logger.warning(
                "get_kennel: no bundled pack manifests found under hachi/packs — "
                "the kennel will fall back to installed skills"
            )
        except Exception:
            _logger.warning("get_kennel: bundled pack listing failed", exc_info=True)

        # Fallback: expose installed skills as kennel items.
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return []
        skills = agent.snapshot_skills() if hasattr(agent, "snapshot_skills") else []
        return [
            {
                "id": getattr(s, "name", str(s)),
                "name": getattr(s, "name", str(s)),
                "description": "",
                "version": "",
                "tags": [],
            }
            for s in skills
        ]

    def add_skill_from_kennel(self, kennel_id: str) -> dict:
        """Install a skill from the kennel (POST /skills/add).

        Installs a bundled pack's ``.og`` files into the user's skills
        directory and re-indexes the live agent (if any) so the pack is
        visible for the rest of this session.

        Only bundled packs are installed today — there is no remote/community
        pack registry (that would be a new network surface and a new
        dependency decision; see T-04 spec ยง9).

        Args:
            kennel_id: The kennel item id returned by GET /skills/kennel.

        Returns:
            Dict with ok=True, the installed pack id, and the installed files.

        Raises:
            GatewayProtocolError: kennel_id is empty or names an unknown pack —
                an unknown pack must never report success.
        """
        if not kennel_id.strip():
            raise GatewayProtocolError("kennel_id cannot be empty")

        from hachi.core.bundled_packs import install_bundled_pack

        # BB-5: paths.workspace_dir does not exist — use skills_dir. Deliberately
        # NOT a local `from hachi.core.paths import get_hachi_paths` here: that
        # would rebind the name to hachi.core.paths's own attribute and silently
        # bypass a `patch("hachi.cli.main.get_hachi_paths", ...)` in tests, which
        # is the established pattern (tests/test_backup_restore.py) for keeping
        # this call off the developer's real ~/.hachi data dir. The module-level
        # import at the top of this file already makes the bare call patchable.
        skills_dir = get_hachi_paths().skills_dir
        result = install_bundled_pack(kennel_id.strip(), skills_dir, confirm=False)
        if result is None:
            raise GatewayProtocolError(f"unknown kennel pack: {kennel_id!r}")
        manifest, installed_files = result

        # Re-index so the newly installed .og files are visible this session.
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is not None:
            try:
                agent.skill_engine.load_directory(skills_dir)
            except Exception:
                _logger.warning(
                    "add_skill_from_kennel: re-index after installing %r failed; "
                    "the pack is on disk but will not be visible until restart",
                    kennel_id,
                    exc_info=True,
                )

        return {
            "ok": True,
            "kennel_id": manifest.id,
            "installed": True,
            "files": installed_files,
        }

    def delete_memory_fact(self, fact_id: str) -> dict:
        """Delete a fact from the vault by its content-hash id (DELETE /memory/facts/{fact_id}).

        Tries to delete from all three collections; Chroma deletes are idempotent
        on missing IDs so multi-collection sweep is safe.

        Args:
            fact_id: SHA-256 content hash returned by any upsert method.

        Returns:
            Dict with ``ok`` boolean.
        """
        if not fact_id.strip():
            raise GatewayProtocolError("fact_id cannot be empty")
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None or agent.vault is None:
            return {"ok": False, "error": "no active session or vault unavailable"}
        vault = agent.vault
        # Try all three writable collections — each delete is idempotent on absent ids.
        results: list[bool] = []
        for fn_name in ("delete_fact", "delete_user_fact", "delete_agent_fact"):
            delete_fn = getattr(vault, fn_name, None)
            if callable(delete_fn):
                try:
                    results.append(bool(delete_fn(fact_id.strip())))
                except Exception:
                    results.append(False)
        return {"ok": any(results)}

    # ------------------------------------------------------------------ #
    # Credential manager (BB-9 / cred: namespace)
    # ------------------------------------------------------------------ #

    _CRED_PREFIX = "cred:"

    def store_secret(self, name: str, value: str) -> dict:
        """Store *value* under ``cred:<name>`` in the secret store."""
        from hachi.core.secret_store import create_default_secret_store

        store = create_default_secret_store()
        try:
            store.set(
                f"{self._CRED_PREFIX}{name}",
                value,
                metadata={
                    "name": f"{self._CRED_PREFIX}{name}",
                    "labels": {"source": "cred-manager"},
                },
            )
        except RuntimeError as exc:
            raise GatewayProtocolError(f"secret backend unavailable: {exc}") from exc
        return {"ok": True, "name": name}

    def list_secret_names(self) -> dict:
        """Return ``{names: [...]}`` — only ``cred:`` prefixed entries, prefix stripped."""
        from hachi.core.secret_store import create_default_secret_store

        rows = create_default_secret_store().list_metadata()
        names = [
            r["name"][len(self._CRED_PREFIX) :]
            for r in rows
            if str(r.get("name", "")).startswith(self._CRED_PREFIX)
        ]
        return {"names": sorted(names)}

    def delete_secret(self, name: str) -> dict:
        """Delete ``cred:<name>`` (idempotent; ignores requests for non-cred: keys)."""
        from hachi.core.secret_store import create_default_secret_store

        create_default_secret_store().delete(f"{self._CRED_PREFIX}{name}")
        return {"ok": True}

    def reload_search_tools(self) -> dict:
        """Re-evaluate search-provider availability after a credential change."""
        with self._agents_lock:
            agent = next(iter(self._agents.values()), None)
        if agent is None:
            return {
                "ok": True,
                "live": False,
                "note": "search tools activate in your next conversation",
            }
        try:
            live = bool(agent.reload_search_provider())
        except Exception as exc:
            return {"ok": False, "live": False, "note": str(exc)}
        return {
            "ok": True,
            "live": live,
            "note": "search tools active" if live else "no search provider configured",
        }

    # ------------------------------------------------------------------
    # Multi-provider admin (provider_admin delegates)
    # ------------------------------------------------------------------

    def list_providers(self) -> dict:
        """Return all configured providers with secrets masked (GET /config/providers).

        Returns:
            Dict with ``providers`` list.
        """
        from hachi.gateway.provider_admin import list_providers as _list_providers

        return _list_providers()

    def add_or_update_provider(self, name: str, spec: dict) -> dict:
        """Upsert a named provider in config.providers.

        Args:
            name: Provider name (``__primary__`` updates the primary provider).
            spec: Dict with kind, model, base_url, allowed_models, headers, api_key.

        Returns:
            Masked provider entry after write.
        """
        from hachi.gateway.provider_admin import add_or_update_provider as _upsert

        result = _upsert(name, spec)
        self._reload_agents()
        return result

    def remove_provider(self, name: str) -> dict:
        """Remove a named provider from config.

        Args:
            name: Provider name to remove.

        Returns:
            ``{"ok": True, "name": name}``.
        """
        from hachi.gateway.provider_admin import remove_provider as _remove

        result = _remove(name)
        self._reload_agents()
        return result

    def get_routing(self) -> dict:
        """Return the capability routing matrix (GET /config/routing).

        Returns:
            Dict with default_model and capabilities list.
        """
        from hachi.gateway.provider_admin import get_routing as _get_routing

        return _get_routing()

    def set_routing(self, default_model: str, capabilities: dict) -> dict:
        """Persist the routing matrix (PUT /config/routing).

        Phase 3 hot-reload: instead of rebooting sessions (_reload_agents), this
        builds a new RoutingSnapshot from the freshly-written config and publishes
        it to the process-level RoutingConfigStore.  Each session's ExecutionLoop
        will notice the bumped version at its own next turn boundary and call
        router.apply_snapshot() — no restart, no killed in-flight turns.

        _reload_agents() is still called for provider/credential changes (those bake
        the API key and base URL into the client at boot; a snapshot swap can't
        rebuild them).

        Args:
            default_model: Default/fallback model id.
            capabilities: Mapping of capability name → ordered model-id list.

        Returns:
            New routing snapshot.
        """
        from hachi.core.config_loader import load_effective_config
        from hachi.core.resilience import RetryPolicy
        from hachi.core.routing.policy import RoutingPolicy
        from hachi.core.routing.registry import ModelRegistry
        from hachi.core.routing.snapshot import RoutingSnapshot
        from hachi.gateway.provider_admin import set_routing as _set_routing
        from hachi.gateway.shared_services import ProcessSharedServices

        # 1. Persist to config (writes settings.toml, returns current routing dict).
        result = _set_routing(default_model, capabilities)

        # 2. Try to publish a new snapshot so live sessions adopt it at turn boundary.
        try:
            shared = ProcessSharedServices.instance()
            store = getattr(shared, "routing_store", None) if shared is not None else None
            if store is not None:
                fresh_cfg = load_effective_config()
                fresh_registry = ModelRegistry.build(fresh_cfg, None)
                fresh_policy = RoutingPolicy.from_config(fresh_cfg)
                if fresh_policy.auto:
                    fresh_policy = fresh_policy.derive_auto_policy(fresh_registry)
                fresh_resilience = RetryPolicy.from_config(fresh_cfg)
                snap = RoutingSnapshot(
                    registry=fresh_registry,
                    policy=fresh_policy,
                    resilience=fresh_resilience,
                    version=store.version + 1,
                    source="ui",
                )
                store.apply(snap)
                _logger.info(
                    "set_routing: published snapshot v%d (default=%s) — "
                    "sessions will adopt at next turn boundary",
                    store.version,
                    default_model,
                )
            else:
                # No store yet (gateway started without routing_store initialised).
                # Fall back to the old behaviour so a routing change still takes effect.
                self._reload_agents()
        except Exception:
            _logger.warning(
                "set_routing: snapshot publish failed — falling back to _reload_agents()",
                exc_info=True,
            )
            self._reload_agents()

        return result

    def validate_routing(self, default_model: str, capabilities: dict) -> dict:
        """Validate a routing config without persisting (POST /config/routing/validate).

        Returns:
            ``{"ok": bool, "issues": list[str], "warnings": list[str]}``.
        """
        from hachi.core.routing.policy import RoutingPolicy
        from hachi.core.routing.registry import ModelRegistry
        from hachi.core.routing.validate import validate_routing as _validate

        try:
            policy = RoutingPolicy(
                default_model=default_model,
                capabilities={k: v for k, v in (capabilities or {}).items()},
            )
            result = _validate(ModelRegistry.build({}, None), policy, providers=None)
            return {"ok": result.ok, "issues": result.issues, "warnings": result.warnings}
        except Exception as exc:
            return {"ok": False, "issues": [str(exc)], "warnings": []}

    def rollback_routing(self) -> dict:
        """Restore the last-known-good routing snapshot (POST /config/routing/rollback).

        Returns:
            ``{"ok": bool, "detail": str, "routing": dict | None}``.
        """
        from hachi.gateway.shared_services import ProcessSharedServices

        shared = ProcessSharedServices.instance()
        store = getattr(shared, "routing_store", None) if shared is not None else None
        if store is None:
            return {"ok": False, "detail": "routing store not available", "routing": None}
        restored = store.rollback()
        if restored is None:
            return {"ok": False, "detail": "no prior snapshot to restore", "routing": None}
        return {
            "ok": True,
            "detail": f"restored v{store.version} (default={restored.policy.default_model})",
            "routing": self.get_routing(),
        }

    def get_routing_audit(self, limit: int = 50) -> list:
        """Return recent routing-change audit records (GET /config/routing/audit).

        Returns:
            List of audit records (newest first). Empty when no changes recorded.
        """
        from hachi.gateway.shared_services import ProcessSharedServices

        shared = ProcessSharedServices.instance()
        store = getattr(shared, "routing_store", None) if shared is not None else None
        if store is None:
            return []
        return store.audit_records(limit=limit)

    def test_provider_connection(self, name: str) -> dict:
        """Best-effort connectivity test for a provider.

        Args:
            name: Provider name (``__primary__`` for the primary provider).

        Returns:
            ``{"ok": bool, "detail": str}``.
        """
        from hachi.gateway.provider_admin import test_connection

        return test_connection(name)

    def list_all_models(self) -> dict:
        """Return model ids per configured provider (GET /config/models/all).

        Returns:
            Dict keyed by provider name → list of model ids.
        """
        from hachi.gateway.provider_admin import list_all_models as _list_all_models

        return _list_all_models()

    def probe_provider(self, spec: dict) -> dict:
        """Probe an unsaved provider spec (POST /config/providers/probe).

        Args:
            spec: ``{kind, base_url, api_key, headers, name}``.

        Returns:
            ``{"ok": bool, "detail": str, "models": list[str]}``.
        """
        from hachi.gateway.provider_admin import probe_provider as _probe

        return _probe(spec)


def _pack_display_name(pack_id: str) -> str:
    """Derive a kennel display name from a bundled pack id.

    E.g. ``"crm-agent"`` -> ``"Crm Agent"``. Deliberately no translation table
    (T-04 spec ยง5) — add one only if a specific pack id titles badly.
    """
    return pack_id.replace("-", " ").title()
