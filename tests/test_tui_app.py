from __future__ import annotations

import asyncio
import json

from textual.app import App

from tokoboros.core.permissions import PermissionDecision, PermissionRequest
from tokoboros.tui.app import (
    APP_CSS,
    AuthoringScreen,
    TokoborosTextualApp,
    _sanitize_agent_markdown,
    _sanitize_legacy_rich_markup,
)


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


def test_sanitize_legacy_rich_markup_removes_style_tags() -> None:
    raw = "[green]ok[/green] [dim]value[/dim] [b cyan]You:[/b cyan]"
    assert _sanitize_legacy_rich_markup(raw) == "ok value You:"


def test_sanitize_agent_markdown_preserves_markdown_emphasis() -> None:
    raw = " [bold]I'm[/bold] **Tokoboros** and [green]ready[/green]. "
    assert _sanitize_agent_markdown(raw) == "I'm **Tokoboros** and ready."


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


def test_boot_runtime_uses_gateway_path_without_local_boot(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url="http://127.0.0.1:8111")

    state: dict[str, bool] = {
        "handshake": False,
        "poller_started": False,
        "boot_complete_queued": False,
    }

    monkeypatch.setattr(
        "tokoboros.tui.app.boot_agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("boot_agent should not run in gateway mode")
        ),
    )

    def _fake_handshake() -> None:
        state["handshake"] = True
        app._gateway_connected = True
        app._gateway_session_id = "session-1"

    def _fake_start_poller() -> None:
        state["poller_started"] = True

    def _fake_queue(cb, *args):
        if cb == app._on_boot_complete:
            state["boot_complete_queued"] = True
        cb(*args)
        return True

    monkeypatch.setattr(app, "_consume_gateway_handshake", _fake_handshake)
    monkeypatch.setattr(app, "_start_gateway_event_poller", _fake_start_poller)
    monkeypatch.setattr(app, "_queue_ui_callback", _fake_queue)

    app._boot_runtime()

    assert state["handshake"] is True
    assert state["poller_started"] is True
    assert state["boot_complete_queued"] is True
    assert app._boot_error is None


def test_gateway_handshake_uses_unversioned_session_create_contract(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url="http://127.0.0.1:8111")

    captured: dict[str, object] = {}

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def _fake_urlopen(target, timeout=None):
        url = target if isinstance(target, str) else target.full_url
        if url.endswith("/readiness"):
            captured["readiness_url"] = url
            return _Response({"ready": True, "blockers": []})

        captured["connect_url"] = url
        data = target if isinstance(target, str) else target
        captured["connect_payload"] = json.loads(data.data.decode("utf-8"))
        return _Response({"accepted": True, "session_id": "sess-123"})

    monkeypatch.setattr("tokoboros.tui.app.request.urlopen", _fake_urlopen)

    app._consume_gateway_handshake()

    assert captured["readiness_url"] == "http://127.0.0.1:8111/readiness"
    assert captured["connect_url"] == "http://127.0.0.1:8111/sessions"
    assert captured["connect_payload"] == {
        "client_id": "tokoboros-tui",
        "surface": "tui",
        "protocol_version": "1.0",
        "capabilities": ["events", "health", "readiness"],
    }
    assert app._gateway_connected is True
    assert app._gateway_session_id == "sess-123"


def test_gateway_event_poller_uses_unversioned_events_endpoint(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url="http://127.0.0.1:8111")
    app._gateway_session_id = "session-7"
    app._gateway_after_sequence = 0

    captured: dict[str, object] = {}
    streamed: list[tuple[str, dict]] = []

    class _Response:
        def read(self) -> bytes:
            payload = {
                "events": [
                    {"sequence": 1, "event_type": "text.stream.delta", "payload": {"text": "hi"}},
                    {"sequence": 2, "event_type": "tool.call.started", "payload": {"name": "x"}},
                ],
                "next_sequence": 2,
            }
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def _fake_urlopen(target, timeout=None):
        captured["events_url"] = target
        app._gateway_stop_event.set()
        return _Response()

    monkeypatch.setattr("tokoboros.tui.app.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(
        app, "_runtime_event_from_thread", lambda et, payload: streamed.append((et, payload))
    )

    app._poll_gateway_events()

    events_target = captured["events_url"]
    events_url = events_target.full_url if hasattr(events_target, "full_url") else events_target
    assert events_url == "http://127.0.0.1:8111/sessions/session-7/events?after=0&limit=100"
    assert streamed[0][0] == "text.stream.delta"
    assert streamed[0][1]["text"] == "hi"
    assert streamed[1][0] == "tool.call.started"
    assert app._gateway_after_sequence == 2


