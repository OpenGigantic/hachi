"""
Operating System Tools

This module provides Tokoboros with its physical hands—allowing it to read files, write files,
and execute raw terminal commands. These functions are bound to Anthropic-compatible JSON schemas
for seamless ingestion by the ToolRegistry.

All tool functions are gated by the PermissionGate. If a gate is provided during registration,
every call is checked against the user's device-permissions.og before execution.
If no gate is provided, tools run unrestricted (used only during internal boot operations).
"""

import os
import subprocess
from pathlib import Path
from typing import Optional
from tokoboros.core.loop import ToolRegistry
from tokoboros.core.permissions import PermissionGate


# ==========================================
# Raw Tool Implementations (Ungated)
# ==========================================
# These are the actual executors. They contain no permission logic.
# They can be called directly by internal boot code that needs
# unrestricted access (e.g., reading config files during startup).

def _raw_read_file(path: str) -> str:
    """Read the contents of a local file."""
    try:
        target = Path(path).resolve()
        if not target.exists():
            return f"Error: File '{target}' does not exist."
        if not target.is_file():
            return f"Error: '{target}' is a directory, not a file."
        
        with open(target, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"


def _raw_write_file(path: str, content: str) -> str:
    """Write string content to a local file, creating parent directories if necessary."""
    try:
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to '{target}'."
    except Exception as e:
        return f"Error writing file: {str(e)}"


def _raw_run_terminal_command(command: str, cwd: str = ".", timeout: int = 120) -> str:
    """Execute a raw shell command and return its stdout and stderr."""
    try:
        target_cwd = Path(cwd).resolve()
        if not target_cwd.exists() or not target_cwd.is_dir():
            return f"Error: Working directory '{target_cwd}' is invalid."

        result = subprocess.run(
            command,
            shell=True,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        output = []
        if result.stdout:
            output.append(f"--- STDOUT ---\n{result.stdout.strip()}")
        if result.stderr:
            output.append(f"--- STDERR ---\n{result.stderr.strip()}")
        
        if not output:
            output.append("Command executed successfully with no output.")
            
        output.append(f"\nExit Code: {result.returncode}")
        return "\n".join(output)

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"


# ==========================================
# Gated Tool Factories
# ==========================================
# Each factory takes the PermissionGate and returns a guarded version
# of the raw tool. The gate checks happen BEFORE execution — if denied,
# the raw function is never called.

def _make_guarded_read_file(gate: PermissionGate):
    """Create a permission-gated read_file tool."""
    def guarded_read_file(path: str) -> str:
        result = gate.check_path(path, "read")
        if not result.allowed:
            return f"Permission Denied: {result.reason}"
        return _raw_read_file(path)
    return guarded_read_file


def _make_guarded_write_file(gate: PermissionGate):
    """Create a permission-gated write_file tool."""
    def guarded_write_file(path: str, content: str) -> str:
        result = gate.check_path(path, "write")
        if not result.allowed:
            return f"Permission Denied: {result.reason}"
        return _raw_write_file(path, content)
    return guarded_write_file


def _make_guarded_run_terminal(gate: PermissionGate):
    """Create a permission-gated run_terminal_command tool."""
    def guarded_run_terminal(command: str, cwd: str = ".") -> str:
        # Check command against terminal rules (allowlist/blocklist)
        # Note: We intentionally do NOT gate the cwd here. The command allowlist
        # already controls what the agent can execute. Gating cwd creates false
        # positives (e.g., blocking "start kiro" because tokoboros runs from
        # D:\OG\object-graph which isn't in allowed_paths). File-level access
        # is already gated by read_file and write_file independently.
        cmd_result = gate.check_command(command)
        if not cmd_result.allowed:
            return f"Permission Denied: {cmd_result.reason}"
        return _raw_run_terminal_command(command, cwd, timeout=gate.terminal_timeout)
    return guarded_run_terminal


# ==========================================
# JSON Schemas
# ==========================================

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read the contents of a local file from the system.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The absolute or relative path to the file to read."
            }
        },
        "required": ["path"]
    }
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": "Write string content to a local file. Will overwrite the file if it already exists, and will create parent directories if needed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The absolute or relative path where the file should be written."
            },
            "content": {
                "type": "string",
                "description": "The string content to write into the file."
            }
        },
        "required": ["path", "content"]
    }
}

RUN_TERMINAL_COMMAND_SCHEMA = {
    "name": "run_terminal_command",
    "description": "Execute a raw shell command in the terminal. Use this to run scripts, interact with git, move files, or run tests.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash/shell command to execute."
            },
            "cwd": {
                "type": "string",
                "description": "The working directory where the command should be run. Defaults to the current directory ('.')."
            }
        },
        "required": ["command"]
    }
}

# ==========================================
# Registration Helper
# ==========================================

def register_os_tools(registry: ToolRegistry, gate: Optional[PermissionGate] = None):
    """
    Mount all OS tools onto the provided ToolRegistry.

    If a PermissionGate is provided, all tools are wrapped with permission checks.
    If not, raw (unrestricted) tools are registered — used only for internal testing.

    Args:
        registry: The tool registry to register into.
        gate: Optional PermissionGate loaded from device-permissions.og.
    """
    if gate:
        registry.register(READ_FILE_SCHEMA, _make_guarded_read_file(gate))
        registry.register(WRITE_FILE_SCHEMA, _make_guarded_write_file(gate))
        registry.register(RUN_TERMINAL_COMMAND_SCHEMA, _make_guarded_run_terminal(gate))
    else:
        registry.register(READ_FILE_SCHEMA, _raw_read_file)
        registry.register(WRITE_FILE_SCHEMA, _raw_write_file)
        registry.register(RUN_TERMINAL_COMMAND_SCHEMA, _raw_run_terminal_command)
