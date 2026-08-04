"""
camera_widget.py
USB camera live-view with photo/video capture and JSON metadata sidecar.

Captures folder is created at:
  - dist/RBL/captures/   when running as a PyInstaller one-folder bundle
  - <project_root>/captures/  in development

Each capture (photo or video) is paired with a same-name .json sidecar
containing an ISO timestamp, the filename, and a full snapshot of every
beamline readout at the moment of capture.

If opencv-python is not installed the widget renders a "cv2 not available"
placeholder — the rest of the application continues to run normally.
"""
import dataclasses
import json
import math
import os
import sys
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSizePolicy, QSpinBox,
)

from rbl.gui import theme

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def captures_dir() -> str:
    """Return (and create if needed) the captures directory.

    Sits beside the executable in a one-folder PyInstaller build so the
    folder is always at a predictable, user-visible location in the
    distribution.
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        # development: project root (two packages up from this file)
        base = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    path = os.path.join(base, "captures")
    os.makedirs(path, exist_ok=True)
    return path


def _serialise(obj):
    """Default JSON serialiser: handle dataclasses, NaN/Inf, unknown types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return str(obj)
        return obj
    return str(obj)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

# Common resolution presets (w, h).  OpenCV clamps to the nearest the camera
# actually supports, so offering too-high values is harmless.
_RES_PRESETS = [
    (640, 480, "640x480"),
    (800, 600, "800x600"),
    (1280, 720, "1280x720 (HD)"),
    (1920, 1080, "1920x1080 (FHD)"),
    (2560, 1440, "2560x1440 (QHD)"),
    (3840, 2160, "3840x2160 (4K)"),
    (0, 0, "Max"),  # sentinel: request 10000x10000
]


