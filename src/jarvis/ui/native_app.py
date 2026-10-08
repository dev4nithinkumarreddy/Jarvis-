"""Native Desktop Application for J.A.R.V.I.S. Mark VII using CustomTkinter.

Zero browser dependency. Runs directly on Windows with Stark Industries
aesthetics, real-time communication log, live telemetry, and instant hotkey summoning.
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Callable

# Auto-configure TCL_LIBRARY and TK_LIBRARY paths on Windows
py_dir = Path(sys.executable).parent
tcl_dir = py_dir / "tcl"
if tcl_dir.exists():
    if "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = str(tcl_dir / "tcl8.6")
    if "TK_LIBRARY" not in os.environ:
        os.environ["TK_LIBRARY"] = str(tcl_dir / "tk8.6")

import customtkinter as ctk

from jarvis.core.channels import Confirmer, OutputChannel

logger = logging.getLogger(__name__)

# Stark Industries Color Palette
BG_DARK = "#070B14"
BG_CARD = "#0C1424"
BORDER_CYAN = "#00F0FF"
ACCENT_CYAN = "#00F0FF"
ACCENT_GOLD = "#FFB700"
ACCENT_RED = "#FF3366"
BORDER_SUBTLE = "#14314A"
BORDER_GOLD = "#4D3800"
TEXT_MAIN = "#DDF7FF"
TEXT_MUTED = "#5A738E"


class NativeAppChannel(OutputChannel, Confirmer):
    """OutputChannel and Confirmer bridge connecting J.A.R.V.I.S. to the native GUI."""

    def __init__(self, app: JarvisNativeApp) -> None:
        self.app = app
        self.input_queue: queue.Queue[str] = queue.Queue()
        self._confirm_result: bool | None = None
        self._confirm_event = threading.Event()

    def say(self, message: str) -> None:
        """Post a message from Jarvis to the GUI log."""
        self.app.post_message("JARVIS", message, is_user=False)

    def ask(self, prompt: str) -> str:
        """Prompt user through GUI and wait for input."""
        self.app.post_message("JARVIS", prompt, is_user=False)
        return self.input_queue.get()

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Present a security confirmation modal inside the native window."""
        self._confirm_result = None
        self._confirm_event.clear()
        self.app.prompt_confirmation(action_description, warning, self._on_confirm_reply)
        self._confirm_event.wait(timeout=30.0)
        return bool(self._confirm_result)

    def _on_confirm_reply(self, approved: bool) -> None:
        self._confirm_result = approved
        self._confirm_event.set()


