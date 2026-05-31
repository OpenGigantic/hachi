from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib
from typing import Any, Dict, Optional

import tomlkit

from tokoboros.core.paths import TokoborosPaths, ensure_directories, get_tokoboros_paths, import_legacy_workspace
from tokoboros.core.settings import ResolvedConfig, default_config


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_toml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _legacy_json_as_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    agent = raw.get("agent", {}) if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {
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


def _read_env_file(project_dir: Optional[Path]) -> Dict[str, str]:
    if not project_dir:
        return {}

    env_path = project_dir / ".env"
    if not env_path.exists():
        return {}

    values: Dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_overrides(project_dir: Optional[Path]) -> Dict[str, Any]:
    env_file = _read_env_file(project_dir)

    def env_get(name: str) -> Optional[str]:
        return os.environ.get(name) or env_file.get(name)

    result: Dict[str, Any] = {}

    if env_get("ANTHROPIC_API_KEY"):
        result = _deep_merge(result, {
            "provider": {
                "kind": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
                "model": env_get("TOKOBOROS_MODEL") or "claude-haiku-4-5",
            }
        })

    openai_key = env_get("OPENAI_API_KEY")
    openai_base = env_get("OPENAI_BASE_URL")
    if openai_key or openai_base:
        result = _deep_merge(result, {
            "provider": {
                "kind": "openai-compatible",
                "api_key_env": "OPENAI_API_KEY",
                "base_url": openai_base,
                "model": env_get("OPENAI_MODEL") or "gpt-4.1-mini",
            }
        })

    if env_get("OLLAMA_HOST"):
        result = _deep_merge(result, {
            "provider": {
                "kind": "ollama",
                "base_url": env_get("OLLAMA_HOST"),
                "model": env_get("OLLAMA_MODEL") or "llama3.1:8b",
            }
        })

    if env_get("TOKOBOROS_PERMISSION_TIER"):
        result = _deep_merge(result, {"permissions": {"tier": env_get("TOKOBOROS_PERMISSION_TIER")}})

    if env_get("TOKOBOROS_MAX_TOKENS"):
        try:
            max_tokens = int(env_get("TOKOBOROS_MAX_TOKENS") or "8192")
            result = _deep_merge(result, {"agent": {"max_tokens": max_tokens}})
        except ValueError:
            pass

    return result


def _project_config(project_dir: Optional[Path]) -> Dict[str, Any]:
    if not project_dir:
        return {}
    return _read_toml(project_dir / ".tokoboros" / "settings.toml")


def load_effective_config(
    project_dir: Optional[Path] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
    paths: Optional[TokoborosPaths] = None,
) -> ResolvedConfig:
    paths = paths or get_tokoboros_paths()
    ensure_directories(paths)
    import_legacy_workspace(paths)

    defaults = default_config().model_dump(mode="python")
    global_config = _read_toml(paths.global_config_toml)

    if not global_config and paths.legacy_config_json.exists():
        global_config = _legacy_json_as_config(paths.legacy_config_json)

    project = _project_config(project_dir)
    env = _env_overrides(project_dir)
    cli = cli_overrides or {}

    merged = defaults
    for layer in (global_config, project, env, cli):
        merged = _deep_merge(merged, layer)

    return ResolvedConfig.model_validate(merged)


def write_global_config(config: ResolvedConfig, paths: Optional[TokoborosPaths] = None) -> Path:
    paths = paths or get_tokoboros_paths()
    ensure_directories(paths)

    if paths.global_config_toml.exists():
        doc = tomlkit.parse(paths.global_config_toml.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()
        doc.add(tomlkit.comment("Tokoboros user settings"))

    payload = config.model_dump(mode="python", exclude_none=True)
    for key, value in payload.items():
        doc[key] = value

    paths.global_config_toml.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return paths.global_config_toml
