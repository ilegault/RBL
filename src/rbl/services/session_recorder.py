"""
session_recorder.py
Orchestrator and single source of truth for the unified session recorder.

Both the Overview RecordingPanel and the Camera tab bind to this object;
neither holds its own copy of any setting and neither owns a cv2.VideoCapture.
There is exactly one SessionRecorder per process.

Design intent
-------------
The key invariant this class enforces: CSV-only is a first-class mode, not a
fallback.  The camera being absent, closed, or broken never blocks or degrades
data logging.  Video is a value-add that requires a working camera; CSV is the
irreducible minimum.  Video may be started and stopped any number of times
within an active session without overwriting earlier recordings (ADR 0004
decision 7).

The ZWO camera is driven through CaptureClient (ADR 0008), which controls
the Capture Process in a separate child process. When the child process exits
or loses the camera mid-run, the ZWO recording ends cleanly while CSV-only
logging stays first-class and uninterrupted.

The timebase: at session start we capture both time.perf_counter() and
datetime.now(UTC) and derive every subsequent wall-clock time from the monotonic
clock.  This means an NTP correction during an 8-hour run cannot reorder rows
or create a backwards jump between the CSV and the video.  See §4 of the design
doc for the reasoning.

Guards (ticket 32): the window installs ``set_start_guard`` / ``set_stop_guard``
so a session cannot start or stop while that would leave the Faraday cup log in a
state the operator has not confirmed (automatic insertion still running).  The disk
auto-stop calls ``stop(force=True)``, which skips the stop guard: a filling disk must
not wait on a dialog.

Threading:
  GUI thread   : this class, CsvLogWriter, events.csv, session.json, QTimer
  Camera thread: VideoRecorder (moved there via moveToThread), frames.csv
  Transcode thread: TranscodeQueue

The VideoRecorder lives on the camera thread so AVI writes never touch the GUI
thread.  offer_frame() is connected to CameraSource.frame_ready with
DirectConnection so it executes on the camera thread without a queue round-trip.
"""
from __future__ import annotations

import csv
import dataclasses
import math
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QObject,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QProgressDialog

from rbl.config import paths, zwo_settings_store
from rbl.config.recording_config import (
    CSV_INTERVAL_DEFAULT_S,
    CSV_INTERVAL_MAX_S,
    CSV_INTERVAL_MIN_S,
    DISK_AUTOSTOP_BYTES,
    DISK_WARN_BYTES,
    MASTER_CODEC_DEFAULT,
    QUALITY_DEFAULT,
    QUALITY_PRESETS,
    RECORD_FPS_DEFAULT,
    SEGMENT_MAX_BYTES,
    SEGMENT_SECONDS_DEFAULT,
)
from rbl.hardware.camera_source import CameraSource
from rbl.services.capture_client import CaptureClient
from rbl.services.csv_log_writer import CsvLogWriter, flatten
from rbl.services.snapshot_json import dump_json
from rbl.services.vacuum_logger import (
    VacuumLogger,
    gauge_labels,
    vacuum_comment_lines,
)
from rbl.services.video_recorder import VideoRecorder
from rbl.services.video_transcoder import TranscodeQueue, ffmpeg_version, find_ffmpeg


def _logs_dir() -> str:
    from rbl.config.paths import LOGS_DIR
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return str(LOGS_DIR)


