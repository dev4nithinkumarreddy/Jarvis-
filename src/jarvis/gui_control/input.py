"""Keyboard and mouse input wrappers with fail-safe enforcement and sensitive window blocking."""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

import pyautogui

logger = logging.getLogger(__name__)


def get_foreground_window_title() -> str:
    """Retrieve the title of the current active foreground window.

    On Windows, uses GetForegroundWindow and GetWindowTextW via ctypes.
    On non-Windows platforms or when unavailable, returns an empty string.

    Returns:
        str: Window title of the active window, or empty string if undetectable.
    """
    if sys.platform != "win32":
        return ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()
    except Exception as exc:
        logger.warning("Failed to retrieve foreground window title: %s", exc)
        return ""


def is_window_title_denied(title: str, denied_titles: list[str]) -> str | None:
    """Check if the given window title matches any keyword in the denylist.

    Args:
        title: Window title to check.
        denied_titles: List of disallowed substring keywords (e.g. '1password', 'bank').

    Returns:
        str | None: The matched blocked keyword, or None if safe.
    """
    if not title or not denied_titles:
        return None
    lower_title = title.lower()
    for keyword in denied_titles:
        cleaned_kw = keyword.strip().lower()
        if cleaned_kw and cleaned_kw in lower_title:
            return keyword
    return None


class BlockedWindowError(PermissionError):
    """Raised when typing is attempted into a blacklisted or sensitive window."""


class GUIInputController:
    """Controls mouse and keyboard inputs with failsafe mechanisms and window filters."""

    def __init__(
        self,
        denied_window_titles: list[str] | None = None,
        pause_seconds: float = 0.1,
        pyautogui_module: Any | None = None,
    ) -> None:
        """Initialize GUIInputController.

        Args:
            denied_window_titles: List of window title substrings where typing is blocked.
            pause_seconds: Delay pause between pyautogui actions.
            pyautogui_module: Optional mock or custom pyautogui module for testing.
        """
        self.denied_window_titles = list(denied_window_titles or [])
        self._pag = pyautogui_module if pyautogui_module is not None else pyautogui
        self._configure_pyautogui(pause_seconds)

    def _configure_pyautogui(self, pause_seconds: float) -> None:
        """Ensure FAILSAFE is enabled and set action pause."""
        try:
            self._pag.FAILSAFE = True
            self._pag.PAUSE = max(0.01, pause_seconds)
        except Exception as exc:
            logger.warning("Could not set pyautogui attributes: %s", exc)

    def check_typing_permission(self) -> None:
        """Verify that the active window does not match any blocked window titles.

        Raises:
            BlockedWindowError: If foreground window title matches any blocked keyword.
        """
        title = get_foreground_window_title()
        matched = is_window_title_denied(title, self.denied_window_titles)
        if matched:
            raise BlockedWindowError(
                f"Typing blocked: Foreground window '{title}' matches blacklisted keyword '{matched}'. "
                "Jarvis refuses to type into sensitive or credential-bearing applications."
            )

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click at absolute screen coordinates (x, y).

        Args:
            x: Absolute virtual desktop X coordinate.
            y: Absolute virtual desktop Y coordinate.
            button: 'left', 'right', or 'middle'.
        """
        self._pag.click(x=x, y=y, button=button)

    def double_click(self, x: int, y: int) -> None:
        """Double click at absolute screen coordinates (x, y).

        Args:
            x: Absolute virtual desktop X coordinate.
            y: Absolute virtual desktop Y coordinate.
        """
        self._pag.doubleClick(x=x, y=y)

    def type_text(self, text: str, interval: float = 0.01) -> None:
        """Type text string using keyboard, enforcing foreground window denylist.

        Args:
            text: Text to type.
            interval: Seconds delay between key strokes.

        Raises:
            BlockedWindowError: If foreground window title matches the denylist.
        """
        self.check_typing_permission()
        self._pag.write(text, interval=interval)

    def press_key(self, key: str) -> None:
        """Press a single key (e.g. 'enter', 'tab', 'esc', 'down').

        Args:
            key: Name of the key to press.
        """
        self.check_typing_permission()
        self._pag.press(key)

    def hotkey(self, *keys: str) -> None:
        """Press a combination of keys simultaneously (e.g. 'ctrl', 'c').

        Args:
            keys: Sequence of keys to press down and release.
        """
        self.check_typing_permission()
        self._pag.hotkey(*keys)

    def scroll(self, amount: int) -> None:
        """Scroll the mouse wheel.

        Args:
            amount: Number of clicks to scroll (positive scrolls up, negative scrolls down).
        """
        self._pag.scroll(amount)
