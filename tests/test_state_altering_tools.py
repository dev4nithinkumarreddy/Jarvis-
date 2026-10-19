"""Tests for state-altering tools: file writes, apps, shell commands, confirmations, and dry-run."""

from pathlib import Path
import re
from unittest.mock import MagicMock
import pytest

from jarvis.core.config import CommandConfig, JarvisConfig
from jarvis.core.orchestrator import Orchestrator
from jarvis.core.types import RiskTier, ToolCall
from jarvis.platform.os_adapter import OSAdapter, WindowsOSAdapter
from jarvis.safety.audit import AuditLogger
from jarvis.safety.confirmers import CLIConfirmer
from jarvis.safety.path_guard import PathNotAllowed
from jarvis.safety.permissions import format_confirmation_description
from jarvis.tools.apps import create_open_app_tool, create_open_path_tool
from jarvis.tools.files_write import (
    create_copy_path_tool,
    create_create_text_file_tool,
    create_move_path_tool,
    create_move_to_trash_tool,
)
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.shell import create_run_command_tool
from jarvis.brain.base import BrainResponse
from jarvis.brain.fake_brain import FakeBrain


# -----------------------------------------------------------------------------
# 1. File Writing Tools Tests
# -----------------------------------------------------------------------------

def test_create_text_file(tmp_path: Path):
    """create_text_file creates new file within allowed root."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "notes.txt"

    tool = create_create_text_file_tool([sandbox])
    assert tool.risk_tier == RiskTier.CONFIRM

    res = tool.handler(path=str(target), content="Buy milk")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "Buy milk"
    assert "Created new" in res


def test_create_text_file_overwrite_warning_in_confirmation(tmp_path: Path):
    """Confirmation text must explicitly contain 'overwrite' when file exists."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    existing = sandbox / "existing.txt"
    existing.write_text("Old content", encoding="utf-8")

    tool = create_create_text_file_tool([sandbox])

    # 1. Existing file: confirmation description mentions overwrite
    call_overwrite = ToolCall(
        id="c1",
        name="create_text_file",
        arguments={"path": str(existing), "content": "New content"},
    )
    desc_overwrite = format_confirmation_description(tool, call_overwrite)
    assert "overwrite" in desc_overwrite.lower()
    assert "yes" in desc_overwrite.lower()

    # 2. Non-existing file: mentions Overwrites: no
    call_new = ToolCall(
        id="c2",
        name="create_text_file",
        arguments={"path": str(sandbox / "new.txt"), "content": "Hello"},
    )
    desc_new = format_confirmation_description(tool, call_new)
    assert "Overwrites: no" in desc_new


def test_move_and_copy_path(tmp_path: Path):
    """move_path and copy_path function properly within allowed roots."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    src = sandbox / "orig.txt"
    src.write_text("original content", encoding="utf-8")

    dst_copy = sandbox / "copied.txt"
    dst_move = sandbox / "moved.txt"

    copy_tool = create_copy_path_tool([sandbox])
    copy_tool.handler(src=str(src), dst=str(dst_copy))
    assert src.exists()
    assert dst_copy.exists()
    assert dst_copy.read_text(encoding="utf-8") == "original content"

    move_tool = create_move_path_tool([sandbox])
    move_tool.handler(src=str(src), dst=str(dst_move))
    assert not src.exists()
    assert dst_move.exists()
    assert dst_move.read_text(encoding="utf-8") == "original content"


def test_move_to_trash(tmp_path: Path):
    """move_to_trash safely trashes file via send2trash."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    victim = sandbox / "delete_me.txt"
    victim.write_text("to be deleted", encoding="utf-8")

    tool = create_move_to_trash_tool([sandbox])
    res = tool.handler(path=str(victim))
    assert not victim.exists()
    assert "Trash" in res or "Recycle Bin" in res


