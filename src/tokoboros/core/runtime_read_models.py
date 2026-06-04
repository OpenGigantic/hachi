"""Immutable TUI-facing read models derived from runtime events."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from tokoboros.core.runtime_events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class SessionStateSnapshot:
    session_id: str
    active_skill: str | None
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
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCPInventorySnapshot:
    server_count: int
    total_tool_count: int
    servers: tuple[MCPServerSnapshot, ...]


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    kind: str
    label: str
    status: str


@dataclass(frozen=True, slots=True)
class JobFailureSnapshot:
    job_id: str
    kind: str
    label: str
    attempt: int
    error: str
    duration_seconds: float
    timestamp: str


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
    payload: dict[str, Any]
    timestamp: str


class RuntimeReadModelStore:
    """Maintains read-only state projections for TUI queries."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boot_ready = False
        self._jobs: dict[str, JobSnapshot] = {}
        self._job_failures: list[JobFailureSnapshot] = []
        self._permission_audit: list[PermissionAuditSnapshot] = []
        self._subagent_reports: list[SubAgentReportSnapshot] = []
        self._events: list[RuntimeEventSnapshot] = []

    def append_event(self, event: RuntimeEvent) -> None:
        with self._lock:
            self._events.append(
                RuntimeEventSnapshot(event.name, dict(event.payload), event.timestamp)
            )
            if len(self._events) > 1000:
                self._events = self._events[-1000:]

            if event.name == "boot.complete":
                self._boot_ready = True

            if event.name == "job.started":
                job_id = str(event.payload.get("job_id", "job:unknown"))
                self._jobs[job_id] = JobSnapshot(
                    job_id=job_id,
                    kind=str(event.payload.get("kind", "generic")),
                    label=str(event.payload.get("label") or event.payload.get("job_name", "Job")),
                    status="running",
                )

            if event.name == "job.completed":
                job_id = str(event.payload.get("job_id", "job:unknown"))
                current = self._jobs.get(job_id)
                self._jobs[job_id] = JobSnapshot(
                    job_id=job_id,
                    kind=current.kind if current else str(event.payload.get("kind", "generic")),
                    label=current.label
                    if current and current.label != "Job"
                    else str(event.payload.get("label") or event.payload.get("job_name", "Job")),
                    status=str(event.payload.get("status", "completed")),
                )

            if event.name == "job.failed":
                job_id = str(event.payload.get("job_id", "job:unknown"))
                current = self._jobs.get(job_id)
                kind = current.kind if current else str(event.payload.get("kind", "generic"))
                label = (
                    current.label
                    if current and current.label != "Job"
                    else str(event.payload.get("label") or event.payload.get("job_name", "Job"))
                )
                self._jobs[job_id] = JobSnapshot(
                    job_id=job_id,
                    kind=kind,
                    label=label,
                    status=str(event.payload.get("status", "failed")),
                )
                self._job_failures.append(
                    JobFailureSnapshot(
                        job_id=job_id,
                        kind=kind,
                        label=label,
                        attempt=int(event.payload.get("attempt", 0)),
                        error=str(event.payload.get("error", "")),
                        duration_seconds=float(event.payload.get("duration_seconds") or 0.0),
                        timestamp=event.timestamp,
                    )
                )

            if event.name == "permission.requested":
                self._permission_audit.append(
                    PermissionAuditSnapshot(
                        operation=str(event.payload.get("operation", "unknown")),
                        tool=str(event.payload.get("tool", "unknown")),
                        target=str(event.payload.get("target", "")),
                        decision="requested",
                        reason="Pending decision",
                    )
                )

            if event.name == "permission.decided":
                self._permission_audit.append(
                    PermissionAuditSnapshot(
                        operation=str(event.payload.get("operation", "unknown")),
                        tool=str(event.payload.get("tool", "unknown")),
                        target=str(event.payload.get("target", "")),
                        decision=str(event.payload.get("decision", "unknown")),
                        reason=str(event.payload.get("reason", "")),
                    )
                )

            if event.name == "subagent.completed":
                self._subagent_reports.append(
                    SubAgentReportSnapshot(
                        role=str(event.payload.get("role", "unknown")),
                        status=str(event.payload.get("status", "unknown")),
                        duration_seconds=float(event.payload.get("duration") or 0.0),
                        iterations=int(event.payload.get("iterations") or 0),
                        summary=str(event.payload.get("summary") or ""),
                    )
                )

            if len(self._permission_audit) > 500:
                self._permission_audit = self._permission_audit[-500:]
            if len(self._subagent_reports) > 300:
                self._subagent_reports = self._subagent_reports[-300:]

    def boot_ready(self) -> bool:
        with self._lock:
            return self._boot_ready

    def active_jobs(self) -> tuple[JobSnapshot, ...]:
        with self._lock:
            return tuple(job for job in self._jobs.values() if job.status == "running")

    def permission_audit(self, limit: int = 50) -> tuple[PermissionAuditSnapshot, ...]:
        with self._lock:
            return tuple(self._permission_audit[-limit:])

    def subagent_reports(self, limit: int = 20) -> tuple[SubAgentReportSnapshot, ...]:
        with self._lock:
            return tuple(self._subagent_reports[-limit:])

    def failed_jobs(self, limit: int = 20) -> tuple[JobFailureSnapshot, ...]:
        with self._lock:
            return tuple(self._job_failures[-limit:])

    def runtime_events(self, limit: int = 100) -> tuple[RuntimeEventSnapshot, ...]:
        with self._lock:
            return tuple(self._events[-limit:])
