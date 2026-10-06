"""
Tests for SessionRecorder — CSV-only mode (no camera, no ffmpeg required).
"""
import csv
import json
import os
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from rbl.services.session_recorder import SessionRecorder


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_recorder(tmp_path, snapshot_fn=None, qapp_=None):
    """Build a SessionRecorder with a closed (mock) camera."""
    if snapshot_fn is None:
        def snapshot_fn():
            return {}

    camera = MagicMock()
    camera.is_open.return_value = False
    camera.actual_size.return_value = (0, 0)
    camera._thread = None
    # Make closed / error signals connectable
    from PySide6.QtCore import QObject, Signal
    class _Cam(QObject):
        closed = Signal()
        error  = Signal(str)
        frame_ready = Signal(object, float)
        format_ready = Signal(str)
        def is_open(self): return False
        def actual_size(self): return (0, 0)
        def latest_frame(self): return None
        _thread = None
    cam = _Cam()

    rec = SessionRecorder(snapshot_fn, cam)
    return rec, tmp_path


def test_csv_only_session_creates_expected_files(tmp_path, qapp):
    rec, folder = _make_recorder(tmp_path)

    # Patch logs_dir to use tmp_path
    import rbl.services.session_recorder as sr_mod
    orig = sr_mod._logs_dir
    sr_mod._logs_dir = lambda: str(tmp_path)
    try:
        assert rec.start()
        time.sleep(0.05)
        rec.stop()
    finally:
        sr_mod._logs_dir = orig

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    assert sessions, "no session folder created"
    sess_dir = os.path.join(tmp_path, sessions[0])

    assert os.path.exists(os.path.join(sess_dir, "data.csv"))
    assert os.path.exists(os.path.join(sess_dir, "events.csv"))
    assert os.path.exists(os.path.join(sess_dir, "session.json"))

    # No video files should exist.
    avi_files = [f for f in os.listdir(sess_dir) if f.endswith(".avi")]
    assert not avi_files


def test_t_rel_s_is_non_decreasing(tmp_path, qapp):
    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    rec, _ = _make_recorder(tmp_path)
    rec.set_csv_interval_s(1)
    rec.start()
    time.sleep(0.1)
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[0])
    data_path = os.path.join(sess_dir, "data.csv")

    rows = list(csv.DictReader(open(data_path, encoding="utf-8")))
    t_vals = [float(r["t_rel_s"]) for r in rows]
    for a, b in zip(t_vals, t_vals[1:]):
        assert b >= a, f"t_rel_s not monotone: {a} then {b}"


def test_wall_utc_derived_not_sampled(tmp_path, qapp):
    """wall_utc must equal t0_wall + t_rel_s, not a fresh datetime.now() call."""
    from datetime import datetime

    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    rec, _ = _make_recorder(tmp_path)
    rec.set_csv_interval_s(1)
    rec.start()
    rec._csv_timer.timeout.emit()
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    rows = list(csv.DictReader(open(os.path.join(sess_dir, "data.csv"), encoding="utf-8")))
    assert len(rows) >= 2
    t0_rel = float(rows[0]["t_rel_s"])
    t0_wall = datetime.fromisoformat(rows[0]["wall_utc"].replace("Z", "+00:00"))
    for row in rows:
        t_rel = float(row["t_rel_s"])
        expected = t0_wall + timedelta(seconds=(t_rel - t0_rel))
        actual = datetime.fromisoformat(row["wall_utc"].replace("Z", "+00:00"))
        assert abs((actual - expected).total_seconds()) < 0.002


