"""Per-session token accounting and cost estimation.

Accumulates :class:`~hachi.core.llm.TokenUsage` across a session and
estimates spend from a per-model price table. Pure and thread-safe, so it can be
unit-tested without an agent and read from any thread (the loop records, the TUI
reads). Prices are USD per 1M tokens and are best-effort estimates — they are
meant to give the user live feedback on spend, not billing-grade accuracy.
"""

from __future__ import annotations

import datetime
import logging
import threading
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from hachi.core.llm import TokenUsage

logger = logging.getLogger(__name__)


@runtime_checkable
class _PricingProvider(Protocol):
    """Minimal duck-type for ModelRegistry's pricing look-up (avoids circular import)."""

    def pricing_for_model(self, model_id: str) -> object | None:  # noqa: D102
        ...


# USD per 1,000,000 tokens, as (input, output). Prefix match on the model id so
# dated variants (e.g. "claude-opus-4-8-20260101") resolve to the family price.
# Single source of truth is src/hachi/data/model-registry.json — add models there,
# not here. This dict is a computed shim so call-sites that key into MODEL_PRICING
# by exact id continue to work.
def _build_model_pricing() -> dict[str, tuple[float, float]]:
    from hachi.core.routing.registry_data import load_model_registry_json

    result: dict[str, tuple[float, float]] = {}
    for desc in load_model_registry_json():
        if desc.pricing is not None:
            result[desc.id] = (desc.pricing.input_per_mtok, desc.pricing.output_per_mtok)
    return result


MODEL_PRICING: dict[str, tuple[float, float]] = _build_model_pricing()
_DEFAULT_PRICING = (1.0, 5.0)

# ---------------------------------------------------------------------------
# Voice / modality pricing (Issue 9)
# ---------------------------------------------------------------------------

# STT: USD per minute of audio.
# TTS: USD per 1,000 characters.
# Prefix-matched on model id (longest prefix wins, like MODEL_PRICING).
#
# Sources (best-effort estimates, not billing-grade):
#   whisper-1:          $0.006 / min   (OpenAI Whisper)
#   tts-1:              $0.015 / 1k chars
#   tts-1-hd:           $0.030 / 1k chars
#   eleven_turbo_v2_5:  $0.30  / 1k chars  (ElevenLabs Turbo, ~0.3 USD/1k)
#   eleven_multilingual_v2: $0.30 / 1k chars
VOICE_PRICING: dict[str, dict[str, float]] = {
    # STT models — keyed by model id prefix, value = { "per_minute": <float> }
    "whisper-1": {"per_minute": 0.006},
    "whisper": {"per_minute": 0.006},
    # TTS models — keyed by model id prefix, value = { "per_1k_chars": <float> }
    "tts-1-hd": {"per_1k_chars": 0.030},
    "tts-1": {"per_1k_chars": 0.015},
    "eleven_turbo_v2_5": {"per_1k_chars": 0.30},
    "eleven_turbo_v2": {"per_1k_chars": 0.30},
    "eleven_multilingual_v2": {"per_1k_chars": 0.30},
    "eleven": {"per_1k_chars": 0.30},
}

_VOICE_LOCK = threading.Lock()


def _voice_cost(modality: Literal["stt", "tts"], model: str, units: float) -> float:
    """Compute the USD cost for a voice operation without side-effects.

    Args:
        modality: ``"stt"`` (units = minutes) or ``"tts"`` (units = chars).
        model: Model identifier. Prefix-matched against :data:`VOICE_PRICING`.
        units: Quantity — minutes for STT, character count for TTS.

    Returns:
        Estimated cost in USD (0.0 on pricing miss).
    """
    model_lower = (model or "").lower()
    best_entry: dict[str, float] | None = None
    best_len = -1
    for prefix, entry in VOICE_PRICING.items():
        if model_lower.startswith(prefix) and len(prefix) > best_len:
            best_entry, best_len = entry, len(prefix)

    if best_entry is None:
        return 0.0

    if modality == "stt":
        rate = best_entry.get("per_minute", 0.0)
        return units * rate
    else:  # tts
        rate = best_entry.get("per_1k_chars", 0.0)
        return units * rate / 1_000.0


