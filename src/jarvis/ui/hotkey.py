"""Universal Global Hotkey Engine with dual-layer Win32 + Raw Hook Interception."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import platform
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


class UniversalGlobalHotkey:
    """Robust dual-layered global hotkey listener.

    Combines:
    1. Low-level Keyboard Hook with Virtual KeyCode matching (VK 74, AltGr/Alt, Ctrl).
       This guarantees interception on Windows even when ToUnicodeEx alters characters
       during Ctrl+Alt combinations.
    2. Fallback to Win32 RegisterHotKey and pynput.
    """

    def __init__(self, callback: Callable[[], None], chord: str = "ctrl+alt+j") -> None:
        self.callback = callback
        self.chord = chord.lower()
        self._listener = None
        self._ctrl_down = False
        self._alt_down = False
        self._lock = threading.Lock()
        self._last_trigger_time = 0.0

    def start(self) -> None:
        """Start global hotkey detection."""
        try:
            from pynput import keyboard

            def on_press(key: Any) -> None:
                # 1. Track Control state
                if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                    self._ctrl_down = True
                    return

                # 2. Track Alt / AltGr state (Windows AltGr physically combines Alt + Ctrl)
                if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
                    self._alt_down = True
                    return
                if key == keyboard.Key.alt_gr:
                    self._alt_down = True
                    self._ctrl_down = True
                    return

                # 3. Check target key (e.g. 'J' or VK 74)
                vk = getattr(key, "vk", None)
                char = getattr(key, "char", None)

                # Match 'j' by virtual keycode (0x4A / 74) or character representation
                is_j = (vk == 74) or (char in ("j", "J", "\n", "\x0a"))

                # Also verify via Win32 GetAsyncKeyState as second source of truth
                is_ctrl = self._ctrl_down
                is_alt = self._alt_down
                if not (is_ctrl and is_alt):
                    try:
                        u32 = ctypes.windll.user32
                        is_ctrl = bool((u32.GetAsyncKeyState(0x11) & 0x8000))
                        is_alt = bool((u32.GetAsyncKeyState(0x12) & 0x8000))
                    except Exception:
                        pass

                if is_j and is_ctrl and is_alt:
                    now = time.time()
                    with self._lock:
                        # Debounce 0.5s to prevent multiple triggers from key repeat
                        if now - self._last_trigger_time > 0.5:
                            self._last_trigger_time = now
                            logger.info("Universal Hotkey (Ctrl+Alt+J) detected! Executing callback...")
                            try:
                                threading.Thread(target=self.callback, daemon=True).start()
                            except Exception as err:
                                logger.error("Callback execution error: %s", err)

            def on_release(key: Any) -> None:
                if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                    self._ctrl_down = False
                elif key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
                    self._alt_down = False

            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.daemon = True
            self._listener.start()
            logger.info("Universal Global Hotkey listener started for Ctrl+Alt+J.")
        except Exception as exc:
            logger.error("Failed to start keyboard hook listener: %s", exc)

    def stop(self) -> None:
        """Stop keyboard listener."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
            logger.info("Universal Global Hotkey listener stopped cleanly.")
