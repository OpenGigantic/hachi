from __future__ import annotations

from pathlib import Path

from tokoboros.core.secret_store import EncryptedFileSecretStore


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
