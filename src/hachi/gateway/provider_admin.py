"""Provider admin — pure config-layer functions for multi-provider management.

Exposed via GatewayControlPlane injected handlers; called by FastAPI routes.
All persistence goes through load_effective_config / write_global_config (no
direct file I/O here).  Secrets are stored via CredentialVault, never in the
config file.
"""

from __future__ import annotations

import logging
from typing import Any, get_args

from hachi.core.config_loader import load_effective_config, write_global_config
from hachi.core.credential_vault import CredentialVault
from hachi.core.routing.capability import Capability
from hachi.core.routing.descriptor import BUNDLED_MODEL_DEFAULTS
from hachi.core.routing.policy import RoutingPolicy
from hachi.core.secret_store import create_default_secret_store
from hachi.core.settings import ProviderKind, ProviderSettings, RoutingSettings

logger = logging.getLogger(__name__)

# All valid provider kinds (derived from the Literal type so settings.py stays canonical)
_VALID_KINDS: frozenset[str] = frozenset(get_args(ProviderKind))

# Capability → human-friendly UI label
CAPABILITY_LABELS: dict[str, str] = {
    Capability.REASONING: "Chat / reasoning",
    Capability.CODING: "Code",
    Capability.VISION: "Image analysis",
    Capability.TITLE_GENERATION: "Conversation titles",
    Capability.COMPACTION: "Memory compaction",
    Capability.SUMMARIZATION: "Summarize",
    Capability.LONG_CONTEXT: "Long context",
    Capability.SPEECH_TO_TEXT: "Speech to text",
    Capability.TEXT_TO_SPEECH: "Text to speech",
    Capability.WEB_SEARCH: "Web search",
    Capability.EMBEDDINGS: "Embeddings",
    Capability.REALTIME: "Realtime",
}

# Ordered list so the UI always renders capabilities in the same order
CAPABILITY_ORDER: list[str] = list(CAPABILITY_LABELS.keys())


def _mask_provider(name: str, ps: ProviderSettings) -> dict[str, Any]:
    """Serialize a ProviderSettings to a safe dict — secrets masked."""
    return {
        "name": name,
        "kind": ps.kind,
        "model": ps.model,
        "base_url": ps.base_url,
        "allowed_models": list(ps.allowed_models),
        "headers": dict(ps.headers),
        "configured": bool(ps.api_key_ref or ps.api_key_env),
        # Never expose raw keys or ref strings — only the configured boolean
    }


# ---------------------------------------------------------------------------
# list_providers
# ---------------------------------------------------------------------------


def list_providers() -> dict[str, Any]:
    """Return all named providers from config, secrets masked.

    Returns:
        Dict with ``providers`` list (each entry: name, kind, model, base_url,
        allowed_models, headers, configured).
    """
    config = load_effective_config()
    items: list[dict[str, Any]] = []

    # Primary provider always present
    items.append(_mask_provider("__primary__", config.provider))

    for name, ps in (config.providers or {}).items():
        items.append(_mask_provider(name, ps))

    return {"providers": items}


# ---------------------------------------------------------------------------
# add_or_update_provider
# ---------------------------------------------------------------------------


