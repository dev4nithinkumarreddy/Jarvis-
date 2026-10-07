"""Tests for read-only tools and tool registry."""

import json
from pathlib import Path
import pytest

from jarvis.core.types import RiskTier, ToolCall, ToolSpec
from jarvis.safety.path_guard import PathNotAllowed
from jarvis.tools.file_tools import (
    create_list_directory_tool,
    create_read_text_file_tool,
    create_search_files_tool,
)
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.system_tools import (
    create_current_time_tool,
    create_system_info_tool,
    get_current_time,
    get_system_info,
)


def test_registry_register_and_get():
    """Verify ToolRegistry stores and retrieves ToolSpecs."""
    registry = ToolRegistry()
    spec = ToolSpec(
        name="test_tool",
        description="A test tool",
        input_schema={},
        risk_tier=RiskTier.AUTO,
        handler=lambda: "ok",
    )
    registry.register(spec)
    assert registry.get("test_tool") == spec
    assert len(registry.list_tools()) == 1


def test_registry_schemas():
    """Verify ToolRegistry outputs JSON schema formatting for LLMs."""
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="Echoes message",
            input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            risk_tier=RiskTier.AUTO,
        )
    )
    schemas = registry.get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "echo"
    assert "input_schema" in schemas[0]


def test_registry_unknown_tool_never_crashes():
    """Executing an unknown tool returns an error ToolResult without raising."""
    registry = ToolRegistry()
    call = ToolCall(id="call_x", name="non_existent_tool", arguments={})
    result = registry.execute(call)
    assert result.call_id == "call_x"
    assert result.ok is False
    assert "Unknown tool" in (result.error or "")


def test_system_info_tool():
    """Verify system_info tool runs and returns cpu, memory, and disk info."""
    spec = create_system_info_tool()
    assert spec.name == "system_info"
    assert spec.risk_tier == RiskTier.AUTO
    assert spec.untrusted_output is False

    data = get_system_info()
    assert "cpu_percent" in data
    assert "memory" in data
    assert "disk" in data
    assert data["memory"]["total_gb"] > 0
    assert data["disk"]["free_gb"] > 0


def test_current_time_tool():
    """Verify current_time tool returns local and UTC timestamps."""
    spec = create_current_time_tool()
    assert spec.name == "current_time"
    assert spec.risk_tier == RiskTier.AUTO

    res = get_current_time()
    assert "local_time" in res
    assert "utc_time" in res
    assert "iso" in res


def test_list_directory_tool(tmp_path: Path):
    """Verify list_directory lists entries and blocks path escapes."""
    allowed = tmp_path / "sandbox"
    allowed.mkdir()
    (allowed / "file1.txt").write_text("a", encoding="utf-8")
    (allowed / "dir1").mkdir()

    tool_spec = create_list_directory_tool([allowed])
    entries = tool_spec.handler(path=str(allowed))
    assert len(entries) == 2
    names = [e["name"] for e in entries]
    assert "file1.txt" in names
    assert "dir1" in names

    # Outside path raises PathNotAllowed
    with pytest.raises(PathNotAllowed):
        tool_spec.handler(path=str(tmp_path / "outside"))


def test_read_text_file_tool(tmp_path: Path):
    """Verify read_text_file reads content, respects max_bytes, and is untrusted."""
    allowed = tmp_path / "sandbox"
    allowed.mkdir()
    sample_file = allowed / "document.txt"
    sample_file.write_text("Hello world from Jarvis", encoding="utf-8")

    tool_spec = create_read_text_file_tool([allowed])
    assert tool_spec.untrusted_output is True

    # Standard read
    content = tool_spec.handler(path=str(sample_file))
    assert content == "Hello world from Jarvis"

    # Truncated read
    truncated = tool_spec.handler(path=str(sample_file), max_bytes=5)
    assert truncated.startswith("Hello")
    assert "truncated" in truncated.lower()

    # Path escape blocked
    with pytest.raises(PathNotAllowed):
        tool_spec.handler(path=str(tmp_path / ".." / "escaped.txt"))


def test_search_files_tool(tmp_path: Path):
    """Verify search_files finds matching files by pattern."""
    allowed = tmp_path / "sandbox"
    allowed.mkdir()
    (allowed / "alpha.py").write_text("# py", encoding="utf-8")
    (allowed / "beta.txt").write_text("text", encoding="utf-8")
    sub = allowed / "sub"
    sub.mkdir()
    (sub / "gamma.py").write_text("# py2", encoding="utf-8")

    tool_spec = create_search_files_tool([allowed])
    py_files = tool_spec.handler(root=str(allowed), name_pattern="*.py")
    assert len(py_files) == 2
    assert any("alpha.py" in f for f in py_files)
    assert any("gamma.py" in f for f in py_files)
