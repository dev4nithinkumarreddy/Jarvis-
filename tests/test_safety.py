"""Tests for Jarvis safety layer: path guarding, permissions, audit, kill switch, and confirmers."""

import json
import os
from pathlib import Path
import sys
import threading
import time
import pytest

from jarvis.core.types import (
    RiskTier,
    SessionState,
    ToolCall,
    ToolSpec,
)
from jarvis.safety.audit import (
    AuditLogger,
    redact_arguments,
    truncate_summary,
    verify_audit_log,
)
from jarvis.safety.confirmers import CLIConfirmer
from jarvis.safety.killswitch import (
    KillSwitch,
    normalize_hotkey,
)
from jarvis.safety.path_guard import (
    PathNotAllowed,
    resolve_allowed,
)
from jarvis.safety.permissions import PermissionEngine


# -----------------------------------------------------------------------------
# 1. Path Guard Tests (Path traversal, symlink/junction escape, OS tricks)
# -----------------------------------------------------------------------------

def test_resolve_allowed_valid_subpath(tmp_path: Path):
    """Path inside allowed root resolves cleanly."""
    root = (tmp_path / "allowed").resolve()
    root.mkdir()
    child = root / "subdir" / "file.txt"
    child.parent.mkdir()
    child.write_text("ok", encoding="utf-8")

    resolved = resolve_allowed(child, [root])
    assert resolved == child.resolve()


def test_resolve_allowed_path_traversal_blocked(tmp_path: Path):
    """Parent directory traversal ('../') attempting escape is rejected."""
    root = (tmp_path / "allowed").resolve()
    root.mkdir()
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")

    # Attempt ../ escape from root
    traversal_path = root / ".." / "outside.txt"
    with pytest.raises(PathNotAllowed) as exc:
        resolve_allowed(traversal_path, [root])
    assert "outside.txt" in str(exc.value)


def test_resolve_allowed_symlink_or_junction_escape_blocked(tmp_path: Path):
    """Symlink or directory junction escaping allowed root is rejected for real."""
    root = (tmp_path / "allowed").resolve()
    root.mkdir()
    external_dir = (tmp_path / "external").resolve()
    external_dir.mkdir()
    secret_file = external_dir / "secret.txt"
    secret_file.write_text("confidential", encoding="utf-8")

    link_path = root / "link_escape"

    # Use NTFS directory junction on Windows (no elevation needed) or os.symlink on Unix
    created_link = False
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(external_dir), str(link_path))
        created_link = True
    else:
        try:
            os.symlink(external_dir, link_path, target_is_directory=True)
            created_link = True
        except (OSError, NotImplementedError):
            pass

    assert created_link is True, "Real directory junction or symlink creation must succeed"

    escaped_file = link_path / "secret.txt"
    with pytest.raises(PathNotAllowed):
        resolve_allowed(escaped_file, [root])


def test_resolve_allowed_windows_case_and_separator_tricks(tmp_path: Path):
    """Handle mixed forward/backward slashes and Windows case insensitivity."""
    root = (tmp_path / "AllowedRoot").resolve()
    root.mkdir()
    sub = root / "SubDir"
    sub.mkdir()
    test_file = sub / "Data.txt"
    test_file.write_text("sample", encoding="utf-8")

    # Mixed slashes and dot segments
    mixed_str = f"{str(root).replace(os.sep, '/')}/SubDir/../SubDir/./Data.txt"
    resolved = resolve_allowed(mixed_str, [root])
    assert resolved == test_file.resolve()

    # Case variations on Windows
    if sys.platform == "win32":
        lower_str = str(test_file).lower()
        resolved_lower = resolve_allowed(lower_str, [root])
        assert resolved_lower == test_file.resolve()

        upper_root = Path(str(root).upper())
        resolved_upper = resolve_allowed(test_file, [upper_root])
        assert resolved_upper == test_file.resolve()


