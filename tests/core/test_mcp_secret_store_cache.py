"""Regression coverage for ``_get_module_secret_store()``'s process-wide cache (BB-175).

Same shape as BB-172 (``core/events/__init__.py``'s ``get_event_store()``): a
module-global cached a store derived from ``get_hachi_paths()`` forever, with
no re-check against a repointed state dir. BB-172's own bug-bank entry names
this exact global -- ``core/mcp_client.py``'s ``_cached_secret_store`` -- as a
known live sibling that was never fixed.

The fail-open this produces: when the cached store is stale, a lookup
returns ``None`` by one of two routes -- a stale *path* just misses (the
file is absent, so ``get()`` returns ``None`` directly) and a stale
*passphrase* raises (``Fernet``/``RuntimeError``), which
``CompositeSecretStore.get`` swallows into ``None`` (see
``_get_module_secret_store()``'s docstring for the full account). Either
route, ``_resolve_secret_refs`` raises ``_MissingSecretError`` and
``MCPManager.setup_from_config`` registers the MCP server as failed with
"references unknown credential ... Store it first via POST
/runtime/secrets" -- even though the credential exists in the store the
caller actually meant. A wrong diagnosis, reported confidently.

These tests never touch the developer's real state dir or the real OS
keyring: every store built in this file uses a ``tmp_path``-backed
``HachiPaths``, and the ``_no_real_keyring`` fixture below monkeypatches
``hachi.core.secret_store.keyring``'s three entrypoints to raise, forcing
every ``CompositeSecretStore`` in this file onto its ``EncryptedFileSecretStore``
fallback -- the same technique
``tests/core/test_secret_store.py::test_composite_get_falls_back_when_keyring_get_is_unavailable``
already uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

import hachi.core.mcp_client as mcp_client_mod
from hachi.core.paths import HachiPaths
from hachi.core.secret_store import create_default_secret_store


def _fake_paths(root: Path) -> HachiPaths:
    return HachiPaths(
        config_dir=root / "config",
        data_dir=root / "data",
        cache_dir=root / "cache",
        state_dir=root / "state",
    )


def _raise(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("keyring unavailable (test)")


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every CompositeSecretStore built in this file onto its file fallback.

    Without this, ``create_default_secret_store()``'s ``KeyringSecretStore``
    half would hit the real OS keyring (Windows Credential Manager) on every
    ``get``/``set``/``healthcheck`` call in this file -- flaky/unavailable on a
    locked-down CI runner and, worse, would write real entries under the
    ``hachi`` service name on the developer's machine.
    """
    monkeypatch.setattr("hachi.core.secret_store.keyring.get_password", _raise)
    monkeypatch.setattr("hachi.core.secret_store.keyring.set_password", _raise)
    monkeypatch.setattr("hachi.core.secret_store.keyring.delete_password", _raise)


@pytest.fixture(autouse=True)
def _reset_cached_secret_store() -> Iterator[None]:
    """Reset the module global under test before AND after every test in this file.

    Mirrors ``tests/core/events/test_event_store.py``'s
    ``TestGetEventStoreSingletonFollowsRepointedStateDir._reset_singleton`` for
    BB-172's accepted fix: the singleton under test here *is* the global state
    this file is about, so leaking it into another test (in this file, or any
    other file that happens to run afterwards) would be the same bug in
    miniature. Deliberately scoped to this file rather than promoted to an
    autouse fixture in ``tests/conftest.py``: BB-172's own fix did the same for
    ``_event_store``/``_event_store_path`` (no conftest-level reset exists for
    it either), relying on the derivation-check fix itself to make staleness
    self-correcting everywhere else in the suite.
    """
    mcp_client_mod._cached_secret_store = None
    mcp_client_mod._cached_secret_store_key = None
    yield
    mcp_client_mod._cached_secret_store = None
    mcp_client_mod._cached_secret_store_key = None


