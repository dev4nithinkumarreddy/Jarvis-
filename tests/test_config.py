"""Tests for configuration loading, schema validation, and error rejection."""

from pathlib import Path
import pytest
from pydantic import ValidationError
from jarvis.core.config import BrainConfig, JarvisConfig, load_config


def test_valid_config_loading(tmp_path: Path):
    """Test loading a fully specified valid config."""
    cfg_file = tmp_path / "valid.yaml"
    cfg_file.write_text(
        """
allowed_roots:
  - "C:/data"
  - "D:/projects"
brain:
  provider: "openai"
  model: "gpt-4o"
max_tool_calls_per_turn: 12
audit_log_path: "logs/test_audit.log"
confirm_timeout_seconds: 45.5
""",
        encoding="utf-8",
    )
    config = load_config(cfg_file)
    assert len(config.allowed_roots) == 2
    assert config.brain.provider == "openai"
    assert config.brain.model == "gpt-4o"
    assert config.max_tool_calls_per_turn == 12
    assert config.audit_log_path == Path("logs/test_audit.log")
    assert config.confirm_timeout_seconds == 45.5


def test_config_defaults_applied(tmp_path: Path):
    """Test defaults when optional fields are omitted."""
    cfg_file = tmp_path / "minimal.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
  model: "mistral"
""",
        encoding="utf-8",
    )
    config = load_config(cfg_file)
    assert config.brain.model == "mistral"
    assert config.max_tool_calls_per_turn == 8
    assert config.audit_log_path == Path("logs/audit.log")
    assert config.confirm_timeout_seconds == 30.0
    assert config.allowed_roots == []


def test_missing_file_raises_filenotfound(tmp_path: Path):
    """Missing config file raises FileNotFoundError."""
    non_existent = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        load_config(non_existent)


def test_invalid_yaml_syntax(tmp_path: Path):
    """Malformed YAML raises ValueError."""
    bad_yaml = tmp_path / "syntax_error.yaml"
    bad_yaml.write_text("brain: [unclosed list", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to parse YAML"):
        load_config(bad_yaml)


def test_non_dict_yaml(tmp_path: Path):
    """YAML containing a scalar or list at root raises ValueError."""
    bad_yaml = tmp_path / "list_root.yaml"
    bad_yaml.write_text("- item1\n- item2", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_config(bad_yaml)


def test_missing_brain_section(tmp_path: Path):
    """Missing brain section raises ValidationError."""
    cfg_file = tmp_path / "missing_brain.yaml"
    cfg_file.write_text("max_tool_calls_per_turn: 5", encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("brain",) for err in errors)


def test_missing_brain_model(tmp_path: Path):
    """Brain model has no default and must be provided."""
    cfg_file = tmp_path / "missing_model.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("brain", "model") for err in errors)


def test_missing_brain_provider(tmp_path: Path):
    """Brain provider is required."""
    cfg_file = tmp_path / "missing_provider.yaml"
    cfg_file.write_text(
        """
brain:
  model: "llama3"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("brain", "provider") for err in errors)


def test_invalid_max_tool_calls_per_turn(tmp_path: Path):
    """max_tool_calls_per_turn must be positive int."""
    cfg_file = tmp_path / "invalid_calls.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
  model: "llama3"
max_tool_calls_per_turn: 0
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any("max_tool_calls_per_turn" in err["loc"] for err in errors)


def test_invalid_confirm_timeout(tmp_path: Path):
    """confirm_timeout_seconds must be positive float/int."""
    cfg_file = tmp_path / "invalid_timeout.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
  model: "llama3"
confirm_timeout_seconds: -10
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any("confirm_timeout_seconds" in err["loc"] for err in errors)


def test_invalid_allowed_roots(tmp_path: Path):
    """allowed_roots must be a list."""
    cfg_file = tmp_path / "invalid_roots.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
  model: "llama3"
allowed_roots: "not_a_list"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any("allowed_roots" in err["loc"] for err in errors)


def test_empty_audit_log_path(tmp_path: Path):
    """Empty audit_log_path string is rejected."""
    cfg_file = tmp_path / "empty_audit.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
  model: "llama3"
audit_log_path: "   "
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any("audit_log_path" in err["loc"] for err in errors)


def test_extra_fields_forbidden(tmp_path: Path):
    """Extra unrecognized fields in config are rejected."""
    cfg_file = tmp_path / "extra_fields.yaml"
    cfg_file.write_text(
        """
brain:
  provider: "ollama"
  model: "llama3"
unknown_parameter: 123
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_config(cfg_file)
    errors = exc_info.value.errors()
    assert any("unknown_parameter" in err["loc"] for err in errors)
