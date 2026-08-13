"""
video_recorder.py
Frame-accurate MJPG segment recorder with non-drifting decimation.

Lives on the camera thread (see session_recorder.py §6 of the design doc).
offer_frame() is called for every frame CameraSource produces; this class
decides whether to write the frame based on a non-drifting accumulator, and
rolls to the next segment file when the time or size limit is reached.

Non-drifting decimation
-----------------------
The naive approach — self._next_due = t_rel_s + interval — accumulates drift
over 8 hours because it anchors each due-time to the actual arrival time of
the previous written frame rather than to a fixed grid.  At record_fps=2 over
8 hours that is 57 600 intervals, each potentially 1-2 ms late, which adds up
to minutes of skew between the CSV rows (anchored to the monotonic clock) and
the video frames.

The correct approach advances _next_due by exactly 1/record_fps each time, so
the grid stays fixed relative to session start.  A resync fires only when we
have fallen far behind (e.g. after the segment roll stalls); this prevents a
burst of catch-up writes without giving up the non-drifting property during
normal operation.

frames.csv
----------
Every written frame is recorded in frames.csv with its exact timestamp (from
the camera thread, before any processing), monotonic frame_index, segment
index, and 0-based segment_frame.  This is the only authoritative mapping
between CSV rows (which carry frame_index) and video frames.

This object is constructed and owned by SessionRecorder but lives on the
camera thread — it is moved there via moveToThread before start() is called.
"""
import csv
import os
import time

from PySide6.QtCore import QObject, Signal

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


class VideoRecorder(QObject):
    """Write MJPG AVI segments and a frames.csv index on the camera thread.

    Signals
    -------
    segment_closed(avi_path, segment_index)
        Emitted after each segment file is finished and flushed.  The
        TranscodeQueue connects to this to start the MP4 transcode.
    error(message)
        Emitted when a fatal write error occurs; recording stops cleanly.
    """

    segment_closed = Signal(str, int)   # avi_path, segment_index
    error          = Signal(str)

    # Check file size every N frames to avoid per-frame stat calls.
    _SIZE_CHECK_INTERVAL = 100

    def __init__(self, folder: str, *,
                 width: int, height: int,
                 record_fps: int,
                 segment_seconds: int,
                 segment_max_bytes: int,
                 parent=None):
        super().__init__(parent)
        self._folder           = folder
        self._width            = width
        self._height           = height
        self._record_fps       = record_fps
        self._segment_seconds  = segment_seconds
        self._segment_max_bytes = segment_max_bytes

        # State
        self._writer: "cv2.VideoWriter | None" = None
        self._frames_file   = None
        self._frames_writer = None
        self._frames_buf: list[dict] = []

        self._segment_index = 0
        self._segment_frame = 0
        self._segment_start_t: float = 0.0
        self._frame_index   = 0          # monotonic across whole session

        self._next_due: float = 0.0
        self._size_check_countdown = self._SIZE_CHECK_INTERVAL
        self._current_avi_path: str = ""

        self._running = False

    # ---- public API --------------------------------------------------------

    def start(self) -> bool:
        """Open the first segment and frames.csv.  Returns False on failure."""
        if not _CV2_OK:
            self.error.emit("cv2 not available — video recording disabled")
            return False

        frames_path = os.path.join(self._folder, "frames.csv")
        try:
            self._frames_file = open(frames_path, "w", newline="", encoding="utf-8")
        except OSError as exc:
            self.error.emit(f"Cannot open frames.csv: {exc}")
            return False

        self._frames_writer = csv.DictWriter(
            self._frames_file,
            fieldnames=["frame_index", "t_rel_s", "wall_utc",
                        "segment", "segment_frame"],
            lineterminator="\n",
        )
        self._frames_writer.writeheader()

        self._next_due = 0.0
        self._running  = True
        return self._open_segment()

    def offer_frame(self, frame, t_rel_s: float, wall_utc: str) -> "int | None":
        """Try to write frame at t_rel_s.  Returns frame_index if written, else None."""
        if not self._running or self._writer is None:
            return None

        # Non-drifting accumulator.
        if t_rel_s + 1e-9 < self._next_due:
            return None
        self._next_due += 1.0 / self._record_fps
        # Resync if we fell far behind (e.g. after a slow segment roll).
        if self._next_due < t_rel_s:
            self._next_due = t_rel_s + 1.0 / self._record_fps

        # Roll segment if time or size limit reached.
        if t_rel_s - self._segment_start_t >= self._segment_seconds:
            if not self._roll_segment(t_rel_s):
                return None
        else:
            self._size_check_countdown -= 1
            if self._size_check_countdown <= 0:
                self._size_check_countdown = self._SIZE_CHECK_INTERVAL
                try:
                    sz = os.path.getsize(self._current_avi_path)
                    if sz >= self._segment_max_bytes:
                        if not self._roll_segment(t_rel_s):
                            return None
                except OSError:
                    pass

        self._writer.write(frame)

        # Record frame timing.
        idx = self._frame_index
        self._frames_buf.append({
            "frame_index":   idx,
            "t_rel_s":       f"{t_rel_s:.6f}",
            "wall_utc":      wall_utc,
            "segment":       self._segment_index,
            "segment_frame": self._segment_frame,
        })
        self._segment_frame += 1
        self._frame_index   += 1

        # Flush frames.csv every 64 rows or 2 s worth of frames.
        if len(self._frames_buf) >= 64:
            self._flush_frames()

        return idx

    def stop(self) -> None:
        """Flush everything, close the current segment, close frames.csv."""
        self._running = False
        self._close_segment()
        self._flush_frames()
        if self._frames_file is not None:
            self._frames_file.close()
            self._frames_file   = None
            self._frames_writer = None

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def segment_index(self) -> int:
        return self._segment_index

    # ---- internal ----------------------------------------------------------

    def _open_segment(self) -> bool:
        name = f"video_{self._segment_index:03d}.avi"
        path = os.path.join(self._folder, name)
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(
            path, fourcc, self._record_fps, (self._width, self._height))
        if not writer.isOpened():
            self.error.emit(f"Cannot open VideoWriter for {name}")
            self._running = False
            return False
        self._writer           = writer
        self._current_avi_path = path
        self._segment_frame    = 0
        return True

    def _close_segment(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def _roll_segment(self, t_rel_s: float = 0.0) -> bool:
        """Close the current segment, emit signal, open the next one."""
        self._close_segment()
        self._flush_frames()
        self.segment_closed.emit(self._current_avi_path, self._segment_index)
        self._segment_index    += 1
        self._segment_start_t   = t_rel_s
        self._size_check_countdown = self._SIZE_CHECK_INTERVAL
        return self._open_segment()

    def _flush_frames(self) -> None:
        if self._frames_writer is not None and self._frames_buf:
            for row in self._frames_buf:
                self._frames_writer.writerow(row)
            self._frames_file.flush()
        self._frames_buf.clear()
