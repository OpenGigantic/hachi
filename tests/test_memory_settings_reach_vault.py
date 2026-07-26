"""Regression tests for T-06 / BB-99 — `[memory]` settings never reached the vault.

``recall_distance_max``, ``core_memory_max_entries``, and ``decay_half_life_days``
(the M2 recency-decay feature) were parsed into a ``MemorySettings`` object and then
thrown away. The vault every session actually uses is built by
``ProcessSharedServices._init`` (``gateway/shared_services.py``), which called
``_provider.initialize(brain_dir=brain_dir)`` unconditionally — never passing the
three settings, even to ``ChromaMemoryProvider`` (the only provider that accepts
them). ``boot_agent``'s settings-carrying branch (``gateway/orchestrator.py``)
never ran on the default backend because ``_shared.vault`` was already non-None
by the time it was reached.

These tests assert the *live vault object* reports the configured values — not
that a config field round-tripped through ``MemorySettings``, and not that a mock
recorded the right kwargs.

Covers:
  - The Chroma path: ``ProcessSharedServices.get_or_create(...).vault`` carries all
    three settings.
  - The non-Chroma path: ``in_memory`` / ``fts5_only`` backends still initialize
    cleanly (no vault, no crash) — proving the fix does not regress the backends
    that intentionally have no recall/decay machinery (out of scope to add it).
  - ``_config_fingerprint`` changes when any of the three settings change, so a
    ``settings.toml`` hot-reload produces a fresh singleton instead of silently
    reusing the vault built from the first config observed in this process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hachi.gateway.shared_services import ProcessSharedServices, _config_fingerprint


@pytest.fixture(autouse=True)
def _clean_singleton():
    # BB-3: never patch.dict(sys.modules) over chromadb — conftest.py warms it once
    # at session start; these tests rely on that warm-up and must not defeat it.
    ProcessSharedServices.clear()
    yield
    ProcessSharedServices.shutdown_all()


# ---------------------------------------------------------------------------
# The Chroma path — the vault must carry the configured settings.
# ---------------------------------------------------------------------------


def test_shared_vault_carries_configured_memory_settings(tmp_path: Path) -> None:
    """The shared vault — the one ``boot_agent`` reuses via `_shared.vault is not
    None` — must be constructed WITH the configured [memory] settings, not
    MemoryVault's defaults."""
    config = {
        "memory": {
            "backend": "chroma",
            "recall_distance_max": 0.42,
            "core_memory_max_entries": 77,
            "decay_half_life_days": 30.0,
        }
    }
    shared = ProcessSharedServices.get_or_create(config, str(tmp_path), force_reinit=True)
    vault = shared.vault
    assert vault is not None, "chroma backend must produce a vault"

    # These are the read-only properties added over the private
    # _decay_half_life_days / _recall_distance_max / _core_memory_max_entries
    # (memory_vault.py) — production code and tests must not reach into the
    # `_`-prefixed attributes directly (T-14).
    assert vault.decay_half_life_days == 30.0
    assert vault.recall_distance_max == 0.42
    assert vault.core_memory_max_entries == 77


def test_shared_vault_default_settings_when_unconfigured(tmp_path: Path) -> None:
    """Sanity companion: an empty [memory] config must still produce a vault, and
    the vault must report MemorySettings' own defaults (0.85 / 1000 / None) — not
    None/0 across the board, which would indicate the settings object itself, not
    just the plumbing, is broken."""
    config: dict[str, object] = {"memory": {"backend": "chroma"}}
    shared = ProcessSharedServices.get_or_create(config, str(tmp_path), force_reinit=True)
    vault = shared.vault
    assert vault is not None

    assert vault.recall_distance_max == 0.85
    assert vault.core_memory_max_entries == 1000
    assert vault.decay_half_life_days is None


# ---------------------------------------------------------------------------
# The non-Chroma path — must keep working, must not silently gain a vault.
# ---------------------------------------------------------------------------