def add_or_update_provider(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Upsert config.providers[name].

    Args:
        name: Logical provider name (must be non-empty; ``__primary__`` updates the
              primary provider instead of the named dict).
        spec: Dict with keys: kind, model, base_url, allowed_models, headers,
              api_key (raw secret — stored in vault, never config file).

    Returns:
        The masked provider entry after write.

    Raises:
        ValueError: Invalid kind or empty name.
    """
    name = name.strip()
    if not name:
        raise ValueError("provider name must be non-empty")

    kind = str(spec.get("kind") or "anthropic").strip().lower()
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"provider.kind must be one of: {', '.join(sorted(_VALID_KINDS))}; got {kind!r}"
        )

    model = str(spec.get("model") or "").strip()
    base_url: str | None = str(spec.get("base_url") or "").strip() or None
    allowed_models: list[str] = [
        str(m).strip() for m in (spec.get("allowed_models") or []) if str(m).strip()
    ]
    headers: dict[str, str] = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
    raw_api_key: str = str(spec.get("api_key") or "").strip()

    config = load_effective_config()

    # Determine the vault key name for this provider's secret
    vault_key = f"provider.{name}.api_key"
    api_key_ref: str | None = None

    if raw_api_key:
        # Store secret in vault + set as env var for the running process
        vault = CredentialVault(secret_store=create_default_secret_store())
        vault.set_token(vault_key, raw_api_key)
        import os

        os.environ[vault_key] = raw_api_key
        api_key_ref = vault_key

    # Build the new ProviderSettings
    ps = ProviderSettings(
        kind=kind,
        model=model or _default_model_for_kind(kind),
        base_url=base_url,
        allowed_models=allowed_models,
        headers=headers,
    )
    if api_key_ref is not None:
        ps.api_key_ref = api_key_ref
    elif name != "__primary__" and config.providers.get(name) is not None:
        # Preserve existing api_key_ref when no new key supplied
        existing = config.providers[name]
        ps.api_key_ref = existing.api_key_ref
        ps.api_key_env = existing.api_key_env
    elif name == "__primary__":
        if not api_key_ref:
            ps.api_key_ref = config.provider.api_key_ref
            ps.api_key_env = config.provider.api_key_env

    if name == "__primary__":
        config.provider = ps
    else:
        if config.providers is None:
            config.providers = {}
        config.providers[name] = ps

    write_global_config(config)
    return _mask_provider(name, ps)


# ---------------------------------------------------------------------------
# remove_provider
# ---------------------------------------------------------------------------


def remove_provider(name: str) -> dict[str, Any]:
    """Delete a named provider from config (primary provider cannot be removed).

    Args:
        name: The provider name to remove.

    Returns:
        ``{"ok": True, "name": name}`` on success.

    Raises:
        ValueError: If name is ``__primary__`` or not found.
    """
    name = name.strip()
    if name == "__primary__":
        raise ValueError("the primary provider cannot be removed")

    config = load_effective_config()
    if name not in (config.providers or {}):
        raise ValueError(f"provider {name!r} not found")

    ps = config.providers.pop(name)

    # Best-effort vault cleanup
    vault_key = f"provider.{name}.api_key"
    try:
        vault = CredentialVault(secret_store=create_default_secret_store())
        if ps.api_key_ref == vault_key:
            vault.delete_token(vault_key)
    except Exception:
        pass

    write_global_config(config)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------------------
# get_routing
# ---------------------------------------------------------------------------


def get_routing() -> dict[str, Any]:
    """Return the routing config, synthesizing defaults when [routing] is absent.

    Always returns all 12 capabilities so the UI always has a complete matrix.

    Returns:
        Dict: ``{default_model, capabilities: [{name, label, models}]}``.
    """
    config = load_effective_config()
    policy = RoutingPolicy.from_config(config)

    rows: list[dict[str, Any]] = []
    for cap_name in CAPABILITY_ORDER:
        try:
            cap = Capability(cap_name)
        except ValueError:
            continue
        models = policy.candidates(cap)
        rows.append(
            {
                "name": cap_name,
                "label": CAPABILITY_LABELS.get(cap_name, cap_name),
                "models": models,
            }
        )

    return {
        "default_model": policy.default_model,
        "capabilities": rows,
    }


# ---------------------------------------------------------------------------
# set_routing
# ---------------------------------------------------------------------------


def set_routing(default_model: str, capabilities: dict[str, list[str]]) -> dict[str, Any]:
    """Persist the routing map to [routing] config section.

    Args:
        default_model: The default / fallback model id.
        capabilities: Mapping of capability name → ordered model-id list.

    Returns:
        The new get_routing() snapshot.
    """
    config = load_effective_config()

    # Validate capability keys
    valid_cap_names = {c.value for c in Capability}
    filtered: dict[str, list[str]] = {}
    for cap_name, models in (capabilities or {}).items():
        if cap_name not in valid_cap_names:
            logger.warning("set_routing: unknown capability %r — skipped", cap_name)
            continue
        filtered[cap_name] = [str(m).strip() for m in models if str(m).strip()]

    if config.routing is None:
        config.routing = RoutingSettings()

    config.routing.default_model = str(default_model).strip() or config.routing.default_model
    config.routing.capabilities = filtered

    write_global_config(config)
    return get_routing()


# ---------------------------------------------------------------------------
# list_all_models
# ---------------------------------------------------------------------------


def list_all_models() -> dict[str, Any]:
    """Return model ids available per configured provider.

    Uses allowed_models when set; otherwise falls back to BUNDLED_MODEL_DEFAULTS
    for anthropic / openai, or an empty list for unknown providers.

    Returns:
        Dict: ``{provider_name: [model_id, ...]}``.
    """
    config = load_effective_config()

    # Build bundled fallback index keyed by provider name
    bundled_by_provider: dict[str, list[str]] = {}
    for desc in BUNDLED_MODEL_DEFAULTS:
        bundled_by_provider.setdefault(desc.provider, []).append(desc.id)

    result: dict[str, list[str]] = {}

    def _models_for(name: str, ps: ProviderSettings) -> list[str]:
        if ps.allowed_models:
            return list(ps.allowed_models)
        kind = str(ps.kind).lower()
        # Map openai-compat kinds to bundled "openai" key
        if kind in {"openai-compatible", "openai"}:
            return list(bundled_by_provider.get("openai", []))
        return list(bundled_by_provider.get(kind, []))

    result["__primary__"] = _models_for("__primary__", config.provider)
    for pname, ps in (config.providers or {}).items():
        result[pname] = _models_for(pname, ps)

    return result


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


def test_connection(name: str) -> dict[str, Any]:
    """Best-effort connectivity test for a configured provider.

    Uses a short timeout; never raises — returns ``{ok, detail}``.

    Args:
        name: Provider name (``__primary__`` for the primary provider).

    Returns:
        ``{"ok": bool, "detail": str}``.
    """
    config = load_effective_config()

    if name == "__primary__":
        ps = config.provider
    else:
        ps = (config.providers or {}).get(name)
        if ps is None:
            return {"ok": False, "detail": f"provider {name!r} not configured"}

    kind = str(ps.kind).lower()
    base_url = ps.base_url or ""

    try:
        import urllib.request

        if kind == "ollama":
            url = (base_url.rstrip("/") or "http://localhost:11434") + "/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
            return {"ok": status == 200, "detail": f"HTTP {status}"}

        elif kind == "anthropic":
            # Resolve API key
            api_key = _resolve_key(ps)
            if not api_key:
                return {"ok": False, "detail": "no API key configured"}
            url = "https://api.anthropic.com/v1/models"
            req = urllib.request.Request(
                url,
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                status = resp.status
            return {"ok": status == 200, "detail": f"HTTP {status}"}

        else:
            # OpenAI-compatible: GET {base_url}/models with Authorization header
            api_key = _resolve_key(ps)
            if not api_key and kind not in {"ollama"}:
                return {"ok": False, "detail": "no API key configured"}
            endpoint = (base_url.rstrip("/") or "https://api.openai.com/v1") + "/models"
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            headers.update(ps.headers or {})
            req = urllib.request.Request(endpoint, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                status = resp.status
            return {"ok": status == 200, "detail": f"HTTP {status}"}

    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_key(ps: ProviderSettings) -> str | None:
    """Resolve the API key for a ProviderSettings from vault or env, or None."""
    import os

    # api_key_ref may point to the vault OR be an env-var name
    ref = ps.api_key_ref or ps.api_key_env or ""
    if ref:
        val = os.environ.get(ref)
        if val:
            return val
        # Try vault
        try:
            vault = CredentialVault(secret_store=create_default_secret_store())
            token = vault.get_token(ref)
            return str(token) if token is not None else None
        except Exception:
            return None
    return None


def _default_model_for_kind(kind: str) -> str:
    _DEFAULTS: dict[str, str] = {
        "anthropic": "claude-haiku-4-5",
        "openai-compatible": "gpt-4o-mini",
        "openrouter": "openai/gpt-4o-mini",
        "groq": "llama-3.1-8b-instant",
        "together": "meta-llama/Llama-3-8b-chat-hf",
        "deepseek": "deepseek-chat",
        "ollama": "llama3.1:8b",
    }
    return _DEFAULTS.get(kind.lower(), "")
