"""``/authoring/targets/*`` -- LLM-assisted .og / config authoring (T-47)."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import (
    _ERROR_RESPONSES,
    AuthoringGenerateRequest,
    AuthoringSourceSaveRequest,
)

router = APIRouter(
    prefix="/authoring",
    tags=["authoring"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("/targets")
async def get_authoring_targets(cp: ControlPlaneDep) -> Any:
    """List editable .og and config files in the workspace."""
    return cp.authoring_targets()


@router.get("/targets/{name}")
async def get_authoring_target(name: str, cp: ControlPlaneDep) -> Any:
    """Return the source text for a single editable target."""
    return cp.authoring_target(name)


@router.post("/targets/{name}/generate")
async def post_authoring_generate(
    name: str, payload: AuthoringGenerateRequest, cp: ControlPlaneDep
) -> Any:
    """Generate a proposed patch for the named target via the LLM (G1-T2 / CRIT-002).

    Runs the blocking LLM call off the event loop via anyio.to_thread.run_sync.
    Returns a real unified diff (old vs proposed) and the proposed content.
    Pending content is stored server-side so /apply can write it without
    the client having to re-send the full body.

    Body: ``{"prompt": "describe the change you want"}``
    Response: ``{"diff": [["" | "add" | "del", "<line>"], ...]}``
    """
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")
    result = await anyio.to_thread.run_sync(lambda: cp.authoring_generate(name, prompt))
    return result


@router.post("/targets/{name}/apply")
async def post_authoring_apply(name: str, cp: ControlPlaneDep) -> Any:
    """Apply the pending patch to the target and trigger a skill-engine re-index."""
    return await anyio.to_thread.run_sync(lambda: cp.authoring_apply(name))


@router.post("/targets/{name}/revert")
async def post_authoring_revert(name: str, cp: ControlPlaneDep) -> Any:
    """Revert the most recently applied patch for the target."""
    return await anyio.to_thread.run_sync(lambda: cp.authoring_revert(name))


# CONTRACT-AUTHORING-SAVE — POST /authoring/targets/{name}/source
@router.post("/targets/{name}/source")
async def post_authoring_save_source(
    name: str, payload: AuthoringSourceSaveRequest, cp: ControlPlaneDep
) -> Any:
    """Persist manually edited .og content for the named target.

    Validates the target name (path-traversal guard), parses the content as a
    valid ObjectGraph for .og files (returns 4xx on parse failure without overwriting),
    and writes atomically.

    Body: ``{"content": "<full .og text>"}``
    Response: ``{"ok": true, "name": "<target-name>"}``
    """
    content = payload.content
    if not content.strip():
        raise HTTPException(status_code=422, detail="content cannot be empty")
    return await anyio.to_thread.run_sync(lambda: cp.authoring_save_source(name, content))