def test_path_escape_rejected_for_write_tools(tmp_path: Path):
    """Write operations outside allowed roots are rejected by PathNotAllowed."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"

    tool = create_create_text_file_tool([sandbox])
    with pytest.raises(PathNotAllowed):
        tool.handler(path=str(outside), content="hack")


# -----------------------------------------------------------------------------
# 2. App & Default Path Launcher Tests
# -----------------------------------------------------------------------------

def test_open_app_allowlist_enforcement():
    """open_app permits only allowlisted apps; rejects unknown apps with allowed list."""
    mock_adapter = MagicMock(spec=OSAdapter)
    allowed_apps = {"notepad": "notepad.exe", "calc": "calc.exe"}
    tool = create_open_app_tool(allowed_apps, os_adapter=mock_adapter)

    # Allowed app
    res = tool.handler(name="notepad")
    assert "Successfully" in res
    mock_adapter.open_app.assert_called_once_with("notepad.exe")

    # Disallowed app raises ValueError detailing allowed applications
    with pytest.raises(ValueError) as exc:
        tool.handler(name="malicious_app")
    assert "malicious_app" in str(exc.value)
    assert "notepad" in str(exc.value)
    assert "calc" in str(exc.value)


def test_open_app_alias_normalization_and_arguments():
    """open_app resolves common synonyms ('browser', 'google chrome') and passes launch arguments."""
    mock_adapter = MagicMock(spec=OSAdapter)
    allowed_apps = {"chrome": "chrome.exe", "vscode": "code"}
    tool = create_open_app_tool(allowed_apps, os_adapter=mock_adapter)

    # Alias 'browser' maps to 'chrome'
    res = tool.handler(name="browser", argument="https://youtube.com")
    assert "Successfully" in res
    assert "https://youtube.com" in res
    mock_adapter.open_app.assert_called_with("chrome.exe https://youtube.com")

    # Alias 'google chrome' maps to 'chrome'
    res2 = tool.handler(name="google chrome")
    assert "Successfully" in res2
    mock_adapter.open_app.assert_called_with("chrome.exe")

    # Alias 'vs code' maps to 'vscode'
    res3 = tool.handler(name="vs code")
    assert "Successfully" in res3
    mock_adapter.open_app.assert_called_with("code")



def test_open_path(tmp_path: Path):
    """open_path opens existing files via os_adapter within allowed root."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    test_file = sandbox / "report.pdf"
    test_file.write_text("pdf header", encoding="utf-8")

    mock_adapter = MagicMock(spec=OSAdapter)
    tool = create_open_path_tool([sandbox], os_adapter=mock_adapter)

    res = tool.handler(path=str(test_file))
    assert "Successfully" in res
    mock_adapter.open_path.assert_called_once()

    # Non-existent file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        tool.handler(path=str(sandbox / "missing.pdf"))

    # Path outside allowed root raises PathNotAllowed
    with pytest.raises(PathNotAllowed):
        tool.handler(path=str(tmp_path / "outside.pdf"))


# -----------------------------------------------------------------------------
# 3. Shell Command Allowlist & Validation Tests
# -----------------------------------------------------------------------------

def test_run_command_allowlist_and_regex(tmp_path: Path):
    """run_command enforces command allowlist, regex validation, and runs without shell=True."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    allowed_commands = {
        "python_eval": CommandConfig(
            executable="python",
            args_schema=["^-c$", "^print\\([0-9]+\\)$"],
            timeout_seconds=10.0,
        ),
    }

    tool = create_run_command_tool(allowed_commands, [sandbox])

    # 1. Allowed command and valid regex args
    res = tool.handler(command_id="python_eval", args=["-c", "print(12345)"], cwd=str(sandbox))
    assert res["returncode"] == 0
    assert "12345" in res["stdout"]

    # 2. Argument regex validation failure
    with pytest.raises(ValueError, match="failed validation pattern"):
        tool.handler(command_id="python_eval", args=["-c", "import os; os.system('calc')"])

    # 3. Disallowed command ID is BLOCKED
    with pytest.raises(PermissionError, match="BLOCKED or not in allowed"):
        tool.handler(command_id="dangerous_rm", args=["-rf", "/"])


def test_run_command_fullmatch_rejects_unanchored_trailing_args(tmp_path: Path):
    """run_command enforces re.fullmatch so unanchored patterns reject trailing injected args."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    allowed_commands = {
        "git_status": CommandConfig(
            executable="python",
            args_schema=["status"],  # unanchored pattern!
            timeout_seconds=5.0,
        ),
    }

    tool = create_run_command_tool(allowed_commands, [sandbox])

    # Exact match succeeds
    res = tool.handler(command_id="git_status", args=["status"], cwd=str(sandbox))
    assert "returncode" in res

    # Trailing argument injection fails with re.fullmatch
    with pytest.raises(ValueError, match="failed validation pattern"):
        tool.handler(command_id="git_status", args=["status --extra-args"], cwd=str(sandbox))


def test_run_command_timeout(tmp_path: Path):
    """run_command times out when execution exceeds configured timeout."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    allowed_commands = {
        "python_sleep": CommandConfig(
            executable="python",
            args_schema=["^-c$", ".*"],
            timeout_seconds=0.2,
        ),
    }

    tool = create_run_command_tool(allowed_commands, [sandbox])
    with pytest.raises(TimeoutError, match="timed out"):
        tool.handler(command_id="python_sleep", args=["-c", "import time; time.sleep(2)"])


def test_run_command_output_truncation(tmp_path: Path):
    """Captured command output is truncated to 10 KB."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    allowed_commands = {
        "large_output": CommandConfig(
            executable="python",
            args_schema=["^-c$", ".*"],
            timeout_seconds=10.0,
        ),
    }

    tool = create_run_command_tool(allowed_commands, [sandbox])
    res = tool.handler(command_id="large_output", args=["-c", "print('A' * 20000)"])
    assert len(res["stdout"]) == 10240
    assert res["truncated"] is True


