"""Frameless Floating Arc Reactor Desktop Widget for J.A.R.V.I.S. Mark VII.

Zero browser reliance. Floats directly on the Windows desktop as an authentic,
sleek, photorealistic glowing Stark Arc Reactor Mark VII that pulses, rotates,
and expands into a high-tech holographic directive console when clicked or summoned.
"""

from __future__ import annotations

import ctypes
import datetime
import logging
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import tkinter as tk
from typing import Any, Callable

# Auto-configure TCL and TK paths on Windows
py_dir = Path(sys.executable).parent
tcl_dir = py_dir / "tcl"
if tcl_dir.exists():
    if "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = str(tcl_dir / "tcl8.6")
    if "TK_LIBRARY" not in os.environ:
        os.environ["TK_LIBRARY"] = str(tcl_dir / "tk8.6")

from jarvis.core.channels import Confirmer, OutputChannel

logger = logging.getLogger(__name__)

# Professional Stark Industries Mark VII Color Palette
BG_TRANSPARENT = "#ff00ff"
BG_PANEL = "#030A14"
BG_PANEL_ALT = "#051120"
BG_INPUT = "#02060D"
BORDER_CYAN = "#00F0FF"
BORDER_CYAN_DIM = "#007A99"
BORDER_SUBTLE = "#0B2238"
ACCENT_CYAN = "#00F0FF"
ACCENT_ICE = "#38BDF8"
ACCENT_GOLD = "#FFB700"
ACCENT_RED = "#FF3366"
ACCENT_GREEN = "#00FFAA"
TEXT_MAIN = "#E2F7FF"
TEXT_MUTED = "#557799"
TEXT_BRIGHT = "#FFFFFF"


