"""
ObjectGraph Skill Tools

This module exposes the SkillEngine to the LLM as callable tools.
These are the tools that make Tokoboros fundamentally different from every other agent —
instead of dumping all skills into the system prompt, the agent surgically queries
for exactly the knowledge it needs via these tools.

The flow is the 3-pass ObjectGraph reading model:
    1. User asks "deploy my app to production"
    2. Agent calls `search_skills(query="deploy production")` → Pass 1: a lightweight list of
       matching skill names + keywords (~tens of tokens).
    3. Agent calls `peek_skill(names=["deploy_to_prod"])` → Pass 2: the compressed `dense`
       summary + a manifest of which heavy blocks exist, so it can decide what (if anything)
       it actually needs to load.
    4. Agent calls `load_skill(name="deploy_to_prod")` → Pass 3: the heavy content (full
       instructions, steps, code, assertions). Optionally narrows to specific `blocks`.
    5. Agent follows the steps and runs the assertions before executing.

This is what makes Tokoboros token-efficient: the agent pays for only the slice of a skill it
needs, instead of every skill file being dumped into the system prompt.

The agent can also grow its own library: after solving a novel task, it calls `save_skill(...)`
to persist the procedure as a new .og file, which is hot-loaded immediately and available to all
future sessions. Calling save_skill again for the same skill refines it (self-improvement loop).
"""

import json

from tokoboros.core.loop import ToolRegistry
from tokoboros.og_engine.engine import SkillEngine


def _make_search_skills(engine: SkillEngine):
    """Factory: creates the search_skills tool closure bound to the engine instance."""

    def search_skills(query: str) -> str:
        results = engine.search(query)
        if not results:
            return (
                "No matching skills found. The user may need to add a .og file to "
                "~/.tokoboros/skills/"
            )
        return json.dumps(results, indent=2)

    return search_skills


def _make_list_skills(engine: SkillEngine):
    """Factory: creates the list_skills tool closure bound to the engine instance."""

    def list_skills() -> str:
        results = engine.list_all_skills()
        if not results:
            return "No skills are currently loaded. The skills directory is empty."
        return json.dumps(results, indent=2)

    return list_skills


def _make_peek_skill(engine: SkillEngine):
    """Factory: creates the peek_skill tool closure bound to the engine instance."""

    def peek_skill(names: list[str]) -> str:
        results = engine.peek(names)
        return json.dumps(results, indent=2)

    return peek_skill


def _make_load_skill(engine: SkillEngine):
    """Factory: creates the load_skill tool closure bound to the engine instance."""

    def load_skill(name: str, blocks: list[str] | None = None) -> str:
        context = engine.resolve_node(name, blocks)
        return context

    return load_skill


def _make_skill_changelog(engine: SkillEngine):
    """Factory: creates the skill_changelog tool closure bound to the engine instance."""

    def skill_changelog(name: str) -> str:
        result = engine.get_node_changelog(name)
        if result is None:
            return f"Skill '{name}' not found. Use search_skills or list_skills to find it."
        return json.dumps(result, indent=2)

    return skill_changelog


def _make_save_skill(engine: SkillEngine):
    """Factory: creates the save_skill tool closure bound to the engine instance."""

    def save_skill(
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
        reason: str = "",
    ) -> str:
        result = engine.upsert_skill(
            name=name,
            keywords=keywords,
            dense=dense,
            full=full,
            steps=steps,
            code=code,
            code_language=code_language,
            warning=warning,
            assertions=assertions,
            requires=requires,
            reason=reason,
        )
        return json.dumps(result, indent=2)

    return save_skill


# ==========================================
# JSON Schemas (Anthropic Tool Format)
# ==========================================

SEARCH_SKILLS_SCHEMA = {
    "name": "search_skills",
    "description": (
        "Pass 1 of skill loading. Search the loaded .og skill files for skills matching a "
        "keyword query. Returns a lightweight list of matching skill names, roles, and "
        "keywords. Use this FIRST; then peek_skill the most promising names before loading. "
        "ALWAYS call this tool when the user asks you to do something you don't inherently "
        "know how to do."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A natural language query or keywords to search for "
                "(e.g., 'deploy production kubernetes').",
            }
        },
        "required": ["query"],
    },
}

LIST_SKILLS_SCHEMA = {
    "name": "list_skills",
    "description": (
        "List ALL currently loaded .og skills available to the agent. "
        "Use this when you want to see what capabilities you have, or when the user asks "
        "'what can you do?'"
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

PEEK_SKILL_SCHEMA = {
    "name": "peek_skill",
    "description": (
        "Pass 2 of skill loading. Given one or more skill node names, return each node's "
        "compressed `dense` summary, any warning, its required dependencies, and a manifest "
        "of which heavy blocks (full, steps, code, ...) exist and their approximate token "
        "cost. This is cheap — use it after search_skills to decide whether you actually need "
        "to load a skill in full, and which one. Does NOT return the heavy block contents."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact skill node names to peek at (e.g., ['deploy_to_prod']).",
            }
        },
        "required": ["names"],
    },
}

