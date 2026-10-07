"""Tests for Phase 8.2: Protected Paths, Safety Boundaries, and Config Immutability.

Verifies:
1. Refusal of WRITE, MOVE, COPY-INTO, and TRASH operations on all protected paths:
   - config.yaml
   - AGENT_RULES.md
   - SECURITY.md
   - src/ directory and files within
   - logs/ directory and files within
   - data/ directory and files within
   - .browser_profile/ directory and files within
   - .env in root and arbitrary nested *.env files
2. Protection against traversal attempts via '..' and NTFS directory junctions.
3. Refusal of READ operations on protected paths, while explicitly allowlisted
   read-safe files (README.md) succeed.
4. Normal filesystem operations (write, read, move, trash) succeed on non-protected paths in allowed_roots.
5. Immutability of JarvisConfig (frozen Pydantic model) and commands/apps allowlists (MappingProxyType).
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from types import MappingProxyType
import pytest

from jarvis.core.config import CommandConfig, JarvisConfig, SafetyConfig, load_config
from jarvis.safety.path_guard import (
    PathNotAllowed,
    ProtectedPathError,
    is_env_file,
    resolve_allowed,
)
from jarvis.tools.apps import create_open_app_tool
from jarvis.tools.file_tools import create_read_text_file_tool
from jarvis.tools.files_write import (
    create_copy_path_tool,
    create_create_text_file_tool,
    create_move_path_tool,
    create_move_to_trash_tool,
)
from jarvis.tools.shell import create_run_command_tool


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def test_env(tmp_path: Path):
    """Sets up an isolated project root simulating Jarvis repo layout."""
    project_root = (tmp_path / "project").resolve()
    project_root.mkdir()

    # Create standard protected files
    (project_root / "config.yaml").write_text("brain:\n  provider: mock\n", encoding="utf-8")
    (project_root / "AGENT_RULES.md").write_text("# Agent Rules\n", encoding="utf-8")
    (project_root / "SECURITY.md").write_text("# Security\n", encoding="utf-8")
    (project_root / "README.md").write_text("# Readme Documentation\n", encoding="utf-8")
    (project_root / ".env").write_text("SECRET_KEY=12345\n", encoding="utf-8")

    # Create protected directories
    src_dir = project_root / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")

    logs_dir = project_root / "logs"
    logs_dir.mkdir()
    (logs_dir / "audit.log").write_text("{}\n", encoding="utf-8")

    data_dir = project_root / "data"
    data_dir.mkdir()
    (data_dir / "memory.db").write_text("sqlite db content\n", encoding="utf-8")

    browser_dir = project_root / ".browser_profile"
    browser_dir.mkdir()
    (browser_dir / "Preferences").write_text("{}\n", encoding="utf-8")

    # Unprotected user directories inside allowed root
    user_docs = project_root / "documents"
    user_docs.mkdir()
    (user_docs / "notes.txt").write_text("meeting notes\n", encoding="utf-8")

    # Nested directory containing a random .env file
    nested_dir = user_docs / "app"
    nested_dir.mkdir()
    (nested_dir / "local.env").write_text("API_PORT=8080\n", encoding="utf-8")
    (nested_dir / ".env").write_text("NESTED_SECRET=999\n", encoding="utf-8")

    protected_paths = [
        project_root / "config.yaml",
        project_root / "AGENT_RULES.md",
        project_root / "SECURITY.md",
        project_root / "src",
        project_root / "logs",
        project_root / "data",
        project_root / ".browser_profile",
        project_root / ".env",
    ]
    read_safe_files = [
        project_root / "README.md",
    ]

    return {
        "root": project_root,
        "protected_paths": protected_paths,
        "read_safe_files": read_safe_files,
        "user_docs": user_docs,
        "nested_dir": nested_dir,
    }


# -----------------------------------------------------------------------------
# 1. Protection for Every Defined Protected Path
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_target",
    [
        "config.yaml",
        "AGENT_RULES.md",
        "SECURITY.md",
        "src/main.py",
        "src/new_module.py",
        "logs/audit.log",
        "logs/new_log.log",
        "data/memory.db",
        "data/new_db.sqlite",
        ".browser_profile/Preferences",
        ".browser_profile/Cookies",
        ".env",
        "documents/app/local.env",
        "documents/app/.env",
        "random_sub/another.env",
    ],
)
def test_write_move_trash_refused_on_protected_paths(test_env, relative_target):
    """Write, move, copy-into, and trash are refused on each protected path."""
    root = test_env["root"]
    protected = test_env["protected_paths"]
    read_safe = test_env["read_safe_files"]
    target_path = root / relative_target

    write_tool = create_create_text_file_tool([root], protected, read_safe)
    move_tool = create_move_path_tool([root], protected, read_safe)
    copy_tool = create_copy_path_tool([root], protected, read_safe)
    trash_tool = create_move_to_trash_tool([root], protected, read_safe)

    # 1. Refuse create / overwrite
    with pytest.raises(ProtectedPathError):
        write_tool.handler(path=str(target_path), content="malicious overwrite")

    # 2. Refuse move (source or destination)
    # Target as destination
    with pytest.raises(ProtectedPathError):
        move_tool.handler(src=str(root / "documents" / "notes.txt"), dst=str(target_path))

    # Target as source (if it exists)
    if target_path.exists():
        with pytest.raises(ProtectedPathError):
            move_tool.handler(src=str(target_path), dst=str(root / "documents" / "exfiltrated.txt"))

    # 3. Refuse copy-into target
    with pytest.raises(ProtectedPathError):
        copy_tool.handler(src=str(root / "documents" / "notes.txt"), dst=str(target_path))

    # 4. Refuse trash (if target exists)
    if target_path.exists():
        with pytest.raises(ProtectedPathError):
            trash_tool.handler(path=str(target_path))


# -----------------------------------------------------------------------------
# 2. Path Traversal & Junction Escape to Protected Paths
# -----------------------------------------------------------------------------


def test_protected_path_via_parent_traversal_refused(test_env):
    """Attempting to access a protected path via '..' is refused."""
    root = test_env["root"]
    protected = test_env["protected_paths"]
    read_safe = test_env["read_safe_files"]

    write_tool = create_create_text_file_tool([root], protected, read_safe)

    # Attempting to reach config.yaml from documents/../config.yaml
    traversal_path = root / "documents" / ".." / "config.yaml"
    with pytest.raises(ProtectedPathError):
        write_tool.handler(path=str(traversal_path), content="evil config")

    # Attempting to reach src/main.py from documents/../src/main.py
    src_traversal = root / "documents" / ".." / "src" / "main.py"
    with pytest.raises(ProtectedPathError):
        write_tool.handler(path=str(src_traversal), content="evil code")


def test_protected_path_via_ntfs_junction_refused(test_env):
    """Attempting to access a protected directory via an NTFS directory junction is refused."""
    root = test_env["root"]
    protected = test_env["protected_paths"]
    read_safe = test_env["read_safe_files"]
    src_dir = root / "src"
    junction_path = root / "documents" / "junction_to_src"

    created = False
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(src_dir), str(junction_path))
        created = True
    else:
        try:
            os.symlink(src_dir, junction_path, target_is_directory=True)
            created = True
        except (OSError, NotImplementedError):
            pass

    assert created is True, "Junction or symlink creation must succeed"

    write_tool = create_create_text_file_tool([root], protected, read_safe)
    read_tool = create_read_text_file_tool([root], protected, read_safe)

    # Write through junction targeting file in src must be refused
    target_in_junction = junction_path / "injected.py"
    with pytest.raises(ProtectedPathError):
        write_tool.handler(path=str(target_in_junction), content="injected code")

    # Read through junction targeting src/main.py must be refused
    existing_in_junction = junction_path / "main.py"
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(existing_in_junction))


# -----------------------------------------------------------------------------
# 3. Read Protection & README.md Safe Allowlist
# -----------------------------------------------------------------------------


def test_reading_protected_paths_refused(test_env):
    """Reading protected paths is refused with ProtectedPathError."""
    root = test_env["root"]
    protected = test_env["protected_paths"]
    read_safe = test_env["read_safe_files"]

    read_tool = create_read_text_file_tool([root], protected, read_safe)

    # Refuse reading config.yaml
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(root / "config.yaml"))

    # Refuse reading AGENT_RULES.md
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(root / "AGENT_RULES.md"))

    # Refuse reading SECURITY.md
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(root / "SECURITY.md"))

    # Refuse reading src/main.py
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(root / "src" / "main.py"))

    # Refuse reading .env
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(root / ".env"))

    # Refuse reading arbitrary nested .env
    with pytest.raises(ProtectedPathError):
        read_tool.handler(path=str(root / "documents" / "app" / "local.env"))


def test_reading_readme_succeeds(test_env):
    """Reading README.md succeeds because it is in read_safe_files."""
    root = test_env["root"]
    protected = test_env["protected_paths"]
    read_safe = test_env["read_safe_files"]

    read_tool = create_read_text_file_tool([root], protected, read_safe)

    # Reading README.md is permitted
    content = read_tool.handler(path=str(root / "README.md"))
    assert "Readme Documentation" in content


# -----------------------------------------------------------------------------
# 4. Non-Protected Paths in allowed_roots Function Normally
# -----------------------------------------------------------------------------


def test_non_protected_paths_work_normally(test_env):
    """Paths in allowed_roots that are NOT protected allow write, read, move, trash normally."""
    root = test_env["root"]
    protected = test_env["protected_paths"]
    read_safe = test_env["read_safe_files"]

    write_tool = create_create_text_file_tool([root], protected, read_safe)
    read_tool = create_read_text_file_tool([root], protected, read_safe)
    move_tool = create_move_path_tool([root], protected, read_safe)
    copy_tool = create_copy_path_tool([root], protected, read_safe)
    trash_tool = create_move_to_trash_tool([root], protected, read_safe)

    # 1. Create a file in unprotected documents directory
    file_a = root / "documents" / "safe_file_a.txt"
    res_create = write_tool.handler(path=str(file_a), content="Safe content here")
    assert "Created new" in res_create
    assert file_a.exists()

    # 2. Read the file
    content = read_tool.handler(path=str(file_a))
    assert content == "Safe content here"

    # 3. Copy the file
    file_b = root / "documents" / "safe_file_b.txt"
    res_copy = copy_tool.handler(src=str(file_a), dst=str(file_b))
    assert "Copied" in res_copy
    assert file_b.exists()

    # 4. Move the copied file
    file_c = root / "documents" / "safe_file_c.txt"
    res_move = move_tool.handler(src=str(file_b), dst=str(file_c))
    assert "Moved" in res_move
    assert file_c.exists()
    assert not file_b.exists()

    # 5. Trash the moved file
    res_trash = trash_tool.handler(path=str(file_c))
    assert "Successfully moved" in res_trash
    assert not file_c.exists()


# -----------------------------------------------------------------------------
# 5. Config & Allowlist Immutability
# -----------------------------------------------------------------------------


def test_config_and_allowlists_are_immutable():
    """JarvisConfig is frozen and allowlists are wrapped in MappingProxyType."""
    config = load_config()

    # 1. JarvisConfig model is frozen: attribute modification raises ValidationError/TypeError
    with pytest.raises(Exception):
        config.confirm_timeout_seconds = 999.0

    with pytest.raises(Exception):
        config.commands = {}

    with pytest.raises(Exception):
        config.apps = {}

    # 2. SafetyConfig model is frozen
    with pytest.raises(Exception):
        config.safety.protected_paths = []

    with pytest.raises(Exception):
        config.safety.read_safe_files = []

    # 3. apps allowlist in create_open_app_tool is immutable (MappingProxyType)
    apps = {"notepad": "notepad.exe", "calc": "calc.exe"}
    tool_app = create_open_app_tool(apps)

    # External mutation of the passed dictionary does not alter the tool's mapping proxy
    apps["calc"] = "malicious_calc.exe"
    apps["evil"] = "evil.exe"

    # Original tool still launches original 'notepad.exe'
    assert tool_app.handler("notepad") is not None
    # Injected entry 'evil' was not picked up by the tool closure's snapshot
    with pytest.raises(ValueError):
        tool_app.handler("evil")

    # 4. commands allowlist in create_run_command_tool is immutable (MappingProxyType)
    cmds = {
        "git_status": CommandConfig(executable="git", args_schema=["^status$"]),
    }
    tool_cmd = create_run_command_tool(cmds, allowed_roots=[Path(".").resolve()])

    # Mutating cmds dict externally does not alter tool's mapping proxy
    cmds["git_status"] = CommandConfig(executable="git", args_schema=[".*"])
    cmds["malicious"] = CommandConfig(executable="cmd.exe")

    with pytest.raises(PermissionError):
        tool_cmd.handler("malicious")


# -----------------------------------------------------------------------------
# 6. is_env_file Helper Precision
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        (".env", True),
        (".env.local", True),
        (".env.production", True),
        (".env.test", True),
        ("app.env", True),
        ("local.env", True),
        ("secrets.env", True),
        ("my_file.env.bak", False),
        ("environment.txt", False),
        ("env.py", False),
        ("envelope.json", False),
    ],
)
def test_is_env_file_patterns(filename, expected):
    """Verify regex and suffix pattern matching for env files."""
    assert is_env_file(Path(filename)) is expected
