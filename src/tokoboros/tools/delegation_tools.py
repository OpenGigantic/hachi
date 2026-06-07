"""
Delegation Tools — Sub-Agent Spawning Interface for the Orchestrator

This module exposes the Sub-Agent Runtime to the LLM as a callable tool.
It is the bridge between the orchestrator's intent ("I need a coder to handle this")
and the isolated execution environment that actually runs the sub-agent.

Architecture:
    Orchestrator LLM
        │
        ▼ calls delegate_task(role="coder", task="...")
    delegation_tools.py
        │ 1. Resolves role → .og file
        │ 2. Builds scoped ToolRegistry (no delegate_task, no memory tools)
        │ 3. Spawns SubAgentRuntime
        ▼ 4. Returns SubAgentReport → orchestrator context
    Sub-Agent (isolated)

Security:
    - Sub-agents do NOT receive the delegate_task tool (no recursive spawning)
    - Sub-agents do NOT receive memory tools (no long-term memory writes)
    - Sub-agents inherit the same PermissionGate as the orchestrator
    - All tool execution is logged via on_event callbacks
"""

import json
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Any

from tokoboros.core.loop import ToolRegistry
from tokoboros.core.permissions import PermissionGate
from tokoboros.core.sub_agent import (
    RoleResolver,
    SubAgentReport,
    SubAgentRuntime,
    SubAgentStatus,
)
from tokoboros.jobs import Job, JobStatus

logger = logging.getLogger(__name__)


# ==========================================
# Tool Schemas (Anthropic Format)
# ==========================================

DELEGATE_TASK_SCHEMA = {
    "name": "delegate_task",
    "description": (
        "Spawn a specialized sub-agent to handle a complex task in isolation. "
        "The sub-agent runs with its own context window, executes the task using "
        "the tools available to it, and returns a short report. "
        "Your context stays clean — you only see the final summary.\n\n"
        "WHEN TO USE:\n"
        "- The task requires 3+ tool calls across multiple domains\n"
        "- The task involves trial-and-error that would pollute your context\n"
        "- The task requires specialized execution (coding, research, email)\n\n"
        "WHEN NOT TO USE:\n"
        "- Simple 1-2 step tasks — just do them directly\n"
        "- Purely conversational questions — answer them yourself\n"
        "- Tasks requiring real-time user interaction"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "description": (
                    "The sub-agent role to use. Maps to a .og file in the roles directory. "
                    "Available roles: 'coder' (coding/engineering), 'researcher' (web research), "
                    "'emailer' (email composition/sending), 'analyst' (data analysis), "
                    "'devops' (system administration)."
                ),
            },
            "task": {
                "type": "string",
                "description": (
                    "A detailed, self-contained description of what the sub-agent should "
                    "accomplish. Be specific — the sub-agent has NO access to your conversation "
                    "history. "
                    "Include file paths, URLs, expected output format, and any constraints."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional additional context for the sub-agent. Use this to pass "
                    "file contents, prior research results, user preferences, or any "
                    "information the sub-agent needs but can't discover on its own."
                ),
            },
        },
        "required": ["role", "task"],
    },
}

