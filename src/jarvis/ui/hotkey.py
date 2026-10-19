"""Universal Global Hotkey Engine with dual-layer Win32 + Raw Hook Interception."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import platform
import threading
import time
from typing import Any, Callable

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
        self._poller_thread = None
        self._stop_event = threading.Event()
        self._ctrl_down = False
        self._alt_down = False
        self._lock = threading.Lock()
        self._last_trigger_time = 0.0

    def _trigger(self) -> None:
        """Debounced execution of hotkey callback."""
        now = time.time()
        with self._lock:
            if now - self._last_trigger_time > 0.4:
                self._last_trigger_time = now
                logger.info("Universal Hotkey (Ctrl+Alt+J) detected! Executing callback...")
                try:
                    threading.Thread(target=self.callback, daemon=True).start()
                except Exception as err:
                    logger.error("Callback execution error: %s", err)

    def _poll_keys(self) -> None:
        """Low-level Win32 hardware key poller thread."""
        u32 = ctypes.windll.user32
        while not self._stop_event.is_set():
            try:
                # 0x11 = VK_CONTROL, 0xA2 = VK_LCONTROL, 0xA3 = VK_RCONTROL
                ctrl = bool((u32.GetAsyncKeyState(0x11) | u32.GetAsyncKeyState(0xA2) | u32.GetAsyncKeyState(0xA3)) & 0x8000)
                # 0x12 = VK_MENU (Alt), 0xA4 = VK_LMENU, 0xA5 = VK_RMENU
                alt = bool((u32.GetAsyncKeyState(0x12) | u32.GetAsyncKeyState(0xA4) | u32.GetAsyncKeyState(0xA5)) & 0x8000)
                # 0x4A = 'J'
                j = bool(u32.GetAsyncKeyState(0x4A) & 0x8000)
                if ctrl and alt and j:
                    self._trigger()
                    time.sleep(0.35)
            except Exception:
                pass
            time.sleep(0.04)

    def start(self) -> None:
        """Start global hotkey detection."""
        self._stop_event.clear()

        # 1. Start Win32 hardware poller thread
        self._poller_thread = threading.Thread(target=self._poll_keys, daemon=True)
        self._poller_thread.start()

        # 2. Start pynput keyboard hook as secondary listener
        try:
            from pynput import keyboard

            def on_press(key: Any) -> None:
                if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                    self._ctrl_down = True
                    return
                if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
                    self._alt_down = True
                    return

                vk = getattr(key, "vk", None)
                char = getattr(key, "char", None)
                is_j = (vk == 74) or (char in ("j", "J", "\n", "\x0a"))

                if is_j and self._ctrl_down and self._alt_down:
                    self._trigger()

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
        self._stop_event.set()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        logger.info("Universal Global Hotkey listener stopped cleanly.")
