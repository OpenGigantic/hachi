from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Protocol, TypedDict

import keyring
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from tokoboros.core.paths import TokoborosPaths, ensure_directories, get_tokoboros_paths


class SecretMetadata(TypedDict, total=False):
    name: str
    backend: str
    updated_at: str
    labels: Dict[str, str]


class SecretStore(Protocol):
    def get(self, name: str) -> Optional[str]: ...

    def set(self, name: str, value: str, metadata: Optional[SecretMetadata] = None) -> None: ...

    def delete(self, name: str) -> None: ...

    def list_metadata(self) -> List[SecretMetadata]: ...

    def healthcheck(self) -> Dict[str, str]: ...


class _MetadataStore:
    def __init__(self, metadata_path: Path):
        self._path = metadata_path

    def _load(self) -> Dict[str, SecretMetadata]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save(self, payload: Dict[str, SecretMetadata]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def upsert(self, name: str, metadata: SecretMetadata) -> None:
        payload = self._load()
        payload[name] = metadata
        self._save(payload)

    def delete(self, name: str) -> None:
        payload = self._load()
        payload.pop(name, None)
        self._save(payload)

    def all(self) -> List[SecretMetadata]:
        payload = self._load()
        rows: List[SecretMetadata] = []
        for key, value in payload.items():
            item = dict(value)
            item.setdefault("name", key)
            rows.append(item)
        return rows


class KeyringSecretStore:
    def __init__(self, service_name: str = "tokoboros", metadata_store: Optional[_MetadataStore] = None):
        self._service_name = service_name
        self._metadata = metadata_store

    def get(self, name: str) -> Optional[str]:
        return keyring.get_password(self._service_name, name)

    def set(self, name: str, value: str, metadata: Optional[SecretMetadata] = None) -> None:
        keyring.set_password(self._service_name, name, value)
        if self._metadata is not None:
            item: SecretMetadata = {
                "name": name,
                "backend": "keyring",
                "labels": (metadata or {}).get("labels", {}),
            }
            self._metadata.upsert(name, item)

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self._service_name, name)
        except Exception:
            pass
        if self._metadata is not None:
            self._metadata.delete(name)

    def list_metadata(self) -> List[SecretMetadata]:
        if self._metadata is None:
            return []
        return self._metadata.all()

    def healthcheck(self) -> Dict[str, str]:
        probe_name = "tokoboros.health.probe"
        probe_value = "ok"
        try:
            keyring.set_password(self._service_name, probe_name, probe_value)
            observed = keyring.get_password(self._service_name, probe_name)
            keyring.delete_password(self._service_name, probe_name)
            if observed != probe_value:
                return {"status": "degraded", "reason": "keyring probe mismatch"}
            return {"status": "ok", "backend": "keyring"}
        except Exception as exc:
            return {"status": "unavailable", "backend": "keyring", "reason": str(exc)}