# -----------------------------------------------------------------------------
# 4. Orchestrator Integration: Denied Confirmations & Dry Run
# -----------------------------------------------------------------------------

def test_denied_confirmation_causes_no_change(tmp_path: Path):
    """When user denies confirmation, the CONFIRM tool is NOT executed."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target_file = sandbox / "uncreated.txt"

    fake_brain = FakeBrain([
        BrainResponse(
            tool_calls=[
                ToolCall(
                    id="call_create",
                    name="create_text_file",
                    arguments={"path": str(target_file), "content": "Important data"},
                ),
            ],
        ),
        BrainResponse(text="I understand you cancelled the file creation."),
    ])

    registry = ToolRegistry()
    registry.register(create_create_text_file_tool([sandbox]))

    # Confirmer mock that always denies
    denying_confirmer = CLIConfirmer(input_fn=lambda _: "n")

    audit_path = tmp_path / "audit.log"
    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=registry,
        confirmer=denying_confirmer,
        audit_logger=AuditLogger(audit_path),
    )

    reply = orchestrator.run_turn("Create uncreated.txt")
    assert "cancelled" in reply.lower() or "denied" in reply.lower()
    # File must NOT exist
    assert not target_file.exists()

    # Audit log confirms denial
    log_content = audit_path.read_text(encoding="utf-8")
    assert "User denied confirmation" in log_content or "failed" in log_content


def test_dry_run_changes_nothing(tmp_path: Path):
    """In dry-run mode, CONFIRM tools describe actions without acting."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target_file = sandbox / "dry_run_file.txt"

    fake_brain = FakeBrain([
        BrainResponse(
            tool_calls=[
                ToolCall(
                    id="call_dry",
                    name="create_text_file",
                    arguments={"path": str(target_file), "content": "Dry run text"},
                ),
            ],
        ),
        BrainResponse(text="[Dry run completed]"),
    ])

    registry = ToolRegistry()
    registry.register(create_create_text_file_tool([sandbox]))

    orchestrator = Orchestrator(
        brain=fake_brain,
        tool_registry=registry,
        audit_logger=AuditLogger(tmp_path / "audit.log"),
        dry_run=True,  # DRY RUN ENABLED
    )

    reply = orchestrator.run_turn("Create dry run file")
    assert reply == "[Dry run completed]"
    # File must NOT have been created!
    assert not target_file.exists()

    # Tool output in conversation history indicates dry run simulation
    tool_resp = orchestrator.messages[2]["content"][0]["content"]
    assert "[Dry Run] Would execute" in tool_resp
    assert "dry_run_file.txt" in tool_resp


# -----------------------------------------------------------------------------
# 5. Acceptance Scenario
# -----------------------------------------------------------------------------

