"""Tokoboros TUI theme and semantic palette.

A single cohesive dark theme inspired by Claude Code / opencode: near-black
surfaces, muted slate borders, and restrained semantic accents. The palette
constants are exported so widgets and the pure ``formatters`` builders share
exactly one source of truth for role/status colors (and stay unit-testable
without importing Textual styling at call time).
"""

from __future__ import annotations

from textual.theme import Theme

# --- Core surfaces -------------------------------------------------------
BACKGROUND = "#0d1117"
SURFACE = "#161b22"
PANEL = "#1c2128"
FOREGROUND = "#c9d1d9"
MUTED = "#8b949e"
BORDER_MUTED = "#30363d"

# --- Semantic accents ----------------------------------------------------
PRIMARY = "#7c9cf0"  # indigo — assistant / primary actions
ACCENT = "#56b6c2"  # teal — user / highlights
SECONDARY = "#9d7cd8"  # muted purple
SUCCESS = "#7ec699"
WARNING = "#e5c07b"
ERROR = "#e88388"

# --- Role colors (used by message widgets) -------------------------------
ROLE_USER = ACCENT
ROLE_AGENT = PRIMARY

# --- Status colors (tool cards, sidebar, jobs) ---------------------------
STATUS_COLORS: dict[str, str] = {
    "running": ACCENT,
    "starting": ACCENT,
    "pending": WARNING,
    "completed": SUCCESS,
    "ready": SUCCESS,
    "done": SUCCESS,
    "failed": ERROR,
    "error": ERROR,
    "cancelled": MUTED,
    "idle": MUTED,
}

# Glyphs (single source so widgets/formatters agree)
GLYPH_RUNNING = "⠿"
GLYPH_DONE = "✓"
GLYPH_ERROR = "✗"
GLYPH_USER = "▍"
GLYPH_AGENT = "◆"
GLYPH_TOOL = "⚙"


def status_color(status: str | None) -> str:
    """Return the semantic color for a status label (falls back to muted)."""
    return STATUS_COLORS.get(str(status or "").lower(), MUTED)


def status_glyph(status: str | None) -> str:
    """Return the glyph for a status label (done/error/running)."""
    label = str(status or "").lower()
    if label in {"completed", "done", "ready"}:
        return GLYPH_DONE
    if label in {"failed", "error"}:
        return GLYPH_ERROR
    return GLYPH_RUNNING


TOKOBOROS_THEME = Theme(
    name="tokoboros",
    primary=PRIMARY,
    secondary=SECONDARY,
    accent=ACCENT,
    warning=WARNING,
    error=ERROR,
    success=SUCCESS,
    foreground=FOREGROUND,
    background=BACKGROUND,
    surface=SURFACE,
    panel=PANEL,
    dark=True,
    variables={
        # Custom CSS variables ($name) consumed by APP_CSS.
        "border-muted": BORDER_MUTED,
        "text-muted": MUTED,
        "role-user": ROLE_USER,
        "role-agent": ROLE_AGENT,
        "footer-key-foreground": PRIMARY,
        "block-cursor-background": PRIMARY,
        "input-cursor-background": PRIMARY,
    },
)
