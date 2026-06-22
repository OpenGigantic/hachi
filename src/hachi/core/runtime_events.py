"""Runtime event taxonomy and adaptation helpers for Hachi.

This module defines a stable, TUI-facing event contract. Legacy internal event
names are adapted into this taxonomy so existing runtime code can evolve without
breaking UI surfaces.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from hachi.core.secret_scanner import redact_secrets, redact_structure

# Boot lifecycle
BOOT_PROGRESS = "boot.progress"
BOOT_COMPLETE = "boot.complete"
BOOT_FAILED = "boot.failed"

# Session lifecycle
SESSION_CHAT_STARTED = "session.chat.started"
SESSION_CHAT_COMPLETED = "session.chat.completed"
SESSION_PERSISTED = "session.persisted"
SESSION_RESUMED = "session.resumed"
SESSION_SHUTDOWN = "session.shutdown"
SESSION_ITERATION_LIMIT = "session.iteration.limit"
SESSION_BUSY = "session.busy"
SESSION_CANCELLED = "session.turn.cancelled"

# Model output lifecycle
THINKING = "thinking.step"
TEXT_STREAM = "text.stream.delta"
COMPACTION = "memory.compaction"
USAGE_UPDATED = "usage.updated"

# Tool lifecycle
TOOL_CALL_STARTED = "tool.call.started"
TOOL_CALL_COMPLETED = "tool.call.completed"

# Permission lifecycle
PERMISSION_REQUESTED = "permission.requested"
PERMISSION_DECIDED = "permission.decided"

# Sub-agent and job lifecycle
SUBAGENT_STARTED = "subagent.started"
SUBAGENT_COMPLETED = "subagent.completed"
JOB_STARTED = "job.started"
JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"

# MCP lifecycle
MCP_SERVER_STARTING = "mcp.server.starting"
MCP_SERVER_READY = "mcp.server.ready"
MCP_SERVER_FAILED = "mcp.server.failed"
MCP_SHUTDOWN = "mcp.shutdown"

# File platform events
FILE_UPLOADED = "file.uploaded"
FILE_EXTRACTED = "file.extracted"

# Search events
SEARCH_COMPLETED = "search.completed"

# Messaging events
MESSAGE_RECEIVED = "message.received"
WEBHOOK_RECEIVED = "webhook.received"

# Durable run lifecycle
RUN_SUSPENDED = "run.suspended"
RUN_RESUMED = "run.resumed"

# Provider / routing events
PROVIDER_FAILOVER = "provider.failover"
MODEL_ROUTED = "model.routed"

# Promoted from raw literals in the codebase (closes taxonomy gap)
JOB_RETRY = "job.retry"
BUDGET_EXCEEDED = "budget.exceeded"
LLM_RETRY = "llm.retry"
SESSION_TIME_LIMIT = "session.time.limit"

# Max chars of a tool result carried in a ``tool.call.completed`` preview.
# Bounded so the event stream stays light, but large enough that expanding a
# tool card in the TUI shows the substance of the result, not a stub.
TOOL_RESULT_PREVIEW_MAX = 2000


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Immutable runtime event record."""

    name: str
    payload: dict[str, Any]
    timestamp: str


@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    """A normalized event plus optional legacy aliases for compatibility."""

    primary: RuntimeEvent
    legacy_aliases: tuple[str, ...] = ()


