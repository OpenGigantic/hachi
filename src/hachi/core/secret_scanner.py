"""Outbound secret-leak guard.

Detects and redacts common credential formats (API keys, tokens, private keys)
so the agent can't accidentally exfiltrate a secret it happened to read — e.g. by
posting a config file's contents to a Slack/Discord channel. The patterns are
deliberately specific (provider-prefixed keys, PEM blocks) to keep false positives
low; generic high-entropy heuristics are intentionally avoided.

The redaction core is pure and dependency-free so it's trivially testable
and reusable across any outbound surface (messaging now; REST/browser are
natural follow-ups). The tracked-tree scan at the bottom of this module is
the exception: it shells out to ``git`` and reads files, and lives here only
because it shares the pattern set and the allowlist helpers.

The same pattern set also backs the tracked-tree scan at the bottom of this
module (PRODUCT-AUDIT P-30): applied to the files tracked by git, it lets the
CI security gate catch a credential *committed to the tree*, which the
environment/config audit cannot see.
"""

from __future__ import annotations

import bisect
import hashlib
import os
import re
import subprocess
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

REDACTION_PLACEHOLDER = "[REDACTED SECRET]"

# (kind, compiled pattern). Order matters: the multi-line PEM block runs first so
# its body is redacted as a unit, and the more specific anthropic key precedes the
# generic openai-style key.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        # The body is bounded to base64 characters (plus whitespace) and a
        # sane maximum length: an unbounded DOTALL span would "match" from a
        # BEGIN marker in one string literal to an END marker in another,
        # flagging prose and source code that merely *discusses* PEM blocks.
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
            r"[A-Za-z0-9+/=\s]{0,16384}?"
            r"-----END [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("github_fine_grained_pat", re.compile(r"github_pat_[A-Za-z0-9_]{40,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    (
        "slack_webhook",
        re.compile(
            r"https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{20,}"
        ),
    ),
    # Telegram bot token: numeric bot id, colon, 35-character secret part.
    ("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("google_oauth_secret", re.compile(r"GOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("stripe_key", re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}")),
    ("sendgrid_key", re.compile(r"SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("npm_token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    # JWT bearer token: three base64url segments, header begins `eyJ` (= base64 `{"`).
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]


def find_secret_kinds(text: str) -> list[str]:
    """Return the kinds of secret detected in ``text`` (deduped, in pattern order)."""
    return [kind for kind, pat in _PATTERNS if pat.search(text)]


def contains_secret(text: str) -> bool:
    """True if ``text`` contains at least one recognized secret."""
    return any(pat.search(text) for _kind, pat in _PATTERNS)


def redact_secrets(text: str, placeholder: str = REDACTION_PLACEHOLDER) -> tuple[str, int]:
    """Replace every recognized secret in ``text`` with ``placeholder``.

    Args:
        text: The text to scrub.
        placeholder: The replacement string.

    Returns:
        ``(redacted_text, count)`` where ``count`` is how many secrets were replaced.
    """
    total = 0
    redacted = text
    for _kind, pat in _PATTERNS:
        redacted, n = pat.subn(placeholder, redacted)
        total += n
    return redacted, total


def outbound_redaction_disabled() -> bool:
    """True if the user opted out of outbound secret redaction (power-user escape hatch).

    Controlled by ``HACHI_ALLOW_OUTBOUND_SECRETS`` — the single env var honored by
    every outbound surface (messaging, REST, scheduled-webhook delivery).
    """
    return os.environ.get("HACHI_ALLOW_OUTBOUND_SECRETS", "").lower() in {"1", "true", "yes"}


def redact_outbound(text: str) -> tuple[str, int]:
    """Redact secrets from outbound text unless the user opted out. Returns ``(text, count)``."""
    if outbound_redaction_disabled():
        return text, 0
    return redact_secrets(text)


def redact_structure(obj: object) -> object:
    """Recursively walk *obj* and redact secrets from every string leaf.

    Handles ``str``, ``dict``, ``list``, and ``tuple`` inputs. All other types
    are returned unchanged. Dicts are rebuilt with both keys and values walked;
    keys that are strings are also redacted (unusual but defensive).

    Args:
        obj: Any Python value — typically a tool-input dict or result preview.

    Returns:
        A new object of the same type with all string leaves scrubbed.
    """
    if isinstance(obj, str):
        redacted, _ = redact_secrets(obj)
        return redacted
    if isinstance(obj, dict):
        return {redact_structure(k): redact_structure(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_structure(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_structure(item) for item in obj)
    return obj


# ---------------------------------------------------------------------------
# Tracked-tree scan (PRODUCT-AUDIT P-30)
#
# `security-audit` inspects the process environment and the resolved config;
# it never opens a file, so a credential committed to the tree passed the CI
# security gate untouched. The functions below apply the same patterns to the
# tracked files themselves. The CLI wires them in behind `--scan-tree`, with
# `.secret-scan-allowlist` at the repo root covering the deliberate fixtures.
#
# The scan fails closed. Anything it could not actually check — a tracked
# file that is missing or unreadable, a file over the size cap, a broken git
# enumeration inside a checkout, a malformed allowlist — is reported as an
# error, never silently treated as a clean pass.
# ---------------------------------------------------------------------------

ALLOWLIST_FILENAME = ".secret-scan-allowlist"

#: Files larger than this are not scanned; they are reported as scan errors
#: (fail closed) rather than silently skipped. 10 MiB is far above any
#: legitimate source file while keeping memory use bounded.
MAX_SCAN_FILE_BYTES = 10 * 1024 * 1024

_PATTERN_KINDS = frozenset(kind for kind, _pattern in _PATTERNS)
_BINARY_PROBE_BYTES = 8192
_FINGERPRINT_HEX_LEN = 12
_HEX_DIGITS = frozenset("0123456789abcdef")
_WALK_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
    }
)


class TreeScanError(RuntimeError):
    """The tracked-tree scan could not be trusted (fail closed)."""


class AllowlistError(TreeScanError):
    """The tree-scan allowlist is unreadable or malformed (fail closed)."""


def secret_fingerprint(value: str) -> str:
    """Return the allowlist fingerprint of a matched secret value.

    The first 12 hex characters of the value's SHA-256. A fingerprint names
    one exact occurrence for allowlisting without storing (or echoing) the
    value itself.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LEN]


@dataclass(frozen=True)
class SecretHit:
    """One pattern match inside a text: kind, 1-based line, value fingerprint."""

    kind: str
    line: int
    fingerprint: str


@dataclass(frozen=True)
class TreeSecretFinding:
    """One secret-shaped string found in a tracked file.

    ``path`` is repo-relative with POSIX separators so findings read the same
    on every platform. The matched text itself is never stored: kind + line +
    fingerprint is enough to locate the credential without echoing it into
    CI logs.
    """

    path: str
    kind: str
    line: int
    fingerprint: str


@dataclass(frozen=True)
class ScanError:
    """One tracked file the scan could not check (path + reason)."""

    path: str
    reason: str


@dataclass(frozen=True)
class TreeScanResult:
    """The outcome of a tracked-tree scan.

    ``findings`` and ``errors`` are distinct on purpose: a scan with errors
    is *incomplete*, not clean, and callers (the CLI) must fail on it.
    ``enumeration`` records how the file list was obtained (``"git"`` or
    ``"walk"``) so a report can show which semantics were in force.
    """

    findings: tuple[TreeSecretFinding, ...]
    errors: tuple[ScanError, ...]
    files_discovered: int
    files_scanned: int
    files_binary_skipped: int
    enumeration: str


def find_secrets(text: str) -> list[SecretHit]:
    """Return every recognized secret in ``text`` with line and fingerprint.

    Unlike :func:`find_secret_kinds` (kinds only, for redaction), this keeps
    positions so a tree-scan finding can point at the exact line, and the
    value fingerprint so an allowlist entry can name this exact occurrence.
    Line-start offsets are precomputed once and bisected per match — counting
    newlines per match would make the scan quadratic in file size. Sorted by
    (line, kind) for deterministic output.
    """
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer("\n", text))
    hits = [
        SecretHit(
            kind=kind,
            line=bisect.bisect_right(line_starts, match.start()),
            fingerprint=secret_fingerprint(match.group()),
        )
        for kind, pattern in _PATTERNS
        for match in pattern.finditer(text)
    ]
    hits.sort(key=lambda hit: (hit.line, hit.kind))
    return hits


def tracked_files(root: Path) -> tuple[list[Path], str]:
    """Return the files tracked by git under ``root``, plus the mode used.

    The mode is ``"git"`` when ``git ls-files`` enumerated the files and
    ``"walk"`` for the filesystem-walk fallback. The fallback exists only
    for trees that are not git checkouts at all (an exported source tree):
    if ``root`` *is* a checkout (a ``.git`` entry exists there) but git
    enumeration fails or times out, :class:`TreeScanError` is raised —
    silently switching semantics would let a broken repo pass as a clean,
    differently-scoped scan.
    """
    is_checkout = (root / ".git").exists()
    failure: str | None = None
    proc: subprocess.CompletedProcess[bytes] | None = None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        failure = f"git ls-files could not run: {exc}"
    if proc is not None:
        if proc.returncode == 0:
            # surrogateescape (os.fsdecode) so non-UTF-8 filenames resolve
            # to their real paths instead of being mangled by replacement.
            names = [os.fsdecode(chunk) for chunk in proc.stdout.split(b"\0") if chunk]
            return [root / name for name in names], "git"
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        failure = f"git ls-files exited {proc.returncode}: {detail[:200]}"
    if is_checkout:
        raise TreeScanError(
            f"cannot enumerate tracked files under {root}: {failure}. "
            "Refusing to fall back to a filesystem walk inside a git checkout."
        )
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _WALK_EXCLUDED_DIRS]
        found.extend(Path(dirpath) / name for name in filenames)
    return sorted(found), "walk"


def _parse_allowlist_entry(entry: str) -> tuple[str, str, str] | None:
    """Parse one allowlist entry into ``(path, kind, fingerprint)``.

    The only accepted form is ``path:kind:fingerprint`` — one exact
    occurrence. Anything else (a bare path, a directory, ``path:kind``
    without a fingerprint, an unknown kind, a non-hex fingerprint) returns
    ``None``: allowlisting a whole file or kind again would let a real
    credential of that kind hide behind a fixture entry.
    """
    head, sep, fingerprint = entry.rpartition(":")
    if (
        not sep
        or len(fingerprint) != _FINGERPRINT_HEX_LEN
        or any(char not in _HEX_DIGITS for char in fingerprint)
    ):
        return None
    path, sep2, kind = head.rpartition(":")
    if not sep2 or not path or kind not in _PATTERN_KINDS:
        return None
    return path, kind, fingerprint


def load_allowlist(path: Path) -> list[str]:
    """Load and validate a tree-scan allowlist file (one entry per line).

    Blank lines and ``#`` comments are ignored; a trailing ``#`` comment
    after an entry is stripped before parsing. A missing file yields an
    empty allowlist — the scan then simply has no exceptions. Any entry
    that is not exactly ``path:kind:fingerprint`` raises
    :class:`AllowlistError`: a malformed entry must fail the scan loudly
    rather than silently suppress nothing (or, worse, everything).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise AllowlistError(f"cannot read allowlist {path}: {exc}") from exc
    entries: list[str] = []
    problems: list[str] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        if _parse_allowlist_entry(entry) is None:
            problems.append(f"line {lineno}: {entry!r}")
        else:
            entries.append(entry)
    if problems:
        raise AllowlistError(
            f"malformed entries in {path} (expected path:kind:fingerprint): " + "; ".join(problems)
        )
    return entries


def is_allowlisted(path: str, kind: str, fingerprint: str, allowlist: Collection[str]) -> bool:
    """True if ``allowlist`` covers this exact occurrence.

    An entry suppresses a hit only when path, kind, *and* the value
    fingerprint all match — a different credential of the same kind in the
    same file still trips the gate. Malformed entries (which
    :func:`load_allowlist` rejects) suppress nothing here.
    """
    for entry in allowlist:
        parsed = _parse_allowlist_entry(entry)
        if parsed is None:
            continue
        entry_path, entry_kind, entry_fingerprint = parsed
        if entry_path == path and entry_kind == kind and entry_fingerprint == fingerprint:
            return True
    return False


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def scan_tree(root: Path, allowlist: Collection[str] = ()) -> TreeScanResult:
    """Scan every tracked file under ``root`` for credential-shaped strings.

    Policies (all fail closed):

    - Binary files (a NUL byte in the first block) are skipped and counted:
      the patterns target text credentials, and decoding arbitrary binaries
      would only manufacture noise.
    - A file that is not valid UTF-8 is scanned anyway, decoded with
      ``errors="replace"``: ASCII credentials survive replacement decoding,
      so malformed text must not become a clean pass.
    - A tracked file that cannot be read (missing, permission error) or is
      over :data:`MAX_SCAN_FILE_BYTES` is recorded in ``errors`` — the scan
      is incomplete, and the caller must fail on it.
    - Symlinks are never followed outside the tree: a link whose resolved
      target is outside ``root`` is not read; instead the link-target
      *string itself* is scanned as text (a target string can itself be
      credential-shaped). Links resolving inside the tree are read normally.
    - Findings covered by ``allowlist`` (see :func:`is_allowlisted`) are
      suppressed. Results are sorted for deterministic output.

    Raises :class:`TreeScanError` when the tracked files cannot be
    enumerated inside a git checkout (see :func:`tracked_files`).
    """
    files, enumeration = tracked_files(root)
    root_resolved = root.resolve()
    findings: list[TreeSecretFinding] = []
    errors: list[ScanError] = []
    files_scanned = 0
    files_binary_skipped = 0

    def collect(relative: str, text: str) -> None:
        for hit in find_secrets(text):
            if is_allowlisted(relative, hit.kind, hit.fingerprint, allowlist):
                continue
            findings.append(
                TreeSecretFinding(
                    path=relative, kind=hit.kind, line=hit.line, fingerprint=hit.fingerprint
                )
            )

    for file_path in files:
        relative = file_path.relative_to(root).as_posix()
        if file_path.is_symlink():
            resolved = file_path.resolve()
            if not _is_within(resolved, root_resolved):
                files_scanned += 1
                collect(relative, os.readlink(file_path))
                continue
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            errors.append(ScanError(relative, f"cannot stat file: {exc.strerror or exc}"))
            continue
        if size > MAX_SCAN_FILE_BYTES:
            errors.append(
                ScanError(
                    relative,
                    f"file size {size} bytes exceeds the {MAX_SCAN_FILE_BYTES}-byte scan limit",
                )
            )
            continue
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            errors.append(ScanError(relative, f"cannot read file: {exc.strerror or exc}"))
            continue
        if b"\0" in data[:_BINARY_PROBE_BYTES]:
            files_binary_skipped += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        files_scanned += 1
        collect(relative, text)

    findings.sort(key=lambda finding: (finding.path, finding.line, finding.kind))
    errors.sort(key=lambda error: error.path)
    return TreeScanResult(
        findings=tuple(findings),
        errors=tuple(errors),
        files_discovered=len(files),
        files_scanned=files_scanned,
        files_binary_skipped=files_binary_skipped,
        enumeration=enumeration,
    )
