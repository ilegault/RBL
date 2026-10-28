"""
recording.py
The Master Recording on disk: SER segments, the frame log, the settings records.

WHY THIS EXISTS
---------------
ADR 0008 decision 6: the ZWO frames are recorded exactly as the camera delivered them
(16-bit stays 16-bit, never converted), and *no segment ever mixes settings*. A frame
taken at 10 ms exposure and one taken at 12 ms must not share a SER file, because the
SER header carries one width, height and depth and a reader has no way to learn that
the exposure changed half-way. So the writer here opens a new segment whenever a
frame's settings generation differs from the open segment's, and each segment gets its
own ``segment_NNN.json`` settings record saying what was in force.

Three durability rules come from the 12-hour drift run that dies at hour 11:

* The SER header's ``FrameCount`` is rewritten every ``CAPTURE_SER_HEADER_REFRESH_S``,
  so a killed process leaves every frame up to the last refresh correctly counted.
* The frame log is flushed after every row.
* The settings record is written when a segment opens and rewritten when it closes,
  through a temp name and ``os.replace``, so a reader never sees half a JSON file.
  (Same technique as ``rbl.services.snapshot_json.dump_json``, copied rather than
  imported: the Capture Process may not import ``rbl.services``.)

No Qt, no ``rbl.services``, no threads of its own: ``RecordingRun`` is driven from the
writer thread that ``process.py`` owns, so everything here is plain sequential code.
Segment numbering and frame numbering continue across video runs in a session
(ADR 0004 decision 7): the caller passes the first indices in and reads the next ones
out.
"""
from __future__ import annotations

import json
import os
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from rbl.config.capture_config import CAPTURE_SER_HEADER_REFRESH_S
from rbl.hardware import ser_format

FRAME_LOG_HEADER_LINES = (
    "# RBL ZWO frame log, schema 1",
    "# wall_utc is when the frame arrived from the camera library, not when its exposure began.",
    "# cup_position reflects the end-station Faraday cup only. Upstream cups are manual and "
    "not recorded; beam-on-target cannot be inferred from this file alone.",
    "frame_index,wall_utc,t_rel_s,segment,segment_frame,cup_position,alumina_position,photo",
)

CUP_POSITION_UNKNOWN = "UNKNOWN"


def format_wall_utc(dt: datetime) -> str:
    """ISO 8601 with milliseconds and ``Z``, same format as ``SessionRecorder._derive_wall``."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO 8601 instant; a naive value is taken as UTC."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _frame_bytes(frame: np.ndarray) -> bytes:
    """The frame's pixels exactly as delivered: uint16 little-endian, uint8 as is."""
    if frame.dtype == np.uint16:
        return frame.astype("<u2", copy=False).tobytes()
    return frame.tobytes()


