from __future__ import annotations

from tokoboros.tui.app import TokoborosTextualApp


class _DummyLoop:
    def __init__(self, closed: bool) -> None:
        self._closed = closed
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def is_closed(self) -> bool:
        return self._closed

    def call_soon_threadsafe(self, callback, *args) -> None:
        self.calls.append((callback, args))


class _DummyStateStore:
    def save_snapshot(self, _snapshot) -> None:
        return

    def shutdown(self) -> None:
        return


def test_queue_ui_callback_returns_false_when_loop_closed() -> None:
    app = TokoborosTextualApp()
    app._loop = _DummyLoop(closed=True)

    queued = app._queue_ui_callback(lambda: None)

    assert queued is False


def test_queue_ui_callback_schedules_when_loop_open() -> None:
    app = TokoborosTextualApp()
    loop = _DummyLoop(closed=False)
    app._loop = loop

    marker: list[str] = []

    def _cb(value: str) -> None:
        marker.append(value)

    queued = app._queue_ui_callback(_cb, "ok")

    assert queued is True
    assert len(loop.calls) == 1
    callback, args = loop.calls[0]
    callback(*args)
    assert marker == ["ok"]


def test_persist_ui_state_tolerates_missing_screen() -> None:
    app = TokoborosTextualApp()
    app._state_store = _DummyStateStore()

    app._persist_ui_state()

    assert app._hydrated_state.last_mode == app.DEFAULT_MODE
