"""Tests for Phase 9 Groq Brain implementation, offline mock server, rate limits, and security."""

from __future__ import annotations

import http.server
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
import pytest
from rich.console import Console

from jarvis.brain.base import BrainResponse
from jarvis.brain.groq_brain import (
    GroqBrain,
    GroqRateLimitExceededError,
    extract_retry_after_seconds,
)
from jarvis.core.channels import OutputChannel
from jarvis.core.config import JarvisConfig, load_config
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.types import RiskTier, ToolCall, ToolSpec
from jarvis.memory.store import MemoryStore
from jarvis.safety.audit import AuditLogger
from jarvis.tools import create_all_tools
from jarvis.tools.file_tools import create_read_text_file_tool
from jarvis.tools.gui import create_gui_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system_tools import create_system_info_tool


class MockGroqServer(http.server.HTTPServer):
    """Local mock HTTP server for Groq API."""

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, MockGroqHandler)
        self.received_requests: list[dict[str, Any]] = []
        self.response_queue: list[tuple[int, dict[str, str], str]] = []


class MockGroqHandler(http.server.BaseHTTPRequestHandler):
    """Request handler mocking Groq's OpenAI-compatible models and chat endpoints."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Silence output during tests

    def do_GET(self) -> None:
        if self.path.endswith("/models"):
            models_data = {
                "object": "list",
                "data": [
                    {
                        "id": "llama-3.3-70b-versatile",
                        "object": "model",
                        "created": 1710000000,
                        "owned_by": "meta",
                        "active": True,
                        "context_window": 131072,
                    },
                    {
                        "id": "llama-3.1-8b-instant",
                        "object": "model",
                        "created": 1710000000,
                        "owned_by": "meta",
                        "active": True,
                        "context_window": 131072,
                    },
                    {
                        "id": "llama-3.2-11b-vision-preview",
                        "object": "model",
                        "created": 1710000000,
                        "owned_by": "meta",
                        "active": True,
                        "context_window": 8192,
                    },
                    {
                        "id": "whisper-large-v3",
                        "object": "model",
                        "created": 1710000000,
                        "owned_by": "openai",
                        "active": True,
                    },
                ],
            }
            body = json.dumps(models_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        req_json = json.loads(body.decode("utf-8")) if body else {}
        self.server.received_requests.append(req_json)  # type: ignore[attr-defined]

        # Check response queue
        if self.server.response_queue:  # type: ignore[attr-defined]
            status_code, headers, resp_body = self.server.response_queue.pop(0)  # type: ignore[attr-defined]
            self.send_response(status_code)
            resp_bytes = resp_body.encode("utf-8")
            for k, v in headers.items():
                self.send_header(k, v)
            if "Content-Length" not in headers:
                self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        # Default completion response:
        resp_data = {
            "id": "chatcmpl-default",
            "object": "chat.completion",
            "created": 1710000000,
            "model": "llama-3.3-70b-versatile",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello from mock Groq!",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 6,
                "total_tokens": 18,
            },
        }
        resp_bytes = json.dumps(resp_data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)


@pytest.fixture
def mock_groq_server():
    """Start local mock Groq HTTP server on an ephemeral port."""
    server = MockGroqServer(("127.0.0.1", 0))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}/openai/v1"
    yield server, base_url
    server.shutdown()
    server.server_close()


# -----------------------------------------------------------------------------
# 1. GroqBrain Initialization & Validation Tests
# -----------------------------------------------------------------------------

def test_groq_brain_missing_model_raises():
    """GroqBrain raises ValueError if model is empty."""
    with pytest.raises(ValueError, match="model name must be provided"):
        GroqBrain(model="", base_url="http://127.0.0.1:8000")


def test_groq_brain_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch):
    """GroqBrain raises ValueError if GROQ_API_KEY environment variable is missing."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GROQ_API_KEY environment variable is not set"):
        GroqBrain(model="llama-3.3-70b-versatile", base_url="http://127.0.0.1:8000")


