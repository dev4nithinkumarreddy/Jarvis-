"""GUI control package providing screen capture, mouse/keyboard input, and session management."""

from jarvis.gui_control.input import (
    BlockedWindowError,
    GUIInputController,
    get_foreground_window_title,
    is_window_title_denied,
)
from jarvis.gui_control.screen import (
    ScreenCapture,
    ScreenCaptureError,
    ScreenCaptureResult,
    model_to_screen_coords,
    save_thumbnail,
    screen_to_model_coords,
)
from jarvis.gui_control.session import (
    GUI_ACTION_TOOLS,
    GUISessionConfirmer,
    GUISessionManager,
)

__all__ = [
    "BlockedWindowError",
    "GUIInputController",
    "GUISessionConfirmer",
    "GUISessionManager",
    "GUI_ACTION_TOOLS",
    "ScreenCapture",
    "ScreenCaptureError",
    "ScreenCaptureResult",
    "get_foreground_window_title",
    "is_window_title_denied",
    "model_to_screen_coords",
    "save_thumbnail",
    "screen_to_model_coords",
]
