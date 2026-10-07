"""Microphone audio input capture using sounddevice."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioCapture:
    """16 kHz mono microphone capture with mute coordination for TTS playback."""

    def __init__(
        self,
        sample_rate: int = 16000,
        device: int | str | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._muted = False
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    @property
    def is_muted(self) -> bool:
        """Return True if capture is currently muted (e.g. while TTS is speaking)."""
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """Mute or unmute microphone audio capture."""
        with self._lock:
            self._muted = muted

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        """List available audio input devices."""
        devices = []
        try:
            device_list = sd.query_devices()
            for idx, dev in enumerate(device_list):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append({
                        "index": idx,
                        "name": dev.get("name", "Unknown"),
                        "channels": dev.get("max_input_channels", 0),
                        "default_samplerate": dev.get("default_samplerate", 16000),
                    })
        except Exception as exc:
            logger.warning(f"Error querying audio devices: {exc}")
        return devices

    @staticmethod
    def list_output_devices() -> list[dict[str, Any]]:
        """List available audio output devices."""
        devices = []
        try:
            device_list = sd.query_devices()
            for idx, dev in enumerate(device_list):
                if dev.get("max_output_channels", 0) > 0:
                    devices.append({
                        "index": idx,
                        "name": dev.get("name", "Unknown"),
                        "channels": dev.get("max_output_channels", 0),
                        "default_samplerate": dev.get("default_samplerate", 44100),
                    })
        except Exception as exc:
            logger.warning(f"Error querying audio output devices: {exc}")
        return devices

    def record_seconds(self, duration: float) -> np.ndarray:
        """Synchronously record audio for a specified duration in seconds.

        Returns:
            1D numpy array of float32 audio samples.
        """
        if self._muted:
            num_samples = int(duration * self.sample_rate)
            return np.zeros(num_samples, dtype=np.float32)

        try:
            num_samples = int(duration * self.sample_rate)
            audio = sd.rec(
                num_samples,
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=self.device,
            )
            sd.wait()
            return audio.flatten()
        except Exception as exc:
            logger.error(f"Error during audio recording: {exc}")
            return np.zeros(0, dtype=np.float32)

    def start_stream(self, callback: Callable[[np.ndarray], None], blocksize: int = 1280) -> None:
        """Start non-blocking background audio stream feeding frames to callback."""
        with self._lock:
            if self._stream is not None:
                return

            def _audio_callback(indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
                if status:
                    logger.debug(f"Audio stream status: {status}")
                if self._muted:
                    # Feed zeros or ignore when muted
                    callback(np.zeros(frames, dtype=np.float32))
                else:
                    callback(indata.flatten().copy())

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=self.device,
                blocksize=blocksize,
                callback=_audio_callback,
            )
            self._stream.start()

    def stop_stream(self) -> None:
        """Stop and close active background audio stream."""
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as exc:
                    logger.debug(f"Error closing audio stream: {exc}")
                self._stream = None