class TestGetModuleSecretStoreFollowsRepointedStateDir:
    def test_repointing_state_dir_rebuilds_the_store_and_finds_the_new_credential(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Core claim: _get_module_secret_store() re-resolves get_hachi_paths() on
        every call rather than caching it forever.

        Asserts on OBSERVABLE BEHAVIOUR, not object identity: a credential
        written into dir B's real store (after the repoint) must be visible
        through the cache, not silently missing because the cache is still
        bound to dir A.

        MUST FAIL before the fix: pre-fix, the second call below returns the
        stale store still pointed at outer_paths, which never saw the
        credential written to inner_paths -- .get() returns None instead of
        "inner-value".
        """
        outer_paths = _fake_paths(tmp_path / "outer")
        inner_paths = _fake_paths(tmp_path / "inner")
        monkeypatch.setenv("HACHI_SECRET_PASSPHRASE", "fixed-passphrase")

        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: outer_paths)
        first = mcp_client_mod._get_module_secret_store()
        # Sanity: the cache is genuinely populated against outer_paths here --
        # it does not yet know about the credential written below.
        assert first.get("cred:demo") is None

        # Write directly into dir B's real store -- "the store the caller
        # actually meant" once repointed, built the exact same way
        # _get_module_secret_store() builds it (create_default_secret_store),
        # not a bypassing test double.
        create_default_secret_store(paths=inner_paths).set("cred:demo", "inner-value")

        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: inner_paths)
        second = mcp_client_mod._get_module_secret_store()
        assert second.get("cred:demo") == "inner-value", (
            "the cache must follow the repointed state dir and see the "
            "credential written to the NEW store, not stay pinned to the stale one"
        )


class TestGetModuleSecretStoreStableDerivation:
    def test_same_derivation_returns_the_identical_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The singleton must not be rebuilt on every call -- only on a change
        in derivation -- or a correctness bug has been traded for a
        performance one."""
        paths = _fake_paths(tmp_path)
        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: paths)
        monkeypatch.setenv("HACHI_SECRET_PASSPHRASE", "fixed-passphrase")

        first = mcp_client_mod._get_module_secret_store()
        second = mcp_client_mod._get_module_secret_store()
        third = mcp_client_mod._get_module_secret_store()

        assert first is second is third


class TestGetModuleSecretStoreFollowsPassphraseChange:
    def test_changing_passphrase_env_var_rebuilds_the_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The passphrase is the second derivation axis: tests/conftest.py's
        _restore_os_environ restores HACHI_SECRET_PASSPHRASE after each test,
        but a stale cached store keeps decrypting with the OLD passphrase
        forever unless the cache key also tracks it.

        MUST FAIL before the fix: the second call returns the identical
        object even though the passphrase changed underneath it.
        """
        paths = _fake_paths(tmp_path)
        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: paths)

        monkeypatch.setenv("HACHI_SECRET_PASSPHRASE", "passphrase-one")
        first = mcp_client_mod._get_module_secret_store()

        monkeypatch.setenv("HACHI_SECRET_PASSPHRASE", "passphrase-two")
        second = mcp_client_mod._get_module_secret_store()

        assert first is not second

    def test_unset_passphrase_is_not_confused_with_an_empty_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """None (unset) and "" (explicitly empty) must hash to distinct cache
        keys -- collapsing them would make an unset-passphrase store and an
        explicitly-empty-passphrase store indistinguishable."""
        paths = _fake_paths(tmp_path)
        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: paths)

        monkeypatch.delenv("HACHI_SECRET_PASSPHRASE", raising=False)
        unset_store = mcp_client_mod._get_module_secret_store()

        monkeypatch.setenv("HACHI_SECRET_PASSPHRASE", "")
        empty_store = mcp_client_mod._get_module_secret_store()

        assert unset_store is not empty_store


class TestSetupFromConfigEndToEndFailOpen:
    def test_stale_store_makes_setup_from_config_report_a_wrong_diagnosis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end through the real module cache: MCPManager.setup_from_config()
        is NOT given an injected store (unlike
        tests/core/test_mcp_registration.py, which monkeypatches
        _get_module_secret_store itself and so never exercises this cache at
        all). Only MCPServerConnection.start is patched (same technique
        test_mcp_registration.py uses) so no subprocess is spawned.

        MUST FAIL before the fix: setup_from_config() reports ok=False with a
        "references unknown credential" _last_error, even though 'apikey'
        exists in the store the repointed paths actually resolve to.
        """
        from hachi.core.mcp_client import MCPManager, MCPServerConfig

        outer_paths = _fake_paths(tmp_path / "outer")
        inner_paths = _fake_paths(tmp_path / "inner")
        monkeypatch.setenv("HACHI_SECRET_PASSPHRASE", "fixed-passphrase")

        # Populate the cache against outer_paths first (e.g. an earlier boot stage).
        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: outer_paths)
        mcp_client_mod._get_module_secret_store()

        # The credential actually lives in inner_paths's store.
        create_default_secret_store(paths=inner_paths).set("cred:apikey", "the-real-value")

        # Repoint -- e.g. a test isolating a real boot, or a hot-reload after a
        # state-dir change.
        monkeypatch.setattr("hachi.core.paths.get_hachi_paths", lambda: inner_paths)

        manager = MCPManager()
        cfg = MCPServerConfig(
            name="ctx7",
            command="npx",
            args=["-y", "@ctx7/mcp"],
            env={"API_KEY": "secret:apikey"},
        )
        with patch("hachi.core.mcp_client.MCPServerConnection.start", return_value=True):
            ok = manager.setup_from_config(cfg)

        assert ok is True, (
            "setup_from_config must resolve 'apikey' from the store the "
            "repointed paths actually point to, not fail with a stale-cache "
            "'unknown credential' diagnosis"
        )
        server = manager._servers.get("ctx7")
        assert server is not None
        assert server._last_error is None
