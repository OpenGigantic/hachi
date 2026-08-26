"""Regression tests for T-58 — `IntegrationRuntime` must not be a plaintext
credential source.

Before this fix, `IntegrationRuntime.load_env_node` split `KEY=VALUE` lines out
of a `role=env` node's `::dense` block into a flat in-memory dict
(`self._env`), `get_credential` returned values from that dict raw, and
`_setup_smtp` passed them straight to `smtplib.login()` — no vault, no
redaction, no secret store. `boot_agent` loads EVERY `.og` file under the
skills dir at every session boot (`gateway/boot/environment.py:build_integrations`),
including pack-installed ones, so a `role=env` node anywhere was a live
cleartext-credential path. See docs/audit/2026-07-19/tasks/T-58-declarative-
integrations-consolidation.md section 7 for the exact sketch this file adapts.

`OGNode` is a pydantic BaseModel (`og_engine/parser.py:130`) and both `dense`
(`:163`) and `config` (`:203`) are real constructor kwargs with defaults, so
hand-constructing nodes below (rather than routing through the parser) is
safe -- verified by reading the model before writing this test.
"""

from __future__ import annotations

from hachi.core.integrations import IntegrationRuntime
from hachi.og_engine.parser import OGNode


def _env_node() -> OGNode:
    """A role=env node carrying a secret in its ::dense block."""
    return OGNode(
        name="creds",
        role="env",
        dense="GMAIL_ADDRESS=me@example.com\nGMAIL_APP_PASSWORD=hunter2\n",
    )


def test_role_env_node_is_not_a_credential_source() -> None:
    """A password in a .og file must never become a live credential.

    Regression: load_env_node() split KEY=VALUE out of a role=env node's ::dense
    block into a plain dict (`self._env`), and _setup_smtp passed the result
    straight to smtplib.login(). No vault, no redaction, no secret store — and
    the boot path loads EVERY .og under the skills dir, including pack-installed
    ones.
    """
    runtime = IntegrationRuntime()
    runtime.load_env_node(_env_node())

    leaked = runtime.get_credential("GMAIL_APP_PASSWORD")
    assert leaked is None, (
        f"role=env node leaked {leaked!r} into the runtime's credential store. "
        "Credentials must come from the CredentialVault, not from a .og file."
    )


def test_integration_runtime_emits_no_stdout(capsys) -> None:
    """Integration setup must log, not print (TUI-safe, config-controlled)."""
    runtime = IntegrationRuntime()
    node = OGNode(name="thing", role="integration", config={"type": "nonsense"})
    runtime.setup_integration(node)

    captured = capsys.readouterr()
    assert captured.out == "", (
        f"IntegrationRuntime wrote to stdout: {captured.out!r}. "
        "Stray stdout corrupts the Textual TUI render and escapes the logging config."
    )
