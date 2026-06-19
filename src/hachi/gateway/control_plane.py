from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True)
class ConnectRequest:
    client_id: str
    surface: str
    protocol_version: str
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectResponse:
    session_id: str
    protocol_version: str
    accepted: bool
    server_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ChatRequest:
    session_id: str
    prompt: str


@dataclass(frozen=True)
class ChatResponse:
    session_id: str
    response: str


@dataclass(frozen=True)
class GatewayEvent:
    sequence: int
    event_type: str
    payload: dict[str, Any]
    time: str


class GatewayProtocolError(ValueError):
    """Raised when a typed gateway envelope is malformed."""


class GatewayUnavailableError(RuntimeError):
    """Raised when a requested gateway operation is unavailable."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class GatewayControlPlane:
    """Typed gateway control plane with runtime, read-model, and setup/config surfaces."""

    def __init__(
        self,
        readiness_probe: Callable[[], tuple[bool, list[str]]] | None = None,
        on_connect: Callable[[str, ConnectRequest], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        on_chat: Callable[[str, str], str] | None = None,
        read_events: Callable[[str, int, int], list[GatewayEvent]] | None = None,
        get_session_state: Callable[[str], dict[str, Any]] | None = None,
        get_recent_sessions: Callable[[int], list[dict[str, Any]]] | None = None,
        get_runtime_events: Callable[[str | None, int], list[dict[str, Any]]] | None = None,
        get_mcp_inventory: Callable[[], dict[str, Any]] | None = None,
        get_active_jobs: Callable[[], list[dict[str, Any]]] | None = None,
        get_failed_jobs: Callable[[int], list[dict[str, Any]]] | None = None,
        get_permission_audit: Callable[[int], list[dict[str, Any]]] | None = None,
        get_subagent_reports: Callable[[str, int], list[dict[str, Any]]] | None = None,
        get_capabilities: Callable[[], list[dict[str, Any]]] | None = None,
        get_config: Callable[[], dict[str, Any]] | None = None,
        update_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        validate_provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        update_permissions: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        update_memory: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        list_models: Callable[[], list[dict[str, Any]]] | None = None,
        get_setup_state: Callable[[], dict[str, Any]] | None = None,
        apply_setup: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        list_schedules: Callable[[], list[dict[str, Any]]] | None = None,
        cancel_schedule: Callable[[str], dict[str, Any]] | None = None,
        toggle_schedule: Callable[[str, bool], dict[str, Any]] | None = None,
        get_skills: Callable[[], list[dict[str, Any]]] | None = None,
        get_memory: Callable[[], dict[str, Any]] | None = None,
        get_memory_recall: Callable[[str, int], list[dict[str, Any]]] | None = None,
        get_og_graph: Callable[[], dict[str, Any]] | None = None,
        get_authoring_targets: Callable[[], list[dict[str, Any]]] | None = None,
        get_authoring_target: Callable[[str], dict[str, Any]] | None = None,
        authoring_apply: Callable[[str], dict[str, Any]] | None = None,
        authoring_revert: Callable[[str], dict[str, Any]] | None = None,
        get_spend: Callable[[str | None], dict[str, Any]] | None = None,
        set_capability_enabled: Callable[[str, bool], dict[str, Any]] | None = None,
        add_memory_fact: Callable[[str, str], dict[str, Any]] | None = None,
        delete_memory_fact: Callable[[str], dict[str, Any]] | None = None,
        max_active_sessions: int = 100,
        max_prompt_chars: int = 100_000,
    ) -> None:
        self._readiness_probe = readiness_probe or (lambda: (True, []))
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_chat = on_chat
        self._read_events = read_events
        self._get_session_state = get_session_state
        self._get_recent_sessions = get_recent_sessions
        self._get_runtime_events = get_runtime_events
        self._get_mcp_inventory = get_mcp_inventory
        self._get_active_jobs = get_active_jobs
        self._get_failed_jobs = get_failed_jobs
        self._get_permission_audit = get_permission_audit
        self._get_subagent_reports = get_subagent_reports
        self._get_capabilities = get_capabilities
        self._get_config = get_config
        self._update_provider = update_provider
        self._validate_provider = validate_provider
        self._update_permissions = update_permissions
        self._update_memory = update_memory
        self._list_models = list_models
        self._get_setup_state = get_setup_state
        self._apply_setup = apply_setup
        self._list_schedules = list_schedules
        self._cancel_schedule = cancel_schedule
        self._toggle_schedule = toggle_schedule
        self._get_skills = get_skills
        self._get_memory = get_memory
        self._get_memory_recall = get_memory_recall
        self._get_og_graph = get_og_graph
        self._get_authoring_targets = get_authoring_targets
        self._get_authoring_target = get_authoring_target
        self._authoring_apply = authoring_apply
        self._authoring_revert = authoring_revert
        self._get_spend = get_spend
        self._set_capability_enabled = set_capability_enabled
        self._add_memory_fact = add_memory_fact
        self._delete_memory_fact = delete_memory_fact
        self._connections: dict[str, dict[str, Any]] = {}
        self._api_sessions: dict[str, str] = {}  # session_key -> session_id (facade sessions)
        self._connections_lock = threading.Lock()
        self._max_active_sessions = max(1, max_active_sessions)
        self._max_prompt_chars = max(1, max_prompt_chars)

    def _require_handler(self, handler: Any, operation: str) -> Any:
        if handler is None:
            raise GatewayUnavailableError(f"{operation} is not attached")
        return handler

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "hachi-gateway",
            "protocol_version": PROTOCOL_VERSION,
            "time": _now_iso(),
        }

    def readiness(self) -> dict[str, Any]:
        ready, blockers = self._readiness_probe()
        with self._connections_lock:
            active = len(self._connections)
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "blockers": blockers,
            "active_connections": active,
            "time": _now_iso(),
        }

    def create_session(
        self,
        *,
        client_id: str,
        surface: str,
        capabilities: list[str] | tuple[str, ...] | None = None,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> dict[str, Any]:
        normalized_client_id = client_id.strip()
        normalized_surface = surface.strip()
        capabilities_raw = list(capabilities or [])

        if not normalized_client_id:
            raise GatewayProtocolError("client_id is required")
        if not normalized_surface:
            raise GatewayProtocolError("surface is required")
        if protocol_version != PROTOCOL_VERSION:
            raise GatewayProtocolError(
                f"Unsupported protocol_version '{protocol_version}'. Expected '{PROTOCOL_VERSION}'"
            )
        if not all(isinstance(item, str) for item in capabilities_raw):
            raise GatewayProtocolError("capabilities must be a list of strings")

        req = ConnectRequest(
            client_id=normalized_client_id,
            surface=normalized_surface,
            protocol_version=protocol_version,
            capabilities=tuple(capabilities_raw),
        )

        with self._connections_lock:
            if len(self._connections) >= self._max_active_sessions:
                raise GatewayProtocolError(
                    f"Maximum active sessions reached ({self._max_active_sessions})."
                )
            session_id = str(uuid.uuid4())
            self._connections[session_id] = {
                "client_id": req.client_id,
                "surface": req.surface,
                "capabilities": list(req.capabilities),
                "connected_at": _now_iso(),
            }

        if self._on_connect is not None:
            try:
                self._on_connect(session_id, req)
            except Exception:
                # Boot (agent/MCP/DB init) failed — release the registered session
                # slot so repeated failed connects don't exhaust max_active_sessions.
                with self._connections_lock:
                    self._connections.pop(session_id, None)
                raise

        response = ConnectResponse(
            session_id=session_id,
            protocol_version=PROTOCOL_VERSION,
            accepted=True,
            server_capabilities=(
                "health",
                "readiness",
                "chat",
                "events",
                "session_state",
                "recent_sessions",
                "runtime_events",
                "mcp_inventory",
                "active_jobs",
                "failed_jobs",
                "permission_audit",
                "subagent_reports",
                "capabilities",
                "config",
                "models",
                "setup",
            ),
        )

        return {
            "accepted": response.accepted,
            "session_id": response.session_id,
            "protocol_version": response.protocol_version,
            "server_capabilities": list(response.server_capabilities),
        }

    def send_message(self, *, session_id: str, prompt: str) -> dict[str, Any]:
        if self._on_chat is None:
            raise GatewayUnavailableError("chat runtime is not attached")

        normalized_session_id = session_id.strip()
        normalized_prompt = prompt.strip()
        if not normalized_session_id:
            raise GatewayProtocolError("session_id is required")
        with self._connections_lock:
            if normalized_session_id not in self._connections:
                raise GatewayProtocolError("Unknown session_id")
        if not normalized_prompt:
            raise GatewayProtocolError("prompt is required")
        if len(prompt) > self._max_prompt_chars:
            raise GatewayProtocolError(
                f"prompt exceeds max length of {self._max_prompt_chars} characters"
            )

        req = ChatRequest(session_id=normalized_session_id, prompt=normalized_prompt)
        result = self._on_chat(req.session_id, req.prompt)
        response = ChatResponse(
            session_id=req.session_id,
            response=result,
        )

        return {
            "session_id": response.session_id,
            "response": response.response,
        }

    def get_or_create_session(self, session_key: str = "__default__") -> str:
        """Return the session_id for ``session_key``, creating it if necessary.

        Extracted from ``complete_chat`` so the streaming path can obtain the
        session_id before launching a background turn, enabling event polling
        while the turn runs.
        """
        with self._connections_lock:
            session_id = self._api_sessions.get(session_key)
            known = bool(session_id and session_id in self._connections)
        if not known:
            created = self.create_session(
                client_id=f"api:{session_key}",
                surface="api",
                protocol_version=PROTOCOL_VERSION,
                capabilities=[],
            )
            session_id = created["session_id"]
            with self._connections_lock:
                self._api_sessions[session_key] = session_id
        assert session_id is not None, "session_id must be set after get_or_create_session"
        return session_id

    def complete_chat(self, prompt: str, *, session_key: str = "__default__") -> dict[str, Any]:
        """Run a prompt through an auto-managed session keyed by ``session_key``.

        Powers stateless-looking facades (OpenAI-compatible endpoint, channel
        webhooks) that don't manage sessions themselves: the first call for a key
        creates a session (booting an agent via on_connect), subsequent calls
        reuse it so the conversation keeps continuity.
        """
        session_id = self.get_or_create_session(session_key)
        return self.send_message(session_id=session_id, prompt=prompt)

    def close_session(self, session_id: str) -> dict[str, Any]:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            raise GatewayProtocolError("session_id is required")
        with self._connections_lock:
            if normalized_session_id not in self._connections:
                raise GatewayProtocolError("Unknown session_id")
            self._connections.pop(normalized_session_id, None)
            # Prune any api-facade session-key that pointed at this session_id.
            stale_keys = [k for k, v in self._api_sessions.items() if v == normalized_session_id]
            for k in stale_keys:
                del self._api_sessions[k]
        if self._on_disconnect is not None:
            self._on_disconnect(normalized_session_id)

        return {"session_id": normalized_session_id, "closed": True}

    def read_session_events(
        self, session_id: str, after: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        if not session_id:
            raise GatewayProtocolError("session_id is required")
        with self._connections_lock:
            if session_id not in self._connections:
                raise GatewayProtocolError("Unknown session_id")
        if self._read_events is None:
            raise GatewayUnavailableError("event stream is not attached")

        safe_limit = max(1, min(limit, 500))
        safe_after = max(0, after)
        batch = self._read_events(session_id, safe_after, safe_limit)
        next_sequence = safe_after
        if batch:
            next_sequence = batch[-1].sequence

        return {
            "session_id": session_id,
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "time": event.time,
                }
                for event in batch
            ],
            "next_sequence": next_sequence,
        }

    def session_state(self, session_id: str) -> Any:
        if not session_id:
            raise GatewayProtocolError("session_id is required")
        with self._connections_lock:
            if session_id not in self._connections:
                raise GatewayProtocolError("Unknown session_id")
        handler = self._require_handler(self._get_session_state, "session state")
        return handler(session_id)

    def recent_sessions(self, limit: int = 10) -> Any:
        handler = self._require_handler(self._get_recent_sessions, "recent sessions")
        return handler(max(1, min(limit, 100)))

    def runtime_events(self, session_id: str | None, limit: int = 100) -> Any:
        handler = self._require_handler(self._get_runtime_events, "runtime events")
        normalized_session_id = session_id.strip() if isinstance(session_id, str) else ""
        return handler(normalized_session_id or None, max(1, min(limit, 500)))

    def mcp_inventory(self) -> Any:
        handler = self._require_handler(self._get_mcp_inventory, "MCP inventory")
        return handler()

    def active_jobs(self) -> Any:
        handler = self._require_handler(self._get_active_jobs, "active jobs")
        return handler()

    def failed_jobs(self, limit: int = 20) -> Any:
        handler = self._require_handler(self._get_failed_jobs, "failed jobs")
        return handler(max(1, min(limit, 200)))

    def permission_audit(self, limit: int = 50) -> Any:
        handler = self._require_handler(self._get_permission_audit, "permission audit")
        return handler(max(1, min(limit, 500)))

    def subagent_reports(self, query: str = "recent delegation", top_k: int = 10) -> Any:
        handler = self._require_handler(self._get_subagent_reports, "subagent reports")
        query_text = query.strip() if query else "recent delegation"
        return handler(query_text, max(1, min(top_k, 100)))

    def capabilities(self) -> Any:
        handler = self._require_handler(self._get_capabilities, "capabilities")
        return handler()

    def get_config(self) -> Any:
        handler = self._require_handler(self._get_config, "config")
        return handler()

    def update_provider(self, payload: dict[str, Any]) -> Any:
        handler = self._require_handler(self._update_provider, "provider update")
        return handler(payload)

    def validate_provider(self, payload: dict[str, Any]) -> Any:
        handler = self._require_handler(self._validate_provider, "provider validation")
        return handler(payload)

    def update_permissions(self, payload: dict[str, Any]) -> Any:
        handler = self._require_handler(self._update_permissions, "permissions update")
        return handler(payload)

    def update_memory(self, payload: dict[str, Any]) -> Any:
        handler = self._require_handler(self._update_memory, "memory update")
        return handler(payload)

    def models(self) -> Any:
        handler = self._require_handler(self._list_models, "model listing")
        return handler()

    def setup_state(self) -> Any:
        handler = self._require_handler(self._get_setup_state, "setup state")
        return handler()

    def apply_setup(self, payload: dict[str, Any]) -> Any:
        handler = self._require_handler(self._apply_setup, "setup apply")
        return handler(payload)

    def list_schedules(self) -> Any:
        if self._list_schedules is None:
            return []
        return self._list_schedules()

    def cancel_schedule(self, schedule_id: str) -> Any:
        if self._cancel_schedule is None:
            return {"ok": False, "detail": "schedules not available"}
        return self._cancel_schedule(schedule_id)

    def toggle_schedule(self, schedule_id: str, enabled: bool) -> Any:
        if self._toggle_schedule is None:
            return {"ok": False, "detail": "schedules not available"}
        return self._toggle_schedule(schedule_id, enabled)

    # ==========================================
    # Web endpoint handlers — added for web UI (#1–#6)
    # ==========================================

    def skills(self) -> Any:
        """Return the .og skill inventory list (GET /skills)."""
        if self._get_skills is None:
            return []
        return self._get_skills()

    def memory(self) -> Any:
        """Return grouped memory facts, blocked items, and proposed changes (GET /memory)."""
        handler = self._require_handler(self._get_memory, "memory facts")
        return handler()

    def memory_recall(self, query: str, top_k: int = 8) -> Any:
        """Return semantic memory recall results (GET /memory/recall)."""
        handler = self._require_handler(self._get_memory_recall, "memory recall")
        return handler(query, max(1, min(top_k, 50)))

    def og_graph(self) -> Any:
        """Return the ObjectGraph node graph (GET /og/graph)."""
        if self._get_og_graph is None:
            return {"nodes": []}
        return self._get_og_graph()

    def authoring_targets(self) -> Any:
        """Return the list of editable authoring targets (GET /authoring/targets)."""
        handler = self._require_handler(self._get_authoring_targets, "authoring targets")
        return handler()

    def authoring_target(self, name: str) -> Any:
        """Return the source of a single authoring target (GET /authoring/targets/{name})."""
        handler = self._require_handler(self._get_authoring_target, "authoring target")
        return handler(name)

    def authoring_apply(self, name: str) -> Any:
        """Apply (no-op stub) to an authoring target (POST /authoring/targets/{name}/apply)."""
        handler = self._require_handler(self._authoring_apply, "authoring apply")
        return handler(name)

    def authoring_revert(self, name: str) -> Any:
        """Revert (no-op stub) an authoring target (POST /authoring/targets/{name}/revert)."""
        handler = self._require_handler(self._authoring_revert, "authoring revert")
        return handler(name)

    def spend(self, session_id: str | None = None) -> Any:
        """Return spend analytics for the current or specified session (GET /spend)."""
        if self._get_spend is None:
            return {"turns": [], "tidies": [], "models": []}
        return self._get_spend(session_id)

    def set_capability_enabled(self, cap_id: str, enabled: bool) -> Any:
        """Toggle a capability on/off (PUT /capabilities/{id}/enabled)."""
        handler = self._require_handler(self._set_capability_enabled, "capability toggle")
        return handler(cap_id, bool(enabled))

    def add_memory_fact(self, group: str, text: str) -> Any:
        """Add a fact to a vault collection (POST /memory/facts)."""
        if self._add_memory_fact is None:
            return {"id": "", "error": "memory vault not available"}
        return self._add_memory_fact(group, text)

    def delete_memory_fact(self, fact_id: str) -> Any:
        """Delete a fact from the vault (DELETE /memory/facts/{fact_id})."""
        if self._delete_memory_fact is None:
            return {"ok": False, "error": "memory vault not available"}
        return self._delete_memory_fact(fact_id)

    # Backward-compatible aliases for previous envelope methods.
    def connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = payload.get("payload") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            raise GatewayProtocolError("payload must be an object")
        request = self.create_session(
            client_id=str(body.get("client_id") or ""),
            surface=str(body.get("surface") or ""),
            capabilities=body.get("capabilities")
            if isinstance(body.get("capabilities"), list)
            else [],
            protocol_version=str(body.get("protocol_version") or ""),
        )
        return {
            "type": "connect_ack",
            "request_id": str(payload.get("request_id") or ""),
            "payload": request,
        }

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = payload.get("payload") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            raise GatewayProtocolError("payload must be an object")
        response = self.send_message(
            session_id=str(body.get("session_id") or ""),
            prompt=str(body.get("prompt") or ""),
        )
        return {
            "type": "chat.response",
            "request_id": str(payload.get("request_id") or ""),
            "payload": response,
        }

    def events(self, session_id: str, after: int = 0, limit: int = 100) -> dict[str, Any]:
        payload = self.read_session_events(session_id=session_id, after=after, limit=limit)
        return {
            "type": "events.batch",
            "payload": payload,
        }


class GatewayHTTPServer:
    """Uvicorn wrapper exposing FastAPI gateway endpoints with start/stop semantics."""

    def __init__(
        self,
        control_plane: GatewayControlPlane,
        host: str = "127.0.0.1",
        port: int = 0,
        auth_token: str | None = None,
        frontend_dist: Path | None = None,
    ) -> None:
        self._control_plane = control_plane
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._frontend_dist = frontend_dist
        self._bound_port: int = 0
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._startup_error: Exception | None = None

    @property
    def bound_port(self) -> int:
        return self._bound_port

    def _detect_bound_port(self) -> int:
        server = self._server
        if server is None:
            return 0

        uvicorn_servers = getattr(server, "servers", []) or []
        for uvicorn_server in uvicorn_servers:
            sockets = getattr(uvicorn_server, "sockets", []) or []
            for socket in sockets:
                sockname = socket.getsockname()
                if isinstance(sockname, tuple) and len(sockname) >= 2:
                    return int(sockname[1])
        return 0

    def _serve(self) -> None:
        from hachi.gateway.fastapi_app import create_gateway_app

        try:
            app = create_gateway_app(
                self._control_plane,
                auth_token=self._auth_token,
                bind_host=self._host,
                frontend_dist=self._frontend_dist,
            )
            config = uvicorn.Config(
                app,
                host=self._host,
                port=self._port,
                log_level="warning",
                lifespan="on",
            )
            server = uvicorn.Server(config)
            self._server = server
            server.run()
        except Exception as exc:  # pragma: no cover
            self._startup_error = exc

    def start(self) -> int:
        if self._thread and self._thread.is_alive():
            return self.bound_port

        self._startup_error = None
        self._bound_port = 0
        self._thread = threading.Thread(
            target=self._serve,
            name="gateway-http",
            daemon=True,
        )
        self._thread.start()

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._startup_error is not None:
                raise OSError(
                    f"Gateway server failed to start: {self._startup_error}"
                ) from self._startup_error
            if self._server is not None and self._server.started:
                self._bound_port = self._detect_bound_port() or self._port
                if self._bound_port:
                    return self._bound_port
            if self._thread and not self._thread.is_alive():
                break
            time.sleep(0.01)

        if self._startup_error is not None:
            raise OSError(
                f"Gateway server failed to start: {self._startup_error}"
            ) from self._startup_error
        raise OSError("Gateway server failed to start within timeout")

    def stop(self) -> None:
        server = self._server
        if server is None:
            return

        server.should_exit = True
        server.force_exit = True

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        self._server = None
        self._bound_port = 0
