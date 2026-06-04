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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tokoboros.og_engine.parser import OGFile, OGNode, parse_og_file

logger = logging.getLogger(__name__)


class SkillEngine:
    """Central registry, retrieval engine, and lifecycle manager for .og skills.

    Maintains a persistent usage database (JSON) that tracks how often each
    skill is loaded, when it was first/last used, and whether it has gone stale.
    """

    def __init__(self):
        self._nodes: dict[str, OGNode] = {}  # node_name -> OGNode
        self._keyword_index: dict[str, list[str]] = {}  # keyword -> [node_name, ...]
        self._files: dict[str, OGFile] = {}  # filepath -> OGFile (file-level metadata + index)

        # Phase 4: Persistent usage tracking
        self._usage_path = Path.home() / ".tokoboros" / "brain" / "skill_usage.json"
        self._usage: dict[str, dict[str, Any]] = self._load_usage()

    def load_directory(self, skills_dir: Path):
        """
        Scan a directory for .og files and index all nodes found within them.

        Args:
            skills_dir: The path to the skills directory (e.g., ~/.tokoboros/skills/).
        """
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

        results = []
        for name in matched_names:
            node = self._nodes[name]
            results.append(
                {
                    "name": node.name,
                    "role": node.role,
                    "keywords": ", ".join(node.keywords),
                    "source_file": node.source_file,
                }
            )

        return results

    def list_all_skills(self) -> list[dict[str, str]]:
        """
        Return a summary of every loaded skill node.
        Useful for the agent to understand what capabilities are available.

        Returns:
            A list of dicts with 'name', 'role', and 'keywords' for each loaded node.
        """
        results = []
        for node in self._nodes.values():
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

        Args:
            name: The node identifier (e.g., 'deploy_to_prod').

        Returns:
            The full OGNode or None if not found.
        """
        return self._nodes.get(name)

    def get_all_nodes(self) -> list[OGNode]:
        """Return all indexed skill nodes."""
        return list(self._nodes.values())

    def resolve_context(self, name: str) -> str:
        """
        Load the full behavioral context for a skill node, including its dependency chain.
        This is the content that gets injected into the agent's working memory.

        Renders all rich content blocks (dense, full, code, steps, warnings, etc.)
        into a single, optimally formatted string for LLM consumption.

        Args:
            name: The node identifier.

        Returns:
            A formatted string containing the node's full content and all
            recursively resolved dependency content.
        """
        node = self._nodes.get(name)
        if not node:
            return f"Error: Skill '{name}' not found in the loaded skills."

        visited: set = set()
        return self._resolve_recursive(node, visited)

    def _resolve_recursive(self, node: OGNode, visited: set) -> str:
        """
        Recursively resolve a node and all its dependencies into a single context string.

        Renders all rich ObjectGraph content blocks in priority order:
        warnings -> dense -> full -> steps -> code -> table -> assertions -> edges
        """
        if node.name in visited:
            return ""
        visited.add(node.name)

        sections = [f"=== SKILL: {node.name} (role: {node.role}) ==="]

        # Resolve dependencies first (via ->[:requires] edges)
        for dep_name in node.requires:
            dep_node = self._nodes.get(dep_name)
            if dep_node:
                sections.append(self._resolve_recursive(dep_node, visited))

        # Also resolve dependencies from ::edges block
        for edge_line in node.edges:
            if "[:requires]" in edge_line:
                dep_id = edge_line.split("]")[-1].strip()
                dep_node = self._nodes.get(dep_id)
                if dep_node and dep_id not in visited:
                    sections.append(self._resolve_recursive(dep_node, visited))

        # Warnings first (always high priority)
        if node.warning:
            sections.append(f"\n⚠️ WARNING: {node.warning}")

        # Core instructions (dense = compressed, full = complete)
        if node.dense:
            sections.append(f"\n--- Instructions ---\n{node.dense}")

        if node.full:
            sections.append(f"\n--- Full Details ---\n{node.full}")

        # Summary (if present, useful for long nodes)
        if node.summary:
            sections.append(f"\n--- Summary ---\n{node.summary}")

        # Ordered steps
        if node.steps:
            sections.append("\n--- Steps ---")
            for i, step in enumerate(node.steps, 1):
                sections.append(f"  {i}. {step}")

        # Code blocks (with language tags)
        for lang, code in node.code_blocks.items():
            sections.append(f"\n--- Code [{lang}] ---\n{code}")

        # Table data
        if node.table:
            sections.append(f"\n--- Data ---\n{node.table}")

        # List items
        if node.list_items:
            sections.append("\n--- Items ---")
            for item in node.list_items:
                sections.append(f"  {item}")

        # Examples
        if node.example:
            sections.append(f"\n--- Example ---\n{node.example}")

        # Notes (low priority)
        if node.note:
            sections.append(f"\n--- Note ---\n{node.note}")

        # References
        if node.reference:
            ref_parts = [f"{k}: {v}" for k, v in node.reference.items()]
            sections.append("\n--- Reference ---\n" + "\n".join(ref_parts))

        # Assertions (safety checks)
        if node.assertions:
            sections.append("\n--- MANDATORY ASSERTIONS (run these before proceeding) ---")
            for assertion in node.assertions:
                sections.append(f"  - {assertion}")

        # Edge declarations (for agent awareness of relationships)
        if node.edges:
            sections.append("\n--- Relationships ---")
            for edge in node.edges:
                sections.append(f"  {edge}")

        return "\n".join(sections)

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

    def get_nodes_by_role(self, role: str) -> list[OGNode]:
        """
        Retrieve all nodes matching a specific role.
        Used by the IntegrationRuntime to find 'env' and 'integration' nodes.

        Args:
            role: The role to filter by (e.g., 'env', 'integration', 'skill').

        Returns:
            A list of OGNode objects matching the given role.
        """
        return [node for node in self._nodes.values() if node.role == role]

    @property
    def node_count(self) -> int:
        """Return the total number of indexed skill nodes."""
        return len(self._nodes)

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

        for name in self._nodes:
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
            if name in self._nodes:
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
            "total_skills": len(self._nodes),
            "tracked_skills": len(tracked),
            "never_used_count": len(never_used),
            "stale_count": len([s for s in stale if s["status"] == "stale"]),
            "top_skills": tracked[:10],
        }
