"""Tests for the tracked-tree secret scan (core/secret_scanner, P-30).

The scan applies the outbound redaction patterns to git-tracked files so a
credential committed to the tree fails the CI security gate. Secrets here are
assembled by concatenation — the same convention as test_secret_scanner.py —
so this file does not itself trip the scan it tests.
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
from hachi.core.secret_scanner import (
    ALLOWLIST_FILENAME,
    AllowlistError,
    find_secrets,
    is_allowlisted,
    load_allowlist,
    scan_tree,
    secret_fingerprint,
    tracked_files,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _scrub_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inherited GIT_* vars (GIT_DIR, GIT_WORK_TREE, ...) from a hook or CI
    wrapper must not leak into the git calls these tests make."""
    for key in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_GITHUB = "ghp_" + "a" * 36
_ANTHROPIC = "sk-ant-" + "b" * 40
_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\n" + "MIIabc123\n" + "-----END RSA PRIVATE KEY-----"

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# find_secrets: position-aware detection
# ---------------------------------------------------------------------------


def test_find_secrets_reports_kind_and_line() -> None:
    text = f"nothing here\nkey = {_AWS}\nother = {_GITHUB}\n"
    hits = find_secrets(text)
    assert [(hit.kind, hit.line) for hit in hits] == [
        ("aws_access_key", 2),
        ("github_token", 3),
    ]


def test_find_secrets_multiline_private_key_reports_start_line() -> None:
    text = f"header\n{_PRIVATE_KEY}\ntrailer\n"
    hits = find_secrets(text)
    assert [(hit.kind, hit.line) for hit in hits] == [("private_key", 2)]


def test_find_secrets_ignores_placeholders_and_truncated_examples() -> None:
    # Documentation placeholders and truncated examples must not be flagged:
    # the patterns require full-length credentials.
    text = "\n".join(
        [
            "api_key = your-api-key-here",
            "token = sk-ant-...",  # truncated example
            "aws = AKIA123",  # too short to be a real access key id
            "gh = ghp_" + "x" * 10,  # truncated token
            "secret = ${PROVIDER_API_KEY}",  # env reference, not a value
        ]
    )
    assert find_secrets(text) == []


# ---------------------------------------------------------------------------
# scan_tree over a scratch tree (walk fallback — no git repo involved)
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_tree_detects_planted_secret_with_path_kind_line(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "config.py", f'# comment\nAPI_KEY = "{_AWS}"\n')
    _write(tmp_path / "README.md", "no secrets here\n")
    result = scan_tree(tmp_path)
    assert [(f.path, f.kind, f.line) for f in result.findings] == [
        ("src/config.py", "aws_access_key", 2)
    ]
    assert result.errors == ()
    assert result.enumeration == "walk"


def test_scan_tree_skips_binary_files(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + _AWS.encode())
    _write(tmp_path / "clean.txt", "hello\n")
    result = scan_tree(tmp_path)
    assert result.findings == ()
    assert result.files_binary_skipped == 1


def test_scan_tree_allowlist_fingerprint_scoping(tmp_path: Path) -> None:
    _write(tmp_path / "fixtures.py", f'a = "{_AWS}"\nb = "{_GITHUB}"\n')
    # A fingerprint entry suppresses that one occurrence only; the GitHub
    # token in the same file still trips the scan.
    entry = f"fixtures.py:aws_access_key:{secret_fingerprint(_AWS)}"
    result = scan_tree(tmp_path, [entry])
    assert [(f.kind, f.line) for f in result.findings] == [("github_token", 2)]
    # With both occurrences allowlisted the file is clean.
    both = [entry, f"fixtures.py:github_token:{secret_fingerprint(_GITHUB)}"]
    assert scan_tree(tmp_path, both).findings == ()


def test_scan_tree_allowlist_directory_prefix_no_longer_suppresses(tmp_path: Path) -> None:
    # Directory (and bare-path / path:kind) entries are not accepted forms:
    # they suppress nothing. load_allowlist rejects them outright (see
    # test_load_allowlist_rejects_malformed_entries).
    _write(tmp_path / "fixtures" / "fake.py", f'a = "{_AWS}"\n')
    result = scan_tree(tmp_path, ["fixtures/"])
    assert [f.path for f in result.findings] == ["fixtures/fake.py"]


def test_load_allowlist_parses_comments_and_blanks(tmp_path: Path) -> None:
    allowlist_file = tmp_path / ALLOWLIST_FILENAME
    allowlist_file.write_text(
        "# a comment\n\n"
        "  tests/x.py:aws_access_key:0123456789ab  \n"
        "tests/y.py:slack_token:0123456789cd  # trailing justification\n",
        encoding="utf-8",
    )
    assert load_allowlist(allowlist_file) == [
        "tests/x.py:aws_access_key:0123456789ab",
        "tests/y.py:slack_token:0123456789cd",
    ]
    assert load_allowlist(tmp_path / "does-not-exist") == []


def test_load_allowlist_rejects_malformed_entries(tmp_path: Path) -> None:
    allowlist_file = tmp_path / ALLOWLIST_FILENAME
    for bad in (
        "tests/x.py",  # bare path
        "tests/",  # directory prefix
        "tests/x.py:aws_access_key",  # kind without fingerprint
        "tests/x.py:aws_access_key:not-hex!!",  # fingerprint not 12 hex chars
        "tests/x.py:unknown_kind:0123456789ab",  # unknown kind
    ):
        allowlist_file.write_text(bad + "\n", encoding="utf-8")
        with pytest.raises(AllowlistError):
            load_allowlist(allowlist_file)