def test_acceptance_create_note_confirmed_and_denied(tmp_path: Path):
    """Acceptance test: 'create a note file called todo.txt' asks confirmation, creates on yes, does nothing on no."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    todo_file = notes_dir / "todo.txt"

    # Part A: Confirmed (User responds 'yes')
    fake_brain_yes = FakeBrain([
        BrainResponse(
            tool_calls=[
                ToolCall(
                    id="c_todo_1",
                    name="create_text_file",
                    arguments={"path": str(todo_file), "content": "- Item 1\n- Item 2"},
                ),
            ],
        ),
        BrainResponse(text="Note file todo.txt has been successfully created in your notes folder."),
    ])

    registry = ToolRegistry()
    registry.register(create_create_text_file_tool([notes_dir]))

    confirmer_yes = CLIConfirmer(input_fn=lambda _: "yes")
    audit_log = tmp_path / "audit.log"

    orchestrator_yes = Orchestrator(
        brain=fake_brain_yes,
        tool_registry=registry,
        confirmer=confirmer_yes,
        audit_logger=AuditLogger(audit_log),
    )

    resp_yes = orchestrator_yes.run_turn("create a note file called todo.txt in my notes folder")
    assert "successfully created" in resp_yes
    assert todo_file.exists()
    assert todo_file.read_text(encoding="utf-8") == "- Item 1\n- Item 2"

    # Clean up file for Part B
    todo_file.unlink()

    # Part B: Denied (User responds 'no')
    fake_brain_no = FakeBrain([
        BrainResponse(
            tool_calls=[
                ToolCall(
                    id="c_todo_2",
                    name="create_text_file",
                    arguments={"path": str(todo_file), "content": "- Item 1\n- Item 2"},
                ),
            ],
        ),
        BrainResponse(text="Creation cancelled upon your request."),
    ])

    confirmer_no = CLIConfirmer(input_fn=lambda _: "no")
    orchestrator_no = Orchestrator(
        brain=fake_brain_no,
        tool_registry=registry,
        confirmer=confirmer_no,
        audit_logger=AuditLogger(audit_log),
    )

    resp_no = orchestrator_no.run_turn("create a note file called todo.txt in my notes folder")
    assert not todo_file.exists()

    # Audit log confirms denial record
    log_lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert any("User denied confirmation" in line for line in log_lines)


def test_set_system_volume_tool():
    """Verify set_system_volume tool spec and handler behavior."""
    from unittest.mock import MagicMock, patch
    from jarvis.tools.system_tools import create_set_volume_tool, set_system_volume

    spec = create_set_volume_tool()
    assert spec.name == "set_system_volume"
    assert spec.risk_tier == RiskTier.CONFIRM

    mock_volume = MagicMock()
    mock_volume.GetMute.return_value = 0
    mock_devices = MagicMock()
    mock_devices.EndpointVolume = mock_volume

    with patch("pycaw.pycaw.AudioUtilities.GetSpeakers", return_value=mock_devices):
        res = set_system_volume(65)
        assert res["status"] == "success"
        assert res["volume_percent"] == 65
        assert mock_volume.SetMasterVolumeLevelScalar.called


def test_mute_system_volume_tool():
    """Verify mute_system_volume tool toggles or sets mute status."""
    from unittest.mock import MagicMock, patch
    from jarvis.tools.system_tools import create_mute_volume_tool, mute_system_volume

    spec = create_mute_volume_tool()
    assert spec.name == "mute_system_volume"
    assert spec.risk_tier == RiskTier.CONFIRM

    mock_volume = MagicMock()
    mock_volume.GetMute.return_value = 0
    mock_volume.GetMasterVolumeLevelScalar.return_value = 0.5
    mock_devices = MagicMock()
    mock_devices.EndpointVolume = mock_volume

    with patch("pycaw.pycaw.AudioUtilities.GetSpeakers", return_value=mock_devices):
        res = mute_system_volume(mute=True)
        assert res["status"] == "success"
        assert res["muted"] is True
        mock_volume.SetMute.assert_called_with(1, None)


def test_lock_workstation_tool():
    """Verify lock_workstation triggers LockWorkStation Windows API."""
    from unittest.mock import patch
    from jarvis.tools.system_tools import create_lock_workstation_tool, lock_workstation

    spec = create_lock_workstation_tool()
    assert spec.name == "lock_workstation"
    assert spec.risk_tier == RiskTier.CONFIRM

    with patch("ctypes.windll.user32.LockWorkStation", return_value=1):
        res = lock_workstation()
        assert res["status"] == "success"
        assert res["locked"] is True


def test_state_altering_tools_includes_volume_and_lock():
    """Verify create_state_altering_tools registers volume and lock tools."""
    from jarvis.tools import create_state_altering_tools
    tools = create_state_altering_tools(
        allowed_roots=[],
        allowed_apps={},
        allowed_commands={},
    )
    names = [t.name for t in tools]
    assert "set_system_volume" in names
    assert "mute_system_volume" in names
    assert "lock_workstation" in names


def test_windows_os_adapter_open_app_resolution():
    """Verify WindowsOSAdapter.open_app resolves executables via PATH, registry, and fallback."""
    from unittest.mock import patch
    from jarvis.platform.os_adapter import WindowsOSAdapter

    adapter = WindowsOSAdapter()

    # Case 1: shutil.which resolves in PATH
    with patch("shutil.which", return_value="C:\\Windows\\notepad.exe"), \
         patch("subprocess.Popen") as mock_popen:
        assert adapter.open_app("notepad.exe") is True
        mock_popen.assert_called_with(["C:\\Windows\\notepad.exe"], shell=False)

    # Case 2: shutil.which is None, resolved via App Paths registry mock
    with patch("shutil.which", return_value=None), \
         patch("winreg.OpenKey"), \
         patch("winreg.QueryValueEx", return_value=("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", None)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("subprocess.Popen") as mock_popen:
        assert adapter.open_app("chrome.exe https://example.com") is True
        mock_popen.assert_called_with(["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "https://example.com"], shell=False)


