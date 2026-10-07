"""Permission engine evaluating tool calls against safety policies, session taint, and confirmations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.core.types import (
    PermissionDecision,
    RiskTier,
    SessionState,
    ToolCall,
    ToolSpec,
)


def format_confirmation_description(tool_spec: ToolSpec, call: ToolCall) -> str:
    """Generate an exact, human-readable description for a CONFIRM tool call."""
    name = tool_spec.name
    args: dict[str, Any] = call.arguments or {}

    if name == "create_text_file":
        path_str = str(args.get("path", ""))
        content = str(args.get("content", ""))
        try:
            target = Path(path_str)
            overwrites = "yes (will overwrite existing file)" if target.exists() else "no"
        except Exception:
            overwrites = "unknown"
        return f"Create text file at '{path_str}'. Overwrites: {overwrites}. Size: {len(content)} characters."

    elif name == "move_path":
        src = str(args.get("src", ""))
        dst = str(args.get("dst", ""))
        try:
            overwrites = "yes (will overwrite existing destination)" if Path(dst).exists() else "no"
        except Exception:
            overwrites = "unknown"
        return f"Move '{src}' -> '{dst}'. Overwrites: {overwrites}."

    elif name == "copy_path":
        src = str(args.get("src", ""))
        dst = str(args.get("dst", ""))
        try:
            overwrites = "yes (will overwrite existing destination)" if Path(dst).exists() else "no"
        except Exception:
            overwrites = "unknown"
        return f"Copy '{src}' -> '{dst}'. Overwrites: {overwrites}."

    elif name == "move_to_trash":
        path_str = str(args.get("path", ""))
        return f"Move '{path_str}' to Trash / Recycle Bin. Permanent deletion: no."

    elif name == "open_app":
        app_name = str(args.get("name", ""))
        return f"Launch desktop application '{app_name}'."

    elif name == "open_path":
        path_str = str(args.get("path", ""))
        return f"Open path '{path_str}' in default system application."

    elif name == "run_command":
        cmd_id = str(args.get("command_id", ""))
        cmd_args = args.get("args", [])
        cwd = str(args.get("cwd", "."))
        return f"Run command '{cmd_id}' with args {cmd_args} in directory '{cwd}'."

    elif name == "open_url":
        url = str(args.get("url", ""))
        return f"Navigate browser to URL '{url}'."

    elif name == "click_element":
        selector = str(args.get("selector") or args.get("description", ""))
        return f"Click browser element '{selector}'."

    elif name == "type_into":
        selector = str(args.get("selector", ""))
        text = str(args.get("text", ""))
        return f"Type text into browser element '{selector}'. Text: '{text}'."

    elif name == "gui_click":
        x = args.get("x", 0)
        y = args.get("y", 0)
        button = str(args.get("button", "left"))
        return f"Click mouse ({button}) at coordinates ({x}, {y})."

    elif name == "gui_type":
        text = str(args.get("text", ""))
        return f"Type text via keyboard: '{text}'."

    elif name == "gui_key":
        key = str(args.get("key", ""))
        return f"Press keyboard key: '{key}'."

    elif name == "gui_scroll":
        amount = args.get("amount", 0)
        direction = "up" if int(amount) > 0 else "down"
        return f"Scroll mouse wheel {direction} by {abs(int(amount))} clicks."

    elif name == "remember":
        text = str(args.get("text", ""))
        return f"Store fact in persistent memory: '{text}'."

    elif name == "forget":
        fact_id = args.get("id", "")
        return f"Permanently delete memory fact [ID: {fact_id}]."

    return f"Execute tool '{tool_spec.name}' with arguments: {args}"


class PermissionEngine:
    """Evaluates whether a tool call can proceed, requires confirmation, or is blocked."""

    TAINT_WARNING: str = (
        "[WARNING: Untrusted external content was read this turn. "
        "The session is tainted. Verify actions carefully before proceeding.]"
    )

    @staticmethod
    def decide(
        tool_spec: ToolSpec,
        call: ToolCall,
        session_state: SessionState | None = None,
    ) -> PermissionDecision:
        """Determine permission decision for a tool invocation.

        Rules:
        - BLOCKED: Tools never run under any circumstance.
        - AUTO: Tool runs automatically without user interaction.
        - CONFIRM: Requires explicit user approval through a Confirmer.
          If session_state is tainted, a visible taint warning is attached to the decision.
        """
        is_tainted = session_state.tainted if session_state is not None else False

        if tool_spec.risk_tier == RiskTier.BLOCKED:
            return PermissionDecision(
                allowed=False,
                reason=f"Tool '{tool_spec.name}' is BLOCKED by safety policy and cannot be executed.",
                needs_confirmation=False,
                warning=None,
            )

        if tool_spec.risk_tier == RiskTier.AUTO:
            return PermissionDecision(
                allowed=True,
                reason=f"Tool '{tool_spec.name}' is approved for automatic execution (AUTO).",
                needs_confirmation=False,
                warning=None,
            )

        if tool_spec.risk_tier == RiskTier.CONFIRM:
            if is_tainted:
                reason = (
                    f"{PermissionEngine.TAINT_WARNING} "
                    f"Tool '{tool_spec.name}' requires user confirmation."
                )
                warning = PermissionEngine.TAINT_WARNING
            else:
                reason = f"Tool '{tool_spec.name}' requires user confirmation."
                warning = None

            return PermissionDecision(
                allowed=True,
                reason=reason,
                needs_confirmation=True,
                warning=warning,
            )

        # Fallback for unexpected risk tiers: fail closed
        return PermissionDecision(
            allowed=False,
            reason=f"Unknown risk tier '{tool_spec.risk_tier}' for tool '{tool_spec.name}'. Blocked by default.",
            needs_confirmation=False,
            warning=None,
        )

    @classmethod
    def format_confirmation_prompt(
        cls,
        tool_spec: ToolSpec,
        call: ToolCall,
        session_state: SessionState | None = None,
    ) -> tuple[str, str | None]:
        """Format human-readable action description and optional warning for Confirmer."""
        decision = cls.decide(tool_spec, call, session_state)
        action_desc = format_confirmation_description(tool_spec, call)
        return action_desc, decision.warning
