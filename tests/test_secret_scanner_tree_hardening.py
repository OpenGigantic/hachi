"""Adversarial tests for the tracked-tree secret scan (P-30 review hardening).

The original scan (see test_secret_scanner_tree.py) proved the happy path.
These tests lock the fail-closed semantics a hard review demanded of it:

- the allowlist suppresses one *occurrence* (path + kind + value
  fingerprint), never every credential of a kind in a file;
- a file with invalid UTF-8 bytes is scanned with replacement decoding, so
  malformed text cannot launder an ASCII credential into a clean pass;
- unreadable, deleted, or oversized tracked files make the scan
  *incomplete* (an error the CLI turns into a failing finding), never a
  silent clean pass;
- a broken git enumeration inside a checkout fails the scan instead of
  silently falling back to a filesystem walk with different semantics;
- symlinks are never followed outside the tree root.

Secrets are assembled by concatenation — the same convention as
test_secret_scanner.py — so this file does not itself trip the scan.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from hachi.cli.main import app
from hachi.core import secret_scanner
from hachi.core.secret_scanner import (
    TreeScanError,
    scan_tree,
    secret_fingerprint,
    tracked_files,
)

runner = CliRunner()

_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_AWS_2 = "AKIA" + "Z" * 16
_ANTHROPIC = "sk-ant-" + "b" * 40

_GIT = shutil.which("git")


@pytest.fixture(autouse=True)
def _scrub_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inherited GIT_* vars (GIT_DIR, GIT_WORK_TREE, ...) from a hook or CI
    wrapper must not leak into the git calls these tests make."""
    for key in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# B1: allowlist granularity is per occurrence (path + kind + fingerprint)
# ---------------------------------------------------------------------------


def test_same_kind_second_occurrence_still_flagged(tmp_path: Path) -> None:
    _write(tmp_path / "fixtures.py", f'a = "{_AWS}"\nb = "{_AWS_2}"\n')
    entry = f"fixtures.py:aws_access_key:{secret_fingerprint(_AWS)}"
    result = scan_tree(tmp_path, [entry])
    # Only the fingerprint-matched occurrence is suppressed; the second AWS
    # key in the same file — same kind, different value — is a finding.
    assert [(f.kind, f.line, f.fingerprint) for f in result.findings] == [
        ("aws_access_key", 2, secret_fingerprint(_AWS_2))
    ]
    assert result.errors == ()


def test_legacy_path_kind_entry_no_longer_suppresses(tmp_path: Path) -> None:
    _write(tmp_path / "fixtures.py", f'a = "{_AWS}"\n')
    result = scan_tree(tmp_path, ["fixtures.py:aws_access_key"])
    assert [(f.kind, f.line) for f in result.findings] == [("aws_access_key", 1)]


def test_wrong_fingerprint_does_not_suppress(tmp_path: Path) -> None:
    _write(tmp_path / "fixtures.py", f'a = "{_AWS}"\n')
    entry = f"fixtures.py:aws_access_key:{secret_fingerprint(_AWS_2)}"
    result = scan_tree(tmp_path, [entry])
    assert len(result.findings) == 1


# ---------------------------------------------------------------------------
# B2: invalid UTF-8 must not launder an ASCII credential
# ---------------------------------------------------------------------------


def test_invalid_utf8_file_with_ascii_credential_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "mixed.bin").write_bytes(b"\xff\xfe\x80" + _AWS.encode() + b"\x81\x82\n")
    result = scan_tree(tmp_path)
    assert [(f.path, f.kind) for f in result.findings] == [("mixed.bin", "aws_access_key")]
    assert result.errors == ()


def test_binary_file_is_counted_as_skipped_not_error(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + _AWS.encode())
    result = scan_tree(tmp_path)
    assert result.findings == ()
    assert result.errors == ()
    assert result.files_binary_skipped == 1


