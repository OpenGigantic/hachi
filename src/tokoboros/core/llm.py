"""
LLM Provider Abstraction Layer

This module provides a unified interface for interacting with various Large Language Models (LLMs).
By strictly programming to the `BaseLLMClient` interface, the Tokoboros agent remains entirely
model-agnostic, allowing seamless swapping between Anthropic, OpenAI, or local execution (e.g., Ollama)
without modifying the core agent loop.
"""

import abc
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import anthropic
import httpx

from tokoboros.core.secret_store import SecretStore


def _read_dotenv(path: Path) -> Dict[str, str]:
    """Parse a simple KEY=VALUE dotenv file into a dictionary."""
    if not path.exists():
        return {}

    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_api_key(api_key_env: Optional[str], explicit_api_key: Optional[str] = None) -> Optional[str]:
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
    role: str = Field(..., description="The role of the speaker (e.g., 'user', 'assistant', 'system').")
    content: str | List[Dict[str, Any]] = Field(..., description="The textual content of the message or a list of content blocks.")

class ToolCall(BaseModel):
    """
    Represents a tool execution requested by the LLM.
    """
    id: str = Field(..., description="The unique identifier for the tool call.")
    name: str = Field(..., description="The name of the tool to execute.")
    input: Dict[str, Any] = Field(..., description="The arguments to pass to the tool.")

class LLMResponse(BaseModel):
    """
    Standardized response returned by any underlying LLM provider.
    """
    content: str = Field(default="", description="The textual response from the model.")
    tool_calls: List[ToolCall] = Field(default_factory=list, description="Any tool calls requested by the model.")


class BaseLLMClient(abc.ABC):
    """
    Abstract Base Class defining the contract for all LLM providers.
    Any new model provider must implement this interface.
    """

    @abc.abstractmethod
    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> LLMResponse:
        """
        Generate a response from the LLM based on the conversation history.

        Args:
            messages: The conversation history.
            system_prompt: Optional top-level instructions for the model.
            tools: Optional JSON Schema definitions of available tools.

        Returns:
            An LLMResponse containing the text output and any requested tool calls.
        """
        pass


class AnthropicClient(BaseLLMClient):
    """
    Concrete implementation of BaseLLMClient for Anthropic's Claude models.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-haiku-4-5", max_tokens: int = 4096):
        """
        Initialize the Anthropic client.

        Args:
            api_key: The Anthropic API key. If None, falls back to the ANTHROPIC_API_KEY environment variable.
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
        messages: List[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> LLMResponse:
        """
        Generate a response from the Anthropic Claude model using Prompt Caching.
        Automatically applies ephemeral cache breakpoints to the system prompt and tools
        to optimize token usage and minimize latency on subsequent turns.
        """
        # Convert our standardized Message objects into Anthropic's expected format
        anthropic_messages = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            # Ensure prompt caching beta headers are applied (required for some SDK versions)
            "extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"}
        }
        
        if system_prompt:
            # Apply prompt caching breakpoint to the system prompt block
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
            
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
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        input=block.input
                    )
                )

        return LLMResponse(content=text_content.strip(), tool_calls=tool_calls)


class OpenAICompatibleClient(BaseLLMClient):
    """OpenAI-compatible chat completions client (works for hosted and local endpoints)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self._http = httpx.Client(timeout=timeout)

    @staticmethod
    def _adapt_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
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
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
            )
        return adapted

    def generate(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload_messages: List[Dict[str, Any]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg.role == "system":
                continue
            payload_messages.append({"role": msg.role, "content": msg.content})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "max_tokens": self.max_tokens,
        }

        adapted_tools = self._adapt_tools(tools)
        if adapted_tools:
            payload["tools"] = adapted_tools
            payload["tool_choice"] = "auto"

        response = self._http.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(content="", tool_calls=[])

        message = choices[0].get("message", {})
        content = message.get("content") or ""

        tool_calls: List[ToolCall] = []
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

        return LLMResponse(content=str(content).strip(), tool_calls=tool_calls)


def create_llm_client(provider_config: Dict[str, Any], secret_store: Optional[SecretStore] = None, max_tokens: int = 4096) -> BaseLLMClient:
    """Build an LLM client from provider config plus secret references."""
    kind = str(provider_config.get("kind", "anthropic")).lower()
    model = str(provider_config.get("model", "claude-haiku-4-5"))
    api_key_ref = provider_config.get("api_key_ref")
    api_key_env = provider_config.get("api_key_env")
    base_url = provider_config.get("base_url")

    api_key: Optional[str] = None
    if api_key_ref and secret_store is not None:
        api_key = secret_store.get(str(api_key_ref))
    if not api_key:
        api_key = _resolve_api_key(api_key_env)

    if kind == "anthropic":
        return AnthropicClient(api_key=api_key, model=model, max_tokens=max_tokens)

    if kind == "ollama":
        resolved_base = str(base_url or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434/v1")
        return OpenAICompatibleClient(base_url=resolved_base, model=model, api_key=api_key, max_tokens=max_tokens)

    if kind == "openai-compatible":
        resolved_base = str(base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
        return OpenAICompatibleClient(base_url=resolved_base, model=model, api_key=api_key, max_tokens=max_tokens)

    raise ValueError(f"Unsupported provider kind: {kind}")
