"""Bounded GUI session management and confirmation routing for step vs session modes."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from jarvis.core.channels import Confirmer
from jarvis.core.config import GUIConfig
from jarvis.safety.killswitch import KillSwitch, get_kill_switch

logger = logging.getLogger(__name__)

GUI_ACTION_TOOLS = frozenset({"gui_click", "gui_type", "gui_key", "gui_scroll"})


class GUISessionManager:
    """Manages bounded GUI control sessions (max actions, timeout, instant killswitch expiry)."""

    def __init__(
        self,
        approval_mode: str = "step",
        max_actions: int = 20,
        timeout_seconds: float = 300.0,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        """Initialize GUISessionManager.

        Args:
            approval_mode: 'step' (confirm each action) or 'session' (bounded session).
            max_actions: Maximum number of GUI actions permitted before re-confirmation.
            timeout_seconds: Duration after which the session automatically expires (default 5 min).
            kill_switch: Optional KillSwitch to monitor for instant session invalidation.
        """
        self.approval_mode = approval_mode
        self.max_actions = max(1, max_actions)
        self.timeout_seconds = max(0.001, timeout_seconds)
        self.kill_switch = kill_switch if kill_switch is not None else get_kill_switch()

        self._active: bool = False
        self._session_start_time: float = 0.0
        self._action_count: int = 0
        self._lock = threading.Lock()

        # Automatically invalidate session when kill switch triggers
        if self.kill_switch is not None:
            self.kill_switch.add_on_trigger(self.invalidate)

    @classmethod
    def from_config(cls, gui_config: GUIConfig, kill_switch: KillSwitch | None = None) -> GUISessionManager:
        """Create a GUISessionManager from a GUIConfig instance."""
        return cls(
            approval_mode=gui_config.approval_mode,
            max_actions=gui_config.session_max_actions,
            timeout_seconds=gui_config.session_timeout_seconds,
            kill_switch=kill_switch,
        )

    def start_session(self) -> None:
        """Start a new bounded GUI control session."""
        with self._lock:
            self._active = True
            self._session_start_time = time.monotonic()
            self._action_count = 0
            logger.info(
                "Started new GUI session (max %d actions, %.1f s timeout)",
                self.max_actions,
                self.timeout_seconds,
            )

    def invalidate(self) -> None:
        """Immediately terminate the active GUI control session."""
        with self._lock:
            if self._active:
                logger.info("GUI control session invalidated.")
            self._active = False
            self._action_count = 0
            self._session_start_time = 0.0

    def is_session_valid(self) -> bool:
        """Check if the session is currently active and within action/time limits."""
        with self._lock:
            if not self._active:
                return False

            if self.kill_switch is not None and self.kill_switch.is_set():
                self._active = False
                return False

            if self._action_count >= self.max_actions:
                logger.info("GUI session expired: reached max actions (%d)", self.max_actions)
                self._active = False
                return False

            elapsed = time.monotonic() - self._session_start_time
            if elapsed >= self.timeout_seconds:
                logger.info("GUI session expired: exceeded timeout (%.1f s)", self.timeout_seconds)
                self._active = False
                return False

            return True

    def record_action(self) -> int:
        """Increment action count and return the new action count."""
        with self._lock:
            self._action_count += 1
            return self._action_count

    def get_remaining_stats(self) -> tuple[int, float]:
        """Return (actions_remaining, seconds_remaining)."""
        with self._lock:
            if not self._active:
                return 0, 0.0
            actions_left = max(0, self.max_actions - self._action_count)
            elapsed = time.monotonic() - self._session_start_time
            seconds_left = max(0.0, self.timeout_seconds - elapsed)
            return actions_left, seconds_left

    def render_banner(self) -> str:
        """Render a visible terminal banner indicating active GUI session status."""
        actions_left, seconds_left = self.get_remaining_stats()
        mins_left = seconds_left / 60.0
        return (
            "\n"
            "╔═══════════════════════════════════════════════════════════════════════════╗\n"
            f"║ [!] ACTIVE GUI SESSION: Action {self._action_count + 1}/{self.max_actions} ({actions_left} remaining) | Time: {mins_left:.1f}m left   ║\n"
            "║ [!] FAIL-SAFE: Slam mouse cursor to any screen corner to abort!            ║\n"
            "║ [!] KILL-SWITCH: Press Ctrl+Alt+Shift+K to instantly kill the agent!      ║\n"
            "╚═══════════════════════════════════════════════════════════════════════════╝\n"
        )

    def format_session_prompt(self, action_desc: str) -> str:
        """Create a prompt asking the user to approve a bounded GUI control session."""
        mins = self.timeout_seconds / 60.0
        return (
            f"Start bounded GUI control session (max {self.max_actions} actions, {mins:.0f} mins) "
            f"to execute: {action_desc}"
        )


class GUISessionConfirmer(Confirmer):
    """Wraps a Confirmer to provide bounded session approval for GUI actions."""

    def __init__(
        self,
        base_confirmer: Confirmer,
        session_manager: GUISessionManager,
    ) -> None:
        """Initialize GUISessionConfirmer.

        Args:
            base_confirmer: The underlying Confirmer (e.g. CLIConfirmer or VoiceConfirmer).
            session_manager: The GUISessionManager tracking session status and limits.
        """
        self.base_confirmer = base_confirmer
        self.session_manager = session_manager

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Route confirmation request through session logic or base confirmer."""
        # Non-session mode or non-GUI actions always require explicit prompt
        if self.session_manager.approval_mode != "session":
            return self.base_confirmer.confirm(action_description, warning=warning)

        # Determine if this action description corresponds to a GUI action
        is_gui_action = any(
            action_description.lower().startswith(prefix)
            for prefix in ("click", "gui_click", "type", "gui_type", "press key", "gui_key", "scroll", "gui_scroll")
        )

        if not is_gui_action:
            # Non-GUI action (e.g. file writing, app launch) must never be bypassed by GUI session
            return self.base_confirmer.confirm(action_description, warning=warning)

        # In session mode for a GUI action:
        if self.session_manager.is_session_valid():
            # Session is active and within bounds: print persistent banner and proceed
            print(self.session_manager.render_banner())
            self.session_manager.record_action()
            return True

        # Session is not active or expired: prompt user to start/renew bounded session
        prompt_text = self.session_manager.format_session_prompt(action_description)
        approved = self.base_confirmer.confirm(prompt_text, warning=warning)
        if approved:
            self.session_manager.start_session()
            print(self.session_manager.render_banner())
            self.session_manager.record_action()
            return True

        return False
