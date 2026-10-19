"""System inspection and time tools for Jarvis."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
import psutil

from jarvis.core.types import RiskTier, ToolSpec


def get_system_info() -> dict[str, Any]:
    """Gather current system CPU, memory, disk, and battery information via psutil."""
    # CPU
    cpu_pct = psutil.cpu_percent(interval=0.1)

    # Memory
    mem = psutil.virtual_memory()
    total_mem_gb = round(mem.total / (1024**3), 2)
    avail_mem_gb = round(mem.available / (1024**3), 2)
    mem_pct = mem.percent

    # Disk free for current working directory drive
    disk = psutil.disk_usage(".")
    total_disk_gb = round(disk.total / (1024**3), 2)
    free_disk_gb = round(disk.free / (1024**3), 2)
    disk_pct = disk.percent

    # Battery
    battery_info: dict[str, Any] | None = None
    try:
        batt = psutil.sensors_battery()
        if batt is not None:
            battery_info = {
                "percent": batt.percent,
                "power_plugged": batt.power_plugged,
            }
    except Exception:
        battery_info = None

    return {
        "cpu_percent": cpu_pct,
        "memory": {
            "total_gb": total_mem_gb,
            "available_gb": avail_mem_gb,
            "percent_used": mem_pct,
        },
        "disk": {
            "total_gb": total_disk_gb,
            "free_gb": free_disk_gb,
            "percent_used": disk_pct,
        },
        "battery": battery_info,
    }


def get_current_time() -> dict[str, str]:
    """Retrieve current local and UTC timestamp."""
    local_dt = datetime.now().astimezone()
    utc_dt = datetime.now(timezone.utc)
    return {
        "local_time": local_dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "utc_time": utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "iso": local_dt.isoformat(),
    }


def set_system_volume(level_percent: int) -> dict[str, Any]:
    """Set Windows master system volume percentage (0 to 100)."""
    target = max(0, min(100, int(level_percent)))
    scalar = target / 100.0
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        volume = devices.EndpointVolume
        volume.SetMasterVolumeLevelScalar(scalar, None)
        if target > 0 and volume.GetMute():
            volume.SetMute(0, None)
        return {
            "status": "success",
            "volume_percent": target,
            "muted": False,
            "message": f"Master volume set to {target}%.",
        }
    except Exception as exc:
        return {"status": "error", "error": f"Failed to set volume: {exc}"}


def mute_system_volume(mute: bool | None = None) -> dict[str, Any]:
    """Mute, unmute, or toggle mute state of Windows master volume."""
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        volume = devices.EndpointVolume
        current_mute = bool(volume.GetMute())
        new_mute = (not current_mute) if mute is None else bool(mute)
        volume.SetMute(int(new_mute), None)
        cur_pct = int(volume.GetMasterVolumeLevelScalar() * 100)
        state_str = "muted" if new_mute else "unmuted"
        return {
            "status": "success",
            "muted": new_mute,
            "volume_percent": cur_pct,
            "message": f"Master audio is now {state_str} (level: {cur_pct}%).",
        }
    except Exception as exc:
        return {"status": "error", "error": f"Failed to alter mute state: {exc}"}


def lock_workstation() -> dict[str, Any]:
    """Lock the Windows workstation immediately."""
    import ctypes
    success = bool(ctypes.windll.user32.LockWorkStation())
    return {
        "status": "success" if success else "failed",
        "locked": success,
        "message": "Workstation locked successfully." if success else "Failed to lock workstation.",
    }


def media_control(action: str) -> dict[str, Any]:
    """Control background media playback using Windows virtual media keys."""
    import ctypes
    user32 = ctypes.windll.user32
    act = action.strip().lower()

    # Windows Virtual Key Codes
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_STOP = 0xB2
    VK_MEDIA_PLAY_PAUSE = 0xB3
    KEYEVENTF_KEYUP = 0x0002

    key_map = {
        "play": VK_MEDIA_PLAY_PAUSE,
        "pause": VK_MEDIA_PLAY_PAUSE,
        "play_pause": VK_MEDIA_PLAY_PAUSE,
        "toggle": VK_MEDIA_PLAY_PAUSE,
        "next": VK_MEDIA_NEXT_TRACK,
        "next_track": VK_MEDIA_NEXT_TRACK,
        "skip": VK_MEDIA_NEXT_TRACK,
        "prev": VK_MEDIA_PREV_TRACK,
        "previous": VK_MEDIA_PREV_TRACK,
        "previous_track": VK_MEDIA_PREV_TRACK,
        "stop": VK_MEDIA_STOP,
    }

    vk = key_map.get(act)
    if not vk:
        return {
            "status": "error",
            "error": f"Unsupported media action '{action}'. Supported: play_pause, next, previous, stop.",
        }

    try:
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        return {
            "status": "success",
            "action": act,
            "message": f"Media command '{act}' dispatched successfully.",
        }
    except Exception as exc:
        return {"status": "error", "error": f"Failed to execute media command: {exc}"}


def toggle_show_desktop() -> dict[str, Any]:
    """Minimize all windows to toggle the clean Windows desktop view (Win + D)."""
    import ctypes
    user32 = ctypes.windll.user32
    VK_LWIN = 0x5B
    VK_D = 0x44
    KEYEVENTF_KEYUP = 0x0002
    try:
        user32.keybd_event(VK_LWIN, 0, 0, 0)
        user32.keybd_event(VK_D, 0, 0, 0)
        user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        return {
            "status": "success",
            "message": "Desktop view toggled (Win + D).",
        }
    except Exception as exc:
        return {"status": "error", "error": f"Failed to toggle desktop: {exc}"}



def get_battery_status() -> dict[str, Any]:
    """Retrieve detailed battery charge percentage, AC power status, and estimated run time."""
    batt = psutil.sensors_battery()
    if batt is None:
        return {
            "has_battery": False,
            "message": "System is connected to dedicated AC power (no battery detected).",
        }

    secs = batt.secsleft
    if secs > 0 and secs != psutil.POWER_TIME_UNLIMITED:
        h = secs // 3600
        m = (secs % 3600) // 60
        time_left = f"{h}h {m}m remaining"
    elif batt.power_plugged:
        time_left = "Plugged in / Charging"
    else:
        time_left = "Calculating remaining runtime..."

    return {
        "has_battery": True,
        "percent": round(batt.percent, 1),
        "power_plugged": batt.power_plugged,
        "time_remaining": time_left,
        "message": f"Battery at {round(batt.percent, 1)}% ({'Plugged in' if batt.power_plugged else 'On Battery'}). {time_left}.",
    }


def get_weather(city: str = "") -> dict[str, Any]:
    """Fetch real-time weather conditions and forecast using wttr.in (zero API key needed)."""
    import urllib.parse
    import urllib.request

    c = city.strip() if city else ""
    query = urllib.parse.quote(c) if c else ""
    url = f"https://wttr.in/{query}?format=j1" if query else "https://wttr.in/?format=j1"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            area_info = data.get("nearest_area", [{}])[0]
            area_name = area_info.get("areaName", [{}])[0].get("value", c or "Current Location")
            country = area_info.get("country", [{}])[0].get("value", "")
            current = data.get("current_condition", [{}])[0]

            temp_c = current.get("temp_C", "--")
            temp_f = current.get("temp_F", "--")
            feels_c = current.get("FeelsLikeC", temp_c)
            desc = current.get("weatherDesc", [{}])[0].get("value", "Clear")
            humidity = current.get("humidity", "--")
            wind_kmph = current.get("windspeedKmph", "--")

            loc = f"{area_name}, {country}".strip(", ")
            summary = f"Weather for {loc}: {desc}, {temp_c}°C ({temp_f}°F), feels like {feels_c}°C, humidity {humidity}%, wind {wind_kmph} km/h."
            return {
                "status": "success",
                "location": loc,
                "temperature_c": temp_c,
                "temperature_f": temp_f,
                "feels_like_c": feels_c,
                "condition": desc,
                "humidity": f"{humidity}%",
                "wind_speed_kmph": wind_kmph,
                "summary": summary,
            }
    except Exception as exc:
        return {"status": "error", "error": f"Failed to retrieve weather: {exc}"}


def create_system_info_tool() -> ToolSpec:
    """Create ToolSpec for system_info."""
    return ToolSpec(
        name="system_info",
        description="Retrieve CPU usage, available memory, disk space, and battery status.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        risk_tier=RiskTier.AUTO,
        handler=get_system_info,
        untrusted_output=False,
    )


def create_current_time_tool() -> ToolSpec:
    """Create ToolSpec for current_time."""
    return ToolSpec(
        name="current_time",
        description="Retrieve the current local and UTC date and time.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        risk_tier=RiskTier.AUTO,
        handler=get_current_time,
        untrusted_output=False,
    )


def create_set_volume_tool() -> ToolSpec:
    """Create ToolSpec for set_system_volume."""
    return ToolSpec(
        name="set_system_volume",
        description="Set the Windows master system volume percentage (0 to 100).",
        input_schema={
            "type": "object",
            "properties": {
                "level_percent": {
                    "type": "integer",
                    "description": "Target volume percentage from 0 (silent) to 100 (maximum).",
                },
            },
            "required": ["level_percent"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=set_system_volume,
        untrusted_output=False,
    )


def create_mute_volume_tool() -> ToolSpec:
    """Create ToolSpec for mute_system_volume."""
    return ToolSpec(
        name="mute_system_volume",
        description="Mute, unmute, or toggle mute state of Windows master audio.",
        input_schema={
            "type": "object",
            "properties": {
                "mute": {
                    "type": "boolean",
                    "description": "True to mute, False to unmute, or omit to toggle current mute state.",
                },
            },
        },
        risk_tier=RiskTier.CONFIRM,
        handler=mute_system_volume,
        untrusted_output=False,
    )


def create_lock_workstation_tool() -> ToolSpec:
    """Create ToolSpec for lock_workstation."""
    return ToolSpec(
        name="lock_workstation",
        description="Lock the Windows workstation screen immediately.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        risk_tier=RiskTier.CONFIRM,
        handler=lock_workstation,
        untrusted_output=False,
    )


def create_battery_status_tool() -> ToolSpec:
    """Create ToolSpec for get_battery_status."""
    return ToolSpec(
        name="battery_status",
        description="Retrieve detailed battery percentage, charging state, and remaining operating time.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        risk_tier=RiskTier.AUTO,
        handler=get_battery_status,
        untrusted_output=False,
    )


def create_weather_tool() -> ToolSpec:
    """Create ToolSpec for get_weather."""
    return ToolSpec(
        name="get_weather",
        description="Retrieve real-time weather conditions, temperature, humidity, and forecasts for any city or current location.",
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City or locality name (e.g. 'London', 'Bangalore', 'New York'). Omit for current location.",
                },
            },
        },
        risk_tier=RiskTier.AUTO,
        handler=get_weather,
        untrusted_output=False,
    )


def create_media_control_tool() -> ToolSpec:
    """Create ToolSpec for media_control."""
    return ToolSpec(
        name="media_control",
        description="Control background media playback (Spotify, YouTube Music, etc.): play/pause, next track, previous track, or stop.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play_pause", "next", "previous", "stop"],
                    "description": "Media action to perform: 'play_pause', 'next', 'previous', or 'stop'.",
                },
            },
            "required": ["action"],
        },
        risk_tier=RiskTier.CONFIRM,
        handler=media_control,
        untrusted_output=False,
    )


def create_toggle_show_desktop_tool() -> ToolSpec:
    """Create ToolSpec for toggle_show_desktop."""
    return ToolSpec(
        name="show_desktop",
        description="Toggle the clean Windows desktop view by minimizing/restoring all application windows.",
        input_schema={
            "type": "object",
            "properties": {},
        },
        risk_tier=RiskTier.CONFIRM,
        handler=toggle_show_desktop,
        untrusted_output=False,
    )


