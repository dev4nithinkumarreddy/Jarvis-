"""Read-only file operations guarded by path boundaries."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Sequence

from jarvis.core.types import RiskTier, ToolSpec
from jarvis.safety.path_guard import resolve_allowed


def create_list_directory_tool(allowed_roots: Sequence[Path | str]) -> ToolSpec:
    """Create list_directory ToolSpec constrained by allowed_roots."""

    def list_directory(path: str = ".") -> list[dict[str, Any]]:
        target_path = resolve_allowed(path, allowed_roots, mode="resolve")
        if not target_path.is_dir():
            raise ValueError(f"Path '{path}' (resolved: '{target_path}') is not a directory.")

        results = []
        for entry in sorted(target_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            is_directory = entry.is_dir()
            size = entry.stat().st_size if entry.is_file() else None
            results.append({
                "name": entry.name,
                "type": "directory" if is_directory else "file",
                "size_bytes": size,
            })
        return results

    return ToolSpec(
        name="list_directory",
        description="List files and subdirectories within an allowed directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to inspect. Defaults to current directory ('.').",
                    "default": ".",
                },
            },
        },
        risk_tier=RiskTier.AUTO,
        handler=list_directory,
        untrusted_output=False,
    )


def create_read_text_file_tool(
    allowed_roots: Sequence[Path | str],
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> ToolSpec:
    """Create read_text_file ToolSpec constrained by allowed_roots with untrusted_output=True."""

    def read_text_file(path: str, max_bytes: int = 100000) -> str:
        target_path = resolve_allowed(
            path,
            allowed_roots,
            mode="read",
            protected_paths=protected_paths,
            read_safe_files=read_safe_files,
        )
        if not target_path.is_file():
            raise ValueError(f"Path '{path}' (resolved: '{target_path}') is not a file.")

        with open(target_path, "rb") as f:
            raw_bytes = f.read(max_bytes + 1)

        is_truncated = len(raw_bytes) > max_bytes
        content_bytes = raw_bytes[:max_bytes] if is_truncated else raw_bytes
        text = content_bytes.decode("utf-8", errors="replace")

        if is_truncated:
            text += f"\n\n[Warning: File content truncated at {max_bytes} bytes]"
        return text

    return ToolSpec(
        name="read_text_file",
        description="Read the text content of a file up to max_bytes.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum number of bytes to read. Defaults to 100000.",
                    "default": 100000,
                },
            },
            "required": ["path"],
        },
        risk_tier=RiskTier.AUTO,
        handler=read_text_file,
        untrusted_output=True,  # External content can taint session
    )


def create_search_files_tool(allowed_roots: Sequence[Path | str]) -> ToolSpec:
    """Create search_files ToolSpec constrained by allowed_roots."""

    def search_files(
        root: str = ".",
        name_pattern: str = "*",
        max_results: int = 50,
    ) -> list[str]:
        target_root = resolve_allowed(root, allowed_roots, mode="resolve")
        if not target_root.is_dir():
            raise ValueError(f"Root path '{root}' (resolved: '{target_root}') is not a directory.")

        matches: list[str] = []
        for p in target_root.rglob("*"):
            if fnmatch.fnmatch(p.name, name_pattern):
                try:
                    # Guard against symlinks escaping root
                    resolve_allowed(p, allowed_roots, mode="resolve")
                    rel = p.relative_to(target_root)
                    matches.append(str(rel))
                    if len(matches) >= max_results:
                        break
                except Exception:
                    continue

        return matches

    return ToolSpec(
        name="search_files",
        description="Search for files by name glob pattern within an allowed directory.",
        input_schema={
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "Directory root to search from. Defaults to current directory ('.').",
                    "default": ".",
                },
                "name_pattern": {
                    "type": "string",
                    "description": "Glob pattern to match file names (e.g. '*.py', '*test*'). Defaults to '*'.",
                    "default": "*",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matching file results to return. Defaults to 50.",
                    "default": 50,
                },
            },
        },
        risk_tier=RiskTier.AUTO,
        handler=search_files,
        untrusted_output=False,
    )
