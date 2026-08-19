"""
Unit tests for rbl.hardware.raster_model — no Qt, no hardware.
"""
import math

import pytest

from rbl.hardware.raster_model import (
    deflection_mrad, displacement_mm, required_differential_kv,
    dwell_uniformity, lissajous_metrics,
)

# ES5 geometry, Section 1.7 of the plan.
L_CM, D_CM = 12.7, 3.8
Q, E_EV, DRIFT_CM = 1, 30_000.0, 100.0


class TestDeflection:
    def test_positive_for_positive_voltage(self):
        assert deflection_mrad(1.0, L_CM, D_CM, Q, E_EV) > 0

    def test_zero_energy_is_nan(self):
        assert math.isnan(deflection_mrad(1.0, L_CM, D_CM, Q, 0.0))

    def test_linear_in_voltage(self):
        theta1 = deflection_mrad(1.0, L_CM, D_CM, Q, E_EV)
        theta2 = deflection_mrad(2.0, L_CM, D_CM, Q, E_EV)
        assert theta2 == pytest.approx(2 * theta1)


class TestDisplacement:
    def test_linear_in_voltage(self):
        x1 = displacement_mm(1.0, L_CM, D_CM, Q, E_EV, DRIFT_CM)
        x2 = displacement_mm(2.0, L_CM, D_CM, Q, E_EV, DRIFT_CM)
        assert x2 == pytest.approx(2 * x1)

    def test_zero_energy_is_nan(self):
        assert math.isnan(displacement_mm(1.0, L_CM, D_CM, Q, 0.0, DRIFT_CM))


class TestRequiredDifferentialKv:
    def test_inverts_displacement_exactly(self):
        req_kv = required_differential_kv(
            target_half_width_mm=5.0, fwhm_mm=2.0, plate_length_cm=L_CM,
            plate_gap_cm=D_CM, charge_state=Q, beam_energy_ev=E_EV,
            drift_cm=DRIFT_CM, turnaround_k=1.5,
        )
        x_at_req = displacement_mm(req_kv, L_CM, D_CM, Q, E_EV, DRIFT_CM)
        assert x_at_req == pytest.approx(5.0 + 1.5 * 2.0, rel=1e-6)

    def test_turnaround_k_is_a_real_parameter(self):
        kv_1 = required_differential_kv(5.0, 2.0, L_CM, D_CM, Q, E_EV, DRIFT_CM,
                                         turnaround_k=1.0)
        kv_2 = required_differential_kv(5.0, 2.0, L_CM, D_CM, Q, E_EV, DRIFT_CM,
                                         turnaround_k=2.0)
        assert kv_2 > kv_1


class TestDwellUniformity:
    def test_wide_scan_is_uniform_over_narrow_sample(self):
        du = dwell_uniformity(fwhm_mm=1.0, scan_half_width_mm=10.0,
                               sample_half_width_mm=5.0)
        assert du["uniformity_pct"] < 1.0

    def test_tight_scan_is_less_uniform(self):
        wide = dwell_uniformity(1.0, 10.0, 5.0)
        tight = dwell_uniformity(1.0, 5.5, 5.0)
        assert tight["uniformity_pct"] > wide["uniformity_pct"]

    def test_degenerate_inputs_return_nan(self):
        du = dwell_uniformity(0.0, 10.0, 5.0)
        assert math.isnan(du["uniformity_pct"])


class TestLissajousMetrics:
    def test_coprime_frequencies_close_after_full_periods(self):
        m = lissajous_metrics(f_fast_hz=517.0, f_slow_hz=64.0, span_fast_mm=10.0,
                               span_slow_mm=10.0, fwhm_mm=1.0)
        assert math.gcd(517, 64) == 1
        assert m["repeat_period_s"] == pytest.approx(1.0, rel=1e-6)
        assert m["fills_uniformly"] is True

    def test_low_ratio_leaves_stripes(self):
        m = lissajous_metrics(f_fast_hz=128.0, f_slow_hz=64.0, span_fast_mm=10.0,
                               span_slow_mm=10.0, fwhm_mm=1.0)
        assert m["lines_per_fwhm"] < 2.0
        assert m["fills_uniformly"] is False

    def test_zero_frequency_is_degenerate(self):
        m = lissajous_metrics(0.0, 64.0, 10.0, 10.0, 1.0)
        assert math.isnan(m["repeat_period_s"])
        assert m["fills_uniformly"] is False
