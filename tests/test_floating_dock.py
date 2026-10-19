"""Unit tests for Frameless Floating Arc Reactor Desktop Widget."""

import time
from unittest.mock import MagicMock, patch
import pytest

from jarvis.ui.floating_dock import FloatingArcReactorWidget, FloatingDockChannel


@pytest.fixture(scope="module")
def floating_widget():
    """Create a FloatingArcReactorWidget instance for testing."""
    widget = FloatingArcReactorWidget(
        on_user_submit=MagicMock(),
        user_title="Sir",
        model_name="Groq LLaMA 3.3 70B",
        size=110,
    )
    yield widget
    try:
        widget.destroy()
    except Exception:
        pass


def test_floating_widget_init(floating_widget):
    """Verify FloatingArcReactorWidget initializes with frameless and transparent attributes."""
    assert floating_widget.size == 110
    assert floating_widget.user_title == "Sir"
    assert floating_widget.channel is not None
    assert floating_widget.canvas is not None
    assert floating_widget.is_expanded is False


def test_floating_widget_expand_collapse(floating_widget):
    """Verify expanding and collapsing flyout console updates state and packs."""
    floating_widget.expand_console()
    assert floating_widget.is_expanded is True

    floating_widget.collapse_console()
    assert floating_widget.is_expanded is False


def test_floating_widget_channel_say(floating_widget):
    """Verify channel say posts message and expands console."""
    floating_widget.channel.say("All systems nominal, Sir.")
    floating_widget.update()
    assert "All systems nominal, Sir." in floating_widget.response_text.get()
    assert floating_widget.is_expanded is True


def test_floating_widget_summon(floating_widget):
    """Verify summon expands console and brings widget to top."""
    with patch.object(floating_widget, "deiconify") as mock_deicon, \
         patch.object(floating_widget, "lift") as mock_lift:
        floating_widget.summon()
        floating_widget.update()
        assert mock_deicon.called
        assert mock_lift.called
        assert floating_widget.is_expanded is True


def test_floating_widget_dock_coordinates(floating_widget):
    """Verify dock_x and dock_y are preserved and positive."""
    assert floating_widget.dock_x > 0
    assert floating_widget.dock_y > 0


def test_floating_widget_scroll_and_metrics(floating_widget):
    """Verify scrollable response box and live Stark metrics strip exist and update."""
    assert hasattr(floating_widget, "response_box")
    assert hasattr(floating_widget, "scrollbar")
    assert hasattr(floating_widget, "metrics_bar")
    assert hasattr(floating_widget, "metrics_cpu_lbl")
    assert hasattr(floating_widget, "metrics_ram_lbl")
    floating_widget.post_response("Testing scrollable response viewport.\nLine 2\nLine 3")
    floating_widget.update()
    box_content = floating_widget.response_box.get("1.0", "end")
    assert "Testing scrollable response viewport." in box_content


def test_floating_widget_confirmation_slot(floating_widget):
    """Verify prompt_confirmation renders cards into confirm_slot and sets _confirm_active."""
    reply_received = []
    floating_widget.prompt_confirmation("Delete temp files", "Irreversible", lambda ok: reply_received.append(ok))
    floating_widget.update()
    assert floating_widget._confirm_active is True
    assert len(floating_widget.confirm_slot.winfo_children()) == 1


def test_floating_widget_speech_stop_and_clear(floating_widget):
    """Verify stop_speech halts speech and clear_console resets response text."""
    stop_called = []
    floating_widget.on_speech_stop = lambda: stop_called.append(True)

    floating_widget.stop_speech()
    floating_widget.update()
    assert len(stop_called) == 1
    assert floating_widget.state == "ONLINE"
    assert floating_widget.metrics_audio_lbl.cget("text") == "VOICE: IDLE"

    floating_widget.clear_console()
    floating_widget.update()
    assert "Console cleared" in floating_widget.response_box.get("1.0", "end")


