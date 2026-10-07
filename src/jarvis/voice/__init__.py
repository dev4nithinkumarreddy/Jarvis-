"""Voice module for Jarvis: Audio Capture, STT, TTS, Triggers, and VoiceChannel."""

from __future__ import annotations

from jarvis.voice.audio_in import AudioCapture
from jarvis.voice.channel import VoiceChannel, parse_spoken_confirmation
from jarvis.voice.stt import FasterWhisperSTT, MockSTT, STTEngine
from jarvis.voice.triggers import (
    MockWakeWordDetector,
    OpenWakeWordDetector,
    PushToTalkTrigger,
    VoiceTrigger,
    WakeWordDetector,
    WakeWordTrigger,
    match_key,
)
from jarvis.voice.tts import MockTTS, PiperTTS, Pyttsx3TTS, TTSEngine, clean_text_for_speech

__all__ = [
    "AudioCapture",
    "FasterWhisperSTT",
    "MockSTT",
    "MockTTS",
    "clean_text_for_speech",
    "MockWakeWordDetector",
    "OpenWakeWordDetector",
    "PiperTTS",
    "PushToTalkTrigger",
    "Pyttsx3TTS",
    "STTEngine",
    "TTSEngine",
    "VoiceChannel",
    "VoiceTrigger",
    "WakeWordDetector",
    "WakeWordTrigger",
    "match_key",
    "parse_spoken_confirmation",
]
