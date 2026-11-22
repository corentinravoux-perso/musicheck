"""Sort, filter, display and export scan records."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "FIELDS",
    "PRESETS",
    "parse_sort",
    "sort_records",
    "parse_filter",
    "filter_records",
    "render_table",
    "write_csv",
    "write_json",
    "summary_counts",
]

# field -> (column header, description)
FIELDS = {
    "verdict": ("Verdict", "OK, SUSPECT, FAKE or ERROR (sorts by severity)"),
    "real": ("Real", "quality the spectrum is typical of (~128k ... full band)"),
    "claimed": ("Claimed", "what the file says it is (format, bitrate, bits/rate)"),
    "cutoff_khz": ("Cutoff", "detected low-pass frequency, kHz (empty: none)"),
    "band_khz": ("Band", "audio bandwidth, kHz: the cutoff, or Nyquist if none"),
    "expected_khz": ("Expect", "cutoff a genuine lossy file should reach, kHz"),
    "est_kbps": ("Est.kbps", "bitrate class of the real content (empty: full band)"),
    "format": ("Format", "MP3, FLAC, AAC, ALAC, OPUS, VORBIS, WAV, ..."),
    "lossless": ("Lossless", "format is lossless"),
    "bitrate": ("kbps", "claimed (average) bitrate"),
    "mode": ("Mode", "CBR, VBR, ABR"),
    "sample_rate": ("Rate", "sample rate, Hz"),
    "bits": ("Bits", "declared bit depth (lossless)"),
    "eff_bits": ("Eff.bits", "bits actually used by the samples"),
    "channels": ("Ch", "number of channels"),
    "duration": ("Time", "duration, seconds"),
    "size_mb": ("MB", "file size, MB"),
    "dr": ("DR", "dynamic range, DR-meter style (higher: less compressed)"),
    "peak_db": ("Peak", "sample peak, dBFS"),
    "rms_db": ("RMS", "RMS level, dBFS"),
    "clip_pct": ("Clip%", "share of clipped samples, %"),
    "stereo_db": ("Side", "side/mid energy, dB (very low: mono as stereo)"),
    "encoder": ("Encoder", "encoder from the LAME header or tags"),
    "lowpass_khz": ("LAME lp", "low-pass recorded in the LAME header, kHz"),
    "codec": ("Codec", "FFmpeg decoder used"),
    "decode_errors": ("Errors", "decode errors"),
    "artist": ("Artist", "tag"),
    "album": ("Album", "tag"),
    "title": ("Title", "tag"),
    "year": ("Year", "tag"),
    "genre": ("Genre", "tag"),
    "track": ("Track", "tag"),
    "bpm": ("BPM", "tag"),
    "key": ("Key", "tag (initial key)"),
    "issues": ("Issues", "problems found"),
    "notes": ("Notes", "informational remarks (mono, clipping, ...)"),
    "file": ("File", "file name"),
    "ext": ("Ext", "file extension"),
    "folder": ("Folder", "folder relative to the scanned root"),
    "path": ("Path", "path relative to the scanned root"),
}

PRESETS = {
    "default": ["verdict", "real", "claimed", "cutoff_khz", "dr", "duration", "path"],
    "quality": ["verdict", "real", "claimed", "cutoff_khz", "expected_khz", "encoder",
                "eff_bits", "issues", "path"],
    "levels": ["verdict", "dr", "peak_db", "rms_db", "clip_pct", "stereo_db", "path"],
    "tags": ["verdict", "real", "artist", "album", "title", "year", "genre", "bpm", "key"],
    "dj": ["verdict", "real", "claimed", "bpm", "key", "artist", "title", "duration"],
    "all": list(FIELDS),
}  # fmt: skip

_MIN_WIDTH = {"verdict": 7, "real": 9, "claimed": 12, "path": 20, "issues": 20}
_SEVERITY = {"OK": 0, "SUSPECT": 1, "FAKE": 2, "ERROR": 3}
_VERDICT_STYLE = {
    "OK": "green",
    "SUSPECT": "yellow",
    "FAKE": "bold red",
    "ERROR": "magenta",
}


def _check_field(name: str) -> str:
    if name not in FIELDS:
        raise ValueError(f"unknown field {name!r}; run 'musicheck fields' for the list")
    return name


def parse_sort(spec: str | None) -> list[tuple[str, bool]]:
    """``"-verdict,cutoff_khz"`` -> ``[("verdict", True), ("cutoff_khz", False)]``.

    A leading ``-`` sorts that key in descending order.
    """
    keys = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        desc = part.startswith("-")
        keys.append((_check_field(part.lstrip("+-")), desc))
    return keys


def _key_value(rec: dict, field: str):
    v = rec.get(field)
    if field == "verdict":
        return _SEVERITY.get(v, -1)
    if isinstance(v, str):
        num = _as_number(v)
        return (
            num if num is not None and field in ("year", "track", "bpm") else v.lower()
        )
    return v


def sort_records(records: list[dict], keys: list[tuple[str, bool]]) -> list[dict]:
    """Stable multi-key sort; empty values always go last."""
    out = list(records)
    for field, desc in reversed(keys):
        present = [r for r in out if _key_value(r, field) not in (None, "")]
        missing = [r for r in out if _key_value(r, field) in (None, "")]
        try:
            present.sort(key=lambda r: _key_value(r, field), reverse=desc)
        except TypeError:  # mixed numbers and text
            present.sort(key=lambda r: str(_key_value(r, field)), reverse=desc)
        out = present + missing
    return out


_FILTER_RE = re.compile(r"^\s*([a-z_]+)\s*(==|=|!=|>=|<=|>|<|!~|~)\s*(.*?)\s*$")


def _as_number(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def parse_filter(expr: str):
    """Compile ``field<op>value`` into a predicate.

    Operators: ``=`` ``!=`` ``<`` ``<=`` ``>`` ``>=`` (numeric when both sides
    are numbers), ``~`` / ``!~`` (case-insensitive substring). ``=`` accepts
    several values separated by ``|``: ``verdict=FAKE|SUSPECT``.
    """
    m = _FILTER_RE.match(expr)
    if not m:
        raise ValueError(
            f"bad filter {expr!r}; expected field<op>value, e.g. verdict!=OK"
        )
    field, op, raw = m.groups()
    _check_field(field)
    if op == "==":
        op = "="
    choices = [c.strip().lower() for c in raw.split("|")]

    def pred(rec: dict) -> bool:
        v = rec.get(field)
        if isinstance(v, bool):
            text = "true" if v else "false"
        else:
            text = "" if v is None else str(v).lower()
        if op in ("~", "!~"):
            hit = any(c in text for c in choices)
            return hit if op == "~" else not hit
        if op in ("=", "!="):
            num = _as_number(v)
            hit = any(
                text == c
                or (
                    num is not None
                    and _as_number(c) is not None
                    and num == _as_number(c)
                )
                for c in choices
            )
            return hit if op == "=" else not hit
        a, b = _as_number(v), _as_number(raw)
        if a is None or b is None:
            if v in (None, ""):
                return False
            a, b = text, raw.lower()
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]

    return pred


def filter_records(records: Iterable[dict], exprs: Iterable[str]) -> list[dict]:
    preds = [parse_filter(e) for e in exprs]
    return [r for r in records if all(p(r) for p in preds)]


def resolve_columns(spec: str | None) -> list[str]:
    if not spec:
        return PRESETS["default"]
    cols: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if part in PRESETS:
            cols.extend(PRESETS[part])
        elif part:
            cols.append(_check_field(part))
    return list(dict.fromkeys(cols))


def _fmt(field: str, v) -> str:
    if v is None or v == "":
        return "-" if field in ("cutoff_khz",) else ""
    if field == "duration":
        m, s = divmod(int(round(v)), 60)
        return f"{m}:{s:02d}"
    if field == "sample_rate":
        return f"{v / 1000:g}k"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def summary_counts(records: Iterable[dict]) -> Counter:
    return Counter(r["verdict"] for r in records)


def render_table(
    records: list[dict], columns: list[str], console=None, title: str | None = None
) -> None:
    """Print records as a rich table, verdicts coloured."""
    from rich.console import Console
    from rich.table import Table

    console = console or Console()
    table = Table(title=title, header_style="bold", pad_edge=False, expand=False)
    for col in columns:
        header = FIELDS[col][0]
        numeric = col not in ("verdict", "real", "claimed", "format", "mode", "encoder", "codec",
                              "artist", "album", "title", "genre", "key", "issues", "notes",
                              "file", "ext", "folder", "path", "year", "track")  # fmt: skip
        wrap = col in (
            "path",
            "issues",
            "notes",
            "title",
            "album",
            "artist",
            "folder",
            "file",
        )
        table.add_column(header, justify="right" if numeric else "left",
                         overflow="fold" if wrap else "ellipsis", no_wrap=not wrap,
                         min_width=_MIN_WIDTH.get(col))  # fmt: skip
    for rec in records:
        cells = []
        for col in columns:
            text = _fmt(col, rec.get(col))
            if col == "verdict":
                text = f"[{_VERDICT_STYLE.get(text, '')}]{text}[/]"
            else:
                text = text.replace("[", r"\[")
            cells.append(text)
        table.add_row(*cells)
    console.print(table)


def write_csv(
    records: list[dict], path: str | Path, columns: list[str] | None = None
) -> None:
    cols = columns or (list(records[0]) if records else list(FIELDS))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_json(records: list[dict], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=1, ensure_ascii=False)
