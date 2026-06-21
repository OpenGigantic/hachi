from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConfigVersion = Literal[1]
ProviderKind = Literal[
    "anthropic", "openai-compatible", "openrouter", "groq", "together", "deepseek", "ollama"
]
PermissionTier = Literal["sandbox", "guided", "unrestricted"]


class ConfigMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    config_version: ConfigVersion = 1
    imported_from_legacy: bool = False


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    persona: str = ""
    max_tokens: int = 8192
    temperature: float = 0.0


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ProviderKind = "anthropic"
    model: str = "claude-haiku-4-5"
    base_url: str | None = None
    api_key_ref: str | None = None
    api_key_env: str | None = None
    # Hard guardrail: when non-empty, ONLY these models may be selected via
    # `/model` / `--model`. Empty (default) = no restriction (any model the
    # provider exposes is allowed).
    allowed_models: list[str] = Field(default_factory=list)


class AskBeforeSettings(BaseModel):
    """User-facing "Ask before…" confirmation preferences.

    Each flag controls whether the agent must pause and ask for confirmation
    before a class of sensitive action. These are surfaced in the web UI
    (Settings → Permissions & safety) and persisted to ``settings.toml`` so they
    survive a reload. ``True`` = ask first (safer); ``False`` = proceed silently.

    The keys mirror the toggle ids the frontend sends, so a single
    ``PUT /config/permissions`` round-trips the panel state.
    """

    model_config = ConfigDict(extra="ignore")

    # Ask before a single task spends over the per-task cost cap.
    spend_over_threshold: bool = True
    # Ask before any tool sends data off the local machine (network egress).
    network_egress: bool = True
    # Ask before deleting files.
    delete_files: bool = True


class PermissionsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: PermissionTier = "guided"
    ask_before: AskBeforeSettings = Field(default_factory=AskBeforeSettings)


MemoryBackend = Literal["chroma", "in_memory", "fts5_only"]


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enable_session_history: bool = True
    enable_semantic_memory: bool = True
    backend: MemoryBackend = "chroma"
    # P2-04: M0 — FTS5 index over memory/*.og files, merged via RRF with Chroma.
    graph: bool = False


class LimitsSettings(BaseModel):
    """Per-session / per-turn safety caps (None = unlimited).

    Mirrors the optional ``[limits]`` TOML section that the gateway orchestrator
    reads and the execution loop enforces, so a cap set via
    ``hachi config-set session_cost_cap_usd`` is actually honoured at runtime
    (rather than written to a key nothing reads).
    """

    model_config = ConfigDict(extra="ignore")

    max_session_cost_usd: float | None = None
    max_session_tokens: int | None = None
    max_turn_cost_usd: float | None = None
    max_turn_tokens: int | None = None


class CapabilityState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    configured: bool = False
    secret_ref: str | None = None


class TelegramChannelSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Telegram chat ids allowed to talk to the agent. Empty = no restriction
    # (the bot answers anyone who messages it — only sensible for a private bot).
    allowlist: list[str] = Field(default_factory=list)


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    telegram: TelegramChannelSettings = Field(default_factory=TelegramChannelSettings)


class ResilienceSettings(BaseModel):
    """Retry and circuit-breaker parameters for the routing resilience layer.

    Maps to ``[routing.resilience]`` in ``settings.toml``.  All fields are
    optional — absent section uses the :class:`~hachi.core.resilience.RetryPolicy`
    defaults.
    """

    model_config = ConfigDict(extra="ignore")

    max_attempts: int = 3
    backoff_base_s: float = 0.5
    timeout_s: float = 60.0
    breaker_threshold: int = 5
    breaker_cooldown_s: float = 30.0


class EventsSettings(BaseModel):
    """Optional ``[events]`` config section (Slice 2.2 — EventBus funnel).

    When ``bus_funnel=true``, ``emit_runtime_event`` / ``relay_event`` publish
    through the ``EventBus`` singleton instead of calling consumers directly.
    Back-compat: the same two sync subscribers (read-models append + SSE surface)
    are registered in the same order, so output is byte-identical by construction.

    Default is ``False`` so the old path stays active until the flag is set.
    """

    model_config = ConfigDict(extra="ignore")

    bus_funnel: bool = False