def test_resolve_allowed_environment_variable_expansion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ensure environment variables are expanded and checked."""
    root = (tmp_path / "allowed").resolve()
    root.mkdir()
    sub = root / "config"
    sub.mkdir()
    monkeypatch.setenv("JARVIS_TEST_DIR", str(sub))

    # Reference through env var
    resolved = resolve_allowed("$JARVIS_TEST_DIR/file.json" if sys.platform != "win32" else "%JARVIS_TEST_DIR%/file.json", [root])
    assert resolved == (sub / "file.json").resolve()


# -----------------------------------------------------------------------------
# 2. Permissions Engine & Taint Rule Tests
# -----------------------------------------------------------------------------

def test_blocked_tools_never_execute():
    """Tools marked RiskTier.BLOCKED are rejected regardless of session state."""
    tool = ToolSpec(
        name="delete_system_disk",
        description="Dangerous operation",
        input_schema={},
        risk_tier=RiskTier.BLOCKED,
    )
    call = ToolCall(id="call_1", name="delete_system_disk")

    # Untainted session
    decision_clean = PermissionEngine.decide(tool, call, SessionState(tainted=False))
    assert decision_clean.allowed is False
    assert decision_clean.needs_confirmation is False
    assert "BLOCKED" in decision_clean.reason

    # Tainted session
    decision_tainted = PermissionEngine.decide(tool, call, SessionState(tainted=True))
    assert decision_tainted.allowed is False
    assert decision_tainted.needs_confirmation is False
    assert "BLOCKED" in decision_tainted.reason


def test_auto_tools_run():
    """Tools marked RiskTier.AUTO run automatically without confirmation."""
    tool = ToolSpec(
        name="list_files",
        description="Read directory listing",
        input_schema={},
        risk_tier=RiskTier.AUTO,
    )
    call = ToolCall(id="call_2", name="list_files")

    decision = PermissionEngine.decide(tool, call, SessionState(tainted=False))
    assert decision.allowed is True
    assert decision.needs_confirmation is False


def test_confirm_tool_without_taint():
    """RiskTier.CONFIRM requires confirmation without taint warning if untainted."""
    tool = ToolSpec(
        name="write_file",
        description="Write data to file",
        input_schema={},
        risk_tier=RiskTier.CONFIRM,
    )
    call = ToolCall(id="call_3", name="write_file")

    decision = PermissionEngine.decide(tool, call, SessionState(tainted=False))
    assert decision.allowed is True
    assert decision.needs_confirmation is True
    assert decision.warning is None
    assert "requires user confirmation" in decision.reason


def test_confirm_tool_tainted_warning_appears():
    """When session is tainted, CONFIRM decision includes visible warning."""
    tool = ToolSpec(
        name="execute_script",
        description="Run local script",
        input_schema={},
        risk_tier=RiskTier.CONFIRM,
    )
    call = ToolCall(id="call_4", name="execute_script")

    session = SessionState(tainted=True)
    decision = PermissionEngine.decide(tool, call, session)
    assert decision.allowed is True
    assert decision.needs_confirmation is True
    assert decision.warning is not None
    assert "WARNING" in decision.warning
    assert "tainted" in decision.warning.lower()
    assert decision.warning in decision.reason

    desc, warn = PermissionEngine.format_confirmation_prompt(tool, call, session)
    assert warn is not None
    assert "tainted" in warn.lower()


# -----------------------------------------------------------------------------
# 3. CLI Confirmer Tests
# -----------------------------------------------------------------------------

def test_confirmer_user_approves():
    """Confirmer returns True when user types 'y' or 'yes'."""
    confirmer_y = CLIConfirmer(timeout_seconds=5.0, input_fn=lambda _: "y")
    assert confirmer_y.confirm("Perform safe action") is True

    confirmer_yes = CLIConfirmer(timeout_seconds=5.0, input_fn=lambda _: "YES")
    assert confirmer_yes.confirm("Perform safe action") is True


def test_confirmer_user_denies():
    """Confirmer returns False when user enters 'n' or empty input."""
    confirmer_n = CLIConfirmer(timeout_seconds=5.0, input_fn=lambda _: "n")
    assert confirmer_n.confirm("Perform action") is False

    confirmer_empty = CLIConfirmer(timeout_seconds=5.0, input_fn=lambda _: "")
    assert confirmer_empty.confirm("Perform action") is False


def test_confirmer_timeout_denied():
    """Confirmer times out and defaults to No (False)."""
    def slow_input(_):
        time.sleep(0.3)
        return "y"

    confirmer = CLIConfirmer(timeout_seconds=0.05, input_fn=slow_input)
    assert confirmer.confirm("Perform action", warning="Dangerous") is False


# -----------------------------------------------------------------------------
# 4. Audit Logger Tests (Redaction, Truncation, Append-Only)
# -----------------------------------------------------------------------------

def test_audit_redaction_secrets():
    """Arguments with secret/key/password/token are redacted recursively."""
    args = {
        "user": "alice",
        "api_key": "secret_abc123",
        "nested": {
            "token": "bearer_987",
            "password_hash": "hashed_val",
            "safe_param": 42,
        },
        "items": [
            {"secret_auth": "xyz", "name": "item1"},
        ],
        "list_of_secrets": ["token1", "token2"],
    }
    redacted = redact_arguments(args)
    assert redacted["user"] == "alice"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["password_hash"] == "[REDACTED]"
    assert redacted["nested"]["safe_param"] == 42
    assert redacted["items"][0]["secret_auth"] == "[REDACTED]"
    assert redacted["items"][0]["name"] == "item1"
    assert redacted["list_of_secrets"] == "[REDACTED]"


def test_audit_summary_truncation():
    """Result summary is truncated to 500 characters max."""
    long_text = "x" * 800
    truncated = truncate_summary(long_text, max_length=500)
    assert len(truncated) == 500
    assert truncated == "x" * 500


def test_audit_logger_append_only_across_runs(tmp_path: Path):
    """Audit logger appends records across multiple logger instances."""
    log_file = tmp_path / "audit.log"

    # Run 1: Write first two events
    with AuditLogger(log_file) as logger1:
        logger1.log_call_requested("tool_a", {"key": "secret1"})
        logger1.log_decision("tool_a", {"key": "secret1"}, decision_reason="Approved")

    # Run 2: Reopen in new instance and write third event
    with AuditLogger(log_file) as logger2:
        logger2.log_executed("tool_a", {"key": "secret1"}, result_summary="Executed OK")

    # Inspect JSONL content
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    record1 = json.loads(lines[0])
    record2 = json.loads(lines[1])
    record3 = json.loads(lines[2])

    assert record1["event"] == "call_requested"
    assert record1["arguments"]["key"] == "[REDACTED]"

    assert record2["event"] == "decision"
    assert record2["decision_reason"] == "Approved"

    assert record3["event"] == "executed"
    assert record3["result_summary"] == "Executed OK"


# -----------------------------------------------------------------------------
# 5. Kill Switch Tests
# -----------------------------------------------------------------------------

def test_kill_switch_trigger_and_is_set():
    """Kill switch trigger sets state and reset clears it."""
    ks = KillSwitch()
    assert ks.is_set() is False

    ks.trigger()
    assert ks.is_set() is True

    ks.reset()
    assert ks.is_set() is False


def test_normalize_hotkey():
    """Hotkeys are formatted cleanly for pynput parsing."""
    assert normalize_hotkey("Ctrl+Alt+Shift+K") == "<ctrl>+<alt>+<shift>+k"
    assert normalize_hotkey("<ctrl>+<alt>+<shift>+k") == "<ctrl>+<alt>+<shift>+k"
    assert normalize_hotkey("CTRL+K") == "<ctrl>+k"


def test_kill_switch_listener_lifecycle():
    """Verify listener daemon thread can start and stop cleanly."""
    triggered = []
    ks = KillSwitch(on_trigger=lambda: triggered.append(True))
    ks.start_hotkey_listener("Ctrl+Alt+Shift+K")
    assert ks._listener is not None
    assert ks._listener.is_alive()
    assert ks._listener.daemon is True

    # Manually trigger callback verification
    ks.trigger()
    assert ks.is_set() is True
    assert triggered == [True]

    ks.stop_hotkey_listener()
    assert ks._listener is None


def test_audit_logger_concurrent_thread_safety(tmp_path: Path):
    """Verify AuditLogger maintains a valid cryptographic hash chain under concurrent writes."""
    log_file = tmp_path / "concurrent_audit.log"
    logger = AuditLogger(log_file)

    num_threads = 10
    events_per_thread = 20

    def worker(thread_id: int):
        for i in range(events_per_thread):
            logger.log_event(
                event="executed",
                tool_name=f"tool_t{thread_id}",
                arguments={"iteration": i, "token": "secret_abc"},
                result_summary=f"Result from thread {thread_id} step {i}",
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    logger.close()

    is_valid, count, err = verify_audit_log(log_file)
    assert is_valid is True, f"Audit log verification failed: {err}"
    assert count == num_threads * events_per_thread
    assert err is None
