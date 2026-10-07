"""Anthropic Claude Brain engine utilizing the official anthropic SDK."""

from __future__ import annotations

import os
from typing import Any
import anthropic

from jarvis.brain.base import Brain, BrainResponse
from jarvis.brain.fake_brain import FakeBrain
from jarvis.brain.prompts import SYSTEM_PROMPT, get_jarvis_system_prompt
from jarvis.core.config import JarvisConfig
from jarvis.core.types import ToolCall, ToolSpec


class AnthropicBrain(Brain):
    """Brain implementation using Anthropic's Messages API with tool use."""

    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        system_prompt: str = SYSTEM_PROMPT,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        """Initialize the Anthropic Brain.

        Args:
            model: Model identifier (from config, never hardcoded).
            max_tokens: Maximum tokens for model generation.
            system_prompt: System prompt instructing the model.
            client: Optional pre-configured Anthropic client (e.g. for testing).

        Raises:
            ValueError: If ANTHROPIC_API_KEY environment variable is missing and client is None.
        """
        if not model:
            raise ValueError("A model name must be provided from configuration. No default is permitted.")

        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt

        if client is not None:
            self.client = client
        else:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY environment variable is not set. "
                    "API keys must come from environment variables only."
                )
            self.client = anthropic.Anthropic(api_key=api_key)

    @classmethod
    def from_config(cls, config: JarvisConfig, **kwargs: Any) -> AnthropicBrain:
        """Create AnthropicBrain from JarvisConfig."""
        user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
        prompt = kwargs.pop("system_prompt", get_jarvis_system_prompt(user_title))
        return cls(
            model=config.brain.model,
            system_prompt=prompt,
            **kwargs,
        )

    def next_step(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[ToolSpec] | None = None,
    ) -> BrainResponse:
        """Execute one step with Claude via the Messages API.

        Args:
            messages: Formatted conversation history.
            tool_specs: Optional list of available ToolSpecs.

        Returns:
            BrainResponse containing text, tool calls, or both.
        """
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system_prompt,
            "messages": messages,
        }

        if tool_specs:
            create_kwargs["tools"] = [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                }
                for spec in tool_specs
            ]

        response = self.client.messages.create(**create_kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        full_text = "\n".join(text_parts).strip() if text_parts else None
        return BrainResponse(text=full_text, tool_calls=tool_calls)


__all__ = ["AnthropicBrain", "Brain", "BrainResponse", "FakeBrain", "SYSTEM_PROMPT"]
