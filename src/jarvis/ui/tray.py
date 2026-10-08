"""Windows System Tray companion application for J.A.R.V.I.S. using pystray."""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import webbrowser
from typing import Any, Callable

from PIL import Image
import pystray

from jarvis.ui.autostart import (
    disable_autostart,
    enable_autostart,
    generate_arc_reactor_icon,
    is_autostart_enabled,
)

logger = logging.getLogger(__name__)


class JarvisTrayApp:
    """Manages the Windows taskbar system tray icon and menu for J.A.R.V.I.S."""

    def __init__(
        self,
        hud_url: str | None = None,
        on_open_app: Callable[[], None] | None = None,
        on_screen_analyze: Callable[[], None] | None = None,
        on_kill_switch: Callable[[], None] | None = None,
        on_shutdown: Callable[[], None] | None = None,
        icon_path: Path | str = "data/icons/jarvis.ico",
    ) -> None:
        self.hud_url = hud_url
        self.on_open_app = on_open_app
        self.on_screen_analyze = on_screen_analyze
        self.on_kill_switch = on_kill_switch
        self.on_shutdown = on_shutdown
        self.icon_path = Path(icon_path)

        if not self.icon_path.exists():
            generate_arc_reactor_icon(self.icon_path)

        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None
        self._hotkey_listener = None

    def _open_app(self, icon: Any = None, item: Any = None) -> None:
        if self.on_open_app:
            self.on_open_app()
        elif self.hud_url:
            self._open_dock(icon, item)

    def _open_hud(self, icon: Any = None, item: Any = None) -> None:
        if self.hud_url:
            webbrowser.open(self.hud_url)

    def _open_dock(self, icon: Any = None, item: Any = None) -> None:
        if not self.hud_url:
            return
        # Immediate audible Stark confirmation chime so user knows shortcut fired
        try:
            import winsound
            winsound.Beep(980, 150)
        except Exception:
            pass

        try:
            from jarvis.ui.widget import launch_widget
            launch_widget(self.hud_url)
        except Exception as e:
            logger.warning("Could not launch widget, opening browser directly: %s", e)
            webbrowser.open(self.hud_url)

    def _trigger_screen(self, icon: Any = None, item: Any = None) -> None:
        if self.on_screen_analyze:
            threading.Thread(target=self.on_screen_analyze, daemon=True).start()

    def _trigger_kill(self, icon: Any = None, item: Any = None) -> None:
        if self.on_kill_switch:
            self.on_kill_switch()

    def _toggle_autostart(self, icon: Any = None, item: Any = None) -> None:
        if is_autostart_enabled():
            disable_autostart()
        else:
            enable_autostart()

    def _exit_app(self, icon: Any = None, item: Any = None) -> None:
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        if self._icon:
            self._icon.stop()
        if self.on_shutdown:
            self.on_shutdown()

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("J.A.R.V.I.S. Mark VII", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⚡ Open J.A.R.V.I.S. (Ctrl+Alt+J)", self._open_app, default=True),
            pystray.MenuItem("👁️ Analyze Screen", self._trigger_screen),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "⚙️ Start with Windows",
                self._toggle_autostart,
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.MenuItem("🛑 Protocol Override (Kill)", self._trigger_kill),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Shutdown J.A.R.V.I.S.", self._exit_app),
        )

    def start(self, blocking: bool = False) -> None:
        """Start the system tray icon and global summon hotkey listener."""
        try:
            image = Image.open(str(self.icon_path))
        except Exception:
            # Fallback to generating fresh icon
            generate_arc_reactor_icon(self.icon_path)
            image = Image.open(str(self.icon_path))

        self._icon = pystray.Icon(
            name="JARVIS",
            icon=image,
            title="J.A.R.V.I.S. Mark VII - Online",
            menu=self._build_menu(),
        )

        # Register robust global summon hotkey (Ctrl+Alt+J)
        try:
            from jarvis.ui.hotkey import UniversalGlobalHotkey

            def on_summon():
                logger.info("Global summon shortcut triggered! Opening J.A.R.V.I.S....")
                self._open_app()

            self._hotkey_listener = UniversalGlobalHotkey(
                callback=on_summon,
                chord="ctrl+alt+j",
            )
            self._hotkey_listener.start()
            logger.info("Global summon hotkey (Ctrl+Alt+J) active.")
        except Exception as exc:
            logger.debug("Summon hotkey registration skipped: %s", exc)

        if blocking:
            self._icon.run()
        else:
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop and remove system tray icon and unhook hotkey."""
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None
        if self._icon:
            self._icon.stop()
            self._icon = None
