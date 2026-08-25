"""T-44 stage: load config + brain, and derive the settings sub-sections boot needs early.

Moved verbatim from `boot_agent`'s old step "1. Load Local Configuration and
Brain" (`:2702-2704`), the provider/model/max_tokens derivation (`:2736-2740`),
and the events/jobs/observability settings parsing (`:2749-2764`, including the
`_apply_observability_settings` side-effecting call). The original grouped one
`from hachi.core.settings import (...)` statement across five names; three of
them (`EvalReportSettings`, `MemoryGardenerSettings`, `RetentionSettings`) are
read only by the "create session onward" tail that stays inline in
`boot_agent` (see the package docstring) -- splitting the import into what each
call site actually uses is behaviour-neutral (import statements are idempotent).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hachi.core.settings import EventsSettings, JobsSettings, ObservabilitySettings

from .stages import ConfigStage

ApplyObservabilitySettings = Callable[[ObservabilitySettings, EventsSettings], None]


def build_config_and_brain(
    *,
    load_config: Callable[[], dict[str, Any]],
    load_brain: Callable[[], str],
    apply_observability_settings: ApplyObservabilitySettings,
) -> ConfigStage:
    """Old step 1 plus its settings-parsing tail, moved verbatim.

    Args:
        load_config: `hachi.gateway.workspace.load_config`, passed in by
            `boot_agent` (already one of its own top-level imports).
        load_brain: `hachi.gateway.workspace.load_brain`, likewise.
        apply_observability_settings: orchestrator-local
            `_apply_observability_settings`, passed in by `boot_agent` --
            see the package docstring for why this direction is deliberate.
    """
    config = load_config()
    brain_prompt = load_brain()

    provider_config = config.get("provider", {})
    model_name = provider_config.get("model") or config.get("agent", {}).get(
        "model", "claude-haiku-4-5"
    )
    max_tokens = config.get("agent", {}).get("max_tokens", 8192)

    events_cfg = EventsSettings.model_validate(config.get("events", {}))
    jobs_cfg = JobsSettings.model_validate(config.get("jobs", {}))

    # Phase 0 / BB-73: wire [observability] config into tracing + metrics.
    obs_cfg = ObservabilitySettings.model_validate(config.get("observability", {}))
    apply_observability_settings(obs_cfg, events_cfg)

    return ConfigStage(
        config=config,
        brain_prompt=brain_prompt,
        provider_config=provider_config,
        model_name=model_name,
        max_tokens=max_tokens,
        events_cfg=events_cfg,
        jobs_cfg=jobs_cfg,
        obs_cfg=obs_cfg,
    )
