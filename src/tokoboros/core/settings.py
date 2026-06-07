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


class PermissionsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: PermissionTier = "guided"


MemoryBackend = Literal["chroma", "in_memory"]


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enable_session_history: bool = True
    enable_semantic_memory: bool = True
    backend: MemoryBackend = "chroma"


class CapabilityState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    configured: bool = False
    secret_ref: str | None = None


class ResolvedConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta: ConfigMeta = Field(default_factory=ConfigMeta)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    permissions: PermissionsSettings = Field(default_factory=PermissionsSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    capabilities: dict[str, CapabilityState] = Field(default_factory=dict)


def default_config() -> ResolvedConfig:
    return ResolvedConfig(
        capabilities={
            "google": CapabilityState(),
            "notion": CapabilityState(),
            "slack": CapabilityState(),
        }
    )
