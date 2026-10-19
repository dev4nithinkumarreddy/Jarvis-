"""Hands-free background countdown timers and voice reminders for J.A.R.V.I.S."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from jarvis.core.types import RiskTier, ToolSpec

logger = logging.getLogger(__name__)

# Active background timer tracking
_active_timers: dict[str, threading.Timer] = {}
_timer_lock = threading.Lock()


def set_timer(
    seconds: int,
    label: str = "Timer",
    notify_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Start a non-blocking background countdown timer that alerts the user upon expiry.

    Args:
        seconds: Countdown duration in seconds (must be >= 1).
        label: Name or purpose of the timer (e.g. 'Coffee', 'Code Build').
        notify_callback: Optional callable receiving expiration notification.
    """
    sec = max(1, int(seconds))
    lbl = label.strip() if label else "Timer"
    timer_id = f"{lbl}_{int(time.time())}"

    def _on_expire():
        with _timer_lock:
            _active_timers.pop(timer_id, None)

        msg = f"Sir, your timer for '{lbl}' ({sec} seconds) has expired."
        logger.info("Timer expired: %s", msg)

        # 1. Play high-tech Stark chime
        try:
            from jarvis.voice.triggers import play_ptt_tone
            play_ptt_tone("summon")
        except Exception:
            pass

        # 2. Invoke notification callback (speaks aloud / updates UI)
        if notify_callback:
            try:
                notify_callback(msg)
            except Exception as exc:
                logger.debug("Timer notification callback failed: %s", exc)

    timer_thread = threading.Timer(sec, _on_expire)
    timer_thread.daemon = True
    with _timer_lock:
        _active_timers[timer_id] = timer_thread
        timer_thread.start()

    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    time_str = f"{h}h {m}m {s}s" if h > 0 else (f"{m}m {s}s" if m > 0 else f"{s} seconds")

    return {
        "status": "success",
        "timer_id": timer_id,
        "label": lbl,
        "duration_seconds": sec,
        "message": f"Timer for '{lbl}' set for {time_str}. I will alert you upon completion, Sir.",
    }


def cancel_timer(timer_id_or_label: str) -> dict[str, Any]:
    """Cancel an active background timer by ID or matching label."""
    target = timer_id_or_label.strip().lower()
    with _timer_lock:
        found_key = None
        for tid in list(_active_timers.keys()):
            if tid.lower() == target or target in tid.lower():
                found_key = tid
                break
        if found_key:
            timer = _active_timers.pop(found_key)
            timer.cancel()
            return {
                "status": "success",
                "message": f"Timer '{found_key}' has been cancelled.",
            }

    return {
        "status": "not_found",
        "message": f"No active timer matching '{timer_id_or_label}' was found.",
    }


def list_active_timers() -> dict[str, Any]:
    """List all currently active countdown timers."""
    with _timer_lock:
        keys = list(_active_timers.keys())
    return {
        "status": "success",
        "count": len(keys),
        "timers": keys,
        "message": f"Currently {len(keys)} timer(s) active: {', '.join(keys) if keys else 'None'}.",
    }


def create_timer_tool(notify_callback: Callable[[str], None] | None = None) -> ToolSpec:
    """Create ToolSpec for set_timer."""
    def _handler(seconds: int, label: str = "Timer", **kwargs: Any) -> dict[str, Any]:
        return set_timer(seconds=seconds, label=label, notify_callback=notify_callback)

    return ToolSpec(
        name="set_timer",
        description="Set a background countdown timer for any number of seconds with an audible Stark alert chime upon completion.",
        input_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "Duration of timer in seconds (e.g. 60 for 1 minute, 300 for 5 minutes).",
                },
                "label": {
                    "type": "string",
                    "description": "Descriptive name for the timer (e.g. 'Tea', 'Meeting', 'Code build').",
                },
            },
            "required": ["seconds"],
        },
        risk_tier=RiskTier.AUTO,
        handler=_handler,
        untrusted_output=False,
    )


def create_cancel_timer_tool() -> ToolSpec:
    """Create ToolSpec for cancel_timer."""
    return ToolSpec(
        name="cancel_timer",
        description="Cancel a running countdown timer by label or name.",
        input_schema={
            "type": "object",
            "properties": {
                "timer_id_or_label": {
                    "type": "string",
                    "description": "Label or timer identifier to cancel.",
                },
            },
            "required": ["timer_id_or_label"],
        },
        risk_tier=RiskTier.AUTO,
        handler=cancel_timer,
        untrusted_output=False,
    )


def create_list_timers_tool() -> ToolSpec:
    """Create ToolSpec for list_active_timers."""
    return ToolSpec(
        name="list_timers",
        description="Check all running background timers and countdowns.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        risk_tier=RiskTier.AUTO,
        handler=list_active_timers,
        untrusted_output=False,
    )
