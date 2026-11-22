"""Session fixtures: genuine and transcoded audio generated with PyAV."""

from __future__ import annotations

import numpy as np
import pytest
from _audio import music, quantize16, read, write

SR = 44100


@pytest.fixture(scope="session")
def library(tmp_path_factory):
    """Folder of synthetic files; name -> path."""
    d = tmp_path_factory.mktemp("library")
    x = music(SR)
    files = {}

    def add(name, *args, **kw):
        files[name] = write(d / name, *args, **kw)

    add("genuine.flac", quantize16(x), SR, "flac", fmt="s16")
    add("g128.mp3", x, SR, "libmp3lame", 128000)
    y, _ = read(files["g128.mp3"])
    add("fake128.flac", quantize16(y), SR, "flac", fmt="s16")
    add("fake320.mp3", y, SR, "libmp3lame", 320000)
    add("g320.mp3", x, SR, "libmp3lame", 320000)
    add("g_aac256.m4a", x, SR, "aac", 256000)
    add("fake_aac256.m4a", y, SR, "aac", 256000)
    add("g_opus128.opus", music(48000), 48000, "libopus", 128000)
    up, _ = read(files["genuine.flac"], rate=96000)
    add("upsampled96.flac", quantize16(up), 96000, "flac", fmt="s16")
    add("g96.flac", quantize16(music(96000)), 96000, "flac", fmt="s16")
    add("padded24.wav", quantize16(x), SR, "pcm_s24le")
    noise = 1e-6 * np.random.default_rng(1).standard_normal(x.shape).astype(np.float32)
    add("g24.wav", x + noise, SR, "pcm_s24le")
    m = music(SR, channels=1)
    add("mono_stereo.flac", quantize16(np.vstack([m, m])), SR, "flac", fmt="s16")
    data = files["genuine.flac"].read_bytes()
    (d / "truncated.flac").write_bytes(data[: len(data) // 2])
    files["truncated.flac"] = d / "truncated.flac"
    (d / "notes.txt").write_text("not audio")
    return d, files
