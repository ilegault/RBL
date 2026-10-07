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
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

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


class TestEndedLadderIsReported:
    def test_the_status_names_the_rule_that_stopped_the_run(self, tab, monkeypatch):
        from PySide6.QtCore import QObject, Signal

        import rbl.gui.load_characterization_tab as mod

        class StoppedRunner(QObject):
            point_measured = Signal(dict)
            finished = Signal(str)
            error = Signal(str)
            progress = Signal(int, int, str)
            abort_rule = "leakage"

            def __init__(self, *a, **k):
                super().__init__()

            def start_mode_c(self, *a, **k):
                self.finished.emit("")

        monkeypatch.setattr(mod, "LoadCharacterizer", StoppedRunner)
        monkeypatch.setattr(mod, "RampEngine", lambda *a, **k: object())
        monkeypatch.setattr(tab.beamline, "build_funcgen_map",
                            lambda: {label: (object(), 1) for label in ("X+", "X-", "Y+", "Y-")})
        tab.on_labjack_connected("T7-1")
        tab.rb_mode_c.setChecked(True)
        tab.btn_run.click()
        assert "leakage" in tab.lbl_status.text()


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


SERIALS_A = {"X+": "S-A", "X-": "S-B", "Y+": "S-C", "Y-": "S-D"}
SERIALS_B = {"X+": "S-C", "X-": "S-B", "Y+": "S-A", "Y-": "S-D"}   # X and Y exchanged


def _records():
    return aa.history()


def _rows(tab):
    return {plate: lbl.text() for plate, lbl in tab.lbl_serials.items()}


@pytest.fixture
def no_modal_dialogs(monkeypatch):
    """Any QDialog.exec fails the test: the panel must go through its two
    replaceable ask methods, never open a modal of its own."""
    def boom(*a, **k):
        pytest.fail("a modal dialog was opened")
    monkeypatch.setattr(QDialog, "exec", boom)


def fake_ask(monkeypatch, tab, answers):
    """Replace the tab's assignment dialog with a queue of canned answers."""
    queue = list(answers)
    seen = []

    def ask(current):
        seen.append(current)
        return queue.pop(0)
    monkeypatch.setattr(tab, "_ask_assignment", ask)
    return seen


