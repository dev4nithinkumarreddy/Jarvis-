"""Tests for J.A.R.V.I.S. Standalone Native Desktop Application."""

import queue
import time
from unittest.mock import MagicMock, patch
import pytest

from jarvis.ui.native_app import JarvisNativeApp, NativeAppChannel


@pytest.fixture(scope="module")
def app():
    """Create a single JarvisNativeApp instance for the test module."""
    with patch("customtkinter.CTk.mainloop"):
        instance = JarvisNativeApp(
            on_user_submit=MagicMock(),
            user_title="Sir",
            model_name="Groq LLaMA 3.3 70B",
        )
        yield instance
        try:
            instance.update_idletasks()
            instance.destroy()
        except Exception:
            pass


def test_native_app_init(app):
    """Verify JarvisNativeApp initializes widgets and channels cleanly."""
    assert app.user_title == "Sir"
    assert app.channel is not None
    assert app.state_pill is not None


def test_native_app_channel_say_and_state(app):
    """Verify NativeAppChannel posts messages and alters status."""
    app.channel.say("All systems online, Sir.")
    app.set_state("THINKING")
    app.update()
    app.set_state("ONLINE")
    app.update()


def test_native_app_summon(app):
    """Verify native app summon unminimizes and brings window to focus."""
    with patch.object(app, "deiconify") as mock_deicon, \
         patch.object(app, "lift") as mock_lift, \
         patch.object(app, "focus_force") as mock_focus:
        mock_deicon.reset_mock()
        mock_lift.reset_mock()
        mock_focus.reset_mock()
        app.summon()
        app.update()
        assert mock_deicon.called
        assert mock_lift.called
        assert mock_focus.called