def test_shared_services_non_chroma_backend_initializes_without_chroma_settings(
    tmp_path: Path,
) -> None:
    """`in_memory` / `fts5_only` have no recall/decay machinery (T-06 §9 — out of
    scope to add it) and must keep initializing via the plain
    ``initialize(brain_dir=...)`` signature — never the settings-carrying branch
    (which would raise, since their ABC signature has no matching keyword args).

    T-49 / BB-146 update: this test used to assert ``shared.vault is None`` for
    ``in_memory`` — that null vault *was* the bug T-49 fixed (boot discarded the
    resolved provider instead of adopting it). ``shared.vault`` is now a
    ``ProviderBackedVault`` that routes reads/writes through the provider's
    upsert/search/delete contract. What this test still guards, unchanged: the
    non-Chroma path must not produce a *Chroma* vault or crash going through the
    settings-carrying branch — see ``test_provider_backed_vault_*`` in
    ``tests/test_memory_provider.py`` for the "actually stores a fact" proof."""
    from hachi.core.memory_provider import ProviderBackedVault
    from hachi.core.memory_vault import MemoryVault

    config = {
        "memory": {
            "backend": "in_memory",
            "recall_distance_max": 0.42,
            "core_memory_max_entries": 77,
            "decay_half_life_days": 30.0,
        }
    }
    shared = ProcessSharedServices.get_or_create(config, str(tmp_path), force_reinit=True)
    assert shared.vault is not None, (
        "T-49: in_memory backend must produce a working provider-backed vault"
    )
    assert isinstance(shared.vault, ProviderBackedVault)
    assert not isinstance(shared.vault, MemoryVault), (
        "in_memory backend must not produce a real Chroma vault "
        "(no recall/decay machinery — T-06 §9, still out of scope)"
    )


# ---------------------------------------------------------------------------
# The fingerprint / hot-reload half of the bug (T-06 §3.6 / §5.2).
# ---------------------------------------------------------------------------


def test_fingerprint_changes_when_recall_settings_change(tmp_path: Path) -> None:
    """A settings.toml edit to a [memory] retrieval control must produce a new
    ProcessSharedServices singleton — otherwise a hot-reload silently keeps
    serving the vault built from the FIRST config seen in this process."""
    base = {"memory": {"backend": "chroma"}}
    tuned = {"memory": {"backend": "chroma", "decay_half_life_days": 30.0}}
    assert _config_fingerprint(base, str(tmp_path)) != _config_fingerprint(tuned, str(tmp_path))


def test_fingerprint_changes_for_each_of_the_three_settings_independently(
    tmp_path: Path,
) -> None:
    """Each of the three settings must independently affect the fingerprint —
    not just decay_half_life_days."""
    brain_dir = str(tmp_path)
    base_fp = _config_fingerprint({"memory": {"backend": "chroma"}}, brain_dir)
    recall_fp = _config_fingerprint(
        {"memory": {"backend": "chroma", "recall_distance_max": 0.1}}, brain_dir
    )
    entries_fp = _config_fingerprint(
        {"memory": {"backend": "chroma", "core_memory_max_entries": 5}}, brain_dir
    )
    decay_fp = _config_fingerprint(
        {"memory": {"backend": "chroma", "decay_half_life_days": 7.0}}, brain_dir
    )
    assert len({base_fp, recall_fp, entries_fp, decay_fp}) == 4, (
        "recall_distance_max, core_memory_max_entries, and decay_half_life_days "
        "must each independently change the fingerprint"
    )


def test_hot_reload_with_tuned_settings_does_not_reuse_stale_singleton(
    tmp_path: Path,
) -> None:
    """End-to-end version of the fingerprint guard: a second `get_or_create` call
    (no `force_reinit`, exactly what a hot-reload does) with a tuned [memory]
    config must return a DIFFERENT singleton carrying the new settings — not the
    cached instance built from the original config."""
    brain_dir = str(tmp_path)
    base_config = {"memory": {"backend": "chroma"}}
    tuned_config = {"memory": {"backend": "chroma", "decay_half_life_days": 30.0}}

    first = ProcessSharedServices.get_or_create(base_config, brain_dir)
    second = ProcessSharedServices.get_or_create(tuned_config, brain_dir)

    assert first is not second, (
        "a [memory] retrieval-control change must produce a new singleton — a "
        "stale-cache hit here silently reintroduces the bug this test locks"
    )
    assert second.vault is not None
    assert second.vault.decay_half_life_days == 30.0
