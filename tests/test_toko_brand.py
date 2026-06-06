"""Tests for the Toko brand glyph helpers (pure, no Textual runtime needed)."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from tokoboros.tui import theme
from tokoboros.tui.art import ouroboros
from tokoboros.tui.widgets import TokoGlyph, TokoSplash


def test_toko_eye_known_states() -> None:
    for state in ("idle", "thinking", "working", "success", "error"):
        glyph, color = theme.toko_eye(state)
        assert glyph
        assert color.startswith("#")


def test_toko_eye_unknown_falls_back_to_idle() -> None:
    assert theme.toko_eye("nonsense") == theme.TOKO_STATE_EYES["idle"]
    assert theme.toko_eye(None) == theme.TOKO_STATE_EYES["idle"]


def test_toko_state_for_event_mapping() -> None:
    assert theme.toko_state_for_event("tool.call.started") == "working"
    assert theme.toko_state_for_event("session.chat.started") == "thinking"
    assert theme.toko_state_for_event("session.chat.completed") == "success"
    assert theme.toko_state_for_event("boot.complete") == "idle"
    assert theme.toko_state_for_event("boot.failed") == "error"


def test_toko_state_for_event_tool_completed_branches() -> None:
    assert theme.toko_state_for_event("tool.call.completed", is_error=False) == "thinking"
    assert theme.toko_state_for_event("tool.call.completed", is_error=True) == "error"


def test_toko_state_for_event_unknown_is_none() -> None:
    assert theme.toko_state_for_event("usage.updated") is None
    assert theme.toko_state_for_event("text.stream.delta") is None


def test_toko_glyph_render_carries_wordmark_and_eye() -> None:
    rendered = TokoGlyph()._render_state("working")
    plain = rendered.plain
    assert theme.TOKO_WORDMARK in plain
    eye, _color = theme.toko_eye("working")
    assert eye in plain
    assert "working" in plain


def test_toko_glyph_status_surfaces_model_and_tokens() -> None:
    glyph = TokoGlyph()
    glyph._status = {"model": "claude-haiku-4-5", "tokens": 12345, "cost": 0.0123}
    plain = glyph._render_state("working").plain
    assert "claude-haiku-4-5" in plain
    assert "12,345 tok" in plain
    assert "~$0.0123" in plain


def test_toko_glyph_paints_without_method_collision() -> None:
    """Regression: ``_render`` once shadowed Textual's internal ``Widget._render``,
    crashing the first real paint. Drive the actual render path to catch it."""

    class _Probe(App[None]):
        def compose(self) -> ComposeResult:
            yield TokoGlyph(id="toko-header")

    async def _run() -> int:
        app = _Probe()
        async with app.run_test(size=(120, 24)) as pilot:
            glyph = app.query_one("#toko-header", TokoGlyph)
            glyph.set_state("working")
            await pilot.pause()
            return len(glyph.render_line(0))

    # Must not raise; a painted strip has at least one segment.
    assert asyncio.run(_run()) > 0


def test_ouroboros_art_carries_serpent_colors() -> None:
    art = ouroboros()
    styles = {str(span.style) for span in art.spans}
    assert theme.TOKO_BODY in styles  # body
    assert theme.TOKO_EYE_CREAM in styles  # eye
    assert theme.TOKO_TONGUE in styles  # tongue
    assert "\n" in art.plain  # multi-line block


def test_toko_eye_frames_define_active_states() -> None:
    assert set(theme.TOKO_EYE_FRAMES) == {"thinking", "working"}
    for frames in theme.TOKO_EYE_FRAMES.values():
        assert len(frames) >= 2


def test_toko_splash_constructs() -> None:
    TokoSplash()  # should not raise


def test_toko_glyph_eye_animates_only_on_active_states() -> None:
    class _Probe(App[None]):
        def compose(self) -> ComposeResult:
            yield TokoGlyph(id="h")

    async def _run() -> tuple[bool, bool]:
        app = _Probe()
        async with app.run_test() as pilot:
            glyph = app.query_one("#h", TokoGlyph)
            glyph.set_state("idle")
            await pilot.pause()
            f = glyph._frame
            glyph._tick()
            idle_changed = glyph._frame != f
            glyph.set_state("thinking")
            await pilot.pause()
            f2 = glyph._frame
            glyph._tick()
            thinking_changed = glyph._frame != f2
            return idle_changed, thinking_changed

    idle_changed, thinking_changed = asyncio.run(_run())
    assert idle_changed is False
    assert thinking_changed is True
