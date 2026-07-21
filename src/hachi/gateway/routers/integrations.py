"""``/integrations/*`` -- OAuth2 connector inventory + lifecycle (T-47).

Does not include ``GET /integrations/oauth/callback`` -- see
``routers/integrations_callback.py`` -- that route is intentionally
unauthenticated (the provider redirects the user's browser here directly).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anyio
from fastapi import APIRouter, Depends

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, _error_response

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("")
async def get_integrations() -> Any:
    """List all connectors with their auth_type, configured, connected, and status fields.

    Issue 5: the Tavily/search connector entry also carries ``fallback_active``
    (True when no web_search provider is registered, so browse_web/Playwright is the
    active fallback). The frontend reads this off GET /integrations to show a
    "web search unavailable — using Playwright fallback" notice. Computed here so
    GET /integrations and the POST /credentials response agree.
    """
    from hachi.integrations.oauth_flow import all_connector_statuses

    statuses = await anyio.to_thread.run_sync(all_connector_statuses)

    fallback_active = True
    try:
        from hachi.gateway.shared_services import ProcessSharedServices

        _shared = ProcessSharedServices.instance()
        if _shared is not None:
            _pr = getattr(_shared, "provider_registry", None)
            if _pr is not None and hasattr(_pr, "get_search"):
                fallback_active = _pr.get_search() is None
    except Exception:
        pass

    for entry in statuses:
        if isinstance(entry, dict) and entry.get("id") == "tavily":
            entry["fallback_active"] = fallback_active
    return statuses


@router.post("/{connector_id}/oauth/start")
async def post_oauth_start(connector_id: str) -> Any:
    """Begin the OAuth2 flow for the given connector.

    Returns ``{"authorization_url": "<provider-authorize-url>"}`` so the
    frontend can open it in a popup window.

    Returns HTTP 404 for unknown connectors, HTTP 409 when the connector's
    client credentials (env vars) are not configured on this machine.
    """
    import re

    from hachi.integrations.oauth_flow import oauth_start

    if not re.match(r"^[a-z0-9_-]+$", connector_id):
        return _error_response(400, "invalid_connector_id", "Connector id is not valid")

    try:
        result = oauth_start(connector_id)
    except ValueError as exc:
        return _error_response(404, "unknown_connector", str(exc))
    except LookupError as exc:
        return _error_response(
            409,
            "credentials_not_configured",
            str(exc),
        )
    return result


@router.post("/{connector_id}/disconnect")
async def post_disconnect(connector_id: str) -> Any:
    """Revoke and delete stored OAuth2 tokens for a connector.

    Returns ``{"connected": false, "connector_id": "<id>", ...status...}``.
    Returns HTTP 404 for unknown connectors.
    """
    import re

    from hachi.integrations.connector_registry import get_connector
    from hachi.integrations.oauth_flow import connector_status, oauth_disconnect

    if not re.match(r"^[a-z0-9_-]+$", connector_id):
        return _error_response(400, "invalid_connector_id", "Connector id is not valid")

    connector = get_connector(connector_id)
    if connector is None:
        return _error_response(404, "unknown_connector", f"Unknown connector: {connector_id!r}")

    result = await anyio.to_thread.run_sync(lambda: oauth_disconnect(connector_id))
    status_info = connector_status(connector_id)
    return {**result, **status_info}


@router.post("/{connector_id}/credentials")
async def post_credentials(connector_id: str, payload: dict[str, Any], cp: ControlPlaneDep) -> Any:
    """Store apikey credentials for a connector.

    Body: ``{"fields": {"<key>": "<value>", ...}}``

    Only keys declared in the connector's ``credential_fields`` are accepted.
    Returns the updated connector status dict (same shape as ``GET /integrations``
    entries) so the frontend can update the card immediately without re-fetching.

    Returns HTTP 400 for invalid connector id format.
    Returns HTTP 404 for unknown connectors.
    Returns HTTP 422 for undeclared field keys or empty values.
    """
    import re

    from hachi.integrations.connector_registry import get_connector
    from hachi.integrations.oauth_flow import (
        connector_status,
        store_apikey_credential,
    )

    if not re.match(r"^[a-z0-9_-]+$", connector_id):
        return _error_response(400, "invalid_connector_id", "Connector id is not valid")

    connector = get_connector(connector_id)
    if connector is None:
        return _error_response(404, "unknown_connector", f"Unknown connector: {connector_id!r}")

    if connector.auth_type != "apikey":
        return _error_response(
            422,
            "wrong_auth_type",
            f"Connector {connector_id!r} uses {connector.auth_type!r} auth, not apikey",
        )

    fields_payload = payload.get("fields")
    if not isinstance(fields_payload, dict):
        return _error_response(422, "invalid_payload", 'Body must be {"fields": {...}}')

    allowed_keys = {f["key"] for f in connector.credential_fields}
    for key, value in fields_payload.items():
        if key not in allowed_keys:
            return _error_response(
                422,
                "unknown_field",
                f"Field key {key!r} is not declared for connector {connector_id!r}. "
                f"Allowed keys: {sorted(allowed_keys)}",
            )
        if not isinstance(value, str) or not value.strip():
            return _error_response(
                422,
                "empty_value",
                f"Value for field {key!r} must be a non-empty string",
            )

    def _store_all() -> None:
        for key, value in fields_payload.items():
            store_apikey_credential(connector_id, key, value.strip())
        # Issue 5 — Tavily-first: when the Tavily connector is configured via the UI,
        # also persist [search] provider = "tavily" to settings.toml so the search
        # provider activates at next boot without manual config edits.
        # Contract C-SEARCHFALLBACK: the search.provider field drives auto-registration
        # in registry.py (another lane); we only persist the TOML value here.
        if connector_id == "tavily":
            try:
                from hachi.core.config_loader import (
                    read_global_config_file,
                    write_global_config_dict,
                )

                # BB-124: read the FILE layer only — never load_effective_config(),
                # whose merged view (defaults + file + env + CLI) would otherwise
                # get baked back into settings.toml by the write below (this was
                # the one writer T-03 could not reach — file owned by a concurrent
                # agent in that wave; converted here as the tracked follow-up).
                _doc = read_global_config_file()
                _search = dict(_doc.get("search") or {})
                if _search.get("provider") != "tavily":
                    _search["provider"] = "tavily"
                    _doc["search"] = _search
                    write_global_config_dict(_doc)
                    _log.info(
                        "post_credentials: persisted [search] provider='tavily' to settings.toml"
                    )
            except Exception:
                # Non-fatal: credential is stored, TOML update is best-effort.
                _log.warning(
                    "post_credentials: failed to persist [search] provider for tavily",
                    exc_info=True,
                )

    await anyio.to_thread.run_sync(_store_all)

    # BB-11: live-reload search tools when Tavily credentials are stored so the
    # web_search tool activates in the CURRENT session without a restart.
    if connector_id == "tavily":
        try:
            await anyio.to_thread.run_sync(cp.reload_search_tools)
        except Exception:
            pass  # non-fatal; tools activate on next boot regardless

    # Issue 5 — expose search fallback status for the UI (contract C-SEARCHFALLBACK).
    # ``fallback_active=True`` means no SearchProvider is registered and browse_web
    # (Playwright) is the current fallback.  The frontend uses this to show a
    # "web search unavailable" badge on the Tavily integration card.
    _fallback_active = True
    try:
        from hachi.gateway.shared_services import ProcessSharedServices

        _shared = ProcessSharedServices.instance()
        if _shared is not None:
            # A ProviderRegistry on the shared services has a registered search
            # provider when Tavily is active; absence means fallback is in use.
            _pr = getattr(_shared, "provider_registry", None)
            if _pr is not None and hasattr(_pr, "get_search"):
                _fallback_active = _pr.get_search() is None
    except Exception:
        pass

    status_info = await anyio.to_thread.run_sync(lambda: connector_status(connector_id))
    return {
        "id": connector.id,
        "name": connector.name,
        "category": connector.category,
        "fallback_active": _fallback_active,
        **status_info,
    }


@router.post("/{connector_id}/credentials/test")
async def post_credentials_test(connector_id: str) -> Any:
    """Validate a connector's stored credentials with a bounded live probe.

    Issue 6b (contract C-CREDTEST). Returns ``{"ok": bool, "message": str}``.
    Never echoes the secret and never raises — a failed or timed-out probe yields
    ``ok: false`` with a short message. Connectors without a cheap probe report
    ``ok: true`` with a "stored; live test not available" message so the UI still
    confirms the credential was saved.
    """
    import re

    from hachi.integrations.connector_registry import get_connector

    if not re.match(r"^[a-z0-9_-]+$", connector_id):
        return _error_response(400, "invalid_connector_id", "Connector id is not valid")
    connector = get_connector(connector_id)
    if connector is None:
        return _error_response(404, "unknown_connector", f"Unknown connector: {connector_id!r}")

    def _resolve_secret(*names: str) -> str | None:
        # Vault first, then environment (canonical + UPPER) — never logged.
        try:
            from hachi.core.credential_vault import CredentialVault

            _v = CredentialVault()
            for n in names:
                val = _v.get_token(n)
                if val:
                    return str(val)
        except Exception:
            pass
        for n in names:
            val = os.environ.get(n) or os.environ.get(n.upper())
            if val:
                return val
        return None

    def _probe() -> dict[str, Any]:
        try:
            if connector_id == "tavily":
                # BB-14: the credential dialog stores under the namespaced key
                # "apikey.tavily.tavily_api_key"; check it first, then the bare
                # name and env. (Reading only "tavily_api_key" made Test
                # Connection always report "No Tavily API key stored" even
                # right after the key was saved.)
                key = _resolve_secret("apikey.tavily.tavily_api_key", "tavily_api_key")
                if not key:
                    return {"ok": False, "message": "No Tavily API key stored."}
                import httpx

                resp = httpx.post(
                    "https://api.tavily.com/search",
                    json={"api_key": key, "query": "ping", "max_results": 1},
                    timeout=8.0,
                )
                if resp.status_code == 200:
                    return {
                        "ok": True,
                        "message": "Tavily reachable — API key is valid.",
                    }
                if resp.status_code in (401, 403):
                    return {"ok": False, "message": "Tavily rejected the API key."}
                return {
                    "ok": False,
                    "message": f"Tavily returned HTTP {resp.status_code}.",
                }
            if connector_id == "telegram":
                token = _resolve_secret("telegram_bot_token")
                if not token:
                    return {"ok": False, "message": "No Telegram bot token stored."}
                import httpx

                resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8.0)
                if resp.status_code == 200 and resp.json().get("ok"):
                    name = resp.json().get("result", {}).get("username", "bot")
                    return {
                        "ok": True,
                        "message": f"Telegram bot @{name} reachable.",
                    }
                return {"ok": False, "message": "Telegram rejected the bot token."}
            # Generic: confirm a credential is present; no live probe available.
            from hachi.integrations.oauth_flow import connector_status

            st = connector_status(connector_id)
            if st.get("status") in {"connected", "configured"}:
                return {
                    "ok": True,
                    "message": "Credential stored; live test not available for this connector.",
                }
            return {
                "ok": False,
                "message": "No credential configured for this connector.",
            }
        except Exception as exc:  # never leak secrets or stack traces to the client
            return {"ok": False, "message": f"Test failed: {type(exc).__name__}"}

    return await anyio.to_thread.run_sync(_probe)


@router.delete("/{connector_id}/credentials")
async def delete_credentials(connector_id: str) -> Any:
    """Clear all stored apikey credentials for a connector.

    Returns the updated connector status dict.

    Returns HTTP 400 for invalid connector id format.
    Returns HTTP 404 for unknown connectors.
    Returns HTTP 422 if the connector is not an apikey connector.
    """
    import re

    from hachi.integrations.connector_registry import get_connector
    from hachi.integrations.oauth_flow import (
        clear_apikey_credentials,
        connector_status,
    )

    if not re.match(r"^[a-z0-9_-]+$", connector_id):
        return _error_response(400, "invalid_connector_id", "Connector id is not valid")

    connector = get_connector(connector_id)
    if connector is None:
        return _error_response(404, "unknown_connector", f"Unknown connector: {connector_id!r}")

    if connector.auth_type != "apikey":
        return _error_response(
            422,
            "wrong_auth_type",
            f"Connector {connector_id!r} uses {connector.auth_type!r} auth, not apikey",
        )

    await anyio.to_thread.run_sync(lambda: clear_apikey_credentials(connector_id))

    status_info = await anyio.to_thread.run_sync(lambda: connector_status(connector_id))
    return {
        "id": connector.id,
        "name": connector.name,
        "category": connector.category,
        **status_info,
    }
