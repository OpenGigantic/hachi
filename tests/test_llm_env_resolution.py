from __future__ import annotations

from pathlib import Path

import httpx

from hachi.core.llm import AnthropicClient, OpenAICompatibleClient, create_llm_client


def test_create_llm_client_reads_api_key_from_dotenv(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    # CF-17/T-67 (BB-2): _resolve_api_key's .env fallback is gated by
    # HACHI_DISABLE_DOTENV_AUTOLOAD, which conftest.py sets to "1" suite-wide for
    # hermeticity. This test's subject IS that fallback, so it must opt back in
    # explicitly — matching production, where the flag is unset.
    monkeypatch.setenv("HACHI_DISABLE_DOTENV_AUTOLOAD", "0")

    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=test-dotenv-key\n",
        encoding="utf-8",
    )

    client = create_llm_client(
        {
            "kind": "anthropic",
            "model": "claude-haiku-4-5",
            "api_key_env": "ANTHROPIC_API_KEY",
        }
    )

    assert isinstance(client, AnthropicClient)
    assert client.api_key == "test-dotenv-key"


def test_create_llm_client_applies_timeout_to_anthropic_sdk_client(monkeypatch) -> None:
    """A per-call timeout must reach the Anthropic SDK client.

    BB-95: create_llm_client omitted `timeout`, so AnthropicClient's own
    default of None was passed to anthropic.Anthropic(). An explicit None is
    a *given* value to that SDK (anthropic 0.105.2's `is_given(None)` is
    True), so it is forwarded to httpx as "no timeout" -- Anthropic calls
    could hang indefinitely, not merely fall back to the SDK's 600s default.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    client = create_llm_client({"kind": "anthropic", "model": "claude-haiku-4-5"})
    assert isinstance(client, AnthropicClient)

    # Read the SDK object's own resolved timeout -- not the argument we passed.
    resolved = client.client.timeout
    assert resolved is not None, "Anthropic SDK client has NO timeout -- calls can hang forever"
    seconds = resolved.read if isinstance(resolved, httpx.Timeout) else float(resolved)
    assert seconds == 60.0


def test_create_llm_client_applies_timeout_to_openai_compatible(monkeypatch) -> None:
    """The OpenAI-compatible path already had 60s; pin it so it cannot silently regress."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    client = create_llm_client({"kind": "openrouter", "model": "m"})
    assert isinstance(client, OpenAICompatibleClient)

    resolved = client._http.timeout
    seconds = resolved.read if isinstance(resolved, httpx.Timeout) else float(resolved)
    assert seconds == 60.0


def test_anthropic_client_default_timeout_is_bounded_not_none(monkeypatch) -> None:
    """BB-95 follow-up: AnthropicClient's OWN constructor default must never be
    an unbounded ``None`` again.

    create_llm_client always passes an explicit timeout now, but two production
    call sites construct AnthropicClient directly -- bypassing create_llm_client
    entirely -- with no explicit timeout: core/sub_agent.py's delegation path and
    tools/memory_tools.py's memory-consolidation path. The class's own default is
    the backstop that makes that whole class of site unable to hang forever again,
    regardless of how it is constructed.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    client = AnthropicClient()

    resolved = client.client.timeout
    assert resolved is not None, "AnthropicClient()'s own default is unbounded again"
    seconds = resolved.read if isinstance(resolved, httpx.Timeout) else float(resolved)
    assert seconds == 60.0
