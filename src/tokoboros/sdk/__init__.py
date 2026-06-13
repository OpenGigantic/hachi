"""tokoboros.sdk — stable public surface for third-party pack authors.

This namespace re-exports the interfaces that external code may safely import
and depend on.  Everything else inside ``tokoboros`` is internal and subject
to change without notice.

Independently semver'd: the SDK minor version increments independently of the
app release so pack authors can pin ``tokoboros-sdk`` without pulling in new
app features.

Stable surface (v0):
    * Tool authoring  — ToolRegistry, ToolSpec
    * Provider seam   — BaseLLMClient
    * Pack authoring  — OgPackManifest, PackScope, ScheduleBudget, parse_pack_toml,
                        render_consent_sheet
    * Runtime events  — all SESSION_*/TOOL_*/MEMORY_*/MCP_*/CHANNEL_* constants
    * Introspection   — sdk_version(), is_compatible()
"""

from __future__ import annotations

__all__ = [
    # Tool authoring
    "ToolRegistry",
    "ToolSpec",
    # Provider seam
    "BaseLLMClient",
    # Pack authoring
    "OgPackManifest",
    "PackScope",
    "ScheduleBudget",
    "parse_pack_toml",
    "render_consent_sheet",
    # Runtime events
    "events",
    # Introspection
    "sdk_version",
    "is_compatible",
    "SDK_VERSION",
]

# ---------------------------------------------------------------------------
# Tool authoring
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Runtime events — expose the module as ``sdk.events`` so callers can write
# ``from tokoboros.sdk import events; events.SESSION_CHAT_COMPLETED``
# ---------------------------------------------------------------------------
from tokoboros.core import runtime_events as events

# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------
from tokoboros.core.llm import BaseLLMClient as BaseLLMClient
from tokoboros.core.loop import ToolRegistry as ToolRegistry

# ---------------------------------------------------------------------------
# Pack authoring
# ---------------------------------------------------------------------------
from tokoboros.core.og_pack import (
    OgPackManifest as OgPackManifest,
)
from tokoboros.core.og_pack import (
    PackScope as PackScope,
)
from tokoboros.core.og_pack import (
    ScheduleBudget as ScheduleBudget,
)
from tokoboros.core.og_pack import (
    parse_pack_toml as parse_pack_toml,
)
from tokoboros.core.og_pack import (
    render_consent_sheet as render_consent_sheet,
)
from tokoboros.gateway.tool_registration import ToolSpec as ToolSpec

# ---------------------------------------------------------------------------
# Semver introspection
# ---------------------------------------------------------------------------

SDK_VERSION: str = "0.1.0"
"""Current SDK semver string.  Updated on each breaking-or-additive change
to the stable surface, independently of the app release."""

_MIN_APP_VERSION: str = "0.1.0"


def sdk_version() -> str:
    """Return the SDK version string."""
    return SDK_VERSION


def is_compatible(required_sdk_version: str) -> bool:
    """Return True if the running SDK satisfies *required_sdk_version*.

    Uses a simple major-match + minor-GTE rule (no patch sensitivity).

    Args:
        required_sdk_version: Minimum SDK version string (``"MAJOR.MINOR.PATCH"``).

    Returns:
        True when ``SDK_VERSION >= required_sdk_version`` (by semver ordering).

    Example::

        from tokoboros.sdk import is_compatible
        assert is_compatible("0.1.0"), "This pack requires SDK ≥ 0.1.0"
    """
    def _parse(v: str) -> tuple[int, int, int]:
        parts = v.split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2] if len(parts) > 2 else 0))

    return _parse(SDK_VERSION) >= _parse(required_sdk_version)
