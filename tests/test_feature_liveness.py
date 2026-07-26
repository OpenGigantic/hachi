"""T-13 — feature-liveness boot smoke.

CF-1's finding was not "eight bugs" — it was that ~4,100 green tests could not
detect eight *completely dead* features, because every one of those tests
verified a unit, never the wiring that connects it to a real boot. This module
is the structural fix: it boots a REAL session (the third entry in
``grep -rln "boot_agent(" tests/`` — the only one that does not fake it) into
an isolated ``tmp_path`` profile and asserts, for each formerly-dead T-04..T-12
feature, a property of a *live object or a real file* — never a mock's
recorded call.

Design notes (read before "simplifying" this file):

* **Not all nine need the boot.** T-05 (routing) and T-12 (provider-kind
  schema) are pure functions; T-04 (kennel) and T-07 (eval_reports config) and
  T-11 (auto-memory key resolution) each construct their own tiny, isolated
  environment. Only T-06/T-08/T-09/T-10 genuinely need a live
  ``ProcessSharedServices`` singleton built by a real boot, so only those four
  share the module-scoped ``live_session`` fixture. Forcing all nine through
  one boot would not make the suite more correct, only slower.

* **Path isolation is the hard part, and the spec's own hint under-covers
  it.** ``boot_agent()``'s call graph does not resolve paths through one
  ``get_hachi_paths`` name — ``hachi.gateway.orchestrator``,
  ``hachi.gateway.workspace``, ``hachi.core.session_store``,
  ``hachi.core.secret_store``, ``hachi.core.config_loader`` and
  ``hachi.og_engine.engine`` each do their own
  ``from hachi.core.paths import get_hachi_paths`` at THEIR OWN module scope,
  which freezes an independent binding at each module's first import —
  patching only ``hachi.gateway.orchestrator.get_hachi_paths`` (the one
  target the T-13 spec's own §3.4(b) names) leaves the other five live,
  and an unguarded boot would write into the real ``%LOCALAPPDATA%\\hachi``
  (verified non-empty on this machine before writing a line of this file).
  ``_patch_every_get_hachi_paths_binding`` below patches EVERY currently-bound
  module attribute (found by scanning ``sys.modules``) instead of a
  hand-maintained list, because a hand-maintained list is exactly the kind of
  thing this task exists to stop trusting. ``hachi.jobs.queue.SQLiteJobQueue``
  is a second, independent offender — its default db path comes from a
  *different* ``PlatformDirs("hachi", "opengigantic")`` call that
  ``get_hachi_paths()`` has no say over at all — patched separately.

* **``tests/conftest.py``'s autouse ``_reset_process_shared_services`` is
  function-scoped and runs BEFORE every test, including the first one that
  triggers this module's fixture** (module-scoped fixtures set up before
  function-scoped ones, so the clear lands *after* the boot but *before* any
  test body — even the first). The booted objects stay alive (the fixture
  holds a live reference the whole module), but
  ``ProcessSharedServices.instance()`` would return ``None`` inside every test
  unless something re-registers the singleton. ``_shared_reattached`` does
  exactly that: puts the SAME real object boot_agent built back under its
  fingerprint. This does not weaken what T-06/T-09/T-10 prove — the object
  reinserted is the one boot_agent actually produced, so a still-missing
  ``session_store`` attachment (T-10 dark) or a vault built without the
  configured settings (T-06 dark) shows up exactly as it would in production.

* **T-11's ``.env`` fallback is neutralised against a false pass, not
  strengthened.** ``memory_vault._get_llm_client()`` tries the secret store
  BEFORE ``_resolve_api_key``'s ``.env`` fallback. On a dev machine that has
  ever run Hachi for real, the OS keyring may genuinely hold an
  ``ANTHROPIC_API_KEY`` under service name ``"hachi"`` — a fixed service name
  no ``get_hachi_paths`` patch touches. That would not make the assertion
  wrong (the reverted code checks ``os.environ`` first and never reaches the
  secret store at all, so reverting still turns this red), but it would make
  the *passing* case ambiguous about which of two mechanisms produced the
  client, and it would make a hermetic test perform a real OS credential
  lookup for no reason. The test forces ``create_default_secret_store()`` to
  return nothing, so only the ``.env`` mechanism named in the checklist is
  exercised.

Every assertion message below names its task and says what a human should
conclude — this suite exists to be read by whoever it fails on.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hachi.core.paths import HachiPaths
from hachi.core.paths import get_hachi_paths as _REAL_GET_HACHI_PATHS

# ---------------------------------------------------------------------------
# Boot-time path isolation (see module docstring)
# ---------------------------------------------------------------------------


def _patch_every_get_hachi_paths_binding(fake_paths: HachiPaths) -> contextlib.ExitStack:
    """Patch every module-level ``get_hachi_paths`` binding to return *fake_paths*.

    Scans ``sys.modules`` for every module whose ``get_hachi_paths`` attribute
    is bound to the real function object and patches it in place, rather than
    hand-listing modules. Two complementary effects fall out of this:

    * Modules already imported by the time this runs (which, in a full-suite
      run, is effectively everything ``boot_agent`` touches) get their frozen
      binding replaced directly.
    * ``hachi.core.paths`` itself is one of the scanned modules, so any
      module that does a LAZY ``from hachi.core.paths import get_hachi_paths``
      *inside* a function body (e.g. ``shared_services._init``) picks up the
      patched version too, because that import resolves the attribute off the
      (now-patched) source module at call time.

    The only case this cannot cover is a module that does a top-level
    rebinding for the FIRST time while this patch is active but whose import
    machinery somehow bypasses attribute lookup on ``hachi.core.paths`` — not
    a real Python import path, so not a practical gap.
    """
    stack = contextlib.ExitStack()
    patched_module_ids: set[int] = set()
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            bound = getattr(module, "get_hachi_paths", None)
        except Exception:
            continue
        if bound is _REAL_GET_HACHI_PATHS and id(module) not in patched_module_ids:
            patched_module_ids.add(id(module))
            stack.enter_context(patch.object(module, "get_hachi_paths", return_value=fake_paths))
    return stack


# ---------------------------------------------------------------------------
# The profile — settings.toml the shared live_session boots from. Every key
# exists because a specific T-0x assertion needs it (see the trailing comment
# on each) — do not "tidy" one away, it silently disables that assertion.
# ---------------------------------------------------------------------------

_PROFILE_TOML = """
[provider]
kind = "ollama"          # key-free, no network at client construction (T-13 §3.4a)
model = "llama3"

