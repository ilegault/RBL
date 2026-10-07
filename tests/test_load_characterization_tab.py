"""
Tests for rbl.gui.load_characterization_tab.LoadCharacterizationTab.

No hardware: a bare Beamline() with no generators connected exercises the
"not connected" paths; the run lifecycle itself is covered end-to-end by
tests/test_load_characterizer.py, so these tests focus on the widget's own
wiring (mode/condition selection, table refresh, the _lj_tabs contract).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import amplifier_assignments as aa
from rbl.config import characterization_history as ch
from rbl.gui import theme
from rbl.gui.load_characterization_tab import LoadCharacterizationTab
from rbl.state.beamline import Beamline

NOW = datetime(2026, 10, 7, 12, 0, 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    beamline = Beamline()
    return LoadCharacterizationTab(beamline, now_fn=lambda: NOW)


def measure(plate, c_pf, cond="ON_PLATES", days_ago=3, method="impedance_sweep",
            g_us=0.01):
    """Write a characterization result as a run would (no assignment on record,
    so the serial is "unassigned")."""
    ch.write_result(
        {"plate_position": plate, "amplifier_serial": "unassigned",
         "load_condition": cond, "method": method,
         "values": {"c_pf": c_pf, "g_us": g_us}},
        NOW - timedelta(days=days_ago))


def row_of(tab, plate):
    return [tab.table.item(r, 0).text() if tab.table.item(r, 0) else ""
            for r in range(tab.table.rowCount())].index(plate)


def cell(tab, plate, col):
    item = tab.table.item(row_of(tab, plate), col)
    return item.text() if item else ""


COL_DISCONNECTED, COL_CABLE, COL_PLATES, COL_CABLE_MINUS_AMP, COL_PLATES_MINUS_CABLE = 1, 2, 3, 4, 5


class TestModeAndConditionSelection:
    def test_defaults_to_mode_a_on_plates(self, tab):
        assert tab.rb_mode_a.isChecked()
        assert tab.rb_on_plates.isChecked()

    def test_mode_b_selection(self, tab):
        tab.rb_mode_b.setChecked(True)
        assert tab.rb_mode_b.isChecked()

    def test_mode_c_selection(self, tab):
        tab.rb_mode_c.setChecked(True)
        assert tab.rb_mode_c.isChecked()

    def test_disconnected_condition_selection(self, tab):
        tab.rb_disconnected.setChecked(True)
        assert tab.rb_disconnected.isChecked()


class TestRunGuards:
    def test_run_without_labjack_connection_warns_and_does_not_start(self, tab, monkeypatch):
        warned = []
        monkeypatch.setattr(
            "rbl.gui.load_characterization_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab.btn_run.click()
        assert warned

    def test_run_without_generator_connected_warns(self, tab, monkeypatch):
        tab.on_labjack_connected("T7-12345")
        warned = []
        monkeypatch.setattr(
            "rbl.gui.load_characterization_tab.QMessageBox.warning",
            lambda *a, **k: warned.append(a))
        tab.btn_run.click()
        assert warned

    def test_abort_with_no_runner_is_a_noop(self, tab):
        tab.btn_abort.click()   # must not raise


class TestComparisonTable:
    """One row per plate position, grouped by axis, newest result per condition."""

    def test_the_layout_is_grouped_by_axis(self, tab):
        assert [tab.table.horizontalHeaderItem(c).text()
                for c in range(tab.table.columnCount())] == [
            "Plate position", "Disconnected", "Cable only", "On plates",
            "Cable minus amplifier", "Plates minus cable"]
        assert [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())] == [
            "X axis", "X+", "X-", "Y axis", "Y+", "Y-"]

    def test_unmeasured_channels_show_placeholder(self, tab):
        for plate in ("X+", "X-", "Y+", "Y-"):
            for col in (COL_DISCONNECTED, COL_CABLE, COL_PLATES):
                assert cell(tab, plate, col) == "not measured"
            assert cell(tab, plate, COL_CABLE_MINUS_AMP) == ""
            assert cell(tab, plate, COL_PLATES_MINUS_CABLE) == ""

    def test_measured_channel_shows_values(self, tab):
        measure("X+", 1612.0, method="charge_integral_ladder", days_ago=32)
        tab.refresh_results()
        text = cell(tab, "X+", COL_PLATES)
        assert "1612" in text
        assert "charge_integral_ladder" in text
        assert "32 days ago" in text
        # the other conditions of the same plate, and the other plates, are untouched
        assert cell(tab, "X+", COL_CABLE) == "not measured"
        assert cell(tab, "X-", COL_PLATES) == "not measured"

    def test_the_difference_columns_locate_where_the_capacitance_lives(self, tab):
        measure("Y-", 400.0, cond="CABLE_ONLY")
        measure("Y-", 1600.0, cond="ON_PLATES")
        tab.refresh_results()
        assert cell(tab, "Y-", COL_PLATES_MINUS_CABLE) == "1200 pF"
        assert cell(tab, "Y-", COL_CABLE_MINUS_AMP) == ""        # no disconnected result
        measure("Y-", 100.0, cond="DISCONNECTED")
        tab.refresh_results()
        assert cell(tab, "Y-", COL_CABLE_MINUS_AMP) == "300 pF"

    def test_a_negative_difference_is_shown_with_its_sign(self, tab):
        measure("Y+", 500.0, cond="CABLE_ONLY")
        measure("Y+", 450.0, cond="ON_PLATES")
        tab.refresh_results()
        assert cell(tab, "Y+", COL_PLATES_MINUS_CABLE) == "-50 pF"

    def test_a_result_that_predates_a_hardware_change_is_flagged(self, tab):
        measure("X+", 1500.0, days_ago=10)       # before the change
        measure("X-", 1500.0, days_ago=1)        # after it
        aa.record_hardware_change(NOW - timedelta(days=5), "new cable")
        tab.refresh_results()
        old = tab.table.item(row_of(tab, "X+"), COL_PLATES)
        new = tab.table.item(row_of(tab, "X-"), COL_PLATES)
        assert old.background().color().name().lower() == theme.WARN.lower()
        assert old.toolTip() == "measured before the hardware change on 2026-10-02"
        assert new.background().color().alpha() == 0
        assert new.toolTip() == ""

    def test_outlier_conductance_is_flagged(self, tab):
        for label, g in (("X+", 0.01), ("X-", 0.01), ("Y+", 0.01), ("Y-", 5.0)):
            measure(label, 1200.0, g_us=g, method="impedance_sweep")
        tab.refresh_results()
        flagged = tab.table.item(row_of(tab, "Y-"), 0)
        assert flagged.background().color().alpha() > 0      # highlighted, not transparent
        quiet = tab.table.item(row_of(tab, "X+"), 0)
        assert quiet.background().color().alpha() == 0

    def test_cable_only_and_disconnected_conductance_do_not_count_as_outliers(self, tab):
        for label in ("X+", "X-", "Y+", "Y-"):
            measure(label, 1200.0, g_us=0.01)
        measure("Y-", 200.0, cond="DISCONNECTED", g_us=9.0)
        tab.refresh_results()
        assert tab.table.item(row_of(tab, "Y-"), 0).background().color().alpha() == 0

    def test_the_default_clock_is_the_real_one(self, qapp):
        # No now_fn: the tab must build and draw using the local clock.
        real = LoadCharacterizationTab(Beamline())
        assert cell(real, "X+", COL_PLATES) == "not measured"

    def test_a_new_result_appears_after_refresh(self, tab):
        assert cell(tab, "X-", COL_PLATES) == "not measured"
        measure("X-", 1234.0, days_ago=0)
        tab.refresh_results()
        assert "1234 pF" in cell(tab, "X-", COL_PLATES) and "today" in cell(tab, "X-", COL_PLATES)

    def test_a_swap_leaves_the_new_amplifiers_plate_not_measured(self, tab):
        a = {"X+": "S-A", "X-": "S-B", "Y+": "S-C", "Y-": "S-D"}
        b = {"X+": "S-B", "X-": "S-A", "Y+": "S-C", "Y-": "S-D"}
        aa.record_assignment(a, NOW - timedelta(days=20), "initial")
        ch.write_result({"plate_position": "X+", "amplifier_serial": "S-A",
                         "load_condition": "ON_PLATES", "method": "impedance_sweep",
                         "values": {"c_pf": 1600.0, "g_us": 0.0}},
                        NOW - timedelta(days=10))
        aa.record_assignment(b, NOW - timedelta(days=2), "swap")
        tab.refresh_results()
        assert cell(tab, "X+", COL_PLATES) == "not measured"
