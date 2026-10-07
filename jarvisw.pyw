"""Silent background entry point for J.A.R.V.I.S. Mark VII on Windows.

Launch without a black terminal window using pythonw.exe or double-clicking from File Explorer.
Spins up Voice (TTS + Mic), Web HUD, and the Windows System Tray companion.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
import webbrowser

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jarvis.core.channels import CompositeOutputChannel
from jarvis.core.config import load_config
from jarvis.safety.audit import AuditLogger
from jarvis.safety.confirmers import CompositeConfirmer
from jarvis.safety.killswitch import get_kill_switch
from jarvis.ui.cli import create_orchestrator
from jarvis.ui.server import HudServer
from jarvis.ui.tray import JarvisTrayApp
from jarvis.voice.audio_in import AudioCapture
from jarvis.voice.channel import VoiceChannel, generate_jarvis_greeting
from jarvis.voice.stt import FasterWhisperSTT
from jarvis.voice.triggers import PushToTalkTrigger
from jarvis.voice.tts import PiperTTS, Pyttsx3TTS


def main() -> None:
    # 1. Load configuration
    config_path = str(PROJECT_ROOT / "config.yaml")
    config = load_config(config_path)

    kill_switch = get_kill_switch()
    kill_switch.start_hotkey_listener()

    active_confirmers = []
    active_outputs = []

    user_title = getattr(config.persona, "user_title", "Sir") if hasattr(config, "persona") else "Sir"

    # 2. Start Local Web HUD Server
    hud_server = HudServer(
        host=config.hud.host,
        port=config.hud.port,
        kill_switch=kill_switch,
        confirm_timeout_seconds=config.hud.confirm_timeout_seconds,
        user_title=user_title,
    )
    hud_server.start()
    active_confirmers.append(hud_server.channel)
    active_outputs.append(hud_server.channel)

    # 3. Initialize TTS and Voice Channel
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
        active_outputs.append(voice_channel)
    except Exception:
        # If microphone fails, TTS output still speaks!
        class DirectTTSOutput:
            def say(self, message: str) -> None:
                tts.speak(message)
        active_outputs.append(DirectTTSOutput())

    composite_confirmer = CompositeConfirmer(active_confirmers)
    composite_output = CompositeOutputChannel(active_outputs)

    # 4. Audit logger & Orchestrator
    audit_logger = AuditLogger(config.audit_log_path)
    audit_logger.set_listener(hud_server.channel.broadcast_audit)

    orchestrator = create_orchestrator(
        config_path=config_path,
        confirmer=composite_confirmer,
    )
    orchestrator.audit_logger = audit_logger

    # 5. Proactive Iron Man Vocal Greeting on Startup!
    greeting_text = generate_jarvis_greeting(user_title)
    # Speaks aloud through TTS and sends to HUD!
    composite_output.say(greeting_text)

    # 6. Open Web HUD in default browser
    webbrowser.open(hud_server.url)

    # 7. Unified input queue and worker loops
    unified_input_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    stop_event = threading.Event()

    # Voice listener thread
    if voice_channel is not None:
        def voice_worker() -> None:
            while not stop_event.is_set():
                try:
                    text = voice_channel.get_user_input()
                    if text and not stop_event.is_set():
                        unified_input_queue.put(("Voice", text))
                except Exception:
                    time.sleep(0.1)

        t_voice = threading.Thread(target=voice_worker, daemon=True)
        t_voice.start()

    # HUD message thread
    def hud_worker() -> None:
        while not stop_event.is_set():
            try:
                text = hud_server.channel.input_queue.get(timeout=0.2)
                if text and not stop_event.is_set():
                    unified_input_queue.put(("HUD", text))
            except queue.Empty:
                continue

    t_hud = threading.Thread(target=hud_worker, daemon=True)
    t_hud.start()

    # 8. Start Windows System Tray Companion
    def analyze_screen_action() -> None:
        prompt = "Jarvis, please analyze my current desktop screen and report what you see."
        composite_output.say("Analyzing display screen now, Sir.")
        reply = orchestrator.run_turn(prompt)
        composite_output.say(reply)

    def shutdown_action() -> None:
        stop_event.set()
        hud_server.stop()
        kill_switch.stop_hotkey_listener()
        sys.exit(0)

    tray_app = JarvisTrayApp(
        hud_url=hud_server.url,
        on_screen_analyze=analyze_screen_action,
        on_kill_switch=lambda: kill_switch.trigger(),
        on_shutdown=shutdown_action,
    )
    tray_app.start(blocking=False)

    # 9. Main event loop
    try:
        while not stop_event.is_set():
            if kill_switch.is_set():
                composite_output.say("Emergency kill switch activated. Standing down.")
                break

            try:
                source, user_text = unified_input_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if not user_text:
                continue

            hud_server.broadcast_state("thinking")
            reply = orchestrator.run_turn(user_text)
            hud_server.broadcast_state("idle")

            composite_output.say(reply)
    finally:
        stop_event.set()
        hud_server.stop()
        tray_app.stop()
        kill_switch.stop_hotkey_listener()


if __name__ == "__main__":
    main()
