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

    NOTE: This is a **seam handler** for the durable job path.  The real
    ``MemoryManager._run_fact_extraction`` and ``_run_typed_extraction`` closures
    require a live LLM client and vault that are not serializable into the job
    payload.  Converting those closures faithfully to a durable handler requires
    injecting the LLM client via ``JobContext`` (design amendment A3) — that work
    is deferred to slice 2.3 when ``JobContext`` service-locator is fully wired.

    For now this handler is registered in the ``JobRegistry`` so the registry is
    complete and the durable path is testable end-to-end, but the actual
    fact-extraction jobs in ``memory.py:sync_turn`` remain on the ``func=`` path
    (not converted) so that LLM-client access is preserved.  The gate test
    (GATE-DURABLE) verifies registry dispatch round-trips; memory-write behavior
    is unchanged.
    """
    if not text:
        logger.debug("handle_extract_fact: empty text — skipping")
        return "skipped: empty text"
    # Seam: log and succeed so tests can verify registry dispatch.
    # Real LLM extraction is NOT performed here (no client available).
    logger.info(
        "handle_extract_fact: seam invoked for session=%r group=%r len=%d "
        "(LLM extraction requires func= path until JobContext is wired in slice 2.3)",
        session_id,
        group,
        len(text),
    )
    return f"seam:extract_fact:session={session_id}:group={group}"


# ---------------------------------------------------------------------------
# Handler: generate_title
# ---------------------------------------------------------------------------


def handle_generate_title(
    session_id: str = "",
    user_message: str = "",
    assistant_message: str = "",
    **_kwargs: Any,
) -> str:
    """Durable handler for background session title generation.

    Replaces the former ``func=`` closure in orchestrator.py so title jobs survive restart.

    The durable path does NOT have an LLM client in the serializable payload, so
    it uses :func:`~hachi.core.session_titler.fallback_title` (deterministic
    truncation of the first user message line) rather than an LLM call.  This is
    an acceptable degradation: the goal is a non-empty title on restart, not a
    perfect one.  If an LLM-generated title was produced and stored *before* the
    restart, ``set_session_title`` is idempotent and the stored title wins.

    Payload keys (serializable):
        session_id:        Target session.
        user_message:      First user turn text (primary signal for title).
        assistant_message: First assistant reply (used as context).
    """
    if not session_id or not user_message:
        logger.debug("handle_generate_title: missing session_id or user_message — skipping")
        return "skipped"
    try:
        from hachi.core.session_store import SessionStore
        from hachi.core.session_titler import fallback_title

        # Durable context: no LLM client available; use deterministic fallback.
        title = fallback_title(user_message)
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
    """Reactive handler for ``file.uploaded`` events (Initiative E, Slice 2.4).

    Runs the ContentExtractor pipeline on the uploaded file:
      1. Resolve FileMetaStore + LocalStorageProvider from paths.
      2. Find the first ContentExtractor that supports the file's media_type.
      3. Extract text, guard_untrusted-wrap it, store as a sidecar (.txt) blob.
      4. Update FileMeta status → ``extracted`` (or ``stored`` on degradation).
      5. Emit ``file.extracted`` via a fresh EventBus publish (no queue to avoid
         recursion).
      6. Optionally index extracted chunks into the ``attachments`` Chroma
         collection when ``[files] index_on_extract = true`` in settings.

    Failures in any step are logged; the handler always returns a status string
    so the JobRegistry marks the job completed (not failed), preventing spurious
    retries on configuration-level mismatches (e.g. extractor unavailable).
    """
    payload = event_payload or {}
    file_id: str = payload.get("file_id", "")
    media_type: str = payload.get("media_type", "")
    name: str = payload.get("name", "")

    if not file_id:
        logger.warning("handle_auto_extract: missing file_id in payload — skipping")
        return "skipped: missing file_id"

    logger.info(
        "handle_auto_extract: processing file_id=%r name=%r media_type=%r seq=%d",
        file_id,
        name,
        media_type,
        event_sequence,
    )

    try:
        from hachi.core.files.store import (
            FileMetaStore,
            LocalStorageProvider,
            _build_extractor_chain,
            find_extractor,
        )
        from hachi.core.paths import get_hachi_paths
        from hachi.core.untrusted_content import guard_untrusted

        paths = get_hachi_paths()
        meta_store = FileMetaStore(paths.files_db)
        storage = LocalStorageProvider(paths.blobs_dir)
        chain = _build_extractor_chain()
        extractor = find_extractor(chain, media_type)

        if extractor is None:
            logger.info(
                "handle_auto_extract: no extractor for media_type=%r (file_id=%r) "
                "— file stays at 'stored'",
                media_type,
                file_id,
            )
            return f"degraded:no_extractor:{file_id}"

        # Open the blob for extraction.
        with storage.open(file_id) as blob_stream:
            raw_text = extractor.extract(blob_stream, media_type)

        # Wrap extracted text with guard_untrusted before it can reach the model.
        guarded_text, flagged = guard_untrusted(raw_text, source=f"file:{name or file_id}")
        if flagged:
            logger.warning(
                "handle_auto_extract: guard_untrusted flagged possible injection in %r",
                file_id,
            )

        # Store guarded text as a sidecar blob under "<file_id>.txt".
        import io as _io

        sidecar_id = f"{file_id}.txt"
        storage.put(sidecar_id, _io.BytesIO(guarded_text.encode("utf-8")))
        meta_store.update_status(file_id, "extracted", extracted_text_ref=sidecar_id)

        logger.info(
            "handle_auto_extract: extracted %d chars from %r → sidecar %r",
            len(guarded_text),
            file_id,
            sidecar_id,
        )

        # Optionally index into 'attachments' Chroma collection (RAG seam).
        _try_index_attachment(file_id, name, raw_text, guarded_text)

        # Emit file.extracted event.
        _emit_file_extracted(file_id, name, media_type, sidecar_id)

        return f"extracted:{file_id}"

    except Exception as exc:
        logger.error(
            "handle_auto_extract: failed for file_id=%r — %s", file_id, exc, exc_info=True
        )
        # Mark as failed in the meta store (best-effort).
        try:
            from hachi.core.files.store import FileMetaStore
            from hachi.core.paths import get_hachi_paths

            FileMetaStore(get_hachi_paths().files_db).update_status(file_id, "failed")
        except Exception:
            pass
        return f"error:{file_id}:{exc}"


def _try_index_attachment(
    file_id: str, name: str, raw_text: str, guarded_text: str
) -> None:
    """Index extracted text chunks into the ``attachments`` Chroma collection.

    Uses the RRF fusion seam (memory_vault.py:785) — inserted into a
    separate ``attachments`` collection so ``semantic_search`` can merge it
    without any caller changes.  Skipped gracefully when chromadb is
    unavailable or ``[files] index_on_extract = false``.
    """
    try:
        from hachi.core.config_loader import load_effective_config

        cfg = load_effective_config()
        if not getattr(cfg.files, "index_on_extract", True):
            return
    except Exception:
        pass  # Default: index when we can

    try:
        import chromadb  # type: ignore[import-untyped]

        from hachi.core.paths import get_hachi_paths

        paths = get_hachi_paths()
        client = chromadb.PersistentClient(path=str(paths.chroma_dir / "attachments"))
        coll = client.get_or_create_collection(
            "attachments",
            metadata={"description": "File attachment extracted text chunks"},
        )
        # Simple whole-doc chunk (chunking at scale is deferred per §8).
        chunk_id = f"{file_id}:0"
        coll.upsert(
            ids=[chunk_id],
            documents=[raw_text[:8000]],  # Chroma default embedding window
            metadatas=[{"file_id": file_id, "name": name, "chunk": 0}],
        )
        logger.debug("handle_auto_extract: indexed chunk %r into attachments collection", chunk_id)
    except ImportError:
        logger.debug("handle_auto_extract: chromadb not available — skipping index")
    except Exception:
        logger.debug("handle_auto_extract: index failed (non-fatal)", exc_info=True)


def _emit_file_extracted(
    file_id: str, name: str, media_type: str, sidecar_id: str
) -> None:
    """Publish ``file.extracted`` via a fresh in-process EventBus call."""
    try:
        from hachi.core.event_bus import EventBus
        from hachi.core.runtime_events import FILE_EXTRACTED, RuntimeEvent, now_iso

        # Fresh bus with no subscribers — publish propagates to any wired bus
        # only if it was injected.  We re-use the shared _shared bus when
        # available (best-effort; not required for correctness).
        bus: Any = None
        try:
            from hachi.gateway.orchestrator import _shared  # type: ignore[attr-defined]

            bus = getattr(_shared, "event_bus", None)
        except Exception:
            pass

        if bus is None:
            bus = EventBus()

        event = RuntimeEvent(
            name=FILE_EXTRACTED,
            payload={
                "file_id": file_id,
                "name": name,
                "media_type": media_type,
                "sidecar_id": sidecar_id,
            },
            timestamp=now_iso(),
        )
        bus.publish(event)
    except Exception:
        logger.debug("_emit_file_extracted: publish failed (non-fatal)", exc_info=True)


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
