"""BB-103: Google Workspace tools were mounted twice during ``boot_agent`` — once via
``TOOL_SPECS`` *with* the session's ``action_approver``, then again via the capability
loop *without* it. ``ToolRegistry.register`` overwrites handlers last-wins
(``core/loop.py:197``), so the approver-less mount's handler survived.

This is an AVAILABILITY defect, not a privilege escalation — read
docs/audit/2026-07-19/tasks/CORRECTIONS.md sections R3/R4 before touching this file.
``GoogleHTTPClient.require_approval`` fails SAFE (denies) when ``self.approver is
None`` (``integrations/google/client.py:288-292``), so the double mount permanently
BLOCKED every destructive Google action (``send_email``, ``trash_file``, …) for
exactly the users who fully configured Google. It did not open a door — it welded one
shut. And ``ToolRegistry.get_schemas`` de-dupes on read (first-wins,
``core/loop.py:330-337``), so the Anthropic API never saw a duplicate-name error; the
residual harm from the schema side was unbounded ``_schemas`` list growth, not an API
failure.

See docs/audit/2026-07-19/tasks/T-35-google-double-mount.md.
"""

from __future__ import annotations

from hachi.core.loop import ToolRegistry
from hachi.gateway.orchestrator import _build_capability_registry
from hachi.integrations.google.client import GoogleHTTPClient


class _AlwaysApprove:
    """Minimal ActionApprover stand-in (structural match to the Protocol)."""

    def require(self, action: str, summary: str, detail: str = "") -> tuple[bool, str]:
        return (True, "approved by test")


def test_google_capability_does_not_mount_tools() -> None:
    """The `google` descriptor must opt out of the capability loop's mount step.

    `TOOL_SPECS` (orchestrator.py, ``register_google_tools(..., approver=action_approver)``)
    is the single mount path. The capability-loop mount must be skipped for `google`
    specifically — `slack`/`notion` (which have only ever had one mount path) must be
    unaffected, and `google` must still be yielded by `iter_enabled()` because
    `install_optional_skill_pack()` and `status_snapshot()` both depend on it.
    """
    registry = _build_capability_registry(
        {
            "capabilities": {
                "google": {"enabled": True, "configured": True},
                "slack": {"enabled": True, "configured": True},
            }
        }
    )
    google = registry.get("google")
    assert google is not None
    # Still enabled: install_optional_skill_pack() and status_snapshot() depend on it.
    assert google.id in {d.id for d in registry.iter_enabled()}
    # But it must not mount tools a second time — TOOL_SPECS does that, with the approver.
    assert google.mounts_tools is False

    slack = registry.get("slack")
    assert slack is not None and slack.mounts_tools is True, (
        "the opt-out must be per-descriptor, not a change to the default — slack/notion "
        "have only ever had one mount path and must be unaffected"
    )


def test_google_destructive_action_is_permitted_once_mounted_only_via_tool_specs() -> None:
    """The real availability property users lost (CORRECTIONS.md R4).

    Proves the actual behaviour, not a proxy for it: a destructive Google action must be
    PERMITTED end-to-end through the REAL ``GoogleHTTPClient.require_approval`` gate once
    Google is mounted exactly once (via TOOL_SPECS, with the approver) — not merely that
    "some object is not None".

    ``google.mounts_tools`` is read with a ``True`` default (via ``getattr``) specifically
    so this test fails *for the true reason* pre-fix — an actual DENIAL through the real
    fail-safe gate — rather than an incidental ``AttributeError`` about a missing dataclass
    field (that failure mode is already covered by
    ``test_google_capability_does_not_mount_tools`` above).
    """
    registry = _build_capability_registry(
        {"capabilities": {"google": {"enabled": True, "configured": True}}}
    )
    google = registry.get("google")
    assert google is not None

    approver = _AlwaysApprove()

    # Path A — TOOL_SPECS: register_google_tools(..., approver=action_approver). Always runs.
    gated_client = GoogleHTTPClient(scopes=["openid"])
    gated_client.approver = approver

    reg = ToolRegistry()
    schema = {
        "name": "google_send_email",
        "input_schema": {"type": "object", "properties": {}},
    }
    reg.register(schema, lambda: gated_client.require_approval("send_email", "send a mail"))

    # Path B — the capability loop. Pre-fix (no `mounts_tools` guard, or a guard that
    # defaults to True) this mount also ran and its approver-less client overwrote the
    # handler above (ToolRegistry.register is last-wins, core/loop.py:197).
    if getattr(google, "mounts_tools", True):
        ungated_client = GoogleHTTPClient(scopes=["openid"])  # hub built with no approver
        reg.register(schema, lambda: ungated_client.require_approval("send_email", "send a mail"))

    allowed, reason = reg._tools["google_send_email"]()
    assert allowed is True, (
        f"destructive Google action was DENIED: {reason!r} — the approver-less "
        "capability-loop mount overwrote the gated TOOL_SPECS mount. This is an "
        "AVAILABILITY bug (fails closed): a missing approver denies, it does not permit."
    )

    # Secondary, weaker invariant (CORRECTIONS.md R3): the schema list must not grow
    # unboundedly from the second mount either.
    assert len(reg._schemas) == 1, f"schema list grew to {len(reg._schemas)} entries"
