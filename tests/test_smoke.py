"""Smoke tests for jarvis package import and default config loading."""

from pathlib import Path
import jarvis
import jarvis.brain
import jarvis.core
import jarvis.gui_control
import jarvis.memory
import jarvis.platform
import jarvis.safety
import jarvis.tools
import jarvis.ui
import jarvis.voice
from jarvis.core import (
    BrainConfig,
    Config,
    Confirmer,
    InputChannel,
    JarvisConfig,
    OutputChannel,
    PermissionDecision,
    RiskTier,
    ToolCall,
    ToolResult,
    ToolSpec,
    load_config,
)
from jarvis.platform import (
    LinuxOSAdapter,
    MacOSAdapter,
    OSAdapter,
    WindowsOSAdapter,
    get_os_adapter,
)


def test_package_metadata():
    """Verify package version and top-level imports."""
    assert hasattr(jarvis, "__version__")
    assert jarvis.__version__ == "0.1.0"


def test_submodules_exist():
    """Verify all required subpackages exist and are importable."""
    assert jarvis.core is not None
    assert jarvis.brain is not None
    assert jarvis.safety is not None
    assert jarvis.tools is not None
    assert jarvis.voice is not None
    assert jarvis.gui_control is not None
    assert jarvis.memory is not None
    assert jarvis.ui is not None
    assert jarvis.platform is not None


def test_load_default_config():
    """Verify that root config.yaml loads cleanly and matches schema."""
    config = load_config("config.yaml")
    assert isinstance(config, JarvisConfig)
    assert isinstance(config.brain, BrainConfig)
    assert config.brain.provider in ("anthropic", "groq")
    assert bool(config.brain.model)
    assert config.max_tool_calls_per_turn == 8
    assert config.audit_log_path == Path("logs/audit.log")
    assert config.confirm_timeout_seconds == 30.0
    assert config.allowed_roots == [Path(".")]
