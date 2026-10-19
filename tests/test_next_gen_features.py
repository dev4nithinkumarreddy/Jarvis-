"""Comprehensive test suite for Next-Gen features: Media Control, Web Search, OCR Vision, and Timers."""

from __future__ import annotations

import io
import time
from unittest.mock import MagicMock, patch
from PIL import Image, ImageDraw
import pytest

from jarvis.core.types import RiskTier
from jarvis.tools.gui import create_gui_tools
from jarvis.tools.system_tools import (
    create_media_control_tool,
    create_toggle_show_desktop_tool,
    media_control,
    toggle_show_desktop,
)
from jarvis.tools.timer import (
    cancel_timer,
    create_cancel_timer_tool,
    create_list_timers_tool,
    create_timer_tool,
    list_active_timers,
    set_timer,
)
from jarvis.tools.web_search import (
    create_latest_news_tool,
    create_web_search_tool,
    get_latest_news,
    web_search,
)


# =========================================================================
# 1. Media Control & Desktop Tools
# =========================================================================

def test_media_control_supported_actions():
    """media_control dispatches key events for valid media directives."""
    with patch("ctypes.windll.user32.keybd_event") as mock_key:
        res_play = media_control("play_pause")
        assert res_play["status"] == "success"
        assert mock_key.call_count == 2  # key down + key up

        res_next = media_control("next")
        assert res_next["status"] == "success"

        res_prev = media_control("previous")
        assert res_prev["status"] == "success"

        res_stop = media_control("stop")
        assert res_stop["status"] == "success"


def test_media_control_invalid_action():
    """media_control returns structured error on invalid command."""
    res = media_control("invalid_cmd")
    assert res["status"] == "error"
    assert "Unsupported media action" in res["error"]


def test_toggle_show_desktop():
    """toggle_show_desktop dispatches Windows + D key chords."""
    with patch("ctypes.windll.user32.keybd_event") as mock_key:
        res = toggle_show_desktop()
        assert res["status"] == "success"
        assert mock_key.call_count >= 4


def test_media_and_desktop_tool_specs():
    """ToolSpec factories return valid CONFIRM-tier specifications."""
    media_tool = create_media_control_tool()
    assert media_tool.name == "media_control"
    assert media_tool.risk_tier == RiskTier.CONFIRM

    desk_tool = create_toggle_show_desktop_tool()
    assert desk_tool.name == "show_desktop"
    assert desk_tool.risk_tier == RiskTier.CONFIRM


# =========================================================================
# 2. Web Search & News Tools
# =========================================================================

def test_web_search_empty_query():
    """web_search rejects blank queries gracefully."""
    res = web_search("   ")
    assert res["status"] == "error"
    assert "Query string cannot be empty" in res["error"]


def test_web_search_parsing_mocked():
    """web_search parses HTML snippets and extracts results correctly."""
    mock_html = (
        '<html><body>'
        '<a class="result__snippet" href="#">Artificial Intelligence advances in 2026.</a>'
        '<a class="result__url" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fai">example.com/ai</a>'
        '</body></html>'
    ).encode("utf-8")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_html
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = web_search("AI breakthroughs", max_results=3)
        assert res["status"] == "success"
        assert len(res["results"]) >= 1
        assert "Artificial Intelligence advances" in res["results"][0]["snippet"]
        assert "https://example.com/ai" in res["results"][0]["url"]


def test_get_latest_news_mocked():
    """get_latest_news triggers web search with news query."""
    with patch("jarvis.tools.web_search.web_search") as mock_ws:
        mock_ws.return_value = {"status": "success", "results": []}
        get_latest_news("technology")
        mock_ws.assert_called_once()
        assert "technology" in mock_ws.call_args[1]["query"]


def test_web_search_tool_specs():
    """ToolSpec factories return valid AUTO-tier specifications."""
    search_spec = create_web_search_tool()
    assert search_spec.name == "web_search"
    assert search_spec.risk_tier == RiskTier.AUTO
    assert search_spec.untrusted_output is True

    news_spec = create_latest_news_tool()
    assert news_spec.name == "get_latest_news"
    assert news_spec.risk_tier == RiskTier.AUTO
    assert news_spec.untrusted_output is True


# =========================================================================
# 3. Smart Timers & Butler Reminders
# =========================================================================

def test_set_and_cancel_timer():
    """set_timer creates a background timer and cancel_timer cancels it."""
    res = set_timer(seconds=60, label="Deep Work")
    assert res["status"] == "success"
    assert "Deep Work" in res["label"]
    tid = res["timer_id"]

    active = list_active_timers()
    assert active["count"] >= 1
    assert tid in active["timers"]

    cancel_res = cancel_timer("Deep Work")
    assert cancel_res["status"] == "success"
    assert "cancelled" in cancel_res["message"]


def test_timer_expiration_callback():
    """Timer invokes callback and alert tone upon completion."""
    invoked = []
    def _cb(msg):
        invoked.append(msg)

    with patch("jarvis.voice.triggers.play_ptt_tone") as mock_tone:
        set_timer(seconds=1, label="Quick Test", notify_callback=_cb)
        time.sleep(1.2)
        assert len(invoked) == 1
        assert "Quick Test" in invoked[0]
        mock_tone.assert_called_with("summon")


def test_timer_tool_specs():
    """Timer ToolSpecs return valid AUTO-tier specifications."""
    t_tool = create_timer_tool()
    assert t_tool.name == "set_timer"
    assert t_tool.risk_tier == RiskTier.AUTO

    c_tool = create_cancel_timer_tool()
    assert c_tool.name == "cancel_timer"

    l_tool = create_list_timers_tool()
    assert l_tool.name == "list_timers"


# =========================================================================
# 4. Screen OCR Vision Integration
# =========================================================================

def test_screen_ocr_extraction_integration():
    """take_screenshot extracts OCR text and includes it in ToolResult content."""
    # Create test image with text
    test_img = Image.new("RGB", (300, 100), color="white")
    buf = io.BytesIO()
    test_img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    mock_screen = MagicMock()
    mock_res = MagicMock()
    mock_res.image_bytes = img_bytes
    mock_res.original_width = 300
    mock_res.original_height = 100
    mock_res.scaled_width = 300
    mock_res.scaled_height = 100
    mock_res.scale_factor = 1.0
    mock_screen.capture_screen.return_value = mock_res

    mock_config = MagicMock()
    mock_config.safety.allowed_gui_actions = ["take_screenshot"]

    tools = create_gui_tools(
        config=mock_config,
        screen_capture=mock_screen,
        supports_images=True,
    )
    screenshot_tool = next(t for t in tools if t.name == "take_screenshot")

    with patch("winocr.recognize_pil") as mock_ocr:
        mock_ocr_result = MagicMock()
        mock_ocr_result.text = "Error 404: Terminal build failed"
        # asyncio.run mock
        async def _ret(*args):
            return mock_ocr_result
        mock_ocr.side_effect = _ret

        result = screenshot_tool.handler()
        assert result.ok is True
        assert "OCR Text Visible on Screen" in result.content
        assert "Error 404: Terminal build failed" in result.content