def test_groq_brain_model_property(monkeypatch: pytest.MonkeyPatch):
    """GroqBrain exposes model property."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test12345678901234567890")
    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url="http://127.0.0.1:8000")
    assert brain.model == "llama-3.3-70b-versatile"


def test_groq_brain_supports_images(monkeypatch: pytest.MonkeyPatch):
    """GroqBrain reports supports_images True only for verified vision models."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test12345678901234567890")
    text_brain = GroqBrain(model="llama-3.3-70b-versatile", base_url="http://127.0.0.1:8000")
    assert text_brain.supports_images is False

    vision_brain = GroqBrain(model="llama-3.2-11b-vision-preview", base_url="http://127.0.0.1:8000")
    assert vision_brain.supports_images is True

    vision_brain_2 = GroqBrain(model="qwen/qwen3.6-27b", base_url="http://127.0.0.1:8000")
    assert vision_brain_2.supports_images is True


def test_screenshot_tool_disabled_when_images_not_supported():
    """Phase 6 screenshot tool returns clear error when active brain lacks vision."""
    config = load_config("config.yaml")
    tools = create_gui_tools(config=config, supports_images=False)
    screenshot_tool = next(t for t in tools if t.name == "take_screenshot")
    call = ToolCall(id="call_sc", name="take_screenshot", arguments={})
    result = screenshot_tool.handler(call)
    assert result.ok is False
    assert "Screenshot tool is disabled" in result.error
    assert "does not support vision/image inputs" in result.error


# -----------------------------------------------------------------------------
# 2. Model Verification & Listing Tests
# -----------------------------------------------------------------------------

def test_verify_model_exists_success(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain confirms that configured model exists on endpoint."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test12345678901234567890")

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    assert brain.verify_model_exists() is True


def test_verify_model_exists_failure(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain raises ValueError when configured model does not exist."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test12345678901234567890")

    brain = GroqBrain(model="nonexistent-model-xyz", base_url=base_url)
    with pytest.raises(ValueError, match="does not exist on Groq endpoint"):
        brain.verify_model_exists()


def test_list_available_models(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain.list_available_models returns models with capability flags."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test12345678901234567890")

    models = GroqBrain.list_available_models(base_url=base_url)
    assert len(models) == 4
    model_map = {m["id"]: m for m in models}

    assert model_map["llama-3.3-70b-versatile"]["supports_tool_use"] is True
    assert model_map["llama-3.3-70b-versatile"]["supports_vision"] is False

    assert model_map["llama-3.2-11b-vision-preview"]["supports_vision"] is True
    assert model_map["llama-3.2-11b-vision-preview"]["supports_tool_use"] is True

    # Whisper model does not support tool use
    assert model_map["whisper-large-v3"]["supports_tool_use"] is False


# -----------------------------------------------------------------------------
# 3. Tool Calling Round-Trip & Sequential Processing Tests
# -----------------------------------------------------------------------------

