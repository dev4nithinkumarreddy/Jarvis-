"""Safety layer for Jarvis: path guarding, permissions, audit logging, and emergency stop."""

from jarvis.safety.audit import (
    AuditEventType,
    AuditLogger,
    redact_arguments,
    verify_audit_log,
)
from jarvis.safety.confirmers import CLIConfirmer
from jarvis.safety.killswitch import (
    KillSwitch,
    get_kill_switch,
    normalize_hotkey,
)
from jarvis.safety.path_guard import (
    PathNotAllowed,
    ProtectedPathError,
    resolve_allowed,
)
from jarvis.safety.permissions import PermissionEngine

__all__ = [
    "AuditEventType",
    "AuditLogger",
    "CLIConfirmer",
    "KillSwitch",
    "PathNotAllowed",
    "PermissionEngine",
    "ProtectedPathError",
    "get_kill_switch",
    "normalize_hotkey",
    "redact_arguments",
    "resolve_allowed",
    "verify_audit_log",
]
