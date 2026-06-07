"""Always-on daemon installer for the Tokoboros gateway.

Installs the gateway as a persistent background service that auto-starts on login:

- **macOS**   → launchd user agent
  ``~/Library/LaunchAgents/ai.tokoboros.gateway.plist``
- **Linux**   → systemd user service
  ``~/.config/systemd/user/tokoboros.service``
- **Windows** → Task Scheduler (ONLOGON trigger, current user, no ``shell=True``)

The ``runner`` parameter on :class:`DaemonInstaller` is injectable so tests run
fully offline — no actual launchctl/systemctl/schtasks calls are made.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Platform constants
# ---------------------------------------------------------------------------

_LAUNCHD_LABEL = "ai.tokoboros.gateway"
_SYSTEMD_UNIT = "tokoboros"
_TASK_NAME = "Tokoboros Gateway"

# Type alias: same signature as subprocess.run for the injectable runner.
Runner = Any  # Callable[[list[str], ...], subprocess.CompletedProcess]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class DaemonConfig:
    """Runtime parameters for the gateway daemon.

    Attributes:
        host: Bind address for the gateway HTTP server.
        port: TCP port for the gateway HTTP server.
        log_path: Path where the daemon writes stdout/stderr (resolved at runtime).
    """

    host: str = "127.0.0.1"
    port: int = 8787
    log_path: Path = field(default_factory=lambda: _default_log_path())


def _default_log_path() -> Path:
    """Return the platform-appropriate log path for the gateway daemon."""
    from tokoboros.core.paths import get_tokoboros_paths

    return Path(get_tokoboros_paths().state_dir) / "gateway.log"


# ---------------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------------


def locate_executable() -> Path:
    """Return the absolute path of the ``tokoboros`` CLI entry point.

    Resolution order:
    1. ``shutil.which("tokoboros")`` — preferred; works for ``pip install`` / ``uv tool install``.
    2. ``sys.executable -m tokoboros`` — fallback for editable/development installs.

    Raises:
        RuntimeError: When neither resolution succeeds.
    """
    which = shutil.which("tokoboros")
    if which:
        return Path(which).resolve()

    # Fallback: invoke as a Python module.  We return sys.executable so callers
    # can build ``[sys.executable, "-m", "tokoboros", ...]`` command lists.
    if Path(sys.executable).exists():
        return Path(sys.executable).resolve()

    raise RuntimeError(
        "Cannot find the 'tokoboros' executable. "
        "Make sure it is installed and available on PATH, "
        "or activate the virtual environment first."
    )


def _build_argv(config: DaemonConfig) -> list[str]:
    """Build the gateway command-line arguments list (no shell=True)."""
    exe = locate_executable()
    # If shutil.which found a direct entry point, call it directly.
    # If we fell back to sys.executable, invoke as a module.
    if exe.name in ("python", "python3", "python.exe", "python3.exe"):
        cmd = [str(exe), "-m", "tokoboros"]
    else:
        cmd = [str(exe)]
    return cmd + [
        "gateway",
        "--host", config.host,
        "--port", str(config.port),
        "--runtime",
    ]


# ---------------------------------------------------------------------------
# Service-file builders (pure — no I/O, unit-testable)
# ---------------------------------------------------------------------------


def build_launchd_plist(argv: list[str], log_path: Path) -> str:
    """Build a launchd plist XML string for the given gateway command.

    Args:
        argv: Full command as a list (no shell).
        log_path: Path for stdout/stderr log file.

    Returns:
        XML plist content as a string.
    """
    args_xml = "\n".join(f"        <string>{a}</string>" for a in argv)
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def build_systemd_unit(argv: list[str], description: str = "Tokoboros Gateway") -> str:
    """Build a systemd user-service unit file for the given gateway command.

    Args:
        argv: Full command as a list (written as space-joined ExecStart).
        description: Free-text description for the unit.

    Returns:
        Systemd unit file content as a string.
    """
    exec_start = " ".join(argv)
    return f"""\
[Unit]
Description={description}
After=network.target

