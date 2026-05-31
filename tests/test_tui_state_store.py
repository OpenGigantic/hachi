from __future__ import annotations

from pathlib import Path

from tokoboros.tui.state_store import TUIStateSnapshot, TUIStateStore


def test_state_store_roundtrip(tmp_path: Path) -> None:
    store = TUIStateStore(db_path=str(tmp_path / "tui_state.db"))
    snapshot = TUIStateSnapshot(
        last_mode="authoring",
        draft_input="draft text",
        authoring_target="brain.og",
        recent_authoring_targets=("brain.og", "skills/general/wiki.og"),
        filters_json='{"severity":"high"}',
    )

    store.save_snapshot(snapshot)
    loaded = store.load_snapshot()

    assert loaded == snapshot
    store.shutdown()


def test_state_store_clear_resets_values(tmp_path: Path) -> None:
    store = TUIStateStore(db_path=str(tmp_path / "tui_state.db"))
    store.save_snapshot(TUIStateSnapshot(last_mode="ops", draft_input="hello"))

    store.clear()
    loaded = store.load_snapshot()

    assert loaded.last_mode == "session"
    assert loaded.draft_input == ""
    assert loaded.recent_authoring_targets == ()
    store.shutdown()
