"""T-60 regression: the twin contract types must collapse to one definition each.

``SearchResult``, ``ExtractResult``, ``SearchProvider`` and ``ProviderHealth`` each
existed as TWO unrelated classes: one dataclass in ``core/providers/base.py`` and one
pydantic model in ``core/providers/search.py`` (for the search types), and two
dataclasses -- one in ``core/providers/base.py``, one in ``core/resilience.py`` -- for
``ProviderHealth``.

The sharper bug: ``HealthRecorder.snapshot()`` (``core/resilience.py``) produced
``resilience.ProviderHealth`` and handed it to ``Provider.update_health`` via
``ModelRouter._get_recorder``'s ``on_update`` wiring -- but ``Provider.update_health``,
``Provider.health`` and ``ProviderRegistry.health()`` are all annotated with
``providers.base.ProviderHealth``. It worked only because the two classes' field names
happened to line up; they had already drifted (``breaker: str`` vs
``Literal["closed", "open", "half_open"]``). A field-equality assertion would have
passed the whole time -- only object identity (``is``) catches this, which is why
these tests assert identity rather than structural equivalence.
"""

from __future__ import annotations

from hachi.core.resilience import RetryPolicy


def test_health_recorder_produces_the_canonical_provider_health() -> None:
    """The health object flowing through the router must be ONE class.

    Regression: HealthRecorder.snapshot() returned resilience.ProviderHealth,
    which was passed to Provider.update_health(health: base.ProviderHealth) via
    ModelRouter._get_recorder's on_update wiring and then collected into
    ProviderRegistry.health() -> dict[str, base.ProviderHealth]. It only worked
    because the field names happened to line up; the classes were unrelated and
    had already drifted (breaker: str vs Literal[...]).
    """
    from hachi.core.providers.base import ProviderHealth as CanonicalHealth
    from hachi.core.resilience import HealthRecorder
    from hachi.core.resilience import ProviderHealth as ResilienceHealth

    assert ResilienceHealth is CanonicalHealth, (
        f"two ProviderHealth classes still exist: {ResilienceHealth!r} vs {CanonicalHealth!r}"
    )

    recorder = HealthRecorder("test-provider", RetryPolicy())
    snapshot = recorder.snapshot()
    assert type(snapshot) is CanonicalHealth, (
        f"HealthRecorder produces {type(snapshot)} but the registry is annotated "
        f"with {CanonicalHealth}"
    )


def test_contract_types_have_exactly_one_definition() -> None:
    """SearchResult / ExtractResult / SearchProvider must resolve to one class each."""
    from hachi.core.providers import ExtractResult, SearchProvider, SearchResult
    from hachi.core.providers import search as search_mod

    assert SearchResult is search_mod.SearchResult
    assert ExtractResult is search_mod.ExtractResult
    assert SearchProvider is search_mod.SearchProvider
