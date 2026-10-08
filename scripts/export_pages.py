"""Export and package the static Arc Reactor HUD bundle for GitHub Pages."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_SRC = PROJECT_ROOT / "src" / "jarvis" / "ui" / "static"
EXPORT_DIR = PROJECT_ROOT / "docs"  # Standard GitHub Pages directory branch/folder


def export_github_pages(target_dir: Path = EXPORT_DIR) -> Path:
    """Export static web HUD assets to the target GitHub Pages directory."""
    if not STATIC_SRC.is_dir():
        raise FileNotFoundError(f"Static source directory not found: {STATIC_SRC}")

    target_dir.mkdir(parents=True, exist_ok=True)

    required_files = ["index.html", "style.css", "app.js"]
    for fname in required_files:
        src_file = STATIC_SRC / fname
        if not src_file.is_file():
            raise FileNotFoundError(f"Missing required asset: {src_file}")
        shutil.copy2(src_file, target_dir / fname)

    # Copy any icons or assets if present
    icons_src = PROJECT_ROOT / "data" / "icons"
    if icons_src.is_dir():
        icons_dest = target_dir / "icons"
        icons_dest.mkdir(exist_ok=True)
        for icon in icons_src.glob("*.ico"):
            shutil.copy2(icon, icons_dest / icon.name)

    # Create .nojekyll so GitHub Pages doesn't ignore files
    (target_dir / ".nojekyll").touch()

    print(f"Successfully exported GitHub Pages bundle to: {target_dir}")
    print("Files packaged:")
    for item in sorted(target_dir.rglob("*")):
        if item.is_file():
            print(f"  - {item.relative_to(target_dir)}")
    return target_dir


if __name__ == "__main__":
    export_github_pages()
