"""
Unit tests for the amp_label load-resolution path in
rbl.config.calibration_config.ac_peak_current_ma/ac_max_peak_kv.

A plate position's MEASURED on-plates capacitance (the newest characterization
result for the amplifier assigned there) sizes the ladder when there is one;
with none, the named SIZING_ASSUMPTION_PF is used - an assumption, deliberately
large, never a measurement. No fallback number is ever presented as measured,
and nothing here corrects an already-recorded value.
"""
from datetime import datetime, timedelta

import pytest

from rbl.config import calibration_config as cc
from rbl.config import characterization_history as ch


def measure(plate, c_pf, cond="ON_PLATES"):
    """Write a characterization result dated yesterday (no assignment on
    record, so the serial is "unassigned")."""
    ch.write_result(
        {"plate_position": plate, "amplifier_serial": "unassigned",
         "load_condition": cond, "method": "impedance_sweep",
         "values": {"c_pf": c_pf, "g_us": 0.0}},
        datetime.now().astimezone() - timedelta(days=1))


class TestLoadPfResolution:
    def test_explicit_load_pf_wins_over_everything(self):
        measure("X+", 999.0)
        result = cc.ac_peak_current_ma(1000.0, 1.0, load_pf=500.0, amp_label="X+")
        expected = cc.ac_shape_k(None) * 1000.0 * 500.0 * 1.0 * 1e-6
        assert result == pytest.approx(expected)

    def test_amp_label_uses_stored_measurement_when_present(self):
        measure("X+", 999.0)
        result = cc.ac_peak_current_ma(1000.0, 1.0, amp_label="X+")
        expected = cc.ac_shape_k(None) * 1000.0 * 999.0 * 1.0 * 1e-6
        assert result == pytest.approx(expected)

    def test_unmeasured_amp_label_falls_back_to_global_constant(self):
        # Kept under its old name: the "global constant" is now the named
        # sizing assumption, not a measurement.
        result = cc.ac_peak_current_ma(1000.0, 1.0, amp_label="X-")
        expected = cc.ac_peak_current_ma(1000.0, 1.0)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(
            cc.ac_shape_k(None) * 1000.0 * cc.SIZING_ASSUMPTION_PF * 1.0 * 1e-6)

    def test_no_amp_label_and_no_load_pf_uses_global_constant(self):
        assert cc.ac_peak_current_ma(1000.0, 1.0) == pytest.approx(
            cc.ac_shape_k(None) * 1000.0 * cc.SIZING_ASSUMPTION_PF * 1.0 * 1e-6)

    def test_ac_max_peak_kv_uses_measured_capacitance_for_labelled_channel(self):
        measure("Y+", 600.0)
        with_measured = cc.ac_max_peak_kv(1000.0, amp_label="Y+")
        default = cc.ac_max_peak_kv(1000.0)
        # A fifth of the assumed capacitance -> a much higher allowed peak kV.
        assert with_measured > default

    def test_measurement_never_alters_calibration_config_constant(self):
        measure("X+", 1.0)
        assert cc.SIZING_ASSUMPTION_PF == 3000   # the documented assumption is untouched


class TestNoFallbackCapacitance:
    def test_an_on_plates_result_is_what_the_plate_resolves_to(self):
        measure("X+", 1600.0)
        assert cc._resolve_load_pf(None, "X+") == 1600.0

    def test_only_a_cable_only_result_resolves_to_the_sizing_assumption(self):
        measure("X+", 400.0, cond="CABLE_ONLY")
        assert cc._resolve_load_pf(None, "X+") == 3000

    def test_only_a_disconnected_result_resolves_to_the_sizing_assumption(self):
        measure("X+", 120.0, cond="DISCONNECTED")
        assert cc._resolve_load_pf(None, "X+") == 3000

    def test_the_basis_says_measured_or_sizing_assumption(self):
        measure("X+", 1600.0)
        assert cc.resolve_load_pf_with_basis(None, "X+") == (1600.0, "measured")
        assert cc.resolve_load_pf_with_basis(None, "X-") == (3000, "sizing assumption")
        assert cc.resolve_load_pf_with_basis(500.0, "X+") == (500.0, "given")

    def test_the_old_global_constant_is_gone(self):
        assert not hasattr(cc, "CAL_LOAD_CAP_PF")

    def test_the_sizing_assumption_is_larger_than_anything_measured_so_far(self):
        # Deliberately: an unmeasured load is treated as large, never small.
        assert cc.SIZING_ASSUMPTION_PF >= 1650
