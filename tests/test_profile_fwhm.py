"""
tests/test_profile_fwhm.py
Offline tests for rbl/hardware/profile_fwhm.py.

All tests are pure Python — no serial port, no scope hardware.
"""
import math

import pytest

from rbl.hardware.profile_fwhm import (
    FwhmError,
    compute_fwhm_samples,
    compute_fwhm_seconds,
    estimate_baseline,
    gaussian_fwhm_fit,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic waveforms
# ---------------------------------------------------------------------------

def _gaussian_wave(n: int, center: float, sigma: float,
                   amplitude: float = 1.0, baseline: float = 0.0) -> list[float]:
    """Return a list of n samples from a Gaussian centred at *center*."""
    return [
        baseline + amplitude * math.exp(-0.5 * ((i - center) / sigma) ** 2)
        for i in range(n)
    ]


def _rect_wave(n: int, start: int, end: int, height: float = 1.0) -> list[float]:
    """Rectangle pulse: height between [start, end), 0 elsewhere."""
    return [height if start <= i < end else 0.0 for i in range(n)]


# ---------------------------------------------------------------------------
# estimate_baseline
# ---------------------------------------------------------------------------

class TestEstimateBaseline:

    def test_flat_signal_returns_value(self):
        volts = [5.0] * 100
        assert estimate_baseline(volts) == pytest.approx(5.0)

    def test_edges_higher_than_centre(self):
        # Gaussian: edges near 0, centre near 1.  Baseline should be ~0.
        v  = _gaussian_wave(200, center=100, sigma=10, amplitude=1.0)
        bl = estimate_baseline(v)
        assert bl < 0.05   # outer 10 % are far from the peak

    def test_dc_offset_recovered(self):
        v  = _gaussian_wave(200, center=100, sigma=10, amplitude=1.0,
                            baseline=0.3)
        bl = estimate_baseline(v)
        assert bl == pytest.approx(0.3, abs=0.02)

    def test_single_sample(self):
        # Wing size clamped to 1, median of [v, v] = v
        assert estimate_baseline([7.0]) == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# compute_fwhm_samples
# ---------------------------------------------------------------------------

class TestComputeFwhmSamples:

    def test_symmetric_gaussian_known_fwhm(self):
        """FWHM of a Gaussian with sigma=20 is 2√(2ln2)·20 ≈ 47.09 samples."""
        sigma  = 20.0
        center = 200.0
        v      = _gaussian_wave(400, center=center, sigma=sigma)
        fwhm   = compute_fwhm_samples(v, subtract_baseline=False)
        expected = 2.0 * math.sqrt(2.0 * math.log(2.0)) * sigma
        assert fwhm == pytest.approx(expected, rel=1e-3)

    def test_narrower_gaussian(self):
        sigma  = 5.0
        v      = _gaussian_wave(100, center=50, sigma=sigma)
        fwhm   = compute_fwhm_samples(v, subtract_baseline=False)
        expected = 2.0 * math.sqrt(2.0 * math.log(2.0)) * sigma
        assert fwhm == pytest.approx(expected, rel=1e-2)

    def test_baseline_subtraction_applied(self):
        """FWHM must be the same with or without baseline subtraction when
        the baseline is a flat DC offset."""
        offset = 0.5
        v_no   = _gaussian_wave(200, center=100, sigma=15, amplitude=2.0)
        v_off  = [x + offset for x in v_no]
        fwhm_no  = compute_fwhm_samples(v_no,  subtract_baseline=False)
        fwhm_off = compute_fwhm_samples(v_off, subtract_baseline=True)
        assert fwhm_off == pytest.approx(fwhm_no, rel=0.01)

    def test_rectangle_pulse_fwhm(self):
        """A rectangle of width W should give FWHM ≈ W (half-max at W/2)."""
        W = 60
        n = 200
        v = _rect_wave(n, start=70, end=70 + W, height=1.0)
        fwhm = compute_fwhm_samples(v, subtract_baseline=False)
        assert fwhm == pytest.approx(W, abs=1.5)

    def test_too_short_raises(self):
        with pytest.raises(FwhmError, match="too short"):
            compute_fwhm_samples([1.0, 2.0, 1.0])

    def test_non_positive_peak_raises(self):
        v = [-1.0] * 100 + [-0.5] + [-1.0] * 100
        with pytest.raises(FwhmError, match="non-positive"):
            compute_fwhm_samples(v, subtract_baseline=False)

    def test_peak_at_edge_crossing_not_found(self):
        """A peak right at the left edge has no left crossing."""
        v = [1.0] + [0.0] * 99
        with pytest.raises(FwhmError, match="crossing not found"):
            compute_fwhm_samples(v, subtract_baseline=False)


    def test_different_amplitudes_same_fwhm(self):
        """Scaling amplitude should not change the FWHM in sample units."""
        v1 = _gaussian_wave(300, center=150, sigma=20, amplitude=1.0)
        v2 = _gaussian_wave(300, center=150, sigma=20, amplitude=5.0)
        f1 = compute_fwhm_samples(v1, subtract_baseline=False)
        f2 = compute_fwhm_samples(v2, subtract_baseline=False)
        assert f1 == pytest.approx(f2, rel=1e-6)

    def test_asymmetric_peak_still_converges(self):
        """A lopsided profile — skewed Gaussian-like shape — should not crash."""
        n = 200
        # Slow rise on left, fast fall on right
        v = [math.exp(-0.5 * ((i - 100) / 30.0) ** 2) if i <= 100
             else math.exp(-0.5 * ((i - 100) / 10.0) ** 2)
             for i in range(n)]
        fwhm = compute_fwhm_samples(v, subtract_baseline=False)
        assert fwhm > 0


# ---------------------------------------------------------------------------
# compute_fwhm_seconds
# ---------------------------------------------------------------------------

class TestComputeFwhmSeconds:

    def test_fwhm_seconds_scales_with_xincr(self):
        v     = _gaussian_wave(300, center=150, sigma=20)
        xincr = 4e-9   # 4 ns per sample
        fwhm_s = compute_fwhm_seconds(v, xincr, subtract_baseline=False)
        fwhm_n = compute_fwhm_samples(v, subtract_baseline=False)
        assert fwhm_s == pytest.approx(fwhm_n * xincr, rel=1e-9)

    def test_negative_xincr_raises(self):
        v = _gaussian_wave(100, center=50, sigma=10)
        with pytest.raises(FwhmError, match="xincr must be positive"):
            compute_fwhm_seconds(v, -1e-9)

    def test_zero_xincr_raises(self):
        v = _gaussian_wave(100, center=50, sigma=10)
        with pytest.raises(FwhmError, match="xincr must be positive"):
            compute_fwhm_seconds(v, 0.0)


# ---------------------------------------------------------------------------
# gaussian_fwhm_fit  (requires scipy)
# ---------------------------------------------------------------------------

class TestGaussianFwhmFit:

    @pytest.fixture(autouse=True)
    def require_scipy(self):
        pytest.importorskip("scipy")

    def test_fit_recovers_known_sigma(self):
        sigma  = 25.0
        v      = _gaussian_wave(500, center=250, sigma=sigma, amplitude=3.0)
        result = gaussian_fwhm_fit(v, xincr=1.0, subtract_baseline=False)
        expected_fwhm = 2.0 * math.sqrt(2.0 * math.log(2.0)) * sigma
        assert result["fwhm_samples"] == pytest.approx(expected_fwhm, rel=0.005)

    def test_fit_center_accurate(self):
        center = 180.0
        v      = _gaussian_wave(400, center=center, sigma=20)
        result = gaussian_fwhm_fit(v, subtract_baseline=False)
        assert result["center"] == pytest.approx(center, abs=0.5)

    def test_fit_amplitude_accurate(self):
        amp    = 2.5
        v      = _gaussian_wave(400, center=200, sigma=20, amplitude=amp)
        result = gaussian_fwhm_fit(v, subtract_baseline=False)
        assert result["amplitude"] == pytest.approx(amp, rel=0.01)

    def test_r_squared_near_one_for_gaussian(self):
        v      = _gaussian_wave(500, center=250, sigma=30, amplitude=1.0)
        result = gaussian_fwhm_fit(v, subtract_baseline=False)
        assert result["r_squared"] > 0.999

    def test_fwhm_seconds_consistent(self):
        xincr  = 4e-9
        v      = _gaussian_wave(500, center=250, sigma=25)
        result = gaussian_fwhm_fit(v, xincr=xincr, subtract_baseline=False)
        assert result["fwhm_seconds"] == pytest.approx(
            result["fwhm_samples"] * xincr, rel=1e-9
        )

    def test_too_short_raises(self):
        with pytest.raises(FwhmError, match="too short"):
            gaussian_fwhm_fit([1.0, 0.5, 0.5])

    def test_non_positive_peak_raises(self):
        v = [-2.0] * 200
        with pytest.raises(FwhmError, match="non-positive"):
            gaussian_fwhm_fit(v, subtract_baseline=False)

    def test_baseline_subtraction_in_fit(self):
        """DC-offset waveform should give the same FWHM as without offset."""
        offset = 0.8
        v_clean  = _gaussian_wave(400, center=200, sigma=20, amplitude=2.0)
        v_offset = [x + offset for x in v_clean]
        r_clean  = gaussian_fwhm_fit(v_clean,  subtract_baseline=True)
        r_offset = gaussian_fwhm_fit(v_offset, subtract_baseline=True)
        assert r_offset["fwhm_samples"] == pytest.approx(
            r_clean["fwhm_samples"], rel=0.01
        )
