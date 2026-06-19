"""
MCP (Model Context Protocol) Client

This module implements an MCP client that spawns external MCP servers as subprocesses
and communicates with them via JSON-RPC 2.0 over stdio. This is the industry-standard
way to give AI agents access to external services (Gmail, Calendar, Slack, etc.)
without hardcoding any API integrations.

How it works:
    1. An .og file with role=mcp defines the server command (e.g., "npx -y @anthropic/mcp-gmail")
    2. Hachi spawns the server as a subprocess on boot
    3. The MCP client calls `tools/list` to discover what tools the server provides
    4. Each tool is dynamically registered in the ToolRegistry
    5. When the agent calls a tool, the MCP client forwards the call to the subprocess
    6. The MCP server handles all auth (OAuth, tokens, etc.) internally

Protocol: JSON-RPC 2.0 over stdin/stdout
Reference: https://modelcontextprotocol.io/
"""

import json
import logging
import os
import shlex
import subprocess
import threading
import time
from typing import Any

from hachi.core.loop import ToolRegistry
from hachi.core.untrusted_content import guard_untrusted

logger = logging.getLogger(__name__)


class MCPServerConnection:
    """
    Manages a persistent connection to a single MCP server subprocess.
    Handles JSON-RPC communication over stdin/stdout.
    """

    # Default timeout for the entire boot sequence (subprocess spawn → initialize → tools/list).
    # A hanging MCP server (e.g. slow npx download on first run or a dead npm registry) must
    # not block the agent boot indefinitely.  UC-DEV-001.
    DEFAULT_BOOT_TIMEOUT_SECONDS: float = 60.0

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] = None,
        env: dict[str, str] = None,
        request_timeout_seconds: float = 30.0,
        boot_timeout_seconds: float | None = None,
    ):
        """
        Initialize an MCP server connection.

        Args:
            name: The logical name of this MCP server (e.g., 'gmail').
            command: The executable command (e.g., 'npx', 'python').
            args: Arguments to pass to the command (e.g., ['-y', '@anthropic/mcp-gmail']).
            env: Environment variables to set for the subprocess.
            request_timeout_seconds: Per-request JSON-RPC timeout.
            boot_timeout_seconds: Maximum wall-clock seconds allowed for the full
                boot sequence (spawn → initialize → tools/list).  Defaults to
                ``DEFAULT_BOOT_TIMEOUT_SECONDS``.  UC-DEV-001.
        """
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._response_cv = threading.Condition(self._lock)
        self._responses: dict[Any, dict[str, Any]] = {}
        self._reader_thread: threading.Thread | None = None
        self._request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self._boot_timeout_seconds = (
            float(boot_timeout_seconds)
            if boot_timeout_seconds is not None
            else self.DEFAULT_BOOT_TIMEOUT_SECONDS
        )
        self._tools: list[dict[str, Any]] = []

    @staticmethod
    def _normalize_tools_payload(raw_tools: Any) -> list[dict[str, Any]]:
        """Normalize tool discovery payload to a stable list with unique named tools."""
        if not isinstance(raw_tools, list):
            return []

        normalized: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            normalized.append(item)
        return normalized

    def _stdout_reader(self) -> None:
        """Continuously read MCP stdout and route JSON-RPC responses by request ID."""
        process = self._process
        if not process or not process.stdout:
            return

        while True:
            try:
                line = process.stdout.readline()
            except Exception:
                return

            if not line:
                return

            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            try:
                response = json.loads(decoded)
            except json.JSONDecodeError:
                continue

            if not isinstance(response, dict) or "id" not in response:
                continue

            with self._response_cv:
                self._responses[response.get("id")] = response
                self._response_cv.notify_all()

    def _build_subprocess_env(self) -> dict[str, str]:
        """Build a minimal environment to avoid leaking host secrets to MCP servers."""
        base_keys = [
            "PATH",
            "HOME",
            # POSIX (macOS/Linux): temp dir, runtime dir, identity, and login shell.
            # Many MCP servers create temp files via TMPDIR; on macOS TMP/TEMP are
            # usually unset, so omitting TMPDIR left them writing to the wrong place.
            "TMPDIR",
            "XDG_RUNTIME_DIR",
            "USER",
            "SHELL",
            # Windows runtime and data directories.
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TMP",
            "TEMP",
            "APPDATA",
            "LOCALAPPDATA",
            "ProgramData",
            "ProgramFiles",
            "ProgramFiles(x86)",
        ]
        env: dict[str, str] = {}
        for key in base_keys:
            value = os.environ.get(key)
            if value:
                env[key] = value

        # Explicit server-level overrides from the OG node are allowed.
        env.update(self.env)
        return env

    def start(self) -> bool:
        """Spawn the MCP server subprocess and initialize the connection.

        The entire boot sequence (spawn → initialize → tools/list) is bounded by
        ``boot_timeout_seconds`` (UC-DEV-001).  If a server hangs past that
        deadline the method returns ``False`` and logs a warning so the agent can
        continue without that MCP server rather than hanging indefinitely.

        Returns:
            True if the server started and initialized successfully.
        """
        result_holder: list[bool] = []
        exc_holder: list[BaseException] = []

        def _do_start() -> None:
            try:
                ok = self._start_inner()
                result_holder.append(ok)
            except BaseException as exc:  # noqa: BLE001
                exc_holder.append(exc)
                result_holder.append(False)

        t = threading.Thread(target=_do_start, name=f"mcp-boot-{self.name}", daemon=True)
        t.start()
        t.join(timeout=self._boot_timeout_seconds)

        if t.is_alive():
            # Boot timed out.  The thread is a daemon so it won't block shutdown,
            # but we must still kill the subprocess to avoid resource leaks.
            logger.warning(
                "[mcp] %s: boot timed out after %.1fs — killing subprocess and continuing "
                "without this MCP server (UC-DEV-001).",
                self.name,
                self._boot_timeout_seconds,
            )
            try:
                proc = self._process
                if proc:
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._process = None
            return False

        if exc_holder:
            logger.warning(
                "[mcp] %s: unexpected error during boot: %s",
                self.name,
                exc_holder[0],
            )
            return False

        return result_holder[0] if result_holder else False

    def _start_inner(self) -> bool:
        """Perform the actual MCP server boot (run inside a timed daemon thread)."""
        try:
            import shutil

            merged_env = self._build_subprocess_env()

            # Resolve the full path to the command so it works even inside a Python venv.
            resolved_command = shutil.which(self.command, path=os.environ.get("PATH", ""))
            if not resolved_command:
                resolved_command = self.command

            full_command = [resolved_command] + self.args

            self._process = subprocess.Popen(
                full_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                shell=False,
                bufsize=0,
            )

            # FIX 2: Drain stderr in a daemon thread to prevent pipe-buffer deadlock.
            def _drain_stderr(proc: subprocess.Popen) -> None:
                try:
                    for _ in iter(proc.stderr.readline, b""):
                        pass
                except Exception:
                    pass

            threading.Thread(
                target=_drain_stderr,
                args=(self._process,),
                daemon=True,
                name="mcp-stderr-drain",
            ).start()

            # FIX 3: Poll for up to 5 s (10 x 0.5 s) rather than breaking after one iteration.
            for _ in range(10):  # Wait up to 5 seconds
                time.sleep(0.5)
                if self._process.poll() is not None:
                    # Process died during startup
                    print(f"  [mcp] {self.name} exited during startup.")
                    self._process = None
                    return False

            self._reader_thread = threading.Thread(
                target=self._stdout_reader,
                name=f"mcp-reader-{self.name}",
                daemon=True,
            )
            self._reader_thread.start()

            # Extra wait for npx to finish downloading on first run
            time.sleep(2)

            # Send the MCP initialize request
            init_response = self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "hachi", "version": "1.0.0"},
                },
            )

            if not init_response or "error" in init_response:
                init_response.get("error", {}).get(
                    "message", "Unknown error"
                ) if init_response else "No response"
                self.stop()
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})

            # Discover available tools
            tools_response = self._send_request("tools/list", {})
            if tools_response and "result" in tools_response:
                tools = tools_response["result"].get("tools", [])
                self._tools = self._normalize_tools_payload(tools)

            return True

        except FileNotFoundError:
            return False
        except Exception:
            self.stop()
            return False

    def stop(self):
        """Terminate the MCP server subprocess."""
        process = self._process
        if process:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        self._process = None
        with self._response_cv:
            self._responses.clear()
            self._response_cv.notify_all()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)
        self._reader_thread = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        """The list of tools discovered from this MCP server."""
        return self._tools

    @property
    def is_alive(self) -> bool:
        """Check if the subprocess is still running."""
        return self._process is not None and self._process.poll() is None

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Call a tool on this MCP server.

        Args:
            tool_name: The name of the tool to call.
            arguments: The arguments to pass to the tool.

        Returns:
            The tool's response as a string.
        """
        response = self._send_request("tools/call", {"name": tool_name, "arguments": arguments})

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

    def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC request and wait for the response.

        FIX 1: Allocate the request ID and write the payload under the lock (minimally),
        then use cv.wait_for to wait for the response.  cv.wait_for releases the lock
        during the wait so other callers can write their own requests and wait concurrently.
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            return None

        # --- Step 1: allocate a unique ID and write the request, both under the lock. ---
        with self._response_cv:
            self._request_id += 1
            request_id = self._request_id

            message = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }

            try:
                payload = json.dumps(message) + "\n"
                self._process.stdin.write(payload.encode("utf-8"))
                self._process.stdin.flush()
            except Exception as e:
                return {"error": {"message": str(e)}}

            # --- Step 2: wait for the reader thread to post the matching response.
            # cv.wait_for releases self._lock during the wait, so other threads can
            # enter this same block and issue their own requests concurrently. ---
            arrived = self._response_cv.wait_for(
                lambda: request_id in self._responses,
                timeout=self._request_timeout_seconds,
            )

            if not arrived:
                return None

            return self._responses.pop(request_id)

    def _send_notification(self, method: str, params: dict[str, Any]):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        message = {"jsonrpc": "2.0", "method": method, "params": params}

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
        self._servers: dict[str, MCPServerConnection] = {}

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
        args = shlex.split(args_raw, posix=(os.name != "nt")) if args_raw else []

        # Parse env vars (keys prefixed with env_)
        env = {}
        for key, value in node.config.items():
            if key.startswith("env_"):
                env_key = key[4:]  # Strip the "env_" prefix
                env[env_key] = value

        # Optional per-server timeouts configurable from the .og node.
        boot_timeout = node.config.get("boot_timeout_seconds")
        request_timeout = node.config.get("request_timeout_seconds")
        kwargs: dict[str, Any] = {}
        if boot_timeout is not None:
            try:
                kwargs["boot_timeout_seconds"] = float(boot_timeout)
            except (ValueError, TypeError):
                pass
        if request_timeout is not None:
            try:
                kwargs["request_timeout_seconds"] = float(request_timeout)
            except (ValueError, TypeError):
                pass

        server = MCPServerConnection(name=node.name, command=command, args=args, env=env, **kwargs)

        if server.start():
            self._servers[node.name] = server
            return True
        return False

    def register_tools(self, registry: ToolRegistry):
        """
        Register all discovered MCP tools into the Hachi ToolRegistry.
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
                    "input_schema": tool.get(
                        "inputSchema", {"type": "object", "properties": {}, "required": []}
                    ),
                }

                # Create a closure that routes to the correct server using the original tool name
                def make_handler(srv: MCPServerConnection, original_name: str):
                    def handler(**kwargs) -> str:
                        # MCP servers are third parties; spotlight their output if it
                        # carries a prompt-injection (same guard as web/REST content).
                        result = srv.call_tool(original_name, kwargs)
                        guarded, _flagged = guard_untrusted(
                            result, source=f"MCP tool '{original_name}' (server '{srv.name}')"
                        )
                        return guarded

                    return handler

                registry.register(schema, make_handler(server, tool_name))

    def get_server_tool_count(self, server_name: str) -> int:
        """Return the discovered tool count for a given server."""
        server = self._servers.get(server_name)
        if not server:
            return 0
        return len(server.tools)

    def get_inventory(self) -> list[dict[str, Any]]:
        """Return a structured MCP inventory snapshot for operator views."""
        rows: list[dict[str, Any]] = []
        for name, server in self._servers.items():
            rows.append(
                {
                    "name": name,
                    "status": "ready" if server.is_alive else "stopped",
                    "tool_count": len(server.tools),
                    "tools": [tool.get("name", "") for tool in server.tools if tool.get("name")],
                }
            )
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
        for _name, server in self._servers.items():
            server.stop()
        self._servers.clear()
