"""Silent background native desktop entry point for J.A.R.V.I.S. Mark VII on Windows.

Launch without a black terminal window using pythonw.exe or double-clicking from Desktop / Startup.
Spins up Voice (TTS + Faster-Whisper), Native Desktop GUI (CustomTkinter), Stark System Tray,
and Universal Global Hotkey (Ctrl + Alt + J) with 100% zero browser dependency.
"""

from __future__ import annotations

import ctypes
import io
import logging
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import traceback

# Auto-configure TCL_LIBRARY and TK_LIBRARY paths on Windows Python 3.14
py_dir = Path(sys.executable).parent
tcl_dir = py_dir / "tcl"
if tcl_dir.exists():
    if "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = str(tcl_dir / "tcl8.6")
    if "TK_LIBRARY" not in os.environ:
        os.environ["TK_LIBRARY"] = str(tcl_dir / "tk8.6")

# Safeguard standard streams when running under pythonw.exe
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r", encoding="utf-8")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Setup persistent file logging for native desktop runner
log_dir = PROJECT_ROOT / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(log_dir / "jarvisw.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jarvisw")


def show_gui_error(title: str, message: str) -> None:
    """Show a native Windows modal error box."""
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x10000)
    except Exception:
        pass


from jarvis.core.channels import CompositeOutputChannel
from jarvis.core.config import load_config
from jarvis.safety.audit import AuditLogger
from jarvis.safety.confirmers import CompositeConfirmer
from jarvis.safety.killswitch import get_kill_switch
from jarvis.ui.cli import create_orchestrator
from jarvis.ui.hotkey import UniversalGlobalHotkey
from jarvis.ui.key_dialog import ensure_api_key
from jarvis.ui.native_app import JarvisNativeApp
from jarvis.ui.tray import JarvisTrayApp
from jarvis.voice.audio_in import AudioCapture
from jarvis.voice.channel import VoiceChannel, generate_jarvis_greeting
from jarvis.voice.stt import FasterWhisperSTT
from jarvis.voice.triggers import PushToTalkTrigger
from jarvis.voice.tts import PiperTTS, Pyttsx3TTS

_instance_lock_socket: socket.socket | None = None
LOCK_PORT = 48999


def acquire_single_instance_or_summon() -> bool:
    """Ensure single instance. If already running, send summon signal and exit."""
    global _instance_lock_socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(5)
        _instance_lock_socket = s
        return True
    except OSError:
        # Another instance is already active. Send summon trigger to it.
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(2.0)
            client.connect(("127.0.0.1", LOCK_PORT))
            client.sendall(b"SUMMON\n")
            client.close()
        except Exception as exc:
            logger.debug("Failed to signal existing instance: %s", exc)
        return False


def start_summon_listener(app: JarvisNativeApp, stop_event: threading.Event) -> None:
    """Listen for summon triggers from subsequent desktop icon launches."""
    global _instance_lock_socket
    if not _instance_lock_socket:
        return

    def _loop() -> None:
        _instance_lock_socket.settimeout(1.0)
        while not stop_event.is_set():
            try:
                conn, _ = _instance_lock_socket.accept()
                data = conn.recv(64)
                conn.close()
                if b"SUMMON" in data:
                    logger.info("Received external summon signal. Bringing app to foreground.")
                    app.summon()
            except socket.timeout:
                continue
            except Exception:
                break

    threading.Thread(target=_loop, daemon=True).start()