def record_voice_usage(
    modality: Literal["stt", "tts"],
    model: str,
    units: float,
) -> float:
    """Record a voice operation into the daily spend ledger and return its cost.

    Integrates with the same ``SessionStore.record_daily_cost`` path used by LLM
    turns so ``/spend/daily`` and per-modality breakdowns include voice spend.

    Args:
        modality: ``"stt"`` (units = minutes of audio) or
                  ``"tts"`` (units = number of characters synthesised).
        model: Model identifier (e.g. ``"whisper-1"``, ``"tts-1"``,
               ``"eleven_turbo_v2_5"``).  Prefix-matched against
               :data:`VOICE_PRICING`.
        units: Quantity for the billing unit (minutes or chars — see *modality*).

    Returns:
        Estimated cost in USD.  Returns ``0.0`` on pricing miss or any error —
        never raises.
    """
    try:
        cost = _voice_cost(modality, model, units)
        if cost > 0:
            # Record into the same daily ledger used by LLM turns.
            # Import lazily to avoid a circular import at module load time.
            from hachi.core.session_store import SessionStore

            date_str = datetime.date.today().isoformat()
            # Use the module-level singleton if available (gateway process), else
            # open a short-lived connection.  Both paths call record_daily_cost
            # which is atomic (INSERT OR IGNORE + UPDATE).
            try:
                from hachi.gateway.orchestrator import _get_session_store

                store = _get_session_store()
                if store is not None:
                    store.record_daily_cost(date_str, cost)
                else:
                    raise RuntimeError("no shared store")
            except Exception:
                # Fallback: open a dedicated connection (unit-test / CLI context).
                store = SessionStore()
                store.record_daily_cost(date_str, cost)
        return cost
    except Exception as exc:
        logger.warning(
            "record_voice_usage: failed to record %s/%s cost (units=%.3f): %s",
            modality,
            model,
            units,
            exc,
        )
        return 0.0


# Anthropic prompt-cache multipliers relative to the base input price.
_CACHE_READ_MULTIPLIER = 0.1
_CACHE_WRITE_MULTIPLIER = 1.25


def record_background_usage(
    model: str,
    usage: TokenUsage,
    *,
    store: Any = None,
    feature: str | None = None,
) -> float:
    """Record a background (non-session) LLM call's spend into the durable daily
    ledger and return its estimated cost.

    Background work — fact extraction, durable session titling, recall summarisation —
    runs outside the per-session :class:`CostTracker`, so without this its spend is
    invisible to ``/spend`` and to the daily spend cap (both read this ledger). Mirrors
    :func:`record_voice_usage`: best-effort, never raises.

    Args:
        model:   Model id the background call used (priced via :func:`pricing_for`).
        usage:   Token usage from the call's ``LLMResponse``.
        store:   Optional :class:`~hachi.core.session_store.SessionStore` (mainly for
            tests). When ``None`` the shared gateway store is used, else a short-lived
            one is opened.
        feature: Optional feature label for per-feature attribution (R10).
            e.g. ``"extraction"``, ``"titling"``, ``"compaction"``.  Ignored today
            (background calls go to the daily ledger, not a per-session tracker)
            but accepted so call sites can be annotated consistently.

    Returns:
        Estimated USD cost recorded (``0.0`` on a pricing miss or any error).
    """
    try:
        in_price, out_price = pricing_for(model)
        cost: float = (
            usage.input_tokens * in_price
            + usage.cache_read_tokens * in_price * _CACHE_READ_MULTIPLIER
            + usage.cache_creation_tokens * in_price * _CACHE_WRITE_MULTIPLIER
            + usage.output_tokens * out_price
        ) / 1_000_000
        if cost <= 0:
            return 0.0

        date_str = datetime.date.today().isoformat()
        if store is not None:
            store.record_daily_cost(date_str, cost)
            return cost

        from hachi.core.session_store import SessionStore

        try:
            from hachi.gateway.orchestrator import _get_session_store

            shared = _get_session_store()
            if shared is not None:
                shared.record_daily_cost(date_str, cost)
            else:
                raise RuntimeError("no shared store")
        except Exception:
            SessionStore().record_daily_cost(date_str, cost)
        return cost
    except Exception as exc:
        logger.warning("record_background_usage: failed for %s: %s", model, exc)
        return 0.0


