"""Regression tests for CockpitData's DEGRADED state (T-21 / BB-113).

``CockpitData``'s own docstring says methods NEVER raise — they always return
real or placeholder data. In practice that meant a crashed or unreachable
gateway (``client is not None``, but every call raises) rendered as a
perfectly healthy, populated cockpit: a fabricated pending approval to write
``~/Documents/report.md`` (with a working Allow button that would 404), a
fabricated spend figure, a fabricated audit log — and the only trace was a
``log.debug`` line invisible at the default log level.

This module locks the fix in at two levels:
  - the facade (``CockpitData``) — a failed live call must return an honest
    empty value and record a degraded reason, never a placeholder;
  - the view (``ApprovalsView``/``AuditView``/``ResourcesView``/
    ``DashboardView``) — the DEGRADED state must actually *render*
    differently from both the fabricated-populated state and the reassuring
    "nothing pending" empty state. Asserting only that a data method returns
    ``[]`` is not enough: a view could still silently show that as "all
    clear", which is exactly the kind of dead-surface-looks-alive bug this
    task exists to fix. The view-level tests below read what is actually
    rendered.

Deliberately a **separate module** from ``TestCockpitDataOffline`` in
``tests/test_cockpit_foundation.py``, which pins the *opposite* contract for
``client=None`` (deliberate offline mode — placeholders are correct there
and must keep working untouched).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from hachi.tui.cockpit.data import CockpitData
from hachi.tui.cockpit.views.approvals import ApprovalsView
from hachi.tui.cockpit.views.audit import AuditView
from hachi.tui.cockpit.views.dashboard import DashboardView
from hachi.tui.cockpit.views.resources import ResourcesView
from hachi.tui.cockpit.views.table import CockpitTable


def _run(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine synchronously for Textual pilot tests."""
    return asyncio.run(coro)


def _broken_client() -> MagicMock:
    """A GatewayClient double that raises on every call the cockpit makes."""
    client = MagicMock()
    client.list_approvals.side_effect = ConnectionError("gateway is down")
    client.permissions_audit.side_effect = ConnectionError("gateway is down")
    client.get_spend.side_effect = ConnectionError("gateway is down")
    client.active_jobs.side_effect = ConnectionError("gateway is down")
    client.list_sessions.side_effect = ConnectionError("gateway is down")
    client.get_config.side_effect = ConnectionError("gateway is down")
    client.subagent_reports.side_effect = ConnectionError("gateway is down")
    client.mcp_inventory.side_effect = ConnectionError("gateway is down")
    client.get_authoring_target.side_effect = ConnectionError("gateway is down")
    return client


# ---------------------------------------------------------------------------
# CockpitData facade — the four required regression tests (T-21 spec §7)
# ---------------------------------------------------------------------------


def test_failed_live_call_returns_empty_not_placeholder() -> None:
    """A dead gateway must never render as a healthy cockpit full of fake approvals.

    Regression: every read method converted an exception into placeholder_*() data,
    so an unreachable gateway showed a fabricated medium-risk pending write to
    ~/Documents/report.md with a working Allow button.
    """
    data = CockpitData(_broken_client())

    assert data.approvals() == []
    assert data.audit() == []
    # The specific fiction must be gone, not merely a different length.
    assert all(getattr(a, "id", None) != "req-001" for a in data.approvals())


def test_failed_live_call_records_a_degraded_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed live call is recorded so views can tell "empty" from "unknown"."""
    with caplog.at_level("WARNING"):
        data = CockpitData(_broken_client())
        data.approvals()

    assert data.degraded("approvals") is not None
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_offline_mode_still_returns_placeholders() -> None:
    """client=None is a deliberate offline mode, not a failure — keep it working."""
    data = CockpitData(None)
    assert len(data.approvals()) > 0


def test_recovered_call_clears_the_degraded_flag() -> None:
    """A gateway that comes back up must not stay marked degraded forever."""
    client = MagicMock()
    client.list_approvals.side_effect = ConnectionError("down")
    data = CockpitData(client)
    data.approvals()
    assert data.degraded("approvals") is not None

    client.list_approvals.side_effect = None
    client.list_approvals.return_value = []
    data.approvals()
    assert data.degraded("approvals") is None


# ---------------------------------------------------------------------------
# CockpitData facade — the trickier "trap" methods called out in the spec
# (aggregates with two placeholder returns, tuple-returning, honest-ish
# fallback already present). Each is a distinct failure shape, not just a
# repeat of the approvals() case above.
# ---------------------------------------------------------------------------


def test_trust_bar_degrades_not_placeholder() -> None:
    """trust_bar() must not mimic the placeholder's fake approval/cost numbers."""
    data = CockpitData(_broken_client())

    snap = data.trust_bar()

    # Placeholder values: approval_count=1, active_job_count=1, cost=0.0049.
    assert snap.approval_count == 0
    assert snap.active_job_count == 0
    assert snap.total_cost_usd == 0.0
    assert data.degraded("trust_bar") is not None


