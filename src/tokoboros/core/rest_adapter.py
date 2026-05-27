"""
Universal REST Adapter

This module provides the UniversalRestAdapter, which takes API definitions
parsed from .og files (`::api` blocks) and dynamically generates native Python
functions that execute the HTTP requests using `httpx`.

These dynamic functions are registered directly into the agent's ToolRegistry.
"""

import re
import json
import httpx
from typing import Dict, Any, Optional

from tokoboros.core.loop import ToolRegistry
from tokoboros.og_engine.parser import OGNode
from tokoboros.core.credential_vault import CredentialVault

class UniversalRestAdapter:
    """
    Dynamically generates native Python tools from .og ::api blocks and registers
    them with the ToolRegistry for LLM consumption.
    """
    def __init__(self, registry: ToolRegistry, vault: Optional[CredentialVault] = None):
        self.registry = registry
        self.vault = vault

    def register_node_apis(self, node: OGNode):
        """Parses all API definitions in an OGNode and registers them as tools."""
        for tool_name, api_def in node.api_defs.items():
            self._register_single_api(tool_name, api_def, node.name)

    def _register_single_api(self, tool_name: str, api_def: Dict[str, str], node_name: str):
        method = api_def.get("method", "GET").upper()
        endpoint = api_def.get("endpoint", "")
        base_desc = api_def.get("description", f"Execute {method} request to {endpoint}")
        
        #Force the LLM to read the .og behavioral instructions
        # before blindly calling the raw REST endpoint.
        description = f"{base_desc}\n\nIMPORTANT: You MUST call `load_skill('{node_name}')` BEFORE using this tool to understand its required context and safety assertions!"
        
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
                "required": required_fields
            }
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
                
                # Inject standard bot headers to prevent 403 blocks
                # from public APIs (like Wikipedia, CoinGecko, ipapi) that reject default python-httpx agents.
                header_keys = {k.lower() for k in headers.keys()}
                if "user-agent" not in header_keys:
                    headers["User-Agent"] = "Tokoboros-Omni-Orchestrator/1.0 (https://github.com/tokoboros/agent)"
                if "accept" not in header_keys:
                    headers["Accept"] = "application/json, text/plain, */*"

                # Dynamic interception of vault variables like {whatsapp_api_token}
                for k, v in headers.items():
                    if isinstance(v, str):
                        matches = re.findall(r"\{([a-zA-Z0-9_]+)\}", v)
                        for vault_key in matches:
                            if self.vault:
                                vault_val = self.vault.get_token(vault_key)
                                if vault_val:
                                    v = v.replace(f"{{{vault_key}}}", vault_val)
                            else:
                                v = v.replace(f"{{{vault_key}}}", "MISSING_TOKEN")
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
            try:
                with httpx.Client(timeout=15.0) as client:
                    response = client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_payload
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