LIST_ROLES_SCHEMA = {
    "name": "list_roles",
    "description": (
        "List all available sub-agent roles that can be used with delegate_task. "
        "Each role maps to a .og blueprint file that defines the sub-agent's "
        "persona, constraints, and capabilities."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


# ==========================================
# Blocked Tools for Sub-Agents
# ==========================================

# These tools are NEVER available to sub-agents.
# This is the security boundary that prevents:
# - Recursive spawning (delegate_task, list_roles)
# - Uncontrolled memory writes (core_save_memory, core_save_user_profile)
# - Memory reads that could leak cross-session data
# - Workflow manipulation (suspend_workflow)
# - Session state corruption (core_search_sessions)
BLOCKED_TOOL_NAMES = frozenset(
    {
        # Delegation (no recursive spawning)
        "delegate_task",
        "delegate_parallel_tasks",
        "list_roles",
        # Core memory (no long-term memory writes from sub-agents)
        "core_save_memory",
        "core_search_memory",
        "core_delete_memory",
        # User profile (sub-agents must never alter user identity)
        "core_save_user_profile",
        "core_search_user_profile",
        "core_delete_user_profile",
        # Agent profile (sub-agents must never alter agent personality)
        "core_save_agent_profile",
        "core_delete_agent_profile",
        # Session persistence (sub-agents don't have sessions)
        "core_search_sessions",
        # Workflow control (registered name is core_suspend_workflow)
        "core_suspend_workflow",
        # Skill authoring + scheduling are privileged main-agent actions
        "save_skill",
        "schedule_task",
        "list_schedules",
        "cancel_schedule",
    }
)


# ==========================================
# Scoped Tool Registry Builder
# ==========================================

# Core tools that are always available to every sub-agent regardless of role
# (these are the minimum required for any useful work)
_CORE_TOOLS = frozenset(
    {
        "run_terminal_command",
        "read_file",
        "write_file",
        "search_skills",
        "peek_skill",
        "load_skill",
    }
)


def _build_scoped_registry(
    parent_registry: ToolRegistry,
    gate: PermissionGate | None = None,
    tool_patterns: str = "",
) -> ToolRegistry:
    """Build a restricted ToolRegistry for a sub-agent.

    Two-stage filtering:
        1. BLOCK: Remove tools in BLOCKED_TOOL_NAMES (security boundary)
        2. ALLOW: If tool_patterns is set, only include tools matching
           the declared patterns (role-based scoping)

    tool_patterns is a pipe-separated string from the role .og file's
    ``::meta tools:`` field. Each pattern is either an exact name or a
    glob prefix (e.g., ``google_*``).

    If tool_patterns is empty, all non-blocked tools pass through
    (backward compatibility).

    Args:
        parent_registry: The orchestrator's full ToolRegistry.
        gate:            Optional PermissionGate (same as orchestrator's).
        tool_patterns:   Pipe-separated tool name patterns (from role .og meta.tools).

    Returns:
        A new ToolRegistry with security-blocked and role-filtered tools removed.
    """
    scoped = ToolRegistry()

    # Parse tool patterns into prefix list
    allowed_prefixes = []
    allowed_exact = set()
    if tool_patterns:
        for pattern in tool_patterns.split("|"):
            pattern = pattern.strip()
            if not pattern:
                continue
            if pattern.endswith("*"):
                allowed_prefixes.append(pattern[:-1])  # "google_*" → "google_"
            else:
                allowed_exact.add(pattern)

    has_filter = bool(allowed_prefixes or allowed_exact)

    for schema in parent_registry.get_schemas():
        tool_name = schema.get("name", "")

        # Stage 1: Security block
        if tool_name in BLOCKED_TOOL_NAMES:
            logger.debug("Blocked tool '%s' from sub-agent registry", tool_name)
            continue

        # Stage 2: Role-based filtering (skip if no patterns declared)
        if has_filter:
            # Core tools always pass through
            if tool_name not in _CORE_TOOLS:
                # Check if tool matches any declared pattern
                matched = tool_name in allowed_exact or any(
                    tool_name.startswith(prefix) for prefix in allowed_prefixes
                )
                if not matched:
                    logger.debug("Role-filtered tool '%s' (not in tool_patterns)", tool_name)
                    continue

        # Copy the tool's handler from the parent registry
        handler = parent_registry._tools.get(tool_name)
        if handler:
            scoped.register(schema, handler)

    return scoped


# ==========================================
# Tool Handlers
# ==========================================


def _make_delegate_task(
    parent_registry: ToolRegistry,
    gate: PermissionGate | None = None,
    on_event: Callable[..., None] | None = None,
    vault=None,
    job_queue=None,
    sub_agent_llm=None,
    cost_tracker=None,
) -> Callable[..., str]:
    """Factory: creates the delegate_task tool handler.

    The handler is a closure that captures the parent_registry, gate, and vault,
    ensuring each sub-agent gets a properly scoped copy of the tools and
    every delegation result is persisted to long-term memory.

    Args:
        parent_registry: The orchestrator's full ToolRegistry.
        gate:            The device permission gate.
        on_event:        Optional progress callback for the CLI/UI.
        vault:           Optional MemoryVault for persisting delegation results.
        job_queue:       Optional job queue for persistence work.

    Returns:
        A callable that accepts (role, task, context) and returns a report string.
    """

    def delegate_task(role: str, task: str, context: str = "") -> str:
        """Spawn an isolated sub-agent and return its execution report.

        This function:
            1. Builds a scoped ToolRegistry (no delegate_task, no memory tools)
            2. Creates a SubAgentRuntime with the role's .og file
            3. Runs the sub-agent to completion (fire-and-forget)
            4. Enqueues persistence work for the delegation log
            5. Returns a compact report for the orchestrator

        Args:
            role:    Sub-agent role name (e.g., 'coder', 'researcher').
            task:    Self-contained task description.
            context: Optional additional context string.

        Returns:
            A structured report string for the orchestrator's context.
        """
        # Emit progress event for CLI display
        if on_event:
            on_event(
                "delegation_start",
                {
                    "role": role,
                    "task": task[:100],
                },
            )

        try:
            # Resolve role .og file to extract tool scope patterns
            tool_patterns = ""
            try:
                resolver = RoleResolver()
                role_path = resolver.resolve(role)
                from tokoboros.og_engine.parser import parse_og_file

                role_og = parse_og_file(str(role_path))
                tool_patterns = role_og.meta.tools
            except Exception:
                pass  # Fall back to unfiltered tools if .og parsing fails

            # Build the scoped tool registry (filtered by role + security)
            scoped_registry = _build_scoped_registry(parent_registry, gate, tool_patterns)

            # Create and run the sub-agent. Pass the credential-resolved LLM client
            # down so the sub-agent uses the SAME working provider/key as the main
            # agent (otherwise it would build a bare client that only reads the env
            # var and fails with "ANTHROPIC_API_KEY must be provided").
            runtime = SubAgentRuntime(
                role=role,
                task=task,
                context=context or None,
                tool_registry=scoped_registry,
                llm_client=sub_agent_llm,
                on_event=on_event,
                cost_tracker=cost_tracker,
            )
            report = runtime.run()

            # Emit completion event
            if on_event:
                on_event(
                    "delegation_complete",
                    {
                        "role": role,
                        "status": report.status.value,
                        "duration": report.duration_seconds,
                        "iterations": report.iterations,
                    },
                )

            _enqueue_delegation_persistence(job_queue, vault, report)

            # Return the compact orchestrator string (not raw JSON)
            return report.to_orchestrator_string()

        except FileNotFoundError as exc:
            # Role .og file not found — provide helpful error
            logger.warning("delegate_task failed: %s", exc)
            error_report = SubAgentReport(
                status=SubAgentStatus.ERROR,
                summary=str(exc),
                role=role,
                task=task,
            )
            _enqueue_delegation_persistence(job_queue, vault, error_report)
            return error_report.to_orchestrator_string()

        except Exception as exc:
            # Unexpected error — still return a structured report
            logger.error("delegate_task failed unexpectedly: %s", exc, exc_info=True)
            error_report = SubAgentReport(
                status=SubAgentStatus.ERROR,
                summary=f"Delegation failed: {exc}",
                role=role,
                task=task,
            )
            _enqueue_delegation_persistence(job_queue, vault, error_report)
            return error_report.to_orchestrator_string()

    return delegate_task


_PARALLEL_MAX_WORKERS = 3  # matches Hermes' "3 parallel sub-agents"
_PARALLEL_DEFAULT_TIMEOUT = 300  # seconds per sub-agent


def _make_delegate_parallel_tasks(
    parent_registry: ToolRegistry,
    gate: PermissionGate | None = None,
    on_event: Callable[..., None] | None = None,
    vault: Any = None,
    job_queue: Any = None,
    sub_agent_llm: Any = None,
    cost_tracker: Any = None,
) -> Callable[..., str]:
    """Factory: creates the delegate_parallel_tasks tool handler.

    Each task in the list is dispatched to a separate sub-agent thread,
    up to :data:`_PARALLEL_MAX_WORKERS` concurrently.  All results are
    collected before returning.

    Args:
        parent_registry: The orchestrator's full ToolRegistry.
        gate:            The device permission gate.
        on_event:        Optional progress callback.
        vault:           Optional MemoryVault for delegation persistence.
        job_queue:       Optional job queue.
        sub_agent_llm:   Credential-resolved LLM client for sub-agents.
        cost_tracker:    Shared cost tracker (thread-safe).

    Returns:
        A callable that accepts ``tasks`` and returns a JSON result string.
    """
    # Build a single-task dispatcher we can call from threads.
    _single = _make_delegate_task(
        parent_registry, gate, on_event, vault, job_queue, sub_agent_llm, cost_tracker
    )

    def delegate_parallel_tasks(
        tasks: list[dict[str, str]],
        timeout_sec: int = _PARALLEL_DEFAULT_TIMEOUT,
    ) -> str:
        """Run up to 3 sub-agent tasks in parallel and return all results.

        Each item in ``tasks`` must have ``role`` and ``task`` keys and may have
        an optional ``context`` key.  Sub-agents run concurrently (max 3 at a
        time).  Slower tasks do not block faster ones from completing.

        Args:
            tasks: List of ``{"role": "...", "task": "...", "context": "..."}``
                   dicts — at most 10 items.
            timeout_sec: Per-task timeout in seconds (default 300).

        Returns:
            JSON string with ``results`` list (one entry per task), each with
            ``role``, ``task``, ``status`` ("ok" | "timeout" | "error"),
            ``output``, and ``index``.
        """
        if not tasks:
            return json.dumps({"results": [], "message": "No tasks provided."})

        items = tasks[:10]  # cap at 10 to avoid runaway spawning

        if on_event:
            on_event(
                "delegation_parallel_start",
                {"count": len(items), "roles": [t.get("role", "?") for t in items]},
            )

        results: list[dict[str, Any]] = [{}] * len(items)
        futures: list[Future[str]] = []

        with ThreadPoolExecutor(
            max_workers=min(_PARALLEL_MAX_WORKERS, len(items)),
            thread_name_prefix="toko-subagent",
        ) as pool:
            for idx, task_spec in enumerate(items):
                role = str(task_spec.get("role", "researcher"))
                task = str(task_spec.get("task", ""))
                context = str(task_spec.get("context", ""))
                fut: Future[str] = pool.submit(_single, role, task, context)
                # Attach metadata to retrieve later
                fut._toko_idx = idx  # type: ignore[attr-defined]
                fut._toko_role = role  # type: ignore[attr-defined]
                fut._toko_task = task  # type: ignore[attr-defined]
                futures.append(fut)

            done, timed_out = futures_wait(futures, timeout=timeout_sec)

        for fut in futures:
            idx = fut._toko_idx  # type: ignore[attr-defined]
            role = fut._toko_role  # type: ignore[attr-defined]
            task = fut._toko_task  # type: ignore[attr-defined]
            if fut in done:
                exc = fut.exception()
                if exc is not None:
                    results[idx] = {
                        "index": idx,
                        "role": role,
                        "task": task[:100],
                        "status": "error",
                        "output": f"Sub-agent error: {exc}",
                    }
                else:
                    results[idx] = {
                        "index": idx,
                        "role": role,
                        "task": task[:100],
                        "status": "ok",
                        "output": fut.result(),
                    }
            else:
                # Timed out — cancel the future (best-effort)
                fut.cancel()
                results[idx] = {
                    "index": idx,
                    "role": role,
                    "task": task[:100],
                    "status": "timeout",
                    "output": (
                        f"Sub-agent did not complete within {timeout_sec}s. "
                        "Consider breaking the task into smaller steps."
                    ),
                }

        ok_count = sum(1 for r in results if r.get("status") == "ok")
        if on_event:
            on_event(
                "delegation_parallel_complete",
                {"total": len(items), "ok": ok_count},
            )

        return json.dumps(
            {
                "results": results,
                "summary": f"{ok_count}/{len(items)} tasks completed successfully.",
            },
            indent=2,
        )

    return delegate_parallel_tasks


DELEGATE_PARALLEL_TASKS_SCHEMA: dict[str, Any] = {
    "name": "delegate_parallel_tasks",
    "description": (
        "Dispatch up to 3 sub-agent tasks in parallel and wait for all results. "
        "Use this when you have multiple INDEPENDENT work items that do not depend on "
        "each other — e.g. researching 3 different APIs concurrently, running "
        "separate code audits in parallel, or generating multiple content drafts at once. "
        "Do NOT use for tasks that must run in sequence. "
        "Each task runs in full isolation with its own tool scope and context. "
        "Returns a JSON object with `results` (one per task) and a `summary`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of tasks to run in parallel (max 10).",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "description": (
                                "Sub-agent role (e.g. 'researcher', 'coder'). "
                                "Use list_roles to see available roles."
                            ),
                        },
                        "task": {
                            "type": "string",
                            "description": "Self-contained task description for this sub-agent.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional background context string.",
                        },
                    },
                    "required": ["role", "task"],
                },
                "maxItems": 10,
            },
            "timeout_sec": {
                "type": "integer",
                "description": (
                    "Maximum seconds to wait per sub-agent (default 300). "
                    "Timed-out tasks are marked with status='timeout'."
                ),
                "default": 300,
            },
        },
        "required": ["tasks"],
    },
}


