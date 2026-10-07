"""Tests for Desktop Screen Vision integration and visual awareness prompt."""

from __future__ import annotations

from pathlib import Path
import pytest

from jarvis.brain.prompts import get_jarvis_system_prompt
from jarvis.tools.gui import create_gui_tools
from jarvis.core.config import JarvisConfig, load_config


def test_system_prompt_includes_visual_awareness() -> None:
    """J.A.R.V.I.S. system prompt instructs model on visual desktop awareness and take_screenshot tool."""
    prompt = get_jarvis_system_prompt("Sir")
    assert "VISUAL DESKTOP AWARENESS:" in prompt
    assert "take_screenshot" in prompt
    assert "look at their screen" in prompt


def test_hud_html_contains_screen_vision_button() -> None:
    """Web HUD contains the 1-click ANALYZE SCREEN button in the input bar."""
    index_html = Path("src/jarvis/ui/static/index.html").read_text(encoding="utf-8")
    assert "screen-vision-btn" in index_html
    assert "SCREEN" in index_html


def test_gui_tools_include_take_screenshot() -> None:
    """GUI tools register take_screenshot when supports_images=True."""
    cfg = load_config("config.yaml")
    tools = create_gui_tools(cfg, supports_images=True)
    tool_names = [t.name for t in tools]
    assert "take_screenshot" in tool_names
