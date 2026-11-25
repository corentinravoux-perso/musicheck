"""Open the app in a native window (pywebview), or in the web browser."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from pathlib import Path

from .server import Library, make_server

__all__ = ["run_app", "config_path", "last_roots"]


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(base) / "musicheck" / "app.json"


def last_roots() -> list[str]:
    """Folders opened last time that still exist."""
    try:
        roots = json.loads(config_path().read_text("utf-8")).get("roots") or []
    except (OSError, ValueError):
        return []
    return [r for r in roots if isinstance(r, str) and Path(r).exists()]


def _save_roots(roots: list[Path]) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"roots": [str(r) for r in roots]}), "utf-8")
    except OSError:
        pass


def _webview():
    try:
        import webview
    except ImportError:
        return None
    return webview


def run_app(
    roots=(),
    *,
    browser: bool = False,
    port: int = 0,
    jobs: int | None = None,
    use_cache: bool = True,
    debug: bool = False,
) -> None:
    """Serve the app and open it; returns when the window is closed
    (browser mode: on Ctrl-C)."""
    roots = list(roots) or last_roots()
    library = Library(roots, use_cache=use_cache, jobs=jobs)
    library.on_roots = _save_roots
    if library.roots:
        _save_roots(library.roots)
    server = make_server(library, port)
    threading.Thread(target=server.serve_forever, name="musicheck-http", daemon=True).start()
    if library.roots:
        library.start_scan()
    webview = None if browser else _webview()
    try:
        if webview is None:
            if not browser:
                print("pywebview is not installed: opening the browser instead "
                      "(pip install 'musicheck[app]' for a window).")  # fmt: skip
            print(f"musicheck is running at {server.url}  (Ctrl-C to stop)")
            webbrowser.open(server.url)
            threading.Event().wait()
        else:
            _window(webview, server, debug)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def _window(webview, server, debug: bool) -> None:
    try:  # GNOME matches the window to musicheck.desktop by this name
        from gi.repository import GLib

        GLib.set_prgname("musicheck")
        GLib.set_application_name("musicheck")
    except Exception:
        pass
    window = webview.create_window(
        "musicheck", server.url, width=1440, height=900, min_size=(980, 620),
        background_color="#EEF0F2", text_select=True,
    )  # fmt: skip

    def pick_folder() -> str | None:
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:
            kind = webview.FOLDER_DIALOG
        res = window.create_file_dialog(kind)
        return str(res[0]) if res else None

    server.folder_picker = pick_folder
    webview.start(debug=debug, private_mode=True)
