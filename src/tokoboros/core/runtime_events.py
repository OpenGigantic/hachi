"""Runtime event taxonomy and adaptation helpers for Tokoboros.

This module defines a stable, TUI-facing event contract. Legacy internal event
names are adapted into this taxonomy so existing runtime code can evolve without
breaking UI surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
from typing import Any, Dict, List, Optional


# Boot lifecycle
BOOT_PROGRESS = "boot.progress"
BOOT_COMPLETE = "boot.complete"
BOOT_FAILED = "boot.failed"

# Session lifecycle
SESSION_CHAT_STARTED = "session.chat.started"
SESSION_CHAT_COMPLETED = "session.chat.completed"
SESSION_PERSISTED = "session.persisted"
SESSION_SHUTDOWN = "session.shutdown"

# Model output lifecycle
THINKING = "thinking.step"
TEXT_STREAM = "text.stream.delta"
COMPACTION = "memory.compaction"

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

# MCP lifecycle
MCP_SERVER_STARTING = "mcp.server.starting"
MCP_SERVER_READY = "mcp.server.ready"
MCP_SERVER_FAILED = "mcp.server.failed"
MCP_SHUTDOWN = "mcp.shutdown"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Immutable runtime event record."""

    name: str
    payload: Dict[str, Any]
    timestamp: str


@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    """A normalized event plus optional legacy aliases for compatibility."""

    primary: RuntimeEvent
    legacy_aliases: tuple[str, ...] = ()


def now_iso() -> str:
    """UTC timestamp in ISO-8601 format."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _event(name: str, payload: Optional[Dict[str, Any]] = None) -> RuntimeEvent:
    return RuntimeEvent(name=name, payload=payload or {}, timestamp=now_iso())


def adapt_event(event_type: str, data: Optional[Dict[str, Any]] = None) -> List[RuntimeEventEnvelope]:
    """Normalize legacy runtime events to the stable event taxonomy.

    Unknown events are preserved under their original names.
    """
    payload = data or {}

    if event_type == "thinking":
        return [RuntimeEventEnvelope(_event(THINKING, {
            "iteration": payload.get("iteration"),
        }), legacy_aliases=("thinking",))]

    if event_type == "text_chunk":
        return [RuntimeEventEnvelope(_event(TEXT_STREAM, {
            "text": payload.get("text", ""),
        }), legacy_aliases=("text_chunk",))]

    if event_type == "compaction":
        return [RuntimeEventEnvelope(_event(COMPACTION, {
            "old_skill": payload.get("old_skill"),
            "new_skill": payload.get("new_skill"),
        }), legacy_aliases=("compaction",))]

    if event_type == "tool_call":
        tool_name = payload.get("name", "unknown")
        tool_input = payload.get("input", {})
        return [
            RuntimeEventEnvelope(_event(TOOL_CALL_STARTED, {
                "name": tool_name,
                "input": tool_input,
            }), legacy_aliases=("tool_call",))
        ]

    if event_type == "tool_result":
        tool_name = payload.get("name", "unknown")
        raw_result = str(payload.get("result", ""))
        return [RuntimeEventEnvelope(_event(TOOL_CALL_COMPLETED, {
            "name": tool_name,
            "result_preview": raw_result[:500],
        }), legacy_aliases=("tool_result",))]

    if event_type == "delegation_start":
        role = payload.get("role", "unknown")
        task = payload.get("task", "")
        return [
            RuntimeEventEnvelope(_event(SUBAGENT_STARTED, {
                "role": role,
                "task": task,
            }), legacy_aliases=("delegation_start",)),
            RuntimeEventEnvelope(_event(JOB_STARTED, {
                "job_id": f"delegate:{role}:{task[:24]}",
                "kind": "subagent",
                "label": f"Delegated to {role}",
            })),
        ]

    if event_type == "delegation_complete":
        role = payload.get("role", "unknown")
        status = payload.get("status", "unknown")
        return [
            RuntimeEventEnvelope(_event(SUBAGENT_COMPLETED, {
                "role": role,
                "status": status,
                "duration": payload.get("duration"),
                "iterations": payload.get("iterations"),
            }), legacy_aliases=("delegation_complete",)),
            RuntimeEventEnvelope(_event(JOB_COMPLETED, {
                "job_id": f"delegate:{role}",
                "kind": "subagent",
                "status": status,
            })),
        ]

    return [RuntimeEventEnvelope(_event(event_type, payload))]
