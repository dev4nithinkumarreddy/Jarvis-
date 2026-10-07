"""Tool registry for storing ToolSpecs and safely dispatching tool invocations."""

from __future__ import annotations

import json
from typing import Any
import inspect

from jarvis.core.types import ToolCall, ToolResult, ToolSpec


class ToolRegistry:
    """Registry maintaining available tools, schemas, and safe execution dispatching."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool_spec: ToolSpec) -> None:
        """Register a ToolSpec into the registry.

        Args:
            tool_spec: The specification of the tool.
        """
        self._tools[tool_spec.name] = tool_spec

    def get(self, name: str) -> ToolSpec | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        """Return all registered ToolSpecs."""
        return list(self._tools.values())

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Produce JSON-schema tool definitions for LLM tool consumption."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in self._tools.values()
        ]

    def execute(self, call: ToolCall) -> ToolResult:
        """Execute a tool call safely.

        If the tool name is unrecognized, returns a ToolResult error without raising an exception.

        Args:
            call: The ToolCall request.

        Returns:
            ToolResult containing execution success or error details.
        """
        spec = self.get(call.name)
        if spec is None:
            return ToolResult(
                call_id=call.id,
                ok=False,
                error=f"Unknown tool '{call.name}'. Tool does not exist.",
            )

        if spec.handler is None:
            return ToolResult(
                call_id=call.id,
                ok=False,
                error=f"Tool '{call.name}' has no executable handler registered.",
            )

        try:
            handler_args = call.arguments or {}
            # Check handler signature to call appropriately
            sig = inspect.signature(spec.handler)
            if len(sig.parameters) == 1 and next(iter(sig.parameters.values())).kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ) and "args" not in sig.parameters and "kwargs" not in sig.parameters and not any(
                p in handler_args for p in sig.parameters
            ):
                result = spec.handler(handler_args)
            else:
                result = spec.handler(**handler_args)

            if isinstance(result, ToolResult):
                result.call_id = call.id
                return result

            if isinstance(result, (dict, list)):
                content = json.dumps(result, indent=2, ensure_ascii=False)
            else:
                content = str(result)

            return ToolResult(
                call_id=call.id,
                ok=True,
                content=content,
            )
        except Exception as exc:
            return ToolResult(
                call_id=call.id,
                ok=False,
                error=f"Error executing tool '{call.name}': {exc}",
            )