def pricing_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD-per-Mtoken for a model id (longest prefix wins)."""
    model = (model or "").lower()
    best: tuple[float, float] | None = None
    best_len = -1
    for prefix, price in MODEL_PRICING.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best if best is not None else _DEFAULT_PRICING


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    """An immutable view of accumulated usage and estimated spend."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    call_count: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "call_count": self.call_count,
            # Fraction of cumulative INPUT tokens served from cache (0..1). 0 here
            # across a long session means prompt caching isn't engaging — a cost smell.
            "cache_hit_rate": round(
                self.cache_read_tokens
                / max(
                    1,
                    self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens,
                ),
                4,
            ),
        }


@dataclass(frozen=True, slots=True)
class ModelCostSnapshot:
    """Per-model slice of accumulated cost.

    Attributes:
        model_id: The exact model identifier as passed to :meth:`CostTracker.record`.
        provider: The provider name, when available (may be ``""`` when the tracker
            has no registry mapping).
        input_tokens: Fresh (non-cached) input tokens consumed by this model.
        output_tokens: Output tokens generated by this model.
        estimated_cost_usd: Estimated spend in USD for this model.
        call_count: Number of :meth:`CostTracker.record` calls attributed to this model.
    """

    model_id: str
    provider: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    call_count: int

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "call_count": self.call_count,
        }


