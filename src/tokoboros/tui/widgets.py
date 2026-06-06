"""Reusable Textual widgets for the Tokoboros session transcript.

The transcript is a scrollable column of these widgets (Claude Code style):
user bubbles, assistant Markdown messages, and inline tool-call cards that
flip from *running* to *done*/*error*. The sidebar carries live session
context (opencode style) rather than a raw event log.
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

from tokoboros.tui import theme
from tokoboros.tui.art import ouroboros
from tokoboros.tui.formatters import format_session_info


class TokoGlyph(Static):
    """Brand header strip: the Toko eye + wordmark, colored by live state.

    The eye is the persistent identity (mirroring the SVG ouroboros in
    ``docs/brand/``); its glyph and color encode the agent's current runtime
    state, so the header doubles as an at-a-glance status indicator. For
    ``thinking``/``working`` the eye is animated on a timer (tail-chasing the
    loop) via ``theme.TOKO_EYE_FRAMES``; other states render a static glyph.
    """

    state: reactive[str] = reactive("idle")

    def __init__(self, **kwargs: Any) -> None:
        self._frame = 0
        super().__init__(self._render_state("idle"), classes="toko-glyph", **kwargs)

    def on_mount(self) -> None:
        # Advance animated states only; static states make _tick a cheap no-op.
        self.set_interval(0.13, self._tick)

    def _tick(self) -> None:
        frames = theme.TOKO_EYE_FRAMES.get(self.state)
        if not frames:
            return
        self._frame = (self._frame + 1) % len(frames)
        self.update(self._render_state(self.state, eye=frames[self._frame]))

    def set_state(self, state: str) -> None:
        """Update the displayed runtime state (idle/thinking/working/…)."""
        self.state = state

    def watch_state(self, state: str) -> None:
        self._frame = 0
        frames = theme.TOKO_EYE_FRAMES.get(state)
        self.update(self._render_state(state, eye=frames[0] if frames else None))

    @staticmethod
    def _render_state(state: str, eye: str | None = None) -> Text:
        glyph, color = theme.toko_eye(state)
        if eye is not None:
            glyph = eye
        body = Text()
        body.append(f"{glyph} ", style=f"bold {color}")
        body.append(theme.TOKO_WORDMARK, style=f"bold {theme.FOREGROUND}")
        body.append(f"  · {state}", style=theme.MUTED)
        return body


class TokoSplash(Static):
    """A large ASCII ouroboros shown on boot — the visible serpent avatar."""

    def __init__(self, **kwargs: Any) -> None:
        caption = Text("the token ouroboros", style=f"italic {theme.MUTED}")
        body = Group(Text(), Align.center(ouroboros()), Text(), Align.center(caption))
        super().__init__(body, classes="toko-splash", **kwargs)


class UserBubble(Static):
    """A single user prompt, marked with a left accent bar."""

    def __init__(self, text: str) -> None:
        self.message_text = text
        body = Text()
        body.append(f"{theme.GLYPH_USER} ", style=theme.ROLE_USER)
        body.append("You", style=f"bold {theme.ROLE_USER}")
        body.append("\n")
        body.append(text, style=theme.FOREGROUND)
        super().__init__(body, classes="msg msg-user")


class AgentMessage(Static):
    """An assistant reply rendered as Markdown under a role header."""

    def __init__(self, text: str) -> None:
        self.message_text = text
        header = Text()
        header.append(f"{theme.GLYPH_AGENT} ", style=theme.ROLE_AGENT)
        header.append("Tokoboros", style=f"bold {theme.ROLE_AGENT}")
        renderable: RenderableType
        if text.strip():
            renderable = Group(header, Markdown(text))
        else:
            renderable = Group(header, Text("(no response)", style=theme.MUTED))
        super().__init__(renderable, classes="msg msg-agent")


class ErrorCard(Static):
    """A runtime/chat error surfaced inline in the transcript."""

    def __init__(self, message: str) -> None:
        body = Text()
        body.append(f"{theme.GLYPH_ERROR} ", style=theme.ERROR)
        body.append("Error\n", style=f"bold {theme.ERROR}")
        body.append(message, style=theme.FOREGROUND)
        super().__init__(body, classes="msg msg-error")


class ToolCard(Vertical):
    """Inline card for a tool call; updates in place running -> done/error."""

    def __init__(self, name: str, input_summary: str = "") -> None:
        super().__init__(classes="tool-card running")
        self.tool_name = name
        self._input_summary = input_summary
        self._status = "running"
        self._header = Static(self._render_header(), classes="tool-header")
        self._body = Static(
            Text(input_summary or "running…", style=theme.MUTED),
            classes="tool-body",
        )

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._body

    def _render_header(self) -> Text:
        header = Text()
        color = theme.status_color(self._status)
        header.append(f"{theme.status_glyph(self._status)} ", style=color)
        header.append(f"{theme.GLYPH_TOOL} ", style=theme.MUTED)
        header.append(self.tool_name, style=f"bold {theme.FOREGROUND}")
        header.append(f"  {self._status}", style=color)
        return header

    def _refresh(self, body: RenderableType) -> None:
        self._header.update(self._render_header())
        self._body.update(body)

    def mark_completed(self, result_preview: str = "") -> None:
        self._status = "completed"
        self.remove_class("running")
        self.add_class("done")
        text = (result_preview or "done").strip() or "done"
        self._refresh(Text(text, style=theme.MUTED))

    def mark_error(self, message: str = "") -> None:
        self._status = "error"
        self.remove_class("running")
        self.add_class("failed")
        self._refresh(Text((message or "failed").strip(), style=theme.ERROR))


class SidebarPanel(Static):
    """Live session context panel (status, model, counters, recent tools)."""

    def update_info(self, info: dict[str, Any]) -> None:
        self.update(format_session_info(info))
