"""Comprehensive offline unit and acceptance tests for Jarvis Voice Channel."""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any
from unittest.mock import MagicMock
import numpy as np
import pytest

from jarvis.brain.base import BrainResponse
from jarvis.brain.fake_brain import FakeBrain
from jarvis.core.config import BrainConfig, JarvisConfig, VoiceConfig
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.types import RiskTier, SessionState, ToolCall, ToolResult, ToolSpec
from jarvis.safety.audit import AuditLogger
from jarvis.safety.killswitch import KillSwitch
from jarvis.tools.files_write import create_create_text_file_tool
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system_tools import create_system_info_tool
from jarvis.voice.audio_in import AudioCapture
from jarvis.voice.channel import VoiceChannel, parse_spoken_confirmation
from jarvis.voice.stt import FasterWhisperSTT, MockSTT
from jarvis.voice.triggers import (
    MockWakeWordDetector,
    PushToTalkTrigger,
    VoiceTrigger,
    WakeWordTrigger,
    match_key,
)
from jarvis.voice.tts import MockTTS, Pyttsx3TTS, clean_text_for_speech


class DummyTrigger(VoiceTrigger):
    """Deterministic voice trigger returning pre-canned audio samples."""

    def __init__(self, samples: list[np.ndarray] | None = None) -> None:
        self.samples = list(samples or [])
        self.call_count = 0

    def listen_for_input(self, timeout: float | None = None) -> np.ndarray:
        self.call_count += 1
        if self.samples:
            return self.samples.pop(0)
        return np.zeros(0, dtype=np.float32)


# =========================================================================
# 1. Spoken Confirmation Parser Tests
# =========================================================================

@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("yes confirm", True),
        ("Yes confirm", True),
        ("yes, confirm!", True),
        ("yes confirm.", True),
        ("  YES CONFIRM  ", True),
        ("no", False),
        ("No", False),
        ("no.", False),
        ("NO!", False),
        ("yes", None),            # Strict: 'yes' alone is NOT approved
        ("confirm", None),        # Strict: 'confirm' alone is NOT approved
        ("yes please", None),
        ("sure", None),
        ("ok", None),
        ("cancel", None),
        ("blah blah", None),
        ("", None),
        ("   ", None),
    ],
)
def test_parse_spoken_confirmation(phrase: str, expected: bool | None) -> None:
    """Verify parse_spoken_confirmation enforces strict 'yes confirm' or 'no' rules."""
    assert parse_spoken_confirmation(phrase) == expected


# =========================================================================
# 2. Key Matching Helper Tests
# =========================================================================

def test_match_key_special_and_char_keys() -> None:
    """Verify match_key accurately matches pynput Key enum and KeyCode instances."""
    from pynput.keyboard import Key, KeyCode

    assert match_key(Key.space, "space") is True
    assert match_key(Key.enter, "enter") is True
    assert match_key(Key.ctrl_l, "ctrl") is True
    assert match_key(Key.ctrl_r, "ctrl") is True
    assert match_key(Key.alt_l, "alt") is True
    assert match_key(Key.shift_r, "shift") is True
    assert match_key(KeyCode.from_char("a"), "a") is True
    assert match_key(KeyCode.from_char("A"), "a") is True

    # Non-matching
    assert match_key(Key.space, "enter") is False
    assert match_key(Key.esc, "space") is False
    assert match_key(None, "space") is False


# =========================================================================
# 3. VoiceChannel Confirmation Tests
# =========================================================================

def test_voice_confirmation_simple_approve() -> None:
    """Spoken 'yes confirm' on normal action without dual factor approves."""
    tts = MockTTS()
    stt = MockSTT(responses=["yes confirm"])
    # Dummy non-empty audio frame
    audio_sample = np.ones(1600, dtype=np.float32)
    trigger = DummyTrigger(samples=[audio_sample])

    channel = VoiceChannel(
        stt=stt,
        tts=tts,
        trigger=trigger,
    )

    result = channel.confirm("Create a text note in notes folder")
    assert result is True
    assert len(tts.spoken_texts) == 1
    assert "Proposed action: Create a text note in notes folder" in tts.spoken_texts[0]


