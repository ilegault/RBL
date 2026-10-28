"""
Acceptance tests for the ZWO Capture Process skeleton running on a fake camera.

WHY THIS EXISTS
---------------
ADR 0008 establishes that the ZWO camera is owned entirely by an external child
process (the Capture Process). The application controls it over a standard input/output
JSON contract. These tests exercise the child process from the outside, verifying
launch, protocol framing, absence of Qt, fake camera streaming, preview generation,
status reporting, settings snapping and reporting, and heartbeat lifecycle.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from rbl.hardware.capture_protocol import check_hello
from tests.capture_child import CaptureChild


@pytest.fixture
def capture_child(tmp_path: Path):
    children: list[CaptureChild] = []

    def _spawn(**kwargs) -> CaptureChild:
        child = CaptureChild(cwd=tmp_path, **kwargs)
        children.append(child)
        return child

    yield _spawn
    for c in children:
        c.kill()


def test_hello_first_and_stdout_is_only_protocol(capture_child) -> None:
    child: CaptureChild = capture_child()
    # Wait for hello
    hello = child.wait_for("hello", timeout=10.0)
    check_hello(hello)

    # First raw stdout line must decode to hello
    assert len(child.raw_stdout_lines) >= 1

    # Send open, set, preview
    rep_open = child.request({"type": "open", "camera_index": 0})
    assert rep_open["type"] == "ok"

    rep_set = child.request({"type": "set", "exposure_us": 10000})
    assert rep_set["type"] == "ok"

    rep_preview = child.request({"type": "preview", "mode": "auto", "brightness": 1.0})
    assert rep_preview["type"] == "ok"

    # Wait 3 s for status/preview traffic
    time.sleep(3.0)

    # Shut down cleanly
    rep_shut = child.request({"type": "shutdown"})
    assert rep_shut["type"] == "ok"
    child.wait_exit(timeout=10.0)

    # Every stdout line decoded without ProtocolError
    assert len(child.raw_stdout_lines) == len(child.all_messages)

    # The stderr lines contain camera opened: Fake ASI FAKE0001
    assert any("camera opened: Fake ASI FAKE0001" in line for line in child.stderr_lines)


def test_no_qt_is_loaded(capture_child) -> None:
    child: CaptureChild = capture_child(python_args=["-X", "importtime"])
    hello = child.wait_for("hello", timeout=10.0)
    check_hello(hello)

    rep_shut = child.request({"type": "shutdown"})
    assert rep_shut["type"] == "ok"
    code = child.wait_exit(timeout=10.0)
    assert code == 0

    assert not any("PySide6" in line for line in child.stderr_lines)


def test_open_status_and_preview(capture_child) -> None:
    child: CaptureChild = capture_child()
    child.wait_for("hello", timeout=10.0)

    rep = child.request({"type": "open", "camera_index": 0})
    assert rep["type"] == "ok"
    assert rep["model"] == "Fake ASI"
    assert rep["serial"] == "FAKE0001"
    assert rep["sensor_width"] == 1920
    assert rep["bayer_pattern"] == "RG"

    controls = rep["controls"]
    for k in ("Exposure", "Gain", "Offset", "BandWidth"):
        assert k in controls
        assert set(controls[k].keys()) >= {"min", "max", "default"}

    settings = rep["settings"]
    assert settings["exposure_us"] == 20000
    assert settings["gain"] == 100
    assert settings["bit_depth"] == 16
    assert settings["roi"] == {"x": 0, "y": 0, "width": 1920, "height": 1080}

    # Status arrives with every D2 field
    status = child.wait_for("status", timeout=10.0)
    expected_status_fields = {
        "acquired_fps",
        "frames_written",
        "frames_dropped_camera",
        "frames_dropped_queue",
        "recording",
        "segment",
        "bytes_per_hour",
        "disk_free_bytes",
        "sensor_temperature_c",
    }
    assert expected_status_fields.issubset(status.keys())

    # Preview arrives
    prev = child.wait_for("preview", timeout=10.0)
    assert prev["width"] <= 960
    jpeg_bytes = base64.b64decode(prev["jpeg_b64"])
    img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    h, w = img.shape[:2]
    assert w == prev["width"]
    assert h == prev["height"]

    child.request({"type": "shutdown"})
    child.wait_exit(timeout=10.0)


def test_set_snaps_and_reports_changes(capture_child) -> None:
    child: CaptureChild = capture_child()
    child.wait_for("hello", timeout=10.0)

    # set before open -> error not_open
    err = child.request({"type": "set", "exposure_us": 5000})
    assert err["type"] == "error"
    assert err["code"] == "not_open"

    child.request({"type": "open", "camera_index": 0})

    # set with roi {x: 1, y: 1, width: 101, height: 51} and exposure_us: 5000
    rep = child.request({
        "type": "set",
        "roi": {"x": 1, "y": 1, "width": 101, "height": 51},
        "exposure_us": 5000,
    })
    assert rep["type"] == "ok"
    assert rep["settings"]["roi"] == {"x": 0, "y": 0, "width": 96, "height": 50}
    assert rep["settings"]["exposure_us"] == 5000

    # Wait for events: settings_changed
    exp_evt = child.wait_for(
        "event",
        timeout=10.0,
        predicate=lambda m: (
            m.get("event") == "settings_changed"
            and "exposure_us: 20000 -> 5000" in m.get("detail", "")
        ),
    )
    assert exp_evt is not None

    roi_evt = child.wait_for(
        "event",
        timeout=10.0,
        predicate=lambda m: (
            m.get("event") == "settings_changed"
            and "roi: 0,0,1920,1080 -> 0,0,96,50" in m.get("detail", "")
        ),
    )
    assert roi_evt is not None

    # Next preview has roi width 96
    prev = child.wait_for(
        "preview",
        timeout=10.0,
        predicate=lambda m: m.get("roi", {}).get("width") == 96,
    )
    assert prev["roi"]["width"] == 96

    child.request({"type": "shutdown"})
    child.wait_exit(timeout=10.0)


def test_heartbeat_keeps_alive_and_its_loss_ends_the_process(capture_child) -> None:
    # 1. With --heartbeat-timeout-s 2, sending heartbeat every 0.5 s keeps child running for 5 s
    child1: CaptureChild = capture_child(extra_args=["--heartbeat-timeout-s", "2"])
    child1.wait_for("hello", timeout=10.0)

    start = time.monotonic()
    while time.monotonic() - start < 5.0:
        child1.send({"type": "heartbeat", "id": 0})
        time.sleep(0.5)

    assert child1.proc.poll() is None, "Child exited early while receiving heartbeats"

    # Then with stdin kept open and no more messages, exits 0 within 10 s
    code1 = child1.wait_exit(timeout=10.0)
    assert code1 == 0

    # 2. A second child whose stdin is closed exits 0 within 10 s
    child2: CaptureChild = capture_child(extra_args=["--heartbeat-timeout-s", "2"])
    child2.wait_for("hello", timeout=10.0)
    child2.close_stdin()
    code2 = child2.wait_exit(timeout=10.0)
    assert code2 == 0

    # 3. shutdown replies ok and the child exits 0 within 10 s
    child3: CaptureChild = capture_child(extra_args=["--heartbeat-timeout-s", "2"])
    child3.wait_for("hello", timeout=10.0)
    rep = child3.request({"type": "shutdown"})
    assert rep["type"] == "ok"
    code3 = child3.wait_exit(timeout=10.0)
    assert code3 == 0
