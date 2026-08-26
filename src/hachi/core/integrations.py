"""
Integration Runtime

This module manages persistent service connections for Hachi.
It bridges the gap between .og file definitions and live API connections,
so the agent never has to re-authenticate or re-write boilerplate scripts.

Architecture:
    - `integration` nodes define the connection type, config, and tool schema.
    - Built-in handlers (SMTP) know how to establish persistent connections.
    - Each integration registers a REAL tool in the ToolRegistry so the LLM
      calls it natively — no script writing, no curl commands.
    - Credentials resolve through an injected `CredentialVault`
      (`hachi.core.credential_vault`) — the same vault `core/rest_adapter.py`
      already uses — never from a `.og` file.

Supported integration types:
    - smtp  : Persistent SMTP connection for email sending.

T-58 (see docs/bug-bank.md): `env.og` / `role=env` nodes used to provide
credentials as plaintext `KEY=VALUE` pairs, parsed straight out of a `.og`
file's `::dense` block into an in-memory dict with no vault, no redaction,
and no secret store — see `load_env_node`'s docstring for the deprecation.
`role=integration type=http` support (the old private HTTP handler) was
removed in the same change: `core/rest_adapter.py`'s `UniversalRestAdapter`
already covers HTTP declaratively (via `::api` blocks) with a vault, an SSRF
allowlist, and outbound redaction this module never had.
"""

import logging
import smtplib
from collections.abc import Callable
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, cast

from hachi.core.credential_vault import CredentialVault
from hachi.core.loop import ToolRegistry
from hachi.og_engine.parser import OGNode

logger = logging.getLogger(__name__)


