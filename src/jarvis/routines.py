"""Scheduled routines using APScheduler for automated daily briefings and health checks."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from jarvis.core.channels import OutputChannel
from jarvis.core.config import JarvisConfig, RoutinesConfig
from jarvis.core.types import RiskTier, ToolCall
from jarvis.safety.path_guard import PathNotAllowed, resolve_allowed
from jarvis.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Directories ignored when scanning for files modified today
IGNORED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    ".browser_profile",
}


def get_files_modified_today(
    allowed_roots: list[Path | str],
    max_files_scanned: int = 500,
    scan_timeout_seconds: float = 5.0,
) -> tuple[list[dict[str, Any]], str | None]:
    """Scan allowed roots for files modified today since 00:00:00 local time.

    Enforces bounds on maximum files scanned and scan timeout, and skips symlinks
    that leave allowed roots.

    Args:
        allowed_roots: List of allowed root paths.
        max_files_scanned: Maximum number of files to scan before halting.
        scan_timeout_seconds: Maximum scan duration in seconds before halting.

    Returns:
        tuple: (modified_files list, limit_hit reason string or None)
    """
    now = datetime.now()
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_timestamp = midnight_today.timestamp()

    start_time = time.monotonic()
    files_scanned_count = 0
    limit_hit: str | None = None
    modified_files: list[dict[str, Any]] = []

    for root_input in allowed_roots:
        if limit_hit:
            break
        try:
            resolved_root = resolve_allowed(root_input, allowed_roots)
            if not resolved_root.is_dir():
                continue

            for dirpath, dirnames, filenames in os.walk(resolved_root):
                if limit_hit:
                    break

                # Filter directories: remove ignored and directory symlinks leaving allowed roots
                filtered_dirs: list[str] = []
                for d in dirnames:
                    if d in IGNORED_DIR_NAMES or d.startswith("."):
                        continue
                    d_path = Path(dirpath) / d
                    if d_path.is_symlink():
                        try:
                            resolve_allowed(d_path, allowed_roots)
                            filtered_dirs.append(d)
                        except (PathNotAllowed, OSError):
                            continue
                    else:
                        filtered_dirs.append(d)
                dirnames[:] = filtered_dirs

                for fname in filenames:
                    if (time.monotonic() - start_time) >= scan_timeout_seconds:
                        limit_hit = f"Scan timeout reached ({scan_timeout_seconds}s limit)"
                        break

                    files_scanned_count += 1
                    if files_scanned_count > max_files_scanned:
                        limit_hit = f"Maximum file scan limit reached ({max_files_scanned} files limit)"
                        break

                    file_path = Path(dirpath) / fname
                    # Do not follow symlinks that leave allowed roots
                    if file_path.is_symlink():
                        try:
                            resolve_allowed(file_path, allowed_roots)
                        except (PathNotAllowed, OSError):
                            continue

                    try:
                        stat = file_path.stat()
                        if stat.st_mtime >= midnight_timestamp:
                            mtime_dt = datetime.fromtimestamp(stat.st_mtime)
                            modified_files.append({
                                "path": str(file_path),
                                "modified": mtime_dt.strftime("%H:%M:%S"),
                                "size": stat.st_size,
                            })
                    except (OSError, PermissionError):
                        continue
        except Exception as exc:
            logger.debug("Failed scanning root %s: %s", root_input, exc)

    # Sort most recently modified first
    modified_files.sort(key=lambda x: x["modified"], reverse=True)
    return modified_files, limit_hit


class RoutinesManager:
    """Manages scheduled background routines, enforcing strict AUTO-only tool execution."""

    def __init__(
        self,
        config: JarvisConfig | RoutinesConfig,
        tool_registry: ToolRegistry,
        output_channel: OutputChannel | None = None,
        allowed_roots: list[Path] | None = None,
    ) -> None:
        """Initialize RoutinesManager.

        Args:
            config: JarvisConfig or RoutinesConfig instance.
            tool_registry: ToolRegistry containing tools.
            output_channel: Channel to deliver scheduled routine outputs.
            allowed_roots: Directories scanned for modified files.
        """
        if isinstance(config, JarvisConfig):
            self.routines_config = config.routines
            self.allowed_roots = list(config.allowed_roots)
        else:
            self.routines_config = config
            self.allowed_roots = list(allowed_roots or [Path(".")])

        self.tool_registry = tool_registry
        self.output_channel = output_channel
        self.scheduler = BackgroundScheduler(daemon=True)
        self._setup_jobs()

    def _setup_jobs(self) -> None:
        """Configure scheduled jobs if routines are enabled."""
        if not self.routines_config.enabled:
            return

        time_str = self.routines_config.briefing_time.strip()
        try:
            parts = time_str.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except Exception as exc:
            logger.warning("Invalid briefing_time '%s', defaulting to 09:00: %s", time_str, exc)
            hour, minute = 9, 0

        trigger = CronTrigger(hour=hour, minute=minute)
        self.scheduler.add_job(
            self.run_daily_briefing,
            trigger=trigger,
            id="daily_briefing",
            replace_existing=True,
        )

    def execute_routine_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Execute a tool for a routine, strictly enforcing AUTO risk tier.

        Args:
            tool_name: The name of the tool to execute.
            arguments: Arguments for the tool.

        Raises:
            PermissionError: If the tool is CONFIRM or BLOCKED.
            ValueError: If tool does not exist.
        """
        spec = self.tool_registry.get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown tool '{tool_name}' requested by routine.")

        # Hard safety enforcement: Routines can NEVER invoke non-AUTO tools
        if spec.risk_tier != RiskTier.AUTO:
            raise PermissionError(
                f"Safety violation: Routines can NEVER invoke non-AUTO tools. "
                f"Tool '{tool_name}' has risk tier '{spec.risk_tier.value}', which is prohibited."
            )

        call = ToolCall(
            id=f"routine_{tool_name}_{datetime.now().strftime('%H%M%S')}",
            name=tool_name,
            arguments=arguments or {},
        )
        return self.tool_registry.execute(call)

    def run_daily_briefing(self) -> str:
        """Execute daily briefing routine using only AUTO tools and send output.

        Returns:
            str: The generated briefing summary.
        """
        logger.info("Executing scheduled Daily Briefing routine.")

        # 1. Fetch current time (AUTO tool)
        time_res = self.execute_routine_tool("current_time")
        time_info = time_res.content if time_res.ok else str(datetime.now())

        # 2. Fetch system and disk info (AUTO tool)
        sys_res = self.execute_routine_tool("system_info")
        sys_info = sys_res.content if sys_res.ok else "System info unavailable"

        # 3. Find files modified today in allowed roots
        max_files = getattr(self.routines_config, "max_files_scanned", 500)
        scan_timeout = getattr(self.routines_config, "scan_timeout_seconds", 5.0)
        modified_files, limit_hit = get_files_modified_today(
            self.allowed_roots,
            max_files_scanned=max_files,
            scan_timeout_seconds=scan_timeout,
        )

        # Assemble briefing report
        lines = [
            "============================================================",
            "                   JARVIS DAILY BRIEFING                    ",
            "============================================================",
            f"Current Time:\n  {time_info}",
            "",
            f"System Status:\n  {sys_info}",
            "",
            f"Files Modified Today ({len(modified_files)} total):",
        ]

        if limit_hit:
            lines.append(f"  [Scan Notice: {limit_hit}]")

        if modified_files:
            for item in modified_files[:15]:
                lines.append(f"  - [{item['modified']}] {item['path']} ({item['size']} bytes)")
            if len(modified_files) > 15:
                lines.append(f"  ... and {len(modified_files) - 15} more file(s).")
        else:
            lines.append("  (No files modified today in allowed roots)")

        lines.append("============================================================")
        briefing_text = "\n".join(lines)

        # Deliver to output channel if configured
        if self.output_channel is not None:
            try:
                if hasattr(self.output_channel, "say"):
                    self.output_channel.say(briefing_text)
                elif hasattr(self.output_channel, "send"):
                    self.output_channel.send(briefing_text)
            except Exception as exc:
                logger.warning("Failed to deliver briefing via output channel: %s", exc)

        return briefing_text

    def start(self) -> None:
        """Start the background scheduler if enabled."""
        if self.routines_config.enabled and not self.scheduler.running:
            self.scheduler.start()
            logger.info(
                "Routines scheduler started (daily briefing at %s)",
                self.routines_config.briefing_time,
            )

    def shutdown(self) -> None:
        """Shutdown the background scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Routines scheduler stopped.")
