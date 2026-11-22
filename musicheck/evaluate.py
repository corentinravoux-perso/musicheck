"""Compare what a file claims with what it contains, and give a verdict.

Verdicts, from best to worst: ``OK``, ``SUSPECT``, ``FAKE``, ``ERROR``.
Each problem is an issue ``{"level", "code", "message"}``; ``INFO`` issues
describe the file without changing the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = [
    "Thresholds",
    "VERDICTS",
    "classify_cutoff",
    "expected_cutoff",
    "evaluate",
    "build_record",
]

VERDICTS = ("OK", "SUSPECT", "FAKE", "ERROR")
_RANK = {v: i for i, v in enumerate(VERDICTS)}

# LAME's default low-pass per CBR bitrate (lame.c, optimum_bandwidth).
LAME_LOWPASS = [
    (8, 2000), (16, 3700), (24, 3900), (32, 5500), (40, 7000), (48, 7500),
    (56, 10000), (64, 11000), (80, 13500), (96, 15100), (112, 15600),
    (128, 17000), (160, 17500), (192, 18600), (224, 19400), (256, 19700),
    (320, 20500),
]  # fmt: skip

# Conservative minima across common encoders: a file below these is suspicious
# whatever encoder made it. MP3 without a LAME tag allows for old encoders.
_TABLES = {
    "MP3": [(32, 5000), (64, 10000), (96, 13500), (128, 15500), (160, 16500),
            (192, 17500), (256, 18800), (320, 19800)],
    "MP2": [(64, 9000), (128, 14000), (192, 16000), (256, 18000), (384, 19500)],
    "AAC": [(32, 7000), (64, 11000), (96, 14000), (128, 15500), (160, 16500),
            (192, 17500), (256, 19000), (320, 19500)],
    "VORBIS": [(48, 9000), (64, 12000), (96, 14500), (128, 16000), (160, 17500),
               (192, 18500), (256, 19500), (320, 19800)],
    "OPUS": [(16, 7000), (24, 7500), (32, 11500), (48, 18500), (64, 19500)],
    "WMA": [(64, 11000), (96, 14000), (128, 15500), (192, 17500), (320, 19000)],
}  # fmt: skip

# (lowest cutoff, equivalent kbps, label), best first.
_CLASSES = [
    (20800, None, "full band"),
    (19900, 320, "~320k"),
    (19000, 256, "~256k"),
    (18000, 192, "~192k"),
    (17300, 160, "~160k"),
    (14500, 128, "~128k"),
    (11500, 96, "~96k"),
    (0, 64, "<=64k"),
]


@dataclass
class Thresholds:
    """Margins used by :func:`evaluate` (Hz unless stated)."""

    lossless_fake_hz: float = 19000.0
    lossless_suspect_hz: float = 20600.0
    lossy_fake_margin: float = 2500.0
    lossy_suspect_margin: float = 900.0
    lame_fake_margin: float = 1200.0
    lame_suspect_margin: float = 500.0
    upsample_factor: float = 1.18
    ultrasonic_empty_db: float = -95.0
    mono_side_db: float = -60.0
    clip_pct: float = 0.05
    truncated_s: float = 2.0
    quiet_ref_db: float = -100.0
    min_seconds: float = 5.0


def _interp(table: list[tuple[int, int]], kbps: float) -> float:
    xs, ys = zip(*table)
    return float(np.interp(kbps, xs, ys))


def expected_cutoff(
    fmt: str, kbps: float | None, lame_lowpass: float | None = None
) -> float | None:
    """Lowest cutoff a genuine file of this format and bitrate should reach.

    The encoder low-pass from a LAME header wins when present.
    """
    if lame_lowpass:
        return float(lame_lowpass)
    table = _TABLES.get(fmt)
    if table is None or not kbps:
        return None
    return _interp(table, kbps)


def classify_cutoff(band_hz: float | None) -> tuple[int | None, str]:
    """Map an audio bandwidth to the MP3 bitrate class it is typical of."""
    if band_hz is None:
        return None, "?"
    for low, kbps, label in _CLASSES:
        if band_hz >= low:
            return kbps, label
    return 64, "<=64k"


def _upsample_source(cutoff: float, sr: int, th: Thresholds) -> str | None:
    """Source rate a hi-res file was upsampled from, judged by its cutoff."""
    for rates, label in (
        ((44100, 48000), "44.1/48 kHz"),
        ((88200, 96000), "88.2/96 kHz"),
    ):
        if max(rates) < sr and cutoff < max(rates) / 2 * th.upsample_factor:
            return label
    return None


def _khz(hz: float | None) -> str:
    return f"{hz / 1000:.1f} kHz" if hz else "?"


def _issue(level: str, code: str, message: str) -> dict:
    return {"level": level, "code": code, "message": message}


def evaluate(meta: dict, ana: dict | None, th: Thresholds | None = None) -> dict:
    """Judge one file from its :func:`~musicheck.metadata.probe` and
    :func:`~musicheck.analysis.analyze` results.

    Returns
    -------
    dict
        ``verdict``, ``issues``, ``band_hz`` (cutoff or Nyquist), ``est_kbps``,
        ``real`` (bitrate class label) and ``expected_hz``.
    """
    th = th or Thresholds()
    issues: list[dict] = []
    out = {"verdict": "OK", "issues": issues, "band_hz": None, "est_kbps": None,
           "real": "?", "expected_hz": None}  # fmt: skip

    if ana is None or ana.get("error"):
        msg = (ana or {}).get("error") or meta.get("probe_error") or "unreadable"
        issues.append(_issue("ERROR", "UNREADABLE", msg))
        out["verdict"] = "ERROR"
        return out

    fmt = meta.get("format") or ana.get("format") or "?"
    lossless = meta.get("lossless")
    if lossless is None:
        from .metadata import LOSSLESS_FORMATS

        lossless = fmt in LOSSLESS_FORMATS
    sr = ana["sample_rate"]
    nyq = sr / 2.0
    cutoff = ana.get("cutoff_hz")
    band = cutoff if cutoff else nyq
    est_kbps, real = classify_cutoff(band)
    out.update(band_hz=band, est_kbps=est_kbps, real=real)

    analysed = ana.get("decoded_duration") or 0.0
    quiet = ana.get("ref_db") is None or ana["ref_db"] < th.quiet_ref_db
    judge_spectrum = not quiet and analysed >= th.min_seconds
    if not judge_spectrum:
        issues.append(
            _issue("INFO", "TOO_QUIET", "too short or too quiet to judge the spectrum")
        )
        out["real"] = "?"
        out["est_kbps"] = None

    if judge_spectrum and lossless:
        if sr >= 88_200:
            if cutoff and cutoff < th.lossless_fake_hz:
                issues.append(_issue("FAKE", "LOSSY_SOURCE",
                    f"hi-res file whose content stops at {_khz(cutoff)}: made from a lossy {real} source"))  # fmt: skip
            elif cutoff and (src := _upsample_source(cutoff, sr, th)):
                issues.append(_issue("FAKE", "UPSAMPLED",
                    f"{sr / 1000:g} kHz file whose content stops at {_khz(cutoff)}: upsampled from {src}"))  # fmt: skip
            elif (
                ana.get("ultrasonic_db") is not None
                and ana["ultrasonic_db"] < th.ultrasonic_empty_db
            ):
                issues.append(_issue("SUSPECT", "NO_ULTRASONIC",
                    f"nothing above 24 kHz in a {sr / 1000:g} kHz file: probably upsampled"))  # fmt: skip
        elif cutoff and cutoff < th.lossless_fake_hz:
            issues.append(_issue("FAKE", "LOSSY_SOURCE",
                f"lossless file whose content stops at {_khz(cutoff)}: transcoded from a lossy {real} source"))  # fmt: skip
        elif cutoff and cutoff < th.lossless_suspect_hz:
            issues.append(_issue("SUSPECT", "LOSSY_SOURCE",
                f"cutoff at {_khz(cutoff)}, typical of a 256-320 kbps lossy source (some masters are low-passed this way)"))  # fmt: skip

    elif judge_spectrum:
        kbps = meta.get("bitrate_kbps")
        lame_lp = meta.get("lame_lowpass_hz")
        expected = expected_cutoff(fmt, kbps, lame_lp)
        if expected is not None:
            expected = min(expected, nyq - 300.0)
            out["expected_hz"] = expected
            fake_m, susp_m = (
                (th.lame_fake_margin, th.lame_suspect_margin)
                if lame_lp
                else (th.lossy_fake_margin, th.lossy_suspect_margin)
            )
            deficit = expected - band
            claim = f"{fmt} {kbps} kbps" if kbps else fmt
            basis = (
                f"its encoder low-passed at {_khz(expected)}"
                if lame_lp
                else f"expected >= {_khz(expected)}"
            )
            halved = est_kbps is not None and kbps and est_kbps * 2 <= kbps
            if deficit > fake_m or (deficit > susp_m and halved and not lame_lp):
                issues.append(_issue("FAKE", "UPCONVERTED",
                    f"{claim} ({basis}) but content stops at {_khz(band)}: re-encoded from a {real} source"))  # fmt: skip
            elif deficit > susp_m:
                issues.append(_issue("SUSPECT", "UPCONVERTED",
                    f"{claim} ({basis}) but content stops at {_khz(band)}: maybe from a {real} source"))  # fmt: skip
            if lame_lp and kbps:
                default = _interp(LAME_LOWPASS, kbps)
                if lame_lp < default - 1500:
                    issues.append(_issue("INFO", "LOW_LOWPASS",
                        f"encoder set a low-pass of {_khz(lame_lp)} (LAME default at {kbps} kbps: {_khz(default)})"))  # fmt: skip

    bits = meta.get("bits") or ana.get("raw_bits")
    eff = ana.get("eff_bits")
    if lossless and bits and eff and eff < bits:
        level = "FAKE" if bits >= 24 and eff <= 16 else "SUSPECT"
        issues.append(
            _issue(
                level,
                "PADDED_BITS",
                f"{bits}-bit file using only {eff} bits: padded from {eff}-bit audio",
            )
        )

    if ana.get("decode_errors"):
        issues.append(
            _issue(
                "SUSPECT",
                "DECODE_ERRORS",
                f"{ana['decode_errors']} decode error(s): file damaged",
            )
        )
    header = meta.get("duration") or ana.get("header_duration")
    decoded = ana.get("decoded_duration") or 0.0
    if (
        header
        and not ana.get("partial")
        and header - decoded > max(th.truncated_s, 0.02 * header)
    ):
        issues.append(_issue("SUSPECT", "TRUNCATED",
            f"decodes to {decoded:.0f} s but the header says {header:.0f} s: truncated"))  # fmt: skip

    channels = ana.get("channels") or 0
    side = ana.get("side_db")
    if channels >= 2 and side is not None and side < th.mono_side_db:
        issues.append(
            _issue("INFO", "MONO", "both channels identical: mono stored as stereo")
        )
    if (ana.get("clip_pct") or 0.0) > th.clip_pct:
        issues.append(
            _issue("INFO", "CLIPPING", f"{ana['clip_pct']:.2f} % of samples clipped")
        )

    worst = max(
        (i["level"] for i in issues if i["level"] in _RANK), key=_RANK.get, default="OK"
    )
    out["verdict"] = worst
    return out


def _claimed(meta: dict, ana: dict | None) -> str:
    fmt = meta.get("format") or "?"
    sr = meta.get("sample_rate") or (ana or {}).get("sample_rate")
    if meta.get("lossless"):
        bits = meta.get("bits") or (ana or {}).get("raw_bits")
        rate = f"{sr / 1000:g}" if sr else "?"
        return f"{fmt} {bits or '?'}/{rate}"
    parts = [fmt]
    if meta.get("bitrate_kbps"):
        parts.append(f"{meta['bitrate_kbps']}k")
    mode = meta.get("bitrate_mode")
    if meta.get("lame_vbr_quality") is not None:
        parts.append(f"V{meta['lame_vbr_quality']}")
    elif mode and mode != "CBR":
        parts.append(mode)
    return " ".join(parts)


def _round(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(float(x), nd)


def build_record(
    path: str | Path,
    measured: dict,
    root: str | Path | None = None,
    th: Thresholds | None = None,
) -> dict:
    """Flatten probe + analysis + verdict into one row for display and sorting."""
    path = Path(path)
    meta = measured.get("meta") or {}
    ana = measured.get("analysis")
    if ana is None and measured.get("error"):
        ana = {"error": measured["error"]}
    ev = evaluate(meta, ana, th)
    ana = ana or {}
    try:
        rel = str(path.relative_to(root)) if root else str(path)
    except ValueError:
        rel = str(path)
    msgs = [i["message"] for i in ev["issues"] if i["level"] != "INFO"]
    notes = [i["message"] for i in ev["issues"] if i["level"] == "INFO"]
    fmt = meta.get("format")
    if not fmt and ana.get("codec"):
        from .metadata import format_from_codec

        fmt = format_from_codec(ana["codec"])
    return {
        "verdict": ev["verdict"],
        "real": ev["real"],
        "claimed": _claimed({**meta, "format": fmt}, ana),
        "cutoff_khz": _round((ana.get("cutoff_hz") or 0) / 1000)
        if ana.get("cutoff_hz")
        else None,
        "band_khz": _round(ev["band_hz"] / 1000) if ev["band_hz"] else None,
        "expected_khz": _round(ev["expected_hz"] / 1000) if ev["expected_hz"] else None,
        "est_kbps": ev["est_kbps"],
        "format": fmt,
        "lossless": meta.get("lossless"),
        "bitrate": meta.get("bitrate_kbps"),
        "mode": meta.get("bitrate_mode") or "",
        "sample_rate": ana.get("sample_rate") or meta.get("sample_rate"),
        "bits": meta.get("bits") or ana.get("raw_bits"),
        "eff_bits": ana.get("eff_bits"),
        "channels": ana.get("channels") or meta.get("channels"),
        "duration": _round(meta.get("duration") or ana.get("decoded_duration"), 1),
        "size_mb": _round((meta.get("size") or 0) / 1e6, 2),
        "dr": _round(ana.get("dr"), 1),
        "peak_db": _round(ana.get("peak_db"), 2),
        "rms_db": _round(ana.get("rms_db"), 1),
        "clip_pct": _round(ana.get("clip_pct"), 3),
        "stereo_db": _round(ana.get("side_db"), 1),
        "encoder": meta.get("lame_encoder") or meta.get("encoder") or "",
        "lowpass_khz": _round(meta["lame_lowpass_hz"] / 1000)
        if meta.get("lame_lowpass_hz")
        else None,
        "codec": ana.get("codec") or "",
        "decode_errors": ana.get("decode_errors"),
        "artist": meta.get("artist") or "",
        "album": meta.get("album") or "",
        "title": meta.get("title") or "",
        "year": meta.get("year") or "",
        "genre": meta.get("genre") or "",
        "track": meta.get("track") or "",
        "bpm": meta.get("bpm") or "",
        "key": meta.get("key") or "",
        "issues": "; ".join(msgs),
        "notes": "; ".join(notes),
        "file": path.name,
        "ext": path.suffix.lower().lstrip("."),
        "folder": str(Path(rel).parent),
        "path": rel,
        "abspath": str(path),
    }
