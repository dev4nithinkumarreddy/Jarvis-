"""Windows startup automation, desktop shortcut, and procedural Arc Reactor icon generator."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


def generate_arc_reactor_icon(output_path: Path | str = "data/icons/jarvis.ico") -> Path:
    """Generate an authentic Stark Arc Reactor .ico file procedurally using Pillow."""
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    images: list[Image.Image] = []

    for size in sizes:
        w, h = size
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        cx, cy = w / 2, h / 2
        r_outer = min(w, h) * 0.46
        r_inner = r_outer * 0.65
        r_core = r_outer * 0.32

        # 1. Dark outer ring background
        draw.ellipse(
            [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
            fill=(4, 10, 24, 255),
            outline=(0, 240, 255, 230),
            width=max(1, int(w * 0.04)),
        )

        # 2. Middle ring (10 electromagnetic repulsor coil markers)
        import math
        coil_r = (r_outer + r_inner) / 2
        for i in range(10):
            angle = i * (2 * math.pi / 10)
            bx = cx + math.cos(angle) * coil_r
            by = cy + math.sin(angle) * coil_r
            cr = max(1.5, w * 0.035)
            draw.ellipse([bx - cr, by - cr, bx + cr, by + cr], fill=(255, 183, 0, 240))

        # 3. Inner gyro ring
        draw.ellipse(
            [cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner],
            outline=(0, 240, 255, 200),
            width=max(1, int(w * 0.03)),
        )

        # 4. Center Glowing Core
        draw.ellipse(
            [cx - r_core, cy - r_core, cx + r_core, cy + r_core],
            fill=(255, 255, 255, 255),
            outline=(0, 240, 255, 255),
            width=max(1, int(w * 0.03)),
        )

        images.append(img)

    # Save as multi-resolution Windows ICO file
    images[-1].save(
        str(out_file),
        format="ICO",
        sizes=[(s[0], s[1]) for s in sizes],
        append_images=images[:-1],
    )
    logger.info("Arc Reactor .ico generated at %s", out_file)
    return out_file


def get_windows_startup_dir() -> Path:
    """Return path to user's Windows Startup directory."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def get_windows_desktop_dir() -> Path:
    """Return path to user's active Windows Desktop directory, resolving OneDrive redirection if present."""
    # 1. Check OneDrive environment variable
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or os.environ.get("OneDriveCommercial")
    if onedrive:
        od_desktop = Path(onedrive) / "Desktop"
        if od_desktop.exists():
            return od_desktop

    # 2. Check USERPROFILE / OneDrive / Desktop
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        up_od_desktop = Path(userprofile) / "OneDrive" / "Desktop"
        if up_od_desktop.exists():
            return up_od_desktop
        return Path(userprofile) / "Desktop"

    return Path.home() / "Desktop"


def create_windows_shortcut(
    shortcut_path: Path,
    target_path: Path,
    arguments: str = "",
    working_dir: Path | None = None,
    icon_path: Path | None = None,
    description: str = "J.A.R.V.I.S. Mark VII - Local AI Desktop Agent",
) -> bool:
    """Create a Windows .lnk shortcut using PowerShell WScript.Shell."""
    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = str(working_dir or target_path.parent).replace("'", "''")
        tgt = str(target_path).replace("'", "''")
        sc = str(shortcut_path).replace("'", "''")
        args = arguments.replace("'", "''")
        desc = description.replace("'", "''")
        icon_arg = ""
        if icon_path and icon_path.exists():
            clean_icon = str(icon_path).replace("'", "''")
            icon_arg = f"$s.IconLocation = '{clean_icon}, 0';"

        ps_script = f"""
        $w = New-Object -ComObject WScript.Shell;
        $s = $w.CreateShortcut('{sc}');
        $s.TargetPath = '{tgt}';
        $s.Arguments = '{args}';
        $s.WorkingDirectory = '{work_dir}';
        $s.Description = '{desc}';
        {icon_arg}
        $s.Save();
        """

        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0 and shortcut_path.exists()
    except Exception as exc:
        logger.warning("Failed to create Windows shortcut '%s': %s", shortcut_path, exc)
        return False


def create_desktop_shortcut(
    project_root: Path | None = None,
    use_pythonw: bool = True,
) -> Path:
    """Create a desktop shortcut to launch J.A.R.V.I.S. silently."""
    root = project_root or Path(__file__).resolve().parent.parent.parent.parent
    icon_path = root / "data" / "icons" / "jarvis.ico"
    if not icon_path.exists():
        generate_arc_reactor_icon(icon_path)

    desktop_dir = get_windows_desktop_dir()
    shortcut_path = desktop_dir / "J.A.R.V.I.S..lnk"

    # Target: pythonw.exe running jarvisw.pyw or -m jarvis.ui.cli run
    if use_pythonw:
        py_exe = Path(sys.executable).parent / "pythonw.exe"
        if not py_exe.exists():
            py_exe = Path(sys.executable)
    else:
        py_exe = Path(sys.executable)

    launcher_script = root / "jarvisw.pyw"
    args = f'"{launcher_script}"' if launcher_script.exists() else '-m jarvis.ui.cli app'

    success = create_windows_shortcut(
        shortcut_path=shortcut_path,
        target_path=py_exe,
        arguments=args,
        working_dir=root,
        icon_path=icon_path,
        description="J.A.R.V.I.S. Mark VII - Stark Industries AI Assistant",
    )

    if not success:
        raise RuntimeError(f"Could not create desktop shortcut at '{shortcut_path}'")

    print(f"Created desktop shortcut: {shortcut_path}")
    return shortcut_path


def is_autostart_enabled() -> bool:
    """Check if J.A.R.V.I.S. is registered in Windows Startup folder."""
    startup_file = get_windows_startup_dir() / "J.A.R.V.I.S..lnk"
    return startup_file.exists()


def enable_autostart(project_root: Path | None = None) -> Path:
    """Configure J.A.R.V.I.S. to start automatically on Windows user login."""
    root = project_root or Path(__file__).resolve().parent.parent.parent.parent
    icon_path = root / "data" / "icons" / "jarvis.ico"
    if not icon_path.exists():
        generate_arc_reactor_icon(icon_path)

    startup_dir = get_windows_startup_dir()
    startup_shortcut = startup_dir / "J.A.R.V.I.S..lnk"

    py_exe = Path(sys.executable).parent / "pythonw.exe"
    if not py_exe.exists():
        py_exe = Path(sys.executable)

    launcher_script = root / "jarvisw.pyw"
    args = f'"{launcher_script}"' if launcher_script.exists() else '-m jarvis.ui.cli run'

    success = create_windows_shortcut(
        shortcut_path=startup_shortcut,
        target_path=py_exe,
        arguments=args,
        working_dir=root,
        icon_path=icon_path,
        description="J.A.R.V.I.S. Mark VII Startup Agent",
    )

    if not success:
        raise RuntimeError(f"Failed to enable Windows autostart at '{startup_shortcut}'")

    print(f"Windows autostart enabled: {startup_shortcut}")
    return startup_shortcut


def disable_autostart() -> bool:
    """Remove J.A.R.V.I.S. from Windows Startup folder."""
    startup_shortcut = get_windows_startup_dir() / "J.A.R.V.I.S..lnk"
    if startup_shortcut.exists():
        try:
            startup_shortcut.unlink()
            print("Windows autostart disabled.")
            return True
        except Exception as exc:
            logger.warning("Failed to remove autostart shortcut: %s", exc)
            return False
    return False