def test_schema_roll_creates_two_parts(tmp_path, qapp):
    """If the snapshot grows new keys mid-session, data_002.csv must be created."""
    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    call_count = [0]
    def snap():
        call_count[0] += 1
        if call_count[0] <= 1:
            return {"a": 1}
        return {"a": 1, "b": 2}

    rec, _ = _make_recorder(tmp_path, snapshot_fn=snap)
    rec.set_csv_interval_s(1)
    rec.start()
    # Trigger periodic write via the recorder timer
    rec._csv_timer.timeout.emit()
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    assert os.path.exists(os.path.join(sess_dir, "data.csv"))
    assert os.path.exists(os.path.join(sess_dir, "data_002.csv"))

    part1 = list(csv.DictReader(open(os.path.join(sess_dir, "data.csv"), encoding="utf-8")))
    part2 = list(csv.DictReader(open(os.path.join(sess_dir, "data_002.csv"), encoding="utf-8")))
    assert "b" not in part1[0]
    assert "b" in part2[0]


def test_session_json_has_no_instrument_readouts(tmp_path, qapp):
    """The manifest must contain no beamline data keys."""
    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    forbidden = {"motors", "logamps", "amps", "funcgens", "scope", "vacuum"}

    def snap():
        return {k: {"connected": True} for k in forbidden}

    rec, _ = _make_recorder(tmp_path, snapshot_fn=snap)
    rec.start()
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    raw = open(os.path.join(sess_dir, "session.json"), encoding="utf-8").read()
    for key in forbidden:
        assert f'"{key}"' not in raw, f"session.json must not contain '{key}'"


def test_add_note_writes_events_csv(tmp_path, qapp):
    """add_note() must write a correctly quoted events.csv row."""
    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    rec, _ = _make_recorder(tmp_path)
    rec.start()
    rec.add_note('beam tuned, "starting" irradiation')
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    rows = list(csv.reader(open(os.path.join(sess_dir, "events.csv"), encoding="utf-8")))
    events = [r for r in rows if len(r) >= 4 and r[3] == "note"]
    assert events, "no note event found"
    detail = events[0][4]
    assert "beam tuned" in detail
    assert '"starting"' in detail


def test_flush_per_row_survives_hard_kill(tmp_path, qapp):
    """data.csv must be readable even if stop() is never called."""
    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    rec, _ = _make_recorder(tmp_path)
    rec.start()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    rows = list(csv.DictReader(open(os.path.join(sess_dir, "data.csv"), encoding="utf-8")))
    assert len(rows) >= 1, "data.csv must have at least the initial row"


@dataclass(frozen=True)
class _FakeChannel:
    label: str


@dataclass(frozen=True)
class _FakeXgsReading:
    channel: _FakeChannel
    pressure: Optional[float]
    raw: str
    state: str


@dataclass(frozen=True)
class _FakeVgcReading:
    channel: str
    pressure: Optional[float]
    raw: str
    state: str


@dataclass(frozen=True)
class _FakeVacuumState:
    timestamp: float
    xgs_readings: list = field(default_factory=list)
    vgc_readings: list = field(default_factory=list)
    xgs_connected: bool = True
    vgc_connected: bool = True
    units_xgs: str = "Torr"
    units_vgc: str = "Torr"


def _make_state(t: float, xgs_p=1e-6, vgc_p=2e-7):
    return _FakeVacuumState(
        timestamp=t,
        xgs_readings=[_FakeXgsReading(_FakeChannel("IG1"), xgs_p, f"{xgs_p:.2e}", "OK")],
        vgc_readings=[_FakeVgcReading("CG1", vgc_p, f"{vgc_p:.2e}", "OK")],
    )


