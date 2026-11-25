"""Find audio files, measure them in parallel, and cache the measurements.

The cache (SQLite, ``$XDG_CACHE_HOME/musicheck/cache.sqlite``) stores raw
measurements keyed by path, size and modification time. Verdicts are
recomputed from them on every run, so re-sorting or re-filtering a library is
instant and threshold changes need no rescan.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .metadata import AUDIO_EXTENSIONS

__all__ = [
    "ANALYSIS_VERSION",
    "Cache",
    "find_audio",
    "measure",
    "scan",
    "default_cache_path",
    "common_root",
]

ANALYSIS_VERSION = 1


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return Path(base) / "musicheck" / "cache.sqlite"


class Cache:
    """Measurements keyed by absolute path, invalidated by size/mtime/version."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS measures (path TEXT PRIMARY KEY, size INTEGER,"
            " mtime_ns INTEGER, version INTEGER, data TEXT)"
        )

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int]:
        st = path.stat()
        return st.st_size, st.st_mtime_ns

    def get(self, path: Path) -> dict | None:
        row = self.db.execute(
            "SELECT size, mtime_ns, version, data FROM measures WHERE path = ?",
            (str(path),),
        ).fetchone()
        if row is None:
            return None
        size, mtime, version, data = row
        if version != ANALYSIS_VERSION or (size, mtime) != self._stamp(path):
            return None
        return json.loads(data)

    def put(self, path: Path, measured: dict) -> None:
        size, mtime = self._stamp(path)
        self.db.execute(
            "INSERT OR REPLACE INTO measures VALUES (?, ?, ?, ?, ?)",
            (str(path), size, mtime, ANALYSIS_VERSION, json.dumps(measured)),
        )

    def carry(self, src: Path, dst: Path, keep_src: bool = True) -> bool:
        """Reuse the measurement of ``src`` for ``dst`` (a copy, move or link
        of the same audio). Returns False when ``src`` has no usable entry."""
        row = self.db.execute(
            "SELECT version, data FROM measures WHERE path = ?", (str(src),)
        ).fetchone()
        if row is None or row[0] != ANALYSIS_VERSION:
            return False
        size, mtime = self._stamp(Path(dst))
        self.db.execute(
            "INSERT OR REPLACE INTO measures VALUES (?, ?, ?, ?, ?)",
            (str(dst), size, mtime, ANALYSIS_VERSION, row[1]),
        )
        if not keep_src:
            self.db.execute("DELETE FROM measures WHERE path = ?", (str(src),))
        self.db.commit()
        return True

    def commit(self) -> None:
        self.db.commit()

    def clear(self) -> int:
        n = self.db.execute("SELECT COUNT(*) FROM measures").fetchone()[0]
        self.db.execute("DELETE FROM measures")
        self.db.commit()
        return n

    def close(self) -> None:
        self.db.commit()
        self.db.close()


def common_root(paths: Iterable[str | Path]) -> Path | None:
    """Folder that record paths are shown relative to."""
    resolved = [Path(p).expanduser().resolve() for p in paths]
    if not resolved:
        return None
    if len(resolved) == 1:
        return resolved[0] if resolved[0].is_dir() else resolved[0].parent
    try:
        return Path(os.path.commonpath(resolved))
    except ValueError:
        return None


def find_audio(paths: Iterable[str | Path], recursive: bool = True) -> list[Path]:
    """Audio files under ``paths`` (files are kept as given), sorted, absolute."""
    found: set[Path] = set()
    for p in paths:
        p = Path(p).expanduser().resolve()
        if p.is_file():
            found.add(p)
        elif p.is_dir():
            if recursive:
                for dirpath, dirnames, filenames in os.walk(p):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for name in filenames:
                        if Path(
                            name
                        ).suffix.lower() in AUDIO_EXTENSIONS and not name.startswith(
                            "._"
                        ):
                            found.add(Path(dirpath) / name)
            else:
                found.update(
                    c
                    for c in p.iterdir()
                    if c.is_file() and c.suffix.lower() in AUDIO_EXTENSIONS
                )
    return sorted(found)


def measure(path: str | Path, max_duration: float | None = None) -> dict:
    """Probe and analyse one file; never raises (errors go in ``error``)."""
    from .analysis import analyze
    from .metadata import probe

    path = Path(path)
    try:
        meta = probe(path)
    except Exception as exc:
        meta = {"probe_error": f"{type(exc).__name__}: {exc}"}
    try:
        ana = analyze(path, max_duration=max_duration)
    except Exception as exc:
        return {"meta": meta, "analysis": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"meta": meta, "analysis": ana, "error": None}


def scan(
    paths: Iterable[str | Path],
    *,
    recursive: bool = True,
    jobs: int | None = None,
    cache: Cache | None = None,
    max_duration: float | None = None,
    on_progress: Callable[[Path, int, int], None] | None = None,
) -> list[tuple[Path, dict]]:
    """Measure every audio file under ``paths``.

    Returns
    -------
    list of (pathlib.Path, dict)
        Path and raw measurement, in path order.
    """
    files = find_audio(paths, recursive)
    results: dict[Path, dict] = {}
    todo: list[Path] = []
    for f in files:
        hit = cache.get(f) if cache else None
        if hit is not None:
            results[f] = hit
        else:
            todo.append(f)

    total = len(files)
    done = len(results)
    if on_progress:
        on_progress(Path(), done, total)

    def record(f: Path, m: dict) -> None:
        nonlocal done
        results[f] = m
        if (
            cache
            and not m.get("error")
            and not (m.get("analysis") or {}).get("partial")
        ):
            cache.put(f, m)
        done += 1
        if on_progress:
            on_progress(f, done, total)

    jobs = jobs or os.cpu_count() or 1
    if jobs == 1 or len(todo) <= 1:
        for f in todo:
            record(f, measure(f, max_duration))
    else:
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(var, "1")  # one thread per worker process
        methods = multiprocessing.get_all_start_methods()
        ctx = multiprocessing.get_context(
            "forkserver" if "forkserver" in methods else "spawn"
        )
        with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as pool:
            futures = {pool.submit(measure, f, max_duration): f for f in todo}
            for i, fut in enumerate(as_completed(futures)):
                f = futures[fut]
                try:
                    m = fut.result()
                except Exception as exc:  # worker crash
                    m = {
                        "meta": {},
                        "analysis": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                record(f, m)
                if cache and i % 50 == 49:
                    cache.commit()
    if cache:
        cache.commit()
    return [(f, results[f]) for f in files]
