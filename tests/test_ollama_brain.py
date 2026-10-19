"""Unit and integration tests for OllamaBrain."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from jarvis.brain.base import BrainResponse
from jarvis.brain.ollama_brain import OllamaBrain, normalize_ollama_base_url
from jarvis.core.config import JarvisConfig
from jarvis.core.types import ToolSpec


def test_normalize_ollama_base_url() -> None:
    """Verify base URL normalization appends /v1 and strips trailing slashes."""
    assert normalize_ollama_base_url("http://localhost:11434") == "http://localhost:11434/v1"
    assert normalize_ollama_base_url("http://localhost:11434/") == "http://localhost:11434/v1"
    assert normalize_ollama_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    assert normalize_ollama_base_url("https://my-ollama.cloud.run") == "https://my-ollama.cloud.run/v1"
    assert normalize_ollama_base_url("") == "http://127.0.0.1:11434/v1"


def test_ollama_brain_init_and_properties() -> None:
    """Verify initialization and vision detection."""
    brain = OllamaBrain(
        model="llama3.2:latest",
        base_url="http://127.0.0.1:11434",
        api_key="custom_token",
    )
    assert brain.model == "llama3.2:latest"
    assert brain.provider == "ollama"
    assert brain.base_url == "http://127.0.0.1:11434/v1"
    assert brain.api_key == "custom_token"
    assert brain.supports_images is False

    vision_brain = OllamaBrain(model="llava:13b")
    assert vision_brain.supports_images is True

    vision_brain2 = OllamaBrain(model="qwen2.5-vl:7b")
    assert vision_brain2.supports_images is True


def test_ollama_brain_from_config(tmp_path) -> None:
    """Verify initialization from JarvisConfig object."""
    from jarvis.core.config import BrainConfig
    cfg = JarvisConfig(
        allowed_roots=["."],
        brain=BrainConfig(
            provider="ollama",
            model="llama3.1:8b",
            base_url="http://127.0.0.1:11434/v1",
        ),
    )
    brain = OllamaBrain.from_config(cfg)
    assert brain.model == "llama3.1:8b"
    assert brain.provider == "ollama"


def test_ollama_headers_with_and_without_api_key() -> None:
    """Verify Authorization Bearer header is included only when api_key is present."""
    brain_no_key = OllamaBrain(model="llama3.2", api_key=None)
    headers = brain_no_key._get_headers()
    assert "Authorization" not in headers

    brain_with_key = OllamaBrain(model="llama3.2", api_key="secret_test_key_123")
    headers_key = brain_with_key._get_headers()
    assert headers_key.get("Authorization") == "Bearer secret_test_key_123"


def test_ollama_next_step_plain_text() -> None:
    """Verify next_step parses standard conversational text and token usage."""
    brain = OllamaBrain(model="llama3.2:latest")

    fake_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "All systems online, Sir. How may I be of service?",
                }
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 12,
            "total_tokens": 27,
        },
    }

    with patch.object(brain, "_http_request", return_value=fake_response) as mock_http:
        resp = brain.next_step([{"role": "user", "content": "Status report, Jarvis."}])

        assert resp.text == "All systems online, Sir. How may I be of service?"
        assert resp.has_tool_calls is False
        assert resp.usage == {"prompt_tokens": 15, "completion_tokens": 12, "total_tokens": 27}
        assert mock_http.call_count == 1


def test_ollama_next_step_tool_call() -> None:
    """Verify next_step parses OpenAI-compatible function calling payload."""
    brain = OllamaBrain(model="llama3.2:latest")

    fake_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_12345",
                            "type": "function",
                            "function": {
                                "name": "open_app",
                                "arguments": '{"name": "calc"}',
                            },
                        }
                    ],
                }
            }
        ],
    }

    from jarvis.core.types import RiskTier
    tool_specs = [
        ToolSpec(
            name="open_app",
            description="Launch application.",
            input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            risk_tier=RiskTier.AUTO,
        )
    ]

    with patch.object(brain, "_http_request", return_value=fake_response):
        resp = brain.next_step([{"role": "user", "content": "Open calculator"}], tool_specs=tool_specs)

        assert resp.has_tool_calls is True
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].id == "call_12345"
        assert resp.tool_calls[0].name == "open_app"
        assert resp.tool_calls[0].arguments == {"name": "calc"}


def test_ollama_list_available_models_via_v1_models() -> None:
    """Verify list_available_models works with /v1/models response."""
    fake_v1_resp = {
        "data": [
            {"id": "llama3.2:latest", "owned_by": "ollama"},
            {"id": "llava:latest", "owned_by": "ollama"},
        ]
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_v1_resp).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        models = OllamaBrain.list_available_models()
        assert len(models) == 2
        ids = [m["id"] for m in models]
        assert "llama3.2:latest" in ids
        assert "llava:latest" in ids

        llava_item = next(m for m in models if m["id"] == "llava:latest")
        assert llava_item["supports_vision"] is True


def test_ollama_verify_model_exists_success_and_failure() -> None:
    """Verify verify_model_exists validates model presence and raises helpful message on absence."""
    brain = OllamaBrain(model="llama3.2")

    with patch.object(OllamaBrain, "list_available_models", return_value=[{"id": "llama3.2:latest"}]):
        assert brain.verify_model_exists() is True

    with patch.object(OllamaBrain, "list_available_models", return_value=[{"id": "qwen2.5:7b"}]):
        with pytest.raises(ValueError) as excinfo:
            brain.verify_model_exists()
        assert "Run 'ollama pull llama3.2' to install this model" in str(excinfo.value)
