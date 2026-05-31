"""Immutable TUI-facing read models derived from runtime events."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Dict, List, Optional, Tuple

from tokoboros.core.runtime_events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class SessionStateSnapshot:
    session_id: str
    active_skill: Optional[str]
    last_user_query: str
    model_name: str
    boot_ready: bool
    pending_job_count: int


@dataclass(frozen=True, slots=True)
class TaskLedgerItemSnapshot:
    skill_name: str
    user_query: str
    summary: str


@dataclass(frozen=True, slots=True)
class MemoryRecallSnapshot:
    fact_id: str
    content: str
    category: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class PermissionAuditSnapshot:
    operation: str
    tool: str
    target: str
    decision: str
    reason: str


@dataclass(frozen=True, slots=True)
class MCPServerSnapshot:
    name: str
    status: str
    tool_count: int
    tools: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCPInventorySnapshot:
    server_count: int
    total_tool_count: int
    servers: Tuple[MCPServerSnapshot, ...]


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    kind: str
    label: str
    status: str


@dataclass(frozen=True, slots=True)
class SubAgentReportSnapshot:
    role: str
    status: str
    duration_seconds: float
    iterations: int
    summary: str


@dataclass(frozen=True, slots=True)
class RuntimeEventSnapshot:
    name: str
    payload: Dict[str, Any]
    timestamp: str


class RuntimeReadModelStore:
    """Maintains read-only state projections for TUI queries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boot_ready = False
        self._jobs: Dict[str, JobSnapshot] = {}
        self._permission_audit: List[PermissionAuditSnapshot] = []
        self._subagent_reports: List[SubAgentReportSnapshot] = []
        self._events: List[RuntimeEventSnapshot] = []

    def append_event(self, event: RuntimeEvent) -> None:
        with self._lock:
            self._events.append(RuntimeEventSnapshot(event.name, dict(event.payload), event.timestamp))
            if len(self._events) > 1000:
                self._events = self._events[-1000:]

            if event.name == "boot.complete":
                self._boot_ready = True

            if event.name == "job.started":
                job_id = str(event.payload.get("job_id", "job:unknown"))
                self._jobs[job_id] = JobSnapshot(
                    job_id=job_id,
                    kind=str(event.payload.get("kind", "generic")),
                    label=str(event.payload.get("label", "Job")),
                    status="running",
                )

            if event.name == "job.completed":
                job_id = str(event.payload.get("job_id", "job:unknown"))
                current = self._jobs.get(job_id)
                self._jobs[job_id] = JobSnapshot(
                    job_id=job_id,
                    kind=current.kind if current else str(event.payload.get("kind", "generic")),
                    label=current.label if current else str(event.payload.get("label", "Job")),
                    status=str(event.payload.get("status", "completed")),
                )

            if event.name == "permission.requested":
                self._permission_audit.append(PermissionAuditSnapshot(
                    operation=str(event.payload.get("operation", "unknown")),
                    tool=str(event.payload.get("tool", "unknown")),
                    target=str(event.payload.get("target", "")),
                    decision="requested",
                    reason="Pending decision",
                ))

            if event.name == "permission.decided":
                self._permission_audit.append(PermissionAuditSnapshot(
                    operation=str(event.payload.get("operation", "unknown")),
                    tool=str(event.payload.get("tool", "unknown")),
                    target=str(event.payload.get("target", "")),
                    decision=str(event.payload.get("decision", "unknown")),
                    reason=str(event.payload.get("reason", "")),
                ))

            if event.name == "subagent.completed":
                self._subagent_reports.append(SubAgentReportSnapshot(
                    role=str(event.payload.get("role", "unknown")),
                    status=str(event.payload.get("status", "unknown")),
                    duration_seconds=float(event.payload.get("duration") or 0.0),
                    iterations=int(event.payload.get("iterations") or 0),
                    summary=str(event.payload.get("summary") or ""),
                ))

            if len(self._permission_audit) > 500:
                self._permission_audit = self._permission_audit[-500:]
            if len(self._subagent_reports) > 300:
                self._subagent_reports = self._subagent_reports[-300:]

    def boot_ready(self) -> bool:
        with self._lock:
            return self._boot_ready

    def active_jobs(self) -> Tuple[JobSnapshot, ...]:
        with self._lock:
            return tuple(job for job in self._jobs.values() if job.status == "running")

    def permission_audit(self, limit: int = 50) -> Tuple[PermissionAuditSnapshot, ...]:
        with self._lock:
            return tuple(self._permission_audit[-limit:])

    def subagent_reports(self, limit: int = 20) -> Tuple[SubAgentReportSnapshot, ...]:
        with self._lock:
            return tuple(self._subagent_reports[-limit:])

    def runtime_events(self, limit: int = 100) -> Tuple[RuntimeEventSnapshot, ...]:
        with self._lock:
            return tuple(self._events[-limit:])
