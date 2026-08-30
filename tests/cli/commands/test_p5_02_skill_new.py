"""P5-02 tests — hachi skill new scaffold command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hachi.cli.commands.skill import skill_app
from hachi.og_engine.parser import parse_og_file

runner = CliRunner()


def test_skill_new_creates_file_at_default_path(tmp_path: Path) -> None:
    """Running `skill new <name>` without --output creates <name>.og in cwd."""
    result = runner.invoke(skill_app, ["new", "hello-world"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    default_file = Path("hello-world.og")
    assert default_file.exists(), "Expected hello-world.og to be created in cwd"
    assert "Created" in result.output

    # Clean up
    default_file.unlink(missing_ok=True)


def test_skill_new_output_flag_respects_path(tmp_path: Path) -> None:
    """--output directs the file to the specified path."""
    out = tmp_path / "custom" / "my_skill.og"
    out.parent.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        skill_app, ["new", "my-skill", "--output", str(out)], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert out.exists(), f"Expected file at {out}"
    assert "Created" in result.output
    # Rich may line-wrap long paths; check the filename is somewhere in the output
    assert out.name in result.output


def test_skill_new_created_message_path_is_not_word_wrapped(tmp_path: Path, monkeypatch) -> None:
    """BB-180: the 'Created <path>' message must never split the filename itself.

    ``console = Console()`` in ``hachi/cli/commands/skill.py`` has no real
    terminal attached under ``CliRunner.invoke()``, so Rich falls back to a
    small fixed width (its 80-column default, adjusted by -1 on a "legacy
    Windows" console -- see ``rich.console.Console.size``). A file path has no
    internal whitespace, so Rich's word-wrapper treats the whole path as one
    "word" and, when it doesn't fit the console width, hard-breaks it
    character-by-character -- including, unluckily, sometimes mid-filename.
    That is exactly what ubuntu-latest's CI runner hit:
    ``test_skill_new_output_flag_respects_path``'s ``out.name`` ("my_skill.og")
    stopped being a contiguous substring of ``result.output`` because the fold
    landed inside it. This dev box's own tmp_path length happens to avoid that
    fold point, so simulate it directly with an explicitly narrow console
    width (smaller than the filename itself, which forces a split regardless
    of the surrounding path's length or the host OS) instead of depending on
    hitting the right tmp_path length by chance.
    """
    from hachi.cli.commands.skill import console as skill_console

    # Narrower than "my_skill.og" (11 chars) -- guarantees a fold lands inside
    # the filename no matter where in the wrapped path it starts.
    monkeypatch.setattr(skill_console, "_width", 8)

    out = tmp_path / "custom" / "my_skill.og"
    out.parent.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        skill_app, ["new", "my-skill", "--output", str(out)], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert out.name in result.output, f"filename was split by console word-wrap: {result.output!r}"


def test_skill_new_domain_appears_in_output(tmp_path: Path) -> None:
    """--domain value is written into the generated .og file."""
    out = tmp_path / "domain-test.og"
    result = runner.invoke(
        skill_app,
        ["new", "domain-test", "--output", str(out), "--domain", "productivity"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    assert "productivity" in content, "domain should appear in the generated file"


def test_skill_new_generates_parseable_og_file(tmp_path: Path) -> None:
    """The generated file must be parseable by parse_og_file without error."""
    out = tmp_path / "parseable-skill.og"
    result = runner.invoke(
        skill_app,
        ["new", "parseable-skill", "--output", str(out), "--domain", "test", "--author", "ci"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out.exists()

    og = parse_og_file(str(out))
    assert og.meta.title == "Parseable Skill"
    assert og.meta.domain == "test"
    assert len(og.nodes) == 1
    node = og.nodes[0]
    assert node.name == "parseable-skill"
    assert node.role == "skill"
    assert node.dense != ""
    assert len(node.steps) >= 1
    assert len(og.index) == 1
    assert og.index[0].node_id == "parseable-skill"