class SessionRecorder(QObject):
    """Orchestrate CSV logging, optional video capture, and rolling transcode.

    Parameters
    ----------
    snapshot_fn : callable() -> dict
        Called on the CSV timer; returns the current beamline state.
    camera      : CameraSource
        Shared camera source; may be open or closed.
    capture     : CaptureClient, optional
        Shared Capture Process client for the ZWO camera.
    """

    state_changed    = Signal()
    settings_changed = Signal()
    status           = Signal(str, str)      # message, theme colour role
    session_started  = Signal(str)           # session folder path
    session_stopped  = Signal(str)
    photo_taken      = Signal(str)           # absolute path of saved PNG or FITS
    zwo_preview      = Signal(QImage, dict)  # forwarded from capture.preview

    def __init__(
        self,
        snapshot_fn,
        camera: CameraSource,
        parent=None,
        *,
        capture: CaptureClient | None = None,
    ):
        super().__init__(parent)
        self._snapshot_fn = snapshot_fn
        self._camera      = camera
        self._capture     = capture
        # Vetoes installed by the window (cup log state); None means always allowed.
        self._start_guard: Callable[[], bool] | None = None
        self._stop_guard:  Callable[[], bool] | None = None

        # Settings
        self._csv_interval_s   = CSV_INTERVAL_DEFAULT_S
        self._record_fps       = RECORD_FPS_DEFAULT
        self._segment_seconds  = SEGMENT_SECONDS_DEFAULT
        self._quality_label    = QUALITY_DEFAULT
        self._master_codec     = MASTER_CODEC_DEFAULT

        # Camera fourcc tracking (set when camera opens)
        self._camera_fourcc_actual    = ""
        self._camera_fourcc_requested = ""

        # Session state
        self._recording        = False
        self._folder: str      = ""
        self._session_id: str  = ""
        self._t0_mono: float   = 0.0
        self._t0_wall: datetime | None = None

        # CSV
        self._csv_writer: CsvLogWriter | None = None
        self._csv_timer  = QTimer(self)
        self._csv_timer.timeout.connect(self._write_csv_row)

        # Events
        self._events_file   = None
        self._events_writer = None

        # Video
        self._video_recorder: VideoRecorder | None = None
        self._transcode_queue: TranscodeQueue | None = None
        self._frame_index_current: int = -1   # last written frame index
        self._frames_written: int = 0
        self._frames_dropped: int = 0
        self._segments_done: int  = 0
        self._transcode_pending: int = 0
        self._next_segment_index: int = 0
        self._video_runs: list[dict] = []
        self._ffmpeg_warning_logged: bool = False

        # ZWO Camera & Capture Process state
        self._zwo_settings, _ = zwo_settings_store.load_settings()
        self._camera_kind: str = (
            self._zwo_settings.camera_kind if capture is not None else "usb"
        )
        self._zwo_video_active: bool = False
        self._zwo_starting_video: bool = False
        self._zwo_stopping_video: bool = False
        self._zwo_next_segment: int = 0
        self._zwo_next_frame: int = 0
        self._zwo_runs: list[dict] = []
        self._zwo_segments: list[str] = []
        self._zwo_info: dict = {}
        self._zwo_status: dict = {}
        self._zwo_settings_applied: dict = {}
        self._zwo_open_on_hello: bool = False
        self._zwo_photo_snaps: dict[int, dict] = {}
        self._pending_start_recording_id: int = -1
        self._pending_set_ids: set[int] = set()

        if self._capture is not None:
            self._capture.preview.connect(self.zwo_preview)
            self._capture.status.connect(self._on_zwo_status)
            self._capture.opened.connect(self._on_zwo_opened)
            self._capture.closed.connect(self._on_zwo_closed)
            self._capture.event.connect(self._on_zwo_event)
            self._capture.exited.connect(self._on_zwo_exited)
            self._capture.hello.connect(self._on_zwo_hello)
            self._capture.reply.connect(self._on_zwo_reply)

        # Photos
        self._photo_index: int = 0

        # Manifest state
        self._segment_meta: list[dict] = []
        self._photo_list:   list[str]  = []
        self._notes_count:  int        = 0

        # Vacuum logging (ADR 0004)
        self._vacuum_logger: VacuumLogger | None = None
        self._vacuum_files: list[str] = []

        # ffmpeg
        self._ffmpeg_path    = find_ffmpeg()
        self._ffmpeg_version = ffmpeg_version(self._ffmpeg_path) if self._ffmpeg_path else ""

        # Camera-loss handling and fourcc tracking
        self._camera.closed.connect(self._on_camera_closed)
        self._camera.error.connect(self._on_camera_error)
        self._camera.format_ready.connect(self._on_camera_format_ready)

    # ---- settings ----------------------------------------------------------

    def set_csv_interval_s(self, v: int) -> None:
        if self._recording:
            return
        self._csv_interval_s = max(CSV_INTERVAL_MIN_S, min(CSV_INTERVAL_MAX_S, v))
        self.settings_changed.emit()

    def set_record_fps(self, v: int) -> None:
        if self._video_recorder is not None:
            return
        self._record_fps = v
        self.settings_changed.emit()

    def set_segment_seconds(self, v: int) -> None:
        if self._video_recorder is not None:
            return
        self._segment_seconds = v
        self.settings_changed.emit()

    def set_quality(self, label: str) -> None:
        if self._video_recorder is not None:
            return
        if label in QUALITY_PRESETS:
            self._quality_label = label
            self.settings_changed.emit()

    def set_master_codec(self, label: str) -> None:
        from rbl.config.recording_config import MASTER_CODECS
        if self._video_recorder is not None:
            return
        if label in MASTER_CODECS:
            self._master_codec = label
            self.settings_changed.emit()

    # ---- ZWO camera & settings ---------------------------------------------

    def camera_kind(self) -> str:
        return self._camera_kind

    def set_camera_kind(self, kind: str) -> None:
        if kind not in ("usb", "zwo"):
            return
        if (self._video_recorder is not None) or self._zwo_video_active:
            self.status.emit("Stop video before switching cameras", "warn")
            return
        if kind == "zwo" and self._capture is None:
            return
        if kind == self._camera_kind:
            return
        if kind == "zwo":
            if self._camera.is_open():
                self._camera.close()
        elif kind == "usb":
            self.disconnect_zwo()
        self._camera_kind = kind
        self._zwo_settings = dataclasses.replace(self._zwo_settings, camera_kind=kind)
        zwo_settings_store.save_settings(self._zwo_settings)
        self.settings_changed.emit()
        self.state_changed.emit()

    def connect_zwo(self) -> None:
        if self._capture is None:
            return
        if not self._capture.is_running():
            self._zwo_open_on_hello = True
            self._capture.start("zwo")
        else:
            self._capture.open(0)

    def disconnect_zwo(self) -> None:
        if self._capture is None:
            return
        if self._zwo_video_active:
            self._stop_zwo_video(detail="disconnect_zwo")
        self._capture.close()
        self._capture.shutdown()
        self._zwo_info = {}
        self._zwo_settings_applied = {}
        self.state_changed.emit()

    def set_zwo_settings(self, **fields: Any) -> int:
        if self._capture is None:
            return -1
        cmd_id = self._capture.apply(**fields)
        if cmd_id >= 0:
            self._pending_set_ids.add(cmd_id)
        return cmd_id

    def set_zwo_preview(self, mode: str, brightness: float) -> None:
        if self._capture is not None and self._capture.is_open():
            self._capture.set_preview(mode, brightness)
        self._zwo_settings = dataclasses.replace(
            self._zwo_settings, preview_mode=mode, brightness=brightness
        )
        zwo_settings_store.save_settings(self._zwo_settings)
        self.settings_changed.emit()
        self.state_changed.emit()

    def take_darks(self, frames: int) -> int:
        if self._camera_kind != "zwo":
            return -1
        if self._zwo_video_active or self._zwo_starting_video:
            self.status.emit("Cannot take dark frames while video is running", "warn")
            return -1
        if self._capture is None or not self._capture.is_open():
            self.status.emit("Cannot take dark frames: camera is not open", "warn")
            return -1
        return self._capture.take_darks(str(paths.ZWO_DARKS_DIR), frames)

    # ---- session control ---------------------------------------------------

    def set_start_guard(self, fn: Callable[[], bool] | None) -> None:
        """Register a veto on ``start()``: when ``fn()`` is False nothing starts.

        The owner of other state (the cup log) decides whether a session may begin;
        the recorder only honours the answer.
        """
        self._start_guard = fn

    def set_stop_guard(self, fn: Callable[[], bool] | None) -> None:
        """Register a veto on ``stop()``: when ``fn()`` is False the session keeps going.

        ``stop(force=True)`` skips it: the disk auto-stop must not wait on a dialog
        while the disk fills.
        """
        self._stop_guard = fn

    def start(self) -> bool:
        if self._recording:
            return False
        if self._start_guard is not None and not self._start_guard():
            return False

        now = datetime.now()
        self._session_id = now.strftime("session_%Y%m%d_%H%M%S")
        self._folder     = os.path.join(_logs_dir(), self._session_id)
        photos_dir = os.path.join(self._folder, "photos")
        try:
            os.makedirs(photos_dir, exist_ok=True)
        except OSError as exc:
            self.status.emit(f"Cannot create session folder: {exc}", "fault")
            return False

        # Capture the timebase anchor.
        self._t0_mono = time.perf_counter()
        self._t0_wall = datetime.now(timezone.utc)

        # Reset counters.
        self._frame_index_current = -1
        self._frames_written  = 0
        self._frames_dropped  = 0
        self._segments_done   = 0
        self._transcode_pending = 0
        self._segment_meta    = []
        self._photo_list      = []
        self._notes_count     = 0
        self._photo_index     = 0
        self._vacuum_logger   = None
        self._vacuum_files    = []
        self._next_segment_index = 0
        self._video_runs      = []
        self._ffmpeg_warning_logged = False
        self._zwo_next_segment = 0
        self._zwo_next_frame = 0
        self._zwo_runs = []
        self._zwo_segments = []
        self._zwo_video_active = False
        self._zwo_starting_video = False
        self._zwo_stopping_video = False

        # Open CSV writer and events.csv.
        self._csv_writer = CsvLogWriter(self._folder, "data")
        try:
            ef = open(os.path.join(self._folder, "events.csv"),
                      "w", newline="", encoding="utf-8")
        except OSError as exc:
            self.status.emit(f"Cannot open events.csv: {exc}", "fault")
            return False
        self._events_file = ef
        self._events_writer = csv.writer(ef, lineterminator="\n")
        self._events_writer.writerow(
            ["t_rel_s", "wall_utc", "frame_index", "event", "detail"])
        ef.flush()

        # Write initial CSV row and start timer.
        self._csv_timer.setInterval(self._csv_interval_s * 1000)
        self._csv_timer.start()
        self._write_csv_row()

        self._recording = True
        self._write_event("session_start",
                          f"csv_interval_s={self._csv_interval_s} "
                          f"record_fps={self._record_fps}")

        self._write_manifest()
        self.state_changed.emit()
        self.session_started.emit(self._folder)
        return True

    def stop(self, force: bool = False) -> None:
        if not self._recording:
            return
        if not force and self._stop_guard is not None and not self._stop_guard():
            return
        self.stop_video()
        if self._capture is not None and self._zwo_stopping_video:
            end = time.monotonic() + 5.0
            app = QApplication.instance()
            while self._zwo_stopping_video and time.monotonic() < end:
                if app is not None:
                    app.processEvents()
                time.sleep(0.005)

        self._recording = False
        self._csv_timer.stop()
        self._write_csv_row()   # final row
        self._write_event("session_stop", "")

        # Wait for pending transcodes with a progress dialog.
        if self._transcode_queue is not None and self._transcode_pending > 0:
            dlg = QProgressDialog(
                "Transcoding final segment(s)…", "Skip", 0, 0)
            dlg.setWindowTitle("Session Recorder")
            dlg.setMinimumDuration(0)
            dlg.setValue(0)

            def _on_done(avi, mp4, ok, msg):
                if self._transcode_pending <= 0:
                    dlg.accept()

            self._transcode_queue.finished_one.connect(_on_done)
            dlg.exec()
            self._transcode_queue.finished_one.disconnect(_on_done)

        if self._transcode_queue is not None:
            self._transcode_queue.stop(wait=True)
            self._transcode_queue = None

        # Close CSV files.
        if self._csv_writer is not None:
            self._csv_writer.close()
            self._csv_writer = None
        if self._events_file is not None:
            self._events_file.close()
            self._events_file   = None
            self._events_writer = None
        if self._vacuum_logger is not None:
            self._vacuum_logger.close()
            self._vacuum_logger = None

        self._write_manifest()
        self.state_changed.emit()
        self.session_stopped.emit(self._folder)

    def is_recording(self) -> bool:
        return self._recording

    def on_vacuum_state(self, state: object) -> None:
        """Ingest a 1 Hz VacuumState snapshot into the session's vacuum log (ADR 0004)."""
        if not self._recording or state is None:
            return

        if self._vacuum_logger is None:
            self._open_session_vacuum_logger(state)
            if self._vacuum_logger is not None:
                self._vacuum_logger.write_row(state)
            return

        ok = self._vacuum_logger.write_row(state)
        if not ok:
            # Gauge set changed mid-session: close old file and open next part
            self._vacuum_logger.close()
            self._open_session_vacuum_logger(state)
            if self._vacuum_logger is not None:
                self._vacuum_logger.write_row(state)

    def _open_session_vacuum_logger(self, state: object) -> None:
        labels = gauge_labels(state)
        logger = VacuumLogger(labels, output_dir=Path(self._folder), file_stem="vacuum")
        comment_lines = vacuum_comment_lines(state, datetime.now(timezone.utc))
        logger.write_header_comment(comment_lines)
        self._vacuum_logger = logger
        csv_name = Path(logger.csv_path).name
        if csv_name not in self._vacuum_files:
            self._vacuum_files.append(csv_name)
        self._write_manifest()

    # ---- actions -----------------------------------------------------------

    def take_photo(self, burst: int = 1) -> str | None:
        """Save a photo + JSON sidecar from the current camera frame.

        Works with no active session (images land in logs/photos/).
        The sidecar captures the current beamline state at the moment of
        the photo so the image is interpretable without a running session.
        """
        if self._camera_kind == "zwo":
            return self._take_zwo_photo(burst)
        return self._take_usb_photo()

    def _take_usb_photo(self) -> str | None:
        try:
            import cv2
        except ImportError:
            return None

        frame = self._camera.latest_frame()
        if frame is None:
            return None

        if self._recording:
            photo_dir = os.path.join(self._folder, "photos")
        else:
            photo_dir = os.path.join(_logs_dir(), "photos")
            os.makedirs(photo_dir, exist_ok=True)

        name = f"photo_{self._photo_index:03d}.png"
        path = os.path.join(photo_dir, name)
        cv2.imwrite(path, frame)
        rel = f"photos/{name}"
        self._photo_index += 1

        # Write JSON sidecar alongside the PNG.
        snap = self._snapshot_fn()
        sidecar = {
            "timestamp": datetime.now().isoformat(),
            "media_file": name,
            "type": "photo",
            "beamline": snap,
        }
        # dump_json, not json.dump: the old writer emitted bare NaN (not JSON —
        # nothing outside Python can read it) and turned any numpy array it met
        # into its repr string via default=str.  See snapshot_json.py.
        sidecar_path = os.path.splitext(path)[0] + ".json"
        if not dump_json(sidecar_path, sidecar):
            self._write_event("sidecar_failed", os.path.basename(sidecar_path))

        if self._recording:
            self._photo_list.append(rel)
            self._write_event("photo", rel)
            self._write_manifest()
            self.state_changed.emit()

        self.photo_taken.emit(path)
        return path

    def _take_zwo_photo(self, burst: int = 1) -> None:
        if self._capture is None or not self._capture.is_open():
            return None
        if self._recording:
            photo_dir = os.path.join(self._folder, "photos")
        else:
            photo_dir = os.path.join(_logs_dir(), "photos")
            os.makedirs(photo_dir, exist_ok=True)

        snap = self._snapshot_fn()
        cmd_id = self._capture.photo(photo_dir, burst)
        if cmd_id >= 0:
            self._zwo_photo_snaps[cmd_id] = snap
        return None

    def add_note(self, text: str) -> None:
        if not self._recording:
            return
        self._notes_count += 1
        self._write_event("note", text)

    def open_session_folder(self) -> None:
        path = self._folder if self._folder else _logs_dir()
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])

    def state(self) -> dict:
        elapsed = 0.0
        if self._recording and self._t0_wall is not None:
            elapsed = time.perf_counter() - self._t0_mono
        free = 0
        if self._folder:
            try:
                free = shutil.disk_usage(self._folder).free
            except OSError:
                pass
        bytes_written = (self._video_recorder.current_file_size_bytes
                         if self._video_recorder is not None else 0)
        return {
            "recording":         self._recording,
            "session_id":        self._session_id,
            "folder":            self._folder,
            "elapsed_s":         elapsed,
            "csv_rows":          self._csv_writer.row_count if self._csv_writer else 0,
            "frames_written":    self._frames_written,
            "frames_dropped":    self._frames_dropped,
            "segments_done":     self._segments_done,
            "transcode_pending": self._transcode_pending,
            "disk_free_bytes":   free,
            "bytes_written":     bytes_written,
            "video_active":      (self._video_recorder is not None) or self._zwo_video_active,
            "ffmpeg_found":      self._ffmpeg_path is not None,
            "master_codec":      self._master_codec,
            "camera_kind":       self._camera_kind,
            "zwo_open":          bool(self._capture is not None and self._capture.is_open()),
            "zwo_recording":     self._zwo_video_active,
            "zwo_status":        dict(self._zwo_status),
            "zwo_info":          dict(self._zwo_info),
            "zwo_settings":      dict(self._zwo_settings_applied),
        }

    # ---- video control (ADR 0004 decision 7) -------------------------------

    def start_video(self) -> bool:
        """Start video recording during an active session.

        Refuses (returns False, emits status warning) when not recording,
        video is already running, or camera is not open.
        """
        if self._camera_kind == "zwo":
            return self._start_zwo_video()
        return self._start_usb_video()

    def _start_zwo_video(self) -> bool:
        if not self._recording:
            self.status.emit("Cannot start video: session is not recording", "warn")
            return False
        if self._zwo_video_active or self._zwo_starting_video:
            self.status.emit("Cannot start video: video is already running", "warn")
            return False
        if self._capture is None or not self._capture.is_open():
            self.status.emit("Cannot start video: camera is not open", "warn")
            return False

        t0_wall_utc = self._derive_wall(0.0)
        cmd_id = self._capture.start_recording(
            self._folder,
            session_t0_utc=t0_wall_utc,
            first_segment_index=self._zwo_next_segment,
            first_frame_index=self._zwo_next_frame,
        )
        if cmd_id < 0:
            return False
        self._pending_start_recording_id = cmd_id
        self._zwo_starting_video = True
        return True

    def _start_usb_video(self) -> bool:
        if not self._recording:
            self.status.emit("Cannot start video: session is not recording", "warn")
            return False
        if self._video_recorder is not None:
            self.status.emit("Cannot start video: video is already running", "warn")
            return False
        if not self._camera.is_open():
            self.status.emit("Cannot start video: camera is not open", "warn")
            return False
        w, h = self._camera.actual_size()
        if w == 0 or h == 0:
            self.status.emit("Cannot start video: camera frame size is invalid", "warn")
            return False

        crf, preset = QUALITY_PRESETS[self._quality_label]
        rec = VideoRecorder(
            self._folder,
            width=w, height=h,
            record_fps=self._record_fps,
            segment_seconds=self._segment_seconds,
            segment_max_bytes=SEGMENT_MAX_BYTES,
            master_codec=self._master_codec,
            first_segment_index=self._next_segment_index,
        )
        rec.segment_closed.connect(self._on_segment_closed)
        rec.error.connect(self._on_video_error)

        # Move to the camera thread so AVI writes happen there.
        cam_thread = self._camera._thread
        if cam_thread is not None:
            rec.moveToThread(cam_thread)

        if not rec.start():
            return False

        # Connect frame_ready with DirectConnection so offer_frame() executes
        # on the camera thread without queuing overhead.
        self._camera.frame_ready.connect(
            self._on_frame, Qt.ConnectionType.DirectConnection)

        self._video_recorder = rec

        # Start transcode queue — only if ffmpeg is actually available.
        if self._ffmpeg_path is not None:
            if self._transcode_queue is None:
                tq = TranscodeQueue(self._ffmpeg_path, crf=crf, preset=preset)
                tq.finished_one.connect(self._on_transcode_done)
                tq.progress.connect(self._on_transcode_progress)
                tq.start()
                self._transcode_queue = tq
        else:
            self._transcode_queue = None
            if not self._ffmpeg_warning_logged:
                self._write_event("transcode_disabled",
                                  "ffmpeg not found — keeping .avi only")
                self._ffmpeg_warning_logged = True

        self._video_runs.append({
            "started_t_rel": self._t_rel(),
            "stopped_t_rel": None,
            "first_segment": self._next_segment_index,
            "last_segment": None,
        })
        self._write_event("video_started", "")
        self._write_manifest()
        self.state_changed.emit()
        return True

    def stop_video(self, detail: str = "") -> None:
        """Stop video recording if active without ending the session."""
        if self._camera_kind == "zwo" or self._zwo_video_active:
            self._stop_zwo_video(detail)
        else:
            self._stop_usb_video(detail)

    def _stop_zwo_video(self, detail: str = "") -> None:
        if not self._zwo_video_active and not self._zwo_starting_video:
            return
        self._zwo_starting_video = False
        self._zwo_stopping_video = True
        if self._zwo_runs and self._zwo_runs[-1]["stopped_t_rel"] is None:
            self._zwo_runs[-1]["stopped_t_rel"] = self._t_rel()
        if self._capture is not None and self._capture.is_open():
            self._capture.stop_recording()
        self._write_event("video_stopped", detail)
        self._write_manifest()
        self.state_changed.emit()

    def _stop_usb_video(self, detail: str = "") -> None:
        if self._video_recorder is None:
            return

        rec = self._video_recorder
        self._video_recorder = None

        try:
            self._camera.frame_ready.disconnect(self._on_frame)
        except (RuntimeError, TypeError):
            pass

        last_seg = rec.segment_index
        if self._video_runs and self._video_runs[-1]["stopped_t_rel"] is None:
            self._video_runs[-1]["stopped_t_rel"] = self._t_rel()
            self._video_runs[-1]["last_segment"] = last_seg

        self._next_segment_index = last_seg + 1

        rec.stop()

        self._write_event("video_stopped", detail)
        self._write_manifest()
        self.state_changed.emit()

    def _on_frame(self, frame, t_mono: float) -> None:
        """Called on camera thread via DirectConnection."""
        if self._video_recorder is None or not self._recording:
            return
        t_rel = t_mono - self._t0_mono
        wall  = self._derive_wall(t_rel)
        idx   = self._video_recorder.offer_frame(frame, t_rel, wall)
        if idx is not None:
            self._frames_written += 1
            self._frame_index_current = idx

    def _derive_wall(self, t_rel_s: float) -> str:
        """Derive UTC wall time from monotonic offset — never calls datetime.now()."""
        wall = self._t0_wall + timedelta(seconds=t_rel_s)
        return wall.strftime("%Y-%m-%dT%H:%M:%S.") + f"{wall.microsecond // 1000:03d}Z"

    def _on_segment_closed(self, avi_path: str, seg_idx: int) -> None:
        self._segments_done += 1
        seg_base = os.path.basename(avi_path)
        # No ffmpeg → no .mp4 will ever exist, so don't promise one in the
        # manifest.  Convert offline with converter\avi2mp4.py instead.
        _transcoding = self._transcode_queue is not None
        mp4_name = os.path.splitext(seg_base)[0] + ".mp4" if _transcoding else None
        self._segment_meta.append({
            "index":            seg_idx,
            "avi":              seg_base,
            "mp4":              mp4_name,
            "frames":           None,
            "transcode":        "pending" if _transcoding else "skipped",
        })
        self._write_event("segment_closed", f"{seg_base}")
        self._write_manifest()

        # Disk guard.
        try:
            free = shutil.disk_usage(self._folder).free
        except OSError:
            free = math.inf
        if free < DISK_AUTOSTOP_BYTES:
            self._write_event("auto_stop", f"disk_free={free}")
            self.stop(force=True)
            return
        if free < DISK_WARN_BYTES:
            self._write_event("disk_low", f"disk_free={free}")
            self.status.emit(f"Disk low: {free // 1024**3} GB free", "warn")

        if self._transcode_queue is not None:
            self._transcode_pending += 1
            self._transcode_queue.enqueue(avi_path)
        self.state_changed.emit()

    def _on_transcode_done(self, avi: str, mp4: str, ok: bool, msg: str) -> None:
        self._transcode_pending = max(0, self._transcode_pending - 1)
        event = "transcode_ok" if ok else "transcode_failed"
        self._write_event(event, f"{os.path.basename(mp4)} {msg}")
        # Update segment manifest.
        base = os.path.basename(avi)
        for seg in self._segment_meta:
            if seg["avi"] == base:
                seg["transcode"] = "ok" if ok else "failed"
                if ok and os.path.exists(mp4):
                    try:
                        seg["mp4_bytes"] = os.path.getsize(mp4)
                    except OSError:
                        pass
        self._write_manifest()
        self.state_changed.emit()

    def _on_transcode_progress(self, pending: int) -> None:
        self._transcode_pending = pending
        self.state_changed.emit()

    def _on_video_error(self, msg: str) -> None:
        if self._video_recorder is not None:
            self.stop_video(detail=f"video_error: {msg}")
        self._write_event("camera_lost", msg)
        self.status.emit(f"Video error: {msg}", "warn")
        self.state_changed.emit()

    def _on_camera_format_ready(self, fourcc: str) -> None:
        self._camera_fourcc_actual    = fourcc
        self._camera_fourcc_requested = self._camera.requested_fourcc()

    def _on_camera_closed(self) -> None:
        if not self._recording:
            return
        if self._video_recorder is not None:
            self.stop_video(detail="camera_closed")
        self._write_event("camera_lost", "camera closed during session")
        self.status.emit("Camera lost — continuing CSV only", "warn")
        self.state_changed.emit()

    def _on_camera_error(self, msg: str) -> None:
        if not self._recording:
            return
        if self._video_recorder is not None:
            self.stop_video(detail="camera_closed")
        self._write_event("camera_lost", msg)
        self.status.emit(f"Camera lost ({msg}) — continuing CSV only", "warn")
        self.state_changed.emit()

    # ---- internal: ZWO CaptureClient slots ---------------------------------

    def _on_zwo_hello(self, msg: dict) -> None:
        if self._zwo_open_on_hello:
            self._zwo_open_on_hello = False
            if self._capture is not None:
                self._capture.open(0)

    def _on_zwo_opened(self, msg: dict) -> None:
        self._zwo_info = dict(msg)
        if self._capture is None:
            return
        if self._zwo_settings.roi is not None:
            x, y, w, h = self._zwo_settings.roi
            roi = {"x": x, "y": y, "width": w, "height": h}
        else:
            sw = msg.get("sensor_width", 0)
            sh = msg.get("sensor_height", 0)
            roi = {"x": 0, "y": 0, "width": sw, "height": sh}

        cmd_id = self._capture.apply(
            exposure_us=self._zwo_settings.exposure_us,
            gain=self._zwo_settings.gain,
            bit_depth=self._zwo_settings.bit_depth,
            keep_one_in_n=self._zwo_settings.keep_one_in_n,
            roi=roi,
        )
        if cmd_id >= 0:
            self._pending_set_ids.add(cmd_id)

        self._capture.set_preview(
            mode=self._zwo_settings.preview_mode,
            brightness=self._zwo_settings.brightness,
        )
        self.state_changed.emit()

    def _on_zwo_closed(self) -> None:
        self._zwo_info = {}
        self.state_changed.emit()

    def _on_zwo_status(self, msg: dict) -> None:
        self._zwo_status = {k: v for k, v in msg.items() if k != "type"}

    def _on_zwo_event(self, event: str, detail: str) -> None:
        self._write_event(f"zwo_{event}", detail)
        if event == "recording_stopped" and detail != "stop":
            if self._zwo_video_active or self._zwo_starting_video:
                self._zwo_video_active = False
                self._zwo_starting_video = False
                self._zwo_stopping_video = False
                if self._zwo_runs and self._zwo_runs[-1]["stopped_t_rel"] is None:
                    self._zwo_runs[-1]["stopped_t_rel"] = self._t_rel()
                self._write_manifest()
                self.status.emit(f"ZWO recording stopped ({detail}) — continuing CSV only", "warn")
                self.state_changed.emit()

    def _on_zwo_exited(self, code: int) -> None:
        if self._zwo_video_active or self._zwo_starting_video:
            self._zwo_video_active = False
            self._zwo_starting_video = False
            self._zwo_stopping_video = False
            if self._zwo_runs and self._zwo_runs[-1]["stopped_t_rel"] is None:
                self._zwo_runs[-1]["stopped_t_rel"] = self._t_rel()
            self._write_event("zwo_capture_lost", f"exit code {code}")
            self.status.emit("ZWO camera lost — continuing CSV only", "warn")
            self._write_manifest()
            self.state_changed.emit()
        self._zwo_info = {}

    def _on_zwo_reply(self, reply_to: int, msg: dict) -> None:
        mtype = msg.get("type")
        if reply_to == self._pending_start_recording_id:
            self._pending_start_recording_id = -1
            self._zwo_starting_video = False
            if mtype == "ok":
                self._zwo_video_active = True
                self._zwo_runs.append({
                    "started_t_rel": self._t_rel(),
                    "stopped_t_rel": None,
                    "first_segment": self._zwo_next_segment,
                    "last_segment": None,
                })
                self._write_event("video_started", "zwo")
                self._write_manifest()
                self.state_changed.emit()
            elif mtype == "error":
                self._zwo_video_active = False
                err_msg = msg.get("message", "")
                self.status.emit(f"Cannot start ZWO video: {err_msg}", "warn")
                self.state_changed.emit()
            return

        if mtype == "ok":
            if reply_to in self._pending_set_ids:
                self._pending_set_ids.remove(reply_to)
                if "settings" in msg:
                    self._zwo_settings_applied = dict(msg["settings"])
                    s = msg["settings"]
                    roi_tuple = None
                    if "roi" in s and s["roi"] is not None:
                        r = s["roi"]
                        roi_tuple = (r["x"], r["y"], r["width"], r["height"])
                    self._zwo_settings = dataclasses.replace(
                        self._zwo_settings,
                        exposure_us=s.get("exposure_us", self._zwo_settings.exposure_us),
                        gain=s.get("gain", self._zwo_settings.gain),
                        bit_depth=s.get("bit_depth", self._zwo_settings.bit_depth),
                        keep_one_in_n=s.get("keep_one_in_n", self._zwo_settings.keep_one_in_n),
                        roi=roi_tuple if "roi" in s else self._zwo_settings.roi,
                    )
                    zwo_settings_store.save_settings(self._zwo_settings)
                    self.settings_changed.emit()
                    self.state_changed.emit()
            if "next_segment_index" in msg:
                self._zwo_video_active = False
                self._zwo_stopping_video = False
                self._zwo_next_segment = msg["next_segment_index"]
                self._zwo_next_frame = msg.get("next_frame_index", self._zwo_next_frame)
                if self._zwo_runs and self._zwo_runs[-1]["last_segment"] is None:
                    if self._zwo_next_segment > self._zwo_runs[-1]["first_segment"]:
                        self._zwo_runs[-1]["last_segment"] = self._zwo_next_segment - 1
                    else:
                        self._zwo_runs[-1]["last_segment"] = self._zwo_runs[-1]["first_segment"]
                for seg in msg.get("segments", []):
                    if seg not in self._zwo_segments:
                        self._zwo_segments.append(seg)
                self._write_manifest()
                self.state_changed.emit()
            if "files" in msg:
                files = msg["files"]
                if files:
                    snap = self._zwo_photo_snaps.pop(reply_to, None)
                    if snap is None:
                        snap = self._snapshot_fn()
                    rel_names = []
                    for fpath in files:
                        base_name = os.path.basename(fpath)
                        sidecar_path = os.path.splitext(fpath)[0] + ".json"
                        sidecar = {
                            "timestamp": datetime.now().isoformat(),
                            "media_file": base_name,
                            "type": "photo",
                            "beamline": snap,
                            "camera": "zwo",
                        }
                        dump_json(sidecar_path, sidecar)
                        if self._recording:
                            rel = f"photos/{base_name}"
                            self._photo_list.append(rel)
                            rel_names.append(rel)

                    if self._recording and rel_names:
                        self._write_event("photo", ", ".join(rel_names))
                        self._write_manifest()
                        self.state_changed.emit()

                    self.photo_taken.emit(files[0])

    # ---- internal: CSV / events / manifest ---------------------------------

    def _t_rel(self) -> float:
        return time.perf_counter() - self._t0_mono

    def _write_csv_row(self) -> None:
        if self._csv_writer is None:
            return
        t_rel = self._t_rel()
        wall  = self._derive_wall(t_rel)
        row: dict = {
            "t_rel_s":  f"{t_rel:.6f}",
            "wall_utc": wall,
            "frame_index": self._frame_index_current if self._frame_index_current >= 0 else "",
        }
        snap = self._snapshot_fn()
        row.update(flatten(snap))

        old_parts = self._csv_writer.parts[:]
        self._csv_writer.write(row)
        new_parts = self._csv_writer.parts
        if len(new_parts) > len(old_parts):
            self._write_event("csv_schema_roll", f"new part: {new_parts[-1]}")
            self._write_manifest()

        self.state_changed.emit()

    def _write_event(self, event: str, detail: str) -> None:
        if self._events_writer is None:
            return
        t_rel = self._t_rel()
        wall  = self._derive_wall(t_rel)
        fi    = self._frame_index_current if self._frame_index_current >= 0 else ""
        self._events_writer.writerow([f"{t_rel:.6f}", wall, fi, event, detail])
        self._events_file.flush()

    def _write_manifest(self) -> None:
        if not self._folder:
            return
        t_rel = self._t_rel()
        stopped_utc = None
        if not self._recording and self._t0_wall is not None:
            stopped_utc = self._derive_wall(t_rel)

        manifest: dict = {
            "session_id":   self._session_id,
            "started_utc":  self._derive_wall(0.0) if self._t0_wall else None,
            "stopped_utc":  stopped_utc,
            "duration_s":   t_rel if not self._recording else None,
            "t0_mono":      self._t0_mono,
            "csv": {
                "interval_s": self._csv_interval_s,
                "rows":       self._csv_writer.row_count if self._csv_writer else 0,
                "parts":      self._csv_writer.parts if self._csv_writer else [],
            },
            "video": {
                "enabled":                (len(self._video_runs) > 0
                                            or self._video_recorder is not None
                                            or len(self._segment_meta) > 0),
                "runs":                   [dict(r) for r in self._video_runs],
                "record_fps":             self._record_fps,
                "segment_seconds":        self._segment_seconds,
                "segment_max_bytes":      SEGMENT_MAX_BYTES,
                "master_codec":           (self._video_recorder.master_codec
                                           if self._video_recorder is not None
                                           else self._master_codec),
                "keep_master":            True,
                "frames_written":         self._frames_written,
                "frames_dropped":         self._frames_dropped,
                "segments":               self._segment_meta,
                "camera_fourcc":          self._camera_fourcc_actual,
                "camera_fourcc_requested": self._camera_fourcc_requested,
            },
            "photos": self._photo_list,
            "ffmpeg": {
                "found":   self._ffmpeg_path is not None,
                "path":    self._ffmpeg_path,
                "version": self._ffmpeg_version,
            },
            "notes": self._notes_count,
        }
        if self._vacuum_files or self._vacuum_logger is not None:
            manifest["vacuum"] = {
                "files": list(self._vacuum_files),
            }
        if self._zwo_runs:
            manifest["zwo"] = {
                "runs": [dict(r) for r in self._zwo_runs],
                "segments": list(self._zwo_segments),
                "camera": {
                    "model": self._zwo_info.get("model", ""),
                    "serial": self._zwo_info.get("serial", ""),
                    "sdk_version": self._zwo_info.get("sdk_version", ""),
                    "bayer_pattern": self._zwo_info.get("bayer_pattern", ""),
                },
            }
        # Strict + atomic: a manifest is rewritten on every segment close, so a
        # crash mid-write used to be able to leave a truncated session.json as
        # the only record of the run.  dump_json writes to a temp name and
        # renames, and refuses to emit NaN.
        path = os.path.join(self._folder, "session.json")
        dump_json(path, manifest)
