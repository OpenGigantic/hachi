"""Tests for T-52: persist compaction checkpoints so resumed sessions match live ones.

Regression (BB-140): ``MemoryManager._sliding_window_compact`` spliced a checkpoint
``Message`` into ``_history`` that lived only in the live process's Python list.
Session persistence wrote exactly two row types per turn — ``"user"`` and
``"assistant"`` — so the checkpoint never reached ``sessions.db``, and a resumed
session silently lost the compacted middle of the conversation: no error, no
warning, just an agent confidently answering from an incomplete picture.

Covers:
  1. A compaction checkpoint is persisted to the real SessionStore, not only kept
     in RAM (``test_compaction_checkpoint_survives_a_session_round_trip`` — the
     behavioural lock).
  2. The persisted row uses a role distinct from "user", so it can never be
     confused with something a human actually typed
     (``test_persisted_checkpoint_is_not_logged_as_a_user_message``).
  3. When the background structured-summary job lands, the persisted row
     converges on the real summary instead of staying stuck on the placeholder
     (``test_background_summary_updates_the_persisted_checkpoint`` — catches a
     partial implementation that persists once and never updates).
  4. ``resume_from_session`` seeds the persisted checkpoint back into the new
     session's ``_history`` (not just leaves it sitting in the DB), disguised as
     ``role="user"`` the same way the live compaction path disguises it — and,
     per the T-52 spec §9 ("do not render checkpoints in the chat UI"), the
     checkpoint text must NOT leak into ``resumed_transcript``, which is the
     feed the UI renders as chat turns.

Reuses the ``InMemoryJobQueue`` + ``_FakeLLMClient`` harness from
``test_compaction_summary_applyback.py`` (module docstring, ``:1-13``) rather than
inventing a second one — same pattern, extended with the SessionStore wiring this
task adds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hachi.core.llm import Message
from hachi.core.memory import MemoryManager
from hachi.core.session_store import CHECKPOINT_ROLE, SessionStore
from hachi.gateway.orchestrator import AgentSession
from hachi.jobs.queue import InMemoryJobQueue, JobWorker

# ---------------------------------------------------------------------------
# Helpers — mirrors tests/test_compaction_summary_applyback.py
# ---------------------------------------------------------------------------

_SENTINEL = MemoryManager._COMPACTION_PLACEHOLDER_SENTINEL
_CHECKPOINT_MARKER = "[CONTEXT CHECKPOINT"
_REAL_SUMMARY = (
    "## Active Task\nFinish the compaction persistence fix.\n\n"
    "## Completed Actions\n1. Wrote update_message_content\n\n"
    "## Active State\nbranch=audit/2026-07-19-wave2, tests green"
)


class _FakeLLMClient:
    """Returns a canned real summary without hitting the network."""

    def __init__(self, summary: str = _REAL_SUMMARY) -> None:
        self._summary = summary
        self.call_count = 0

    def generate(self, messages: list, **kwargs: Any) -> Any:  # noqa: ANN001
        self.call_count += 1
        response = MagicMock()
        response.content = self._summary
        response.tool_calls = []
        return response


def _make_messages(n: int) -> list[Message]:
    """Create a simple alternating user/assistant history of length n."""
    msgs: list[Message] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(Message(role=role, content=f"message {i}"))
    return msgs


def _build_mm_and_trigger(
    store: SessionStore,
    session_id: str,
    fake_client: _FakeLLMClient | None = None,
    max_active_messages: int = 8,
) -> tuple[MemoryManager, InMemoryJobQueue]:
    """Create a store-bound MemoryManager, fill history, and trigger compaction.

    Mirrors ``test_compaction_summary_applyback.py``'s ``_build_mm_and_trigger``,
    plus the ``set_session_store`` wiring T-52 adds.
    """
    if fake_client is None:
        fake_client = _FakeLLMClient()
    queue = InMemoryJobQueue()
    mm = MemoryManager(
        max_active_messages=max_active_messages,
        job_queue=queue,
        llm_client=fake_client,
    )
    mm.set_session_store(store, session_id)  # the wiring added for T-52
    for m in _make_messages(max_active_messages + 4):
        mm._history.append(m)
    mm._sliding_window_compact()
    return mm, queue


# ---------------------------------------------------------------------------
# Test 1 (the lock): the checkpoint row exists in sessions.db, not only in RAM
# ---------------------------------------------------------------------------


def test_compaction_checkpoint_survives_a_session_round_trip(tmp_path: Path) -> None:
    """A compaction checkpoint must be in the durable transcript, not only in RAM.

    Regression (T-52 / BB-140): compaction replaced dropped messages with a
    checkpoint that lived only in MemoryManager._history. Session persistence
    wrote user/assistant rows only, so a resumed session silently lost the
    compacted middle of the conversation with no error and no warning.

    Deliberately does NOT drain the background job queue: the row must already
    be there right after _sliding_window_compact() returns, so a crash before
    the background summary job finishes still leaves an honest marker instead of
    a silent hole (T-52 spec §5, option A).
    """
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        session_id = store.start_session(model="test")

        mm, _queue = _build_mm_and_trigger(store, session_id, max_active_messages=6)

        rows = store.get_session_messages(session_id, limit=200)
        roles = [r["role"] for r in rows]
        assert any(_CHECKPOINT_MARKER in (r["content"] or "") for r in rows), (
            f"no checkpoint row persisted; roles were {roles}"
        )
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Test 2: the persisted role must not be "user"
# ---------------------------------------------------------------------------


def test_persisted_checkpoint_is_not_logged_as_a_user_message(tmp_path: Path) -> None:
    """The checkpoint must be distinguishable from something the human typed."""
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        session_id = store.start_session(model="test")

        mm, _queue = _build_mm_and_trigger(store, session_id, max_active_messages=6)

        rows = store.get_session_messages(session_id, limit=200)
        checkpoint_rows = [r for r in rows if _CHECKPOINT_MARKER in (r["content"] or "")]
        assert checkpoint_rows, "no checkpoint row persisted"
        for row in checkpoint_rows:
            assert row["role"] != "user", (
                "checkpoint row persisted with role='user' — indistinguishable "
                "from something the human actually typed"
            )
            assert row["role"] == CHECKPOINT_ROLE
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Test 3: the row converges on the real summary once the background job lands
# ---------------------------------------------------------------------------


def test_background_summary_updates_the_persisted_checkpoint(tmp_path: Path) -> None:
    """When the real summary lands, the persisted row converges on it.

    Catches a partial implementation that persists the placeholder once and
    never converges it — the exact failure mode option B in the T-52 spec (§5)
    was rejected for.
    """
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        session_id = store.start_session(model="test")

        mm, queue = _build_mm_and_trigger(store, session_id, max_active_messages=6)

        # Before the job runs, the persisted row holds the async placeholder.
        rows_before = store.get_session_messages(session_id, limit=200)
        placeholder_rows = [r for r in rows_before if _SENTINEL in (r["content"] or "")]
        assert placeholder_rows, "expected the placeholder checkpoint to be persisted first"

        # Drain the queue — runs the background summary job synchronously in test.
        worker = JobWorker(queue)
        drained = 0
        while worker.execute_one():
            drained += 1
        assert drained >= 1, "no jobs were drained"

        rows_after = store.get_session_messages(session_id, limit=200)
        sentinel_present = any(_SENTINEL in (r["content"] or "") for r in rows_after)
        assert not sentinel_present, (
            "persisted checkpoint row still contains the placeholder sentinel "
            "after the background summary job completed"
        )
        real_summary_present = any("Active Task" in (r["content"] or "") for r in rows_after)
        assert real_summary_present, "real summary text not found in the persisted row"
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Test 4: resume_from_session seeds the checkpoint back into _history
# ---------------------------------------------------------------------------


def test_resume_from_session_seeds_checkpoint_into_history(tmp_path: Path) -> None:
    """resume_from_session must put the checkpoint's text back into _history.

    This is the other half of the round trip: persisting the row is useless if
    the session that resumes never sees it again. Seeded as role="user" — the
    same disguise MemoryManager._sliding_window_compact already uses for the
    live in-memory checkpoint (core/memory.py) — so the LLM sees it identically
    whether the session is live or resumed.
    """
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        source_session_id = store.start_session(model="test")
        _build_mm_and_trigger(store, source_session_id, max_active_messages=6)

        resumed_memory = MemoryManager()
        sess = AgentSession.__new__(AgentSession)
        sess._session_store = store
        sess.memory = resumed_memory
        sess._resumed_transcript = []

        sess.resume_from_session(source_session_id)

        checkpoint_messages = [
            m
            for m in resumed_memory.history
            if isinstance(m.content, str) and _CHECKPOINT_MARKER in m.content
        ]
        assert checkpoint_messages, (
            "resume_from_session did not seed the persisted checkpoint into "
            "_history — a resumed session would silently lose the compacted "
            "middle of the conversation"
        )
        assert checkpoint_messages[0].role == "user", (
            "seeded checkpoint must disguise as role='user', matching the live "
            "in-memory compaction path, so the LLM sees it identically"
        )
    finally:
        store.shutdown()


def test_resume_from_session_excludes_checkpoint_from_resumed_transcript(
    tmp_path: Path,
) -> None:
    """A checkpoint must not surface in the UI-facing resumed_transcript feed.

    T-52 spec §9 ("out of scope"): rendering checkpoints in the chat UI is a
    separate, frontend-owned change. resumed_transcript is exactly the feed the
    UI renders as chat turns (see AgentSession.resumed_transcript docstring), so
    the checkpoint must be seeded into _history (test above) but kept out of
    this list.
    """
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        source_session_id = store.start_session(model="test")
        _build_mm_and_trigger(store, source_session_id, max_active_messages=6)

        resumed_memory = MemoryManager()
        sess = AgentSession.__new__(AgentSession)
        sess._session_store = store
        sess.memory = resumed_memory
        sess._resumed_transcript = []

        sess.resume_from_session(source_session_id)

        leaked = [
            content for _role, content in sess._resumed_transcript if _CHECKPOINT_MARKER in content
        ]
        assert not leaked, (
            f"checkpoint text leaked into resumed_transcript (UI-rendered feed): {leaked}"
        )
    finally:
        store.shutdown()
