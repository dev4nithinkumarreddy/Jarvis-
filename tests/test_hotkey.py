"""Tests for the dual-mode UniversalGlobalHotkey manager."""

import time
from unittest.mock import MagicMock
from pynput import keyboard
import pytest

from jarvis.ui.hotkey import UniversalGlobalHotkey


def test_universal_global_hotkey_start_stop():
    """Verify UniversalGlobalHotkey starts and stops cleanly."""
    callback = MagicMock()
    hk = UniversalGlobalHotkey(callback=callback, chord="ctrl+alt+j")
    hk.start()
    assert hk._listener is not None
    hk.stop()
    assert hk._listener is None


def test_universal_global_hotkey_dispatch():
    """Verify UniversalGlobalHotkey dispatches callback on Ctrl + Alt + J sequence."""
    fired = []
    hk = UniversalGlobalHotkey(callback=lambda: fired.append(True), chord="ctrl+alt+j")
    hk.start()

    # Simulate keyboard events on the hook listener
    hk._listener.on_press(keyboard.Key.ctrl_l)
    hk._listener.on_press(keyboard.Key.alt_l)
    # Simulate virtual keycode 74 (J key on Windows)
    hk._listener.on_press(keyboard.KeyCode(vk=74, char=None))

    time.sleep(0.1)
    hk.stop()
    assert len(fired) >= 1
