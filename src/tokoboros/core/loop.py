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

import logging
import time
from collections.abc import Callable
from typing import Any

from tokoboros.core.llm import BaseLLMClient, Message, ToolCall
from tokoboros.core.tool_results import ToolResultBlocks
from tokoboros.core.tracing import span
from tokoboros.core.turn_context import TurnContext

logger = logging.getLogger(__name__)

# Transient HTTP statuses worth retrying on (rate limit, overloaded, gateway errors).
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
_RETRYABLE_HINTS = ("overloaded", "rate limit", "timeout", "timed out", "temporarily", "connection")
_MAX_LLM_RETRIES = 3
_LLM_RETRY_BASE_DELAY = 1.0

# Estimated-token threshold above which the loop losslessly clears large, re-fetchable
# tool outputs from older turns (see MemoryManager.clear_stale_tool_results). Set high
# so ordinary short conversations are never touched — only genuinely large contexts.
_TOOL_RESULT_CLEAR_TOKENS = 12000


def _is_retryable_llm_error(exc: Exception) -> bool:
    """Best-effort detection of transient LLM/provider errors (provider-agnostic).

    Checks an HTTP status code (Anthropic/OpenAI SDK exceptions expose
    ``status_code``; httpx exposes ``response.status_code``) and falls back to
    message heuristics for connection/timeout errors that carry no status.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    message = str(exc).lower()
    return any(hint in message for hint in _RETRYABLE_HINTS)


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
        # tool_name -> lazy-loading group. Default "core" means a tool is always
        # sent unless the orchestrator explicitly reclassifies it, so a forgotten
        # tool fails safe (visible/callable) rather than silently hidden.
        self._groups: dict[str, str] = {}

    def register(self, schema: dict[str, Any], func: Callable[..., Any], group: str = "core"):
        """
        Register a new tool.

        Args:
            schema: The JSON Schema definition of the tool (Anthropic/OpenAI format).
            func: The actual Python callable that executes the tool.
            group: Lazy-loading group; "core" tools are always sent to the model,
                   others are sent only when their group is active for the turn.
        """
        tool_name = schema.get("name")
        if not tool_name:
            raise ValueError("Tool schema must contain a 'name' field.")

        self._tools[tool_name] = func
        self._schemas.append(schema)
        self._groups[tool_name] = group

    def classify(
        self,
        core_names: set[str],
        group_by_name: dict[str, str],
        prefix_groups: dict[str, str] | None = None,
        default: str = "core",
    ) -> None:
        """Stamp lazy-loading groups by tool name, applied once after registration.

        Resolution order (first match wins):
        1. ``core_names`` → ``"core"``
        2. ``group_by_name`` exact match → mapped group
        3. ``prefix_groups`` prefix match → mapped group (longest prefix wins)
        4. Tools already tagged with an explicit non-core group are left intact
        5. Everything else (or tools still at the ``register`` default of "core") → ``default``

        Args:
            core_names: Tool names that should be in the "core" (always-on) group.
            group_by_name: Exact name → group mapping for statically-known tools.
            prefix_groups: Prefix → group mapping, e.g. ``{"google_": "google"}``.
            default: Group assigned to any otherwise-unclassified tool.
        """
        for name in self._tools:
            if name in core_names:
                self._groups[name] = "core"
            elif name in group_by_name:
                self._groups[name] = group_by_name[name]
            elif self._groups.get(name, "core") == "core":
                # Only reclassify tools that haven't been given an explicit group.
                if prefix_groups:
                    # Longest matching prefix wins to avoid ambiguity.
                    match = max(
                        (p for p in prefix_groups if name.startswith(p)),
                        key=len,
                        default=None,
                    )
                    if match:
                        self._groups[name] = prefix_groups[match]
                        continue
                self._groups[name] = default

    def available_groups(self) -> set[str]:
        """Return the set of groups currently in use (for system-prompt listing)."""
        return set(self._groups.values())

    def get_schemas(self, active_groups: set[str] | None = None) -> list[dict[str, Any]]:
        """Retrieve tool schemas.

        ``active_groups=None`` returns every schema (backward-compatible default used
        by sub-agents and existing callers). Otherwise returns "core" plus the active
        groups, so the model only sees the lazily-loaded tools it has unlocked.
        """
        if active_groups is None:
            return self._schemas
        allowed = {"core"} | active_groups
        return [s for s in self._schemas if self._groups.get(s.get("name"), "core") in allowed]

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
        cost_tracker=None,
        max_cost_usd: float = 0.0,
        max_tokens: int = 0,
        max_cost_per_turn: float = 0.0,
        max_tokens_per_turn: int = 0,
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
            max_cost_usd: Per-session cost cap in USD (0 = unlimited).
            max_tokens: Per-session token cap (0 = unlimited).
            max_cost_per_turn: Per-turn cost cap in USD (0 = unlimited). Stops the
                agent mid-turn if a single user message would cost more than this.
            max_tokens_per_turn: Per-turn token cap (0 = unlimited). Stops the agent
                mid-turn if a single user message would consume more than this.
        """
        self.llm_client = llm_client
        self.registry = registry
        self.memory = memory
        self.base_system_prompt = base_system_prompt
        self.on_event = on_event or (lambda *a, **kw: None)
        self.skill_engine = skill_engine
        self.cost_tracker = cost_tracker
        # Per-session safety caps (0 = unlimited). Enforced between iterations so a
        # runaway tool-calling loop can't silently drain the user's API budget.
        self.max_cost_usd = max_cost_usd
        self.max_tokens = max_tokens
        # Per-turn safety caps (0 = unlimited). Enforced after each LLM call within
        # a single run() invocation so a runaway tool-calling sequence within one
        # user message can't exhaust the user's API budget.
        self.max_cost_per_turn = max_cost_per_turn
        self.max_tokens_per_turn = max_tokens_per_turn
        # Baseline snapshot taken at the start of each run(); used to compute deltas.
        self._turn_tokens_start: int = 0
        self._turn_cost_start: float = 0.0
        self._current_turn_context: TurnContext | None = None
        # Tool groups unlocked for the current turn (besides always-on "core").
        # Reset each run(); mutated when the model calls use_tools / load_skill.
        self._active_groups: set[str] = set()

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

    def _system_blocks(self) -> tuple[str, str | None]:
        """Return ``(cacheable prefix, per-turn tail)`` for the system prompt.

        Uses the frozen TurnContext's precomputed cache-aware split when present
        so the stable brain/.og prefix carries the cache breakpoint and the
        volatile time/recall tail stays after it. Falls back to the full prompt as
        a single (cacheable) block when there is no split — same as before.
        """
        tc = self._current_turn_context
        if tc is not None and tc.cacheable_system:
            return tc.cacheable_system, (tc.dynamic_system or None)
        return self._build_system_prompt(), None

    def _generate_with_retry(self, **kwargs: Any):
        """Call the LLM with bounded exponential-backoff retry on transient errors.

        A single rate-limit / overloaded / network blip should not abort the whole
        turn. Non-retryable errors (auth, 400s) propagate immediately; retryable
        ones are retried up to ``_MAX_LLM_RETRIES`` times before re-raising.
        """
        delay = _LLM_RETRY_BASE_DELAY
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                return self.llm_client.generate(**kwargs)
            except Exception as exc:
                if attempt >= _MAX_LLM_RETRIES or not _is_retryable_llm_error(exc):
                    raise
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt,
                    _MAX_LLM_RETRIES,
                    exc,
                    delay,
                )
                self.on_event("llm_retry", {"attempt": attempt, "error": str(exc)})
                time.sleep(delay)
                delay *= 2

    def _budget_exceeded(self) -> str | None:
        """Return a reason string if a per-session budget cap is hit, else None."""
        if self.cost_tracker is None or (self.max_cost_usd <= 0 and self.max_tokens <= 0):
            return None
        snap = self.cost_tracker.snapshot()
        if self.max_tokens > 0 and snap.total_tokens >= self.max_tokens:
            return f"token budget reached ({snap.total_tokens:,} >= {self.max_tokens:,})"
        if self.max_cost_usd > 0 and snap.estimated_cost_usd >= self.max_cost_usd:
            return (
                f"cost budget reached (~${snap.estimated_cost_usd:.4f} >= ${self.max_cost_usd:.2f})"
            )
        return None

    def _turn_budget_exceeded(self) -> str | None:
        """Return a reason string if a per-turn budget cap is hit, else None.

        Compares the current cumulative snapshot against the baseline recorded at
        the start of this ``run()`` call, so only the spend incurred by the current
        user message is counted — not earlier turns in the same session.
        """
        if self.cost_tracker is None or (
            self.max_cost_per_turn <= 0 and self.max_tokens_per_turn <= 0
        ):
            return None
        snap = self.cost_tracker.snapshot()
        turn_tokens = snap.total_tokens - self._turn_tokens_start
        turn_cost = snap.estimated_cost_usd - self._turn_cost_start
        if self.max_tokens_per_turn > 0 and turn_tokens >= self.max_tokens_per_turn:
            return (
                f"per-turn token limit reached ({turn_tokens:,} >= {self.max_tokens_per_turn:,})"
            )
        if self.max_cost_per_turn > 0 and turn_cost >= self.max_cost_per_turn:
            return (
                f"per-turn cost limit reached (~${turn_cost:.4f} >= ${self.max_cost_per_turn:.2f})"
            )
        return None

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

        # Only "core" tools are sent until the model unlocks groups this turn.
        self._active_groups = set()

        # Record the cost/token baseline at turn start so _turn_budget_exceeded()
        # can compute deltas rather than checking cumulative session totals.
        if self.cost_tracker is not None:
            _snap = self.cost_tracker.snapshot()
            self._turn_tokens_start = _snap.total_tokens
            self._turn_cost_start = _snap.estimated_cost_usd

        # FIX H5: honour max_iterations declared on the TurnContext (overrides default)
        if turn_context is not None:
            tc_max = getattr(turn_context, "max_iterations", None)
            if tc_max is not None:
                max_iterations = tc_max

        try:
            iterations = 0

            while iterations < max_iterations:
                iterations += 1

                # Stop before spending more if a per-session budget cap was hit.
                budget_reason = self._budget_exceeded()
                if budget_reason:
                    self.on_event("budget_exceeded", {"reason": budget_reason})
                    self.memory.complete_current_task(f"Stopped: {budget_reason}")
                    return f"[BUDGET LIMIT REACHED] {budget_reason}"

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

                # Lossless context reclamation: once history grows large, replace big
                # re-fetchable tool outputs in older turns with a short placeholder.
                # Keeps every tool_use/tool_result pair intact (no API 400) and costs
                # no tokens, running BEFORE the lossy LLM-summary compaction. Guarded
                # by getattr so alternate memory backends without it are unaffected.
                _estimate = getattr(self.memory, "estimate_token_count", None)
                _clear = getattr(self.memory, "clear_stale_tool_results", None)
                if (
                    callable(_estimate)
                    and callable(_clear)
                    and _estimate() > _TOOL_RESULT_CLEAR_TOKENS
                ):
                    cleared = _clear()
                    if cleared:
                        self.on_event("tool_results_cleared", {"count": cleared})

                self.on_event("thinking", {"iteration": iterations})

                # Build the system prompt split into a stable cacheable prefix and a
                # per-turn tail, so prompt caching reads the brain/.og prefix instead
                # of rewriting it every turn (time/recall live in the dynamic tail).
                cacheable_system, dynamic_system = self._system_blocks()
                gen_kwargs: dict[str, Any] = {
                    "messages": self.memory.history,
                    "system_prompt": cacheable_system,
                    "tools": self.registry.get_schemas(self._active_groups),
                }
                # Only pass the dynamic block when there is one, so clients without
                # the cache-split parameter keep working unchanged.
                if dynamic_system is not None:
                    gen_kwargs["dynamic_system"] = dynamic_system

                # 1. Ask the LLM to generate the next step (with transient-error retry)
                with span("llm.generate", model=getattr(self.llm_client, "model", "unknown")):
                    response = self._generate_with_retry(**gen_kwargs)

                # Account for token usage / estimated cost and surface it live.
                if self.cost_tracker is not None:
                    model_name = getattr(self.llm_client, "model", "unknown")
                    self.cost_tracker.record(model_name, response.usage)
                    self.on_event("usage", self.cost_tracker.snapshot().to_dict())

                # Stop this turn if a per-turn budget cap is now exceeded.
                turn_budget_reason = self._turn_budget_exceeded()
                if turn_budget_reason:
                    self.on_event("budget_exceeded", {"reason": turn_budget_reason})
                    self.memory.complete_current_task(f"Stopped: {turn_budget_reason}")
                    return f"[BUDGET LIMIT REACHED] {turn_budget_reason}"

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

                    # Lazy tool loading: the model unlocks an optional group for the
                    # rest of this turn; the handler only confirms — state lives here.
                    if tool_call.name == "use_tools":
                        group = tool_call.input.get("group", "")
                        if group:
                            self._active_groups.add(group)

                    # Detect skill switch via load_skill calls (compaction deferred to next
                    # iteration)
                    if tool_call.name == "load_skill":
                        new_skill = tool_call.input.get("name", "")
                        if new_skill:
                            # Auto-unlock the skill's own tool group (its REST APIs are
                            # registered under group == skill name) so the model rarely
                            # needs an explicit use_tools call after loading a skill.
                            self._active_groups.add(new_skill)
                            # Phase 4: Track skill usage
                            if self.skill_engine:
                                self.skill_engine.record_use(new_skill)
                            if new_skill != self.memory.active_skill:
                                self.memory.signal_skill_switch(new_skill)

                    tool_error = False
                    try:
                        with span("tool.call", tool=tool_call.name):
                            result = self.registry.execute(tool_call)
                    except SuspendWorkflow as e:
                        suspend_reason = e.reason
                        break
                    except Exception as e:
                        # FIX H6: capture error and mark the tool_result block accordingly
                        result = f"Tool Execution Error: {str(e)}"
                        tool_error = True

                    # Most tools return text (stringified here). A tool that needs
                    # the model to *see* richer content (e.g. an image) returns a
                    # ToolResultBlocks, whose blocks pass through to the tool_result
                    # content untouched. Only this exact type opts into pass-through,
                    # so tools that return plain dicts keep being stringified.
                    if isinstance(result, ToolResultBlocks):
                        result_content: Any = list(result.blocks)
                        result_summary = result.summary or "[multimodal content]"
                    else:
                        result_content = str(result)
                        result_summary = result_content

                    self.on_event(
                        "tool_result", {"name": tool_call.name, "result": result_summary[:500]}
                    )

                    tr: dict[str, Any] = {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": result_content,
                    }
                    if tool_error:
                        tr["is_error"] = True  # FIX H6: signal failure to the Anthropic API
                    tool_results.append(tr)

                # Every tool_use block in the assistant message MUST have a matching
                # tool_result, or the next turn's history is rejected by the Anthropic
                # API (400). On suspend we break early, so synthesize results for the
                # suspending tool and any that never ran.
                answered_ids = {tr["tool_use_id"] for tr in tool_results}
                for tool_call in response.tool_calls:
                    if tool_call.id not in answered_ids:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call.id,
                                "content": "[not executed: workflow suspended]",
                            }
                        )

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
