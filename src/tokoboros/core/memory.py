"""
Session Memory Manager

This module handles intelligent conversation history management for Tokoboros.
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
"""

import json
import logging
import threading
from typing import Any

from tokoboros.core.llm import Message

logger = logging.getLogger(__name__)


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
    Production-grade session memory for the Tokoboros agent.

    Responsibilities:
        - Maintain the active conversation history (messages sent to the LLM).
        - Track which skill is currently active.
        - Detect skill switches and trigger automatic compaction.
        - Maintain a persistent task ledger across the entire session.
        - Enforce a sliding window to cap raw message count.
        - Enqueue background fact-extraction jobs to the durable queue.
    """

    def __init__(self, max_active_messages: int = 30, job_queue: Any | None = None):
        """
        Initialize the memory manager.

        Args:
            max_active_messages: Maximum number of raw messages before triggering
                                 sliding-window compaction. Prevents unbounded growth
                                 even within a single skill execution.
            job_queue: Optional durable JobQueue for background work.
                       If provided, sync_turn() enqueues jobs instead of spawning threads.
                       Falls back to thread-based sync if not provided (backward compat).
        """
        self._history: list[Message] = []
        self._task_ledger: list[TaskRecord] = []
        self._active_skill: str | None = None
        self._skill_start_index: int = 0  # Index in _history where current skill began
        self._max_active_messages = max_active_messages
        self._last_user_query: str = ""
        self.vault = None
        self.job_queue = job_queue
        self._sync_lock = threading.Lock()  # For thread-based fallback

    def set_vault(self, vault):
        """Bind the Long-Term Memory Vault to the manager for auto-flushing."""
        self.vault = vault

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
            # Phase 1: Use durable queue
            from tokoboros.jobs.queue import Job, JobStatus

            job = Job(
                job_id="",  # Will be assigned by queue
                name="fact_extraction",
                status=JobStatus.PENDING,
                func=self._run_fact_extraction,
                args=(combined,),
                job_type="sync",
            )
            try:
                job_id = self.job_queue.enqueue(job)
                logger.debug(f"Enqueued fact extraction job {job_id}")
            except Exception as e:
                logger.warning(
                    f"Failed to enqueue fact extraction job: {e}. Falling back to thread."
                )
                self._spawn_sync_thread(combined)
        else:
            # Fallback: spawn daemon thread
            self._spawn_sync_thread(combined)

    def _spawn_sync_thread(self, combined: str) -> None:
        """Spawn a background thread for fact extraction (fallback mechanism)."""
        logger.warning("Memory sync falling back to daemon thread -- no job queue configured")

        def _extract():
            if not self._sync_lock.acquire(blocking=False):
                return  # Another sync is already running, skip this one
            try:
                self._run_fact_extraction(combined)
            except Exception:
                pass  # Never interrupt the agent
            finally:
                self._sync_lock.release()

        threading.Thread(target=_extract, name="sync-turn", daemon=True).start()

    def _run_fact_extraction(self, combined: str) -> None:
        """LLM-powered fact extraction from a user/assistant exchange.

        Produces two buckets:
          - user_facts: routed to vault.upsert_user_fact() (bounded profile)
          - workspace_facts: routed to vault.upsert_fact() (unbounded store)
        """
        from tokoboros.core.llm import AnthropicClient
        from tokoboros.core.llm import Message as LLMMessage

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

        client = AnthropicClient()  # Uses cheap haiku model
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
            if isinstance(fact, str) and len(fact) > 5:
                self.vault.upsert_user_fact(fact)

        for fact in data.get("workspace_facts", []):
            if isinstance(fact, str) and len(fact) > 5:
                self.vault.upsert_fact(fact, category="auto_synced")

        for fact in data.get("agent_facts", []):
            if isinstance(fact, str) and len(fact) > 5:
                self.vault.upsert_agent_fact(fact)

        extracted = (
            len(data.get("user_facts", []))
            + len(data.get("workspace_facts", []))
            + len(data.get("agent_facts", []))
        )
        if extracted:
            logger.debug("sync_turn extracted %d facts", extracted)

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
        dropped_messages = self._history[2:safe_cut]
        recent = self._history[safe_cut:]

        # Flush permanent facts to vault via queue if available, else daemon thread
        if self.vault:
            if self.job_queue:
                from tokoboros.jobs.queue import Job, JobStatus

                job = Job(
                    job_id="",
                    name="flush_facts",
                    status=JobStatus.PENDING,
                    func=self._flush_facts_to_vault,
                    args=(dropped_messages,),
                    job_type="sync",
                )

                try:
                    self.job_queue.enqueue(job)
                except Exception as enqueue_err:
                    logger.warning(
                        "Failed to enqueue flush_facts job: %s. "
                        "Memory sync falling back to daemon thread -- no job queue configured",
                        enqueue_err,
                    )
                    threading.Thread(
                        target=self._flush_facts_to_vault,
                        args=(dropped_messages,),
                        name="flush-facts",
                        daemon=True,
                    ).start()
            else:
                logger.warning(
                    "Memory sync falling back to daemon thread -- no job queue configured"
                )
                threading.Thread(
                    target=self._flush_facts_to_vault,
                    args=(dropped_messages,),
                    name="flush-facts",
                    daemon=True,
                ).start()

        # Generate structured summary (synchronous — we need the result now)
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

        Args:
            candidate_cut: The initially desired cut index.

        Returns:
            A safe cut index in the range [0, candidate_cut].
        """
        history = self._history

        def _is_safe(cut: int) -> bool:
            """True when the retained slice history[cut:] has no orphaned tool_result."""
            open_tool_uses: set = set()
            for msg in history[cut:]:
                content = msg.content
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        open_tool_uses.add(block.get("id"))
                    elif block.get("type") == "tool_result":
                        open_tool_uses.discard(block.get("tool_use_id"))
            # Safe when every tool_result in [cut:] has its tool_use also in [cut:]
            # AND there are no outstanding open tool_use blocks without a result.
            # We detect unsafety by checking whether any tool_result at [cut:]
            # references a tool_use_id not present in [cut:].
            seen_tool_use_ids: set = set()
            for msg in history[cut:]:
                content = msg.content
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        seen_tool_use_ids.add(block.get("id"))
            for msg in history[cut:]:
                content = msg.content
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        if block.get("tool_use_id") not in seen_tool_use_ids:
                            return False
            return True

        cut = candidate_cut
        while cut > 0 and not _is_safe(cut):
            cut -= 1
        return cut

    def _flush_facts_to_vault(self, messages: list) -> None:
        """Extract permanent facts from dropped messages and save to vault.

        Runs in a background thread. Errors are silently swallowed to never
        interrupt the main agent loop.
        """
        try:
            user_texts = [
                m.content for m in messages if m.role == "user" and isinstance(m.content, str)
            ]
            if not user_texts:
                return

            snippet = "\n".join(user_texts)
            if len(snippet.strip()) < 10:
                return

            from tokoboros.core.llm import AnthropicClient
            from tokoboros.core.llm import Message as LLMMessage

            client = AnthropicClient()
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
                if isinstance(fact, str) and len(fact) > 5:
                    self.vault.upsert_fact(fact, category="auto_flushed_context")
        except Exception:
            pass

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
            from tokoboros.core.llm import AnthropicClient
            from tokoboros.core.llm import Message as LLMMessage

            client = AnthropicClient()
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
