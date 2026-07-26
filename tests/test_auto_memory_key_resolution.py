"""T-11 / BB-100 regression tests — auto-memory API key resolution.

Background fact extraction ("auto-memory") was gated TWICE on the literal
``ANTHROPIC_API_KEY`` environment variable:

  1. ``core/memory_vault.py`` ``_get_llm_client()`` -- the client factory the
     durable job path (``job_handlers.build_job_context``) uses whenever no
     ``_llm_client_factory`` is injected (i.e. every real production job).
  2. ``core/memory.py`` ``extract_facts()`` -- re-checked the SAME env var even
     though it already receives a resolved ``llm_client`` argument from both of
     its callers, each of which already gates on ``client is None`` before
     calling it.

Neither gate matched the chat path's key resolution
(``core/llm.py create_llm_client`` -> secret store, then ``_resolve_api_key``:
explicit -> env var -> project ``.env``), so any user whose Anthropic key lived
in the OS keyring/secret store or a project ``.env`` file (rather than a
literal shell-exported env var) got working chat and silent zero auto-memory.

This is a **recurrence**: BB-40 documented three root causes for the same
overall symptom, and its recorded Fix claims cause #2 (this exact env-var
gate) was changed to ``if llm_client is None`` -- but ``git log -L
218,228:src/hachi/core/memory.py`` shows exactly one commit touching those
lines (the original ``04f2f90`` feature commit); the change was never
applied. See the corrected BB-40 entry and the new BB-100 entry in
``docs/bug-bank.md``.

Separately: when a durable job handler degrades gracefully (vault or LLM
client unresolvable) it returns a ``"skipped:<reason>"`` string instead of
raising -- deliberately, to avoid a FAILED-job restart retry loop
(``job_handlers.py``). ``jobs/queue.py`` mapped every non-raising handler
outcome to a bare ``job.completed`` event, so a skip was indistinguishable
from a real extraction to anything outside the handler. This file also locks
that fix (a distinct ``job.skipped`` event carrying the reason).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    """The whole point of every test in this file: the key must NOT be in os.environ.

    ``_get_llm_client`` memoises its result into a module global
    (``memory_vault._llm_client_instance``) -- reset it before every test here or
    the tests become order-dependent (a client built by an earlier test would be
    returned unchanged by a later one that expects ``None``).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import hachi.core.memory_vault as mv

    monkeypatch.setattr(mv, "_llm_client_instance", None, raising=False)


def _patch_secret_store(monkeypatch: pytest.MonkeyPatch, key_value: str | None) -> None:
    """Patch ``create_default_secret_store`` to return a stub whose ``.get()``
    always returns ``key_value``.

    Isolates a test from whatever the developer's own OS keyring/secret store
    may actually contain (this repo's dev machine may well have a real
    ANTHROPIC_API_KEY stored under the "hachi" service name) so "resolved via
    .env" / "resolved via nothing" tests are not accidentally made to pass (or
    fail) by a real credential rather than the mechanism under test.
    """
    import hachi.core.secret_store as secret_store_module

    class _Store:
        def get(self, name: str) -> str | None:
            return key_value

    monkeypatch.setattr(
        secret_store_module, "create_default_secret_store", lambda *a, **kw: _Store()
    )


# ---------------------------------------------------------------------------
# Gate 2 -- core/memory_vault.py _get_llm_client()
# ---------------------------------------------------------------------------


def test_memory_llm_client_resolves_key_from_project_dotenv(tmp_path, monkeypatch):
    """A key that lives ONLY in a project .env (never exported to os.environ,
    and absent from the secret store) must still produce a working client --
    the same key the chat path (``create_llm_client``) accepts via
    ``_resolve_api_key``.
    """
    _patch_secret_store(monkeypatch, None)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-test-not-real\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # _resolve_api_key reads Path.cwd() / ".env"
    # CF-17/T-67 (BB-2): the .env fallback is gated by HACHI_DISABLE_DOTENV_AUTOLOAD,
    # which conftest.py sets to "1" suite-wide for hermeticity. This test's subject IS
    # that fallback, so it must opt back in explicitly — matching production, where
    # the flag is unset.
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "0")

    from hachi.core.memory_vault import _get_llm_client

    client = _get_llm_client()
    assert client is not None, (
        "auto-memory refused to build a client although the key is resolvable via .env — "
        "the same key the chat path (create_llm_client) accepts"
    )


def test_memory_llm_client_resolves_key_from_secret_store(tmp_path, monkeypatch):
    """A key that lives ONLY in the secret store (no env var, no project .env)
    must also produce a client -- the other half of the chat-path resolution
    chain that a literal ``os.environ[...]`` check can never see.
    """
    monkeypatch.chdir(tmp_path)  # empty scratch dir: no .env to leak the key in via the wrong path
    _patch_secret_store(monkeypatch, "sk-test-not-real")

    from hachi.core.memory_vault import _get_llm_client

    client = _get_llm_client()
    assert client is not None, (
        "auto-memory refused to build a client although the key is resolvable via the "
        "secret store — the same store create_llm_client checks first"
    )


def test_memory_llm_client_returns_none_when_no_key_resolvable_anywhere(tmp_path, monkeypatch):
    """No env var, no project .env, and an empty secret store => None, not a
    crash -- and the acceptance criterion that the warning names all three
    resolution sources (not just "ANTHROPIC_API_KEY not set?").
    """
    _patch_secret_store(monkeypatch, None)
    monkeypatch.chdir(tmp_path)  # empty scratch dir: no .env

    import hachi.core.memory_vault as mv

    warnings: list[str] = []
    monkeypatch.setattr(
        mv.logger, "warning", lambda msg, *a, **kw: warnings.append(msg % a if a else msg)
    )

    client = mv._get_llm_client()
    assert client is None
    assert warnings, "no warning was logged when no Anthropic key was resolvable anywhere"
    combined = " ".join(warnings).lower()
    assert "secret store" in combined
    assert "anthropic_api_key" in combined
    assert ".env" in combined


