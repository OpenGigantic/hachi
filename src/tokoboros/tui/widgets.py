"""Reusable Textual widgets for the Tokoboros session transcript.

The transcript is a scrollable column of these widgets (Claude Code style):
user bubbles, assistant Markdown messages, and inline tool-call cards that
flip from *running* to *done*/*error*. The sidebar carries live session
context (opencode style) rather than a raw event log.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from tokoboros.tui import theme
from tokoboros.tui.formatters import format_session_info


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
