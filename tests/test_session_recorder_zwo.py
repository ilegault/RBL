"""
test_session_recorder_zwo.py
Tests for SessionRecorder driving the ZWO camera (Seam B).
"""
from __future__ import annotations

import csv
import json
import os
import signal
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from rbl.config import paths
from rbl.hardware.camera_source import CameraSource
from rbl.services.capture_client import CaptureClient
from rbl.services.session_recorder import SessionRecorder
from tests.qt_wait import wait_until


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def capture_client(qapp):
    c = CaptureClient()
    yield c
    c.shutdown()


def test_zwo_video_in_a_session(tmp_path, monkeypatch, qapp, capture_client):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "zwo_darks")

    cam = CameraSource()
    capture_client.start("fake")
    rec = SessionRecorder(lambda: {"beamline_v": 1.0}, cam, capture=capture_client)

    rec.set_camera_kind("zwo")
    assert rec.camera_kind() == "zwo"

    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    rec.set_zwo_settings(
        roi={"x": 0, "y": 0, "width": 64, "height": 32},
        exposure_us=10000,
    )
    assert wait_until(lambda: rec.state()["zwo_settings"].get("roi", {}).get("width") == 64)
    assert rec.state()["zwo_settings"]["roi"]["width"] == 64

    assert rec.start()
    session_folder = Path(rec.state()["folder"])

    # First video run
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])
    assert wait_until(lambda: False, 2.0) or True  # wait 2 s spinning processEvents
    time.sleep(2.0)
    qapp.processEvents()

    rec.stop_video()
    assert wait_until(lambda: not rec.state()["zwo_recording"])

    # Second video run in same session
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])
    time.sleep(1.0)
    qapp.processEvents()
    rec.stop_video()
    assert wait_until(lambda: not rec.state()["zwo_recording"])

    rec.stop()

    zwo_dir = session_folder / "zwo"
    assert (zwo_dir / "segment_000.ser").is_file()
    assert (zwo_dir / "segment_001.ser").is_file()
    assert (zwo_dir / "frames.csv").is_file()

    events_path = session_folder / "events.csv"
    assert events_path.is_file()
    with open(events_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        events = [row["event"] for row in reader]

    assert "video_started" in events
    assert "zwo_recording_started" in events
    assert "zwo_segment_opened" in events
    assert "zwo_recording_stopped" in events
    assert "video_stopped" in events

    manifest_path = session_folder / "session.json"
    assert manifest_path.is_file()
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "zwo" in manifest
    assert manifest["zwo"]["camera"]["serial"] == "FAKE0001"
    assert "segment_000.ser" in manifest["zwo"]["segments"]
    assert "segment_001.ser" in manifest["zwo"]["segments"]
    assert rec.state()["zwo_settings"]["roi"]["width"] == 64


def test_zwo_photo_writes_fits_and_sidecar(tmp_path, monkeypatch, qapp, capture_client):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "zwo_darks")

    cam = CameraSource()
    capture_client.start("fake")
    rec = SessionRecorder(lambda: {"beamline_v": 2.5}, cam, capture=capture_client)

    rec.set_camera_kind("zwo")
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    photos_taken: list[str] = []
    rec.photo_taken.connect(photos_taken.append)

    assert rec.start()
    session_folder = Path(rec.state()["folder"])

    assert rec.take_photo() is None
    assert wait_until(lambda: len(photos_taken) > 0)

    rec.stop()

    fits_file = session_folder / "photos" / "zwo_photo_000_0.fits"
    sidecar_file = session_folder / "photos" / "zwo_photo_000_0.json"
    assert fits_file.is_file()
    assert sidecar_file.is_file()

    with open(sidecar_file, "r", encoding="utf-8") as f:
        sidecar = json.load(f)
    assert sidecar.get("camera") == "zwo"
    assert "beamline" in sidecar
    assert sidecar["beamline"].get("beamline_v") == 2.5

    events_path = session_folder / "events.csv"
    with open(events_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        photo_events = [row for row in reader if row["event"] == "photo"]
    assert len(photo_events) == 1
    assert photos_taken[0] == str(fits_file.resolve())


def test_capture_death_keeps_csv(tmp_path, monkeypatch, qapp, capture_client):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "zwo_darks")

    cam = CameraSource()
    pids: list[int] = []
    capture_client.hello.connect(lambda msg: pids.append(msg["pid"]))
    capture_client.start("fake")
    assert wait_until(lambda: len(pids) > 0)
    child_pid = pids[0]

    rec = SessionRecorder(lambda: {"beamline_v": 3.0}, cam, capture=capture_client)
    rec.set_csv_interval_s(1)
    rec.set_camera_kind("zwo")
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    statuses: list[tuple[str, str]] = []
    rec.status.connect(lambda msg, role: statuses.append((msg, role)))

    assert rec.start()
    session_folder = Path(rec.state()["folder"])
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])

    # Kill child from outside
    os.kill(child_pid, signal.SIGTERM)

    assert wait_until(lambda: not rec.state()["zwo_recording"])
    assert not rec.state()["zwo_recording"]

    # CSV continues growing over next 3 seconds
    initial_rows = rec.state()["csv_rows"]
    end_time = time.monotonic() + 3.0
    while time.monotonic() < end_time:
        qapp.processEvents()
        time.sleep(0.05)
    final_rows = rec.state()["csv_rows"]
    assert final_rows > initial_rows

    rec.stop()

    events_path = session_folder / "events.csv"
    with open(events_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        events = [row["event"] for row in reader]
    assert "zwo_capture_lost" in events

    warn_msgs = [msg for msg, role in statuses if role == "warn"]
    assert any("ZWO camera lost — continuing CSV only" in msg for msg in warn_msgs)


def test_switching_kind_is_refused_while_video_runs(tmp_path, monkeypatch, qapp, capture_client):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "zwo_darks")

    cam = CameraSource()
    capture_client.start("fake")
    rec = SessionRecorder(lambda: {}, cam, capture=capture_client)

    rec.set_camera_kind("zwo")
    rec.connect_zwo()
    assert wait_until(lambda: rec.state()["zwo_open"])

    assert rec.start()
    assert rec.start_video()
    assert wait_until(lambda: rec.state()["zwo_recording"])

    statuses: list[tuple[str, str]] = []
    rec.status.connect(lambda msg, role: statuses.append((msg, role)))

    rec.set_camera_kind("usb")
    assert rec.camera_kind() == "zwo"
    assert any(
        msg == "Stop video before switching cameras" and role == "warn"
        for msg, role in statuses
    )

    rec.stop_video()
    rec.stop()

    # With no capture, set_camera_kind("zwo") leaves "usb"
    rec_no_cap = SessionRecorder(lambda: {}, cam, capture=None)
    assert rec_no_cap.camera_kind() == "usb"
    rec_no_cap.set_camera_kind("zwo")
    assert rec_no_cap.camera_kind() == "usb"


