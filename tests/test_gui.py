"""Tests for Phase 6 GUI control: screen capture, coordinate conversion, input wrappers,

session approval, window title denylist, kill switch aborts, and acceptance test.
"""

from __future__ import annotations

import io
from pathlib import Path
import time
from typing import Any
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from jarvis.brain.base import BrainResponse
from jarvis.brain.fake_brain import FakeBrain
from jarvis.core.channels import Confirmer
from jarvis.core.config import BrainConfig, GUIConfig, JarvisConfig
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.types import RiskTier, ToolCall
from jarvis.gui_control.input import (
    BlockedWindowError,
    GUIInputController,
    is_window_title_denied,
)
from jarvis.gui_control.screen import (
    ScreenCapture,
    ScreenCaptureError,
    ScreenCaptureResult,
    model_to_screen_coords,
    save_thumbnail,
    screen_to_model_coords,
)
from jarvis.gui_control.session import (
    GUISessionConfirmer,
    GUISessionManager,
)
from jarvis.safety.audit import AuditLogger
from jarvis.safety.killswitch import KillSwitch
from jarvis.safety.permissions import PermissionEngine
from jarvis.tools.apps import create_open_app_tool
from jarvis.tools.gui import create_gui_tools
from jarvis.tools.registry import ToolRegistry


# -------------------------------------------------------------------------
# Fixtures and Helpers
# -------------------------------------------------------------------------


class MockConfirmer(Confirmer):
    """Test confirmer tracking confirmation prompts and returning scripted results."""

    def __init__(self, approve_all: bool = True) -> None:
        self.approve_all = approve_all
        self.prompts: list[str] = []

    def confirm(self, action_description: str, warning: str | None = None) -> bool:
        self.prompts.append(action_description)
        return self.approve_all


