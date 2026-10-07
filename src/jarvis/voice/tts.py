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
    """Strip markdown formatting, URLs, code blocks, and symbols for natural TTS."""
    if not text:
        return ""
    import re

    # Remove code blocks ```...```
    cleaned = re.sub(r"```[\s\S]*?```", " code block omitted. ", text)
    # Remove inline code `...`
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Remove markdown links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    # Remove markdown headers #, ##, etc.
    cleaned = re.sub(r"^[#]+\s*", "", cleaned, flags=re.MULTILINE)
    # Remove bold/italic markers
    cleaned = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", cleaned)
    # Remove blockquotes
    cleaned = re.sub(r"^\s*>\s*", "", cleaned, flags=re.MULTILINE)
    # Remove bullet markers
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
    # Normalize fancy quotes and dashes
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    cleaned = cleaned.replace("‘", "'").replace("’", "'")
    cleaned = cleaned.replace("—", " - ").replace("–", " - ")
    # Collapse multiple whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class Pyttsx3TTS(TTSEngine):
    """Local offline TTS engine using pyttsx3 with sounddevice audio output."""

    def __init__(
        self,
        rate: int = 190,
        device: int | str | None = None,
    ) -> None:
        super().__init__()
        self.rate = rate
        self.device = device
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
        return self._engine

    def _speak_impl(self, text: str) -> None:
        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return

        import os
        import tempfile
        import wave
        import sounddevice as sd
        import numpy as np

        engine = self._get_engine()
        tmp_wav = os.path.join(
            tempfile.gettempdir(),
            f"jarvis_tts_{os.getpid()}_{threading.get_ident()}.wav",
        )
        try:
            engine.save_to_file(clean_text, tmp_wav)
            engine.runAndWait()

            if os.path.exists(tmp_wav) and os.path.getsize(tmp_wav) > 44:
                with wave.open(tmp_wav, "rb") as wf:
                    sr = wf.getframerate()
                    raw = wf.readframes(wf.getnframes())
                if raw:
                    audio_data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    sd.play(audio_data, samplerate=sr, device=self.device)
                    sd.wait()
            else:
                engine.say(clean_text)
                engine.runAndWait()
        except Exception as exc:
            logger.warning(f"Error during audio playback via sounddevice: {exc}")
            try:
                engine.say(clean_text)
                engine.runAndWait()
            except Exception:
                pass
        finally:
            if os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass


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


class MockTTS(TTSEngine):
    """Mock TTS engine recording utterances for fast, silent testing."""

    def __init__(self) -> None:
        super().__init__()
        self.spoken_texts: list[str] = []

    def _speak_impl(self, text: str) -> None:
        self.spoken_texts.append(text)