class CameraWidget(QGroupBox):
    """USB camera live-view with photo/video capture.

    Call ``set_metadata_provider(fn)`` where *fn* is a zero-argument callable
    that returns a dict of the current beamline state; the widget stores this
    dict in every sidecar JSON it writes.
    """

    _FRAME_INTERVAL_MS = 33   # ~30 FPS poll

    def __init__(self, parent=None):
        super().__init__("Camera", parent)
        self._cap = None
        self._current_frame = None
        self._recording = False
        self._video_writer = None
        self._rec_name = ""
        self._rec_path = ""
        self._rec_start_meta: dict = {}
        self._rec_meta_log: list[dict] = []  # timestamped metadata snapshots
        self._get_metadata = None   # () -> dict

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(self._FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._grab_frame)

        self._meta_timer = QTimer(self)
        self._meta_timer.setInterval(self._spin_meta.value() * 1000)
        self._meta_timer.timeout.connect(self._log_metadata)

        if not _CV2_OK:
            self._lbl_status.setText("opencv-python not installed — camera unavailable")
            self._lbl_status.setStyleSheet(
                f"color: {theme.FAULT}; font-size: {theme.FS_CAPTION}px;")
            self._btn_open.setEnabled(False)

    # ---- public API --------------------------------------------------------

    def set_metadata_provider(self, fn):
        """Register a callable ``fn()`` that returns the current beamline state."""
        self._get_metadata = fn

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        # Camera selector row
        top = QHBoxLayout()
        top.setSpacing(4)
        self._combo = QComboBox()
        self._combo.setToolTip("Camera index (0 = first USB camera found)")
        for i in range(5):
            self._combo.addItem(f"Camera {i}", i)
        top.addWidget(self._combo)

        self._btn_open = QPushButton("Open")
        self._btn_open.setFixedWidth(64)
        self._btn_open.clicked.connect(self._toggle_camera)
        top.addWidget(self._btn_open)

        self._lbl_res = QLabel("")
        self._lbl_res.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        top.addWidget(self._lbl_res)

        lay.addLayout(top)

        # Settings row (resolution, FPS, metadata interval) — shown when camera is open
        settings = QHBoxLayout()
        settings.setSpacing(4)

        settings.addWidget(QLabel("Res:"))
        self._combo_res = QComboBox()
        self._combo_res.setToolTip("Recording resolution (applied on next Open)")
        for w, h, label in _RES_PRESETS:
            self._combo_res.addItem(label, (w, h))
        self._combo_res.setCurrentIndex(len(_RES_PRESETS) - 1)  # default: Max
        self._combo_res.currentIndexChanged.connect(self._apply_resolution)
        settings.addWidget(self._combo_res)

        settings.addWidget(QLabel("FPS:"))
        self._spin_fps = QSpinBox()
        self._spin_fps.setRange(1, 120)
        self._spin_fps.setValue(30)
        self._spin_fps.setToolTip("Target capture FPS (applied on next Open)")
        self._spin_fps.valueChanged.connect(self._apply_fps)
        settings.addWidget(self._spin_fps)

        settings.addWidget(QLabel("Meta interval:"))
        self._spin_meta = QSpinBox()
        self._spin_meta.setRange(1, 300)
        self._spin_meta.setValue(1)
        self._spin_meta.setSuffix(" s")
        self._spin_meta.setToolTip(
            "Seconds between metadata snapshots during video recording.\n"
            "Increase for long recordings to reduce overhead.")
        self._spin_meta.valueChanged.connect(self._apply_meta_interval)
        settings.addWidget(self._spin_meta)

        lay.addLayout(settings)

        # Live-feed display
        self._lbl_feed = QLabel("No camera")
        self._lbl_feed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_feed.setMinimumSize(320, 240)
        self._lbl_feed.setStyleSheet("background: #111; color: #888;")
        self._lbl_feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._lbl_feed, stretch=1)

        # Status line
        self._lbl_status = QLabel("Disconnected")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        self._lbl_status.setWordWrap(True)
        lay.addWidget(self._lbl_status)

        # Capture buttons
        btns = QHBoxLayout()
        btns.setSpacing(4)

        self._btn_photo = QPushButton("Take Photo")
        self._btn_photo.setEnabled(False)
        self._btn_photo.clicked.connect(self._take_photo)
        btns.addWidget(self._btn_photo)

        self._btn_record = QPushButton("Record")
        self._btn_record.setEnabled(False)
        self._btn_record.clicked.connect(self._toggle_record)
        btns.addWidget(self._btn_record)

        lay.addLayout(btns)

    # ---- Camera open / close -----------------------------------------------

    def _toggle_camera(self):
        if self._cap is not None:
            self._close_camera()
        else:
            self._open_camera()

    def _open_camera(self):
        idx = self._combo.currentData()
        # CAP_DSHOW is the DirectShow backend — most reliable for USB cameras
        # on Windows.  Falls back to the default backend on non-Windows.
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            self._set_status(f"Camera {idx}: could not open", theme.FAULT)
            return

        # Request MJPEG format — many USB cameras send uncompressed at
        # lower resolutions but can stream HD only as MJPEG.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        # Apply user-selected resolution (0,0 sentinel = request max).
        req_w, req_h = self._combo_res.currentData()
        if req_w == 0:
            req_w, req_h = 10000, 10000
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, req_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, req_h)
        cap.set(cv2.CAP_PROP_FPS, self._spin_fps.value())

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._lbl_res.setText(f"{w}x{h} @ {fps:.0f} fps")

        self._cap = cap
        self._combo.setEnabled(False)
        self._btn_open.setText("Close")
        self._btn_photo.setEnabled(True)
        self._btn_record.setEnabled(True)
        self._set_status(f"Camera {idx} open", theme.OK)
        self._timer.start()

    def _close_camera(self):
        self._timer.stop()
        self._meta_timer.stop()
        self._lbl_res.setText("")
        if self._recording:
            self._stop_recording()
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._current_frame = None
        self._lbl_feed.setPixmap(QPixmap())
        self._lbl_feed.setText("No camera")
        self._btn_open.setText("Open")
        self._combo.setEnabled(True)
        self._btn_photo.setEnabled(False)
        self._btn_record.setEnabled(False)
        self._set_status("Disconnected", theme.NEUTRAL)

    # ---- Live setting changes -----------------------------------------------

    def _apply_resolution(self):
        """Apply the selected resolution to the open camera (if any)."""
        if self._cap is None:
            return
        req_w, req_h = self._combo_res.currentData()
        if req_w == 0:
            req_w, req_h = 10000, 10000
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, req_w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, req_h)
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._lbl_res.setText(f"{w}x{h} @ {fps:.0f} fps")

    def _apply_fps(self, value: int):
        """Apply the selected FPS to the open camera (if any)."""
        if self._cap is None:
            return
        self._cap.set(cv2.CAP_PROP_FPS, value)
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._lbl_res.setText(f"{w}x{h} @ {fps:.0f} fps")

    def _apply_meta_interval(self, value: int):
        """Update the metadata logging interval (seconds)."""
        self._meta_timer.setInterval(value * 1000)

    # ---- Frame grab --------------------------------------------------------

    def _grab_frame(self):
        if self._cap is None:
            return
        ret, frame = self._cap.read()
        if not ret:
            return
        self._current_frame = frame

        if self._recording and self._video_writer is not None:
            self._video_writer.write(frame)

        # Convert BGR -> RGB for Qt
        h, w, ch = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self._lbl_feed.width(),
            self._lbl_feed.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._lbl_feed.setPixmap(pix)

    # ---- Photo capture -----------------------------------------------------

    def _take_photo(self):
        if self._current_frame is None:
            self._set_status("No frame yet — wait for the feed to start", theme.WARN)
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"photo_{ts}"
        captures = captures_dir()
        img_path = os.path.join(captures, f"{name}.png")
        cv2.imwrite(img_path, self._current_frame)
        self._write_sidecar(name, "photo", img_path)
        self._set_status(f"Saved {name}.png  (+.json)", theme.OK)

    # ---- Video recording ---------------------------------------------------

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self._cap is None:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._rec_name = f"video_{ts}"
        captures = captures_dir()
        self._rec_path = os.path.join(captures, f"{self._rec_name}.avi")

        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            fps = 30.0
        # MJPG codec in AVI container: lossless-ish quality, widely supported,
        # no extra codec install required on Windows.
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        self._video_writer = cv2.VideoWriter(self._rec_path, fourcc, fps, (w, h))
        if not self._video_writer.isOpened():
            self._video_writer = None
            self._set_status("Failed to open video writer — check codec support", theme.FAULT)
            return
        self._rec_start_meta = self._collect_metadata()
        self._rec_meta_log = []
        self._recording = True
        self._meta_timer.start()

        self._btn_record.setText("Stop")
        self._btn_record.setStyleSheet(
            f"background: {theme.FAULT}; color: white; font-weight: bold;")
        self._set_status("Recording…", theme.FAULT)

    def _stop_recording(self):
        self._recording = False
        self._meta_timer.stop()
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None
        self._btn_record.setText("Record")
        self._btn_record.setStyleSheet("")
        self._write_sidecar(
            self._rec_name, "video", self._rec_path,
            extra={
                "beamline_at_start": self._rec_start_meta,
                "beamline_over_time": self._rec_meta_log,
            },
        )
        self._set_status(
            f"Saved {self._rec_name}.avi  (+.json)", theme.OK)

    # ---- Periodic metadata logging during recording -------------------------

    def _log_metadata(self):
        """Append a timestamped metadata snapshot to the recording log."""
        if not self._recording:
            return
        entry = {
            "timestamp": datetime.now().isoformat(),
            "beamline": self._collect_metadata(),
        }
        self._rec_meta_log.append(entry)

    # ---- Metadata / sidecar ------------------------------------------------

    def _collect_metadata(self) -> dict:
        if self._get_metadata is not None:
            try:
                return self._get_metadata()
            except Exception:
                pass
        return {}

    def _write_sidecar(self, base_name: str, media_type: str, media_path: str,
                       extra: dict | None = None):
        """Write a JSON sidecar alongside the media file.

        The sidecar has the same stem as the media file so they are trivially
        paired in any file browser or sorting by name.
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "media_file": os.path.basename(media_path),
            "type": media_type,
            "beamline": self._collect_metadata(),
        }
        if extra:
            record.update(extra)
        json_path = os.path.join(captures_dir(), f"{base_name}.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, default=_serialise)

    # ---- Helpers -----------------------------------------------------------

    def _set_status(self, text: str, role: str = theme.NEUTRAL):
        self._lbl_status.setText(text)
        self._lbl_status.setStyleSheet(
            f"color: {role}; font-size: {theme.FS_CAPTION}px;")

    def closeEvent(self, event):
        self._close_camera()
        super().closeEvent(event)
