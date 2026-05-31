"""Credential vault backed by OS-native keychain storage with encrypted fallback."""

from __future__ import annotations

from typing import Optional

from tokoboros.core.secret_store import SecretStore, create_default_secret_store


class CredentialVault:
    def __init__(self, secret_store: Optional[SecretStore] = None):
        self._store = secret_store or create_default_secret_store()
        self._cache: dict[str, str] = {}

    def get_token(self, token_key: str, prompt_if_missing: bool = True) -> Optional[str]:
        """Retrieve a token by logical key, optionally prompting when missing."""
        cached = self._cache.get(token_key)
        if cached:
            return cached

        token = self._store.get(token_key)
        if token:
            self._cache[token_key] = token
            return token

        if not prompt_if_missing:
            return None

        formatted_name = token_key.replace("_", " ").title().replace(" Token", "").replace(" Key", "")
        print("\n[Security Vault] Missing credentials detected.")
        print(f"I need your {formatted_name} token to proceed.")
        user_token = input("Paste token value: ").strip()

        if not user_token:
            raise ValueError(f"Required token '{token_key}' was not provided.")

        self.set_token(token_key, user_token)
        print("[Security Vault] Token saved via secure backend. Resuming execution...\n")
        return user_token

    def set_token(self, token_key: str, value: str) -> None:
        self._store.set(token_key, value, metadata={"name": token_key, "labels": {"source": "vault"}})
        self._cache[token_key] = value

    def delete_token(self, token_key: str) -> None:
        self._store.delete(token_key)
        self._cache.pop(token_key, None)

    def list_token_metadata(self) -> list[dict[str, str]]:
        rows = []
        for entry in self._store.list_metadata():
            rows.append(
                {
                    "name": str(entry.get("name", "")),
                    "backend": str(entry.get("backend", "unknown")),
                }
            )
        return rows

    def clear_all(self) -> int:
        count = 0
        for entry in self._store.list_metadata():
            name = entry.get("name")
            if not name:
                continue
            self._store.delete(name)
            self._cache.pop(str(name), None)
            count += 1
        return count
