"""
ObjectGraph Skill Tools

This module exposes the SkillEngine to the LLM as callable tools.
These are the tools that make Tokoboros fundamentally different from every other agent —
instead of dumping all skills into the system prompt, the agent surgically queries
for exactly the knowledge it needs via these tools.

The flow is:
    1. User asks "deploy my app to production"
    2. Agent calls `search_skills(query="deploy production")` → gets a list of matching skill names
    3. Agent calls `load_skill(name="deploy_to_prod")` → gets the full instructions + steps + assertions
    4. Agent follows the steps and runs the assertions before executing
"""

import json
from tokoboros.og_engine.engine import SkillEngine
from tokoboros.core.loop import ToolRegistry


def _make_search_skills(engine: SkillEngine):
    """Factory: creates the search_skills tool closure bound to the engine instance."""
    def search_skills(query: str) -> str:
        results = engine.search(query)
        if not results:
            return "No matching skills found. The user may need to add a .og file to ~/.tokoboros/skills/"
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


def _make_load_skill(engine: SkillEngine):
    """Factory: creates the load_skill tool closure bound to the engine instance."""
    def load_skill(name: str) -> str:
        context = engine.resolve_context(name)
        return context
    return load_skill


# ==========================================
# JSON Schemas (Anthropic Tool Format)
# ==========================================

SEARCH_SKILLS_SCHEMA = {
    "name": "search_skills",
    "description": (
        "Search the loaded .og skill files for skills matching a keyword query. "
        "Returns a lightweight list of matching skill names, roles, and keywords. "
        "Use this FIRST to find which skill to load before calling load_skill. "
        "ALWAYS call this tool when the user asks you to do something you don't inherently know how to do."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A natural language query or keywords to search for (e.g., 'deploy production kubernetes')."
            }
        },
        "required": ["query"]
    }
}

LIST_SKILLS_SCHEMA = {
    "name": "list_skills",
    "description": (
        "List ALL currently loaded .og skills available to the agent. "
        "Use this when you want to see what capabilities you have, or when the user asks 'what can you do?'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

LOAD_SKILL_SCHEMA = {
    "name": "load_skill",
    "description": (
        "Load the FULL behavioral context of a specific skill by its exact name. "
        "This returns the complete instructions, ordered steps, and mandatory assertions. "
        "You MUST follow the instructions and steps exactly as written in the loaded skill. "
        "You MUST execute any assertions listed before proceeding with the task. "
        "Only call this after finding the skill name via search_skills or list_skills."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The exact name of the skill node to load (e.g., 'deploy_to_prod')."
            }
        },
        "required": ["name"]
    }
}


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
    registry.register(LOAD_SKILL_SCHEMA, _make_load_skill(engine))
