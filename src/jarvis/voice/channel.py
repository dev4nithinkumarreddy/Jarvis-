"""VoiceChannel implementing InputChannel, OutputChannel, and Confirmer."""

from __future__ import annotations

from datetime import datetime
import logging
import re
import threading
from typing import Any, Callable
from rich.console import Console

from jarvis.core.channels import Confirmer, InputChannel, OutputChannel
from jarvis.core.config import JarvisConfig
from jarvis.voice.audio_in import AudioCapture
from jarvis.voice.stt import STTEngine
from jarvis.voice.triggers import PushToTalkTrigger, VoiceTrigger, match_key
from jarvis.voice.tts import TTSEngine

logger = logging.getLogger(__name__)
console = Console()


def parse_spoken_confirmation(spoken_text: str) -> bool | None:
    """Parse spoken confirmation response.

    Rules:
    - Only the exact phrase 'yes confirm' returns True.
    - Only the exact word 'no' returns False.
    - Anything else (including 'yes', 'confirm', noise, silence) returns None.
    """
    clean = re.sub(r"[^\w\s]", "", spoken_text.lower()).strip()
    if clean == "yes confirm":
        return True
    if clean == "no":
        return False
    return None


def generate_jarvis_greeting(user_title: str = "Sir") -> str:
    """Generate dynamic time-of-day greeting in Paul Bettany J.A.R.V.I.S. persona."""
    now = datetime.now()
    hour = now.hour

    if 5 <= hour < 12:
        salutation = f"Good morning, {user_title}."
    elif 12 <= hour < 17:
        salutation = f"Good afternoon, {user_title}."
    else:
        salutation = f"Good evening, {user_title}."

    return f"{salutation} All Stark systems are online. J.A.R.V.I.S. is at your service. How may I assist you today?"


class VoiceChannel(InputChannel, OutputChannel, Confirmer):
    """Voice communication channel providing speech I/O and strict confirmation policies."""

    def __init__(
        self,
        stt: STTEngine,
        tts: TTSEngine,
        trigger: VoiceTrigger | None = None,
        capture: AudioCapture | None = None,
        config: JarvisConfig | None = None,
        confirm_timeout: float = 30.0,
        dual_confirm_key: str = "enter",
        dual_event_hook: Callable[[threading.Event], None] | None = None,
        audio_capture: AudioCapture | None = None,
        confirm_timeout_seconds: float | None = None,
    ) -> None:
        self.stt = stt
        self.tts = tts
        self.capture = capture or audio_capture
        if trigger is None and self.capture is not None:
            key_name = config.voice.ptt_key if config is not None else "space"
            self.trigger = PushToTalkTrigger(capture=self.capture, key_name=key_name)
        else:
            self.trigger = trigger  # type: ignore[assignment]
        self.config = config
        self._dual_event_hook = dual_event_hook

        timeout_val = confirm_timeout_seconds if confirm_timeout_seconds is not None else confirm_timeout
        self.confirm_timeout = (
            config.confirm_timeout_seconds if config is not None else timeout_val
        )
        self.dual_confirm_key = (
            config.voice.dual_confirm_key if config is not None else dual_confirm_key
        )

        # Coordinate TTS playback with microphone capture muting
        if self.capture is not None:
            self.tts.set_capture_muter(self.capture.set_muted)

    def say(self, message: str) -> None:
        """Deliver a spoken message aloud via TTS (and print to console)."""
        if not message.strip():
            return
        console.print(f"[bold green]Jarvis (voice) >[/bold green] {message}")
        self.tts.speak(message)

    def get_user_input(self, prompt: str = "") -> str:
        """Prompt the user and capture transcribed speech input."""
        if prompt.strip():
            self.say(prompt)

        audio = self.trigger.listen_for_input()
        if len(audio) == 0:
            return ""

        transcript = self.stt.transcribe(audio).strip()
        if transcript:
            console.print(f"[bold cyan]You (voice) >[/bold cyan] {transcript}")
        return transcript

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        """Prompt user for spoken confirmation of a risky action.

        Policies:
        1. The agent speaks the confirmation text aloud via TTS.
        2. Only exact spoken words 'yes confirm' or 'no' count. Anything else,
           or silence past the timeout, is NO.
        3. For any action whose description contains 'overwrite', 'move',
           or a tainted warning, require the user to ALSO press a configured key.
        """
        # Determine if dual-factor key press is required
        desc_lower = action_description.lower()
        warn_lower = (warning or "").lower()
        is_overwrite = (
            ("overwrite" in desc_lower and "overwrites: no" not in desc_lower)
            or "overwrites: yes" in desc_lower
        )
        is_dual_required = (
            is_overwrite
            or "move" in desc_lower
            or "taint" in warn_lower
            or "taint" in desc_lower
        )

        # Prepare spoken confirmation prompt
        prompt_parts: list[str] = []
        if warning:
            prompt_parts.append(f"Warning: {warning}")
        prompt_parts.append(f"Proposed action: {action_description}")

        if is_dual_required:
            prompt_parts.append(
                f"To confirm, say 'yes confirm' and press the {self.dual_confirm_key} key. Say 'no' to cancel."
            )
        else:
            prompt_parts.append("Say 'yes confirm' to approve, or say 'no' to cancel.")

        full_prompt = ". ".join(prompt_parts)

        # Set up keyboard listener for dual-factor verification
        key_pressed_event = threading.Event()
        listener = None
        if is_dual_required:
            if self._dual_event_hook is not None:
                self._dual_event_hook(key_pressed_event)
            else:
                from pynput import keyboard

                def on_press(key: Any) -> None:
                    if match_key(key, self.dual_confirm_key):
                        key_pressed_event.set()

                listener = keyboard.Listener(on_press=on_press)
                listener.start()

        try:
            # 1. Speak prompt aloud (microphone is muted during playback)
            self.say(full_prompt)

            # 2. Record spoken response within confirmation timeout
            audio = self.trigger.listen_for_input(timeout=self.confirm_timeout)
            if len(audio) == 0:
                logger.info("Voice confirmation timed out or received no audio: DENIED.")
                return False

            # 3. Transcribe response
            transcript = self.stt.transcribe(audio).strip()
            console.print(f"[bold cyan]You (confirmation) >[/bold cyan] {transcript}")

            parsed = parse_spoken_confirmation(transcript)

            if parsed is None:
                logger.info(
                    f"Spoken response '{transcript}' is neither 'yes confirm' nor 'no': DENIED."
                )
                return False

            if parsed is False:
                logger.info("Spoken confirmation was 'no': DENIED.")
                return False

            # parsed is True ('yes confirm')
            if is_dual_required:
                if not key_pressed_event.is_set():
                    logger.warning(
                        f"Spoken 'yes confirm' received, but required key [{self.dual_confirm_key}] was not pressed: DENIED."
                    )
                    return False
                logger.info(
                    f"Action approved with spoken 'yes confirm' and key [{self.dual_confirm_key}] press."
                )
                return True

            logger.info("Action approved with spoken 'yes confirm'.")
            return True

        finally:
            if listener is not None:
                try:
                    listener.stop()
                    listener.join(timeout=1.0)
                except Exception as exc:
                    logger.debug(f"Error stopping key listener: {exc}")
