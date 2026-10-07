"""Shell execution tool strictly guarded by allowlists and regex argument rules."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from jarvis.core.config import CommandConfig
from jarvis.core.types import RiskTier, ToolSpec
from jarvis.safety.path_guard import resolve_allowed

MAX_OUTPUT_BYTES = 10240  # 10 KB


def create_run_command_tool(
    allowed_commands: Mapping[str, CommandConfig],
    allowed_roots: Sequence[Path | str],
) -> ToolSpec:
    """Create run_command ToolSpec enforcing strict command allowlist (RiskTier.CONFIRM)."""
    commands_map = MappingProxyType(dict(allowed_commands))

    def run_command(
        command_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        if command_id not in commands_map:
            allowed_list = sorted(list(commands_map.keys()))
            allowed_desc = ", ".join(f"'{c}'" for c in allowed_list) if allowed_list else "none configured"
            raise PermissionError(
                f"Command '{command_id}' is BLOCKED or not in allowed commands list. "
                f"Allowed command IDs: [{allowed_desc}]."
            )

        cmd_config = commands_map[command_id]
        clean_args = [str(a) for a in (args or [])]

        # Validate arguments against regex pattern schema
        if cmd_config.args_schema:
            if len(clean_args) != len(cmd_config.args_schema):
                raise ValueError(
                    f"Command '{command_id}' expects {len(cmd_config.args_schema)} arguments, "
                    f"got {len(clean_args)}: {clean_args}"
                )
            for idx, (pattern, arg_val) in enumerate(zip(cmd_config.args_schema, clean_args)):
                if not re.fullmatch(pattern, arg_val):
                    raise ValueError(
                        f"Argument at index {idx} ('{arg_val}') failed validation pattern '{pattern}' "
                        f"for command '{command_id}'."
                    )

        # Resolve working directory within allowed roots
        if cwd:
            resolved_cwd = resolve_allowed(cwd, allowed_roots)
        elif cmd_config.allowed_cwd:
            resolved_cwd = resolve_allowed(cmd_config.allowed_cwd, allowed_roots)
        elif allowed_roots:
            resolved_cwd = Path(allowed_roots[0]).resolve()
        else:
            resolved_cwd = resolve_allowed(".", allowed_roots)

        # Build argument list: NEVER use shell=True
        cmd_exec_list = [cmd_config.executable] + clean_args
        timeout = cmd_config.timeout_seconds or 20.0

        try:
            completed = subprocess.run(
                cmd_exec_list,
                cwd=str(resolved_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            stdout_trunc = completed.stdout[:MAX_OUTPUT_BYTES]
            stderr_trunc = completed.stderr[:MAX_OUTPUT_BYTES]
            return {
                "command_id": command_id,
                "returncode": completed.returncode,
                "stdout": stdout_trunc,
                "stderr": stderr_trunc,
                "truncated": len(completed.stdout) > MAX_OUTPUT_BYTES or len(completed.stderr) > MAX_OUTPUT_BYTES,
            }
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Command '{command_id}' timed out after {timeout} seconds.") from exc
        except Exception as exc:
            raise RuntimeError(f"Error executing command '{command_id}': {exc}") from exc

    return ToolSpec(
        name="run_command",
        description="Run an allowlisted shell command with pre-validated arguments.",
        input_schema={
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "description": "Identifier of the allowed command.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of positional command-line arguments.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory (must be inside allowed roots).",
                },
            },
            "required": ["command_id"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=run_command,
        untrusted_output=False,
    )
