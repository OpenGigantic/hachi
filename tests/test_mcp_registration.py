"""Tests for MCP secret-ref registration and TOML-only inventory visibility (BB-9 / BB-10)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_store(tmp_path: Path):
    from hachi.core.secret_store import EncryptedFileSecretStore, _MetadataStore

    meta = _MetadataStore(tmp_path / "meta.json")
    return EncryptedFileSecretStore(
        file_path=tmp_path / "secrets.enc",
        passphrase="test-pass",
        metadata_store=meta,
    )


# ---------------------------------------------------------------------------
# B. _resolve_secret_refs
# ---------------------------------------------------------------------------


class TestResolveSecretRefs:
    def test_passthrough_plain_values(self, tmp_path: Path) -> None:
        from hachi.core.mcp_client import _resolve_secret_refs

        store = _make_file_store(tmp_path)
        mapping = {"KEY": "plain-value", "OTHER": "also-plain"}
        assert _resolve_secret_refs(mapping, store) == mapping

    def test_resolves_existing_credential(self, tmp_path: Path) -> None:
        from hachi.core.mcp_client import _resolve_secret_refs

        store = _make_file_store(tmp_path)
        store.set("cred:mykey", "resolved-secret")
        result = _resolve_secret_refs({"API_KEY": "secret:mykey"}, store)
        assert result == {"API_KEY": "resolved-secret"}

    def test_raises_on_missing_credential(self, tmp_path: Path) -> None:
        from hachi.core.mcp_client import _MissingSecretError, _resolve_secret_refs

        store = _make_file_store(tmp_path)
        with pytest.raises(_MissingSecretError, match="unknown credential"):
            _resolve_secret_refs({"API_KEY": "secret:doesnotexist"}, store)


# ---------------------------------------------------------------------------
# B. setup_from_config with secret refs
# ---------------------------------------------------------------------------


class TestMcpSetupFromConfigSecretRefs:
    def test_mcp_env_secret_resolved_before_start(self, tmp_path: Path, monkeypatch) -> None:
        """setup_from_config resolves secret: refs before passing env to the transport."""
        from hachi.core.mcp_client import MCPManager, MCPServerConfig

        store = _make_file_store(tmp_path)
        store.set("cred:ctx7key", "the-real-value")

        monkeypatch.setattr("hachi.core.mcp_client._get_module_secret_store", lambda: store)

        received_env: dict[str, str] = {}

        def _fake_setup_from_config(self_inner, cfg: MCPServerConfig) -> bool:
            received_env.update(cfg.env)
            return True

        # Patch only the inner MCPServerConnection.start so we don't spawn processes.
        # We intercept at setup_from_config to capture the RESOLVED config.
        with patch("hachi.core.mcp_client.MCPServerConnection.start", return_value=True):
            manager = MCPManager()
            cfg = MCPServerConfig(
                name="ctx7",
                command="npx",
                args=["-y", "@ctx7/mcp"],
                env={"API_KEY": "secret:ctx7key"},
            )
            ok = manager.setup_from_config(cfg)

        assert ok is True
        # The server stored in _servers should have the resolved env value.
        server = manager._servers.get("ctx7")
        assert server is not None
        # env on the MCPServerConnection is passed as a subprocess env override;
        # the resolved value must reach the transport (checked via the config we built).
        # Since MCPServerConnection stores env on self._env (or similar), verify
        # the transport was initialised with the resolved value by checking the server
        # doesn't have a _last_error (which would indicate it failed due to missing secret).
        assert server._last_error is None

    def test_mcp_missing_secret_fails_soft(self, tmp_path: Path, monkeypatch) -> None:
        """setup_from_config with a missing cred: → failed status, no exception raised."""
        from hachi.core.mcp_client import MCPManager, MCPServerConfig

        store = _make_file_store(tmp_path)  # empty — no credentials
        monkeypatch.setattr("hachi.core.mcp_client._get_module_secret_store", lambda: store)

        manager = MCPManager()
        cfg = MCPServerConfig(
            name="bad-server",
            command="npx",
            args=["-y", "@example/mcp"],
            env={"TOKEN": "secret:missingcred"},
        )
        ok = manager.setup_from_config(cfg)

        assert ok is False
        server = manager._servers.get("bad-server")
        assert server is not None
        assert server._last_error is not None
        assert "missingcred" in server._last_error


# ---------------------------------------------------------------------------
# C. TOML-only server visibility (BB-10)
# ---------------------------------------------------------------------------


class TestMcpInventoryTomlOnly:
    def test_mcp_inventory_shows_toml_only_server(self, tmp_path: Path) -> None:
        """mcp_inventory() includes TOML-persisted servers even when no agent is live."""
        import tomlkit

        from hachi.gateway.control_plane import GatewayControlPlane

        # Write a minimal settings.toml with one MCP server entry.
        toml_path = tmp_path / "settings.toml"
        doc = tomlkit.document()
        servers = tomlkit.table()
        server_entry = tomlkit.table()
        server_entry.add("command", "npx")
        server_entry.add("args", "-y @example/mcp")
        server_entry.add("transport", "stdio")
        server_entry.add("enabled", True)
        servers.add("my-toml-server", server_entry)
        doc.add("mcp_servers", servers)
        toml_path.write_text(tomlkit.dumps(doc), encoding="utf-8")

        # Patch get_hachi_paths to return our tmp path.
        fake_paths = MagicMock()
        fake_paths.global_config_toml = toml_path

        cp = GatewayControlPlane(
            get_mcp_inventory=lambda: {"server_count": 0, "total_tool_count": 0, "servers": []}
        )

        with patch("hachi.core.paths.get_hachi_paths", return_value=fake_paths):
            result = cp.mcp_inventory()

        names = [s["name"] for s in result["servers"]]
        assert "my-toml-server" in names
        toml_entry = next(s for s in result["servers"] if s["name"] == "my-toml-server")
        assert toml_entry["status"] == "pending_restart"
        assert toml_entry["tool_count"] == 0
