"""
Ticket 47: the spike recorder service.

Synthetic payloads are fed through `on_window` exactly as Beamline's
`raw_window_ready` would deliver them (tests/payloads.py builds the real shape).
Output files are real, in a temp folder; the clock is faked.
"""
import csv
import inspect
from datetime import datetime

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import amplifier_assignments as aa
from rbl.config import hardware_config as SC
from rbl.services.spike_recorder import SpikeRecorder
from rbl.state.setpoints import FuncGenSetpoints
from tests.payloads import window_payload

DT = 1e-4                       # 10 kS/s
WINDOW = 1000                   # 0.1 s per window
VOLTS_PER_MA = 1.0 / SC.CURRENT_MONITOR_MA_PER_VOLT
NOW = datetime(2026, 10, 7, 12, 0, 0)
AIN_I = SC.AMP_CHANNEL_MAP["X+"]["current"]
AIN_V = SC.AMP_CHANNEL_MAP["X+"]["voltage"]
AIN_I_Y = SC.AMP_CHANNEL_MAP["Y+"]["current"]
AIN_V_Y = SC.AMP_CHANNEL_MAP["Y+"]["voltage"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setpoints(qapp):
    sp = FuncGenSetpoints()
    sp.update("A1", output_on=True, amp_vpp=1.0)      # X+ driven
    return sp


@pytest.fixture
def rec(qapp, setpoints, tmp_path):
    r = SpikeRecorder(setpoints, now_fn=lambda: NOW)
    r.start(tmp_path / "run")
    yield r
    r.stop()


class Stream:
    """Feeds back-to-back windows of a per-sample mA trace for chosen plates."""

    def __init__(self, rec, seed=0):
        self.rec = rec
        self.rng = np.random.default_rng(seed)
        self.n = 0                       # samples fed so far

    def feed(self, seconds, level_ma=2.0, spikes=(), plates=("X+",)):
        """`spikes` is [(offset_s_from_this_call, n_samples, ma)]."""
        total = int(round(seconds / DT))
        trace = level_ma + 0.05 * self.rng.standard_normal(total)
        for off, n, ma in spikes:
            i = int(round(off / DT))
            trace[i:i + n] = ma
        for k in range(0, total, WINDOW):
            seg = trace[k:k + WINDOW] * VOLTS_PER_MA
            wf = {}
            for p in plates:
                ain_i = SC.AMP_CHANNEL_MAP[p]["current"]
                ain_v = SC.AMP_CHANNEL_MAP[p]["voltage"]
                wf[ain_i] = seg
                wf[ain_v] = np.full(seg.size, 1.5)
            self.rec.on_window(window_payload(
                {}, waveforms=wf, sample_period=DT))
        self.n += total


def _rows(folder):
    p = folder / "spikes.csv"
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _files(folder):
    d = folder / "spikes"
    return sorted(d.glob("*.csv")) if d.exists() else []


def _read_wave(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_one_excursion_after_the_reference_minute_makes_one_row_and_one_file(rec, tmp_path):
    s = Stream(rec)
    s.feed(60.0)
    s.feed(10.0, spikes=[(5.0, 10, 6.0)])          # 1 ms at 6 mA, 5 s in
    rows = _rows(tmp_path / "run")
    assert len(rows) == 1
    row = rows[0]
    assert row["plate_position"] == "X+"
    assert float(row["peak_ma"]) == pytest.approx(6.0, rel=0.05)
    assert float(row["duration_s"]) == pytest.approx(1e-3, rel=0.05)
    assert float(row["reference_ma"]) == pytest.approx(2.0, rel=0.05)
    assert float(row["sample_interval_s"]) == pytest.approx(DT)
    assert row["peak_is_lower_bound"] in ("False", "0", "false")
    assert row["gap_to_previous_s"] == ""
    files = _files(tmp_path / "run")
    assert len(files) == 1
    wave = _read_wave(files[0])
    assert list(wave[0].keys()) == ["t_s", "voltage_kv", "current_ma"]
    t = [float(r["t_s"]) for r in wave]
    spike_t = 60.0 + 5.0
    assert t[0] == pytest.approx(spike_t - 2.0, abs=2 * DT)
    assert t[-1] == pytest.approx(spike_t + 1e-3 + 2.0, abs=2 * WINDOW * DT)
    assert max(float(r["current_ma"]) for r in wave) == pytest.approx(6.0, rel=0.05)
    assert float(wave[0]["voltage_kv"]) == pytest.approx(1.5)


def test_the_row_carries_a_wall_time_and_the_serial_in_force(qapp, setpoints, tmp_path):
    aa.record_assignment({"X+": "SN-1", "X-": "SN-2", "Y+": "SN-3", "Y-": "SN-4"},
                         datetime(2026, 1, 1), "initial")
    r = SpikeRecorder(setpoints, now_fn=lambda: NOW)
    r.start(tmp_path / "run")
    s = Stream(r)
    s.feed(60.0)
    s.feed(3.0, spikes=[(1.0, 10, 6.0)])
    r.stop()
    (row,) = _rows(tmp_path / "run")
    assert row["amplifier_serial"] == "SN-1"
    assert datetime.fromisoformat(row["time_iso"]) == datetime(
        2026, 10, 7, 12, 1, 1)          # start + 60 s + 1 s into the next feed


def test_no_serial_on_record_is_said_not_guessed(rec, tmp_path):
    s = Stream(rec)
    s.feed(60.0)
    s.feed(3.0, spikes=[(1.0, 10, 6.0)])
    (row,) = _rows(tmp_path / "run")
    assert row["amplifier_serial"] == "not recorded"


def test_an_excursion_in_the_first_minute_is_not_recorded(rec, tmp_path):
    s = Stream(rec)
    s.feed(30.0, spikes=[(10.0, 10, 9.0)])
    s.feed(35.0)
    assert _rows(tmp_path / "run") == []
    assert _files(tmp_path / "run") == []


def test_a_setpoint_change_restarts_the_reference_minute(rec, setpoints, tmp_path):
    s = Stream(rec)
    s.feed(61.0)
    setpoints.update("A1", freq_hz=50.0)             # new operating point
    s.feed(30.0, level_ma=3.0, spikes=[(10.0, 10, 12.0)])
    assert _rows(tmp_path / "run") == []
    s.feed(40.0, level_ma=3.0)                       # reference done at 3 mA
    s.feed(3.0, level_ma=3.0, spikes=[(1.0, 10, 12.0)])
    (row,) = _rows(tmp_path / "run")
    assert float(row["reference_ma"]) == pytest.approx(3.0, rel=0.05)


def test_each_new_reference_is_announced(rec, setpoints):
    seen = []
    rec.reference_captured.connect(lambda plate, d: seen.append((plate, d)))
    s = Stream(rec)
    s.feed(61.0)
    assert [p for p, _ in seen] == ["X+"]
    assert seen[0][1]["current_ma"] == pytest.approx(2.0, rel=0.05)
    assert seen[0][1]["threshold_ma"] == pytest.approx(4.0, rel=0.05)


def test_two_spikes_50ms_apart_share_one_file_and_report_the_gap(rec, tmp_path):
    s = Stream(rec)
    s.feed(60.0)
    # first: 1 ms from 5.000 s; second starts 50 ms after the first ENDS.
    s.feed(10.0, spikes=[(5.0, 10, 6.0), (5.0 + 1e-3 + 0.05, 10, 7.0)])
    rows = _rows(tmp_path / "run")
    assert len(rows) == 2
    assert float(rows[1]["gap_to_previous_s"]) == pytest.approx(0.05, abs=DT)
    assert len(_files(tmp_path / "run")) == 1


def test_spikes_far_apart_get_separate_files(rec, tmp_path):
    s = Stream(rec)
    s.feed(60.0)
    s.feed(20.0, spikes=[(2.0, 10, 6.0), (12.0, 10, 6.0)])
    assert len(_rows(tmp_path / "run")) == 2
    assert len(_files(tmp_path / "run")) == 2


def test_a_plate_with_its_output_off_never_produces_rows(qapp, tmp_path):
    sp = FuncGenSetpoints()                          # every output off
    r = SpikeRecorder(sp, now_fn=lambda: NOW)
    r.start(tmp_path / "run")
    s = Stream(r)
    s.feed(70.0, spikes=[(65.0, 10, 9.0)])
    r.stop()
    assert _rows(tmp_path / "run") == []
    assert _files(tmp_path / "run") == []


def test_only_driven_plates_are_watched(rec, tmp_path):
    s = Stream(rec)
    s.feed(60.0, plates=("X+", "Y+"))
    s.feed(5.0, spikes=[(1.0, 10, 9.0)], plates=("X+", "Y+"))
    assert {r["plate_position"] for r in _rows(tmp_path / "run")} == {"X+"}


def test_a_zero_amplitude_output_is_not_driven(qapp, tmp_path):
    sp = FuncGenSetpoints()
    sp.update("A1", output_on=True, amp_vpp=0.0)
    r = SpikeRecorder(sp, now_fn=lambda: NOW)
    r.start(tmp_path / "run")
    s = Stream(r)
    s.feed(65.0, spikes=[(62.0, 10, 9.0)])
    r.stop()
    assert _rows(tmp_path / "run") == []


def test_nothing_is_recorded_when_not_running(qapp, setpoints, tmp_path):
    r = SpikeRecorder(setpoints, now_fn=lambda: NOW)
    s = Stream(r)
    s.feed(70.0, spikes=[(65.0, 10, 9.0)])
    assert not r.is_running()
    assert not (tmp_path / "run").exists()


def test_spike_recorded_signal_carries_the_row(rec):
    got = []
    rec.spike_recorded.connect(got.append)
    s = Stream(rec)
    s.feed(60.0)
    s.feed(3.0, spikes=[(1.0, 10, 6.0)])
    assert len(got) == 1
    assert got[0]["plate_position"] == "X+"
    assert got[0]["peak_ma"] == pytest.approx(6.0, rel=0.05)


def test_save_pre_event_writes_the_last_seconds_of_both_monitors(rec, tmp_path):
    s = Stream(rec)
    s.feed(30.0)
    rec.save_pre_event("X+", seconds=10)
    files = [f for f in _files(tmp_path / "run") if "pre_event" in f.name]
    assert len(files) == 1
    wave = _read_wave(files[0])
    t = [float(r["t_s"]) for r in wave]
    assert t[-1] - t[0] == pytest.approx(10.0, abs=0.2)
    assert t[-1] == pytest.approx(30.0, abs=0.2)
    assert float(wave[0]["voltage_kv"]) == pytest.approx(1.5)


def test_save_pre_event_for_an_undriven_plate_writes_nothing(rec, tmp_path):
    s = Stream(rec)
    s.feed(5.0)
    rec.save_pre_event("Y-", seconds=10)
    assert _files(tmp_path / "run") == []


def test_stop_writes_a_waveform_that_was_still_waiting_for_its_after_window(rec, tmp_path):
    s = Stream(rec)
    s.feed(60.0)
    s.feed(1.5, spikes=[(1.0, 10, 6.0)])             # only 0.5 s after the spike
    assert _files(tmp_path / "run") == []
    rec.stop()
    (f,) = _files(tmp_path / "run")
    assert float(_read_wave(f)[-1]["t_s"]) == pytest.approx(61.5, abs=0.2)


def test_restarting_in_a_new_folder_starts_clean(qapp, setpoints, tmp_path):
    r = SpikeRecorder(setpoints, now_fn=lambda: NOW)
    r.start(tmp_path / "a")
    Stream(r).feed(61.0)
    r.stop()
    r.start(tmp_path / "b")
    s = Stream(r)
    s.feed(5.0, spikes=[(1.0, 10, 9.0)])             # reference not yet captured
    r.stop()
    assert _rows(tmp_path / "b") == []
    assert (tmp_path / "b" / "spikes.csv").exists()


def test_the_recorder_never_commands_anything(qapp, setpoints, tmp_path, monkeypatch):
    """Replace every method that could change hardware or a setpoint with one
    that fails the test, then run a full recording that contains spikes."""
    from rbl.hardware.funcgen_driver import DG1022Z
    from rbl.services.amp_drive import AmpDrive
    from rbl.state.beamline import Beamline

    verbs = ("update", "set", "apply", "output", "command", "enable", "disable",
             "zero", "ramp", "write", "send", "on", "off", "stop", "abort")

    def poison(cls):
        for name, fn in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_") and not name.startswith("__"):
                continue
            if name.startswith("__") or not name.startswith(verbs):
                continue
            def boom(*a, _n=name, _c=cls.__name__, **k):
                pytest.fail(f"recorder called {_c}.{_n}")
            monkeypatch.setattr(cls, name, boom)

    r = SpikeRecorder(setpoints, now_fn=lambda: NOW)
    r.start(tmp_path / "run")
    for cls in (FuncGenSetpoints, DG1022Z, AmpDrive, Beamline):
        poison(cls)
    s = Stream(r)
    s.feed(60.0)
    s.feed(10.0, spikes=[(2.0, 10, 6.0), (6.0, 10, 20.0)])
    r.on_setpoint_changed("A1", setpoints.get("A1"))
    r.save_pre_event("X+")
    r.stop()
    assert len(_rows(tmp_path / "run")) == 2
