"""Persistent SQLite memory store for user facts and conversation turns."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive pattern definitions
CARD_PATTERN = re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b|\b\d{13,19}\b")
API_KEY_PATTERNS = [
    re.compile(r"gsk_[a-zA-Z0-9_\-]{20,}"),      # Groq key
    re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"),  # Anthropic key
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),         # OpenAI / generic key
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),         # GitHub PAT
    re.compile(r"glpat-[a-zA-Z0-9_\-]{20,}"),   # GitLab PAT
    re.compile(r"xox[baprs]-[a-zA-Z0-9\-]{20,}"),  # Slack token
]
PASSWORD_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|pin|secret|api_key|token|auth_token)\s*[:=]\s*\S+"
)
PASSWORD_PHRASE_PATTERN = re.compile(
    r"(?i)\b(?:my|the)\s+(?:password|pin|secret|api\s*key)\s+(?:is|was)\s+\S+"
)


def detect_sensitive_pattern(text: str) -> str | None:
    """Check text for secrets, credentials, or card numbers.

    Returns:
        Explanation string describing why the text is refused, or None if safe.
    """
    if not text:
        return None

    # 1. Payment cards
    for m in CARD_PATTERN.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19:
            return "credit card or payment card number pattern detected"

    # 2. Known API key / secret patterns
    for pat in API_KEY_PATTERNS:
        if pat.search(text):
            return "API key or secret token pattern detected"

    # 3. Explicit password or credential assignments
    if PASSWORD_ASSIGNMENT_PATTERN.search(text):
        return "password or credential assignment pattern detected"

    if PASSWORD_PHRASE_PATTERN.search(text):
        return "password or credential phrase pattern detected"

    return None


def redact_sensitive_text(text: str) -> str:
    """Replace all detected sensitive spans (cards, API keys, credentials) with '[REDACTED]'.

    Returns:
        The text with all sensitive spans replaced by '[REDACTED]'.
    """
    if not text:
        return text

    redacted = str(text)

    # 1. API Keys
    for pat in API_KEY_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)

    # 2. Payment card numbers (13-19 digits)
    def _redact_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19:
            return "[REDACTED]"
        return match.group(0)

    redacted = CARD_PATTERN.sub(_redact_card, redacted)

    # 3. Explicit password or credential assignments
    redacted = PASSWORD_ASSIGNMENT_PATTERN.sub("[REDACTED]", redacted)

    # 4. Password phrases
    redacted = PASSWORD_PHRASE_PATTERN.sub("[REDACTED]", redacted)

    return redacted


class SecretPatternRefusalError(ValueError):
    """Raised when attempting to store sensitive credentials (passwords, keys, card numbers)."""


class MemoryStore:
    """Thread-safe SQLite memory store for user facts and conversation history."""

    def __init__(self, db_path: Path | str = "data/memory.db", enabled: bool = True) -> None:
        """Initialize MemoryStore.

        Args:
            db_path: Path to SQLite database file.
            enabled: If False, database is not created or accessed on disk.
        """
        self.db_path = Path(db_path)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        if self.enabled:
            self._ensure_db()

    def _ensure_db(self) -> None:
        """Create parent directory and initialize tables if enabled."""
        if not self.enabled:
            return

        with self._lock:
            if self._conn is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                )
                self._conn.row_factory = sqlite3.Row
                with self._conn:
                    self._conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS facts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            text TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            tainted INTEGER DEFAULT 0
                        );
                        """
                    )
                    self._conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS conversations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ts TEXT NOT NULL,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL
                        );
                        """
                    )
                    # Safe migration: ensure 'tainted' column exists in facts table
                    cursor = self._conn.execute("PRAGMA table_info(facts);")
                    columns = [row["name"] for row in cursor.fetchall()]
                    if "tainted" not in columns:
                        self._conn.execute("ALTER TABLE facts ADD COLUMN tainted INTEGER DEFAULT 0;")

    def remember(self, text: str, tainted: bool | int = False) -> tuple[int, str]:
        """Store a new fact explicitly provided by the user.

        Args:
            text: Fact text to store.
            tainted: Whether fact was stored during a turn that read untrusted content.

        Returns:
            tuple: (fact_id, text)

        Raises:
            SecretPatternRefusalError: If text contains passwords, keys, or card numbers.
            RuntimeError: If memory store is disabled.
        """
        if not self.enabled:
            raise RuntimeError("Memory store is disabled in configuration.")

        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("Fact text cannot be empty.")

        # Enforce security check: never store secrets
        reason = detect_sensitive_pattern(cleaned_text)
        if reason:
            raise SecretPatternRefusalError(
                f"Cannot store memory: {reason}. Jarvis strictly refuses to persist secrets or credentials."
            )

        now_utc = datetime.now(timezone.utc).isoformat()
        self._ensure_db()
        assert self._conn is not None

        tainted_val = 1 if tainted else 0
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "INSERT INTO facts (text, created_at, tainted) VALUES (?, ?, ?);",
                    (cleaned_text, now_utc, tainted_val),
                )
                fact_id = int(cursor.lastrowid)

        return fact_id, cleaned_text

    def recall(self, query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        """Search stored facts matching query.

        Args:
            query: Substring to search for. If empty, returns recent facts.
            limit: Maximum number of facts to return.

        Returns:
            list[dict]: List of matching facts [{'id': ..., 'text': ..., 'created_at': ..., 'tainted': ...}]
        """
        if not self.enabled:
            return []

        self._ensure_db()
        assert self._conn is not None

        q = query.strip()
        with self._lock:
            if not q or q == "*":
                cursor = self._conn.execute(
                    "SELECT id, text, created_at, tainted FROM facts ORDER BY id DESC LIMIT ?;",
                    (limit,),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT id, text, created_at, tainted FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT ?;",
                    (f"%{q}%", limit),
                )
            rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "text": row["text"],
                "created_at": row["created_at"],
                "tainted": int(row["tainted"] if "tainted" in row.keys() and row["tainted"] is not None else 0),
            }
            for row in rows
        ]

    def list_facts(self) -> list[dict[str, Any]]:
        """List all stored facts in ascending order of ID."""
        if not self.enabled:
            return []

        self._ensure_db()
        assert self._conn is not None

        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, text, created_at, tainted FROM facts ORDER BY id ASC;"
            )
            rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "text": row["text"],
                "created_at": row["created_at"],
                "tainted": int(row["tainted"] if "tainted" in row.keys() and row["tainted"] is not None else 0),
            }
            for row in rows
        ]

    def forget(self, fact_id: int) -> bool:
        """Delete a fact by ID.

        Args:
            fact_id: Primary key ID of fact to delete.

        Returns:
            bool: True if fact existed and was deleted, False otherwise.
        """
        if not self.enabled:
            return False

        self._ensure_db()
        assert self._conn is not None

        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "DELETE FROM facts WHERE id = ?;",
                    (fact_id,),
                )
                return cursor.rowcount > 0

    def clear_facts(self) -> int:
        """Delete all stored facts."""
        if not self.enabled:
            return 0

        self._ensure_db()
        assert self._conn is not None

        with self._lock:
            with self._conn:
                cursor = self._conn.execute("DELETE FROM facts;")
                return cursor.rowcount

    def log_conversation(self, role: str, content: str) -> None:
        """Record a conversation turn message with sensitive pattern redaction."""
        if not self.enabled:
            return

        self._ensure_db()
        assert self._conn is not None

        # Redact secrets, keys, credentials, or card numbers
        text_str = str(content)
        while detect_sensitive_pattern(text_str) is not None:
            redacted = redact_sensitive_text(text_str)
            if redacted == text_str:
                text_str = "[REDACTED]"
                break
            text_str = redacted

        now_utc = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO conversations (ts, role, content) VALUES (?, ?, ?);",
                    (now_utc, role, text_str),
                )

    def get_recent_conversations(self, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve the last limit conversation messages."""
        if not self.enabled:
            return []

        self._ensure_db()
        assert self._conn is not None

        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, ts, role, content FROM conversations ORDER BY id DESC LIMIT ?;",
                (limit,),
            )
            rows = cursor.fetchall()

        # Return in chronological order
        return [
            {"id": row["id"], "ts": row["ts"], "role": row["role"], "content": row["content"]}
            for row in reversed(rows)
        ]

    def clear_conversations(self) -> int:
        """Delete all recorded conversation messages."""
        if not self.enabled:
            return 0

        self._ensure_db()
        assert self._conn is not None

        with self._lock:
            with self._conn:
                cursor = self._conn.execute("DELETE FROM conversations;")
                return cursor.rowcount

    def export_data(self) -> dict[str, Any]:
        """Export all facts and conversation logs to a structured dictionary."""
        return {
            "facts": self.list_facts(),
            "conversations": self.get_recent_conversations(limit=10000),
        }

    def close(self) -> None:
        """Close SQLite database connection if open."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