def _enqueue_delegation_persistence(job_queue, vault, report: SubAgentReport) -> None:
    """Persist a delegation result through the job queue when available.

    When no queue is configured, the persistence work runs inline so tests and
    development workflows still retain delegation history.
    """
    if not vault:
        return

    if job_queue:
        job = Job(
            job_id="",
            name="persist_delegation",
            status=JobStatus.PENDING,
            func=_persist_delegation_result,
            args=(vault, report),
            job_type="persist",
        )

        try:
            job_queue.enqueue(job)
            return
        except Exception:
            logger.debug("Failed to enqueue delegation persistence job", exc_info=True)

    _persist_delegation_result(vault, report)


def _persist_delegation_result(vault, report: SubAgentReport) -> str:
    """Persist a delegation result to long-term memory.

    Two writes happen:
        1. delegation_log: The full delegation record (searchable by role, task, status)
        2. core_memory: Key workspace facts extracted from the sub-agent's summary
           (e.g., files created, commands run, configurations applied)

    This function is queue-friendly and returns the delegation log ID.

    Args:
        vault:  The MemoryVault instance (may be None if vault is not configured).
        report: The SubAgentReport to persist.
    """
    if not vault:
        return ""

    try:
        entry_id = vault.log_delegation(
            role=report.role,
            task=report.task,
            status=report.status.value,
            summary=report.summary,
            duration_seconds=report.duration_seconds,
            iterations=report.iterations,
            token_estimate=report.token_estimate,
        )

        if report.status == SubAgentStatus.SUCCESS and len(report.summary) > 30:
            vault.upsert_fact(
                f"[delegation:{report.role}] {report.summary[:500]}",
                category="delegation_result",
            )

        return entry_id
    except Exception:
        logger.debug("Failed to persist delegation result", exc_info=True)
        return ""


