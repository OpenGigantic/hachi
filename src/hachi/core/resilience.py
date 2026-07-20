"""Resilience layer: retry, circuit-breaker, and health tracking.

Closes debt D6 (600s Anthropic default; no retry/breaker). Design doc 13 §5.

``ResilientClient`` decorates any ``BaseLLMClient`` with:
  - per-call timeout (default 60s, down from 600s)
  - bounded exponential-backoff retry for transient errors (429, 5xx, timeout)
  - circuit-breaker with half-open hysteresis so a flapping provider can't
    oscillate the model mid-task (amendment A12)
  - latency and error-rate recording into ``ProviderHealth``

``resilient_call`` is a generic variant for ``SpeechProvider`` / ``SearchProvider``.

T-24/BB-116 -- retry ownership and the SDK's own ``max_retries``:
    ``ResilientClient`` / ``resilient_call`` are now the SOLE retry owner for
    any client that opts in by being wrapped here (``core/loop.py``'s own
    streaming retry loop, and the two divergent "is this transient"
    predicates it used to disagree with this module about, are gone).

    The ``anthropic`` SDK (0.105.2) still applies its own
    ``DEFAULT_MAX_RETRIES = 2`` underneath every call this module makes --
    deliberately left AT ITS DEFAULT rather than forced to ``max_retries=0``
    on ``anthropic.Anthropic(...)``. That change lives in ``core/llm.py``
    (out of this task's file scope) and, more importantly, would strip ALL
    retry protection from the several call sites that construct
    ``AnthropicClient``/``OpenAICompatibleClient`` directly and are never
    wrapped in a ``ResilientClient`` at all (e.g. ``core/sub_agent.py``'s
    delegation path, ``tools/memory_tools.py``'s background job) -- for
    those, the SDK's own retry is the ONLY resilience they have. The
    trade-off, stated plainly: the effective worst-case call budget for a
    ``ResilientClient``-wrapped chain is up to ``max_attempts x chain_length``
    resilience-level calls, each of which may itself be up to
    ``1 + DEFAULT_MAX_RETRIES`` (3) HTTP requests at the SDK layer -- e.g. 9
    resilience-level calls -> up to 27 HTTP requests, not 9. Still a large
    reduction from the four-layer-stacked worst case this task closed (up to
    63 HTTP requests for the same 9-call scenario), and it costs nothing at
    call sites this module can't see. See BB-116 in docs/bug-bank.md.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from hachi.core.llm import BaseLLMClient, LLMResponse, Message

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes / exception patterns that indicate a transient error worth retrying.
#
# T-24/BB-116: this is the UNION of what used to be two independently-maintained
# sets that disagreed -- this module's own {429,500,502,503,504} and
# core/loop.py's (now-deleted) _RETRYABLE_STATUS {408,409,429,500,502,503,504,529}.
# Dropping 408/409/529 would be a behaviour regression: 529 is "overloaded", the
# status the default provider (Anthropic) actually returns under load.
_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

# Exception type names that are transient (matched by class name substring).
_TRANSIENT_EXCEPTION_NAMES: tuple[str, ...] = (
    "timeout",
    "Timeout",
    "RateLimitError",
    "APIConnectionError",
    "ConnectError",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "InternalServerError",
    "OverloadedError",
    "ServiceUnavailable",
)

# T-24/BB-116: message-substring fallback for exceptions that carry neither a
# recognised class name nor an HTTP status attribute. Moved here from the
# deleted core/loop.py._RETRYABLE_HINTS -- it was that module's own last-resort
# check and had no equivalent in this module before the merge.
_RETRYABLE_HINTS: tuple[str, ...] = (
    "overloaded",
    "rate limit",
    "timeout",
    "timed out",
    "temporarily",
    "connection",
)


def _is_transient(exc: BaseException) -> bool:
    """Return ``True`` when *exc* is likely a transient error worth retrying.

    T-24/BB-116: this is now the SOLE definition of "transient" in the
    codebase -- the union of two strategies that used to live in separate
    modules and disagree (``core/loop.py``'s now-deleted
    ``_is_retryable_llm_error`` retried 408/409/529; this function's
    predecessor did not). Order of checks:

    1. Exception class-name substring match (this function's original
       strategy) -- e.g. a provider SDK's ``RateLimitError``.
    2. HTTP status code, probed via ``status_code``, then ``status``, then
       ``response.status_code`` (the union of both modules' attribute-probing
       strategies) -- against the union status set above. A *recognised*
       (present, int) status is conclusive either way; it does not fall
       through to the message-hint check below.
    3. Message-substring hints (``_RETRYABLE_HINTS``) -- the last resort for
       an exception that carries neither of the above (e.g. a bare
       ``ConnectionError`` with no status attribute at all).
    """
    cls_name = type(exc).__name__
    for name in _TRANSIENT_EXCEPTION_NAMES:
        if name in cls_name:
            return True

    # httpx / anthropic SDK may embed the status code under different names.
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS_CODES

    message = str(exc).lower()
    return any(hint in message for hint in _RETRYABLE_HINTS)


# Public name -- the one predicate every caller (core/loop.py,
# ResilientClient's own streaming retry below) should use going forward.
# `_is_transient` remains the definition site (its existing internal call
# sites in this module, and tests/test_search_platform.py, patch it by this
# name) so both names always resolve to the identical function object -- never
# two implementations that can drift apart.
is_transient = _is_transient


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    """Configures retry and circuit-breaker behaviour.

    Args:
        max_attempts: Total number of attempts (1 = no retry).
        backoff_base_s: Base wait in seconds before the first retry; doubles each attempt.
        timeout_s: Per-call timeout in seconds.  Default 60s (down from the 600s SDK default).
        breaker_threshold: Consecutive failures before the breaker opens.
        breaker_cooldown_s: Seconds the breaker stays open before entering half-open.
    """

    model_config = ConfigDict(extra="ignore")

    max_attempts: int = 3
    backoff_base_s: float = 0.5
    timeout_s: float = 60.0
    breaker_threshold: int = 5
    breaker_cooldown_s: float = 30.0

    @classmethod
    def from_config(cls, config: Any) -> RetryPolicy:
        """Extract ``[routing.resilience]`` from a :class:`~hachi.core.settings.ResolvedConfig`.

        Falls back to defaults when the section is absent.
        """
        try:
            routing = getattr(config, "routing", None)
            if routing is not None:
                res = getattr(routing, "resilience", None)
                if res is not None:
                    if isinstance(res, RetryPolicy):
                        return res
                    return cls.model_validate(res if isinstance(res, dict) else res.model_dump())
        except Exception:
            pass
        return cls()


# ---------------------------------------------------------------------------
# ProviderHealth
# ---------------------------------------------------------------------------


@dataclass
class ProviderHealth:
    """Live health snapshot for one provider instance.

    Attributes:
        provider: Provider name / registry key.
        available: ``False`` when the provider is known-broken.
        recent_error_rate: Fraction of recent calls that failed (0.0–1.0).
        p50_latency_ms: Median latency of successful calls in milliseconds.
        consecutive_failures: Number of failures without a success in between.
        breaker: Current breaker state.
    """

    provider: str
    available: bool = True
    recent_error_rate: float = 0.0
    p50_latency_ms: float = 0.0
    consecutive_failures: int = 0
    breaker: Literal["closed", "open", "half_open"] = "closed"

    def is_usable(self) -> bool:
        """Return ``True`` when the provider may be tried."""
        return self.available and self.breaker != "open"


# ---------------------------------------------------------------------------
# HealthRecorder
# ---------------------------------------------------------------------------


class HealthRecorder:
    """Thread-safe recorder that tracks latency and failures for one provider.

    Maintains a sliding window of recent call outcomes and derives the
    :class:`ProviderHealth` snapshot consumed by the router.
    """

    _WINDOW = 20  # calls retained for error-rate calculation

    def __init__(
        self,
        provider: str,
        policy: RetryPolicy,
        *,
        on_update: Callable[[ProviderHealth], None] | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._lock = threading.Lock()
        self._outcomes: list[bool] = []  # True = success
        self._latencies_ms: list[float] = []
        self._consecutive_failures = 0
        self._breaker: Literal["closed", "open", "half_open"] = "closed"
        self._breaker_opened_at: float = 0.0
        # H7: optional sink invoked after every outcome so a caller (the model
        # router) can wire real call results into its own provider-health view
        # instead of that view staying permanently at its boot-time default.
        self._on_update = on_update

    def _notify(self) -> None:
        if self._on_update is not None:
            self._on_update(self.snapshot())

    def record_success(self, latency_ms: float) -> None:
        """Record a successful call."""
        with self._lock:
            self._outcomes.append(True)
            self._latencies_ms.append(latency_ms)
            if len(self._outcomes) > self._WINDOW:
                self._outcomes.pop(0)
            if len(self._latencies_ms) > self._WINDOW:
                self._latencies_ms.pop(0)
            self._consecutive_failures = 0
            # Close breaker on success (even from half-open).
            self._breaker = "closed"
        self._notify()

    def record_failure(self) -> None:
        """Record a failed call and potentially open the circuit breaker."""
        with self._lock:
            self._outcomes.append(False)
            if len(self._outcomes) > self._WINDOW:
                self._outcomes.pop(0)
            self._consecutive_failures += 1
            if (
                self._consecutive_failures >= self._policy.breaker_threshold
                and self._breaker == "closed"
            ):
                self._breaker = "open"
                self._breaker_opened_at = time.monotonic()
                logger.warning(
                    "HealthRecorder[%s]: circuit breaker OPENED after %d consecutive failures",
                    self._provider,
                    self._consecutive_failures,
                )
            elif self._breaker == "half_open":
                # H7: a failed probe during the half-open trial must reopen the
                # breaker immediately — previously only closed->open existed, so
                # a still-flapping provider stuck in half-open let every probe
                # through instead of backing off again.
                self._breaker = "open"
                self._breaker_opened_at = time.monotonic()
                logger.warning(
                    "HealthRecorder[%s]: half-open probe failed — circuit breaker REOPENED",
                    self._provider,
                )
        self._notify()

    def snapshot(self) -> ProviderHealth:
        """Return the current health snapshot (thread-safe)."""
        with self._lock:
            # Transition open → half-open after cooldown.
            if (
                self._breaker == "open"
                and time.monotonic() - self._breaker_opened_at >= self._policy.breaker_cooldown_s
            ):
                self._breaker = "half_open"
                logger.info("HealthRecorder[%s]: circuit breaker → HALF-OPEN", self._provider)

            n = len(self._outcomes)
            error_rate = sum(1 for ok in self._outcomes if not ok) / n if n > 0 else 0.0
            lats = sorted(self._latencies_ms)
            # Nearest-rank percentile: rank = ceil(p * n), 1-indexed, clamped to
            # [0, n-1] after converting to a 0-index. `len(lats) // 2` is off by
            # one for even-length datasets (e.g. n=4 picks index 2 instead of 1).
            p50 = lats[max(0, math.ceil(0.5 * len(lats)) - 1)] if lats else 0.0

            return ProviderHealth(
                provider=self._provider,
                available=True,
                recent_error_rate=error_rate,
                p50_latency_ms=p50,
                consecutive_failures=self._consecutive_failures,
                breaker=self._breaker,
            )


# ---------------------------------------------------------------------------
# Generic resilient_call
# ---------------------------------------------------------------------------


def resilient_call(
    fn: Callable[[], T],
    policy: RetryPolicy,
    recorder: HealthRecorder,
    *,
    on_failover: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call *fn* with retry and circuit-breaker semantics.

    Args:
        fn: The callable to execute (must be idempotent).
        policy: Retry/breaker configuration.
        recorder: Health recorder that tracks outcomes.
        on_failover: Optional callback invoked on each retry ``(attempt, exc)``.

    Returns:
        The return value of *fn*.

    Raises:
        The last exception when all attempts are exhausted.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        health = recorder.snapshot()
        if not health.is_usable():
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(f"Provider {recorder._provider} circuit breaker is open")

        t0 = time.monotonic()
        try:
            result = fn()
            latency_ms = (time.monotonic() - t0) * 1000
            recorder.record_success(latency_ms)
            return result
        except Exception as exc:
            last_exc = exc
            recorder.record_failure()
            if not _is_transient(exc) or attempt >= policy.max_attempts:
                raise
            wait = policy.backoff_base_s * (2 ** (attempt - 1))
            logger.debug(
                "resilient_call: attempt %d/%d failed (%s); retrying in %.2fs",
                attempt,
                policy.max_attempts,
                type(exc).__name__,
                wait,
            )
            if on_failover is not None:
                on_failover(attempt, exc)
            time.sleep(wait)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("resilient_call: no attempts made")  # pragma: no cover


# ---------------------------------------------------------------------------
# ResilientClient
# ---------------------------------------------------------------------------


class ResilientClient(BaseLLMClient):  # type: ignore[misc]
    """Decorates any :class:`~hachi.core.llm.BaseLLMClient` with retry and circuit-breaker.

    ``ResilientClient`` IS a ``BaseLLMClient``, so it is a drop-in replacement
    everywhere a ``BaseLLMClient`` is expected (the loop is unchanged).

    Fallback chain: when *inner* fails terminally (after retries / breaker open),
    the first element of *fallbacks* is tried.  On that failing too, the next is
    tried, and so on.  Each transition emits a ``provider.failover`` log event.

    Args:
        inner: The primary client.
        policy: Retry/breaker configuration.
        recorder: Health recorder for the primary provider.
        fallbacks: Optional ordered list of ``(client, recorder)`` fallback pairs.
    """

    def __init__(
        self,
        inner: BaseLLMClient,
        policy: RetryPolicy,
        recorder: HealthRecorder,
        fallbacks: list[tuple[BaseLLMClient, HealthRecorder]] | None = None,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._recorder = recorder
        self._fallbacks: list[tuple[BaseLLMClient, HealthRecorder]] = fallbacks or []
        # Expose `model` attribute from inner for cost tracking / introspection.
        self.model: str = getattr(inner, "model", "unknown")
        # Expose `max_tokens` too: the loop's pre-flight token-safety ceiling reads it
        # off the turn client (loop.py's _safety_ceiling_exceeded). Without it the
        # ceiling silently degrades to a post-hoc check whenever [routing] is enabled
        # (BB-118) -- enabling a cost feature must never weaken a safety guardrail.
        self.max_tokens: int = getattr(inner, "max_tokens", 0) or 0
        # T-24/BB-116: optional sink invoked with (attempt, exc) whenever this
        # client retries -- a resilient_call() attempt inside _call_with_chain
        # (generate(), or the fallback-chain leg of generate_stream()) or the
        # primary streaming pre-output retry below. Lets a caller (typically
        # ExecutionLoop) re-surface the same user-visible LLM_RETRY runtime
        # event that used to come from the loop's own (now-deleted) retry loop,
        # without this module depending on ExecutionLoop.on_event. None (the
        # default) is a silent no-op -- e.g. a bare ResilientClient built
        # outside a loop, as in this module's own tests.
        self.on_retry: Callable[[int, BaseException], None] | None = None

    def _call_with_chain(
        self,
        fn_builder: Callable[[BaseLLMClient], Callable[[], Any]],
        *,
        include_primary: bool = True,
    ) -> Any:
        """Drive the fallback chain for a given operation.

        Args:
            fn_builder: Given a client, returns the zero-arg callable to try.
            include_primary: When ``False``, skip ``self._inner`` and start the
                chain at the first fallback. H7: a mid-stream primary failure in
                ``generate_stream`` must not re-invoke the same primary that just
                failed — that risks a double-print/double-bill of a stream that
                already emitted partial output before it broke.
        """
        chain = [(self._inner, self._recorder)] + self._fallbacks
        if not include_primary:
            chain = chain[1:]
        last_exc: BaseException | None = None

        for idx, (client, recorder) in enumerate(chain):
            if idx > 0:
                logger.warning(
                    "ResilientClient: failing over to fallback[%d] (model=%s)",
                    idx,
                    getattr(client, "model", "unknown"),
                )

            fn = fn_builder(client)
            try:
                result = resilient_call(fn, self._policy, recorder, on_failover=self.on_retry)
                # T-25/BB-117: stamp the client that actually produced this
                # response so cost attribution follows the server, not the
                # primary's fixed `self.model` -- this is what makes a failover
                # to fallback[idx] billable at the fallback's price. isinstance
                # guard: _call_with_chain is generic (fn_builder can drive any
                # zero-arg callable, e.g. resilient_call for non-LLM operations).
                if isinstance(result, LLMResponse) and result.served_model is None:
                    result.served_model = getattr(client, "model", None)
                return result
            except Exception as exc:
                last_exc = exc
                logger.error(
                    "ResilientClient: fallback[%d] (model=%s) failed: %s",
                    idx,
                    getattr(client, "model", "unknown"),
                    exc,
                )

        raise last_exc or RuntimeError("ResilientClient: all fallbacks exhausted")

    # ------------------------------------------------------------------
    # BaseLLMClient interface
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        dynamic_system: str | None = None,
    ) -> LLMResponse:
        """Generate a response via the chain with retry and circuit-breaker."""

        def _build(client: BaseLLMClient) -> Callable[[], LLMResponse]:
            def _call() -> LLMResponse:
                return client.generate(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    dynamic_system=dynamic_system,
                )

            return _call

        return self._call_with_chain(_build)

    def generate_stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        dynamic_system: str | None = None,
    ) -> Generator[str, None, LLMResponse]:
        """Stream text deltas via the primary client, retrying pre-output failures.

        T-24/BB-116: resilience is the sole retry owner (core/loop.py's own
        streaming retry loop is gone). A PRE-output failure on the primary --
        no chunk has reached the caller yet, so nothing has been double-printed
        or double-billed -- is retried against THIS client's own policy
        (``self._policy.max_attempts``, the same exponential backoff
        ``resilient_call`` uses). This is deliberately NOT routed through
        ``resilient_call`` itself: that function's ``Callable[[], T]`` contract
        doesn't fit a generator that must forward chunks to the caller AS THEY
        ARRIVE, not only after the whole call completes.

        Once ANY chunk has been yielded (mid-stream failure), retries are
        exhausted, or the failure isn't transient, this degrades to
        ``generate()`` over the fallback chain with the primary excluded --
        H7: a stream that already emitted output must NEVER be re-invoked
        (double-print / double-bill risk). See ``_call_with_chain``'s
        docstring for the same rule stated there.
        """
        last_exc: BaseException | None = None

        for attempt in range(1, self._policy.max_attempts + 1):
            chunks_yielded = 0
            t0 = time.monotonic()
            try:
                # Capture and RETURN the inner generator's value (the final
                # LLMResponse). `yield from` evaluates to the delegated
                # generator's return value; dropping it makes this generator
                # return None, so callers that read the streamed response
                # (loop.run -> response.usage) crash on a successful stream.
                gen = self._inner.generate_stream(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    dynamic_system=dynamic_system,
                )
                response: LLMResponse | None = None
                while True:
                    try:
                        chunk = next(gen)
                    except StopIteration as si:
                        response = si.value
                        break
                    chunks_yielded += 1
                    yield chunk
                latency_ms = (time.monotonic() - t0) * 1000
                self._recorder.record_success(latency_ms)
                # T-25/BB-117: this success path does NOT go through
                # _call_with_chain (see the module docstring above) -- it is
                # the path almost every real turn takes, so a stamp added only
                # in _call_with_chain would leave it unstamped. Backfill from
                # the primary when the client itself didn't report one.
                if response is not None and response.served_model is None:
                    response.served_model = getattr(self._inner, "model", None)
                return response
            except Exception as exc:
                last_exc = exc
                self._recorder.record_failure()
                if (
                    chunks_yielded > 0
                    or attempt >= self._policy.max_attempts
                    or not is_transient(exc)
                ):
                    break
                wait = self._policy.backoff_base_s * (2 ** (attempt - 1))
                logger.warning(
                    "ResilientClient.generate_stream: primary pre-output failure "
                    "(attempt %d/%d): %s -- retrying in %.2fs",
                    attempt,
                    self._policy.max_attempts,
                    type(exc).__name__,
                    wait,
                )
                if self.on_retry is not None:
                    self.on_retry(attempt, exc)
                time.sleep(wait)

        # Exhausted retries / non-transient / mid-stream failure: degrade to
        # the fallback chain via generate(), skipping the primary (H7).
        if self._fallbacks:
            logger.warning(
                "ResilientClient.generate_stream: primary failed (%s); "
                "falling back to generate() via chain",
                type(last_exc).__name__ if last_exc is not None else "unknown",
            )
            response = self._call_with_chain(
                lambda c: (
                    lambda: c.generate(  # noqa: E731
                        messages=messages,
                        system_prompt=system_prompt,
                        tools=tools,
                        dynamic_system=dynamic_system,
                    )
                ),
                include_primary=False,
            )
            if response.content:
                yield response.content
            return response

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("ResilientClient.generate_stream: no attempts made")  # pragma: no cover

    def list_models(self) -> list[str]:
        """Delegate to the inner client (no retry needed for model listing)."""
        try:
            return list(self._inner.list_models())
        except Exception:
            return []

    @property
    def supports_vision(self) -> bool:
        """Return ``True`` when the primary inner client's model has the VISION capability.

        Delegates to the inner client's ``supports_vision`` property so callers
        need not unwrap the resilience layer.
        """
        # BaseLLMClient declares supports_vision with a concrete `False` default
        # (core/llm.py), so any BaseLLMClient-typed self._inner always has it,
        # inherited if not overridden -- read it directly (BB-107).
        return bool(self._inner.supports_vision)

    @property
    def _health(self) -> ProviderHealth:
        """Current health snapshot (convenience attribute)."""
        return self._recorder.snapshot()
