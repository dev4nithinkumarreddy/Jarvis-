"""Memory tools for storing, searching, listing, and forgetting facts."""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import JarvisConfig
from jarvis.core.types import RiskTier, SessionState, ToolCall, ToolResult, ToolSpec
from jarvis.memory.store import MemoryStore, SecretPatternRefusalError

logger = logging.getLogger(__name__)


def create_memory_tools(
    config: JarvisConfig,
    memory_store: MemoryStore | None = None,
    session_state: SessionState | None = None,
) -> list[ToolSpec]:
    """Create and configure all Memory ToolSpecs.

    Args:
        config: System configuration containing memory settings.
        memory_store: Optional MemoryStore instance (e.g. for testing with custom path).
        session_state: Optional SessionState tracking session taint.

    Returns:
        list[ToolSpec]: The 4 memory tools: remember, recall, list_memories, forget.
    """
    store = (
        memory_store
        if memory_store is not None
        else MemoryStore(
            db_path=config.memory.db_path,
            enabled=config.memory.enabled,
        )
    )

    def handle_remember(text: Any = "", **kwargs: Any) -> ToolResult:
        """Store a fact in memory after explicit user confirmation."""
        call_id = ""
        is_session_tainted = False
        if session_state is not None and session_state.tainted:
            is_session_tainted = True

        if isinstance(text, ToolCall):
            call_id = text.id
            text_str = str(text.arguments.get("text", "")).strip()
            if text.arguments.get("_session_tainted"):
                is_session_tainted = True
        else:
            text_str = str(text).strip()
            if kwargs.get("_session_tainted"):
                is_session_tainted = True

        if not text_str:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Missing required argument 'text'.",
            )

        if not store.enabled:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Memory store is disabled in configuration.",
            )

        try:
            fact_id, stored_text = store.remember(text_str, tainted=is_session_tainted)
            return ToolResult(
                call_id=call_id,
                ok=True,
                content=f"Remembered fact [ID {fact_id}]: '{stored_text}'",
            )
        except SecretPatternRefusalError as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=str(exc),
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Failed to store fact: {exc}",
            )

    def handle_recall(query: Any = "", **kwargs: Any) -> ToolResult:
        """Search memory for facts matching a query."""
        call_id = ""
        if isinstance(query, ToolCall):
            call_id = query.id
            q_str = str(query.arguments.get("query", "")).strip()
        else:
            q_str = str(query).strip()

        if not store.enabled:
            return ToolResult(
                call_id=call_id,
                ok=True,
                content="Memory store is disabled.",
            )

        try:
            facts = store.recall(q_str, limit=10)
            if not facts:
                return ToolResult(
                    call_id=call_id,
                    ok=True,
                    content=f"No memories found matching '{q_str}'.",
                )

            lines = [f"Found {len(facts)} memory record(s):"]
            for f in facts:
                lines.append(f"- [ID {f['id']}] {f['text']} (stored: {f['created_at']})")
            return ToolResult(
                call_id=call_id,
                ok=True,
                content="\n".join(lines),
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Failed to recall facts: {exc}",
            )

    def handle_list_memories(*args: Any, **kwargs: Any) -> ToolResult:
        """List all stored facts."""
        call_id = ""
        for a in args:
            if isinstance(a, ToolCall):
                call_id = a.id
                break
        if not call_id:
            for v in kwargs.values():
                if isinstance(v, ToolCall):
                    call_id = v.id
                    break

        if not store.enabled:
            return ToolResult(
                call_id=call_id,
                ok=True,
                content="Memory store is disabled.",
            )

        try:
            facts = store.list_facts()
            if not facts:
                return ToolResult(
                    call_id=call_id,
                    ok=True,
                    content="No memories stored yet.",
                )

            lines = [f"Stored memories ({len(facts)} total):"]
            for f in facts:
                lines.append(f"- [ID {f['id']}] {f['text']} (stored: {f['created_at']})")
            return ToolResult(
                call_id=call_id,
                ok=True,
                content="\n".join(lines),
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Failed to list memories: {exc}",
            )

    def handle_forget(id: Any = None, **kwargs: Any) -> ToolResult:
        """Delete a fact by its ID."""
        call_id = ""
        if isinstance(id, ToolCall):
            call_id = id.id
            raw_id = id.arguments.get("id")
        else:
            raw_id = id

        if raw_id is None:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Missing required argument 'id'.",
            )

        try:
            fact_id = int(raw_id)
        except (ValueError, TypeError):
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Argument 'id' must be an integer.",
            )

        if not store.enabled:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Memory store is disabled in configuration.",
            )

        try:
            deleted = store.forget(fact_id)
            if deleted:
                return ToolResult(
                    call_id=call_id,
                    ok=True,
                    content=f"Successfully forgot memory fact [ID {fact_id}].",
                )
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Memory fact with ID {fact_id} not found.",
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Failed to forget memory: {exc}",
            )

    return [
        ToolSpec(
            name="remember",
            description="Store a personal fact or preference in persistent memory. Only call when user explicitly asks to remember something.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The fact or preference to remember. Passwords, keys, or cards are strictly refused.",
                    },
                },
                "required": ["text"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=handle_remember,
        ),
        ToolSpec(
            name="recall",
            description="Search persistent memory for facts matching a keyword or phrase.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword or substring to search for. Empty returns recent facts.",
                    },
                },
                "required": [],
            },
            risk_tier=RiskTier.AUTO,
            handler=handle_recall,
        ),
        ToolSpec(
            name="list_memories",
            description="List all facts currently stored in persistent memory.",
            input_schema={"type": "object", "properties": {}},
            risk_tier=RiskTier.AUTO,
            handler=handle_list_memories,
        ),
        ToolSpec(
            name="forget",
            description="Permanently delete a specific fact from persistent memory by its ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "The numeric ID of the memory fact to remove.",
                    },
                },
                "required": ["id"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=handle_forget,
        ),
    ]
