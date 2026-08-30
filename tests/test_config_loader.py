from __future__ import annotations

import tomllib
import types
import typing
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hachi.core.config_loader import load_effective_config
from hachi.core.paths import HachiPaths
from hachi.core.settings import ResolvedConfig
from tests.conftest import REPO_ROOT


def _paths(tmp_path: Path) -> HachiPaths:
    return HachiPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
    )


def test_config_precedence_explicit_provider_wins_over_env(monkeypatch, tmp_path: Path) -> None:
    """Explicit provider.kind in the config file must not be overridden by env vars.

    The old behaviour (env always won) caused bug reports where switching from
    OpenAI → Anthropic in the setup wizard had no effect while OPENAI_API_KEY
    was still present in the environment.
    """
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    (paths.config_dir / "settings.toml").write_text(
        """
[provider]
kind = "anthropic"
model = "global-model"
""",
        encoding="utf-8",
    )

    project_dir = tmp_path / "project"
    (project_dir / ".hachi").mkdir(parents=True, exist_ok=True)
    (project_dir / ".hachi" / "settings.toml").write_text(
        """
[provider]
model = "project-model"
""",
        encoding="utf-8",
    )
    (project_dir / ".env").write_text(
        "OPENAI_API_KEY=testkey\nOPENAI_MODEL=env-model\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    cfg = load_effective_config(
        project_dir=project_dir,
        cli_overrides={"provider": {"model": "cli-model"}},
        paths=paths,
    )

    # Explicit config kind must survive; CLI model override must still apply.
    assert cfg.provider.kind == "anthropic"
    assert cfg.provider.model == "cli-model"


def test_config_precedence_env_auto_detects_without_explicit_config(
    monkeypatch, tmp_path: Path
) -> None:
    """When no config file has been written, env vars auto-detect the provider."""
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    project_dir = tmp_path / "project"
    (project_dir / ".hachi").mkdir(parents=True, exist_ok=True)
    (project_dir / ".env").write_text("OPENAI_API_KEY=testkey\n", encoding="utf-8")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cfg = load_effective_config(project_dir=project_dir, paths=paths)

    # No explicit config → env-var detection should kick in.
    assert cfg.provider.kind == "openai-compatible"


def test_env_numeric_parse_failure_does_not_override_agent_tokens(
    monkeypatch, tmp_path: Path
) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HACHI_MAX_TOKENS", "not-a-number")

    cfg = load_effective_config(paths=paths)

    assert cfg.agent.max_tokens == 8192


def test_env_file_with_malformed_quotes_is_tolerated(monkeypatch, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".env").write_text(
        'OPENAI_API_KEY="unterminated\nOPENAI_MODEL=env-model\n',
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    cfg = load_effective_config(project_dir=project_dir, paths=paths)

    assert cfg.provider.kind == "openai-compatible"
    assert cfg.provider.model == "env-model"


def test_openai_default_model_is_catalog_member(monkeypatch, tmp_path: Path) -> None:
    """config_loader must not default to a model id that is absent from the registry.

    gpt-4.1-mini is not in BUNDLED_MODEL_DEFAULTS, so using it as the fallback model
    breaks cost tracking and model-selection UI (Bug 1).
    """
    from hachi.core.routing.registry import ModelRegistry

    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    cfg = load_effective_config(paths=paths)
    default_model = cfg.provider.model

    reg = ModelRegistry.build(object(), None)
    assert reg.get(default_model) is not None, (
        f"Default model {default_model!r} is not in ModelRegistry catalog — "
        "cost tracking and model-selection UI will be broken"
    )


def test_openai_key_env_does_not_reset_existing_openai_model(monkeypatch, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    (paths.config_dir / "settings.toml").write_text(
        """
[provider]
kind = "openai-compatible"
model = "gpt-5.4-nano"
api_key_env = "OPENAI_API_KEY"
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    cfg = load_effective_config(paths=paths)

    assert cfg.provider.kind == "openai-compatible"
    assert cfg.provider.model == "gpt-5.4-nano"


# ---------------------------------------------------------------------------
# BB-92 — settings.example.toml must be valid TOML that round-trips through
# the real ResolvedConfig schema. It is the file the repo tells every new
# user to copy; a parse failure is swallowed by _read_toml(primary=True)
# (logger.error, returns {}) so the user's whole config silently no-ops.
# ---------------------------------------------------------------------------

_EXAMPLE_TOML = REPO_ROOT / "settings.example.toml"

# These sections are open-ended dicts whose keys are chosen by the user
# (provider names, capability names, MCP server names) — their children must
# not be checked against a fixed field list.
_OPEN_DICT_SECTIONS = {"providers", "capabilities", "mcp_servers"}


def test_settings_example_toml_parses() -> None:
    """The file the repo's own header tells users to copy must be valid TOML.

    A malformed PRIMARY settings file is swallowed by _read_toml
    (core/config_loader.py:130-145): it logs logger.error and returns {}, so
    Hachi keeps running silently on built-in defaults with the user's entire
    file ignored — no crash, no non-zero exit code.
    """
    tomllib.loads(_EXAMPLE_TOML.read_text(encoding="utf-8"))  # raises TOMLDecodeError if broken


def _model_for(annotation: object) -> type[BaseModel] | None:
    """Unwrap an ``X | None`` annotation and return X when it is a pydantic model.

    ``routing`` and ``voice`` on ResolvedConfig are ``SomeSettings | None = None``
    with no default factory, so an *instance* has ``None`` for those fields.
    Resolving from the annotation (rather than an instance) is what lets the
    recursion below reach into them.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            found = _model_for(arg)
            if found is not None:
                return found
    return None


def _unknown_keys(parsed: dict[str, Any], model: type[BaseModel], prefix: str = "") -> list[str]:
    """Recursively collect keys in *parsed* that are not real fields on *model*.

    Every pydantic model in hachi.core.settings sets ``extra="ignore"``, so
    ``ResolvedConfig.model_validate(parsed)`` would silently accept — and then
    silently drop — an unknown key instead of raising. This walks
    ``model_fields`` by hand so unknown/typo'd/renamed keys are caught.
    """
    bad: list[str] = []
    fields = model.model_fields
    for key, value in parsed.items():
        path = f"{prefix}{key}"
        if key not in fields:
            bad.append(path)
            continue
        if key in _OPEN_DICT_SECTIONS:
            continue
        nested = _model_for(fields[key].annotation)
        if nested is not None and isinstance(value, dict):
            bad.extend(_unknown_keys(value, nested, prefix=f"{path}."))
    return bad


def test_settings_example_toml_keys_are_known_config_fields() -> None:
    """Every key documented in settings.example.toml must be a real ResolvedConfig field.

    Walks model_fields by hand rather than through model_validate(): ResolvedConfig
    and all of its nested section models set extra="ignore", so model_validate()
    accepts anything and asserts nothing here (verified empirically — see
    docs/audit/2026-07-19/tasks/T-02-settings-example-toml.md §7). This is what
    would catch, e.g., a documented-but-unwired config section like eval_reports.
    """
    parsed = tomllib.loads(_EXAMPLE_TOML.read_text(encoding="utf-8"))
    unknown = _unknown_keys(parsed, ResolvedConfig)
    assert not unknown, f"settings.example.toml documents keys Hachi ignores: {unknown}"
