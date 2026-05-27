"""
Tokoboros Ephemeral Sandbox Execution Engine

This module provides a secure, isolated execution environment for arbitrary Python code.
It relies on Astral's `uv` toolchain to instantly spin up virtual environments and install
dependencies on the fly, preventing host system pollution.
"""

import os
import time
import shutil
import hashlib
import tempfile
import subprocess
from typing import List, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    """Represents the outcome of a sandbox execution."""
    stdout: str
    stderr: str
    exit_code: int
    duration_sec: float
    execution_dir: str
    success: bool

    @property
    def formatted_output(self) -> str:
        """Returns a consolidated view of the execution result for the LLM."""
        status = "SUCCESS" if self.success else "FAILED"
        output = f"Execution Status: {status} (Exit Code: {self.exit_code}) in {self.duration_sec:.2f}s\n"
        output += "-" * 40 + "\n"
        if self.stdout.strip():
            output += f"STDOUT:\n{self.stdout}\n"
        if self.stderr.strip():
            output += f"STDERR:\n{self.stderr}\n"
        if not self.stdout.strip() and not self.stderr.strip():
            output += "No output produced.\n"
        return output


class SandboxExecutor:
    """
    Manages the ephemeral execution of Python scripts using `uv`.
    """

    def __init__(self, sandbox_root: Optional[str] = None):
        """
        Initializes the Sandbox Executor.
        
        Args:
            sandbox_root: The directory where ephemeral environments will be built.
                          Defaults to ~/.tokoboros/sandbox.
        """
        if sandbox_root is None:
            self.sandbox_root = Path.home() / ".tokoboros" / "sandbox"
        else:
            self.sandbox_root = Path(sandbox_root)
            
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self._uv_path = self._resolve_uv()

    def _resolve_uv(self) -> str:
        """
        Locates the `uv` executable. 
        Raises EnvironmentError if `uv` is not installed on the system.
        """
        import sys
        
        # Check PATH first
        uv_path = shutil.which("uv")
        if uv_path:
            return uv_path
            
        # Check alongside the current python interpreter (e.g. inside a venv)
        uv_path_local = Path(sys.executable).parent / ("uv.exe" if os.name == "nt" else "uv")
        if uv_path_local.exists():
            return str(uv_path_local)

        raise EnvironmentError(
            "CRITICAL: The 'uv' package manager was not found on PATH or alongside Python. "
            "The Tokoboros Sandbox requires 'uv' to execute code securely. "
            "Please install it (e.g., pip install uv) or ensure it is accessible."
        )

    def _generate_env_hash(self, script_content: str, dependencies: List[str]) -> str:
        """Generates a stable hash for the specific execution payload."""
        payload = f"{script_content}-{'|'.join(sorted(dependencies))}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def execute(self, script_content: str, dependencies: Optional[List[str]] = None, timeout: int = 120) -> SandboxResult:
        """
        Executes arbitrary Python code in an isolated, ephemeral environment.

        Args:
            script_content: The raw Python code to execute.
            dependencies: A list of PyPI packages required (e.g., ['pandas', 'requests']).
            timeout: Maximum execution time in seconds.

        Returns:
            SandboxResult: Contains the stdout, stderr, and execution metadata.
        """
        deps = dependencies or []
        env_hash = self._generate_env_hash(script_content, deps)
        execution_dir = self.sandbox_root / f"exec_{env_hash}"
        execution_dir.mkdir(exist_ok=True)

        script_path = execution_dir / "main.py"
        script_path.write_text(script_content, encoding="utf-8")

        # Construct the uv run command.
        # Syntax: uv run --python 3.12 --with <dep> --with <dep> main.py
        # This implicitly manages downloading python and the dependencies without polluting global state.
        cmd = [self._uv_path, "run"]
        for dep in deps:
            cmd.extend(["--with", dep])
        cmd.append(str(script_path))

        start_time = time.time()
        
        try:
            # We explicitly do not use capture_output=True so we can stream if necessary in future,
            # but for now we wait() and capture standard pipes.
            process = subprocess.run(
                cmd,
                cwd=str(execution_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                # Isolate environment variables to prevent accidental host pollution or credential leakage,
                # while allowing basic paths needed for Python execution.
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), # Needed on Windows
                    "USERPROFILE": os.environ.get("USERPROFILE", ""), # Needed for standard OS paths
                }
            )
            
            duration = time.time() - start_time
            return SandboxResult(
                stdout=process.stdout,
                stderr=process.stderr,
                exit_code=process.returncode,
                duration_sec=duration,
                execution_dir=str(execution_dir),
                success=(process.returncode == 0)
            )
            
        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            return SandboxResult(
                stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else "",
                stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else f"Execution timed out after {timeout} seconds.",
                exit_code=124, # Standard timeout exit code
                duration_sec=duration,
                execution_dir=str(execution_dir),
                success=False
            )
        except Exception as e:
            duration = time.time() - start_time
            return SandboxResult(
                stdout="",
                stderr=f"Sandbox execution crashed: {str(e)}",
                exit_code=-1,
                duration_sec=duration,
                execution_dir=str(execution_dir),
                success=False
            )
