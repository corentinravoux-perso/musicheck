"""Generate test audio: a music-like signal, encoded and transcoded with PyAV."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np


def music(sr: int = 44100, seconds: float = 12.0, seed: int = 0, top_hz: float | None = None,
          channels: int = 2, level: float = 0.25) -> np.ndarray:  # fmt: skip
    """Pink-ish noise with a beat envelope and tones, content up to Nyquist.

    Returns float32 array of shape (channels, n).
    """
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    f = np.fft.rfftfreq(n, 1 / sr)
    shape = 1 / np.sqrt(np.maximum(f, 20.0))
    shape /= np.sqrt(1 + (f / 12000.0) ** 2)  # gentle treble roll-off
    if top_hz:
        shape[f > top_hz] = 0
    out = []
    common = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    for _ in range(channels):
        own = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
        x = np.fft.irfft((0.7 * common + 0.5 * own) * shape, n)
        out.append(x)
    x = np.array(out)
    t = np.arange(n) / sr
    env = 0.55 + 0.45 * np.abs(np.sin(np.pi * 2.0 * t)) ** 4
    tones = sum(0.3 * np.sin(2 * np.pi * f0 * t) for f0 in (110.0, 220.0, 330.0, 440.0))
    x = x / np.std(x) * env + 0.5 * tones
    x = x / np.max(np.abs(x)) * level * 3.5
    return x.astype(np.float32)


def write(path: Path, x: np.ndarray, sr: int, codec: str, bit_rate: int | None = None,
          fmt: str | None = None) -> Path:  # fmt: skip
    """Encode a float (channels, n) array with an FFmpeg encoder."""
    layout = "stereo" if x.shape[0] == 2 else "mono"
    with av.open(str(path), "w") as out:
        st = out.add_stream(codec, rate=sr, layout=layout)
        if bit_rate:
            st.bit_rate = bit_rate
        if fmt:
            st.format = fmt
        cc = st.codec_context
        frame_size = cc.frame_size or 4096
        resampler = av.AudioResampler(
            format=cc.format.name, layout=layout, rate=sr, frame_size=frame_size
        )
        step = 4096
        for i in range(0, x.shape[1], step):
            chunk = np.ascontiguousarray(x[:, i : i + step])
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout=layout)
            frame.sample_rate = sr
            frame.pts = i
            for rf in resampler.resample(frame):
                for pkt in st.encode(rf):
                    out.mux(pkt)
        for rf in resampler.resample(None):
            for pkt in st.encode(rf):
                out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)
    return path


def read(path: Path, rate: int | None = None) -> tuple[np.ndarray, int]:
    """Decode a file to float32 (channels, n), optionally resampled."""
    with av.open(str(path)) as c:
        st = c.streams.audio[0]
        sr = rate or st.codec_context.sample_rate
        layout = st.codec_context.layout.name
        rs = av.AudioResampler(format="fltp", layout=layout, rate=sr)
        chunks = []
        for frame in c.decode(st):
            for rf in rs.resample(frame):
                chunks.append(rf.to_ndarray())
        for rf in rs.resample(None):
            chunks.append(rf.to_ndarray())
    return np.concatenate(chunks, axis=1), sr


def quantize16(x: np.ndarray) -> np.ndarray:
    return (np.round(np.clip(x, -1, 1 - 2**-15) * 32768) / 32768).astype(np.float32)
