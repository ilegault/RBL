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
irreducible minimum.

The timebase: at session start we capture both time.perf_counter() and
datetime.now(UTC) and derive every subsequent wall-clock time from the monotonic
clock.  This means an NTP correction during an 8-hour run cannot reorder rows
or create a backwards jump between the CSV and the video.  See §4 of the design
doc for the reasoning.

Threading:
  GUI thread   : this class, CsvLogWriter, events.csv, session.json, QTimer
  Camera thread: VideoRecorder (moved there via moveToThread), frames.csv
  Transcode thread: TranscodeQueue

The VideoRecorder lives on the camera thread so AVI writes never touch the GUI
thread.  offer_frame() is connected to CameraSource.frame_ready with
DirectConnection so it executes on the camera thread without a queue round-trip.
"""
import csv
import math
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import (
    QObject,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import QProgressDialog

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
from rbl.services.csv_log_writer import CsvLogWriter, flatten
from rbl.services.snapshot_json import dump_json
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
    """

    state_changed    = Signal()
    settings_changed = Signal()
    status           = Signal(str, str)      # message, theme colour role
    session_started  = Signal(str)           # session folder path
    session_stopped  = Signal(str)
    photo_taken      = Signal(str)           # absolute path of saved PNG

    def __init__(self, snapshot_fn, camera: CameraSource, parent=None):
        super().__init__(parent)
        self._snapshot_fn = snapshot_fn
        self._camera      = camera

        # Settings
        self._csv_interval_s   = CSV_INTERVAL_DEFAULT_S
        self._record_fps       = RECORD_FPS_DEFAULT
        self._segment_seconds  = SEGMENT_SECONDS_DEFAULT
        self._quality_label    = QUALITY_DEFAULT
        self._master_codec     = MASTER_CODEC_DEFAULT
        self._video_enabled    = False

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

        # Photos
        self._photo_index: int = 0

        # Manifest state
        self._segment_meta: list[dict] = []
        self._photo_list:   list[str]  = []
        self._notes_count:  int        = 0

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
        if self._recording:
            return
        self._record_fps = v
        self.settings_changed.emit()

    def set_segment_seconds(self, v: int) -> None:
        if self._recording:
            return
        self._segment_seconds = v
        self.settings_changed.emit()

    def set_quality(self, label: str) -> None:
        if self._recording:
            return
        if label in QUALITY_PRESETS:
            self._quality_label = label
            self.settings_changed.emit()

    def set_master_codec(self, label: str) -> None:
        from rbl.config.recording_config import MASTER_CODECS
        if self._recording:
            return
        if label in MASTER_CODECS:
            self._master_codec = label
            self.settings_changed.emit()

    def set_video_enabled(self, on: bool) -> None:
        if self._recording:
            return
        self._video_enabled = on
        self.settings_changed.emit()

    # ---- session control ---------------------------------------------------

    def start(self) -> bool:
        if self._recording:
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

        # Video (optional).
        self._start_video()

        # Write initial CSV row and start timer.
        self._csv_timer.setInterval(self._csv_interval_s * 1000)
        self._csv_timer.start()
        self._write_csv_row()

        self._recording = True
        self._write_event("session_start",
                          f"csv_interval_s={self._csv_interval_s} "
                          f"video={self._video_recorder is not None} "
                          f"record_fps={self._record_fps}")
        self._write_manifest()
        self.state_changed.emit()
        self.session_started.emit(self._folder)
        return True

    def stop(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._csv_timer.stop()
        self._write_csv_row()   # final row
        self._write_event("session_stop", "")

        # Stop video.
        if self._video_recorder is not None:
            self._video_recorder.stop()
            self._video_recorder = None

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

        self._write_manifest()
        self.state_changed.emit()
        self.session_stopped.emit(self._folder)

    def is_recording(self) -> bool:
        return self._recording

    # ---- actions -----------------------------------------------------------

    def take_photo(self) -> "str | None":
        """Save a PNG + JSON sidecar from the current camera frame.

        Works with no active session (images land in logs/photos/).
        The sidecar captures the current beamline state at the moment of
        the photo so the image is interpretable without a running session.
        """
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
            "video_active":      self._video_recorder is not None,
            "ffmpeg_found":      self._ffmpeg_path is not None,
            "master_codec":      self._master_codec,
        }

    # ---- internal: video ---------------------------------------------------

    def _start_video(self) -> None:
        if not self._video_enabled:
            return
        if not self._camera.is_open():
            return
        w, h = self._camera.actual_size()
        if w == 0 or h == 0:
            return

        crf, preset = QUALITY_PRESETS[self._quality_label]
        rec = VideoRecorder(
            self._folder,
            width=w, height=h,
            record_fps=self._record_fps,
            segment_seconds=self._segment_seconds,
            segment_max_bytes=SEGMENT_MAX_BYTES,
            master_codec=self._master_codec,
        )
        rec.segment_closed.connect(self._on_segment_closed)
        rec.error.connect(self._on_video_error)

        # Move to the camera thread so AVI writes happen there.
        cam_thread = self._camera._thread
        if cam_thread is not None:
            rec.moveToThread(cam_thread)

        if not rec.start():
            return

        # Connect frame_ready with DirectConnection so offer_frame() executes
        # on the camera thread without queuing overhead.
        self._camera.frame_ready.connect(
            self._on_frame, Qt.ConnectionType.DirectConnection)

        self._video_recorder   = rec

        # Start transcode queue — only if ffmpeg is actually available.
        # Without this guard the queue starts anyway, every closed segment is
        # enqueued, and each one immediately fails with "ffmpeg not found",
        # writing a transcode_failed event per segment into the session log and
        # leaving dead .mp4 names in the manifest.  The lab build ships no
        # ffmpeg by design, so that is the normal case, not an error case.
        if self._ffmpeg_path is not None:
            tq = TranscodeQueue(self._ffmpeg_path, crf=crf, preset=preset)
            tq.finished_one.connect(self._on_transcode_done)
            tq.progress.connect(self._on_transcode_progress)
            tq.start()
            self._transcode_queue = tq
        else:
            self._transcode_queue = None
            self._write_event("transcode_disabled",
                              "ffmpeg not found — keeping .avi only")

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
            self.stop()
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
        self._write_event("camera_lost", msg)
        self._video_recorder = None
        self.status.emit(f"Video error: {msg}", "warn")
        self.state_changed.emit()

    def _on_camera_format_ready(self, fourcc: str) -> None:
        self._camera_fourcc_actual    = fourcc
        self._camera_fourcc_requested = self._camera.requested_fourcc()

    def _on_camera_closed(self) -> None:
        if not self._recording:
            return
        if self._video_recorder is not None:
            self._camera.frame_ready.disconnect(self._on_frame)
            self._video_recorder.stop()
            self._video_recorder = None
        self._write_event("camera_lost", "camera closed during session")
        self.status.emit("Camera lost — continuing CSV only", "warn")
        self.state_changed.emit()

    def _on_camera_error(self, msg: str) -> None:
        if not self._recording:
            return
        if self._video_recorder is not None:
            try:
                self._camera.frame_ready.disconnect(self._on_frame)
            except RuntimeError:
                pass
            self._video_recorder.stop()
            self._video_recorder = None
        self._write_event("camera_lost", msg)
        self.status.emit(f"Camera lost ({msg}) — continuing CSV only", "warn")
        self.state_changed.emit()

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

        manifest = {
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
                "enabled":                (self._video_recorder is not None
                                            or len(self._segment_meta) > 0),
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
        # Strict + atomic: a manifest is rewritten on every segment close, so a
        # crash mid-write used to be able to leave a truncated session.json as
        # the only record of the run.  dump_json writes to a temp name and
        # renames, and refuses to emit NaN.
        path = os.path.join(self._folder, "session.json")
        dump_json(path, manifest)
