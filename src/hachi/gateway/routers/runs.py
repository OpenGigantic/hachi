"""``/runs/*`` -- durable run read models + lifecycle (Initiative D, T-47)."""

from __future__ import annotations

import logging
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("", summary="List recent durable runs")
async def get_runs(limit: int = Query(default=100, ge=1, le=500)) -> Any:
    """Return recent durable runs across all sessions, newest first."""
    from hachi.core.runtime.state import RunStateView
    from hachi.core.runtime.store import RunStateStore

    store = RunStateStore()
    runs = store.list_all(limit=limit)
    return {"runs": [RunStateView.from_run_state(r).model_dump() for r in runs]}


@router.get("/{run_id}", summary="Get a durable run by ID")
async def get_run(run_id: str) -> Any:
    """Return a single RunState by run_id.  404 if not found."""
    from hachi.core.runtime.state import RunStateView
    from hachi.core.runtime.store import RunStateStore

    store = RunStateStore()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return RunStateView.from_run_state(run).model_dump()


@router.post("/{run_id}/wake", summary="Wake a WAITING run")
async def post_run_wake(
    run_id: str,
    cp: ControlPlaneDep,
    body: dict[str, Any] | None = None,
) -> Any:
    """Wake a WAITING run by resolving its wait condition.

    The run transitions WAITING→RUNNING and the resumed turn is started in the
    background via the job queue (durable, survives restart).

    Returns ``{"status": "waking", "run_id": "..."}`` immediately; the actual
    resume happens asynchronously.  If the run is not WAITING, returns 409.
    """
    from hachi.core.runtime.state import RunStatus
    from hachi.core.runtime.store import RunStateStore
    from hachi.core.runtime.waits import get_wait_registry

    body = body or {}
    result_payload: dict[str, Any] = body.get("result", {}) if isinstance(body, dict) else {}
    source: str = body.get("source", "webhook") if isinstance(body, dict) else "webhook"

    store = RunStateStore()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    if run.status != RunStatus.WAITING:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id!r} is {run.status.value!r}, not 'waiting'",
        )

    # Resolve via WaitConditionRegistry (idempotent)
    registry = get_wait_registry()
    wake_key = run.wait.key if run.wait else run_id
    resolved_id = registry.resolve(wake_key, result_payload)

    if resolved_id is None and run.wait is not None:
        # Key not in registry (e.g., after restart) — fall through to direct dispatch
        pass

    # Dispatch resume_run as a durable job
    try:
        from hachi.jobs import Job, JobStatus

        job = Job(
            job_id="",
            name=f"resume_run.{run_id}",
            handler_name="resume_run",
            payload={"run_id": run_id, "result": result_payload, "source": source},
            status=JobStatus.PENDING,
        )
        cp.job_queue.enqueue(job)
    except Exception as exc:
        _log.warning("post_run_wake: failed to enqueue resume job for %r: %s", run_id, exc)
        # Fall back to inline attempt
        try:
            from hachi.core.runtime.resume import resume_run

            await anyio.to_thread.run_sync(lambda: resume_run(run_id, result_payload))
        except Exception as resume_exc:
            raise HTTPException(
                status_code=503,
                detail=f"Resume failed: {resume_exc}",
            ) from resume_exc

    return {"status": "waking", "run_id": run_id, "source": source}


@router.delete("/{run_id}", summary="Cancel a durable run")
async def delete_run(run_id: str) -> Any:
    """Cancel a durable run (sets status to CANCELLED and disarms its wait).

    Idempotent: cancelling an already-terminal run returns 200 with the
    current status.
    """
    from hachi.core.runtime.state import RunStatus
    from hachi.core.runtime.store import RunStateStore
    from hachi.core.runtime.waits import get_wait_registry
    from hachi.core.runtime_events import now_iso

    store = RunStateStore()
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    if run.status in terminal:
        return {
            "run_id": run_id,
            "status": run.status.value,
            "note": "already terminal",
        }

    get_wait_registry().disarm(run_id)
    run = run.model_copy(update={"status": RunStatus.CANCELLED, "updated_at": now_iso()})
    store.upsert(run)
    return {"run_id": run_id, "status": RunStatus.CANCELLED.value}