# ---------------------------------------------------------------------------
# Gate 1 -- core/memory.py extract_facts()
# ---------------------------------------------------------------------------


def test_extract_facts_does_not_gate_on_the_env_var():
    """A working injected ``llm_client`` must be used even when
    ``ANTHROPIC_API_KEY`` is absent from ``os.environ`` -- BB-40's recorded fix
    for this exact gate ("changed... to `if llm_client is None`") was never
    actually applied; this is what proves it now has been.

    Adapted from the spec's §7 sketch: the sketch has ``_Client.generate``
    *raise* ``AssertionError`` to mark "we reached the LLM". Doing that
    literally makes the post-fix run FAIL too (the raise propagates
    uncaught out of ``extract_facts`` and the test), never reaching the
    ``assert result != ...`` line at all -- so the test could never go green.
    Returning a valid, empty extraction response and recording a
    ``called`` flag proves the same fact (the gate let a real ``.generate()``
    call through) while leaving a clean assertion to run afterward.
    """
    from hachi.core.llm import LLMResponse, TokenUsage
    from hachi.core.memory import extract_facts

    class _Client:
        model = "claude-haiku-4-5"

        def __init__(self) -> None:
            self.called = False

        def generate(self, *a, **kw):
            self.called = True
            return LLMResponse(
                content='{"user_facts": [], "workspace_facts": [], "agent_facts": []}',
                tool_calls=[],
                usage=TokenUsage(),
            )

    class _Vault:
        def upsert_fact(self, *a, **kw):
            return "id"

        def upsert_user_fact(self, *a, **kw):
            return "id"

        def upsert_agent_fact(self, *a, **kw):
            return "id"

    client = _Client()
    result = extract_facts(
        "a long enough exchange to pass the length gate",
        vault=_Vault(),
        llm_client=client,
        memory_writer=None,
    )
    assert result != "skipped:no_api_key", (
        "extract_facts refused a working injected client because ANTHROPIC_API_KEY "
        "was not in os.environ (BB-40's documented fix was never applied)"
    )
    assert client.called, (
        "extract_facts returned without ever calling llm_client.generate() -- "
        "it must have taken some other early-return path"
    )


def test_extract_facts_skips_cleanly_when_llm_client_is_none():
    """The replacement gate (`llm_client is None`) must still skip gracefully.

    This is the "belt and braces" half of the fix (spec §5.2): the client
    itself -- not an Anthropic-specific env var -- is the correct signal that
    extraction can run. Confirms the new gate still degrades gracefully
    instead of crashing when no client is available.
    """
    from hachi.core.memory import extract_facts

    class _Vault:
        pass

    result = extract_facts(
        "a long enough exchange to pass the length gate",
        vault=_Vault(),
        llm_client=None,
        memory_writer=None,
    )
    assert result == "skipped:no_llm_client"


# ---------------------------------------------------------------------------
# Skip visibility -- jobs/queue.py + core/runtime/job_handlers.py
# ---------------------------------------------------------------------------


def test_skipped_extraction_is_distinguishable_from_success():
    """A skip must be observable from OUTSIDE ``handle_extract_fact`` -- via the
    emitted job event, not by inspecting the handler's return value (spec §7).

    Drives a real ``handle_extract_fact`` through ``InMemoryJobQueue`` +
    ``JobRegistry`` + ``JobWorker``, exactly as production's
    ``ResidentWorkerThread`` would, with no vault resolvable so the handler
    takes the ``"skipped:no_vault"`` branch. Verifies (a) the durable status
    stays COMPLETED (spec §3.5 -- no retry loop introduced) and (b) an
    externally-observable signal distinguishes the skip from a real
    extraction (spec §5.3).
    """
    from hachi.core.runtime.job_handlers import handle_extract_fact
    from hachi.core.runtime_events import JOB_SKIPPED
    from hachi.jobs import InMemoryJobQueue, Job, JobRegistry, JobStatus, JobWorker

    queue = InMemoryJobQueue()
    job_id = queue.enqueue(
        Job(
            job_id="",
            name="extract_fact",
            handler_name="extract_fact",
            payload={
                "session_id": "sess-skip",
                "text": "Some meaningful conversation text that is long enough.",
                "typed": False,
            },
        )
    )

    def _handler(**kwargs) -> str:
        # No vault resolvable -- mirrors a ChromaDB-unavailable production environment.
        return handle_extract_fact(
            **kwargs,
            _vault_factory=lambda: None,
            _llm_client_factory=lambda: object(),
        )

    registry = JobRegistry()
    registry.register("extract_fact", _handler)

    events: list[tuple[str, dict]] = []
    worker = JobWorker(
        queue,
        on_job_event=lambda name, payload: events.append((name, dict(payload))),
        registry=registry,
    )
    assert worker.execute_one()

    event_names = [name for name, _ in events]
    assert "job.completed" in event_names, (
        "the durable status signal must be unchanged — a skip must still be "
        "reported COMPLETED, never FAILED (a raise would trigger an unhelpful "
        "restart retry loop, see job_handlers.py)"
    )
    assert JOB_SKIPPED in event_names, (
        f"a skipped extraction is indistinguishable from a real one from outside "
        f"the handler — got events {event_names}"
    )
    skip_payload = next(payload for name, payload in events if name == JOB_SKIPPED)
    assert skip_payload.get("reason") == "no_vault"

    job = queue.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED, "a skip must stay COMPLETED — no retry loop"
