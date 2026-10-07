"""Turn loop orchestrator coordinating Brain, safety checks, tools, and audit logs."""

from __future__ import annotations

import base64
import logging
from typing import Any

from jarvis.brain.base import Brain
from jarvis.brain.groq_brain import GroqBrain, GroqRateLimitExceededError
from jarvis.core.channels import Confirmer, OutputChannel
from jarvis.core.config import JarvisConfig
from jarvis.core.types import SessionState, ToolResult
from jarvis.memory.store import MemoryStore
from jarvis.safety.audit import AuditLogger
from jarvis.safety.confirmers import CLIConfirmer
from jarvis.safety.killswitch import KillSwitch, get_kill_switch
from jarvis.safety.permissions import PermissionEngine
from jarvis.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class Orchestrator:
    """Manages conversational turns, dispatches tool execution, and enforces safety boundaries."""

    def __init__(
        self,
        brain: Brain,
        tool_registry: ToolRegistry,
        config: JarvisConfig | None = None,
        session_state: SessionState | None = None,
        audit_logger: AuditLogger | None = None,
        kill_switch: KillSwitch | None = None,
        confirmer: Confirmer | None = None,
        permission_engine: PermissionEngine | None = None,
        memory_store: MemoryStore | None = None,
        output_channel: OutputChannel | None = None,
        max_tool_calls_per_turn: int = 8,
        dry_run: bool = False,
    ) -> None:
        self.brain = brain
        self.tool_registry = tool_registry
        self.config = config
        self.dry_run = dry_run
        self.output_channel = output_channel
        self.session_state = session_state if session_state is not None else SessionState(tainted=False)
        self.max_tool_calls_per_turn = (
            config.max_tool_calls_per_turn if config is not None else max_tool_calls_per_turn
        )

        log_path = config.audit_log_path if config is not None else "logs/audit.log"
        self.audit_logger = audit_logger if audit_logger is not None else AuditLogger(log_path)
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()
        self.confirmer = confirmer if confirmer is not None else (
            CLIConfirmer.from_config(config) if config is not None else CLIConfirmer()
        )
        self.permission_engine = permission_engine if permission_engine is not None else PermissionEngine()

        # Memory store and context parameters
        if memory_store is not None:
            self.memory_store = memory_store
        elif config is not None and getattr(config, "memory", None) is not None and config.memory.enabled:
            self.memory_store = MemoryStore(config.memory.db_path, enabled=config.memory.enabled)
        else:
            self.memory_store = None

        self.max_context_chars = (
            config.memory.max_context_chars if config is not None and getattr(config, "memory", None) is not None else 2000
        )
        if config is not None and getattr(config, "brain", None) is not None and getattr(config.brain, "history_turns", None) is not None:
            self.history_turns = config.brain.history_turns
        elif config is not None and getattr(config, "brain", None) is not None and getattr(config.brain, "provider", "") == "groq":
            self.history_turns = 4
        elif config is not None and getattr(config, "memory", None) is not None:
            self.history_turns = config.memory.history_turns
        else:
            self.history_turns = 10

        self.messages: list[dict[str, Any]] = []

    def reset_conversation(self) -> None:
        """Clear conversation message history."""
        self.messages.clear()

    def _get_memory_context(self, user_text: str) -> str | None:
        """Retrieve stored facts from MemoryStore within character budget."""
        if self.memory_store is None or not self.memory_store.enabled:
            return None

        try:
            facts = self.memory_store.list_facts()
            if not facts:
                return None

            header = (
                "<user_provided_memory_context>\n"
                "NOTICE: The following facts were explicitly stored by the user in previous sessions:"
            )
            footer = "\n</user_provided_memory_context>"
            overhead = len(header) + len(footer) + 2

            lines: list[str] = []
            current_len = overhead

            for f in facts:
                is_tainted = bool(f.get("tainted", 0))
                if is_tainted:
                    line = f"- [ID {f['id']}] {f['text']} [saved during a turn that read untrusted content]"
                else:
                    line = f"- [ID {f['id']}] {f['text']}"
                if current_len + len(line) + 1 > self.max_context_chars:
                    break
                lines.append(line)
                current_len += len(line) + 1

            if not lines:
                return None

            return f"{header}\n" + "\n".join(lines) + footer
        except Exception as exc:
            logger.warning("Failed to retrieve memory context: %s", exc)
            return None

    def _get_context_messages(self) -> list[dict[str, Any]]:
        """Return conversational history sliced to at most history_turns user turns.

        Enforces conversation history limits with summarisation OFF in this phase.
        Ensures the first message in the sliced history is always a 'user' message.
        """
        if not self.messages:
            return []

        # Find indices of all top-level user prompts (excluding tool_result blocks)
        user_turn_indices: list[int] = []
        for i, m in enumerate(self.messages):
            if m.get("role") == "user":
                content = m.get("content")
                is_tool_result = (
                    isinstance(content, list)
                    and len(content) > 0
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "tool_result"
                )
                if not is_tool_result:
                    user_turn_indices.append(i)

        if len(user_turn_indices) <= self.history_turns:
            return list(self.messages)

        start_idx = user_turn_indices[-self.history_turns]
        return list(self.messages[start_idx:])

    def run_turn(self, user_text: str, output_channel: OutputChannel | None = None) -> str:
        """Execute a full turn for the user's input text.

        Flow:
        User text -> Brain -> for each tool call:
          Kill switch check -> PermissionEngine -> execute -> audit -> ToolResult back to Brain
        until Brain returns plain text or max_tool_calls_per_turn is reached.

        Args:
            user_text: Plain text instruction from the user.
            output_channel: Optional output channel for announcing notices directly to user.

        Returns:
            The final assistant reply string or halt notice.
        """
        # Initial kill switch check
        if self.kill_switch.is_set():
            halt_msg = "Execution halted: Kill switch is active."
            logger.warning(halt_msg)
            return halt_msg

        # Add user text to conversation history, injecting memory context if present
        memory_ctx = self._get_memory_context(user_text)
        if memory_ctx:
            prompt_content = f"{memory_ctx}\n\n{user_text}"
        else:
            prompt_content = user_text

        self.messages.append({"role": "user", "content": prompt_content})
        tool_calls_count = 0

        while True:
            # Check tool call turn limit
            if tool_calls_count >= self.max_tool_calls_per_turn:
                limit_msg = (
                    f"Turn halted: Maximum tool calls per turn limit ({self.max_tool_calls_per_turn}) reached."
                )
                self.messages.append({"role": "assistant", "content": limit_msg})
                if self.memory_store is not None and self.memory_store.enabled:
                    self.memory_store.log_conversation("user", user_text)
                    self.memory_store.log_conversation("assistant", limit_msg)
                return limit_msg

            # Get next response from Brain using windowed messages
            available_specs = self.tool_registry.list_tools()
            messages_to_send = self._get_context_messages()
            try:
                brain_response = self.brain.next_step(messages_to_send, available_specs)
            except GroqRateLimitExceededError as rle:
                wait_msg = f"Rate limit reached (HTTP 429). {rle}"
                logger.warning(wait_msg)
                active_out = output_channel or self.output_channel
                if active_out is not None:
                    try:
                        active_out.say(wait_msg)
                    except Exception:
                        pass
                elif hasattr(self, "confirmer") and hasattr(self.confirmer, "say"):
                    try:
                        self.confirmer.say(wait_msg)
                    except Exception:
                        pass
                return wait_msg

            # Log LLM token usage if reported
            if brain_response.usage:
                self.audit_logger.log_event(
                    event="llm_usage",
                    provider=getattr(self.brain, "provider", "brain"),
                    model=getattr(self.brain, "model", ""),
                    prompt_tokens=brain_response.usage.get("prompt_tokens"),
                    completion_tokens=brain_response.usage.get("completion_tokens"),
                    total_tokens=brain_response.usage.get("total_tokens"),
                )

            # If Brain returned plain text without tool calls, we are done
            if not brain_response.has_tool_calls:
                final_text = brain_response.text or ""
                if final_text:
                    self.messages.append({"role": "assistant", "content": final_text})
                if self.memory_store is not None and self.memory_store.enabled:
                    self.memory_store.log_conversation("user", user_text)
                    if final_text:
                        self.memory_store.log_conversation("assistant", final_text)
                return final_text

            is_groq = isinstance(self.brain, GroqBrain) or (
                self.config is not None
                and getattr(self.config, "brain", None) is not None
                and getattr(self.config.brain, "provider", "") == "groq"
            )

            if is_groq:
                tool_calls_payload: list[dict[str, Any]] = []
                for call in brain_response.tool_calls:
                    if "_raw_arguments" in call.arguments:
                        raw_args_str = str(call.arguments["_raw_arguments"])
                    else:
                        import json as _json
                        raw_args_str = _json.dumps(call.arguments, ensure_ascii=False)
                    tool_calls_payload.append({
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": raw_args_str,
                        },
                    })
                self.messages.append({
                    "role": "assistant",
                    "content": brain_response.text,
                    "tool_calls": tool_calls_payload,
                })
            else:
                # Brain returned tool calls: record assistant message (Anthropic block format)
                assistant_content: list[dict[str, Any]] = []
                if brain_response.text:
                    assistant_content.append({"type": "text", "text": brain_response.text})

                for call in brain_response.tool_calls:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    })

                self.messages.append({"role": "assistant", "content": assistant_content})

            # Process each requested tool call sequentially
            tool_result_blocks: list[dict[str, Any]] = []

            for call in brain_response.tool_calls:
                # 1. Enforce max tool calls per turn
                if tool_calls_count >= self.max_tool_calls_per_turn:
                    err = f"Turn limit exceeded: reached {self.max_tool_calls_per_turn} calls."
                    wrapped_data = f"<tool_output_data>\nERROR: {err}\n</tool_output_data>"
                    if is_groq:
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": wrapped_data,
                        })
                    else:
                        tool_result_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": wrapped_data,
                            "is_error": True,
                        })
                    continue

                tool_calls_count += 1

                # 2. Check kill switch before EVERY tool call
                if self.kill_switch.is_set():
                    halt_msg = "Tool execution halted: Kill switch was triggered."
                    self.audit_logger.log_failed(call.name, call.arguments, halt_msg)
                    return halt_msg

                # 3. Check for invalid JSON arguments from model
                if "_invalid_json_error" in call.arguments:
                    parse_err = (
                        f"Invalid JSON arguments from model: {call.arguments['_invalid_json_error']}. "
                        f"Raw input: {call.arguments.get('_raw_arguments')}"
                    )
                    self.audit_logger.log_failed(call.name, {}, parse_err)
                    tool_result = ToolResult(
                        call_id=call.id,
                        ok=False,
                        error=parse_err,
                    )
                else:
                    # 4. Audit log request
                    self.audit_logger.log_call_requested(call.name, call.arguments)

                    # 5. PermissionEngine decision
                    if call.name == "remember":
                        call.arguments["_session_tainted"] = self.session_state.tainted

                    tool_spec = self.tool_registry.get(call.name)
                    if tool_spec is None:
                        # Unknown tool: execute via registry returns error ToolResult without crash
                        self.audit_logger.log_decision(call.name, call.arguments, f"Unknown tool '{call.name}'")
                        tool_result = self.tool_registry.execute(call)
                        self.audit_logger.log_failed(call.name, call.arguments, tool_result.error or "Unknown tool")
                    else:
                        decision = self.permission_engine.decide(tool_spec, call, self.session_state)
                        self.audit_logger.log_decision(call.name, call.arguments, decision.reason)

                        if not decision.allowed:
                            tool_result = ToolResult(
                                call_id=call.id,
                                ok=False,
                                error=f"Permission denied: {decision.reason}",
                            )
                            self.audit_logger.log_failed(call.name, call.arguments, tool_result.error or "Denied")
                        elif decision.needs_confirmation:
                            action_desc, warning = self.permission_engine.format_confirmation_prompt(
                                tool_spec, call, self.session_state
                            )
                            if self.dry_run:
                                tool_result = ToolResult(
                                    call_id=call.id,
                                    ok=True,
                                    content=f"[Dry Run] Would execute: {action_desc}",
                                )
                                self.audit_logger.log_executed(
                                    call.name,
                                    call.arguments,
                                    tool_result.content or "",
                                    thumbnail_path=tool_result.thumbnail_path,
                                )
                            else:
                                confirmed = self.confirmer.confirm(action_desc, warning=warning)
                                if not confirmed:
                                    tool_result = ToolResult(
                                        call_id=call.id,
                                        ok=False,
                                        error="Operation denied: User did not confirm.",
                                    )
                                    self.audit_logger.log_failed(call.name, call.arguments, "User denied confirmation")
                                else:
                                    tool_result = self.tool_registry.execute(call)
                                    if tool_result.ok:
                                        self.audit_logger.log_executed(
                                            call.name,
                                            call.arguments,
                                            tool_result.content or "",
                                            thumbnail_path=tool_result.thumbnail_path,
                                        )
                                    else:
                                        self.audit_logger.log_failed(
                                            call.name,
                                            call.arguments,
                                            tool_result.error or "Failed",
                                            thumbnail_path=tool_result.thumbnail_path,
                                        )
                        else:
                            # AUTO approved
                            tool_result = self.tool_registry.execute(call)
                            if tool_result.ok:
                                self.audit_logger.log_executed(
                                    call.name,
                                    call.arguments,
                                    tool_result.content or "",
                                    thumbnail_path=tool_result.thumbnail_path,
                                )
                            else:
                                self.audit_logger.log_failed(
                                    call.name,
                                    call.arguments,
                                    tool_result.error or "Failed",
                                    thumbnail_path=tool_result.thumbnail_path,
                                )

                        # 6. Mark session tainted if untrusted_output=True returns successfully
                        if tool_spec.untrusted_output and tool_result.ok:
                            self.session_state.tainted = True

                # 7. Truncate output to budget and wrap as data
                content_body = tool_result.content if tool_result.ok else f"ERROR: {tool_result.error}"
                if self.memory_store is not None and self.memory_store.enabled:
                    self.memory_store.log_conversation(f"tool:{call.name}", content_body)

                truncation_limit = 4096
                if self.config is not None and getattr(self.config, "brain", None) is not None:
                    truncation_limit = getattr(self.config.brain, "tool_output_truncation_bytes", 4096)

                raw_bytes = content_body.encode("utf-8")
                if len(raw_bytes) > truncation_limit:
                    content_body = raw_bytes[:truncation_limit].decode("utf-8", errors="replace") + (
                        f"\n\n[Warning: Tool output truncated at {truncation_limit} bytes for token budget]"
                    )

                wrapped_data = f"<tool_output_data>\n{content_body}\n</tool_output_data>"

                if is_groq:
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": wrapped_data,
                    })
                else:
                    if tool_result.image_bytes:
                        b64_img = base64.b64encode(tool_result.image_bytes).decode("ascii")
                        tool_content: str | list[dict[str, Any]] = [
                            {"type": "text", "text": wrapped_data},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": tool_result.image_media_type,
                                    "data": b64_img,
                                },
                            },
                        ]
                    else:
                        tool_content = wrapped_data

                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": tool_content,
                        "is_error": not tool_result.ok,
                    })

            if not is_groq and tool_result_blocks:
                # Append tool results as user message
                self.messages.append({
                    "role": "user",
                    "content": tool_result_blocks,
                })

            # If limit reached after processing these calls, halt turn
            if tool_calls_count >= self.max_tool_calls_per_turn:
                stop_msg = (
                    f"Turn halted: Maximum tool calls per turn limit ({self.max_tool_calls_per_turn}) reached."
                )
                self.messages.append({"role": "assistant", "content": stop_msg})
                if self.memory_store is not None and self.memory_store.enabled:
                    self.memory_store.log_conversation("user", user_text)
                    self.memory_store.log_conversation("assistant", stop_msg)
                return stop_msg
