import struct

from musicheck.lame import _id3v2_size, parse_lame_info


def _frame(tag=b"Info", encoder=b"LAME3.100", method=1, lowpass=205, quality=None):
    header = bytes([0xFF, 0xFB, 0xE0, 0x64])  # MPEG1 layer III 320 kbps 44.1 kHz stereo
    flags = 0x0F if quality is not None else 0x07
    body = (
        tag + struct.pack(">I", flags) + struct.pack(">II", 1000, 400000) + bytes(100)
    )
    if quality is not None:
        body += struct.pack(">I", quality)
    body += encoder + bytes([0x00 | method, lowpass]) + bytes(20)
    return header + bytes(32) + body


def test_cbr_header():
    info = parse_lame_info(b"\x00" * 7 + _frame())
    assert info.tag == "Info"
    assert info.encoder == "LAME3.100"
    assert info.method == "CBR"
    assert info.lowpass_hz == 20500


def test_vbr_quality():
    info = parse_lame_info(_frame(tag=b"Xing", method=4, lowpass=195, quality=97))
    assert info.method == "VBR"
    assert info.vbr_quality == 0
    assert info.lowpass_hz == 19500


def test_no_header():
    assert parse_lame_info(bytes([0xFF, 0xFB, 0xE0, 0x64]) + bytes(400)) is None
    assert parse_lame_info(b"garbage" * 10) is None


def test_id3v2_size():
    assert _id3v2_size(b"ID3\x04\x00\x00\x00\x00\x02\x01") == 10 + 257
    assert _id3v2_size(b"\xff\xfb\x00\x00" + bytes(6)) == 0
