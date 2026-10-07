"""Tests for autostart, Windows shortcut creation, and Arc Reactor icon generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from PIL import Image
import pytest

from jarvis.ui.autostart import (
    create_windows_shortcut,
    generate_arc_reactor_icon,
    get_windows_desktop_dir,
    get_windows_startup_dir,
)


def test_generate_arc_reactor_icon(tmp_path: Path) -> None:
    """Arc Reactor icon is generated procedurally with multiple resolutions in valid ICO format."""
    ico_file = tmp_path / "test_reactor.ico"
    generate_arc_reactor_icon(ico_file)

    assert ico_file.exists()
    assert ico_file.stat().st_size > 500

    # Verify Pillow can open and read ICO layers
    with Image.open(ico_file) as img:
        assert img.format == "ICO"
        assert img.size == (256, 256)


def test_get_windows_directories() -> None:
    """Windows Startup and Desktop directories are resolved to valid Path objects."""
    startup = get_windows_startup_dir()
    desktop = get_windows_desktop_dir()

    assert isinstance(startup, Path)
    assert isinstance(desktop, Path)
    assert "Startup" in str(startup)
