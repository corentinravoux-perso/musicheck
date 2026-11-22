"""Read what a file claims about itself: format, bitrate, bit depth, tags."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .lame import read_lame_info

__all__ = ["AUDIO_EXTENSIONS", "LOSSLESS_FORMATS", "probe", "format_from_codec"]

AUDIO_EXTENSIONS = {
    ".mp3", ".mp2", ".flac", ".m4a", ".mp4", ".m4b", ".aac", ".alac", ".ogg",
    ".oga", ".opus", ".wav", ".aif", ".aiff", ".aifc", ".wv", ".ape", ".wma",
    ".tta", ".dsf", ".dff",
}  # fmt: skip

LOSSLESS_FORMATS = {
    "FLAC",
    "ALAC",
    "WAV",
    "AIFF",
    "WAVPACK",
    "APE",
    "TTA",
    "DSD",
    "WMA-L",
}

_CODEC_FORMATS = {
    "mp3": "MP3", "mp3float": "MP3", "mp2": "MP2", "mp2float": "MP2",
    "flac": "FLAC", "alac": "ALAC", "aac": "AAC", "aac_fixed": "AAC",
    "aac_latm": "AAC", "opus": "OPUS", "libopus": "OPUS", "vorbis": "VORBIS",
    "libvorbis": "VORBIS", "wavpack": "WAVPACK", "ape": "APE", "tta": "TTA",
    "wmav1": "WMA", "wmav2": "WMA", "wmapro": "WMA", "wmalossless": "WMA-L",
    "dsd_lsbf": "DSD", "dsd_msbf": "DSD", "dsd_lsbf_planar": "DSD",
    "dsd_msbf_planar": "DSD", "ac3": "AC3", "eac3": "EAC3",
}  # fmt: skip

_TAG_KEYS = {
    "artist": ["TPE1", "artist", "\xa9ART", "Author", "Artist"],
    "album": ["TALB", "album", "\xa9alb", "WM/AlbumTitle", "Album"],
    "title": ["TIT2", "title", "\xa9nam", "Title"],
    "year": ["TDRC", "TYER", "date", "year", "\xa9day", "WM/Year", "Year"],
    "genre": ["TCON", "genre", "\xa9gen", "WM/Genre", "Genre"],
    "track": ["TRCK", "tracknumber", "trkn", "WM/TrackNumber", "Track"],
    "bpm": ["TBPM", "bpm", "tmpo", "WM/BeatsPerMinute", "BPM"],
    "key": [
        "TKEY", "initialkey", "key", "----:com.apple.iTunes:initialkey",
        "WM/InitialKey",
    ],
    "encoded_by": [
        "TSSE", "TENC", "encoder", "encoded-by", "\xa9too",
        "WM/EncodingSettings", "WM/ToolName",
    ],
}  # fmt: skip


def format_from_codec(codec: str) -> str:
    """Map an FFmpeg decoder name to a format label (``"MP3"``, ``"FLAC"``)."""
    if codec.startswith("pcm_"):
        return "WAV"
    return _CODEC_FORMATS.get(codec, codec.upper())


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "text"):  # ID3 frame
        text = value.text
        return _to_text(text[0] if isinstance(text, list) and text else text)
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):  # ASF
        return _to_text(value.value)
    if isinstance(value, (list, tuple)):
        return _to_text(value[0]) if value else ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00 ")
    return str(value).strip()


def _tag(tags: Any, keys: list[str]) -> str:
    if not tags:
        return ""
    for key in keys:
        try:
            value = tags[key]
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        text = _to_text(value)
        if text:
            return text
    return ""


def _format_of(obj: Any, ext: str) -> str:
    name = type(obj).__name__
    info = obj.info
    if name in ("MP3", "EasyMP3"):
        return "MP2" if getattr(info, "layer", 3) == 2 else "MP3"
    if name in ("MP4", "EasyMP4"):
        codec = getattr(info, "codec", "") or ""
        if codec.startswith("alac"):
            return "ALAC"
        if codec.startswith("mp4a"):
            return "AAC"
        return codec.upper() or "MP4"
    if name == "ASF":
        codec = (getattr(info, "codec_name", "") or "").lower()
        return "WMA-L" if "lossless" in codec else "WMA"
    return {
        "FLAC": "FLAC", "OggFLAC": "FLAC", "OggVorbis": "VORBIS",
        "OggOpus": "OPUS", "WAVE": "WAV", "AIFF": "AIFF", "WavPack": "WAVPACK",
        "MonkeysAudio": "APE", "TrueAudio": "TTA", "AAC": "AAC", "DSF": "DSD",
        "DSDIFF": "DSD", "OggSpeex": "SPEEX", "OggTheora": "THEORA",
    }.get(name, ext.lstrip(".").upper())  # fmt: skip


def probe(path: str | Path) -> dict:
    """Read container metadata with mutagen.

    Returns
    -------
    dict
        ``format``, ``lossless``, ``bitrate_kbps``, ``bitrate_mode``,
        ``sample_rate``, ``bits``, ``channels``, ``duration``, ``encoder``,
        LAME fields (``lame_encoder``, ``lame_method``, ``lame_lowpass_hz``,
        ``lame_vbr_quality``) and tags. Missing values are ``None`` or ``""``.
        ``probe_error`` holds a message when mutagen cannot read the file.
    """
    import mutagen

    path = Path(path)
    size = os.path.getsize(path)
    out: dict = {
        "format": None, "lossless": None, "bitrate_kbps": None,
        "bitrate_mode": "", "sample_rate": None, "bits": None, "channels": None,
        "duration": None, "encoder": "", "size": size, "probe_error": None,
        "lame_encoder": "", "lame_method": None, "lame_lowpass_hz": None,
        "lame_vbr_quality": None,
    }  # fmt: skip
    for key in _TAG_KEYS:
        out[key] = ""

    try:
        obj = mutagen.File(path)
    except Exception as exc:  # mutagen raises many unrelated types
        out["probe_error"] = f"{type(exc).__name__}: {exc}"
        return out
    if obj is None:
        out["probe_error"] = "unrecognised by mutagen"
        return out

    info = obj.info
    fmt = _format_of(obj, path.suffix.lower())
    out["format"] = fmt
    out["lossless"] = fmt in LOSSLESS_FORMATS
    out["sample_rate"] = getattr(info, "sample_rate", None) or None
    out["channels"] = getattr(info, "channels", None) or None
    out["bits"] = getattr(info, "bits_per_sample", None) or None
    length = getattr(info, "length", None)
    out["duration"] = float(length) if length else None

    bitrate = getattr(info, "bitrate", None)
    if not bitrate and length:
        bitrate = size * 8 / length
    out["bitrate_kbps"] = round(bitrate / 1000) if bitrate else None

    mode = getattr(info, "bitrate_mode", None)
    if mode is not None:
        label = str(mode).rsplit(".", 1)[-1]  # BitrateMode.CBR -> CBR
        out["bitrate_mode"] = "" if label == "UNKNOWN" else label
    elif fmt in ("OPUS", "VORBIS"):
        out["bitrate_mode"] = "VBR"

    for key, keys in _TAG_KEYS.items():
        out[key] = _tag(obj.tags, keys)
    enc = " ".join(
        x
        for x in (
            getattr(info, "encoder_info", ""),
            getattr(info, "encoder_settings", ""),
        )
        if x
    )
    out["encoder"] = enc or out["encoded_by"]

    if fmt == "MP3":
        try:
            lame = read_lame_info(path)
        except OSError:
            lame = None
        if lame is not None:
            out["lame_encoder"] = lame.encoder
            out["lame_method"] = lame.method
            out["lame_lowpass_hz"] = lame.lowpass_hz
            out["lame_vbr_quality"] = lame.vbr_quality
            if not out["bitrate_mode"] and lame.method:
                out["bitrate_mode"] = lame.method
            if not out["encoder"] and lame.encoder:
                out["encoder"] = lame.encoder
    return out
