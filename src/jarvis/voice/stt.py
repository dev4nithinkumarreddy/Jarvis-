"""Speech-to-Text (STT) transcription engine using faster-whisper."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import Any
import numpy as np

logger = logging.getLogger(__name__)


class STTEngine(ABC):
    """Abstract interface for Speech-to-Text transcription."""

    @abstractmethod
    def transcribe(self, audio_data: np.ndarray | str | Path) -> str:
        """Transcribe 16 kHz audio samples or audio file path into text.

        Args:
            audio_data: 1D numpy array of float32 samples or path to audio file.

        Returns:
            Transcribed text string.
        """
        pass


class FasterWhisperSTT(STTEngine):
    """Local Speech-to-Text engine powered by faster-whisper (CTranslate2)."""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = "en",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model: Any = None

    def _ensure_model(self) -> Any:
        """Lazy load faster-whisper model."""
        if self._model is None:
            import os
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            from faster_whisper import WhisperModel
            logger.info(f"Loading faster-whisper model '{self.model_size}' on {self.device} ({self.compute_type})...")
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def warmup(self, async_mode: bool = True) -> None:
        """Pre-load faster-whisper model into memory so first query is instantaneous."""
        if self._model is not None:
            return
        if async_mode:
            import threading
            threading.Thread(target=self._ensure_model, daemon=True).start()
        else:
            self._ensure_model()

    def transcribe(self, audio_data: np.ndarray | str | Path) -> str:
        """Transcribe audio into text using faster-whisper."""
        if isinstance(audio_data, np.ndarray) and len(audio_data) == 0:
            return ""

        model = self._ensure_model()
        transcribe_kwargs: dict[str, Any] = {
            "beam_size": 5,
            "vad_filter": True,
        }
        if self.language:
            transcribe_kwargs["language"] = self.language

        segments, _ = model.transcribe(
            audio_data,
            **transcribe_kwargs,
        )
        return " ".join(s.text.strip() for s in segments).strip()


class MockSTT(STTEngine):
    """Mock STT engine for fast, offline unit tests without model loading."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.history: list[Any] = []

    def transcribe(self, audio_data: np.ndarray | str | Path) -> str:
        self.history.append(audio_data)
        if self.responses:
            return self.responses.pop(0)
        return ""