def test_settings_persist_across_recorders(tmp_path, monkeypatch, qapp, capture_client):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "zwo_darks")

    cam = CameraSource()
    capture_client.start("fake")
    rec1 = SessionRecorder(lambda: {}, cam, capture=capture_client)
    rec1.set_camera_kind("zwo")
    rec1.connect_zwo()
    assert wait_until(lambda: rec1.state()["zwo_open"])

    rec1.set_zwo_settings(gain=150)
    assert wait_until(lambda: rec1.state()["zwo_settings"].get("gain") == 150)
    rec1.disconnect_zwo()

    # Second recorder + new client
    client2 = CaptureClient()
    try:
        client2.start("fake")
        rec2 = SessionRecorder(lambda: {}, cam, capture=client2)
        rec2.set_camera_kind("zwo")
        rec2.connect_zwo()
        assert wait_until(lambda: rec2.state()["zwo_open"])
        assert wait_until(lambda: rec2.state()["zwo_settings"].get("gain") == 150)
        assert rec2.state()["zwo_settings"]["gain"] == 150
    finally:
        client2.shutdown()


def test_usb_only_session_has_no_zwo_block(tmp_path, monkeypatch, qapp):
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("rbl.services.session_recorder._logs_dir", lambda: str(logs_dir))
    monkeypatch.setattr(paths, "ZWO_DARKS_DIR", tmp_path / "zwo_darks")

    cam = CameraSource()
    rec = SessionRecorder(lambda: {}, cam, capture=None)
    assert rec.state()["camera_kind"] == "usb"

    assert rec.start()
    session_folder = Path(rec.state()["folder"])
    rec.stop()

    manifest_path = session_folder / "session.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "zwo" not in manifest
    assert rec.state()["camera_kind"] == "usb"
