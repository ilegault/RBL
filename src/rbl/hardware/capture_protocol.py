"""
capture_protocol.py
The contract between the application and the ZWO Capture Process, plus the
small pure rules both sides use.

WHY THIS EXISTS
---------------
The Capture Process (ADR 0008) is a separate program that owns the camera.
What crosses the pipe must be described once, validated identically by the
sender and the receiver, and be independent of Python. This docstring is the
AUTHORITATIVE description of the protocol. A compiled replacement for the
Capture Process implements exactly what this docstring describes, and nothing
else. The module is pure: no Qt, no I/O, and it imports nothing from
``rbl.gui``, ``rbl.services`` or ``rbl.state``, so the Qt-free Capture Process
can import it.

FRAMING
-------
UTF-8 JSON, one object per line, terminated by ``\\n``, on the child's stdin
(commands) and stdout (replies and notifications). Every message has a string
``"type"``. Every command has an integer ``"id"``; its reply is
``{"type": "ok", "reply_to": id, ...}`` or
``{"type": "error", "reply_to": id, "code": str, "message": str}``.
Booleans are never accepted where an integer is required. ``NaN`` and
infinities are never sent.

The command ``preview`` and the notification ``preview`` share a type name.
They are told apart by ``id``: a message with an ``id`` is a command, one
without is a notification.

HELLO
-----
The child's first line is ``hello``. ``check_hello`` raises ``ProtocolError``
when ``type`` is not ``hello`` or ``protocol`` differs from
``CAPTURE_PROTOCOL_VERSION``; the application then sends ``shutdown`` and does
not open the camera.

COMMANDS (application -> Capture Process)
-----------------------------------------
Every command also carries ``id: int``.

``open``
    fields: ``camera_index: int >= 0``.
    reply: model, serial, sdk_version, sensor_width, sensor_height,
    bayer_pattern, controls, settings.
``close``
    fields: none. reply: none.
``set``
    fields: at least one of ``exposure_us: int >= 1``, ``gain: int >= 0``,
    ``bit_depth: 8|16``, ``roi: {x >= 0, y >= 0, width >= 1, height >= 1}``,
    ``keep_one_in_n: 1..CAPTURE_KEEP_ONE_IN_N_MAX``; no other keys besides
    ``type`` and ``id``.
    reply: ``settings`` (as applied, after snapping).
``preview``
    fields: ``mode: "auto"|"linear"``, ``brightness: finite number``.
    reply: none.
``start_recording``
    fields: ``folder: str`` (absolute session folder), ``session_t0_utc: str``
    (ISO 8601), ``first_segment_index: int >= 0``,
    ``first_frame_index: int >= 0``. reply: none.
``stop_recording``
    fields: none. reply: next_segment_index, next_frame_index,
    ``segments: [str]``.
``photo``
    fields: ``folder: str``, ``burst: 1..CAPTURE_PHOTO_BURST_MAX``.
    reply: ``files: [str]`` (absolute).
``take_darks``
    fields: ``folder: str``, ``frames: 1..CAPTURE_DARK_FRAMES_MAX``.
    reply: ``file: str``.
``beamline``
    fields: ``cup_position: str`` (one of CUP_POSITION_LABELS),
    ``alumina_position: str``. reply: none.
``heartbeat``
    fields: none. No reply at all.
``shutdown``
    fields: none. reply: ``ok``, then the process exits.

``controls`` (in the ``open`` reply) is ``{name: {"min", "max", "default"}}``
for ``Exposure``, ``Gain``, ``Offset`` and ``BandWidth``. ``settings`` is
``{"exposure_us", "gain", "bit_depth", "roi", "keep_one_in_n", "offset",
"usb_bandwidth", "high_speed_mode"}``. Other fields of an ``ok`` are free.

NOTIFICATIONS (Capture Process -> application)
----------------------------------------------
``hello``
    ``protocol: int, pid: int, cameras: [str]``. First line, always.
``status``
    ``acquired_fps, frames_written, frames_dropped_camera,
    frames_dropped_queue, recording, segment, bytes_per_hour,
    disk_free_bytes, sensor_temperature_c``. Every CAPTURE_STATUS_INTERVAL_S
    while open. ``segment`` is an int, ``-1`` when not recording.
``preview``
    ``width, height, roi, stretch_mode, brightness, jpeg_b64``. At most
    CAPTURE_PREVIEW_MAX_FPS while open.
``event``
    ``event: str, detail: str``. See spec D6.
``fatal``
    ``message: str``. Just before exiting on an unrecoverable error.

ERROR CODES
-----------
Refusals use ``error`` with these codes and no other: ``not_open``,
``already_recording``, ``not_recording``, ``recording`` (command not allowed
while recording: ``take_darks``, ``close``), ``low_disk``, ``bad_request``,
``camera_error``.

VALIDATION
----------
Two module-level tables (``_COMMANDS``, ``_NOTIFICATIONS``) map each message
type to its fields; adding a message is one table row. ``encode`` validates
before writing, so neither side can send a message the other would refuse.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from rbl.config.capture_config import (
    CAPTURE_DARK_FRAMES_MAX,
    CAPTURE_KEEP_ONE_IN_N_MAX,
    CAPTURE_PHOTO_BURST_MAX,
    CAPTURE_PROTOCOL_VERSION,
)
from rbl.hardware.cup_status import CupPosition


class ProtocolError(ValueError):
    """A message that violates the capture contract."""


ERROR_CODES = (
    "not_open",
    "already_recording",
    "not_recording",
    "recording",
    "low_disk",
    "bad_request",
    "camera_error",
)
CUP_POSITION_LABELS = ("IN", "OUT", "IN_TRANSIT", "INDETERMINATE", "UNKNOWN")

_Check = Callable[[Any], bool]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_min(lo: int) -> _Check:
    return lambda v: _is_int(v) and v >= lo


def _int_range(lo: int, hi: int) -> _Check:
    return lambda v: _is_int(v) and lo <= v <= hi


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    return _is_number(v) and math.isfinite(v)


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(s, str) for s in v)


def _str_in(*options: str) -> _Check:
    return lambda v: isinstance(v, str) and v in options


def _bit_depth(v: Any) -> bool:
    return _is_int(v) and v in (8, 16)


def _number_or_none(v: Any) -> bool:
    return v is None or _is_number(v)


def _iso(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    try:
        datetime.fromisoformat(v)
    except ValueError:
        return False
    return True


def _roi(v: Any) -> bool:
    if not isinstance(v, dict) or set(v) != {"x", "y", "width", "height"}:
        return False
    return (
        _int_min(0)(v["x"])
        and _int_min(0)(v["y"])
        and _int_min(1)(v["width"])
        and _int_min(1)(v["height"])
    )


# type -> (required fields, optional fields, at-least-one-optional required)
_Schema = tuple[dict[str, _Check], dict[str, _Check], bool]

_COMMANDS: dict[str, _Schema] = {
    "open": ({"camera_index": _int_min(0)}, {}, False),
    "close": ({}, {}, False),
    "set": (
        {},
        {
            "exposure_us": _int_min(1),
            "gain": _int_min(0),
            "bit_depth": _bit_depth,
            "roi": _roi,
            "keep_one_in_n": _int_range(1, CAPTURE_KEEP_ONE_IN_N_MAX),
        },
        True,
    ),
    "preview": ({"mode": _str_in("auto", "linear"), "brightness": _finite}, {}, False),
    "start_recording": (
        {
            "folder": _nonempty_str,
            "session_t0_utc": _iso,
            "first_segment_index": _int_min(0),
            "first_frame_index": _int_min(0),
        },
        {},
        False,
    ),
    "stop_recording": ({}, {}, False),
    "photo": (
        {"folder": _nonempty_str, "burst": _int_range(1, CAPTURE_PHOTO_BURST_MAX)},
        {},
        False,
    ),
    "take_darks": (
        {"folder": _nonempty_str, "frames": _int_range(1, CAPTURE_DARK_FRAMES_MAX)},
        {},
        False,
    ),
    "beamline": (
        {"cup_position": _str_in(*CUP_POSITION_LABELS), "alumina_position": _is_str},
        {},
        False,
    ),
    "heartbeat": ({}, {}, False),
    "shutdown": ({}, {}, False),
}

# Replies and notifications. Only "ok" accepts fields beyond those listed.
_NOTIFICATIONS: dict[str, _Schema] = {
    "ok": ({"reply_to": _is_int}, {}, False),
    "error": (
        {"reply_to": _is_int, "code": _str_in(*ERROR_CODES), "message": _is_str},
        {},
        False,
    ),
    "hello": ({"protocol": _is_int, "pid": _is_int, "cameras": _str_list}, {}, False),
    "status": (
        {
            "acquired_fps": _is_number,
            "frames_written": _is_int,
            "frames_dropped_camera": _is_int,
            "frames_dropped_queue": _is_int,
            "recording": _is_bool,
            "segment": _is_int,
            "bytes_per_hour": _is_number,
            "disk_free_bytes": _is_int,
            "sensor_temperature_c": _number_or_none,
        },
        {},
        False,
    ),
    "preview": (
        {
            "width": _int_min(1),
            "height": _int_min(1),
            "roi": _roi,
            "stretch_mode": _is_str,
            "brightness": _finite,
            "jpeg_b64": _is_str,
        },
        {},
        False,
    ),
    "event": ({"event": _is_str, "detail": _is_str}, {}, False),
    "fatal": ({"message": _is_str}, {}, False),
}


def _validate(msg: Any) -> None:
    if not isinstance(msg, dict):
        raise ProtocolError("message is not a JSON object")
    mtype = msg.get("type")
    if not isinstance(mtype, str):
        raise ProtocolError("missing or non-string 'type'")
    if "id" in msg:
        schema = _COMMANDS.get(mtype)
        if schema is None:
            raise ProtocolError(f"unknown command type {mtype!r}")
        if not _is_int(msg["id"]):
            raise ProtocolError("command 'id' must be an integer")
        base = {"type", "id"}
    else:
        if mtype in _COMMANDS and mtype not in _NOTIFICATIONS:
            raise ProtocolError(f"command {mtype!r} has no integer 'id'")
        schema = _NOTIFICATIONS.get(mtype)
        if schema is None:
            raise ProtocolError(f"unknown message type {mtype!r}")
        base = {"type"}
    required, optional, need_one = schema
    for name, check in required.items():
        if name not in msg:
            raise ProtocolError(f"{mtype}: missing field {name!r}")
        if not check(msg[name]):
            raise ProtocolError(f"{mtype}: bad value for {name!r}")
    present = [k for k in optional if k in msg]
    for name in present:
        if not optional[name](msg[name]):
            raise ProtocolError(f"{mtype}: bad value for {name!r}")
    if need_one and not present:
        raise ProtocolError(f"{mtype}: no settings given")
    if mtype != "ok":
        extra = set(msg) - base - set(required) - set(optional)
        if extra:
            raise ProtocolError(f"{mtype}: unexpected field(s) {sorted(extra)}")


def encode(msg: dict[str, Any]) -> bytes:
    """Validate ``msg`` and return its wire form (compact JSON + newline)."""
    _validate(msg)
    try:
        text = json.dumps(msg, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(str(exc)) from exc
    return text.encode("utf-8") + b"\n"


def decode(line: bytes) -> dict[str, Any]:
    """Parse and validate one wire line."""
    if line.endswith(b"\n"):
        line = line[:-1]
    if line.endswith(b"\r"):
        line = line[:-1]
    try:
        msg = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"undecodable line: {exc}") from exc
    _validate(msg)
    assert isinstance(msg, dict)
    return msg


# ---------------------------------------------------------------- commands
@dataclass(frozen=True)
class OpenCmd:
    camera_index: int
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "open", "id": cmd_id, "camera_index": self.camera_index}


@dataclass(frozen=True)
class CloseCmd:
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "close", "id": cmd_id}


@dataclass(frozen=True)
class SetCmd:
    exposure_us: Optional[int] = None
    gain: Optional[int] = None
    bit_depth: Optional[int] = None
    roi: Optional[dict[str, int]] = None
    keep_one_in_n: Optional[int] = None
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        msg: dict[str, Any] = {"type": "set", "id": cmd_id}
        for name in ("exposure_us", "gain", "bit_depth", "roi", "keep_one_in_n"):
            value = getattr(self, name)
            if value is not None:
                msg[name] = value
        return msg


@dataclass(frozen=True)
class PreviewCmd:
    mode: str
    brightness: float
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {
            "type": "preview",
            "id": cmd_id,
            "mode": self.mode,
            "brightness": self.brightness,
        }


@dataclass(frozen=True)
class StartRecordingCmd:
    folder: str
    session_t0_utc: str
    first_segment_index: int
    first_frame_index: int
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {
            "type": "start_recording",
            "id": cmd_id,
            "folder": self.folder,
            "session_t0_utc": self.session_t0_utc,
            "first_segment_index": self.first_segment_index,
            "first_frame_index": self.first_frame_index,
        }


@dataclass(frozen=True)
class StopRecordingCmd:
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "stop_recording", "id": cmd_id}


@dataclass(frozen=True)
class PhotoCmd:
    folder: str
    burst: int
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "photo", "id": cmd_id, "folder": self.folder, "burst": self.burst}


@dataclass(frozen=True)
class TakeDarksCmd:
    folder: str
    frames: int
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "take_darks", "id": cmd_id, "folder": self.folder, "frames": self.frames}


@dataclass(frozen=True)
class BeamlineCmd:
    cup_position: str
    alumina_position: str
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {
            "type": "beamline",
            "id": cmd_id,
            "cup_position": self.cup_position,
            "alumina_position": self.alumina_position,
        }


@dataclass(frozen=True)
class HeartbeatCmd:
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "heartbeat", "id": cmd_id}


@dataclass(frozen=True)
class ShutdownCmd:
    id: int = 0

    def to_message(self, cmd_id: int) -> dict[str, Any]:
        return {"type": "shutdown", "id": cmd_id}


Command = (
    OpenCmd
    | CloseCmd
    | SetCmd
    | PreviewCmd
    | StartRecordingCmd
    | StopRecordingCmd
    | PhotoCmd
    | TakeDarksCmd
    | BeamlineCmd
    | HeartbeatCmd
    | ShutdownCmd
)


def command_from_message(msg: dict[str, Any]) -> Command:
    """Validate ``msg`` as a command and return its dataclass (``id`` included)."""
    _validate(msg)
    if "id" not in msg:
        raise ProtocolError("not a command: no 'id'")
    cid: int = msg["id"]
    t = msg["type"]
    if t == "open":
        return OpenCmd(msg["camera_index"], cid)
    if t == "close":
        return CloseCmd(cid)
    if t == "set":
        return SetCmd(
            exposure_us=msg.get("exposure_us"),
            gain=msg.get("gain"),
            bit_depth=msg.get("bit_depth"),
            roi=msg.get("roi"),
            keep_one_in_n=msg.get("keep_one_in_n"),
            id=cid,
        )
    if t == "preview":
        return PreviewCmd(msg["mode"], msg["brightness"], cid)
    if t == "start_recording":
        return StartRecordingCmd(
            msg["folder"],
            msg["session_t0_utc"],
            msg["first_segment_index"],
            msg["first_frame_index"],
            cid,
        )
    if t == "stop_recording":
        return StopRecordingCmd(cid)
    if t == "photo":
        return PhotoCmd(msg["folder"], msg["burst"], cid)
    if t == "take_darks":
        return TakeDarksCmd(msg["folder"], msg["frames"], cid)
    if t == "beamline":
        return BeamlineCmd(msg["cup_position"], msg["alumina_position"], cid)
    if t == "heartbeat":
        return HeartbeatCmd(cid)
    return ShutdownCmd(cid)


def check_hello(msg: dict[str, Any]) -> None:
    """Raise ``ProtocolError`` unless ``msg`` is a hello of our protocol version."""
    if msg.get("type") != "hello":
        raise ProtocolError(f"expected hello, got {msg.get('type')!r}")
    if msg.get("protocol") != CAPTURE_PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol {msg.get('protocol')!r} != expected {CAPTURE_PROTOCOL_VERSION}"
        )


# ------------------------------------------------------------------- rules
_MIN_WIDTH = 8
_MIN_HEIGHT = 2


def snap_roi(
    x: int, y: int, width: int, height: int, sensor_width: int, sensor_height: int
) -> tuple[int, int, int, int]:
    """Snap an ROI to what the sensor accepts, keeping the Bayer pattern.

    Even x and y keep the ROI's Bayer pattern identical to the sensor's, so
    one ``bayer_pattern`` describes every segment.
    """
    x = max(0, min(x - x % 2, sensor_width - _MIN_WIDTH))
    y = max(0, min(y - y % 2, sensor_height - _MIN_HEIGHT))
    width = min(width, sensor_width - x)
    height = min(height, sensor_height - y)
    width -= width % 8
    height -= height % 2
    return x, y, max(width, _MIN_WIDTH), max(height, _MIN_HEIGHT)


def estimate_bytes_per_hour(
    width: int, height: int, bit_depth: int, acquired_fps: float, keep_one_in_n: int
) -> float:
    return width * height * (bit_depth / 8) * acquired_fps / keep_one_in_n * 3600


def cup_position_label(connected: bool, stale: bool, confirmed: CupPosition) -> str:
    """The frame log's cup column: UNKNOWN unless the reading is live."""
    if not connected or stale:
        return "UNKNOWN"
    return confirmed.value
