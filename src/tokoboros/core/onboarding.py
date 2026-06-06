from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tokoboros.core.config_loader import load_effective_config, write_global_config
from tokoboros.core.paths import (
    TokoborosPaths,
    ensure_directories,
    get_tokoboros_paths,
    import_legacy_workspace,
)
from tokoboros.gateway.workspace import install_optional_skill_pack

SetupTier = Literal["lightning", "recommended", "enhanced", "power"]
SetupStep = Literal["welcome", "provider", "safety", "memory", "integrations", "advanced", "finish"]


class OnboardingState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    setup_version: int = 1
    selected_tier: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    last_seen_release: str | None = None
    migration_markers: dict[str, str] = Field(default_factory=dict)


class SetupPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: SetupTier
    steps: list[SetupStep] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)
    enable_memory: bool = True
    show_integrations: bool = False
    show_advanced: bool = False


TIER_STEP_PLANS: dict[str, list[SetupStep]] = {
    "lightning": ["welcome", "provider", "safety", "finish"],
    "recommended": ["welcome", "provider", "safety", "memory", "finish"],
    "enhanced": ["welcome", "provider", "safety", "memory", "integrations", "finish"],
    "power": ["welcome", "provider", "safety", "memory", "integrations", "advanced", "finish"],
}


_PROVIDER_KINDS = {
    "anthropic",
    "openai-compatible",
    "openrouter",
    "groq",
    "together",
    "deepseek",
    "ollama",
}
_PERMISSION_TIERS = {"sandbox", "guided", "unrestricted"}

# Providers that require an API key (ollama runs locally and does not).
_KEYLESS_PROVIDERS = {"ollama"}

# Sensible default model + the env var users are most likely to have set, per provider.
# Used by the conversational quickstart so non-technical users don't have to know
# model identifiers.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {"model": "claude-haiku-4-5", "api_key_env": "ANTHROPIC_API_KEY"},
    "openai-compatible": {"model": "gpt-4o-mini", "api_key_env": "OPENAI_API_KEY"},
    "openrouter": {"model": "openai/gpt-4o-mini", "api_key_env": "OPENROUTER_API_KEY"},
    "groq": {"model": "llama-3.3-70b-versatile", "api_key_env": "GROQ_API_KEY"},
    "together": {
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "api_key_env": "TOGETHER_API_KEY",
    },
    "deepseek": {"model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY"},
    "ollama": {"model": "llama3.1", "api_key_env": ""},
}


def default_model_for(kind: str) -> str:
    """Return a reasonable default model id for a provider kind."""
    return _PROVIDER_DEFAULTS.get(kind.strip().lower(), {}).get("model", "")


def default_api_key_env_for(kind: str) -> str:
    """Return the conventional API-key env var name for a provider kind."""
    return _PROVIDER_DEFAULTS.get(kind.strip().lower(), {}).get("api_key_env", "")


