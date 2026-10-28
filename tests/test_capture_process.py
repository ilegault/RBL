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
import json
import struct
import time
from pathlib import Path
from typing import Any

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


# --- Recording (ticket 66) ---
# Every frame written is compared pixel for pixel with the formula FakeCamera uses;
# SER is parsed here with struct, never with rbl.hardware.ser_format.

W, H = 64, 32
ROI = {"x": 0, "y": 0, "width": W, "height": H}


@pytest.fixture
def child(tmp_path: Path):
    c = CaptureChild(cwd=tmp_path)
    c.wait_for("hello", timeout=10.0)
    yield c
    c.kill()


def _open(c: CaptureChild) -> None:
    assert c.request({"type": "open", "camera_index": 0})["type"] == "ok"
    rep = c.request({"type": "set", "roi": ROI, "exposure_us": 10000})
    assert rep["type"] == "ok"
    assert rep["settings"]["roi"] == ROI


def _start(c: CaptureChild, folder: Path, seg: int = 0, frame: int = 0) -> dict[str, Any]:
    return c.request({
        "type": "start_recording",
        "folder": str(folder),
        "session_t0_utc": "2026-01-01T00:00:00Z",
        "first_segment_index": seg,
        "first_frame_index": frame,
    })


def _expected(k: int) -> np.ndarray:
    x = np.arange(W, dtype=np.int64)
    y = np.arange(H, dtype=np.int64).reshape(-1, 1)
    return (((k * 7 + x + 3 * y) % 4096) << 4).astype(np.uint16)


def _k_of(frame: np.ndarray) -> int:
    pixel = int(frame[0, 0]) >> 4
    for k in range(4096):  # 7 is odd, so k -> (k*7) % 4096 is invertible
        if (k * 7) % 4096 == pixel:
            return k
    raise AssertionError("no k")


def read_ser(path: Path) -> tuple[dict[str, Any], list[np.ndarray], list[int]]:
    data = path.read_bytes()
    (file_id, _lu, color, little, width, height, depth, count) = struct.unpack_from(
        "<14s7i", data, 0
    )
    assert file_id == b"LUCAM-RECORDER"
    assert depth == 16
    size = width * height * 2
    frames = [
        np.frombuffer(data, dtype="<u2", count=width * height, offset=178 + i * size)
        .reshape(height, width)
        for i in range(count)
    ]
    trailer_at = 178 + count * size
    assert len(data) == trailer_at + 8 * count
    stamps = list(struct.unpack_from(f"<{count}q", data, trailer_at))
    return {"count": count, "color": color, "little": little}, frames, stamps


