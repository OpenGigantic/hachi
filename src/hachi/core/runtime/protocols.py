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
