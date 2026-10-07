"""Core data types and contracts for Jarvis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskTier(str, Enum):
    """Risk tier assigned to each tool in code.

    AUTO: Safe to run automatically without prompting the user.
    CONFIRM: Requires user confirmation before execution.
    BLOCKED: Execution is completely blocked.
    """

    AUTO = "AUTO"
    CONFIRM = "CONFIRM"
    BLOCKED = "BLOCKED"


@dataclass
class ToolSpec:
    """Specification of a tool available to the agent."""

    name: str
    description: str
    input_schema: dict[str, Any]
    risk_tier: RiskTier
    handler: Any = None
    untrusted_output: bool = False


@dataclass
class ToolCall:
    """Represents an invocation of a tool requested by the brain."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result returned from a tool execution."""

    call_id: str
    ok: bool
    content: str | None = None
    error: str | None = None
    image_bytes: bytes | None = None
    image_media_type: str = "image/png"
    thumbnail_path: str | None = None


@dataclass
class PermissionDecision:
    """Decision indicating whether a tool call is permitted to execute."""

    allowed: bool
    reason: str
    needs_confirmation: bool
    warning: str | None = None


@dataclass
class SessionState:
    """Tracks session-level safety context across turns.

    tainted: True if untrusted external content (e.g. web pages, unverified files)
             was read during the current turn.
    """

    tainted: bool = False