[Service]
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


# ---------------------------------------------------------------------------
# Platform-specific path helpers
# ---------------------------------------------------------------------------


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{_SYSTEMD_UNIT}.service"


# ---------------------------------------------------------------------------
# DaemonInstaller
# ---------------------------------------------------------------------------


class DaemonInstaller:
    """Platform-aware daemon manager for the Tokoboros gateway.

    Supports macOS (launchd), Linux (systemd --user), and Windows (schtasks).
    Inject ``runner`` to replace subprocess calls in tests.

    Args:
        config: Gateway host/port/log configuration.
        runner: Callable with the same signature as :func:`subprocess.run`.
            Defaults to :func:`subprocess.run`.
    """

    def __init__(
        self,
        config: DaemonConfig | None = None,
        runner: Runner = None,
    ) -> None:
        self._config = config or DaemonConfig()
        self._runner = runner or subprocess.run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self) -> str:
        """Install and start the gateway daemon.  Returns a human-readable summary."""
        platform = _detect_platform()
        if platform == "macos":
            return self._install_launchd()
        if platform == "linux":
            return self._install_systemd()
        if platform == "windows":
            return self._install_windows()
        raise RuntimeError(f"Unsupported platform: {sys.platform!r}")

    def uninstall(self) -> str:
        """Stop and remove the daemon service."""
        platform = _detect_platform()
        if platform == "macos":
            return self._uninstall_launchd()
        if platform == "linux":
            return self._uninstall_systemd()
        if platform == "windows":
            return self._uninstall_windows()
        raise RuntimeError(f"Unsupported platform: {sys.platform!r}")

    def start(self) -> str:
        """Start the daemon without reinstalling."""
        platform = _detect_platform()
        if platform == "macos":
            self._run(["launchctl", "start", _LAUNCHD_LABEL])
            return f"Started launchd agent '{_LAUNCHD_LABEL}'."
        if platform == "linux":
            self._run(["systemctl", "--user", "start", _SYSTEMD_UNIT])
            return f"Started systemd unit '{_SYSTEMD_UNIT}'."
        if platform == "windows":
            self._run(["schtasks", "/run", "/tn", _TASK_NAME])
            return f"Started Windows task '{_TASK_NAME}'."
        raise RuntimeError(f"Unsupported platform: {sys.platform!r}")

    def stop(self) -> str:
        """Stop the daemon without uninstalling."""
        platform = _detect_platform()
        if platform == "macos":
            self._run(["launchctl", "stop", _LAUNCHD_LABEL])
            return f"Stopped launchd agent '{_LAUNCHD_LABEL}'."
        if platform == "linux":
            self._run(["systemctl", "--user", "stop", _SYSTEMD_UNIT])
            return f"Stopped systemd unit '{_SYSTEMD_UNIT}'."
        if platform == "windows":
            self._run(["schtasks", "/end", "/tn", _TASK_NAME])
            return f"Stopped Windows task '{_TASK_NAME}'."
        raise RuntimeError(f"Unsupported platform: {sys.platform!r}")

    def status(self) -> str:
        """Return a human-readable status string for the daemon."""
        platform = _detect_platform()
        try:
            if platform == "macos":
                result = self._run(
                    ["launchctl", "list", _LAUNCHD_LABEL], capture=True
                )
                lines = result.stdout.strip() if result.stdout else ""
                return f"launchd: {lines or 'not running'}"
            if platform == "linux":
                result = self._run(
                    ["systemctl", "--user", "is-active", _SYSTEMD_UNIT], capture=True
                )
                state = result.stdout.strip() if result.stdout else "unknown"
                return f"systemd: {state}"
            if platform == "windows":
                result = self._run(
                    ["schtasks", "/query", "/tn", _TASK_NAME, "/fo", "LIST"],
                    capture=True,
                )
                return f"schtasks: {result.stdout.strip() if result.stdout else 'unknown'}"
        except subprocess.CalledProcessError as exc:
            return f"not installed (exit {exc.returncode})"
        raise RuntimeError(f"Unsupported platform: {sys.platform!r}")

    # ------------------------------------------------------------------
    # macOS (launchd)
    # ------------------------------------------------------------------

    def _install_launchd(self) -> str:
        argv = _build_argv(self._config)
        plist_content = build_launchd_plist(argv, self._config.log_path)
        plist_path = _launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content, encoding="utf-8")
        # Unload first in case it was already registered (idempotent).
        try:
            self._run(["launchctl", "unload", str(plist_path)])
        except subprocess.CalledProcessError:
            pass  # Not loaded yet — fine.
        self._run(["launchctl", "load", "-w", str(plist_path)])
        return (
            f"Installed launchd agent '{_LAUNCHD_LABEL}'.\n"
            f"  plist: {plist_path}\n"
            f"  log:   {self._config.log_path}\n"
            "Gateway will start now and on every future login."
        )

    def _uninstall_launchd(self) -> str:
        plist_path = _launchd_plist_path()
        try:
            self._run(["launchctl", "unload", "-w", str(plist_path)])
        except subprocess.CalledProcessError:
            pass  # Already unloaded.
        if plist_path.exists():
            plist_path.unlink()
        return f"Removed launchd agent '{_LAUNCHD_LABEL}'."

    # ------------------------------------------------------------------
    # Linux (systemd --user)
    # ------------------------------------------------------------------

    def _install_systemd(self) -> str:
        argv = _build_argv(self._config)
        unit_content = build_systemd_unit(argv)
        unit_path = _systemd_unit_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_content, encoding="utf-8")
        self._run(["systemctl", "--user", "daemon-reload"])
        self._run(["systemctl", "--user", "enable", "--now", _SYSTEMD_UNIT])
        return (
            f"Installed systemd user service '{_SYSTEMD_UNIT}'.\n"
            f"  unit: {unit_path}\n"
            "Gateway will start now and on every future login."
        )

    def _uninstall_systemd(self) -> str:
        try:
            self._run(["systemctl", "--user", "disable", "--now", _SYSTEMD_UNIT])
        except subprocess.CalledProcessError:
            pass
        unit_path = _systemd_unit_path()
        if unit_path.exists():
            unit_path.unlink()
        try:
            self._run(["systemctl", "--user", "daemon-reload"])
        except subprocess.CalledProcessError:
            pass
        return f"Removed systemd user service '{_SYSTEMD_UNIT}'."

    # ------------------------------------------------------------------
    # Windows (Task Scheduler)
    # ------------------------------------------------------------------

    def _install_windows(self) -> str:
        argv = _build_argv(self._config)
        # schtasks requires a single quoted string for /tr.
        # We join with spaces — no shell=True; schtasks handles it.
        tr = " ".join(argv)
        self._run([
            "schtasks", "/create",
            "/tn", _TASK_NAME,
            "/tr", tr,
            "/sc", "ONLOGON",
            "/it",   # Interactive (runs in current user session)
            "/f",    # Force overwrite if already exists
        ])
        return (
            f"Installed Task Scheduler task '{_TASK_NAME}'.\n"
            "Gateway will start at next logon. To start now: "
            "tokoboros daemon start"
        )

    def _uninstall_windows(self) -> str:
        try:
            self._run(["schtasks", "/end", "/tn", _TASK_NAME])
        except subprocess.CalledProcessError:
            pass
        self._run(["schtasks", "/delete", "/tn", _TASK_NAME, "/f"])
        return f"Removed Task Scheduler task '{_TASK_NAME}'."

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        cmd: list[str],
        capture: bool = False,
    ) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
        """Execute ``cmd`` via the injectable runner.

        Never uses ``shell=True`` (security guardrail).
        """
        return self._runner(
            cmd,
            check=True,
            text=True,
            capture_output=capture,
        )


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _detect_platform() -> str:
    """Return one of ``'macos'``, ``'linux'``, or ``'windows'``."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    # Broad fallback: treat unknown Unixes as Linux (systemd may not be present
    # but the unit file placement is still the XDG convention).
    return "linux"
