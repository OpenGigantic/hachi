"""Structural contracts the engine depends on, implemented at the service edge.

T-68: three engine-side call sites (``core.cost``, ``core.runtime.resume``,
``core.runtime.job_handlers``) each reached directly into
``hachi.gateway.shared_services`` for the process-wide ``ProcessSharedServices``
singleton — three declared, frozen import-linter exemptions. All three want
the same narrow slice of it (``session_store`` / ``event_bus``), so this
module declares that slice as a :class:`Protocol` owned by ``core`` and a
tiny registration seam: the gateway edge registers a resolver here once (at
import time, in ``gateway/shared_services.py``); the engine reads through the
registered resolver instead of naming ``hachi.gateway`` at all.

``core`` must never import ``hachi.gateway`` — the whole point of this
module is that it does not need to, structurally.

T-42: a second, structurally identical seam lives here for a different
target. ``core.runtime.resume`` also reached for two methods
(``get_live_session`` / ``rehydrate``) off the live in-process gateway
runtime (``hachi.gateway.in_process_runtime._InProcessGatewayRuntime``) — via
an import of the CLI's entry-point module and the module global
``_ACTIVE_RUNTIME`` it read there, touching two private attributes
(``_agents`` / ``_agents_lock``) along the way. That was the only
``core -> cli`` import in the repo, previously kept legal by a standing
import-linter exemption. ``SessionRuntime`` below is that narrow slice; the
gateway edge registers the live runtime instance via
:func:`set_active_session_runtime` from its two boot sites
(``cli/main.py``'s ``_start_gateway_server`` and the ``gateway`` command).
``core`` must never import the CLI either, for the same structural reason it
must never import ``hachi.gateway``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProcessServices(Protocol):
    """The narrow slice of the process-wide shared-services singleton the
    engine reaches for.

    Structurally satisfied by
    :class:`hachi.gateway.shared_services.ProcessSharedServices` — ``core``
    must never name that class directly.
    """

    session_store: Any  # hachi.core.session_store.SessionStore | None
    event_bus: Any  # hachi.core.event_bus.EventBus | None


_PROCESS_SERVICES_PROVIDER: Callable[[], ProcessServices | None] | None = None


def set_process_services_provider(
    provider: Callable[[], ProcessServices | None] | None,
) -> None:
    """Register the process-wide services provider. Called by the gateway edge only.

    ``provider`` is a zero-arg callable, not a frozen instance, so it always
    reflects the *current* singleton (the gateway registers
    ``ProcessSharedServices.instance`` itself) rather than whichever instance
    happened to exist at registration time.

    BB-6: the provider may legitimately resolve to ``None`` — no gateway
    session has booted yet. Callers of :func:`get_active_process_services`
    must treat that as "no shared services available yet", never as an error.
    """
    global _PROCESS_SERVICES_PROVIDER  # noqa: PLW0603
    _PROCESS_SERVICES_PROVIDER = provider


def get_active_process_services() -> ProcessServices | None:
    """Return the current process-wide services object, or ``None``.

    Reads the registered provider through the module object (not a captured
    binding) so a late registration is visible at call time. Returns
    ``None`` both when nothing has ever registered a provider (the engine
    is used standalone, with no gateway present at all) and when a provider
    is registered but currently has nothing to offer (BB-6: no session has
    booted yet) — the two are indistinguishable to a caller and both mean
    "there is no shared store/bus right now".
    """
    if _PROCESS_SERVICES_PROVIDER is None:
        return None
    return _PROCESS_SERVICES_PROVIDER()


@runtime_checkable
class SessionRuntime(Protocol):
    """The minimum surface cold-start resume needs from a live gateway runtime.

    Implemented by ``hachi.gateway.in_process_runtime._InProcessGatewayRuntime``.
    ``core`` must never name that class. Deliberately two methods only — this
    protocol must stay the size of the actual dependency (T-42 §5 decision 1);
    do not add ``chat``, ``interrupt``, or anything else "while you're there".
    """

    def get_live_session(self, session_id: str) -> Any | None:
        """Return the live in-memory session, or None if it is not resident.

        Implementations own their own locking; callers must not.
        """

    def rehydrate(self, session_id: str) -> Any | None:
        """Rebuild a session from the durable transcript, or None on failure."""


_ACTIVE_SESSION_RUNTIME: SessionRuntime | None = None


def set_active_session_runtime(runtime: SessionRuntime | None) -> None:
    """Register the process-wide session runtime. Called by the gateway edge only."""
    global _ACTIVE_SESSION_RUNTIME  # noqa: PLW0603
    _ACTIVE_SESSION_RUNTIME = runtime


def get_active_session_runtime() -> SessionRuntime | None:
    """Read the registered runtime through the module object (late writes visible)."""
    return _ACTIVE_SESSION_RUNTIME