class OnboardingService:
    def __init__(self, paths: TokoborosPaths | None = None):
        self.paths = paths or get_tokoboros_paths()
        ensure_directories(self.paths)
        import_legacy_workspace(self.paths)

    def resume_state(self) -> OnboardingState:
        path = self.paths.onboarding_state_file
        if not path.exists():
            return OnboardingState()

        try:
            return OnboardingState.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return OnboardingState()

    def save_state(self, state: OnboardingState) -> Path:
        self.paths.onboarding_state_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.onboarding_state_file.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return self.paths.onboarding_state_file

    def detect_environment(self, project_dir: Path | None = None) -> dict[str, Any]:
        return {
            "anthropic_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "openai_env": bool(os.environ.get("OPENAI_API_KEY")),
            "ollama_cli": shutil.which("ollama") is not None,
            "legacy_workspace_exists": self.paths.legacy_dir.exists(),
            "project_dir": str(project_dir) if project_dir else None,
        }

    def validate_provider(
        self,
        *,
        kind: str,
        api_key_env: str | None = None,
        api_key: str | None = None,
    ) -> tuple[bool, str]:
        """Check that a provider is usable BEFORE the first chat.

        Today a missing/typo'd API key only surfaces when the agent first tries to
        answer — the most common stumbling block for non-technical users. This
        validates the provider kind and that an API key is actually resolvable
        (passed directly, or present in the named/conventional environment
        variable). Local providers (ollama) need no key.

        Returns ``(ok, message)`` where ``message`` is human-friendly guidance.
        """
        kind_n = (kind or "").strip().lower()
        if kind_n not in _PROVIDER_KINDS:
            return False, (
                f"Unknown provider '{kind}'. Choose one of: {', '.join(sorted(_PROVIDER_KINDS))}."
            )
        if kind_n in _KEYLESS_PROVIDERS:
            return True, "Local provider — no API key required."

        env_name = (api_key_env or default_api_key_env_for(kind_n)).strip()
        resolved = api_key or (os.environ.get(env_name) if env_name else None)
        if not resolved:
            return False, (
                f"No API key found for {kind_n}. Set it first, e.g.:\n"
                f"  export {env_name or 'YOUR_API_KEY_ENV'}=sk-...\n"
                "then re-run setup."
            )
        return True, f"API key found in {env_name}."

    def quick_setup(
        self,
        *,
        kind: str,
        model: str | None = None,
        api_key_env: str | None = None,
        permission_tier: str = "guided",
        enable_memory: bool = True,
        project_dir: Path | None = None,
    ) -> SetupPlan:
        """One-call conversational setup: pick provider + safety, persist, done.

        Wraps :meth:`build_tier_plan` + :meth:`apply_setup` with non-technical
        defaults (sensible model per provider, guided safety tier, memory on) so a
        brand-new user is ready after answering a couple of plain questions.
        """
        kind_n = (kind or "").strip().lower()
        if kind_n not in _PROVIDER_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(_PROVIDER_KINDS))}")
        resolved_model = (model or default_model_for(kind_n)).strip()
        resolved_env = (api_key_env or default_api_key_env_for(kind_n)).strip() or None

        answers = {
            "provider": {
                "kind": kind_n,
                "model": resolved_model,
                "api_key_env": resolved_env,
            },
            "permission_tier": permission_tier,
        }
        tier = "recommended" if enable_memory else "lightning"
        plan_payload = self.build_tier_plan(tier, answers)
        return self.apply_setup(plan_payload, project_dir=project_dir)

    def needs_setup(self, project_dir: Path | None = None) -> bool:
        config = load_effective_config(project_dir=project_dir, paths=self.paths)
        state = self.resume_state()

        provider_ready = bool(config.provider.kind and config.provider.model)
        tier_ready = bool(config.permissions.tier)
        completed = set(state.completed_steps)
        is_marked_done = {"provider", "safety"}.issubset(completed)

        return not (provider_ready and tier_ready and is_marked_done)

    def build_tier_plan(self, tier: str, answers: dict[str, Any]) -> dict[str, Any]:
        normalized_tier = str(tier).strip().lower()
        if normalized_tier not in TIER_STEP_PLANS:
            normalized_tier = "lightning"

        return SetupPlan(
            tier=normalized_tier,  # type: ignore[arg-type]
            steps=TIER_STEP_PLANS[normalized_tier],
            answers=dict(answers),
            enable_memory=normalized_tier in {"recommended", "enhanced", "power"},
            show_integrations=normalized_tier in {"enhanced", "power"},
            show_advanced=normalized_tier == "power",
        ).model_dump(mode="python")

    def apply_setup(
        self, plan_payload: dict[str, Any], project_dir: Path | None = None
    ) -> SetupPlan:
        plan = SetupPlan.model_validate(plan_payload)
        config = load_effective_config(project_dir=project_dir, paths=self.paths)

        provider = plan.answers.get("provider", {})
        if isinstance(provider, dict):
            kind = provider.get("kind")
            model = provider.get("model")
            base_url = provider.get("base_url")
            api_key_env = provider.get("api_key_env")

            if kind is not None:
                normalized_kind = str(kind).strip()
                if normalized_kind not in _PROVIDER_KINDS:
                    raise ValueError(
                        "provider.kind must be one of: anthropic, openai-compatible, ollama"
                    )
                config.provider.kind = normalized_kind  # type: ignore[assignment]
            if model is not None:
                normalized_model = str(model).strip()
                if not normalized_model:
                    raise ValueError("provider.model cannot be empty")
                config.provider.model = normalized_model
            if base_url is not None:
                config.provider.base_url = str(base_url).strip() or None
            if api_key_env is not None:
                config.provider.api_key_env = str(api_key_env).strip() or None

        safety_tier = plan.answers.get("permission_tier")
        if safety_tier is not None:
            normalized_tier = str(safety_tier).strip()
            if normalized_tier not in _PERMISSION_TIERS:
                raise ValueError("permission_tier must be one of: sandbox, guided, unrestricted")
            config.permissions.tier = normalized_tier  # type: ignore[assignment]

        memory_answers = plan.answers.get("memory", {})
        if isinstance(memory_answers, dict):
            if "enable_session_history" in memory_answers:
                value = memory_answers["enable_session_history"]
                if not isinstance(value, bool):
                    raise ValueError("memory.enable_session_history must be a boolean")
                config.memory.enable_session_history = value
            if "enable_semantic_memory" in memory_answers:
                value = memory_answers["enable_semantic_memory"]
                if not isinstance(value, bool):
                    raise ValueError("memory.enable_semantic_memory must be a boolean")
                config.memory.enable_semantic_memory = value
        elif plan.enable_memory:
            config.memory.enable_session_history = True
            config.memory.enable_semantic_memory = True

        integrations = plan.answers.get("integrations", {})
        if isinstance(integrations, dict):
            for cap_id, cap_answer in integrations.items():
                if cap_id not in config.capabilities:
                    continue

                cap_state = config.capabilities[cap_id]
                if isinstance(cap_answer, dict):
                    if "enabled" in cap_answer:
                        enabled = cap_answer["enabled"]
                        if not isinstance(enabled, bool):
                            raise ValueError(f"integrations.{cap_id}.enabled must be a boolean")
                        cap_state.enabled = enabled
                    if "configured" in cap_answer:
                        configured = cap_answer["configured"]
                        if not isinstance(configured, bool):
                            raise ValueError(f"integrations.{cap_id}.configured must be a boolean")
                        cap_state.configured = configured
                else:
                    if not isinstance(cap_answer, bool):
                        raise ValueError(f"integrations.{cap_id} must be a boolean")
                    cap_state.enabled = cap_answer

                if cap_state.enabled:
                    install_optional_skill_pack(cap_id)

        write_global_config(config, paths=self.paths)

        state = self.resume_state()
        state.selected_tier = plan.tier
        state.setup_version = max(state.setup_version, 1)
        for step in plan.steps:
            if (
                step in {"provider", "safety", "memory", "integrations", "advanced", "finish"}
                and step not in state.completed_steps
            ):
                state.completed_steps.append(step)
        self.save_state(state)

        return plan
