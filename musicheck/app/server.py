"""Local HTTP server behind the desktop app: JSON API, static files, audio.

The server binds to 127.0.0.1 only. Every API call must carry the session
token (header ``X-Musicheck-Token``, or ``?t=`` for media URLs that the
browser fetches by itself) and a ``Host`` header naming this server, so other
local pages cannot drive it (no CSRF, no DNS rebinding).
"""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import secrets
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import __version__
from ..evaluate import Thresholds, build_record, evaluate
from ..organize import apply as apply_moves
from ..organize import plan as plan_moves
from ..scan import Cache, common_root, scan

__all__ = ["Library", "make_server", "track_id"]

_MIME = {
    ".mp3": "audio/mpeg", ".mp2": "audio/mpeg", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".m4b": "audio/mp4",
    ".aac": "audio/aac", ".alac": "audio/mp4", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
    ".aif": "audio/aiff", ".aiff": "audio/aiff", ".aifc": "audio/aiff",
    ".wv": "audio/x-wavpack", ".ape": "audio/x-ape", ".wma": "audio/x-ms-wma",
}  # fmt: skip
_COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
_MODES = ("copy", "move", "link", "hardlink")


def track_id(path: str | Path) -> str:
    """Stable short id of a file, from its absolute path."""
    return hashlib.sha1(str(path).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _finite(obj):
    """Replace NaN and infinities (invalid in JSON) by None, recursively."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _issues(measured: dict) -> list[dict]:
    meta = measured.get("meta") or {}
    ana = measured.get("analysis")
    if ana is None and measured.get("error"):
        ana = {"error": measured["error"]}
    return evaluate(meta, ana)["issues"]


class Library:
    """The scanned folders, their records and the background scan.

    Parameters
    ----------
    roots : list of path
        Folders (or files) to scan.
    cache_path : path, optional
        Measurement cache; default ``~/.cache/musicheck/cache.sqlite``.
    use_cache : bool
        Read and update the cache.
    jobs : int, optional
        Parallel workers for a scan (default: all cores).
    """

    def __init__(self, roots=(), cache_path=None, use_cache=True, jobs=None):
        self.lock = threading.Lock()
        self.cache_path = cache_path
        self.use_cache = use_cache
        self.jobs = jobs
        self.roots: list[Path] = []
        self.root: Path | None = None
        self.records: dict[str, dict] = {}
        self.measured: dict[str, dict] = {}
        self.generation = 0
        self.scan_state = {"running": False, "done": 0, "total": 0, "current": "",
                           "error": None, "finished_at": None}  # fmt: skip
        self._thread: threading.Thread | None = None
        self.on_roots = None  # called with the new roots after set_roots
        if roots:
            self.set_roots(roots)

    # -- state ---------------------------------------------------------------

    def set_roots(self, roots) -> None:
        paths = [Path(r).expanduser().resolve() for r in roots]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise ValueError(f"not found: {', '.join(missing)}")
        with self.lock:
            if self.scan_state["running"]:
                raise RuntimeError("a scan is running")
            self.roots = paths
            self.root = common_root(paths)
            self.records, self.measured = {}, {}
            self.generation += 1
        if self.on_roots:
            self.on_roots(paths)

    def state(self) -> dict:
        with self.lock:
            return {
                "version": __version__,
                "roots": [str(r) for r in self.roots],
                "root": str(self.root) if self.root else None,
                "home": str(Path.home()),
                "generation": self.generation,
                "count": len(self.records),
                "scan": dict(self.scan_state),
            }

    def tracks(self) -> dict:
        with self.lock:
            return {"generation": self.generation, "root": str(self.root or ""),
                    "tracks": list(self.records.values())}  # fmt: skip

    def spectra(self, points: int = 128) -> dict:
        """Every track's averaged spectrum, pooled to ``points`` values (dB)."""
        with self.lock:
            items = [(tid, m) for tid, m in self.measured.items()]
            gen = self.generation
        out = {}
        for tid, m in items:
            ana = (m or {}).get("analysis") or {}
            db = ana.get("spectrum_db") or []
            if not db:
                continue
            k = max(1, len(db) // points)
            pooled = [round(sum(db[i:i + k]) / len(db[i:i + k]), 1)
                      for i in range(0, len(db), k)]  # fmt: skip
            out[tid] = {"db": pooled, "nyquist_hz": (ana.get("sample_rate") or 0) / 2}
        return {"generation": gen, "spectra": out}

    def detail(self, tid: str) -> dict | None:
        with self.lock:
            rec = self.records.get(tid)
            measured = self.measured.get(tid)
        if rec is None:
            return None
        measured = measured or {}
        ana = measured.get("analysis") or {}
        meta = measured.get("meta") or {}
        sr = ana.get("sample_rate") or rec.get("sample_rate")
        th = Thresholds()
        ref_khz, ref_label = rec.get("expected_khz"), "expected"
        if ref_khz is None and rec.get("lossless") and sr:
            if sr < 88_200:
                ref_khz, ref_label = th.lossless_suspect_hz / 1000, "lossless"
            else:  # content a genuine hi-res file has above its upsampling source
                src_nyq = 24.0 if sr <= 96_000 else 48.0
                ref_khz, ref_label = round(src_nyq * th.upsample_factor, 1), "hi-res"
        return {
            "track": rec,
            "issues": _issues(measured),
            "spectrum": {
                "db": ana.get("spectrum_db") or [],
                "nyquist_hz": sr / 2 if sr else None,
                "ref_db": ana.get("ref_db"),
                "reference_khz": ref_khz,
                "reference_label": ref_label if ref_khz else None,
            },
            "extra": {
                "lame_method": meta.get("lame_method"),
                "lame_vbr_quality": meta.get("lame_vbr_quality"),
                "encoded_by": meta.get("encoded_by") or "",
                "header_duration": ana.get("header_duration"),
                "decoded_duration": ana.get("decoded_duration"),
                "stereo_corr": ana.get("stereo_corr"),
                "overs": ana.get("overs"),
                "ultrasonic_db": ana.get("ultrasonic_db"),
                "cutoff_drop_db": ana.get("cutoff_drop_db"),
            },
            "has_cover": find_cover(Path(rec["abspath"])) is not None,
        }

    def path_of(self, tid: str) -> Path | None:
        with self.lock:
            rec = self.records.get(tid)
        return Path(rec["abspath"]) if rec else None

    def records_for(self, ids) -> list[dict]:
        with self.lock:
            return [self.records[i] for i in ids if i in self.records]

    # -- scanning ------------------------------------------------------------

    def start_scan(self) -> bool:
        """Start a background scan; False when one is already running."""
        with self.lock:
            if self.scan_state["running"] or not self.roots:
                return False
            self.scan_state.update(running=True, done=0, total=0, current="", error=None)
            self._thread = threading.Thread(target=self._scan, name="musicheck-scan",
                                            daemon=True)  # fmt: skip
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        """Block until the running scan, if any, ends (used by tests)."""
        if self._thread:
            self._thread.join(timeout)

    def _progress(self, path: Path, done: int, total: int) -> None:
        with self.lock:
            self.scan_state.update(done=done, total=total,
                                   current=path.name if path.name else "")  # fmt: skip

    def _scan(self) -> None:
        roots = list(self.roots)
        cache = Cache(self.cache_path) if self.use_cache else None
        try:
            measured = scan(roots, jobs=self.jobs, cache=cache, on_progress=self._progress)
            root = common_root(roots)
            records, raw = {}, {}
            for path, m in measured:
                tid = track_id(path)
                rec = build_record(path, m, root)
                rec["id"] = tid
                records[tid] = rec
                raw[tid] = m
            with self.lock:
                if roots == self.roots:  # folder not changed meanwhile
                    self.records, self.measured = records, raw
                    self.generation += 1
                self.scan_state.update(running=False, current="", finished_at=time.time())
        except Exception as exc:  # report, keep serving
            with self.lock:
                self.scan_state.update(running=False, current="",
                                       error=f"{type(exc).__name__}: {exc}")  # fmt: skip
        finally:
            if cache:
                cache.close()

    # -- organize ------------------------------------------------------------

    def plan(self, body: dict) -> tuple[list, str]:
        ids = body.get("ids") or []
        dest = str(body.get("dest") or "").strip()
        template = str(body.get("template") or "").strip()
        mode = body.get("mode") or "copy"
        if not ids:
            raise ValueError("no track selected")
        if not dest:
            raise ValueError("choose a destination folder")
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r}")
        records = self.records_for(ids)
        try:
            moves = plan_moves(records, dest, template or ".", bool(body.get("keep_tree")))
        except KeyError as exc:
            raise ValueError(f"unknown field {exc} in template") from exc
        return moves, mode

    def apply(self, body: dict) -> dict:
        moves, mode = self.plan(body)
        with self.lock:
            if self.scan_state["running"]:
                raise RuntimeError("wait for the scan to finish")
        done, errors = apply_moves(moves, mode)
        if self.use_cache and done:
            cache = Cache(self.cache_path)
            try:
                for m in moves:
                    if not m.skip and m.dst.exists():
                        cache.carry(m.src, m.dst, keep_src=mode != "move")
            finally:
                cache.close()
        return {"done": done, "errors": errors, "mode": mode}


def find_cover(path: Path) -> tuple[bytes, str] | None:
    """Embedded front cover of an audio file, else an image in its folder."""
    try:
        import mutagen

        obj = mutagen.File(path)
    except Exception:
        obj = None
    if obj is not None:
        pics = list(getattr(obj, "pictures", None) or [])  # FLAC, Ogg
        tags = getattr(obj, "tags", None)
        if tags is not None:
            try:
                pics += [f for k, f in tags.items() if str(k).startswith("APIC")]
            except AttributeError:
                pass
            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                fmt = getattr(covr[0], "imageformat", 13)
                mime = "image/png" if fmt == 14 else "image/jpeg"
                return bytes(covr[0]), mime
        if pics:
            front = next((p for p in pics if getattr(p, "type", 0) == 3), pics[0])
            return bytes(front.data), getattr(front, "mime", "") or "image/jpeg"
    try:
        for child in sorted(path.parent.iterdir()):
            if child.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and \
                    child.stem.lower() in _COVER_NAMES:  # fmt: skip
                mime = mimetypes.guess_type(child.name)[0] or "image/jpeg"
                return child.read_bytes(), mime
    except OSError:
        pass
    return None


def reveal(path: Path) -> None:
    """Show ``path`` selected in the file manager (folder opened as fallback)."""
    uri = path.as_uri()
    if shutil.which("gdbus"):
        res = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
             "--object-path", "/org/freedesktop/FileManager1",
             "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
            capture_output=True, timeout=5,
        )  # fmt: skip
        if res.returncode == 0:
            return
    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        raise RuntimeError("no file manager found")
    subprocess.Popen([opener, str(path.parent)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)  # fmt: skip


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- plumbing ------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        extra = {"Cache-Control": "no-store", **(extra or {})}
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        body = json.dumps(_finite(obj), ensure_ascii=False, allow_nan=False, default=str)
        self._send(status, body.encode(), "application/json")

    def _error(self, status: int, message: str):
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in self.server.allowed_hosts

    def _token_ok(self, query: dict) -> bool:
        tok = self.headers.get("X-Musicheck-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(tok, self.server.token)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("request too large")
        data = self.rfile.read(n) if n else b"{}"
        obj = json.loads(data or b"{}")
        if not isinstance(obj, dict):
            raise ValueError("expected a JSON object")
        return obj

    # -- routes --------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        if url.path in ("/", "/index.html"):
            return self._index()
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if not url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        if not self._token_ok(query):
            return self._error(HTTPStatus.FORBIDDEN, "bad token")
        lib = self.server.library
        parts = url.path.strip("/").split("/")
        if parts == ["api", "state"]:
            return self._json(lib.state())
        if parts == ["api", "tracks"]:
            return self._json(lib.tracks())
        if parts == ["api", "spectra"]:
            return self._json(lib.spectra())
        if len(parts) >= 3 and parts[1] == "track":
            tid = parts[2]
            if len(parts) == 3:
                d = lib.detail(tid)
                return self._json(d) if d else self._error(404, "unknown track")
            if parts[3] == "audio":
                return self._audio(lib.path_of(tid))
            if parts[3] == "cover":
                path = lib.path_of(tid)
                found = find_cover(path) if path else None
                if not found:
                    return self._error(404, "no cover")
                return self._send(200, found[0], found[1])
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        url = urlsplit(self.path)
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        if not self._token_ok({}):
            return self._error(HTTPStatus.FORBIDDEN, "bad token")
        lib = self.server.library
        try:
            body = self._body()
            if url.path == "/api/scan":
                started = lib.start_scan()
                return self._json({"started": started, "state": lib.state()})
            if url.path == "/api/open":
                paths = body.get("paths") or []
                if isinstance(paths, str):
                    paths = [paths]
                lib.set_roots(paths)
                lib.start_scan()
                return self._json(lib.state())
            if url.path == "/api/pick-folder":
                picker = self.server.folder_picker
                if picker is None:
                    return self._error(501, "no folder dialog in browser mode")
                return self._json({"path": picker()})
            if url.path == "/api/reveal":
                path = lib.path_of(str(body.get("id")))
                if path is None:
                    return self._error(404, "unknown track")
                reveal(path)
                return self._json({"ok": True})
            if url.path == "/api/organize/plan":
                moves, mode = lib.plan(body)
                rows = [{"src": str(m.src), "dst": str(m.dst), "skip": m.skip} for m in moves]
                todo = sum(1 for m in moves if not m.skip)
                return self._json({"moves": rows, "todo": todo, "mode": mode})
            if url.path == "/api/organize/apply":
                if body.get("confirm") is not True:
                    return self._error(400, "confirmation missing")
                result = lib.apply(body)
                lib.start_scan()
                return self._json(result)
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except RuntimeError as exc:
            return self._error(HTTPStatus.CONFLICT, str(exc))
        except (OSError, subprocess.SubprocessError) as exc:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    # -- files ---------------------------------------------------------------

    def _index(self):
        html = resources.files("musicheck.app").joinpath("static/index.html").read_text("utf-8")
        html = html.replace("__MUSICHECK_TOKEN__", self.server.token)
        html = html.replace("__MUSICHECK_DESKTOP__", "1" if self.server.folder_picker else "0")
        self._send(200, html.encode(), "text/html; charset=utf-8",
                   {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                    "media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                    "connect-src 'self'; frame-ancestors 'none'"})  # fmt: skip

    def _static(self, name: str):
        parts = name.split("/")
        if len(parts) > 2 or (len(parts) == 2 and parts[0] != "fonts") or \
                any(not p or p.startswith(".") for p in parts):  # fmt: skip
            return self._error(404, "not found")
        res = resources.files("musicheck.app").joinpath("static")
        for part in parts:  # one segment per call: Traversable on Python 3.10
            res = res.joinpath(part)
        if not res.is_file():
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(name)[0] or (
            "font/woff2" if name.endswith(".woff2") else "application/octet-stream")
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
            ctype += "; charset=utf-8"
        self._send(200, res.read_bytes(), ctype, {"Cache-Control": "max-age=3600"}
                   if parts[0] == "fonts" else None)

    def _audio(self, path: Path | None):
        if path is None or not path.is_file():
            return self._error(404, "unknown track")
        size = path.stat().st_size
        ctype = _MIME.get(path.suffix.lower(), "application/octet-stream")
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a:
                    start = int(a)
                    end = min(int(b), size - 1) if b else size - 1
                else:  # suffix range: last N bytes
                    start, end = max(0, size - int(b)), size - 1
            except ValueError:
                start, end = 0, size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the player seeks by dropping connections


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, library: Library, token: str):
        super().__init__(addr, _Handler)
        self.library = library
        self.token = token
        self.folder_picker = None  # set by the desktop window
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


def make_server(library: Library, port: int = 0) -> _Server:
    """Create (not start) the server on 127.0.0.1; ``port=0`` picks a free one."""
    return _Server(("127.0.0.1", port), library, secrets.token_urlsafe(24))
