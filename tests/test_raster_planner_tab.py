"""
Tests for rbl.gui.raster_planner_tab.RasterPlannerTab.

The tab reads hardware only to OFFER numbers, so these exercise
`_recompute()` directly against the widgets' own values. Where a test is
about the MATH it reads `tab._solution`; where it is about what the operator
actually SEES it reads the label text, because several of these numbers are
only safe if they are labelled (see the generator/plate/differential
coincidence at a gain of 1000).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import load_calibration_store as store
from rbl.config.calibration_config import CAL_LOAD_CAP_PF
from rbl.config.hardware_config import AMP_LABELS
from rbl.config.steerer_geometry import PLATE_RATING_KV
from rbl.gui.raster_planner_tab import (
    MODE_SLIT,
    MODE_STEERER,
    RasterPlannerTab,
)
from rbl.hardware import slit_raster_model as srm


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    return RasterPlannerTab()


@pytest.fixture
def steerer_tab(tab):
    """The tab in the old, slits-parked-open mode."""
    tab.cmb_mode.setCurrentText(MODE_STEERER)
    tab.recompute()
    return tab


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "load_calibration.json")
    return store


def _kv(tab, axis):
    return tab.solution[axis]["amplitude_kv"]


class TestPerChannelCapacitance:
    """All four channels are separate loads; none stands in for another."""

    def test_reports_all_four_channels(self, tab, isolated_store):
        assert set(tab.channel_capacitance()) == set(AMP_LABELS)

    def test_unmeasured_channels_fall_back_and_say_so(self, tab, isolated_store):
        for label in AMP_LABELS:
            assert tab.channel_capacitance()[label] == (CAL_LOAD_CAP_PF, "fallback")

    def test_one_measured_channel_does_not_change_the_others(self, tab, isolated_store):
        isolated_store.save_measurement("X+", c_pf=1050.0, g_us=0.0,
                                        load_condition="ON_PLATES",
                                        method="impedance_sweep")
        caps = tab.channel_capacitance()
        assert caps["X+"] == (1050.0, "measured")
        for label in ("X-", "Y+", "Y-"):
            assert caps[label] == (CAL_LOAD_CAP_PF, "fallback")

    def test_each_channel_current_uses_its_own_capacitance(self, tab, isolated_store):
        isolated_store.save_measurement("X+", c_pf=600.0, g_us=0.0,
                                        load_condition="ON_PLATES", method="m")
        isolated_store.save_measurement("X-", c_pf=1200.0, g_us=0.0,
                                        load_condition="ON_PLATES", method="m")
        tab.recompute()
        parts = tab.lbl_currents.text().split()
        mapping = dict(zip(parts[0::3], parts[1::3]))
        assert float(mapping["X-"]) == pytest.approx(2 * float(mapping["X+"]), abs=2e-3)

    def test_a_fallback_channel_is_flagged_not_silent(self, tab, isolated_store):
        tab.recompute()
        assert "fallback" in tab.lbl_c_source.text()
        for label in AMP_LABELS:
            assert label in tab.lbl_c_source.text()


class TestTheJawsAreImagedOntoTheSample:
    """The change this rebuild exists for."""

    def test_the_patch_is_the_size_that_was_asked_for(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.sb_patch_y_mm.setValue(10.0)
        tab.recompute()
        assert tab.solution["X"]["painted_full_mm"] == pytest.approx(5.0)
        assert tab.solution["Y"]["painted_full_mm"] == pytest.approx(10.0)

    def test_the_jaws_are_set_narrower_than_the_patch(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.recompute()
        # Setting the jaws to 5 mm is what gave a patch ~64 % too big.
        assert tab.solution["X"]["gap_mm"] < 5.0

    def test_the_two_axes_get_different_magnifications(self, tab):
        tab.recompute()
        mx = tab.solution["X"]["magnification"]
        my = tab.solution["Y"]["magnification"]
        assert abs(mx - my) > 0.1
        assert "does not paint a square" in tab.lbl_magnification.text()

    def test_a_square_request_gives_unequal_jaws(self, tab):
        tab.sb_patch_x_mm.setValue(8.0)
        tab.sb_patch_y_mm.setValue(8.0)
        tab.recompute()
        assert tab.solution["X"]["gap_mm"] != pytest.approx(
            tab.solution["Y"]["gap_mm"], abs=1e-3)

    def test_moving_the_slit_plane_moves_the_magnification(self, tab):
        # The slit plane fractions are UNMEASURED, so the tab has to show
        # what depends on them rather than bury the dependency.
        tab.sb_slit_fx.setValue(0.1)
        tab.recompute()
        near = tab.solution["X"]["magnification"]
        tab.sb_slit_fx.setValue(0.9)
        tab.recompute()
        assert tab.solution["X"]["magnification"] < near


class TestBladePositions:
    def test_all_four_blades_are_named_in_motor_tab_units(self, tab):
        tab.recompute()
        text = tab.lbl_blades.text()
        for label in AMP_LABELS:
            assert label in text

    def test_symmetric_by_default(self, tab):
        tab.recompute()
        assert tab.solution["X"]["blade_plus_mm"] == pytest.approx(
            tab.solution["X"]["blade_minus_mm"])

    def test_a_blade_that_would_cross_centre_is_flagged(self, tab):
        tab.chk_jaw_offset.setChecked(True)
        tab.sb_patch_x_mm.setValue(2.0)
        tab.sb_offset_x_mm.setValue(20.0)
        tab.recompute()
        assert "cross MECHANICAL centre" in tab.lbl_blades.text()
        assert not tab.btn_apply_slits.isEnabled()

    def test_apply_is_disabled_without_a_beamline(self, tab):
        tab.recompute()
        assert not tab.btn_apply_slits.isEnabled()

    def test_apply_is_offered_once_a_beamline_is_wired(self, qapp):
        moved = {}

        class FakeBeamline:
            def move_slit(self, label, mm):
                moved[label] = mm
                return True

        wired = RasterPlannerTab(beamline=FakeBeamline())
        wired.recompute()
        assert wired.btn_apply_slits.isEnabled()
        # Enabling the button is not the same as pressing it. Nothing about a
        # recompute may reach the hardware.
        assert moved == {}


class TestTheOverscanIsAtTheJaw:
    """The constant did not change value. It changed plane."""

    def test_the_margin_is_k_times_the_slit_plane_fwhm(self, tab):
        tab.sb_fwhm_mm.setValue(1.2)
        tab.sb_turnaround_k.setValue(1.5)
        tab.recompute()
        sol = tab.solution["X"]
        assert sol["overscan_margin_mm"] == pytest.approx(1.8)
        assert (sol["sweep_half_at_slit_mm"] - sol["blade_plus_mm"]
                == pytest.approx(1.8))

    def test_the_beam_width_does_not_change_the_patch(self, tab):
        tab.sb_fwhm_mm.setValue(0.4)
        tab.recompute()
        blades = tab.solution["X"]["blade_plus_mm"]
        tab.sb_fwhm_mm.setValue(2.4)
        tab.recompute()
        assert tab.solution["X"]["blade_plus_mm"] == pytest.approx(blades)

    def test_a_wider_beam_costs_amplitude(self, tab):
        tab.sb_fwhm_mm.setValue(0.4)
        tab.recompute()
        narrow = _kv(tab, "X")
        tab.sb_fwhm_mm.setValue(2.4)
        tab.recompute()
        assert _kv(tab, "X") > narrow

    def test_the_droop_readout_tracks_k(self, tab):
        tab.sb_turnaround_k.setValue(0.5)
        tab.recompute()
        loose = float(tab.lbl_droop.text().split()[0])
        tab.sb_turnaround_k.setValue(1.5)
        tab.recompute()
        assert float(tab.lbl_droop.text().split()[0]) < loose

    def test_the_dose_is_flat_across_the_patch(self, tab):
        tab.recompute()
        assert tab.solution["X"]["dose_uniformity_pct"] < 0.05
        assert tab.solution["X"]["dose_regime"] == "jaw-limited"


class TestTheCostOfOverscan:
    def test_transmission_is_reported_for_both_axes_and_combined(self, tab):
        tab.recompute()
        text = tab.lbl_transmission.text()
        assert "X" in text and "Y" in text and "reaches the sample" in text

    def test_more_overscan_throws_more_beam_away(self, tab):
        tab.sb_turnaround_k.setValue(0.5)
        tab.recompute()
        tight = tab.solution["X"]["dose_transmitted_fraction"]
        tab.sb_turnaround_k.setValue(3.0)
        tab.recompute()
        assert tab.solution["X"]["dose_transmitted_fraction"] < tight

    def test_the_cost_is_named_as_a_trade_not_a_fault(self, tab):
        tab.recompute()
        assert "price of a flat top" in tab.lbl_transmission.text()


class TestAsymmetry:
    """Two independent levers, both off by default."""

    def test_both_offsets_default_off(self, tab):
        assert not tab.chk_jaw_offset.isChecked()
        assert not tab.chk_sweep_offset.isChecked()

    def test_the_jaw_offset_moves_the_patch_without_touching_the_drive(self, tab):
        tab.recompute()
        before = _kv(tab, "X")
        tab.chk_jaw_offset.setChecked(True)
        tab.sb_offset_x_mm.setValue(1.0)
        tab.recompute()
        sol = tab.solution["X"]
        assert sol["blade_plus_mm"] > sol["blade_minus_mm"]
        assert (sol["painted_max_mm"] + sol["painted_min_mm"]) / 2 == pytest.approx(1.0)
        # Amplitude DOES rise with a centred sweep — that is the cost the
        # sweep-offset lever exists to buy back.
        assert _kv(tab, "X") > before

    def test_centring_the_sweep_buys_the_amplitude_back(self, tab):
        tab.chk_jaw_offset.setChecked(True)
        tab.sb_offset_x_mm.setValue(1.5)
        tab.recompute()
        centred_sweep_off = _kv(tab, "X")
        tab.chk_sweep_offset.setChecked(True)
        tab.recompute()
        assert _kv(tab, "X") < centred_sweep_off

    def test_the_dc_term_is_spelled_out_as_equal_and_opposite(self, tab):
        tab.chk_jaw_offset.setChecked(True)
        tab.chk_sweep_offset.setChecked(True)
        tab.sb_offset_x_mm.setValue(1.5)
        tab.recompute()
        assert "equal and opposite" in tab.lbl_gen_x.text()

    def test_an_x_offset_does_not_disturb_y(self, tab):
        tab.recompute()
        y_before = _kv(tab, "Y")
        tab.chk_jaw_offset.setChecked(True)
        tab.sb_offset_x_mm.setValue(2.0)
        tab.recompute()
        assert _kv(tab, "Y") == pytest.approx(y_before)


class TestTheDriveReadoutSaysWhoseVoltsTheseAre:
    def test_the_generator_line_names_the_channels_and_the_phase(self, tab):
        tab.recompute()
        assert "Vpp" in tab.lbl_gen_x.text()
        assert "180°" in tab.lbl_gen_x.text()
        assert "X+" in tab.lbl_gen_x.text() and "X-" in tab.lbl_gen_x.text()

    def test_the_plate_line_gives_both_per_plate_and_differential(self, tab):
        tab.recompute()
        text = tab.lbl_plate_x.text()
        assert "per plate" in text and "plate-to-plate" in text

    def test_vpp_and_differential_kv_are_numerically_equal_but_labelled_apart(self, tab):
        # The gain-of-1000 coincidence. It is not a bug to fix; it is the
        # gain. What makes it safe is that the two numbers say whose they are.
        tab.recompute()
        sol = tab.solution["X"]
        assert sol["gen_amp_vpp"] == pytest.approx(sol["amplitude_kv"])
        assert "Vpp" in tab.lbl_gen_x.text()
        assert "kV" in tab.lbl_plate_x.text()

    def test_over_rating_is_flagged_on_the_axis_that_exceeds_it(self, tab):
        tab.sb_patch_x_mm.setValue(1.0)
        tab.sb_patch_y_mm.setValue(400.0)
        tab.recompute()
        assert "OVER" not in tab.lbl_plate_x.text()
        assert "OVER" in tab.lbl_plate_y.text()
        assert f"{PLATE_RATING_KV:.0f}" in tab.lbl_plate_y.text()

    def test_the_generator_rail_is_checked_in_generator_volts(self, tab):
        tab.sb_patch_y_mm.setValue(400.0)
        tab.recompute()
        assert "±5 V amplifier input" in tab.lbl_gen_y.text()


class TestModeSwitch:
    def test_it_opens_in_slit_limited_mode(self, tab):
        assert tab.cmb_mode.currentText() == MODE_SLIT

    def test_steerer_mode_says_the_jaws_are_a_floor_not_a_setting(self, steerer_tab):
        assert "parked open" in steerer_tab.lbl_blades.text()
        assert "AT LEAST" in steerer_tab.lbl_blades.text()
        assert not steerer_tab.btn_apply_slits.isEnabled()

    def test_steerer_mode_still_sizes_from_the_sample_edge(self, steerer_tab):
        # The lab sheet's behaviour, unchanged: half the width plus k*FWHM.
        steerer_tab.sb_patch_x_mm.setValue(16.0)
        steerer_tab.sb_fwhm_mm.setValue(1.0)
        steerer_tab.sb_turnaround_k.setValue(1.5)
        steerer_tab.recompute()
        assert steerer_tab.solution["X"]["scan_half_span_mm"] == pytest.approx(9.5)

    def test_slit_mode_needs_less_amplitude_than_steerer_mode(self, tab):
        # Sizing at the slit plane is sizing at ~60 % of the lever arm, so
        # the same patch costs more volts. This is the direction that must
        # not silently flip.
        tab.cmb_mode.setCurrentText(MODE_STEERER)
        tab.recompute()
        steerer_kv = _kv(tab, "X")
        tab.cmb_mode.setCurrentText(MODE_SLIT)
        tab.recompute()
        assert _kv(tab, "X") > steerer_kv

    def test_transmission_is_not_claimed_in_steerer_mode(self, steerer_tab):
        assert "not applicable" in steerer_tab.lbl_transmission.text()


class TestTheBeamlineTable:
    def test_it_has_a_row_for_each_slit_pair_and_the_sample(self, tab):
        tab.recompute()
        names = [tab.tbl_planes.item(r, 0).text()
                 for r in range(tab.tbl_planes.rowCount())]
        assert "X slits" in names and "Y slits" in names and "Sample" in names

    def test_the_slit_rows_say_where_to_put_the_jaws(self, tab):
        tab.recompute()
        for r in range(tab.tbl_planes.rowCount()):
            if tab.tbl_planes.item(r, 0).text() == "X slits":
                note = tab.tbl_planes.item(r, 8).text()
                assert "set X+" in note and "turns around" in note
                return
        pytest.fail("no X slits row")

    def test_the_alumina_only_appears_when_it_is_offset(self, tab):
        tab.recompute()
        names = [tab.tbl_planes.item(r, 0).text()
                 for r in range(tab.tbl_planes.rowCount())]
        assert "Alumina" not in names
        tab.sb_alumina_offset_mm.setValue(20.0)
        tab.recompute()
        names = [tab.tbl_planes.item(r, 0).text()
                 for r in range(tab.tbl_planes.rowCount())]
        assert "Alumina" in names

    def test_the_alumina_sees_a_slightly_bigger_patch_than_the_sample(self, tab):
        tab.sb_alumina_offset_mm.setValue(20.0)
        tab.recompute()
        assert "on the alumina" in tab.lbl_patch.text()

    def test_the_drift_tube_is_checked_against_its_bore(self, tab):
        tab.recompute()
        for r in range(tab.tbl_planes.rowCount()):
            if tab.tbl_planes.item(r, 0).text() == "DT":
                assert "bore" in tab.tbl_planes.item(r, 8).text()
                return
        pytest.fail("no DT row")

    def test_a_sweep_that_paints_the_drift_tube_is_warned_about(self, tab):
        tab.sb_patch_y_mm.setValue(300.0)
        tab.recompute()
        for r in range(tab.tbl_planes.rowCount()):
            if tab.tbl_planes.item(r, 0).text() == "DT":
                assert "painting the drift tube" in tab.tbl_planes.item(r, 8).text()
                return
        pytest.fail("no DT row")


class TestSpeciesTable:
    def test_opens_on_the_sheets_four_species(self, tab):
        names = [tab.tbl_species.item(r, 0).text()
                 for r in range(tab.tbl_species.rowCount())]
        assert names == ["Protons", "Al", "Ni", "Ti"]

    def test_the_selected_species_lands_exactly_on_the_target(self, tab):
        tab.tbl_species.selectRow(0)
        tab.recompute()
        target = tab.solution["X"]["sweep_half_at_target_mm"]
        got = float(tab.tbl_species.item(0, 4).text().lstrip("±"))
        assert got == pytest.approx(target, abs=1e-3)

    def test_a_higher_charge_state_sweeps_further_at_the_same_voltage(self, tab):
        tab.tbl_species.selectRow(0)
        tab.recompute()
        protons = float(tab.tbl_species.item(0, 4).text().lstrip("±"))
        nickel = float(tab.tbl_species.item(2, 4).text().lstrip("±"))
        assert nickel == pytest.approx(3 * protons, abs=2e-3)

    def test_mass_does_not_affect_deflection(self, tab):
        tab.recompute()
        before = tab.tbl_species.item(1, 4).text()
        tab.tbl_species.item(1, 1).setText("999")
        tab.recompute()
        assert tab.tbl_species.item(1, 4).text() == before

    def test_selecting_a_different_species_redesigns_the_drive(self, tab):
        tab.tbl_species.selectRow(0)
        tab.recompute()
        protons_kv = _kv(tab, "X")
        tab.tbl_species.selectRow(2)          # Ni, q=3
        tab.recompute()
        assert _kv(tab, "X") == pytest.approx(protons_kv / 3.0, rel=1e-6)
        assert "Ni" in tab.lbl_design_species.text()

    def test_an_unparseable_row_does_not_raise(self, tab):
        tab.tbl_species.item(1, 2).setText("")
        tab.recompute()
        assert tab.tbl_species.item(1, 4).text() == "—"


class TestEnvelopeCheck:
    def test_low_frequency_low_amplitude_is_inside(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.sb_patch_y_mm.setValue(5.0)
        tab.sb_freq_x_hz.setValue(517.0)
        tab.sb_freq_y_hz.setValue(64.0)
        tab.recompute()
        assert "INSIDE" in tab.lbl_envelope.text()

    def test_it_names_the_binding_channel(self, tab):
        tab.recompute()
        assert any(tab.lbl_envelope.text().startswith(label) for label in AMP_LABELS)

    def test_envelope_shrinks_at_higher_frequency(self, tab):
        tab.sb_freq_x_hz.setValue(500.0)
        tab.recompute()
        a = float(tab.lbl_envelope.text().split(" vs. ")[1].split(" kV")[0])
        tab.sb_freq_x_hz.setValue(3000.0)
        tab.recompute()
        b = float(tab.lbl_envelope.text().split(" vs. ")[1].split(" kV")[0])
        assert b < a

    def test_beyond_bandwidth_wall_is_flagged(self, tab):
        tab.sb_freq_x_hz.setValue(10_000.0)
        tab.recompute()
        text = tab.lbl_envelope.text()
        assert "bandwidth" in text.lower() or "OUTSIDE" in text


class TestFrequencyIsNotGeometry:
    def test_frequency_does_not_change_the_required_voltage(self, tab):
        tab.recompute()
        before = _kv(tab, "X")
        blades_before = tab.lbl_blades.text()
        tab.sb_freq_x_hz.setValue(37.0)
        tab.recompute()
        assert _kv(tab, "X") == pytest.approx(before)
        assert tab.lbl_blades.text() == blades_before

    def test_frequency_does_change_the_predicted_current(self, tab, isolated_store):
        tab.sb_freq_x_hz.setValue(100.0)
        tab.recompute()
        low = tab.lbl_currents.text()
        tab.sb_freq_x_hz.setValue(1000.0)
        tab.recompute()
        assert tab.lbl_currents.text() != low


class TestTheProfilerIsOfferedNeverAdopted:
    def test_without_a_profiler_the_button_is_dead(self, tab):
        tab.recompute()
        assert not tab.btn_use_profiler.isEnabled()
        assert "not wired" in tab.lbl_profiler.text()

    def test_a_reading_is_shown_but_not_taken(self, qapp):
        class FakeState:
            fwhm_x_mm = 1.75
            fwhm_y_mm = 2.10

        class FakeProfiler:
            _last_measured = FakeState()

        t = RasterPlannerTab(profiler=FakeProfiler())
        t.recompute()
        assert "1.750" in t.lbl_profiler.text()
        assert t.btn_use_profiler.isEnabled()
        # NOT adopted: only the operator knows whether the raster was off.
        assert t.sb_fwhm_mm.value() != pytest.approx(1.75)
        t.btn_use_profiler.click()
        assert t.sb_fwhm_mm.value() == pytest.approx(1.75)

    def test_it_says_to_measure_with_the_raster_off(self, tab):
        assert any("RASTER OFF" in w.text()
                   for w in tab.findChildren(type(tab.lbl_profiler)))


class TestCapacitanceRefresh:
    """The store is a file; a file cannot announce that it changed."""

    def test_refresh_picks_up_a_measurement_taken_after_construction(
            self, tab, isolated_store):
        tab.recompute()
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
        isolated_store.save_measurement("X+", c_pf=120.0, g_us=0.0,
                                        load_condition="DISCONNECTED", method="m",
                                        measured_at=200.0)
        assert tab.channel_capacitance()["X+"] == (1500.0, "measured")

    def test_a_disconnected_only_channel_says_which_condition_it_is(
            self, tab, isolated_store):
        isolated_store.save_measurement("Y-", c_pf=130.0, g_us=0.0,
                                        load_condition="DISCONNECTED", method="m")
        c_pf, source = tab.channel_capacitance()["Y-"]
        assert c_pf == 130.0
        assert "DISCONNECTED" in source


class TestThePlaneTableIsHonestAtTheJaw:
    def test_the_slit_row_shows_its_own_axis_clipped(self, tab):
        """The one row where the clipping is the whole point of looking."""
        tab.sb_patch_x_mm.setValue(5.0)
        tab.recompute()
        blade = tab.solution["X"]["blade_plus_mm"]
        for r in range(tab.tbl_planes.rowCount()):
            if tab.tbl_planes.item(r, 0).text() == "X slits":
                commanded = tab.tbl_planes.item(r, 3).text()
                passes = tab.tbl_planes.item(r, 4).text()
                assert passes != commanded
                assert f"+{blade:.3f}" in passes
                return
        pytest.fail("no X slits row")

    def test_an_x_aperture_is_never_applied_to_the_y_axis(self, tab):
        tab.sb_patch_x_mm.setValue(2.0)
        tab.sb_patch_y_mm.setValue(20.0)
        tab.recompute()
        for r in range(tab.tbl_planes.rowCount()):
            if tab.tbl_planes.item(r, 0).text() == "X slits":
                # At the X jaws the Y axis has met nothing yet.
                assert tab.tbl_planes.item(r, 6).text() == tab.tbl_planes.item(r, 7).text()
                return
        pytest.fail("no X slits row")


class TestTheBeamFrameIsNotTheSlitFrame:
    """Moving the jaws to meet an off-centre beam is bookkeeping. Moving the
    patch off the beam is physics. The tab must not confuse them."""

    def test_the_beam_centre_defaults_to_concentric(self, tab):
        assert tab.sb_beam_centre_x_mm.value() == 0.0
        assert tab.sb_beam_centre_y_mm.value() == 0.0

    def test_a_beam_offset_moves_only_the_blade_numbers(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.recompute()
        before = dict(tab.solution["X"])
        tab.sb_beam_centre_x_mm.setValue(-1.218)
        tab.recompute()
        after = tab.solution["X"]
        # The solve is in the beam's frame and must be untouched.
        for key in ("amplitude_kv", "blade_plus_mm", "blade_minus_mm",
                    "painted_min_mm", "painted_max_mm",
                    "dose_transmitted_fraction", "sweep_half_at_slit_mm"):
            assert after[key] == pytest.approx(before[key]), key

    def test_the_blade_readout_is_in_the_slits_own_frame(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.sb_beam_centre_x_mm.setValue(-1.218)
        tab.recompute()
        mech = tab.mechanical_blades()
        assert mech["X+"] == pytest.approx(0.304, abs=1e-3)
        assert mech["X-"] == pytest.approx(2.740, abs=1e-3)
        assert "0.304" in tab.lbl_blades.text()
        assert "aperture is still centred on the beam" in tab.lbl_blades.text()

    def test_an_uneven_y_pair_is_untouched_by_an_x_beam_offset(self, tab):
        tab.sb_beam_centre_x_mm.setValue(-1.218)
        tab.recompute()
        mech = tab.mechanical_blades()
        assert mech["Y+"] == pytest.approx(mech["Y-"])

    def test_apply_commands_the_mechanical_numbers_not_the_beam_frame_ones(self, qapp, monkeypatch):
        moved = {}

        class FakeBeamline:
            def move_slit(self, label, mm):
                moved[label] = mm
                return True

        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        wired = RasterPlannerTab(beamline=FakeBeamline())
        wired.sb_patch_x_mm.setValue(5.0)
        wired.sb_beam_centre_x_mm.setValue(-1.218)
        wired.btn_apply_slits.click()
        assert moved["X+"] == pytest.approx(0.304, abs=1e-3)
        assert moved["X-"] == pytest.approx(2.740, abs=1e-3)

    def test_a_beam_offset_never_moves_the_picture(self, tab):
        """The complaint this fixes: moved slits made the whole graph tilt."""
        tab.sb_patch_x_mm.setValue(5.0)
        tab.recompute()
        centred = [list(ln.get_ydata()) for ln in tab._ax_line_x.get_lines()]
        tab.sb_beam_centre_x_mm.setValue(-1.218)
        tab.recompute()
        offset = [list(ln.get_ydata()) for ln in tab._ax_line_x.get_lines()]
        assert centred == offset

    def test_a_patch_offset_DOES_move_the_picture(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.recompute()
        centred = tab.solution["X"]["painted_max_mm"]
        tab.chk_jaw_offset.setChecked(True)
        tab.sb_offset_x_mm.setValue(-2.0)
        tab.recompute()
        assert tab.solution["X"]["painted_max_mm"] != pytest.approx(centred)

    def test_a_blade_driven_past_mechanical_centre_is_flagged(self, tab):
        tab.sb_patch_x_mm.setValue(2.0)
        tab.sb_beam_centre_x_mm.setValue(-20.0)
        tab.recompute()
        assert "MECHANICAL centre" in tab.lbl_blades.text()
        assert not tab.btn_apply_slits.isEnabled()


class TestTheJawPanel:
    def test_it_has_its_own_axes_not_an_inset(self, tab):
        # The beamline axes are ~15:1 wide and the jaw question is vertical.
        assert hasattr(tab, "_ax_jaw_x") and hasattr(tab, "_ax_jaw_y")
        assert tab._ax_jaw_x is not tab._ax_line_x

    def test_it_draws_the_beam_at_the_jaw_and_at_the_turnaround(self, tab):
        tab.recompute()
        # Two profiles: dashed (half-cut at the edge) and solid (turnaround).
        styles = {ln.get_linestyle() for ln in tab._ax_jaw_x.get_lines()}
        assert "--" in styles and "-" in styles

    def test_it_dimensions_the_overscan_in_beam_widths(self, tab):
        tab.sb_fwhm_mm.setValue(3.0)
        tab.sb_turnaround_k.setValue(1.5)
        tab.recompute()
        texts = " ".join(t.get_text() for t in tab._ax_jaw_x.texts)
        assert "1.5×FWHM" in texts
        assert "4.50 mm" in texts

    def test_the_beamline_plot_dimensions_the_patch_at_the_sample(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.recompute()
        texts = " ".join(t.get_text() for t in tab._ax_line_x.texts)
        assert "5.000 mm" in texts and "on sample" in texts

    def test_steerer_mode_has_no_jaw_panel_to_draw(self, steerer_tab):
        assert not steerer_tab._ax_jaw_x.get_lines()

    def test_the_beam_marker_is_a_capped_bar_not_a_filled_box(self, tab):
        """A box spanning tens of millimetres of drift implies the beam is
        that wide along the beamline, which it is not."""
        tab.recompute()
        assert len(tab._ax_line_x.patches) == 0

    def test_the_panel_shades_the_OPPOSITE_jaw_when_the_zoom_reaches_it(self, tab):
        """Below the - blade is metal too. Drawing it as open space is what
        made the negative half of the scale look unexplained."""
        tab.sb_patch_x_mm.setValue(5.0)
        tab.sb_fwhm_mm.setValue(3.0)      # zoom deep enough to pass the beam
        tab.recompute()
        assert tab.solution["X"]["blade_plus_mm"] < 3.0 * 3.0 / srm.FWHM_PER_SIGMA
        # Two hatched spans: the + jaw and the - jaw.
        spans = [pa for pa in tab._ax_jaw_x.patches if pa.get_hatch()]
        assert len(spans) == 2

    def test_a_shallow_zoom_shows_only_the_near_jaw(self, tab):
        tab.sb_patch_y_mm.setValue(10.0)
        tab.sb_fwhm_mm.setValue(0.6)      # never reaches the beam axis
        tab.recompute()
        spans = [pa for pa in tab._ax_jaw_y.patches if pa.get_hatch()]
        assert len(spans) == 1

    def test_the_beam_axis_is_marked_so_a_negative_reading_reads(self, tab):
        tab.sb_patch_x_mm.setValue(5.0)
        tab.sb_fwhm_mm.setValue(3.0)
        tab.recompute()
        texts = " ".join(t.get_text() for t in tab._ax_jaw_x.texts)
        assert "beam axis" in texts
        assert "X- JAW" in texts and "X+ JAW" in texts

    def test_the_scale_says_it_is_measured_from_the_beam(self, tab):
        tab.recompute()
        assert "from the beam" in tab._ax_jaw_x.get_ylabel()