class ObservabilitySettings(BaseModel):
    """Optional ``[observability]`` config section (Slice 2.3 — tracing / metrics).

    When ``enabled=True`` the gateway calls :func:`~hachi.core.tracing.setup_from_config`
    at boot, selecting the exporter via :attr:`exporter`.

    Attributes:
        enabled:           Master switch.  Default ``False`` so default runtime is
                           unchanged (tracing stays dormant unless opted in).
        exporter:          One of ``"console"`` (default), ``"otlp"``, ``"langfuse"``.
        endpoint:          OTLP endpoint URL (optional; falls back to
                           ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var).
        metrics_enabled:   When ``True``, also initialise the metrics facade.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    exporter: str = "console"
    endpoint: str | None = None
    metrics_enabled: bool = False


class JobsSettings(BaseModel):
    """Optional ``[jobs]`` config section (Slice 2.2 — durable job handlers).

    When ``durable_handlers=true``:
      - A ``JobRegistry`` is built at boot with the four named handlers.
      - ``_enqueue_title_job`` converts to ``handler_name=`` so titles survive
        restarts (fact-extraction jobs are converted inside ``MemoryManager``
        when this flag is in the env; see memory.py comment).
      - The scheduler sweeps WAITING runs past ``wait.timeout_at`` (A4).
      - On boot, WAITING runs are re-armed and RUNNING crashes are resolved (§6).

    Default is ``False`` so the old func= path stays active.

    ``worker_count`` is wired but not enforced (single worker pool is sufficient
    for single-node/single-user; seam for future throughput).
    """

    model_config = ConfigDict(extra="ignore")

    durable_handlers: bool = False
    worker_count: int = 1


class VoiceSettings(BaseModel):
    """Optional ``[voice]`` config section (Initiative F — Voice Platform).

    Provides STT/TTS configuration with the established HACHI_STT_* / HACHI_TTS_*
    env-var precedence as the fallback.  All fields are optional; when absent the
    :class:`~hachi.core.providers.speech.OpenAICompatibleSpeechProvider` reads
    directly from the env vars (same precedence order, back-compat guaranteed).

    Attributes:
        stt_model: Whisper model name (e.g. ``"whisper-1"``).
        stt_base_url: Base URL for the STT endpoint (OpenAI-compat).
        stt_api_key_env: Name of the env var that holds the STT API key.
        tts_model: TTS model name (e.g. ``"tts-1"``).
        tts_base_url: Base URL for the TTS endpoint (OpenAI-compat).
        tts_api_key_env: Name of the env var that holds the TTS API key.
        tts_voice: Default TTS voice name (e.g. ``"alloy"``).
    """

    model_config = ConfigDict(extra="ignore")

    stt_model: str | None = None
    stt_base_url: str | None = None
    stt_api_key_env: str | None = None
    tts_model: str | None = None
    tts_base_url: str | None = None
    tts_api_key_env: str | None = None
    tts_voice: str | None = None


class RoutingSettings(BaseModel):
    """Optional ``[routing]`` config section.

    When absent from ``settings.toml``, :class:`~hachi.core.routing.policy.RoutingPolicy`
    synthesises back-compat behaviour (single model for every capability).

    Attributes:
        default_model: The fallback / session model id.
        auto: When ``True``, derive smart-default policy from the registry (A1).
        capabilities: Explicit ordered candidate list per capability name.
        model_overrides: Extra :class:`~hachi.core.routing.descriptor.ModelDescriptor`
            dicts to register (highest precedence in the registry).
        resilience: Retry / circuit-breaker settings.
    """

    model_config = ConfigDict(extra="ignore")

    default_model: str = "claude-haiku-4-5"
    auto: bool = False
    capabilities: dict[str, list[str]] = Field(default_factory=dict)
    model_overrides: list[Any] = Field(default_factory=list)
    resilience: ResilienceSettings = Field(default_factory=ResilienceSettings)


class ResolvedConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta: ConfigMeta = Field(default_factory=ConfigMeta)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    # Named additional providers for multi-provider /model switching.
    # Configure via [providers.NAME] sections in ~/.hachi/settings.toml.
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    permissions: PermissionsSettings = Field(default_factory=PermissionsSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    capabilities: dict[str, CapabilityState] = Field(default_factory=dict)
    channels: ChannelSettings = Field(default_factory=ChannelSettings)
    # Optional [routing] section — absent = back-compat (one model for everything).
    routing: RoutingSettings | None = None
    # Optional [voice] section — absent = fall back to HACHI_STT_* / HACHI_TTS_* env vars.
    voice: VoiceSettings | None = None
    # Optional [events] section — absent = old direct-call funnel (back-compat).
    events: EventsSettings = Field(default_factory=EventsSettings)
    # Optional [jobs] section — absent = func= closures (back-compat).
    jobs: JobsSettings = Field(default_factory=JobsSettings)
    # Optional [observability] section — absent = tracing off (back-compat).
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


def default_config() -> ResolvedConfig:
    return ResolvedConfig(
        capabilities={
            "google": CapabilityState(),
            "notion": CapabilityState(),
            "slack": CapabilityState(),
        }
    )
