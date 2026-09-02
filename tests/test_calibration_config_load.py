"""
Unit tests for the amp_label load-resolution path added to
rbl.config.calibration_config.ac_peak_current_ma/ac_max_peak_kv (Phase 1,
Section 2.5) — per-channel measured capacitance shortens the ladder more
accurately than CAL_LOAD_CAP_PF once measured, without ever being consulted
by anything that corrects an already-recorded value.
"""
import pytest

from rbl.config import calibration_config as cc
from rbl.config import load_calibration_store as store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "load_calibration.json")
    return store


class TestLoadPfResolution:
    def test_explicit_load_pf_wins_over_everything(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=999.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        result = cc.ac_peak_current_ma(1000.0, 1.0, load_pf=500.0, amp_label="X+")
        expected = cc.ac_shape_k(None) * 1000.0 * 500.0 * 1.0 * 1e-6
        assert result == pytest.approx(expected)

    def test_amp_label_uses_stored_measurement_when_present(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=999.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        result = cc.ac_peak_current_ma(1000.0, 1.0, amp_label="X+")
        expected = cc.ac_shape_k(None) * 1000.0 * 999.0 * 1.0 * 1e-6
        assert result == pytest.approx(expected)

    def test_unmeasured_amp_label_falls_back_to_global_constant(self, isolated_store):
        result = cc.ac_peak_current_ma(1000.0, 1.0, amp_label="X-")
        expected = cc.ac_peak_current_ma(1000.0, 1.0)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(
            cc.ac_shape_k(None) * 1000.0 * cc.CAL_LOAD_CAP_PF * 1.0 * 1e-6)

    def test_no_amp_label_and_no_load_pf_uses_global_constant(self, isolated_store):
        assert cc.ac_peak_current_ma(1000.0, 1.0) == pytest.approx(
            cc.ac_shape_k(None) * 1000.0 * cc.CAL_LOAD_CAP_PF * 1.0 * 1e-6)

    def test_ac_max_peak_kv_uses_measured_capacitance_for_labelled_channel(self, isolated_store):
        isolated_store.save_measurement("Y+", c_pf=600.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        with_measured = cc.ac_max_peak_kv(1000.0, amp_label="Y+")
        default = cc.ac_max_peak_kv(1000.0)
        # Half the capacitance -> roughly double the max peak kV (until the
        # CAL_MAX_KV ceiling clamps it).
        assert with_measured > default

    def test_measurement_never_alters_calibration_config_constant(self, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        assert cc.CAL_LOAD_CAP_PF == 1500   # the documented fallback is untouched
