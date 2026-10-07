"""Tests for Piper neural voice model downloader and fallback synthesis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from jarvis.voice.models import DEFAULT_MODEL_KEY, VOICE_MODELS, setup_piper_voice
from jarvis.voice.tts import MockTTS, PiperTTS


def test_voice_models_catalog() -> None:
    """Piper voice models catalog contains alan British Butler model."""
    assert "alan" in VOICE_MODELS
    assert "en_GB-alan-medium.onnx" in VOICE_MODELS["alan"]["onnx_filename"]
    assert "en_GB-alan-medium.onnx.json" in VOICE_MODELS["alan"]["json_filename"]


def test_setup_piper_voice_dry(tmp_path: Path) -> None:
    """setup_piper_voice downloads files and updates config."""
    dummy_onnx = tmp_path / "en_GB-alan-medium.onnx"
    dummy_json = tmp_path / "en_GB-alan-medium.onnx.json"
    dummy_config = tmp_path / "config.yaml"
    dummy_config.write_text("voice:\n  tts_engine: pyttsx3\n", encoding="utf-8")

    with patch("jarvis.voice.models.download_file_with_progress") as mock_dl:
        def fake_download(url: str, dest: Path) -> None:
            dest.write_text("fake_model_data", encoding="utf-8")
        mock_dl.side_effect = fake_download

        onnx_res, json_res = setup_piper_voice(
            voice_key="alan",
            models_dir=tmp_path,
            config_path=dummy_config,
        )

        assert onnx_res == dummy_onnx
        assert json_res == dummy_json
        assert dummy_onnx.exists()
        assert dummy_json.exists()

        cfg_content = dummy_config.read_text(encoding="utf-8")
        assert "tts_engine: piper" in cfg_content


def test_piper_tts_fallback() -> None:
    """PiperTTS falls back gracefully to secondary TTSEngine when model is missing."""
    fallback = MockTTS()
    tts = PiperTTS(
        model_path="non_existent_model.onnx",
        config_path="non_existent_model.json",
        fallback_tts=fallback,
    )

    # Should not raise; should invoke fallback
    tts.speak("Good evening, Sir.")
    assert len(fallback.spoken_texts) == 1
    assert "Good evening, Sir." in fallback.spoken_texts[0]