def test_session_recorder_writes_vacuum_csv_and_json(tmp_path, qapp, monkeypatch):
    """Start session, deliver 5 states to on_vacuum_state, stop:
    vacuum.csv with 5 rows and vacuum.json.
    """
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    rec, _ = _make_recorder(tmp_path)
    rec.start()
    for i in range(5):
        rec.on_vacuum_state(_make_state(1000.0 + i))
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    vac_csv = os.path.join(sess_dir, "vacuum.csv")
    vac_json = os.path.join(sess_dir, "vacuum.json")
    sess_json = os.path.join(sess_dir, "session.json")

    assert os.path.exists(vac_csv)
    assert os.path.exists(vac_json)

    lines = open(vac_csv, encoding="utf-8").readlines()
    comment_lines = [line for line in lines if line.startswith("#")]
    assert len(comment_lines) >= 1
    assert comment_lines[0].startswith("# vacuum_logger RBL")

    data_lines = [line for line in lines if not line.startswith("#")]
    # First data line is CSV header row
    assert len(data_lines) == 6  # header + 5 rows
    rows = list(csv.DictReader(data_lines))
    assert len(rows) == 5

    manifest = json.loads(open(sess_json, encoding="utf-8").read())
    assert manifest.get("vacuum") == {"files": ["vacuum.csv"]}


def test_session_recorder_ignores_vacuum_states_outside_session(tmp_path, qapp, monkeypatch):
    """States delivered before start() and after stop() write nothing anywhere."""
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    rec, _ = _make_recorder(tmp_path)
    # Deliver before start
    rec.on_vacuum_state(_make_state(100.0))
    rec.start()
    rec.stop()
    # Deliver after stop
    rec.on_vacuum_state(_make_state(200.0))

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    vac_csv = os.path.join(sess_dir, "vacuum.csv")
    assert not os.path.exists(vac_csv)


def test_session_recorder_gauge_set_change_rolls_vacuum_file(tmp_path, qapp, monkeypatch):
    """A state with a different gauge set mid-session produces vacuum_2.csv,
    and session.json lists both.
    """
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    rec, _ = _make_recorder(tmp_path)
    rec.start()
    rec.on_vacuum_state(_make_state(1000.0))

    # Second state has an additional gauge
    changed_state = _FakeVacuumState(
        timestamp=1001.0,
        xgs_readings=[
            _FakeXgsReading(_FakeChannel("IG1"), 1e-6, "1e-6", "OK"),
            _FakeXgsReading(_FakeChannel("IG2"), 2e-6, "2e-6", "OK"),
        ],
        vgc_readings=[_FakeVgcReading("CG1", 2e-7, "2e-7", "OK")],
    )
    rec.on_vacuum_state(changed_state)
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    assert os.path.exists(os.path.join(sess_dir, "vacuum.csv"))
    assert os.path.exists(os.path.join(sess_dir, "vacuum_2.csv"))
    assert os.path.exists(os.path.join(sess_dir, "vacuum.json"))
    assert os.path.exists(os.path.join(sess_dir, "vacuum_2.json"))

    sess_json = os.path.join(sess_dir, "session.json")
    manifest = json.loads(open(sess_json, encoding="utf-8").read())
    assert manifest.get("vacuum") == {"files": ["vacuum.csv", "vacuum_2.csv"]}


def test_vacuum_comment_lines_header_used_by_both():
    """vacuum_comment_lines is used by both VacuumTab._build_comment_lines and the recorder."""
    from rbl.gui.vacuum_tab import VacuumTab
    from rbl.services.vacuum_logger import vacuum_comment_lines

    state = _make_state(1000.0)
    lines_fn = vacuum_comment_lines(state)
    assert len(lines_fn) >= 1
    assert lines_fn[0].startswith("vacuum_logger RBL")

    tab_lines = getattr(VacuumTab, "_build_comment_lines")(state)
    assert tab_lines == lines_fn


def test_main_window_wires_vacuum_changed_to_session_recorder(qapp, monkeypatch):
    """MainWindow connects beamline.vacuum_changed to session_recorder.on_vacuum_state
    (ADR 0004 decision 6).
    """
    from rbl.gui.app import MainWindow
    win = MainWindow()
    delivered = []
    monkeypatch.setattr(win.session_recorder, "on_vacuum_state", lambda st: delivered.append(st))
    st = _make_state(1234.0)
    win.beamline.vacuum_changed.emit(st)
    assert delivered == [st]
    win.close()