def test_voice_confirmation_simple_deny() -> None:
    """Spoken 'no' denies the action."""
    tts = MockTTS()
    stt = MockSTT(responses=["no"])
    audio_sample = np.ones(1600, dtype=np.float32)
    trigger = DummyTrigger(samples=[audio_sample])

    channel = VoiceChannel(
        stt=stt,
        tts=tts,
        trigger=trigger,
    )

    result = channel.confirm("Create a text note in notes folder")
    assert result is False


def test_voice_confirmation_unrecognized_speech_denied() -> None:
    """Spoken 'yes' (without confirm) or noise is strictly denied."""
    tts = MockTTS()
    # First attempt: user says 'yes'
    stt1 = MockSTT(responses=["yes"])
    trigger1 = DummyTrigger(samples=[np.ones(1600, dtype=np.float32)])
    channel1 = VoiceChannel(stt=stt1, tts=tts, trigger=trigger1)
    assert channel1.confirm("Create note") is False

    # Second attempt: user says 'maybe later'
    stt2 = MockSTT(responses=["maybe later"])
    trigger2 = DummyTrigger(samples=[np.ones(1600, dtype=np.float32)])
    channel2 = VoiceChannel(stt=stt2, tts=tts, trigger=trigger2)
    assert channel2.confirm("Create note") is False


def test_voice_confirmation_timeout_denied() -> None:
    """Timeout resulting in empty audio strictly denies confirmation."""
    tts = MockTTS()
    stt = MockSTT(responses=[""])
    trigger = DummyTrigger(samples=[np.zeros(0, dtype=np.float32)])  # Timed out

    channel = VoiceChannel(stt=stt, tts=tts, trigger=trigger)
    assert channel.confirm("Create note") is False


# =========================================================================
# 4. Dual-Factor Voice Confirmation Tests
# =========================================================================

def test_voice_confirmation_dual_factor_overwrite() -> None:
    """Action with 'overwrite' requires spoken 'yes confirm' AND physical key press."""
    tts = MockTTS()
    audio_sample = np.ones(1600, dtype=np.float32)

    # Case A: User said 'yes confirm' but key was NOT pressed -> DENIED
    stt_a = MockSTT(responses=["yes confirm"])
    trigger_a = DummyTrigger(samples=[audio_sample])
    channel_a = VoiceChannel(
        stt=stt_a,
        tts=tts,
        trigger=trigger_a,
        dual_event_hook=lambda evt: None,  # Key not pressed
    )
    assert channel_a.confirm("Create file todo.txt. Overwrites: yes.") is False

    # Case B: User said 'yes confirm' AND key WAS pressed -> APPROVED
    stt_b = MockSTT(responses=["yes confirm"])
    trigger_b = DummyTrigger(samples=[audio_sample])
    channel_b = VoiceChannel(
        stt=stt_b,
        tts=tts,
        trigger=trigger_b,
        dual_event_hook=lambda evt: evt.set(),  # Key pressed
    )
    assert channel_b.confirm("Create file todo.txt. Overwrites: yes.") is True


def test_voice_confirmation_dual_factor_move() -> None:
    """Action with 'move' requires dual-factor physical key press."""
    tts = MockTTS()
    audio_sample = np.ones(1600, dtype=np.float32)

    # Key not pressed -> False
    stt1 = MockSTT(responses=["yes confirm"])
    trigger1 = DummyTrigger(samples=[audio_sample])
    channel1 = VoiceChannel(stt=stt1, tts=tts, trigger=trigger1, dual_event_hook=lambda evt: None)
    assert channel1.confirm("Move C:/a.txt -> C:/b.txt") is False

    # Key pressed -> True
    stt2 = MockSTT(responses=["yes confirm"])
    trigger2 = DummyTrigger(samples=[audio_sample])
    channel2 = VoiceChannel(stt=stt2, tts=tts, trigger=trigger2, dual_event_hook=lambda evt: evt.set())
    assert channel2.confirm("Move C:/a.txt -> C:/b.txt") is True