def test_gateway_chat_uses_unversioned_messages_endpoint(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url="http://127.0.0.1:8111")
    app._gateway_session_id = "session-9"

    captured: dict[str, object] = {}
    results: list[tuple[str, str | None]] = []

    class _Response:
        def read(self) -> bytes:
            return json.dumps({"session_id": "session-9", "response": "ok"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    def _fake_urlopen(target, timeout=None):
        captured["chat_url"] = target.full_url
        captured["chat_payload"] = json.loads(target.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr("tokoboros.tui.app.request.urlopen", _fake_urlopen)
    monkeypatch.setattr(app, "_on_chat_result", lambda text, err: results.append((text, err)))
    monkeypatch.setattr(
        app, "_queue_ui_callback", lambda callback, *args: (callback(*args), True)[1]
    )

    app._run_gateway_chat("hello")

    assert captured["chat_url"] == "http://127.0.0.1:8111/sessions/session-9/messages"
    assert captured["chat_payload"] == {"prompt": "hello"}
    assert results == [("ok", None)]


def _sample_permission_request(request_id: str = "req-1") -> PermissionRequest:
    return PermissionRequest(
        request_id=request_id,
        operation="execute",
        tool="run_terminal_command",
        target="rm -rf /tmp",
        reason="blocked by policy",
        policy_summary="guided",
    )


def test_request_permission_decision_handles_approve_deny_and_allow_once(monkeypatch) -> None:
    for action in ("approve", "deny", "allow_once"):
        app = TokoborosTextualApp()

        def _fake_show_modal(
            request: PermissionRequest,
            _app: TokoborosTextualApp = app,
            _action: str = action,
        ) -> None:
            _app._resolve_permission_decision(
                request.request_id, PermissionDecision(action=_action)
            )

        monkeypatch.setattr(app, "_show_permission_modal", _fake_show_modal)
        # call_from_thread replaced _queue_ui_callback; patch it to invoke the modal directly.
        monkeypatch.setattr(app, "call_from_thread", lambda fn, *args: fn(*args))

        decision = app._request_permission_decision(_sample_permission_request())

        assert decision.action == action


def test_request_permission_decision_returns_dismissed_when_modal_closes_without_result(
    monkeypatch,
) -> None:
    app = TokoborosTextualApp()

    def _fake_show_modal(request: PermissionRequest) -> None:
        app._resolve_permission_decision(
            request.request_id,
            PermissionDecision(action="deny", rationale="Dismissed without approval"),
        )

    monkeypatch.setattr(app, "_show_permission_modal", _fake_show_modal)
    monkeypatch.setattr(app, "call_from_thread", lambda fn, *args: fn(*args))

    decision = app._request_permission_decision(_sample_permission_request())

    assert decision.action == "deny"
    assert decision.rationale == "Dismissed without approval"


def test_request_permission_decision_times_out_and_cleans_waiter(monkeypatch) -> None:
    app = TokoborosTextualApp()

    class _TimeoutEvent:
        def wait(self, timeout=None) -> bool:
            return False

        def set(self) -> None:
            return

    monkeypatch.setattr("tokoboros.tui.app.threading.Event", _TimeoutEvent)
    # call_from_thread replaced _queue_ui_callback; make it a no-op so the waiter times out.
    monkeypatch.setattr(app, "call_from_thread", lambda fn, *args: None)

    request = _sample_permission_request("req-timeout")
    decision = app._request_permission_decision(request)

    assert decision.action == "deny"
    assert decision.rationale == "Approval timed out"
    assert request.request_id not in app._approval_waiters


def test_resolve_permission_decision_ignores_replay_for_expired_request() -> None:
    app = TokoborosTextualApp()
    request_id = "req-replay"

    class _Event:
        def __init__(self) -> None:
            self.set_count = 0

        def set(self) -> None:
            self.set_count += 1

    waiter = _Event()
    holder: dict[str, PermissionDecision] = {}
    app._approval_waiters[request_id] = (waiter, holder)

    app._resolve_permission_decision(
        request_id, PermissionDecision(action="approve", rationale="ok")
    )
    app._resolve_permission_decision(
        request_id, PermissionDecision(action="deny", rationale="replay")
    )

    assert waiter.set_count == 1
    assert holder["decision"].action == "approve"
    assert holder["decision"].rationale == "ok"
    assert request_id not in app._approval_waiters


def test_action_interrupt_stops_poller_and_resolves_pending_approvals() -> None:
    app = TokoborosTextualApp(gateway_base_url="http://127.0.0.1:8111")

    class _Event:
        def __init__(self) -> None:
            self.set_count = 0

        def set(self) -> None:
            self.set_count += 1

    waiter = _Event()
    holder: dict[str, PermissionDecision] = {}
    app._approval_waiters["req-int"] = (waiter, holder)

    app.action_interrupt()

    assert app._gateway_stop_event.is_set() is True
    assert waiter.set_count == 1
    assert holder["decision"].action == "deny"
    assert holder["decision"].rationale == "Interrupted by operator"
    assert "req-int" not in app._approval_waiters


def test_on_unmount_joins_live_threads_and_cleans_waiters() -> None:
    app = TokoborosTextualApp(gateway_base_url="http://127.0.0.1:8111")

    class _DummyThread:
        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(float(timeout if timeout is not None else 0.0))

    class _DummyAgent:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    class _DummyStore:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def save_snapshot(self, _snapshot) -> None:
            return

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    class _Event:
        def __init__(self) -> None:
            self.set_count = 0

        def set(self) -> None:
            self.set_count += 1

    boot_thread = _DummyThread()
    chat_thread = _DummyThread()
    poller_thread = _DummyThread()
    waiter = _Event()
    holder: dict[str, PermissionDecision] = {}
    agent = _DummyAgent()
    store = _DummyStore()

    app._boot_thread = boot_thread
    app._chat_thread = chat_thread
    app._gateway_event_thread = poller_thread
    app._approval_waiters["req-shutdown"] = (waiter, holder)
    app._agent = agent
    app._state_store = store

    app.on_unmount()

    assert app._gateway_stop_event.is_set() is True
    assert boot_thread.join_calls == [1.0]
    assert chat_thread.join_calls == [1.0]
    assert poller_thread.join_calls == [1.0]
    assert waiter.set_count == 1
    assert holder["decision"].action == "deny"
    assert holder["decision"].rationale == "App shutting down"
    assert "req-shutdown" not in app._approval_waiters
    assert agent.shutdown_calls == 1
    assert store.shutdown_calls == 1


def test_authoring_screen_snapshot_refresh_is_noop_without_body() -> None:
    """Regression: Textual runs every on_mount in the MRO, so AuthoringScreen
    (which replaces the snapshot layout) also runs the inherited
    SnapshotScreen.refresh_from_agent. With no #snapshot-body it must no-op, not
    raise NoMatches. ^r refresh routes through the same method."""

    class _Host(App[None]):
        CSS = APP_CSS

    async def _run() -> int:
        host = _Host()
        async with host.run_test(size=(100, 30)) as pilot:
            screen = AuthoringScreen()
            await host.push_screen(screen)
            await pilot.pause()
            # Direct call raised NoMatches before the fix.
            screen.refresh_from_agent(None)
            return len(screen.query("#snapshot-body"))

    assert asyncio.run(_run()) == 0
