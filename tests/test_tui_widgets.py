"""Tests for the TUI rendering helpers and theme palette.

These cover the *pure* builders (no running Textual app required): the theme
status mapping, the sidebar/settings formatters, and the small app-level
helpers that classify tool-call payloads.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from hachi.tui import theme
from hachi.tui.app import _event_input_summary, _looks_like_error
from hachi.tui.formatters import (
    format_session_info,
    format_settings,
    format_status_bar,
    format_welcome,
)
from hachi.tui.widgets import (
    InputHistory,
    SlashPalette,
    search_transcript,
    submit_starter,
    transcript_overflow,
)

_CMDS = (
    ("/help", "Show help"),
    ("/memory <query>", "Search memory"),
    ("/model [name]", "Switch model"),
    ("/quit", "Quit"),
)


def _render(renderable) -> str:
    console = Console(file=StringIO(), width=80, no_color=True)
    console.print(renderable)
    return console.file.getvalue()


def test_welcome_greets_the_user() -> None:
    out = _render(format_welcome())
    assert "hachi" in out
    assert "suggestion" in out


class _FakeScreen:
    def __init__(self) -> None:
        self.cleared = False
        self.state: str | None = None

    def clear_welcome(self) -> None:
        self.cleared = True

    def set_hachi_state(self, state: str) -> None:
        self.state = state


class _FakeApp:
    def __init__(self) -> None:
        self.screen = _FakeScreen()
        self.submitted: list[str] = []

    def submit_prompt(self, prompt: str) -> None:
        self.submitted.append(prompt)


def test_submit_starter_clears_welcome_and_submits() -> None:
    app = _FakeApp()
    submit_starter(app, "What can you do?")
    assert app.submitted == ["What can you do?"]
    assert app.screen.cleared is True
    assert app.screen.state == "thinking"


def test_slash_palette_filters_to_prefix() -> None:
    palette = SlashPalette(_CMDS)
    assert palette.update("/me") is True
    verbs = [verb for verb, _ in palette.matches]
    assert "/memory <query>" in verbs
    assert "/model [name]" not in verbs  # '/me' is not a prefix of '/model'


def test_slash_palette_hidden_without_leading_slash_or_after_space() -> None:
    palette = SlashPalette(_CMDS)
    assert palette.update("hello") is False
    assert palette.update("") is False
    assert palette.update("/memory now") is False  # space → past the verb


def test_slash_palette_move_wraps() -> None:
    palette = SlashPalette(_CMDS)
    palette.update("/")  # all commands match
    assert palette.index == 0
    palette.move(-1)
    assert palette.index == len(palette.matches) - 1
    palette.move(1)
    assert palette.index == 0


def test_slash_palette_selected_verb_strips_arg_hints() -> None:
    palette = SlashPalette(_CMDS)
    palette.update("/mem")
    assert palette.selected_verb() == "/memory"


def test_slash_palette_render_lists_matches() -> None:
    palette = SlashPalette(_CMDS)
    palette.update("/m")
    out = _render(palette.render())
    assert "/memory" in out and "/model" in out


def test_input_history_dedupes_and_skips_blank() -> None:
    h = InputHistory()
    h.add("a")
    h.add("a")  # consecutive duplicate ignored
    h.add("  ")  # blank ignored
    h.add("b")
    assert h.prev("") == "b"
    assert h.prev("") == "a"
    assert h.prev("") == "a"  # clamped at the oldest entry


def test_input_history_prev_next_cycle_with_draft() -> None:
    h = InputHistory()
    h.add("one")
    h.add("two")
    assert h.prev("draft") == "two"  # stashes the in-progress draft
    assert h.prev("draft") == "one"
    assert h.next() == "two"
    assert h.next() == "draft"  # past the newest → restored draft
    assert h.next() is None  # already at the draft


def test_input_history_empty() -> None:
    h = InputHistory()
    assert h.prev("x") is None
    assert h.next("x") is None


def test_input_history_respects_cap() -> None:
    h = InputHistory(cap=2)
    for item in ("1", "2", "3"):
        h.add(item)
    assert h.prev("") == "3"
    assert h.prev("") == "2"
    assert h.prev("") == "2"  # '1' was dropped by the cap


# --- transcript search ---------------------------------------------------
_TRANSCRIPT = [
    ("you", "How do I deploy the gateway?"),
    ("hachi", "Run `hachi gateway` to start the gateway server locally."),
    ("you", "Thanks!"),
]


def test_search_transcript_finds_case_insensitive_matches() -> None:
    hits = search_transcript(_TRANSCRIPT, "GATEWAY")
    # Two messages mention the gateway (the user question + the agent reply).
    assert [h["index"] for h in hits] == [1, 2]
    assert hits[0]["role"] == "you"
    assert hits[1]["role"] == "hachi"
    # The agent reply mentions "gateway" twice.
    assert hits[1]["count"] == 2


def test_search_transcript_snippet_is_ellipsized_when_clipped() -> None:
    long_text = "x" * 100 + "needle" + "y" * 100
    hits = search_transcript([("you", long_text)], "needle", context=10)
    snippet = hits[0]["snippet"]
    assert "needle" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")


def test_search_transcript_empty_query_and_no_match() -> None:
    assert search_transcript(_TRANSCRIPT, "   ") == []
    assert search_transcript(_TRANSCRIPT, "nonexistent-token") == []


def test_search_transcript_respects_limit() -> None:
    entries = [("you", "match here")] * 50
    hits = search_transcript(entries, "match", limit=5)
    assert len(hits) == 5


# --- transcript cap ------------------------------------------------------
def test_transcript_overflow_under_cap_returns_zero() -> None:
    assert transcript_overflow(10, 400) == 0
    assert transcript_overflow(400, 400) == 0


def test_transcript_overflow_over_cap_returns_excess() -> None:
    assert transcript_overflow(405, 400) == 5
    assert transcript_overflow(1000, 400) == 600


def test_transcript_overflow_disabled_for_nonpositive_cap() -> None:
    assert transcript_overflow(10_000, 0) == 0
    assert transcript_overflow(10_000, -1) == 0


# --- theme ---------------------------------------------------------------
def test_status_color_known_and_unknown() -> None:
    assert theme.status_color("completed") == theme.SUCCESS
    assert theme.status_color("running") == theme.ACCENT
    assert theme.status_color("failed") == theme.ERROR
    assert theme.status_color("totally-unknown") == theme.MUTED


def test_status_glyph_maps_lifecycle() -> None:
    assert theme.status_glyph("completed") == theme.GLYPH_DONE
    assert theme.status_glyph("failed") == theme.GLYPH_ERROR
    assert theme.status_glyph("running") == theme.GLYPH_RUNNING
    assert theme.status_glyph(None) == theme.GLYPH_RUNNING


# --- app helpers ---------------------------------------------------------
def test_event_input_summary_truncates_and_joins() -> None:
    summary = _event_input_summary({"role": "coder", "task": "x" * 80})
    assert "role=coder" in summary
    assert "…" in summary  # long value truncated
    assert _event_input_summary({}) == "running…"
    assert _event_input_summary(None) == "running…"


def test_looks_like_error_detects_failures() -> None:
    assert _looks_like_error("Delegation failed: ANTHROPIC_API_KEY must be provided")
    assert _looks_like_error("Traceback (most recent call last)")
    assert not _looks_like_error("4")
    assert not _looks_like_error("ok, computed result")


# --- sidebar formatter ---------------------------------------------------
def test_format_session_info_renders_status_and_tools() -> None:
    out = _render(
        format_session_info(
            {
                "status": "Working",
                "status_kind": "running",
                "model": "claude-haiku-4-5",
                "tier": "guided",
                "workspace": "hachi",
                "session": "abc123",
                "stream": "live",
                "jobs": 2,
                "approvals": 1,
                "warnings": 0,
                "recent_tools": [
                    {"name": "delegate_task", "status": "failed"},
                    {"name": "read_file", "status": "completed"},
                ],
            }
        )
    )
    assert "Working" in out
    assert "claude-haiku-4-5" in out
    assert "guided" in out
    assert "delegate_task" in out
    assert "read_file" in out


def test_format_session_info_handles_empty_tools_and_tokens() -> None:
    out = _render(
        format_session_info(
            {
                "status": "Idle",
                "status_kind": "idle",
                "recent_tools": [],
                "tokens": 12345,
                "cost": 0.0123,
            }
        )
    )
    assert "no tool activity yet" in out
    assert "12,345" in out


# --- status bar formatter ------------------------------------------------
def test_format_status_bar_with_usage_shows_model_tokens_cost() -> None:
    out = _render(
        format_status_bar(
            eye=theme.hachi_eye("working")[0],
            eye_color=theme.hachi_eye("working")[1],
            state="working",
            model="claude-haiku-4-5",
            tokens=12345,
            cost=0.0123,
        )
    )
    assert theme.HACHI_WORDMARK in out
    assert "claude-haiku-4-5" in out
    assert "12,345 tok" in out
    assert "~$0.0123" in out
    assert "working" in out


def test_format_status_bar_without_usage_is_brand_and_state_only() -> None:
    out = _render(
        format_status_bar(
            eye=theme.hachi_eye("idle")[0],
            eye_color=theme.hachi_eye("idle")[1],
            state="idle",
        )
    )
    assert theme.HACHI_WORDMARK in out
    # "idle" maps to the hachi status word "on watch"
    assert "on watch" in out
    # no tokens/cost until the first turn produces usage ("tok " unit, distinct
    # from the "tok" inside the hachi wordmark)
    assert "tok " not in out
    assert "$" not in out


# --- settings formatter --------------------------------------------------
def test_format_settings_renders_overview_and_raw() -> None:
    out = _render(
        format_settings(
            {"session_id": "sess-123"},
            {
                "provider": {"kind": "anthropic", "model": "claude-haiku-4-5"},
                "permissions": {"tier": "guided"},
                "memory": {"enable_semantic_memory": True},
            },
            [{"session_id": "sess-123", "created_at": "2026-06-05"}],
        )
    )
    assert "sess-123" in out
    assert "anthropic" in out
    assert "guided" in out
    assert "Raw config" in out
