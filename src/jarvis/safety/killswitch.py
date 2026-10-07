"""Process-wide KillSwitch and global hotkey listener using pynput."""

from __future__ import annotations

import logging
import threading
from typing import Callable
from pynput import keyboard

logger = logging.getLogger(__name__)


def normalize_hotkey(hotkey_str: str) -> str:
    """Normalize a human-readable hotkey combination into pynput's expected format.

    Examples:
        'Ctrl+Alt+Shift+K' -> '<ctrl>+<alt>+<shift>+k'
        '<ctrl>+<alt>+<shift>+k' -> '<ctrl>+<alt>+<shift>+k'
    """
    modifiers = {"ctrl", "alt", "shift", "cmd", "super", "win"}
    parts = [p.strip() for p in hotkey_str.split("+") if p.strip()]
    normalized = []
    for part in parts:
        cleaned = part.lower().strip("<>")
        if cleaned in modifiers:
            normalized.append(f"<{cleaned}>")
        else:
            normalized.append(cleaned)
    return "+".join(normalized)


class KillSwitch:
    """Process-wide emergency stop mechanism for Jarvis tool execution.

    The orchestrator checks `is_set()` before dispatching every tool call. Once triggered,
    subsequent tool calls are immediately halted.

    Operating System Permission Requirements:
    - Windows:
      Uses Win32 low-level keyboard hooks (`SetWindowsHookExW`). No special permissions
      are required for standard desktop applications. However, if an elevated (Administrator)
      window currently has input focus, Windows UIPI (User Interface Privilege Isolation)
      blocks non-elevated hooks from receiving keystrokes unless Jarvis is also run as Administrator.
    - macOS:
      Requires Accessibility permissions in System Settings -> Privacy & Security -> Accessibility.
      Without explicit user approval, macOS Quartz event taps will fail to intercept keystrokes.
    - Linux:
      Requires an active X11 display. Under native Wayland sessions, global keystroke interception
      is restricted by design unless running under XWayland or with specialized compositor input portals.
    """

    def __init__(self, on_trigger: Callable[[], None] | None = None) -> None:
        self._event = threading.Event()
        self._listener: keyboard.GlobalHotKeys | None = None
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        if on_trigger is not None:
            self._callbacks.append(on_trigger)

    def add_on_trigger(self, callback: Callable[[], None]) -> None:
        """Register an additional callback to execute when the kill switch triggers."""
        with self._lock:
            self._callbacks.append(callback)

    def trigger(self) -> None:
        """Trigger the kill switch, halting all subsequent tool executions."""
        self._event.set()
        logger.warning("KillSwitch triggered! All tool executions will be halted.")
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"Error executing on_trigger callback: {e}")

    def is_set(self) -> bool:
        """Check whether the kill switch has been engaged.

        Returns:
            True if triggered, False otherwise.
        """
        return self._event.is_set()

    def reset(self) -> None:
        """Reset the kill switch back to normal state."""
        self._event.clear()

    def start_hotkey_listener(self, hotkey: str = "Ctrl+Alt+Shift+K") -> None:
        """Start global hotkey listener in a daemon thread.

        Args:
            hotkey: Hotkey string (e.g. 'Ctrl+Alt+Shift+K').
        """
        with self._lock:
            if self._listener is not None and self._listener.is_alive():
                return  # Listener already running

            parsed_hotkey = normalize_hotkey(hotkey)

            def _on_hotkey() -> None:
                self.trigger()

            hotkey_mapping = {parsed_hotkey: _on_hotkey}
            self._listener = keyboard.GlobalHotKeys(hotkey_mapping)
            self._listener.daemon = True
            self._listener.start()
            logger.info(f"KillSwitch hotkey listener started for '{parsed_hotkey}'")

    def stop_hotkey_listener(self) -> None:
        """Stop the background hotkey listener if active."""
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None


# Global process-wide default instance
_default_kill_switch: KillSwitch | None = None
_instance_lock = threading.Lock()


def get_kill_switch() -> KillSwitch:
    """Obtain the process-wide KillSwitch instance."""
    global _default_kill_switch
    with _instance_lock:
        if _default_kill_switch is None:
            _default_kill_switch = KillSwitch()
        return _default_kill_switch