def test_voice_confirmation_dual_factor_tainted_warning() -> None:
    """Tainted session warning triggers dual-factor physical key press requirement."""
    tts = MockTTS()
    audio_sample = np.ones(1600, dtype=np.float32)
    taint_warn = "Untrusted external content was read this turn. The session is tainted."

    # Key not pressed -> False
    stt1 = MockSTT(responses=["yes confirm"])
    trigger1 = DummyTrigger(samples=[audio_sample])
    channel1 = VoiceChannel(stt=stt1, tts=tts, trigger=trigger1, dual_event_hook=lambda evt: None)
    assert channel1.confirm("Create file safe.txt", warning=taint_warn) is False

    # Key pressed -> True
    stt2 = MockSTT(responses=["yes confirm"])
    trigger2 = DummyTrigger(samples=[audio_sample])
    channel2 = VoiceChannel(stt=stt2, tts=tts, trigger=trigger2, dual_event_hook=lambda evt: evt.set())
    assert channel2.confirm("Create file safe.txt", warning=taint_warn) is True


# =========================================================================
# 5. Microphone Muting during TTS Playback
# =========================================================================

def test_tts_playback_disables_microphone_capture() -> None:
    """Audio capture must be muted during TTS speech to prevent echo/feedback."""
    capture = AudioCapture()
    assert capture.is_muted is False

    observed_muted_state: list[bool] = []

    class CapturingTTS(MockTTS):
        def _speak_impl(self, text: str) -> None:
            super()._speak_impl(text)
            observed_muted_state.append(capture.is_muted)

    tts = CapturingTTS()
    tts.set_capture_muter(capture.set_muted)

    tts.speak("Hello, I am Jarvis.")

    assert len(observed_muted_state) == 1
    assert observed_muted_state[0] is True   # Muted while speaking
    assert capture.is_muted is False         # Restored afterwards


# =========================================================================
# 6. Triggers Unit Tests
# =========================================================================

def test_push_to_talk_timeout() -> None:
    """PTT listener returns empty audio array when key is never pressed."""
    capture = AudioCapture()
    trigger = PushToTalkTrigger(capture=capture, key_name="space")
    audio = trigger.listen_for_input(timeout=0.01)
    assert len(audio) == 0


def test_wake_word_trigger_mock() -> None:
    """WakeWordTrigger detects wake word and accumulates frames until silence."""
    capture = AudioCapture(sample_rate=16000)

    # Create dummy frames:
    # 1. Silence frame (score 0.1)
    # 2. Wake frame (score 0.8) -> trigger wake word
    # 3. Speech frame (energy high)
    # 4. Silence frame (energy 0) -> silence timeout
    f_silence = np.zeros(1280, dtype=np.float32)
    f_speech = np.ones(1280, dtype=np.float32) * 0.1

    scores = [
        {"hey_jarvis_v0.1.onnx": 0.1},
        {"hey_jarvis_v0.1.onnx": 0.85},
    ]
    mock_detector = MockWakeWordDetector(scores=scores)

    # Mock capture start_stream to feed these frames to the callback
    def mock_start_stream(callback: Any, blocksize: int = 1280) -> None:
        def stream_feeder() -> None:
            callback(f_silence)
            callback(f_silence)  # wake frame
            callback(f_speech)   # voice
            callback(f_silence)  # silence 1
            callback(f_silence)  # silence 2

        t = threading.Thread(target=stream_feeder, daemon=True)
        t.start()

    capture.start_stream = mock_start_stream  # type: ignore[method-assign]
    capture.stop_stream = lambda: None       # type: ignore[method-assign]

    trigger = WakeWordTrigger(
        capture=capture,
        wake_word="hey_jarvis",
        wake_threshold=0.5,
        silence_timeout_seconds=0.05,
        energy_threshold=0.01,
        detector=mock_detector,
    )

    audio = trigger.listen_for_input(timeout=2.0)
    assert len(audio) > 0


# =========================================================================
# 7. Phase 4 Acceptance Criteria Tests
# =========================================================================

