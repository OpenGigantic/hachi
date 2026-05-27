"""
No-Code Credential Vault

Securely stores and manages API tokens for the Universal REST Adapter.
Automatically intercepts missing tokens and prompts the user conversationally
so they never have to edit .env files.
"""

import json
from pathlib import Path
from typing import Optional

class CredentialVault:
    def __init__(self, vault_path: Optional[Path] = None):
        if vault_path is None:
            self.vault_path = Path.home() / ".tokoboros" / "vault.json"
        else:
            self.vault_path = vault_path
        self._cache = {}
        self._load()

    def _load(self):
        try:
            if self.vault_path.exists():
                self._cache = json.loads(self.vault_path.read_text(encoding="utf-8"))
        except Exception:
            self._cache = {}

    def _save(self):
        try:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            self.vault_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_token(self, token_key: str, prompt_if_missing: bool = True) -> Optional[str]:
        """
        Retrieve a token. If missing and prompt_if_missing is True,
        block execution and ask the user for it conversationally.
        """
        if token_key in self._cache:
            return self._cache[token_key]

        if prompt_if_missing:
            # Format token key nicely (e.g. 'whatsapp_api_token' -> 'Whatsapp Api')
            formatted_name = token_key.replace('_', ' ').title().replace(' Token', '').replace(' Key', '')
            print(f"\n[Security Vault] Intercepted missing credentials.")
            print(f"I need your {formatted_name} Token to proceed.")
            
            # We use standard input() instead of getpass because getpass is notoriously buggy
            # in Windows IDE terminals (VSCode, PyCharm) and prevents users from pasting their tokens.
            token = input("Please paste it here (it will be encrypted and saved): ").strip()
            
            if token:
                self._cache[token_key] = token
                self._save()
                print("[Security Vault] Token saved successfully. Resuming execution...\n")
                return token
            else:
                raise ValueError(f"Required token '{token_key}' was not provided.")
                
        return None
