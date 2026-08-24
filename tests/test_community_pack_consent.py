"""T-56 / CF-14 — community pack installs must go through the same consent
gate as ogpack installs.

Regression: `hachi skill pack install user/repo` copied .og files into
``<skills_dir>/packs/<name>/`` -- the SAME directory ``hachi ogpack install``
writes to, and the SAME directory ``SkillEngine.load_directory`` globs for
``**/*.og`` at boot -- with no manifest parse, no hash check, no scope
declaration and no confirmation prompt. `hachi ogpack install`, writing to
that identical directory, already required all four.

These tests assert the *observed outcome on disk* (and the printed sheet),
not a mock's call count: the whole finding is that files landed on disk
without a yes.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hachi.cli.commands.skill import skill_app


def _make_source_pack(tmp_path: Path) -> Path:
    src = tmp_path / "src-pack"
    src.mkdir()
    (src / "x.og").write_text("@node test\n::dense\nhello\n", encoding="utf-8")
    (src / "pack.json").write_text('{"name": "fake-pack", "version": "1.0.0"}', encoding="utf-8")
    return src


def test_community_pack_install_refuses_without_consent(tmp_path, monkeypatch) -> None:
    """Declining the consent prompt must leave nothing on disk.

    Regression: `hachi skill pack install` copied .og files into the SAME
    <skills_dir>/packs/ tree that the consent-gated ogpack installer uses, with
    no manifest parse, no hash check and no prompt.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.gateway.workspace.get_skills_dir", lambda: skills_dir)

    src = _make_source_pack(tmp_path)
    # "n" answers the consent prompt. Before the fix there is no prompt at all
    # and the input is simply ignored -- which is exactly what this test catches.
    result = CliRunner().invoke(skill_app, ["pack", "install", str(src)], input="n\n")

    installed = list(skills_dir.rglob("*.og"))
    assert installed == [], (
        f"expected no installed .og after declining, found {installed}. "
        "The community pack installer must not write to skills_dir without consent."
    )
    assert result.exit_code == 0


def test_community_pack_install_computes_integrity_hashes(tmp_path, monkeypatch) -> None:
    """An accepted install must leave a verifiable pack.toml behind."""
    from hachi.core.og_pack import get_installed_manifest, verify_pack_checksums

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.gateway.workspace.get_skills_dir", lambda: skills_dir)

    src = _make_source_pack(tmp_path)
    result = CliRunner().invoke(skill_app, ["pack", "install", str(src)], input="y\n")
    assert result.exit_code == 0, result.output

    manifest = get_installed_manifest("fake-pack", skills_dir)
    assert manifest is not None, "community install must leave an OgPackManifest"
    assert manifest.sha256s, "sha256s must be computed over the fetched .og files"
    assert verify_pack_checksums(skills_dir / "packs" / "fake-pack", manifest) == []


def test_community_pack_install_is_visible_to_ogpack_list(tmp_path, monkeypatch) -> None:
    """An installed community pack must be discoverable via list_ogpacks (`hachi pack list`).

    Regression (og_pack.py, not community_packs.py):
    `install_ogpack_from_dir` records install metadata by calling
    `_write_installed_meta`, which builds `.installed.toml` with a naive
    f-string (``f'source = "{source}"'``) instead of a TOML-safe writer. On
    Windows, `source` is a backslash-separated path (e.g.
    ``C:\\Users\\...\\my-pack``), and TOML basic strings treat backslash as
    an escape-sequence introducer, so the written file is frequently not
    valid TOML. `_read_installed_meta`'s ``except Exception: return {}``
    swallows the resulting `TOMLDecodeError` silently, so `list_ogpacks`
    treats a *successfully installed, checksum-verified* pack as if it were
    never installed at all. This pre-dates T-56 and reproduces identically
    through the existing `hachi pack install <local-dir>` (og_pack) command
    -- it is not specific to the community-pack path -- but T-56's own
    acceptance criteria require a community install to be list-visible, and
    every community pack necessarily has a local *source* (a fetch temp
    dir) by the time `install_ogpack_from_dir` is called, so this fix lives
    here.
    """
    from hachi.core.og_pack import list_ogpacks

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.gateway.workspace.get_skills_dir", lambda: skills_dir)

    src = _make_source_pack(tmp_path)
    result = CliRunner().invoke(skill_app, ["pack", "install", str(src), "--yes"])
    assert result.exit_code == 0, result.output

    packs = list_ogpacks(skills_dir)
    assert [p.id for p in packs] == ["fake-pack"], (
        "installed community pack must be visible to list_ogpacks (hachi pack list); "
        f"got {packs!r} -- see .installed.toml for a TOML-escaping bug in "
        "og_pack._write_installed_meta"
    )