class TestAmplifiersPanel:
    """Recording which amplifier drives which plate, and hardware changes."""

    def test_with_no_assignment_every_row_reads_not_set(self, tab, no_modal_dialogs):
        assert _rows(tab) == {"X+": "not set", "X-": "not set", "Y+": "not set", "Y-": "not set"}

    def test_the_first_recording_is_the_initial_assignment(
            self, tab, monkeypatch, no_modal_dialogs):
        seen = fake_ask(monkeypatch, tab, [(SERIALS_A, NOW - timedelta(days=1), "fitted")])
        tab.btn_record_swap.click()
        assert seen == [None]                              # nothing was in force yet
        assert _rows(tab) == SERIALS_A
        (rec,) = _records()
        assert rec["kind"] == "initial"
        assert rec["mapping"] == SERIALS_A
        assert rec["note"] == "fitted"

    def test_a_second_recording_is_a_swap_and_updates_the_rows(
            self, tab, monkeypatch, no_modal_dialogs):
        seen = fake_ask(monkeypatch, tab, [
            (SERIALS_A, NOW - timedelta(days=5), ""),
            (SERIALS_B, NOW - timedelta(days=1), "X and Y exchanged")])
        tab.btn_record_swap.click()
        tab.btn_record_swap.click()
        assert seen[1] == SERIALS_A                        # the dialog starts from what is in force
        assert [r["kind"] for r in _records()] == ["initial", "swap"]
        assert _rows(tab) == SERIALS_B

    def test_cancelling_writes_nothing(self, tab, monkeypatch, no_modal_dialogs):
        fake_ask(monkeypatch, tab, [None])
        tab.btn_record_swap.click()
        assert _records() == []
        assert _rows(tab)["X+"] == "not set"

    def test_a_swap_moves_results_with_their_amplifier(
            self, tab, monkeypatch, no_modal_dialogs):
        aa.record_assignment(SERIALS_A, NOW - timedelta(days=20), "initial")
        ch.write_result({"plate_position": "X+", "amplifier_serial": "S-A",
                         "load_condition": "ON_PLATES", "method": "impedance_sweep",
                         "values": {"c_pf": 1600.0, "g_us": 0.0}}, NOW - timedelta(days=10))
        tab.refresh_results()
        assert "1600 pF" in cell(tab, "X+", COL_PLATES)
        fake_ask(monkeypatch, tab, [(SERIALS_B, NOW - timedelta(days=1), "")])
        tab.btn_record_swap.click()
        assert cell(tab, "X+", COL_PLATES) == "not measured"      # new amplifier, no result yet

    def test_a_swap_tells_the_planner_to_re_read(self, tab, monkeypatch, no_modal_dialogs):
        fake_ask(monkeypatch, tab, [(SERIALS_A, NOW, "")])
        changed = []
        tab.measurements_changed.connect(lambda: changed.append(1))
        tab.btn_record_swap.click()
        assert changed == [1]

    def test_a_refused_mapping_writes_nothing_and_says_so(
            self, tab, monkeypatch, no_modal_dialogs):
        fake_ask(monkeypatch, tab, [({"X+": "S-A", "X-": "S-B", "Y+": "S-C"}, NOW, "")])
        warned = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
        tab.btn_record_swap.click()
        assert warned and _records() == []

    def test_recording_a_hardware_change_flags_older_results_without_a_rebuild(
            self, tab, monkeypatch, no_modal_dialogs):
        measure("X+", 1500.0, days_ago=10)
        tab.refresh_results()
        before = tab.table.item(row_of(tab, "X+"), COL_PLATES)
        assert before.background().color().alpha() == 0
        monkeypatch.setattr(tab, "_ask_hardware_change",
                            lambda: (NOW - timedelta(days=2), "feedthrough replaced"))
        tab.btn_record_hw_change.click()
        (rec,) = _records()
        assert rec["kind"] == "hardware_change" and rec["note"] == "feedthrough replaced"
        after = tab.table.item(row_of(tab, "X+"), COL_PLATES)
        assert after.background().color().name().lower() == theme.WARN.lower()

    def test_cancelling_a_hardware_change_writes_nothing(
            self, tab, monkeypatch, no_modal_dialogs):
        monkeypatch.setattr(tab, "_ask_hardware_change", lambda: None)
        tab.btn_record_hw_change.click()
        assert _records() == []


class TestTheDialogsThemselves:
    """The real dialogs are built and read, never exec'd."""

    def test_the_assignment_dialog_needs_four_distinct_serials(self, qapp):
        from rbl.gui.load_characterization_tab import AssignmentDialog
        dlg = AssignmentDialog(None, NOW)
        ok = dlg.buttons.button(dlg.buttons.StandardButton.Ok)
        assert not ok.isEnabled()
        for plate, serial in SERIALS_A.items():
            dlg.serial_edits[plate].setText(serial)
        assert ok.isEnabled()
        dlg.serial_edits["Y-"].setText("S-A")                 # a duplicate
        assert not ok.isEnabled()
        dlg.serial_edits["Y-"].setText("S-D")
        dlg.note_edit.setText("fitted")
        mapping, when, note = dlg.answer()
        assert mapping == SERIALS_A and note == "fitted"
        assert abs((when - NOW).total_seconds()) < 60        # defaults to now

    def test_the_assignment_dialog_starts_from_the_assignment_in_force(self, qapp):
        from rbl.gui.load_characterization_tab import AssignmentDialog
        dlg = AssignmentDialog(SERIALS_A, NOW)
        assert {p: e.text() for p, e in dlg.serial_edits.items()} == SERIALS_A

    def test_the_hardware_change_dialog_needs_a_note(self, qapp):
        from rbl.gui.load_characterization_tab import HardwareChangeDialog
        dlg = HardwareChangeDialog(NOW)
        ok = dlg.buttons.button(dlg.buttons.StandardButton.Ok)
        assert not ok.isEnabled()
        dlg.note_edit.setText("new HV cable")
        assert ok.isEnabled()
        when, note = dlg.answer()
        assert note == "new HV cable"
        assert abs((when - NOW).total_seconds()) < 60
