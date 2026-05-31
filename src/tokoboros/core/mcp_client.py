"""
MCP (Model Context Protocol) Client

This module implements an MCP client that spawns external MCP servers as subprocesses
and communicates with them via JSON-RPC 2.0 over stdio. This is the industry-standard
way to give AI agents access to external services (Gmail, Calendar, Slack, etc.)
without hardcoding any API integrations.

How it works:
    1. An .og file with role=mcp defines the server command (e.g., "npx -y @anthropic/mcp-gmail")
    2. Tokoboros spawns the server as a subprocess on boot
    3. The MCP client calls `tools/list` to discover what tools the server provides
    4. Each tool is dynamically registered in the ToolRegistry
    5. When the agent calls a tool, the MCP client forwards the call to the subprocess
    6. The MCP server handles all auth (OAuth, tokens, etc.) internally

Protocol: JSON-RPC 2.0 over stdin/stdout
Reference: https://modelcontextprotocol.io/
"""

import json
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from tokoboros.core.loop import ToolRegistry


class MCPServerConnection:
    """
    Manages a persistent connection to a single MCP server subprocess.
    Handles JSON-RPC communication over stdin/stdout.
    """

    def __init__(self, name: str, command: str, args: List[str] = None, env: Dict[str, str] = None):
        """
        Initialize an MCP server connection.

        Args:
            name: The logical name of this MCP server (e.g., 'gmail').
            command: The executable command (e.g., 'npx', 'python').
            args: Arguments to pass to the command (e.g., ['-y', '@anthropic/mcp-gmail']).
            env: Environment variables to set for the subprocess.
        """
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._tools: List[Dict[str, Any]] = []

    def start(self) -> bool:
        """
        Spawn the MCP server subprocess and initialize the connection.

        Returns:
            True if the server started and initialized successfully.
        """
        try:
            import os
            import shutil
            import sys
            merged_env = {**os.environ, **self.env}

            # Resolve the full path to the command so it works even inside a Python venv
            resolved_command = shutil.which(self.command, path=os.environ.get("PATH", ""))
            if not resolved_command:
                resolved_command = self.command

            full_command = [resolved_command] + self.args

            # On Windows, use shell=True for npx/node commands
            use_shell = sys.platform == "win32" and self.command in ("npx", "node", "npm")

            if use_shell:
                # Quote paths with spaces for shell execution
                quoted_parts = []
                for part in full_command:
                    if " " in part:
                        quoted_parts.append(f'"{part}"')
                    else:
                        quoted_parts.append(part)
                shell_command = " ".join(quoted_parts)
            else:
                shell_command = full_command

            self._process = subprocess.Popen(
                shell_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                shell=use_shell,
                bufsize=0
            )

            # Wait for the server to boot (npx may need to download the package first)
            for _ in range(10):  # Wait up to 5 seconds
                time.sleep(0.5)
                if self._process.poll() is not None:
                    # Process died during startup
                    stderr = self._process.stderr.read().decode("utf-8", errors="replace")[:500]
                    print(f"  [mcp] {self.name} exited during startup. stderr: {stderr}")
                    self._process = None
                    return False
                break  # Process is alive, proceed

            # Extra wait for npx to finish downloading on first run
            time.sleep(2)

            # Send the MCP initialize request
            init_response = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "tokoboros",
                    "version": "1.0.0"
                }
            })

            if not init_response or "error" in init_response:
                error_msg = init_response.get("error", {}).get("message", "Unknown error") if init_response else "No response"
                self.stop()
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})

            # Discover available tools
            tools_response = self._send_request("tools/list", {})
            if tools_response and "result" in tools_response:
                self._tools = tools_response["result"].get("tools", [])

            return True

        except FileNotFoundError:
            return False
        except Exception as e:
            self.stop()
            return False

    def stop(self):
        """Terminate the MCP server subprocess."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    @property
    def tools(self) -> List[Dict[str, Any]]:
        """The list of tools discovered from this MCP server."""
        return self._tools

    @property
    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        return self._process is not None and self._process.poll() is None

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Call a tool on this MCP server.

        Args:
            tool_name: The name of the tool to call.
            arguments: The arguments to pass to the tool.

        Returns:
            The tool's response as a string.
        """
        response = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        if not response:
            return f"Error: No response from MCP server '{self.name}'"

        if "error" in response:
            error = response["error"]
            return f"MCP Error: {error.get('message', 'Unknown error')}"

        result = response.get("result", {})
        content = result.get("content", [])

        # Extract text from content blocks
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif isinstance(block, str):
                text_parts.append(block)

        return "\n".join(text_parts) if text_parts else json.dumps(result)

    def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request and wait for the response."""
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None

        with self._lock:
            self._request_id += 1
            request_id = self._request_id

            message = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params
            }

            try:
                payload = json.dumps(message) + "\n"
                self._process.stdin.write(payload.encode("utf-8"))
                self._process.stdin.flush()

                # Read response lines until we get one with our request ID
                deadline = time.time() + 30  # 30 second timeout
                while time.time() < deadline:
                    line = self._process.stdout.readline()
                    if not line:
                        break

                    line = line.decode("utf-8").strip()
                    if not line:
                        continue

                    try:
                        response = json.loads(line)
                        if isinstance(response, dict) and response.get("id") == request_id:
                            return response
                    except json.JSONDecodeError:
                        continue

                return None
            except Exception as e:
                return {"error": {"message": str(e)}}

    def _send_notification(self, method: str, params: Dict[str, Any]):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }

        try:
            payload = json.dumps(message) + "\n"
            self._process.stdin.write(payload.encode("utf-8"))
            self._process.stdin.flush()
        except Exception:
            pass


class MCPManager:
    """
    Manages the lifecycle of all MCP server connections.
    Loads MCP definitions from .og nodes, starts servers, and registers tools.
    """

    def __init__(self):
        self._servers: Dict[str, MCPServerConnection] = {}

    def setup_from_node(self, node) -> bool:
        """
        Create and start an MCP server from a role=mcp .og node.

        Expected ::config format:
            command=npx
            args=-y @anthropic/mcp-gmail
            env_KEY=VALUE   (optional env vars, prefixed with env_)

        Args:
            node: An OGNode with role='mcp'.

        Returns:
            True if the server started successfully.
        """
        command = node.config.get("command", "")
        if not command:
            print(f"  [mcp] No command specified for {node.name}")
            return False

        # Parse args (space-separated string)
        args_raw = node.config.get("args", "")
        args = args_raw.split() if args_raw else []

        # Parse env vars (keys prefixed with env_)
        env = {}
        for key, value in node.config.items():
            if key.startswith("env_"):
                env_key = key[4:]  # Strip the "env_" prefix
                env[env_key] = value

        server = MCPServerConnection(
            name=node.name,
            command=command,
            args=args,
            env=env
        )

        if server.start():
            self._servers[node.name] = server
            return True
        return False

    def register_tools(self, registry: ToolRegistry):
        """
        Register all discovered MCP tools into the Tokoboros ToolRegistry.
        Each MCP tool becomes a native LLM tool the agent can call directly.

        Args:
            registry: The ToolRegistry to mount tools onto.
        """
        for server_name, server in self._servers.items():
            for tool in server.tools:
                tool_name = tool.get("name", "")
                if not tool_name:
                    continue

                # Namespace the tool to prevent collisions across multiple servers or native tools
                namespaced_name = f"{server_name}_{tool_name}"

                # Build the Anthropic-compatible schema
                schema = {
                    "name": namespaced_name,
                    "description": tool.get("description", f"Tool from MCP server '{server_name}'"),
                    "input_schema": tool.get("inputSchema", {
                        "type": "object",
                        "properties": {},
                        "required": []
                    })
                }

                # Create a closure that routes to the correct server using the original tool name
                def make_handler(srv: MCPServerConnection, original_name: str):
                    def handler(**kwargs) -> str:
                        return srv.call_tool(original_name, kwargs)
                    return handler

                registry.register(schema, make_handler(server, tool_name))

    def get_server_tool_count(self, server_name: str) -> int:
        """Return the discovered tool count for a given server."""
        server = self._servers.get(server_name)
        if not server:
            return 0
        return len(server.tools)

    def get_inventory(self) -> List[Dict[str, Any]]:
        """Return a structured MCP inventory snapshot for operator views."""
        rows: List[Dict[str, Any]] = []
        for name, server in self._servers.items():
            rows.append({
                "name": name,
                "status": "ready" if server.is_alive else "stopped",
                "tool_count": len(server.tools),
                "tools": [tool.get("name", "") for tool in server.tools if tool.get("name")],
            })
        return rows

    @property
    def server_count(self) -> int:
        """Number of active MCP server connections."""
        return len(self._servers)

    @property
    def total_tool_count(self) -> int:
        """Total number of tools across all MCP servers."""
        return sum(len(s.tools) for s in self._servers.values())

    def shutdown(self):
        """Terminate all MCP server subprocesses."""
        for name, server in self._servers.items():
            server.stop()
        self._servers.clear()
