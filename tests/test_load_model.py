"""
Unit tests for rbl.hardware.load_model — no Qt, no hardware.
"""
import math

import numpy as np
import pytest

from rbl.config.calibration_config import ac_shape_k, AC_DEFAULT_SHAPE
from rbl.hardware.load_model import (
    admittance_from_fundamentals, capacitance_from_charge, envelope_walls,
    _SHAPE_K,
)


class TestShapeKCrossCheck:
    """Guards against `load_model._SHAPE_K` drifting from the authoritative
    `calibration_config._AC_PEAK_CURRENT_K` it deliberately duplicates to stay
    config-lookup-free (see load_model's module docstring)."""

    @pytest.mark.parametrize("shape", ["sine", "triangle", "ramp", "square"])
    def test_matches_calibration_config(self, shape):
        assert _SHAPE_K[shape] == pytest.approx(ac_shape_k(shape))

    def test_default_shape_is_covered(self):
        assert AC_DEFAULT_SHAPE in _SHAPE_K


class TestAdmittanceFromFundamentals:
    def test_pure_capacitor_has_zero_conductance(self):
        f = 1000.0
        c_pf = 1200.0
        v_pk_kv = 1.0
        i_pk_ma = 2 * math.pi * f * (c_pf * 1e-12) * (v_pk_kv * 1000.0) * 1e3
        r = admittance_from_fundamentals(i_pk_ma, v_pk_kv, 90.0, f)
        assert r["c_pf"] == pytest.approx(c_pf, rel=1e-6)
        assert r["g_us"] == pytest.approx(0.0, abs=1e-9)
        assert r["loss_tangent"] == pytest.approx(0.0, abs=1e-9)

    def test_phase_off_90_shows_up_as_conductance(self):
        f = 1000.0
        i_pk_ma = 2 * math.pi * f * (1200e-12) * 1000.0 * 1e3
        r = admittance_from_fundamentals(i_pk_ma, 1.0, 80.0, f)
        assert r["g_us"] > 0
        assert r["loss_tangent"] > 0

    @pytest.mark.parametrize("kwargs", [
        dict(i_fund_ma=1.0, v_fund_kv=0.0, phase_deg=90.0, freq_hz=1000.0),
        dict(i_fund_ma=1.0, v_fund_kv=1.0, phase_deg=90.0, freq_hz=0.0),
        dict(i_fund_ma=float("nan"), v_fund_kv=1.0, phase_deg=90.0, freq_hz=1000.0),
    ])
    def test_degenerate_inputs_return_nan(self, kwargs):
        r = admittance_from_fundamentals(**kwargs)
        assert math.isnan(r["c_pf"])
        assert math.isnan(r["g_us"])
        assert math.isnan(r["loss_tangent"])


class TestCapacitanceFromCharge:
    def test_recovers_known_capacitance_from_a_rectangular_pulse(self):
        fs = 1_000_000.0
        dt = 1.0 / fs
        n = 1000
        baseline = 0.5
        pulse_ma = np.full(n, baseline)
        pulse_ma[100:200] = baseline + 10.0
        delta_v_kv = 2.0
        q_expected_c = 10e-3 * 100e-6
        c_expected_pf = q_expected_c / (delta_v_kv * 1000.0) * 1e12
        c_pf = capacitance_from_charge(pulse_ma, dt, baseline, delta_v_kv)
        assert c_pf == pytest.approx(c_expected_pf, rel=0.02)

    def test_sign_of_step_does_not_matter(self):
        fs = 1_000_000.0
        dt = 1.0 / fs
        pulse_ma = np.full(1000, 0.5)
        pulse_ma[100:200] = 0.5 + 10.0
        c_pos = capacitance_from_charge(pulse_ma, dt, 0.5, 2.0)
        c_neg = capacitance_from_charge(pulse_ma, dt, 0.5, -2.0)
        assert c_pos == pytest.approx(c_neg)

    def test_degenerate_inputs_return_nan(self):
        assert math.isnan(capacitance_from_charge([1.0], 1e-6, 0.0, 1.0))
        assert math.isnan(capacitance_from_charge([1.0, 2.0], 1e-6, 0.0, 0.0))


class TestEnvelopeWalls:
    @pytest.mark.parametrize("v_kv,f_hz", [
        (5.0, 833.0), (4.0, 1042.0), (2.0, 2083.0), (1.0, 4167.0),
    ])
    def test_reproduces_section_1_6_table(self, v_kv, f_hz):
        walls = envelope_walls(load_pf=1200.0, trip_ma=20.0, shape="triangle",
                                max_kv=5.0, max_f_hz=10_000.0)
        v_check = np.interp(f_hz, walls["freq_hz"], walls["current_wall_kv"])
        assert v_check == pytest.approx(v_kv, rel=0.01)

    def test_voltage_wall_is_flat_at_max_kv(self):
        walls = envelope_walls(1200.0, 20.0, "triangle", 5.0, 10_000.0)
        assert np.all(walls["voltage_wall_kv"] == 5.0)

    def test_envelope_is_nan_past_bandwidth(self):
        walls = envelope_walls(1200.0, 20.0, "triangle", 5.0, 10_000.0)
        past = walls["freq_hz"] > 10_000.0
        assert past.any()
        assert np.all(np.isnan(walls["envelope_kv"][past]))

    def test_rejects_non_positive_inputs(self):
        with pytest.raises(ValueError):
            envelope_walls(0.0, 20.0, "triangle", 5.0, 10_000.0)

    def test_unknown_shape_raises(self):
        with pytest.raises(ValueError):
            envelope_walls(1200.0, 20.0, "hexagon", 5.0, 10_000.0)