# ---------------------------------------------------------------------------
# B3/B7: unreadable, deleted, or oversized files fail closed
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_GIT is None, reason="git not available")
def test_deleted_tracked_file_makes_scan_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _write(tmp_path / "gone.py", "clean = True\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "gone.py")
    (tmp_path / "gone.py").unlink()
    result = scan_tree(tmp_path)
    assert result.findings == ()
    assert [e.path for e in result.errors] == ["gone.py"]


def test_oversized_file_is_a_scan_error_not_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secret_scanner, "MAX_SCAN_FILE_BYTES", 8)
    _write(tmp_path / "big.py", f'API_KEY = "{_AWS}" and some more text\n')
    result = scan_tree(tmp_path)
    assert result.findings == ()
    assert [e.path for e in result.errors] == ["big.py"]
    assert "size" in result.errors[0].reason


# ---------------------------------------------------------------------------
# B4: git enumeration failure inside a checkout fails the scan
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_GIT is None, reason="git not available")
def test_broken_git_enumeration_raises_instead_of_walking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _write(tmp_path / "leaked.py", f'API_KEY = "{_AWS}"\n')
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "leaked.py")
    # Corrupt the index: `git ls-files` now exits non-zero. The scan must
    # fail loudly rather than fall back to a walk (which would report the
    # secret under different semantics — or, worse, miss tracked state).
    (tmp_path / ".git" / "index").write_bytes(b"garbage")
    with pytest.raises(TreeScanError):
        tracked_files(tmp_path)
    with pytest.raises(TreeScanError):
        scan_tree(tmp_path)


def test_enumeration_mode_is_reported(tmp_path: Path) -> None:
    _write(tmp_path / "plain.py", "x = 1\n")
    files, mode = tracked_files(tmp_path)
    assert mode == "walk"
    assert [p.name for p in files] == ["plain.py"]
    result = scan_tree(tmp_path)
    assert result.enumeration == "walk"
    assert result.files_discovered == 1
    assert result.files_scanned == 1


# ---------------------------------------------------------------------------
# B6: line numbers stay correct deep into a file (precomputed offsets)
# ---------------------------------------------------------------------------


def test_find_secrets_line_number_deep_in_file() -> None:
    lines = ["filler"] * 599 + [f'key = "{_AWS}"']
    hits = secret_scanner.find_secrets("\n".join(lines))
    assert [(h.kind, h.line) for h in hits] == [("aws_access_key", 600)]


# ---------------------------------------------------------------------------
# B10: symlink policy — never read content outside the tree root
# ---------------------------------------------------------------------------


