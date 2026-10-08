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


def _make_open_camera():
    from PySide6.QtCore import QObject, Signal

    class _OpenCam(QObject):
        closed = Signal()
        error = Signal(str)
        frame_ready = Signal(object, float)
        format_ready = Signal(str)

        def is_open(self):
            return True

        def actual_size(self):
            return (4, 4)

        def latest_frame(self):
            return None

        def requested_fourcc(self):
            return "MJPG"

        _thread = None

    return _OpenCam()


def test_start_video_guards_and_refusals(tmp_path, qapp, monkeypatch):
    """start_video() before start() returns False and creates no file;
    called twice while running, the second returns False.
    """
    from tests.test_video_recorder import _patch_cv2
    writers = []
    _patch_cv2(monkeypatch, writers)
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    cam = _make_open_camera()
    rec = SessionRecorder(lambda: {}, cam)

    # 1. Before start(): returns False, creates no file
    assert rec.start_video() is False
    assert len(os.listdir(tmp_path)) == 0

    # 2. Start session
    assert rec.start() is True
    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    assert len(sessions) == 1

    # 3. Call start_video() once: True
    assert rec.start_video() is True

    # 4. Call start_video() second time while running: False
    assert rec.start_video() is False

    rec.stop()


def test_session_video_start_stop_multiple_runs_no_overwrite(tmp_path, qapp, monkeypatch):
    """Session test: start session, start_video(), offer frames, stop_video(),
    start_video(), offer frames, stop session. No segment file name repeats, the first
    run's segment file still exists, events.csv has two video_started and two
    video_stopped, and session.json video.runs has two entries.
    """
    from tests.test_video_recorder import _fake_frame, _patch_cv2
    writers = []
    _patch_cv2(monkeypatch, writers)
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    cam = _make_open_camera()
    rec = SessionRecorder(lambda: {}, cam)
    rec.set_record_fps(2)

    assert rec.start() is True
    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[0])

    # Run 1
    assert rec.start_video() is True
    for i in range(5):
        cam.frame_ready.emit(_fake_frame(), float(i))
    rec.stop_video()

    # Run 2
    assert rec.start_video() is True
    for i in range(5, 10):
        cam.frame_ready.emit(_fake_frame(), float(i))
    rec.stop()

    # Check segment files
    seg0 = os.path.join(sess_dir, "video_000.avi")
    seg1 = os.path.join(sess_dir, "video_001.avi")
    assert os.path.exists(seg0), "first run's segment file must still exist"
    assert os.path.exists(seg1), "second run's segment file must exist"

    # Check events.csv
    events_path = os.path.join(sess_dir, "events.csv")
    event_rows = list(csv.reader(open(events_path, encoding="utf-8")))
    video_started = [r for r in event_rows if len(r) >= 4 and r[3] == "video_started"]
    video_stopped = [r for r in event_rows if len(r) >= 4 and r[3] == "video_stopped"]
    assert len(video_started) == 2, f"expected 2 video_started events, got {len(video_started)}"
    assert len(video_stopped) == 2, f"expected 2 video_stopped events, got {len(video_stopped)}"

    # Check session.json manifest
    manifest_path = os.path.join(sess_dir, "session.json")
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    runs = manifest.get("video", {}).get("runs", [])
    assert len(runs) == 2, f"expected 2 runs in video.runs, got {runs}"
    assert runs[0]["first_segment"] == 0
    assert runs[0]["last_segment"] == 0
    assert isinstance(runs[0]["started_t_rel"], float)
    assert isinstance(runs[0]["stopped_t_rel"], float)

    assert runs[1]["first_segment"] == 1
    assert runs[1]["last_segment"] == 1
    assert isinstance(runs[1]["started_t_rel"], float)
    assert isinstance(runs[1]["stopped_t_rel"], float)


def test_camera_closed_ends_video_run_session_continues(tmp_path, qapp, monkeypatch):
    """The camera closing while video runs ends that video run with a video_stopped
    event whose detail is camera_closed. The session keeps running.
    """
    from tests.test_video_recorder import _fake_frame, _patch_cv2
    writers = []
    _patch_cv2(monkeypatch, writers)
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    cam = _make_open_camera()
    rec = SessionRecorder(lambda: {}, cam)

    assert rec.start() is True
    assert rec.start_video() is True
    cam.frame_ready.emit(_fake_frame(), 1.0)

    # Camera closes
    cam.closed.emit()

    assert rec.is_recording() is True, "session must continue running"

    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[0])
    events_path = os.path.join(sess_dir, "events.csv")
    event_rows = list(csv.reader(open(events_path, encoding="utf-8")))
    stopped_rows = [r for r in event_rows if len(r) >= 5 and r[3] == "video_stopped"]
    assert len(stopped_rows) == 1
    assert stopped_rows[0][4] == "camera_closed"


def test_session_start_with_video_enabled_backwards_compat(tmp_path, qapp, monkeypatch):
    """Starting a session never creates a video segment until video is explicitly started."""
    from tests.test_video_recorder import _fake_frame, _patch_cv2
    writers = []
    _patch_cv2(monkeypatch, writers)
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(tmp_path))

    cam = _make_open_camera()
    rec = SessionRecorder(lambda: {}, cam)
    assert rec.start() is True
    # Video must NOT start automatically with session start
    assert rec.state()["video_active"] is False

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[0])

    # No video segments exist before video is started
    video_files_before = [
        f for f in os.listdir(sess_dir) if f.startswith("video_") and f.endswith(".avi")
    ]
    assert len(video_files_before) == 0

    # Events log has no video_started event
    events_path = os.path.join(sess_dir, "events.csv")
    events_rows = list(csv.reader(open(events_path, encoding="utf-8")))
    assert not any(len(r) >= 4 and r[3] == "video_started" for r in events_rows)

    # Manifest reports video not enabled
    manifest = json.loads(open(os.path.join(sess_dir, "session.json"), encoding="utf-8").read())
    assert manifest.get("video", {}).get("enabled") is False
    assert manifest.get("video", {}).get("runs") == []

    # Now start video explicitly and verify segment creation
    assert rec.start_video() is True
    assert rec.state()["video_active"] is True
    for i in range(5):
        cam.frame_ready.emit(_fake_frame(), float(i))
    rec.stop_video()
    assert rec.state()["video_active"] is False

    video_files_after = [
        f for f in os.listdir(sess_dir) if f.startswith("video_") and f.endswith(".avi")
    ]
    assert len(video_files_after) == 1
    assert video_files_after[0] == "video_000.avi"

    rec.stop()


