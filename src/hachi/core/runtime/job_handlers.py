"""Durable job handlers for background work (Initiative D — JobRegistry activation).

Handlers are registered under stable names so jobs survive process restarts.
Each handler receives keyword arguments from the serialisable payload dict
(no live closures).

JobContext is a lightweight service locator rebuilt from the payload for each
handler invocation — avoiding closure capture of live runtime state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class JobContext:
    """Service locator for durable job handlers.

    Built from a serialisable ``{session_id, ...}`` payload so handlers can
    reconstruct all necessary services without live closure capture.
    """

    session_id: str | None = None
    # Lazily resolved services — populated by build_job_context
    vault: Any = None
    memory_manager: Any = None


def build_job_context(payload: dict[str, Any]) -> JobContext:
    """Reconstruct a JobContext from a serialisable payload dict.

    Imports are deferred to avoid circular dependencies at module load time.
    Services that are unavailable (e.g., vault not configured) are left as None
    and handlers are expected to guard against None.
    """
    session_id = payload.get("session_id")
    ctx = JobContext(session_id=session_id)

    # Lazily import vault to avoid circular imports
    try:
        from hachi.core.memory_vault import get_memory_vault

        ctx.vault = get_memory_vault()
    except Exception:
        logger.debug("build_job_context: memory vault not available")

    return ctx


# ---------------------------------------------------------------------------
# Handler: extract_fact
# ---------------------------------------------------------------------------


def handle_extract_fact(
    session_id: str = "",
    text: str = "",
    group: str = "core_memory",
    **_kwargs: Any,
) -> str:
    """Durable handler for background fact extraction into memory vault.

    Replaces the former func= closure in memory.py so facts survive restart.
    """
    if not text:
        logger.debug("handle_extract_fact: empty text — skipping")
        return "skipped: empty text"
    try:
        from hachi.core.memory_vault import get_memory_vault

        vault = get_memory_vault()
        vault.add(group, text)
        logger.debug("handle_extract_fact: stored fact in %r for session %r", group, session_id)
        return "ok"
    except Exception as exc:
        logger.error("handle_extract_fact: failed — %s", exc)
        raise


# ---------------------------------------------------------------------------
# Handler: generate_title
# ---------------------------------------------------------------------------


def handle_generate_title(
    session_id: str = "",
    prompt_text: str = "",
    **_kwargs: Any,
) -> str:
    """Durable handler for background session title generation.

    Replaces the former func= closure in orchestrator.py so title jobs survive restart.
    """
    if not session_id or not prompt_text:
        logger.debug("handle_generate_title: missing session_id or prompt_text — skipping")
        return "skipped"
    try:
        from hachi.core.session_store import SessionStore
        from hachi.core.session_titler import generate_title

        title = generate_title(prompt_text)
        if title:
            store = SessionStore()
            store.set_session_title(session_id, title)
            logger.debug("handle_generate_title: set title %r for session %r", title, session_id)
        return title or "no title generated"
    except Exception as exc:
        logger.error("handle_generate_title: failed — %s", exc)
        raise


# ---------------------------------------------------------------------------
# Handler: auto_extract (reactive file pipeline seam — A8)
# ---------------------------------------------------------------------------


def handle_auto_extract(
    event_name: str = "",
    event_payload: dict[str, Any] | None = None,
    event_timestamp: str = "",
    event_sequence: int = 0,
    **_kwargs: Any,
) -> str:
    """Reactive stub handler for file.uploaded events (A8 — seam for slice 2.4).

    When the file platform is built (slice 2.4), this handler will trigger
    the extract→index pipeline.  For now it logs and returns so the reactive
    seam is testable end-to-end.
    """
    file_id = (event_payload or {}).get("file_id", "unknown")
    logger.info(
        "handle_auto_extract: received %r (file_id=%r seq=%d) — pipeline not yet built (slice 2.4 seam)",  # noqa: E501
        event_name,
        file_id,
        event_sequence,
    )
    return f"seam:auto_extract:{file_id}"


# ---------------------------------------------------------------------------
# Handler: resume_run (wake a WAITING run)
# ---------------------------------------------------------------------------


def handle_resume_run(
    run_id: str = "",
    result: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> str:
    """Durable handler to resume a WAITING run.

    Dispatched by WaitConditionRegistry.resolve() → job queue so the wake is
    durable and survives a restart between wake scheduling and execution.
    """
    if not run_id:
        logger.warning("handle_resume_run: missing run_id")
        return "error: missing run_id"
    result = result or {}
    try:
        from hachi.core.runtime.resume import resume_run

        resume_run(run_id, result)
        return f"resumed:{run_id}"
    except Exception as exc:
        logger.error("handle_resume_run: failed to resume %r — %s", run_id, exc)
        raise
