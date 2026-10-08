"""Tests for API key dialog and .env persistence."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from jarvis.core.config import load_dotenv_if_present
from jarvis.ui.key_dialog import _save_to_env, ensure_api_key


def test_save_to_env_creates_and_updates(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    _save_to_env(env_file, "GROQ_API_KEY", "gsk_test_key_123")

    content = env_file.read_text(encoding="utf-8")
    assert "GROQ_API_KEY=gsk_test_key_123" in content

    # Updating existing key
    _save_to_env(env_file, "GROQ_API_KEY", "gsk_updated_key_456")
    updated_content = env_file.read_text(encoding="utf-8")
    assert "GROQ_API_KEY=gsk_updated_key_456" in updated_content
    assert "gsk_test_key_123" not in updated_content


def test_load_dotenv_if_present(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CUSTOM_TEST_VAR=hello_jarvis\n# Comment\n", encoding="utf-8")

    with patch.dict(os.environ, {}, clear=False):
        if "CUSTOM_TEST_VAR" in os.environ:
            del os.environ["CUSTOM_TEST_VAR"]

        load_dotenv_if_present(env_file)
        assert os.environ.get("CUSTOM_TEST_VAR") == "hello_jarvis"


def test_ensure_api_key_existing_env() -> None:
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_already_set"}):
        result = ensure_api_key("config.yaml")
        assert result is True


def test_ensure_api_key_cancelled(tmp_path: Path) -> None:
    with (
        patch("jarvis.ui.key_dialog.load_dotenv_if_present"),
        patch("jarvis.core.config.load_dotenv_if_present"),
        patch.dict(os.environ, {}, clear=False),
    ):
        if "GROQ_API_KEY" in os.environ:
            del os.environ["GROQ_API_KEY"]

        with patch("jarvis.ui.key_dialog.prompt_api_key", return_value=None):
            result = ensure_api_key("config.yaml")
            assert result is False
