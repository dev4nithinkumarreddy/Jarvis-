"""Tools package defining read-only and state-altering tools, registration, and factories."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from jarvis.core.config import JarvisConfig
from jarvis.core.types import ToolSpec
from jarvis.platform.os_adapter import OSAdapter, get_os_adapter
from jarvis.tools.apps import create_open_app_tool, create_open_path_tool
from jarvis.tools.file_tools import (
    create_list_directory_tool,
    create_read_text_file_tool,
    create_search_files_tool,
)
from jarvis.tools.browser import BrowserSession, create_browser_tools
from jarvis.tools.files_write import (
    create_copy_path_tool,
    create_create_text_file_tool,
    create_move_path_tool,
    create_move_to_trash_tool,
)
from jarvis.tools.gui import create_gui_tools
from jarvis.tools.memory import create_memory_tools
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.shell import create_run_command_tool
from jarvis.tools.system_tools import (
    create_current_time_tool,
    create_system_info_tool,
)


def create_read_only_tools(
    allowed_roots: Sequence[Path | str],
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> list[ToolSpec]:
    """Factory creating all 5 read-only tools (AUTO tier)."""
    return [
        create_list_directory_tool(allowed_roots),
        create_read_text_file_tool(
            allowed_roots,
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        ),
        create_search_files_tool(allowed_roots),
        create_system_info_tool(),
        create_current_time_tool(),
    ]


def create_state_altering_tools(
    allowed_roots: Sequence[Path | str],
    allowed_apps: dict[str, str],
    allowed_commands: dict[str, Any],
    os_adapter: OSAdapter | None = None,
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> list[ToolSpec]:
    """Factory creating state-altering tools (CONFIRM tier)."""
    adapter = os_adapter if os_adapter is not None else get_os_adapter()
    return [
        create_create_text_file_tool(
            allowed_roots,
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        ),
        create_move_path_tool(
            allowed_roots,
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        ),
        create_copy_path_tool(
            allowed_roots,
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        ),
        create_move_to_trash_tool(
            allowed_roots,
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        ),
        create_open_app_tool(allowed_apps, adapter),
        create_open_path_tool(
            allowed_roots,
            adapter,
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        ),
        create_run_command_tool(allowed_commands, allowed_roots),
    ]


def create_all_tools(
    config: JarvisConfig,
    os_adapter: OSAdapter | None = None,
    browser_session: BrowserSession | None = None,
    input_controller: Any | None = None,
    screen_capture: Any | None = None,
    memory_store: Any | None = None,
    session_state: Any | None = None,
    supports_images: bool = True,
) -> list[ToolSpec]:
    """Factory creating all available tools configured from JarvisConfig."""
    protected_paths = config.safety.protected_paths if hasattr(config, "safety") else None
    read_safe_files = config.safety.read_safe_files if hasattr(config, "safety") else None
    read_only = create_read_only_tools(
        allowed_roots=config.allowed_roots,
        protected_paths=protected_paths,
        read_safe_files=read_safe_files,
    )
    state_altering = create_state_altering_tools(
        allowed_roots=config.allowed_roots,
        allowed_apps=config.apps,
        allowed_commands=config.commands,
        os_adapter=os_adapter,
        protected_paths=protected_paths,
        read_safe_files=read_safe_files,
    )
    browser_tools = create_browser_tools(config, session=browser_session)
    gui_tools = create_gui_tools(
        config=config,
        input_controller=input_controller,
        screen_capture=screen_capture,
        supports_images=supports_images,
    )
    memory_tools = create_memory_tools(
        config=config,
        memory_store=memory_store,
        session_state=session_state,
    )
    return read_only + state_altering + browser_tools + gui_tools + memory_tools


__all__ = [
    "BrowserSession",
    "ToolRegistry",
    "create_all_tools",
    "create_browser_tools",
    "create_copy_path_tool",
    "create_create_text_file_tool",
    "create_current_time_tool",
    "create_gui_tools",
    "create_list_directory_tool",
    "create_memory_tools",
    "create_move_path_tool",
    "create_move_to_trash_tool",
    "create_open_app_tool",
    "create_open_path_tool",
    "create_read_only_tools",
    "create_read_text_file_tool",
    "create_run_command_tool",
    "create_search_files_tool",
    "create_state_altering_tools",
    "create_system_info_tool",
]
