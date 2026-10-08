"""Interactive Stark-themed API Key setup dialog for J.A.R.V.I.S. Mark VII."""

from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from jarvis.core.config import load_config, load_dotenv_if_present


def ensure_api_key(config_path: str = "config.yaml") -> bool:
    """Ensure the required API key for the configured brain provider is available.

    If not found in environment or .env file, displays a GUI setup dialog.
    Returns True if key is set, False if the user cancelled.
    """
    load_dotenv_if_present()
    try:
        config = load_config(config_path)
        provider = config.brain.provider.lower()
    except Exception:
        provider = "groq"

    key_var_name = "GROQ_API_KEY" if provider == "groq" else "ANTHROPIC_API_KEY"
    existing_key = os.environ.get(key_var_name, "").strip()
    if existing_key:
        return True

    # Prompt user via GUI
    key = prompt_api_key(provider=provider, key_var_name=key_var_name)
    if not key:
        return False

    # Persist to .env in project root
    project_root = Path(config_path).resolve().parent
    env_file = project_root / ".env"
    _save_to_env(env_file, key_var_name, key)

    os.environ[key_var_name] = key
    return True


def _save_to_env(env_file: Path, key_name: str, key_val: str) -> None:
    """Save or update key in .env file."""
    lines: list[str] = []
    found = False
    if env_file.is_file():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(f"{key_name}=") or stripped.startswith(f"export {key_name}="):
                        lines.append(f"{key_name}={key_val}\n")
                        found = True
                    else:
                        lines.append(line)
        except Exception:
            lines = []

    if not found:
        lines.append(f"{key_name}={key_val}\n")

    try:
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass


def prompt_api_key(provider: str = "groq", key_var_name: str = "GROQ_API_KEY") -> Optional[str]:
    """Display an Arc Reactor-themed dialog prompting for the missing API key."""
    result: list[Optional[str]] = [None]

    root = tk.Tk()
    root.title("J.A.R.V.I.S. Mark VII - System Activation")
    root.geometry("520x360")
    root.resizable(False, False)

    # Stark Industries Dark Theme colors
    bg_dark = "#0a1128"
    card_bg = "#111d40"
    cyan_glow = "#00e5ff"
    text_color = "#e2e8f0"
    muted_text = "#94a3b8"

    root.configure(bg=bg_dark)

    # Center window on screen
    root.update_idletasks()
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = max(0, (screen_width - 520) // 2)
    y = max(0, (screen_height - 360) // 2)
    root.geometry(f"+{x}+{y}")

    # Top banner frame
    banner = tk.Frame(root, bg=bg_dark)
    banner.pack(fill="x", padx=24, pady=(20, 10))

    title_label = tk.Label(
        banner,
        text="⚡ J.A.R.V.I.S. ACTIVATION REQUIRED",
        font=("Segoe UI", 14, "bold"),
        fg=cyan_glow,
        bg=bg_dark,
    )
    title_label.pack(anchor="w")

    subtitle = tk.Label(
        banner,
        text=f"The neural core requires an active {provider.upper()} API Key to come online.",
        font=("Segoe UI", 9),
        fg=muted_text,
        bg=bg_dark,
    )
    subtitle.pack(anchor="w", pady=(4, 0))

    # Inner Card frame
    card = tk.Frame(root, bg=card_bg, highlightbackground=cyan_glow, highlightthickness=1)
    card.pack(fill="both", expand=True, padx=24, pady=10)

    instr = tk.Label(
        card,
        text=f"Please enter your {key_var_name}:",
        font=("Segoe UI", 10, "bold"),
        fg=text_color,
        bg=card_bg,
    )
    instr.pack(anchor="w", padx=16, pady=(16, 6))

    entry_var = tk.StringVar()
    entry = tk.Entry(
        card,
        textvariable=entry_var,
        font=("Consolas", 10),
        bg="#060c1c",
        fg=cyan_glow,
        insertbackground=cyan_glow,
        relief="flat",
        highlightbackground="#1e293b",
        highlightcolor=cyan_glow,
        highlightthickness=1,
    )
    entry.pack(fill="x", padx=16, pady=4, ipady=6)
    entry.focus_set()

    # Right-click context menu for paste
    menu = tk.Menu(entry, tearoff=0)
    menu.add_command(label="Paste", command=lambda: entry.event_generate("<<Paste>>"))

    def show_context_menu(event: tk.Event) -> None:
        menu.tk_popup(event.x_root, event.y_root)

    entry.bind("<Button-3>", show_context_menu)

    # Info hint
    if provider == "groq":
        hint_text = "💡 Don't have a key? Get one free at console.groq.com/keys"
    else:
        hint_text = "💡 Obtain your key from console.anthropic.com/settings/keys"

    hint_label = tk.Label(
        card,
        text=hint_text,
        font=("Segoe UI", 8),
        fg=muted_text,
        bg=card_bg,
    )
    hint_label.pack(anchor="w", padx=16, pady=(6, 12))

    # Action buttons frame
    btn_frame = tk.Frame(root, bg=bg_dark)
    btn_frame.pack(fill="x", padx=24, pady=(10, 20))

    status_label = tk.Label(btn_frame, text="", font=("Segoe UI", 9), fg="#f87171", bg=bg_dark)
    status_label.pack(side="left")

    def on_activate() -> None:
        key_val = entry_var.get().strip()
        if not key_val:
            status_label.config(text="Please paste an API key.")
            return
        result[0] = key_val
        root.destroy()

    def on_cancel() -> None:
        result[0] = None
        root.destroy()

    cancel_btn = tk.Button(
        btn_frame,
        text="Cancel",
        font=("Segoe UI", 9),
        bg="#1e293b",
        fg=text_color,
        activebackground="#334155",
        activeforeground=text_color,
        relief="flat",
        cursor="hand2",
        padx=14,
        pady=5,
        command=on_cancel,
    )
    cancel_btn.pack(side="right", padx=(8, 0))

    activate_btn = tk.Button(
        btn_frame,
        text="⚡ Activate J.A.R.V.I.S.",
        font=("Segoe UI", 9, "bold"),
        bg=cyan_glow,
        fg="#050b14",
        activebackground="#38bdf8",
        activeforeground="#050b14",
        relief="flat",
        cursor="hand2",
        padx=14,
        pady=5,
        command=on_activate,
    )
    activate_btn.pack(side="right")

    # Enter key triggers activate
    entry.bind("<Return>", lambda _: on_activate())

    root.mainloop()
    return result[0]
