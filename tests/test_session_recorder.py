"""
Tests for SessionRecorder — CSV-only mode (no camera, no ffmpeg required).
"""
import csv
import os
import time
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtWidgets")
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
    import rbl.services.session_recorder as sr_mod
    sr_mod._logs_dir = lambda: str(tmp_path)

    rec, _ = _make_recorder(tmp_path)
    rec.start()
    # Grab internals right after start
    t0_wall = rec._t0_wall
    time.sleep(0.05)
    rec.stop()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    rows = list(csv.DictReader(open(os.path.join(sess_dir, "data.csv"), encoding="utf-8")))
    for row in rows:
        t_rel = float(row["t_rel_s"])
        expected = t0_wall + timedelta(seconds=t_rel)
        expected_str = (expected.strftime("%Y-%m-%dT%H:%M:%S.")
                        + f"{expected.microsecond // 1000:03d}Z")
        assert row["wall_utc"] == expected_str, f"row wall_utc mismatch at t_rel={t_rel}"


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
    rec.start()
    # Trigger a second CSV write manually
    rec._write_csv_row()
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
    # Write some rows directly, then abandon without stop().
    for _ in range(3):
        rec._write_csv_row()
    # Manually close the file handle to simulate process death.
    if rec._csv_writer:
        rec._csv_writer.close()
    if rec._events_file:
        rec._events_file.close()

    sessions = [d for d in os.listdir(tmp_path) if d.startswith("session_")]
    sess_dir = os.path.join(tmp_path, sessions[-1])
    rows = list(csv.DictReader(open(os.path.join(sess_dir, "data.csv"), encoding="utf-8")))
    assert len(rows) >= 1, "data.csv must have at least the initial row"
