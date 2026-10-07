"""Path guard for enforcing directory boundary safety and protected path integrity."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence


class PathNotAllowed(PermissionError):
    """Raised when an attempted path access is outside the configured allowed roots."""

    def __init__(
        self,
        path: Path | str,
        allowed_roots: Sequence[Path | str],
        resolved_path: Path | None = None,
    ) -> None:
        self.path = path
        self.allowed_roots = list(allowed_roots)
        self.resolved_path = resolved_path
        msg = (
            f"Access denied: path '{path}' (resolved: '{resolved_path}') "
            f"is not within any allowed roots: {[str(r) for r in self.allowed_roots]}"
        )
        super().__init__(msg)


class ProtectedPathError(PermissionError):
    """Raised when an attempted path access targets a protected path."""

    def __init__(
        self,
        path: Path | str,
        protected_root: Path | str,
        operation: str = "access",
        resolved_path: Path | None = None,
    ) -> None:
        self.path = path
        self.protected_root = protected_root
        self.operation = operation
        self.resolved_path = resolved_path
        msg = (
            f"Access denied: path '{path}' (resolved: '{resolved_path}') "
            f"is protected against {operation.upper()} operations (protected target: '{protected_root}')."
        )
        super().__init__(msg)


def is_env_file(path: Path) -> bool:
    """Check if file matches an environment secret file (.env, *.env, .env.*)."""
    name = path.name.lower()
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def get_default_protected_paths() -> list[Path]:
    """Default system protected paths resolved against current working directory."""
    defaults = [
        "config.yaml",
        "AGENT_RULES.md",
        "SECURITY.md",
        "src",
        "logs",
        "data",
        ".browser_profile",
        ".env",
    ]
    return [Path(p).resolve() for p in defaults]


def get_default_read_safe_files() -> list[Path]:
    """Default files permitted for read-only access even if matching protected boundaries."""
    return [Path("README.md").resolve()]


def resolve_allowed(
    path: Path | str,
    allowed_roots: Sequence[Path | str],
    *,
    mode: str = "read",  # "read", "write", "move", "copy", "trash", "delete", "resolve"
    protected_paths: Sequence[Path | str] | None = None,
    read_safe_files: Sequence[Path | str] | None = None,
) -> Path:
    """Expand environment variables and user home, resolve symlinks and '..',
    and verify the resulting path is strictly within one of the allowed roots
    and not under any protected paths.

    Args:
        path: The path to validate and resolve.
        allowed_roots: Sequence of permitted root directory paths.
        mode: Operation type ('read', 'write', 'move', 'copy', 'trash', 'delete', 'resolve').
        protected_paths: Optional sequence of protected paths. If None, defaults are used.
        read_safe_files: Optional sequence of read-safe files. If None, defaults are used.

    Returns:
        The canonical, fully resolved Path object if inside an allowed root.

    Raises:
        PathNotAllowed: If the resolved path falls outside all allowed roots.
        ProtectedPathError: If the resolved path targets a protected path for the requested operation.
    """
    # 1. Expand environment variables and user tilde (~)
    expanded_path_str = os.path.expandvars(os.path.expanduser(str(path)))

    # 2. Fully resolve symlinks, junctions, and relative components ('..', '.')
    resolved_candidate = Path(expanded_path_str).resolve()

    # 3. Check against each resolved allowed root
    inside_allowed = False
    for root in allowed_roots:
        expanded_root_str = os.path.expandvars(os.path.expanduser(str(root)))
        resolved_root = Path(expanded_root_str).resolve()
        try:
            if resolved_candidate == resolved_root or resolved_candidate.is_relative_to(resolved_root):
                inside_allowed = True
                break
        except (ValueError, TypeError):
            # Handles disparate drives or incompatible path types
            continue

    # If no allowed root contained the resolved path, reject
    if not inside_allowed:
        raise PathNotAllowed(
            path=path,
            allowed_roots=allowed_roots,
            resolved_path=resolved_candidate,
        )

    # 4. If mode is pure 'resolve', boundary checks complete
    op = mode.lower()
    if op == "resolve":
        return resolved_candidate

    # 5. Check protected paths
    active_protected = (
        [Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve() for p in protected_paths]
        if protected_paths is not None
        else get_default_protected_paths()
    )
    active_read_safe = (
        [Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve() for p in read_safe_files]
        if read_safe_files is not None
        else get_default_read_safe_files()
    )

    # If operation is read, check if target is explicitly read-safe
    if op == "read":
        for safe in active_read_safe:
            try:
                if resolved_candidate == safe:
                    return resolved_candidate
            except (ValueError, TypeError):
                continue

    # Check env file pattern
    if is_env_file(resolved_candidate):
        raise ProtectedPathError(
            path=path,
            protected_root=resolved_candidate.name,
            operation=op,
            resolved_path=resolved_candidate,
        )

    # Check protected roots
    for prot in active_protected:
        try:
            if resolved_candidate == prot or resolved_candidate.is_relative_to(prot):
                raise ProtectedPathError(
                    path=path,
                    protected_root=prot,
                    operation=op,
                    resolved_path=resolved_candidate,
                )
        except (ValueError, TypeError):
            continue

    return resolved_candidate