def test_is_allowlisted_entry_forms() -> None:
    fp = "0123456789ab"
    entry = f"a/b.py:aws_access_key:{fp}"
    assert is_allowlisted("a/b.py", "aws_access_key", fp, [entry])
    # Any deviation — path, kind, or the value fingerprint — is not covered.
    assert not is_allowlisted("a/b.py", "aws_access_key", "fedcba987654", [entry])
    assert not is_allowlisted("a/b.py", "github_token", fp, [entry])
    assert not is_allowlisted("a/c.py", "aws_access_key", fp, [entry])
    # Broader forms suppress nothing (load_allowlist rejects them anyway).
    assert not is_allowlisted("a/b.py", "aws_access_key", fp, ["a/b.py"])
    assert not is_allowlisted("a/b.py", "aws_access_key", fp, ["a/"])
    assert not is_allowlisted("a/b.py", "aws_access_key", fp, ["a/b.py:aws_access_key"])


# ---------------------------------------------------------------------------
# git-backed enumeration: tracked files only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_scan_tree_scans_tracked_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Do not let repo discovery wander above the scratch tree.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _write(tmp_path / "tracked.py", f'a = "{_AWS}"\n')
    _write(tmp_path / "untracked.py", f'b = "{_GITHUB}"\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    files, mode = tracked_files(tmp_path)
    assert mode == "git"
    assert [p.name for p in files] == ["tracked.py"]
    result = scan_tree(tmp_path)
    assert [(f.path, f.kind) for f in result.findings] == [("tracked.py", "aws_access_key")]
    assert result.errors == ()


# ---------------------------------------------------------------------------
# The real tree: clean under the committed allowlist
# ---------------------------------------------------------------------------


def test_repo_tree_scans_clean_with_committed_allowlist() -> None:
    allowlist = load_allowlist(_REPO_ROOT / ALLOWLIST_FILENAME)
    assert allowlist, "the committed allowlist must exist and be non-empty"
    result = scan_tree(_REPO_ROOT, allowlist)
    assert result.findings == ()
    # Fail loudly on an incomplete scan (e.g. broken git) instead of
    # silently passing — the scan must have actually covered the tree.
    assert result.errors == ()
    assert result.enumeration == "git"


def test_committed_allowlist_entries_are_wellformed_and_live() -> None:
    # load_allowlist raises on any malformed entry, so reaching the loop at
    # all proves the committed file is fingerprint-form throughout.
    entries = load_allowlist(_REPO_ROOT / ALLOWLIST_FILENAME)
    raw = scan_tree(_REPO_ROOT)
    assert raw.errors == ()
    live = {(f.path, f.kind, f.fingerprint) for f in raw.findings}
    for entry in entries:
        head, _, fingerprint = entry.rpartition(":")
        path, _, kind = head.rpartition(":")
        assert (path, kind, fingerprint) in live, f"stale allowlist entry: {entry}"


# ---------------------------------------------------------------------------
# CLI wiring: security-audit --scan-tree
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


def test_security_audit_scan_tree_flags_planted_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _audit_fixture(monkeypatch, tmp_path)
    tree = tmp_path / "tree"
    _write(tree / "leaked.py", f'API_KEY = "{_ANTHROPIC}"\n')

    result = runner.invoke(
        app,
        ["security-audit", "--json", "--fail-on-high", "--scan-tree", "--tree-root", str(tree)],
        env={"COLUMNS": "40"},
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    tree_findings = [f for f in payload["findings"] if f["check_id"] == "tree.committed_secret"]
    assert len(tree_findings) == 1
    assert "leaked.py" in tree_findings[0]["message"]
    assert "anthropic_key" in tree_findings[0]["message"]
    # The credential itself must never be echoed into the report / CI logs.
    assert _ANTHROPIC not in result.stdout


def test_security_audit_scan_tree_clean_tree_and_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _audit_fixture(monkeypatch, tmp_path)
    tree = tmp_path / "tree"
    _write(tree / "fixture.py", f'FAKE = "{_AWS}"\n')

    # Negative control: without the allowlist, the same tree produces the
    # finding and fails under --fail-on-high — so the clean run below is a
    # meaningful assertion, not a vacuous one.
    control = runner.invoke(
        app,
        ["security-audit", "--json", "--fail-on-high", "--scan-tree", "--tree-root", str(tree)],
        env={"COLUMNS": "40"},
    )
    assert control.exit_code == 2, control.output
    control_payload = json.loads(control.stdout)
    assert any(f["check_id"] == "tree.committed_secret" for f in control_payload["findings"])

    _write(
        tree / ALLOWLIST_FILENAME,
        f"fixture.py:aws_access_key:{secret_fingerprint(_AWS)}\n",
    )
    result = runner.invoke(
        app,
        ["security-audit", "--json", "--fail-on-high", "--scan-tree", "--tree-root", str(tree)],
        env={"COLUMNS": "40"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert all(f["check_id"] != "tree.committed_secret" for f in payload["findings"])
    assert payload["tree_scan"]["errors"] == 0


def test_security_audit_without_scan_tree_does_not_open_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default audit behaviour is unchanged: no tree check unless asked."""
    _audit_fixture(monkeypatch, tmp_path)
    tree = tmp_path / "tree"
    _write(tree / "leaked.py", f'API_KEY = "{_ANTHROPIC}"\n')
    monkeypatch.chdir(tree)

    result = runner.invoke(app, ["security-audit", "--json"], env={"COLUMNS": "40"})

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert all(f["check_id"] != "tree.committed_secret" for f in payload["findings"])
