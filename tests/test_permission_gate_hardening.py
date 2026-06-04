from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.permissions import PermissionGate, generate_permissions_og
from tokoboros.tools.os_tools import register_os_tools


def _write_permissions_file(
    tmp_path: Path,
    *,
    max_file_size_mb: int = 50,
    allow_write: bool = True,
    allow_delete: bool = False,
) -> Path:
    allowed_dir = tmp_path / "allowed"
    blocked_dir = tmp_path / "blocked"
    allowed_dir.mkdir(parents=True, exist_ok=True)
    blocked_dir.mkdir(parents=True, exist_ok=True)

    content = generate_permissions_og("guided", custom_paths=[str(allowed_dir)])
    content = content.replace(
        "blocked_paths="
        + ", ".join(
            str(p)
            for p in (Path.home() / ".ssh", Path.home() / ".tokoboros", Path.home() / "AppData")
        ),
        f"blocked_paths={blocked_dir}",
    )
    content = content.replace("max_file_size_mb=50", f"max_file_size_mb={max_file_size_mb}")
    content = content.replace(
        "allow_write=true", f"allow_write={'true' if allow_write else 'false'}"
    )
    content = content.replace(
        "allow_delete=false", f"allow_delete={'true' if allow_delete else 'false'}"
    )

    permissions_file = tmp_path / "device-permissions.og"
    permissions_file.write_text(content, encoding="utf-8")
    return permissions_file


