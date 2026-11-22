import numpy as np
import pytest

from musicheck.spectrum import detect_cutoff, median_smooth

FREQS = np.fft.rfftfreq(4096, 1 / 44100)
RNG = np.random.default_rng(0)


def _spectrum(cut=None, slope_db_per_khz=2.0, floor=-130.0):
    s = -40.0 - slope_db_per_khz * FREQS / 1000 + RNG.normal(0, 2, FREQS.size)
    if cut:
        s[FREQS > cut] = floor + RNG.normal(0, 1, (FREQS > cut).sum())
    return np.maximum(s, floor)


@pytest.mark.parametrize("cut", [11000, 16000, 17000, 19500, 20500])
def test_brickwall_found(cut):
    res = detect_cutoff(FREQS, _spectrum(cut))
    assert res.hz == pytest.approx(cut, abs=150)
    assert res.drop_db > 30


def test_full_band_has_no_cutoff():
    assert detect_cutoff(FREQS, _spectrum()).hz is None


def test_gradual_rolloff_is_not_a_cutoff():
    # 4 dB/kHz down to a noise floor: a natural roll-off, not a filter
    assert (
        detect_cutoff(FREQS, _spectrum(slope_db_per_khz=4.0, floor=-115.0)).hz is None
    )


def test_narrow_tone_above_cut_ignored():
    s = _spectrum(16000)
    s[np.argmin(abs(FREQS - 19000))] = -45.0  # pilot tone
    assert detect_cutoff(FREQS, s).hz == pytest.approx(16000, abs=150)


def test_median_smooth_keeps_length():
    x = np.arange(10.0)
    assert median_smooth(x, 3).shape == x.shape
