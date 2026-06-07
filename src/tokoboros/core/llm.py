"""
LLM Provider Abstraction Layer

This module provides a unified interface for interacting with various Large Language Models (LLMs).
By strictly programming to the `BaseLLMClient` interface, the Tokoboros agent remains entirely
model-agnostic, allowing seamless swapping between Anthropic, OpenAI, or local execution
(e.g., Ollama) without modifying the core agent loop.
"""

import abc
import json
import os
from pathlib import Path
from typing import Any

import anthropic
import httpx
from pydantic import BaseModel, Field

from tokoboros.core.secret_store import SecretStore


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE dotenv file into a dictionary."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_api_key(api_key_env: str | None, explicit_api_key: str | None = None) -> str | None:
    """Resolve provider API key from explicit value, env var, then local project .env."""
    if explicit_api_key:
        return explicit_api_key
    if not api_key_env:
        return None

    env_name = str(api_key_env)
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env

    # Fallback for local dev: allow project-level .env without requiring shell export.
    dotenv_values = _read_dotenv(Path.cwd() / ".env")
    return dotenv_values.get(env_name)


class Message(BaseModel):
    """
    Represents a standardized message in the conversation history.
    """

    role: str = Field(
        ..., description="The role of the speaker (e.g., 'user', 'assistant', 'system')."
    )
    content: str | list[dict[str, Any]] = Field(
        ..., description="The textual content of the message or a list of content blocks."
    )


class ToolCall(BaseModel):
    """
    Represents a tool execution requested by the LLM.
    """

    id: str = Field(..., description="The unique identifier for the tool call.")
    name: str = Field(..., description="The name of the tool to execute.")
    input: dict[str, Any] = Field(..., description="The arguments to pass to the tool.")


class TokenUsage(BaseModel):
    """Token accounting for a single LLM generation."""

    input_tokens: int = Field(default=0, description="Prompt/input tokens consumed.")
    output_tokens: int = Field(default=0, description="Generated/output tokens.")
    cache_read_tokens: int = Field(
        default=0, description="Input tokens served from the prompt cache (cheaper)."
    )
    cache_creation_tokens: int = Field(
        default=0, description="Input tokens written to the prompt cache (slightly pricier)."
    )


class LLMResponse(BaseModel):
    """
    Standardized response returned by any underlying LLM provider.
    """

    content: str = Field(default="", description="The textual response from the model.")
    tool_calls: list[ToolCall] = Field(
        default_factory=list, description="Any tool calls requested by the model."
    )
    usage: TokenUsage = Field(
        default_factory=TokenUsage, description="Token usage for this generation."
    )


class BaseLLMClient(abc.ABC):
    """
    Abstract Base Class defining the contract for all LLM providers.
    Any new model provider must implement this interface.
    """

    @abc.abstractmethod
    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        dynamic_system: str | None = None,
    ) -> LLMResponse:
        """
        Generate a response from the LLM based on the conversation history.

        Args:
            messages: The conversation history.
            system_prompt: Stable top-level instructions (the cacheable prefix).
            tools: Optional JSON Schema definitions of available tools.
            dynamic_system: Optional per-turn system content (time, recalled
                context) kept AFTER the cache breakpoint so it never invalidates
                the cached prefix. Providers without caching simply append it.

        Returns:
            An LLMResponse containing the text output and any requested tool calls.
        """
        pass

    def list_models(self) -> list[str]:
        """Return the model ids the provider currently exposes.

        Best-effort and network-bound: implementations must NEVER raise — they
        return ``[]`` when discovery is unavailable (offline, no endpoint, auth
        error) so callers can fall back to a curated/configured list.
        """
        return []