[memory]
backend = "chroma"       # T-06 needs a real vault built through ProcessSharedServices
recall_distance_max = 0.42
core_memory_max_entries = 77
decay_half_life_days = 30.0

[events]
bus_funnel = true        # T-09 needs a real shared EventBus on ProcessSharedServices

[eval_reports]
enabled = false          # presence-without-crash is what T-07 asserts elsewhere

[memory_gardener]
enabled = true           # T-08 (landed as Option A, see docs/DECISIONS.md): the
interval_seconds = 2     # schedule must actually run — short interval so the
                          # suite does not wait long for the first real tick
"""


# ---------------------------------------------------------------------------
# live_session — ONE real boot, shared by the four tests that need a live
# ProcessSharedServices singleton (T-06 / T-08 / T-09 / T-10). See the module
# docstring for why the other five tests below do not use this fixture.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_session(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("liveness")
    config_dir = root / "config"
    data_dir = root / "data"
    cache_dir = root / "cache"
    state_dir = root / "state"
    for directory in (config_dir, data_dir, cache_dir, state_dir):
        directory.mkdir(parents=True, exist_ok=True)

    paths = HachiPaths(
        config_dir=config_dir, data_dir=data_dir, cache_dir=cache_dir, state_dir=state_dir
    )
    paths.skills_dir.mkdir(parents=True, exist_ok=True)
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
    paths.global_config_toml.write_text(_PROFILE_TOML, encoding="utf-8")

    stack = _patch_every_get_hachi_paths_binding(paths)
    # SQLiteJobQueue's default db path is resolved via an UNRELATED
    # PlatformDirs("hachi", "opengigantic") call in hachi.jobs.queue — it does
    # not go through get_hachi_paths() at all, so the scan above cannot reach
    # it. Left unpatched, boot_agent()'s bare SQLiteJobQueue() would create a
    # real jobs.db under the developer's actual AppData directory.
    stack.enter_context(
        patch(
            "hachi.jobs.queue._default_db_path",
            return_value=state_dir / "jobs.db",
        )
    )

    try:
        from hachi.gateway.orchestrator import boot_agent

        session = boot_agent()
    except Exception:
        stack.close()
        raise

    import hachi.gateway.shared_services as shared_services_mod

    # Captured immediately after boot, before any function-scoped autouse
    # fixture has had a chance to clear _INSTANCES (see module docstring).
    fingerprint, shared = next(iter(shared_services_mod._INSTANCES.items()))

    try:
        yield session, paths, fingerprint, shared
    finally:
        try:
            session.shutdown()
        except Exception:
            pass
        # AgentSession.shutdown() deliberately does NOT close the shared vault
        # / session_store (BB-45 / BB-125 is-still-shared guards) — that is
        # correct in production (only ProcessSharedServices.shutdown_all()
        # should), but conftest.py's per-test ProcessSharedServices.clear()
        # orphans this singleton from _INSTANCES long before the session-scoped
        # shutdown_all() at the very end of the whole suite would reach it,
        # which would leave an open ChromaDB PersistentClient / sqlite3 WAL
        # handle blocking this module's tmp_path cleanup on Windows (T-13 §12
        # known trap). Close them directly via the captured reference instead
        # of going through the (possibly-already-cleared) registry.
        if shared.vault is not None:
            try:
                shared.vault.close()
            except Exception:
                pass
        if shared.session_store is not None:
            try:
                shared.session_store.shutdown()
            except Exception:
                pass
        stack.close()


@pytest.fixture
def _shared_reattached(live_session: Any):
    """Re-register the booted singleton for the duration of one test.

    See the module docstring's third bullet for why this is necessary and why
    it does not weaken the tests that use it.
    """
    _session, _paths, fingerprint, shared = live_session
    import hachi.gateway.shared_services as shared_services_mod

    shared_services_mod._INSTANCES[fingerprint] = shared
    yield shared


# ---------------------------------------------------------------------------
# T-04 — kennel pack store: a real .og lands on disk (BB-98)
# ---------------------------------------------------------------------------


def test_liveness_t04_kennel_lists_and_installs_bundled_packs(tmp_path: Path) -> None:
    """GET /skills/kennel lists the real bundled packs; POST /skills/add installs one.

    Goes through the actual composition root (``_InProcessGatewayRuntime`` —
    the class ``cli/main.py``'s gateway wiring constructs) rather than calling
    ``hachi.core.bundled_packs`` directly, so a regression in the WIRING
    (e.g. the original ``hachi.og_engine.bundled_packs.list_bundled_packs``
    import that names a module that has never existed) fails this test even
    though the underlying core module would still be perfectly correct.
    """
    from hachi.cli.main import _InProcessGatewayRuntime
    from hachi.gateway.control_plane import GatewayProtocolError

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    state_dir = tmp_path / "state"
    for directory in (config_dir, data_dir, cache_dir, state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    paths = HachiPaths(
        config_dir=config_dir, data_dir=data_dir, cache_dir=cache_dir, state_dir=state_dir
    )
    paths.skills_dir.mkdir(parents=True, exist_ok=True)

    runtime = _InProcessGatewayRuntime()
    with patch("hachi.cli.main.get_hachi_paths", return_value=paths):
        kennel = runtime.get_kennel()
        ids = {item["id"] for item in kennel}
        assert len(kennel) >= 5, (
            f"T-04: GET /skills/kennel returned only {len(kennel)} item(s) — the "
            "kennel fell through to the empty installed-skills fallback, which "
            "means hachi.core.bundled_packs.list_bundled_manifests() is unreachable "
            "again (BB-98: wrong module/symbol swallowed by a bare except)"
        )
        assert {
            "crm-agent",
            "research-agent",
            "inbox-sentinel",
            "finance-agent",
            "meeting-copilot",
        } <= ids, f"T-04: kennel is missing one of the five shipped packs; got ids={ids}"

        crm = next(item for item in kennel if item["id"] == "crm-agent")
        assert crm["description"], (
            "T-04: kennel item has no description — expected real manifest text"
        )
        assert crm["version"] == "1.0.0", (
            "T-04: kennel item version did not come from pack.toml semver"
        )

        result = runtime.add_skill_from_kennel("inbox-sentinel")
        assert result["ok"] is True

        installed_dir = paths.skills_dir / "packs" / "inbox-sentinel"
        assert installed_dir.exists(), (
            "T-04: POST /skills/add reported ok=True but wrote no pack directory — "
            "install_bundled_pack() was never called (BB-98: kennel_id validated "
            "then never used again)"
        )
        assert any(p.suffix == ".og" for p in installed_dir.iterdir()), (
            "T-04: the pack directory exists but contains no .og files"
        )

        with pytest.raises(GatewayProtocolError):
            runtime.add_skill_from_kennel("no-such-liveness-pack")
        assert not (paths.skills_dir / "packs" / "no-such-liveness-pack").exists(), (
            "T-04: an unknown pack id must not report success or write anything"
        )


# ---------------------------------------------------------------------------
# T-05 — routing cost warnings: validate_routing surfaces a BB-30 warning
# (pure function — no boot needed; T-13 §5.2 keeps it here for a complete
# checklist)
# ---------------------------------------------------------------------------


def test_liveness_t05_routing_validate_surfaces_cost_warning() -> None:
    from hachi.core.routing.capability import Capability
    from hachi.core.routing.descriptor import ModelDescriptor, Pricing
    from hachi.core.routing.policy import RoutingPolicy
    from hachi.core.routing.registry import ModelRegistry
    from hachi.core.routing.validate import validate_routing

    registry = ModelRegistry()
    for model_id, price in (("liveness-cheap-model", 0.15), ("liveness-pricey-model", 2.50)):
        registry.register(
            ModelDescriptor(
                id=model_id,
                provider="anthropic",
                capabilities=frozenset({Capability.REASONING}),
                context_window=200_000,
                max_output=8_192,
                pricing=Pricing(input_per_mtok=price, output_per_mtok=price * 4),
            )
        )
    policy = RoutingPolicy(
        default_model="liveness-cheap-model",
        capabilities={"reasoning": ["liveness-pricey-model"]},
    )

    result = validate_routing(registry, policy, providers=None)

    assert result.ok, "T-05: a costly override is advisory, never blocking"
    assert result.warnings, (
        "T-05: validate_routing() produced no warning for a capability pinned "
        "at ~16x the default model's price — policy.costly_capability_warnings() "
        "is being called with the wrong arity again and the TypeError is being "
        "swallowed (BB-93)"
    )
    assert any("liveness-pricey-model" in w for w in result.warnings), (
        f"T-05: none of the warnings name the costly model: {result.warnings!r}"
    )


# ---------------------------------------------------------------------------
# T-06 — [memory] settings reach the vault (BB-99)
# ---------------------------------------------------------------------------


def test_liveness_t06_memory_settings_reach_vault(_shared_reattached: Any) -> None:
    from hachi.gateway.shared_services import ProcessSharedServices

    shared = ProcessSharedServices.instance()
    assert shared is not None, "T-06: no shared services after a real boot (BB-6)"
    vault = shared.vault
    assert vault is not None, "T-06: chroma backend must produce a live vault"

    assert vault.decay_half_life_days == 30.0, (
        "T-06: the profile configured decay_half_life_days=30.0 but the live "
        f"vault reports {vault.decay_half_life_days!r} — the M2 decay setting "
        "is not reaching the vault ProcessSharedServices._init() builds again"
    )
    assert vault.recall_distance_max == 0.42, (
        "T-06: recall_distance_max=0.42 was configured but the live vault "
        f"reports {vault.recall_distance_max!r}"
    )
    assert vault.core_memory_max_entries == 77, (
        "T-06: core_memory_max_entries=77 was configured but the live vault "
        f"reports {vault.core_memory_max_entries!r}"
    )


# ---------------------------------------------------------------------------
# T-07 — [eval_reports] registered on ResolvedConfig (BB-101)
# (own isolated profile — no boot needed)
# ---------------------------------------------------------------------------


def test_liveness_t07_eval_reports_section_registered_and_round_trips(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from hachi.core.config_loader import load_effective_config
    from hachi.core.settings import ResolvedConfig

    assert "eval_reports" in ResolvedConfig.model_fields, (
        "T-07: ResolvedConfig has no `eval_reports` field — the documented "
        "[eval_reports] section is silently dropped by extra='ignore' inside "
        "config_loader.load_effective_config's ResolvedConfig.model_validate(merged)"
    )

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    state_dir = tmp_path / "state"
    for directory in (config_dir, data_dir, cache_dir, state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    paths = HachiPaths(
        config_dir=config_dir, data_dir=data_dir, cache_dir=cache_dir, state_dir=state_dir
    )
    # enabled=true is deliberately NOT the pydantic default (False) — a test
    # that only checks the default value round-trips cannot tell "the file was
    # read" apart from "the field silently kept its default".
    paths.global_config_toml.write_text(
        "[eval_reports]\nenabled = true\ninterval_seconds = 3600\n", encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING, logger="hachi.core.config_loader"):
        cfg = load_effective_config(paths=paths)
    dumped = cfg.model_dump(mode="python", exclude_none=True)

    assert "eval_reports" in dumped, (
        "T-07: [eval_reports] did not survive the real load_effective_config() "
        "pipeline — boot_agent's config.get('eval_reports', {}) will always see {}"
    )
    assert dumped["eval_reports"]["enabled"] is True, (
        "T-07: eval_reports.enabled did not round-trip from settings.toml"
    )
    assert dumped["eval_reports"]["interval_seconds"] == 3600, (
        "T-07: eval_reports.interval_seconds did not round-trip from settings.toml"
    )

    unknown_key_warnings = [
        r.message for r in caplog.records if "unknown top-level key" in r.message
    ]
    assert not unknown_key_warnings, (
        "T-07: the loader still warns the user that their own documented "
        f"[eval_reports] key is unknown: {unknown_key_warnings!r}"
    )


# ---------------------------------------------------------------------------
# T-08 — MemoryGardener: the Option-A schedule actually runs (BB-126)
# ---------------------------------------------------------------------------


def test_liveness_t08_memory_gardener_schedule_runs(live_session: Any) -> None:
    """[memory_gardener] enabled=true must produce a real, running daemon.

    T-08 landed as Option A (wired end-to-end — see docs/DECISIONS.md
    "T-08 — MemoryGardener: wired up end-to-end (Option A)"), not the spec's
    own recommended Option B. This asserts the schedule half: a thread named
    "hachi-memory-gardener" is alive and a real gardening pass has written
    pending_diffs.json under the configured memory_dir — the two facts a
    boot-time smoke can observe without depending on the A2/A3 HTTP surface,
    which belongs to T-15/gateway wiring, not this module.
    """
    _session, paths, _fingerprint, _shared = live_session

    thread_names = [t.name for t in threading.enumerate()]
    assert "hachi-memory-gardener" in thread_names, (
        "T-08: no thread named 'hachi-memory-gardener' is running — boot_agent "
        "did not start _start_memory_gardener even though "
        "[memory_gardener] enabled=true was configured; the schedule (A1) is "
        "dark again"
    )

    diffs_path = paths.memory_dir / "pending_diffs.json"
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and not diffs_path.exists():
        time.sleep(0.2)

    assert diffs_path.exists(), (
        "T-08: the gardener thread is alive but pending_diffs.json was never "
        "written under get_hachi_paths().memory_dir within 8s — "
        "MemoryGardener.run() is not actually being invoked on the configured "
        "interval, or GardeningResult.save() is writing somewhere else"
    )
    payload = json.loads(diffs_path.read_text(encoding="utf-8"))
    assert payload.get("run_ts"), (
        f"T-08: pending_diffs.json exists but is not a real GardeningResult dump: {payload!r}"
    )


# ---------------------------------------------------------------------------
# T-09 — file.extracted reaches the shared EventBus (BB-96)
# ---------------------------------------------------------------------------


def test_liveness_t09_file_extracted_reaches_shared_bus(_shared_reattached: Any) -> None:
    from hachi.core.runtime.job_handlers import _emit_file_extracted
    from hachi.core.runtime_events import FILE_EXTRACTED

    shared = _shared_reattached
    assert shared.event_bus is not None, (
        "T-09: no shared EventBus exists after a real boot with "
        "[events] bus_funnel=true — BB-6 boot failure, not a T-09 regression"
    )

    received: list[Any] = []
    shared.event_bus.subscribe(FILE_EXTRACTED, received.append)

    _emit_file_extracted("liveness-file-1", "report.pdf", "application/pdf", "sidecar-1")

    assert received, (
        "T-09: file.extracted never reached the shared EventBus — "
        "_emit_file_extracted() is publishing into a fresh, subscriber-less "
        "EventBus() again instead of ProcessSharedServices.instance().event_bus "
        "(BB-96: it imported a module-level `_shared` from "
        "hachi.gateway.orchestrator that has never existed there)"
    )
    assert received[0].name == FILE_EXTRACTED
    assert received[0].payload["file_id"] == "liveness-file-1"
    assert received[0].payload["sidecar_id"] == "sidecar-1"


# ---------------------------------------------------------------------------
# T-10 — the shared session_store is genuinely shared (BB-125)
# ---------------------------------------------------------------------------


def test_liveness_t10_shared_session_store(_shared_reattached: Any) -> None:
    from hachi.gateway.fastapi_app import _resolve_session_store

    shared = _shared_reattached
    assert shared.session_store is not None, (
        "T-10: boot_agent created a SessionStore but never attached it to the "
        "shared singleton — every 'prefer the shared store' branch is dead "
        "again, and the gateway's per-message ingress-idempotency path opens "
        "(and never closes) a fresh sqlite3/WAL connection per message (BB-125)"
    )
    assert _resolve_session_store() is shared.session_store, (
        "T-10: fastapi_app._resolve_session_store() did not return the "
        "identical SessionStore object boot_agent attached — it resolved (or "
        "would have manufactured) a different one"
    )


# ---------------------------------------------------------------------------
# T-11 — auto-memory resolves the API key the way chat does (BB-100)
# (own isolated .env — no boot needed)
# ---------------------------------------------------------------------------


def test_liveness_t11_auto_memory_key_resolves_without_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hachi.core.memory.vault as memory_vault_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # _get_llm_client memoises into a module global — reset it, or a prior
    # test in this process (or a previous run of this same test) makes the
    # assertion pass for the wrong reason.
    monkeypatch.setattr(memory_vault_mod, "_llm_client_instance", None, raising=False)

    class _NullSecretStore:
        """Forces resolution down the .env fallback — see module docstring."""

        def get(self, name: str) -> str | None:
            return None

    monkeypatch.setattr(
        "hachi.core.secret_store.create_default_secret_store",
        lambda *args, **kwargs: _NullSecretStore(),
    )

    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-liveness-not-a-real-key\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)  # _resolve_api_key reads Path.cwd() / ".env"
    # CF-17/T-67 (BB-2): _resolve_api_key's .env fallback is now gated by
    # HACHI_DISABLE_DOTENV_AUTOLOAD, which conftest.py sets to "1" suite-wide for
    # hermeticity. This test's whole subject IS that .env fallback, so it must opt
    # back in explicitly — matching production, where the flag is unset.
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "0")

    client = memory_vault_mod._get_llm_client()

    assert client is not None, (
        "T-11: auto-memory refused to build an LLM client even though the "
        "Anthropic key was resolvable via a project .env — exactly the "
        "mechanism core/llm.py's create_llm_client (the chat path) uses. "
        "Background fact extraction is a silent no-op again for every user "
        "who does not export a literal ANTHROPIC_API_KEY shell env var "
        "(BB-100, a BB-40 recurrence)"
    )


# ---------------------------------------------------------------------------
# T-12 — provider kinds: the schema accepts what the factory implements (BB-121)
# (pure function — no boot needed)
# ---------------------------------------------------------------------------


def test_liveness_t12_provider_kinds_schema_accepts_gemini_and_chatgpt_web() -> None:
    from hachi.core.settings import ResolvedConfig

    # Hardcoded, NOT derived from ProviderKind.__args__: if the literal ever
    # regresses back to the original 7-kind list, deriving the expected set
    # from the literal itself would silently stop checking the two kinds that
    # matter, and this test would pass vacuously against the very regression
    # it exists to catch.
    documented_kinds = (
        "anthropic",
        "openai-compatible",
        "openrouter",
        "groq",
        "together",
        "deepseek",
        "ollama",
        "gemini",
        "chatgpt-web",
    )
    for kind in documented_kinds:
        cfg = ResolvedConfig.model_validate({"provider": {"kind": kind}})
        assert cfg.provider.kind == kind, (
            f"T-12: ResolvedConfig rejected provider kind {kind!r}, which "
            "create_llm_client() implements — the schema Literal and the "
            "factory's kind== branches have diverged again (BB-121)"
        )