def now_iso() -> str:
    """UTC timestamp in ISO-8601 format."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def _event(name: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
    base = dict(payload) if payload else {}
    # Stamp trace_id / session_id from the active TraceContext (if any).
    # Import is deferred to avoid a circular-import at module load time.
    try:
        from hachi.core.obs.trace import current as _current_trace

        tc = _current_trace()
        if tc is not None:
            base.setdefault("trace_id", tc.trace_id)
            base.setdefault("session_id", tc.session_id)
    except Exception:  # pragma: no cover — obs module not yet available
        pass
    return RuntimeEvent(name=name, payload=base, timestamp=now_iso())


def adapt_event(event_type: str, data: dict[str, Any] | None = None) -> list[RuntimeEventEnvelope]:
    """Normalize legacy runtime events to the stable event taxonomy.

    Unknown events are preserved under their original names.
    """
    payload = data or {}

    if event_type == "thinking":
        return [
            RuntimeEventEnvelope(
                _event(
                    THINKING,
                    {
                        "iteration": payload.get("iteration"),
                    },
                ),
                legacy_aliases=("thinking",),
            )
        ]

    if event_type in ("text_chunk", "text_delta"):
        return [
            RuntimeEventEnvelope(
                _event(
                    TEXT_STREAM,
                    {
                        "text": payload.get("text", ""),
                    },
                ),
                legacy_aliases=("text_chunk", "text_delta"),
            )
        ]

    if event_type == "session_cancelled":
        return [
            RuntimeEventEnvelope(
                _event(
                    SESSION_CANCELLED,
                    {
                        "partial_text": payload.get("partial_text", ""),
                    },
                ),
                legacy_aliases=("session_cancelled",),
            )
        ]

    if event_type == "usage":
        return [
            RuntimeEventEnvelope(
                _event(USAGE_UPDATED, dict(payload)),
                legacy_aliases=("usage",),
            )
        ]

    if event_type == "compaction":
        return [
            RuntimeEventEnvelope(
                _event(
                    COMPACTION,
                    {
                        "old_skill": payload.get("old_skill"),
                        "new_skill": payload.get("new_skill"),
                    },
                ),
                legacy_aliases=("compaction",),
            )
        ]

    if event_type == "tool_call":
        tool_name = payload.get("name", "unknown")
        raw_input = payload.get("input", {})
        # ADV-009: always scrub secrets from tool inputs before they enter the event
        # stream, TUI transcript, gateway SSE, or persisted SQLite.  This is NOT
        # subject to HACHI_ALLOW_OUTBOUND_SECRETS — that env var governs only
        # user-visible outbound messaging; internal event/transcript redaction is
        # unconditional.
        safe_input = redact_structure(raw_input)
        return [
            RuntimeEventEnvelope(
                _event(
                    TOOL_CALL_STARTED,
                    {
                        "name": tool_name,
                        "input": safe_input,
                    },
                ),
                legacy_aliases=("tool_call",),
            )
        ]

    if event_type == "tool_result":
        tool_name = payload.get("name", "unknown")
        raw_result = str(payload.get("result", ""))
        # ADV-009: redact BEFORE truncating so a secret near the start of a long
        # result is not preserved inside the preview window.
        safe_result, _ = redact_secrets(raw_result)
        return [
            RuntimeEventEnvelope(
                _event(
                    TOOL_CALL_COMPLETED,
                    {
                        "name": tool_name,
                        "result_preview": safe_result[:TOOL_RESULT_PREVIEW_MAX],
                    },
                ),
                legacy_aliases=("tool_result",),
            )
        ]

    if event_type == "delegation_start":
        role = payload.get("role", "unknown")
        task = payload.get("task", "")
        return [
            RuntimeEventEnvelope(
                _event(
                    SUBAGENT_STARTED,
                    {
                        "role": role,
                        "task": task,
                    },
                ),
                legacy_aliases=("delegation_start",),
            ),
            RuntimeEventEnvelope(
                _event(
                    JOB_STARTED,
                    {
                        "job_id": f"delegate:{role}:{task[:24]}",
                        "kind": "subagent",
                        "label": f"Delegated to {role}",
                    },
                )
            ),
        ]

    if event_type == "delegation_complete":
        role = payload.get("role", "unknown")
        status = payload.get("status", "unknown")
        task = payload.get("task", "")
        return [
            RuntimeEventEnvelope(
                _event(
                    SUBAGENT_COMPLETED,
                    {
                        "role": role,
                        "status": status,
                        "duration": payload.get("duration"),
                        "iterations": payload.get("iterations"),
                    },
                ),
                legacy_aliases=("delegation_complete",),
            ),
            RuntimeEventEnvelope(
                _event(
                    JOB_COMPLETED,
                    {
                        # Must reconstruct the exact JOB_STARTED job_id (which folds in
                        # the task slug) so the read model clears the matching "running"
                        # row instead of leaving an orphaned subagent job that never
                        # leaves the Active Jobs table. Parallel same-role delegations
                        # each get a distinct id via the task slug.
                        "job_id": f"delegate:{role}:{str(task)[:24]}",
                        "kind": "subagent",
                        "status": status,
                    },
                )
            ),
        ]

    return [RuntimeEventEnvelope(_event(event_type, payload))]
