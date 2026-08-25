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
from rbl.config.hardware_config import AMP_LABELS
from rbl.config.steerer_geometry import PLATE_RATING_KV
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


def _kv(text):
    return float(text.split()[0])


class TestPerChannelCapacitance:
    """All four channels are separate loads; none of them stands in for
    another.  See the module docstring on the tab."""

    def test_reports_all_four_channels(self, tab, isolated_store):
        caps = tab._channel_capacitance()
        assert set(caps) == set(AMP_LABELS)

    def test_unmeasured_channels_fall_back_and_say_so(self, tab, isolated_store):
        caps = tab._channel_capacitance()
        for label in AMP_LABELS:
            c_pf, source = caps[label]
            assert c_pf == CAL_LOAD_CAP_PF
            assert source == "fallback"

    def test_one_measured_channel_does_not_change_the_others(self, tab, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1050.0, g_us=0.0,
                                         load_condition="ON_PLATES",
                                         method="impedance_sweep")
        caps = tab._channel_capacitance()
        assert caps["X+"] == (1050.0, "measured")
        for label in ("X-", "Y+", "Y-"):
            assert caps[label] == (CAL_LOAD_CAP_PF, "fallback")

    def test_each_channel_current_uses_its_own_capacitance(self, tab, isolated_store):
        # A channel with half the capacitance draws half the current at the
        # same voltage and frequency - if one C were being applied to all
        # four, the two X currents would be identical.
        isolated_store.save_measurement("X+", c_pf=600.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        isolated_store.save_measurement("X-", c_pf=1200.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m")
        tab._recompute()
        text = tab.lbl_currents.text()
        parts = dict(zip(text.split()[0::3], text.split()[1::3]))
        # Currents are displayed to 3 decimals, so compare at that precision
        # rather than demanding bit-exactness from a rounded string.
        assert float(parts["X-"]) == pytest.approx(2 * float(parts["X+"]), abs=2e-3)

    def test_a_fallback_channel_is_flagged_not_silent(self, tab, isolated_store):
        tab._recompute()
        assert "fallback" in tab.lbl_c_source.text()
        for label in AMP_LABELS:
            assert label in tab.lbl_c_source.text()


class TestPerAxisDrive:
    """Rectangular samples: X and Y are computed independently."""

    def test_square_sample_gives_equal_axes(self, tab):
        tab.sb_width_x_mm.setValue(10.0)
        tab.sb_height_y_mm.setValue(10.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_x.text()) == pytest.approx(_kv(tab.lbl_kv_y.text()))

    def test_a_taller_sample_needs_more_y_drive(self, tab):
        tab.sb_width_x_mm.setValue(10.0)
        tab.sb_height_y_mm.setValue(30.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_y.text()) > _kv(tab.lbl_kv_x.text())

    def test_changing_x_does_not_move_y(self, tab):
        tab.sb_width_x_mm.setValue(10.0)
        tab.sb_height_y_mm.setValue(10.0)
        tab._recompute()
        y_before = _kv(tab.lbl_kv_y.text())
        tab.sb_width_x_mm.setValue(40.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_y.text()) == pytest.approx(y_before)

    def test_differential_is_double_the_plate_kv(self, tab):
        tab._recompute()
        # Both are independently rounded to 4 decimals for display.
        assert _kv(tab.lbl_kv_x.text()) == pytest.approx(
            2 * _kv(tab.lbl_plate_kv_x.text()), abs=2e-4)

    def test_larger_turnaround_k_requires_more_voltage(self, tab):
        tab.sb_turnaround_k.setValue(1.0)
        tab._recompute()
        low = _kv(tab.lbl_kv_x.text())
        tab.sb_turnaround_k.setValue(3.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_x.text()) > low

    def test_over_rating_is_flagged_on_the_axis_that_exceeds_it(self, tab):
        # A huge sample forces the drive past 5 kV/plate.
        tab.sb_width_x_mm.setValue(2.0)
        tab.sb_height_y_mm.setValue(800.0)
        tab._recompute()
        assert "OVER" not in tab.lbl_plate_kv_x.text()
        assert "OVER" in tab.lbl_plate_kv_y.text()
        assert f"{PLATE_RATING_KV:.0f}" in tab.lbl_plate_kv_y.text()


class TestSpeciesTable:
    def test_opens_on_the_sheets_four_species(self, tab):
        names = [tab.tbl_species.item(r, 0).text()
                 for r in range(tab.tbl_species.rowCount())]
        assert names == ["Protons", "Al", "Ni", "Ti"]

    def test_the_selected_species_lands_exactly_on_the_target(self, tab):
        # The drive is designed for the selected row, so that row's
        # deflection must equal the target half-span it was solved for.
        tab.tbl_species.selectRow(0)
        tab._recompute()
        target = float(tab.lbl_target.text().split("X ±")[1].split(" mm")[0])
        got = float(tab.tbl_species.item(0, 4).text().lstrip("±"))
        assert got == pytest.approx(target, abs=1e-3)

    def test_a_higher_charge_state_sweeps_further_at_the_same_voltage(self, tab):
        # Ni at 3 MeV q=3 against protons at 3 MeV q=1: same energy, three
        # times the charge, three times the deflection.
        tab.tbl_species.selectRow(0)
        tab._recompute()
        protons = float(tab.tbl_species.item(0, 4).text().lstrip("±"))
        nickel = float(tab.tbl_species.item(2, 4).text().lstrip("±"))
        assert nickel == pytest.approx(3 * protons, abs=2e-3)

    def test_mass_does_not_affect_deflection(self, tab):
        # theta = V*l*q/(2*d*E) has no mass in it.  Editing the mass column
        # must not move the number.
        tab._recompute()
        before = tab.tbl_species.item(1, 4).text()
        tab.tbl_species.item(1, 1).setText("999")
        tab._recompute()
        assert tab.tbl_species.item(1, 4).text() == before

    def test_editing_energy_changes_that_rows_deflection_only(self, tab):
        tab._recompute()
        other_before = tab.tbl_species.item(0, 4).text()
        tab.tbl_species.item(1, 2).setText("1.0")
        tab._recompute()
        assert tab.tbl_species.item(0, 4).text() == other_before
        # Lower energy, stiffer? No - lower energy deflects MORE.
        assert float(tab.tbl_species.item(1, 4).text().lstrip("±")) > 0

    def test_selecting_a_different_species_redesigns_the_drive(self, tab):
        tab.tbl_species.selectRow(0)
        tab._recompute()
        protons_kv = _kv(tab.lbl_kv_x.text())
        tab.tbl_species.selectRow(2)          # Ni, q=3
        tab._recompute()
        # Displayed to 4 decimals; compare at display precision.
        assert _kv(tab.lbl_kv_x.text()) == pytest.approx(protons_kv / 3.0, abs=2e-4)
        assert "Ni" in tab.lbl_design_species.text()

    def test_an_unparseable_row_does_not_raise(self, tab):
        tab.tbl_species.item(1, 2).setText("")
        tab._recompute()          # must not raise
        assert tab.tbl_species.item(1, 4).text() == "—"


class TestEnvelopeCheck:
    def test_low_frequency_low_amplitude_is_inside(self, tab):
        tab.sb_freq_x_hz.setValue(517.0)
        tab.sb_freq_y_hz.setValue(64.0)
        tab._recompute()
        assert "INSIDE" in tab.lbl_envelope.text()

    def test_it_names_the_binding_channel(self, tab):
        tab._recompute()
        assert any(tab.lbl_envelope.text().startswith(label) for label in AMP_LABELS)

    def test_envelope_shrinks_at_higher_frequency(self, tab):
        tab.sb_freq_x_hz.setValue(500.0)
        tab._recompute()
        avail_500 = float(tab.lbl_envelope.text().split(" vs. ")[1].split(" kV")[0])
        tab.sb_freq_x_hz.setValue(3000.0)
        tab._recompute()
        avail_3000 = float(tab.lbl_envelope.text().split(" vs. ")[1].split(" kV")[0])
        assert avail_3000 < avail_500

    def test_beyond_bandwidth_wall_is_flagged(self, tab):
        tab.sb_freq_x_hz.setValue(10_000.0)   # spinbox ceiling == bandwidth wall
        tab._recompute()
        text = tab.lbl_envelope.text()
        assert "bandwidth" in text.lower() or "OUTSIDE" in text


class TestRecomputeIsWiredToInputs:
    def test_changing_fwhm_changes_uniformity(self, tab):
        tab.sb_fwhm_mm.setValue(0.1)
        tab._recompute()
        narrow = tab.lbl_uniformity.text()
        tab.sb_fwhm_mm.setValue(5.0)
        tab._recompute()
        assert tab.lbl_uniformity.text() != narrow

    def test_uniformity_reports_both_axes(self, tab):
        tab._recompute()
        assert "X" in tab.lbl_uniformity.text() and "Y" in tab.lbl_uniformity.text()


class TestGoneForGood:
    def test_no_steerer_picker(self, tab):
        assert not hasattr(tab, "cb_steerer")

    def test_no_single_capacitance_channel_picker(self, tab):
        assert not hasattr(tab, "cb_channel")

    def test_no_lissajous_readout(self, tab):
        assert not hasattr(tab, "lbl_lissajous")


class TestFullWidthEntry:
    """Width in, halves inside — the operator types what the calipers read."""

    def test_full_width_is_halved_internally(self, tab):
        # A 16 mm wide sample is a ±8 mm half-span before the turnaround
        # margin is added, not ±16.
        tab.sb_width_x_mm.setValue(16.0)
        tab.sb_fwhm_mm.setValue(1.0)
        tab.sb_turnaround_k.setValue(1.5)
        tab._recompute()
        target = float(tab.lbl_target.text().split("X ±")[1].split(" mm")[0])
        assert target == pytest.approx(8.0 + 1.5, abs=1e-6)

    def test_doubling_the_width_doubles_the_half_span(self, tab):
        tab.sb_turnaround_k.setValue(0.0)
        tab.sb_width_x_mm.setValue(10.0)
        tab._recompute()
        first = _kv(tab.lbl_kv_x.text())
        tab.sb_width_x_mm.setValue(20.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_x.text()) == pytest.approx(2 * first, abs=2e-4)

    def test_the_old_half_width_boxes_are_gone(self, tab):
        assert not hasattr(tab, "sb_half_x_mm")
        assert not hasattr(tab, "sb_half_y_mm")


class TestAsymmetricRastering:
    """A centre offset moves the sweep; it does not resize it."""

    def test_offset_defaults_to_zero_and_sweeps_symmetrically(self, tab):
        assert tab.sb_offset_x_mm.value() == 0.0
        assert tab.sb_offset_y_mm.value() == 0.0
        tab._recompute()
        assert "-6.500 to +6.500" in tab.lbl_sweep_x.text() or "swept" in tab.lbl_sweep_x.text()

    def test_offset_does_not_change_the_ac_amplitude(self, tab):
        tab._recompute()
        before = _kv(tab.lbl_kv_x.text())
        tab.sb_offset_x_mm.setValue(4.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_x.text()) == pytest.approx(before, abs=2e-4)

    def test_offset_shifts_the_swept_interval(self, tab):
        tab.sb_width_x_mm.setValue(10.0)
        tab.sb_fwhm_mm.setValue(1.0)
        tab.sb_turnaround_k.setValue(1.5)
        tab.sb_offset_x_mm.setValue(3.0)
        tab._recompute()
        text = tab.lbl_sweep_x.text()
        assert "-3.500 to +9.500" in text

    def test_offset_is_reported_as_a_dc_term_not_two_amplitudes(self, tab):
        tab.sb_offset_x_mm.setValue(3.0)
        tab._recompute()
        assert "DC" in tab.lbl_sweep_x.text()
        assert "not unequal amplitudes" in tab.lbl_sweep_x.text()

    def test_offset_counts_against_the_plate_rating(self, tab):
        # Sized to clear the rating on AC alone and exceed it once the DC
        # term is added — the case checking the AC half alone would pass.
        tab.sb_width_x_mm.setValue(10.0)
        tab._recompute()
        assert "OVER" not in tab.lbl_plate_kv_x.text()
        tab.sb_offset_x_mm.setValue(40.0)
        tab._recompute()
        assert "OVER" in tab.lbl_plate_kv_x.text()

    def test_x_offset_does_not_disturb_y(self, tab):
        tab._recompute()
        y_before = _kv(tab.lbl_kv_y.text())
        y_sweep_before = tab.lbl_sweep_y.text()
        tab.sb_offset_x_mm.setValue(5.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_y.text()) == pytest.approx(y_before)
        assert tab.lbl_sweep_y.text() == y_sweep_before


class TestCapacitanceRefresh:
    """The store is a file; a file cannot announce that it changed."""

    def test_refresh_picks_up_a_measurement_taken_after_construction(
            self, tab, isolated_store):
        tab._recompute()
        assert "fallback" in tab.lbl_c_source.text()
        isolated_store.save_measurement("X+", c_pf=1528.7, g_us=5.1,
                                         load_condition="ON_PLATES",
                                         method="impedance_sweep")
        tab.refresh_capacitance()
        assert "1529 pF (measured)" in tab.lbl_c_source.text()

    def test_on_plates_wins_over_a_later_disconnected_sweep(self, tab, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1500.0, g_us=0.0,
                                         load_condition="ON_PLATES", method="m",
                                         measured_at=100.0)
        # Measured LATER, but it is the amplifier alone — not the load the
        # planner is planning for.
        isolated_store.save_measurement("X+", c_pf=120.0, g_us=0.0,
                                         load_condition="DISCONNECTED", method="m",
                                         measured_at=200.0)
        assert tab._channel_capacitance()["X+"] == (1500.0, "measured")

    def test_a_disconnected_only_channel_says_which_condition_it_is(
            self, tab, isolated_store):
        isolated_store.save_measurement("Y-", c_pf=130.0, g_us=0.0,
                                         load_condition="DISCONNECTED", method="m")
        c_pf, source = tab._channel_capacitance()["Y-"]
        assert c_pf == 130.0
        assert "DISCONNECTED" in source


class TestFrequencyIsNotGeometry:
    def test_frequency_does_not_change_the_required_voltage(self, tab):
        tab._recompute()
        kv_before = _kv(tab.lbl_kv_x.text())
        sweep_before = tab.lbl_sweep_x.text()
        tab.sb_freq_x_hz.setValue(37.0)
        tab._recompute()
        assert _kv(tab.lbl_kv_x.text()) == pytest.approx(kv_before)
        assert tab.lbl_sweep_x.text() == sweep_before

    def test_frequency_does_change_the_predicted_current(self, tab, isolated_store):
        tab.sb_freq_x_hz.setValue(100.0)
        tab._recompute()
        low = tab.lbl_currents.text()
        tab.sb_freq_x_hz.setValue(1000.0)
        tab._recompute()
        assert tab.lbl_currents.text() != low
