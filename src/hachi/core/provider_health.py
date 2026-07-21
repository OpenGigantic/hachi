"""``ProviderHealth`` — the canonical provider health-snapshot contract.

T-60: extracted to its own dependency-free module. ``ProviderHealth`` is
produced by :class:`~hachi.core.resilience.HealthRecorder` and consumed by the
provider adapters in ``core/providers/`` and by
:class:`~hachi.core.routing.router.ModelRouter`.

It cannot be defined in either ``core/resilience.py`` or
``core/providers/base.py`` with the other importing it: ``providers/base.py``
imports ``hachi.core.routing.capability`` / ``.descriptor``, and importing
*any* name from the ``hachi.core.routing`` package eagerly runs
``hachi/core/routing/__init__.py``, which imports ``routing/router.py``,
which imports ``core/resilience.py`` -- so ``resilience.py`` importing
``providers/base.py`` (directly, or indirectly via the ``providers`` package
``__init__``) is a real circular import, not a hypothetical one. This module
has zero ``hachi.*`` imports so both sides can depend on it safely.
``core/providers/base.py`` re-exports :class:`ProviderHealth` from here so the
existing public import path (``hachi.core.providers.base.ProviderHealth``) is
unchanged for every other consumer. See ``docs/bug-bank.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ProviderHealth:
    """Health snapshot for one provider.

    Produced by :class:`~hachi.core.resilience.HealthRecorder` and consumed by
    :class:`~hachi.core.routing.router.ModelRouter` to skip open-breaker providers.
    """

    provider: str
    available: bool = True
    recent_error_rate: float = 0.0
    p50_latency_ms: float = 0.0
    consecutive_failures: int = 0
    breaker: Literal["closed", "open", "half_open"] = "closed"

    def is_usable(self) -> bool:
        """Return ``True`` when the provider may be tried (breaker not fully open)."""
        return self.available and self.breaker != "open"
