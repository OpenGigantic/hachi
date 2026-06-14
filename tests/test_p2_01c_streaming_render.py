"""P2-01c: TUI incremental streaming render + Esc-interrupt.

These tests exercise the streaming-bubble logic and Esc-interrupt without
a running Textual event loop.  The SessionScreen methods are tested through
a lightweight proxy class that holds the same instance state but uses a plain
attribute for `app` (avoiding Textual's read-only property).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tokoboros.tui.app import SessionScreen
from tokoboros.tui.widgets import AgentMessage

# ---------------------------------------------------------------------------
# Proxy — exercises SessionScreen methods without a real Textual app
# ---------------------------------------------------------------------------


class _ScreenProxy:
    """Holds SessionScreen state + methods; `app` is a plain attribute."""

    def __init__(self, mock_app: Any = None) -> None:
        self._tool_cards: dict = {}
        self._welcome_widgets: list = []
        self._streaming_bubble: AgentMessage | None = None
        self._streaming_text: str = ""
        self._appended: list = []
        self.app = mock_app or MagicMock()

    def _append(self, widget: Any) -> None:
        self._appended.append(widget)
        widget.update = MagicMock()  # avoid Static internals

    # Borrow the real implementations unbound
    ingest_runtime_event = SessionScreen.ingest_runtime_event
    finalize_streaming_bubble = SessionScreen.finalize_streaming_bubble
    reset_streaming_state = SessionScreen.reset_streaming_state
    on_key = SessionScreen.on_key
    # Defensive helpers used by the streaming path; no-op without a Textual loop.
    _at_bottom = SessionScreen._at_bottom
    _scroll_to_tail = SessionScreen._scroll_to_tail


def _make_screen(chat_alive: bool = False) -> _ScreenProxy:
    mock_app = MagicMock()
    mock_app.mark_turn_streamed = MagicMock()
    mock_app.last_agent_text = ""
    thread = MagicMock()
    thread.is_alive.return_value = chat_alive
    mock_app._chat_thread = thread
    return _ScreenProxy(mock_app)


# ---------------------------------------------------------------------------
# AgentMessage.stream_delta
# ---------------------------------------------------------------------------


def test_agent_message_initial_text() -> None:
    msg = AgentMessage("Hello, world!")
    assert msg.message_text == "Hello, world!"


def test_stream_delta_updates_message_text() -> None:
    msg = AgentMessage("Hello")
    msg.update = MagicMock()
    msg.stream_delta("Hello, world!")
    assert msg.message_text == "Hello, world!"


def test_stream_delta_calls_update() -> None:
    msg = AgentMessage("Hello")
    msg.update = MagicMock()
    msg.stream_delta("Hello, world!")
    msg.update.assert_called_once()


def test_stream_delta_empty_produces_cursor_renderable() -> None:
    from io import StringIO

    from rich.console import Console

    msg = AgentMessage("")
    renderable = msg._build_renderable("")
    console = Console(file=StringIO(), width=80, no_color=True)
    console.print(renderable)
    rendered = console.file.getvalue()
    assert "▌" in rendered


def test_stream_delta_successive_calls_accumulate() -> None:
    msg = AgentMessage("A")
    calls: list = []
    msg.update = lambda r: calls.append(r)
    msg.stream_delta("AB")
    msg.stream_delta("ABC")
    assert msg.message_text == "ABC"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# SessionScreen streaming state machine
# ---------------------------------------------------------------------------


def test_first_delta_creates_streaming_bubble() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "Hello"})
    assert screen._streaming_bubble is not None
    assert len(screen._appended) == 1
    assert isinstance(screen._appended[0], AgentMessage)


def test_subsequent_delta_updates_in_place() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "Hello"})
    screen.ingest_runtime_event("text.stream.delta", {"text": ", world"})
    assert len(screen._appended) == 1
    assert screen._streaming_text == "Hello, world"


def test_empty_delta_is_ignored() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": ""})
    assert screen._streaming_bubble is None
    assert len(screen._appended) == 0


def test_mark_turn_streamed_called_on_delta() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "Hi"})
    screen.app.mark_turn_streamed.assert_called_once()


def test_finalize_commits_accumulated_text() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "Hello"})
    screen.ingest_runtime_event("text.stream.delta", {"text": ", world"})
    screen.finalize_streaming_bubble()
    assert screen.app.last_agent_text == "Hello, world"


def test_finalize_clears_streaming_state() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "data"})
    screen.finalize_streaming_bubble()
    assert screen._streaming_bubble is None
    assert screen._streaming_text == ""


def test_finalize_noop_when_no_streaming() -> None:
    screen = _make_screen()
    screen.finalize_streaming_bubble()  # must not raise
    assert screen.app.last_agent_text == ""


def test_reset_streaming_state_discards_in_flight_bubble() -> None:
    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "partial"})
    screen.reset_streaming_state()
    assert screen._streaming_bubble is None
    assert screen._streaming_text == ""


def test_non_stream_event_does_not_create_bubble() -> None:
    screen = _make_screen()
    # Simulate a tool.call.started event — it falls through without creating a bubble
    screen.ingest_runtime_event("tool.call.started", {"name": "some_tool"})
    # The tool.call.started handler needs the _append method only, not streaming
    assert screen._streaming_bubble is None


def test_tool_call_seals_bubble_so_text_and_cards_interleave() -> None:
    """A tool call mid-turn seals the current text bubble; following narration
    starts a fresh bubble below the card — keeping chronological order."""
    from tokoboros.tui.widgets import AgentMessage, ToolCard

    screen = _make_screen()
    screen.ingest_runtime_event("text.stream.delta", {"text": "Let me check"})
    assert screen._streaming_bubble is not None

    screen.ingest_runtime_event(
        "tool.call.started", {"name": "google_list_emails", "input": {"q": "in:inbox"}}
    )
    # Sealed: the card was appended below the text, not above it.
    assert screen._streaming_bubble is None

    screen.ingest_runtime_event("text.stream.delta", {"text": "Found 20 emails"})
    assert screen._streaming_bubble is not None

    kinds = [type(w).__name__ for w in screen._appended]
    assert kinds == ["AgentMessage", "ToolCard", "AgentMessage"]
    assert isinstance(screen._appended[0], AgentMessage)
    assert isinstance(screen._appended[1], ToolCard)


# ---------------------------------------------------------------------------
# Esc-interrupt
# ---------------------------------------------------------------------------


def test_esc_interrupts_when_turn_active() -> None:
    screen = _make_screen(chat_alive=True)
    key_event = MagicMock()
    key_event.key = "escape"
    screen.on_key(key_event)
    screen.app.action_interrupt.assert_called_once()
    key_event.stop.assert_called_once()


def test_esc_noop_when_no_active_turn() -> None:
    screen = _make_screen(chat_alive=False)
    screen.app._chat_thread = None
    key_event = MagicMock()
    key_event.key = "escape"
    screen.on_key(key_event)
    screen.app.action_interrupt.assert_not_called()
    key_event.stop.assert_not_called()


def test_non_escape_key_ignored_even_when_turn_active() -> None:
    screen = _make_screen(chat_alive=True)
    key_event = MagicMock()
    key_event.key = "enter"
    screen.on_key(key_event)
    screen.app.action_interrupt.assert_not_called()


def test_esc_noop_when_app_is_not_tokoborosapp() -> None:
    """If the app isn't a TokoborosTextualApp, on_key is a no-op."""
    screen = _ScreenProxy(mock_app=object())  # not a TokoborosTextualApp
    key_event = MagicMock()
    key_event.key = "escape"
    screen.on_key(key_event)  # must not raise