def _make_list_roles() -> Callable[..., str]:
    """Factory: creates the list_roles tool handler.

    Returns:
        A callable that returns a JSON list of available role names.
    """

    def list_roles() -> str:
        """List all available sub-agent roles.

        Returns:
            A JSON string with available role names and their source paths.
        """
        try:
            resolver = RoleResolver()
            roles = resolver.list_available_roles()
            if not roles:
                return json.dumps(
                    {
                        "roles": [],
                        "message": (
                            "No role .og files found. Create one at "
                            "~/.tokoboros/skills/roles/<name>.og"
                        ),
                    }
                )
            return json.dumps({"roles": roles})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    return list_roles


# ==========================================
# Registration Helper
# ==========================================


def register_delegation_tools(
    registry: ToolRegistry,
    gate: PermissionGate | None = None,
    on_event: Callable[..., None] | None = None,
    vault=None,
    job_queue=None,
    sub_agent_llm=None,
    cost_tracker=None,
) -> None:
    """Mount delegation tools onto the provided ToolRegistry.

    This must be called AFTER all other tools are registered, because
    delegate_task needs access to the full parent registry to build
    scoped copies for sub-agents.

    Args:
        registry:  The orchestrator's tool registry (will be the parent for sub-agents).
        gate:      The device PermissionGate instance.
        on_event:  Optional callback for delegation progress events.
        vault:     Optional MemoryVault for persisting delegation results.
        job_queue: Optional job queue for delegation persistence.
    """
    registry.register(
        DELEGATE_TASK_SCHEMA,
        _make_delegate_task(
            registry, gate, on_event, vault, job_queue, sub_agent_llm, cost_tracker
        ),
    )
    registry.register(
        DELEGATE_PARALLEL_TASKS_SCHEMA,
        _make_delegate_parallel_tasks(
            registry, gate, on_event, vault, job_queue, sub_agent_llm, cost_tracker
        ),
    )
    registry.register(
        LIST_ROLES_SCHEMA,
        _make_list_roles(),
    )
