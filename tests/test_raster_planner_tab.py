"""
Tests for rbl.gui.raster_planner_tab.RasterPlannerTab — a pure calculator
tab (no hardware, no live stream), so these tests exercise `_recompute()`
directly against the widgets' own values.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication

from rbl.config import load_calibration_store as store
from rbl.config.calibration_config import CAL_LOAD_CAP_PF
from rbl.gui.raster_planner_tab import RasterPlannerTab


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    return RasterPlannerTab()


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "load_calibration.json")
    return store


class TestCapacitanceSource:
    def test_defaults_to_global_fallback(self, tab):
        load_pf, label = tab._load_pf()
        assert load_pf == CAL_LOAD_CAP_PF
        assert "fallback" in label

    def test_uses_measured_capacitance_when_channel_selected(self, tab, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1050.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="impedance_sweep")
        tab.cb_channel.setCurrentText("X+")
        load_pf, label = tab._load_pf()
        assert load_pf == 1050.0
        assert "measured" in label

    def test_unmeasured_channel_falls_back(self, tab, isolated_store):
        tab.cb_channel.setCurrentText("Y-")
        load_pf, _ = tab._load_pf()
        assert load_pf == CAL_LOAD_CAP_PF


class TestRequiredDrive:
    def test_differential_is_double_the_plate_kv(self, tab):
        tab._recompute()
        diff_text = tab.lbl_differential_kv.text()
        plate_text = tab.lbl_plate_kv.text()
        diff_kv = float(diff_text.split()[0])
        plate_kv = float(plate_text.split()[0])
        # Both are independently rounded to 4 decimals for display, so allow
        # for that rounding rather than requiring bit-exact equality.
        assert diff_kv == pytest.approx(2 * plate_kv, abs=2e-4)

    def test_larger_turnaround_k_requires_more_voltage(self, tab):
        tab.sb_turnaround_k.setValue(1.0)
        tab._recompute()
        low = float(tab.lbl_differential_kv.text().split()[0])
        tab.sb_turnaround_k.setValue(3.0)
        tab._recompute()
        high = float(tab.lbl_differential_kv.text().split()[0])
        assert high > low


class TestEnvelopeCheck:
    def test_low_frequency_low_amplitude_is_inside(self, tab):
        tab.sb_freq_fast_hz.setValue(517.0)
        tab.sb_freq_slow_hz.setValue(64.0)
        tab._recompute()
        assert "INSIDE" in tab.lbl_envelope.text()

    def test_envelope_shrinks_at_higher_frequency(self, tab):
        tab.sb_freq_fast_hz.setValue(500.0)
        tab._recompute()
        avail_500 = float(tab.lbl_envelope.text().split(" vs. ")[1].split(" kV")[0])
        tab.sb_freq_fast_hz.setValue(3000.0)
        tab._recompute()
        avail_3000 = float(tab.lbl_envelope.text().split(" vs. ")[1].split(" kV")[0])
        assert avail_3000 < avail_500

    def test_beyond_bandwidth_wall_is_flagged(self, tab):
        tab.sb_freq_fast_hz.setValue(10_000.0)   # the spinbox ceiling == the bandwidth wall
        tab._recompute()
        assert "bandwidth" in tab.lbl_envelope.text().lower() or "OUTSIDE" in tab.lbl_envelope.text()


class TestLissajous:
    def test_coprime_frequencies_fill_uniformly(self, tab):
        tab.sb_freq_fast_hz.setValue(517.0)
        tab.sb_freq_slow_hz.setValue(64.0)
        tab._recompute()
        assert "fills uniformly" in tab.lbl_lissajous.text()

    def test_low_ratio_leaves_stripes(self, tab):
        tab.sb_freq_fast_hz.setValue(128.0)
        tab.sb_freq_slow_hz.setValue(64.0)
        tab._recompute()
        assert "STRIPES" in tab.lbl_lissajous.text()


class TestRecomputeIsWiredToInputs:
    def test_changing_fwhm_changes_uniformity(self, tab):
        tab.sb_fwhm_mm.setValue(0.1)
        tab._recompute()
        narrow = tab.lbl_uniformity.text()
        tab.sb_fwhm_mm.setValue(5.0)
        tab._recompute()
        wide = tab.lbl_uniformity.text()
        assert narrow != wide

    def test_steerer_change_triggers_recompute_without_raising(self, tab):
        tab.cb_steerer.setCurrentText("ES10 (2EA021440)")
        assert tab.lbl_differential_kv.text() != "—"
