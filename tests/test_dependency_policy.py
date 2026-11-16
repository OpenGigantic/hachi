"""Dependency-usage policy guards.

`chromadb` 1.5.9 carries four unpatched advisories, all of them defects in its
**HTTP server** API (`/api/v2/...`): pre-auth code injection via a model
repository supplied to a server endpoint with `trust_remote_code`, cross-tenant
read/write, and an RBAC provider that does not scope permissions by tenant.

Hachi only ever uses the **embedded** `PersistentClient` — in-process, no
listener, single-tenant — so that surface is not reachable here. That
acceptance is only sound while these properties hold, so they are asserted
structurally: adding server mode or `trust_remote_code` must fail CI rather
than quietly widen exposure.

See SECURITY.md → "Dependency CVE posture" and PRODUCT-AUDIT P-02.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _hits(pattern: str) -> list[str]:
    """Return `path:line` for every regex match across src/."""
    rx = re.compile(pattern)
    found: list[str] = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line):
                found.append(f"{path.relative_to(SRC.parent)}:{lineno}: {line.strip()}")
    return found


def test_no_chromadb_http_client() -> None:
    """Only the embedded PersistentClient is allowed.

    An HttpClient would make the process a client of a chromadb *server*, which
    is the deployment mode all four advisories apply to.
    """
    hits = _hits(r"chromadb\.HttpClient|from\s+chromadb\.(?:api|server)")
    assert not hits, "chromadb server/client API must not be used:\n  " + "\n  ".join(hits)


def test_no_trust_remote_code() -> None:
    """`trust_remote_code` is the trigger for CVE-2026-45829 / -45833."""
    hits = _hits(r"trust_remote_code")
    assert not hits, "trust_remote_code must never be enabled:\n  " + "\n  ".join(hits)


def test_no_chromadb_server_entrypoint() -> None:
    """Hachi must never start chromadb's HTTP server or expose its routes."""
    hits = _hits(r'"chroma"\s*,\s*"run"|chromadb\.Server|/api/v2/')
    assert not hits, "chromadb server entrypoint/routes must not appear:\n  " + "\n  ".join(hits)


def test_chromadb_is_imported_lazily_where_used() -> None:
    """Vault imports stay inside functions so session boot never pays for chroma.

    This is a performance contract, but it also keeps the embedded client from
    being constructed at import time where a future refactor could turn it into
    a module-level singleton listener.
    """
    vault = (SRC / "hachi" / "core" / "memory_vault.py").read_text(encoding="utf-8")
    top_level = [
        ln
        for ln in vault.splitlines()
        if re.match(r"^import chromadb|^from chromadb", ln)
    ]
    assert not top_level, f"chromadb must not be imported at module scope: {top_level}"
