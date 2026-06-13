"""P2-09: Status strip, SpendScreen, and Ops timeline governance surfaces.

Tests:
- format_status_strip: state/jobs/approvals/spend rendering
- format_spend: token/cost breakdown rendering
- StatusStrip widget exists and has update_info
- SpendScreen class is mounted in MODES
- format_spend handles empty usage gracefully
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# format_status_strip tests
# ---------------------------------------------------------------------------


def test_status_strip_shows_state():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Working…", "status_kind": "running"})
    plain = text.plain
    assert "Working" in plain


def test_status_strip_shows_jobs():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Idle", "status_kind": "idle", "jobs": 3})
    assert "3" in text.plain


def test_status_strip_shows_approvals_with_bang():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Idle", "status_kind": "idle", "approvals": 2})
    assert "2!" in text.plain


def test_status_strip_no_bang_when_zero_approvals():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Idle", "status_kind": "idle", "approvals": 0})
    assert "0!" not in text.plain


def test_status_strip_shows_spend_when_cost_provided():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Idle", "status_kind": "idle", "cost": 0.0412})
    assert "$" in text.plain


def test_status_strip_omits_spend_when_no_cost():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Idle", "status_kind": "idle"})
    assert "$" not in text.plain


def test_status_strip_shows_warnings_when_nonzero():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({"status": "Idle", "status_kind": "idle", "warnings": 1})
    assert "warn" in text.plain


def test_status_strip_empty_info():
    from tokoboros.tui.formatters import format_status_strip

    text = format_status_strip({})
    assert text.plain  # must not crash


# ---------------------------------------------------------------------------
# format_spend tests
# ---------------------------------------------------------------------------

_SAMPLE_USAGE = {
    "input_tokens": 10_000,
    "output_tokens": 500,
    "cache_read_tokens": 5_000,
    "cache_creation_tokens": 1_000,
    "total_tokens": 16_500,
    "estimated_cost_usd": 0.0235,
    "call_count": 7,
}


def test_format_spend_empty_usage_returns_friendly_message():

    from tokoboros.tui.formatters import format_spend

    result = format_spend({})
    # Should return a Group-like renderable with a friendly message (no crash).
    assert result is not None


def test_format_spend_shows_input_tokens():
    from rich.console import Console

    from tokoboros.tui.formatters import format_spend

    result = format_spend(_SAMPLE_USAGE, model="claude-haiku-4-5")
    console = Console(record=True, width=120)
    console.print(result)
    output = console.export_text()
    assert "10,000" in output or "10000" in output  # input tokens


def test_format_spend_shows_cache_hit_rate():
    from rich.console import Console

    from tokoboros.tui.formatters import format_spend

    result = format_spend(_SAMPLE_USAGE, model="claude-haiku-4-5")
    console = Console(record=True, width=120)
    console.print(result)
    output = console.export_text()
    assert "cache-hit" in output.lower() or "cache" in output.lower()


def test_format_spend_shows_model_name():
    from rich.console import Console

    from tokoboros.tui.formatters import format_spend

    result = format_spend(_SAMPLE_USAGE, model="claude-haiku-4-5")
    console = Console(record=True, width=120)
    console.print(result)
    output = console.export_text()
    assert "claude-haiku-4-5" in output


def test_format_spend_shows_compaction_events():
    from rich.console import Console

    from tokoboros.tui.formatters import format_spend

    events = [
        {"name": "context.compacted", "timestamp": "2026-06-13T10:00:00",
         "payload": {"tokens": 1000}}
    ]
    result = format_spend(_SAMPLE_USAGE, model="claude-haiku-4-5", events=events)
    console = Console(record=True, width=120)
    console.print(result)
    output = console.export_text()
    assert "compaction" in output.lower() or "compact" in output.lower()


def test_format_spend_no_compaction_events_shows_none():
    from rich.console import Console

    from tokoboros.tui.formatters import format_spend

    result = format_spend(_SAMPLE_USAGE, model="claude-haiku-4-5", events=[])
    console = Console(record=True, width=120)
    console.print(result)
    output = console.export_text()
    assert "no compaction" in output.lower()


# ---------------------------------------------------------------------------
# StatusStrip widget
# ---------------------------------------------------------------------------


def test_status_strip_widget_exists():
    from tokoboros.tui.widgets import StatusStrip

    assert hasattr(StatusStrip, "update_info")


def test_status_strip_widget_is_static_subclass():
    from textual.widgets import Static

    from tokoboros.tui.widgets import StatusStrip

    assert issubclass(StatusStrip, Static)


# ---------------------------------------------------------------------------
# SpendScreen wiring
# ---------------------------------------------------------------------------


def test_spend_screen_in_modes():
    from tokoboros.tui.app import TokoborosTextualApp

    assert "spend" in TokoborosTextualApp.MODES


def test_spend_screen_has_ctrl3_binding():
    from tokoboros.tui.app import TokoborosTextualApp

    keys = {b.key for b in TokoborosTextualApp.BINDINGS}
    assert "ctrl+3" in keys


def test_spend_screen_action_exists():
    from tokoboros.tui.app import TokoborosTextualApp

    assert hasattr(TokoborosTextualApp, "action_mode_spend")


def test_memory_still_accessible_ctrl4():
    from tokoboros.tui.app import TokoborosTextualApp

    # After adding Spend at ctrl+3, Memory moved to ctrl+4
    bindings = {b.key: b.action for b in TokoborosTextualApp.BINDINGS}
    assert "mode_memory" in bindings.get("ctrl+4", "")
