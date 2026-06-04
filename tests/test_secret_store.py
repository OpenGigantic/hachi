from __future__ import annotations

import os
from pathlib import Path

import pytest

from tokoboros.core.secret_store import (
    CompositeSecretStore,
    EncryptedFileSecretStore,
    KeyringSecretStore,
    _MetadataStore,
)


def test_encrypted_file_secret_store_roundtrip(tmp_path: Path) -> None:
    store = EncryptedFileSecretStore(
        file_path=tmp_path / "secrets.enc",
        passphrase="test-passphrase",
    )

    store.set("slack.bot_token", "xoxb-secret")
    assert store.get("slack.bot_token") == "xoxb-secret"

    store.delete("slack.bot_token")
    assert store.get("slack.bot_token") is None


def test_encrypted_store_healthcheck_requires_passphrase(tmp_path: Path) -> None:
    store = EncryptedFileSecretStore(file_path=tmp_path / "secrets.enc", passphrase=None)
    result = store.healthcheck()
    assert result["status"] == "unavailable"


def test_composite_get_falls_back_when_keyring_get_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    metadata = _MetadataStore(tmp_path / "secrets_metadata.json")
    keyring_store = KeyringSecretStore(service_name="tokoboros-test", metadata_store=metadata)
    fallback_store = EncryptedFileSecretStore(
        file_path=tmp_path / "secrets.enc",
        passphrase="test-passphrase",
        metadata_store=metadata,
    )
    composite = CompositeSecretStore(primary=keyring_store, fallback=fallback_store)

    fallback_store.set("provider.api_key", "fallback-secret")
    monkeypatch.setattr(
        "tokoboros.core.secret_store.keyring.get_password",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("keyring unavailable")),
    )

    assert composite.get("provider.api_key") == "fallback-secret"


def test_missing_passphrase_blocks_fallback_set_when_keyring_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    metadata = _MetadataStore(tmp_path / "secrets_metadata.json")
    keyring_store = KeyringSecretStore(service_name="tokoboros-test", metadata_store=metadata)
    fallback_store = EncryptedFileSecretStore(
        file_path=tmp_path / "secrets.enc",
        passphrase=os.environ.get("TOKOBOROS_SECRET_PASSPHRASE"),
        metadata_store=metadata,
    )
    composite = CompositeSecretStore(primary=keyring_store, fallback=fallback_store)

    monkeypatch.setattr(
        "tokoboros.core.secret_store.keyring.set_password",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("keyring unavailable")),
    )
    monkeypatch.setattr(
        "tokoboros.core.secret_store.keyring.get_password",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("keyring unavailable")),
    )
    monkeypatch.setattr(
        "tokoboros.core.secret_store.keyring.delete_password",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("keyring unavailable")),
    )

    with pytest.raises(RuntimeError, match="TOKOBOROS_SECRET_PASSPHRASE"):
        composite.set("openai.api_key", "secret")


def test_corrupted_metadata_file_is_tolerated_and_recovers(tmp_path: Path) -> None:
    metadata_path = tmp_path / "secrets_metadata.json"
    metadata_path.write_text("{not-json", encoding="utf-8")

    metadata = _MetadataStore(metadata_path)
    assert metadata.all() == []

    metadata.upsert("token.one", {"name": "token.one", "backend": "keyring"})
    rows = metadata.all()
    assert len(rows) == 1
    assert rows[0]["name"] == "token.one"


def test_keyring_delete_metadata_desync_is_self_healed(tmp_path: Path, monkeypatch) -> None:
    metadata = _MetadataStore(tmp_path / "secrets_metadata.json")
    store = KeyringSecretStore(service_name="tokoboros-test", metadata_store=metadata)

    metadata.upsert("slack.bot_token", {"name": "slack.bot_token", "backend": "keyring"})
    monkeypatch.setattr(
        "tokoboros.core.secret_store.keyring.delete_password",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("missing in keyring")),
    )

    store.delete("slack.bot_token")

    assert store.list_metadata() == []
