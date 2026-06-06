from __future__ import annotations

from pathlib import Path

from tokoboros.core.llm import Message
from tokoboros.core.memory import MemoryManager
from tokoboros.core.session_store import SessionStore
from tokoboros.tui.app import TokoborosTextualApp

_GATEWAY = "http://127.0.0.1:8111"


# --- Backend resume support ------------------------------------------------


def test_session_store_resume_helpers(tmp_path: Path) -> None:
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        sid = store.start_session(model="test-model")
        store.log_message(sid, "user", "hello")
        store.log_message(sid, "assistant", "hi there")
        store.log_message(sid, "tool", "raw output", tool_name="read_file")

        assert store.get_latest_session_id() == sid

        messages = store.get_session_messages(sid)
        assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "hi there"
    finally:
        store.shutdown()


def test_session_store_get_session_messages_respects_limit(tmp_path: Path) -> None:
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        sid = store.start_session(model="m")
        for i in range(10):
            store.log_message(sid, "user", f"msg-{i}")
        recent = store.get_session_messages(sid, limit=3)
        # Most recent three, in chronological order.
        assert [m["content"] for m in recent] == ["msg-7", "msg-8", "msg-9"]
    finally:
        store.shutdown()


def test_get_latest_session_id_none_when_empty(tmp_path: Path) -> None:
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    try:
        assert store.get_latest_session_id() is None
    finally:
        store.shutdown()


def test_memory_seed_history_appends_without_compaction() -> None:
    memory = MemoryManager(max_active_messages=2)
    seeded = memory.seed_history(
        [
            Message(role="user", content="first"),
            Message(role="assistant", content="reply"),
            Message(role="user", content="second"),
        ]
    )
    assert seeded == 3
    # Bulk seed bypasses the per-message sliding window.
    assert len(memory.history) == 3
    assert memory._last_user_query == "second"


def test_memory_seed_history_empty_is_noop() -> None:
    memory = MemoryManager()
    assert memory.seed_history([]) == 0
    assert memory.history == []


# --- Setup controller on demand --------------------------------------------


def test_ensure_setup_controller_is_idempotent() -> None:
    app = TokoborosTextualApp(gateway_base_url=_GATEWAY)
    assert app.setup_controller is None
    controller = app.ensure_setup_controller()
    assert controller is not None
    assert app.setup_controller is controller
    # Calling again returns the same instance rather than rebuilding state.
    assert app.ensure_setup_controller() is controller


# --- Slash command dispatch ------------------------------------------------


def test_slash_command_quit_calls_exit(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url=_GATEWAY)
    calls: list[str] = []
    monkeypatch.setattr(app, "exit", lambda: calls.append("exit"))
    app.handle_slash_command("/quit")
    assert calls == ["exit"]


def test_slash_command_unknown_is_reported(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url=_GATEWAY)
    notes: list[str] = []
    monkeypatch.setattr(app, "_session_note", lambda text, **_kw: notes.append(text))
    app.handle_slash_command("/definitely-not-a-command")
    assert any("Unknown command" in note for note in notes)


def test_slash_command_settings_switches_mode(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url=_GATEWAY)
    switched: list[str] = []
    monkeypatch.setattr(app, "_switch_mode", lambda mode: switched.append(mode))
    app.handle_slash_command("/settings")
    assert switched == ["settings"]


def test_slash_command_plan_expands_prompt(monkeypatch) -> None:
    app = TokoborosTextualApp(gateway_base_url=_GATEWAY)
    sent: list[tuple[str, str | None]] = []
    monkeypatch.setattr(app, "set_toko_state_safe", lambda _s: None)
    monkeypatch.setattr(
        app,
        "submit_prompt",
        lambda prompt, display_text=None: sent.append((prompt, display_text)),
    )
    app.handle_slash_command("/plan ship the feature")
    assert len(sent) == 1
    expanded, display = sent[0]
    assert "PLAN MODE" in expanded
    assert "ship the feature" in expanded
    assert display == "/plan ship the feature"