class JarvisNativeApp(ctk.CTk):
    """Standalone native desktop application for J.A.R.V.I.S. Mark VII."""

    def __init__(
        self,
        on_user_submit: Callable[[str], None] | None = None,
        on_screen_analyze: Callable[[], None] | None = None,
        on_kill_switch: Callable[[], None] | None = None,
        user_title: str = "Sir",
        model_name: str = "Groq LLaMA 3.3 70B",
    ) -> None:
        super().__init__()

        self.on_user_submit = on_user_submit
        self.on_screen_analyze = on_screen_analyze
        self.on_kill_switch = on_kill_switch
        self.user_title = user_title
        self.model_name = model_name

        # Setup Window Properties
        self.title("J.A.R.V.I.S. Mark VII // Stark Defense Systems")
        self.geometry("520x780")
        self.minsize(440, 600)
        self.configure(fg_color=BG_DARK)

        # Set App Icon if exists
        icon_path = Path("data/icons/jarvis.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        # Channel Bridge
        self.channel = NativeAppChannel(self)

        # Build UI Elements
        self._build_header()
        self._build_telemetry()
        self._build_transcript()
        self._build_input_bar()

        # Keyboard shortcuts within app
        self.bind("<Control-Return>", lambda e: self._on_send_click())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_header(self) -> None:
        """Top Arc Reactor Header."""
        header_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12, border_width=1, border_color=BORDER_CYAN)
        header_frame.pack(fill="x", padx=14, pady=(14, 8))

        # Title & Subtitle
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=12)

        title_lbl = ctk.CTkLabel(
            title_box,
            text="J.A.R.V.I.S. MARK VII",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color=ACCENT_CYAN,
        )
        title_lbl.pack(anchor="w")

        sub_lbl = ctk.CTkLabel(
            title_box,
            text=f"STARK DEFENSE CORE // ATTENDING: {self.user_title.upper()}",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=TEXT_MUTED,
        )
        sub_lbl.pack(anchor="w")

        # Top Controls: State Pill & Kill Button
        ctrl_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        ctrl_box.pack(side="right", padx=16, pady=12)

        self.state_pill = ctk.CTkLabel(
            ctrl_box,
            text="● ONLINE",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#00FFAA",
            fg_color="#003322",
            corner_radius=8,
            padx=10,
            pady=4,
        )
        self.state_pill.pack(side="left", padx=(0, 8))

        if self.on_kill_switch:
            kill_btn = ctk.CTkButton(
                ctrl_box,
                text="OVERRIDE",
                width=70,
                height=26,
                font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
                fg_color="#330B14",
                hover_color=ACCENT_RED,
                text_color=ACCENT_RED,
                corner_radius=6,
                border_width=1,
                border_color=ACCENT_RED,
                command=self.on_kill_switch,
            )
            kill_btn.pack(side="left")

    def _build_telemetry(self) -> None:
        """Live Hardware & Neural Core Telemetry Strip."""
        telem_frame = ctk.CTkFrame(self, fg_color=BG_DARK, height=28)
        telem_frame.pack(fill="x", padx=18, pady=(0, 6))

        self.model_lbl = ctk.CTkLabel(
            telem_frame,
            text=f"BRAIN: {self.model_name}",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=TEXT_MUTED,
        )
        self.model_lbl.pack(side="left")

        self.shortcut_hint = ctk.CTkLabel(
            telem_frame,
            text="SUMMON: Ctrl+Alt+J | PTT: Hold Space",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=ACCENT_CYAN,
        )
        self.shortcut_hint.pack(side="right")

    def _build_transcript(self) -> None:
        """Scrollable message history."""
        self.transcript_box = ctk.CTkScrollableFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.transcript_box.pack(fill="both", expand=True, padx=14, pady=4)

        # Initial System Banner
        self.post_message(
            "STARK DEFENSE PROTOCOL",
            f"J.A.R.V.I.S. Mark VII standalone neural engine active.\nAwaiting your directive, {self.user_title}.",
            is_user=False,
            is_system=True,
        )

    def _build_input_bar(self) -> None:
        """Bottom Interactive Directive Input Frame."""
        input_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12, border_width=1, border_color=BORDER_CYAN)
        input_frame.pack(fill="x", padx=14, pady=(8, 14))

        # Screen Vision Button
        if self.on_screen_analyze:
            vision_btn = ctk.CTkButton(
                input_frame,
                text="👁️ SCREEN",
                width=64,
                height=38,
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                fg_color="#0D223A",
                hover_color="#184373",
                text_color=ACCENT_CYAN,
                corner_radius=8,
                command=self.on_screen_analyze,
            )
            vision_btn.pack(side="left", padx=(10, 6), pady=10)

        # Text Input
        self.entry = ctk.CTkEntry(
            input_frame,
            placeholder_text=f"Enter directive for J.A.R.V.I.S....",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color="#070E1C",
            text_color=TEXT_MAIN,
            border_color=BORDER_SUBTLE,
            corner_radius=8,
            height=38,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=6, pady=10)
        self.entry.bind("<Return>", lambda e: self._on_send_click())

        # Transmit Button
        send_btn = ctk.CTkButton(
            input_frame,
            text="TRANSMIT",
            width=80,
            height=38,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color=ACCENT_CYAN,
            hover_color="#00B8CC",
            text_color="#040A18",
            corner_radius=8,
            command=self._on_send_click,
        )
        send_btn.pack(side="right", padx=(6, 10), pady=10)

    def _on_send_click(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.post_message(self.user_title.upper(), text, is_user=True)
        self.set_state("THINKING")

        # Relay to backend
        self.channel.input_queue.put(text)
        if self.on_user_submit:
            threading.Thread(target=self.on_user_submit, args=(text,), daemon=True).start()

    def set_state(self, state: str) -> None:
        """Update the status pill text and color."""
        def _update():
            s = state.upper()
            if s == "THINKING":
                self.state_pill.configure(text="● THINKING", text_color=ACCENT_GOLD, fg_color="#332800")
            elif s == "LISTENING":
                self.state_pill.configure(text="● LISTENING", text_color=ACCENT_CYAN, fg_color="#002B33")
            elif s == "IDLE" or s == "ONLINE":
                self.state_pill.configure(text="● ONLINE", text_color="#00FFAA", fg_color="#003322")
            else:
                self.state_pill.configure(text=f"● {s}", text_color=TEXT_MAIN, fg_color="#182233")
        self.after(0, _update)

    def post_message(self, author: str, content: str, is_user: bool = False, is_system: bool = False) -> None:
        """Safely post a new bubble into the scrollable transcript."""
        def _append():
            bubble = ctk.CTkFrame(
                self.transcript_box,
                fg_color="#0F1B2F" if is_user else ("#091322" if is_system else "#0B182B"),
                corner_radius=10,
                border_width=1,
                border_color=BORDER_GOLD if is_user else BORDER_SUBTLE,
            )
            bubble.pack(fill="x", padx=6, pady=4)

            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            hdr = ctk.CTkLabel(
                bubble,
                text=f"{author}  //  {now_str}",
                font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
                text_color=ACCENT_GOLD if is_user else (ACCENT_CYAN if not is_system else TEXT_MUTED),
            )
            hdr.pack(anchor="w", padx=12, pady=(8, 2))

            msg_lbl = ctk.CTkLabel(
                bubble,
                text=content,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=TEXT_MAIN,
                justify="left",
                wraplength=440,
            )
            msg_lbl.pack(anchor="w", padx=12, pady=(2, 8))

            # Auto-scroll to bottom
            self.transcript_box._parent_canvas.yview_moveto(1.0)
            self.set_state("ONLINE")

        self.after(0, _append)

    def prompt_confirmation(self, action_description: str, warning: str | None, callback: Callable[[bool], None]) -> None:
        """Display an action approval card directly inside the transcript."""
        def _render_dialog():
            card = ctk.CTkFrame(self.transcript_box, fg_color="#261017", corner_radius=10, border_width=1, border_color=ACCENT_RED)
            card.pack(fill="x", padx=6, pady=6)

            title = ctk.CTkLabel(
                card,
                text="⚠️ SECURITY PROTOCOL CONFIRMATION REQUIRED",
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                text_color=ACCENT_RED,
            )
            title.pack(anchor="w", padx=12, pady=(8, 2))

            warning_text = f"\nWarning: {warning}" if warning else ""
            details = ctk.CTkLabel(
                card,
                text=f"Action: {action_description}{warning_text}",
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=TEXT_MAIN,
                justify="left",
            )
            details.pack(anchor="w", padx=12, pady=4)

            btn_box = ctk.CTkFrame(card, fg_color="transparent")
            btn_box.pack(anchor="e", padx=12, pady=(4, 8))

            def _reply(approved: bool):
                card.destroy()
                callback(approved)

            deny_btn = ctk.CTkButton(btn_box, text="DENY", width=60, height=26, fg_color="#330B14", text_color=ACCENT_RED, command=lambda: _reply(False))
            deny_btn.pack(side="left", padx=4)

            allow_btn = ctk.CTkButton(btn_box, text="APPROVE", width=70, height=26, fg_color="#008060", hover_color="#00AA80", command=lambda: _reply(True))
            allow_btn.pack(side="left", padx=4)

        self.after(0, _render_dialog)

    def summon(self) -> None:
        """Instantly summon, unminimize, and bring the window to top focus on desktop."""
        def _bring_to_front():
            # Play crisp Stark chime
            try:
                import winsound
                winsound.Beep(980, 150)
            except Exception:
                pass

            self.deiconify()
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
            self.entry.focus_set()

        self.after(0, _bring_to_front)

    def _on_close(self) -> None:
        """Minimize to tray instead of terminating when user clicks X."""
        self.withdraw()
