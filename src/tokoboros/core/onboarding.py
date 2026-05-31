from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from tokoboros.core.config_loader import load_effective_config
from tokoboros.core.config_loader import write_global_config
from tokoboros.core.paths import TokoborosPaths, ensure_directories, get_tokoboros_paths, import_legacy_workspace
from tokoboros.gateway.workspace import install_optional_skill_pack


SetupTier = Literal["lightning", "recommended", "enhanced", "power"]
SetupStep = Literal["welcome", "provider", "safety", "memory", "integrations", "advanced", "finish"]


class OnboardingState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    setup_version: int = 1
    selected_tier: Optional[str] = None
    completed_steps: List[str] = Field(default_factory=list)
    last_seen_release: Optional[str] = None
    migration_markers: Dict[str, str] = Field(default_factory=dict)


class SetupPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: SetupTier
    steps: List[SetupStep] = Field(default_factory=list)
    answers: Dict[str, Any] = Field(default_factory=dict)
    enable_memory: bool = True
    show_integrations: bool = False
    show_advanced: bool = False


TIER_STEP_PLANS: Dict[str, List[SetupStep]] = {
    "lightning": ["welcome", "provider", "safety", "finish"],
    "recommended": ["welcome", "provider", "safety", "memory", "finish"],
    "enhanced": ["welcome", "provider", "safety", "memory", "integrations", "finish"],
    "power": ["welcome", "provider", "safety", "memory", "integrations", "advanced", "finish"],
}


class OnboardingService:
    def __init__(self, paths: Optional[TokoborosPaths] = None):
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

    def detect_environment(self, project_dir: Optional[Path] = None) -> Dict[str, Any]:
        return {
            "anthropic_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "openai_env": bool(os.environ.get("OPENAI_API_KEY")),
            "ollama_cli": shutil.which("ollama") is not None,
            "legacy_workspace_exists": self.paths.legacy_dir.exists(),
            "project_dir": str(project_dir) if project_dir else None,
        }

    def needs_setup(self, project_dir: Optional[Path] = None) -> bool:
        config = load_effective_config(project_dir=project_dir, paths=self.paths)
        state = self.resume_state()

        provider_ready = bool(config.provider.kind and config.provider.model)
        tier_ready = bool(config.permissions.tier)
        completed = set(state.completed_steps)
        is_marked_done = {"provider", "safety"}.issubset(completed)

        return not (provider_ready and tier_ready and is_marked_done)

    def build_tier_plan(self, tier: str, answers: Dict[str, Any]) -> Dict[str, Any]:
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

    def apply_setup(self, plan_payload: Dict[str, Any], project_dir: Optional[Path] = None) -> SetupPlan:
        plan = SetupPlan.model_validate(plan_payload)
        config = load_effective_config(project_dir=project_dir, paths=self.paths)

        provider = plan.answers.get("provider", {})
        if isinstance(provider, dict):
            kind = provider.get("kind")
            model = provider.get("model")
            base_url = provider.get("base_url")
            api_key_env = provider.get("api_key_env")

            if kind:
                config.provider.kind = str(kind)  # type: ignore[assignment]
            if model:
                config.provider.model = str(model)
            if base_url is not None:
                config.provider.base_url = str(base_url)
            if api_key_env is not None:
                config.provider.api_key_env = str(api_key_env)

        safety_tier = plan.answers.get("permission_tier")
        if safety_tier:
            config.permissions.tier = str(safety_tier)  # type: ignore[assignment]

        memory_answers = plan.answers.get("memory", {})
        if isinstance(memory_answers, dict):
            if "enable_session_history" in memory_answers:
                config.memory.enable_session_history = bool(memory_answers["enable_session_history"])
            if "enable_semantic_memory" in memory_answers:
                config.memory.enable_semantic_memory = bool(memory_answers["enable_semantic_memory"])
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
                    cap_state.enabled = bool(cap_answer.get("enabled", cap_state.enabled))
                    cap_state.configured = bool(cap_answer.get("configured", cap_state.configured))
                else:
                    cap_state.enabled = bool(cap_answer)

                if cap_state.enabled:
                    install_optional_skill_pack(cap_id)

        write_global_config(config, paths=self.paths)

        state = self.resume_state()
        state.selected_tier = plan.tier
        state.setup_version = max(state.setup_version, 1)
        for step in plan.steps:
            if step in {"provider", "safety", "memory", "integrations", "advanced", "finish"} and step not in state.completed_steps:
                state.completed_steps.append(step)
        self.save_state(state)

        return plan
