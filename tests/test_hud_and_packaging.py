"""Tests for Phase 8.1: Hardened Local Web HUD, Token handling in fragment,
Host and Origin validation, CSP headers, zero-token logging, and SHA-256 audit chaining.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any
import pytest
from fastapi.testclient import TestClient

from jarvis.core.channels import Confirmer
from jarvis.safety.audit import AuditLogger, verify_audit_log
from jarvis.safety.confirmers import CompositeConfirmer
from jarvis.safety.killswitch import KillSwitch
from jarvis.ui.server import HudChannel, HudServer, is_allowed_host, is_allowed_origin


# -------------------------------------------------------------------------
# Mock Confirmers for Racing Tests
# -------------------------------------------------------------------------


class DelayedConfirmer(Confirmer):
    """Confirmer that responds after a configurable delay."""

    def __init__(self, answer: bool, delay_seconds: float) -> None:
        self.answer = answer
        self.delay_seconds = delay_seconds
        self.dismissed = False

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        start = time.time()
        while time.time() - start < self.delay_seconds:
            if self.dismissed:
                return False
            time.sleep(0.01)
        return self.answer

    def dismiss(self) -> None:
        self.dismissed = True


# -------------------------------------------------------------------------
# 1. Token in Fragment Flow Works
# -------------------------------------------------------------------------


def test_token_in_fragment_flow_works() -> None:
    """HUD URL places token in URL fragment, and client authenticates over WS and REST header."""
    ks = KillSwitch()
    token = "test_fragment_token_98765"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    # 1. URL property contains token in fragment (#token=...), not query string (?token=...)
    assert f"#token={token}" in server.url
    assert f"?token={token}" not in server.url

    # 2. Root page GET / succeeds without query string and serves HTML
    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert "JARVIS HUD" in resp_root.text

    # 3. WebSocket connects to /ws without query params and authenticates via first message
    with client.websocket_connect("/ws") as ws:
        # First message delivers auth token
        ws.send_json({"type": "auth", "token": token})

        # Server responds with initial state and stats
        state_msg = ws.receive_json()
        assert state_msg["type"] == "state"
        assert state_msg["state"] == "idle"

        stats_msg = ws.receive_json()
        assert stats_msg["type"] == "stats"

        # Subsequent messages work
        ws.send_json({"type": "chat", "text": "hello from fragment flow"})
        time.sleep(0.05)
        assert server.channel.input_queue.get(timeout=1.0) == "hello from fragment flow"

    # 4. REST routes authenticate using X-Session-Token header
    resp_api = client.post(
        "/api/chat",
        headers={"X-Session-Token": token},
        json={"text": "hello from REST header"},
    )
    assert resp_api.status_code == 200
    assert resp_api.json()["status"] == "received"
    assert server.channel.input_queue.get(timeout=1.0) == "hello from REST header"


# -------------------------------------------------------------------------
# 2. Token in Query String Rejected
# -------------------------------------------------------------------------


def test_token_in_query_string_rejected() -> None:
    """Passing token in query string is strictly rejected on HTTP and WebSocket."""
    ks = KillSwitch()
    token = "forbidden_query_token"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    # 1. Root page with query string token is rejected with 400 Bad Request
    resp_page = client.get(f"/?token={token}")
    assert resp_page.status_code == 400
    assert "Token in query string is prohibited" in resp_page.text

    # 2. REST API with query string token is rejected with 400 Bad Request
    resp_api = client.post(f"/api/chat?token={token}", json={"text": "attempt"})
    assert resp_api.status_code == 400
    assert "Token in query string is prohibited" in resp_api.text

    # 3. WebSocket with query string token is closed with policy violation (1008)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws?token={token}") as ws:
            pass


# -------------------------------------------------------------------------
# 3. Wrong Host Header Rejected
# -------------------------------------------------------------------------


def test_wrong_host_rejected() -> None:
    """Requests with untrusted Host header are rejected with 400 or WebSocket 1008."""
    ks = KillSwitch()
    token = "host_test_token"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    # Allowed hosts pass
    assert is_allowed_host("127.0.0.1:8000", 8000) is True
    assert is_allowed_host("localhost:8000", 8000) is True
    assert is_allowed_host("127.0.0.1", 8000) is True
    assert is_allowed_host("localhost", 8000) is True

    # Disallowed hosts fail
    assert is_allowed_host("evil.com", 8000) is False
    assert is_allowed_host("192.168.1.100:8000", 8000) is False
    assert is_allowed_host("example.org", 8000) is False
    assert is_allowed_host(None, 8000) is False

    # HTTP request with untrusted Host header returns 400 Bad Request
    resp_bad_host = client.get("/", headers={"Host": "evil.com"})
    assert resp_bad_host.status_code == 400
    assert "Untrusted Host" in resp_bad_host.text

    # WebSocket connection with untrusted Host is rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"Host": "evil.com"}) as ws:
            pass


# -------------------------------------------------------------------------
# 4. Missing or Invalid Token Rejected
# -------------------------------------------------------------------------


def test_missing_or_invalid_token_rejected() -> None:
    """Endpoints and WebSockets reject requests missing authentication or with invalid token."""
    ks = KillSwitch()
    token = "correct_token_123"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    # 1. REST endpoint without header returns 401 Unauthorized
    resp_no_token = client.post("/api/chat", json={"text": "hello"})
    assert resp_no_token.status_code == 401

    # 2. REST endpoint with incorrect token returns 401 Unauthorized
    resp_wrong_token = client.post(
        "/api/chat",
        headers={"X-Session-Token": "wrong_token"},
        json={"text": "hello"},
    )
    assert resp_wrong_token.status_code == 401

    # 3. WebSocket sending invalid token in first message is closed
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": "invalid_token"})
            _ = ws.receive_json()

    # 4. WebSocket sending non-auth message first is closed
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "chat", "text": "sneak in"})
            _ = ws.receive_json()


# -------------------------------------------------------------------------
# 5. Token Never Appears in Captured Server Logs
# -------------------------------------------------------------------------


def test_token_never_appears_in_captured_server_logs(caplog: pytest.LogCaptureFixture) -> None:
    """The session token never appears in any server logging output."""
    ks = KillSwitch()
    secret_token = "ultra_secret_token_never_logged_xyz123"
    server = HudServer(session_token=secret_token, kill_switch=ks, allow_testserver=True)

    with caplog.at_level(logging.DEBUG):
        client = TestClient(server.app)

        # Access root page
        _ = client.get("/")

        # Valid REST call
        _ = client.post(
            "/api/status",
            headers={"X-Session-Token": secret_token},
        )

        # Invalid REST call
        _ = client.post(
            "/api/status",
            headers={"X-Session-Token": "bad_token"},
        )

        # WebSocket connection
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": secret_token})
            _ = ws.receive_json()  # state
            _ = ws.receive_json()  # stats
            ws.send_json({"type": "chat", "text": "test logging"})

    # Assert secret token does not appear in any log record message or formatting
    for record in caplog.records:
        msg = record.getMessage()
        assert secret_token not in msg, f"Secret token leaked in log record: {msg}"


# -------------------------------------------------------------------------
# 6. CSP Header Present and No Wildcard CORS
# -------------------------------------------------------------------------


def test_csp_header_present_and_no_wildcard_cors() -> None:
    """Content-Security-Policy header is present, style-src lacks unsafe-inline, and wildcard CORS is absent."""
    ks = KillSwitch()
    server = HudServer(kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    resp = client.get("/")
    assert resp.status_code == 200

    # Content-Security-Policy header present
    csp = resp.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert "object-src 'none'" in csp

    # No wildcard CORS
    cors = resp.headers.get("Access-Control-Allow-Origin")
    assert cors != "*"


# -------------------------------------------------------------------------
# 7. Audit Log SHA-256 Hash Chain and Verification Command
# -------------------------------------------------------------------------


def test_audit_sha256_chain_and_verify(tmp_path: Path) -> None:
    """Audit logger generates cryptographic SHA-256 hash chains verified by verify_audit_log."""
    audit_file = tmp_path / "tamper_test.log"
    logger = AuditLogger(audit_file)

    # Log 3 events
    logger.log_call_requested("tool_a", {"param": "val1"})
    logger.log_decision("tool_a", {"param": "val1"}, decision_reason="Approved")
    logger.log_executed("tool_a", {"param": "val1"}, result_summary="Success")
    logger.close()

    # 1. Legitimate log passes verification
    is_valid, count, err = verify_audit_log(audit_file)
    assert is_valid is True
    assert count == 3
    assert err is None

    # Inspect records on disk: verify prev_hash and record_hash exist
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    rec2 = json.loads(lines[2])

    assert rec0["prev_hash"] == "0" * 64
    assert rec1["prev_hash"] == rec0["record_hash"]
    assert rec2["prev_hash"] == rec1["record_hash"]

    # 2. Tampered record fails verification
    tampered_rec1 = json.loads(lines[1])
    tampered_rec1["decision_reason"] = "Tampered decision"
    lines[1] = json.dumps(tampered_rec1)
    audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    is_valid_tampered, _, err_tampered = verify_audit_log(audit_file)
    assert is_valid_tampered is False
    assert err_tampered is not None
    assert "Hash mismatch" in err_tampered or "Broken hash chain" in err_tampered


# -------------------------------------------------------------------------
# 8. Confirmation Race (First Answer Wins)
# -------------------------------------------------------------------------


def test_confirmation_race_first_answer_wins() -> None:
    """In multi-channel confirmation, the first answer wins and dismisses others."""
    # Scenario A: Fast Approver (0.05s) vs Slow Denier (0.5s) -> Fast Approver WINS (True)
    fast_approver = DelayedConfirmer(answer=True, delay_seconds=0.05)
    slow_denier = DelayedConfirmer(answer=False, delay_seconds=0.5)

    composite_a = CompositeConfirmer([fast_approver, slow_denier])
    result_a = composite_a.confirm("Action A")

    assert result_a is True
    assert slow_denier.dismissed is True

    # Scenario B: Fast Denier (0.05s) vs Slow Approver (0.5s) -> Fast Denier WINS (False)
    fast_denier = DelayedConfirmer(answer=False, delay_seconds=0.05)
    slow_approver = DelayedConfirmer(answer=True, delay_seconds=0.5)

    composite_b = CompositeConfirmer([fast_denier, slow_approver])
    result_b = composite_b.confirm("Action B")

    assert result_b is False
    assert slow_approver.dismissed is True


# -------------------------------------------------------------------------
# 9. Kill Button Triggers the Switch
# -------------------------------------------------------------------------


def test_kill_button_triggers_switch() -> None:
    """Kill endpoint in HUD immediately triggers the process-wide KillSwitch."""
    ks = KillSwitch()
    token = "kill_test_token"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    assert ks.is_set() is False

    # POST to /api/kill with X-Session-Token triggers switch
    resp = client.post("/api/kill", headers={"X-Session-Token": token})
    assert resp.status_code == 200
    assert resp.json()["status"] == "killed"

    assert ks.is_set() is True


def test_websocket_kill_message_triggers_switch() -> None:
    """Sending kill event over authenticated WebSocket triggers the KillSwitch."""
    ks = KillSwitch()
    token = "ws_kill_token"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    assert ks.is_set() is False

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        _ = ws.receive_json()  # state
        _ = ws.receive_json()  # stats
        ws.send_json({"type": "kill"})
        time.sleep(0.05)

    assert ks.is_set() is True


# -------------------------------------------------------------------------
# 10. HUD Never Receives Secrets (Audit Redaction Before Broadcast)
# -------------------------------------------------------------------------


def test_hud_never_receives_secrets() -> None:
    """All audit events and assistant messages are redacted prior to HUD broadcast."""
    ks = KillSwitch()
    token = "redact_test_token"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    secret_key = "sk-ant-api03-secretkey1234567890abcdef1234567890"
    card_num = "4111 2222 3333 4444"

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        _ = ws.receive_json()  # state
        _ = ws.receive_json()  # stats

        # 1. Broadcast an audit record containing secrets
        audit_record = {
            "timestamp": "2026-10-07T12:00:00Z",
            "event": "executed",
            "tool_name": "read_text_file",
            "arguments": {"path": "keys.env"},
            "result_summary": f"Content contains Anthropic key {secret_key} and card {card_num}",
        }
        server.channel.broadcast_audit(audit_record)

        # Receive audit payload
        audit_payload = ws.receive_json()
        assert audit_payload["type"] == "audit"
        rec = audit_payload["record"]

        # Secrets MUST NOT appear anywhere in the transmitted record
        rec_json = json.dumps(rec)
        assert secret_key not in rec_json
        assert card_num not in rec_json
        assert "[REDACTED]" in rec_json

        # 2. Assistant message via say() with secrets
        server.channel.say(f"Here is your key: {secret_key}")
        chat_payload = ws.receive_json()
        assert chat_payload["type"] == "chat"
        chat_content = chat_payload["content"]

        assert secret_key not in chat_content
        assert "[REDACTED]" in chat_content


# -------------------------------------------------------------------------
# 11. Acceptance Test: Multi-Channel CLI + HUD Flow & Audit Log Verification
# -------------------------------------------------------------------------


def test_acceptance_multichannel_hud_flow(tmp_path: Path) -> None:
    """Acceptance: multi-channel HUD approvals/denials appear correctly in audit log."""
    audit_file = tmp_path / "acceptance_audit.log"
    logger = AuditLogger(audit_file)

    ks = KillSwitch()
    token = "acceptance_token"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True)
    client = TestClient(server.app)

    logger.set_listener(server.channel.broadcast_audit)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        _ = ws.receive_json()  # state
        _ = ws.receive_json()  # stats

        # Log an executed event
        logger.log_executed("create_text_file", {"path": "test.txt"}, "Created file")
        ws_msg = ws.receive_json()
        assert ws_msg["type"] == "audit"
        assert ws_msg["record"]["tool_name"] == "create_text_file"

    # Verify audit file contains the executed record and valid SHA-256 hash
    is_valid, count, err = verify_audit_log(audit_file)
    assert is_valid is True
    assert count == 1
    assert err is None

    logger.close()


# -------------------------------------------------------------------------
# 12. Synthetic testserver Host Rejected in Normal Mode
# -------------------------------------------------------------------------


def test_testserver_rejected_in_normal_mode() -> None:
    """Synthetic testserver host is strictly rejected in normal mode (allow_testserver=False)."""
    # 1. Direct function check: rejected when allow_testserver=False
    assert is_allowed_host("testserver", 8000, allow_testserver=False) is False
    assert is_allowed_host("testserver:8000", 8000, allow_testserver=False) is False
    # Allowed when allow_testserver=True
    assert is_allowed_host("testserver", 8000, allow_testserver=True) is True
    assert is_allowed_host("testserver:8000", 8000, allow_testserver=True) is True

    # 2. Server in normal production mode (allow_testserver=False)
    server = HudServer(allow_testserver=False)
    client = TestClient(server.app, base_url="http://127.0.0.1:8000")

    # Normal local Host succeeds
    resp_ok = client.get("/")
    assert resp_ok.status_code == 200

    # Sending Host: testserver is rejected with 400 Bad Request
    resp_rejected = client.get("/", headers={"Host": "testserver"})
    assert resp_rejected.status_code == 400
    assert "Untrusted Host" in resp_rejected.text

    # WebSocket with Host: testserver is rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/ws", headers={"Host": "testserver"}) as ws:
            pass


# -------------------------------------------------------------------------
# 13. Unauthenticated WebSocket Isolation & Timeout Dropping
# -------------------------------------------------------------------------


def test_unauthenticated_websocket_receives_no_broadcasts_and_times_out() -> None:
    """Unauthenticated WebSocket receives no transcript, audit, or telemetry, and is dropped after timeout."""
    ks = KillSwitch()
    token = "valid_auth_token_xyz"
    # Set short auth timeout for fast test execution
    server = HudServer(
        session_token=token,
        kill_switch=ks,
        allow_testserver=True,
        auth_timeout_seconds=0.2,
    )
    client = TestClient(server.app)

    # Connect without sending auth token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as ws:
            # While socket is unauthenticated, server broadcasts various events
            server.channel.say("Private conversation transcript")
            server.channel.broadcast_audit({
                "event": "executed",
                "tool_name": "secret_tool",
                "arguments": {},
            })
            server.broadcast({"type": "stats", "cpu": 99, "ram": 99, "disk": 99})

            # Wait past timeout for server to drop the connection
            time.sleep(0.3)
            # Attempting to receive must fail because server disconnected/closed socket
            _ = ws.receive_json()


# -------------------------------------------------------------------------
# 14. Packaging & Dependencies Completeness
# -------------------------------------------------------------------------


def test_pyproject_dependencies_completeness() -> None:
    """Verify that pyproject.toml declares all required runtime packages including groq."""
    pyproject_path = Path("pyproject.toml")
    assert pyproject_path.exists(), "pyproject.toml not found"

    content = pyproject_path.read_text(encoding="utf-8")
    assert "groq==" in content or '"groq"' in content, "groq dependency missing from pyproject.toml"
    assert "anthropic==" in content, "anthropic dependency missing from pyproject.toml"
    assert "fastapi==" in content, "fastapi dependency missing from pyproject.toml"
    assert "uvicorn==" in content, "uvicorn dependency missing from pyproject.toml"
    assert "playwright==" in content, "playwright dependency missing from pyproject.toml"


# -------------------------------------------------------------------------
# 15. Iron Man HUD Holographic Elements & Persona Delivery
# -------------------------------------------------------------------------


def test_iron_man_hud_elements_and_persona() -> None:
    """Verify Iron Man Arc Reactor elements, circular gauges, SFX controls, and persona title."""
    ks = KillSwitch()
    token = "stark_token_007"
    server = HudServer(session_token=token, kill_switch=ks, allow_testserver=True, user_title="Sir")
    client = TestClient(server.app)

    # 1. Root page serves Iron Man HUD elements
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "JARVIS HUD" in html
    assert "arc-reactor-canvas" in html
    assert "sfx-toggle-btn" in html
    assert "cpu-circle" in html
    assert "ram-circle" in html
    assert "disk-circle" in html
    assert "STARK DEFENSE" in html

    # 2. WebSocket transmits user_title in initial state
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        state_msg = ws.receive_json()
        assert state_msg["type"] == "state"
        assert state_msg["state"] == "idle"
        assert state_msg["user_title"] == "Sir"


