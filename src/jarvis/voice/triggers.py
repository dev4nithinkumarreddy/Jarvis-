"""Voice trigger implementations for Push-to-Talk (PTT) and Wake Word detection."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import queue
import threading
import time
from typing import Any, Callable
import numpy as np
from pynput import keyboard

from jarvis.voice.audio_in import AudioCapture

logger = logging.getLogger(__name__)


def match_key(key: Any, key_name: str) -> bool:
    """Check if a pynput key event matches a configured key name string.

    Supports:
        - Special keys: 'space', 'enter', 'return', 'ctrl', 'alt', 'shift', 'tab', 'esc', etc.
        - Function keys: 'f1' through 'f12'
        - Character keys: 'a' through 'z', '0' through '9'
    """
    if key is None or not key_name:
        return False

    name = key_name.lower().strip()

    # Special common key aliases
    if name == "space":
        return key == keyboard.Key.space
    if name in ("enter", "return"):
        return key == keyboard.Key.enter
    if name == "ctrl":
        return key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
    if name == "alt":
        return key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r)
    if name == "shift":
        return key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)
    if name == "tab":
        return key == keyboard.Key.tab
    if name in ("esc", "escape"):
        return key == keyboard.Key.esc

    # Direct match via keyboard.Key enum name
    if hasattr(keyboard.Key, name):
        return key == getattr(keyboard.Key, name)

    # Character match for letters / numbers
    if hasattr(key, "char") and key.char:
        return key.char.lower() == name

    return False


class VoiceTrigger(ABC):
    """Abstract interface for triggers initiating speech input collection."""

    @abstractmethod
    def listen_for_input(self, timeout: float | None = None) -> np.ndarray:
        """Wait for trigger and return recorded audio samples as 1D float32 array.

        Args:
            timeout: Maximum seconds to wait before aborting (None for indefinite).

        Returns:
            1D numpy array of float32 audio samples, or empty array if timed out.
        """
        pass


class PushToTalkTrigger(VoiceTrigger):
    """Hold a configurable physical key to record audio; release to finish."""

    def __init__(
        self,
        capture: AudioCapture,
        key_name: str = "space",
    ) -> None:
        self.capture = capture
        self.key_name = key_name
        self._is_held = False
        self._audio_chunks: list[np.ndarray] = []
        self._lock = threading.Lock()

    def _audio_callback(self, chunk: np.ndarray) -> None:
        """Collect incoming audio chunks while key is held."""
        with self._lock:
            if self._is_held:
                self._audio_chunks.append(chunk)

    def listen_for_input(self, timeout: float | None = None) -> np.ndarray:
        """Wait for PTT key press, record while held, and return audio on release."""
        pressed_event = threading.Event()
        released_event = threading.Event()
        self._audio_chunks = []

        def on_press(key: Any) -> None:
            if match_key(key, self.key_name):
                with self._lock:
                    if not self._is_held:
                        self._is_held = True
                        pressed_event.set()

        def on_release(key: Any) -> None:
            if match_key(key, self.key_name):
                with self._lock:
                    if self._is_held:
                        self._is_held = False
                        released_event.set()
                        return False  # Stop pynput listener

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()

        try:
            # Start background audio streaming
            self.capture.start_stream(self._audio_callback)

            # Wait for user to press the key
            if not pressed_event.wait(timeout=timeout):
                logger.debug("Push-to-talk timed out waiting for key press.")
                return np.zeros(0, dtype=np.float32)

            logger.info(f"Push-to-talk [{self.key_name}] active: recording...")

            # Wait for user to release the key
            released_event.wait()
            logger.info("Push-to-talk released: recording finished.")

        finally:
            self.capture.stop_stream()
            listener.stop()
            listener.join(timeout=1.0)
            with self._lock:
                self._is_held = False

        with self._lock:
            if not self._audio_chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._audio_chunks)


class WakeWordDetector(ABC):
    """Abstract interface for wake word scoring models."""

    @abstractmethod
    def predict(self, frame_int16: np.ndarray) -> dict[str, float]:
        """Score 16 kHz int16 audio frame (typically 1280 samples / 80ms)."""
        pass


class OpenWakeWordDetector(WakeWordDetector):
    """Wake word scoring powered by openWakeWord (ONNX runtime)."""

    def __init__(
        self,
        model_name: str = "hey_jarvis_v0.1.onnx",
        inference_framework: str = "onnx",
    ) -> None:
        self.model_name = model_name
        self.inference_framework = inference_framework
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from openwakeword.model import Model
            logger.info(f"Loading openWakeWord model '{self.model_name}' ({self.inference_framework})...")
            self._model = Model(
                wakeword_models=[self.model_name],
                inference_framework=self.inference_framework,
            )
        return self._model

    def predict(self, frame_int16: np.ndarray) -> dict[str, float]:
        model = self._ensure_model()
        return model.predict(frame_int16)


class MockWakeWordDetector(WakeWordDetector):
    """Mock wake word detector for deterministic offline unit tests."""

    def __init__(self, scores: list[dict[str, float]] | None = None) -> None:
        self.scores = list(scores or [])

    def predict(self, frame_int16: np.ndarray) -> dict[str, float]:
        if self.scores:
            return self.scores.pop(0)
        return {}


class WakeWordTrigger(VoiceTrigger):
    """Wake word detector (openWakeWord) with silence VAD to end utterances."""

    def __init__(
        self,
        capture: AudioCapture,
        wake_word: str = "hey_jarvis",
        wake_threshold: float = 0.5,
        silence_timeout_seconds: float = 1.5,
        energy_threshold: float = 0.015,
        max_utterance_seconds: float = 15.0,
        detector: WakeWordDetector | None = None,
        on_wake_detected: Callable[[], None] | None = None,
    ) -> None:
        self.capture = capture
        self.wake_word = wake_word
        self.wake_threshold = wake_threshold
        self.silence_timeout_seconds = silence_timeout_seconds
        self.energy_threshold = energy_threshold
        self.max_utterance_seconds = max_utterance_seconds
        self.detector = detector if detector is not None else OpenWakeWordDetector(wake_word)
        self.on_wake_detected = on_wake_detected

    def _matches_wake_word(self, scores: dict[str, float]) -> bool:
        """Check if any returned score for target wake word exceeds threshold."""
        target = self.wake_word.lower().replace(".onnx", "").replace(".tflite", "")
        for key, score in scores.items():
            k_lower = key.lower()
            if target in k_lower and score >= self.wake_threshold:
                logger.info(f"Wake word '{key}' matched with score {score:.3f} >= {self.wake_threshold}")
                return True
        return False

    def listen_for_input(self, timeout: float | None = None) -> np.ndarray:
        """Continuously listen for wake word, then record speech until silence timeout."""
        frame_queue: queue.Queue[np.ndarray] = queue.Queue()

        def audio_callback(chunk: np.ndarray) -> None:
            frame_queue.put(chunk)

        self.capture.start_stream(audio_callback, blocksize=1280)
        start_time = time.time()
        wake_detected = False

        try:
            # Stage 1: Listen for wake word
            logger.info(f"Listening for wake word '{self.wake_word}'...")
            while not wake_detected:
                if timeout is not None and (time.time() - start_time) > timeout:
                    logger.debug("Wake word listener timed out.")
                    return np.zeros(0, dtype=np.float32)

                try:
                    chunk = frame_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if len(chunk) < 1280:
                    continue

                # openWakeWord strictly requires 16-bit PCM integer samples
                chunk_int16 = (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16)
                scores = self.detector.predict(chunk_int16)

                if self._matches_wake_word(scores):
                    wake_detected = True
                    if self.on_wake_detected:
                        self.on_wake_detected()
                    break

            # Stage 2: Wake word was detected, record speech until silence timeout
            logger.info("Wake word detected! Recording utterance until silence...")
            utterance_chunks: list[np.ndarray] = []
            silence_time = 0.0
            had_voice = False
            utterance_start = time.time()
            chunk_duration = 1280 / self.capture.sample_rate

            while True:
                elapsed_utterance = time.time() - utterance_start
                if elapsed_utterance >= self.max_utterance_seconds:
                    logger.info("Maximum utterance duration reached.")
                    break

                try:
                    chunk = frame_queue.get(timeout=0.2)
                except queue.Empty:
                    silence_time += 0.2
                    if had_voice and silence_time >= self.silence_timeout_seconds:
                        break
                    continue

                utterance_chunks.append(chunk)

                # Compute frame RMS energy for Voice Activity / Silence detection
                rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) > 0 else 0.0
                if rms >= self.energy_threshold:
                    had_voice = True
                    silence_time = 0.0
                else:
                    silence_time += chunk_duration

                # If speech was heard and silence follows for silence_timeout_seconds, stop
                if had_voice and silence_time >= self.silence_timeout_seconds:
                    logger.info(f"Silence timeout ({self.silence_timeout_seconds}s) reached after speech.")
                    break

                # If no speech was heard at all for twice the silence timeout, abort
                if not had_voice and silence_time >= (self.silence_timeout_seconds * 2):
                    logger.info("No speech detected following wake word.")
                    break

            if not utterance_chunks:
                return np.zeros(0, dtype=np.float32)

            return np.concatenate(utterance_chunks)

        finally:
            self.capture.stop_stream()
