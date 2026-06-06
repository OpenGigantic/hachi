"""Generated ASCII art for the Toko brand — the token-ouroboros.

The serpent is drawn algorithmically as a thick aspect-corrected ring (cells are
~2x tall, so the y-distance is scaled) with a mouth gap at the top, a brighter
head lobe holding a cream eye, and an orange forked tongue flicking toward the
tail. Rendering as a Rich ``Text`` keeps per-cell coloring; the math is cheap and
deterministic, so it is generated on demand rather than stored as a giant string.
"""

from __future__ import annotations

import math

from rich.text import Text

from tokoboros.tui import theme

_BLOCK = "█"


def ouroboros(width: int = 34, height: int = 15) -> Text:
    """Return the Toko ouroboros as a colored Rich ``Text`` block."""
    cx, cy = (width - 1) / 2, (height - 1) / 2
    r_out, r_in, gap_deg = 0.95, 0.58, 32

    grid: list[list[str | None]] = [[None] * width for _ in range(height)]

    def place(angle_deg: float, radius: float, color: str, *, fill_empty: bool = False) -> None:
        a = math.radians(angle_deg)
        y = round(cy - radius * math.sin(a) * (height / 2))
        x = round(cx + radius * math.cos(a) * (width / 2))
        if 0 <= y < height and 0 <= x < width:
            if fill_empty or grid[y][x] is not None:
                grid[y][x] = color

    # Body: a filled ring with a wedge removed at the top (the mouth).
    for y in range(height):
        for x in range(width):
            dx = (x - cx) / (width / 2)
            dy = (y - cy) / (height / 2)
            r = math.hypot(dx, dy)
            ang = math.degrees(math.atan2(-dy, dx))
            in_gap = abs(((ang - 90 + 180) % 360) - 180) <= gap_deg / 2
            if r_in <= r <= r_out and not in_gap:
                grid[y][x] = theme.TOKO_BODY

    # Head: brighter lobe just clockwise of the gap.
    for ang in range(52, 76, 3):
        for radius in (0.62, 0.74, 0.86, 0.95):
            place(ang, radius, theme.TOKO_BODY_BRIGHT, fill_empty=True)

    # Eye, embedded in the front of the head.
    place(60, 0.74, theme.TOKO_EYE_CREAM, fill_empty=True)

    # Forked tongue, flicking from the head into the gap toward the tail.
    for ang, radius in [(72, 0.66), (80, 0.60), (90, 0.54), (90, 0.66), (100, 0.60)]:
        place(ang, radius, theme.TOKO_TONGUE, fill_empty=True)

    text = Text()
    for y in range(height):
        for x in range(width):
            color = grid[y][x]
            text.append(_BLOCK if color else " ", style=color or "")
        if y < height - 1:
            text.append("\n")
    return text
