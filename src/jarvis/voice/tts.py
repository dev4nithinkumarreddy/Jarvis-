"""Text-to-Speech (TTS) engine interfaces with Piper and pyttsx3 implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from pathlib import Path
import threading
from typing import Callable
import numpy as np

logger = logging.getLogger(__name__)


class TTSEngine(ABC):
    """Abstract interface for local Text-to-Speech engines."""

    def __init__(self) -> None:
        self._muter: Callable[[bool], None] | None = None
        self._is_speaking = False
        self._lock = threading.Lock()

    def set_capture_muter(self, muter: Callable[[bool], None] | None) -> None:
        """Register a callback to mute/unmute microphone capture while TTS is active."""
        self._muter = muter

    def is_speaking(self) -> bool:
        """Return True if TTS is currently synthesizing or playing audio."""
        return self._is_speaking

    @abstractmethod
    def _speak_impl(self, text: str) -> None:
        """Engine-specific synthesis and playback implementation."""
        pass

    def stop(self) -> None:
        """Interrupt and silence any ongoing speech synthesis or playback."""
        self._is_speaking = False
        if self._muter:
            self._muter(False)
        self._stop_impl()

    def _stop_impl(self) -> None:
        """Subclass override for engine-specific speech cancellation."""
        pass

    def speak(self, text: str) -> None:
        """Speak the given text while coordinating microphone muting."""
        if not text.strip():
            return

        with self._lock:
            self._is_speaking = True
            if self._muter:
                self._muter(True)
            try:
                self._speak_impl(text)
            finally:
                self._is_speaking = False
                if self._muter:
                    self._muter(False)



def clean_text_for_speech(text: str) -> str:
    """Transform raw LLM output and markdown tables into smooth, natural spoken conversational dialogue."""
    if not text:
        return ""
    import re

    # 1. Remove code blocks ```...```
    cleaned = re.sub(r"```[\s\S]*?```", " code block omitted. ", text)
    # Remove inline code `...`
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Remove markdown links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)

    # 2. Phonetically normalize J.A.R.V.I.S. so TTS engines pronounce it as "Jarvis" rather than individual letters
    cleaned = re.sub(r"(?i)\bj(?:\.|\s*)*a(?:\.|\s*)*r(?:\.|\s*)*v(?:\.|\s*)*i(?:\.|\s*)*s\.?", "Jarvis", cleaned)

    # 2. Transmute markdown tables into fluent spoken clauses
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Table delimiter line e.g. |---|---|
        if re.match(r"^\s*\|?\s*[-:]+[-| :]+\|?\s*$", line):
            continue
        # Table row check
        if line.startswith("|") or (line.count("|") >= 2):
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            if not cells:
                continue
            # Skip purely generic table headers (e.g. Component | Status)
            c0_lower = cells[0].lower()
            clast_lower = cells[-1].lower()
            if any(h in c0_lower for h in ("component", "metric", "item", "parameter", "key")) and any(
                h in clast_lower for h in ("status", "value", "details", "condition", "state")
            ):
                continue
            if len(cells) == 2:
                lines.append(f"{cells[0]}: {cells[1]}.")
            else:
                lines.append(", ".join(cells) + ".")
            continue
        # Bullet list item e.g. - Point 1 or * Point 1
        bullet_m = re.match(r"^[\*\-\+]\s+(.*)", line)
        if bullet_m:
            lines.append(bullet_m.group(1))
            continue
        lines.append(line)

    cleaned = " ".join(lines)

    # 3. Remove markdown headers #, ##, etc.
    cleaned = re.sub(r"^[#]+\s*", "", cleaned, flags=re.MULTILINE)
    # Remove bold/italic markers **bold**, *italic*, __bold__
    cleaned = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", cleaned)
    # Remove remaining orphan asterisks and bullet symbols
    cleaned = cleaned.replace("*", "").replace("◆", "").replace("•", "")
    # Remove blockquotes
    cleaned = re.sub(r"^\s*>\s*", "", cleaned, flags=re.MULTILINE)
    # Remove bullet markers
    cleaned = re.sub(r"^\s*[-+]\s+", "", cleaned, flags=re.MULTILINE)
    # Turn any stray pipes into natural pauses (commas)
    cleaned = cleaned.replace("|", ", ")

    # 4. Symbol & technical unit expansions for natural British butler delivery
    cleaned = re.sub(r"(\d+)\s*%", r"\1 percent", cleaned)
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*GB\b", r"\1 gigabytes", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*MB\b", r"\1 megabytes", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*TB\b", r"\1 terabytes", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"°C\b", " degrees Celsius", cleaned)
    cleaned = re.sub(r"°F\b", " degrees Fahrenheit", cleaned)
    cleaned = re.sub(r"\bkm/h\b|\bkmph\b", " kilometers per hour", cleaned, flags=re.IGNORECASE)

    # 5. Normalize quotes, dashes, and cadence
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    cleaned = cleaned.replace("‘", "'").replace("’", "'")
    cleaned = cleaned.replace("—", " - ").replace("–", " - ")

    # 6. Clean up duplicate punctuation and whitespace
    cleaned = re.sub(r",\s*,+", ", ", cleaned)
    cleaned = re.sub(r"\.+\s*\.+", ". ", cleaned)
    cleaned = re.sub(r"\s*([.,;:])", r"\1 ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned



class Pyttsx3TTS(TTSEngine):
    """Local offline TTS engine using Windows SAPI5 / pyttsx3 with guaranteed multi-turn voice playback."""

    def __init__(
        self,
        rate: int = 190,
        device: int | str | None = None,
    ) -> None:
        super().__init__()
        self.rate = rate
        self.device = device
        import sys
        self._is_windows = sys.platform == "win32"

    def _speak_impl(self, text: str) -> None:
        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return

        # 1. On Windows, use native COM SAPI.SpVoice directly (zero event loop hangs, 100% reliable)
        if self._is_windows:
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                try:
                    speaker = win32com.client.Dispatch("SAPI.SpVoice")
                    # Map rate: pyttsx3 uses ~190 WPM; SAPI5 uses -10 to 10 where 0 is normal, 1 is slightly faster
                    if -10 <= self.rate <= 10:
                        sapi_rate = self.rate
                    else:
                        sapi_rate = max(-10, min(10, int((self.rate - 150) / 25)))
                    speaker.Rate = sapi_rate
                    speaker.Volume = 100
                    # 0 = SVSFDefault (synchronous playback, coordinates properly with mic muting)
                    speaker.Speak(clean_text, 0)
                    return
                finally:
                    pythoncom.CoUninitialize()
            except Exception as exc:
                logger.warning("Native SAPI.SpVoice error (%s), attempting fallback...", exc)

        # 2. Secondary fallback for Linux/macOS or if COM dispatch failed: pyttsx3 per-utterance
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            engine.say(clean_text)
            engine.runAndWait()
            try:
                engine.stop()
            except Exception:
                pass
            return
        except Exception as exc:
            logger.warning("pyttsx3 speech fallback failed: %s", exc)

        # 3. Last-resort fallback on Windows via PowerShell SpeechSynthesizer
        if self._is_windows:
            try:
                import subprocess
                ps_script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$synth.Speak([Console]::In.ReadToEnd())"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    input=clean_text,
                    text=True,
                    timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                logger.error("PowerShell speech synthesizer fallback failed: %s", exc)

    def _stop_impl(self) -> None:
        """Interrupt active SAPI5 speech utterance on Windows."""
        if self._is_windows:
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                try:
                    speaker = win32com.client.Dispatch("SAPI.SpVoice")
                    # SVSFPurgeBeforeSpeak (2) purges pending and active speech
                    speaker.Speak("", 2)
                finally:
                    pythoncom.CoUninitialize()
            except Exception as exc:
                logger.debug("Failed to purge SAPI speech: %s", exc)



class PiperTTS(TTSEngine):
    """Fast local neural TTS engine using Piper ONNX models with pyttsx3 fallback."""

    def __init__(
        self,
        model_path: Path | str,
        config_path: Path | str | None = None,
        device: int | str | None = None,
        fallback_tts: TTSEngine | None = None,
    ) -> None:
        super().__init__()
        self.model_path = Path(model_path)
        self.config_path = Path(config_path) if config_path else None
        self.device = device
        self.fallback_tts = fallback_tts
        self._voice = None

    def _get_voice(self):
        if self._voice is None:
            from piper import PiperVoice
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"Piper voice model not found at '{self.model_path}'. "
                    "Download model (.onnx and .onnx.json) via 'jarvis voice setup-piper'."
                )
            self._voice = PiperVoice.load(
                model_path=str(self.model_path),
                config_path=str(self.config_path) if self.config_path else None,
            )
        return self._voice

    def _speak_impl(self, text: str) -> None:
        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return
        try:
            voice = self._get_voice()
            import sounddevice as sd
            audio_chunks = []
            for chunk in voice.synthesize(clean_text):
                audio_chunks.append(chunk.audio_data)

            if audio_chunks:
                full_audio = np.concatenate(audio_chunks)
                sd.play(full_audio, samplerate=voice.config.sample_rate, device=self.device)
                sd.wait()
        except Exception as exc:
            if self.fallback_tts is not None:
                logger.warning("Piper TTS synthesis encountered an issue (%s). Using fallback TTS.", exc)
                self.fallback_tts._speak_impl(text)
            else:
                raise

    def _stop_impl(self) -> None:
        """Interrupt Piper audio stream and fallback engine."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        if self.fallback_tts is not None:
            self.fallback_tts.stop()


class MockTTS(TTSEngine):
    """Mock TTS engine recording utterances for fast, silent testing."""

    def __init__(self) -> None:
        super().__init__()
        self.spoken_texts: list[str] = []
        self.stopped = False

    def _speak_impl(self, text: str) -> None:
        self.spoken_texts.append(text)

    def _stop_impl(self) -> None:
        self.stopped = True

