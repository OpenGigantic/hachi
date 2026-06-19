"""P2-01a — generate_stream() provider seam unit tests.

Verifies that:
  - BaseLLMClient default fallback yields one chunk = full content, returns LLMResponse
  - drain_stream() helper returns (chunks, LLMResponse)
  - AnthropicClient.generate_stream() yields SDK text_stream chunks (mocked SDK)
  - AnthropicClient.generate_stream() returns LLMResponse with tool_calls + usage
  - OpenAICompatibleClient.generate_stream() yields SSE text deltas (mocked httpx)
  - OpenAICompatibleClient.generate_stream() accumulates tool-call fragments across chunks
  - Existing single-shot generate() path is untouched (existing tests pass, no regression)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from hachi.core.llm import (
    LLMResponse,
    Message,
    TokenUsage,
    ToolCall,
    drain_stream,
)

# ---------------------------------------------------------------------------
# Helper: minimal concrete BaseLLMClient for testing the default fallback
# ---------------------------------------------------------------------------


class _MinimalClient:
    """Concrete client that only implements generate(); inherits generate_stream default."""

    model = "minimal"

    def __init__(self, response: LLMResponse) -> None:
        self._response = response
        self.generate_calls = 0

    def generate(self, **_kwargs: Any) -> LLMResponse:
        self.generate_calls += 1
        return self._response

    # generate_stream is inherited from BaseLLMClient (default fallback)
    # We can't actually inherit since _MinimalClient doesn't extend BaseLLMClient,
    # so we import the default from the ABC and call it directly.
    def generate_stream(self, **kwargs: Any):  # type: ignore[override]
        from hachi.core.llm import BaseLLMClient

        return BaseLLMClient.generate_stream(self, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Default fallback tests
# ---------------------------------------------------------------------------


def test_default_fallback_yields_content_as_one_chunk() -> None:
    client = _MinimalClient(LLMResponse(content="hello world", tool_calls=[]))
    gen = client.generate_stream(messages=[Message(role="user", content="hi")])
    chunks: list[str] = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration:
        pass
    assert chunks == ["hello world"]


def test_default_fallback_returns_llm_response() -> None:
    resp = LLMResponse(
        content="answer",
        tool_calls=[ToolCall(id="tc1", name="tool_x", input={"k": "v"})],
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    client = _MinimalClient(resp)
    gen = client.generate_stream(messages=[Message(role="user", content="q")])
    chunks, final = drain_stream(gen)
    assert chunks == ["answer"]
    assert final.content == "answer"
    assert len(final.tool_calls) == 1
    assert final.tool_calls[0].name == "tool_x"
    assert final.usage.input_tokens == 10


def test_default_fallback_empty_content_yields_nothing() -> None:
    client = _MinimalClient(LLMResponse(content="", tool_calls=[]))
    gen = client.generate_stream(messages=[Message(role="user", content="q")])
    chunks, final = drain_stream(gen)
    assert chunks == []
    assert final.content == ""


def test_default_fallback_calls_generate_once() -> None:
    client = _MinimalClient(LLMResponse(content="done"))
    gen = client.generate_stream(messages=[Message(role="user", content="go")])
    drain_stream(gen)
    assert client.generate_calls == 1


# ---------------------------------------------------------------------------
# drain_stream helper
# ---------------------------------------------------------------------------


def test_drain_stream_returns_chunks_and_response() -> None:
    resp = LLMResponse(content="abc def", tool_calls=[])
    client = _MinimalClient(resp)
    chunks, final = drain_stream(
        client.generate_stream(messages=[Message(role="user", content="x")])
    )
    assert "".join(chunks) == "abc def"
    assert final is resp or final.content == "abc def"


# ---------------------------------------------------------------------------
# AnthropicClient.generate_stream() — mocked SDK
# ---------------------------------------------------------------------------


def _make_anthropic_client() -> Any:
    """Return an AnthropicClient with the Anthropic SDK patched so no API key needed."""
    from hachi.core.llm import AnthropicClient

    with patch("hachi.core.llm.anthropic.Anthropic") as mock_anthropic:
        client = AnthropicClient.__new__(AnthropicClient)
        client.api_key = "sk-test"
        client.model = "claude-test"
        client.max_tokens = 1024
        client.client = mock_anthropic()
    return client


def test_anthropic_generate_stream_yields_text_deltas() -> None:
    client = _make_anthropic_client()

    # Build a fake stream context manager
    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_stream.text_stream = iter(["Hello", ", ", "world!"])

    # Final message with text content
    fake_final = MagicMock()
    fake_final.content = [MagicMock(type="text", text="Hello, world!")]
    fake_final.usage = MagicMock(
        input_tokens=8, output_tokens=5,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    fake_stream.get_final_message = MagicMock(return_value=fake_final)

    client.client.messages.stream = MagicMock(return_value=fake_stream)

    gen = client.generate_stream(messages=[Message(role="user", content="hi")])
    chunks, final = drain_stream(gen)

    assert chunks == ["Hello", ", ", "world!"]
    assert final.content == "Hello, world!"
    assert final.usage.input_tokens == 8
    assert final.usage.output_tokens == 5


def test_anthropic_generate_stream_returns_tool_calls() -> None:
    client = _make_anthropic_client()

    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_stream.text_stream = iter([])  # no text — pure tool call response

    # 'name' is a special MagicMock constructor arg (sets repr); set it as attribute instead
    fake_tc = MagicMock()
    fake_tc.type = "tool_use"
    fake_tc.id = "call_1"
    fake_tc.name = "my_tool"
    fake_tc.input = {"x": 1}
    fake_final = MagicMock()
    fake_final.content = [fake_tc]
    fake_final.usage = MagicMock(
        input_tokens=5, output_tokens=3,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    fake_stream.get_final_message = MagicMock(return_value=fake_final)
    client.client.messages.stream = MagicMock(return_value=fake_stream)

    _, final = drain_stream(
        client.generate_stream(messages=[Message(role="user", content="use tool")])
    )

    assert len(final.tool_calls) == 1
    assert final.tool_calls[0].id == "call_1"
    assert final.tool_calls[0].name == "my_tool"
    assert final.tool_calls[0].input == {"x": 1}


def test_anthropic_generate_stream_passes_system_prompt() -> None:
    """generate_stream must include the system prompt in the SDK call kwargs."""
    client = _make_anthropic_client()

    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_stream.text_stream = iter(["ok"])
    fake_final = MagicMock()
    fake_final.content = [MagicMock(type="text", text="ok")]
    fake_final.usage = MagicMock(
        input_tokens=1, output_tokens=1,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    fake_stream.get_final_message = MagicMock(return_value=fake_final)
    client.client.messages.stream = MagicMock(return_value=fake_stream)

    drain_stream(
        client.generate_stream(
            messages=[Message(role="user", content="hi")],
            system_prompt="You are a test assistant.",
        )
    )

    call_kwargs = client.client.messages.stream.call_args[1]
    assert "system" in call_kwargs
    system_blocks = call_kwargs["system"]
    assert any("You are a test assistant." in b.get("text", "") for b in system_blocks)


# ---------------------------------------------------------------------------
# OpenAICompatibleClient.generate_stream() — mocked httpx
# ---------------------------------------------------------------------------


def _make_openai_client() -> Any:
    from hachi.core.llm import OpenAICompatibleClient

    client = OpenAICompatibleClient.__new__(OpenAICompatibleClient)
    client.base_url = "https://api.example.com/v1"
    client.model = "gpt-test"
    client.api_key = "sk-test"
    client.max_tokens = 1024
    client._http = MagicMock()
    return client


def _sse_lines(*chunks: str, include_usage: bool = False) -> str:
    """Build a fake SSE response body with text delta chunks."""
    lines: list[str] = []
    for _i, text in enumerate(chunks):
        data: dict[str, Any] = {
            "choices": [{"delta": {"content": text}, "index": 0}]
        }
        lines.append(f"data: {json.dumps(data)}")
    if include_usage:
        usage_data = {"usage": {"prompt_tokens": 10, "completion_tokens": len(chunks)}}
        lines.append(f"data: {json.dumps(usage_data)}")
    lines.append("data: [DONE]")
    return "\n".join(lines)


def _make_mock_stream_response(lines_body: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.iter_lines = MagicMock(return_value=iter(lines_body.splitlines()))
    return mock_resp


def test_openai_stream_yields_text_deltas() -> None:
    client = _make_openai_client()
    body = _sse_lines("Hi ", "there", "!")
    client._http.stream = MagicMock(return_value=_make_mock_stream_response(body))

    gen = client.generate_stream(messages=[Message(role="user", content="hello")])
    chunks, final = drain_stream(gen)

    assert chunks == ["Hi ", "there", "!"]
    assert final.content == "Hi there!"


def test_openai_stream_captures_usage_from_final_chunk() -> None:
    client = _make_openai_client()
    body = _sse_lines("answer", include_usage=True)
    client._http.stream = MagicMock(return_value=_make_mock_stream_response(body))

    _, final = drain_stream(
        client.generate_stream(messages=[Message(role="user", content="q")])
    )

    assert final.usage.input_tokens == 10
    assert final.usage.output_tokens == 1


def test_openai_stream_accumulates_tool_call_fragments() -> None:
    """Tool-call arguments arrive in pieces; must be reassembled into one ToolCall."""
    client = _make_openai_client()

    # Simulate tool-call streaming: id/name in first chunk, args split across two
    chunks_data = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_99", "type": "function",
             "function": {"name": "search", "arguments": ""}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"q":'}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"cats"}'}}
        ]}}]},
    ]
    lines = "\n".join(f"data: {json.dumps(d)}" for d in chunks_data) + "\ndata: [DONE]"

    mock_resp = _make_mock_stream_response(lines)
    client._http.stream = MagicMock(return_value=mock_resp)

    _, final = drain_stream(
        client.generate_stream(messages=[Message(role="user", content="search")])
    )

    assert len(final.tool_calls) == 1
    tc = final.tool_calls[0]
    assert tc.id == "call_99"
    assert tc.name == "search"
    assert tc.input == {"q": "cats"}


def test_openai_stream_empty_lines_ignored() -> None:
    """Empty lines and non-data lines in the SSE stream must be ignored safely."""
    client = _make_openai_client()
    lines = "\n: keepalive\n\ndata: [DONE]"
    mock_resp = _make_mock_stream_response(lines)
    client._http.stream = MagicMock(return_value=mock_resp)

    chunks, final = drain_stream(
        client.generate_stream(messages=[Message(role="user", content="hi")])
    )

    assert chunks == []
    assert final.content == ""


# ---------------------------------------------------------------------------
# Existing generate() path unchanged (regression guard)
# ---------------------------------------------------------------------------


def test_anthropic_generate_still_works_after_refactor() -> None:
    """Refactoring generate() to use _build_kwargs must not change its behaviour."""
    from hachi.core.llm import AnthropicClient

    client = AnthropicClient.__new__(AnthropicClient)
    client.api_key = "sk-test"
    client.model = "claude-haiku-4-5"
    client.max_tokens = 1024

    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="legacy answer")]
    fake_response.usage = MagicMock(
        input_tokens=3, output_tokens=2,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    mock_client = MagicMock()
    mock_client.messages.create = MagicMock(return_value=fake_response)
    client.client = mock_client

    result = client.generate(messages=[Message(role="user", content="hi")])
    assert result.content == "legacy answer"
    assert result.usage.input_tokens == 3


def test_openai_generate_still_works_after_refactor() -> None:
    """Refactoring generate() to use _build_request must not change its behaviour."""
    client = _make_openai_client()
    client._http.post = MagicMock(return_value=MagicMock(
        json=MagicMock(return_value={
            "choices": [{"message": {"content": "legacy reply", "tool_calls": []}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }),
        raise_for_status=MagicMock(),
    ))

    result = client.generate(messages=[Message(role="user", content="test")])
    assert result.content == "legacy reply"
    assert result.usage.input_tokens == 4