def test_acceptance_ptt_disk_space_answered_aloud(tmp_path: Path) -> None:
    """Acceptance: in push-to-talk mode, 'how much disk space do I have?' is answered aloud."""
    audit_log = tmp_path / "audit.log"
    config = JarvisConfig(
        allowed_roots=[tmp_path],
        brain=BrainConfig(provider="fake", model="fake-model"),
        audit_log_path=audit_log,
        voice=VoiceConfig(stt_model="base", tts_engine="pyttsx3"),
    )

    registry = ToolRegistry()
    registry.register(create_system_info_tool())

    # User query utterance
    audio_sample = np.ones(1600, dtype=np.float32)
    stt = MockSTT(responses=["how much disk space do I have?"])
    tts = MockTTS()
    trigger = DummyTrigger(samples=[audio_sample])

    voice_channel = VoiceChannel(
        stt=stt,
        tts=tts,
        trigger=trigger,
        config=config,
    )

    # Script fake brain: calls system_info then returns spoken answer
    script = [
        BrainResponse(
            text=None,
            tool_calls=[ToolCall(id="call_sys", name="system_info", arguments={})],
        ),
        BrainResponse(
            text="You have 120 GB of free disk space available.",
            tool_calls=[],
        ),
    ]
    brain = FakeBrain(responses=script)

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        config=config,
        audit_logger=AuditLogger(audit_log),
        kill_switch=KillSwitch(),
        confirmer=voice_channel,
    )

    # 1. PTT capture input
    query_text = voice_channel.get_user_input()
    assert query_text == "how much disk space do I have?"

    # 2. Run turn
    turn_response = orchestrator.run_turn(query_text)
    assert "120 GB" in turn_response

    # 3. Answer aloud via voice channel
    voice_channel.say(turn_response)

    # Verify response was spoken aloud via TTS
    assert len(tts.spoken_texts) >= 1
    assert "120 GB of free disk space" in tts.spoken_texts[-1]


def test_acceptance_file_creation_spoken_confirmation_denial(tmp_path: Path) -> None:
    """Acceptance: a file-creation request asks for spoken confirmation and respects 'no'."""
    audit_log = tmp_path / "audit.log"
    target_file = tmp_path / "todo.txt"
    config = JarvisConfig(
        allowed_roots=[tmp_path],
        brain=BrainConfig(provider="fake", model="fake-model"),
        audit_log_path=audit_log,
        voice=VoiceConfig(),
    )

    registry = ToolRegistry()
    registry.register(create_create_text_file_tool(config.allowed_roots))

    # User spoken confirmation: 'no'
    audio_sample = np.ones(1600, dtype=np.float32)
    stt = MockSTT(responses=["no"])
    tts = MockTTS()
    trigger = DummyTrigger(samples=[audio_sample])

    voice_channel = VoiceChannel(
        stt=stt,
        tts=tts,
        trigger=trigger,
        config=config,
    )

    script = [
        BrainResponse(
            text=None,
            tool_calls=[
                ToolCall(
                    id="call_create",
                    name="create_text_file",
                    arguments={"path": str(target_file), "content": "1. Buy milk"},
                )
            ],
        ),
        BrainResponse(text="File creation was cancelled by the user.", tool_calls=[]),
    ]
    brain = FakeBrain(responses=script)

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        config=config,
        audit_logger=AuditLogger(audit_log),
        kill_switch=KillSwitch(),
        confirmer=voice_channel,
    )

    response = orchestrator.run_turn("create a todo.txt file")

    # 1. Voice channel asked for confirmation aloud
    assert len(tts.spoken_texts) >= 1
    assert any("Proposed action:" in t for t in tts.spoken_texts)

    # 2. File was NOT created on disk
    assert not target_file.exists()

    # 3. Audit log records the denial
    audit_content = audit_log.read_text(encoding="utf-8")
    assert "create_text_file" in audit_content
    assert "failed" in audit_content
    assert "User denied confirmation" in audit_content


def test_acceptance_file_creation_spoken_confirmation_approval(tmp_path: Path) -> None:
    """Acceptance: a file-creation request asks for spoken confirmation and approves on 'yes confirm'."""
    audit_log = tmp_path / "audit.log"
    target_file = tmp_path / "todo.txt"
    config = JarvisConfig(
        allowed_roots=[tmp_path],
        brain=BrainConfig(provider="fake", model="fake-model"),
        audit_log_path=audit_log,
        voice=VoiceConfig(),
    )

    registry = ToolRegistry()
    registry.register(create_create_text_file_tool(config.allowed_roots))

    # User spoken confirmation: 'yes confirm'
    audio_sample = np.ones(1600, dtype=np.float32)
    stt = MockSTT(responses=["yes confirm"])
    tts = MockTTS()
    trigger = DummyTrigger(samples=[audio_sample])

    voice_channel = VoiceChannel(
        stt=stt,
        tts=tts,
        trigger=trigger,
        config=config,
    )

    script = [
        BrainResponse(
            text=None,
            tool_calls=[
                ToolCall(
                    id="call_create",
                    name="create_text_file",
                    arguments={"path": str(target_file), "content": "1. Buy milk"},
                )
            ],
        ),
        BrainResponse(text="Note file todo.txt created successfully.", tool_calls=[]),
    ]
    brain = FakeBrain(responses=script)

    orchestrator = Orchestrator(
        brain=brain,
        tool_registry=registry,
        config=config,
        audit_logger=AuditLogger(audit_log),
        kill_switch=KillSwitch(),
        confirmer=voice_channel,
    )

    response = orchestrator.run_turn("create a todo.txt file")

    # 1. Confirmation was prompted aloud
    assert any("Proposed action:" in t for t in tts.spoken_texts)

    # 2. File was created on disk
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "1. Buy milk"

    # 3. Audit log records successful execution
    audit_content = audit_log.read_text(encoding="utf-8")
    assert "executed" in audit_content