class IntegrationRuntime:
    """
    Manages the lifecycle of all active service integrations.
    Establishes persistent connections from integration nodes and registers
    live tool handlers. Credentials resolve through an injected
    CredentialVault (T-58) — role=env nodes are no longer a credential
    source; see load_env_node.
    """

    def __init__(self, vault: CredentialVault | None = None) -> None:
        self._vault = vault
        self._connections: dict[str, Any] = {}  # integration_name -> live connection object
        self._handlers: dict[str, Callable] = {}  # integration_name -> callable tool function
        self._integration_nodes: dict[str, OGNode] = {}

    # ==========================================
    # Credential Loading
    # ==========================================

    def load_env_node(self, node: OGNode) -> None:
        """
        DEPRECATED (T-58) — role=env credential blocks are no longer honoured.

        Originally: ingest credentials from a role=env node's ::dense block.
        Parsed key=value pairs and merged them into a flat in-memory
        credential store (self._env), read back raw by get_credential() and
        handed straight to smtplib.login() by _setup_smtp — no vault, no
        redaction, no secret store, and the boot path loads EVERY .og under
        the skills dir, including pack-installed ones. That plaintext path is
        now closed.

        Credentials must live in the CredentialVault instead (see __init__'s
        `vault` parameter and _setup_smtp). This method stores nothing. It
        logs a one-time, actionable migration warning naming the offending
        file and the credential KEY NAMES it found (never the values), plus
        the exact command to store each one in the vault.

        Args:
            node: An OGNode with role='env'. Its dense block is inspected
                only to name the keys in the warning — none of the values are
                read into memory.
        """
        keys: list[str] = []
        for line in node.dense.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, _value = line.partition("=")
                keys.append(key.strip())

        if not keys:
            return

        migrate_commands = "; ".join(f"hachi secrets --set {key}" for key in keys)
        logger.warning(
            "role=env node %r in %s carries %d credential key(s) that are now IGNORED "
            "(role=env is no longer a credential source, T-58). Store them in the "
            "credential vault instead: %s",
            node.name,
            node.source_file or "<unknown file>",
            len(keys),
            migrate_commands,
        )

    def _resolve_credential(self, key: str) -> str | None:
        """Resolve a named credential from the vault (never from a .og file).

        Returns None when no vault is configured, no key name was given, or
        the vault has nothing stored under that name. CredentialVault.get_token
        raises ValueError for "missing" — treated here the same as "absent"
        rather than letting it propagate as an integration-setup crash.
        """
        if not self._vault or not key:
            return None
        try:
            # CredentialVault.get_token is declared `-> str | None`; the cast
            # is only needed because that precision doesn't survive mypy's
            # inference through secret_store.py's own looser typing.
            return cast("str | None", self._vault.get_token(key, prompt_if_missing=False))
        except ValueError:
            return None

    def get_credential(self, key: str) -> str | None:
        """
        Retrieve a credential by its key name from the CredentialVault.

        Args:
            key: The credential key (e.g., 'GMAIL_APP_PASSWORD').

        Returns:
            The credential value, or None if not found or no vault is
            configured. Never reads from a .og file's ::dense block — see
            load_env_node (deprecated).
        """
        return self._resolve_credential(key)

    # ==========================================
    # Integration Setup
    # ==========================================

    def setup_integration(self, node: OGNode) -> bool:
        """
        Read an integration node's ::config, establish a persistent connection,
        and create a callable handler function.

        Args:
            node: An OGNode with role='integration'.

        Returns:
            True if the integration was set up successfully, False otherwise.
        """
        integration_type = node.config.get("type", "").lower()
        self._integration_nodes[node.name] = node

        try:
            if integration_type == "smtp":
                return self._setup_smtp(node)
            else:
                # T-58: type=http was removed — core/rest_adapter.py's
                # UniversalRestAdapter already covers declarative HTTP (::api
                # blocks) with a vault, an SSRF allowlist, and outbound
                # redaction this module never had.
                logger.warning(
                    "[integration] Unknown type %r for %s, skipping.",
                    integration_type,
                    node.name,
                )
                return False
        except Exception:
            logger.warning("[integration] Failed to setup %s", node.name, exc_info=True)
            return False

    def register_tools(self, registry: ToolRegistry):
        """
        Register all active integration handlers as real tools in the ToolRegistry.

        Args:
            registry: The tool registry to mount tools onto.
        """
        for name, node in self._integration_nodes.items():
            if name not in self._handlers:
                continue

            # Build the tool schema from the node's ::tool block
            schema = self._build_tool_schema(node)
            if schema:
                registry.register(schema, self._handlers[name])

    def shutdown(self):
        """Gracefully close all persistent connections."""
        for _name, conn in self._connections.items():
            try:
                if hasattr(conn, "quit"):
                    conn.quit()
                elif hasattr(conn, "close"):
                    conn.close()
            except Exception:
                pass
        self._connections.clear()
        self._handlers.clear()

    # ==========================================
    # SMTP Handler
    # ==========================================

    def _setup_smtp(self, node: OGNode) -> bool:
        """
        Establish a persistent SMTP connection using config from the .og file.

        Expected ::config keys:
            type: smtp
            host: smtp.gmail.com
            port: 587
            use_tls: true
            auth_email_key: GMAIL_ADDRESS         (a key name in the credential
                                                    vault — `hachi secrets --set
                                                    GMAIL_ADDRESS`)
            auth_password_key: GMAIL_APP_PASSWORD (same, e.g. `hachi secrets
                                                    --set GMAIL_APP_PASSWORD`)
        """
        host = node.config.get("host", "smtp.gmail.com")
        port = int(node.config.get("port", "587"))
        use_tls = node.config.get("use_tls", "true").lower() == "true"

        # T-58: resolve credentials from the CredentialVault. Falls back to
        # "" (never to a .og-sourced dict) when the vault is unset or the key
        # is missing, exactly matching the pre-T-58 empty-string fallback.
        email_key = node.config.get("auth_email_key", "")
        password_key = node.config.get("auth_password_key", "")
        email_address = self._resolve_credential(email_key) or ""
        password = self._resolve_credential(password_key) or ""

        if not email_address or not password:
            return False

        # Establish persistent SMTP connection
        server = smtplib.SMTP(host, port, timeout=30)
        if use_tls:
            server.starttls()
        server.login(email_address, password)

        self._connections[node.name] = server

        # Create the handler closure
        def send_email(to: str, subject: str, body: str) -> str:
            """Send an email via the persistent SMTP connection."""
            try:
                for field_name, field_value in [("to", to), ("subject", subject)]:
                    if field_value and (chr(13) in field_value or chr(10) in field_value):
                        raise ValueError(
                            f"Email field {field_name!r} contains illegal newline characters"
                        )

                msg = MIMEMultipart()
                msg["From"] = email_address
                msg["To"] = to
                msg["Subject"] = subject
                msg.attach(MIMEText(body, "plain"))

                # Reconnect if the connection dropped
                conn = self._connections.get(node.name)
                try:
                    conn.noop()
                except Exception:
                    conn = smtplib.SMTP(host, port, timeout=30)
                    if use_tls:
                        conn.starttls()
                    conn.login(email_address, password)
                    self._connections[node.name] = conn

                conn.sendmail(email_address, to, msg.as_string())
                return f"Email sent successfully to {to} with subject '{subject}'."
            except Exception as e:
                return f"Failed to send email: {str(e)}"

        self._handlers[node.name] = send_email
        # T-58: never log the email address (or any credential) — name the
        # node only.
        logger.debug("[smtp] %s connected", node.name)
        return True

    # ==========================================
    # Schema Builder
    # ==========================================

    def _build_tool_schema(self, node: OGNode) -> dict[str, Any] | None:
        """
        Build an Anthropic-compatible tool JSON schema from the node's ::tool block.

        Expected ::tool keys:
            name: send_email
            description: Send an email via Gmail
            params: to|recipient email, subject|email subject, body|email body
        """
        tool_name = node.tool_def.get("name", "")
        tool_desc = node.tool_def.get("description", "")

        if not tool_name:
            return None

        # Parse params: "to|recipient email, subject|email subject, body|email body"
        params_raw = node.tool_def.get("params", "")
        properties: dict[str, Any] = {}
        required: list[str] = []

        if params_raw:
            for param in params_raw.split(","):
                param = param.strip()
                if "|" in param:
                    param_name, param_desc = param.split("|", 1)
                else:
                    param_name = param
                    param_desc = param

                properties[param_name.strip()] = {
                    "type": "string",
                    "description": param_desc.strip(),
                }
                required.append(param_name.strip())

        return {
            "name": tool_name,
            "description": tool_desc,
            "input_schema": {"type": "object", "properties": properties, "required": required},
        }
