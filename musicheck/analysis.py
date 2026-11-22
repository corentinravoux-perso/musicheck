"""Decode an audio file once and measure what it really contains.

Decoding streams through PyAV (FFmpeg bundled in the wheel), so memory stays
flat whatever the track length. One pass collects

* the averaged power spectrum of the mid channel, and the cutoff found in it;
* the bits actually used by integer samples (a 24-bit file padded from 16 bits
  has its 8 low bits always zero);
* peak, RMS, a DR-meter style dynamic range, clipped samples;
* the side/mid energy ratio (mono content stored as stereo);
* decode errors and the decoded duration (truncated or corrupt files).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .spectrum import detect_cutoff

__all__ = ["analyze", "SPECTRUM_POINTS"]

CLIP_LEVEL = 0.99997  # about -0.0003 dBFS
SPECTRUM_POINTS = 512
_TINY = 1e-20
_INT_FORMATS = {"u8": 8, "s16": 16, "s32": 32, "s64": 64}


def _nfft(sample_rate: int) -> int:
    if sample_rate <= 50_000:
        return 4096
    if sample_rate <= 100_000:
        return 8192
    return 16384


def _db(x: float) -> float | None:
    return 10.0 * math.log10(x) if x > 0 else None


class _Accumulator:
    """Collects statistics over fixed blocks of three seconds."""

    def __init__(
        self, sample_rate: int, channels: int, spec_cols: int, frames_hint: int
    ):
        self.sr = sample_rate
        self.nch = channels
        self.block = 3 * sample_rate
        self.nfft = _nfft(sample_rate)
        self.hop = self.nfft // 2
        self.window = np.hanning(self.nfft).astype(np.float32)
        self.norm = (self.window.sum() / 2.0) ** 2
        nbins = self.nfft // 2 + 1
        self.power_sum = np.zeros(nbins)
        self.power_max = np.zeros(nbins)
        self.nframes = 0
        self.carry = np.zeros(0, dtype=np.float32)
        self.pending: list[np.ndarray] = []
        self.npending = 0
        self.nsamples = 0
        self.sumsq = np.zeros(channels)
        self.peak = np.zeros(channels)
        self.block_rms: list[np.ndarray] = []
        self.block_peak: list[np.ndarray] = []
        self.clipped = 0
        self.overs = 0
        self.ll = self.rr = self.lr = 0.0
        self.mid_energy = self.side_energy = 0.0
        self.orbits = 0
        self.storage_bits: int | None = None
        self.spec_cols = spec_cols
        self.frames_hint = max(1, frames_hint)
        if spec_cols:
            self.spec = np.zeros((spec_cols, nbins))
            self.spec_count = np.zeros(spec_cols)

    def feed(self, frame) -> None:
        arr = frame.to_ndarray()
        nch = frame.layout.nb_channels
        base = frame.format.name.rstrip("p")
        if not frame.format.is_planar:
            arr = arr.reshape(-1, nch).T
        if nch != self.nch:
            if nch < self.nch:
                return
            arr = arr[: self.nch]
        bits = _INT_FORMATS.get(base)
        if bits is not None:
            self.storage_bits = bits
            if base == "u8":
                x = (arr.astype(np.float32) - 128.0) / 128.0
            else:
                self.orbits |= int(np.bitwise_or.reduce(arr, axis=None))
                x = arr.astype(np.float32) / float(2 ** (bits - 1))
        else:
            x = arr.astype(np.float32, copy=False)
        self.pending.append(x)
        self.npending += x.shape[1]
        while self.npending >= self.block:
            self._take(self.block)

    def _take(self, n: int) -> None:
        data = (
            np.concatenate(self.pending, axis=1)
            if len(self.pending) > 1
            else self.pending[0]
        )
        block, rest = data[:, :n], data[:, n:]
        self.pending = [rest] if rest.shape[1] else []
        self.npending = rest.shape[1]
        self._process(block)

    def finish(self) -> None:
        if self.npending:
            self._take(self.npending)

    def _process(self, x: np.ndarray) -> None:
        n = x.shape[1]
        if n == 0:
            return
        self.nsamples += n
        absx = np.abs(x)
        sq = np.einsum("ij,ij->i", x, x, dtype=np.float64)
        pk = absx.max(axis=1)
        self.sumsq += sq
        self.peak = np.maximum(self.peak, pk)
        if n >= self.sr:  # DR blocks shorter than a second are ignored
            self.block_rms.append(np.sqrt(2.0 * sq / n))
            self.block_peak.append(pk)
        hot = absx >= CLIP_LEVEL
        self.clipped += int(np.count_nonzero(hot[:, 1:] & hot[:, :-1]))
        self.overs += int(np.count_nonzero(absx > 1.0))

        if self.nch >= 2:
            # einsum, not @: matmul goes through (multi-threaded) BLAS
            left, right = x[0], x[1]
            self.ll += float(sq[0])
            self.rr += float(sq[1])
            self.lr += float(np.einsum("i,i->", left, right, dtype=np.float64))
            mid = 0.5 * (left + right)
            side = 0.5 * (left - right)
            self.mid_energy += float(np.einsum("i,i->", mid, mid, dtype=np.float64))
            self.side_energy += float(np.einsum("i,i->", side, side, dtype=np.float64))
            if self.nch > 2:
                mid = x.mean(axis=0)
        else:
            mid = x[0]
        self._stft(mid.astype(np.float32, copy=False))

    def _stft(self, mid: np.ndarray) -> None:
        buf = np.concatenate([self.carry, mid]) if self.carry.size else mid
        if buf.size < self.nfft:
            self.carry = buf
            return
        frames = sliding_window_view(buf, self.nfft)[:: self.hop]
        power = np.abs(np.fft.rfft(frames * self.window, axis=1)) ** 2 / self.norm
        self.power_sum += power.sum(axis=0)
        self.power_max = np.maximum(self.power_max, power.max(axis=0))
        if self.spec_cols:
            idx = self.nframes + np.arange(len(frames))
            cols = np.minimum(
                self.spec_cols - 1, idx * self.spec_cols // self.frames_hint
            )
            np.add.at(self.spec, cols, power)
            np.add.at(self.spec_count, cols, 1)
        self.nframes += len(frames)
        self.carry = buf[len(frames) * self.hop :].copy()

    def dynamic_range(self) -> float | None:
        """DR value in the style of the TT DR meter (top 20 % loudest blocks)."""
        if not self.block_rms:
            return None
        rms = np.array(self.block_rms)
        peak = np.array(self.block_peak)
        values = []
        for ch in range(rms.shape[1]):
            r = np.sort(rms[:, ch])[::-1]
            top = r[: max(1, int(round(0.2 * len(r))))]
            rms_top = math.sqrt(float(np.mean(top**2)))
            p = np.sort(peak[:, ch])[::-1]
            p2 = p[1] if len(p) > 1 else p[0]
            if rms_top > 0 and p2 > 0:
                values.append(20.0 * math.log10(p2 / rms_top))
        return float(np.mean(values)) if values else None


def _pool_spectrum(power: np.ndarray, points: int) -> np.ndarray:
    edges = np.linspace(0, power.size, points + 1).astype(int)
    return np.array(
        [
            power[a:b].mean() if b > a else power[a]
            for a, b in zip(edges[:-1], edges[1:])
        ]
    )


def analyze(
    path: str | Path,
    *,
    max_duration: float | None = None,
    spectrogram_cols: int = 0,
) -> dict:
    """Decode ``path`` and return its measured properties.

    Parameters
    ----------
    path : str or pathlib.Path
        Audio file readable by FFmpeg.
    max_duration : float, optional
        Stop after this many seconds of audio (faster, but no truncation check).
    spectrogram_cols : int
        When positive, also return a time-averaged spectrogram with this many
        time columns (used for plots, not cached).

    Returns
    -------
    dict
        Plain JSON-serialisable values; ``spectrum_db`` is the averaged mid
        spectrum pooled to :data:`SPECTRUM_POINTS` bins from 0 to Nyquist.
    """
    import av

    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("no audio stream")
        cc = stream.codec_context
        sr = int(cc.sample_rate)
        nch = int(cc.layout.nb_channels) if cc.layout else int(cc.channels)
        if stream.duration and stream.time_base:
            header_duration = float(stream.duration * stream.time_base)
        elif container.duration:
            header_duration = container.duration / av.time_base
        else:
            header_duration = 0.0
        target = (
            header_duration
            if not max_duration
            else min(header_duration or max_duration, max_duration)
        )
        hop = _nfft(sr) // 2
        acc = _Accumulator(sr, nch, spectrogram_cols, int(target * sr / hop) + 1)
        max_samples = int(max_duration * sr) if max_duration else None
        errors = 0
        fed = 0
        stopped_early = False
        try:
            for packet in container.demux(stream):
                try:
                    frames = packet.decode()
                except av.error.FFmpegError:
                    errors += 1
                    continue
                for frame in frames:
                    acc.feed(frame)
                    fed += frame.samples
                if max_samples and fed >= max_samples:
                    stopped_early = True
                    break
        except av.error.FFmpegError:
            errors += 1
        codec = cc.name
        raw_bits = int(cc.bits_per_raw_sample or 0) or None
    acc.finish()

    freqs = np.fft.rfftfreq(acc.nfft, 1.0 / sr)
    nframes = max(acc.nframes, 1)
    mean_power = acc.power_sum / nframes
    power_db = 10.0 * np.log10(mean_power + _TINY)
    cutoff = detect_cutoff(freqs, power_db) if acc.nframes >= 8 else None

    ultrasonic_db = None
    if sr >= 88_200 and cutoff is not None:
        band = (freqs >= 24_000) & (freqs <= min(40_000, sr / 2 - 500))
        if band.any():
            ultrasonic_db = float(np.median(power_db[band])) - cutoff.ref_db

    eff_bits = None
    if acc.storage_bits and acc.storage_bits > 8:
        mask = (1 << acc.storage_bits) - 1
        used = acc.orbits & mask
        if used:
            tz = (used & -used).bit_length() - 1
            eff_bits = acc.storage_bits - tz

    total = acc.nsamples * max(acc.nch, 1)
    rms = math.sqrt(float(acc.sumsq.sum()) / total) if total else 0.0
    peak = float(acc.peak.max()) if acc.nsamples else 0.0
    corr = None
    if acc.ll > 0 and acc.rr > 0:
        corr = acc.lr / math.sqrt(acc.ll * acc.rr)
    side_db = None
    if acc.mid_energy > 0:
        side_db = (
            _db(acc.side_energy / acc.mid_energy) if acc.side_energy > 0 else -200.0
        )

    result = {
        "codec": codec,
        "sample_rate": sr,
        "channels": nch,
        "raw_bits": raw_bits,
        "storage_bits": acc.storage_bits,
        "eff_bits": eff_bits,
        "header_duration": header_duration or None,
        "decoded_duration": acc.nsamples / sr if sr else 0.0,
        "partial": stopped_early,
        "decode_errors": errors,
        "cutoff_hz": cutoff.hz if cutoff else None,
        "cutoff_drop_db": cutoff.drop_db if cutoff else None,
        "ref_db": cutoff.ref_db if cutoff else None,
        "ultrasonic_db": ultrasonic_db,
        "peak_db": 20.0 * math.log10(peak) if peak > 0 else None,
        "rms_db": 20.0 * math.log10(rms) if rms > 0 else None,
        "dr": acc.dynamic_range(),
        "clip_pct": 100.0 * acc.clipped / total if total else 0.0,
        "overs": acc.overs,
        "stereo_corr": corr,
        "side_db": side_db,
        "spectrum_db": np.round(
            10.0 * np.log10(_pool_spectrum(mean_power, SPECTRUM_POINTS) + _TINY), 1
        ).tolist(),
    }
    if spectrogram_cols:
        counts = np.maximum(acc.spec_count, 1)[:, None]
        result["spectrogram_db"] = 10.0 * np.log10(acc.spec / counts + _TINY)
        result["freqs"] = freqs
        result["power_db"] = power_db
    return result
