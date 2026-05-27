"""
LLM Provider Abstraction Layer

This module provides a unified interface for interacting with various Large Language Models (LLMs).
By strictly programming to the `BaseLLMClient` interface, the Tokoboros agent remains entirely
model-agnostic, allowing seamless swapping between Anthropic, OpenAI, or local execution (e.g., Ollama)
without modifying the core agent loop.
"""

import abc
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import anthropic

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
