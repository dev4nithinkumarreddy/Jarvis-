"""Append-only JSONL audit logger for tool execution with argument redaction and SHA-256 hash chaining."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Literal

AuditEventType = Literal["call_requested", "decision", "executed", "failed"]

GENESIS_HASH = "0" * 64

REDACTION_KEYWORDS = (
    "key",
    "token",
    "password",
    "secret",
    "auth",
    "credential",
    "private",
)


def redact_arguments(data: Any) -> Any:
    """Recursively redact dictionary fields that look like secrets or credentials."""
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(secret_term in k_lower for secret_term in REDACTION_KEYWORDS):
                redacted[str(k)] = "[REDACTED]"
            else:
                redacted[str(k)] = redact_arguments(v)
        return redacted
    elif isinstance(data, list):
        return [redact_arguments(item) for item in data]
    return data


def truncate_summary(text: str | None, max_length: int = 500) -> str | None:
    """Truncate result summary to at most max_length characters and redact secrets."""
    if text is None:
        return None
    s = str(text)
    try:
        from jarvis.memory.store import redact_sensitive_text
        s = redact_sensitive_text(s)
    except ImportError:
        pass
    if len(s) > max_length:
        return s[:max_length]
    return s


def verify_audit_log(log_path: Path | str) -> tuple[bool, int, str | None]:
    """Verify cryptographic SHA-256 hash chain of the audit log file.

    Returns:
        (is_valid, record_count, error_message)
    """
    path = Path(log_path)
    if not path.exists():
        return True, 0, None

    expected_prev = GENESIS_HASH
    count = 0

    with open(path, mode="r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                entry = json.loads(line_str)
            except Exception as e:
                return False, count, f"Line {idx}: Malformed JSON ({e})"

            actual_record_hash = entry.get("record_hash")
            actual_prev_hash = entry.get("prev_hash")

            if not actual_record_hash or not actual_prev_hash:
                return False, count, f"Line {idx}: Missing cryptographic hash chain entries"

            if actual_prev_hash != expected_prev:
                return False, count, (
                    f"Line {idx}: Broken hash chain - expected prev_hash {expected_prev}, got {actual_prev_hash}"
                )

            # Recompute record hash over canonical JSON without record_hash
            payload = {k: v for k, v in entry.items() if k != "record_hash"}
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            computed_hash = hashlib.sha256(canonical).hexdigest()

            if computed_hash != actual_record_hash:
                return False, count, (
                    f"Line {idx}: Hash mismatch - computed {computed_hash}, recorded {actual_record_hash}"
                )

            expected_prev = actual_record_hash
            count += 1

    return True, count, None


class AuditLogger:
    """Append-only JSONL logger recording every tool event with safety redactions and SHA-256 hash chaining."""

    def __init__(
        self,
        log_path: Path | str = "logs/audit.log",
        listener: Any | None = None,
    ) -> None:
        self.log_path = Path(log_path)
        self._listener = listener
        self._lock = threading.Lock()
        self._file = None
        self._last_hash = self._read_last_hash()
        self._ensure_open()

    def _read_last_hash(self) -> str:
        """Read the record_hash of the last entry in existing log file, or return genesis hash."""
        if not self.log_path.exists():
            return GENESIS_HASH
        try:
            with open(self.log_path, mode="r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
                if not lines:
                    return GENESIS_HASH
                last_entry = json.loads(lines[-1])
                return str(last_entry.get("record_hash", GENESIS_HASH))
        except Exception:
            return GENESIS_HASH

    def set_listener(self, listener: Any | None) -> None:
        """Attach a live listener callback (e.g. for HUD broadcast)."""
        self._listener = listener

    def _ensure_open(self) -> None:
        """Ensure destination directory exists and file is opened in append mode."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, mode="a", encoding="utf-8")

    def log_event(
        self,
        event: AuditEventType | str,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
        decision_reason: str | None = None,
        result_summary: str | None = None,
        thumbnail_path: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Record an audit event to the append-only JSONL log with cryptographic chaining.

        Args:
            event: Event type ('call_requested', 'decision', 'executed', 'failed', 'llm_usage', etc.).
            tool_name: The name of the tool (optional for system/llm events).
            arguments: Tool arguments to be logged (secrets will be redacted).
            decision_reason: Explanation of the permission decision (if applicable).
            result_summary: Output summary truncated to 500 characters (if applicable).
            thumbnail_path: Optional path to screenshot thumbnail for GUI actions.
            **extra: Additional metadata fields to log (e.g. usage statistics, model).

        Returns:
            The recorded log entry dictionary.
        """
        with self._lock:
            record: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "tool_name": tool_name,
                "arguments": redact_arguments(arguments if arguments is not None else {}),
                "result_summary": truncate_summary(result_summary),
                "decision_reason": decision_reason,
                "prev_hash": self._last_hash,
            }
            if thumbnail_path is not None:
                record["thumbnail_path"] = str(thumbnail_path)

            for k, v in extra.items():
                if isinstance(v, str):
                    from jarvis.memory.store import redact_sensitive_text
                    record[k] = redact_sensitive_text(v)
                elif isinstance(v, dict):
                    record[k] = redact_arguments(v)
                else:
                    record[k] = v

            canonical = json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
            rec_hash = hashlib.sha256(canonical).hexdigest()
            record["record_hash"] = rec_hash
            self._last_hash = rec_hash

            line = json.dumps(record, ensure_ascii=False) + "\n"

            if self._file is None or self._file.closed:
                self._ensure_open()
            assert self._file is not None
            self._file.write(line)
            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except (OSError, AttributeError):
                pass

        if self._listener is not None:
            try:
                self._listener(record)
            except Exception:
                pass

        return record

    def log_call_requested(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Convenience method to log 'call_requested' event."""
        return self.log_event(
            event="call_requested",
            tool_name=tool_name,
            arguments=arguments,
        )

    def log_decision(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision_reason: str,
    ) -> dict[str, Any]:
        """Convenience method to log 'decision' event."""
        return self.log_event(
            event="decision",
            tool_name=tool_name,
            arguments=arguments,
            decision_reason=decision_reason,
        )

    def log_executed(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_summary: str,
        thumbnail_path: str | None = None,
    ) -> dict[str, Any]:
        """Convenience method to log 'executed' event."""
        return self.log_event(
            event="executed",
            tool_name=tool_name,
            arguments=arguments,
            result_summary=result_summary,
            thumbnail_path=thumbnail_path,
        )

    def log_failed(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        error_summary: str,
        thumbnail_path: str | None = None,
    ) -> dict[str, Any]:
        """Convenience method to log 'failed' event."""
        return self.log_event(
            event="failed",
            tool_name=tool_name,
            arguments=arguments,
            result_summary=error_summary,
            thumbnail_path=thumbnail_path,
        )

    @property
    def last_hash(self) -> str:
        """Return the record_hash of the most recently written audit record."""
        with self._lock:
            return self._last_hash

    def close(self) -> str:
        """Close the underlying log file handle and return the final record_hash."""
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.flush()
                self._file.close()
            return self._last_hash

    def __enter__(self) -> AuditLogger:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
