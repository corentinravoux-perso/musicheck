"""End-to-end verdicts on generated genuine and transcoded files."""

import pytest

from musicheck.evaluate import build_record, classify_cutoff, expected_cutoff
from musicheck.scan import measure

CASES = {
    "genuine.flac": ("OK", None),
    "g128.mp3": ("OK", None),
    "g320.mp3": ("OK", None),
    "g_aac256.m4a": ("OK", None),
    "g_opus128.opus": ("OK", None),
    "g96.flac": ("OK", None),
    "g24.wav": ("OK", None),
    "fake128.flac": ("FAKE", "transcoded from a lossy"),
    "fake320.mp3": ("FAKE", "re-encoded from a ~128k"),
    "fake_aac256.m4a": ("FAKE", "re-encoded"),
    "upsampled96.flac": ("FAKE", "upsampled from 44.1/48"),
    "padded24.wav": ("FAKE", "padded from 16-bit"),
    "truncated.flac": ("SUSPECT", "truncated"),
}


@pytest.fixture(scope="module")
def records(library):
    root, files = library
    return {
        name: build_record(path, measure(path), root) for name, path in files.items()
    }


@pytest.mark.parametrize("name", CASES)
def test_verdict(records, name):
    verdict, text = CASES[name]
    rec = records[name]
    assert rec["verdict"] == verdict, rec["issues"]
    if text:
        assert text in rec["issues"]


def test_properties(records):
    rec = records["fake320.mp3"]
    assert rec["format"] == "MP3" and rec["bitrate"] == 320
    assert rec["cutoff_khz"] == pytest.approx(16.8, abs=0.4)
    assert rec["real"] == "~128k"
    assert records["genuine.flac"]["cutoff_khz"] is None
    assert records["genuine.flac"]["real"] == "full band"
    assert records["g24.wav"]["eff_bits"] == 24
    assert records["padded24.wav"]["eff_bits"] == 16
    assert records["genuine.flac"]["dr"] > 5
    assert "mono stored as stereo" in records["mono_stereo.flac"]["notes"]
    assert records["mono_stereo.flac"]["verdict"] == "OK"


def test_unreadable(tmp_path):
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"not audio at all" * 100)
    rec = build_record(bad, measure(bad), tmp_path)
    assert rec["verdict"] == "ERROR"


def test_tables():
    assert expected_cutoff("MP3", 320, 20500) == 20500
    assert expected_cutoff("MP3", 320) > 19000
    assert expected_cutoff("FLAC", 900) is None
    assert classify_cutoff(16000) == (128, "~128k")
    assert classify_cutoff(22050) == (None, "full band")
