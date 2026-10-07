"""Tests for proactive Iron Man vocal greeting generation and persona initialization."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
import pytest

from jarvis.voice.channel import generate_jarvis_greeting


def test_generate_jarvis_greeting_morning() -> None:
    """Between 05:00 and 11:59, greeting is Good morning."""
    mock_morning = datetime(2026, 10, 7, 9, 30, 0)
    with patch("jarvis.voice.channel.datetime") as mock_dt:
        mock_dt.now.return_value = mock_morning
        greeting = generate_jarvis_greeting(user_title="Sir")
        assert "Good morning, Sir." in greeting
        assert "J.A.R.V.I.S. is at your service" in greeting


def test_generate_jarvis_greeting_afternoon() -> None:
    """Between 12:00 and 16:59, greeting is Good afternoon."""
    mock_afternoon = datetime(2026, 10, 7, 14, 15, 0)
    with patch("jarvis.voice.channel.datetime") as mock_dt:
        mock_dt.now.return_value = mock_afternoon
        greeting = generate_jarvis_greeting(user_title="Sir")
        assert "Good afternoon, Sir." in greeting
        assert "All Stark systems are online" in greeting


def test_generate_jarvis_greeting_evening() -> None:
    """After 17:00 or before 05:00, greeting is Good evening."""
    mock_evening = datetime(2026, 10, 7, 21, 45, 0)
    with patch("jarvis.voice.channel.datetime") as mock_dt:
        mock_dt.now.return_value = mock_evening
        greeting = generate_jarvis_greeting(user_title="Sir")
        assert "Good evening, Sir." in greeting
        assert "J.A.R.V.I.S. is at your service" in greeting


def test_generate_jarvis_greeting_custom_title() -> None:
    """Greeting respects custom user title from configuration."""
    mock_night = datetime(2026, 10, 7, 23, 0, 0)
    with patch("jarvis.voice.channel.datetime") as mock_dt:
        mock_dt.now.return_value = mock_night
        greeting = generate_jarvis_greeting(user_title="Mr. Stark")
        assert "Good evening, Mr. Stark." in greeting
