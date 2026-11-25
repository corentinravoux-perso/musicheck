"""Add musicheck to the desktop's applications menu (freedesktop .desktop)."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

__all__ = ["install_desktop", "uninstall_desktop"]


def _data_home() -> Path:
    import os

    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _paths() -> tuple[Path, Path]:
    data = _data_home()
    return (data / "applications" / "musicheck.desktop",
            data / "icons" / "hicolor" / "scalable" / "apps" / "musicheck.svg")  # fmt: skip


def install_desktop(python: str | None = None) -> list[Path]:
    """Write the launcher and icon for the current user; returns their paths."""
    entry, icon = _paths()
    exe = shlex.quote(python or sys.executable)
    entry.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(resources.files("musicheck.app").joinpath("static/icon.svg").read_bytes())
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=musicheck\n"
        "GenericName=Audio quality checker\n"
        "Comment=Find fake lossless and up-converted music files\n"
        f"Exec={exe} -m musicheck app %F\n"
        "Icon=musicheck\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;\n"
        "Keywords=music;audio;flac;mp3;spectrum;fake;transcode;\n"
        "StartupWMClass=musicheck\n",
        "utf-8",
    )
    for cmd in (["update-desktop-database", str(entry.parent)],
                ["gtk-update-icon-cache", "-q", str(icon.parents[3])]):  # fmt: skip
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True)
    return [entry, icon]


def uninstall_desktop() -> list[Path]:
    """Remove the launcher and icon; returns the paths that existed."""
    removed = []
    for path in _paths():
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
