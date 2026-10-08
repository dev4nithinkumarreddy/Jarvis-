"""Tests for GitHub Pages packaging, universal relative paths, and CORS bridge."""

from __future__ import annotations

from pathlib import Path
import pytest

from jarvis.ui.server import is_allowed_origin
from scripts.export_pages import export_github_pages


def test_is_allowed_origin_github_pages() -> None:
    # Standard local origins
    assert is_allowed_origin("http://127.0.0.1:8000", "127.0.0.1", 8000) is True
    assert is_allowed_origin("http://localhost:8000", "127.0.0.1", 8000) is True

    # GitHub Pages origins
    assert is_allowed_origin("https://dev4nithinkumarreddy.github.io", "127.0.0.1", 8000) is True
    assert is_allowed_origin("https://stark-industries.github.io", "127.0.0.1", 8000) is True
    assert is_allowed_origin("null", "127.0.0.1", 8000) is True

    # Malicious third-party origin rejected
    assert is_allowed_origin("https://evil-hacker.com", "127.0.0.1", 8000) is False


def test_export_github_pages_bundle(tmp_path: Path) -> None:
    out_dir = export_github_pages(tmp_path)
    assert (out_dir / "index.html").is_file()
    assert (out_dir / "style.css").is_file()
    assert (out_dir / "app.js").is_file()
    assert (out_dir / ".nojekyll").is_file()

    html_content = (out_dir / "index.html").read_text(encoding="utf-8")
    # Verify universal relative paths (not root-slashed)
    assert 'href="./style.css"' in html_content
    assert 'src="./app.js"' in html_content