def test_dashboard_degrades_not_placeholder() -> None:
    """dashboard() must not mimic the placeholder's fabricated pending approval."""
    data = CockpitData(_broken_client())

    snap = data.dashboard()

    assert snap.pending_approvals == ()
    assert snap.active_jobs == ()
    assert snap.recent_sessions == ()
    assert data.degraded("dashboard") is not None


def test_mcp_degrades_to_two_empty_lists() -> None:
    """mcp() returns a 2-tuple; its honest empty is ([], []), not a single []."""
    data = CockpitData(_broken_client())

    servers, tools = data.mcp()

    assert servers == []
    assert tools == []
    assert data.degraded("mcp") is not None


def test_authoring_source_degrades_but_records_reason() -> None:
    """authoring_source() already had an honest-ish fallback string; it must
    now also record the degraded reason instead of staying silent."""
    data = CockpitData(_broken_client())

    src, diff = data.authoring_source("brain-main.og")

    assert isinstance(src, str)
    assert diff == []
    assert data.degraded("authoring_source") is not None


# ---------------------------------------------------------------------------
# View rendering — the real, user-visible property. A dead gateway must
# actually *render* its degraded state, not merely make a facade method
# return an empty collection that a view could still show as "all clear".
# ---------------------------------------------------------------------------


class _ApprovalsApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ApprovalsView()


class _AuditApp(App[None]):
    def compose(self) -> ComposeResult:
        yield AuditView()


class _ResourcesApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ResourcesView()


class _DashboardApp(App[None]):
    def compose(self) -> ComposeResult:
        yield DashboardView()


def test_approvals_view_renders_degraded_not_fabricated_row() -> None:
    """With a client that raises, ApprovalsView must show no rows and an
    explicit "can't reach Hachi" message — never the fabricated req-001 row,
    and never the reassuring "no approvals waiting" text (which reads as a
    healthy, idle cockpit rather than an unreachable one)."""

    async def _body() -> None:
        async with _ApprovalsApp().run_test() as pilot:
            view = pilot.app.query_one(ApprovalsView)
            view.populate(CockpitData(_broken_client()))
            await pilot.pause()

            table = view.query_one(CockpitTable)
            assert table._rows == [], f"expected no rows, got {table._rows!r}"
            assert "no approvals waiting" not in table._empty_text
            assert "can't reach" in table._empty_text

    _run(_body())


def test_audit_view_renders_degraded_not_empty() -> None:
    """AuditView must show an explicit unavailable message, not the ordinary
    "no audit events yet" text (which claims a known-empty log, not an
    unknown one)."""

    async def _body() -> None:
        async with _AuditApp().run_test() as pilot:
            view = pilot.app.query_one(AuditView)
            view.populate(CockpitData(_broken_client()))
            await pilot.pause()

            table = view.query_one(CockpitTable)
            assert table._rows == [], f"expected no rows, got {table._rows!r}"
            assert table._empty_text != "no audit events yet"
            assert "can't reach" in table._empty_text

    _run(_body())


def test_resources_view_renders_degraded_banner() -> None:
    """ResourcesView must not silently show $0.00 / representative token
    counts as though that were real spend data read from a live gateway."""

    async def _body() -> None:
        async with _ResourcesApp().run_test() as pilot:
            view = pilot.app.query_one(ResourcesView)
            view.populate(CockpitData(_broken_client()))
            await pilot.pause()

            cost_widget = pilot.app.query_one("#res-cost", Static)
            rendered = str(cost_widget.render()).lower()
            assert "can't reach" in rendered

    _run(_body())


def test_dashboard_view_renders_degraded_banner_not_fabricated_approval() -> None:
    """DashboardView's needs-you panel must not show the fabricated pending
    write to ~/Documents/report.md when the gateway is unreachable."""

    async def _body() -> None:
        async with _DashboardApp().run_test() as pilot:
            view = pilot.app.query_one(DashboardView)
            view.populate(CockpitData(_broken_client()))
            await pilot.pause()

            panel = pilot.app.query_one("#needs-you-panel", Static)
            rendered = str(panel.render())
            assert "report.md" not in rendered
            assert "can't reach" in rendered.lower()

    _run(_body())
