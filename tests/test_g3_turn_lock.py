"""G3-T3 / REL-01 / HIGH-003 — per-session chat serialization in _InProcessGatewayRuntime.

Two assertions:
1. Same-session: concurrent chat() calls are serialized — the second starts only
   after the first finishes (no interleaving of entry/exit order).
2. Different-sessions: chat() calls on separate sessions CAN overlap — their
   execution interleaves, proving no global lock was introduced.

T-71: all three timing-sensitive tests in this file were converted from
`time.monotonic()` deltas / raw cross-thread timestamps to explicit
threading.Barrier / threading.Event coordination, asserting on recorded order or
on deterministic timeout/deadlock outcomes instead of wall-clock comparisons —
see each test's docstring for the specific mechanism.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

# We import the class under test directly; no real LLM / ChromaDB involved.
from hachi.cli.main import _InProcessGatewayRuntime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLEEP = 0.05  # seconds each fake chat() "works" — short but enough to interleave


@dataclass
class _CallRecord:
    """Thread-safe ordered log of enter/exit markers for one session.

    T-71: order (append order under a lock), not wall-clock duration, is what
    proves serialization — whichever marker lands in the list first really did
    happen first, regardless of how fast or slow the host machine is. Previously
    this recorded raw `time.monotonic()` timestamps and callers compared them;
    that comparison starved under `-n auto` scheduling jitter even though the
    underlying property (lock exclusivity) does not depend on wall-clock speed.
    """

    sequence: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, session_id: str) -> None:
        with self.lock:
            self.sequence.append(f"{session_id}:enter")
        time.sleep(SLEEP)
        with self.lock:
            self.sequence.append(f"{session_id}:exit")


def _make_fake_agent(recorder: _CallRecord, session_id: str) -> MagicMock:
    """Return a mock AgentSession whose .chat() records entry/exit order."""
    agent = MagicMock()
    agent.chat.side_effect = lambda _prompt: recorder.record(session_id) or "ok"
    agent.shutdown.return_value = None
    return agent


def _register(runtime: _InProcessGatewayRuntime, session_id: str, agent: Any) -> None:
    """Inject a fake agent into the runtime without booting a real one."""
    with runtime._agents_lock:
        runtime._agents[session_id] = agent
        if session_id not in runtime._chat_locks:
            runtime._chat_locks[session_id] = threading.Lock()


# ---------------------------------------------------------------------------
# Test 1 — same session: calls must NOT overlap
# ---------------------------------------------------------------------------


class TestSameSessionSerialized:
    def test_two_concurrent_chats_do_not_overlap(self) -> None:
        """Two threads calling chat() on the same session must be serialized.

        T-71: proven via the ORDER of markers `_CallRecord` appends (enter/exit
        strings under a lock), not via `time.monotonic()` timestamps. If the
        calls are genuinely serialized, the recorded sequence must strictly
        alternate enter, exit, enter, exit — two "enter" markers can never
        appear back to back. This holds regardless of host speed (unlike a
        wall-clock comparison, which starves under `-n auto`).
        """
        runtime = _InProcessGatewayRuntime()
        recorder = _CallRecord()
        session_id = "session-A"
        _register(runtime, session_id, _make_fake_agent(recorder, session_id))

        barrier = threading.Barrier(2)

        def _chat() -> None:
            barrier.wait()  # start both threads simultaneously
            runtime.chat(session_id, "hello")

        t1 = threading.Thread(target=_chat)
        t2 = threading.Thread(target=_chat)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not t1.is_alive() and not t2.is_alive(), "a chat() call did not finish in time"
        assert recorder.sequence == [
            f"{session_id}:enter",
            f"{session_id}:exit",
            f"{session_id}:enter",
            f"{session_id}:exit",
        ], f"calls overlapped — recorded order was {recorder.sequence}"


# ---------------------------------------------------------------------------
# Test 2 — different sessions: calls MUST overlap (no global lock)
# ---------------------------------------------------------------------------


class TestDifferentSessionsConcurrent:
    def test_two_sessions_run_concurrently(self) -> None:
        """Two threads chatting on DIFFERENT sessions must be able to run in parallel.

        T-71: proven with a `threading.Barrier(2)` instead of a wall-clock
        measurement (previously `wall_time < 1.7 * SLEEP`, which starves under
        `-n auto` — this was the one of the ten that reproducibly failed under
        parallel workers, see T-71 report). Each fake chat() only returns once
        BOTH sessions have reached the barrier. If a global lock served sessions
        one at a time, the second call could never reach the barrier until the
        first released it — which it can't, because it is waiting on the very
        same barrier — so the barrier deterministically times out
        (BrokenBarrierError) instead of passing slowly. No elapsed-time
        comparison is involved.
        """
        runtime = _InProcessGatewayRuntime()
        barrier = threading.Barrier(2, timeout=5)

        def _make_agent() -> MagicMock:
            agent = MagicMock()

            def _side(_prompt: str) -> str:
                barrier.wait()
                return "ok"

            agent.chat.side_effect = _side
            agent.shutdown.return_value = None
            return agent

        _register(runtime, "session-A", _make_agent())
        _register(runtime, "session-B", _make_agent())

        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def _run(session_id: str) -> None:
            try:
                runtime.chat(session_id, "hello")
            except BaseException as exc:  # noqa: BLE001 — must surface on the main thread
                with errors_lock:
                    errors.append(exc)

        t1 = threading.Thread(target=_run, args=("session-A",))
        t2 = threading.Thread(target=_run, args=("session-B",))
        t1.start()
        t2.start()
        t1.join(timeout=8)
        t2.join(timeout=8)

        assert not t1.is_alive() and not t2.is_alive(), "a chat() call did not finish in time"
        assert not errors, (
            f"sessions did not run concurrently — barrier never satisfied: {errors!r}"
        )

    def test_interleaving_of_entry_exit_timestamps(self) -> None:
        """Verify overlap via an explicit handshake, not raw timestamps: session-Y
        must be able to enter its critical section while session-X's is still open.

        T-71: session-X's fake chat() is held open by the test until session-Y has
        confirmed entry. If a global lock serialized the two sessions, session-Y's
        chat() would never signal entry while X is held open (X will not release
        until it sees Y enter) — so the wait for `y_entered` deterministically
        times out. No timestamp comparison is involved.

        X's own hold timeout (30s) is deliberately much larger than the outer
        check's timeout (5s) below: if they were comparable, a broken (globally
        locked) run could pass by accident whenever X's own safety-release beat
        the outer check's timeout in the race between two independent clocks —
        exactly the kind of scheduling-dependent flake this task exists to
        remove. Making the hold an order of magnitude longer than the check
        means the check always loses that race when the bug is real.
        """
        runtime = _InProcessGatewayRuntime()
        sequence: list[str] = []
        seq_lock = threading.Lock()
        x_entered = threading.Event()
        y_entered = threading.Event()
        x_may_exit = threading.Event()

        def _make_agent(sid: str) -> MagicMock:
            agent = MagicMock()

            def _side(_prompt: str) -> str:
                with seq_lock:
                    sequence.append(f"{sid}:enter")
                if sid == "session-X":
                    x_entered.set()
                    # Held open until Y has entered (or a 30s safety cap; see the
                    # docstring for why this must stay much larger than the
                    # outer check's timeout below).
                    x_may_exit.wait(timeout=30)
                else:
                    y_entered.set()
                with seq_lock:
                    sequence.append(f"{sid}:exit")
                return "ok"

            agent.chat.side_effect = _side
            agent.shutdown.return_value = None
            return agent

        _register(runtime, "session-X", _make_agent("session-X"))
        _register(runtime, "session-Y", _make_agent("session-Y"))

        def _go(sid: str) -> None:
            runtime.chat(sid, "hi")

        # Daemon so a failed assertion below (which skips the join calls) can
        # never leave a live non-daemon thread blocking interpreter shutdown.
        tx = threading.Thread(target=_go, args=("session-X",), daemon=True)
        ty = threading.Thread(target=_go, args=("session-Y",), daemon=True)
        tx.start()

        try:
            assert x_entered.wait(timeout=5), "session-X never entered"
            ty.start()

            # For concurrent execution, session-Y must be able to enter while
            # session-X is still active (X has not been released yet).
            assert y_entered.wait(timeout=5), (
                f"session-Y did not enter while session-X was still active — "
                f"sessions did not overlap: {sequence}"
            )
        finally:
            # Always release X (pass or fail) so its thread doesn't sit parked
            # for the rest of the 30s hold once this test has already decided
            # its outcome.
            x_may_exit.set()

        tx.join(timeout=5)
        ty.join(timeout=5)
        assert not tx.is_alive() and not ty.is_alive(), "a chat() call did not finish in time"

        assert sequence[0] == "session-X:enter"
        assert sequence[1] == "session-Y:enter", (
            f"session-Y did not enter before session-X exited: {sequence}"
        )


# ---------------------------------------------------------------------------
# Test 3 — lock cleanup on close_connection
# ---------------------------------------------------------------------------


class TestLockCleanup:
    def test_close_connection_removes_chat_lock(self) -> None:
        """close_connection() must drop the per-session lock to avoid a leak."""
        runtime = _InProcessGatewayRuntime()
        agent = MagicMock()
        agent.shutdown.return_value = None
        _register(runtime, "session-Z", agent)

        assert "session-Z" in runtime._chat_locks
        runtime.close_connection("session-Z")
        assert "session-Z" not in runtime._chat_locks
        assert "session-Z" not in runtime._agents