class SerSegmentWriter:
    """One SER file, written frame by frame, with a header that is kept honest."""

    def __init__(
        self,
        path: Path,
        *,
        width: int,
        height: int,
        pixel_depth: int,
        bayer_pattern: str,
        model: str,
        serial: str,
        opened_utc: datetime,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = path
        self._width = width
        self._height = height
        self._pixel_depth = pixel_depth
        self._color_id = ser_format.color_id_for_bayer(bayer_pattern)
        self._instrument = f"{model} {serial}"
        self._date_local = ser_format.ticks_from_datetime(
            opened_utc.astimezone().replace(tzinfo=None)
        )
        self._date_utc = ser_format.ticks_from_datetime(opened_utc)
        self._monotonic = monotonic
        self._timestamps: list[int] = []
        self._closed = False
        self._fh = open(path, "xb")
        header = self._header(0)
        self._fh.write(header)
        self._bytes_written = len(header)
        self._last_refresh = self._monotonic()

    def _header(self, frame_count: int) -> bytes:
        return ser_format.pack_header(
            color_id=self._color_id,
            width=self._width,
            height=self._height,
            pixel_depth=self._pixel_depth,
            frame_count=frame_count,
            observer="RBL",
            instrument=self._instrument,
            telescope="",
            date_local=self._date_local,
            date_utc=self._date_utc,
        )

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    @property
    def frame_count(self) -> int:
        return len(self._timestamps)

    def bytes_if_appended(self, frame: np.ndarray) -> int:
        """File size if ``frame`` were appended and the segment closed now (trailer included)."""
        return (
            self._bytes_written
            + len(_frame_bytes(frame))
            + 8 * (len(self._timestamps) + 1)
        )

    def append(self, frame: np.ndarray, utc_ticks: int) -> None:
        data = _frame_bytes(frame)
        self._fh.write(data)
        self._bytes_written += len(data)
        self._timestamps.append(utc_ticks)
        if self._monotonic() - self._last_refresh >= CAPTURE_SER_HEADER_REFRESH_S:
            self.refresh_header()

    def refresh_header(self) -> None:
        """Rewrite the header's FrameCount with the current count, then flush."""
        end = self._fh.tell()
        self._fh.seek(ser_format.SER_FRAME_COUNT_OFFSET)
        self._fh.write(struct.pack("<i", len(self._timestamps)))
        self._fh.seek(end)
        self._fh.flush()
        self._last_refresh = self._monotonic()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.refresh_header()
            trailer = ser_format.pack_trailer(self._timestamps)
            self._fh.write(trailer)
            self._bytes_written += len(trailer)
            self._fh.flush()
        finally:
            self._fh.close()


class FrameLog:
    """``zwo/frames.csv``: appended across video runs, flushed after every row."""

    def __init__(self, path: Path) -> None:
        is_new = not path.exists() or path.stat().st_size == 0
        self._fh = open(path, "a", encoding="utf-8", newline="\n")
        if is_new:
            self._fh.write("\n".join(FRAME_LOG_HEADER_LINES) + "\n")
            self._fh.flush()

    def write_row(
        self,
        *,
        frame_index: int,
        wall_utc: str,
        t_rel_s: float,
        segment: int,
        segment_frame: int,
        cup_position: str,
        alumina_position: str = "",
        photo: str = "",
    ) -> None:
        self._fh.write(
            f"{frame_index},{wall_utc},{t_rel_s:.6f},{segment},{segment_frame},"
            f"{cup_position},{alumina_position},{photo}\n"
        )
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def write_settings_record(path: Path, record: dict[str, Any]) -> None:
    """Write ``record`` as JSON (indent 2) to ``<path>.part``, then rename onto ``path``."""
    part = path.with_name(path.name + ".part")
    with open(part, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
        fh.flush()
    os.replace(part, path)


@dataclass(frozen=True)
class StampedFrame:
    """A frame as the acquisition thread hands it to the writer."""

    frame: np.ndarray
    utc: datetime
    acquired_index: int
    generation: int
    settings: dict[str, Any]
    cup_position: str
    photo: str = ""  # file name when this frame was also kept as a photo (spec D7)


class RecordingRun:
    """One ``start_recording`` .. ``stop_recording`` run: segments, rows and records.

    ``counters()`` returns the *cumulative* ``(camera_drops, queue_drops)`` so each
    segment's record can state the drops that happened during that segment.
    """

    def __init__(
        self,
        folder: Path,
        *,
        session_t0_utc: datetime,
        first_segment_index: int,
        first_frame_index: int,
        camera: dict[str, str],
        temperature_c: Callable[[], float],
        counters: Callable[[], tuple[int, int]],
        event: Callable[[str, str], None],
        monotonic: Callable[[], float] = time.monotonic,
        max_segment_bytes: int | None = None,
    ) -> None:
        self._max_segment_bytes = max_segment_bytes
        self.dir = folder / "zwo"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._t0 = session_t0_utc
        self._camera = camera
        self._temperature_c = temperature_c
        self._counters = counters
        self._event = event
        self._monotonic = monotonic
        self.next_segment_index = first_segment_index
        self.next_frame_index = first_frame_index
        self.segments: list[str] = []
        self.frames_written = 0
        self.current_segment = -1
        self._log = FrameLog(self.dir / "frames.csv")
        self._ser: SerSegmentWriter | None = None
        self._generation = -1
        self._record: dict[str, Any] = {}
        self._record_path: Path | None = None
        self._segment_frames = 0
        self._base_drops = (0, 0)

    def write(self, item: StampedFrame) -> None:
        if self._ser is not None and item.generation != self._generation:
            self.close_segment("settings_change")
        if self._ser is not None and self._max_segment_bytes is not None and (
            self._segment_frames > 0
            and self._ser.bytes_if_appended(item.frame) > self._max_segment_bytes
        ):
            self.close_segment("size_limit")
        if self._ser is None:
            self._open_segment(item)
        assert self._ser is not None
        self._ser.append(item.frame, ser_format.ticks_from_datetime(item.utc))
        self._log.write_row(
            frame_index=self.next_frame_index,
            wall_utc=format_wall_utc(item.utc),
            t_rel_s=(item.utc - self._t0).total_seconds(),
            segment=self.current_segment,
            segment_frame=self._segment_frames,
            cup_position=item.cup_position,
            photo=item.photo,
        )
        self.next_frame_index += 1
        self.frames_written += 1
        self._segment_frames += 1

    def _open_segment(self, item: StampedFrame) -> None:
        index = self.next_segment_index
        name = f"segment_{index:03d}.ser"
        height, width = item.frame.shape[:2]
        self._ser = SerSegmentWriter(
            self.dir / name,
            width=width,
            height=height,
            pixel_depth=16 if item.frame.dtype == np.uint16 else 8,
            bayer_pattern=self._camera["bayer_pattern"],
            model=self._camera["model"],
            serial=self._camera["serial"],
            opened_utc=item.utc,
            monotonic=self._monotonic,
        )
        self.next_segment_index = index + 1
        self.current_segment = index
        self._generation = item.generation
        self._segment_frames = 0
        self._base_drops = self._counters()
        self.segments.append(name)
        self._record_path = self.dir / f"segment_{index:03d}.json"
        temperature = self._temperature_c()
        self._record = {
            "schema": 1,
            "segment": index,
            "file": name,
            "opened_utc": format_wall_utc(item.utc),
            "closed_utc": None,
            "closed_reason": None,
            "frames": 0,
            "frames_dropped_camera": 0,
            "frames_dropped_queue": 0,
            "camera": dict(self._camera),
            "settings": item.settings,
            "sensor_temperature_c_open": temperature,
            "sensor_temperature_c_close": None,
            "focus_preset": None,
        }
        write_settings_record(self._record_path, self._record)
        self._event("segment_opened", name)

    def close_segment(self, reason: str) -> None:
        if self._ser is None or self._record_path is None:
            return
        ser = self._ser
        self._ser = None
        ser.close()
        camera_drops, queue_drops = self._counters()
        self._record.update(
            closed_utc=format_wall_utc(datetime.now(timezone.utc)),
            closed_reason=reason,
            frames=self._segment_frames,
            frames_dropped_camera=max(0, camera_drops - self._base_drops[0]),
            frames_dropped_queue=max(0, queue_drops - self._base_drops[1]),
            sensor_temperature_c_close=self._temperature_c(),
        )
        write_settings_record(self._record_path, self._record)
        self._event(
            "segment_closed", f"{self._record['file']}, {reason}, {self._segment_frames}"
        )

    @property
    def bytes_written(self) -> int:
        return self._ser.bytes_written if self._ser is not None else 0

    def close(self, reason: str) -> None:
        try:
            self.close_segment(reason)
        finally:
            self._log.close()


def utc_from_anchor(
    anchor_utc: datetime, anchor_perf: float, perf_now: float
) -> datetime:
    """Wall time from the process's own monotonic anchor, never a fresh ``datetime.now()``."""
    return anchor_utc + timedelta(seconds=perf_now - anchor_perf)
