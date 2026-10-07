"""
Tests for the Calibration tab's wiring into MainWindow (rbl/gui/app.py):
tab registration, the shared LabJack window feed, stream-profile forcing at
run start / restore at run end, and AmpTab's profile selector being disabled
for the duration of a run.

Headless (offscreen Qt). Hardware calls are avoided by faking out
CalibrationRunner and CalibrationWriter — this file tests the WIRING
MainWindow makes, not CalibrationRunner's own sweep behaviour (covered by
tests/test_calibration_runner.py).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import rbl.gui.calibration_tab as calibration_tab_module
from rbl.config.calibration_config import CAL_SWEEP_PROFILE


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _no_modal_message_boxes(monkeypatch):
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture(autouse=True)
def _accept_checklist_dialog(monkeypatch):
    """The pre-run checklist is modal; auto-accept it in every test here."""
    monkeypatch.setattr(
        calibration_tab_module._PreRunChecklistDialog, "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )


class FakeWriter:
    """No filesystem side effects — real CalibrationWriter is covered
    elsewhere (tests/test_calibration_writer.py)."""
    def __init__(self, *a, **k):
        self.csv_path = "FAKE.csv"
        self.meta_path = "FAKE.json"

    def write_row(self, row):
        pass

    def update_metadata(self, **fields):
        pass

    def close(self):
        return str(self.csv_path)


class FakeRunner(QObject):
    """Stands in for CalibrationRunner: same public signal/slot surface,
    but start_* methods do nothing until the test drives them — letting a
    test observe the profile-forced state before finishing."""

    progress = Signal(int, int, str)
    row_recorded = Signal(dict)
    finished = Signal(str)
    error = Signal(str)
    pair_change_requested = Signal(str)
    overcurrent = Signal(str, float, float)
    dwell_started = Signal(float)

    last_instance = None

    def __init__(self, funcgen_map, load_condition, parent=None, writer=None,
                 run_id=None):
        super().__init__(parent)
        FakeRunner.last_instance = self
        self.funcgen_map = funcgen_map
        self.load_condition = load_condition
        self.writer = writer
        self.operator_note = ""
        self._zero_dwell_s = 0.0
        self._dwell_output_off = False
        self._use_pair_profile = True
        self.started = None
        self.aborted = False

    def start_sweep(self, channels=None, return_to_zero=False):
        self.started = "sweep"

    def start_ac_sweep(self, channels=None, freq_hz=None, return_to_zero=False):
        self.started = "ac_sweep"

    def start_drift(self, setpoint_kv, duration_h, ac_channels=None,
                    protections_ok=None):
        self.started = ("drift", setpoint_kv, duration_h)
        self.protections_ok = protections_ok

    def abort(self):
        self.aborted = True

    def on_window(self, payload):
        pass


@pytest.fixture
def win(qapp, monkeypatch):
    monkeypatch.setattr(calibration_tab_module, "CalibrationRunner", FakeRunner)
    monkeypatch.setattr(calibration_tab_module, "CalibrationWriter", FakeWriter)
    from rbl.gui.app import MainWindow
    w = MainWindow()
    yield w
    w.close()


class TestAppConstruction:
    def test_calibration_tab_present(self, win):
        assert hasattr(win, "calibration_tab")
        assert any(decl.title == "HV Calibration" and getattr(win, decl.attr_name) is not None
                   for decl in win.TAB_DECLARATIONS)


class TestRawWindowFeed:
    def test_calibration_tab_receives_window_payloads(self, win):
        class _Sink:
            def __init__(self):
                self.received = []
            def on_window(self, payload):
                self.received.append(payload)

        sink = _Sink()
        win.calibration_tab.on_window = sink.on_window
        payload = {"channels": {}, "t": 1.0}
        win.beamline.raw_window_ready.emit(payload)
        assert sink.received == [payload]


class TestProfileForcingAndRestore:
    def _start_a_run(self, win):
        win.beamline.active_profile = "FULL"
        win.calibration_tab.on_labjack_connected("T7-12345")
        win.calibration_tab.on_profile_changed("FULL")
        win.calibration_tab.cbo_load.setCurrentIndex(1)   # a real LoadCondition
        assert win.calibration_tab.cbo_load.currentData() is not None
        win.calibration_tab.btn_run.click()

    def test_starting_a_run_forces_cal_profile(self, win):
        self._start_a_run(win)
        # Default mode is DC sweep → uses CAL_SWEEP_PROFILE ("AMP_PAIR")
        assert win.beamline.active_profile == CAL_SWEEP_PROFILE

    def test_ending_a_run_restores_the_prior_profile(self, win):
        self._start_a_run(win)
        assert win.beamline.active_profile == CAL_SWEEP_PROFILE

        runner = FakeRunner.last_instance
        assert runner is not None
        runner.finished.emit("fake.csv")
        assert win.beamline.active_profile == "FULL"

    def test_abort_also_restores_the_prior_profile(self, win):
        self._start_a_run(win)
        win.calibration_tab.btn_abort.click()
        runner = FakeRunner.last_instance
        assert runner is not None
        assert runner.aborted is True
        runner.finished.emit("")
        assert win.beamline.active_profile == "FULL"





class FakeSpikeRecorder:
    """Stands in for SpikeRecorder: records how it was started and stopped."""
    def __init__(self):
        self.running = False
        self.started_with = None
        self.stops = 0

    def start(self, folder, driven_plates=None):
        self.running = True
        self.started_with = (folder, set(driven_plates or ()))

    def stop(self):
        self.running = False
        self.stops += 1

    def is_running(self):
        return self.running


class TestDriftPassOnThePlates:
    """The tab supplies the protections and starts the spike recorder."""

    @staticmethod
    def launch(win, interlock="ok", hours=24.0):
        from rbl.config.calibration_config import LoadCondition
        tab = win.calibration_tab
        recorder = FakeSpikeRecorder()
        tab.set_spike_recorder(recorder)
        win.beamline.hv_interlock_status_for = lambda kv: (interlock, f"{kv:.1f} kV: {interlock}")
        tab.on_labjack_connected("T7-1")
        tab.rb_drift.setChecked(True)
        idx = tab.cbo_load.findData(LoadCondition.ON_PLATES)
        tab.cbo_load.setCurrentIndex(idx)
        tab.spn_drift_h.setValue(hours)
        tab.btn_run.click()
        return tab, recorder

    def test_a_24_hour_pass_on_the_plates_starts_with_the_recorder_running(self, win):
        tab, recorder = self.launch(win, "ok", hours=24.0)
        runner = FakeRunner.last_instance
        assert runner.started == ("drift", tab.spn_drift_kv.value(), 24.0)
        assert recorder.running
        folder, driven = recorder.started_with
        assert driven == {"X+", "X-", "Y+", "Y-"}
        assert str(folder).endswith("_spikes")

    def test_the_runner_is_given_a_protections_check_that_reads_both_protections(self, win):
        tab, recorder = self.launch(win, "ok")
        check = FakeRunner.last_instance.protections_ok
        assert check() == (True, "")
        recorder.running = False
        ok, reason = check()
        assert not ok and "spike recorder not running" in reason
        recorder.running = True
        win.beamline.hv_interlock_status_for = lambda kv: ("block", "pressure unknown")
        ok, reason = check()
        assert not ok and "HV interlock not permitting" in reason

    def test_a_blocked_interlock_refuses_the_pass_and_stops_the_recorder(self, win):
        FakeRunner.last_instance = None
        tab, recorder = self.launch(win, "block")
        assert FakeRunner.last_instance is None or FakeRunner.last_instance.started is None
        assert not recorder.running and recorder.stops == 1

    def test_no_recorder_at_all_refuses_the_pass(self, win):
        from rbl.config.calibration_config import LoadCondition
        FakeRunner.last_instance = None
        tab = win.calibration_tab
        tab.set_spike_recorder(None)
        win.beamline.hv_interlock_status_for = lambda kv: ("ok", "")
        tab.on_labjack_connected("T7-1")
        tab.rb_drift.setChecked(True)
        tab.cbo_load.setCurrentIndex(tab.cbo_load.findData(LoadCondition.ON_PLATES))
        tab.btn_run.click()
        assert FakeRunner.last_instance is None

    def test_the_recorder_stops_when_the_pass_finishes(self, win):
        tab, recorder = self.launch(win, "ok")
        FakeRunner.last_instance.finished.emit("FAKE.csv")
        assert not recorder.running and recorder.stops == 1

    def test_the_reason_a_pass_ended_stays_on_screen(self, win):
        tab, _recorder = self.launch(win, "ok")
        runner = FakeRunner.last_instance
        runner.finished.emit("FAKE.csv")
        runner.error.emit("Drift pass ended: spike recorder not running. Outputs zeroed.")
        assert "spike recorder not running" in tab.lbl_state.text()

    def test_the_commanded_voltage_is_the_highest_any_channel_will_reach(self, win):
        tab = win.calibration_tab
        tab.spn_drift_kv.setValue(2.0)
        assert tab.drift_commanded_kv() == 2.0
        tab.chk_drift_ac.setChecked(True)
        for amp, cb in tab.drift_ac_checks().items():
            cb.setChecked(amp == "Y+")
        tab.drift_ac_amp_spins()["Y+"].setValue(3.5)
        # Y+ is driven AC to 3.5 kV; the other three stay DC at the 2.0 kV setpoint.
        assert tab.drift_commanded_kv() == 3.5


class TestSpikeChartWiring:
    def test_a_spike_the_recorder_logs_appears_on_the_load_characterization_chart(self, win):
        tab = win.load_char_tab
        before = sum(len(c.get_offsets()) for c in tab.spike_axes.collections
                     if c.get_label() in ("X+", "X-", "Y+", "Y-"))
        win.spike_recorder.spike_recorded.emit(
            {"plate_position": "Y-", "duration_s": 0.003, "peak_ma": 33.0,
             "peak_is_lower_bound": False})
        after = sum(len(c.get_offsets()) for c in tab.spike_axes.collections
                    if c.get_label() in ("X+", "X-", "Y+", "Y-"))
        assert after == before + 1
