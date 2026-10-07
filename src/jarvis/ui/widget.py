"""Floating Translucent Desktop Widget dock using pywebview."""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)


def is_pywebview_available() -> bool:
    """Check if pywebview is installed."""
    try:
        import webview
        return True
    except ImportError:
        return False


def launch_widget(
    url: str,
    title: str = "J.A.R.V.I.S. Mark VII",
    width: int = 440,
    height: int = 760,
    on_top: bool = True,
    transparent: bool = True,
    frameless: bool = True,
) -> None:
    """Launch the Arc Reactor HUD in a borderless, always-on-top, translucent desktop dock.

    Args:
        url: Full local URL to HUD with session token.
        title: Window title.
        width: Initial dock width.
        height: Initial dock height.
        on_top: Keep window always on top.
        transparent: Glassmorphic transparency.
        frameless: Borderless window without standard OS chrome.
    """
    if not is_pywebview_available():
        raise RuntimeError(
            "pywebview is required for the floating desktop widget. "
            "Please install it using: pip install pywebview"
        )

    import webview

    # Append #widget mode parameter if not already present
    widget_url = url
    if "#" in widget_url:
        widget_url = widget_url + "&mode=widget"
    else:
        widget_url = widget_url + "#mode=widget"

    logger.info("Launching floating desktop widget dock at %s", widget_url)

    # Calculate default right-docked screen coordinates if possible
    window = webview.create_window(
        title=title,
        url=widget_url,
        width=width,
        height=height,
        frameless=frameless,
        transparent=transparent,
        on_top=on_top,
        easy_drag=True,
    )

    webview.start(debug=False)
