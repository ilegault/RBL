"""
Ticket 48: sessions run the spike recorder, and the Overview tab warns without blocking.

A real MainWindow, real SessionRecorder and SpikeRecorder, a real (temp) session folder.
Only the raw stream windows and the setpoints are synthetic. Spikes are fed straight to
`SpikeRecorder.on_window`, the slot `Beamline.raw_window_ready` is wired to.
"""
import os
from datetime import datetime, timedelta

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from rbl.config import characterization_history as ch
from rbl.config import hardware_config as SC
from rbl.config.calibration_config import LoadCondition
from rbl.hardware.funcgen_safety import _AMP_GAIN
from rbl.hardware.load_model import predicted_peak_ma
from tests.payloads import window_payload

DT = 1e-4
WINDOW = 1000                       # 0.1 s
AIN_I = SC.AMP_CHANNEL_MAP["X+"]["current"]
AIN_V = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
V_PER_MA = 1.0 / SC.CURRENT_MONITOR_MA_PER_VOLT


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, qapp, monkeypatch):
    import rbl.config.paths as paths
    import rbl.services.cup_log as cup_log_mod
    import rbl.services.session_recorder as sr
    from rbl.gui.app import MainWindow

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir(exist_ok=True)
    monkeypatch.setattr(sr, "_logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(cup_log_mod, "FARADAY_CUP_DIR", tmp_path / "cup_tests")
    w = MainWindow()
    monkeypatch.setattr(w, "_ask_continue_dose", lambda totals: None)
    yield w
    if w.session_recorder.is_recording():
        w.session_recorder.stop(force=True)
    w.close()


def _drive_x_plus(win):
    win.beamline.funcgen_setpoints.update("A1", output_on=True, amp_vpp=1.0)


class Feeder:
    """Back-to-back windows on X+; levels in mA, `rail` spans in raw volts."""

    def __init__(self, win, seed=0):
        self.rec = win.spike_recorder
        self.rng = np.random.default_rng(seed)

    def feed(self, seconds, level_ma=2.0, spikes=(), rail=()):
        """spikes: (offset_s, n_samples, ma); rail: (offset_s, n_samples)."""
        total = int(round(seconds / DT))
        volts = (level_ma + 0.05 * self.rng.standard_normal(total)) * V_PER_MA
        for off, n, ma in spikes:
            i = int(round(off / DT))
            volts[i:i + n] = ma * V_PER_MA
        for off, n in rail:
            i = int(round(off / DT))
            volts[i:i + n] = 10.0
        for k in range(0, total, WINDOW):
            seg = volts[k:k + WINDOW]
            self.rec.on_window(window_payload(
                {}, waveforms={AIN_I: seg, AIN_V: np.full(seg.size, 1.5)},
                sample_period=DT))


def _session_with_reference(win):
    _drive_x_plus(win)
    assert win.session_recorder.start()
    f = Feeder(win)
    f.feed(60.0)
    return f


def test_starting_a_session_runs_the_spike_recorder(win):
    assert not win.spike_recorder.is_running()
    assert win.session_recorder.start()
    folder = win.spike_recorder.output_dir()
    assert win.spike_recorder.is_running()
    assert os.path.isfile(os.path.join(folder, "spikes.csv"))
    win.session_recorder.stop(force=True)
    assert not win.spike_recorder.is_running()


def test_a_spike_at_the_rail_warns_with_its_duration(win):
    f = _session_with_reference(win)
    f.feed(2.0, rail=[(0.5, 30)])                    # 3 ms at 10.0 V
    warnings = win.overview_tab.amp_warnings.warnings()
    assert len(warnings) == 1
    assert "at the rail" in warnings[0]
    assert "3.0 ms" in warnings[0]
    assert "X+" in warnings[0]
    assert win.overview_tab.amp_warnings.spike_count("X+") == 1
    assert win.overview_tab.amp_warnings.spike_count_text("X+").endswith("1")


def test_a_spike_below_the_rail_does_not_warn(win):
    f = _session_with_reference(win)
    f.feed(2.0, spikes=[(0.5, 10, 6.0)])
    assert win.overview_tab.amp_warnings.spike_count("X+") == 1
    assert win.overview_tab.amp_warnings.warnings() == []


def test_spikes_close_together_do_not_warn(win):
    f = _session_with_reference(win)
    f.feed(2.0, spikes=[(0.5, 10, 6.0), (0.55, 10, 6.0)])
    assert win.overview_tab.amp_warnings.spike_count("X+") == 2
    assert win.overview_tab.amp_warnings.warnings() == []


def test_a_spike_is_written_to_the_session_event_log(win):
    f = _session_with_reference(win)
    f.feed(2.0, spikes=[(0.5, 10, 6.0)])
    events = os.path.join(win.spike_recorder.output_dir(), "events.csv")
    with open(events, encoding="utf-8") as fh:
        assert any(",spike," in line and "X+" in line for line in fh)


def test_a_reference_off_the_prediction_warns(win):
    when = datetime.now() - timedelta(days=1)
    ch.write_result({
        "plate_position": "X+", "amplifier_serial": ch.UNASSIGNED,
        "load_condition": LoadCondition.ON_PLATES.value,
        "method": "impedance_sweep", "values": {"c_pf": 1000.0},
    }, when)
    win.beamline.funcgen_setpoints.update(
        "A1", output_on=True, amp_vpp=1.0, freq_hz=1000.0)
    params = win.beamline.funcgen_setpoints.get("A1")
    predicted = predicted_peak_ma(
        params.shape, params.freq_hz, 1000.0, params.amp_vpp / 2.0 * _AMP_GAIN)
    panel = win.overview_tab.amp_warnings

    win.spike_recorder.reference_captured.emit("X+", {"current_ma": predicted * 1.05})
    assert panel.warnings() == []
    win.spike_recorder.reference_captured.emit("X+", {"current_ma": predicted * 1.2})
    assert len(panel.warnings()) == 1
    assert "differs" in panel.warnings()[0]
    # X- has no measured capacitance: nothing to compare against.
    win.spike_recorder.reference_captured.emit("X-", {"current_ma": predicted * 5})
    assert len(panel.warnings()) == 1


def test_warnings_never_open_a_dialog_and_stay_until_dismissed(win, monkeypatch):
    def _fail(*a, **k):
        pytest.fail("a warning opened a dialog")

    monkeypatch.setattr(QMessageBox, "exec", _fail)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_fail))
    monkeypatch.setattr(QDialog, "exec", _fail)

    f = _session_with_reference(win)
    f.feed(2.0, rail=[(0.5, 30)])
    panel = win.overview_tab.amp_warnings
    assert len(panel.warnings()) == 1
    f.feed(2.0)                                       # more windows
    assert len(panel.warnings()) == 1
    panel.dismiss_buttons()[0].click()
    assert panel.warnings() == []
