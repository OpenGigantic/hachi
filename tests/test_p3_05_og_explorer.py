"""P3-05: OG Explorer TUI screen tests.

Tests:
  1. OGExplorerScreen class exists and is a SnapshotScreen subclass.
  2. OGExplorerScreen is wired to 'og_explorer' MODES key.
  3. format_og_explorer handles empty data gracefully.
  4. format_og_explorer uses pass-depth counters.
  5. snapshot_og_explorer returns expected keys.
  6. format_memory_diffs renders correct column count.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. OGExplorerScreen is a SnapshotScreen subclass
# ---------------------------------------------------------------------------


def test_og_explorer_screen_is_snapshot_screen() -> None:
    from tokoboros.tui.app import OGExplorerScreen, SnapshotScreen

    assert issubclass(OGExplorerScreen, SnapshotScreen)


# ---------------------------------------------------------------------------
# 2. OGExplorerScreen is wired to 'og_explorer' in MODES
# ---------------------------------------------------------------------------


def test_og_explorer_in_modes() -> None:
    from tokoboros.tui.app import OGExplorerScreen, TokoborosTextualApp

    assert "og_explorer" in TokoborosTextualApp.MODES
    assert TokoborosTextualApp.MODES["og_explorer"] is OGExplorerScreen


def test_og_explorer_binding_has_action_handler() -> None:
    """Regression: the ctrl+9 / f9 binding referenced ``mode_og_explorer`` but the
    matching ``action_mode_og_explorer`` method was missing, so the key (and the
    footer click) silently did nothing. Every bound mode action must resolve."""
    from tokoboros.tui.app import TokoborosTextualApp

    actions = {b.action for b in TokoborosTextualApp.BINDINGS if b.action.startswith("mode_")}
    for action in actions:
        assert hasattr(TokoborosTextualApp, f"action_{action}"), (
            f"binding action {action!r} has no action_{action} handler"
        )
    assert hasattr(TokoborosTextualApp, "action_mode_og_explorer")


# ---------------------------------------------------------------------------
# 3. format_og_explorer handles empty data
# ---------------------------------------------------------------------------


def test_format_og_explorer_empty_data() -> None:
    from tokoboros.tui.formatters import format_og_explorer

    result = format_og_explorer({})
    assert result is not None


# ---------------------------------------------------------------------------
# 4. format_og_explorer uses pass-depth counters
# ---------------------------------------------------------------------------


def test_format_og_explorer_counters() -> None:
    """Counters must appear in the rendered output (Rich Group)."""
    from io import StringIO

    from rich.console import Console

    from tokoboros.tui.formatters import format_og_explorer

    data = {
        "skills": [],
        "counters": {"pass1_search": 42, "pass2_peek": 7, "pass3_resolve": 1},
        "node_count": 0,
    }
    result = format_og_explorer(data)

    out = StringIO()
    console = Console(file=out, width=120, no_color=True)
    console.print(result)
    text = out.getvalue()
    assert "42" in text, "pass1 count must appear in output"
    assert "Pass 1" in text or "pass1" in text.lower()


# ---------------------------------------------------------------------------
# 5. snapshot_og_explorer returns expected keys
# ---------------------------------------------------------------------------


def test_snapshot_og_explorer_keys() -> None:
    """AgentSession.snapshot_og_explorer() must expose skills, counters, node_count."""
    from unittest.mock import MagicMock

    from tokoboros.gateway.orchestrator import AgentSession

    mock_engine = MagicMock()
    mock_engine.get_skill_inventory.return_value = [
        {"name": "weather_skill", "role": "skill", "keyword_count": 4,
         "use_count": 3, "last_used": None}
    ]
    mock_engine.get_usage_counters.return_value = {
        "pass1_search": 10,
        "pass2_peek": 5,
        "pass3_resolve": 2,
    }
    mock_engine.get_all_nodes.return_value = [MagicMock()]

    session = AgentSession.__new__(AgentSession)
    session.skill_engine = mock_engine

    result = session.snapshot_og_explorer()
    assert "skills" in result
    assert "counters" in result
    assert "node_count" in result
    assert result["node_count"] == 1
    assert result["counters"]["pass1_search"] == 10


# ---------------------------------------------------------------------------
# 6. format_memory_diffs renders correct column count
# ---------------------------------------------------------------------------


def test_format_memory_diffs_column_count() -> None:
    from tokoboros.tui.formatters import format_memory_diffs

    table = format_memory_diffs([])
    # Table has 5 columns: Kind, Node A, Node B, Confidence, Reason
    assert len(table.columns) == 5
