"""Regression tests for scripts/lint_skills.py's T-72 reserved-tag rule.

The .og executable surface -- ``::config``/``::tool``/``::api`` block types and
``role=env``/``role=integration``/``role=mcp`` node roles -- is reserved for
first-party skills under ``src/hachi/default_skills/`` (docs/DECISIONS.md,
docs/reference/objectgraph-spec.md section 4.7 "Runtime & Integration Tags").
These tags let an installed .og ingest credentials, call arbitrary HTTP
endpoints, and start an MCP subprocess at session boot
(``gateway/orchestrator.py``'s three ``get_nodes_by_role`` scans). A
community/third-party .og that declares any of the six is a lint ERROR.

This is the regression lock for the *linter*, not the runtime -- see
docs/bug-bank.md BB-133. Before this fix, ``scripts/lint_skills.py`` had no
concept of "reserved" tags or "first-party" at all, so a community pack using
``role=mcp`` would lint clean.
"""

from __future__ import annotations

from pathlib import Path

from scripts.lint_skills import check_reserved_tags, is_first_party_skill, main

_DEFAULT_SKILLS_ROOT = Path(__file__).parent.parent / "src" / "hachi" / "default_skills"

# A community-style pack using ::api (the declarative, REST-calling block type).
_API_OG = """::meta
  title: Reserved Tag Test (api)
  version: 0.1.0

::index
  # node-id  | type  | scope | conf | keywords
  uses_api   | skill | all   | 1.0  | alpha

::node[uses_api]
->[:role=skill]
->[:keywords=alpha]

::api[call_out]
method=GET
endpoint=https://example.invalid/ping
description=test fixture only, never called

::dense
test node
"""

# A community-style pack using role=mcp + ::config (the imperative, subprocess
# starting role). Mirrors the real MCPManager.setup_from_node ::config keys
# (command/args) documented in core/mcp_client.py.
_MCP_ROLE_OG = """::meta
  title: Reserved Tag Test (role=mcp)
  version: 0.1.0

::index
  # node-id   | type | scope | conf | keywords
  spawn_mcp   | mcp  | all   | 1.0  | beta

::node[spawn_mcp]
->[:role=mcp]
->[:keywords=beta]

::config
command=npx
args=-y some-mcp-server

::dense
test node
"""

# A community-style pack using role=integration + ::config + ::tool together --
# the realistic pairing from core/integrations.py (setup_integration +
# _build_tool_schema). Exercises multiple simultaneous violations.
_INTEGRATION_OG = """::meta
  title: Reserved Tag Test (role=integration)
  version: 0.1.0

::index
  # node-id      | type        | scope | conf | keywords
  send_via_smtp  | integration | all   | 1.0  | gamma

::node[send_via_smtp]
->[:role=integration]
->[:keywords=gamma]

::config
type=smtp
host=smtp.example.invalid
port=587

::tool
name=send_email
description=Send an email
params=to|recipient, subject|subject, body|body

::dense
test node
"""

# No reserved tags anywhere -- must stay clean regardless of directory.
_CLEAN_OG = """::meta
  title: Clean Test
  version: 0.1.0

::index
  # node-id | type  | scope | conf | keywords
  clean     | skill | all   | 1.0  | delta

::node[clean]
->[:role=skill]
->[:keywords=delta]

::dense
nothing reserved here
"""


def test_first_party_default_skill_using_api_is_not_flagged() -> None:
    """A bundled skill under default_skills/ may use ::api -- it is first-party."""
    currency = _DEFAULT_SKILLS_ROOT / "general" / "currency.og"
    assert currency.exists(), f"fixture skill missing: {currency}"

    assert is_first_party_skill(currency) is True
    assert check_reserved_tags(currency) == []


def test_community_pack_using_api_block_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "community_pack.og"
    path.write_text(_API_OG, encoding="utf-8")

    assert is_first_party_skill(path) is False
    violations = check_reserved_tags(path)
    assert violations, "a non-first-party .og declaring ::api must be rejected"
    assert any("api" in v for v in violations)


def test_community_pack_using_reserved_role_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "community_pack.og"
    path.write_text(_MCP_ROLE_OG, encoding="utf-8")

    violations = check_reserved_tags(path)
    assert violations, "a non-first-party .og declaring role=mcp must be rejected"
    assert any("mcp" in v for v in violations)


def test_community_pack_using_config_and_tool_reports_both(tmp_path: Path) -> None:
    path = tmp_path / "community_pack.og"
    path.write_text(_INTEGRATION_OG, encoding="utf-8")

    violations = check_reserved_tags(path)
    codes = " ".join(violations)
    assert "config" in codes
    assert "tool" in codes
    assert "integration" in codes
    assert len(violations) >= 3, violations


def test_community_pack_without_reserved_tags_is_clean(tmp_path: Path) -> None:
    path = tmp_path / "community_pack.og"
    path.write_text(_CLEAN_OG, encoding="utf-8")

    assert check_reserved_tags(path) == []


def test_cli_rejects_a_throwaway_community_pack(tmp_path: Path, capsys) -> None:
    """End-to-end: the real scripts/lint_skills.py entry point rejects a
    throwaway .og outside default_skills/ that declares role=mcp -- T-72 section
    10's "highest-value manual check", automated so it can't silently regress.
    """
    path = tmp_path / "community_pack.og"
    path.write_text(_MCP_ROLE_OG, encoding="utf-8")

    exit_code = main([str(path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "role=mcp" in out


def test_cli_default_invocation_still_lints_only_default_skills() -> None:
    """No-arg invocation (the preflight-blocking call) must keep scanning
    exactly the bundled default_skills/ tree -- all first-party, so the new
    rule never fires there and the exit code contract is unchanged."""
    exit_code = main([])
    assert exit_code == 0
