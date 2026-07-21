"""``/runtime/mcp/*`` -- MCP server inventory + lifecycle. One of the 7 routers
the former ``runtime_ops`` splits into (T-47 Sec 3.3).

No ``prefix=`` -- ``runtime_ops`` carried none, so this (and its 6 siblings)
must not gain one either, or every route under it silently re-paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from hachi.gateway.deps import ControlPlaneDep, require_auth
from hachi.gateway.fastapi_app import _ERROR_RESPONSES, McpRegisterRequest

router = APIRouter(
    tags=["runtime"],
    dependencies=[Depends(require_auth)],
    responses=_ERROR_RESPONSES,
)


@router.get("/runtime/mcp/inventory")
async def get_mcp_inventory(cp: ControlPlaneDep) -> Any:
    return cp.mcp_inventory()


# G1-T5 / CONTRACT-MCP — POST /runtime/mcp/register
@router.post("/runtime/mcp/register")
async def post_mcp_register(payload: McpRegisterRequest, cp: ControlPlaneDep) -> Any:
    """Register a new MCP server and bring it online without a gateway restart.

    Body (stdio transport):
        ``{"name": "...", "command": "npx", "args": "...", "env_vars": {}}``

    Body (sse / http transport):
        ``{"name": "...", "transport": "sse"|"http", "url": "https://...", "headers": {}}``
    """
    _ALLOWED_COMMANDS = {"npx", "uvx", "python", "python3", "node", "uv"}
    _ALLOWED_TRANSPORTS = {"stdio", "sse", "http"}
    _SECRET_PATTERNS = {
        "key",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "private",
        "auth",
        "bearer",
        "apikey",
        "api_key",
    }
    import re

    name = payload.name.strip()
    transport = payload.transport.strip().lower()

    if not name or not re.match(r"^[\w-]+$", name):
        raise HTTPException(
            status_code=422,
            detail=(
                "name must be non-empty and contain only letters, digits, hyphens, underscores"
            ),
        )
    if transport not in _ALLOWED_TRANSPORTS:
        raise HTTPException(
            status_code=422,
            detail=f"transport must be one of: {', '.join(sorted(_ALLOWED_TRANSPORTS))}",
        )

    if transport == "stdio":
        command = payload.command.strip()
        args = payload.args.strip()
        if command not in _ALLOWED_COMMANDS:
            raise HTTPException(
                status_code=422,
                detail=f"command must be one of: {', '.join(sorted(_ALLOWED_COMMANDS))}",
            )
        if not args:
            raise HTTPException(status_code=422, detail="args is required for stdio transport")
        if re.search(r"[;&|><`]|\$[({]|&&|\|\|", args):
            raise HTTPException(
                status_code=422,
                detail="args must not contain shell metacharacters",
            )
    else:
        # sse / http
        url = payload.url.strip()
        if not url or not re.match(r"^https?://", url):
            raise HTTPException(
                status_code=422,
                detail=(f"url is required and must be an http/https URL for {transport} transport"),
            )

    # Reject raw secrets in env_vars/headers; allow secret:<name> references (BB-9).
    # A key that looks like a secret (contains "key", "token", etc.) is accepted ONLY
    # when its value is a secret: reference — which resolves at connect time, so the
    # real credential never touches TOML.  We also verify the cred: entry exists now
    # so a dangling reference is caught before the config is persisted.
    all_pairs: list[tuple[str, str]] = list(payload.env_vars.items()) + list(
        payload.headers.items()
    )
    for env_key, env_val in all_pairs:
        lower = env_key.lower()
        key_looks_secret = any(p in lower for p in _SECRET_PATTERNS)
        val_is_ref = isinstance(env_val, str) and env_val.startswith("secret:")
        if key_looks_secret and not val_is_ref:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"key '{env_key}' looks like a secret; "
                    "store the value via POST /runtime/secrets "
                    "and reference it as secret:<name>"
                ),
            )
        if val_is_ref:
            cred_name = env_val[len("secret:") :]
            try:
                from hachi.core.secret_store import (
                    create_default_secret_store as _css,
                )

                if _css().get(f"cred:{cred_name}") is None:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"secret reference 'secret:{cred_name}' "
                            "has no matching credential. "
                            f"Store it via POST /runtime/secrets name='{cred_name}'."
                        ),
                    )
            except HTTPException:
                raise
            except Exception:
                pass  # secret store unavailable — skip validation, fail at connect time

    return cp.register_mcp_server(
        name=name,
        transport=transport,
        command=payload.command.strip(),
        args=payload.args.strip(),
        env_vars=dict(payload.env_vars),
        url=payload.url.strip(),
        headers=dict(payload.headers),
    )


# CONTRACT-MCP — POST /runtime/mcp/{name}/enable
@router.post("/runtime/mcp/{name}/enable")
async def post_mcp_enable(name: str, cp: ControlPlaneDep) -> Any:
    """Enable the named MCP server (persists ``enabled=true`` to settings.toml).

    Returns ``{"ok": true}`` on success, HTTP 404 if the server is unknown.
    """
    return cp.set_mcp_enabled(name=name, enabled=True)


# CONTRACT-MCP — POST /runtime/mcp/{name}/disable
@router.post("/runtime/mcp/{name}/disable")
async def post_mcp_disable(name: str, cp: ControlPlaneDep) -> Any:
    """Disable the named MCP server (persists ``enabled=false`` to settings.toml).

    Returns ``{"ok": true}`` on success, HTTP 404 if the server is unknown.
    """
    return cp.set_mcp_enabled(name=name, enabled=False)


# CONTRACT-MCP — DELETE /runtime/mcp/{name}
@router.delete("/runtime/mcp/{name}")
async def delete_mcp_server(name: str, cp: ControlPlaneDep) -> Any:
    """Remove a registered MCP server from settings.toml.

    Returns ``{"ok": true}`` on success, HTTP 404 if the server is unknown.
    """
    return cp.delete_mcp_server(name=name)
