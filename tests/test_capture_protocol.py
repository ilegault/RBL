"""Pure tests for the capture contract: no Qt, no process."""
import dataclasses
import json

import pytest

from rbl.config.capture_config import (
    CAPTURE_DARK_FRAMES_MAX,
    CAPTURE_KEEP_ONE_IN_N_MAX,
    CAPTURE_PHOTO_BURST_MAX,
    CAPTURE_PROTOCOL_VERSION,
)
from rbl.hardware import capture_protocol as cp
from rbl.hardware.cup_status import CupPosition

ROI = {"x": 0, "y": 2, "width": 640, "height": 400}

COMMANDS = [
    cp.OpenCmd(0),
    cp.CloseCmd(),
    cp.SetCmd(exposure_us=500, gain=10, bit_depth=8, roi=ROI, keep_one_in_n=4),
    cp.SetCmd(gain=0),
    cp.PreviewCmd("auto", 1.5),
    cp.StartRecordingCmd("C:/s", "2026-10-27T12:00:00+00:00", 0, 0),
    cp.StopRecordingCmd(),
    cp.PhotoCmd("C:/s", 3),
    cp.TakeDarksCmd("C:/s", 10),
    cp.BeamlineCmd("IN", ""),
    cp.HeartbeatCmd(),
    cp.ShutdownCmd(),
]

NOTIFICATIONS = [
    {"type": "ok", "reply_to": 3, "anything": [1, 2]},
    {"type": "error", "reply_to": 3, "code": "not_open", "message": "x"},
    {"type": "hello", "protocol": 1, "pid": 5, "cameras": ["ASI585MC"]},
    {
        "type": "status", "acquired_fps": 99.5, "frames_written": 1,
        "frames_dropped_camera": 0, "frames_dropped_queue": 0, "recording": False,
        "segment": -1, "bytes_per_hour": 1.0, "disk_free_bytes": 10,
        "sensor_temperature_c": 31.2,
    },
    {
        "type": "preview", "width": 4, "height": 4, "roi": ROI,
        "stretch_mode": "auto", "brightness": 1.0, "jpeg_b64": "AA==",
    },
    {"type": "event", "event": "low_disk", "detail": ""},
    {"type": "fatal", "message": "boom"},
]


@pytest.mark.parametrize("cmd", COMMANDS)
def test_every_command_round_trips(cmd):
    msg = cmd.to_message(7)
    wire = cp.encode(msg)
    assert wire.endswith(b"\n") and wire.count(b"\n") == 1
    assert cp.decode(wire) == msg
    assert cp.command_from_message(cp.decode(wire)) == dataclasses.replace(cmd, id=7)


@pytest.mark.parametrize("msg", NOTIFICATIONS)
def test_every_message_round_trips(msg):
    wire = cp.encode(msg)
    assert wire.endswith(b"\n") and wire.count(b"\n") == 1
    assert cp.decode(wire) == msg


def test_set_omits_none_fields():
    assert cp.SetCmd(gain=3).to_message(1) == {"type": "set", "id": 1, "gain": 3}


def _raw(**kw):
    return json.dumps(kw).encode()


def _set(**kw):
    return {"type": "set", "id": 1, **kw}


BAD = [
    b"{not json",
    b"[1, 2]",
    _raw(id=1),
    _raw(type="nope", id=1),
    _raw(type="close"),
    _raw(type="close", id="1"),
    _raw(type="close", id=True),
    _raw(**_set()),
    _raw(**_set(gain=1, extra=2)),
    _raw(**_set(bit_depth=12)),
    _raw(**_set(keep_one_in_n=0)),
    _raw(**_set(keep_one_in_n=CAPTURE_KEEP_ONE_IN_N_MAX + 1)),
    _raw(type="photo", id=1, folder="f", burst=0),
    _raw(type="photo", id=1, folder="f", burst=CAPTURE_PHOTO_BURST_MAX + 1),
    _raw(type="take_darks", id=1, folder="f", frames=0),
    _raw(type="take_darks", id=1, folder="f", frames=CAPTURE_DARK_FRAMES_MAX + 1),
    _raw(**_set(roi={"x": 0, "y": 0, "width": 0, "height": 4})),
    b'{"type":"preview","id":1,"mode":"auto","brightness":NaN}',
    _raw(type="beamline", id=1, cup_position="MAYBE", alumina_position=""),
    _raw(type="error", reply_to=1, code="oops", message=""),
    _raw(type="start_recording", id=1, folder="f", session_t0_utc="yesterday",
         first_segment_index=0, first_frame_index=0),
    _raw(type="open", id=1, camera_index=True),
    b"\xff\xfe",
]


@pytest.mark.parametrize("line", BAD)
def test_decode_rejects(line):
    with pytest.raises(cp.ProtocolError):
        cp.decode(line)


def test_encode_refuses_what_decode_refuses():
    with pytest.raises(cp.ProtocolError):
        cp.encode({"type": "set", "id": 1})
    with pytest.raises(cp.ProtocolError):
        cp.encode({"type": "set", "id": 1, "gain": 1, "bogus": 1})
    with pytest.raises(cp.ProtocolError):
        cp.encode({"type": "preview", "id": 1, "mode": "auto", "brightness": float("nan")})


def test_decode_strips_crlf():
    assert cp.decode(b'{"type":"close","id":1}\r\n') == {"type": "close", "id": 1}


def test_check_hello():
    cp.check_hello({"type": "hello", "protocol": CAPTURE_PROTOCOL_VERSION, "pid": 1, "cameras": []})
    with pytest.raises(cp.ProtocolError):
        cp.check_hello({"type": "status", "protocol": CAPTURE_PROTOCOL_VERSION})
    with pytest.raises(cp.ProtocolError):
        cp.check_hello({"type": "hello", "protocol": 2, "pid": 1, "cameras": []})


@pytest.mark.parametrize(
    "args, expected",
    [
        ((1, 1, 101, 51, 1920, 1080), (0, 0, 96, 50)),
        ((1900, 1070, 500, 500, 1920, 1080), (1900, 1070, 16, 10)),
        ((0, 0, 3, 1, 1920, 1080), (0, 0, 8, 2)),
        ((0, 0, 1920, 1080, 1920, 1080), (0, 0, 1920, 1080)),
        ((1919, 1079, 100, 100, 1920, 1080), (1912, 1078, 8, 2)),
    ],
)
def test_snap_roi(args, expected):
    got = cp.snap_roi(*args)
    assert got == expected
    x, y, w, h = got
    assert x % 2 == 0 and y % 2 == 0 and w % 8 == 0 and h % 2 == 0
    assert x + w <= args[4] and y + h <= args[5]


def test_bytes_per_hour_and_cup_label():
    full = cp.estimate_bytes_per_hour(640, 400, 16, 100.0, 1)
    assert full == 640 * 400 * 2 * 100 * 3600
    assert cp.estimate_bytes_per_hour(640, 400, 16, 100.0, 4) == full / 4
    for pos in CupPosition:
        assert cp.cup_position_label(True, False, pos) == pos.value
        assert cp.cup_position_label(False, False, pos) == "UNKNOWN"
        assert cp.cup_position_label(True, True, pos) == "UNKNOWN"