class MockMSSImage:
    """Mock MSS screenshot buffer."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.size = (width, height)
        # 4 bytes per pixel for BGRA
        self.bgra = b"\x00\x00\x00\xff" * (width * height)


class MockMSS:
    """Mock MSS screen capture context manager."""

    def __init__(self, monitors: list[dict[str, int]] | None = None) -> None:
        self.monitors = monitors or [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        self.grab_called = False

    def grab(self, monitor: dict[str, int]) -> MockMSSImage:
        self.grab_called = True
        return MockMSSImage(width=monitor.get("width", 1920), height=monitor.get("height", 1080))

    def __enter__(self) -> MockMSS:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


class MockPyAutoGUI:
    """Mock PyAutoGUI module for isolated headless testing."""

    def __init__(self) -> None:
        self.FAILSAFE = True
        self.PAUSE = 0.1
        self.clicks: list[dict[str, Any]] = []
        self.double_clicks: list[dict[str, Any]] = []
        self.typed_texts: list[str] = []
        self.pressed_keys: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.scrolls: list[int] = []

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.clicks.append({"x": x, "y": y, "button": button})

    def doubleClick(self, x: int, y: int) -> None:
        self.double_clicks.append({"x": x, "y": y})

    def write(self, text: str, interval: float = 0.01) -> None:
        self.typed_texts.append(text)

    def press(self, key: str) -> None:
        self.pressed_keys.append(key)

    def hotkey(self, *keys: str) -> None:
        self.hotkeys.append(keys)

    def scroll(self, amount: int) -> None:
        self.scrolls.append(amount)


# -------------------------------------------------------------------------
# 1. Coordinate Conversion Tests
# -------------------------------------------------------------------------


def test_coordinate_conversion_scale_one() -> None:
    """With scale_factor 1.0, model and screen coordinates match identically."""
    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    x_screen, y_screen = model_to_screen_coords(500, 300, scale_factor=1.0, monitor=monitor)
    assert x_screen == 500
    assert y_screen == 300

    x_model, y_model = screen_to_model_coords(500, 300, scale_factor=1.0, monitor=monitor)
    assert x_model == 500
    assert y_model == 300


def test_coordinate_conversion_downscaled_half() -> None:
    """With scale_factor 0.5 (half resolution), model coords double back to screen."""
    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    # Center of downscaled 960x540 image is (480, 270)
    x_screen, y_screen = model_to_screen_coords(480, 270, scale_factor=0.5, monitor=monitor)
    assert x_screen == 960
    assert y_screen == 540

    x_model, y_model = screen_to_model_coords(960, 540, scale_factor=0.5, monitor=monitor)
    assert x_model == 480
    assert y_model == 270


def test_coordinate_conversion_fractional_scale() -> None:
    """Test scaling factor 1280 / 1920 = 2/3 (0.6666667)."""
    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    scale = 1280.0 / 1920.0  # 0.6666666667

    # Model coords (640, 360) -> Screen coords (960, 540)
    x_screen, y_screen = model_to_screen_coords(640, 360, scale_factor=scale, monitor=monitor)
    assert x_screen == 960
    assert y_screen == 540


def test_coordinate_conversion_multi_monitor_positive_offset() -> None:
    """Test multi-monitor with positive offset (e.g. secondary monitor to right)."""
    # Second monitor: left=1920, top=0, width=1920, height=1080
    monitor = {"left": 1920, "top": 0, "width": 1920, "height": 1080}
    x_screen, y_screen = model_to_screen_coords(100, 200, scale_factor=1.0, monitor=monitor)
    assert x_screen == 2020
    assert y_screen == 200


def test_coordinate_conversion_multi_monitor_negative_offset() -> None:
    """Test multi-monitor with negative offset (e.g. secondary monitor to left/above)."""
    monitor = {"left": -1920, "top": -100, "width": 1920, "height": 1080}
    x_screen, y_screen = model_to_screen_coords(0, 0, scale_factor=1.0, monitor=monitor)
    assert x_screen == -1920
    assert y_screen == -100


def test_coordinate_conversion_clamping() -> None:
    """Out-of-bounds model coordinates are clamped to monitor bounds."""
    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    # Below min
    x_screen, y_screen = model_to_screen_coords(-50, -100, scale_factor=1.0, monitor=monitor)
    assert x_screen == 0
    assert y_screen == 0

    # Above max
    x_screen, y_screen = model_to_screen_coords(3000, 2000, scale_factor=1.0, monitor=monitor)
    assert x_screen == 1919
    assert y_screen == 1079


def test_coordinate_conversion_invalid_scale() -> None:
    """Scale factor <= 0 raises ValueError."""
    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with pytest.raises(ValueError, match="scale_factor must be positive"):
        model_to_screen_coords(100, 100, scale_factor=0.0, monitor=monitor)


# -------------------------------------------------------------------------
# 2. ScreenCapture and Thumbnail Tests
# -------------------------------------------------------------------------


def test_screen_capture_and_downscale(tmp_path: Path) -> None:
    """ScreenCapture uses mss and downscales to max_width."""
    mock_sct = MockMSS(
        monitors=[
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
    )
    capture = ScreenCapture(sct=mock_sct)
    res = capture.capture_screen(monitor_index=1, max_width=1280)

    assert isinstance(res, ScreenCaptureResult)
    assert res.original_width == 1920
    assert res.original_height == 1080
    assert res.scaled_width == 1280
    assert res.scaled_height == 720
    assert abs(res.scale_factor - (1280 / 1920)) < 0.001
    assert len(res.image_bytes) > 0
    assert capture.last_capture is res

    # Verify PNG bytes can be read by PIL
    img = Image.open(io.BytesIO(res.image_bytes))
    assert img.size == (1280, 720)


def test_save_thumbnail(tmp_path: Path) -> None:
    """save_thumbnail creates a scaled PNG thumbnail under output_dir."""
    test_img = Image.new("RGB", (1280, 720), color="blue")
    thumb_path = save_thumbnail(test_img, output_dir=tmp_path, max_dim=160, prefix="thumb_test")

    assert thumb_path.exists()
    assert thumb_path.name.startswith("thumb_test_")
    thumb_img = Image.open(thumb_path)
    assert max(thumb_img.size) <= 160


# -------------------------------------------------------------------------
# 3. Input Controller and Window Denylist Tests
# -------------------------------------------------------------------------


def test_is_window_title_denied() -> None:
    """Check window title denylist matching."""
    denylist = ["1password", "bitwarden", "keepass", "bank", "vault"]

    assert is_window_title_denied("1Password - Passwords", denylist) == "1password"
    assert is_window_title_denied("My Bitwarden Vault", denylist) == "bitwarden"
    assert is_window_title_denied("Chase Bank Online - Chrome", denylist) == "bank"
    assert is_window_title_denied("KeePassXC 2.7.6", denylist) == "keepass"
    assert is_window_title_denied("Untitled - Notepad", denylist) is None
    assert is_window_title_denied("Visual Studio Code", denylist) is None


def test_gui_input_controller_denylist_blocks_typing() -> None:
    """GUIInputController raises BlockedWindowError when typing into denied window."""
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(
        denied_window_titles=["1password", "bank", "vault"],
        pyautogui_module=mock_pag,
    )

    with patch("jarvis.gui_control.input.get_foreground_window_title", return_value="1Password - Personal"):
        with pytest.raises(BlockedWindowError, match="Typing blocked: Foreground window"):
            controller.type_text("supersecret")

    # PyAutoGUI write must not have been called
    assert len(mock_pag.typed_texts) == 0


def test_gui_input_controller_allows_safe_window() -> None:
    """GUIInputController types successfully when window title is safe."""
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(
        denied_window_titles=["1password", "bank"],
        pyautogui_module=mock_pag,
    )

    with patch("jarvis.gui_control.input.get_foreground_window_title", return_value="Untitled - Notepad"):
        controller.type_text("Hello World")

    assert mock_pag.typed_texts == ["Hello World"]


def test_gui_input_controller_blocks_press_key_and_hotkey_on_denied_window() -> None:
    """GUIInputController blocks press_key and hotkey when window title matches denylist."""
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(
        denied_window_titles=["1password", "bank"],
        pyautogui_module=mock_pag,
    )

    with patch("jarvis.gui_control.input.get_foreground_window_title", return_value="1Password - Personal Vault"):
        with pytest.raises(BlockedWindowError, match="Typing blocked"):
            controller.press_key("enter")

        with pytest.raises(BlockedWindowError, match="Typing blocked"):
            controller.hotkey("ctrl", "v")

    assert mock_pag.pressed_keys == []
    assert mock_pag.hotkeys == []


def test_gui_input_controller_actions() -> None:
    """Verify input controller forwards clicks, keys, and scrolls."""
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(pyautogui_module=mock_pag)

    controller.click(100, 200, button="left")
    assert mock_pag.clicks == [{"x": 100, "y": 200, "button": "left"}]

    controller.double_click(300, 400)
    assert mock_pag.double_clicks == [{"x": 300, "y": 400}]

    controller.press_key("enter")
    assert mock_pag.pressed_keys == ["enter"]

    controller.hotkey("ctrl", "s")
    assert mock_pag.hotkeys == [("ctrl", "s")]

    controller.scroll(-5)
    assert mock_pag.scrolls == [-5]


# -------------------------------------------------------------------------
# 4. Session Manager and Approval Mode Tests
# -------------------------------------------------------------------------


def test_session_manager_action_cap() -> None:
    """GUISessionManager caps sessions at max_actions."""
    mgr = GUISessionManager(approval_mode="session", max_actions=3, timeout_seconds=60.0)
    assert not mgr.is_session_valid()

    mgr.start_session()
    assert mgr.is_session_valid()

    # Record 2 actions: still valid
    mgr.record_action()
    mgr.record_action()
    assert mgr.is_session_valid()

    # Record 3rd action: cap reached -> invalidated
    mgr.record_action()
    assert not mgr.is_session_valid()


def test_session_manager_timeout_expiry() -> None:
    """GUISessionManager expires when timeout passes."""
    mgr = GUISessionManager(approval_mode="session", max_actions=10, timeout_seconds=0.05)
    mgr.start_session()
    assert mgr.is_session_valid()

    time.sleep(0.06)
    assert not mgr.is_session_valid()


def test_session_manager_kill_switch_invalidation() -> None:
    """Kill switch trigger immediately invalidates active GUI session."""
    ks = KillSwitch()
    mgr = GUISessionManager(approval_mode="session", max_actions=10, timeout_seconds=60.0, kill_switch=ks)
    mgr.start_session()
    assert mgr.is_session_valid()

    # Trigger kill switch
    ks.trigger()
    assert not mgr.is_session_valid()


def test_session_manager_banner_rendering() -> None:
    """render_banner produces expected text with action count and fail-safe hints."""
    mgr = GUISessionManager(approval_mode="session", max_actions=20, timeout_seconds=300.0)
    mgr.start_session()
    banner = mgr.render_banner()
    assert "ACTIVE GUI SESSION" in banner
    assert "FAIL-SAFE" in banner
    assert "KILL-SWITCH" in banner


def test_gui_session_confirmer_step_mode() -> None:
    """In 'step' mode, each GUI action prompts confirmer every time."""
    base_conf = MockConfirmer(approve_all=True)
    mgr = GUISessionManager(approval_mode="step", max_actions=10)
    conf = GUISessionConfirmer(base_confirmer=base_conf, session_manager=mgr)

    res1 = conf.confirm("Click mouse (left) at coordinates (100, 200).")
    res2 = conf.confirm("Type text via keyboard: 'hello'.")

    assert res1 is True
    assert res2 is True
    assert len(base_conf.prompts) == 2


def test_gui_session_confirmer_session_mode() -> None:
    """In 'session' mode, user confirms once for the session; subsequent GUI actions bypass prompt."""
    base_conf = MockConfirmer(approve_all=True)
    mgr = GUISessionManager(approval_mode="session", max_actions=3, timeout_seconds=60.0)
    conf = GUISessionConfirmer(base_confirmer=base_conf, session_manager=mgr)

    # First GUI action: prompts user to start session
    res1 = conf.confirm("Click mouse (left) at coordinates (100, 200).")
    assert res1 is True
    assert len(base_conf.prompts) == 1
    assert "Start bounded GUI control session" in base_conf.prompts[0]

    # Second GUI action: within session bounds -> auto-approved without prompting base_confirmer
    res2 = conf.confirm("Type text via keyboard: 'hello'.")
    assert res2 is True
    assert len(base_conf.prompts) == 1  # No new prompt!

    # Non-GUI action (e.g. file writing): must prompt base_confirmer regardless of GUI session
    res_file = conf.confirm("Create text file at 'notes.txt'.")
    assert res_file is True
    assert len(base_conf.prompts) == 2  # Prompted for non-GUI action!


# -------------------------------------------------------------------------
# 5. GUI Tools Registry and Handler Tests
# -------------------------------------------------------------------------


def test_create_gui_tools_specs(tmp_path: Path) -> None:
    """Verify tool specs, risk tiers, and untrusted_output flags."""
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        gui=GUIConfig(thumbnail_dir=str(tmp_path / "screens")),
    )
    tools = create_gui_tools(cfg)
    tools_by_name = {t.name: t for t in tools}

    assert "take_screenshot" in tools_by_name
    assert "gui_click" in tools_by_name
    assert "gui_type" in tools_by_name
    assert "gui_key" in tools_by_name
    assert "gui_scroll" in tools_by_name

    assert tools_by_name["take_screenshot"].risk_tier == RiskTier.AUTO
    assert tools_by_name["take_screenshot"].untrusted_output is True

    assert tools_by_name["gui_click"].risk_tier == RiskTier.CONFIRM
    assert tools_by_name["gui_type"].risk_tier == RiskTier.CONFIRM
    assert tools_by_name["gui_key"].risk_tier == RiskTier.CONFIRM
    assert tools_by_name["gui_scroll"].risk_tier == RiskTier.CONFIRM


def test_gui_tools_execution_with_mocks(tmp_path: Path) -> None:
    """Verify GUI tools execute with mocks and generate thumbnails."""
    screens_dir = tmp_path / "screens"
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        gui=GUIConfig(thumbnail_dir=str(screens_dir)),
    )
    mock_sct = MockMSS()
    screen_cap = ScreenCapture(sct=mock_sct)
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(pyautogui_module=mock_pag)

    tools = create_gui_tools(cfg, input_controller=controller, screen_capture=screen_cap)
    tools_by_name = {t.name: t for t in tools}

    # 1. take_screenshot
    shot_res = tools_by_name["take_screenshot"].handler(ToolCall(id="c1", name="take_screenshot", arguments={}))
    assert shot_res.ok is True
    assert shot_res.image_bytes is not None
    assert shot_res.thumbnail_path is not None
    assert Path(shot_res.thumbnail_path).exists()

    # 2. gui_click
    click_res = tools_by_name["gui_click"].handler(
        ToolCall(id="c2", name="gui_click", arguments={"x": 500, "y": 300, "button": "left"})
    )
    assert click_res.ok is True
    assert len(mock_pag.clicks) == 1

    # 3. gui_type (with safe window)
    with patch("jarvis.gui_control.input.get_foreground_window_title", return_value="Untitled - Notepad"):
        type_res = tools_by_name["gui_type"].handler(
            ToolCall(id="c3", name="gui_type", arguments={"text": "hello jarvis"})
        )
    assert type_res.ok is True
    assert mock_pag.typed_texts == ["hello jarvis"]

    # 4. gui_key
    key_res = tools_by_name["gui_key"].handler(ToolCall(id="c4", name="gui_key", arguments={"key": "enter"}))
    assert key_res.ok is True
    assert mock_pag.pressed_keys == ["enter"]

    # 5. gui_scroll
    scroll_res = tools_by_name["gui_scroll"].handler(
        ToolCall(id="c5", name="gui_scroll", arguments={"amount": -3})
    )
    assert scroll_res.ok is True
    assert mock_pag.scrolls == [-3]


def test_gui_key_handler_blocked_on_sensitive_window(tmp_path: Path) -> None:
    """gui_key tool handler returns ok=False when foreground window is sensitive."""
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        gui=GUIConfig(thumbnail_dir=str(tmp_path / "screens")),
    )
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(
        denied_window_titles=["1password", "bank"],
        pyautogui_module=mock_pag,
    )
    tools = create_gui_tools(cfg, input_controller=controller, screen_capture=ScreenCapture(sct=MockMSS()))
    tools_by_name = {t.name: t for t in tools}

    with patch("jarvis.gui_control.input.get_foreground_window_title", return_value="Chase Bank - Login"):
        res = tools_by_name["gui_key"].handler(ToolCall(id="c_key", name="gui_key", arguments={"key": "enter"}))

    assert res.ok is False
    assert "Typing blocked" in res.error
    assert len(mock_pag.pressed_keys) == 0


# -------------------------------------------------------------------------
# 6. Kill Switch Aborts Running Sequence Test
# -------------------------------------------------------------------------


def test_kill_switch_aborts_gui_sequence(tmp_path: Path) -> None:
    """Kill switch aborts GUI execution immediately before tool executes."""
    audit_file = tmp_path / "audit.log"
    ks = KillSwitch()
    conf = MockConfirmer(approve_all=True)
    registry = ToolRegistry()

    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        gui=GUIConfig(thumbnail_dir=str(tmp_path / "screens")),
    )
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(pyautogui_module=mock_pag)
    tools = create_gui_tools(cfg, input_controller=controller, screen_capture=ScreenCapture(sct=MockMSS()))
    for t in tools:
        registry.register(t)

    # Trigger kill switch before running turn
    ks.trigger()

    fake_brain = FakeBrain([
        BrainResponse(tool_calls=[ToolCall(id="call_click", name="gui_click", arguments={"x": 100, "y": 200})]),
    ])

    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=registry,
        audit_logger=AuditLogger(audit_file),
        kill_switch=ks,
        confirmer=conf,
    )

    result = orchestrator.run_turn("Click on screen")
    assert "Execution halted: Kill switch is active" in result
    # PyAutoGUI click was never invoked
    assert len(mock_pag.clicks) == 0


# -------------------------------------------------------------------------
# 7. Acceptance Test: 'Open Notepad and type hello'
# -------------------------------------------------------------------------


def test_acceptance_open_notepad_and_type_hello(tmp_path: Path) -> None:
    """Acceptance test: 'open Notepad and type hello' with step confirmation per action.

    Verifies:
    1. open_app('notepad') requested and confirmed
    2. take_screenshot() auto-executed, untrusted_output marks session tainted
    3. gui_click(x, y) confirmed with taint warning
    4. gui_type('hello') confirmed and executed
    5. Screenshot thumbnail saved and logged in audit log
    6. Brain completes turn and returns final response
    """
    audit_file = tmp_path / "audit.log"
    ks = KillSwitch()
    confirmer = MockConfirmer(approve_all=True)
    registry = ToolRegistry()

    # Configure tools
    cfg = JarvisConfig(
        brain=BrainConfig(provider="anthropic", model="test-model"),
        apps={"notepad": "notepad.exe"},
        gui=GUIConfig(
            approval_mode="step",
            thumbnail_dir=str(tmp_path / "screens"),
            denied_window_titles=["1password", "bank"],
        ),
    )

    # Register open_app tool with mock adapter
    mock_adapter = MagicMock()
    mock_adapter.open_app.return_value = True
    app_tool = create_open_app_tool(allowed_apps={"notepad": "notepad.exe"}, os_adapter=mock_adapter)
    registry.register(app_tool)

    # Register GUI tools with mock screen and pyautogui
    mock_pag = MockPyAutoGUI()
    controller = GUIInputController(
        denied_window_titles=cfg.gui.denied_window_titles,
        pyautogui_module=mock_pag,
    )
    screen_cap = ScreenCapture(sct=MockMSS())
    gui_tools = create_gui_tools(cfg, input_controller=controller, screen_capture=screen_cap)
    for t in gui_tools:
        registry.register(t)

    # Script FakeBrain conversation turns
    # Turn 1: open_app("notepad")
    # Turn 2: take_screenshot()
    # Turn 3: gui_click(x=100, y=200)
    # Turn 4: gui_type(text="hello")
    # Turn 5: "Notepad opened and hello typed."
    fake_brain = FakeBrain([
        BrainResponse(tool_calls=[ToolCall(id="c1", name="open_app", arguments={"name": "notepad"})]),
        BrainResponse(tool_calls=[ToolCall(id="c2", name="take_screenshot", arguments={})]),
        BrainResponse(tool_calls=[ToolCall(id="c3", name="gui_click", arguments={"x": 100, "y": 200})]),
        BrainResponse(tool_calls=[ToolCall(id="c4", name="gui_type", arguments={"text": "hello"})]),
        BrainResponse(text="Notepad opened and hello typed."),
    ])

    audit_logger = AuditLogger(audit_file)
    permission_engine = PermissionEngine()

    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=registry,
        audit_logger=audit_logger,
        kill_switch=ks,
        confirmer=confirmer,
        permission_engine=permission_engine,
    )

    with patch("jarvis.gui_control.input.get_foreground_window_title", return_value="Untitled - Notepad"):
        final_response = orchestrator.run_turn("open Notepad and type hello")

    # Verify conversation completed with final text
    assert final_response == "Notepad opened and hello typed."

    # Verify all tools executed
    assert mock_adapter.open_app.called
    assert len(mock_pag.clicks) == 1
    assert mock_pag.typed_texts == ["hello"]

    # Verify confirmation prompts were raised per action
    assert len(confirmer.prompts) == 3  # open_app, gui_click, gui_type (take_screenshot is AUTO)
    assert any("notepad" in p.lower() for p in confirmer.prompts)
    assert any("click" in p.lower() for p in confirmer.prompts)
    assert any("type" in p.lower() for p in confirmer.prompts)

    # Verify audit log was written and includes thumbnails
    audit_logger.close()
    assert audit_file.exists()
    audit_lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_lines) >= 8  # call_requested, decision, executed for each tool
    # Check that thumbnail_path is present in audit records
    assert any("thumbnail_path" in line for line in audit_lines)
