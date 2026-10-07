"""Platform operating system adapter interface and implementations."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod


class OSAdapter(ABC):
    """Abstract interface for operating system interactions."""

    @abstractmethod
    def open_app(self, command: str) -> bool:
        """Open an application by launch command or executable path.

        Args:
            command: Executable command string.

        Returns:
            True if process launch was initiated, False otherwise.
        """
        pass

    @abstractmethod
    def open_path(self, path: Path | str) -> bool:
        """Open a file or directory with the system default handler.

        Args:
            path: Target file or directory path.

        Returns:
            True if path opening was initiated, False otherwise.
        """
        pass


class WindowsOSAdapter(OSAdapter):
    """Windows-specific OS adapter implementation."""

    def open_app(self, command: str) -> bool:
        """Launch an application command on Windows without shell=True."""
        try:
            cmd_parts = shlex.split(command, posix=False)
            subprocess.Popen(cmd_parts, shell=False)
            return True
        except Exception as exc:
            raise RuntimeError(f"Failed to launch application '{command}' on Windows: {exc}") from exc

    def open_path(self, path: Path | str) -> bool:
        """Open a file or folder in Windows Explorer or its associated default app."""
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")

        try:
            # os.startfile directly calls Win32 ShellExecute without shell=True
            os.startfile(str(target))
            return True
        except Exception as exc:
            raise RuntimeError(f"Failed to open path '{target}' on Windows: {exc}") from exc


class MacOSAdapter(OSAdapter):
    """macOS-specific OS adapter (stub: Windows is the current target OS)."""

    def open_app(self, command: str) -> bool:
        raise NotImplementedError("macOS OSAdapter is not implemented for the Windows target OS.")

    def open_path(self, path: Path | str) -> bool:
        raise NotImplementedError("macOS OSAdapter is not implemented for the Windows target OS.")


class LinuxOSAdapter(OSAdapter):
    """Linux-specific OS adapter (stub: Windows is the current target OS)."""

    def open_app(self, command: str) -> bool:
        raise NotImplementedError("Linux OSAdapter is not implemented for the Windows target OS.")

    def open_path(self, path: Path | str) -> bool:
        raise NotImplementedError("Linux OSAdapter is not implemented for the Windows target OS.")


def get_os_adapter(platform_name: str | None = None) -> OSAdapter:
    """Select and instantiate the appropriate OSAdapter for the platform.

    Args:
        platform_name: Optional explicit sys.platform override (useful for testing).
                       If None, the current sys.platform is used.

    Returns:
        OSAdapter instance matching the platform.
    """
    plat = platform_name if platform_name is not None else sys.platform
    if plat == "win32":
        return WindowsOSAdapter()
    elif plat == "darwin":
        return MacOSAdapter()
    elif plat.startswith("linux"):
        return LinuxOSAdapter()
    else:
        return LinuxOSAdapter()
