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
