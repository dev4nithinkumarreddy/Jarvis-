"""Groq Brain engine utilizing the official groq SDK with OpenAI-compatible tool use."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Mapping, Sequence
import groq

from jarvis.brain.base import Brain, BrainResponse
from jarvis.brain.prompts import SYSTEM_PROMPT, get_jarvis_system_prompt
from jarvis.core.config import JarvisConfig
from jarvis.core.types import ToolCall, ToolSpec
from jarvis.memory.store import redact_sensitive_text

logger = logging.getLogger(__name__)

# Verified multimodal / vision-capable models hosted on Groq
VERIFIED_VISION_MODELS = {
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.2-11b-vision-preview",
    "llama-3.2-90b-vision-preview",
}


class GroqRateLimitExceededError(RuntimeError):
    """Raised when Groq HTTP 429 retry wait exceeds brain.max_wait_seconds."""

    def __init__(self, wait_seconds: float, message: str) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


def extract_retry_after_seconds(headers: Mapping[str, str] | None) -> float:
    """Extract wait time in seconds from HTTP 429 response headers.

    Checks:
    1. 'retry-after' (seconds or float)
    2. 'x-ratelimit-reset-requests'
    3. 'x-ratelimit-reset-tokens'
    """
    if not headers:
        return 1.0

    # 1. Standard Retry-After header
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return float(retry_after)
        except (ValueError, TypeError):
            pass

    # 2. Groq rate limit reset headers
    for hdr_name in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        val = headers.get(hdr_name)
        if val:
            val_clean = val.strip().lower()
            if val_clean.endswith("ms"):
                try:
                    return max(0.001, float(val_clean[:-2]) / 1000.0)
                except ValueError:
                    pass
            elif val_clean.endswith("s"):
                try:
                    return max(0.1, float(val_clean[:-1]))
                except ValueError:
                    pass
            elif val_clean.endswith("m"):
                try:
                    return max(1.0, float(val_clean[:-1]) * 60.0)
                except ValueError:
                    pass
            try:
                return float(val_clean)
            except ValueError:
                pass

    return 1.0


def normalize_groq_base_url(url: str) -> str:
    """Normalize base_url so that the Groq SDK does not double '/openai/v1'.

    The official Groq Python SDK automatically routes chat completions and models
    requests through '/openai/v1/...'. If the user or config supplies
    'https://api.groq.com/openai/v1', we strip the trailing '/openai/v1' so the SDK
    does not attempt to request '/openai/v1/openai/v1/...'.
    """
    cleaned = url.rstrip("/")
    if cleaned.endswith("/openai/v1"):
        cleaned = cleaned[:-len("/openai/v1")].rstrip("/")
    return cleaned or "https://api.groq.com"


class GroqBrain(Brain):
    """Brain implementation using Groq's official SDK with tool calling."""

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        max_tokens: int = 1024,
        max_context_tokens: int = 6000,
        max_wait_seconds: float = 20.0,
        compact_tool_descriptions: bool = True,
        system_prompt: str = SYSTEM_PROMPT,
        client: groq.Groq | None = None,
    ) -> None:
        """Initialize the Groq Brain.

        Args:
            model: Model identifier from config (e.g. 'llama-3.3-70b-versatile').
            base_url: Base URL for Groq API endpoint.
            max_tokens: Maximum completion tokens.
            max_context_tokens: Approximate budget for input context tokens.
            max_wait_seconds: Maximum seconds to wait on HTTP 429 backoff before aborting.
            compact_tool_descriptions: Whether to compact tool descriptions to conserve tokens.
            system_prompt: System prompt for instructions.
            client: Optional pre-configured groq.Groq client (e.g. for testing).

        Raises:
            ValueError: If model is empty or GROQ_API_KEY environment variable is missing.
        """
        if not model:
            raise ValueError("A model name must be provided from configuration. No default is permitted.")

        self._model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.max_context_tokens = max_context_tokens
        self.max_wait_seconds = max_wait_seconds
        self.compact_tool_descriptions = compact_tool_descriptions
        self.system_prompt = system_prompt

        if client is not None:
            self.client = client
        else:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise ValueError(
                    "GROQ_API_KEY environment variable is not set. "
                    "API keys must come from environment variables only."
                )
            norm_url = normalize_groq_base_url(self.base_url)
            self.client = groq.Groq(api_key=api_key, base_url=norm_url, max_retries=0)

    @property
    def model(self) -> str:
        """Return the configured model identifier."""
        return self._model

    @property
    def supports_images(self) -> bool:
        """Return True only if the configured model is a verified vision-capable model."""
        m_lower = self._model.lower()
        return m_lower in VERIFIED_VISION_MODELS or "vision" in m_lower

    @classmethod
    def from_config(cls, config: JarvisConfig, **kwargs: Any) -> GroqBrain:
        """Create GroqBrain from JarvisConfig."""
        user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
        prompt = kwargs.pop("system_prompt", get_jarvis_system_prompt(user_title))
        return cls(
            model=config.brain.model,
            base_url=config.brain.base_url,
            max_tokens=config.brain.max_tokens,
            max_context_tokens=config.brain.max_context_tokens,
            max_wait_seconds=config.brain.max_wait_seconds,
            compact_tool_descriptions=config.brain.compact_tool_descriptions,
            system_prompt=prompt,
            **kwargs,
        )

    def verify_model_exists(self) -> bool:
        """Verify that configured model exists on the Groq endpoint.

        Raises:
            ValueError: If the configured model is not available in Groq's model list.
        """
        try:
            models_response = self.client.models.list()
            available_ids = {m.id for m in models_response.data}
            if self._model not in available_ids:
                raise ValueError(
                    f"Configured model '{self._model}' does not exist on Groq endpoint. "
                    f"Available models: {sorted(list(available_ids))[:10]}... "
                    "Run 'jarvis brain models' to inspect all available models."
                )
            return True
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            # Sanitize exception message to ensure API key is never leaked
            clean_err = redact_sensitive_text(str(exc))
            raise RuntimeError(f"Failed to verify model existence against Groq endpoint: {clean_err}") from None

    @classmethod
    def list_available_models(
        cls,
        client: groq.Groq | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
    ) -> list[dict[str, Any]]:
        """List all models available from Groq's models endpoint.

        Returns:
            list[dict]: Models with id, owner, tool-use support, and vision support.
        """
        if client is None:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise ValueError(
                    "GROQ_API_KEY environment variable is not set. "
                    "API keys must come from environment variables only."
                )
            norm_url = normalize_groq_base_url(base_url)
            client = groq.Groq(api_key=api_key, base_url=norm_url, max_retries=0)

        resp = client.models.list()
        results: list[dict[str, Any]] = []

        for m in resp.data:
            m_id = m.id
            m_lower = m_id.lower()
            supports_vision = m_lower in VERIFIED_VISION_MODELS or "vision" in m_lower
            # Per Groq official docs: all hosted LLM models on Groq support tool use
            # Whisper audio models do not support tool calling
            supports_tool_use = "whisper" not in m_lower

            results.append({
                "id": m_id,
                "owned_by": getattr(m, "owned_by", "groq"),
                "created": getattr(m, "created", 0),
                "context_window": getattr(m, "context_window", None),
                "supports_tool_use": supports_tool_use,
                "supports_vision": supports_vision,
            })

        results.sort(key=lambda item: item["id"])
        return results

    def _map_tool_spec(self, spec: ToolSpec) -> dict[str, Any]:
        """Convert Jarvis ToolSpec to Groq OpenAI-compatible function schema."""
        desc = spec.description
        if self.compact_tool_descriptions and len(desc) > 80:
            # Compact description to first sentence to reduce prompt tokens
            desc = desc.split(".")[0].strip() + "."

        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": desc,
                "parameters": spec.input_schema,
            },
        }

    def _estimate_tokens(self, text: str) -> int:
        """Rough token count estimation (~4 characters per token)."""
        return max(1, len(text) // 4)

    def _format_and_prune_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize messages to Groq OpenAI format and prune older turns to respect token budget."""
        formatted: list[dict[str, Any]] = []

        # 1. System prompt message is always first
        formatted.append({
            "role": "system",
            "content": self.system_prompt,
        })

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    formatted.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Could contain tool_result blocks from earlier turn format
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            tool_id = item.get("tool_use_id") or item.get("id") or "call_unknown"
                            body = item.get("content")
                            if isinstance(body, list):
                                text_accum = "".join(
                                    b.get("text", "") for b in body if isinstance(b, dict) and b.get("type") == "text"
                                )
                                body_str = text_accum
                            else:
                                body_str = str(body) if body is not None else ""
                            formatted.append({
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": body_str,
                            })
                        elif isinstance(item, dict) and item.get("type") == "text":
                            formatted.append({"role": "user", "content": item.get("text", "")})
                continue

            if role == "assistant":
                if isinstance(content, str):
                    formatted.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts: list[str] = []
                    t_calls: list[dict[str, Any]] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, dict) and block.get("type") == "tool_use":
                            call_id = block.get("id", "call_unknown")
                            name = block.get("name", "")
                            args = block.get("input", {})
                            args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
                            t_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": args_str,
                                },
                            })
                    asst_msg: dict[str, Any] = {"role": "assistant"}
                    asst_msg["content"] = "\n".join(text_parts) if text_parts else None
                    if t_calls:
                        asst_msg["tool_calls"] = t_calls
                    formatted.append(asst_msg)
                elif "tool_calls" in msg:
                    formatted.append(msg)
                continue

            if role == "tool":
                formatted.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id") or msg.get("id", "call_unknown"),
                    "content": str(content) if content is not None else "",
                })
                continue

        # 2. Prune older intermediate turns if approximate tokens exceed max_context_tokens
        # Always retain system prompt (index 0) and the final turn
        total_tokens = sum(self._estimate_tokens(str(m.get("content", "")) + str(m.get("tool_calls", ""))) for m in formatted)
        while total_tokens > self.max_context_tokens and len(formatted) > 3:
            # Drop the oldest non-system message (index 1)
            dropped = formatted.pop(1)
            total_tokens -= self._estimate_tokens(str(dropped.get("content", "")) + str(dropped.get("tool_calls", "")))

        return formatted

    def next_step(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[ToolSpec] | None = None,
    ) -> BrainResponse:
        """Execute one step with Groq chat completions API.

        Args:
            messages: Conversation turns.
            tool_specs: Available tools.

        Returns:
            BrainResponse with text, tool calls, and token usage.
        """
        groq_messages = self._format_and_prune_messages(messages)

        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": groq_messages,
            "max_tokens": self.max_tokens,
        }

        if tool_specs:
            create_kwargs["tools"] = [self._map_tool_spec(s) for s in tool_specs]
            create_kwargs["tool_choice"] = "auto"

        max_retries = 3
        attempts = 0

        while True:
            attempts += 1
            try:
                response = self.client.chat.completions.create(**create_kwargs)
                break
            except groq.RateLimitError as rle:
                # HTTP 429: Rate limit encountered
                headers = getattr(rle.response, "headers", None)
                wait_sec = extract_retry_after_seconds(headers)

                if wait_sec > self.max_wait_seconds:
                    raise GroqRateLimitExceededError(
                        wait_seconds=wait_sec,
                        message=(
                            f"Rate limit exceeded (HTTP 429). Required wait is {wait_sec:.1f}s, "
                            f"which exceeds configured max_wait_seconds ({self.max_wait_seconds:.1f}s). "
                            f"Please retry after {wait_sec:.1f} seconds."
                        ),
                    ) from None

                if attempts >= max_retries:
                    raise GroqRateLimitExceededError(
                        wait_seconds=wait_sec,
                        message=(
                            f"Rate limit exceeded (HTTP 429). Retried {max_retries} times. "
                            f"Please retry in {wait_sec:.1f} seconds."
                        ),
                    ) from None

                logger.warning(
                    "Rate limit (429) hit. Backing off for %.2fs (attempt %d/%d)...",
                    wait_sec,
                    attempts,
                    max_retries,
                )
                time.sleep(wait_sec)

            except (groq.AuthenticationError, groq.PermissionDeniedError) as auth_err:
                # NEVER retry on 401 or 403; report cleanly with secrets redacted
                clean_msg = redact_sensitive_text(str(auth_err))
                status_code = getattr(auth_err, "status_code", 401)
                raise PermissionError(
                    f"Groq API authentication failed (HTTP {status_code}): {clean_msg}. "
                    "Please check your GROQ_API_KEY environment variable."
                ) from None

            except Exception as exc:
                # Sanitize error to guarantee raw key never leaks in exception
                clean_err = redact_sensitive_text(str(exc))
                raise RuntimeError(f"Groq API request failed: {clean_err}") from None

        choice = response.choices[0]
        msg = choice.message
        text_content = msg.content or None
        tool_calls: list[ToolCall] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                raw_arguments = tc.function.arguments or "{}"
                try:
                    parsed_args = json.loads(raw_arguments)
                    if not isinstance(parsed_args, dict):
                        raise ValueError(f"Arguments must be a JSON object, got {type(parsed_args).__name__}")
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=fn_name,
                        arguments=parsed_args,
                    ))
                except Exception as parse_exc:
                    # Model returned invalid JSON arguments: send error back to model
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=fn_name,
                        arguments={
                            "_invalid_json_error": str(parse_exc),
                            "_raw_arguments": raw_arguments,
                        },
                    ))

        # Capture token usage
        usage_dict: dict[str, int] | None = None
        if response.usage:
            usage_dict = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
            }

        return BrainResponse(
            text=text_content,
            tool_calls=tool_calls,
            usage=usage_dict,
        )
