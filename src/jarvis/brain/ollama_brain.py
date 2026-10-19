"""Ollama Brain engine supporting local and remote Ollama deployments with tool use."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence

from jarvis.brain.base import Brain, BrainResponse
from jarvis.brain.prompts import SYSTEM_PROMPT, get_jarvis_system_prompt
from jarvis.core.config import JarvisConfig
from jarvis.core.types import ToolCall, ToolSpec
from jarvis.memory.store import redact_sensitive_text

logger = logging.getLogger(__name__)

# Verified vision-capable multimodal models commonly pulled in Ollama
VERIFIED_VISION_MODELS = {
    "llava",
    "llava-llama3",
    "llava-phi3",
    "bakllava",
    "llama3.2-vision",
    "moondream",
    "minicpm-v",
    "qwen2.5-vl",
}


def normalize_ollama_base_url(url: str) -> str:
    """Normalize base URL for Ollama chat completions.

    Ensures endpoint terminates in /v1 for OpenAI-compatible chat completions,
    handling bare hostnames like http://localhost:11434 and http://127.0.0.1:11434 cleanly.
    """
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        cleaned = "http://127.0.0.1:11434"
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


class OllamaBrain(Brain):
    """Brain implementation connecting to Ollama (local daemon or remote endpoint)."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key: str | None = None,
        max_tokens: int = 1024,
        max_context_tokens: int = 8192,
        compact_tool_descriptions: bool = True,
        system_prompt: str = SYSTEM_PROMPT,
        timeout: float = 60.0,
        fallback_base_url: str | None = None,
        fallback_model: str | None = None,
    ) -> None:
        if not model:
            raise ValueError("A model name must be provided from configuration for OllamaBrain.")

        self._model = model
        self.base_url = normalize_ollama_base_url(base_url)
        self.api_key = api_key or os.environ.get("OLLAMA_API_KEY")
        self.max_tokens = max_tokens
        self.max_context_tokens = max_context_tokens
        self.compact_tool_descriptions = compact_tool_descriptions
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.fallback_base_url = normalize_ollama_base_url(fallback_base_url) if fallback_base_url else "http://127.0.0.1:11434/v1"
        self.fallback_model = fallback_model or "llama3.2:latest"
        self.is_using_fallback = False

    @property
    def model(self) -> str:
        """Return the configured model identifier."""
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        """Dynamically update active neural model on the fly."""
        if not value or not value.strip():
            raise ValueError("Model identifier cannot be empty.")
        self._model = value.strip()

    @property
    def provider(self) -> str:
        """Return provider identifier."""
        return "ollama"

    @property
    def supports_images(self) -> bool:
        """Return True if the model identifier indicates vision capabilities."""
        m_lower = self._model.lower()
        return any(v in m_lower for v in VERIFIED_VISION_MODELS) or "vision" in m_lower

    @classmethod
    def from_config(cls, config: JarvisConfig, **kwargs: Any) -> OllamaBrain:
        """Instantiate OllamaBrain from JarvisConfig."""
        user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
        prompt = kwargs.pop("system_prompt", get_jarvis_system_prompt(user_title))
        base_url = getattr(config.brain, "base_url", "http://127.0.0.1:11434/v1")
        api_key = os.environ.get("OLLAMA_API_KEY")

        return cls(
            model=config.brain.model,
            base_url=base_url,
            api_key=api_key,
            max_tokens=config.brain.max_tokens,
            max_context_tokens=config.brain.max_context_tokens,
            compact_tool_descriptions=config.brain.compact_tool_descriptions,
            system_prompt=prompt,
            **kwargs,
        )

    def _get_headers(self) -> dict[str, str]:
        """Construct HTTP headers for Ollama API requests."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Jarvis-Mark-VII/1.0",
        }
        if self.api_key and self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"
        return headers

    def _http_request(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute HTTP request against the Ollama server."""
        url = f"{self.base_url.rstrip('/')}{endpoint}"
        headers = self._get_headers()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None

        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="replace")
            clean_err = redact_sensitive_text(f"HTTP {http_err.code}: {err_body or http_err.reason}")
            raise RuntimeError(f"Ollama server returned error at {endpoint}: {clean_err}") from http_err
        except urllib.error.URLError as url_err:
            clean_err = redact_sensitive_text(str(url_err.reason))
            # Auto-Failover to local offline Ollama daemon if remote fails and not already on local
            is_local = "127.0.0.1" in self.base_url or "localhost" in self.base_url
            if not is_local and self.fallback_base_url and not self.is_using_fallback:
                logger.warning(
                    "Remote Ollama endpoint unreachable (%s). Initiating failover to local offline core: %s",
                    clean_err,
                    self.fallback_base_url,
                )
                try:
                    fallback_url = f"{self.fallback_base_url.rstrip('/')}{endpoint}"
                    fallback_req = urllib.request.Request(
                        fallback_url,
                        data=data,
                        headers={"Content-Type": "application/json", "User-Agent": "Jarvis-Mark-VII/1.0"},
                        method="POST" if payload else "GET",
                    )
                    with urllib.request.urlopen(fallback_req, timeout=10.0) as fb_resp:
                        self.is_using_fallback = True
                        fb_body = fb_resp.read().decode("utf-8")
                        return json.loads(fb_body) if fb_body else {}
                except Exception as fb_exc:
                    logger.debug("Local failover also unavailable: %s", fb_exc)

            raise RuntimeError(
                f"Failed to connect to Ollama at {self.base_url} ({clean_err}). "
                "Ensure Ollama is running ('ollama serve') or check your base_url in config.yaml."
            ) from url_err

    def verify_model_exists(self) -> bool:
        """Verify that configured model exists on the Ollama endpoint.

        Checks /models (under /v1) or falls back to root /api/tags if needed.
        """
        try:
            available_models = self.list_available_models(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            model_ids = {m["id"].lower() for m in available_models}

            req_norm = self._model.lower()
            # Direct match or match without tag e.g. "llama3.2" matching "llama3.2:latest"
            matched = (
                req_norm in model_ids
                or any(m.split(":")[0] == req_norm for m in model_ids)
                or any(m == f"{req_norm}:latest" for m in model_ids)
            )

            if not matched:
                avail_names = sorted([m["id"] for m in available_models])
                raise ValueError(
                    f"Configured model '{self._model}' is not available on Ollama server at {self.base_url}.\n"
                    f"Available models: {avail_names if avail_names else 'None (no models pulled yet)'}.\n"
                    f"Run 'ollama pull {self._model}' to install this model."
                )
            return True
        except ValueError:
            raise
        except Exception as exc:
            clean_err = redact_sensitive_text(str(exc))
            raise RuntimeError(f"Failed to verify model existence against Ollama endpoint: {clean_err}") from None

    @classmethod
    def list_available_models(
        cls,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        """List all models available on the Ollama instance.

        Queries /v1/models (OpenAI compatibility) and falls back to /api/tags.
        """
        norm_v1 = normalize_ollama_base_url(base_url)
        root_url = re.sub(r"/v1/?$", "", norm_v1)
        api_key = api_key or os.environ.get("OLLAMA_API_KEY")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Jarvis-Mark-VII/1.0",
        }
        if api_key and api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"

        results: list[dict[str, Any]] = []

        # Try /v1/models
        models_data = None
        try:
            req = urllib.request.Request(f"{norm_v1}/models", headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models_data = data.get("data")
        except Exception:
            pass

        if models_data and isinstance(models_data, list):
            for m in models_data:
                m_id = m.get("id", "")
                m_lower = m_id.lower()
                supports_vision = any(v in m_lower for v in VERIFIED_VISION_MODELS) or "vision" in m_lower
                results.append({
                    "id": m_id,
                    "owned_by": m.get("owned_by", "ollama"),
                    "created": m.get("created", 0),
                    "context_window": None,
                    "supports_tool_use": True,
                    "supports_vision": supports_vision,
                })
            results.sort(key=lambda x: x["id"])
            return results

        # Fallback to Ollama native /api/tags
        try:
            req = urllib.request.Request(f"{root_url}/api/tags", headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("models", []):
                    m_id = m.get("name") or m.get("model", "")
                    m_lower = m_id.lower()
                    supports_vision = any(v in m_lower for v in VERIFIED_VISION_MODELS) or "vision" in m_lower
                    results.append({
                        "id": m_id,
                        "owned_by": "ollama",
                        "size": m.get("size"),
                        "modified_at": m.get("modified_at"),
                        "supports_tool_use": True,
                        "supports_vision": supports_vision,
                    })
        except Exception as exc:
            clean_err = redact_sensitive_text(str(exc))
            raise RuntimeError(f"Could not retrieve model list from Ollama server at {base_url}: {clean_err}") from exc

        results.sort(key=lambda x: x["id"])
        return results

    def _map_tool_spec(self, spec: ToolSpec) -> dict[str, Any]:
        """Convert Jarvis ToolSpec to OpenAI-compatible function calling definition."""
        desc = spec.description
        if self.compact_tool_descriptions and len(desc) > 80:
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
        """Rough token estimation (~4 chars per token)."""
        return max(1, len(text) // 4)

    def _format_and_prune_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize messages to OpenAI-compatible chat format and prune older turns."""
        formatted: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    formatted.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            tool_id = item.get("tool_use_id") or item.get("id") or "call_unknown"
                            body = item.get("content")
                            if isinstance(body, list):
                                body_str = "".join(b.get("text", "") for b in body if isinstance(b, dict))
                            else:
                                body_str = str(body) if body is not None else ""
                            formatted.append({
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": body_str,
                            })
                        elif isinstance(item, dict) and item.get("type") == "image":
                            # Support OpenAI multimodal vision format
                            source = item.get("source", {})
                            b64_data = source.get("data", "")
                            m_type = source.get("media_type", "image/png")
                            if b64_data:
                                formatted.append({
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "image_url",
                                            "image_url": {"url": f"data:{m_type};base64,{b64_data}"},
                                        }
                                    ],
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

        # Prune context window if budget exceeded
        total_tokens = sum(self._estimate_tokens(str(m.get("content", "")) + str(m.get("tool_calls", ""))) for m in formatted)
        while total_tokens > self.max_context_tokens and len(formatted) > 3:
            dropped = formatted.pop(1)
            total_tokens -= self._estimate_tokens(str(dropped.get("content", "")) + str(dropped.get("tool_calls", "")))

        return formatted

    def next_step(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[ToolSpec] | None = None,
    ) -> BrainResponse:
        """Execute one completion turn via Ollama /v1/chat/completions."""
        ollama_messages = self._format_and_prune_messages(messages)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": ollama_messages,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        if tool_specs:
            payload["tools"] = [self._map_tool_spec(s) for s in tool_specs]
            payload["tool_choice"] = "auto"

        response_data = self._http_request("/chat/completions", payload=payload)

        choices = response_data.get("choices", [])
        if not choices:
            return BrainResponse(text="", tool_calls=[])

        first_choice = choices[0]
        message_data = first_choice.get("message", {})

        assistant_text = message_data.get("content") or ""
        tool_calls: list[ToolCall] = []

        raw_calls = message_data.get("tool_calls") or []
        for raw in raw_calls:
            call_id = raw.get("id") or f"call_ollama_{len(tool_calls)}"
            fn = raw.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", {})

            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    parsed_args = {"_raw_arguments": raw_args}
            elif isinstance(raw_args, dict):
                parsed_args = raw_args
            else:
                parsed_args = {}

            tool_calls.append(ToolCall(id=call_id, name=fn_name, arguments=parsed_args))

        usage_data = response_data.get("usage")
        usage_dict = None
        if isinstance(usage_data, dict):
            usage_dict = {
                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0),
                "total_tokens": usage_data.get("total_tokens", 0),
            }

        return BrainResponse(
            text=assistant_text,
            tool_calls=tool_calls,
            usage=usage_dict,
        )