def test_symlink_pointing_outside_tree_is_not_followed(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text(f'SECRET = "{_AWS}"\n', encoding="utf-8")
    try:
        os.symlink(outside, tree / "link.txt")
        # A link whose target *string* is itself credential-shaped: the
        # string is scanned as text, the (nonexistent) target never read.
        os.symlink("/nonexistent/" + _AWS_2, tree / "string-link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    result = scan_tree(tree)
    # The credential inside the outside file must NOT surface: the link was
    # not followed. The credential-shaped target string DOES surface.
    assert [(f.path, f.kind) for f in result.findings] == [("string-link.txt", "aws_access_key")]
    assert result.errors == ()


# ---------------------------------------------------------------------------
# Pattern coverage: every kind in _PATTERNS is actually caught by the scan
# ---------------------------------------------------------------------------

_SAMPLES = {
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc123\n-----END RSA PRIVATE KEY-----",
    "anthropic_key": "sk-ant-" + "a" * 40,
    "openai_key": "sk-" + "a" * 40,
    "aws_access_key": "AKIA" + "A" * 16,
    "github_token": "ghp_" + "a" * 36,
    "github_fine_grained_pat": "github_pat_" + "a" * 40,
    "slack_token": "xoxb-" + "1" * 12,
    "slack_webhook": "https://hooks.slack.com/services/T"
    + "1" * 8
    + "/B"
    + "1" * 8
    + "/"
    + "a" * 24,
    "telegram_bot_token": "123456789:" + "A" * 35,
    "google_api_key": "AIza" + "a" * 35,
    "google_oauth_secret": "GOCSPX-" + "a" * 20,
    "stripe_key": "sk_live_" + "a" * 24,
    "sendgrid_key": "SG." + "a" * 16 + "." + "b" * 16,
    "npm_token": "npm_" + "a" * 36,
    "jwt": "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10,
}


def test_every_pattern_kind_is_flagged_in_tree_scan(tmp_path: Path) -> None:
    kinds = {kind for kind, _pattern in secret_scanner._PATTERNS}
    assert set(_SAMPLES) == kinds, "the sample map must cover every pattern kind"
    for kind, sample in _SAMPLES.items():
        _write(tmp_path / f"sample_{kind}.txt", f'value = "{sample}"\n')
    result = scan_tree(tmp_path)
    assert result.errors == ()
    assert {f.kind for f in result.findings} == kinds


# ---------------------------------------------------------------------------
# CLI: root validation (B5) and incomplete scan (B3)
# ---------------------------------------------------------------------------


def _audit_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neutral security-audit config (same pattern as test_cli_gateway_command)."""
    permissions_file = tmp_path / "device-permissions.og"
    permissions_file.write_text("", encoding="utf-8")

    fake_config = SimpleNamespace(
        permissions=SimpleNamespace(tier="guided"),
        capabilities={},
    )

    class FakePermissionGate:
        def __init__(self, _permissions_file: Path) -> None:
            self._term = SimpleNamespace(allow_all=False)

    monkeypatch.setattr("hachi.cli.main.load_effective_config", lambda: fake_config)
    monkeypatch.setattr("hachi.cli.main.get_permissions_file", lambda: permissions_file)
    monkeypatch.setattr("hachi.cli.main.PermissionGate", FakePermissionGate)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("HACHI_REST_ALLOWLIST", "api.example.com")
    monkeypatch.setenv("HACHI_REST_ALLOW_PRIVATE", "false")
    monkeypatch.delenv("HACHI_SANDBOX_BACKEND", raising=False)


def test_cli_nonexistent_tree_root_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _audit_fixture(monkeypatch, tmp_path)
    result = runner.invoke(
        app,
        ["security-audit", "--json", "--scan-tree", "--tree-root", str(tmp_path / "nope")],
        env={"COLUMNS": "40"},
    )
    assert result.exit_code == 2, result.output
    assert '"status": "pass"' not in result.stdout


@pytest.mark.skipif(_GIT is None, reason="git not available")
def test_cli_incomplete_scan_fails_the_audit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _audit_fixture(monkeypatch, tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    tree = tmp_path / "tree"
    _write(tree / "gone.py", "clean = True\n")
    _git(tree, "init", "-q")
    _git(tree, "add", "gone.py")
    (tree / "gone.py").unlink()

    result = runner.invoke(
        app,
        ["security-audit", "--json", "--fail-on-high", "--scan-tree", "--tree-root", str(tree)],
        env={"COLUMNS": "40"},
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    incomplete = [f for f in payload["findings"] if f["check_id"] == "tree.scan_incomplete"]
    assert len(incomplete) == 1
    assert "gone.py" in incomplete[0]["message"]
    assert payload["tree_scan"]["errors"] == 1
    assert payload["tree_scan"]["enumeration"] == "git"
    # No credential finding: incomplete is distinguishable from dirty.
    assert all(f["check_id"] != "tree.committed_secret" for f in payload["findings"])


def test_cli_clean_scan_reports_stats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _audit_fixture(monkeypatch, tmp_path)
    tree = tmp_path / "tree"
    _write(tree / "clean.py", f'FAKE = "{_ANTHROPIC}"\n')
    _write(
        tree / ".secret-scan-allowlist",
        f"clean.py:anthropic_key:{secret_fingerprint(_ANTHROPIC)}\n",
    )
    result = runner.invoke(
        app,
        ["security-audit", "--json", "--scan-tree", "--tree-root", str(tree)],
        env={"COLUMNS": "40"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["tree_scan"]["files_scanned"] == 2  # clean.py + the allowlist itself
    assert payload["tree_scan"]["errors"] == 0