# =========================================================================
# 8. FasterWhisperSTT Language Configuration Tests
# =========================================================================

def test_faster_whisper_stt_language_configuration() -> None:
    """FasterWhisperSTT initializes with language='en' by default and passes it to model.transcribe."""
    stt = FasterWhisperSTT(model_size="base", language="en")
    assert stt.language == "en"

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "Hello Jarvis"
    mock_model.transcribe.return_value = ([mock_segment], None)

    stt._model = mock_model
    audio_sample = np.ones(1600, dtype=np.float32)
    result = stt.transcribe(audio_sample)

    assert result == "Hello Jarvis"
    mock_model.transcribe.assert_called_once()
    _, kwargs = mock_model.transcribe.call_args
    assert kwargs.get("language") == "en"
    assert kwargs.get("beam_size") == 5
    assert kwargs.get("vad_filter") is True


def test_faster_whisper_stt_empty_audio_returns_empty_without_model() -> None:
    """FasterWhisperSTT returns empty string immediately on empty audio without loading model."""
    stt = FasterWhisperSTT(model_size="base", language="en")
    result = stt.transcribe(np.zeros(0, dtype=np.float32))
    assert result == ""
    assert stt._model is None


def test_faster_whisper_stt_custom_or_none_language() -> None:
    """FasterWhisperSTT handles custom language or None (auto-detect)."""
    stt_none = FasterWhisperSTT(language=None)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], None)
    stt_none._model = mock_model

    audio_sample = np.ones(1600, dtype=np.float32)
    stt_none.transcribe(audio_sample)

    _, kwargs = mock_model.transcribe.call_args
    assert "language" not in kwargs


# =========================================================================
# 9. TTS Text Cleaning and Output Device Tests
# =========================================================================

def test_clean_text_for_speech_markdown_stripping() -> None:
    """clean_text_for_speech strips markdown bold, links, code blocks, and symbols."""
    raw = "**Hello**, check [my site](https://example.com) and `print('code')`.\n```python\nx = 1\n```\n- Point 1\n- Point 2"
    cleaned = clean_text_for_speech(raw)
    assert "**" not in cleaned
    assert "[" not in cleaned
    assert "https://example.com" not in cleaned
    assert "my site" in cleaned
    assert "print('code')" in cleaned
    assert "code block omitted" in cleaned
    assert "Point 1 Point 2" in cleaned


def test_clean_text_for_speech_unicode_quotes() -> None:
    """clean_text_for_speech normalizes smart quotes and dashes."""
    raw = '“Hello world” — ‘test’'
    cleaned = clean_text_for_speech(raw)
    assert '“' not in cleaned
    assert '”' not in cleaned
    assert '‘' not in cleaned
    assert '’' not in cleaned
    assert '"Hello world"' in cleaned
    assert "'test'" in cleaned


def test_pyttsx3_tts_device_configuration() -> None:
    """Pyttsx3TTS accepts device parameter and stores it."""
    tts_default = Pyttsx3TTS()
    assert tts_default.device is None

    tts_custom = Pyttsx3TTS(device="Speakers")
    assert tts_custom.device == "Speakers"


def test_audio_capture_list_output_devices() -> None:
    """AudioCapture.list_output_devices returns list of output device dicts."""
    devices = AudioCapture.list_output_devices()
    assert isinstance(devices, list)
    for dev in devices:
        assert "name" in dev
        assert "channels" in dev
        assert dev["channels"] > 0