class FloatingDockChannel(OutputChannel, Confirmer):
    """OutputChannel and Confirmer bridge connecting J.A.R.V.I.S. to the floating widget."""

    def __init__(self, widget: FloatingArcReactorWidget) -> None:
        self.widget = widget
        self.input_queue: queue.Queue[str] = queue.Queue()
        self._confirm_result: bool | None = None
        self._confirm_event = threading.Event()

    def say(self, message: str) -> None:
        """Post a response to the floating console."""
        self.widget.post_response(message)

    def ask(self, prompt: str) -> str:
        """Prompt user via floating console and wait for input."""
        self.widget.post_response(prompt)
        self.widget.expand_console()
        return self.input_queue.get()

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Prompt security confirmation inside the floating console."""
        self._confirm_result = None
        self._confirm_event.clear()
        self.widget.prompt_confirmation(action_description, warning, self._on_confirm_reply)
        self._confirm_event.wait(timeout=30.0)
        return bool(self._confirm_result)

    def _on_confirm_reply(self, approved: bool) -> None:
        self._confirm_result = approved
        self._confirm_event.set()


class FloatingArcReactorWidget(tk.Tk):
    """Authentic Stark Mark VII Frameless Floating Arc Reactor Desktop Widget."""

    def __init__(
        self,
        on_user_submit: Callable[[str], None] | None = None,
        on_screen_analyze: Callable[[], None] | None = None,
        on_voice_toggle: Callable[[], None] | None = None,
        on_kill_switch: Callable[[], None] | None = None,
        on_speech_stop: Callable[[], None] | None = None,
        user_title: str = "Sir",
        model_name: str = "gpt-oss:120b",
        on_model_change: Callable[[str], None] | None = None,
        size: int = 110,
    ) -> None:
        super().__init__()

        self.on_user_submit = on_user_submit
        self.on_screen_analyze = on_screen_analyze
        self.on_voice_toggle = on_voice_toggle
        self.on_kill_switch = on_kill_switch
        self.on_speech_stop = on_speech_stop
        self.on_model_change = on_model_change
        self.user_title = user_title
        self.model_name = model_name
        self.size = size
        self.is_expanded = False
        self._confirm_active = False

        # Configure Frameless Window with True Color Transparency
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-transparentcolor", BG_TRANSPARENT)
        self.configure(bg=BG_TRANSPARENT)

        # Default Position: Bottom-Right above Windows taskbar
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.dock_x = max(20, sw - self.size - 40)
        self.dock_y = max(20, sh - self.size - 80)
        self.geometry(f"{self.size}x{self.size}+{self.dock_x}+{self.dock_y}")

        # Dragging State
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._has_dragged = False

        # Animation State
        self.angle_offset = 0.0
        self.pulse_phase = 0.0
        self.state = "ONLINE"
        self._animating = True

        # Global Hotkey within Dock: Escape halts speech immediately
        self.bind("<Escape>", lambda e: self.stop_speech())

        # Channel Bridge
        self.channel = FloatingDockChannel(self)

        # Build UI Structure
        self._build_ui()
        self._animate()
        self.after(500, self._update_metrics)

    def _build_ui(self) -> None:
        """Construct the container holding the circular reactor and expandable Stark HUD console."""
        self.container = tk.Frame(self, bg=BG_TRANSPARENT)
        self.container.pack(fill="both", expand=True)

        # 1. Arc Reactor Canvas
        self.canvas = tk.Canvas(
            self.container,
            width=self.size,
            height=self.size,
            bg=BG_TRANSPARENT,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(side="left")

        # Bind Drag and Click Interactions
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._show_context_menu)

        # 2. Holographic Flyout Console (Initially hidden)
        self.flyout = tk.Frame(
            self.container,
            bg=BG_PANEL,
            highlightbackground=BORDER_CYAN,
            highlightcolor=BORDER_CYAN,
            highlightthickness=1,
            padx=12,
            pady=10,
        )

        # --- FLYOUT HEADER (Stark Industries Tactical Header) ---
        header = tk.Frame(self.flyout, bg=BG_PANEL)
        header.pack(side="top", fill="x", pady=(0, 6))

        title_lbl = tk.Label(
            header,
            text=f"◈ J.A.R.V.I.S. // {self.user_title.upper()}",
            font=("Segoe UI", 9, "bold"),
            fg=ACCENT_CYAN,
            bg=BG_PANEL,
        )
        title_lbl.pack(side="left")

        self.state_lbl = tk.Label(
            header,
            text="● ONLINE",
            font=("Consolas", 8, "bold"),
            fg=ACCENT_GREEN,
            bg="#002418",
            padx=6,
            pady=2,
            relief="flat",
        )
        self.state_lbl.pack(side="left", padx=6)

        # Interactive Neural Model Chip
        display_m = self.model_name.split("/")[-1].split(":")[0].upper()
        self.model_chip = tk.Button(
            header,
            text=f"🧠 {display_m} ▾",
            font=("Segoe UI", 8, "bold"),
            fg=ACCENT_ICE,
            bg="#061A2E",
            activeforeground="#FFFFFF",
            activebackground="#0B3056",
            relief="flat",
            bd=0,
            padx=7,
            pady=2,
            cursor="hand2",
            command=self.show_model_switcher_menu,
        )
        self.model_chip.pack(side="left", padx=(0, 6))

        # Close / Collapse Flyout
        collapse_btn = tk.Button(
            header,
            text="✕",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_MUTED,
            bg=BG_PANEL,
            activeforeground=ACCENT_RED,
            activebackground=BG_PANEL,
            relief="flat",
            bd=0,
            padx=5,
            pady=2,
            cursor="hand2",
            command=self.collapse_console,
        )
        collapse_btn.pack(side="right")

        # Clear Response Log Button
        self.clear_btn = tk.Button(
            header,
            text="⟲ CLEAR",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_MUTED,
            bg="#06182B",
            activeforeground=TEXT_BRIGHT,
            activebackground="#0E3359",
            relief="flat",
            bd=0,
            padx=7,
            pady=2,
            cursor="hand2",
            command=self.clear_console,
        )
        self.clear_btn.pack(side="right", padx=(0, 4))

        # Immediate Speech Stop / Silence Button
        self.stop_btn = tk.Button(
            header,
            text="■ STOP",
            font=("Segoe UI", 8, "bold"),
            fg="#FF7590",
            bg="#2A0B13",
            activeforeground="#FFFFFF",
            activebackground="#4A1220",
            relief="flat",
            bd=0,
            padx=7,
            pady=2,
            cursor="hand2",
            command=self.stop_speech,
        )
        self.stop_btn.pack(side="right", padx=(0, 4))

        # Quick Protocols Dropdown Button
        self.protocols_btn = tk.Button(
            header,
            text="⚡ PROTOCOLS",
            font=("Segoe UI", 8, "bold"),
            fg=ACCENT_CYAN,
            bg="#072238",
            activeforeground="#FFFFFF",
            activebackground="#0E3E66",
            relief="flat",
            bd=0,
            padx=7,
            pady=2,
            cursor="hand2",
            command=self.show_protocols_menu,
        )
        self.protocols_btn.pack(side="right", padx=(0, 4))

        # --- TACTICAL TELEMETRY STRIP (Stark HUD Gauges) ---
        self.metrics_bar = tk.Frame(
            self.flyout,
            bg=BG_PANEL_ALT,
            padx=10,
            pady=4,
            highlightbackground=BORDER_SUBTLE,
            highlightcolor=BORDER_CYAN_DIM,
            highlightthickness=1,
        )
        self.metrics_bar.pack(side="top", fill="x", pady=(0, 6))

        self.metrics_cpu_lbl = tk.Label(
            self.metrics_bar,
            text="CPU: 0%",
            font=("Consolas", 8, "bold"),
            fg=ACCENT_CYAN,
            bg=BG_PANEL_ALT,
        )
        self.metrics_cpu_lbl.pack(side="left", padx=(0, 12))

        self.metrics_ram_lbl = tk.Label(
            self.metrics_bar,
            text="RAM: 0%",
            font=("Consolas", 8, "bold"),
            fg=ACCENT_ICE,
            bg=BG_PANEL_ALT,
        )
        self.metrics_ram_lbl.pack(side="left", padx=(0, 12))

        core_name = self.model_name.split("/")[-1] if "/" in self.model_name else self.model_name
        if len(core_name) > 22:
            core_name = core_name[:20] + ".."
        self.metrics_model_lbl = tk.Label(
            self.metrics_bar,
            text=f"CORE: {core_name.upper()}",
            font=("Consolas", 8, "bold"),
            fg=ACCENT_GOLD,
            bg=BG_PANEL_ALT,
        )
        self.metrics_model_lbl.pack(side="left", padx=(0, 12))

        self.metrics_audio_lbl = tk.Label(
            self.metrics_bar,
            text="VOICE: READY",
            font=("Consolas", 8, "bold"),
            fg=ACCENT_GREEN,
            bg=BG_PANEL_ALT,
        )
        self.metrics_audio_lbl.pack(side="right")

        # --- ANCHOR BOTTOM CONTROLS (Always visible, never clipped) ---
        self.hint_lbl = tk.Label(
            self.flyout,
            text="💡 Directives: Click 🎙️ MIC or hold [Spacebar] | Type directive & hit Enter",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=BG_PANEL,
            anchor="w",
        )
        self.hint_lbl.pack(side="bottom", fill="x", pady=(3, 0))

        # Bottom Directive Input Bar
        self.input_bar = tk.Frame(self.flyout, bg=BG_PANEL)
        self.input_bar.pack(side="bottom", fill="x", pady=(0, 5))

        # High-Tech Quick Action Chips Row
        self.chips_bar = tk.Frame(self.flyout, bg=BG_PANEL)
        self.chips_bar.pack(side="bottom", fill="x", pady=(0, 6))

        def _make_chip(label: str, directive: str, icon: str = ""):
            btn = tk.Button(
                self.chips_bar,
                text=f"{icon} {label}".strip(),
                font=("Segoe UI", 7, "bold"),
                fg=ACCENT_CYAN,
                bg="#061D32",
                activeforeground="#FFFFFF",
                activebackground="#0B3E6B",
                relief="flat",
                bd=0,
                padx=8,
                pady=3,
                cursor="hand2",
                command=lambda: self._dispatch_protocol(directive),
            )
            btn.pack(side="left", padx=(0, 6))
            return btn

        _make_chip("STATUS", "Give me a quick system diagnostic report.", "⚡")
        _make_chip("WEATHER", "What is the current weather outside?", "🌦️")
        _make_chip("HEADLINES", "Check recent tech headlines.", "📰")
        _make_chip("MEDIA", "Toggle media play/pause.", "⏯️")
        _make_chip("DESKTOP", "Show clean desktop.", "🖥️")
        _make_chip("5M TIMER", "Set a timer for 5 minutes.", "⏱️")

        if self.on_screen_analyze:
            self.vision_btn = tk.Button(
                self.input_bar,
                text="👁️ SCREEN",
                font=("Segoe UI", 8, "bold"),
                fg=ACCENT_CYAN,
                bg="#061E33",
                activebackground="#0C3E69",
                activeforeground="#FFFFFF",
                relief="flat",
                bd=0,
                padx=9,
                pady=5,
                cursor="hand2",
                command=self.on_screen_analyze,
            )
            self.vision_btn.pack(side="left", padx=(0, 6))

        def _trigger_mic():
            if self.on_voice_toggle:
                self.on_voice_toggle()
            else:
                self.post_response(f"Voice engine is warming up, {self.user_title}. Hold [Spacebar] or click 🎙️ MIC in a moment.")

        self.mic_btn = tk.Button(
            self.input_bar,
            text="🎙️ MIC",
            font=("Segoe UI", 8, "bold"),
            fg=ACCENT_CYAN,
            bg="#061E33",
            activebackground="#0C3E69",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=9,
            pady=5,
            cursor="hand2",
            command=_trigger_mic,
        )
        self.mic_btn.pack(side="left", padx=(0, 6))

        self.entry = tk.Entry(
            self.input_bar,
            font=("Segoe UI", 10),
            fg=TEXT_MAIN,
            bg=BG_INPUT,
            insertbackground=ACCENT_CYAN,
            relief="flat",
            bd=1,
            highlightthickness=1,
            highlightbackground=BORDER_SUBTLE,
            highlightcolor=BORDER_CYAN,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self._on_transmit())

        transmit_btn = tk.Button(
            self.input_bar,
            text="TRANSMIT ❯",
            font=("Segoe UI", 8, "bold"),
            fg="#020812",
            bg=ACCENT_CYAN,
            activebackground="#38BDF8",
            activeforeground="#020812",
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            command=self._on_transmit,
        )
        transmit_btn.pack(side="right")

        # Security Confirmation Slot
        self.confirm_slot = tk.Frame(self.flyout, bg=BG_PANEL)
        self.confirm_slot.pack(side="bottom", fill="x", pady=(0, 4))

        # --- MIDDLE: Scrollable Holographic Viewport ---
        self.scroll_frame = tk.Frame(
            self.flyout,
            bg=BG_INPUT,
            highlightbackground=BORDER_SUBTLE,
            highlightcolor=BORDER_CYAN,
            highlightthickness=1,
        )
        self.scroll_frame.pack(side="top", fill="both", expand=True, pady=(0, 6))

        self.response_box = tk.Text(
            self.scroll_frame,
            font=("Segoe UI", 9),
            fg=TEXT_MAIN,
            bg=BG_INPUT,
            wrap="word",
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            height=6,
            cursor="arrow",
            selectbackground="#0B3056",
            selectforeground="#FFFFFF",
        )
        self.scrollbar = tk.Scrollbar(
            self.scroll_frame,
            command=self.response_box.yview,
            width=10,
            bg="#06182B",
            troughcolor=BG_INPUT,
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.response_box.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.response_box.pack(side="left", fill="both", expand=True)

        # Stark HUD High-Tech Typography Tags
        self.response_box.tag_configure("title", font=("Segoe UI", 10, "bold"), foreground=ACCENT_CYAN, spacing3=4)
        self.response_box.tag_configure("bold", font=("Segoe UI", 9, "bold"), foreground=TEXT_BRIGHT)
        self.response_box.tag_configure("bullet", font=("Segoe UI", 9, "bold"), foreground=ACCENT_GOLD)
        self.response_box.tag_configure("key", font=("Segoe UI", 9, "bold"), foreground=ACCENT_CYAN)
        self.response_box.tag_configure("value", font=("Segoe UI", 9), foreground=TEXT_MAIN)
        self.response_box.tag_configure("normal", font=("Segoe UI", 9), foreground=TEXT_MAIN)

        initial_greeting = f"At your service, {self.user_title}.\nArc Reactor online. Click core or enter directive below."
        self.response_text = tk.StringVar(value=initial_greeting)
        self._render_styled_response(initial_greeting)

    def _on_press(self, event: tk.Event) -> None:
        self._drag_start_x = event.x
        self._drag_start_y = event.y
        self._has_dragged = False

    def _on_drag(self, event: tk.Event) -> None:
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self._has_dragged = True
            new_x = self.winfo_x() + dx
            new_y = self.winfo_y() + dy
            self.dock_x = new_x
            self.dock_y = new_y
            self.geometry(f"+{new_x}+{new_y}")

    def _on_release(self, event: tk.Event) -> None:
        if not self._has_dragged:
            self.toggle_console()

    def toggle_console(self) -> None:
        """Toggle between pure floating Arc Reactor and expanded console."""
        if self.is_expanded:
            self.collapse_console()
        else:
            self.expand_console()

    def expand_console(self) -> None:
        """Expand the holographic directive console with ample width for all controls."""
        if not self.is_expanded:
            self.is_expanded = True
            self.flyout.pack(side="left", fill="both", expand=True, padx=(6, 0))
        w = self.size + 620
        h = 440 if getattr(self, "_confirm_active", False) else 370
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        cur_x = self.winfo_x() if self.winfo_x() > 0 else self.dock_x
        cur_y = self.winfo_y() if self.winfo_y() > 0 else self.dock_y
        if cur_x + w > sw - 20:
            cur_x = max(20, sw - w - 20)
        if cur_y + h > sh - 60:
            cur_y = max(20, sh - h - 60)
        self.dock_x = cur_x
        self.dock_y = cur_y
        self.geometry(f"{w}x{h}+{cur_x}+{cur_y}")
        self.entry.focus_set()

    def collapse_console(self) -> None:
        """Collapse back to just the floating circular Arc Reactor."""
        if self.is_expanded:
            self.is_expanded = False
            self.flyout.pack_forget()
            cur_x = self.winfo_x() if self.winfo_x() > 0 else self.dock_x
            cur_y = self.winfo_y() if self.winfo_y() > 0 else self.dock_y
            self.dock_x = cur_x
            self.dock_y = cur_y
            self.geometry(f"{self.size}x{self.size}+{cur_x}+{cur_y}")

    def stop_speech(self) -> None:
        """Immediately interrupt ongoing speech synthesis/playback."""
        if self.on_speech_stop:
            try:
                self.on_speech_stop()
            except Exception as exc:
                logger.debug("Speech stop callback error: %s", exc)
        try:
            from jarvis.voice.triggers import play_ptt_tone
            play_ptt_tone("stop_speech")
        except Exception:
            pass
        self.set_state("ONLINE")
        if hasattr(self, "metrics_audio_lbl"):
            self.metrics_audio_lbl.config(text="VOICE: IDLE")

    def clear_console(self) -> None:
        """Clear the holographic response viewport."""
        greeting = f"Console cleared, {self.user_title}.\nArc Reactor online. Ready for your directive."
        self.response_text.set(greeting)
        self._render_styled_response(greeting)

    def show_model_switcher_menu(self, event: tk.Event | None = None) -> None:
        """Display Stark neural model switcher popup menu."""
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            activebackground="#0B3056",
            activeforeground=ACCENT_CYAN,
        )

        models = [
            ("🧠 GPT-OSS 120B (Flagship)", "gpt-oss:120b"),
            ("⚡ DeepSeek V4.1 Flash (Turbo)", "deepseek-v4.1-flash"),
            ("🚀 GLM 5.3 Flash (High-Speed)", "glm-5.3-flash"),
            ("💻 Kimi K2.7 Code (Developer)", "kimi-k2.7-code"),
            ("🛡️ Mistral Large 4 (Reasoning)", "mistral-large-4"),
            ("🔬 Nemotron-3 Super (NVIDIA)", "nemotron-3-super"),
            ("🏠 Local Ollama Daemon (Offline)", "llama3.2:latest"),
        ]

        def _select_model(name: str):
            self.model_name = name
            display_m = name.split("/")[-1].split(":")[0].upper()
            self.model_chip.config(text=f"🧠 {display_m} ▾")
            self.post_response(f"Neural core recalibrated: Model switched to {name}, {self.user_title}.")
            if self.on_model_change:
                self.on_model_change(name)

        for label, model_id in models:
            is_active = (self.model_name == model_id)
            prefix = "✓ " if is_active else "  "
            menu.add_command(
                label=f"{prefix}{label}",
                command=lambda m=model_id: _select_model(m),
            )

        try:
            x = self.model_chip.winfo_rootx()
            y = self.model_chip.winfo_rooty() + self.model_chip.winfo_height() + 2
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def show_protocols_menu(self) -> None:
        """Display Stark Quick Protocols popup menu."""
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=BG_PANEL,
            fg=TEXT_MAIN,
            activebackground="#0B3056",
            activeforeground=ACCENT_CYAN,
        )
        menu.add_command(
            label="📊 Diagnostic Status Briefing",
            command=lambda: self._dispatch_protocol("Run a comprehensive system diagnostic status report on CPU, memory, storage, and battery."),
        )
        menu.add_command(
            label="🌦️ Current Weather Briefing",
            command=lambda: self._dispatch_protocol("Provide the current weather conditions and temperature for my location."),
        )
        menu.add_command(
            label="📰 Latest Tech Headlines",
            command=lambda: self._dispatch_protocol("Check the latest technology headlines and give me a quick briefing."),
        )
        if self.on_screen_analyze:
            menu.add_command(label="👁️ Analyze Active Screen", command=self.on_screen_analyze)
        menu.add_separator()
        menu.add_command(
            label="⏯️ Play / Pause Media",
            command=lambda: self._dispatch_protocol("Toggle media play/pause."),
        )
        menu.add_command(
            label="🖥️ Toggle Clean Desktop",
            command=lambda: self._dispatch_protocol("Show the clean Windows desktop."),
        )
        menu.add_command(
            label="🔒 Lock Workstation",
            command=lambda: self._dispatch_protocol("Lock my workstation now."),
        )
        menu.add_command(label="■ Silence Voice (Esc)", command=self.stop_speech)
        menu.add_command(label="⟲ Clear Viewport", command=self.clear_console)
        try:
            x = self.protocols_btn.winfo_rootx()
            y = self.protocols_btn.winfo_rooty() + self.protocols_btn.winfo_height() + 2
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _dispatch_protocol(self, directive: str) -> None:
        """Send a quick protocol directive to the orchestrator."""
        self.entry.delete(0, "end")
        self.set_state("THINKING")
        self.post_response(f"Initiating protocol: \"{directive}\"...")
        self.channel.input_queue.put(directive)
        if self.on_user_submit:
            threading.Thread(target=self.on_user_submit, args=(directive,), daemon=True).start()

    def _on_transmit(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.set_state("THINKING")
        self.post_response(f"Processing directive: \"{text}\"...")
        self.channel.input_queue.put(text)
        if self.on_user_submit:
            threading.Thread(target=self.on_user_submit, args=(text,), daemon=True).start()

    def _insert_inline_tokens(self, text_segment: str) -> None:
        """Parse inline markdown **bold** tokens into native styled Tkinter text."""
        parts = re.split(r"(\*\*.*?\*\*)", text_segment)
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) >= 4:
                self.response_box.insert("end", part[2:-2], "bold")
            else:
                self.response_box.insert("end", part, "normal")

    def _render_styled_response(self, text: str) -> None:
        """Render text into the response_box with clean HUD formatting, stripping raw markdown artifacts."""
        if not hasattr(self, "response_box"):
            return

        self.response_box.configure(state="normal")
        self.response_box.delete("1.0", "end")

        lines = text.splitlines()
        for i, line in enumerate(lines):
            raw_line = line.strip()
            if not raw_line:
                self.response_box.insert("end", "\n")
                continue

            # 1. Skip table separator lines like |---|---|
            if re.match(r"^\|?[\s\-:]+(\|[\s\-:]+)+\|?$", raw_line):
                continue

            # 2. Markdown Headings (e.g., #, ##, ###)
            heading_match = re.match(r"^#{1,6}\s*(.*)", raw_line)
            if heading_match:
                title_text = heading_match.group(1).replace("**", "")
                self.response_box.insert("end", f"▶ {title_text.upper()}\n", "title")
                continue

            # 3. Markdown Table rows: | Col 1 | Col 2 | ...
            if raw_line.startswith("|") and raw_line.endswith("|"):
                cells = [c.strip().replace("**", "") for c in raw_line.strip("|").split("|")]
                cells = [c for c in cells if c]
                if len(cells) >= 2:
                    self.response_box.insert("end", "  ◆ ", "bullet")
                    self.response_box.insert("end", f"{cells[0]}: ", "key")
                    self.response_box.insert("end", f"{' | '.join(cells[1:])}\n", "value")
                elif len(cells) == 1:
                    self.response_box.insert("end", "  ◆ ", "bullet")
                    self.response_box.insert("end", f"{cells[0]}\n", "normal")
                continue

            # 4. Bullet lists (*, -, +)
            bullet_match = re.match(r"^[\*\-\+]\s+(.*)", raw_line)
            if bullet_match:
                self.response_box.insert("end", "  ◆ ", "bullet")
                self._insert_inline_tokens(bullet_match.group(1))
                self.response_box.insert("end", "\n")
                continue

            # 5. Numbered lists (1., 2.)
            num_match = re.match(r"^(\d+[\.\)])\s+(.*)", raw_line)
            if num_match:
                self.response_box.insert("end", f"  {num_match.group(1)} ", "bullet")
                self._insert_inline_tokens(num_match.group(2))
                self.response_box.insert("end", "\n")
                continue

            # 6. Standard paragraph line with potential inline bold
            self._insert_inline_tokens(line)
            self.response_box.insert("end", "\n")

        self.response_box.see("end")
        self.response_box.configure(state="disabled")

    def post_response(self, text: str) -> None:
        """Update speech / response text in the flyout with clean HUD rendering and auto-scroll."""
        def _update():
            self.response_text.set(text)
            self._render_styled_response(text)
            self.expand_console()
            self.set_state("ONLINE")
        self.after(0, _update)

    def set_state(self, state: str) -> None:
        """Update animation and status colors."""
        def _update():
            s = state.upper()
            self.state = s
            if s == "THINKING":
                self.state_lbl.config(text="● THINKING", fg=ACCENT_GOLD, bg="#332400")
            elif s == "LISTENING":
                self.state_lbl.config(text="● LISTENING", fg=ACCENT_CYAN, bg="#002B33")
            else:
                self.state_lbl.config(text="● ONLINE", fg=ACCENT_GREEN, bg="#002418")
        self.after(0, _update)

    def prompt_confirmation(self, action_description: str, warning: str | None, callback: Callable[[bool], None]) -> None:
        """Display an inline confirmation card inside the floating console with high-visibility buttons."""
        def _render():
            self._confirm_active = True
            self.expand_console()
            warn_text = f"\n⚠️ Warning: {warning}" if warning else ""
            msg = f"⚠️ SECURITY CONFIRMATION REQUIRED:\nAction: {action_description}{warn_text}"
            self.response_text.set(msg)

            for w in self.confirm_slot.winfo_children():
                w.destroy()

            card = tk.Frame(
                self.confirm_slot,
                bg="#171202",
                highlightbackground=ACCENT_GOLD,
                highlightcolor=ACCENT_GOLD,
                highlightthickness=1,
                padx=10,
                pady=8,
            )
            card.pack(fill="x")

            lbl = tk.Label(
                card,
                text=msg,
                font=("Segoe UI", 8, "bold"),
                fg="#FFD54F",
                bg="#171202",
                justify="left",
                wraplength=520,
                anchor="w",
            )
            lbl.pack(fill="x", pady=(0, 8))

            btn_box = tk.Frame(card, bg="#171202")
            btn_box.pack(fill="x")

            def _reply(approved: bool):
                self._confirm_active = False
                card.destroy()
                self.expand_console()
                callback(approved)

            deny_btn = tk.Button(
                btn_box,
                text="✕  DENY ACTION",
                font=("Segoe UI", 9, "bold"),
                bg="#4A0E1A",
                fg="#FF6B8B",
                activebackground="#661424",
                activeforeground="#FFFFFF",
                relief="flat",
                bd=0,
                padx=14,
                pady=6,
                cursor="hand2",
                command=lambda: _reply(False),
            )
            deny_btn.pack(side="left", padx=(0, 10))

            approve_btn = tk.Button(
                btn_box,
                text="✓  APPROVE ACTION",
                font=("Segoe UI", 9, "bold"),
                bg="#006644",
                fg="#FFFFFF",
                activebackground="#00885B",
                activeforeground="#FFFFFF",
                relief="flat",
                bd=0,
                padx=14,
                pady=6,
                cursor="hand2",
                command=lambda: _reply(True),
            )
            approve_btn.pack(side="left")

        self.after(0, _render)

    def _update_metrics(self) -> None:
        """Periodic background refresh of live CPU and RAM metrics with visual indicator."""
        if not self._animating:
            return
        try:
            import psutil
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            if hasattr(self, "metrics_cpu_lbl"):
                self.metrics_cpu_lbl.config(text=f"CPU: {cpu:.0f}%")
            if hasattr(self, "metrics_ram_lbl"):
                self.metrics_ram_lbl.config(text=f"RAM: {ram:.0f}%")
        except Exception:
            pass
        self.after(2500, self._update_metrics)

    def summon(self) -> None:
        """Instantly bring floating Arc Reactor to front and expand console."""
        def _elevate():
            try:
                import winsound
                winsound.Beep(980, 150)
            except Exception:
                pass

            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            try:
                hwnd = self.winfo_id()
                u32 = ctypes.windll.user32
                k32 = ctypes.windll.kernel32
                u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)
                fg_hwnd = u32.GetForegroundWindow()
                fg_thread = u32.GetWindowThreadProcessId(fg_hwnd, None)
                cur_thread = k32.GetCurrentThreadId()
                if fg_thread and fg_thread != cur_thread:
                    u32.AttachThreadInput(cur_thread, fg_thread, True)
                    u32.SetForegroundWindow(hwnd)
                    u32.BringWindowToTop(hwnd)
                    u32.AttachThreadInput(cur_thread, fg_thread, False)
                else:
                    u32.SetForegroundWindow(hwnd)
                    u32.BringWindowToTop(hwnd)
            except Exception:
                pass
            self.expand_console()
            self.focus_force()
            self.entry.focus_set()

        self.after(0, _elevate)

    def _show_context_menu(self, event: tk.Event) -> None:
        """Context menu on right-click."""
        menu = tk.Menu(self, tearoff=0, bg=BG_PANEL, fg=TEXT_MAIN, activebackground="#0B3056", activeforeground=ACCENT_CYAN)
        menu.add_command(label="⚡ Summon & Expand Console", command=self.summon)
        menu.add_command(label="💬 Toggle Console Bar", command=self.toggle_console)
        if self.on_screen_analyze:
            menu.add_command(label="👁️ Analyze Screen", command=self.on_screen_analyze)
        if self.on_kill_switch:
            menu.add_separator()
            menu.add_command(label="🛑 Emergency Override", command=self.on_kill_switch)
        menu.add_separator()
        menu.add_command(label="❌ Hide to Tray", command=self.withdraw)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _draw(self) -> None:
        """Procedurally render the authentic Stark Industries Arc Reactor Mark VII."""
        self.canvas.delete("all")
        cx = self.size / 2.0
        cy = self.size / 2.0
        r_outer = (self.size / 2.0) - 3.0
        r_ticks = r_outer * 0.90
        r_coils = r_outer * 0.74
        r_inner = r_outer * 0.54
        r_gyro = r_outer * 0.40
        r_core = r_outer * 0.27

        # State-Reactive Lighting Profiles
        if self.state == "THINKING":
            halo_color = "#3A2800"
            aura_color = "#8A5C00"
            ring_color = ACCENT_GOLD
            core_color = "#FFF8CC"
            coil_color = "#FF9100"
            gyro_color = "#FFAB00"
            tick_color = "#B38600"
        elif self.state == "LISTENING":
            halo_color = "#002B3D"
            aura_color = "#006B8F"
            ring_color = "#00FFFF"
            core_color = "#FFFFFF"
            coil_color = "#00E5FF"
            gyro_color = "#80D8FF"
            tick_color = "#00B4D8"
        else:  # ONLINE (Idle / Standby)
            halo_color = "#021A2E"
            aura_color = "#06466E"
            ring_color = ACCENT_CYAN
            core_color = "#FFFFFF"
            coil_color = ACCENT_GOLD
            gyro_color = "#00D4FF"
            tick_color = "#0B4B75"

        # 1. Holographic HUD Reticle & Cardinal Ticks
        for deg in (0, 90, 180, 270):
            rad = math.radians(deg)
            x1 = cx + math.cos(rad) * (r_outer - 1)
            y1 = cy + math.sin(rad) * (r_outer - 1)
            x2 = cx + math.cos(rad) * (r_outer + 2)
            y2 = cy + math.sin(rad) * (r_outer + 2)
            self.canvas.create_line(x1, y1, x2, y2, fill=ring_color, width=2.0)

        # 2. Outer Multi-Stage Radiant Atmospheric Halo Glow
        self.canvas.create_oval(
            cx - r_outer - 2, cy - r_outer - 2, cx + r_outer + 2, cy + r_outer + 2,
            fill="", outline=halo_color, width=3,
        )
        self.canvas.create_oval(
            cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
            fill="#020712", outline=ring_color, width=2.0,
        )

        # 3. 36 Circumferential Laser Degree Ticks
        for i in range(36):
            deg_rad = i * (2.0 * math.pi / 36.0)
            is_cardinal = (i % 9 == 0)
            tick_len = 4 if is_cardinal else 2.5
            tx1 = cx + math.cos(deg_rad) * (r_ticks - tick_len)
            ty1 = cy + math.sin(deg_rad) * (r_ticks - tick_len)
            tx2 = cx + math.cos(deg_rad) * r_ticks
            ty2 = cy + math.sin(deg_rad) * r_ticks
            self.canvas.create_line(
                tx1, ty1, tx2, ty2,
                fill=ring_color if is_cardinal else tick_color,
                width=1.5 if is_cardinal else 1.0,
            )

        # 4. Dynamic Orbiting Telemetry Arc
        arc_start = (math.degrees(self.angle_offset * 1.5)) % 360
        self.canvas.create_arc(
            cx - r_ticks + 2, cy - r_ticks + 2, cx + r_ticks - 2, cy + r_ticks - 2,
            start=arc_start, extent=65, style="arc", outline=ring_color, width=1.8,
        )

        # 5. 10 Segmented Repulsor Spoke Lines (Clockwise)
        for i in range(10):
            a = self.angle_offset + i * (2.0 * math.pi / 10.0)
            x1 = cx + math.cos(a) * (r_inner + 2)
            y1 = cy + math.sin(a) * (r_inner + 2)
            x2 = cx + math.cos(a) * (r_coils - 3)
            y2 = cy + math.sin(a) * (r_coils - 3)
            self.canvas.create_line(x1, y1, x2, y2, fill=ring_color, width=1.5)

        # 6. 10 Electromagnetic Copper Solenoid Coils
        cr = max(3.0, self.size * 0.048)
        for i in range(10):
            a = self.angle_offset + i * (2.0 * math.pi / 10.0)
            bx = cx + math.cos(a) * r_coils
            by = cy + math.sin(a) * r_coils

            # Solenoid Mounting Bracket
            self.canvas.create_oval(
                bx - cr - 1, by - cr - 1, bx + cr + 1, by + cr + 1,
                fill="#041220", outline=ring_color, width=1.0,
            )
            # Copper Solenoid Core
            self.canvas.create_oval(
                bx - cr, by - cr, bx + cr, by + cr,
                fill=coil_color, outline="",
            )
            # Center Energy Diode
            self.canvas.create_oval(
                bx - 1.5, by - 1.5, bx + 1.5, by + 1.5,
                fill="#FFFFFF", outline="",
            )

        # 7. Middle Stator Track Ring
        self.canvas.create_oval(
            cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
            outline=aura_color, width=2.5,
        )

        # 8. Counter-Rotating Gyroscopic Stator Nodes (Counter-Clockwise: -angle_offset * 1.5)
        for j in range(6):
            ga = -self.angle_offset * 1.5 + j * (2.0 * math.pi / 6.0)
            gx = cx + math.cos(ga) * r_gyro
            gy = cy + math.sin(ga) * r_gyro

            # Energy track connection to core
            cx_inner = cx + math.cos(ga) * (r_core + 2)
            cy_inner = cy + math.sin(ga) * (r_core + 2)
            self.canvas.create_line(cx_inner, cy_inner, gx, gy, fill=gyro_color, width=1.2)

            self.canvas.create_oval(
                gx - 2.5, gy - 2.5, gx + 2.5, gy + 2.5,
                fill=gyro_color, outline="",
            )

        # 9. Central Tripartite Aperture Conduit Blades (120° Spacing)
        for k in range(3):
            ka = -self.angle_offset * 0.8 + k * (2.0 * math.pi / 3.0)
            kx1 = cx + math.cos(ka) * (r_core * 0.4)
            ky1 = cy + math.sin(ka) * (r_core * 0.4)
            kx2 = cx + math.cos(ka) * (r_core * 1.2)
            ky2 = cy + math.sin(ka) * (r_core * 1.2)
            self.canvas.create_line(kx1, ky1, kx2, ky2, fill=ring_color, width=1.6)

        # 10. Central Palladium Reactor Core with Breathing Sine-Wave Pulse
        pulse_val = 1.0 + 0.18 * math.sin(self.pulse_phase)
        active_core_r = r_core * pulse_val

        # Outer Radiant Plasma Diffusion
        self.canvas.create_oval(
            cx - active_core_r - 5, cy - active_core_r - 5,
            cx + active_core_r + 5, cy + active_core_r + 5,
            fill="", outline=aura_color, width=2.5,
        )
        # Brilliant Solid Palladium Core
        self.canvas.create_oval(
            cx - active_core_r, cy - active_core_r,
            cx + active_core_r, cy + active_core_r,
            fill=core_color, outline=ring_color, width=2.0,
        )

    def _animate(self) -> None:
        """Drive 30 FPS smooth physics rotation and breathing pulse."""
        if not self._animating:
            return
        try:
            if self.state == "THINKING":
                self.angle_offset += 0.07
                self.pulse_phase += 0.22
            elif self.state == "LISTENING":
                self.angle_offset += 0.03
                self.pulse_phase += 0.26
            else:
                self.angle_offset += 0.018
                self.pulse_phase += 0.075

            self._draw()
            self.after(35, self._animate)
        except Exception:
            pass

    def destroy(self) -> None:
        self._animating = False
        super().destroy()
