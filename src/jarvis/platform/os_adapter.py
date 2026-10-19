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
        """Launch an application command on Windows with multi-stage executable resolution."""
        import shutil
        import winreg

        try:
            cmd_parts = shlex.split(command, posix=False)
            if not cmd_parts:
                raise ValueError("Empty application command string.")

            exe = cmd_parts[0]
            args = cmd_parts[1:]

            # 1. Direct path exists (only if contains path separators)
            if ("\\" in exe or "/" in exe) and Path(exe).exists():
                subprocess.Popen(cmd_parts, shell=False)
                return True

            # 2. Executable in system PATH
            found_path = shutil.which(exe)
            if found_path:
                subprocess.Popen([found_path] + args, shell=False)
                return True

            # 3. Check Windows App Paths Registry (e.g. chrome.exe, msedge.exe)
            reg_name = exe if exe.lower().endswith(".exe") else f"{exe}.exe"
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                key_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{reg_name}"
                try:
                    with winreg.OpenKey(root, key_path) as k:
                        app_path, _ = winreg.QueryValueEx(k, "")
                        if app_path and Path(app_path).exists():
                            subprocess.Popen([app_path] + args, shell=False)
                            return True
                except Exception:
                    pass

            # 4. Fallback to Windows ShellExecute / os.startfile
            try:
                os.startfile(command)
                return True
            except Exception:
                pass

            # 5. Final attempt with original command parts
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
