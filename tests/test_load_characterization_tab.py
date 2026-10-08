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


class TestClampTestInTheTab:
    @staticmethod
    def fake_runner(monkeypatch, tab, result, finish=True):
        from PySide6.QtCore import QObject, Signal

        import rbl.gui.load_characterization_tab as mod
        calls = []

        class Runner(QObject):
            point_measured = Signal(dict)
            finished = Signal(str)
            error = Signal(str)
            progress = Signal(int, int, str)
            abort_rule = None
            clamp_result = result

            def __init__(self, *a, **k):
                super().__init__()

            def start_clamp_test(self, amp_label, **kw):
                calls.append(amp_label)
                if finish:
                    self.finished.emit("")

        monkeypatch.setattr(mod, "LoadCharacterizer", Runner)
        monkeypatch.setattr(mod, "RampEngine", lambda *a, **k: object())
        monkeypatch.setattr(tab.beamline, "build_funcgen_map",
                            lambda: {label: (object(), 1) for label in ("X+", "X-", "Y+", "Y-")})
        tab.on_labjack_connected("T7-1")
        return calls

    def test_selecting_clamp_test_and_running_starts_it_on_the_selected_plate(
            self, tab, monkeypatch):
        calls = self.fake_runner(monkeypatch, tab, {"reached": True, "clamp_ma": 17.5,
                                                     "clamp_freq_hz": 2000, "peak_kv": 1.0})
        tab.cb_channel.setCurrentText("Y+")
        tab.rb_mode_clamp.setChecked(True)
        tab.btn_run.click()
        assert calls == ["Y+"]
        assert "17.5 mA" in tab.lbl_status.text() and "2000" in tab.lbl_status.text()

    def test_a_clamp_that_was_not_reached_says_so(self, tab, monkeypatch):
        self.fake_runner(monkeypatch, tab, {"reached": False, "clamp_ma": None,
                                             "clamp_freq_hz": None, "peak_kv": 1.0})
        tab.rb_mode_clamp.setChecked(True)
        tab.btn_run.click()
        assert "not reached" in tab.lbl_status.text()

    def test_the_tab_says_at_least_20_ma_for_a_clamp_at_the_rail(self, tab, monkeypatch):
        self.fake_runner(monkeypatch, tab, {"reached": True, "clamp_ma": 20.0,
                                             "clamp_freq_hz": 2000, "peak_kv": 1.0,
                                             "at_rail": True})
        tab.rb_mode_clamp.setChecked(True)
        tab.btn_run.click()
        assert "at least 20 mA" in tab.lbl_status.text()

    def test_every_mode_and_condition_button_is_locked_while_a_run_is_active(
            self, tab, monkeypatch):
        self.fake_runner(monkeypatch, tab, None, finish=False)
        tab.rb_mode_clamp.setChecked(True)
        tab.btn_run.click()
        for rb in (tab.rb_mode_a, tab.rb_mode_b, tab.rb_mode_c, tab.rb_mode_clamp,
                   tab.rb_disconnected, tab.rb_on_plates, tab.rb_cable_only):
            assert not rb.isEnabled()
        assert tab.btn_abort.isEnabled() and not tab.btn_run.isEnabled()


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



# ---------------------------------------------------------------------------
# Ticket 50: the spike chart
# ---------------------------------------------------------------------------

SPIKE_COLUMNS = ["time_iso", "plate_position", "amplifier_serial", "duration_s", "peak_ma",
                 "peak_is_lower_bound", "charge_uc", "gap_to_previous_s",
                 "sample_interval_s", "reference_ma", "threshold_ma"]


def write_spike_csv(path, spikes):
    """spikes: [(plate, duration_s, peak_ma, lower_bound)]"""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SPIKE_COLUMNS, lineterminator="\n")
        w.writeheader()
        for plate, dur, peak, lb in spikes:
            w.writerow({"time_iso": "2026-10-07T12:00:00", "plate_position": plate,
                        "amplifier_serial": "S", "duration_s": dur, "peak_ma": peak,
                        "peak_is_lower_bound": lb, "charge_uc": 1.0,
                        "gap_to_previous_s": "", "sample_interval_s": 1e-4,
                        "reference_ma": 2.0, "threshold_ma": 4.0})


def spike_collections(tab):
    """{plate: scatter collection} for the markers on the spike chart."""
    return {c.get_label(): c for c in tab.spike_axes.collections
            if c.get_label() in ("X+", "X-", "Y+", "Y-")}


def marker_count(tab):
    return sum(len(c.get_offsets()) for c in spike_collections(tab).values())


def live_spike(plate="X+", duration=0.002, peak=30.0, lower_bound=False):
    return {"plate_position": plate, "duration_s": duration, "peak_ma": peak,
            "peak_is_lower_bound": lower_bound}


