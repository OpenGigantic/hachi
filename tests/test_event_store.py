"""Tests for EventStore -- durable SQLite event log (T-75 Stage A).

Store-level only: nothing here goes through ``RuntimeReadModelStore``.
Stage B owns the durability tests that thread the store through it (see
``docs/audit/2026-07-19/tasks/T-75-durable-event-log.md`` §7).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import hachi.core.events as events_mod
from hachi.core.events.store import EventRecord, EventStore
from hachi.core.paths import HachiPaths
from hachi.core.secret_scanner import REDACTION_PLACEHOLDER

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    """Provide an EventStore backed by a temp directory."""
    return EventStore(db_path=tmp_path / "test_events.db")


# ---------------------------------------------------------------------------
# 1. append -> read_since round-trips name, payload and timestamp intact
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_append_then_read_since_round_trips_fields(self, store: EventStore) -> None:
        payload = {"name": "read_file", "input": {"path": "a.txt"}, "nested": {"a": [1, 2, 3]}}
        seq = store.append("s1", "tool.call.completed", payload, "2026-08-26T00:00:00+00:00")

        assert seq > 0
        rows = store.read_since("s1", after=0, limit=100)
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, EventRecord)
        assert row.name == "tool.call.completed"
        assert row.payload == payload  # JSON round-trip preserved the nested structure
        assert row.timestamp == "2026-08-26T00:00:00+00:00"
        assert row.sequence == seq


# ---------------------------------------------------------------------------
# 2. Reopening a second EventStore over the same file sees the first one's
#    rows, and max_seq() continues rather than restarting at 0. Core claim
#    of the whole task, at store level.
# ---------------------------------------------------------------------------


class TestReopenAcrossRestart:
    def test_second_store_over_same_path_sees_first_stores_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "events.db"

        first = EventStore(db_path=db)
        for i in range(3):
            first.append("s1", "tool.call.completed", {"i": i}, "t")
        first.close()

        # "Restart": a brand-new store object over the same file. No shared state.
        second = EventStore(db_path=db)
        rows = second.read_since("s1", after=0, limit=100)
        assert len(rows) == 3
        assert [r.payload["i"] for r in rows] == [0, 1, 2]

    def test_max_seq_continues_rather_than_restarting_at_zero(self, tmp_path: Path) -> None:
        db = tmp_path / "events.db"

        first = EventStore(db_path=db)
        for _ in range(5):
            first.append("s1", "tool.call.completed", {}, "t")
        assert first.max_seq() == 5
        first.close()

        second = EventStore(db_path=db)
        assert second.max_seq() == 5, "max_seq() must not restart at 0 after reopening"

        next_seq = second.append("s1", "tool.call.completed", {}, "t")
        assert next_seq == 6, "AUTOINCREMENT must continue the sequence, not restart it"
        assert second.max_seq() == 6


# ---------------------------------------------------------------------------
# 3. read_since filters by session_id and respects after/limit
# ---------------------------------------------------------------------------


class TestReadSinceFiltering:
    def test_sessions_do_not_bleed_into_each_other(self, store: EventStore) -> None:
        store.append("s1", "tool.call.completed", {"who": "s1-a"}, "t")
        store.append("s2", "tool.call.completed", {"who": "s2-a"}, "t")
        store.append("s1", "tool.call.completed", {"who": "s1-b"}, "t")

        s1_rows = store.read_since("s1", after=0, limit=100)
        s2_rows = store.read_since("s2", after=0, limit=100)

        assert [r.payload["who"] for r in s1_rows] == ["s1-a", "s1-b"]
        assert [r.payload["who"] for r in s2_rows] == ["s2-a"]

    def test_after_excludes_already_seen_rows(self, store: EventStore) -> None:
        seqs = [store.append("s1", "tool.call.completed", {"i": i}, "t") for i in range(5)]

        rows = store.read_since("s1", after=seqs[1], limit=100)
        assert [r.sequence for r in rows] == seqs[2:]

    def test_limit_caps_the_result_and_keeps_oldest_first(self, store: EventStore) -> None:
        for i in range(10):
            store.append("s1", "tool.call.completed", {"i": i}, "t")

        rows = store.read_since("s1", after=0, limit=3)
        assert len(rows) == 3
        assert [r.payload["i"] for r in rows] == [0, 1, 2]  # oldest -> newest, capped


# ---------------------------------------------------------------------------
# 4. The deny-list: a text.stream.delta append does not create a row; a
#    tool.call.completed one does.
# ---------------------------------------------------------------------------


class TestEphemeralDenyList:
    def test_streaming_delta_is_not_persisted_but_tool_event_is(self, store: EventStore) -> None:
        delta_seq = store.append("s1", "text.stream.delta", {"text": "hi"}, "t")
        thinking_seq = store.append("s1", "thinking.step", {"iteration": 1}, "t")
        tool_seq = store.append("s1", "tool.call.completed", {"name": "x"}, "t")

        assert not delta_seq, "text.stream.delta must be denied (falsy sentinel)"
        assert not thinking_seq, "thinking.step must be denied (falsy sentinel)"
        assert tool_seq, "tool.call.completed must be persisted"

        rows = store.read_since("s1", after=0, limit=100)
        assert len(rows) == 1
        assert rows[0].name == "tool.call.completed"


# ---------------------------------------------------------------------------
# 5. Schema version is stamped: a freshly created database reports version 1.
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_fresh_database_is_stamped_schema_version_one(self, tmp_path: Path) -> None:
        db = tmp_path / "events.db"
        store = EventStore(db_path=db)
        store.close()

        conn = sqlite3.connect(str(db))
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "schema_version" in tables
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            assert row is not None
            assert row[0] == 1
        finally:
            conn.close()

    def test_opening_a_future_version_database_raises(self, tmp_path: Path) -> None:
        """§8 acceptance mirror (test_migrations.py): a too-new database must raise,
        not be silently read -- an event log has no source to rebuild from."""
        from hachi.core.migrations import SchemaTooNewError, set_schema_version

        db = tmp_path / "events.db"
        EventStore(db_path=db).close()

        conn = sqlite3.connect(str(db))
        try:
            set_schema_version(conn, 99)
        finally:
            conn.close()

        with pytest.raises(SchemaTooNewError):
            EventStore(db_path=db)


# ---------------------------------------------------------------------------
# 6. Payloads are redacted before they hit disk (T-75 Stage A2).
#
#    Two layers under test, separately:
#    - redact_structure(): general, pattern-based (13 vendor-prefix shapes).
#    - _SENSITIVE_PAYLOAD_KEYS: narrow, name-based, for values (like
#      run.suspended's wait_key) that carry no recognisable vendor prefix and
#      so are provably invisible to the pattern matcher.
# ---------------------------------------------------------------------------


class TestPayloadRedactedBeforePersisting:
    def test_vendor_shaped_secret_in_nested_payload_is_redacted(self, tmp_path: Path) -> None:
        """A fake-but-real-prefix-shaped secret, buried inside nested structure,
        must be absent both from read_since()'s return AND from the raw SQLite
        payload column -- the second assertion is what actually proves nothing
        sensitive reached disk, not just that the read path masks it."""
        db = tmp_path / "events.db"
        store = EventStore(db_path=db)
        fake_key = "sk-ant-" + "A" * 40
        payload = {
            "error": f"AuthenticationError: Authorization: Bearer {fake_key}",
            "detail": {"nested": ["fine", fake_key]},
        }
        seq = store.append("s1", "llm.retry", payload, "t")

        rows = store.read_since("s1", after=0, limit=100)
        assert len(rows) == 1
        assert fake_key not in json.dumps(rows[0].payload)

        conn = sqlite3.connect(str(db))
        try:
            raw = conn.execute("SELECT payload FROM events WHERE seq = ?", (seq,)).fetchone()[0]
        finally:
            conn.close()
        assert fake_key not in raw, "the raw SQLite column must not contain the secret either"

    def test_wait_key_is_scrubbed_while_sibling_fields_survive_unchanged(
        self, tmp_path: Path
    ) -> None:
        """redact_structure() cannot see this: a Hachi-internal webhook resume
        token carries no vendor prefix, so no pattern matches it. Only the
        narrow _SENSITIVE_PAYLOAD_KEYS layer can catch it -- and it must not
        take ordinary audit content (wait_kind, run_id, session_id, reason)
        down with it."""
        db = tmp_path / "events.db"
        store = EventStore(db_path=db)
        webhook_token = "opaque-webhook-resume-token-no-vendor-prefix"
        payload = {
            "run_id": "run-123",
            "session_id": "s1",
            "turn_id": "turn-9",
            "wait_kind": "webhook",
            "wait_key": webhook_token,
            "reason": "waiting for external callback",
        }
        seq = store.append("s1", "run.suspended", payload, "t")

        rows = store.read_since("s1", after=0, limit=100)
        assert len(rows) == 1
        result = rows[0].payload
        assert result["wait_key"] == REDACTION_PLACEHOLDER
        assert result["wait_kind"] == "webhook"
        assert result["run_id"] == "run-123"
        assert result["session_id"] == "s1"
        assert result["turn_id"] == "turn-9"
        assert result["reason"] == "waiting for external callback"

        conn = sqlite3.connect(str(db))
        try:
            raw = conn.execute("SELECT payload FROM events WHERE seq = ?", (seq,)).fetchone()[0]
        finally:
            conn.close()
        assert webhook_token not in raw

    def test_ordinary_payload_is_not_over_redacted(self, store: EventStore) -> None:
        """No secret-shaped content anywhere: tool name, an int, a bool and a
        short string must round-trip byte-identical."""
        payload = {"name": "read_file", "count": 3, "ok": True, "path": "a.txt"}
        store.append("s1", "tool.call.completed", payload, "t")

        rows = store.read_since("s1", after=0, limit=100)
        assert len(rows) == 1
        assert rows[0].payload == payload


# ---------------------------------------------------------------------------
# 7. The store does not fail open on a corrupt row (T-75 Stage A2).
# ---------------------------------------------------------------------------


class TestCorruptPayloadDoesNotFailSilently:
    def test_corrupt_payload_row_logs_a_warning_and_returns_empty_dict(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        db = tmp_path / "events.db"
        store = EventStore(db_path=db)
        seq = store.append("s1", "tool.call.completed", {"ok": True}, "t")

        # Damage the row directly -- bypasses append() entirely, so this
        # exercises _safe_loads()'s decode-failure path specifically.
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE events SET payload = ? WHERE seq = ?", ("{not valid json", seq))
            conn.commit()
        finally:
            conn.close()

        with caplog.at_level(logging.WARNING, logger="hachi.core.events.store"):
            rows = store.read_since("s1", after=0, limit=100)

        assert len(rows) == 1
        assert rows[0].payload == {}, "a damaged row must not take down the whole read"

        # The requirement is that the failure becomes LOUD -- asserting only the
        # {} above would pass just as well against the silent version this
        # replaces, so assert the warning actually fired and names the row.
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(str(seq) in msg for msg in warnings), (
            f"expected a warning naming seq={seq}, got: {warnings}"
        )


# ---------------------------------------------------------------------------
# 8. rebind_session() -- retags rows by exact session_id equality (T-75 Stage D).
#
#    Store-level coverage of the method RuntimeReadModelStore.set_session_id()
#    calls to close the boot-window orphan (test_event_store_durability.py
#    covers it end-to-end through RuntimeReadModelStore).
# ---------------------------------------------------------------------------


class TestRebindSession:
    def test_rebind_retags_rows_and_returns_the_moved_count(self, store: EventStore) -> None:
        store.append("__boot__abc", "boot.progress", {"i": 0}, "t")
        store.append("__boot__abc", "boot.progress", {"i": 1}, "t")
        store.append("s2", "tool.call.completed", {"who": "s2"}, "t")

        moved = store.rebind_session("__boot__abc", "s1")

        assert moved == 2
        assert [r.payload["i"] for r in store.read_since("s1", after=0, limit=100)] == [0, 1]
        assert store.read_since("__boot__abc", after=0, limit=100) == []
        # s2's row, never tagged with the rebound id, is untouched.
        assert [r.payload["who"] for r in store.read_since("s2", after=0, limit=100)] == ["s2"]

    def test_rebind_is_an_exact_equality_match_not_a_prefix(self, store: EventStore) -> None:
        """The safety property the whole fix depends on: two DIFFERENT
        placeholders never bleed into each other, even though both were
        minted from the same ``__boot__`` prefix."""
        store.append("__boot__aaa", "boot.progress", {"who": "a"}, "t")
        store.append("__boot__bbb", "boot.progress", {"who": "b"}, "t")

        store.rebind_session("__boot__aaa", "session-a")

        assert [r.payload["who"] for r in store.read_since("session-a", after=0, limit=100)] == [
            "a"
        ]
        # b's row is untouched -- still under its own original placeholder,
        # not swept up by a's rebind despite the shared literal prefix.
        assert [r.payload["who"] for r in store.read_since("__boot__bbb", after=0, limit=100)] == [
            "b"
        ]

    def test_rebind_of_an_id_with_no_rows_returns_zero(self, store: EventStore) -> None:
        assert store.rebind_session("__boot__nonexistent", "s1") == 0


# ---------------------------------------------------------------------------
# 9. get_event_store() -- the process-wide singleton must not outlive a
#    repointed state dir (T-75 Stage E).
#
#    Regression coverage for the failure reproduced directly: a test that
#    isolates a real boot by patching get_hachi_paths() to a tmp_path (e.g.
#    test_boot_agent_stages.py, test_feature_liveness.py) permanently pinned
#    the singleton to that temp path. The patch unwinds, the temp directory
#    is deleted, but the singleton keeps its open SQLite handle and append()
#    keeps reporting success against an orphaned file -- silently, no
#    exception -- which is why this surfaced as unrelated failures elsewhere
#    rather than one clear error.
#
#    These tests never touch the developer's real %LOCALAPPDATA%\hachi --
#    same discipline as test_jobs_db_path_migration.py's module docstring:
#    an "outer" tmp_path-backed HachiPaths stands in for "the currently
#    active, non-isolated path" and an "inner" one for the isolated window,
#    so "the patch unwinds" is exercised faithfully without ever resolving
#    to the real machine state dir.
# ---------------------------------------------------------------------------


def _fake_paths(root: Path) -> HachiPaths:
    return HachiPaths(
        config_dir=root / "config",
        data_dir=root / "data",
        cache_dir=root / "cache",
        state_dir=root / "state",
    )


class TestGetEventStoreSingletonFollowsRepointedStateDir:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self) -> Iterator[None]:
        """Reset the module global before AND after every test in this class.

        The singleton under test here *is* the global state T-75 Stage E is
        about -- leaking it into another test in this file would be the same
        bug in miniature, so it is cleared on both sides, not just torn down
        after, and any store left open by a failed assertion is closed too.
        """
        events_mod._event_store = None
        events_mod._event_store_path = None
        yield
        if events_mod._event_store is not None:
            events_mod._event_store.close()
        events_mod._event_store = None
        events_mod._event_store_path = None

    def test_singleton_follows_a_repointed_state_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Core T-75 Stage E claim: get_event_store() re-resolves the state
        dir on every call rather than caching it forever.

        MUST FAIL before the fix: pre-fix, the second get_event_store() call
        below returns the exact same (stale) object as the first, still
        pointed at inner_paths rather than the now-current outer_paths.
        """
        outer_paths = _fake_paths(tmp_path / "outer")
        inner_paths = _fake_paths(tmp_path / "inner")

        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: outer_paths)

        with monkeypatch.context() as isolated:
            isolated.setattr("hachi.core.paths.get_hachi_paths", lambda: inner_paths)
            temp_store = events_mod.get_event_store()
            assert temp_store._db_path == inner_paths.events_db

        # The inner patch has now unwound -- get_hachi_paths() resolves outer_paths
        # again, exactly like a test's isolation patch unwinding after boot.
        real_store = events_mod.get_event_store()
        assert real_store._db_path == outer_paths.events_db, (
            "get_event_store() must follow the repointed state dir, not stay "
            "pinned to the temp path from the first call"
        )
        assert real_store._db_path != inner_paths.events_db

    def test_repointing_closes_the_previous_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BB-42: a superseded store must not leak its SQLite handle."""
        outer_paths = _fake_paths(tmp_path / "outer")
        inner_paths = _fake_paths(tmp_path / "inner")

        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: inner_paths)
        temp_store = events_mod.get_event_store()

        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: outer_paths)
        events_mod.get_event_store()  # repoint -- must close temp_store's connection

        with pytest.raises(sqlite3.ProgrammingError):
            temp_store._conn.execute("SELECT 1")

    def test_same_path_call_returns_the_identical_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The singleton must not be rebuilt on every call -- only on a
        repoint -- or a correctness bug has been traded for a performance one."""
        paths = _fake_paths(tmp_path)
        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: paths)

        first = events_mod.get_event_store()
        second = events_mod.get_event_store()

        assert first is second