def test_check_command_rejects_prefix_spoof(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    result = gate.check_command("python-malware --steal-data")
    assert result.allowed is False


def test_allowlist_prefix_collision_is_rejected(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    result = gate.check_command("gitx status")
    assert result.allowed is False


def test_guided_tier_blocks_python_interpreter(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    result = gate.check_command("python -c \"print('hello')\"")
    assert result.allowed is False
    assert "blocked in guided tier" in result.reason


def test_guided_tier_blocks_nested_interpreter_via_wrapper(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    result = gate.check_command("npx --yes node -e \"console.log('x')\"")
    assert result.allowed is False
    assert "Nested interpreter 'node' is blocked" in result.reason


def test_blocked_command_substring_match_is_enforced(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    result = gate.check_command("echo hello && shutdown /r /t 0")
    assert result.allowed is False
    assert "blocked command pattern" in result.reason


def test_quoted_executable_name_remains_allowlisted(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    result = gate.check_command('"git" status')
    assert result.allowed is True


def test_max_file_size_enforced_for_read_and_write(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path, max_file_size_mb=0)
    gate = PermissionGate(permissions_file)
    registry = ToolRegistry()
    register_os_tools(registry, gate=gate)

    allowed_file = tmp_path / "allowed" / "sample.txt"
    allowed_file.write_text("x", encoding="utf-8")

    read_result = registry._tools["read_file"](path=str(allowed_file))
    assert "Permission Denied" in read_result

    write_result = registry._tools["write_file"](path=str(allowed_file), content="x")
    assert "Permission Denied" in write_result


def test_blocked_subpath_overrides_allowed_root(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "allowed"
    blocked_dir = allowed_dir / "blocked"
    blocked_dir.mkdir(parents=True, exist_ok=True)
    target_file = blocked_dir / "secret.txt"
    target_file.write_text("nope", encoding="utf-8")

    content = generate_permissions_og("guided", custom_paths=[str(allowed_dir)])
    content = content.replace(
        "blocked_paths="
        + ", ".join(
            str(p)
            for p in (Path.home() / ".ssh", Path.home() / ".tokoboros", Path.home() / "AppData")
        ),
        f"blocked_paths={blocked_dir}",
    )
    permissions_file = tmp_path / "device-permissions.og"
    permissions_file.write_text(content, encoding="utf-8")
    gate = PermissionGate(permissions_file)

    denied = gate.check_path(str(target_file), "read")
    assert denied.allowed is False
    assert "inside blocked directory" in denied.reason


def test_relative_traversal_outside_allowed_root_is_denied(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    traversal = tmp_path / "allowed" / ".." / "outside.txt"

    denied = gate.check_path(str(traversal), "read")
    assert denied.allowed is False
    assert "not in your allowed directories" in denied.reason


def test_allowed_root_boundary_enforced_for_inside_and_outside_paths(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    inside = tmp_path / "allowed" / "inside.txt"
    inside.write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside" / "outside.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("no", encoding="utf-8")

    allowed = gate.check_path(str(inside), "read")
    denied = gate.check_path(str(outside), "read")

    assert allowed.allowed is True
    assert denied.allowed is False
    assert "not in your allowed directories" in denied.reason


def test_symlink_escape_outside_allowed_root_is_denied(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    link = tmp_path / "allowed" / "escape-link"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable in this environment")

    denied = gate.check_path(str(link / "secret.txt"), "read")
    assert denied.allowed is False
    assert "not in your allowed directories" in denied.reason


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only drive/case normalization check")
def test_windows_drive_and_case_variants_resolve_to_allowed_path(tmp_path: Path) -> None:
    permissions_file = _write_permissions_file(tmp_path)
    gate = PermissionGate(permissions_file)

    target = tmp_path / "allowed" / "MiXeDCaSe.txt"
    target.write_text("ok", encoding="utf-8")

    raw = str(target)
    drive, rest = raw[:2], raw[2:]
    flipped_drive = drive.lower() if drive[0].isupper() else drive.upper()
    mixed_case_path = flipped_drive + rest.swapcase()

    result = gate.check_path(mixed_case_path, "read")
    assert result.allowed is True


def test_file_size_boundaries_cover_zero_exact_and_over_limit(tmp_path: Path) -> None:
    zero_limit_gate = PermissionGate(_write_permissions_file(tmp_path, max_file_size_mb=0))

    zero_file = tmp_path / "allowed" / "zero.txt"
    zero_file.write_text("", encoding="utf-8")
    one_byte_file = tmp_path / "allowed" / "one-byte.txt"
    one_byte_file.write_text("x", encoding="utf-8")

    assert zero_limit_gate.check_path(str(zero_file), "read").allowed is True
    assert zero_limit_gate.check_path(str(zero_file), "write").allowed is True
    assert zero_limit_gate.check_path(str(one_byte_file), "read").allowed is False
    assert zero_limit_gate.check_path(str(one_byte_file), "write").allowed is False

    one_mb_gate = PermissionGate(_write_permissions_file(tmp_path, max_file_size_mb=1))
    exact = tmp_path / "allowed" / "exact.bin"
    exact.write_bytes(b"a" * (1024 * 1024))
    over = tmp_path / "allowed" / "over.bin"
    over.write_bytes(b"a" * (1024 * 1024 + 1))

    assert one_mb_gate.check_path(str(exact), "read").allowed is True
    assert one_mb_gate.check_path(str(exact), "write").allowed is True
    assert one_mb_gate.check_path(str(over), "read").allowed is False
    assert one_mb_gate.check_path(str(over), "write").allowed is False


def test_write_and_delete_operation_flags_enforced(tmp_path: Path) -> None:
    gate = PermissionGate(_write_permissions_file(tmp_path, allow_write=False, allow_delete=False))

    target = tmp_path / "allowed" / "mutable.txt"
    target.write_text("content", encoding="utf-8")

    denied_write = gate.check_path(str(target), "write")
    denied_delete = gate.check_path(str(target), "delete")

    assert denied_write.allowed is False
    assert "Write operations are disabled" in denied_write.reason
    assert denied_delete.allowed is False
    assert "Delete operations are disabled" in denied_delete.reason


def test_timeout_bound_long_running_command_is_terminated(tmp_path: Path) -> None:
    permissions_file = tmp_path / "device-permissions.og"
    content = generate_permissions_og("unrestricted", custom_paths=[str(tmp_path / "allowed")])
    content = content.replace("timeout=120", "timeout=1")
    permissions_file.write_text(content, encoding="utf-8")
    gate = PermissionGate(permissions_file)

    registry = ToolRegistry()
    register_os_tools(registry, gate=gate)

    command = f'"{sys.executable}" -c "exec(\'import time\\ntime.sleep(2)\')"'

    result = registry._tools["run_terminal_command"](command=command)
    assert "timed out" in result.lower()