class TestSpikeChart:
    def test_loading_a_file_draws_a_marker_per_spike_in_a_colour_per_plate(
            self, tab, monkeypatch, tmp_path):
        path = tmp_path / "spikes.csv"
        write_spike_csv(path, [("X+", 0.001, 8.0, False), ("X+", 0.01, 25.0, False),
                               ("Y-", 0.0005, 6.0, False)])
        monkeypatch.setattr(tab, "_ask_spike_file", lambda: path)
        tab.btn_open_spikes.click()
        assert marker_count(tab) == 3
        assert tab.spike_axes.get_xscale() == "log" and tab.spike_axes.get_yscale() == "log"
        colours = {plate: tuple(c.get_edgecolor()[0])
                   for plate, c in spike_collections(tab).items()}
        assert set(colours) == {"X+", "Y-"}
        assert colours["X+"] != colours["Y-"]

    def test_a_lower_bound_peak_is_drawn_hollow(self, tab, monkeypatch, tmp_path):
        path = tmp_path / "spikes.csv"
        write_spike_csv(path, [("X+", 0.0005, 30.0, True), ("X+", 0.005, 30.0, False)])
        monkeypatch.setattr(tab, "_ask_spike_file", lambda: path)
        tab.btn_open_spikes.click()
        faces = spike_collections(tab)["X+"].get_facecolor()
        assert len(faces) == 2
        assert faces[0][3] == 0.0         # lower bound: face colour none
        assert faces[1][3] > 0.0          # resolved peak: filled

    def test_a_live_spike_adds_a_marker_without_reloading(self, tab, monkeypatch):
        asked = []
        monkeypatch.setattr(tab, "_ask_spike_file", lambda: asked.append(1))
        assert marker_count(tab) == 0
        tab.add_spike(live_spike("X+"))
        assert marker_count(tab) == 1
        tab.add_spike(live_spike("Y+", peak=50.0, lower_bound=True))
        assert marker_count(tab) == 2
        assert asked == []

    def test_a_live_row_from_the_recorder_signal_is_accepted(self, tab):
        # The recorder's signal carries the CSV row with real types.
        tab.add_spike({"time_iso": "x", "plate_position": "Y+", "amplifier_serial": "S",
                       "duration_s": 0.004, "peak_ma": 42.0, "peak_is_lower_bound": False,
                       "charge_uc": 3.0, "gap_to_previous_s": "", "sample_interval_s": 1e-4,
                       "reference_ma": 2.0, "threshold_ma": 4.0})
        assert marker_count(tab) == 1

    def test_the_rating_zones_and_lines_are_there(self, tab):
        ax = tab.spike_axes
        labels = [a.get_label() for a in (*ax.patches, *ax.collections, *ax.lines)]
        assert "over burst rating" in labels
        assert "beyond 4 ms burst" in labels
        horizontal = {tuple(ln.get_ydata()) for ln in ax.lines}
        assert (20.0, 20.0) in horizontal and (100.0, 100.0) in horizontal

    def test_the_axes_are_labelled(self, tab):
        ax = tab.spike_axes
        assert ax.get_xlabel() == "Spike duration (s)"
        assert ax.get_ylabel() == "Peak current (mA)"

    def test_cancelling_the_file_dialog_changes_nothing(self, tab, monkeypatch):
        tab.add_spike(live_spike("X+"))
        monkeypatch.setattr(tab, "_ask_spike_file", lambda: None)
        tab.btn_open_spikes.click()
        assert marker_count(tab) == 1

    def test_while_a_past_file_is_shown_live_spikes_do_not_mix_into_it(
            self, tab, monkeypatch, tmp_path):
        path = tmp_path / "spikes.csv"
        write_spike_csv(path, [("X+", 0.001, 8.0, False)])
        monkeypatch.setattr(tab, "_ask_spike_file", lambda: path)
        tab.btn_open_spikes.click()
        tab.add_spike(live_spike("Y+"))
        assert marker_count(tab) == 1                   # the file's one spike only
        tab.btn_show_live.click()                       # back to the live run
        assert marker_count(tab) == 1                   # ... which has the one live spike
        assert set(spike_collections(tab)) == {"Y+"}

    def test_a_file_with_unreadable_rows_loads_the_rest(self, tab, monkeypatch, tmp_path):
        path = tmp_path / "spikes.csv"
        write_spike_csv(path, [("X+", 0.001, 8.0, False)])
        with open(path, "a", encoding="utf-8") as f:
            f.write("t,Z+,S,notanumber,5,False,1,,1e-4,2,4\n")
        monkeypatch.setattr(tab, "_ask_spike_file", lambda: path)
        tab.btn_open_spikes.click()
        assert marker_count(tab) == 1
        assert "1 unreadable" in tab.lbl_spikes.text()

    def test_the_status_counts_the_spikes_per_plate(self, tab):
        tab.add_spike(live_spike("X+"))
        tab.add_spike(live_spike("X+"))
        tab.add_spike(live_spike("Y-"))
        text = tab.lbl_spikes.text()
        assert "X+ 2" in text and "Y- 1" in text and "X- 0" in text

    def test_the_recorders_signal_feeds_the_chart(self, tab):
        from PySide6.QtCore import QObject, Signal

        class Rec(QObject):
            spike_recorded = Signal(dict)
        rec = Rec()
        tab.set_spike_recorder(rec)
        rec.spike_recorded.emit(live_spike("X-"))
        assert marker_count(tab) == 1
