"""
Universal REST Adapter

This module provides the UniversalRestAdapter, which takes API definitions
parsed from .og files (`::api` blocks) and dynamically generates native Python
functions that execute the HTTP requests using `httpx`.

These dynamic functions are registered directly into the agent's ToolRegistry.
"""

import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlparse

import httpx

from tokoboros.core.credential_vault import CredentialVault
from tokoboros.core.loop import ToolRegistry
from tokoboros.og_engine.parser import OGNode


class UniversalRestAdapter:
    """
    Dynamically generates native Python tools from .og ::api blocks and registers
    them with the ToolRegistry for LLM consumption.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        vault: CredentialVault | None = None,
        allowed_hosts: list[str] | None = None,
    ):
        self.registry = registry
        self.vault = vault
        self.allowed_hosts = allowed_hosts or self._load_allowed_hosts_from_env()
        self._allow_private_egress = (
            os.environ.get("TOKOBOROS_REST_ALLOW_PRIVATE", "false").lower() == "true"
        )

    @staticmethod
    def _load_allowed_hosts_from_env() -> list[str]:
        raw = os.environ.get("TOKOBOROS_REST_ALLOWLIST", "")
        return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]

    @staticmethod
    def _host_in_allowlist(host: str, allowlist: list[str]) -> bool:
        host = host.lower()
        for item in allowlist:
            if item.startswith("*."):
                suffix = item[1:].lower()  # includes leading dot
                if host.endswith(suffix) and host != suffix[1:]:
                    return True
            elif host == item.lower():
                return True
        return False

    def _is_public_host(self, host: str) -> bool:
        lowered = host.lower()
        if lowered in {"localhost", "localhost.localdomain"}:
            return False

        try:
            ip = ipaddress.ip_address(lowered)
            return not (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            )
        except ValueError:
            pass

        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            return False

        for info in infos:
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                return False
        return True

    def _validate_outbound_url(self, raw_url: str, includes_secret: bool) -> str | None:
        try:
            parsed = urlparse(raw_url)
        except Exception:
            return "Invalid URL format"

        if parsed.scheme not in {"http", "https"}:
            return "Only http/https URLs are allowed"

        host = parsed.hostname
        if not host:
            return "URL hostname is required"

        host_lc = host.lower()
        if self.allowed_hosts and not self._host_in_allowlist(host_lc, self.allowed_hosts):
            return f"Host '{host_lc}' is not allowed by TOKOBOROS_REST_ALLOWLIST"

        if includes_secret and not self.allowed_hosts:
            return "Secret interpolation requires TOKOBOROS_REST_ALLOWLIST to be configured"

        if not self._allow_private_egress and not self._is_public_host(host_lc):
            return f"Host '{host_lc}' resolved to a private or unsafe address"

        return None

    def register_node_apis(self, node: OGNode):
        """Parses all API definitions in an OGNode and registers them as tools."""
        for tool_name, api_def in node.api_defs.items():
            self._register_single_api(tool_name, api_def, node.name)

    def _register_single_api(self, tool_name: str, api_def: dict[str, str], node_name: str):
        method = api_def.get("method", "GET").upper()
        endpoint = api_def.get("endpoint", "")
        base_desc = api_def.get("description", f"Execute {method} request to {endpoint}")

        # Force the LLM to read the .og behavioral instructions
        # before blindly calling the raw REST endpoint.
        description = (
            f"{base_desc}\n\nIMPORTANT: You MUST call `load_skill('{node_name}')` BEFORE using "
            "this tool to understand its required context and safety assertions!"
        )

        # Parse schema into proper LLM properties
        raw_schema = {}
        if "schema" in api_def:
            # We already validated it's valid JSON in Phase 1
            raw_schema = json.loads(api_def["schema"])

        properties = {}
        required_fields = []
        for k, v in raw_schema.items():
            if isinstance(v, str):
                properties[k] = {"type": v, "description": f"Parameter: {k}"}
                required_fields.append(k)
            elif isinstance(v, dict):
                properties[k] = v  # Assume it's already a proper JSON Schema dict
                required_fields.append(k)

        llm_schema = {
            "name": tool_name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required_fields,
            },
        }

        raw_headers_str = api_def.get("headers", "{}")

        # The dynamic function that will be executed
        def dynamic_api_tool(**kwargs) -> str:
            # 1. Prepare Endpoint (replace path params if any exist in endpoint string)
            url = endpoint
            for k, v in kwargs.items():
                if f"{{{k}}}" in url:
                    url = url.replace(f"{{{k}}}", str(v))

            # 2. Prepare Headers (inject vault tokens & default headers)
            try:
                headers = json.loads(raw_headers_str)
                secret_interpolated = False

                # Inject standard bot headers to prevent 403 blocks
                # from public APIs (like Wikipedia, CoinGecko, ipapi) that reject default
                # python-httpx agents.
                header_keys = {k.lower() for k in headers.keys()}
                if "user-agent" not in header_keys:
                    headers["User-Agent"] = (
                        "Tokoboros-Omni-Orchestrator/1.0 (https://github.com/tokoboros/agent)"
                    )
                if "accept" not in header_keys:
                    headers["Accept"] = "application/json, text/plain, */*"

                # Dynamic interception of vault variables like {whatsapp_api_token}
                for k, v in headers.items():
                    if isinstance(v, str):
                        matches = re.findall(r"\{([a-zA-Z0-9_]+)\}", v)
                        for vault_key in matches:
                            normalized_header = k.lower().strip()
                            if normalized_header in {"host", "x-forwarded-host", "forwarded"}:
                                return (
                                    f"Error: Header '{k}' is not allowed. "
                                    "Host-routing headers are blocked by outbound policy."
                                )
                            if normalized_header not in {
                                "authorization",
                                "x-api-key",
                                "api-key",
                                "x-auth-token",
                            }:
                                return (
                                    f"Error: Secret interpolation in header '{k}' is not allowed. "
                                    "Use Authorization/x-api-key style headers only."
                                )
                            if self.vault:
                                vault_val = self.vault.get_token(vault_key)
                                if vault_val:
                                    v = v.replace(f"{{{vault_key}}}", vault_val)
                                    secret_interpolated = True
                                else:
                                    return (
                                        f"Error: Missing secret '{vault_key}' in credential vault."
                                    )
                            else:
                                return (
                                    "Error: No credential vault configured for secret "
                                    "interpolation."
                                )
                        headers[k] = v
            except Exception as e:
                return f"Error parsing headers: {e}"

            # 3. Prepare Payload/Query
            params = {}
            json_payload = {}

            # Separate body payload vs query params based on HTTP method
            # Path params were already injected into the URL
            for k, v in kwargs.items():
                if f"{{{k}}}" not in endpoint:
                    if method in ("GET", "DELETE"):
                        params[k] = v
                    else:
                        json_payload[k] = v

            # Clean up empty payloads
            params = params if params else None
            json_payload = json_payload if json_payload else None

            # 4. Execute HTTP Request
            validation_error = self._validate_outbound_url(url, includes_secret=secret_interpolated)
            if validation_error:
                return f"Blocked by outbound policy: {validation_error}"

            try:
                # follow_redirects=False is explicit: a 3xx to an internal host is a
                # classic SSRF bypass of the public-host check above. (Residual: a
                # narrow DNS-rebinding window between validation and connect remains;
                # mitigated by the public-IP check + the secret allowlist.)
                with httpx.Client(timeout=15.0, follow_redirects=False) as client:
                    response = client.request(
                        method=method, url=url, headers=headers, params=params, json=json_payload
                    )
                    response.raise_for_status()

                    try:
                        return json.dumps(response.json(), indent=2)
                    except json.JSONDecodeError:
                        return response.text

            except httpx.HTTPStatusError as exc:
                return f"HTTP Error {exc.response.status_code}: {exc.response.text}"
            except httpx.RequestError as exc:
                return f"Request Error: {str(exc)}"
            except Exception as exc:
                return f"Unexpected Error: {str(exc)}"

        # Set the name of the function to the tool name (helps with debugging stack traces)
        dynamic_api_tool.__name__ = tool_name

        # Register the dynamic function with the ToolRegistry
        self.registry.register(llm_schema, dynamic_api_tool)
