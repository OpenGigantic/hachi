from __future__ import annotations

import json
import logging
import os
import tomllib
from pathlib import Path
from typing import Any

import tomlkit

from tokoboros.core.paths import (
    TokoborosPaths,
    ensure_directories,
    get_tokoboros_paths,
    import_legacy_workspace,
)
from tokoboros.core.settings import ResolvedConfig, default_config

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        # A corrupt config must not crash every command (config/doctor/etc.);
        # fall back to defaults so the user can re-run setup.
        logger.warning("Ignoring malformed config %s: %s", path, exc)
        return {}


def _legacy_json_as_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    agent = raw.get("agent", {}) if isinstance(raw, dict) else {}
    out: dict[str, Any] = {
        "meta": {"imported_from_legacy": True},
        "agent": {
            "max_tokens": int(agent.get("max_tokens", 8192)),
            "temperature": float(agent.get("temperature", 0.0)),
        },
        "provider": {
            "kind": "anthropic",
            "model": str(agent.get("model", "claude-haiku-4-5")),
            "api_key_env": "ANTHROPIC_API_KEY",
        },
    }
    return out


def _read_env_file(project_dir: Path | None) -> dict[str, str]:
    if not project_dir:
        return {}

    env_path = project_dir / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_overrides(
    project_dir: Path | None,
    base_provider: dict[str, Any] | None = None,
    *,
    has_explicit_provider: bool = False,
) -> dict[str, Any]:
    env_file = _read_env_file(project_dir)
    current_provider = dict(base_provider or {})
    base_kind = str(current_provider.get("kind", "")).strip().lower()

    def env_get(name: str) -> str | None:
        return os.environ.get(name) or env_file.get(name)

    result: dict[str, Any] = {}

    # When the user has written an explicit provider choice to their config file,
    # honour it — don't let env-var auto-detection override it.  Only
    # TOKOBOROS_MODEL is still respected as an explicit per-run override.
    if has_explicit_provider:
        explicit_model = env_get("TOKOBOROS_MODEL")
        if explicit_model:
            result = _deep_merge(result, {"provider": {"model": explicit_model}})
    else:
        has_anthropic = bool(env_get("ANTHROPIC_API_KEY"))
        openai_key = env_get("OPENAI_API_KEY")
        openai_base = env_get("OPENAI_BASE_URL")
        has_openai = bool(openai_key or openai_base)
        has_ollama = bool(env_get("OLLAMA_HOST"))

        if has_anthropic:
            provider_payload: dict[str, Any] = {
                "kind": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
            }
            explicit_model = env_get("TOKOBOROS_MODEL")
            if explicit_model:
                provider_payload["model"] = explicit_model
            elif not (has_openai or has_ollama) and base_kind != "anthropic":
                provider_payload["model"] = "claude-haiku-4-5"
            result = _deep_merge(result, {"provider": provider_payload})

        if has_openai:
            provider_payload = {
                "kind": "openai-compatible",
                "api_key_env": "OPENAI_API_KEY",
            }
            if openai_base:
                provider_payload["base_url"] = openai_base
            explicit_model = env_get("OPENAI_MODEL")
            if explicit_model:
                provider_payload["model"] = explicit_model
            elif not has_ollama and base_kind != "openai-compatible":
                provider_payload["model"] = "gpt-4.1-mini"
            result = _deep_merge(result, {"provider": provider_payload})

        if has_ollama:
            provider_payload = {
                "kind": "ollama",
                "base_url": env_get("OLLAMA_HOST"),
            }
            explicit_model = env_get("OLLAMA_MODEL")
            if explicit_model:
                provider_payload["model"] = explicit_model
            elif base_kind != "ollama":
                provider_payload["model"] = "llama3.1:8b"
            result = _deep_merge(result, {"provider": provider_payload})

    if env_get("TOKOBOROS_PERMISSION_TIER"):
        result = _deep_merge(
            result, {"permissions": {"tier": env_get("TOKOBOROS_PERMISSION_TIER")}}
        )

    if env_get("TOKOBOROS_MAX_TOKENS"):
        try:
            max_tokens = int(env_get("TOKOBOROS_MAX_TOKENS") or "8192")
            result = _deep_merge(result, {"agent": {"max_tokens": max_tokens}})
        except ValueError:
            pass

    return result


def _project_config(project_dir: Path | None) -> dict[str, Any]:
    if not project_dir:
        return {}
    return _read_toml(project_dir / ".tokoboros" / "settings.toml")


def load_effective_config(
    project_dir: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    paths: TokoborosPaths | None = None,
) -> ResolvedConfig:
    paths = paths or get_tokoboros_paths()
    ensure_directories(paths)
    import_legacy_workspace(paths)

    defaults = default_config().model_dump(mode="python")
    global_config = _read_toml(paths.global_config_toml)

    if not global_config and paths.legacy_config_json.exists():
        global_config = _legacy_json_as_config(paths.legacy_config_json)

    project = _project_config(project_dir)
    pre_env = _deep_merge(_deep_merge(defaults, global_config), project)
    # Detect whether the user has explicitly written a provider kind to their
    # config files.  When true, env-var auto-detection must not override it.
    has_explicit_provider = bool(
        global_config.get("provider", {}).get("kind") or project.get("provider", {}).get("kind")
    )
    env = _env_overrides(
        project_dir,
        base_provider=pre_env.get("provider", {}),
        has_explicit_provider=has_explicit_provider,
    )
    cli = cli_overrides or {}

    merged = defaults
    for layer in (global_config, project, env, cli):
        merged = _deep_merge(merged, layer)

    return ResolvedConfig.model_validate(merged)


def write_global_config(config: ResolvedConfig, paths: TokoborosPaths | None = None) -> Path:
    paths = paths or get_tokoboros_paths()
    ensure_directories(paths)

    if paths.global_config_toml.exists():
        doc = tomlkit.parse(paths.global_config_toml.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
        doc.add(tomlkit.comment("Tokoboros user settings"))

    payload = config.model_dump(mode="python", exclude_none=True)
    # Don't write an empty providers table — it's the default and would clutter the file.
    if not payload.get("providers"):
        payload.pop("providers", None)
    # Same for channels: only persist once a channel actually has configuration
    # (e.g. a Telegram allowlist), otherwise it just adds an empty table.
    channels = payload.get("channels") or {}
    if not channels.get("telegram", {}).get("allowlist"):
        payload.pop("channels", None)
    for key, value in payload.items():
        doc[key] = value

    # Atomic write: serialize to a temp file then os.replace, so a crash mid-write
    # can't truncate settings.toml (which would silently reset the user's config).
    target = paths.global_config_toml
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, target)
    return target