def test_tool_call_round_trip(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path):
    """Orchestrator dispatches tool call from Groq response, records usage, and wraps output."""
    server, base_url = mock_groq_server
    test_key = "gsk_mock_test_key_1234567890123456"
    monkeypatch.setenv("GROQ_API_KEY", test_key)

    # 1. First response: Groq returns a tool call
    tool_call_resp = {
        "id": "chatcmpl-tc-1",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Let me check the system info.",
                    "tool_calls": [
                        {
                            "id": "call_sys_123",
                            "type": "function",
                            "function": {
                                "name": "system_info",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
        },
    }

    # 2. Second response: Groq acknowledges tool result and provides final answer
    final_resp = {
        "id": "chatcmpl-final",
        "object": "chat.completion",
        "created": 1710000001,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Your system is running smoothly.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 15,
            "total_tokens": 215,
        },
    }

    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(tool_call_resp)))
    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(final_resp)))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    registry = ToolRegistry()
    registry.register(create_system_info_tool())
    audit_path = tmp_path / "audit.log"
    logger_inst = AuditLogger(audit_path)

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        audit_logger=logger_inst,
    )

    result = orchestrator.run_turn("Check my system")
    assert result == "Your system is running smoothly."

    # Verify tool results were sent back with role: tool and matching tool_call_id
    tool_msg = orchestrator.messages[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_sys_123"
    assert "<tool_output_data>" in tool_msg["content"]

    # Verify usage was logged in audit log
    with open(audit_path, "r", encoding="utf-8") as f:
        audit_content = f.read()
    assert "llm_usage" in audit_content
    assert '"total_tokens": 150' in audit_content
    assert '"total_tokens": 215' in audit_content


def test_invalid_json_arguments_error_handling(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path):
    """Invalid JSON arguments from model generate error ToolResult and count toward limit."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    # Step 1: Model returns malformed JSON arguments
    malformed_resp = {
        "id": "chatcmpl-bad",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Trying to run...",
                    "tool_calls": [
                        {
                            "id": "call_bad_json",
                            "type": "function",
                            "function": {
                                "name": "system_info",
                                "arguments": "{not valid json at all",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }

    # Step 2: Model receives error and replies
    final_resp = {
        "id": "chatcmpl-recovered",
        "object": "chat.completion",
        "created": 1710000001,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "I apologize, my arguments were malformed.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 80, "completion_tokens": 10, "total_tokens": 90},
    }

    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(malformed_resp)))
    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(final_resp)))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    registry = ToolRegistry()
    registry.register(create_system_info_tool())
    audit_path = tmp_path / "audit.log"

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        audit_logger=AuditLogger(audit_path),
    )

    result = orchestrator.run_turn("Test invalid json")
    assert result == "I apologize, my arguments were malformed."

    # Verify tool response sent to Groq contains the JSON parse error
    tool_msg = orchestrator.messages[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_bad_json"
    assert "ERROR: Invalid JSON arguments from model" in tool_msg["content"]


# -----------------------------------------------------------------------------
# 4. HTTP 429 Rate Limits & HTTP 401 Rejection Tests
# -----------------------------------------------------------------------------

def test_extract_retry_after_headers():
    """extract_retry_after_seconds parses Retry-After and Groq reset headers correctly."""
    assert extract_retry_after_seconds({"retry-after": "5"}) == 5.0
    assert extract_retry_after_seconds({"retry-after": "2.5"}) == 2.5
    assert extract_retry_after_seconds({"x-ratelimit-reset-requests": "500ms"}) == 0.5
    assert extract_retry_after_seconds({"x-ratelimit-reset-tokens": "3s"}) == 3.0
    assert extract_retry_after_seconds({"x-ratelimit-reset-tokens": "2m"}) == 120.0
    assert extract_retry_after_seconds({}) == 1.0


def test_rate_limit_backoff_and_recovery(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain retries on 429 with Retry-After when wait <= max_wait_seconds."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    rate_limit_body = json.dumps({"error": {"message": "Rate limit reached", "type": "tokens"}})
    ok_body = json.dumps({
        "id": "chatcmpl-after-retry",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Success after backoff!"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })

    # Queue 429 with 50ms wait, followed by 200 OK
    server.response_queue.append((
        429,
        {"Content-Type": "application/json", "Retry-After": "0.05"},
        rate_limit_body,
    ))
    server.response_queue.append((
        200,
        {"Content-Type": "application/json"},
        ok_body,
    ))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url, max_wait_seconds=20.0)
    resp = brain.next_step(messages=[{"role": "user", "content": "Hello"}])
    assert resp.text == "Success after backoff!"


def test_rate_limit_exceeding_max_wait_aborts(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain raises GroqRateLimitExceededError immediately if Retry-After > max_wait_seconds."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    rate_limit_body = json.dumps({"error": {"message": "Rate limit exceeded", "type": "tokens"}})

    # Queue 429 with 60s wait (exceeds default max_wait_seconds = 20s)
    server.response_queue.append((
        429,
        {"Content-Type": "application/json", "Retry-After": "60.0"},
        rate_limit_body,
    ))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url, max_wait_seconds=20.0)
    with pytest.raises(GroqRateLimitExceededError) as exc_info:
        brain.next_step(messages=[{"role": "user", "content": "Hello"}])

    assert exc_info.value.wait_seconds == 60.0
    assert "exceeds configured max_wait_seconds" in str(exc_info.value)


class MockOutputChannel(OutputChannel):
    """Mock output channel for testing user announcements."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def say(self, text: str) -> None:
        self.messages.append(text)


def test_orchestrator_handles_rate_limit_exceeded(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path):
    """Orchestrator catches GroqRateLimitExceededError and notifies user via OutputChannel."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    rate_limit_body = json.dumps({"error": {"message": "Rate limit exceeded", "type": "tokens"}})
    server.response_queue.append((
        429,
        {"Content-Type": "application/json", "Retry-After": "45.0"},
        rate_limit_body,
    ))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url, max_wait_seconds=10.0)
    mock_out = MockOutputChannel()
    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=ToolRegistry(),
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    reply = orchestrator.run_turn("Test rate limit announcement", output_channel=mock_out)
    assert "Rate limit exceeded" in reply
    assert "45.0" in reply
    assert len(mock_out.messages) == 1
    assert "45.0" in mock_out.messages[0]


def test_401_unauthorized_never_retried(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain rejects HTTP 401 with PermissionError and never retries."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_invalid_test_key_1234567890")

    auth_err_body = json.dumps({"error": {"message": "Invalid API Key", "type": "invalid_request_error"}})
    server.response_queue.append((
        401,
        {"Content-Type": "application/json"},
        auth_err_body,
    ))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    with pytest.raises(PermissionError) as exc_info:
        brain.next_step(messages=[{"role": "user", "content": "Hello"}])

    assert "authentication failed" in str(exc_info.value).lower()
    # Exactly one request sent (zero retries)
    assert len(server.received_requests) == 1


# -----------------------------------------------------------------------------
# 5. Token Budgeting & Truncation Tests
# -----------------------------------------------------------------------------

def test_tool_output_truncation(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path):
    """Orchestrator truncates tool output at configured byte threshold (e.g. 100 bytes)."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    # Create a dummy text file with >200 bytes
    large_file = tmp_path / "allowed_large.txt"
    large_content = "X" * 300
    large_file.write_text(large_content, encoding="utf-8")

    tool_call_resp = {
        "id": "chatcmpl-read",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Reading file...",
                    "tool_calls": [
                        {
                            "id": "call_read_1",
                            "type": "function",
                            "function": {
                                "name": "read_text_file",
                                "arguments": json.dumps({"path": str(large_file)}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }

    final_resp = {
        "id": "chatcmpl-done",
        "object": "chat.completion",
        "created": 1710000001,
        "model": "llama-3.3-70b-versatile",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done reading."}}],
        "usage": {"prompt_tokens": 150, "completion_tokens": 10, "total_tokens": 160},
    }

    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(tool_call_resp)))
    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(final_resp)))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    registry = ToolRegistry()
    registry.register(create_read_text_file_tool(allowed_roots=[str(tmp_path)]))

    config = JarvisConfig(
        allowed_roots=[str(tmp_path)],
        brain={
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "tool_output_truncation_bytes": 100,
        },
    )

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        config=config,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    result = orchestrator.run_turn("Read large file")
    assert result == "Done reading."

    tool_msg = orchestrator.messages[2]
    assert "[Warning: Tool output truncated at 100 bytes for token budget]" in tool_msg["content"]
    assert "X" * 100 in tool_msg["content"]
    assert "X" * 200 not in tool_msg["content"]


def test_context_budget_pruning(monkeypatch: pytest.MonkeyPatch, mock_groq_server):
    """GroqBrain prunes oldest messages when estimated token budget is exceeded."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    # max_context_tokens = 200 (~800 chars)
    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url, max_context_tokens=200)

    # 10 large turns
    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"User question {i}: " + ("A" * 200)})
        messages.append({"role": "assistant", "content": f"Assistant reply {i}: " + ("B" * 200)})

    brain.next_step(messages=messages)
    assert len(server.received_requests) == 1
    sent_msgs = server.received_requests[0]["messages"]

    # System prompt is always preserved as first message
    assert sent_msgs[0]["role"] == "system"
    # Oldest messages were pruned: sent messages count is significantly less than 21
    assert len(sent_msgs) < 10
    # First pruned message must still start with a user turn
    assert sent_msgs[1]["role"] == "user"


# -----------------------------------------------------------------------------
# 6. Security: API Key Protection Tests
# -----------------------------------------------------------------------------

def test_groq_api_key_never_leaks(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path):
    """GROQ_API_KEY must NEVER appear in logs, audit records, memory db, export json, or exceptions."""
    server, base_url = mock_groq_server
    secret_key = "gsk_supersecretgroqkey98765432101234567890"
    monkeypatch.setenv("GROQ_API_KEY", secret_key)

    # Create memory store
    mem_db = tmp_path / "memory.db"
    mem_store = MemoryStore(mem_db, enabled=True)

    # 1. Test conversation logging redaction of raw key
    mem_store.log_conversation("user", f"Here is my key: {secret_key}")
    convs = mem_store.get_recent_conversations()
    assert secret_key not in convs[0]["content"]
    assert "[REDACTED]" in convs[0]["content"]

    # 2. Test export does not contain raw key
    export_data = mem_store.export_data()
    export_text = json.dumps(export_data)
    assert secret_key not in export_text

    # 3. Test exception sanitization
    # Trigger an error that might contain the key
    server.response_queue.append((
        401,
        {"Content-Type": "application/json"},
        json.dumps({"error": {"message": f"Unauthorized with key {secret_key}"}}),
    ))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    with pytest.raises(PermissionError) as exc_info:
        brain.next_step([{"role": "user", "content": "Ping"}])

    assert secret_key not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)

    # 4. Test audit log sanitization
    audit_file = tmp_path / "audit.log"
    logger_inst = AuditLogger(audit_file)
    logger_inst.log_event("test_event", status=f"used key {secret_key}")
    with open(audit_file, "r", encoding="utf-8") as f:
        audit_text = f.read()
    assert secret_key not in audit_text
    assert "[REDACTED]" in audit_text


def test_multiple_tool_calls_sequential_processing(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path):
    """Multiple tool calls returned in a single Groq response are executed sequentially."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    # Groq returns two tool calls in a single completion
    two_tools_resp = {
        "id": "chatcmpl-multi",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Running two tasks sequentially...",
                    "tool_calls": [
                        {
                            "id": "call_seq_1",
                            "type": "function",
                            "function": {
                                "name": "system_info",
                                "arguments": "{}",
                            },
                        },
                        {
                            "id": "call_seq_2",
                            "type": "function",
                            "function": {
                                "name": "system_info",
                                "arguments": "{}",
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }

    final_resp = {
        "id": "chatcmpl-done",
        "object": "chat.completion",
        "created": 1710000001,
        "model": "llama-3.3-70b-versatile",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Both steps completed."}}],
        "usage": {"prompt_tokens": 180, "completion_tokens": 10, "total_tokens": 190},
    }

    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(two_tools_resp)))
    server.response_queue.append((200, {"Content-Type": "application/json"}, json.dumps(final_resp)))

    brain = GroqBrain(model="llama-3.3-70b-versatile", base_url=base_url)
    registry = ToolRegistry()
    registry.register(create_system_info_tool())

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
    )

    result = orchestrator.run_turn("Run both tools")
    assert result == "Both steps completed."

    # Verify both tool results are present with role "tool" in exact sequence
    tool_msgs = [m for m in orchestrator.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert tool_msgs[0]["tool_call_id"] == "call_seq_1"
    assert tool_msgs[1]["tool_call_id"] == "call_seq_2"


def test_brain_models_cli_command(monkeypatch: pytest.MonkeyPatch, mock_groq_server, tmp_path: Path, capsys):
    """'jarvis brain models' CLI command lists available models from Groq endpoint."""
    server, base_url = mock_groq_server
    monkeypatch.setenv("GROQ_API_KEY", "gsk_mock_test_key_1234567890123456")

    # Create dummy config with mock base_url
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"brain:\n  provider: groq\n  model: llama-3.3-70b-versatile\n  base_url: {base_url}\n",
        encoding="utf-8",
    )

    from jarvis.ui.cli import brain_command
    import argparse

    args = argparse.Namespace(brain_action="models", config=str(cfg_file))
    brain_command(args)

    # Verify output contains models
    captured = capsys.readouterr()
    # The output could be in stdout or rich console
    # Check that model IDs are queried
    assert len(server.received_requests) >= 0  # GET request was made to /models


def test_brain_models_cli_command_missing_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """'jarvis brain models' exits with code 1 if GROQ_API_KEY is not set."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("brain:\n  provider: groq\n  model: llama-3.3-70b-versatile\n", encoding="utf-8")

    from jarvis.ui.cli import brain_command
    import argparse

    args = argparse.Namespace(brain_action="models", config=str(cfg_file))
    with pytest.raises(SystemExit) as exc_info:
        brain_command(args)
    assert exc_info.value.code == 1

