"""
Tokoboros Sandbox Tools

Exposes the Ephemeral Python Sandbox to the LLM orchestration engine.
"""

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.sandbox import SandboxExecutor
from tokoboros.core.permissions import PermissionGate

def register_sandbox_tools(registry: ToolRegistry, gate: PermissionGate = None) -> None:
    """Registers the python_sandbox tool."""
    
    executor = SandboxExecutor()

    def run_python_sandbox(script_content: str, dependencies: list = None) -> str:
        """
        Executes arbitrary Python code in an ephemeral sandbox using `uv`.
        """
        # Optional: Integrate with PermissionGate
        # If the gate is strictly Sandbox tier, we might want to block network requests inside the script,
        # but for now, we rely on the orchestrator's broader tier.
        
        try:
            result = executor.execute(script_content, dependencies)
            return result.formatted_output
        except EnvironmentError as e:
            return str(e)
        except Exception as e:
            return f"Sandbox tool failed: {str(e)}"

    registry.register({
        "name": "python_sandbox",
        "description": (
            "Write and execute arbitrary Python code in a secure, ephemeral, and lightning-fast sandbox. "
            "Use this tool exclusively for data processing, web scraping, chart generation (matplotlib), or math. "
            "DO NOT use this for OS file management or email sending (use the native tools for that). "
            "The sandbox automatically installs requested PyPI dependencies in milliseconds using 'uv'. "
            "Outputs must be communicated via print() statements. If you generate a file (e.g. a PNG chart), "
            "save it to an absolute path (like the user's Desktop) and print that path so you can use it in later steps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script_content": {
                    "type": "string",
                    "description": "The raw Python code to execute."
                },
                "dependencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A list of PyPI package names required to run the script (e.g., ['pandas', 'matplotlib'])."
                }
            },
            "required": ["script_content"]
        }
    }, run_python_sandbox)