def test_community_pack_install_yes_flag_skips_prompt(tmp_path, monkeypatch) -> None:
    """--yes must still allow a fully non-interactive (scripted) install.

    A real script has no tty attached, so no ``input=`` is passed at all here.
    If the prompt were not correctly bypassed this would hang or Abort.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.gateway.workspace.get_skills_dir", lambda: skills_dir)

    src = _make_source_pack(tmp_path)
    result = CliRunner().invoke(skill_app, ["pack", "install", str(src), "--yes"])

    assert result.exit_code == 0, result.output
    installed = skills_dir / "packs" / "fake-pack" / "x.og"
    assert installed.exists(), "hachi skill pack install --yes must still install non-interactively"


def test_community_pack_install_json_yes_stdout_is_pure_json(tmp_path, monkeypatch) -> None:
    """`--json` must still emit only parseable JSON on stdout.

    The consent sheet is human-readable text; if it were written to stdout in
    `--json` mode it would corrupt the output a script parses. It must still
    be shown somewhere (stderr) so an interactive user isn't asked to
    confirm something they can't see.
    """
    import json as jsonlib

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.gateway.workspace.get_skills_dir", lambda: skills_dir)

    src = _make_source_pack(tmp_path)
    result = CliRunner().invoke(skill_app, ["pack", "install", str(src), "--json", "--yes"])

    assert result.exit_code == 0, result.output
    data = jsonlib.loads(result.stdout)
    assert data["name"] == "fake-pack"
    assert data["file_count"] == 1
    assert "No integration scopes declared" in result.stderr


def test_community_pack_consent_sheet_declares_no_scopes(tmp_path, monkeypatch) -> None:
    """A community pack's consent sheet must not fabricate scopes it never declared.

    `pack.json` has no scope vocabulary (T-56 §5/§9): the honest sheet says
    "No integration scopes declared", not an invented scope list -- inventing
    one so the sheet "looks fuller" would itself be a fabricated security
    claim, the precise failure mode this task exists to stop.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.gateway.workspace.get_skills_dir", lambda: skills_dir)

    src = _make_source_pack(tmp_path)
    result = CliRunner().invoke(skill_app, ["pack", "install", str(src)], input="n\n")

    assert "No integration scopes declared" in result.output


# ---------------------------------------------------------------------------
# Consent-sheet rendering integrity: the scope id must reach the terminal.
#
# Found while verifying the "one consent implementation" acceptance criterion:
# render_consent_sheet/render_scope_diff correctly embed "[gmail.read]" in the
# returned string (og_pack.py is not the bug), but console.print(sheet) treats
# "[gmail.read]" as Rich markup, fails to recognise it as a style, and drops
# it -- so a real terminal shows only the prose description, never the scope
# id, which is the precise permission being granted. Every install path (both
# community and ogpack-native) now shares confirm_pack_consent_or_exit, so
# this defect's blast radius covers all of them.
# ---------------------------------------------------------------------------


def _make_ogpack_dir(pack_dir: Path, *, pack_id: str, semver: str, scopes: list[str]) -> Path:
    pack_dir.mkdir()
    scopes_toml = ", ".join(repr(s) for s in scopes)
    (pack_dir / "pack.toml").write_text(
        f'id = "{pack_id}"\nsemver = "{semver}"\nscopes = [{scopes_toml}]\n',
        encoding="utf-8",
    )
    (pack_dir / "a.og").write_text("::node[a]\n->[:keywords=a]\n\n::dense\na\n", encoding="utf-8")
    return pack_dir


def test_pack_install_consent_sheet_shows_scope_id_not_just_description(
    tmp_path, monkeypatch
) -> None:
    """The rendered consent sheet a user actually sees must show the scope id.

    Regression: `console.print(sheet)` interprets `[gmail.read]` as an
    unrecognised Rich markup tag and silently drops it. The description
    ("Read Gmail messages and labels") alone is not the same disclosure --
    the id is the precise, machine-checkable permission being granted.
    """
    from hachi.cli.main import app

    src = _make_ogpack_dir(
        tmp_path / "scoped-pack", pack_id="scoped-pack", semver="1.0.0", scopes=["gmail.read"]
    )
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.cli.commands.pack._get_skills_dir", lambda: skills_dir)

    result = CliRunner().invoke(app, ["pack", "install", str(src)], input="n\n")

    assert "gmail.read" in result.output, (
        "the consent sheet must show the scope id being requested, not just its "
        f"prose description; rendered output was: {result.output!r}"
    )


def test_pack_update_scope_diff_shows_scope_id_not_just_description(tmp_path, monkeypatch) -> None:
    """The rendered "widens permissions" diff must show the new scope's id.

    This is the exact call site the bug was found at (`ogpack_update`'s
    `console.print(diff)`, separate from the shared consent-sheet helper).
    """
    from hachi.cli.main import app

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr("hachi.cli.commands.pack._get_skills_dir", lambda: skills_dir)

    v1 = _make_ogpack_dir(tmp_path / "v1", pack_id="widening-pack", semver="1.0.0", scopes=[])
    v2 = _make_ogpack_dir(
        tmp_path / "v2", pack_id="widening-pack", semver="2.0.0", scopes=["gmail.read"]
    )

    runner = CliRunner()
    install_result = runner.invoke(app, ["pack", "install", str(v1), "--yes"])
    assert install_result.exit_code == 0, install_result.output

    update_result = runner.invoke(app, ["pack", "update", str(v2)], input="n\n")

    assert "gmail.read" in update_result.output, (
        "the scope-widening diff must show the new scope's id, not just its "
        f"prose description; rendered output was: {update_result.output!r}"
    )
