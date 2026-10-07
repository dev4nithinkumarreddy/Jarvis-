"""Acceptance tests for Jarvis chat queries and path refusal."""

from pathlib import Path
from jarvis.brain.base import BrainResponse
from jarvis.brain.fake_brain import FakeBrain
from jarvis.core.types import ToolCall
from jarvis.ui.cli import create_orchestrator


def test_acceptance_disk_space_query(tmp_path: Path):
    """Acceptance test: 'what's my disk space?' executes system_info and replies."""
    fake_brain = FakeBrain([
        BrainResponse(
            tool_calls=[ToolCall(id="call_disk", name="system_info", arguments={})],
        ),
        BrainResponse(text="You currently have 156.2 GB of free disk space."),
    ])

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"""
allowed_roots:
  - "{tmp_path.as_posix()}"
brain:
  provider: "anthropic"
  model: "claude-3-5-sonnet-20241022"
max_tool_calls_per_turn: 8
audit_log_path: "{ (tmp_path / 'audit.log').as_posix() }"
confirm_timeout_seconds: 30.0
""",
        encoding="utf-8",
    )

    orchestrator = create_orchestrator(config_path=str(cfg_file), brain=fake_brain)
    response = orchestrator.run_turn("what's my disk space?")
    assert "156.2 GB" in response
    assert "disk space" in response


def test_acceptance_list_files_in_allowed_folder(tmp_path: Path):
    """Acceptance test: 'list files in <allowed folder>' returns folder contents."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "notes.txt").write_text("Hello", encoding="utf-8")
    (sandbox / "data.csv").write_text("1,2", encoding="utf-8")

    fake_brain = FakeBrain([
        BrainResponse(
            tool_calls=[
                ToolCall(id="call_list", name="list_directory", arguments={"path": str(sandbox)}),
            ],
        ),
        BrainResponse(text="The folder contains: data.csv and notes.txt."),
    ])

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"""
allowed_roots:
  - "{sandbox.as_posix()}"
brain:
  provider: "anthropic"
  model: "claude-3-5-sonnet-20241022"
max_tool_calls_per_turn: 8
audit_log_path: "{ (tmp_path / 'audit.log').as_posix() }"
confirm_timeout_seconds: 30.0
""",
        encoding="utf-8",
    )

    orchestrator = create_orchestrator(config_path=str(cfg_file), brain=fake_brain)
    response = orchestrator.run_turn(f"list files in {sandbox}")
    assert "data.csv" in response
    assert "notes.txt" in response


def test_acceptance_refuses_paths_outside_allowed_roots(tmp_path: Path):
    """Acceptance test: Refuses paths outside allowed roots with a clear refusal message."""
    allowed = tmp_path / "allowed_dir"
    allowed.mkdir()

    outside_file = tmp_path / "forbidden.txt"
    outside_file.write_text("sensitive", encoding="utf-8")

    fake_brain = FakeBrain([
        BrainResponse(
            tool_calls=[
                ToolCall(id="call_refuse", name="read_text_file", arguments={"path": str(outside_file)}),
            ],
        ),
        BrainResponse(
            text="Access Denied: Path is outside configured allowed roots. I cannot read forbidden.txt.",
        ),
    ])

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"""
allowed_roots:
  - "{allowed.as_posix()}"
brain:
  provider: "anthropic"
  model: "claude-3-5-sonnet-20241022"
max_tool_calls_per_turn: 8
audit_log_path: "{ (tmp_path / 'audit.log').as_posix() }"
confirm_timeout_seconds: 30.0
""",
        encoding="utf-8",
    )

    orchestrator = create_orchestrator(config_path=str(cfg_file), brain=fake_brain)
    response = orchestrator.run_turn(f"read {outside_file}")
    assert "Access Denied" in response or "outside" in response.lower()

    # Tool output block recorded Access denied
    tool_resp = orchestrator.messages[2]["content"][0]
    assert tool_resp["is_error"] is True
    assert "Access denied" in tool_resp["content"]