def read_log(path: Path) -> tuple[list[str], list[list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    comments = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#")]
    assert body[0] == (
        "frame_index,wall_utc,t_rel_s,segment,segment_frame,cup_position,"
        "alumina_position,photo"
    )
    return comments, [ln.split(",") for ln in body[1:]]


def _check_frames(frames: list[np.ndarray]) -> list[int]:
    ks = [_k_of(f) for f in frames]
    for f, k in zip(frames, ks):
        assert np.array_equal(f, _expected(k))
    return ks


def _events(c: CaptureChild) -> list[dict[str, Any]]:
    with c._lock:
        return [m for m in c.all_messages if m.get("type") == "event"]


def test_recording_writes_ser_frame_log_and_settings_record(child, tmp_path) -> None:
    _open(child)
    assert _start(child, tmp_path)["type"] == "ok"
    time.sleep(2.0)
    stop = child.request({"type": "stop_recording"})
    assert stop["type"] == "ok"

    zwo = tmp_path / "zwo"
    assert sorted(p.name for p in zwo.glob("segment_*.ser")) == ["segment_000.ser"]
    head, frames, stamps = read_ser(zwo / "segment_000.ser")
    assert head["count"] == len(frames) > 10
    assert head["color"] == 8 and head["little"] == 1
    _check_frames(frames)
    assert stamps == sorted(stamps)

    comments, rows = read_log(zwo / "frames.csv")
    assert len(comments) == 3
    assert len(rows) == len(frames)
    assert [int(r[4]) for r in rows] == list(range(len(rows)))
    assert [int(r[0]) for r in rows] == list(range(len(rows)))
    assert {r[3] for r in rows} == {"0"}

    rec = json.loads((zwo / "segment_000.json").read_text(encoding="utf-8"))
    assert rec["closed_reason"] == "stop"
    assert rec["frames"] == len(rows)
    assert rec["settings"]["roi"] == ROI
    assert rec["focus_preset"] is None
    assert not list(zwo.glob("*.part"))

    assert stop["next_segment_index"] == 1
    assert stop["next_frame_index"] == len(rows)
    assert stop["segments"] == ["segment_000.ser"]

    names = [e["event"] for e in _events(child)]
    wanted = ["recording_started", "segment_opened", "segment_closed", "recording_stopped"]
    positions = [names.index(n) for n in wanted]
    assert positions == sorted(positions)


def test_settings_change_mid_recording_starts_a_segment(child, tmp_path) -> None:
    _open(child)
    _start(child, tmp_path)
    time.sleep(1.0)
    assert child.request({"type": "set", "exposure_us": 12000})["type"] == "ok"
    time.sleep(1.0)
    child.request({"type": "stop_recording"})

    zwo = tmp_path / "zwo"
    assert sorted(p.name for p in zwo.glob("segment_*.ser")) == [
        "segment_000.ser",
        "segment_001.ser",
    ]
    rec0 = json.loads((zwo / "segment_000.json").read_text(encoding="utf-8"))
    rec1 = json.loads((zwo / "segment_001.json").read_text(encoding="utf-8"))
    assert rec0["settings"]["exposure_us"] == 10000
    assert rec1["settings"]["exposure_us"] == 12000
    assert rec0["closed_reason"] == "settings_change"
    assert rec1["closed_reason"] == "stop"

    _, rows = read_log(zwo / "frames.csv")
    segs = [int(r[3]) for r in rows]
    assert segs == sorted(segs) and set(segs) == {0, 1}
    for n in (0, 1):
        _, frames, _ = read_ser(zwo / f"segment_{n:03d}.ser")
        _check_frames(frames)
    details = [e["detail"] for e in _events(child) if e["event"] == "settings_changed"]
    assert "exposure_us: 10000 -> 12000" in details


def test_keep_one_in_three(child, tmp_path) -> None:
    _open(child)
    assert child.request({"type": "set", "keep_one_in_n": 3})["type"] == "ok"
    _start(child, tmp_path)
    time.sleep(2.0)
    child.request({"type": "stop_recording"})

    _, frames, _ = read_ser(tmp_path / "zwo" / "segment_000.ser")
    ks = _check_frames(frames)
    assert len(ks) > 3
    assert all(k % 3 == 0 for k in ks)
    assert [b - a for a, b in zip(ks, ks[1:])] == [3] * (len(ks) - 1)
    rec = json.loads((tmp_path / "zwo" / "segment_000.json").read_text(encoding="utf-8"))
    assert rec["frames_dropped_queue"] == 0
    assert rec["settings"]["keep_one_in_n"] == 3


def test_second_run_continues_numbering(child, tmp_path) -> None:
    _open(child)
    _start(child, tmp_path)
    time.sleep(1.0)
    first = child.request({"type": "stop_recording"})
    _start(child, tmp_path, first["next_segment_index"], first["next_frame_index"])
    time.sleep(1.0)
    second = child.request({"type": "stop_recording"})

    zwo = tmp_path / "zwo"
    assert second["segments"] == ["segment_001.ser"]
    assert (zwo / "segment_001.ser").exists()
    text = (zwo / "frames.csv").read_text(encoding="utf-8")
    assert text.count("frame_index,wall_utc") == 1
    _, rows = read_log(zwo / "frames.csv")
    assert [int(r[0]) for r in rows] == list(range(len(rows)))
    assert second["next_frame_index"] == len(rows)
    assert {r[3] for r in rows} == {"0", "1"}


def test_beamline_cup_position_lands_in_the_frame_log(child, tmp_path) -> None:
    assert _start(child, tmp_path)["code"] == "not_open"
    _open(child)
    assert child.request({"type": "stop_recording"})["code"] == "not_recording"
    _start(child, tmp_path)
    time.sleep(0.5)
    for label in ("IN", "OUT"):
        rep = child.request(
            {"type": "beamline", "cup_position": label, "alumina_position": ""}
        )
        assert rep["type"] == "ok"
        time.sleep(0.5)
    assert _start(child, tmp_path)["code"] == "already_recording"
    assert child.request({"type": "close"})["code"] == "recording"
    child.request({"type": "stop_recording"})

    _, rows = read_log(tmp_path / "zwo" / "frames.csv")
    cups = [r[5] for r in rows]
    assert cups[0] == "UNKNOWN"
    first_in = cups.index("IN")
    first_out = cups.index("OUT")
    assert set(cups[:first_in]) == {"UNKNOWN"}
    assert set(cups[first_in:first_out]) == {"IN"}
    assert set(cups[first_out:]) == {"OUT"}


# --- How a recording ends (ticket 67) ---


def _record_dir_files(tmp_path: Path) -> tuple[Path, list[Path]]:
    zwo = tmp_path / "zwo"
    return zwo, sorted(zwo.glob("segment_*.ser"))


def _records(zwo: Path) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(zwo.glob("segment_*.json"))
    ]


def test_size_limit_rolls_segments(tmp_path) -> None:
    limit = 178 + 10 * W * H * 2
    c = CaptureChild(cwd=tmp_path, extra_args=["--segment-max-bytes", str(limit)])
    try:
        c.wait_for("hello", timeout=10.0)
        _open(c)
        assert _start(c, tmp_path)["type"] == "ok"
        time.sleep(2.0)
        assert c.request({"type": "stop_recording"})["type"] == "ok"
        zwo, sers = _record_dir_files(tmp_path)
        assert len(sers) >= 3
        total = 0
        for s in sers:
            assert s.stat().st_size <= limit
            head, frames, _ = read_ser(s)
            assert head["count"] == len(frames) > 0
            total += len(frames)
        recs = _records(zwo)
        assert [r["closed_reason"] for r in recs[:-1]] == ["size_limit"] * (len(recs) - 1)
        assert recs[-1]["closed_reason"] == "stop"
        _, rows = read_log(zwo / "frames.csv")
        assert [int(r[0]) for r in rows] == list(range(total))
    finally:
        c.kill()


def test_low_disk_refuses_to_start(tmp_path) -> None:
    c = CaptureChild(cwd=tmp_path, extra_args=["--disk-stop-bytes", str(10**18)])
    try:
        c.wait_for("hello", timeout=10.0)
        _open(c)
        rep = _start(c, tmp_path)
        assert rep["type"] == "error" and rep["code"] == "low_disk"
        assert not list((tmp_path / "zwo").glob("segment_*.ser"))
    finally:
        c.kill()


def test_camera_lost_leaves_a_readable_recording(tmp_path) -> None:
    c = CaptureChild(cwd=tmp_path, extra_args=["--fake-fail-after", "40"])
    try:
        c.wait_for("hello", timeout=10.0)
        _open(c)
        assert _start(c, tmp_path)["type"] == "ok"
        c.wait_for("event", timeout=15.0, predicate=lambda m: m["event"] == "camera_lost")
        c.wait_for("event", timeout=5.0, predicate=lambda m: m["event"] == "recording_stopped")
        zwo = tmp_path / "zwo"
        assert _records(zwo)[-1]["closed_reason"] == "camera_lost"
        head, frames, _ = read_ser(zwo / "segment_000.ser")
        _, rows = read_log(zwo / "frames.csv")
        assert head["count"] == len(frames) == len(rows) > 0
        assert c.request({"type": "set", "exposure_us": 12000})["code"] == "not_open"
        assert c.proc.poll() is None
    finally:
        c.kill()


@pytest.mark.parametrize("how", ["heartbeat", "shutdown"])
def test_heartbeat_loss_closes_files_and_exits(tmp_path, how) -> None:
    c = CaptureChild(cwd=tmp_path, extra_args=["--heartbeat-timeout-s", "2"])
    try:
        c.wait_for("hello", timeout=10.0)
        _open(c)
        assert _start(c, tmp_path)["type"] == "ok"
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            c.send({"type": "heartbeat", "id": 0})
            time.sleep(0.3)
        if how == "shutdown":
            assert c.request({"type": "shutdown"})["type"] == "ok"
        assert c.wait_exit(timeout=10.0) == 0
        zwo = tmp_path / "zwo"
        want = "heartbeat_lost" if how == "heartbeat" else "shutdown"
        assert _records(zwo)[-1]["closed_reason"] == want
        head, frames, _ = read_ser(zwo / "segment_000.ser")
        _, rows = read_log(zwo / "frames.csv")
        assert head["count"] == len(frames) == len(rows) > 0
    finally:
        c.kill()


def test_killed_process_keeps_counted_frames(tmp_path) -> None:
    c = CaptureChild(cwd=tmp_path)
    try:
        c.wait_for("hello", timeout=10.0)
        _open(c)
        assert _start(c, tmp_path)["type"] == "ok"
        end = time.monotonic() + 5.5
        while time.monotonic() < end:
            c.send({"type": "heartbeat", "id": 0})
            time.sleep(0.5)
        c.kill()
        data = (tmp_path / "zwo" / "segment_000.ser").read_bytes()
        count = struct.unpack_from("<i", data, 38)[0]
        assert count > 0
        size = W * H * 2
        assert len(data) >= 178 + count * size
        for i in range(count):
            f = np.frombuffer(data, dtype="<u2", count=W * H, offset=178 + i * size)
            f = f.reshape(H, W)
            assert np.array_equal(f, _expected(_k_of(f)))
    finally:
        c.kill()
