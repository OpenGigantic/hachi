"""
Device Permission Gate

The single enforcement layer for all device access in Tokoboros.
Reads ~/.tokoboros/device-permissions.og on boot and gates every tool call
that touches the user's filesystem or terminal.

Architecture:
    device-permissions.og → PermissionGate (immutable) → wraps all tools

Security Invariants:
    1. Only device-permissions.og can grant device access.
    2. blocked_paths always wins over allowed_paths.
    3. Missing file = Sandbox tier (fail safe, never fail open).
    4. All paths resolved to canonical absolute form (no .. traversal).
    5. The gate is immutable after construction — no runtime modification.
"""

from dataclasses import dataclass
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple


# ==========================================
# Data Models (Immutable)
# ==========================================

@dataclass(frozen=True)
class PermissionResult:
    """
    The outcome of a single permission check.

    Attributes:
        allowed: Whether the operation is permitted.
        reason: Human-readable explanation. Shown to the LLM on denial
                so it can inform the user what to change.
    """
    allowed: bool
    reason: str


@dataclass(frozen=True)
class PermissionRequest:
    """A runtime approval request emitted when policy denies a sensitive action."""

    request_id: str
    operation: str
    tool: str
    target: str
    reason: str
    policy_summary: str


@dataclass(frozen=True)
class PermissionDecision:
    """User decision returned by approval UIs (TUI modal, future APIs)."""

    action: Literal["approve", "deny", "allow_once"]
    rationale: str = ""


@dataclass(frozen=True)
class FilesystemRules:
    """Immutable ruleset for filesystem access."""
    allowed_paths: Tuple[Path, ...]
    blocked_paths: Tuple[Path, ...]
    allow_all: bool
    allow_write: bool
    allow_delete: bool
    max_file_size_mb: int


@dataclass(frozen=True)
class TerminalRules:
    """Immutable ruleset for terminal command execution."""
    enabled: bool
    allowed_commands: Tuple[str, ...]
    blocked_commands: Tuple[str, ...]
    allow_all: bool
    timeout: int


@dataclass(frozen=True)
class MCPRules:
    """Immutable ruleset for MCP filesystem server."""
    filesystem_enabled: bool


# ==========================================
# Constants
# ==========================================

_HOME = Path.home()
_PERMISSIONS_FILE_NAME = "device-permissions.og"
_PERMISSIONS_HINT = f"~/.tokoboros/{_PERMISSIONS_FILE_NAME}"

# Commands that are safe for everyday use
_SAFE_COMMANDS = (
    # Filesystem navigation & management
    "dir", "ls", "mkdir", "md", "move", "copy", "ren", "rename",
    "type", "cat", "head", "tail", "echo", "find", "where", "which",
    "tree", "cd", "pushd", "popd",
    # App launching (Windows: start, macOS: open, Linux: xdg-open)
    "start", "open", "xdg-open", "explorer",
    # Developer tools
    "python", "python3", "pip", "pip3",
    "git", "npm", "npx", "node",
    "code", "notepad",
    # System info (read-only)
    "cls", "clear", "whoami", "hostname", "ping",
)

# Commands that can cause irreversible damage
_DANGEROUS_COMMANDS = (
    "rm -rf", "del /s /q", "del /f /s /q",
    "format", "diskpart",
    "shutdown", "restart",
    "taskkill",
    "reg delete", "reg add",
    "net user", "net localgroup",
    "cipher /w",
    "sfc", "dism", "bcdedit",
    "powershell -enc", "powershell -e ",
)

# Paths that should always be blocked by default
_DEFAULT_BLOCKED = (
    _HOME / ".ssh",
    _HOME / ".tokoboros",
    _HOME / "AppData",
)

# ==========================================
# Tier Default Configurations
# ==========================================
# Each tier defines the default config values per permission category.
# User-provided values in device-permissions.og override these.