class EncryptedFileSecretStore:
    def __init__(self, file_path: Path, passphrase: Optional[str], metadata_store: Optional[_MetadataStore] = None):
        self._file_path = file_path
        self._passphrase = passphrase
        self._metadata = metadata_store

    @staticmethod
    def _derive_key(passphrase: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=390000,
        )
        return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))

    def _read_blob(self) -> Dict[str, str]:
        if not self._file_path.exists():
            return {"salt": "", "payload": ""}
        try:
            return json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            return {"salt": "", "payload": ""}

    def _decrypt_all(self) -> Dict[str, str]:
        if not self._passphrase:
            raise RuntimeError("Encrypted fallback requires TOKOBOROS_SECRET_PASSPHRASE")

        blob = self._read_blob()
        salt_b64 = blob.get("salt", "")
        payload_b64 = blob.get("payload", "")
        if not salt_b64 or not payload_b64:
            return {}

        salt = base64.b64decode(salt_b64.encode("utf-8"))
        key = self._derive_key(self._passphrase, salt)
        cipher = Fernet(key)
        plaintext = cipher.decrypt(payload_b64.encode("utf-8")).decode("utf-8")
        data = json.loads(plaintext)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}

    def _encrypt_all(self, payload: Dict[str, str]) -> None:
        if not self._passphrase:
            raise RuntimeError("Encrypted fallback requires TOKOBOROS_SECRET_PASSPHRASE")

        blob = self._read_blob()
        if blob.get("salt"):
            salt = base64.b64decode(blob["salt"].encode("utf-8"))
        else:
            salt = os.urandom(16)

        key = self._derive_key(self._passphrase, salt)
        cipher = Fernet(key)
        ciphertext = cipher.encrypt(json.dumps(payload).encode("utf-8"))

        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(
                {
                    "salt": base64.b64encode(salt).decode("utf-8"),
                    "payload": ciphertext.decode("utf-8"),
                    "fingerprint": hashlib.sha256(ciphertext).hexdigest(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def get(self, name: str) -> Optional[str]:
        return self._decrypt_all().get(name)

    def set(self, name: str, value: str, metadata: Optional[SecretMetadata] = None) -> None:
        payload = self._decrypt_all()
        payload[name] = value
        self._encrypt_all(payload)
        if self._metadata is not None:
            item: SecretMetadata = {
                "name": name,
                "backend": "encrypted_file",
                "labels": (metadata or {}).get("labels", {}),
            }
            self._metadata.upsert(name, item)

    def delete(self, name: str) -> None:
        payload = self._decrypt_all()
        payload.pop(name, None)
        self._encrypt_all(payload)
        if self._metadata is not None:
            self._metadata.delete(name)

    def list_metadata(self) -> List[SecretMetadata]:
        if self._metadata is None:
            return []
        return self._metadata.all()

    def healthcheck(self) -> Dict[str, str]:
        if not self._passphrase:
            return {
                "status": "unavailable",
                "backend": "encrypted_file",
                "reason": "TOKOBOROS_SECRET_PASSPHRASE is not set",
            }
        try:
            probe = "tokoboros.health.probe"
            self.set(probe, "ok")
            observed = self.get(probe)
            self.delete(probe)
            if observed != "ok":
                return {"status": "degraded", "backend": "encrypted_file", "reason": "probe mismatch"}
            return {"status": "ok", "backend": "encrypted_file"}
        except Exception as exc:
            return {"status": "unavailable", "backend": "encrypted_file", "reason": str(exc)}


class CompositeSecretStore:
    def __init__(self, primary: SecretStore, fallback: SecretStore):
        self._primary = primary
        self._fallback = fallback

    def get(self, name: str) -> Optional[str]:
        primary_value = self._primary.get(name)
        if primary_value is not None:
            return primary_value
        return self._fallback.get(name)

    def set(self, name: str, value: str, metadata: Optional[SecretMetadata] = None) -> None:
        primary_status = self._primary.healthcheck().get("status")
        if primary_status == "ok":
            self._primary.set(name, value, metadata)
            return
        self._fallback.set(name, value, metadata)

    def delete(self, name: str) -> None:
        self._primary.delete(name)
        self._fallback.delete(name)

    def list_metadata(self) -> List[SecretMetadata]:
        metadata: Dict[str, SecretMetadata] = {}
        for row in self._fallback.list_metadata() + self._primary.list_metadata():
            metadata[row.get("name", "")] = row
        return [row for _, row in sorted(metadata.items()) if row.get("name")]

    def healthcheck(self) -> Dict[str, str]:
        primary = self._primary.healthcheck()
        if primary.get("status") == "ok":
            return {"status": "ok", "backend": primary.get("backend", "primary")}

        fallback = self._fallback.healthcheck()
        if fallback.get("status") == "ok":
            return {"status": "ok", "backend": fallback.get("backend", "fallback")}

        return {
            "status": "unavailable",
            "reason": f"primary={primary.get('reason', 'n/a')}; fallback={fallback.get('reason', 'n/a')}",
        }


def create_default_secret_store(paths: Optional[TokoborosPaths] = None) -> SecretStore:
    paths = paths or get_tokoboros_paths()
    ensure_directories(paths)

    metadata = _MetadataStore(paths.secrets_metadata_file)
    keyring_store = KeyringSecretStore(service_name="tokoboros", metadata_store=metadata)
    fallback = EncryptedFileSecretStore(
        file_path=paths.secrets_fallback_file,
        passphrase=os.environ.get("TOKOBOROS_SECRET_PASSPHRASE"),
        metadata_store=metadata,
    )
    return CompositeSecretStore(keyring_store, fallback)
