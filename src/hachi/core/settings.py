from __future__ import annotations

from typing import Literal

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


def default_config() -> ResolvedConfig:
    return ResolvedConfig(
        capabilities={
            "google": CapabilityState(),
            "notion": CapabilityState(),
            "slack": CapabilityState(),
        }
    )
