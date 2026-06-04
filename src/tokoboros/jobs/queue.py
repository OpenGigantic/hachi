"""
Durable Job Queue — Replaces background daemon threads with visible work tracking

This module provides a job queue interface and a simple in-memory backend.
It replaces fire-and-forget background threads with:
    - Durable work items that persist across shutdown
    - Visible job state and failure events
    - Retry logic with exponential backoff
    - Operator observability (can query job status, failures)

The interface supports multiple backends (in-memory for MVP, SQLite for durable v1).
"""

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Job lifecycle states."""

    PENDING = "pending"  # Queued, waiting to execute
    RUNNING = "running"  # Currently executing
    COMPLETED = "completed"  # Successfully finished
    FAILED = "failed"  # Failed after retries exhausted
    CANCELLED = "cancelled"  # Explicitly cancelled


@dataclass(slots=True)
class JobResult:
    """Outcome of a job execution."""

    status: JobStatus
    output: Any | None = None
    error: str | None = None
    exception: str | None = None
    duration_seconds: float = 0.0


@dataclass(slots=True)
class Job:
    """A single unit of work in the queue."""

    job_id: str
    name: str
    status: JobStatus = JobStatus.PENDING

    # Work payload
    func: Callable | None = field(default=None, repr=False)
    args: tuple = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)

    # Retry logic
    attempt: int = 0
    max_retries: int = 3
    backoff_base_seconds: float = 2.0

    # Timing
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    scheduled_for: str | None = None

    # Results
    result: JobResult | None = None

    # Metadata (for audit/routing)
    session_id: str | None = None
    turn_id: str | None = None
    job_type: str = "background"  # "background", "prefetch", "sync", "persist", "delegate"

    def to_dict(self) -> dict[str, Any]:
        """Serialize job to dict (excluding the callable func)."""
        return {
            "job_id": self.job_id,
            "name": self.name,
            "status": self.status.value,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "scheduled_for": self.scheduled_for,
            "result": {
                "status": self.result.status.value,
                "output": str(self.result.output)[:500] if self.result.output else None,
                "error": self.result.error,
                "duration_seconds": self.result.duration_seconds,
            }
            if self.result
            else None,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "job_type": self.job_type,
        }


class JobQueue(ABC):
    """Abstract interface for a durable job queue backend."""

    @abstractmethod
    def enqueue(self, job: Job) -> str:
        """
        Add a job to the queue.

        Args:
            job: The job to enqueue

        Returns:
            The job ID
        """
        pass

    @abstractmethod
    def get_pending(self, limit: int = 10) -> list[Job]:
        """Fetch up to `limit` pending jobs."""
        pass

    @abstractmethod
    def get_job(self, job_id: str) -> Job | None:
        """Fetch a job by ID."""
        pass

    @abstractmethod
    def update_status(
        self, job_id: str, status: JobStatus, result: JobResult | None = None
    ) -> bool:
        """
        Update a job's status and optionally set its result.

        Returns:
            True if updated, False if job not found
        """
        pass

    @abstractmethod
    def get_recent_jobs(self, limit: int = 50, job_type: str | None = None) -> list[Job]:
        """Fetch recent jobs, optionally filtered by type."""
        pass

    @abstractmethod
    def get_failed_jobs(self, limit: int = 20) -> list[Job]:
        """Fetch failed jobs for debugging/recovery."""
        pass

    @abstractmethod
    def cleanup_completed(self, older_than_hours: int = 24) -> int:
        """Remove completed/failed jobs older than N hours. Returns count removed."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Graceful shutdown (flush pending work if durable backend)."""
        pass


