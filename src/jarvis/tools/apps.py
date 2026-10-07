"""Application and default path launcher tools with allowlist enforcement."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from jarvis.core.types import RiskTier, ToolSpec
from jarvis.platform.os_adapter import OSAdapter, get_os_adapter
from jarvis.safety.path_guard import resolve_allowed


def create_open_app_tool(
    allowed_apps: Mapping[str, str],
    os_adapter: OSAdapter | None = None,
) -> ToolSpec:
    """Create open_app ToolSpec restricted to configured allowlist (RiskTier.CONFIRM)."""
    adapter = os_adapter if os_adapter is not None else get_os_adapter()
    apps_map = MappingProxyType(dict(allowed_apps))

    def open_app(name: str) -> str:
        clean_name = name.strip().lower()
        matched_cmd = None
        for app_name, cmd in apps_map.items():
            if app_name.strip().lower() == clean_name:
                matched_cmd = cmd
                break

        if matched_cmd is None:
            available_list = sorted(list(apps_map.keys()))
            available_desc = ", ".join(f"'{a}'" for a in available_list) if available_list else "none configured"
            raise ValueError(
                f"Application '{name}' is not allowed. "
                f"Allowed applications from config: [{available_desc}]."
            )

        adapter.open_app(matched_cmd)
        return f"Successfully initiated launch for application '{name}'."

    return ToolSpec(
        name="open_app",
        description="Launch an allowed desktop application by name.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Configured application name to launch.",
                },
            },
            "required": ["name"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=open_app,
        untrusted_output=False,
    )


def create_open_path_tool(
    allowed_roots: Sequence[Path | str],
    os_adapter: OSAdapter | None = None,
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> ToolSpec:
    """Create open_path ToolSpec opening a file with default app (RiskTier.CONFIRM)."""
    adapter = os_adapter if os_adapter is not None else get_os_adapter()

    def open_path(path: str) -> str:
        target_path = resolve_allowed(
            path,
            allowed_roots,
            mode="read",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        if not target_path.exists():
            raise FileNotFoundError(f"Path does not exist: {target_path}")

        adapter.open_path(target_path)
        return f"Successfully opened '{target_path}' in the default system handler."

    return ToolSpec(
        name="open_path",
        description="Open a file or directory with its default associated application.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to open in system viewer/editor.",
                },
            },
            "required": ["path"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=open_path,
        untrusted_output=False,
    )