_TIER_DEFAULTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "sandbox": {
        "filesystem": {
            "allowed_paths": str(_HOME / "Desktop"),
            "blocked_paths": ", ".join(str(p) for p in _DEFAULT_BLOCKED),
            "allow_write": "true",
            "allow_delete": "false",
            "max_file_size_mb": "50",
        },
        "terminal": {
            "enabled": "false",
            "allowed_commands": "",
            "blocked_commands": "",
            "timeout": "120",
        },
        "mcp_filesystem": {
            "enabled": "true",
        },
    },
    "guided": {
        "filesystem": {
            "allowed_paths": ", ".join(str(p) for p in [
                _HOME / "Desktop", _HOME / "Downloads", _HOME / "Documents",
            ]),
            "blocked_paths": ", ".join(str(p) for p in _DEFAULT_BLOCKED),
            "allow_write": "true",
            "allow_delete": "false",
            "max_file_size_mb": "50",
        },
        "terminal": {
            "enabled": "true",
            "allowed_commands": ", ".join(_SAFE_COMMANDS),
            "blocked_commands": ", ".join(_DANGEROUS_COMMANDS),
            "timeout": "120",
        },
        "mcp_filesystem": {
            "enabled": "true",
        },
    },
    "unrestricted": {
        "filesystem": {
            "allowed_paths": "*",
            "blocked_paths": str(_HOME / ".ssh"),
            "allow_write": "true",
            "allow_delete": "true",
            "max_file_size_mb": "500",
        },
        "terminal": {
            "enabled": "true",
            "allowed_commands": "*",
            "blocked_commands": "format, shutdown",
            "timeout": "120",
        },
        "mcp_filesystem": {
            "enabled": "true",
        },
    },
}


# ==========================================
# Helpers
# ==========================================

def _resolve_path(raw: str) -> Path:
    """Resolve a raw path string to an absolute canonical path."""
    raw = raw.strip()
    if raw.startswith("~"):
        return Path(raw).expanduser().resolve()
    return Path(raw).resolve()


