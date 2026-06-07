"""
ObjectGraph Skill Engine

This module is the brain indexer for Tokoboros. On boot, it scans the user's ~/.tokoboros/skills/
directory, parses every .og file, and builds a fast in-memory index. When the agent receives
a user query, it uses this engine to find the exact skill node it needs without loading
all skills into the context window.

Phase 4 additions:
    - Usage Tracking: Every load_skill call records a timestamp and increments the
      use_count in a persistent JSON file (~/.tokoboros/brain/skill_usage.json).
    - Stale Detection: get_stale_skills() returns skills not used in N days.
    - Analytics: get_usage_report() returns full lifecycle stats for all skills.

This is the core architectural advantage over agents like OpenClaw — instead of dumping
every skill into the system prompt (which bloats tokens and causes hallucination),
we provide surgical, O(1) keyword-based retrieval.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tokoboros.og_engine.parser import (
    OGChangelogEntry,
    OGFile,
    OGFileMeta,
    OGNode,
    parse_og_file,
)
from tokoboros.og_engine.serializer import index_entry_for_node, serialize_og_file
from tokoboros.og_engine.skill_md import skill_md_to_og

logger = logging.getLogger(__name__)

# Upper bound (in estimated tokens) on a node's ::dense block. The dense block is
# the Pass-2 routing summary — if it grows into a paragraph it defeats the format.
DENSE_TOKEN_BUDGET = 30

# Default heavy blocks rendered by a Pass-3 load when the agent does not request
# a specific subset. Execution-critical content only — low-value asides (note,
# example, reference) are opt-in to keep loads lean.
DEFAULT_LOAD_BLOCKS: tuple[str, ...] = (
    "warning",
    "full",
    "steps",
    "code",
    "table",
    "assertions",
)

# Every renderable block, in stable priority order. Requesting "all" expands to this.
ALL_BLOCKS: tuple[str, ...] = (
    "warning",
    "dense",
    "summary",
    "full",
    "steps",
    "code",
    "table",
    "list",
    "example",
    "note",
    "reference",
    "assertions",
    "edges",
)


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token).

    Used for the Pass-2 block-cost manifest and the token-savings benchmark.
    Intentionally dependency-free so it works identically in CI and offline.

    Args:
        text: The string to measure.

    Returns:
        An approximate token count (minimum 1 for any non-empty string).
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _lint_result(path: Path, node_count: int, issues: list[dict[str, str]]) -> dict[str, Any]:
    """Assemble a lint report with an error/warning/info summary and an ``ok`` verdict."""
    errors = sum(1 for i in issues if i["level"] == "error")
    warnings = sum(1 for i in issues if i["level"] == "warning")
    info = sum(1 for i in issues if i["level"] == "info")
    return {
        "file": str(path),
        "ok": errors == 0,
        "node_count": node_count,
        "issues": issues,
        "summary": {"errors": errors, "warnings": warnings, "info": info},
    }


def lint_og_file(filepath: str | Path) -> dict[str, Any]:
    """Lint a single ``.og`` skill file for structural quality and token efficiency.

    Checks that authored/community skills follow the conventions the 3-pass loader
    relies on: every node is routable (has an ``::index`` entry + keywords), carries a
    within-budget ``::dense`` block for cheap Pass-2 peeking, has real Pass-3 content,
    and declares no dangling ``::index`` entries. Cross-file ``requires`` are reported
    as info (they may resolve elsewhere in the library), not errors.

    Returns:
        A report dict: ``file``, ``ok`` (no errors), ``node_count``, ``issues``
        (each ``{level, node, code, message}``), and a ``summary`` count map.
        ``ok`` is False only when there are errors; warnings are advisory.
    """
    path = Path(filepath)
    issues: list[dict[str, str]] = []

    def add(level: str, node: str, code: str, message: str) -> None:
        issues.append({"level": level, "node": node, "code": code, "message": message})

    if not path.exists():
        add("error", "*", "missing-file", f"File not found: {path}")
        return _lint_result(path, 0, issues)

    try:
        og = parse_og_file(str(path))
    except Exception as e:
        add("error", "*", "parse-error", f"Failed to parse: {e}")
        return _lint_result(path, 0, issues)

    nodes = og.nodes
    node_names = {n.name for n in nodes}
    index_ids = {e.node_id for e in og.index}

    if not nodes:
        add("error", "*", "no-nodes", "No ::node blocks found - file defines no skills.")

    for entry in og.index:
        if entry.node_id not in node_names:
            add(
                "warning",
                entry.node_id,
                "dangling-index",
                f"::index lists '{entry.node_id}' but no matching ::node exists.",
            )

    for node in nodes:
        if node.name not in index_ids:
            add(
                "warning",
                node.name,
                "missing-index",
                "Node has no ::index entry - it won't be routable in Pass 1.",
            )
        if not node.keywords:
            add(
                "warning",
                node.name,
                "no-keywords",
                "Node has no keywords - it won't surface in search_skills.",
            )
        if not node.dense.strip():
            add(
                "warning",
                node.name,
                "missing-dense",
                "Node has no ::dense block - Pass 2 (cheap peek) falls back to heavier blocks.",
            )
        else:
            tokens = estimate_tokens(node.dense)
            if tokens > DENSE_TOKEN_BUDGET:
                add(
                    "warning",
                    node.name,
                    "dense-over-budget",
                    f"::dense is ~{tokens} tokens; budget is {DENSE_TOKEN_BUDGET}. Compress it.",
                )
        has_content = any(
            [
                node.full.strip(),
                node.steps,
                node.code_blocks,
                node.table.strip(),
                node.list_items,
                node.summary.strip(),
            ]
        )
        if not has_content:
            add(
                "info",
                node.name,
                "empty-node",
                "Node has metadata but no Pass-3 content (full/steps/code/table).",
            )
        for dep in node.requires:
            if dep not in node_names:
                add(
                    "info",
                    node.name,
                    "external-requires",
                    f"requires '{dep}' is not defined in this file "
                    "(ensure it exists elsewhere in your skill library).",
                )

    return _lint_result(path, len(nodes), issues)


class SkillEngine:
    """Central registry, retrieval engine, and lifecycle manager for .og skills.

    Maintains a persistent usage database (JSON) that tracks how often each
    skill is loaded, when it was first/last used, and whether it has gone stale.

    Scope enforcement is applied at query time via ``agent_role``. Nodes with
    ``scope="all"`` or ``scope="public"`` are always visible. Nodes with any
    other scope (e.g. ``"orchestrator"``, ``"admin"``) are only returned when
    ``agent_role`` matches that scope. The default ``"all"`` therefore exposes
    only publicly-visible nodes — callers that need elevated scopes (e.g. the
    main orchestrator reading ``brain.og``) must pass their role explicitly.
    """

    def __init__(self, agent_role: str = "*") -> None:
        """Initialise the engine.

        Args:
            agent_role: The role of the agent that will query this engine.
                Nodes whose ``scope`` does not match this role (or ``"all"`` /
                ``"public"``) are hidden from all query results.

                Special values:

                - ``"*"`` (the default) bypasses all scope checks — every node
                  is returned.  Use this for CLI diagnostic tools (``skill
                  show``, ``skill search``, ``doctor``, etc.) where the human
                  user should see the full library.
                - ``"orchestrator"`` sees ``scope=all/public`` AND
                  ``scope=orchestrator`` nodes (brain.og identity + rules).
                  Pass this in :func:`create_agent_session` for the main
                  session engine.
                - Any other value (e.g. ``"analyst"``) sees only
                  ``scope=all/public`` nodes, hiding orchestrator-internal
                  nodes from worker sub-agents.
        """
        self._agent_role = agent_role
        self._nodes: dict[str, OGNode] = {}  # node_name -> OGNode
        self._keyword_index: dict[str, list[str]] = {}  # keyword -> [node_name, ...]
        self._files: dict[str, OGFile] = {}  # filepath -> OGFile (file-level metadata + index)
        self._skills_dir: Path | None = None  # root dir; where authored skills are written

        # Phase 4: Persistent usage tracking
        self._usage_path = Path.home() / ".tokoboros" / "brain" / "skill_usage.json"
        self._usage: dict[str, dict[str, Any]] = self._load_usage()

    def _is_visible(self, node: OGNode) -> bool:
        """Return True when this node is accessible to the engine's agent role.

        ``scope="all"`` and ``scope="public"`` are always accessible.
        Any other scope (e.g. ``"orchestrator"``, ``"admin"``) is only
        accessible when ``self._agent_role`` exactly matches it.  The special
        role ``"*"`` bypasses all checks — used by CLI diagnostic tools.
        """
        if self._agent_role == "*":
            return True
        return node.scope in ("all", "public", self._agent_role)

    def load_directory(self, skills_dir: Path):
        """
        Scan a directory for .og files and index all nodes found within them.

        Args:
            skills_dir: The path to the skills directory (e.g., ~/.tokoboros/skills/).
        """
        self._skills_dir = skills_dir
        if not skills_dir.exists() or not skills_dir.is_dir():
            return

        for og_file in skills_dir.glob("**/*.og"):
            self.load_file(og_file)

    def load_file(self, filepath: Path):
        """
        Parse a single .og file and add its nodes and file-level metadata to the index.

        Stores the full OGFile object (with ::meta, ::index, ::schema, ::changelog)
        alongside individual nodes for granular retrieval.

        Args:
            filepath: Absolute path to the .og file.
        """
        try:
            og_file = parse_og_file(str(filepath))
            self._files[str(filepath)] = og_file

            for node in og_file.nodes:
                self._nodes[node.name] = node

                # Index every keyword for fast lookup
                for kw in node.keywords:
                    kw_lower = kw.lower()
                    if kw_lower not in self._keyword_index:
                        self._keyword_index[kw_lower] = []
                    if node.name not in self._keyword_index[kw_lower]:
                        self._keyword_index[kw_lower].append(node.name)

            # Also index keywords from ::index entries for file-level routing
            for entry in og_file.index:
                for kw in entry.keywords:
                    kw_lower = kw.lower()
                    if kw_lower not in self._keyword_index:
                        self._keyword_index[kw_lower] = []
                    if entry.node_id not in self._keyword_index[kw_lower]:
                        self._keyword_index[kw_lower].append(entry.node_id)
        except Exception as e:
            # Silently skip malformed files so one bad skill doesn't crash the agent
            logger.warning("Failed to parse %s: %s", filepath, e)

    def search(self, query: str) -> list[dict[str, str]]:
        """
        Search the skill index by keyword matching against a natural language query.
        Returns a lightweight list of matching node summaries (name + role + keywords)
        so the agent can decide which skill to load in full.

        Args:
            query: A natural language query or keyword string from the agent.

        Returns:
            A list of dicts with 'name', 'role', 'keywords', and 'source_file' for each match.
        """
        query_terms = set(query.lower().split())
        matched_names: set = set()

        # Direct keyword match
        for term in query_terms:
            if term in self._keyword_index:
                matched_names.update(self._keyword_index[term])

        # Fuzzy substring match across all keywords
        for kw, names in self._keyword_index.items():
            for term in query_terms:
                if term in kw or kw in term:
                    matched_names.update(names)

        _PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}

        results = []
        for name in matched_names:
            node = self._nodes.get(name)
            if node is None or not self._is_visible(node):
                continue
            priority = (node.traverse or {}).get("priority", "normal")
            sort_key = _PRIORITY_ORDER.get(priority, 1)
            results.append(
                (
                    sort_key,
                    {
                        "name": node.name,
                        "role": node.role,
                        "keywords": ", ".join(node.keywords),
                        "source_file": node.source_file,
                    },
                )
            )

        results.sort(key=lambda t: t[0])
        return [r for _, r in results]

    def list_all_skills(self) -> list[dict[str, str]]:
        """
        Return a summary of every loaded skill node visible to this engine's role.

        Nodes whose scope does not match the engine's ``agent_role`` are silently
        omitted, so the caller only sees skills it is allowed to use.

        Returns:
            A list of dicts with 'name', 'role', and 'keywords' for each
            accessible loaded node.
        """
        results = []
        for node in self._nodes.values():
            if not self._is_visible(node):
                continue
            results.append(
                {
                    "name": node.name,
                    "role": node.role,
                    "keywords": ", ".join(node.keywords),
                }
            )
        return results

    def get_node(self, name: str) -> OGNode | None:
        """
        Retrieve the full OGNode by its exact name.

        Nodes outside the engine's agent role scope are treated as if they
        do not exist (returns ``None``), so RBAC enforcement is consistent
        whether a caller uses the tool path or the direct accessor.

        Args:
            name: The node identifier (e.g., 'deploy_to_prod').

        Returns:
            The full OGNode or None if not found or not accessible.
        """
        node = self._nodes.get(name)
        if node is None or not self._is_visible(node):
            return None
        return node

    def get_all_nodes(self) -> list[OGNode]:
        """Return all indexed skill nodes visible to this engine's agent role."""
        return [n for n in self._nodes.values() if self._is_visible(n)]

    def peek(self, names: list[str]) -> dict[str, Any]:
        """Pass 2: return the compressed view of one or more nodes.

        This is the cheap middle step of the 3-pass model. For each requested
        node it returns only the ``dense`` summary (the ~15-token compression),
        any ``warning`` (warnings always surface), the names of required
        dependencies, and a manifest of which heavy blocks exist and roughly how
        many tokens each would cost in a full load. It deliberately does NOT
        return ``full``/``steps``/``code`` bodies — the agent reads this to
        decide whether a Pass-3 ``load_skill`` is even necessary.

        Args:
            names: One or more node identifiers to peek at.

        Returns:
            A dict mapping each requested name to its compressed view, or to an
            ``{"error": ...}`` entry if the node is unknown.
        """
        result: dict[str, Any] = {}
        for name in names:
            node = self._nodes.get(name)
            if not node or not self._is_visible(node):
                result[name] = {"error": f"Skill '{name}' not found."}
                continue

            available: dict[str, int] = {}
            if node.full:
                available["full"] = estimate_tokens(node.full)
            if node.steps:
                available["steps"] = estimate_tokens("\n".join(node.steps))
            if node.code_blocks:
                available["code"] = estimate_tokens("\n".join(node.code_blocks.values()))
            if node.table:
                available["table"] = estimate_tokens(node.table)
            if node.list_items:
                available["list"] = estimate_tokens("\n".join(node.list_items))
            if node.example:
                available["example"] = estimate_tokens(node.example)
            if node.assertions:
                available["assertions"] = estimate_tokens("\n".join(node.assertions))
            if node.note:
                available["note"] = estimate_tokens(node.note)
            if node.reference:
                available["reference"] = estimate_tokens(str(node.reference))

            result[name] = {
                "dense": node.dense or node.summary or "",
                "warning": node.warning,
                "requires": list(node.requires),
                "available_blocks": available,
                "traverse": node.traverse or {},
            }
        return result

    def resolve_node(self, name: str, blocks: list[str] | None = None) -> str:
        """Pass 3: render a single node's heavy content, optionally a subset.

        Renders only the requested ``blocks`` (or :data:`DEFAULT_LOAD_BLOCKS`
        when none are given). Passing ``["all"]`` expands to :data:`ALL_BLOCKS`.

        Unlike the previous implementation, this does NOT recursively expand the
        ``->[:requires]`` dependency tree. Dependencies are listed by name in a
        footer so the agent can choose to ``load_skill`` them only if needed —
        this is what makes loading selective instead of an all-or-nothing dump.

        Args:
            name: The node identifier.
            blocks: Optional list of block names to render (e.g. ``["steps"]``),
                or ``["all"]`` for everything. Defaults to the execution set.

        Returns:
            A formatted string containing only the requested content.
        """
        node = self._nodes.get(name)
        if not node or not self._is_visible(node):
            return f"Error: Skill '{name}' not found in the loaded skills."

        selected = list(blocks) if blocks else list(DEFAULT_LOAD_BLOCKS)
        if "all" in selected:
            selected = list(ALL_BLOCKS)
        want = set(selected)

        sections = [f"=== SKILL: {node.name} (role: {node.role}) ==="]

        if "warning" in want and node.warning:
            sections.append(f"\n⚠️ WARNING: {node.warning}")
        if "dense" in want and node.dense:
            sections.append(f"\n--- Summary (dense) ---\n{node.dense}")
        if "summary" in want and node.summary:
            sections.append(f"\n--- Summary ---\n{node.summary}")
        if "full" in want and node.full:
            sections.append(f"\n--- Full Details ---\n{node.full}")
        if "steps" in want and node.steps:
            sections.append("\n--- Steps ---")
            for i, step in enumerate(node.steps, 1):
                sections.append(f"  {i}. {step}")
        if "code" in want:
            for lang, code in node.code_blocks.items():
                sections.append(f"\n--- Code [{lang}] ---\n{code}")
        if "table" in want and node.table:
            sections.append(f"\n--- Data ---\n{node.table}")
        if "list" in want and node.list_items:
            sections.append("\n--- Items ---")
            for item in node.list_items:
                sections.append(f"  {item}")
        if "example" in want and node.example:
            sections.append(f"\n--- Example ---\n{node.example}")
        if "note" in want and node.note:
            sections.append(f"\n--- Note ---\n{node.note}")
        if "reference" in want and node.reference:
            ref_parts = [f"{k}: {v}" for k, v in node.reference.items()]
            sections.append("\n--- Reference ---\n" + "\n".join(ref_parts))
        if "assertions" in want and node.assertions:
            sections.append("\n--- MANDATORY ASSERTIONS (run these before proceeding) ---")
            for assertion in node.assertions:
                sections.append(f"  - {assertion}")
        if "edges" in want and node.edges:
            sections.append("\n--- Relationships ---")
            for edge in node.edges:
                sections.append(f"  {edge}")

        # Dependency footer — names only. The agent expands these on demand
        # rather than the engine pulling the whole tree at full content.
        deps = list(node.requires)
        for edge_line in node.edges:
            if "[:requires]" in edge_line:
                dep_id = edge_line.split("]")[-1].strip()
                if dep_id and dep_id not in deps:
                    deps.append(dep_id)
        if deps:
            sections.append(
                "\n--- Dependencies (call load_skill on these only if needed) ---\n"
                f"  {', '.join(deps)}"
            )

        return "\n".join(sections)

    def resolve_context(self, name: str) -> str:
        """Pass 3 with default blocks — the behavioral context for a skill node.

        Thin wrapper over :meth:`resolve_node` that renders the execution-default
        block set. Kept for backward compatibility with existing callers.

        Args:
            name: The node identifier.

        Returns:
            A formatted string containing the node's execution-critical content.
        """
        return self.resolve_node(name)

    # ==========================================
    # Skill Authoring (agent-written .og skills)
    # ==========================================

    @staticmethod
    def _slugify(name: str) -> str:
        """Turn an arbitrary skill name into a safe snake_case identifier.

        Also doubles as path-traversal defence: every non-alphanumeric character
        (including ``/``, ``\\`` and ``.``) is collapsed to ``_``, so the result
        can never escape the target directory when used as a filename.
        """
        slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
        return slug or "skill"

    @staticmethod
    def validate_dense(dense: str) -> tuple[bool, int]:
        """Check a dense block against the token budget.

        Returns ``(ok, token_count)``. A dense block is the compressed Pass-2
        summary; oversized ones are rejected so authored skills stay efficient.
        """
        count = estimate_tokens(dense)
        return count <= DENSE_TOKEN_BUDGET, count

    def upsert_skill(
        self,
        *,
        name: str,
        keywords: list[str],
        dense: str,
        full: str = "",
        steps: list[str] | None = None,
        code: str = "",
        code_language: str = "text",
        warning: str = "",
        assertions: list[str] | None = None,
        requires: list[str] | None = None,
        role: str = "skill",
        scope: str = "all",
        domain: str = "",
        reason: str = "",
        skills_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Create or refine an .og skill and hot-load it into the live index.

        Writes a single-node ObjectGraph file to ``<skills_dir>/learned/<slug>.og``,
        then immediately parses and indexes it so the skill is usable in the same
        session. If the file already exists, this is treated as a refinement: the
        changelog gains an ``updated`` entry and the version's patch number is
        bumped. The ``dense`` block is validated against the token budget first —
        an oversized block is rejected without writing anything.

        Args:
            name: Human/skill name; slugified into the node id and filename.
            keywords: Routing keywords for Pass-1 search.
            dense: Compressed Pass-2 summary (must fit the token budget).
            full: Full Pass-3 prose.
            steps: Ordered execution steps.
            code: A single code block body (optional).
            code_language: Language tag for the code block.
            warning: Critical callout.
            assertions: Validation conditions to run before proceeding.
            requires: Names of skills this one depends on.
            role: Node role (default "skill").
            scope: Access scope (default "all").
            domain: Pipe-separated domain tags for the file meta.
            reason: Short description recorded in the changelog.
            skills_dir: Override the target dir (defaults to the loaded skills dir).

        Returns:
            A status dict: ``status`` is ``created``/``updated``/``rejected``/``error``.
        """
        ok, dense_tokens = self.validate_dense(dense)
        if not ok:
            return {
                "status": "rejected",
                "reason": (
                    f"dense block is ~{dense_tokens} tokens; budget is {DENSE_TOKEN_BUDGET}. "
                    "Compress it to a few pipe-separated keywords (no full sentences)."
                ),
                "dense_tokens": dense_tokens,
            }

        target_dir = skills_dir or self._skills_dir
        if target_dir is None:
            return {"status": "error", "reason": "no skills directory configured"}

        learned_dir = target_dir / "learned"
        learned_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slugify(name)
        path = learned_dir / f"{slug}.og"
        is_update = path.exists()

        node = OGNode(
            name=slug,
            source_file=str(path),
            role=role,
            scope=scope,
            keywords=[kw.strip() for kw in keywords if kw.strip()],
            requires=[r.strip() for r in (requires or []) if r.strip()],
            dense=dense,
            full=full,
            steps=list(steps or []),
            code_blocks={code_language: code} if code else {},
            warning=warning,
            assertions=list(assertions or []),
        )

        today = datetime.now(UTC).date().isoformat()
        if is_update:
            existing = parse_og_file(str(path))
            changelog = list(existing.changelog)
            changelog.append(
                OGChangelogEntry(
                    date=today,
                    action="updated",
                    target=f"node[{slug}]",
                    description=reason or "refined by agent",
                )
            )
            version = self._bump_patch(existing.meta.version or "1.0.0")
            created = existing.meta.created or today
        else:
            changelog = [
                OGChangelogEntry(
                    date=today,
                    action="added",
                    target=f"node[{slug}]",
                    description=reason or "authored by agent",
                )
            ]
            version = "1.0.0"
            created = today

        meta = OGFileMeta(
            title=name,
            version=version,
            author="tokoboros-agent",
            created=created,
            updated=today,
            domain=domain,
            scope=scope,
        )
        og = OGFile(
            source_file=str(path),
            meta=meta,
            index=[index_entry_for_node(node)],
            changelog=changelog,
            nodes=[node],
        )

        path.write_text(serialize_og_file(og), encoding="utf-8")
        self.load_file(path)  # hot-load so the skill is usable immediately

        # Lint the just-written skill and surface advisory feedback so the agent
        # can self-correct on its next refinement (the quality side of the
        # self-improvement loop). Dense budget + index are already guaranteed
        # above, so what remains is searchability/content/dependency hygiene.
        report = lint_og_file(path)
        advisories = [
            f"{issue['code']}: {issue['message']}"
            for issue in report["issues"]
            if issue["level"] in ("warning", "info")
        ]

        result: dict[str, Any] = {
            "status": "updated" if is_update else "created",
            "name": slug,
            "path": str(path),
            "version": version,
            "dense_tokens": dense_tokens,
        }
        if advisories:
            result["lint_advisories"] = advisories
        return result

    @staticmethod
    def _bump_patch(version: str) -> str:
        """Increment the patch component of a semver string (best-effort)."""
        parts = version.split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            return ".".join(parts)
        except (ValueError, IndexError):
            return version

    def import_skill_md(self, path: Path, skills_dir: Path | None = None) -> dict[str, Any]:
        """Import a SKILL.md (Agent Skills) file as an .og skill and hot-load it.

        Bridges the community SKILL.md ecosystem into Tokoboros' token-efficient
        format: the frontmatter + markdown body are converted to an ObjectGraph
        node, written to ``<skills_dir>/imported/<slug>.og``, and indexed so the
        skill is immediately usable via search/peek/load.

        Args:
            path: Path to the SKILL.md (or any .md skill) file.
            skills_dir: Override the target dir (defaults to the loaded skills dir).

        Returns:
            A status dict: ``status`` is ``imported`` or ``error``.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            return {"status": "error", "reason": str(e)}

        target_dir = skills_dir or self._skills_dir
        if target_dir is None:
            return {"status": "error", "reason": "no skills directory configured"}

        og = skill_md_to_og(text, fallback_name=path.stem)
        node = og.nodes[0]
        imported_dir = Path(target_dir) / "imported"
        imported_dir.mkdir(parents=True, exist_ok=True)
        out_path = imported_dir / f"{node.name}.og"
        out_path.write_text(serialize_og_file(og), encoding="utf-8")
        self.load_file(out_path)  # hot-load so the skill is usable immediately

        return {
            "status": "imported",
            "name": node.name,
            "path": str(out_path),
            "keywords": node.keywords,
        }

    def export_skill(self, name: str) -> str | None:
        """Serialize a single loaded skill node back to ``.og`` text (or None)."""
        node = self._nodes.get(name)
        if not node:
            return None
        og = OGFile(
            meta=OGFileMeta(title=name, version="1.0.0", author="tokoboros-export"),
            index=[index_entry_for_node(node)],
            nodes=[node],
        )
        return serialize_og_file(og)

    def get_file_index(self, filepath: str) -> str:
        """Return the formatted ::index routing table for a specific .og file.

        This is designed for injection into a sub-agent's system prompt so it
        has a complete, token-efficient map of the file's contents (~30 tokens)
        before reading any node bodies.

        Args:
            filepath: The absolute path to the .og file.

        Returns:
            A formatted index string, or empty string if no index exists.
        """
        og_file = self._files.get(filepath)
        if og_file:
            return og_file.get_index_prompt()
        return ""

    def get_file_meta(self, filepath: str) -> dict[str, str] | None:
        """Return the ::meta block for a specific .og file.

        Args:
            filepath: The absolute path to the .og file.

        Returns:
            A dict of metadata fields, or None if the file is not loaded.
        """
        og_file = self._files.get(filepath)
        if og_file and og_file.meta:
            return og_file.meta.model_dump()
        return None

    def get_file_changelog(self, filepath: str) -> list[dict[str, str]]:
        """Return the ::changelog entries for a specific .og file.

        Enables delta loading: agents check only the changelog to discover
        what changed since their last read.

        Args:
            filepath: The absolute path to the .og file.

        Returns:
            A list of changelog entry dicts.
        """
        og_file = self._files.get(filepath)
        if og_file and og_file.changelog:
            return [e.model_dump() for e in og_file.changelog]
        return []

    def get_node_changelog(self, name: str) -> dict[str, Any] | None:
        """Return a skill node's version + changelog for delta-loading.

        This realizes the ObjectGraph "delta load" idea: instead of re-reading a
        skill's heavy content to find out whether it changed, an agent that loaded
        the skill earlier can check just its current version and changelog entries
        (a few tokens) and decide whether a fresh ``load_skill`` is even warranted.

        Args:
            name: The exact skill node id.

        Returns:
            A dict with ``name``, ``version``, ``updated``, and ``changelog``
            (list of entry dicts), or ``None`` if the node is unknown.
        """
        node = self._nodes.get(name)
        if not node:
            return None
        filepath = node.source_file
        meta = self.get_file_meta(filepath) or {}
        return {
            "name": name,
            "version": meta.get("version", ""),
            "updated": meta.get("updated", ""),
            "changelog": self.get_file_changelog(filepath),
        }

    def get_traverse_hints(self, name: str) -> dict[str, Any] | None:
        """Return the ``::traverse`` hints for a skill node.

        Traverse hints guide how the agent (or the engine) should navigate the
        skill graph when loading this node:

        - ``priority`` — ``"high"`` / ``"normal"`` / ``"low"``; controls where
          this node appears in ``search()`` results.
        - ``entry`` — preferred starting node when this skill acts as a subgraph
          root (e.g. ``entry: auth`` tells the agent to load ``auth`` first).
        - ``depth`` — maximum edge-follow depth (e.g. ``depth: 2``).

        Args:
            name: The exact skill node id.

        Returns:
            A dict with ``name`` + all traverse key/value pairs, or ``None``
            if the node is unknown.  Returns an empty ``traverse`` dict if the
            node exists but has no ``::traverse`` block.
        """
        node = self._nodes.get(name)
        if not node:
            return None
        return {
            "name": name,
            "traverse": dict(node.traverse or {}),
        }

    @staticmethod
    def _direct_deps(node: OGNode) -> list[str]:
        """Combine a node's ``requires`` field with any ``->[:requires] <id>`` edges.

        Mirrors the dependency extraction in :meth:`resolve_node`'s footer so both
        agree on what a skill directly depends on.
        """
        deps = list(node.requires)
        for edge_line in node.edges:
            if "[:requires]" in edge_line:
                dep_id = edge_line.split("]")[-1].strip()
                if dep_id and dep_id not in deps:
                    deps.append(dep_id)
        return deps

    def get_dependency_chain(self, name: str, max_depth: int = 10) -> dict[str, Any] | None:
        """Resolve a skill's transitive ``requires`` dependency chain.

        Realizes ObjectGraph's typed dependency-aware traversal: from a skill node,
        walk its ``requires`` / ``->[:requires]`` edges to discover every prerequisite
        skill the agent should ``load_skill`` before executing — without loading any
        heavy content. Shared dependencies are visited once; back-edges are reported as
        a cycle rather than looping forever.

        Args:
            name: The exact skill node id.
            max_depth: Safety bound on traversal depth.

        Returns:
            ``{name, direct, chain, missing, has_cycle}`` where ``direct`` is the
            immediate dependencies, ``chain`` is the ordered transitive closure
            (excluding the root), ``missing`` are referenced deps not currently
            loaded, and ``has_cycle`` flags a dependency loop. ``None`` if unknown.
        """
        if name not in self._nodes:
            return None

        direct = self._direct_deps(self._nodes[name])
        chain: list[str] = []
        missing: list[str] = []
        visited: set[str] = set()
        has_cycle = False

        def walk(current: str, depth: int, path: frozenset[str]) -> None:
            nonlocal has_cycle
            node = self._nodes.get(current)
            if node is None or depth > max_depth:
                return
            for dep in self._direct_deps(node):
                if dep in path:  # back-edge into the current path → cycle
                    has_cycle = True
                    continue
                if dep in visited:  # already resolved on another branch (diamond)
                    continue
                visited.add(dep)
                if dep in self._nodes:
                    chain.append(dep)
                    walk(dep, depth + 1, path | {dep})
                elif dep not in missing:
                    missing.append(dep)

        walk(name, 0, frozenset({name}))
        return {
            "name": name,
            "direct": direct,
            "chain": chain,
            "missing": missing,
            "has_cycle": has_cycle,
        }

    def get_skill_graph(self) -> dict[str, Any]:
        """Build the whole-library dependency graph from typed ``requires`` edges.

        Aggregates every loaded node into an adjacency structure with entry-point
        roots (nothing requires them), missing dependencies (referenced but not
        loaded), and any dependency cycles — the data behind ``skill graph`` and
        the Mermaid/DOT visualizations.

        Returns:
            ``{skill_count, edge_count, edges, roots, missing, cyclic, nodes}``
            where ``nodes`` is ``[{name, role, direct, missing, has_cycle}]`` and
            ``edges`` is ``[[from, to], ...]``.
        """
        names = sorted(node.name for node in self.get_all_nodes())
        roles = {node.name: node.role for node in self.get_all_nodes()}
        nodes: list[dict[str, Any]] = []
        edges: list[list[str]] = []
        depended_on: set[str] = set()
        missing: set[str] = set()
        cyclic: list[str] = []

        for name in names:
            chain = self.get_dependency_chain(name) or {}
            direct = list(chain.get("direct", []))
            nodes.append(
                {
                    "name": name,
                    "role": roles.get(name, ""),
                    "direct": direct,
                    "missing": list(chain.get("missing", [])),
                    "has_cycle": bool(chain.get("has_cycle", False)),
                }
            )
            for dep in direct:
                edges.append([name, dep])
                if dep in roles:
                    depended_on.add(dep)
                else:
                    missing.add(dep)
            if chain.get("has_cycle"):
                cyclic.append(name)

        roots = [name for name in names if name not in depended_on]
        return {
            "skill_count": len(names),
            "edge_count": len(edges),
            "edges": edges,
            "roots": roots,
            "missing": sorted(missing),
            "cyclic": cyclic,
            "nodes": nodes,
        }

    def get_nodes_by_role(self, role: str) -> list[OGNode]:
        """
        Retrieve all nodes matching a specific role that are visible to the engine's
        agent role.  Used by the IntegrationRuntime to find 'env' and 'integration'
        nodes; also used by the doctor command to count MCP server declarations.

        Args:
            role: The role to filter by (e.g., 'env', 'integration', 'skill').

        Returns:
            A list of OGNode objects matching the given role.
        """
        return [n for n in self._nodes.values() if n.role == role and self._is_visible(n)]

    @property
    def node_count(self) -> int:
        """Return the number of indexed skill nodes visible to this engine's role."""
        if self._agent_role == "*":
            return len(self._nodes)
        return sum(1 for n in self._nodes.values() if self._is_visible(n))

    # ==========================================
    # Skill Lifecycle & Usage Tracking
    # ==========================================

    def _load_usage(self) -> dict[str, dict[str, Any]]:
        """Load the usage database from disk. Returns empty dict on error."""
        try:
            if self._usage_path.exists():
                return json.loads(self._usage_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load skill usage DB, starting fresh")
        return {}

    def _save_usage(self) -> None:
        """Persist the usage database to disk."""
        try:
            self._usage_path.parent.mkdir(parents=True, exist_ok=True)
            self._usage_path.write_text(
                json.dumps(self._usage, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            logger.warning("Failed to persist skill usage DB")

    def record_use(self, name: str) -> None:
        """Record a skill load event. Called by the execution loop on load_skill.

        Updates use_count, last_used, and first_used timestamps.

        Args:
            name: The exact skill node name that was loaded.
        """
        now = datetime.now(UTC).isoformat()
        entry = self._usage.setdefault(
            name,
            {
                "use_count": 0,
                "first_used": now,
                "last_used": now,
            },
        )
        entry["use_count"] += 1
        entry["last_used"] = now
        self._save_usage()

    def get_stale_skills(self, stale_days: int = 30) -> list[dict[str, Any]]:
        """Return skills that haven't been used within the given threshold.

        A skill is "stale" if it has usage records but last_used is older
        than stale_days ago. Skills with no usage records are considered
        "never used" and returned separately.

        Args:
            stale_days: Number of days of inactivity before a skill is stale.

        Returns:
            List of dicts with name, last_used, use_count, and days_inactive.
        """
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        stale = []

        for name, node in self._nodes.items():
            if not self._is_visible(node):
                continue
            entry = self._usage.get(name)
            if not entry:
                stale.append(
                    {
                        "name": name,
                        "last_used": None,
                        "use_count": 0,
                        "days_inactive": None,
                        "status": "never_used",
                    }
                )
                continue

            try:
                last = datetime.fromisoformat(entry["last_used"])
                if last < cutoff:
                    days = (datetime.now(UTC) - last).days
                    stale.append(
                        {
                            "name": name,
                            "last_used": entry["last_used"],
                            "use_count": entry["use_count"],
                            "days_inactive": days,
                            "status": "stale",
                        }
                    )
            except (ValueError, KeyError):
                continue

        return stale

    def get_usage_report(self) -> dict[str, Any]:
        """Generate a full usage analytics report for all loaded skills.

        Returns:
            A dict with total_skills, tracked_skills, top_skills (sorted by use_count),
            stale_skills, and never_used counts.
        """
        tracked = []
        for name, entry in self._usage.items():
            node = self._nodes.get(name)
            if node is not None and self._is_visible(node):
                tracked.append(
                    {
                        "name": name,
                        "use_count": entry.get("use_count", 0),
                        "first_used": entry.get("first_used"),
                        "last_used": entry.get("last_used"),
                    }
                )

        tracked.sort(key=lambda x: x["use_count"], reverse=True)

        stale = self.get_stale_skills()
        never_used = [s for s in stale if s["status"] == "never_used"]

        return {
            "total_skills": self.node_count,
            "tracked_skills": len(tracked),
            "never_used_count": len(never_used),
            "stale_count": len([s for s in stale if s["status"] == "stale"]),
            "top_skills": tracked[:10],
        }

    def get_skill_inventory(self) -> list[dict[str, Any]]:
        """Return a per-node inventory of every loaded skill with usage stats.

        Unlike ``get_usage_report`` (which only surfaces the top-10 tracked
        skills), this lists ALL nodes — joining each node's metadata with its
        usage record — so a UI can show the full loaded-skills picture. Sorted
        by use_count desc, then name. ``last_used`` is ``None`` for never-used
        skills.
        """
        inventory: list[dict[str, Any]] = []
        for name, node in self._nodes.items():
            if not self._is_visible(node):
                continue
            entry = self._usage.get(name) or {}
            inventory.append(
                {
                    "name": name,
                    "role": node.role,
                    "keyword_count": len(node.keywords),
                    "use_count": int(entry.get("use_count", 0) or 0),
                    "last_used": entry.get("last_used"),
                }
            )
        inventory.sort(key=lambda item: (-item["use_count"], item["name"]))
        return inventory