class InMemoryJobQueue(JobQueue):
    """
    Simple in-memory job queue for MVP.

    Note: Jobs are lost on process restart. Durable SQLite backend
    comes in Phase 1 upgrade v1.0.
    """

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._pending_queue: list[str] = []  # job_ids in order
        self._lock = threading.RLock()

    def enqueue(self, job: Job) -> str:
        """Add job to queue."""
        with self._lock:
            if not job.job_id:
                job.job_id = str(uuid.uuid4())
            if not job.scheduled_for:
                job.scheduled_for = datetime.now(UTC).isoformat()
            self._jobs[job.job_id] = job
            self._pending_queue.append(job.job_id)
        logger.debug(f"Enqueued job {job.name} (id={job.job_id})")
        return job.job_id

    def get_pending(self, limit: int = 10) -> list[Job]:
        """Fetch pending jobs."""
        with self._lock:
            result = []
            seen = set()
            now = datetime.now(UTC)
            for job_id in self._pending_queue:
                if job_id in seen or job_id not in self._jobs:
                    continue
                job = self._jobs[job_id]
                if job.status != JobStatus.PENDING:
                    continue

                if job.scheduled_for:
                    try:
                        scheduled_dt = datetime.fromisoformat(job.scheduled_for)
                        if scheduled_dt > now:
                            continue
                    except Exception:
                        pass

                result.append(job)
                seen.add(job_id)
                if len(result) >= limit:
                    break
            return result

    def get_job(self, job_id: str) -> Job | None:
        """Fetch a job by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def update_status(
        self, job_id: str, status: JobStatus, result: JobResult | None = None
    ) -> bool:
        """Update job status."""
        with self._lock:
            if job_id not in self._jobs:
                return False

            job = self._jobs[job_id]
            if status == JobStatus.RUNNING and job.status != JobStatus.PENDING:
                return False

            job.status = status
            if result:
                job.result = result
            if status == JobStatus.RUNNING and not job.started_at:
                job.started_at = datetime.now(UTC).isoformat()

            if status == JobStatus.COMPLETED:
                job.completed_at = datetime.now(UTC).isoformat()
            elif status == JobStatus.FAILED:
                job.completed_at = datetime.now(UTC).isoformat()

            return True

    def get_recent_jobs(self, limit: int = 50, job_type: str | None = None) -> list[Job]:
        """Fetch recent jobs."""
        with self._lock:
            jobs = list(self._jobs.values())
            if job_type:
                jobs = [j for j in jobs if j.job_type == job_type]
            # Sort by creation time, most recent first
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    def get_failed_jobs(self, limit: int = 20) -> list[Job]:
        """Fetch failed jobs."""
        with self._lock:
            failed = [j for j in self._jobs.values() if j.status == JobStatus.FAILED]
            failed.sort(key=lambda j: j.completed_at or j.created_at, reverse=True)
            return failed[:limit]

    def cleanup_completed(self, older_than_hours: int = 24) -> int:
        """Remove old completed/failed jobs."""
        with self._lock:
            cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
            count = 0
            to_delete = []

            for job_id, job in self._jobs.items():
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                    if job.completed_at:
                        completed_dt = datetime.fromisoformat(job.completed_at)
                        if completed_dt < cutoff:
                            to_delete.append(job_id)
                            count += 1

            for job_id in to_delete:
                del self._jobs[job_id]
                self._pending_queue = [jid for jid in self._pending_queue if jid != job_id]

        logger.debug(f"Cleaned up {count} old jobs")
        return count

    def shutdown(self) -> None:
        """Shutdown (no-op for in-memory backend)."""
        with self._lock:
            total = len(self._jobs)
        logger.debug(f"InMemoryJobQueue shutdown: {total} jobs in memory")


class JobWorker:
    """
    Executes jobs from the queue with retry logic and event emission.

    Typically run as a background thread or async task.
    """

    def __init__(
        self,
        queue: JobQueue,
        on_job_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.queue = queue
        self.on_job_event = on_job_event or (lambda *_a, **_kw: None)
        self._running = False

    def execute_one(self) -> bool:
        """
        Fetch and execute one pending job.

        Returns:
            True if a job was executed, False if queue is empty.
        """
        jobs = self.queue.get_pending(limit=1)
        if not jobs:
            return False

        job = jobs[0]
        logger.debug(f"Executing job {job.name} (id={job.job_id})")

        claimed = self.queue.update_status(job.job_id, JobStatus.RUNNING)
        if not claimed:
            return False

        self.on_job_event(
            "job.started",
            {
                "job_id": job.job_id,
                "job_name": job.name,
                "job_type": job.job_type,
                "kind": job.job_type,
                "label": job.name,
            },
        )

        start_time = datetime.now(UTC)
        result = None

        try:
            if job.func:
                output = job.func(*job.args, **job.kwargs)
                duration = (datetime.now(UTC) - start_time).total_seconds()
                result = JobResult(
                    status=JobStatus.COMPLETED,
                    output=output,
                    duration_seconds=duration,
                )
                logger.debug(f"Job {job.name} completed in {duration:.2f}s")
                self.on_job_event(
                    "job.completed",
                    {
                        "job_id": job.job_id,
                        "job_name": job.name,
                        "duration_seconds": duration,
                        "kind": job.job_type,
                        "label": job.name,
                    },
                )
        except Exception as e:
            duration = (datetime.now(UTC) - start_time).total_seconds()
            job.attempt += 1

            if job.attempt < job.max_retries:
                # Retry with backoff
                backoff = job.backoff_base_seconds ** (job.attempt - 1)
                logger.warning(
                    f"Job {job.name} attempt {job.attempt} failed: {e}. "
                    f"Will retry in {backoff:.1f}s"
                )
                job.scheduled_for = (datetime.now(UTC) + timedelta(seconds=backoff)).isoformat()
                self.queue.update_status(job.job_id, JobStatus.PENDING)
                self.on_job_event(
                    "job.retry",
                    {
                        "job_id": job.job_id,
                        "job_name": job.name,
                        "kind": job.job_type,
                        "label": job.name,
                        "attempt": job.attempt,
                        "error": str(e),
                        "backoff_seconds": backoff,
                    },
                )
            else:
                # Exhausted retries
                result = JobResult(
                    status=JobStatus.FAILED,
                    error=str(e),
                    exception=f"{type(e).__name__}: {str(e)}",
                    duration_seconds=duration,
                )
                logger.error(f"Job {job.name} failed after {job.attempt} attempts: {e}")
                self.on_job_event(
                    "job.failed",
                    {
                        "job_id": job.job_id,
                        "job_name": job.name,
                        "kind": job.job_type,
                        "status": JobStatus.FAILED.value,
                        "label": job.name,
                        "attempt": job.attempt,
                        "error": str(e),
                        "duration_seconds": duration,
                    },
                )

        if result:
            self.queue.update_status(job.job_id, result.status, result=result)

        return True
