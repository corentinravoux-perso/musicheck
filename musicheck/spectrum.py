"""Find the frequency where an averaged power spectrum falls off a cliff.

Lossy encoders apply a steep low-pass filter (LAME at 128 kbps cuts at
17 kHz, at 320 kbps at 20.5 kHz) and zero everything above it. The cut survives
any later re-encoding, so a FLAC or a "320 kbps" MP3 made from a 128 kbps file
still shows a wall at 16-17 kHz. Natural recordings roll off gradually into a
noise floor instead. :func:`detect_cutoff` looks for the wall.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

__all__ = ["Cutoff", "detect_cutoff", "median_smooth"]


@dataclass
class Cutoff:
    """Result of :func:`detect_cutoff`.

    Attributes
    ----------
    hz : float or None
        Frequency of the cliff, ``None`` when the spectrum has no sharp
        cut (full band, or a gradual roll-off).
    drop_db : float
        Level difference across the cliff (0 when none was found).
    ref_db : float
        Median level of the 1-6 kHz band, the reference for other levels.
    """

    hz: float | None
    drop_db: float
    ref_db: float


def median_smooth(values: np.ndarray, width: int) -> np.ndarray:
    """Running median of odd ``width``, edges padded by reflection."""
    width = max(1, int(width) | 1)
    if width == 1 or values.size < width:
        return values.copy()
    half = width // 2
    padded = np.pad(values, half, mode="edge")
    return np.median(sliding_window_view(padded, width), axis=1)


def detect_cutoff(
    freqs: np.ndarray,
    power_db: np.ndarray,
    *,
    min_hz: float = 2500.0,
    local_drop_db: float = 15.0,
    global_drop_db: float = 18.0,
    content_floor_db: float = -85.0,
) -> Cutoff:
    """Locate a brick-wall low-pass in an averaged power spectrum.

    A bin ``k`` is a candidate when

    * the median level over ``[f-700, f-120]`` Hz exceeds the median over
      ``[f+120, f+700]`` Hz by at least ``local_drop_db`` (the fall is steep),
    * it also exceeds the 95th percentile of everything above ``f+120`` Hz by
      ``global_drop_db`` (nothing comes back above the cut),
    * the level below the cut is within ``content_floor_db`` of the 1-6 kHz
      reference (there is music below it, not only noise floor).

    The candidate with the largest local drop wins; its frequency is refined
    to the last bin above the midpoint of the two levels.

    Parameters
    ----------
    freqs : numpy.ndarray
        Bin frequencies in Hz, evenly spaced from 0 to Nyquist.
    power_db : numpy.ndarray
        Averaged power per bin in dB.
    min_hz : float
        Lowest frequency considered for a cliff.
    local_drop_db, global_drop_db, content_floor_db : float
        Thresholds described above.

    Returns
    -------
    Cutoff
    """
    freqs = np.asarray(freqs, dtype=float)
    power_db = np.asarray(power_db, dtype=float)
    n = freqs.size
    df = float(freqs[1] - freqs[0])
    smooth = median_smooth(power_db, int(round(100.0 / df)))

    ref_mask = (freqs >= 1000.0) & (freqs <= 6000.0)
    ref = float(np.median(smooth[ref_mask])) if ref_mask.any() else float(smooth.max())

    outer = max(4, int(round(700.0 / df)))
    gap = max(1, int(round(120.0 / df)))
    guard = max(1, int(round(60.0 / df)))
    end = n - guard
    width = outer - gap
    if n < 2 * outer + 4:
        return Cutoff(None, 0.0, ref)
    rolling = np.median(sliding_window_view(smooth, width), axis=1)

    start = max(outer, int(np.searchsorted(freqs, min_hz)))
    best_k, best_drop, best_lo, best_hi = None, 0.0, 0.0, 0.0
    for k in range(start, end - gap - 3):
        lo = rolling[k - outer]
        if k + outer <= end:
            hi = rolling[k + gap]
        else:
            hi = float(np.median(smooth[k + gap : end]))
        drop = lo - hi
        if drop < local_drop_db or drop <= best_drop:
            continue
        if lo < ref + content_floor_db:
            continue
        above = float(np.percentile(smooth[k + gap : end], 95))
        if lo - above < global_drop_db:
            continue
        best_k, best_drop, best_lo, best_hi = k, drop, lo, hi

    if best_k is None:
        return Cutoff(None, 0.0, ref)

    mid = 0.5 * (best_lo + best_hi)
    lo_k = max(0, best_k - outer)
    hi_k = min(end - 1, best_k + outer)
    idx = best_k
    for j in range(hi_k, lo_k - 1, -1):
        if smooth[j] >= mid:
            idx = j
            break
    hz = float(freqs[idx])
    if idx + 1 < n and smooth[idx] != smooth[idx + 1]:
        frac = (smooth[idx] - mid) / (smooth[idx] - smooth[idx + 1])
        hz += float(np.clip(frac, 0.0, 1.0)) * df
    return Cutoff(hz, float(best_drop), ref)