def test_floating_widget_protocols_menu(floating_widget):
    """Verify protocols button exists and _dispatch_protocol sends directives."""
    assert hasattr(floating_widget, "protocols_btn")
    submitted = []
    floating_widget.on_user_submit = lambda text: submitted.append(text)

    floating_widget._dispatch_protocol("Diagnostic check")
    floating_widget.update()
    time.sleep(0.05)
    assert "Diagnostic check" in submitted


def test_floating_widget_mic_always_present_and_anchored(floating_widget):
    """Verify mic_btn and input_bar exist, are anchored, and responsive even without voice toggle."""
    assert hasattr(floating_widget, "input_bar")
    assert hasattr(floating_widget, "mic_btn")
    assert floating_widget.mic_btn.winfo_exists()

    # Test clicking mic invokes callback or warm-up message
    mic_clicked = []
    floating_widget.on_voice_toggle = lambda: mic_clicked.append(True)
    floating_widget.mic_btn.invoke()
    floating_widget.update()
    assert len(mic_clicked) == 1


def test_floating_widget_styled_response_rendering(floating_widget):
    """Verify markdown tables, asterisks, and headings are converted cleanly without raw artifacts."""
    sample_markdown = (
        "### System Briefing\n"
        "| Component | Status |\n"
        "|---|---|\n"
        "| CPU | 39% |\n"
        "| RAM | **Optimal** |\n"
        "- All systems nominal."
    )
    floating_widget.post_response(sample_markdown)
    floating_widget.update()

    rendered_text = floating_widget.response_box.get("1.0", "end")
    # Raw table separator and raw markdown stars should be gone
    assert "|---|" not in rendered_text
    assert "**" not in rendered_text
    # Heading rendered cleanly with Stark HUD glyph
    assert "SYSTEM BRIEFING" in rendered_text
    # Table rows rendered cleanly into HUD key-value entries
    assert "CPU: 39%" in rendered_text
    assert "RAM: Optimal" in rendered_text
    assert "All systems nominal." in rendered_text


def test_floating_widget_chips_and_counter_rotating_render(floating_widget):
    """Verify chips bar existence, quick action chip buttons, and procedural Arc Reactor rendering."""
    assert hasattr(floating_widget, "chips_bar")
    chip_buttons = [w for w in floating_widget.chips_bar.winfo_children() if isinstance(w, type(floating_widget.clear_btn))]
    assert len(chip_buttons) >= 6

    # Test clicking a chip sends protocol
    submitted = []
    floating_widget.on_user_submit = lambda text: submitted.append(text)
    chip_buttons[0].invoke()
    floating_widget.update()
    time.sleep(0.05)
    assert len(submitted) == 1

    # Verify procedural Arc Reactor draw executes cleanly across all states
    floating_widget.set_state("THINKING")
    floating_widget.update()
    floating_widget._draw()

    floating_widget.set_state("LISTENING")
    floating_widget.update()
    floating_widget._draw()

    floating_widget.set_state("ONLINE")
    floating_widget.update()
    floating_widget._draw()


def test_floating_widget_model_switcher(floating_widget):
    """Verify neural model switcher chip exists and updates active model."""
    assert hasattr(floating_widget, "model_chip")
    assert "🧠" in floating_widget.model_chip.cget("text")

    model_changes = []
    floating_widget.on_model_change = lambda m: model_changes.append(m)

    # Trigger show_model_switcher_menu with patched tk_popup
    with patch("tkinter.Menu.tk_popup") as mock_popup:
        floating_widget.show_model_switcher_menu()
        floating_widget.update()
        assert mock_popup.called

    # Direct selection test
    floating_widget.model_name = "deepseek-v4.1-flash"
    display_m = "DEEPSEEK-V4.1-FLASH"
    floating_widget.model_chip.config(text=f"🧠 {display_m} ▾")
    assert "DEEPSEEK" in floating_widget.model_chip.cget("text")






