"""Tests for platform OSAdapter interfaces and implementations."""

from pathlib import Path
import sys
from unittest.mock import patch
import pytest

from jarvis.platform.os_adapter import (
    LinuxOSAdapter,
    MacOSAdapter,
    OSAdapter,
    WindowsOSAdapter,
    get_os_adapter,
)


def test_os_adapter_abc():
    """Verify OSAdapter cannot be instantiated directly."""
    with pytest.raises(TypeError):
        OSAdapter()  # type: ignore


def test_platform_stubs_raise_not_implemented():
    """Verify that non-target platform stubs raise NotImplementedError."""
    mac = MacOSAdapter()
    lin = LinuxOSAdapter()

    with pytest.raises(NotImplementedError, match="macOS OSAdapter is not implemented"):
        mac.open_app("calculator")
    with pytest.raises(NotImplementedError, match="macOS OSAdapter is not implemented"):
        mac.open_path("some/path")

    with pytest.raises(NotImplementedError, match="Linux OSAdapter is not implemented"):
        lin.open_app("terminal")
    with pytest.raises(NotImplementedError, match="Linux OSAdapter is not implemented"):
        lin.open_path("some/path")


def test_windows_os_adapter_implementation(tmp_path: Path):
    """Verify WindowsOSAdapter executes open_app and open_path on Windows."""
    adapter = WindowsOSAdapter()

    # Test open_app via subprocess.Popen mock
    with patch("subprocess.Popen") as mock_popen:
        assert adapter.open_app("notepad.exe test.txt") is True
        mock_popen.assert_called_once_with(["notepad.exe", "test.txt"], shell=False)

    # Test open_path via os.startfile mock (or real file)
    test_file = tmp_path / "doc.txt"
    test_file.write_text("content", encoding="utf-8")

    with patch("os.startfile", create=True) as mock_startfile:
        assert adapter.open_path(test_file) is True
        mock_startfile.assert_called_once_with(str(test_file.resolve()))


def test_get_os_adapter_by_platform_name():
    """Verify selector returns correct adapter for platform strings."""
    win = get_os_adapter("win32")
    assert isinstance(win, WindowsOSAdapter)

    mac = get_os_adapter("darwin")
    assert isinstance(mac, MacOSAdapter)

    lin = get_os_adapter("linux")
    assert isinstance(lin, LinuxOSAdapter)


def test_get_os_adapter_current_platform():
    """Verify selector for the current running sys.platform."""
    adapter = get_os_adapter()
    assert isinstance(adapter, OSAdapter)
    if sys.platform == "win32":
        assert isinstance(adapter, WindowsOSAdapter)
    elif sys.platform == "darwin":
        assert isinstance(adapter, MacOSAdapter)
    elif sys.platform.startswith("linux"):
        assert isinstance(adapter, LinuxOSAdapter)
