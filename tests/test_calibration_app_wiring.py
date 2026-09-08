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

    def __init__(self, funcgen_map, load_condition, parent=None, writer=None,
                 run_id=None):
        super().__init__(parent)
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

    def start_drift(self, setpoint_kv, duration_h, ac_channels=None):
        self.started = ("drift", setpoint_kv, duration_h)

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
        assert win.calibration_tab in win._lj_tabs
        print("[OK] app constructs with the calibration tab present")


class TestRawWindowFeed:
    def test_calibration_tab_receives_window_payloads(self, win):
        class _Sink:
            def __init__(self):
                self.received = []
            def on_window(self, payload):
                self.received.append(payload)

        sink = _Sink()
        win.calibration_tab._runner = sink
        payload = {"channels": {}, "t": 1.0}
        win.beamline.raw_window_ready.emit(payload)
        assert sink.received == [payload]
        win.calibration_tab._runner = None
        print("[OK] calibration tab receives window payloads")


class TestProfileForcingAndRestore:
    def _start_a_run(self, win):
        win.beamline.active_profile = "FULL"
        win.calibration_tab.on_profile_changed("FULL")
        win.calibration_tab.cbo_load.setCurrentIndex(1)   # a real LoadCondition
        assert win.calibration_tab.cbo_load.currentData() is not None
        win.calibration_tab._on_run_clicked()

    def test_starting_a_run_forces_cal_profile(self, win):
        self._start_a_run(win)
        # Default mode is DC sweep → uses CAL_SWEEP_PROFILE ("AMP_PAIR")
        assert win.beamline.active_profile == CAL_SWEEP_PROFILE
        print("[OK] starting a run forces CAL_SWEEP_PROFILE")

    def test_ending_a_run_restores_the_prior_profile(self, win):
        self._start_a_run(win)
        assert win.beamline.active_profile == CAL_SWEEP_PROFILE

        win.calibration_tab._on_finished("fake.csv")
        assert win.beamline.active_profile == "FULL"
        print("[OK] ending a run restores the prior profile")

    def test_abort_also_restores_the_prior_profile(self, win):
        self._start_a_run(win)
        win.calibration_tab._on_abort_clicked()
        assert win.calibration_tab._runner.aborted is True
        # abort() on the fake doesn't emit finished on its own -- simulate
        # what CalibrationRunner.abort() does synchronously in production.
        win.calibration_tab._on_finished("")
        assert win.beamline.active_profile == "FULL"


class TestAmpTabDisabledDuringRun:
    def test_profile_selector_disabled_during_run_and_reenabled_after(self, win):
        assert win.amp_tab._profile_combo.isEnabled()

        win.beamline.active_profile = "FULL"
        win.calibration_tab.on_profile_changed("FULL")
        win.calibration_tab.cbo_load.setCurrentIndex(1)
        win.calibration_tab._on_run_clicked()
        assert not win.amp_tab._profile_combo.isEnabled()

        win.calibration_tab._on_finished("fake.csv")
        assert win.amp_tab._profile_combo.isEnabled()
        print("[OK] AmpTab profile selector is disabled during a run and re-enabled after")