def main() -> None:
    logger.info("Initializing J.A.R.V.I.S. Mark VII standalone desktop application...")
    config_path = str(PROJECT_ROOT / "config.yaml")

    # Single-instance handling
    if not acquire_single_instance_or_summon():
        logger.info("J.A.R.V.I.S. is already active. Signaled running instance to summon.")
        # Spoken audio notification
        try:
            tts = Pyttsx3TTS()
            tts.speak("At your service, Sir. Restoring J.A.R.V.I.S. now.")
        except Exception:
            pass
        return

    # Ensure required Groq API key is configured
    if not ensure_api_key(config_path):
        logger.info("Activation cancelled by user.")
        return

    # 1. Load configuration
    config = load_config(config_path)

    kill_switch = get_kill_switch()
    kill_switch.start_hotkey_listener()

    user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"
    model_name = getattr(config.brain, "model", "Groq LLaMA 3.3 70B")

    # 2. Initialize TTS Engine
    fallback_tts = Pyttsx3TTS(device=config.voice.output_device)
    if config.voice.tts_engine == "piper" and config.voice.piper_model_path:
        tts = PiperTTS(
            model_path=config.voice.piper_model_path,
            config_path=config.voice.piper_config_path,
            device=config.voice.output_device,
            fallback_tts=fallback_tts,
        )
    else:
        tts = fallback_tts

    def speak_async(text: str) -> None:
        threading.Thread(target=tts.speak, args=(text,), daemon=True).start()

    # 3. Create Orchestrator with security confirmer
    stop_event = threading.Event()

    # Placeholders to resolve forward references
    app_ref: list[JarvisNativeApp | None] = [None]

    def on_submit_handler(text: str) -> None:
        if not app_ref[0]:
            return
        app = app_ref[0]
        app.set_state("THINKING")
        try:
            reply = orchestrator.run_turn(text)
            app.channel.say(reply)
            speak_async(reply)
        except Exception as err:
            err_msg = f"Neural engine encountered an exception: {err}"
            logger.exception("Error during turn processing:")
            app.channel.say(err_msg)
            speak_async("An error occurred during directive processing, Sir.")
        finally:
            app.set_state("ONLINE")

    def on_screen_analyze_handler() -> None:
        if not app_ref[0]:
            return
        app = app_ref[0]
        app.set_state("THINKING")
        app.channel.say("Analyzing display screen now, Sir.")
        speak_async("Analyzing display screen now, Sir.")
        try:
            reply = orchestrator.run_turn("Jarvis, please analyze my current desktop screen.")
            app.channel.say(reply)
            speak_async(reply)
        except Exception as err:
            logger.exception("Screen analysis error:")
            app.channel.say(f"Screen vision failed: {err}")
        finally:
            app.set_state("ONLINE")

    # 4. Instantiate Native Desktop Application
    app = JarvisNativeApp(
        on_user_submit=on_submit_handler,
        on_screen_analyze=on_screen_analyze_handler,
        on_kill_switch=lambda: kill_switch.trigger(),
        user_title=user_title,
        model_name=model_name,
    )
    app_ref[0] = app

    # 5. Composite Confirmer & Output
    active_confirmers = [app.channel]
    active_outputs = [app.channel]

    voice_channel = None
    try:
        audio_capture = AudioCapture(
            sample_rate=config.voice.sample_rate,
            device=config.voice.input_device,
        )
        stt = FasterWhisperSTT(
            model_size=config.voice.stt_model,
            device=config.voice.stt_device,
            language=getattr(config.voice, "language", "en"),
        )
        stt.warmup(async_mode=True)
        trigger = PushToTalkTrigger(
            capture=audio_capture,
            key_name=config.voice.ptt_key,
        )
        voice_channel = VoiceChannel(
            stt=stt,
            tts=tts,
            trigger=trigger,
            capture=audio_capture,
            config=config,
        )
        active_confirmers.append(voice_channel)
    except Exception as exc:
        logger.warning("Microphone voice channel unavailable, operating in text-only mode: %s", exc)

    composite_confirmer = CompositeConfirmer(active_confirmers)

    audit_logger = AuditLogger(config.audit_log_path)
    orchestrator = create_orchestrator(
        config_path=config_path,
        confirmer=composite_confirmer,
    )
    orchestrator.audit_logger = audit_logger

    # 6. Global Summon Shortcut (Ctrl + Alt + J)
    hotkey = UniversalGlobalHotkey(callback=app.summon, chord="ctrl+alt+j")
    hotkey.start()
    logger.info("Universal Global Hotkey (Ctrl+Alt+J) listening.")

    # 7. Single-instance summon listener
    start_summon_listener(app, stop_event)

    # 8. Background Voice Listener Thread (Push-to-Talk)
    if voice_channel is not None:
        def voice_worker() -> None:
            while not stop_event.is_set():
                try:
                    user_spoken = voice_channel.get_user_input()
                    if user_spoken and not stop_event.is_set():
                        app.post_message(user_title.upper(), user_spoken, is_user=True)
                        on_submit_handler(user_spoken)
                except Exception:
                    time.sleep(0.1)

        threading.Thread(target=voice_worker, daemon=True).start()

    # 9. Windows System Tray Companion
    def shutdown_action() -> None:
        stop_event.set()
        hotkey.stop()
        kill_switch.stop_hotkey_listener()
        if _instance_lock_socket:
            try:
                _instance_lock_socket.close()
            except Exception:
                pass
        app.destroy()
        sys.exit(0)

    tray_app = JarvisTrayApp(
        on_open_app=app.summon,
        on_screen_analyze=on_screen_analyze_handler,
        on_kill_switch=lambda: kill_switch.trigger(),
        on_shutdown=shutdown_action,
    )
    tray_app.start(blocking=False)

    # 10. Proactive Vocal Greeting on Startup!
    greeting_text = generate_jarvis_greeting(user_title)
    app.channel.say(greeting_text)
    speak_async(greeting_text)

    # 11. Run Tkinter Event Loop
    try:
        app.mainloop()
    finally:
        stop_event.set()
        hotkey.stop()
        tray_app.stop()
        kill_switch.stop_hotkey_listener()
        if _instance_lock_socket:
            try:
                _instance_lock_socket.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            err_msg = f"J.A.R.V.I.S. shutdown with status code {exc.code}.\nCheck logs/jarvisw.log for details."
            logger.error(err_msg)
            show_gui_error("J.A.R.V.I.S. Notice", err_msg)
    except Exception as exc:
        logger.exception("Fatal error during J.A.R.V.I.S. execution:")
        show_gui_error(
            "J.A.R.V.I.S. Startup Error",
            f"An error occurred during startup:\n\n{exc}\n\nReview logs/jarvisw.log for the full traceback.",
        )
