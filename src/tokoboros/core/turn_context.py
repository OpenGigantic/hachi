"""
Turn Context — Immutable per-request execution state

This module provides TurnContext, which captures all the immutable state needed
for a single turn of agent execution. Instead of mutating shared loop state
(base_system_prompt), each turn gets its own frozen context snapshot.

Benefits:
    - Thread-safe: no mutation of shared state
    - Traceable: full execution context is captured once at turn start
    - Testable: can pass context through mock pipelines
    - Serializable: can be logged and replayed for debugging
"""

import datetime
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnContext:
    """
    Immutable execution context for a single agent turn.

    Captures all the dynamic/static state needed to run the LLM + tools
    in a single turn, preventing mutations to shared ExecutionLoop state.
    """

    # Turn identity
    turn_id: str
    session_id: str
    timestamp: str  # ISO format

    # User input for this turn
    user_input: str

    # Composed system prompt (time-aware, memory-aware, frozen for this turn)
    system_prompt: str

    # Prompt-cache-aware split of ``system_prompt``: ``cacheable_system`` is the
    # stable prefix (brain/.og/perms + session-frozen profiles) that carries the
    # cache breakpoint; ``dynamic_system`` is the per-turn tail (time, recalled
    # context) that must sit AFTER the breakpoint so it never invalidates the cache.
    cacheable_system: str = ""
    dynamic_system: str = ""

    # Workspace state snapshots (frozen at turn start)
    active_skill: str | None = None
    user_profile_snapshot: str | None = None
    agent_profile_snapshot: str | None = None
    recalled_context: str | None = None

    # Execution parameters
    max_iterations: int = 15

    # Origin metadata (for audit/tracing)
    surface: str = "cli"  # "cli", "tui", "web", "webhook", etc.
    source_ip: str | None = None

    @classmethod
    def build(
        cls,
        turn_id: str,
        session_id: str,
        user_input: str,
        system_prompt_base: str,
        *,
        active_skill: str | None = None,
        user_profile_snapshot: str | None = None,
        agent_profile_snapshot: str | None = None,
        recalled_context: str | None = None,
        max_iterations: int = 15,
        surface: str = "cli",
        source_ip: str | None = None,
    ) -> "TurnContext":
        """
        Factory method to build a TurnContext with proper timestamp and composition.

        Args:
            turn_id: Unique identifier for this turn (UUID or sequence)
            session_id: The session this turn belongs to
            user_input: The user's input for this turn
            system_prompt_base: Base system prompt (brain.og + skills + perms)
            active_skill: Current skill name if any
            user_profile_snapshot: User profile facts captured for this turn
            agent_profile_snapshot: Agent profile facts captured for this turn
            recalled_context: Pre-fetched context from memory
            max_iterations: Max loop iterations for this turn
            surface: Where this request originated (for audit)
            source_ip: Originating IP if remote (for audit)

        Returns:
            A fully initialized immutable TurnContext.
        """
        timestamp = datetime.datetime.now(datetime.UTC).isoformat()

        # --- Cacheable prefix: stable across the session ---------------------
        # The brain/.og/permissions base plus session-frozen profiles do not
        # change turn-to-turn, so they carry the prompt-cache breakpoint and are
        # read from cache (0.1x) on every subsequent turn.
        cacheable_parts = [system_prompt_base]
        if user_profile_snapshot:
            cacheable_parts.append(
                f"\n\n--- USER PROFILE (captured for this session) ---\n{user_profile_snapshot}"
            )
        if agent_profile_snapshot:
            cacheable_parts.append(
                f"\n\n--- AGENT PROFILE (captured for this session) ---\n{agent_profile_snapshot}"
            )
        cacheable_system = "".join(cacheable_parts)

        # --- Dynamic tail: changes every turn -------------------------------
        # The time header (changes every second) and per-query recalled context
        # MUST stay after the cache breakpoint, or they'd force a fresh cache
        # write of the whole prefix on every turn (the classic caching anti-pattern).
        dynamic_parts = [_build_time_header()]
        if recalled_context:
            dynamic_parts.append(f"\n\n--- RECALLED CONTEXT ---\n{recalled_context}")
        dynamic_system = "".join(dynamic_parts)

        # Full prompt keeps the stable content first (model reads instructions
        # before per-turn context); also the single-block fallback path then caches.
        composed_prompt = f"{cacheable_system}\n\n{dynamic_system}"

        return cls(
            turn_id=turn_id,
            session_id=session_id,
            timestamp=timestamp,
            user_input=user_input,
            system_prompt=composed_prompt,
            cacheable_system=cacheable_system,
            dynamic_system=dynamic_system,
            active_skill=active_skill,
            user_profile_snapshot=user_profile_snapshot,
            agent_profile_snapshot=agent_profile_snapshot,
            recalled_context=recalled_context,
            max_iterations=max_iterations,
            surface=surface,
            source_ip=source_ip,
        )


def _build_time_header() -> str:
    """Generate the dynamic time block (changes every turn — NOT cached)."""
    local_dt = datetime.datetime.now().astimezone()
    current_time = local_dt.strftime("%A, %Y-%m-%d %H:%M:%S")
    tz_offset = local_dt.strftime("%z")
    tz_fmt = f"{tz_offset[:3]}:{tz_offset[3:]}" if len(tz_offset) == 5 else tz_offset

    return (
        f"--- CURRENT TIME AND DATE ---\n"
        f"The current local time is: {current_time}\n"
        f"Your Local Timezone Offset is: {tz_fmt}\n"
        f"Use this context to resolve relative times like 'today' or 'tomorrow', "
        f"and ALWAYS append the timezone offset when creating ISO strings.\n"
    )
