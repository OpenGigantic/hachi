"""Tests for P5-05: tokoboros memoir CLI command."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_memoir_imports_without_error() -> None:
    """The memoir command is importable from the CLI module."""
    from tokoboros.cli.main import memoir  # noqa: F401 — import is the test

    assert callable(memoir)


def test_memoir_empty_memory_dir(tmp_path: Path, monkeypatch) -> None:
    """With an empty memory dir, memoir exits cleanly (no crash, prints message)."""
    import tokoboros.cli.main as cli_main
    from tokoboros.cli.main import app
    from tokoboros.core.paths import TokoborosPaths

    # memory_dir intentionally NOT created — simulates missing dir
    fake_paths = TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )
    # Patch the reference used inside cli/main.py
    monkeypatch.setattr(cli_main, "get_tokoboros_paths", lambda: fake_paths)

    runner = CliRunner()
    result = runner.invoke(app, ["memoir", "--year", "2025"])
    # Should exit cleanly (exit code 0) and mention no files
    assert result.exit_code == 0
    assert "No memory files found" in result.output


def test_memoir_json_output_contains_year(tmp_path: Path, monkeypatch) -> None:
    """With seeded memory dir, --json output contains the target year."""
    import tokoboros.cli.main as cli_main
    from tokoboros.cli.main import app
    from tokoboros.core.paths import TokoborosPaths

    # Create memory dir with a journal file containing nodes for 2025
    memory_dir = tmp_path / "data" / "memory"
    memory_dir.mkdir(parents=True)

    journal_og = memory_dir / "journal-testbox.og"
    journal_og.write_text(
        """\
::meta
  title: Test Journal
  version: 1.0.0
  domain: memory|journal

::index
  # node-id     | type  | scope | conf | keywords
  my_fact        | skill | all   | 1.0  | test, fact, 2025
  my_decision    | skill | all   | 1.0  | test, decision, 2025

::node[my_fact]
->[:role=fact]
->[:keywords=test, fact]

::dense
A fact recorded in 2025 about the project.

::node[my_decision]
->[:role=decision]
->[:keywords=test, decision]

::dense
A key decision made in 2025 regarding architecture.
""",
        encoding="utf-8",
    )

    fake_paths = TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )
    # Patch the name used at call-site in cli/main.py
    monkeypatch.setattr(cli_main, "get_tokoboros_paths", lambda: fake_paths)

    runner = CliRunner()
    result = runner.invoke(app, ["memoir", "--year", "2025", "--json"])
    assert result.exit_code == 0, result.output

    # Parse the JSON from output
    raw = result.output.strip()
    parsed = json.loads(raw)
    assert parsed["year"] == 2025
    assert isinstance(parsed["facts"], list)
    assert isinstance(parsed["decisions"], list)


def test_memoir_output_writes_file(tmp_path: Path, monkeypatch) -> None:
    """--output flag writes the memoir to a file."""
    import tokoboros.cli.main as cli_main
    from tokoboros.cli.main import app
    from tokoboros.core.paths import TokoborosPaths

    memory_dir = tmp_path / "data" / "memory"
    memory_dir.mkdir(parents=True)

    # One node that references 2024
    journal_og = memory_dir / "journal-dev.og"
    journal_og.write_text(
        """\
::meta
  title: Dev Journal
  version: 1.0.0
  domain: memory|journal

::index
  # node-id   | type  | scope | conf | keywords
  event_2024   | skill | all   | 1.0  | event, 2024

::node[event_2024]
->[:role=event]
->[:keywords=event]

::dense
Released v1.0 in 2024.
""",
        encoding="utf-8",
    )

    fake_paths = TokoborosPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        legacy_dir=tmp_path / "legacy",
    )
    # Patch the name used at call-site in cli/main.py
    monkeypatch.setattr(cli_main, "get_tokoboros_paths", lambda: fake_paths)

    out_file = tmp_path / "memoir_2024.txt"
    runner = CliRunner()
    result = runner.invoke(app, ["memoir", "--year", "2024", "--output", str(out_file)])
    assert result.exit_code == 0, result.output
    assert out_file.exists(), "Output file was not created"
    content = out_file.read_text(encoding="utf-8")
    assert "2024" in content
