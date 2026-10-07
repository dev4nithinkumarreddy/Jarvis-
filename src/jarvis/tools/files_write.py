"""State-altering file manipulation tools with confirmation requirements."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Sequence
import send2trash

from jarvis.core.types import RiskTier, ToolSpec
from jarvis.safety.path_guard import resolve_allowed


def create_create_text_file_tool(
    allowed_roots: Sequence[Path | str],
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> ToolSpec:
    """Tool creating a text file inside allowed roots (RiskTier.CONFIRM)."""

    def create_text_file(path: str, content: str) -> str:
        target_path = resolve_allowed(
            path,
            allowed_roots,
            mode="write",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)

        was_overwrite = target_path.exists()
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)

        action = "Overwrote existing" if was_overwrite else "Created new"
        return f"{action} file at '{target_path}' ({len(content)} characters)."

    return ToolSpec(
        name="create_text_file",
        description="Create or overwrite a text file with specified content.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to create.",
                },
                "content": {
                    "type": "string",
                    "description": "Text content to write into the file.",
                },
            },
            "required": ["path", "content"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=create_text_file,
        untrusted_output=False,
    )


def create_move_path_tool(
    allowed_roots: Sequence[Path | str],
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> ToolSpec:
    """Tool moving a file or directory within allowed roots (RiskTier.CONFIRM)."""

    def move_path(src: str, dst: str) -> str:
        source_path = resolve_allowed(
            src,
            allowed_roots,
            mode="move",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        if not source_path.exists():
            raise FileNotFoundError(f"Source path does not exist: {source_path}")

        dest_path = resolve_allowed(
            dst,
            allowed_roots,
            mode="move",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        was_overwrite = dest_path.exists()
        shutil.move(str(source_path), str(dest_path))

        action = "Moved and overwrote" if was_overwrite else "Moved"
        return f"{action} '{source_path}' -> '{dest_path}'."

    return ToolSpec(
        name="move_path",
        description="Move or rename a file or directory.",
        input_schema={
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Source file or directory path.",
                },
                "dst": {
                    "type": "string",
                    "description": "Destination file or directory path.",
                },
            },
            "required": ["src", "dst"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=move_path,
        untrusted_output=False,
    )


def create_copy_path_tool(
    allowed_roots: Sequence[Path | str],
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> ToolSpec:
    """Tool copying a file or directory within allowed roots (RiskTier.CONFIRM)."""

    def copy_path(src: str, dst: str) -> str:
        source_path = resolve_allowed(
            src,
            allowed_roots,
            mode="read",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        if not source_path.exists():
            raise FileNotFoundError(f"Source path does not exist: {source_path}")

        dest_path = resolve_allowed(
            dst,
            allowed_roots,
            mode="copy",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        was_overwrite = dest_path.exists()
        if source_path.is_dir():
            shutil.copytree(str(source_path), str(dest_path), dirs_exist_ok=True)
        else:
            shutil.copy2(str(source_path), str(dest_path))

        action = "Copied and overwrote" if was_overwrite else "Copied"
        return f"{action} '{source_path}' -> '{dest_path}'."

    return ToolSpec(
        name="copy_path",
        description="Copy a file or directory to a destination path.",
        input_schema={
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Source file or directory path.",
                },
                "dst": {
                    "type": "string",
                    "description": "Destination file or directory path.",
                },
            },
            "required": ["src", "dst"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=copy_path,
        untrusted_output=False,
    )


def create_move_to_trash_tool(
    allowed_roots: Sequence[Path | str],
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> ToolSpec:
    """Tool moving a path to Trash/Recycle Bin using send2trash (RiskTier.CONFIRM)."""

    def move_to_trash(path: str) -> str:
        target_path = resolve_allowed(
            path,
            allowed_roots,
            mode="trash",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        if not target_path.exists():
            raise FileNotFoundError(f"Target path to trash does not exist: {target_path}")

        send2trash.send2trash(str(target_path))
        return f"Successfully moved '{target_path}' to Recycle Bin / Trash."

    return ToolSpec(
        name="move_to_trash",
        description="Safely move a file or directory to the system Recycle Bin / Trash.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file or directory to send to Trash.",
                },
            },
            "required": ["path"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=move_to_trash,
        untrusted_output=False,
    )
