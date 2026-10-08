"""Floating Translucent Desktop Widget dock using native browser App Mode or pywebview."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser
from typing import Any

logger = logging.getLogger(__name__)

_widget_proc: subprocess.Popen | None = None
_widget_lock = threading.Lock()


def get_browser_app_executable() -> Path | None:
    """Find installed Chrome or Edge executable supporting --app mode."""
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def is_pywebview_available() -> bool:
    """Check if pywebview or standalone app mode window is available."""
    return get_browser_app_executable() is not None


def toggle_widget(url: str) -> None:
    """Summon, toggle, or restore the floating desktop widget.

    If the widget window is already active, restores it to the front.
    If not open, spawns a borderless dedicated window.
    """
    global _widget_proc
    with _widget_lock:
        if _widget_proc is not None:
            if _widget_proc.poll() is None:
                # Still running: Bring window to foreground via user32 if possible
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    # Find window by title
                    hwnd = user32.FindWindowW(None, "JARVIS HUD // STARK INDUSTRIES MARK VII")
                    if hwnd:
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
                        user32.SetForegroundWindow(hwnd)
                        return
                except Exception:
                    pass
                return
            else:
                _widget_proc = None

    threading.Thread(target=launch_widget, args=(url,), daemon=True).start()


def launch_widget(
    url: str,
    title: str = "J.A.R.V.I.S. Mark VII",
    width: int = 440,
    height: int = 760,
    on_top: bool = True,
    transparent: bool = True,
    frameless: bool = True,
) -> None:
    """Launch the Arc Reactor HUD in a standalone borderless, always-on-top desktop dock.

    Uses native Chrome/Edge Chromium App Mode for 100% native WebSockets, WebGL,
    and glassmorphic CSS performance without COM/WebView2 runtime initialization issues.
    """
    global _widget_proc

    # Append #widget mode parameter if not already present
    widget_url = url
    if "#" in widget_url:
        if "mode=widget" not in widget_url:
            widget_url = widget_url + "&mode=widget"
    else:
        widget_url = widget_url + "#mode=widget"

    browser_bin = get_browser_app_executable()
    if browser_bin is not None:
        logger.info("Launching standalone desktop dock using %s at %s", browser_bin.name, widget_url)
        cmd = [
            str(browser_bin),
            "--new-window",
            f"--app={widget_url}",
            f"--window-size={width},{height}",
            "--window-position=1460,80",
        ]
        with _widget_lock:
            try:
                _widget_proc = subprocess.Popen(cmd)
                return
            except Exception as exc:
                logger.warning("Failed to spawn browser app window (%s), falling back to browser.", exc)

    # Fallback: Open in default browser using Windows shell
    try:
        subprocess.Popen(["cmd.exe", "/c", "start", "", widget_url], shell=False)
    except Exception:
        webbrowser.open(widget_url)
