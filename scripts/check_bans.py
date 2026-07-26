#!/usr/bin/env python
"""Ban-lint — the repo's written bans, made mechanical (CF-17).

A ban with no check is advice. This is the check. It enforces the bans recorded in
CLAUDE.md "Out of scope" against tracked source, so a reintroduction fails the gate
instead of surviving until the next audit.

Blocking in the gate from day one (unlike the advisory Phase-2/3 rails): its baseline
is zero the moment the banned pattern is deleted, so there is no burn-down to ratchet.

Stdlib only — never imports the product.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Each ban: (id, regex pattern, root-relative dirs to scan, human-readable reason).
# Keep this to bans CLAUDE.md actually states -- a ban-lint that encodes bans nobody
# agreed to is the same failure mode in reverse (T-67 spec Sec 9).
#
# tokoboros-env-shim matches the discriminating SHAPE of the banned shim
# (`TOKOBOROS_` uppercase + trailing underscore + more uppercase/digits/underscore),
# not the bare brand word -- the word legitimately appears elsewhere (a user-facing
# brain.og self-heal in gateway/workspace.py, a pytest norecursedirs entry, and their
# tests), and those must NOT fire this rail. See T-67 spec Sec 3.3.
BANS: list[tuple[str, str, tuple[str, ...], str]] = [
    (
        "tokoboros-env-shim",
        r"TOKOBOROS_[A-Z0-9_]+",
        ("src", "scripts"),
        "CLAUDE.md bans tokoboros compatibility shims. Use HACHI_* directly.",
    ),
]

# utf-8-sig: transparently strips a leading BOM (e.g. src/hachi/client/models.py)
# instead of leaving a stray U+FEFF in the decoded text -- see scripts/check_getattr.py
# for the same convention (there it prevents ast.parse from choking on it; here it just
# keeps line 1 clean since this script does plain regex line-scanning).
_ENCODING = "utf-8-sig"
_SCAN_SUFFIXES = (".py", ".toml", ".yml")


def _files_under(rel_dir: str) -> list[Path]:
    root_dir = ROOT / rel_dir
    if not root_dir.is_dir():
        return []
    return [
        p
        for p in root_dir.rglob("*")
        if p.is_file() and p.suffix in _SCAN_SUFFIXES and "__pycache__" not in p.parts
    ]


def main() -> int:
    problems: list[str] = []
    for ban_id, pattern, dirs, reason in BANS:
        regex = re.compile(pattern)
        for rel_dir in dirs:
            for path in sorted(_files_under(rel_dir)):
                rel = path.relative_to(ROOT).as_posix()
                text = path.read_text(encoding=_ENCODING)
                for lineno, line in enumerate(text.splitlines(), start=1):
                    match = regex.search(line)
                    if match:
                        problems.append(
                            f"{rel}:{lineno}: banned pattern [{ban_id}] "
                            f"{match.group(0)!r} -- {reason}"
                        )

    if problems:
        print(f"check_bans: FAIL -- {len(problems)} banned pattern(s) found:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"check_bans: OK -- {len(BANS)} ban(s) checked, 0 violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