class AnthropicClient(BaseLLMClient):
    """
    Concrete implementation of BaseLLMClient for Anthropic's Claude models.
    """

    def __init__(
        self, api_key: str | None = None, model: str = "claude-haiku-4-5", max_tokens: int = 4096
    ):
        """
        Initialize the Anthropic client.

        Args:
            api_key: The Anthropic API key. If None, falls back to the ANTHROPIC_API_KEY
                environment variable.
            model: The target Claude model identifier.
            max_tokens: The maximum number of tokens to generate.
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY must be provided or set in the environment.")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model
        self.max_tokens = max_tokens

    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        dynamic_system: str | None = None,
    ) -> LLMResponse:
        """
        Generate a response from the Anthropic Claude model using Prompt Caching.
        Automatically applies ephemeral cache breakpoints to the system prompt and tools
        to optimize token usage and minimize latency on subsequent turns.
        """
        # Convert our standardized Message objects into Anthropic's expected format
        anthropic_messages = [
            {"role": m.role, "content": m.content} for m in messages if m.role != "system"
        ]

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            # Ensure prompt caching beta headers are applied (required for some SDK versions)
            "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"},
        }

        if system_prompt:
            # Cache the STABLE prefix only (breakpoint here). Per-turn content goes
            # in a second, uncached block AFTER the breakpoint so the cached prefix
            # is reused (0.1x reads) instead of rewritten every turn.
            system_blocks: list[dict[str, Any]] = [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
            ]
            if dynamic_system:
                system_blocks.append({"type": "text", "text": dynamic_system})
            kwargs["system"] = system_blocks

        if tools:
            # Apply prompt caching breakpoint to the tools array.
            # By marking the final tool as ephemeral, we cache the entire tools schema block.
            tools_copy = list(tools)
            if tools_copy:
                last_tool = dict(tools_copy[-1])
                last_tool["cache_control"] = {"type": "ephemeral"}
                tools_copy[-1] = last_tool
            kwargs["tools"] = tools_copy

        # Execute the API call
        response = self.client.messages.create(**kwargs)

        # Parse the response into our standardized format
        text_content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        raw_usage = getattr(response, "usage", None)
        usage = TokenUsage(
            input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw_usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(raw_usage, "cache_creation_input_tokens", 0) or 0,
        )

        return LLMResponse(content=text_content.strip(), tool_calls=tool_calls, usage=usage)

    def list_models(self) -> list[str]:
        """List Claude models via the Anthropic models API (sorted; [] on error)."""
        try:
            page = self.client.models.list()
            ids = [str(m.id) for m in page.data if getattr(m, "id", None)]
            return sorted(ids)
        except Exception:
            return []


class OpenAICompatibleClient(BaseLLMClient):
    """OpenAI-compatible chat completions client (works for hosted and local endpoints)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client and release its connections."""
        self._http.close()

    def __del__(self) -> None:
        """Safety-net finalizer — close the HTTP client if not already closed."""
        try:
            self._http.close()
        except Exception:
            pass

    @staticmethod
    def _adapt_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not tools:
            return []

        adapted = []
        for tool in tools:
            adapted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", "tool"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "input_schema", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return adapted

    def generate(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        dynamic_system: str | None = None,
    ) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload_messages: list[dict[str, Any]] = []
        if system_prompt:
            # No provider-side prompt caching here, so just recombine the stable
            # prefix and the per-turn tail into one system message.
            content = f"{system_prompt}\n\n{dynamic_system}" if dynamic_system else system_prompt
            payload_messages.append({"role": "system", "content": content})

        for msg in messages:
            if msg.role == "system":
                continue
            payload_messages.append({"role": msg.role, "content": msg.content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "max_tokens": self.max_tokens,
        }

        adapted_tools = self._adapt_tools(tools)
        if adapted_tools:
            payload["tools"] = adapted_tools
            payload["tool_choice"] = "auto"

        response = self._http.post(
            f"{self.base_url}/chat/completions", headers=headers, json=payload
        )
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(content="", tool_calls=[])

        message = choices[0].get("message", {})
        content = message.get("content") or ""

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls", []) or []:
            fn = raw_call.get("function", {})
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str)
            except Exception:
                args = {}

            tool_calls.append(
                ToolCall(
                    id=str(raw_call.get("id", "tool_call")),
                    name=str(fn.get("name", "unknown_tool")),
                    input=args,
                )
            )

        raw_usage = data.get("usage", {}) or {}
        usage = TokenUsage(
            input_tokens=int(raw_usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("completion_tokens", 0) or 0),
        )

        return LLMResponse(content=str(content).strip(), tool_calls=tool_calls, usage=usage)

    def list_models(self) -> list[str]:
        """List models via the OpenAI-compatible /models endpoint (sorted; [] on error).

        Works for hosted OpenAI-compatible providers and local Ollama (its
        /v1/models endpoint), which is how those are wired here.
        """
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = self._http.get(f"{self.base_url}/models", headers=headers)
            response.raise_for_status()
            data = response.json().get("data", [])
            ids = [str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")]
            return sorted(ids)
        except Exception:
            return []


def resolve_available_models(
    discovered: list[str],
    allowed: list[str],
    current: str = "",
) -> list[str]:
    """The switchable model list for the UI.

    ``allowed`` (the config guardrail) is a HARD cap: when non-empty, the list is
    exactly the allowlist; otherwise it is whatever the provider exposed
    (``discovered``). The ``current`` model is always included so it can be shown
    and marked. Order is preserved and duplicates removed.
    """
    source = list(allowed) if allowed else list(discovered)
    result: list[str] = []
    for name in source:
        if name and name not in result:
            result.append(name)
    if current and current not in result:
        result.insert(0, current)
    return result


def can_switch_model(target: str, allowed: list[str]) -> bool:
    """Guardrail check: may ``target`` be selected?

    An empty ``allowed`` list means unrestricted. Otherwise the target must be a
    member of the allowlist.
    """
    if not allowed:
        return True
    return target in allowed


# Named OpenAI-compatible providers → their default base URL. All speak the same
# /chat/completions protocol, so one client serves them; a config `base_url` overrides.
_OPENAI_COMPAT_PRESETS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com/v1",
    # Google Gemini — official OpenAI-compatible endpoint (free tier available).
    # Get a free key at https://aistudio.google.com/  (set GEMINI_API_KEY).
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


def create_llm_client(
    provider_config: dict[str, Any], secret_store: SecretStore | None = None, max_tokens: int = 4096
) -> BaseLLMClient:
    """Build an LLM client from provider config plus secret references."""
    kind = str(provider_config.get("kind", "anthropic")).lower()
    model = str(provider_config.get("model", "claude-haiku-4-5"))
    api_key_ref = provider_config.get("api_key_ref")
    api_key_env = provider_config.get("api_key_env")
    base_url = provider_config.get("base_url")

    api_key: str | None = None
    if api_key_ref and secret_store is not None:
        api_key = secret_store.get(str(api_key_ref))
    if not api_key:
        api_key = _resolve_api_key(api_key_env)

    if kind == "anthropic":
        return AnthropicClient(api_key=api_key, model=model, max_tokens=max_tokens)

    if kind == "ollama":
        resolved_base = str(
            base_url or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434/v1"
        )
        return OpenAICompatibleClient(
            base_url=resolved_base, model=model, api_key=api_key, max_tokens=max_tokens
        )

    if kind == "gemini":
        # Google Gemini — official OpenAI-compatible endpoint (free tier).
        # Falls back to GEMINI_API_KEY then GOOGLE_AI_API_KEY env vars.
        if not api_key:
            api_key = _resolve_api_key("GEMINI_API_KEY") or _resolve_api_key("GOOGLE_AI_API_KEY")
        resolved_base = str(base_url or _OPENAI_COMPAT_PRESETS["gemini"])
        # Default model if caller left the generic "claude-haiku-4-5" unchanged.
        if model == "claude-haiku-4-5":
            model = "gemini-2.0-flash"
        return OpenAICompatibleClient(
            base_url=resolved_base, model=model, api_key=api_key, max_tokens=max_tokens
        )

    if kind == "chatgpt-web":
        # Unofficial ChatGPT web-session provider.
        # ⚠ This uses the reverse-engineered ChatGPT web API and may violate
        #   OpenAI's Terms of Service. Use only on accounts you own and accept
        #   the risk of account suspension.
        from tokoboros.core.unofficial_providers import ChatGPTWebClient

        session_token = (
            api_key
            or _resolve_api_key("CHATGPT_SESSION_TOKEN")
            or _resolve_api_key("CHATGPT_ACCESS_TOKEN")
        )
        return ChatGPTWebClient(
            session_token=session_token,
            model=model if model != "claude-haiku-4-5" else "gpt-4o",
            max_tokens=max_tokens,
        )

    if kind in _OPENAI_COMPAT_PRESETS:
        # Named OpenAI-compatible providers (OpenRouter, Groq, Together, DeepSeek):
        # same wire protocol, different base URL. A config `base_url` still overrides.
        resolved_base = str(base_url or _OPENAI_COMPAT_PRESETS[kind])
        return OpenAICompatibleClient(
            base_url=resolved_base, model=model, api_key=api_key, max_tokens=max_tokens
        )

    if kind == "openai-compatible":
        resolved_base = str(
            base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        )
        return OpenAICompatibleClient(
            base_url=resolved_base, model=model, api_key=api_key, max_tokens=max_tokens
        )

    raise ValueError(f"Unsupported provider kind: {kind}")
