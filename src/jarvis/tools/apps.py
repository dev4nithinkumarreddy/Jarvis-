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

    # Built-in common user alias normalizations
    aliases = {
        "browser": "chrome",
        "web browser": "chrome",
        "google chrome": "chrome",
        "google": "chrome",
        "microsoft edge": "edge",
        "msedge": "edge",
        "visual studio code": "vscode",
        "vs code": "vscode",
        "files": "explorer",
        "file explorer": "explorer",
        "windows explorer": "explorer",
        "powershell": "terminal",
        "windows terminal": "terminal",
        "command prompt": "cmd",
        "prompt": "cmd",
        "music": "spotify",
    }

    def open_app(name: str, argument: str = "") -> str:
        clean_name = name.strip().lower()
        # Check alias table first
        normalized = aliases.get(clean_name, clean_name)

        matched_cmd = None
        for app_name, cmd in apps_map.items():
            if app_name.strip().lower() in (clean_name, normalized):
                matched_cmd = cmd
                break

        if matched_cmd is None:
            available_list = sorted(list(apps_map.keys()))
            available_desc = ", ".join(f"'{a}'" for a in available_list) if available_list else "none configured"
            raise ValueError(
                f"Application '{name}' is not allowed. "
                f"Allowed applications from config: [{available_desc}]."
            )

        full_cmd = f"{matched_cmd} {argument.strip()}".strip() if argument.strip() else matched_cmd
        adapter.open_app(full_cmd)
        target_info = f" with '{argument.strip()}'" if argument.strip() else ""
        return f"Successfully initiated launch for application '{name}'{target_info}."

    return ToolSpec(
        name="open_app",
        description="Launch an allowed desktop application by name (e.g. 'chrome', 'edge', 'browser', 'notepad', 'calc', 'vscode', 'explorer', 'spotify') with optional argument or URL.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Configured application name or alias to launch (e.g. 'chrome', 'browser', 'notepad', 'calc', 'vscode').",
                },
                "argument": {
                    "type": "string",
                    "description": "Optional argument, file path, or URL to open with the application.",
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
