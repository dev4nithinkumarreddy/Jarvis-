"""Core contracts, types, channels, and configuration for Jarvis."""

from jarvis.core.channels import Confirmer, InputChannel, OutputChannel
from jarvis.core.config import BrainConfig, Config, JarvisConfig, load_config
from jarvis.core.types import (
    PermissionDecision,
    RiskTier,
    SessionState,
    ToolCall,
    ToolResult,
    ToolSpec,
)

__all__ = [
    "BrainConfig",
    "Config",
    "Confirmer",
    "InputChannel",
    "JarvisConfig",
    "Orchestrator",
    "OutputChannel",
    "PermissionDecision",
    "RiskTier",
    "SessionState",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "load_config",
]


def __getattr__(name: str):
    if name == "Orchestrator":
        from jarvis.core.orchestrator import Orchestrator
        return Orchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