class CostTracker:
    """Accumulate token usage and estimate cost across a session.

    Per-model and per-provider attribution (Slice 2.5):
    Each :meth:`record` call is attributed to the given *model* id.  Callers can
    retrieve per-model breakdowns via :meth:`per_model_snapshot` and roll them up
    by provider via :meth:`per_provider_snapshot`.
    """

    def __init__(
        self,
        registry: _PricingProvider | None = None,
        *,
        provider_map: dict[str, str] | None = None,
    ) -> None:
        """Initialise the tracker.

        Args:
            registry: Optional :class:`~hachi.core.routing.registry.ModelRegistry`.
                When provided, pricing is looked up from the registry first (which
                absorbs the bundled defaults table); the legacy ``MODEL_PRICING``
                dict is used as a fallback when the registry returns ``None``.
            provider_map: Optional ``{model_id: provider_name}`` mapping used to
                attribute calls to a provider dimension without a full registry.
                When ``None``, the provider dimension is derived from the registry
                (if available) or left as an empty string.
        """
        self._lock = threading.Lock()
        self._input = 0
        self._output = 0
        self._cache_read = 0
        self._cache_creation = 0
        self._cost = 0.0
        self._calls = 0
        self._registry = registry
        self._provider_map: dict[str, str] = dict(provider_map) if provider_map else {}
        # Per-model accumulators: model_id → [input, output, cache_read, cache_creation, cost, n]
        self._by_model: dict[str, list[float]] = {}
        # Per-feature accumulators (R10): feature → [in, out, cache_read, cache_creation, cost, n]
        # Unlabelled calls are bucketed under "unattributed".
        self._by_feature: dict[str, list[float]] = {}

    def _resolve_pricing(self, model: str) -> tuple[float, float, float, float]:
        """Return (input, output, cache_read_mult, cache_write_mult), registry-backed.

        The cache multipliers are per-model because providers differ: Anthropic
        cache reads are ~0.1x the input price, OpenAI cached input is ~0.5x.
        Honouring the descriptor's mults keeps displayed cost accurate per provider
        instead of assuming Anthropic economics for every model. Falls back to the
        module constants when no registry/descriptor pricing is available.
        """
        if self._registry is not None:
            try:
                result = self._registry.pricing_for_model(model)
                if result is not None:
                    return (
                        float(getattr(result, "input_per_mtok", 0.0)),
                        float(getattr(result, "output_per_mtok", 0.0)),
                        float(getattr(result, "cache_read_mult", _CACHE_READ_MULTIPLIER)),
                        float(getattr(result, "cache_write_mult", _CACHE_WRITE_MULTIPLIER)),
                    )
            except Exception:
                pass
        in_price, out_price = pricing_for(model)
        return (in_price, out_price, _CACHE_READ_MULTIPLIER, _CACHE_WRITE_MULTIPLIER)

    def _provider_for_model(self, model: str) -> str:
        """Return the provider name for *model*, or ``""`` when unknown."""
        if model in self._provider_map:
            return self._provider_map[model]
        # Try to resolve via the registry's descriptor (if it exposes one).
        if self._registry is not None:
            try:
                desc = self._registry.get(model)  # type: ignore[attr-defined]
                if desc is not None:
                    return str(getattr(desc, "provider", ""))
            except Exception:
                pass
        return ""

    def record(self, model: str, usage: TokenUsage, *, feature: str | None = None) -> None:
        """Add one generation's usage, pricing it against ``model``.

        Args:
            model:   Model identifier used for pricing lookup.
            usage:   Token usage from the LLM response.
            feature: Optional label for per-feature cost attribution (R10).
                     e.g. ``"chat"``, ``"titling"``, ``"extraction"``, ``"compaction"``.
                     Unlabelled calls are bucketed under ``"unattributed"``.
        """
        in_price, out_price, cache_read_mult, cache_write_mult = self._resolve_pricing(model)
        # input_tokens, cache_read and cache_creation are SEPARATE, mutually-exclusive
        # counters (input_tokens is the fresh, non-cached portion), so they are additive:
        # price fresh input at the base rate, cache reads cheaper, cache writes pricier.
        # The multipliers are per-model (Anthropic ~0.1x read / 1.25x write; OpenAI ~0.5x
        # read) so each provider's cache economics are honoured, not assumed.
        cost = (
            usage.input_tokens * in_price
            + usage.cache_read_tokens * in_price * cache_read_mult
            + usage.cache_creation_tokens * in_price * cache_write_mult
            + usage.output_tokens * out_price
        ) / 1_000_000

        _feature = feature or "unattributed"

        with self._lock:
            self._input += usage.input_tokens
            self._output += usage.output_tokens
            self._cache_read += usage.cache_read_tokens
            self._cache_creation += usage.cache_creation_tokens
            self._cost += cost
            self._calls += 1

            # Per-model attribution: [input, output, cache_read, cache_creation, cost, calls]
            acc = self._by_model.setdefault(model, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            acc[0] += usage.input_tokens
            acc[1] += usage.output_tokens
            acc[2] += usage.cache_read_tokens
            acc[3] += usage.cache_creation_tokens
            acc[4] += cost
            acc[5] += 1

            # Per-feature attribution (R10): same layout as per-model.
            facc = self._by_feature.setdefault(_feature, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            facc[0] += usage.input_tokens
            facc[1] += usage.output_tokens
            facc[2] += usage.cache_read_tokens
            facc[3] += usage.cache_creation_tokens
            facc[4] += cost
            facc[5] += 1

    def cache_metrics(self, model: str, usage: TokenUsage) -> dict[str, float | int | str]:
        """Per-call cache/cost breakdown for observability — hit-rate, fresh-vs-cached
        cost split, and what this call would cost with no cache / a full cache.

        Used by the execution loop to emit a ``cache_metrics`` event after each
        generation so cache effectiveness is visible per request, not just buried in
        the aggregate spend total. Pure; resolves per-model pricing + cache multipliers.
        """
        from hachi.core.cache_observability import cache_call_metrics

        in_price, out_price, cache_read_mult, cache_write_mult = self._resolve_pricing(model)
        metrics: dict[str, float | int | str] = cache_call_metrics(
            model=model,
            usage=usage,
            input_price=in_price,
            output_price=out_price,
            cache_read_mult=cache_read_mult,
            cache_write_mult=cache_write_mult,
        ).to_dict()
        return metrics

    def snapshot(self) -> CostSnapshot:
        with self._lock:
            return CostSnapshot(
                input_tokens=self._input,
                output_tokens=self._output,
                cache_read_tokens=self._cache_read,
                cache_creation_tokens=self._cache_creation,
                total_tokens=self._input + self._output + self._cache_read + self._cache_creation,
                estimated_cost_usd=self._cost,
                call_count=self._calls,
            )

    def per_model_snapshot(self) -> dict[str, ModelCostSnapshot]:
        """Return per-model cost attribution.

        Returns:
            A dict mapping ``model_id → ModelCostSnapshot``.  Models are ordered
            by descending estimated cost.
        """
        with self._lock:
            items = dict(self._by_model)

        result: dict[str, ModelCostSnapshot] = {}
        for mid, acc in items.items():
            result[mid] = ModelCostSnapshot(
                model_id=mid,
                provider=self._provider_for_model(mid),
                input_tokens=int(acc[0]),
                output_tokens=int(acc[1]),
                estimated_cost_usd=acc[4],
                call_count=int(acc[5]),
            )
        return dict(sorted(result.items(), key=lambda kv: kv[1].estimated_cost_usd, reverse=True))

    def per_provider_snapshot(self) -> dict[str, ModelCostSnapshot]:
        """Return cost rolled up by provider name.

        Models from the same provider are summed together.  When a model's
        provider is unknown (``""``) it is grouped under ``"unknown"``.

        Returns:
            A dict mapping ``provider_name → ModelCostSnapshot`` (with
            ``model_id`` set to the provider name for convenience).
        """
        per_model = self.per_model_snapshot()
        by_provider: dict[str, list[ModelCostSnapshot]] = {}
        for snap in per_model.values():
            pname = snap.provider or "unknown"
            by_provider.setdefault(pname, []).append(snap)

        result: dict[str, ModelCostSnapshot] = {}
        for pname, snaps in by_provider.items():
            result[pname] = ModelCostSnapshot(
                model_id=pname,
                provider=pname,
                input_tokens=sum(s.input_tokens for s in snaps),
                output_tokens=sum(s.output_tokens for s in snaps),
                estimated_cost_usd=sum(s.estimated_cost_usd for s in snaps),
                call_count=sum(s.call_count for s in snaps),
            )
        return dict(sorted(result.items(), key=lambda kv: kv[1].estimated_cost_usd, reverse=True))

    def per_feature_snapshot(self) -> dict[str, dict[str, float | int]]:
        """Return per-feature cost attribution (R10).

        Returns:
            A dict mapping ``feature_name → {input_tokens, output_tokens,
            total_tokens, estimated_cost_usd, call_count}``.  Features ordered
            by descending estimated cost.  Unlabelled calls appear under
            ``"unattributed"``.
        """
        with self._lock:
            items = dict(self._by_feature)

        result: dict[str, dict[str, float | int]] = {}
        for fname, acc in items.items():
            total_tokens = int(acc[0]) + int(acc[1]) + int(acc[2]) + int(acc[3])
            result[fname] = {
                "input_tokens": int(acc[0]),
                "output_tokens": int(acc[1]),
                "total_tokens": total_tokens,
                "estimated_cost_usd": acc[4],
                "call_count": int(acc[5]),
            }
        return dict(
            sorted(result.items(), key=lambda kv: kv[1]["estimated_cost_usd"], reverse=True)
        )
