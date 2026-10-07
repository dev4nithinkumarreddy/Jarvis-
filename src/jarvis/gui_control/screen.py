"""Screen capture and coordinate mapping utilities with HiDPI and multi-monitor support."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
import logging
from pathlib import Path
from typing import Any

import mss
from PIL import Image

logger = logging.getLogger(__name__)


class ScreenCaptureError(RuntimeError):
    """Raised when screen capture fails (e.g. locked desktop or graphics API error)."""


@dataclass
class ScreenCaptureResult:
    """Represents a captured and scaled screenshot with coordinate metadata."""

    image_bytes: bytes
    scale_factor: float
    original_width: int
    original_height: int
    scaled_width: int
    scaled_height: int
    monitor: dict[str, int]


def model_to_screen_coords(
    x_model: int | float,
    y_model: int | float,
    scale_factor: float,
    monitor: dict[str, int],
) -> tuple[int, int]:
    """Convert model coordinates (from downscaled screenshot) back to real screen coordinates.

    Coordinate Transformation Math:
    -------------------------------
    1. A screenshot of physical monitor size (W_orig, H_orig) at screen offset (left, top)
       is downscaled by scale_factor = min(1.0, max_width / W_orig).
    2. The model operates on the downscaled coordinate space:
       0 <= x_model < W_orig * scale_factor
       0 <= y_model < H_orig * scale_factor
    3. To map back to physical screen space:
       x_rel = round(x_model / scale_factor)
       y_rel = round(y_model / scale_factor)
       x_screen = monitor["left"] + x_rel
       y_screen = monitor["top"] + y_rel
    4. To prevent out-of-bounds clicks, the coordinates are clamped:
       left <= x_screen <= left + W_orig - 1
       top  <= y_screen <= top + H_orig - 1

    Args:
        x_model: X coordinate predicted or requested by the model.
        y_model: Y coordinate predicted or requested by the model.
        scale_factor: The downscale factor applied to the original screenshot (0.0 < scale <= 1.0).
        monitor: Monitor bounding box dict containing 'left', 'top', 'width', and 'height'.

    Returns:
        tuple[int, int]: (x_screen, y_screen) in absolute OS virtual desktop pixels.
    """
    if scale_factor <= 0:
        raise ValueError(f"scale_factor must be positive, got {scale_factor}")

    left = monitor.get("left", 0)
    top = monitor.get("top", 0)
    width = monitor.get("width", 1920)
    height = monitor.get("height", 1080)

    # 1. Unscale to monitor-relative pixels
    x_rel = round(x_model / scale_factor)
    y_rel = round(y_model / scale_factor)

    # 2. Add monitor offset
    x_screen = left + x_rel
    y_screen = top + y_rel

    # 3. Clamp to monitor bounds
    x_clamped = max(left, min(x_screen, left + width - 1))
    y_clamped = max(top, min(y_screen, top + height - 1))

    return int(x_clamped), int(y_clamped)


def screen_to_model_coords(
    x_screen: int | float,
    y_screen: int | float,
    scale_factor: float,
    monitor: dict[str, int],
) -> tuple[int, int]:
    """Convert real screen coordinates to model coordinate space.

    Args:
        x_screen: Absolute OS virtual desktop X coordinate.
        y_screen: Absolute OS virtual desktop Y coordinate.
        scale_factor: Downscale factor applied to original screenshot.
        monitor: Monitor bounding box dict containing 'left', 'top', 'width', and 'height'.

    Returns:
        tuple[int, int]: (x_model, y_model) clamped to the downscaled image dimensions.
    """
    if scale_factor <= 0:
        raise ValueError(f"scale_factor must be positive, got {scale_factor}")

    left = monitor.get("left", 0)
    top = monitor.get("top", 0)
    width = monitor.get("width", 1920)
    height = monitor.get("height", 1080)

    scaled_width = int(round(width * scale_factor))
    scaled_height = int(round(height * scale_factor))

    x_rel = x_screen - left
    y_rel = y_screen - top

    x_model = round(x_rel * scale_factor)
    y_model = round(y_rel * scale_factor)

    x_clamped = max(0, min(x_model, scaled_width - 1))
    y_clamped = max(0, min(y_model, scaled_height - 1))

    return int(x_clamped), int(y_clamped)


def save_thumbnail(
    image_input: Image.Image | bytes,
    output_dir: Path | str = "logs/screens",
    max_dim: int = 160,
    prefix: str = "thumb",
) -> Path:
    """Generate and save a downscaled thumbnail image for audit logging.

    Args:
        image_input: PIL Image or raw image bytes.
        output_dir: Directory where the thumbnail will be saved.
        max_dim: Maximum width or height of the generated thumbnail.
        prefix: Prefix for the generated thumbnail filename.

    Returns:
        Path: Path to the saved thumbnail file.
    """
    if isinstance(image_input, bytes):
        pil_img = Image.open(io.BytesIO(image_input))
    else:
        pil_img = image_input

    # Compute thumbnail scale
    w, h = pil_img.size
    max_side = max(w, h)
    if max_side > max_dim:
        scale = max_dim / max_side
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        thumb = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    else:
        thumb = pil_img.copy()

    dest_dir = Path(output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    file_path = dest_dir / f"{prefix}_{timestamp}.png"

    thumb.save(file_path, format="PNG")
    return file_path


class ScreenCapture:
    """Manages screen grabbing via mss and image scaling with Pillow."""

    def __init__(self, sct: Any | None = None) -> None:
        """Initialize ScreenCapture.
        
        Args:
            sct: Optional pre-configured or mocked mss instance (e.g. for testing).
        """
        self._custom_sct = sct
        self.last_capture: ScreenCaptureResult | None = None

    def get_monitors(self) -> list[dict[str, int]]:
        """Return the list of detected monitors."""
        if self._custom_sct is not None:
            return list(self._custom_sct.monitors)
        with getattr(mss, "MSS", mss.mss)() as sct:
            return list(sct.monitors)

    def capture_screen(
        self,
        monitor_index: int = 1,
        max_width: int = 1280,
    ) -> ScreenCaptureResult:
        """Capture the selected monitor and downscale if wider than max_width.

        Args:
            monitor_index: Index of the monitor (1 = primary display, 0 = all combined).
            max_width: Maximum pixel width for the output image sent to the brain.

        Returns:
            ScreenCaptureResult containing PNG bytes, scale factor, and dimensions.

        Raises:
            ScreenCaptureError: If capture fails due to locked screen, missing permissions, etc.
        """
        try:
            if self._custom_sct is not None:
                sct = self._custom_sct
                monitors = sct.monitors
                idx = monitor_index if monitor_index < len(monitors) else (1 if len(monitors) > 1 else 0)
                monitor = dict(monitors[idx])
                sct_img = sct.grab(monitor)
            else:
                with getattr(mss, "MSS", mss.mss)() as sct:
                    monitors = sct.monitors
                    idx = monitor_index if monitor_index < len(monitors) else (1 if len(monitors) > 1 else 0)
                    monitor = dict(monitors[idx])
                    sct_img = sct.grab(monitor)

            # Convert BGRA mss raw buffer to RGB PIL Image
            raw_img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            orig_w, orig_h = raw_img.size

            scale_factor = min(1.0, max_width / orig_w) if orig_w > 0 else 1.0
            new_w = int(round(orig_w * scale_factor))
            new_h = int(round(orig_h * scale_factor))

            if scale_factor < 1.0:
                scaled_img = raw_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            else:
                scaled_img = raw_img

            buf = io.BytesIO()
            scaled_img.save(buf, format="PNG")
            image_bytes = buf.getvalue()

            result = ScreenCaptureResult(
                image_bytes=image_bytes,
                scale_factor=scale_factor,
                original_width=orig_w,
                original_height=orig_h,
                scaled_width=new_w,
                scaled_height=new_h,
                monitor=monitor,
            )
            self.last_capture = result
            return result
        except Exception as exc:
            if isinstance(exc, ScreenCaptureError):
                raise
            raise ScreenCaptureError(
                f"Failed to capture screen: {exc}. Ensure the display is active and unlocked."
            ) from exc
