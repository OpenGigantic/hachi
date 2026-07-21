"""Gateway route handlers, lifted out of the ``create_gateway_app`` closure (T-47).

Each submodule declares one module-scope ``APIRouter`` (or a small family of
them, e.g. the former ``runtime_ops`` split into 7 prefix-less routers -- see
``docs/audit/2026-07-19/tasks/T-47-lift-routers-out-of-closure.md`` Sec 3.3).
``ALL_ROUTERS`` is the assembly list ``create_gateway_app`` mounts via
``app.include_router(...)`` -- it is intentionally a plain module-level
constant (not a factory) so a router is importable and testable on its own,
without ever calling ``create_gateway_app``.
"""

from __future__ import annotations

from fastapi import APIRouter

from hachi.gateway.routers.approvals import router as approvals_router
from hachi.gateway.routers.authoring import router as authoring_router
from hachi.gateway.routers.channels import router as channels_router
from hachi.gateway.routers.config import router as config_router
from hachi.gateway.routers.files import router as files_router
from hachi.gateway.routers.integrations import router as integrations_router
from hachi.gateway.routers.integrations_callback import router as integrations_callback_router
from hachi.gateway.routers.interactions import router as interactions_router
from hachi.gateway.routers.jobs import router as jobs_router
from hachi.gateway.routers.mcp import router as mcp_router
from hachi.gateway.routers.memory import router as memory_router
from hachi.gateway.routers.og import router as og_router
from hachi.gateway.routers.openai_compat import router as openai_compat_router
from hachi.gateway.routers.permissions import router as permissions_router
from hachi.gateway.routers.runs import router as runs_router
from hachi.gateway.routers.runtime_events import router as runtime_events_router
from hachi.gateway.routers.schedules import router as schedules_router
from hachi.gateway.routers.secrets import router as secrets_router
from hachi.gateway.routers.sessions import router as sessions_router
from hachi.gateway.routers.setup import router as setup_router
from hachi.gateway.routers.skills import router as skills_router
from hachi.gateway.routers.spend import router as spend_router
from hachi.gateway.routers.subagents import router as subagents_router
from hachi.gateway.routers.system import router as system_router
from hachi.gateway.routers.voice import router as voice_router

ALL_ROUTERS: list[APIRouter] = [
    # Largest-first (T-47 Sec 5 decision 5): config + the runtime_ops split
    # are 39% of the route surface and prove the pattern.
    config_router,
    # T-47 Sec 3.3: the former `runtime_ops` (17 routes, no prefix) split into
    # these 7 prefix-less routers along its five unrelated concerns.
    runtime_events_router,
    mcp_router,
    secrets_router,
    jobs_router,
    permissions_router,
    subagents_router,
    schedules_router,
    sessions_router,
    system_router,
    setup_router,
    # The remaining 14 routers.
    openai_compat_router,
    channels_router,
    approvals_router,
    skills_router,
    memory_router,
    og_router,
    authoring_router,
    spend_router,
    integrations_router,
    integrations_callback_router,
    runs_router,
    files_router,
    voice_router,
    interactions_router,
]
