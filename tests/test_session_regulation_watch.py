"""
Ticket 49: the regulation detector watches a running session in observe mode.

Windows go through Beamline.raw_window_ready exactly as the stream worker's do,
into a real MainWindow, so the feed, the responder and the Overview warnings
list are the production wiring.
"""
import glob
import json
import os

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import hardware_config as SC
from tests.payloads import window_payload

DT = 1e-4
N = 10_000                          # 1 s window: whole cycles at 10 Hz
FREQ = 10.0
T = np.arange(N) * DT
AIN_I = SC.AMP_CHANNEL_MAP["X+"]["current"]
AIN_V = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
COMMANDED_KV = 3.0                  # amp_vpp 6.0 -> 3 kV


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(tmp_path, qapp, monkeypatch):
    import rbl.config.paths as paths
    import rbl.services.cup_log as cup_log_mod
    import rbl.services.regulation_response as rr_module
    import rbl.services.session_recorder as sr
    from rbl.gui.app import MainWindow

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir(exist_ok=True)
    monkeypatch.setattr(sr, "_logs_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(cup_log_mod, "FARADAY_CUP_DIR", tmp_path / "cup_tests")
    monkeypatch.setattr(rr_module.trip_history, "TRIP_HISTORY_PATH",
                        tmp_path / "trip.jsonl")
    w = MainWindow()
    monkeypatch.setattr(w, "_ask_continue_dose", lambda totals: None)
    w.beamline.funcgen_setpoints.update(
        "A1", output_on=True, amp_vpp=2 * COMMANDED_KV, freq_hz=FREQ)
    yield w
    if w.session_recorder.is_recording():
        w.session_recorder.stop(force=True)
    w.close()


def _emit(win, fraction, current_ma=2.0):
    v = fraction * COMMANDED_KV * np.sin(2 * np.pi * FREQ * T) \
        / SC.VOLTAGE_MONITOR_KV_PER_VOLT
    i = np.full(N, current_ma / SC.CURRENT_MONITOR_MA_PER_VOLT)
    win.beamline.raw_window_ready.emit(window_payload(
        {}, waveforms={AIN_V: v, AIN_I: i}, sample_period=DT))


def test_a_plate_at_40_percent_of_commanded_warns_naming_the_plate(win):
    assert win.session_recorder.start()
    for _ in range(3):
        _emit(win, 0.4)
    warnings = win.overview_tab.amp_warnings.warnings()
    assert len(warnings) == 1
    assert "X+" in warnings[0]


def test_a_healthy_plate_does_not_warn(win):
    assert win.session_recorder.start()
    for _ in range(4):
        _emit(win, 1.0)
    assert win.overview_tab.amp_warnings.warnings() == []


def test_nothing_is_watched_outside_a_session(win):
    for _ in range(4):
        _emit(win, 0.4)
    assert win.overview_tab.amp_warnings.warnings() == []


def test_an_undriven_plate_is_not_judged(win):
    win.beamline.funcgen_setpoints.update("A1", output_on=False)
    assert win.session_recorder.start()
    for _ in range(4):
        _emit(win, 0.0)
    assert win.overview_tab.amp_warnings.warnings() == []


def test_amp_off_in_a_session_writes_trip_record_and_pre_event_file(win, tmp_path):
    assert win.session_recorder.start()
    folder = win.spike_recorder.output_dir()
    for _ in range(3):
        _emit(win, 0.4)
    files = glob.glob(os.path.join(folder, "spikes", "X+_pre_event_*.csv"))
    assert len(files) == 1
    with open(tmp_path / "trip.jsonl", encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    assert len(records) == 1
    assert records[0]["state"] == "amp_off"
    assert records[0]["mode"] == "observe"
    assert records[0]["frequency_hz"] == FREQ
