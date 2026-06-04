"""
Autonomous Execution Loop (ReAct / Tool-Calling)

This module implements the core reasoning engine for Tokoboros. It manages the continuous
cycle of observing the environment, reasoning via the LLM, and executing tools until the
assigned task is completed.

Uses the MemoryManager for intelligent context management across skill switches.

Phase 1 Update:
    - Accepts immutable TurnContext per execution instead of mutating base_system_prompt
    - Prevents mutable shared state issues across concurrent sessions/turns
"""

from collections.abc import Callable
from typing import Any

from tokoboros.core.llm import BaseLLMClient, Message, ToolCall
from tokoboros.core.turn_context import TurnContext


class AgentException(Exception):
    """Base exception for agent execution errors."""

    pass


class SuspendWorkflow(Exception):
    """Signal that the agent loop should suspend and wait for an external trigger."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ToolRegistry:
    """
    Manages the registration and execution of agentic tools.
    Separates the tool schemas (for the LLM) from the actual Python implementations.
    """

    def __init__(self):
        self._tools: dict[str, Callable[..., Any]] = {}
        self._schemas: list[dict[str, Any]] = []

    def register(self, schema: dict[str, Any], func: Callable[..., Any]):
        """
        Register a new tool.

        Args:
            schema: The JSON Schema definition of the tool (Anthropic/OpenAI format).
            func: The actual Python callable that executes the tool.
        """
        tool_name = schema.get("name")
        if not tool_name:
            raise ValueError("Tool schema must contain a 'name' field.")

        self._tools[tool_name] = func
        self._schemas.append(schema)

    def get_schemas(self) -> list[dict[str, Any]]:
        """Retrieve all registered tool schemas."""
        return self._schemas

    def execute(self, tool_call: ToolCall) -> Any:
        """
        Execute a tool requested by the LLM.

        Args:
            tool_call: The ToolCall object containing the name and arguments.

        Returns:
            The execution result (stringified for LLM ingestion).
        """
        if tool_call.name not in self._tools:
            return f"Error: Tool '{tool_call.name}' is not registered."

        func = self._tools[tool_call.name]
        try:
            result = func(**tool_call.input)
            return result
        except SuspendWorkflow as e:
            raise e  # Let the execution loop handle suspension
        except Exception:
            raise  # FIX H6: re-raise so run() can set is_error on the tool_result block


class ExecutionLoop:
    """The main autonomous execution orchestrator.

    Manages the conversation state via MemoryManager and handles the LLM's
    multi-step tool interactions with automatic context compaction.
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        registry: ToolRegistry,
        memory,
        base_system_prompt: str = "",
        on_event: Callable[..., None] | None = None,
        skill_engine=None,
    ):
        """Initialize the execution loop.

        Args:
            llm_client: The LLM provider (e.g., AnthropicClient).
            registry: The registry containing all available tools.
            memory: The MemoryManager for intelligent history management.
            base_system_prompt: The base system instructions (from brain.og + skill awareness).
            on_event: Optional callback invoked during execution for UI rendering.
                      Signature: on_event(event_type: str, data: dict)
                      Event types: "thinking", "tool_call", "tool_result", "text_chunk",
                      "compaction"
            skill_engine: Optional SkillEngine for usage tracking on load_skill calls.
        """
        self.llm_client = llm_client
        self.registry = registry
        self.memory = memory
        self.base_system_prompt = base_system_prompt
        self.on_event = on_event or (lambda *a, **kw: None)
        self.skill_engine = skill_engine
        self._current_turn_context: TurnContext | None = None

    def add_user_message(self, text: str):
        """Add a user query to the active session history via the MemoryManager."""
        self.memory.add_message(Message(role="user", content=text))

    def _build_system_prompt(self) -> str:
        """
        Construct the full system prompt by combining the base prompt
        with the task ledger context header.

        If a TurnContext is active, uses the composed system_prompt from it.
        Otherwise, falls back to base_system_prompt + context_header.
        """
        if self._current_turn_context:
            # Use the frozen system prompt from the TurnContext
            # (already includes time header, memory snapshot, recall, and base prompt)
            return self._current_turn_context.system_prompt

        # Fallback for compatibility: build from base_system_prompt + task ledger
        context_header = self.memory.get_context_header()
        if context_header:
            return f"{self.base_system_prompt}\n\n{context_header}"
        return self.base_system_prompt

    def run(self, max_iterations: int = 15, turn_context: TurnContext | None = None) -> str:
        """
        Execute the autonomous loop until the model stops calling tools or hits the limit.

        Args:
            max_iterations: Safety threshold to prevent infinite tool-calling loops.
            turn_context: Optional immutable context for this turn (Phase 1 feature).
                         If provided, uses the composed system_prompt from the context
                         instead of mutating base_system_prompt. Prevents shared state issues.

        Returns:
            The final textual response from the agent.
        """
        # Set the turn context for this execution (used by _build_system_prompt)
        prev_context = self._current_turn_context
        self._current_turn_context = turn_context

        # FIX H5: honour max_iterations declared on the TurnContext (overrides default)
        if turn_context is not None:
            tc_max = getattr(turn_context, "max_iterations", None)
            if tc_max is not None:
                max_iterations = tc_max

        try:
            iterations = 0

            while iterations < max_iterations:
                iterations += 1

                # Apply any deferred compaction from a previous skill switch.
                # This is the SAFE moment — all tool_use/tool_result pairs are complete.
                if self.memory.apply_pending_compaction():
                    self.on_event(
                        "compaction",
                        {
                            "old_skill": getattr(self.memory, "_pending_old_skill", "unknown"),
                            "new_skill": self.memory.active_skill,
                        },
                    )

                self.on_event("thinking", {"iteration": iterations})

                # Build the dynamic system prompt (includes task ledger)
                system_prompt = self._build_system_prompt()

                # 1. Ask the LLM to generate the next step
                response = self.llm_client.generate(
                    messages=self.memory.history,
                    system_prompt=system_prompt,
                    tools=self.registry.get_schemas(),
                )

                # Record the assistant's generation in history
                assistant_content: list[dict[str, Any]] = []

                if response.content:
                    assistant_content.append({"type": "text", "text": response.content})
                    self.on_event("text_chunk", {"text": response.content})

                for tc in response.tool_calls:
                    assistant_content.append(
                        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                    )

                if not assistant_content:
                    break

                self.memory.add_message(Message(role="assistant", content=assistant_content))

                # 2. If no tools were called, the agent has finished its task
                if not response.tool_calls:
                    # Auto-sync: extract facts from this turn in the background
                    self.memory.sync_turn(self.memory._last_user_query, response.content or "")
                    # Log the completed task to the ledger
                    self.memory.complete_current_task(
                        response.content[:100] if response.content else "Task completed"
                    )
                    return response.content

                # 3. Execute requested tools and detect skill switches
                tool_results: list[dict[str, Any]] = []
                suspend_reason = None

                for tool_call in response.tool_calls:
                    self.on_event("tool_call", {"name": tool_call.name, "input": tool_call.input})

                    # Detect skill switch via load_skill calls (compaction deferred to next
                    # iteration)
                    if tool_call.name == "load_skill":
                        new_skill = tool_call.input.get("name", "")
                        if new_skill:
                            # Phase 4: Track skill usage
                            if self.skill_engine:
                                self.skill_engine.record_use(new_skill)
                            if new_skill != self.memory.active_skill:
                                self.memory.signal_skill_switch(new_skill)

                    tool_error = False
                    try:
                        result = self.registry.execute(tool_call)
                    except SuspendWorkflow as e:
                        suspend_reason = e.reason
                        break
                    except Exception as e:
                        # FIX H6: capture error and mark the tool_result block accordingly
                        result = f"Tool Execution Error: {str(e)}"
                        tool_error = True

                    self.on_event(
                        "tool_result", {"name": tool_call.name, "result": str(result)[:500]}
                    )

                    tr: dict[str, Any] = {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": str(result),
                    }
                    if tool_error:
                        tr["is_error"] = True  # FIX H6: signal failure to the Anthropic API
                    tool_results.append(tr)

                if tool_results:
                    self.memory.add_message(Message(role="user", content=tool_results))

                if suspend_reason:
                    self.memory.complete_current_task(f"Suspended: {suspend_reason}")
                    return f"[WORKFLOW SUSPENDED] {suspend_reason}"

            raise AgentException(
                f"Execution loop exceeded maximum iterations ({max_iterations}). Task incomplete."
            )

        finally:
            # Restore previous context
            self._current_turn_context = prev_context