LOAD_SKILL_SCHEMA = {
    "name": "load_skill",
    "description": (
        "Pass 3 of skill loading. Load the heavy behavioral content of a specific skill by "
        "its exact name: full instructions, ordered steps, code, and mandatory assertions. "
        "You MUST follow the instructions and steps exactly as written, and execute any "
        "assertions before proceeding. Only call this after peek_skill confirms you need it. "
        "By default this loads the execution-critical blocks; pass `blocks` to narrow to just "
        "what you need (e.g., ['steps'] or ['code']) or ['all'] for everything. Dependencies "
        "are listed by name in the result — load_skill them only if you actually need them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The exact name of the skill node to load (e.g., 'deploy_to_prod').",
            },
            "blocks": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional subset of blocks to render: any of warning, dense, summary, "
                    "full, steps, code, table, list, example, note, reference, assertions, "
                    "edges — or ['all']. Omit for the execution-critical default set."
                ),
            },
        },
        "required": ["name"],
    },
}


SKILL_CHANGELOG_SCHEMA = {
    "name": "skill_changelog",
    "description": (
        "Delta-load check for a skill. Return a skill's current version, last-updated "
        "date, and changelog WITHOUT loading its heavy content. Use this when you (or a "
        "previous session) already loaded a skill and want to know whether it changed "
        "before spending tokens to load_skill it again — if the version/changelog is "
        "unchanged since you last read it, you can reuse what you already know."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The exact skill node name (e.g., 'deploy_to_prod').",
            }
        },
        "required": ["name"],
    },
}


SAVE_SKILL_SCHEMA = {
    "name": "save_skill",
    "description": (
        "Author or refine a reusable .og skill so you (and future sessions) can reload it via "
        "search_skills/load_skill instead of re-deriving it. Call this AFTER you successfully "
        "work out how to do a non-trivial task that has no existing skill, to capture the "
        "procedure. Calling it again with the same name refines the existing skill (its "
        "changelog and version are updated automatically). The `dense` block MUST be a compact "
        "pipe-separated keyword summary (a few words, NOT sentences) — oversized dense blocks "
        "are rejected. Put the real prose in `full` and the actions in `steps`. The result may "
        "include `lint_advisories` (e.g. missing keywords, no Pass-3 content) — read them and "
        "refine the skill so it stays searchable and useful."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name; becomes a snake_case id (e.g., 'rotate_api_keys').",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Search keywords that should route a query to this skill.",
            },
            "dense": {
                "type": "string",
                "description": (
                    "Compact pipe-separated summary, e.g. 'rotate|api keys|vault|zero-downtime'."
                ),
            },
            "full": {
                "type": "string",
                "description": "Full explanation/context for the skill (Pass-3 prose).",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered steps to execute the task.",
            },
            "code": {"type": "string", "description": "Optional reference code block body."},
            "code_language": {
                "type": "string",
                "description": "Language tag for the code block (e.g., 'bash', 'python').",
            },
            "warning": {"type": "string", "description": "Critical caveat, if any."},
            "assertions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Conditions to verify before/while executing.",
            },
            "requires": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of other skills this one depends on.",
            },
            "reason": {
                "type": "string",
                "description": "Short note recorded in the skill's changelog.",
            },
        },
        "required": ["name", "keywords", "dense"],
    },
}


# ==========================================
# Lazy tool-loading meta-tool
# ==========================================

USE_TOOLS_SCHEMA = {
    "name": "use_tools",
    "description": (
        "Activate an optional tool group for the rest of THIS turn, making its tools "
        "callable on your next step. Only core tools are sent by default to keep your "
        "context lean; call this when a task needs a capability that isn't currently "
        "available. The available group names are listed in the system prompt. "
        "Note: load_skill auto-activates a skill's own tools, so you rarely need this for "
        "skills — use it for domain groups like 'schedule', 'sandbox', or 'browser'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "group": {
                "type": "string",
                "description": "The tool group to activate for the rest of the turn.",
            },
        },
        "required": ["group"],
    },
}


def _make_use_tools(registry: ToolRegistry):
    """Build the use_tools handler. It only confirms — the ExecutionLoop performs the
    actual per-turn activation by intercepting the call (mirrors load_skill)."""

    def use_tools(group: str) -> str:
        groups = registry.available_groups() - {"core"}
        if group in groups:
            return f"Activated tool group '{group}'. Its tools are now available to call."
        return f"Unknown group '{group}'. Available groups: {sorted(groups)}"

    return use_tools


# ==========================================
# Registration Helper
# ==========================================


def register_og_tools(registry: ToolRegistry, engine: SkillEngine):
    """
    Mount all ObjectGraph skill tools onto the provided ToolRegistry.

    Args:
        registry: The tool registry to register into.
        engine: The initialized SkillEngine with skills already loaded.
    """
    registry.register(SEARCH_SKILLS_SCHEMA, _make_search_skills(engine))
    registry.register(LIST_SKILLS_SCHEMA, _make_list_skills(engine))
    registry.register(PEEK_SKILL_SCHEMA, _make_peek_skill(engine))
    registry.register(LOAD_SKILL_SCHEMA, _make_load_skill(engine))
    registry.register(SKILL_CHANGELOG_SCHEMA, _make_skill_changelog(engine))
    registry.register(SAVE_SKILL_SCHEMA, _make_save_skill(engine))
