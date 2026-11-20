"""Regression tests: booting an agent must not require an LLM credential.

`POST /sessions` used to answer 500 on any machine with no provider key, because
`boot_agent` built the provider client eagerly and `AnthropicClient.__init__` raises
`ValueError("ANTHROPIC_API_KEY must be provided ...")` when no key resolves.

Two things are wrong with that:

1. It contradicts the promise in CONTRIBUTING.md — "No API key is needed to develop
   or to run the test suite" — and CLAUDE.md's rule that credentials are resolved
   lazily at call time, not during boot.
2. The 500 is raised at *session creation*, so every endpoint that needs a live
   session (memory CRUD, config actions, the whole cockpit UI) is unusable on a
   keyless box even though none of them touch the model.

Boot must therefore succeed and the *first generation* must fail with an actionable
message. These tests lock both halves, plus the gateway round-trip.
"""

from __future__ import annotations

from typing import Any

import pytest

# Every provider env var create_llm_client can consume. The test process must look
# like a fresh developer machine: no key anywhere.
_PROVIDER_KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_AI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture()
def no_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider credential from the environment."""
    for var in _PROVIDER_KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_boot_client_is_unconfigured_without_credentials(
    no_provider_credentials: None,
) -> None:
    """No credential ⇒ boot still yields a client; only generation fails."""
    from hachi.core.llm import LLMNotConfiguredError, UnconfiguredLLMClient
    from hachi.gateway.orchestrator import _make_boot_client

    client = _make_boot_client({"kind": "anthropic", "model": "claude-haiku-4-5"}, None, 4096)

    assert isinstance(client, UnconfiguredLLMClient), (
        "boot must substitute a placeholder client rather than raising; got "
        f"{type(client).__name__}"
    )

    with pytest.raises(LLMNotConfiguredError) as excinfo:
        client.generate([])

    message = str(excinfo.value)
    # The message has to tell the user how to fix it — a bare
    # "ANTHROPIC_API_KEY must be provided" does not.
    assert "hachi quickstart" in message
    assert "ANTHROPIC_API_KEY" in message


def test_boot_client_is_real_when_credentials_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credential in the environment ⇒ a real provider client, as before."""
    from hachi.core.llm import AnthropicClient
    from hachi.gateway.orchestrator import _make_boot_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")

    client = _make_boot_client({"kind": "anthropic", "model": "claude-haiku-4-5"}, None, 4096)

    assert isinstance(client, AnthropicClient)
    assert client.api_key == "sk-ant-test-not-a-real-key"


def test_gateway_create_session_without_credentials(
    no_provider_credentials: None,
) -> None:
    """The reported bug: POST /sessions must not 500 on a keyless machine."""
    from hachi.cli.main import _resolve_gateway_auth_token, _start_gateway_server
    from hachi.client.gateway import GatewayClient

    token = _resolve_gateway_auth_token("127.0.0.1")
    server, base_url, runtime = _start_gateway_server(
        host="127.0.0.1", port=0, attach_runtime=True
    )
    try:
        client: Any = GatewayClient(base_url, token)
        resp = client.create_session(client_id="test-boot-no-creds", surface="test")
        assert resp.get("session_id") or resp.get("id"), (
            f"session creation must succeed without a provider key; got {resp}"
        )
    finally:
        runtime.shutdown()
        server.stop()
