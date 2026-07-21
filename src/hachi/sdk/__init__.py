"""hachi.sdk — stable public surface for third-party pack authors.

This namespace re-exports the interfaces that external code may safely import
and depend on.  Everything else inside ``hachi`` is internal and subject
to change without notice.

Independently semver'd: ``SDK_VERSION`` tracks this surface and is versioned
separately from the app release — it may stay put across app releases that add
no SDK-facing capability, or move independently of one that does.

Install with ``pip install hachi-og``. The SDK ships as part of the base
package; no extra is required — everything it re-exports already lives in
core dependencies.

Stable surface (v0):
    * Tool authoring  — ToolRegistry, ToolSpec
    * Provider seam   — BaseLLMClient
    * Pack authoring  — OgPackManifest, PackScope, ScheduleBudget, parse_pack_toml,
                        render_consent_sheet
    * Runtime events  — all SESSION_*/TOOL_*/MEMORY_*/MCP_*/CHANNEL_* constants
    * Introspection   — sdk_version(), is_compatible()

See ``docs/guides/sdk.md`` for the full stability policy, a runnable example,
and the ``sdk/`` vs ``client/`` distinction.
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
# ``from hachi.sdk import events; events.SESSION_CHAT_COMPLETED``
# ---------------------------------------------------------------------------
from hachi.core import runtime_events as events

# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------
from hachi.core.llm import BaseLLMClient as BaseLLMClient
from hachi.core.loop import ToolRegistry as ToolRegistry

# ---------------------------------------------------------------------------
# Pack authoring
# ---------------------------------------------------------------------------
from hachi.core.og_pack import (
    OgPackManifest as OgPackManifest,
)
from hachi.core.og_pack import (
    PackScope as PackScope,
)
from hachi.core.og_pack import (
    ScheduleBudget as ScheduleBudget,
)
from hachi.core.og_pack import (
    parse_pack_toml as parse_pack_toml,
)
from hachi.core.og_pack import (
    render_consent_sheet as render_consent_sheet,
)
from hachi.gateway.tool_registration import ToolSpec as ToolSpec

# ---------------------------------------------------------------------------
# Semver introspection
# ---------------------------------------------------------------------------

SDK_VERSION: str = "0.1.0"
"""Current SDK semver string.  Updated on each breaking-or-additive change
to the stable surface, independently of the app release."""


def sdk_version() -> str:
    """Return the SDK version string."""
    return SDK_VERSION


def is_compatible(required_sdk_version: str) -> bool:
    """Return True if the running SDK satisfies *required_sdk_version*.

    Major versions must match exactly; the running minor must be >= the required
    minor. Patch is not considered. Returns False for a malformed argument.

    Args:
        required_sdk_version: Minimum SDK version string (``"MAJOR.MINOR[.PATCH]"``).

    Returns:
        True when the major versions match and the running minor is >= the
        required minor. False on a major mismatch, a lower minor, or an
        unparsable argument.

    Example::

        from hachi.sdk import is_compatible
        assert is_compatible("0.1.0"), "This pack requires SDK ≥ 0.1.0"
    """

    def _parse(v: str) -> tuple[int, int] | None:
        parts = v.split(".")
        try:
            return (int(parts[0]), int(parts[1]))
        except (IndexError, ValueError):
            return None

    want = _parse(required_sdk_version)
    have = _parse(SDK_VERSION)
    if want is None or have is None:
        return False
    return have[0] == want[0] and have[1] >= want[1]
