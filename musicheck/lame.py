"""Read the Xing/Info, LAME and VBRI headers at the start of an MP3 file.

The LAME tag records the encoder version, the bitrate method and the low-pass
the encoder applied. That low-pass is what the audio should reach: a file whose
header says 20.5 kHz but whose spectrum stops at 16 kHz was encoded from a
source that had already lost its treble.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LameInfo", "read_lame_info", "parse_lame_info"]

_VBR_METHODS = {
    1: "CBR",
    2: "ABR",
    3: "VBR",
    4: "VBR",
    5: "VBR",
    6: "VBR",
    8: "CBR",
    9: "ABR",
}


@dataclass
class LameInfo:
    """Fields read from the first MP3 frame.

    Attributes
    ----------
    tag : str
        ``"Xing"`` (VBR), ``"Info"`` (CBR) or ``"VBRI"`` (Fraunhofer).
    encoder : str
        Encoder string such as ``"LAME3.100"``, empty when absent.
    method : str or None
        ``"CBR"``, ``"ABR"`` or ``"VBR"`` from the LAME tag.
    lowpass_hz : int or None
        Low-pass frequency written by the encoder.
    vbr_quality : int or None
        LAME ``-V`` level, for VBR files.
    """

    tag: str
    encoder: str = ""
    method: str | None = None
    lowpass_hz: int | None = None
    vbr_quality: int | None = None


def _id3v2_size(data: bytes) -> int:
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = 0
    for b in data[6:10]:
        size = (size << 7) | (b & 0x7F)
    footer = 10 if data[5] & 0x10 else 0
    return 10 + size + footer


def _is_frame_header(b: bytes) -> bool:
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return False
    version = (b[1] >> 3) & 3
    layer = (b[1] >> 1) & 3
    bitrate = (b[2] >> 4) & 0xF
    rate = (b[2] >> 2) & 3
    return version != 1 and layer == 1 and bitrate not in (0, 15) and rate != 3


def parse_lame_info(data: bytes) -> LameInfo | None:
    """Parse the headers from the bytes that start at the first MP3 frame."""
    pos = 0
    limit = min(len(data) - 4, 65536)
    while pos < limit and not _is_frame_header(data[pos : pos + 4]):
        pos += 1
    if pos >= limit:
        return None
    h = data[pos : pos + 4]
    mpeg1 = ((h[1] >> 3) & 3) == 3
    mono = ((h[3] >> 6) & 3) == 3
    side = (17 if mono else 32) if mpeg1 else (9 if mono else 17)

    vbri = pos + 4 + 32
    if data[vbri : vbri + 4] == b"VBRI":
        return LameInfo(tag="VBRI", encoder="FhG", method="VBR")

    x = pos + 4 + side
    tag = data[x : x + 4]
    if tag not in (b"Xing", b"Info"):
        return None
    info = LameInfo(tag=tag.decode())
    if len(data) < x + 8:
        return info
    (flags,) = struct.unpack(">I", data[x + 4 : x + 8])
    p = x + 8
    p += 4 if flags & 1 else 0
    p += 4 if flags & 2 else 0
    p += 100 if flags & 4 else 0
    quality = None
    if flags & 8:
        if len(data) >= p + 4:
            (quality,) = struct.unpack(">I", data[p : p + 4])
        p += 4
    enc = data[p : p + 9]
    if len(enc) < 9 or not all(32 <= c < 127 for c in enc[:4]):
        return info
    info.encoder = enc.split(b"\x00")[0].decode("ascii", "replace").strip()
    if len(data) < p + 11:
        return info
    info.method = _VBR_METHODS.get(data[p + 9] & 0x0F)
    lowpass = data[p + 10] * 100
    info.lowpass_hz = lowpass or None
    if info.method == "VBR" and quality is not None and quality <= 100:
        info.vbr_quality = (100 - quality) // 10
    return info


def read_lame_info(path: str | Path) -> LameInfo | None:
    """Read the Xing/Info/LAME or VBRI header of an MP3 file, if any."""
    with open(path, "rb") as f:
        head = f.read(10)
        skip = _id3v2_size(head)
        f.seek(skip)
        data = f.read(16384)
    return parse_lame_info(data)
