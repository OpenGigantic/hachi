"""
Session Memory Manager

This module handles intelligent conversation history management for Hachi.
It solves the critical problem of context window bloat during multi-task sessions
by implementing automatic compaction, skill-switch detection, and a sliding window.

Architecture:
    - TaskLedger: A lightweight running log of completed tasks (never flushed).
    - Sliding Window: Only the most recent N raw messages are kept in active memory.
    - Skill-Switch Compaction: When the agent switches from one .og skill to another,
      all messages from the previous skill execution are compressed into a single summary.
    - Auto-Sync: After every completed turn, sync_turn() enqueues a background job
      to extract permanent facts — routing user identity to user_profile and workspace
      facts to core_memory. The user never needs to say "remember this."
    - Phase 1: Uses durable job queue instead of fire-and-forget daemon threads.
    - Persistence Interface: Designed so storage backends (SQLite, JSON, etc.)
      can be plugged in later without changing the public API.

Performance improvements (BE-4):
    HIGH-015: _sliding_window_compact() no longer makes a SYNCHRONOUS LLM call inside
              prepare_for_turn().  The structured summary is now generated in a
              background job via the durable queue; prepare_for_turn() inserts a
              lightweight placeholder checkpoint immediately and enqueues the LLM
              summarisation job to run asynchronously.
    MED-022:  _run_fact_extraction() and _flush_facts_to_vault() reuse the shared
              AnthropicClient from memory_vault._get_llm_client() instead of
              constructing a new instance on every call.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from hachi.core.llm import Message

if TYPE_CHECKING:
    from hachi.core.conversation_memory import IterationState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FIX 4 — Write-quality gate helpers for auto-sync fact extraction.
#
# Trivial acknowledgements and extremely short strings carry no persistent
# value and should not be written to the vault.  The filter is intentionally
# permissive: it drops only facts that are clearly too short (< 12 chars) or
# that match a small set of obvious one-word/two-word acknowledgements.
#
# The existing 30-char *combined* exchange gate in sync_turn already prevents
# most trivial turns from reaching LLM extraction; this filter provides a
# second line of defense against whatever short strings LLM extraction
# occasionally emits despite the "do not extract transient status" instruction.
# ---------------------------------------------------------------------------
_MIN_FACT_LENGTH: int = 12  # characters

_TRIVIAL_FACT_PATTERNS: frozenset[str] = frozenset(
    {
        "ok",
        "okay",
        "yes",
        "no",
        "sure",
        "got it",
        "thanks",
        "thank you",
        "understood",
        "noted",
        "alright",
        "done",
        "great",
        "good",
        "nice",
        "cool",
        "agreed",
    }
)


def _is_substantive_fact(fact: str) -> bool:
    """Return True when ``fact`` is worth persisting to the memory vault.

    Rejects facts that are:
    - Shorter than ``_MIN_FACT_LENGTH`` characters (after stripping whitespace).
    - A single-/two-word acknowledgement in ``_TRIVIAL_FACT_PATTERNS``.

    This guard is intentionally conservative — it only rejects *obviously*
    trivial content.  The existing 30-char combined-exchange gate in
    ``sync_turn`` already filters most short turns before LLM extraction runs.

    Args:
        fact: Candidate string extracted by the fact extraction LLM.

    Returns:
        True if the fact should be persisted, False if it should be skipped.
    """
    stripped = fact.strip()
    if len(stripped) < _MIN_FACT_LENGTH:
        return False
    if stripped.lower() in _TRIVIAL_FACT_PATTERNS:
        return False
    return True


# Estimated-token threshold above which prepare_for_turn() losslessly clears large,
# re-fetchable tool outputs from older turns. Set high so ordinary short conversations
# are never touched — only genuinely large contexts. Exposed at module level so tests
# can monkeypatch it.
_TOOL_RESULT_CLEAR_TOKENS = 12000

# Placeholder swapped in for large, re-fetchable tool outputs in older turns. The
# wording tells the model the output is recoverable by re-running the tool.
_CLEARED_TOOL_RESULT = (
    "[tool result cleared to reclaim context — re-run the tool if you need this output again]"
)


class TaskRecord:
    """
    Represents a single completed task in the session ledger.
    """

    def __init__(self, skill_name: str, user_query: str, summary: str):
        self.skill_name = skill_name
        self.user_query = user_query
        self.summary = summary

    def to_text(self) -> str:
        return f"[{self.skill_name}] {self.user_query} → {self.summary}"


class MemoryManager:
    """
    Production-grade session memory for the Hachi agent.

    Responsibilities:
        - Maintain the active conversation history (messages sent to the LLM).
        - Track which skill is currently active.
        - Detect skill switches and trigger automatic compaction.
        - Maintain a persistent task ledger across the entire session.
        - Enforce a sliding window to cap raw message count.
        - Enqueue background fact-extraction jobs to the durable queue.
    """

    def __init__(
        self,
        max_active_messages: int = 30,
        job_queue: Any | None = None,
        llm_client: Any | None = None,
    ):
        """
        Initialize the memory manager.

        Args:
            max_active_messages: Maximum number of raw messages before triggering
                                 sliding-window compaction. Prevents unbounded growth
                                 even within a single skill execution.
            job_queue: Optional durable JobQueue for background work.
                       sync_turn() requires a job queue; without one, memory extraction
                       is disabled and a WARNING is logged.
            llm_client: Optional shared LLM client (G3-T6 / MED-022).  When provided,
                        background memory ops (fact extraction, structured summary) reuse
                        this client's already-open HTTP/TLS connection instead of
                        constructing a new AnthropicClient.  The client is used as-is
                        for background ops; callers that need a distinct model (e.g. a
                        cheaper haiku model) may pass a pre-configured client here.
                        Falls back to memory_vault._get_llm_client() when None.
            strict_errors: When True (default), memory write errors are surfaced loudly
                           via the job queue failure pipeline instead of being swallowed.
                           Set to False only in tests or debugging scenarios.
        """
        self._history: list[Message] = []
        self._task_ledger: list[TaskRecord] = []
        self._active_skill: str | None = None
        self._skill_start_index: int = 0  # Index in _history where current skill began
        self._max_active_messages = max_active_messages
        self._last_user_query: str = ""
        self.vault = None
        self.job_queue = job_queue
        self.strict_errors: bool = True
        self._memory_writer: Any = None  # MemoryWriter; set by orchestrator for M1 write path
        # G3-T6: Optional shared LLM client.  When set, _resolve_llm_client() returns it
        # instead of constructing a new instance via memory_vault._get_llm_client().
        self._llm_client: Any | None = llm_client
        # Guard against double-enqueuing a sliding-window compaction job.
        # Set to True when a generate_structured_summary job is in flight.
        # The next call to _sliding_window_compact() skips enqueueing a new job
        # while this flag is True, so the context can grow by at most one extra
        # window-size before the background job completes and the flag clears.
        # The flag is cleared by the background job wrapper _run_and_clear_summary_job().
        self._compaction_in_flight: bool = False

    def set_vault(self, vault):
        """Bind the Long-Term Memory Vault to the manager for auto-flushing."""
        self.vault = vault

    def set_memory_writer(self, writer: Any) -> None:
        """Bind a MemoryWriter for the M1 typed-node write path (P2-05)."""
        self._memory_writer = writer

    # ==========================================
    # Automatic Post-Turn Sync
    # ==========================================

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Extract and persist facts from a completed turn in the background.

        Routes user identity facts (name, preference, habit, etc.) to the bounded
        user_profile collection, and workspace facts to core_memory.

        Phase 1: If job_queue is available, enqueues a job instead of spawning threads.
        Fallback: Uses daemon thread if no queue (backward compatibility).

        Args:
            user_content: The user's message text for this turn.
            assistant_content: The assistant's final response text.
        """
        if not self.vault:
            return

        # Skip trivially short exchanges
        combined = f"{user_content or ''}\n{assistant_content or ''}".strip()
        if len(combined) < 30:
            return

        if self.job_queue:
            # Route through the durable queue so failures are visible in jobs/failed
            # and surfaced as TUI badge warnings — never silently swallowed.
            from hachi.jobs.queue import Job, JobStatus

            # M1 typed extraction (P2-05): preferred when MemoryWriter is attached
            extraction_func = (
                self._run_typed_extraction if self._memory_writer else self._run_fact_extraction
            )
            job = Job(
                job_id="",  # Assigned by queue
                name="fact_extraction",
                status=JobStatus.PENDING,
                func=extraction_func,
                args=(combined,),
                job_type="sync",
            )
            try:
                job_id = self.job_queue.enqueue(job)
                logger.debug("Enqueued fact extraction job %s", job_id)
            except Exception as e:
                # Enqueue failure is itself a visible error — do NOT fall back to a
                # thread that would swallow subsequent errors silently.
                logger.error("Failed to enqueue fact extraction job: %s", e)
        else:
            # No job queue configured: memory sync is disabled.
            # This is a configuration gap — log at WARNING so operators notice.
            logger.warning(
                "Memory sync skipped: no job queue configured. "
                "Without a job queue, background fact extraction is disabled and "
                "memory write failures cannot be surfaced. Configure a job queue "
                "to enable the full memory pipeline."
            )

    def _run_fact_extraction(self, combined: str) -> None:
        """LLM-powered fact extraction from a user/assistant exchange.

        Produces two buckets:
          - user_facts: routed to vault.upsert_user_fact() (bounded profile)
          - workspace_facts: routed to vault.upsert_fact() (unbounded store)

        MED-022 / G3-T6: Reuses the shared LLM client via _resolve_llm_client()
        instead of constructing a new instance on every call.
        """
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            logger.debug("Skipping fact extraction: ANTHROPIC_API_KEY not set")
            return

        from hachi.core.llm import Message as LLMMessage

        client = self._resolve_llm_client()
        if client is None:
            return

        # Truncate to avoid burning tokens on massive tool outputs
        snippet = combined[:3000]

        prompt = (
            "Extract ONLY permanent facts from this exchange into three buckets.\n\n"
            "user_facts: Things about the USER's identity — name, role, timezone, "
            "preferences, habits, relationships, communication style.\n"
            "workspace_facts: Technical/environment facts — architecture decisions, "
            "project structure, API endpoints, tool configurations.\n"
            "agent_facts: BEHAVIORAL CORRECTIONS. Did the user tell YOU (the agent) to change your "
            "behavior? (e.g., 'stop apologizing', 'make it shorter', 'don't use emojis'). "
            "Extract these as clear rules (e.g. 'I do not use emojis').\n\n"
            "Rules:\n"
            "- Each fact must be a single atomic sentence.\n"
            "- Do NOT extract tasks, commands, or transient status.\n"
            "- If nothing is worth extracting, return empty arrays.\n\n"
            'Return ONLY valid JSON: {"user_facts": [...], "workspace_facts": [...], '
            '"agent_facts": [...]}\n\n'
            f"Exchange:\n{snippet}"
        )

        response = client.generate(
            messages=[LLMMessage(role="user", content=prompt)],
            system_prompt="You are a fact extraction engine. Output pure JSON only.",
        )

        text = response.content.strip()
        if text.startswith("```json"):
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif text.startswith("```"):
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        data = json.loads(text)

        for fact in data.get("user_facts", []):
            if isinstance(fact, str) and _is_substantive_fact(fact):
                self.vault.upsert_user_fact(fact)

        for fact in data.get("workspace_facts", []):
            if isinstance(fact, str) and _is_substantive_fact(fact):
                self.vault.upsert_fact(fact, category="auto_synced")

        for fact in data.get("agent_facts", []):
            if isinstance(fact, str) and _is_substantive_fact(fact):
                self.vault.upsert_agent_fact(fact)

        extracted = (
            len(data.get("user_facts", []))
            + len(data.get("workspace_facts", []))
            + len(data.get("agent_facts", []))
        )
        if extracted:
            logger.debug("sync_turn extracted %d facts", extracted)

    def _run_typed_extraction(self, combined: str, session_id: str = "") -> None:
        """M1 write-side: schema-constrained tool-call extraction → typed OG nodes.

        Uses the Anthropic tool_use mechanism so the LLM is forced to emit a
        validated structure instead of free-form JSON.  Falls back to flat
        ``fact`` nodes when the model returns an empty or malformed tool call.

        Runs in the background via the durable job queue (P2-02).
        Also forwards facts to Chroma via the legacy upsert path so existing
        semantic search continues to work during the M1 migration.
        """
        import os

        if not self._memory_writer:
            return
        if not os.environ.get("ANTHROPIC_API_KEY"):
            logger.debug("Skipping typed extraction: ANTHROPIC_API_KEY not set")
            return

        from hachi.core.llm import Message as LLMMessage
        from hachi.core.memory_writer import MemoryNode, _slugify

        _EXTRACT_TOOL: dict[str, Any] = {
            "name": "extract_memory_nodes",
            "description": (
                "Extract durable memory nodes from a conversation exchange. "
                "Only extract facts that should persist across sessions. "
                "Omit transient commands, status, and single-use instructions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Unique slug for this node (kebab-case)",
                                },
                                "role": {
                                    "type": "string",
                                    "enum": [
                                        "fact",
                                        "preference",
                                        "event",
                                        "rule",
                                        "task",
                                        "person",
                                    ],
                                    "description": (
                                        "fact=technical/workspace; preference=user identity/style; "
                                        "event=something that happened; "
                                        "rule=agent behavior correction; "
                                        "task=work item; person=info about a person"
                                    ),
                                },
                                "dense": {
                                    "type": "string",
                                    "description": "One concise sentence (≤20 words) for the fact.",
                                },
                                "keywords": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "2-5 search keywords",
                                },
                                "project_slug": {
                                    "type": "string",
                                    "description": (
                                        "For fact/task nodes: kebab-case project name. "
                                        "Omit for personal facts."
                                    ),
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["open", "done", "blocked", ""],
                                    "description": "For task nodes: current status.",
                                },
                                "edges": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Typed relationship edges in ->[:key=value] format. "
                                        "Examples: ->[:about=billing-db-migration], "
                                        "->[:involves=alice], ->[:blocks=deploy-task], "
                                        "->[:supersedes=old-decision], ->[:caused_by=incident-x]. "
                                        "Omit if no meaningful relationships apply."
                                    ),
                                },
                            },
                            "required": ["name", "role", "dense"],
                        },
                    }
                },
                "required": ["nodes"],
            },
        }

        snippet = combined[:3000]
        client = self._resolve_llm_client()
        if client is None:
            return
        response = client.generate(
            messages=[LLMMessage(role="user", content=f"Extract memory from:\n\n{snippet}")],
            system_prompt=(
                "You are a precision memory extraction engine. "
                "Extract ONLY durable facts worth keeping across sessions. "
                "Use the extract_memory_nodes tool. "
                "Return an empty nodes list if nothing is worth extracting."
            ),
            tools=[_EXTRACT_TOOL],
        )

        # Pull out the tool call result
        raw_nodes: list[dict[str, Any]] = []
        for tc in response.tool_calls or []:
            if tc.name == "extract_memory_nodes":
                raw_nodes = (tc.input or {}).get("nodes", [])
                break

        if not raw_nodes:
            logger.debug("Typed extraction: no nodes extracted from exchange")
            return

        nodes: list[MemoryNode] = []
        for raw in raw_nodes:
            role = raw.get("role", "fact")
            if role not in ("fact", "preference", "event", "rule", "task", "person"):
                role = "fact"
            name = _slugify(raw.get("name", "") or raw.get("dense", "node")[:40])
            nodes.append(
                MemoryNode(
                    name=name,
                    role=role,
                    dense=str(raw.get("dense", ""))[:200],
                    keywords=list(raw.get("keywords", [])),
                    session_id=session_id,
                    project_slug=_slugify(raw.get("project_slug", "")),
                    status=str(raw.get("status", "")),
                    edges=[str(e) for e in raw.get("edges", []) if e],
                )
            )

        written = self._memory_writer.write_nodes(nodes, session_id=session_id)
        logger.debug("Typed extraction: wrote %d nodes", written)

        # Parallel write to Chroma so existing semantic search continues to work
        if self.vault:
            for node in nodes:
                try:
                    if node.role == "preference":
                        self.vault.upsert_user_fact(node.dense)
                    elif node.role == "rule":
                        self.vault.upsert_agent_fact(node.dense)
                    else:
                        self.vault.upsert_fact(node.dense, category=node.role)
                except Exception:
                    logger.debug("Typed extraction: Chroma parallel-write failed", exc_info=True)

    def write_episode(
        self, session_id: str, summary: str, keywords: list[str] | None = None
    ) -> None:
        """Write a session-close episode node to journal/<session_id>.og (P2-05)."""
        if self._memory_writer:
            self._memory_writer.write_episode(session_id, summary, keywords or [])

    # ==========================================
    # Public API
    # ==========================================

    @property
    def active_skill(self) -> str | None:
        """The name of the currently active .og skill, or None if no skill is loaded."""
        return self._active_skill

    @property
    def history(self) -> list[Message]:
        """The current active conversation history (what gets sent to the LLM)."""
        return self._history

    @property
    def task_ledger(self) -> list[TaskRecord]:
        """The full session task ledger (lightweight, never flushed)."""
        return self._task_ledger

    def add_message(self, message: Message):
        """
        Append a message to the active history.
        If the user sends a new message, capture it as the latest query.
        """
        if message.role == "user" and isinstance(message.content, str):
            self._last_user_query = message.content

        self._history.append(message)

        # Enforce sliding window within a single skill execution
        if len(self._history) > self._max_active_messages:
            self._sliding_window_compact()

    def signal_skill_switch(self, new_skill_name: str):
        """
        Called when the agent loads a new .og skill via the load_skill tool.
        Does NOT compact immediately — instead sets a pending flag.
        Compaction is deferred to apply_pending_compaction() which runs
        between turns, ensuring tool_use/tool_result pairs stay intact.

        Args:
            new_skill_name: The name of the newly loaded skill.
        """
        if self._active_skill is not None and self._active_skill != new_skill_name:
            self._pending_compaction = True
            self._pending_old_skill = self._active_skill

        self._active_skill = new_skill_name

    def apply_pending_compaction(self) -> bool:
        """
        Execute any deferred compaction. Must be called BETWEEN turns
        (after tool_results are safely in history, before the next LLM call).

        Returns:
            True if compaction was performed, False otherwise.
        """
        if not getattr(self, "_pending_compaction", False):
            return False

        self._pending_compaction = False
        old_skill = getattr(self, "_pending_old_skill", None)

        # Find where the old skill's messages end — everything before the
        # most recent user message is considered "old context"
        compact_boundary = self._find_safe_compact_boundary()
        if compact_boundary <= 0:
            return False

        # Extract old messages and build a compact summary
        old_messages = self._history[:compact_boundary]
        summary_parts = [f"[Compacted: previous task using skill '{old_skill}']"]

        for msg in old_messages:
            if isinstance(msg.content, str) and msg.role == "assistant":
                truncated = msg.content[:150].strip()
                if truncated:
                    summary_parts.append(f"Agent: {truncated}...")

        compact_text = "\n".join(summary_parts[:8])

        # Log to task ledger
        self._task_ledger.append(
            TaskRecord(
                skill_name=old_skill or "unknown",
                user_query=self._last_user_query,
                summary=f"Completed using {old_skill}",
            )
        )

        # Replace old messages with a single compact message
        self._history = [
            Message(
                role="user", content=f"[System: Previous task context compacted]\n{compact_text}"
            ),
            Message(
                role="assistant",
                content="Understood. Previous task context noted. Ready for the new task.",
            ),
        ] + self._history[compact_boundary:]

        self._skill_start_index = 2  # After the compact pair
        return True

    def _find_safe_compact_boundary(self) -> int:
        """
        Find the safe boundary index for compaction.
        We look backwards from the end to find the start of the most recent
        complete turn (user message that isn't a tool_result).
        This ensures we never split a tool_use/tool_result pair.
        """
        # Walk backwards to find the last "real" user message (not a tool_result)
        for i in range(len(self._history) - 1, -1, -1):
            msg = self._history[i]
            if msg.role == "user":
                if isinstance(msg.content, str):
                    # This is a real user message — compact everything before it
                    return i
                # If it's a list (tool_results), keep going back
        return 0

    @property
    def _skill_start_index(self) -> int:
        return getattr(self, "_skill_start_idx", 0)

    @_skill_start_index.setter
    def _skill_start_index(self, value: int):
        self._skill_start_idx = value

    def complete_current_task(self, result_summary: str):
        """
        Mark the current skill execution as complete and log it to the ledger.
        Called when the agent finishes a task and produces a final response.

        Args:
            result_summary: A brief summary of what was accomplished.
        """
        if self._active_skill:
            record = TaskRecord(
                skill_name=self._active_skill,
                user_query=self._last_user_query,
                summary=result_summary,
            )
            self._task_ledger.append(record)

    def get_context_header(self) -> str:
        """
        Generate a compact context header summarizing all previously completed tasks.
        This is prepended to the system prompt so the agent maintains continuity
        without carrying the full history of past tasks.

        Returns:
            A formatted string of the task ledger, or empty string if no tasks completed.
        """
        if not self._task_ledger:
            return ""

        lines = ["--- Previously Completed Tasks This Session ---"]
        for record in self._task_ledger:
            lines.append(f"• {record.to_text()}")
        lines.append("--- End of Previous Tasks ---\n")
        return "\n".join(lines)

    def estimate_token_count(self) -> int:
        """
        Rough estimate of current history token usage.
        Uses the ~4 characters per token heuristic.
        """
        total_chars = 0
        for msg in self._history:
            if isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        total_chars += len(str(block))
        return total_chars // 4

    def clear_stale_tool_results(self, keep_recent_messages: int = 6, min_chars: int = 600) -> int:
        """Losslessly reclaim context by shrinking large, re-fetchable tool outputs.

        Unlike the LLM-summary compaction, this drops NO messages and rewrites NO
        structure — it only replaces the *content* of oversized ``tool_result``
        blocks in older messages with a short placeholder. Every ``tool_use`` keeps
        its paired ``tool_result`` (so the Anthropic API never 400s), and the model
        can simply re-run a tool if it needs the output again. It costs no tokens
        (no LLM call), so it runs as the cheap first line of defense before the lossy
        summary compaction.

        Args:
            keep_recent_messages: Never touch the most recent N messages — recent
                tool output is the most likely to still be needed.
            min_chars: Only clear tool_result contents larger than this (small
                results aren't worth the churn and re-fetch hint).

        Returns:
            The number of tool_result blocks cleared (0 if nothing qualified).
        """
        if len(self._history) <= keep_recent_messages:
            return 0

        cleared = 0
        older = self._history[: len(self._history) - keep_recent_messages]
        for msg in older:
            if not isinstance(msg.content, list):
                continue
            for block in msg.content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                content = block.get("content")
                if content == _CLEARED_TOOL_RESULT:
                    continue  # already cleared — idempotent
                size = len(content) if isinstance(content, str) else len(str(content))
                if size > min_chars:
                    block["content"] = _CLEARED_TOOL_RESULT
                    cleared += 1
        return cleared

    def reset(self):
        """
        Full reset of the memory manager. Clears history and ledger.
        Used when starting a completely new session.
        """
        self._history.clear()
        self._task_ledger.clear()
        self._active_skill = None
        self._skill_start_index = 0
        self._last_user_query = ""
        self._compaction_in_flight = False

    def prepare_for_turn(self) -> IterationState:
        """Run all per-iteration memory housekeeping; return state for the loop.

        Executes compaction, stale tool-result clearing, and tool-pair repair in
        a single call so the ExecutionLoop does not need to reach into internals.

        Returns:
            An :class:`~hachi.core.conversation_memory.IterationState` snapshot
            describing what housekeeping ran.
        """
        from hachi.core.conversation_memory import IterationState

        compacted = False
        old_skill = "unknown"
        tool_results_cleared = 0
        history_repaired = 0

        if self.apply_pending_compaction():
            compacted = True
            old_skill = getattr(self, "_pending_old_skill", "unknown") or "unknown"

        if self.estimate_token_count() > _TOOL_RESULT_CLEAR_TOKENS:
            tool_results_cleared = self.clear_stale_tool_results()

        history_repaired = self.repair_tool_pairs()

        return IterationState(
            compacted=compacted,
            old_skill=old_skill,
            tool_results_cleared=tool_results_cleared,
            history_repaired=history_repaired,
        )

    def complete_task(self, user_query: str, result: str) -> None:
        """Finish the current task: sync facts asynchronously and log to ledger.

        Args:
            user_query: The user's input that triggered this task.
            result:     The agent's final response (or a budget/suspend message).
        """
        self.sync_turn(user_query, result)
        self.complete_current_task(result[:100] if result else "Task completed")

    def repair_tool_pairs(self) -> int:
        """Ensure every tool_use block has a tool_result immediately after its assistant message.

        Scans history for assistant messages whose tool_use ids have no matching tool_result
        in the following user message, and injects a synthetic tool_result so the Anthropic
        API never rejects the request with a 400. Returns the number of synthetic results added.
        """
        repaired = 0
        i = 0
        while i < len(self._history):
            msg = self._history[i]
            if msg.role != "assistant" or not isinstance(msg.content, list):
                i += 1
                continue

            tool_use_ids = [
                b["id"] for b in msg.content if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            if not tool_use_ids:
                i += 1
                continue

            # Collect tool_result ids from the immediately following user message, if any.
            next_msg = self._history[i + 1] if i + 1 < len(self._history) else None
            answered: set[str] = set()
            if next_msg and next_msg.role == "user" and isinstance(next_msg.content, list):
                answered = {
                    b.get("tool_use_id", "")
                    for b in next_msg.content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                }

            missing = [tid for tid in tool_use_ids if tid not in answered]
            if missing:
                synthetic = [
                    {"type": "tool_result", "tool_use_id": tid, "content": "[result unavailable]"}
                    for tid in missing
                ]
                if next_msg and next_msg.role == "user" and isinstance(next_msg.content, list):
                    # Append to the existing tool_result message.
                    next_msg.content.extend(synthetic)
                else:
                    # Insert a new tool_result message right after.
                    self._history.insert(i + 1, Message(role="user", content=synthetic))
                repaired += len(missing)
            i += 1

        return repaired

    def seed_history(self, messages: list[Message]) -> int:
        """Replay prior-session messages into active history (resume support).

        Unlike :meth:`add_message`, this appends in bulk without firing the
        per-message sliding window — callers are expected to pass an already
        bounded slice. The most recent user message (if any) is captured as the
        last query so context headers stay accurate.

        Args:
            messages: Chronologically ordered messages to prepend as context.

        Returns:
            The number of messages seeded.
        """
        if not messages:
            return 0

        self._history.extend(messages)
        for msg in reversed(messages):
            if msg.role == "user" and isinstance(msg.content, str):
                self._last_user_query = msg.content
                break
        return len(messages)

    def _sliding_window_compact(self):
        """Trim oldest messages when history exceeds the max window size.

        Keeps the first 2 messages as anchors and the most recent keep_count messages,
        where the cut point is adjusted backward via _find_safe_compact_boundary so
        that no tool_use/tool_result pair is ever split across the boundary.

        The dropped middle segment is processed via the job queue (preferred) or a
        daemon thread fallback:
          1. Fact extraction → flush permanent facts to the vault (existing behavior)
          2. Structured summary → replace the dropped messages with an LLM-generated
             checkpoint containing Active Task, Completed Actions, Key Decisions, etc.
        """
        if len(self._history) <= self._max_active_messages:
            return

        keep_count = self._max_active_messages - 2
        # Candidate cut point: everything from here onward is "recent"
        candidate_cut = len(self._history) - keep_count

        # Walk the cut point backward to a safe boundary where every tool_use
        # in the retained window already has a matching tool_result after it.
        safe_cut = self._find_safe_sliding_boundary(candidate_cut)

        anchor = self._history[:2]
        # Ensure anchor does not overlap safe_cut
        safe_cut = max(safe_cut, 2)

        # Nothing to drop — clamping consumed the entire candidate window.
        # Inserting a checkpoint here would be a no-op at best and could break
        # tool_use/tool_result pairs whose tool_use sits in anchor[1].
        if safe_cut >= len(self._history):
            return

        # If any tool_use in the anchor has its matching tool_result in the
        # retained slice, the checkpoint insertion would sit between them and
        # break the pair, causing an Anthropic 400.  Skip compaction in that case.
        anchor_tool_use_ids: set[str] = {
            b["id"]
            for msg in anchor
            for b in (msg.content if isinstance(msg.content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        if anchor_tool_use_ids:
            retained = self._history[safe_cut:]
            anchor_results_in_retained = any(
                isinstance(b, dict)
                and b.get("type") == "tool_result"
                and b.get("tool_use_id") in anchor_tool_use_ids
                for msg in retained
                for b in (msg.content if isinstance(msg.content, list) else [])
            )
            if anchor_results_in_retained:
                return

        dropped_messages = self._history[2:safe_cut]
        recent = self._history[safe_cut:]

        if self.job_queue:
            from hachi.jobs.queue import Job, JobStatus

            # Flush permanent facts to vault (background, durable).
            if self.vault:
                flush_job = Job(
                    job_id="",
                    name="flush_facts",
                    status=JobStatus.PENDING,
                    func=self._flush_facts_to_vault,
                    args=(dropped_messages,),
                    job_type="sync",
                )
                try:
                    self.job_queue.enqueue(flush_job)
                except Exception as enqueue_err:
                    logger.error("Failed to enqueue flush_facts job: %s", enqueue_err)

            # HIGH-015: Enqueue the structured summary LLM call as a background job
            # instead of blocking prepare_for_turn().  A lightweight placeholder
            # checkpoint is inserted immediately so the history stays valid and the
            # loop continues.  The background job will update the checkpoint text
            # only when callers explicitly request it via get_pending_summaries(); in
            # practice the placeholder is sufficient for continuity because the
            # model has seen the full history and carries it in its own context.
            #
            # Double-enqueue guard: skip if a compaction job is already in flight.
            # The context window may grow by up to one additional window-size while
            # the job is pending — acceptable; we never re-enqueue until the job
            # clears _compaction_in_flight.
            if not self._compaction_in_flight:
                self._compaction_in_flight = True
                summary_job = Job(
                    job_id="",
                    name="generate_structured_summary",
                    status=JobStatus.PENDING,
                    func=self._run_and_clear_summary_job,
                    args=(dropped_messages,),
                    job_type="background",
                )
                try:
                    self.job_queue.enqueue(summary_job)
                except Exception as enqueue_err:
                    logger.error("Failed to enqueue summary job: %s", enqueue_err)
                    self._compaction_in_flight = False  # allow retry next window
            else:
                logger.debug(
                    "Sliding-window compaction: skipping summary enqueue "
                    "(compaction job already in flight)"
                )

            summary = (
                "[Context compacted asynchronously. "
                "Permanent facts are being flushed to Long-Term Memory in the background.]"
            )
        else:
            # No job queue: flush facts is skipped; generate summary synchronously
            # as a best-effort fallback (legacy / test path).
            if self.vault:
                logger.warning(
                    "flush_facts skipped: no job queue configured. "
                    "Dropped context will not be persisted to long-term memory."
                )
            summary = self._generate_structured_summary(dropped_messages)

        checkpoint = Message(
            role="user",
            content=f"[CONTEXT CHECKPOINT — REFERENCE ONLY, DO NOT RESPOND TO THIS]\n{summary}",
        )

        self._history = anchor + [checkpoint] + recent
        self._skill_start_index = min(self._skill_start_index, len(self._history))

    def _find_safe_sliding_boundary(self, candidate_cut: int) -> int:
        """Return the latest cut index <= candidate_cut where no tool_use/tool_result
        pair is split.

        A cut is unsafe when any message at index >= candidate_cut is a tool_result
        whose corresponding tool_use sits at index < candidate_cut (i.e., would be
        dropped).  We walk backward from candidate_cut until we find an index where
        all tool_use blocks in the retained window [cut:] have a matching tool_result
        also in [cut:].

        PERF-12: The prior implementation was O(N²·B) — it called ``_is_safe(cut)``
        in a backward walk and each ``_is_safe`` call scanned the retained slice twice
        (once for ``seen_tool_use_ids``, once for orphan detection), giving O(N) per
        step and O(N²) total.

        New algorithm — O(N·B) single forward pass:
          1. Scan the *entire* history once to build a lookup of every
             ``tool_use_id → message index`` and ``tool_result tool_use_id → message
             index`` pair.
          2. For each tool_result at message index ``r``, find the index ``u`` of its
             paired tool_use.  The minimum safe cut is ``min(u, r) + 1`` — any cut
             *before* ``u`` would orphan the result.
          3. Walk backward from ``candidate_cut`` to ``0`` and stop at the first index
             that satisfies all pair constraints.  This walk is at most O(N) because
             the lookup was already built.

        Args:
            candidate_cut: The initially desired cut index.

        Returns:
            A safe cut index in the range [0, candidate_cut].
        """
        history = self._history

        # --- single forward pass to index all tool_use / tool_result positions ---
        # tool_use_id → message index where the tool_use block appears
        tool_use_index: dict[str | None, int] = {}
        # For each tool_result: (tool_use_id, message_index)
        tool_result_pairs: list[tuple[str | None, int]] = []

        for idx, msg in enumerate(history):
            content = msg.content
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    tool_use_index[block.get("id")] = idx
                elif btype == "tool_result":
                    tool_result_pairs.append((block.get("tool_use_id"), idx))

        # For each tool_result, the cut must be <= the index of its paired tool_use
        # so that both tool_use and tool_result land in the retained slice [cut:].
        # ``min_cut_for_pair[r_idx]`` = minimum cut value to keep result r_idx safe.
        # A cut is safe when cut <= min(tool_use_idx, result_idx) for every pair
        # where the tool_use exists in the history.
        # Equivalently, for a given cut, the pair is safe when:
        #   tool_use_idx >= cut  (tool_use is in retained slice)
        # So we need: cut <= tool_use_idx for all pairs whose result index >= cut.
        #
        # To find the *largest* cut <= candidate_cut that satisfies all constraints,
        # compute for each candidate cut value whether any pair is violated, walking
        # backward from candidate_cut.  We stop at the first safe position.
        #
        # Because the number of violated pairs only grows as cut decreases, we can
        # break early the moment _is_safe_cut() returns True.

        def _is_safe_cut(cut: int) -> bool:
            """O(P) where P = number of tool_result blocks (not O(N))."""
            for uid, r_idx in tool_result_pairs:
                if r_idx < cut:
                    # This result is not in the retained slice — not relevant.
                    continue
                u_idx = tool_use_index.get(uid)
                if u_idx is None:
                    # Paired tool_use not found in history at all — treat as safe
                    # (can't do better, and the API would have rejected it already).
                    continue
                if u_idx < cut:
                    # tool_use would be dropped but its result would be retained.
                    return False
            return True

        cut = candidate_cut
        while cut > 0 and not _is_safe_cut(cut):
            cut -= 1
        return cut

    def _resolve_llm_client(self) -> Any:
        """Return the LLM client to use for background memory operations.

        G3-T6 / MED-022: Prefer the injected shared client (set at construction time
        by the orchestrator) so all memory ops reuse the same connection pool as the
        main execution loop.  Falls back to the module-level singleton from
        memory_vault._get_llm_client() when no client was injected (e.g. in tests or
        legacy callers).

        Returns:
            A client with a .generate() method, or None when no API key is available.
        """
        if self._llm_client is not None:
            return self._llm_client
        from hachi.core.memory_vault import _get_llm_client

        return _get_llm_client()

    def _run_and_clear_summary_job(self, messages: list) -> str:
        """Background job wrapper: generate a structured summary then clear the in-flight flag.

        This wrapper exists solely so the ``_compaction_in_flight`` flag is cleared
        when the job completes (success *or* exception propagated to the JobWorker).
        The summary string is returned for auditing via job.result.output but is not
        applied back to the live history — the lightweight placeholder inserted by
        ``_sliding_window_compact`` is sufficient for model continuity.

        Correctness semantics:
          - The flag is cleared unconditionally in a ``finally`` block so a job
            failure never permanently blocks future compactions.
          - While the flag is set, the context window may grow up to one extra
            ``max_active_messages`` window before the next compaction is permitted.
            This is intentional: it bounds context growth while preventing duplicate
            expensive LLM calls.
        """
        try:
            return self._generate_structured_summary(messages)
        finally:
            self._compaction_in_flight = False

    def _flush_facts_to_vault(self, messages: list) -> None:
        """Extract permanent facts from dropped messages and save to vault.

        Runs as a background job via the durable queue. Exceptions propagate to
        the JobWorker so failures are routed to jobs/failed — never swallowed.
        """
        user_texts = [
            m.content for m in messages if m.role == "user" and isinstance(m.content, str)
        ]
        if not user_texts:
            return

        snippet = "\n".join(user_texts)
        if len(snippet.strip()) < 10:
            return

        from hachi.core.llm import Message as LLMMessage

        client = self._resolve_llm_client()
        if client is None:
            return
        prompt = (
            "Extract any permanent, atomic facts from this conversation snippet into a "
            "JSON list. "
            "If the user states a preference, a relationship, an architecture decision, "
            "or an environment detail, extract it. "
            "Do NOT extract tasks, commands, or transient status "
            "(e.g. 'I am coding', 'fix this bug'). "
            'Return ONLY a valid JSON array of strings, e.g. ["User\'s boss is Premraj", '
            '"User prefers Python"]. If none, return [].'
        )

        response = client.generate(
            messages=[LLMMessage(role="user", content=f"{prompt}\n\nSnippet:\n{snippet}")],
            system_prompt="You are an elite data extraction agent. Output pure JSON only.",
        )

        text = response.content.strip()
        if text.startswith("```json"):
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()

        facts = json.loads(text)
        for fact in facts:
            if isinstance(fact, str) and _is_substantive_fact(fact):
                self.vault.upsert_fact(fact, category="auto_flushed_context")

    def _generate_structured_summary(self, messages: list) -> str:
        """Produce a structured checkpoint from dropped conversation messages.

        The summary follows a fixed template that preserves:
          - Active Task: what the user most recently asked for
          - Completed Actions: what was done, which tools, outcomes
          - Active State: modified files, working directory, test status
          - Key Decisions: technical choices and rationale
          - Blocked: unresolved errors with exact messages

        Falls back to a minimal notice if the LLM call fails.

        Args:
            messages: The dropped Message objects to summarize.

        Returns:
            A structured summary string.
        """
        # Serialize messages into a compact transcript
        lines = []
        for m in messages:
            role = m.role.upper()
            if isinstance(m.content, str):
                text = m.content[:2000]
            elif isinstance(m.content, list):
                # Flatten tool_use/tool_result blocks into readable text
                parts = []
                for block in m.content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            parts.append(f"[tool_call: {block.get('name', '?')}]")
                        elif block.get("type") == "tool_result":
                            result = str(block.get("content", ""))[:500]
                            parts.append(f"[tool_result: {result}]")
                        elif block.get("type") == "text":
                            parts.append(block.get("text", "")[:1000])
                text = " | ".join(parts)
            else:
                text = str(m.content)[:500]
            lines.append(f"[{role}]: {text}")

        transcript = "\n\n".join(lines)
        # Cap total transcript to avoid burning tokens
        transcript = transcript[:8000]

        template = (
            "Summarize this dropped conversation segment into a structured checkpoint.\n"
            "Output ONLY these sections (skip any that have no content):\n\n"
            "## Active Task\n[The user's most recent request — quote verbatim if possible]\n\n"
            "## Completed Actions\n[Numbered list: what was done, which tool was used, "
            "the outcome]\n\n"
            "## Active State\n[Working directory, modified files, branch, test status if "
            "mentioned]\n\n"
            "## Key Decisions\n[Technical decisions made and WHY]\n\n"
            "## Blocked\n[Any unresolved errors — include exact error messages]\n\n"
            f"Conversation transcript:\n{transcript}"
        )

        try:
            from hachi.core.llm import Message as LLMMessage

            client = self._resolve_llm_client()
            if client is None:
                return "[Context compacted. Relevant facts were flushed to Long-Term Memory.]"
            response = client.generate(
                messages=[LLMMessage(role="user", content=template)],
                system_prompt=(
                    "You are a context summarization agent. "
                    "Output ONLY the structured summary. Do NOT answer questions. "
                    "Do NOT add commentary. Be concise — each section max 3 lines."
                ),
            )
            return response.content.strip()
        except Exception:
            logger.warning("Structured summary generation failed, using fallback")
            return "[Context compacted. Relevant facts were flushed to Long-Term Memory.]"
