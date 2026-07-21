#!/usr/bin/env python3
"""Lint all shipped .og skill files in default_skills/.

Exits 0 when every file is error-free; exits 1 and prints a report on any error.
Warnings are printed but do not affect the exit code — only errors do.

Also enforces the .og executable-surface policy decided in T-72
(docs/DECISIONS.md; docs/reference/objectgraph-spec.md section 4.7 "Runtime &
Integration Tags (Internal)"): the ``::config``/``::tool``/``::api`` block
types and the ``role=env``/``role=integration``/``role=mcp`` node roles let an
installed .og ingest credentials, call arbitrary HTTP endpoints, and start an
MCP subprocess at session boot (``gateway/orchestrator.py``'s three
``get_nodes_by_role`` scans). They are reserved for first-party skills under
``src/hachi/default_skills/``; any other .og file this linter is pointed at
that uses one is a hard ERROR, not a warning (BB-133).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hachi.og_engine.engine import lint_og_file  # noqa: E402
from hachi.og_engine.parser import parse_og_file  # noqa: E402

_SKILLS_ROOT = Path(__file__).parent.parent / "src" / "hachi" / "default_skills"

# T-72 / docs/DECISIONS.md: the .og executable surface reserved for
# first-party skills. Community/third-party packs may not declare these --
# see docs/reference/objectgraph-spec.md section 4.7.
RESERVED_BLOCK_TYPES: tuple[str, ...] = ("config", "tool", "api")
RESERVED_ROLES: tuple[str, ...] = ("env", "integration", "mcp")

# The OGNode field that holds each reserved block type's parsed content.
# A non-empty dict means the block was present in the source AND produced at
# least one key=value pair (see og_engine/parser.py's block dispatch,
# block_type == "config"/"tool"/"api"); an empty/inert block is not flagged,
# matching what the block actually *does* at runtime.
_BLOCK_TYPE_FIELD = {"config": "config", "tool": "tool_def", "api": "api_defs"}


def is_first_party_skill(og_file: Path | str) -> bool:
    """True if ``og_file`` lives under the bundled ``default_skills/`` tree.

    ``default_skills/`` is the only place the .og executable surface
    (``::config``/``::tool``/``::api``, ``role=env|integration|mcp``) is
    allowed -- see docs/DECISIONS.md (T-72). Everything else this linter is
    pointed at is treated as a community/third-party pack.
    """
    try:
        Path(og_file).resolve().relative_to(_SKILLS_ROOT.resolve())
        return True
    except ValueError:
        return False


def check_reserved_tags(og_file: Path | str) -> list[str]:
    """Return one message per reserved-tag violation in a non-first-party .og.

    Empty for first-party files (anything under ``_SKILLS_ROOT``) and for
    files that use none of the reserved block types/roles. Parse failures are
    already reported elsewhere (``lint_og_file``'s ``parse-error``) and are
    silently skipped here rather than raised.
    """
    og_file = Path(og_file)
    if is_first_party_skill(og_file):
        return []

    try:
        parsed = parse_og_file(str(og_file))
    except Exception:
        return []

    violations: list[str] = []
    for node in parsed.nodes:
        for block_type in RESERVED_BLOCK_TYPES:
            field = _BLOCK_TYPE_FIELD[block_type]
            if getattr(node, field):
                violations.append(
                    f"{node.name}: reserved block '::{block_type}' is only allowed in "
                    f"{_SKILLS_ROOT.name}/ (first-party) skills -- see "
                    "docs/reference/objectgraph-spec.md section 4.7 (T-72)"
                )
        if node.role in RESERVED_ROLES:
            violations.append(
                f"{node.name}: reserved role 'role={node.role}' is only allowed in "
                f"{_SKILLS_ROOT.name}/ (first-party) skills -- see "
                "docs/reference/objectgraph-spec.md section 4.7 (T-72)"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    roots = [Path(a) for a in args] if args else [_SKILLS_ROOT]

    og_files: set[Path] = set()
    for root in roots:
        if root.is_dir():
            og_files.update(root.rglob("*.og"))
        else:
            og_files.add(root)
    sorted_files = sorted(og_files)

    if not sorted_files:
        print(f"ERROR: no .og files found under {roots}", file=sys.stderr)
        return 1

    total_errors = 0
    total_warnings = 0
    files_with_errors = 0

    for og_file in sorted_files:
        result = lint_og_file(og_file)
        reserved_violations = check_reserved_tags(og_file)
        try:
            rel = og_file.relative_to(_SKILLS_ROOT)
        except ValueError:
            rel = og_file

        errors = result["summary"]["errors"] + len(reserved_violations)
        warnings = result["summary"]["warnings"]
        total_errors += errors
        total_warnings += warnings

        if errors:
            files_with_errors += 1
            print(f"\n[FAIL] {rel}  ({errors} error(s), {warnings} warning(s))")
            for issue in result["issues"]:
                prefix = "  ERROR" if issue["level"] == "error" else "  warn "
                print(f"{prefix}  [{issue['node']}]  {issue['code']}: {issue['message']}")
            for message in reserved_violations:
                print(f"  ERROR  [reserved-tag]  {message}")
        elif warnings:
            print(f"[warn] {rel}  ({warnings} warning(s))")
        else:
            print(f"[ ok ] {rel}")

    print(
        f"\n{'=' * 60}\n"
        f"Scanned {len(sorted_files)} file(s): "
        f"{files_with_errors} with errors, "
        f"{total_warnings} total warning(s)\n"
        f"{'=' * 60}"
    )

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
