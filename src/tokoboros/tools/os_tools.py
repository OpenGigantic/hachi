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
import shlex
import subprocess
from pathlib import Path
from typing import Any

from tokoboros.core.loop import ToolRegistry

_HOME_ALIAS_NAMES = {"desktop", "downloads", "documents"}


def _normalize_user_path(raw_path: str) -> Path:
    """Resolve paths with friendly aliases like 'Desktop' to user home folders."""
    raw = raw_path.strip()
    if not raw:
        return Path(raw_path).resolve()

    if raw.startswith("~"):
        return Path(raw).expanduser().resolve()

    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()

    parts = [part for part in candidate.parts if part and part != "."]
    if parts and parts[0].lower() in _HOME_ALIAS_NAMES:
        home_candidate = Path.home().joinpath(*parts)
        cwd_candidate = Path.cwd().joinpath(*parts)
        if home_candidate.exists() or not cwd_candidate.exists():
            return home_candidate.resolve()

    return candidate.resolve()


def _format_directory_listing(target: Path, max_entries: int = 200) -> str:
    """Render a small, deterministic directory listing for plain-text tool results."""
    try:
        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except Exception as e:
        return f"Error listing directory '{target}': {str(e)}"

    lines = [f"Directory listing for '{target}':"]
    if not entries:
        lines.append("(empty)")
        return "\n".join(lines)

    for item in entries[:max_entries]:
        suffix = "/" if item.is_dir() else ""
        lines.append(f"- {item.name}{suffix}")

    remaining = len(entries) - max_entries
    if remaining > 0:
        lines.append(f"... and {remaining} more entries")

    return "\n".join(lines)


# ==========================================
# Raw Tool Implementations (Ungated)
# ==========================================
# These are the actual executors. They contain no permission logic.
# They can be called directly by internal boot code that needs
# unrestricted access (e.g., reading config files during startup).


def _raw_read_file(path: str) -> str:
    """Read the contents of a local file."""
    try:
        target = _normalize_user_path(path)
        if not target.exists():
            return f"Error: File '{target}' does not exist."
        if target.is_dir():
            return _format_directory_listing(target)
        if not target.is_file():
            return f"Error: '{target}' is not a regular file."

        with open(target, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"


def _raw_write_file(path: str, content: str) -> str:
    """Write string content to a local file, creating parent directories if necessary."""
    try:
        target = _normalize_user_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to '{target}'."
    except Exception as e:
        return f"Error writing file: {str(e)}"


def _raw_run_terminal_command(command: str, cwd: str = ".", timeout: int = 120) -> str:
    """Execute a single command (argv-style) and return its stdout and stderr."""
    try:
        target_cwd = Path(cwd).resolve()
        if not target_cwd.exists() or not target_cwd.is_dir():
            return f"Error: Working directory '{target_cwd}' is invalid."

        raw = command.strip()
        if not raw:
            return "Error: Empty command is not allowed."

        # Hard-block shell chaining/redirection operators since commands run without a shell.
        blocked_fragments = ("&&", "||", "|", ";", "`", "$(", ">", "<")
        for fragment in blocked_fragments:
            if fragment in raw:
                return (
                    "Error: Shell operators are not allowed in run_terminal_command. "
                    "Provide a single executable with arguments only."
                )

        argv = shlex.split(raw, posix=(os.name != "nt"))
        if not argv:
            return "Error: Command parsing produced no executable."
        if os.name == "nt":
            argv = [
                (
                    token[1:-1]
                    if len(token) >= 2 and token.startswith('"') and token.endswith('"')
                    else token
                )
                for token in argv
            ]

        executable = Path(argv[0]).name.lower()
        if executable in {"pwd", "cd"}:
            return f"--- STDOUT ---\n{target_cwd}\n\nExit Code: 0"

        if executable in {"ls", "dir"}:
            list_target = target_cwd
            if len(argv) > 1 and argv[1] not in {".", "./"}:
                list_target = _normalize_user_path(argv[1])
            if not list_target.exists():
                return f"Error: Path '{list_target}' does not exist."
            if list_target.is_file():
                return f"--- STDOUT ---\n{list_target.name}\n\nExit Code: 0"
            return f"--- STDOUT ---\n{_format_directory_listing(list_target)}\n\nExit Code: 0"

        result = subprocess.run(
            argv,
            shell=False,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
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


def _make_guarded_read_file(gate: Any):
    """Create a permission-gated read_file tool."""

    def guarded_read_file(path: str) -> str:
        normalized = _normalize_user_path(path)
        result = gate.check_path(str(normalized), "read")
        if not result.allowed:
            return f"Permission Denied: {result.reason}"
        try:
            target = normalized
            if (
                target.exists()
                and target.is_file()
                and target.stat().st_size > gate.max_file_size_bytes
            ):
                return (
                    "Permission Denied: File exceeds configured max_file_size_mb. "
                    "Update device-permissions.og to raise the limit."
                )
        except Exception:
            pass
        return _raw_read_file(str(normalized))

    return guarded_read_file


def _make_guarded_write_file(gate: Any):
    """Create a permission-gated write_file tool."""

    def guarded_write_file(path: str, content: str) -> str:
        normalized = _normalize_user_path(path)
        result = gate.check_path(str(normalized), "write")
        if not result.allowed:
            return f"Permission Denied: {result.reason}"
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > gate.max_file_size_bytes:
            return (
                "Permission Denied: Content exceeds configured max_file_size_mb. "
                "Split the file or increase the limit in device-permissions.og."
            )
        return _raw_write_file(str(normalized), content)

    return guarded_write_file


def _make_guarded_run_terminal(gate: Any):
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
                "description": "The absolute or relative path to the file to read.",
            }
        },
        "required": ["path"],
    },
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": "Write string content to a local file. Will overwrite the file if it already "
    "exists, and will create parent directories if needed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The absolute or relative path where the file should be written.",
            },
            "content": {
                "type": "string",
                "description": "The string content to write into the file.",
            },
        },
        "required": ["path", "content"],
    },
}

RUN_TERMINAL_COMMAND_SCHEMA = {
    "name": "run_terminal_command",
    "description": "Execute a single command with arguments (no shell operators). Use this to run "
    "scripts, interact with git, move files, or run tests.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash/shell command to execute."},
            "cwd": {
                "type": "string",
                "description": "The working directory where the command should be run. "
                "Defaults to the current directory ('.').",
            },
        },
        "required": ["command"],
    },
}

# ==========================================
# Registration Helper
# ==========================================


def register_os_tools(registry: ToolRegistry, gate: Any | None = None):
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
