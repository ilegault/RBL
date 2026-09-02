"""
tests/test_ac_metrics.py

Unit tests for rbl.hardware.ac_metrics.

All tests use synthetic signals with analytic ground truth so the expected
values are exact (within floating-point round-off).  No hardware, no Qt.

Signal conventions:
    FS  = sample rate (Hz)
    F   = drive frequency (Hz)
    A   = peak amplitude (V or mA, units don't matter here)
    n   = number of samples (a large integer multiple of FS/F → many cycles)
"""
import math

import numpy as np
import pytest

from rbl.hardware.ac_metrics import (
    MIN_CYCLES,
    PEAK_PERCENTILE,
    ac_metrics,
    fundamental,
    phase_difference_deg,
    whole_cycle_samples,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FS = 50_000.0   # Hz — matches the LabJack stream rate
F  = 250.0      # Hz
A  = 2.0        # peak amplitude


def _make_signals(fs=FS, f=F, a=A, n_cycles=200):
    """Return (t, sine, triangle, square) arrays for n_cycles full cycles."""
    n = int(round(n_cycles * fs / f))
    t = np.arange(n) / fs
    sine = a * np.sin(2 * np.pi * f * t)
    tri  = a * (2 / np.pi) * np.arcsin(np.sin(2 * np.pi * f * t))
    squ  = a * np.sign(np.sin(2 * np.pi * f * t))
    return t, sine, tri, squ


# ---------------------------------------------------------------------------
# whole_cycle_samples
# ---------------------------------------------------------------------------

class TestWholeCycleSamples:
    def test_exact_multiple(self):
        """10 full cycles at 250 Hz into 50 kHz."""
        n = int(FS / F * 10)   # exactly 200 samples = 10 cycles
        m = whole_cycle_samples(n, FS, F)
        assert m == n

    def test_truncates_partial_cycle(self):
        """Extra samples beyond the last full cycle are discarded."""
        n = int(FS / F * 10) + 50   # 10 full cycles + 50 stray samples
        m = whole_cycle_samples(n, FS, F)
        expected = int(round(10 * FS / F))
        assert m == expected

    def test_too_few_cycles_returns_zero(self):
        """Fewer than MIN_CYCLES whole cycles → return 0."""
        n = int(FS / F * (MIN_CYCLES - 1))
        assert whole_cycle_samples(n, FS, F) == 0

    def test_exactly_min_cycles_nonzero(self):
        """Exactly MIN_CYCLES whole cycles → non-zero."""
        n = int(FS / F * MIN_CYCLES)
        assert whole_cycle_samples(n, FS, F) > 0

    def test_zero_freq_returns_zero(self):
        assert whole_cycle_samples(1000, FS, 0.0) == 0

    def test_negative_freq_returns_zero(self):
        assert whole_cycle_samples(1000, FS, -10.0) == 0

    def test_zero_samples_returns_zero(self):
        assert whole_cycle_samples(0, FS, F) == 0

    def test_zero_sample_rate_returns_zero(self):
        assert whole_cycle_samples(1000, 0.0, F) == 0


# ---------------------------------------------------------------------------
# fundamental
# ---------------------------------------------------------------------------

class TestFundamental:
    """Verify amplitude recovery against analytic Fourier coefficients."""

    def setup_method(self):
        _, self.sine, self.tri, self.squ = _make_signals()

    def test_sine_amplitude(self):
        """Fundamental of A*sin recovers A."""
        amp, _ = fundamental(self.sine, FS, F)
        assert abs(amp - A) < 1e-3, f"sine amp={amp}"

    def test_triangle_amplitude(self):
        """Fundamental of triangle = A * 8/π²."""
        expected = A * 8 / np.pi ** 2
        amp, _ = fundamental(self.tri, FS, F)
        assert abs(amp - expected) < 1e-3, f"tri amp={amp}"

    def test_square_amplitude(self):
        """Fundamental of square = A * 4/π."""
        expected = A * 4 / np.pi
        amp, _ = fundamental(self.squ, FS, F)
        assert abs(amp - expected) < 1e-3, f"squ amp={amp}"

    def test_sine_phase_is_minus_pi_over_2(self):
        """sin(2πft) phase relative to cos(2πft) = -π/2."""
        _, phase = fundamental(self.sine, FS, F)
        assert abs(phase - (-math.pi / 2)) < 1e-3, f"sine phase={phase}"

    def test_cosine_phase_is_zero(self):
        """A*cos(2πft): phase should be ~0."""
        n = len(self.sine)
        t = np.arange(n) / FS
        cosine = A * np.cos(2 * np.pi * F * t)
        _, phase = fundamental(cosine, FS, F)
        assert abs(phase) < 1e-3, f"cosine phase={phase}"

    def test_empty_returns_nan(self):
        amp, phase = fundamental([], FS, F)
        assert math.isnan(amp) and math.isnan(phase)

    def test_too_short_returns_nan(self):
        """Only 2 samples — nowhere near MIN_CYCLES."""
        amp, phase = fundamental([1.0, 2.0], FS, F)
        assert math.isnan(amp) and math.isnan(phase)

    def test_dc_input_freq_zero_returns_nan(self):
        """freq_hz=0 → no cycles → (nan, nan)."""
        amp, phase = fundamental(self.sine, FS, 0.0)
        assert math.isnan(amp) and math.isnan(phase)

    def test_returns_float_tuple(self):
        amp, phase = fundamental(self.sine, FS, F)
        assert isinstance(amp, float) and isinstance(phase, float)

    def test_amplitude_nonnegative(self):
        """Amplitude must always be non-negative."""
        amp, _ = fundamental(self.sine, FS, F)
        assert amp >= 0.0

    def test_list_input_accepted(self):
        """Accepts plain Python list, not just ndarray."""
        amp, _ = fundamental(list(self.sine[:2000]), FS, F)
        assert not math.isnan(amp)

    def test_all_nan_samples_returns_nan(self):
        """If all samples in the retained segment are NaN → (nan, nan)."""
        bad = np.full(1000, float("nan"))
        amp, phase = fundamental(bad, FS, F)
        assert math.isnan(amp) and math.isnan(phase)


# ---------------------------------------------------------------------------
# ac_metrics
# ---------------------------------------------------------------------------

class TestAcMetrics:
    def setup_method(self):
        _, self.sine, self.tri, self.squ = _make_signals()

    # -- Key presence and type -----------------------------------------------

    def test_all_keys_present(self):
        m = ac_metrics(self.sine, FS, F)
        expected_keys = {
            "rms", "mean", "peak", "peak_abs",
            "fund_amp", "fund_phase", "crest", "fund_ratio",
            "n_samples", "n_cycles",
        }
        assert expected_keys == set(m.keys())

    # -- Empty input ---------------------------------------------------------

    def test_empty_all_nan(self):
        m = ac_metrics([], FS, F)
        for key in ("rms", "mean", "peak", "peak_abs", "fund_amp",
                    "fund_phase", "crest", "fund_ratio"):
            assert math.isnan(m[key]), f"{key} not NaN"
        assert m["n_samples"] == 0
        assert m["n_cycles"] == 0

    # -- DC / freq=0 ---------------------------------------------------------

    def test_dc_setpoint_fund_fields_nan(self):
        m = ac_metrics(self.sine, FS, 0.0)
        assert m["n_cycles"] == 0
        assert math.isnan(m["fund_amp"])
        assert math.isnan(m["fund_phase"])

    def test_dc_setpoint_rms_still_computed(self):
        m = ac_metrics(self.sine, FS, 0.0)
        assert not math.isnan(m["rms"])
        assert not math.isnan(m["mean"])

    # -- Crest factors -------------------------------------------------------

    def test_sine_crest_factor(self):
        m = ac_metrics(self.sine, FS, F)
        assert abs(m["crest"] - math.sqrt(2)) < 0.02

    def test_triangle_crest_factor(self):
        m = ac_metrics(self.tri, FS, F)
        assert abs(m["crest"] - math.sqrt(3)) < 0.02

    def test_square_crest_factor(self):
        m = ac_metrics(self.squ, FS, F)
        assert abs(m["crest"] - 1.0) < 0.02

    # -- RMS correctness -----------------------------------------------------

    def test_sine_rms_is_a_over_sqrt2(self):
        m = ac_metrics(self.sine, FS, F)
        expected = A / math.sqrt(2)
        assert abs(m["rms"] - expected) < 1e-4

    def test_triangle_rms_is_a_over_sqrt3(self):
        m = ac_metrics(self.tri, FS, F)
        expected = A / math.sqrt(3)
        assert abs(m["rms"] - expected) < 1e-3

    def test_square_rms_is_a(self):
        m = ac_metrics(self.squ, FS, F)
        assert abs(m["rms"] - A) < 1e-3

    # -- Mean (symmetric AC → ~0) --------------------------------------------

    def test_sine_mean_near_zero(self):
        m = ac_metrics(self.sine, FS, F)
        assert abs(m["mean"]) < 1e-10

    def test_dc_offset_measured(self):
        shifted = self.sine + 1.5
        m = ac_metrics(shifted, FS, F)
        assert abs(m["mean"] - 1.5) < 1e-4

    # -- Peak ----------------------------------------------------------------

    def test_peak_abs_at_least_peak(self):
        m = ac_metrics(self.sine, FS, F)
        assert m["peak_abs"] >= m["peak"]

    def test_peak_below_true_amplitude(self):
        # Robust peak (99.9th percentile) must be ≤ A for a bounded signal
        m = ac_metrics(self.sine, FS, F)
        assert m["peak"] <= A + 1e-6

    # -- fund_ratio ----------------------------------------------------------

    def test_fund_ratio_near_one_for_sine(self):
        """Sine has essentially all energy at the fundamental."""
        m = ac_metrics(self.sine, FS, F)
        assert m["fund_ratio"] > 0.99

    def test_fund_ratio_less_than_one_for_triangle(self):
        """Triangle fundamental (0.811*A) < robust peak (~A), so ratio < 1."""
        m = ac_metrics(self.tri, FS, F)
        assert m["fund_ratio"] < 1.0

    # -- n_samples / n_cycles ------------------------------------------------

    def test_n_samples_correct(self):
        m = ac_metrics(self.sine, FS, F)
        assert m["n_samples"] == len(self.sine)

    def test_n_cycles_positive(self):
        m = ac_metrics(self.sine, FS, F)
        assert m["n_cycles"] > 0

    # -- fund_amp matches fundamental() --------------------------------------

    def test_fund_amp_consistent_with_fundamental(self):
        m = ac_metrics(self.sine, FS, F)
        amp, _ = fundamental(self.sine, FS, F)
        assert abs(m["fund_amp"] - amp) < 1e-12

    # -- List input accepted -------------------------------------------------

    def test_list_input_accepted(self):
        m = ac_metrics(list(self.sine[:2000]), FS, F)
        assert not math.isnan(m["rms"])


# ---------------------------------------------------------------------------
# phase_difference_deg
# ---------------------------------------------------------------------------

class TestPhaseDifferenceDeg:
    def test_capacitive_lead_90(self):
        """Current leads voltage by 90° on a purely capacitive load."""
        _, sine, tri, _ = _make_signals()
        C = 1200e-12
        for name, v_kv in (("sine", sine), ("triangle", tri)):
            i_ma = np.gradient(v_kv * 1000.0, 1 / FS) * C * 1e3
            _, pv = fundamental(v_kv, FS, F)
            _, pi = fundamental(i_ma, FS, F)
            d = phase_difference_deg(pi, pv)
            assert abs(d - 90.0) < 1.0, f"{name}: phase={d}"

    def test_zero_difference(self):
        assert phase_difference_deg(0.0, 0.0) == pytest.approx(0.0)

    def test_exact_90(self):
        result = phase_difference_deg(math.pi / 2, 0.0)
        assert result == pytest.approx(90.0)

    def test_exact_minus_90(self):
        result = phase_difference_deg(-math.pi / 2, 0.0)
        assert result == pytest.approx(-90.0)

    def test_wraps_near_plus_180(self):
        """Values should land in (-180, 180]."""
        result = phase_difference_deg(math.pi + 0.01, 0.0)
        assert -180.0 < result <= 180.0

    def test_wraps_near_minus_180(self):
        result = phase_difference_deg(-math.pi - 0.01, 0.0)
        assert -180.0 < result <= 180.0

    def test_nan_input_returns_nan(self):
        assert math.isnan(phase_difference_deg(float("nan"), 0.0))
        assert math.isnan(phase_difference_deg(0.0, float("nan")))
        assert math.isnan(phase_difference_deg(float("nan"), float("nan")))

    def test_inf_input_returns_nan(self):
        assert math.isnan(phase_difference_deg(float("inf"), 0.0))

    def test_antisymmetric(self):
        """phase_diff(a, b) = -phase_diff(b, a)."""
        a, b = 1.1, 0.3
        assert phase_difference_deg(a, b) == pytest.approx(-phase_difference_deg(b, a))

    def test_result_in_valid_range(self):
        # (180, 0) is excluded: exact π difference maps to -180 due to modular
        # arithmetic, which is the boundary the docstring notation allows.
        for a_deg, b_deg in [(350, 5), (5, 350), (0, 359)]:
            a_rad = math.radians(a_deg)
            b_rad = math.radians(b_deg)
            d = phase_difference_deg(a_rad, b_rad)
            assert -180.0 < d <= 180.0, f"a={a_deg}, b={b_deg}: d={d}"


# ---------------------------------------------------------------------------
# Noise rejection (the core claim of the module)
# ---------------------------------------------------------------------------

class TestNoiseRejection:
    """The fundamental estimate must be far more robust than the peak estimate."""

    def test_fundamental_more_robust_than_peak(self):
        """Same synthetic as the self-test block in the module."""
        _, _, tri, _ = _make_signals()
        C = 1200e-12
        i_clean = np.gradient(tri * 1000.0, 1 / FS) * C * 1e3
        rng = np.random.default_rng(42)
        i_noisy = i_clean + rng.normal(0, 1.4, len(i_clean))

        clean_peak = np.percentile(np.abs(i_clean), PEAK_PERCENTILE)
        noisy_peak = np.percentile(np.abs(i_noisy), PEAK_PERCENTILE)
        pk_err = noisy_peak / clean_peak - 1

        clean_fund = fundamental(i_clean, FS, F)[0]
        noisy_fund = fundamental(i_noisy, FS, F)[0]
        f_err = noisy_fund / clean_fund - 1

        # Fundamental error must be < 2%; peak error must be > 50%
        assert abs(f_err) < 0.02, f"fund_err={f_err:.3%}"
        assert abs(pk_err) > 0.5,  f"pk_err={pk_err:.3%}"
