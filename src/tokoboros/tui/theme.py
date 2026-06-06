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

# --- Brand: Toko, the token-ouroboros ------------------------------------
# Terminal companion to the SVG mark in ``docs/brand/``. The eye is the
# persistent brand atom; its glyph and color encode the live runtime state,
# so the header doubles as a status readout (idle/thinking/working/…).
TOKO_WORDMARK = "tokoboros"

# state -> (eye glyph, color)
TOKO_STATE_EYES: dict[str, tuple[str, str]] = {
    "idle": ("◉", MUTED),
    "thinking": ("◓", PRIMARY),
    "working": ("●", ACCENT),
    "success": ("◕", SUCCESS),
    "error": ("◑", ERROR),
}

# Serpent palette for the ASCII ouroboros splash (tui/art.py)
TOKO_BODY = "#1C8A5F"
TOKO_BODY_BRIGHT = "#34B27B"
TOKO_EYE_CREAM = "#FDF6EC"
TOKO_TONGUE = "#E8794A"

# Animated eye frames cycled by the header on a timer (tail-chasing the loop).
# States not listed here render the static glyph from TOKO_STATE_EYES.
TOKO_EYE_FRAMES: dict[str, list[str]] = {
    "thinking": ["◐", "◓", "◑", "◒"],
    "working": ["◉", "◎", "●", "◎"],
}

# runtime event -> Toko state (None = leave the glyph unchanged)
_TOKO_EVENT_STATES: dict[str, str] = {
    "session.chat.started": "thinking",
    "thinking.step": "thinking",
    "permission.requested": "thinking",
    "permission.decided": "thinking",
    "tool.call.started": "working",
    "session.chat.completed": "success",
    "job.completed": "success",
    "boot.complete": "idle",
    "session.ready": "idle",
    "gateway.connect.completed": "idle",
    "boot.failed": "error",
    "job.failed": "error",
    "gateway.events.error": "error",
}


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


def toko_eye(state: str | None) -> tuple[str, str]:
    """Return the ``(eye glyph, color)`` for a Toko runtime state."""
    return TOKO_STATE_EYES.get(str(state or "idle").lower(), TOKO_STATE_EYES["idle"])


def toko_state_for_event(event_type: str, *, is_error: bool = False) -> str | None:
    """Map a runtime event to a Toko state, or ``None`` to leave it unchanged.

    ``tool.call.completed`` resolves to ``error`` when the result looks like a
    failure (caller passes ``is_error``) and otherwise back to ``thinking``,
    since the agent resumes reasoning after a successful tool call.
    """
    if event_type == "tool.call.completed":
        return "error" if is_error else "thinking"
    return _TOKO_EVENT_STATES.get(event_type)


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
