"""GUI control tools for screen capture, mouse clicks, keyboard typing, and scrolling."""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.config import JarvisConfig
from jarvis.core.types import RiskTier, ToolCall, ToolResult, ToolSpec
from jarvis.gui_control.input import BlockedWindowError, GUIInputController
from jarvis.gui_control.screen import (
    ScreenCapture,
    ScreenCaptureError,
    model_to_screen_coords,
    save_thumbnail,
)

logger = logging.getLogger(__name__)


def create_gui_tools(
    config: JarvisConfig,
    input_controller: GUIInputController | None = None,
    screen_capture: ScreenCapture | None = None,
    supports_images: bool = True,
) -> list[ToolSpec]:
    """Create and configure all GUI control ToolSpecs.

    Args:
        config: System configuration with GUI settings.
        input_controller: Optional GUIInputController instance (e.g. for testing with mocks).
        screen_capture: Optional ScreenCapture instance (e.g. for testing with mocks).
        supports_images: Whether the active LLM brain supports image inputs. If False, screenshot tools are disabled.

    Returns:
        list[ToolSpec]: List of registered GUI control tools.
    """
    controller = (
        input_controller
        if input_controller is not None
        else GUIInputController(denied_window_titles=config.gui.denied_window_titles)
    )
    screen = screen_capture if screen_capture is not None else ScreenCapture()
    thumbnail_dir = config.gui.thumbnail_dir
    max_screen_width = config.gui.max_screen_width

    def _safe_capture_thumbnail(prefix: str) -> str | None:
        """Capture screen and save thumbnail for visual audit trail without breaking on failure."""
        try:
            capture_res = screen.capture_screen(max_width=max_screen_width)
            thumb_path = save_thumbnail(capture_res.image_bytes, output_dir=thumbnail_dir, prefix=prefix)
            return str(thumb_path)
        except Exception as exc:
            logger.debug("Visual audit thumbnail capture skipped: %s", exc)
            return None

    def handle_take_screenshot(monitor: Any = 1, **kwargs: Any) -> ToolResult:
        """Capture display screenshot."""
        call_id = ""
        if isinstance(monitor, ToolCall):
            call_id = monitor.id
            monitor_idx = int(monitor.arguments.get("monitor", 1))
        else:
            monitor_idx = int(monitor)

        if not supports_images:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=(
                    "Screenshot tool is disabled: the active brain model does not support vision/image inputs. "
                    "Please configure a vision-capable model to enable screen capture."
                ),
            )

        try:
            res = screen.capture_screen(monitor_index=monitor_idx, max_width=max_screen_width)
            thumb_path = save_thumbnail(res.image_bytes, output_dir=thumbnail_dir, prefix="thumb_screen")
            summary = (
                f"Screenshot captured: monitor={monitor_idx}, "
                f"original_size=({res.original_width}x{res.original_height}), "
                f"scaled_size=({res.scaled_width}x{res.scaled_height}), "
                f"scale_factor={res.scale_factor:.4f}. "
                f"Thumbnail: {thumb_path}"
            )
            return ToolResult(
                call_id=call_id,
                ok=True,
                content=summary,
                image_bytes=res.image_bytes,
                image_media_type="image/png",
                thumbnail_path=str(thumb_path),
            )
        except ScreenCaptureError as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Failed to capture screenshot: {exc}",
            )

    def handle_gui_click(
        x: Any = None,
        y: Any = None,
        button: str = "left",
        **kwargs: Any,
    ) -> ToolResult:
        """Click mouse at specified coordinates."""
        call_id = ""
        if isinstance(x, ToolCall):
            call_id = x.id
            raw_x = x.arguments.get("x")
            raw_y = x.arguments.get("y")
            button = str(x.arguments.get("button", "left")).lower()
        else:
            raw_x = x
            raw_y = y
            button = str(button).lower()

        if raw_x is None or raw_y is None:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Missing required coordinates 'x' and 'y'.",
            )

        try:
            x_int = int(raw_x)
            y_int = int(raw_y)
        except (ValueError, TypeError):
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Coordinates 'x' and 'y' must be valid integers.",
            )

        # Map model coordinates to physical screen coordinates if prior capture exists
        if screen.last_capture is not None:
            real_x, real_y = model_to_screen_coords(
                x_int,
                y_int,
                screen.last_capture.scale_factor,
                screen.last_capture.monitor,
            )
        else:
            real_x, real_y = x_int, y_int

        try:
            controller.click(x=real_x, y=real_y, button=button)
            thumb_path = _safe_capture_thumbnail("thumb_click")
            msg = (
                f"Clicked mouse ({button}) at screen coordinates ({real_x}, {real_y}) "
                f"[model request: ({x_int}, {y_int})]."
            )
            if thumb_path:
                msg += f" Thumbnail: {thumb_path}"
            return ToolResult(
                call_id=call_id,
                ok=True,
                content=msg,
                thumbnail_path=thumb_path,
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Mouse click failed: {exc}",
            )

    def handle_gui_type(text: Any = "", **kwargs: Any) -> ToolResult:
        """Type text into active foreground window."""
        call_id = ""
        if isinstance(text, ToolCall):
            call_id = text.id
            text_str = str(text.arguments.get("text", ""))
        else:
            text_str = str(text)

        try:
            controller.type_text(text_str)
            thumb_path = _safe_capture_thumbnail("thumb_type")
            msg = f"Typed {len(text_str)} characters into foreground window."
            if thumb_path:
                msg += f" Thumbnail: {thumb_path}"
            return ToolResult(
                call_id=call_id,
                ok=True,
                content=msg,
                thumbnail_path=thumb_path,
            )
        except BlockedWindowError as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=str(exc),
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Keyboard typing failed: {exc}",
            )

    def handle_gui_key(key: Any = "", **kwargs: Any) -> ToolResult:
        """Press a keyboard key."""
        call_id = ""
        if isinstance(key, ToolCall):
            call_id = key.id
            key_str = str(key.arguments.get("key", ""))
        else:
            key_str = str(key)

        if not key_str:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Missing required argument 'key'.",
            )

        try:
            controller.press_key(key_str)
            thumb_path = _safe_capture_thumbnail("thumb_key")
            msg = f"Pressed key '{key_str}'."
            if thumb_path:
                msg += f" Thumbnail: {thumb_path}"
            return ToolResult(
                call_id=call_id,
                ok=True,
                content=msg,
                thumbnail_path=thumb_path,
            )
        except BlockedWindowError as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=str(exc),
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Pressing key '{key_str}' failed: {exc}",
            )

    def handle_gui_scroll(amount: Any = None, **kwargs: Any) -> ToolResult:
        """Scroll mouse wheel."""
        call_id = ""
        if isinstance(amount, ToolCall):
            call_id = amount.id
            raw_amount = amount.arguments.get("amount")
        else:
            raw_amount = amount

        if raw_amount is None:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Missing required argument 'amount'.",
            )
        try:
            amount_int = int(raw_amount)
        except (ValueError, TypeError):
            return ToolResult(
                call_id=call_id,
                ok=False,
                error="Argument 'amount' must be an integer.",
            )

        try:
            controller.scroll(amount_int)
            thumb_path = _safe_capture_thumbnail("thumb_scroll")
            direction = "up" if amount_int > 0 else "down"
            msg = f"Scrolled mouse wheel {direction} by {abs(amount_int)} clicks."
            if thumb_path:
                msg += f" Thumbnail: {thumb_path}"
            return ToolResult(
                call_id=call_id,
                ok=True,
                content=msg,
                thumbnail_path=thumb_path,
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                ok=False,
                error=f"Scrolling failed: {exc}",
            )

    return [
        ToolSpec(
            name="take_screenshot",
            description="Capture a screenshot of the specified display monitor.",
            input_schema={
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "integer",
                        "description": "Monitor index to capture (default: 1 for primary display).",
                    },
                },
                "required": [],
            },
            risk_tier=RiskTier.AUTO,
            untrusted_output=True,
            handler=handle_take_screenshot,
        ),
        ToolSpec(
            name="gui_click",
            description="Click the mouse at specified (x, y) coordinates on the screen.",
            input_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate to click."},
                    "y": {"type": "integer", "description": "Y coordinate to click."},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button to click (default: 'left').",
                    },
                },
                "required": ["x", "y"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=handle_gui_click,
        ),
        ToolSpec(
            name="gui_type",
            description="Type a text string using the keyboard into the active window.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type."},
                },
                "required": ["text"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=handle_gui_type,
        ),
        ToolSpec(
            name="gui_key",
            description="Press a keyboard key (e.g. 'enter', 'tab', 'escape', 'down', 'backspace').",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name to press."},
                },
                "required": ["key"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=handle_gui_key,
        ),
        ToolSpec(
            name="gui_scroll",
            description="Scroll the mouse wheel up (positive) or down (negative).",
            input_schema={
                "type": "object",
                "properties": {
                    "amount": {"type": "integer", "description": "Number of scroll clicks (positive up, negative down)."},
                },
                "required": ["amount"],
            },
            risk_tier=RiskTier.CONFIRM,
            handler=handle_gui_scroll,
        ),
    ]