def _parse_csv(raw: str) -> Tuple[str, ...]:
    """Parse a comma-separated string into a tuple of stripped values."""
    if not raw or not raw.strip():
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _is_subpath(child: Path, parent: Path) -> bool:
    """Check if child is equal to or a descendant of parent."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _get_config(node_map: Dict, node_name: str, tier: str) -> Dict[str, str]:
    """
    Get merged config for a permission category.
    User-provided values override tier defaults.
    """
    defaults = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["sandbox"]).get(node_name, {})
    parsed = node_map.get(node_name)
    user_config = parsed.config if parsed else {}
    return {**defaults, **user_config}


# ==========================================
# PermissionGate
# ==========================================

class PermissionGate:
    """
    The central permission enforcement layer for Tokoboros.

    Constructed once on boot from device-permissions.og. Immutable after init.
    All tool functions that touch the device must call check_path() or
    check_command() before executing.

    Usage:
        gate = PermissionGate(Path("~/.tokoboros/device-permissions.og"))
        result = gate.check_path("/Users/x/Desktop/file.txt", "read")
        if not result.allowed:
            return f"⛔ Permission Denied: {result.reason}"
    """

    def __init__(self, permissions_file: Path):
        """
        Parse device-permissions.og and build immutable rule sets.
        Falls back to Sandbox defaults if the file is missing or malformed.
        """
        node_map: Dict = {}

        if permissions_file.exists():
            try:
                from tokoboros.og_engine.parser import parse_og_file
                og_file = parse_og_file(str(permissions_file))
                node_map = {n.name: n for n in og_file.nodes}
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error("Failed to parse device permissions: %s", e)
                # Malformed file → Sandbox fallback

        # Determine tier
        device_node = node_map.get("device_access")
        self._tier = (
            device_node.config.get("tier", "sandbox") if device_node else "sandbox"
        )
        if self._tier not in _TIER_DEFAULTS:
            self._tier = "sandbox"

        # Build immutable rule sets
        self._fs = self._build_filesystem_rules(node_map)
        self._term = self._build_terminal_rules(node_map)
        self._mcp = self._build_mcp_rules(node_map)

    # ── Rule Builders ──

    def _build_filesystem_rules(self, node_map: Dict) -> FilesystemRules:
        """Construct FilesystemRules from parsed config + tier defaults."""
        cfg = _get_config(node_map, "filesystem", self._tier)
        raw_allowed = cfg.get("allowed_paths", "")
        raw_blocked = cfg.get("blocked_paths", "")

        allow_all = raw_allowed.strip() == "*"
        allowed = () if allow_all else tuple(_resolve_path(p) for p in _parse_csv(raw_allowed))
        blocked = tuple(_resolve_path(p) for p in _parse_csv(raw_blocked))

        return FilesystemRules(
            allowed_paths=allowed,
            blocked_paths=blocked,
            allow_all=allow_all,
            allow_write=cfg.get("allow_write", "true").lower() == "true",
            allow_delete=cfg.get("allow_delete", "false").lower() == "true",
            max_file_size_mb=int(cfg.get("max_file_size_mb", "50")),
        )

    def _build_terminal_rules(self, node_map: Dict) -> TerminalRules:
        """Construct TerminalRules from parsed config + tier defaults."""
        cfg = _get_config(node_map, "terminal", self._tier)
        raw_allowed = cfg.get("allowed_commands", "")
        raw_blocked = cfg.get("blocked_commands", "")

        allow_all = raw_allowed.strip() == "*"
        allowed = () if allow_all else _parse_csv(raw_allowed)
        blocked = _parse_csv(raw_blocked)

        return TerminalRules(
            enabled=cfg.get("enabled", "false").lower() == "true",
            allowed_commands=allowed,
            blocked_commands=blocked,
            allow_all=allow_all,
            timeout=int(cfg.get("timeout", "120")),
        )

    def _build_mcp_rules(self, node_map: Dict) -> MCPRules:
        """Construct MCPRules from parsed config + tier defaults."""
        cfg = _get_config(node_map, "mcp_filesystem", self._tier)
        return MCPRules(
            filesystem_enabled=cfg.get("enabled", "true").lower() == "true",
        )

    # ── Public Check Methods ──

    def check_path(self, path: str, operation: str) -> PermissionResult:
        """
        Check if a filesystem path is accessible for a given operation.

        Args:
            path: Absolute or relative path to check.
            operation: One of 'read', 'write', 'delete', 'list'.

        Evaluation order:
            1. Resolve to canonical absolute path.
            2. Blocked paths check → DENY (always wins).
            3. Allow-all check → proceed to operation check.
            4. Allowed paths check → if match, proceed to operation check.
            5. Default → DENY.

        Returns:
            PermissionResult with .allowed and .reason.
        """
        try:
            resolved = _resolve_path(path)
        except Exception:
            return PermissionResult(False, f"Invalid path: '{path}'.")

        # 1. Blocked paths always win
        for bp in self._fs.blocked_paths:
            if _is_subpath(resolved, bp):
                return PermissionResult(
                    False,
                    f"'{resolved}' is inside blocked directory '{bp}'. "
                    f"This restriction cannot be overridden."
                )

        # 2. Path accessibility check
        path_allowed = self._fs.allow_all
        if not path_allowed:
            for ap in self._fs.allowed_paths:
                if _is_subpath(resolved, ap):
                    path_allowed = True
                    break

        if not path_allowed:
            return PermissionResult(
                False,
                f"'{resolved}' is not in your allowed directories. "
                f"Add it to allowed_paths in {_PERMISSIONS_HINT}."
            )

        # 3. Operation-specific checks
        if operation == "write" and not self._fs.allow_write:
            return PermissionResult(
                False,
                f"Write operations are disabled. "
                f"Set allow_write=true in {_PERMISSIONS_HINT}."
            )

        if operation == "delete" and not self._fs.allow_delete:
            return PermissionResult(
                False,
                f"Delete operations are disabled. "
                f"Set allow_delete=true in {_PERMISSIONS_HINT}."
            )

        return PermissionResult(True, f"Path '{resolved}' is accessible for '{operation}'.")

    def check_command(self, command: str) -> PermissionResult:
        """
        Check if a terminal command is allowed to execute.

        Args:
            command: The raw shell command string.

        Evaluation order:
            1. Is terminal enabled? → DENY if not.
            2. Check blocked_commands (substring match) → DENY.
            3. Is allowed_commands '*'? → ALLOW.
            4. Check allowed_commands (prefix match) → ALLOW.
            5. Default → DENY.

        Returns:
            PermissionResult with .allowed and .reason.
        """
        if not self._term.enabled:
            return PermissionResult(
                False,
                f"Terminal access is disabled in '{self._tier}' tier. "
                f"Set terminal enabled=true in {_PERMISSIONS_HINT}."
            )

        cmd_lower = command.strip().lower()

        # Blocked commands (substring match — catches variations)
        for bc in self._term.blocked_commands:
            if bc.lower() in cmd_lower:
                return PermissionResult(
                    False,
                    f"'{bc}' is a blocked command pattern. "
                    f"This restriction is in place for your safety."
                )

        # Allow-all shortcut
        if self._term.allow_all:
            return PermissionResult(True, f"Command allowed (unrestricted mode).")

        # Allowed commands (prefix match)
        for ac in self._term.allowed_commands:
            if cmd_lower.startswith(ac.lower()):
                return PermissionResult(True, f"Command '{ac}' is allowed.")

        return PermissionResult(
            False,
            f"'{command.split()[0]}' is not in your allowed commands. "
            f"Add it to allowed_commands in {_PERMISSIONS_HINT}."
        )

    def is_mcp_filesystem_enabled(self) -> bool:
        """Whether the MCP filesystem server should be started."""
        return self._mcp.filesystem_enabled

    def get_allowed_paths(self) -> List[Path]:
        """
        Resolved absolute paths the agent can access.
        Used by the MCP boot sequence to configure the filesystem server.
        Returns all user-accessible paths (excluding blocked).
        """
        if self._fs.allow_all:
            # For unrestricted, give MCP common user dirs (MCP needs explicit paths)
            return [_HOME / "Desktop", _HOME / "Downloads", _HOME / "Documents"]
        return list(self._fs.allowed_paths)

    def get_system_prompt_injection(self) -> str:
        """
        Returns a system prompt block informing the LLM of its boundaries.
        Prevents the LLM from wasting tokens on operations that will be blocked.
        """
        lines = ["\n--- DEVICE PERMISSIONS ---"]
        lines.append(f"Access Tier: {self._tier.upper()}")

        if self._fs.allow_all:
            lines.append("Filesystem: FULL ACCESS (some paths still blocked for safety)")
        else:
            path_names = [p.name for p in self._fs.allowed_paths]
            lines.append(f"Accessible Folders: {', '.join(path_names)}")

        if self._term.enabled:
            lines.append("Terminal: ENABLED (some commands may be restricted)")
        else:
            lines.append("Terminal: DISABLED — do NOT use run_terminal_command")

        if not self._fs.allow_delete:
            lines.append("Delete Operations: DISABLED — do NOT delete files")

        lines.append(
            "If a tool returns '⛔ Permission Denied', do NOT retry. "
            "Inform the user and suggest they update " + _PERMISSIONS_HINT
        )
        return "\n".join(lines)

    # ── Properties ──

    @property
    def tier(self) -> str:
        """The active permission tier: 'sandbox', 'guided', or 'unrestricted'."""
        return self._tier

    @property
    def terminal_timeout(self) -> int:
        """Max seconds a terminal command is allowed to run."""
        return self._term.timeout

    @property
    def summary(self) -> str:
        """Human-readable one-liner for the boot banner."""
        if self._fs.allow_all:
            paths_str = "All"
        else:
            paths_str = ", ".join(p.name for p in self._fs.allowed_paths)
        terminal_str = "ON" if self._term.enabled else "OFF"
        return f"Tier: {self._tier.capitalize()} | Folders: {paths_str} | Terminal: {terminal_str}"


class InteractivePermissionGate:
    """Permission gate wrapper that supports interactive escalation decisions.

    The base PermissionGate remains the source of truth. Escalations only apply
    in-memory for the current process and never modify device-permissions.og.
    """

    def __init__(
        self,
        base_gate: PermissionGate,
        approval_handler: Optional[Callable[[PermissionRequest], PermissionDecision]] = None,
        event_emitter: Optional[Callable[[str, Dict[str, str]], None]] = None,
    ):
        self._base = base_gate
        self._approval_handler = approval_handler
        self._emit = event_emitter or (lambda *_a, **_kw: None)
        self._allowed_session_paths: set[tuple[str, str]] = set()
        self._allowed_session_commands: set[str] = set()

    @property
    def summary(self) -> str:
        return self._base.summary

    @property
    def terminal_timeout(self) -> int:
        return self._base.terminal_timeout

    @property
    def tier(self) -> str:
        return self._base.tier

    def is_mcp_filesystem_enabled(self) -> bool:
        return self._base.is_mcp_filesystem_enabled()

    def get_allowed_paths(self) -> List[Path]:
        return self._base.get_allowed_paths()

    def get_system_prompt_injection(self) -> str:
        return self._base.get_system_prompt_injection()

    def _request_decision(
        self,
        operation: str,
        tool: str,
        target: str,
        reason: str,
    ) -> PermissionDecision:
        request = PermissionRequest(
            request_id=uuid.uuid4().hex,
            operation=operation,
            tool=tool,
            target=target,
            reason=reason,
            policy_summary=self._base.summary,
        )

        self._emit("permission.requested", {
            "request_id": request.request_id,
            "operation": operation,
            "tool": tool,
            "target": target,
            "reason": reason,
            "policy_summary": request.policy_summary,
        })

        if not self._approval_handler:
            decision = PermissionDecision(action="deny", rationale="No approval handler available")
        else:
            try:
                decision = self._approval_handler(request)
            except Exception as exc:
                decision = PermissionDecision(action="deny", rationale=f"Approval handler failed: {exc}")

        self._emit("permission.decided", {
            "request_id": request.request_id,
            "operation": operation,
            "tool": tool,
            "target": target,
            "decision": decision.action,
            "reason": decision.rationale or reason,
        })

        return decision

    def check_path(self, path: str, operation: str) -> PermissionResult:
        try:
            resolved_target = str(_resolve_path(path))
        except Exception:
            resolved_target = path
        if (operation, resolved_target) in self._allowed_session_paths:
            return PermissionResult(True, "Allowed for current runtime session.")

        base_result = self._base.check_path(path, operation)
        if base_result.allowed:
            return base_result

        decision = self._request_decision(
            operation=operation,
            tool="filesystem",
            target=resolved_target,
            reason=base_result.reason,
        )

        if decision.action == "approve":
            self._allowed_session_paths.add((operation, resolved_target))
            return PermissionResult(True, "Approved for session by operator.")
        if decision.action == "allow_once":
            return PermissionResult(True, "Allowed once by operator.")
        return PermissionResult(False, decision.rationale or base_result.reason)

    def check_command(self, command: str) -> PermissionResult:
        normalized = command.strip()
        if normalized in self._allowed_session_commands:
            return PermissionResult(True, "Allowed for current runtime session.")

        base_result = self._base.check_command(command)
        if base_result.allowed:
            return base_result

        decision = self._request_decision(
            operation="execute",
            tool="run_terminal_command",
            target=normalized,
            reason=base_result.reason,
        )

        if decision.action == "approve":
            self._allowed_session_commands.add(normalized)
            return PermissionResult(True, "Approved for session by operator.")
        if decision.action == "allow_once":
            return PermissionResult(True, "Allowed once by operator.")
        return PermissionResult(False, decision.rationale or base_result.reason)


# ==========================================
# Template Generator
# ==========================================

def generate_permissions_og(tier: str, custom_paths: Optional[List[str]] = None) -> str:
    """
    Generate the contents of a device-permissions.og file for the given tier.

    Args:
        tier: One of 'sandbox', 'guided', 'unrestricted'.
        custom_paths: Optional list of custom paths for the filesystem node.
                      If None, tier defaults are used.

    Returns:
        The full .og file content as a string.
    """
    defaults = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["sandbox"])
    fs = defaults["filesystem"].copy()
    term = defaults["terminal"]
    mcp = defaults["mcp_filesystem"]

    if custom_paths:
        fs["allowed_paths"] = ", ".join(custom_paths)

    lines = [
        "# ============================================================",
        "#  Tokoboros Device Permissions",
        "#  Controls what the agent can access on your device.",
        "#  Edit this file anytime, or run: tokoboros permissions",
        "# ============================================================",
        "",
        "::meta",
        "  title: Device Permissions Configuration",
        "  version: 1.0.0",
        "  domain: security|permissions|device|filesystem|terminal",
        "  scope: core",
        "",
        "::index",
        "  # node-id          | type     | scope  | conf | keywords",
        "  device_access       | concept  | all    | 1.0  | tier, level, mode",
        "  filesystem          | concept  | all    | 1.0  | folders, paths, read, write",
        "  terminal            | concept  | all    | 1.0  | shell, cmd, bash, execute",
        "  mcp_filesystem      | concept  | all    | 1.0  | mcp, server, context",
        "",
        "# ────────────────────────────────────────────────────────────",
        "::node[device_access]",
        "->[:role=permission]",
        "::dense",
        f"Master device access tier configuration. Currently set to: {tier}",
        "::config",
        f"tier={tier}",
        "",
        "# ────────────────────────────────────────────────────────────",
        "::node[filesystem]",
        "->[:role=permission]",
        "::dense",
        "Controls which local directories the agent is permitted to read, write, and delete.",
        "::config",
        f"allowed_paths={fs['allowed_paths']}",
        f"blocked_paths={fs['blocked_paths']}",
        f"allow_write={fs['allow_write']}",
        f"allow_delete={fs['allow_delete']}",
        f"max_file_size_mb={fs['max_file_size_mb']}",
        "",
        "# ────────────────────────────────────────────────────────────",
        "::node[terminal]",
        "->[:role=permission]",
        "::dense",
        "Controls whether the agent can execute shell commands, and which commands are allowed or blocked.",
        "::config",
        f"enabled={term['enabled']}",
        f"allowed_commands={term['allowed_commands']}",
        f"blocked_commands={term['blocked_commands']}",
        f"timeout={term['timeout']}",
        "",
        "# ────────────────────────────────────────────────────────────",
        "::node[mcp_filesystem]",
        "->[:role=permission]",
        "::dense",
        "Controls whether the native MCP filesystem server is started for IDE-like context integration.",
        "::config",
        f"enabled={mcp['enabled']}",
        "",
    ]
    return "\n".join(lines)
