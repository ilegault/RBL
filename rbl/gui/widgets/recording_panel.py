"""
recording_panel.py
Compact "Session Recorder" group box for the Overview tab.

Replaces the two independent CameraWidget and LoggerWidget group boxes with a
single panel that owns neither its camera device nor its data — both are owned
by SessionRecorder and CameraSource (both injected at construction time).

All state is rendered from recorder.state() on state_changed.  No local copies
of settings are kept; writing to a spinbox writes through recorder.set_*() and
re-renders on settings_changed.  This is what makes both views stay in sync:
the Overview panel and the Camera tab are two renderers of one model.
"""
import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QInputDialog, QSizePolicy,
)

from rbl.config.recording_config import (
    RES_PRESETS, PREVIEW_FPS_DEFAULT, RECORD_FPS_MIN, RECORD_FPS_MAX,
    CSV_INTERVAL_MIN_S, CSV_INTERVAL_MAX_S, CSV_INTERVAL_DEFAULT_S,
    SEGMENT_SECONDS_CHOICES, QUALITY_PRESETS, QUALITY_DEFAULT,
)
from rbl.gui import theme

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


class RecordingPanel(QGroupBox):
    """Compact session recorder panel for the Overview PanelArea column."""

    def __init__(self, recorder, parent=None):
        super().__init__("Session Recorder", parent)
        self._recorder = recorder
        self._camera   = recorder._camera
        self._blocking = False   # guard against signal loops while setting widget values

        self._build_ui()
        self._connect_signals()
        self._render()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        # ---- Camera open row -----------------------------------------------
        cam_row = QHBoxLayout()
        cam_row.setSpacing(4)

        self._combo_cam = QComboBox()
        for i in range(5):
            self._combo_cam.addItem(f"Camera {i}", i)
        cam_row.addWidget(self._combo_cam)

        self._btn_open = QPushButton("Open")
        self._btn_open.setFixedWidth(60)
        self._btn_open.clicked.connect(self._toggle_camera)
        cam_row.addWidget(self._btn_open)

        self._lbl_res = QLabel("")
        self._lbl_res.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        cam_row.addWidget(self._lbl_res, stretch=1)
        lay.addLayout(cam_row)

        # ---- Settings row --------------------------------------------------
        s1 = QHBoxLayout()
        s1.setSpacing(4)
        s1.addWidget(QLabel("Res:"))
        self._combo_res = QComboBox()
        for w, h, label in RES_PRESETS:
            self._combo_res.addItem(label, (w, h))
        self._combo_res.setCurrentIndex(len(RES_PRESETS) - 1)
        s1.addWidget(self._combo_res)

        s1.addWidget(QLabel("Preview:"))
        self._spin_preview = QSpinBox()
        self._spin_preview.setRange(1, 120)
        self._spin_preview.setValue(PREVIEW_FPS_DEFAULT)
        self._spin_preview.setSuffix(" fps")
        s1.addWidget(self._spin_preview)

        s1.addWidget(QLabel("Record:"))
        self._spin_rec_fps = QSpinBox()
        self._spin_rec_fps.setRange(RECORD_FPS_MIN, RECORD_FPS_MAX)
        self._spin_rec_fps.setValue(self._recorder._record_fps)
        self._spin_rec_fps.setSuffix(" fps")
        self._spin_rec_fps.valueChanged.connect(self._on_rec_fps_changed)
        s1.addWidget(self._spin_rec_fps)
        lay.addLayout(s1)

        # ---- Live feed -----------------------------------------------------
        self._lbl_feed = QLabel("No camera")
        self._lbl_feed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_feed.setMinimumSize(240, 180)
        self._lbl_feed.setStyleSheet("background: #111; color: #888;")
        self._lbl_feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._lbl_feed, stretch=1)

        # ---- CSV / segment row ---------------------------------------------
        s2 = QHBoxLayout()
        s2.setSpacing(4)
        s2.addWidget(QLabel("CSV every:"))
        self._spin_csv = QSpinBox()
        self._spin_csv.setRange(CSV_INTERVAL_MIN_S, CSV_INTERVAL_MAX_S)
        self._spin_csv.setValue(self._recorder._csv_interval_s)
        self._spin_csv.setSuffix(" s")
        self._spin_csv.valueChanged.connect(self._on_csv_interval_changed)
        s2.addWidget(self._spin_csv)

        s2.addWidget(QLabel("Segment:"))
        self._combo_seg = QComboBox()
        for secs in SEGMENT_SECONDS_CHOICES:
            if secs < 60:
                label = f"{secs} s"
            elif secs < 3600:
                label = f"{secs // 60} min"
            else:
                label = f"{secs // 3600} h"
            self._combo_seg.addItem(label, secs)
        # Default to 10 min
        for i in range(self._combo_seg.count()):
            if self._combo_seg.itemData(i) == self._recorder._segment_seconds:
                self._combo_seg.setCurrentIndex(i)
                break
        self._combo_seg.currentIndexChanged.connect(self._on_segment_changed)
        s2.addWidget(self._combo_seg)
        lay.addLayout(s2)

        # ---- Quality / video-enable row ------------------------------------
        s3 = QHBoxLayout()
        s3.setSpacing(4)
        s3.addWidget(QLabel("Quality:"))
        self._combo_quality = QComboBox()
        for label in QUALITY_PRESETS:
            self._combo_quality.addItem(label)
        self._combo_quality.setCurrentText(QUALITY_DEFAULT)
        self._combo_quality.currentTextChanged.connect(self._on_quality_changed)
        s3.addWidget(self._combo_quality)

        self._chk_video = QCheckBox("Record video")
        self._chk_video.setChecked(self._recorder._video_enabled)
        self._chk_video.toggled.connect(self._on_video_enabled_changed)
        s3.addWidget(self._chk_video)
        lay.addLayout(s3)

        # ---- Start/Stop button ---------------------------------------------
        self._btn_session = QPushButton("● START SESSION")
        self._btn_session.setMinimumHeight(36)
        self._btn_session.clicked.connect(self._toggle_session)
        lay.addWidget(self._btn_session)

        # ---- Status lines --------------------------------------------------
        self._lbl_status = QLabel("No session")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        self._lbl_status.setWordWrap(True)
        lay.addWidget(self._lbl_status)

        self._lbl_stats = QLabel("")
        self._lbl_stats.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        self._lbl_stats.setWordWrap(True)
        lay.addWidget(self._lbl_stats)

        # ---- Action buttons ------------------------------------------------
        btns = QHBoxLayout()
        btns.setSpacing(4)
        self._btn_photo = QPushButton("Take Photo")
        self._btn_photo.clicked.connect(self._take_photo)
        btns.addWidget(self._btn_photo)

        self._btn_note = QPushButton("Add Note")
        self._btn_note.clicked.connect(self._add_note)
        btns.addWidget(self._btn_note)

        self._btn_folder = QPushButton("Open Folder")
        self._btn_folder.clicked.connect(self._recorder.open_session_folder)
        btns.addWidget(self._btn_folder)
        lay.addLayout(btns)

    # ---- Signal wiring -----------------------------------------------------

    def _connect_signals(self):
        self._recorder.state_changed.connect(self._render)
        self._recorder.settings_changed.connect(self._render)
        self._recorder.photo_taken.connect(self._on_photo_taken)
        self._camera.opened.connect(self._on_camera_opened)
        self._camera.closed.connect(self._on_camera_closed_ui)
        self._camera.preview_ready.connect(self._on_preview_frame)

    # ---- Camera control ----------------------------------------------------

    def _toggle_camera(self):
        if self._camera.is_open():
            self._camera.close()
        else:
            idx = self._combo_cam.currentData()
            w, h = self._combo_res.currentData()
            fps  = self._spin_preview.value()
            self._camera.open(idx, w, h, fps)

    def _on_camera_opened(self, w: int, h: int, fps: float):
        self._lbl_res.setText(f"{w}x{h} @ {fps:.0f} fps")
        self._btn_open.setText("Close")
        self._combo_cam.setEnabled(False)
        self._render()

    def _on_camera_closed_ui(self):
        self._lbl_res.setText("")
        self._lbl_feed.setPixmap(QPixmap())
        self._lbl_feed.setText("No camera")
        self._btn_open.setText("Open")
        self._combo_cam.setEnabled(True)
        self._render()

    def _on_preview_frame(self, frame):
        if not self.isVisible():
            return
        try:
            import cv2 as _cv2
            h, w, ch = frame.shape
            rgb = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)
            img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(img).scaled(
                self._lbl_feed.width(), self._lbl_feed.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            self._lbl_feed.setPixmap(pix)
        except Exception:
            pass

    # ---- Settings handlers -------------------------------------------------

    def _on_rec_fps_changed(self, v: int):
        if self._blocking:
            return
        self._recorder.set_record_fps(v)

    def _on_csv_interval_changed(self, v: int):
        if self._blocking:
            return
        self._recorder.set_csv_interval_s(v)

    def _on_segment_changed(self):
        if self._blocking:
            return
        self._recorder.set_segment_seconds(self._combo_seg.currentData())

    def _on_quality_changed(self, label: str):
        if self._blocking:
            return
        self._recorder.set_quality(label)

    def _on_video_enabled_changed(self, on: bool):
        if self._blocking:
            return
        self._recorder.set_video_enabled(on)

    # ---- Session control ---------------------------------------------------

    def _toggle_session(self):
        if self._recorder.is_recording():
            self._recorder.stop()
        else:
            self._recorder.start()

    def _on_photo_taken(self, path: str):
        self._btn_photo.setText("✓ Saved")
        self._btn_photo.setStyleSheet(
            f"background: {theme.OK}; color: white; font-weight: bold;")
        fname = os.path.basename(path)
        prev_style = self._lbl_status.styleSheet()
        prev_text  = self._lbl_status.text()
        self._lbl_status.setText(f"Photo: {fname}")
        self._lbl_status.setStyleSheet(
            f"color: {theme.OK}; font-size: {theme.FS_CAPTION}px;")

        def _restore():
            self._btn_photo.setText("Take Photo")
            self._btn_photo.setStyleSheet("")
            self._lbl_status.setText(prev_text)
            self._lbl_status.setStyleSheet(prev_style)

        QTimer.singleShot(2000, _restore)

    def _take_photo(self):
        self._recorder.take_photo()

    def _add_note(self):
        text, ok = QInputDialog.getText(self, "Add Note", "Note:")
        if ok and text:
            self._recorder.add_note(text)

    # ---- Rendering ---------------------------------------------------------

    def _render(self):
        st = self._recorder.state()
        rec = st["recording"]

        # Update settings widgets without triggering handlers.
        self._blocking = True
        self._spin_rec_fps.setValue(self._recorder._record_fps)
        self._spin_csv.setValue(self._recorder._csv_interval_s)
        self._combo_quality.setCurrentText(self._recorder._quality_label)
        self._chk_video.setChecked(self._recorder._video_enabled)
        self._blocking = False

        # Enable/disable settings controls while recording.
        for w in (self._spin_rec_fps, self._spin_csv, self._combo_seg,
                  self._combo_quality, self._chk_video):
            w.setEnabled(not rec)

        # Video checkbox: also disabled if no camera or no cv2.
        if not self._camera.is_open() or not _CV2_OK:
            self._chk_video.setEnabled(False)
            tip = ("Camera must be open to record video."
                   if _CV2_OK else "opencv-python not installed.")
            self._chk_video.setToolTip(tip)

        # Session button.
        if rec:
            self._btn_session.setText("■ STOP SESSION")
            self._btn_session.setStyleSheet(
                f"background: {theme.FAULT}; color: white; font-weight: bold;")
        else:
            self._btn_session.setText("● START SESSION")
            self._btn_session.setStyleSheet("")

        # Status and stats.
        if rec:
            elapsed = st["elapsed_s"]
            h = int(elapsed) // 3600
            m = (int(elapsed) % 3600) // 60
            s = int(elapsed) % 60
            self._lbl_status.setText(
                f"{st['session_id']}  ·  {h:02d}:{m:02d}:{s:02d}")
            self._lbl_status.setStyleSheet(
                f"color: {theme.OK}; font-size: {theme.FS_CAPTION}px;")

            stats = (f"{st['csv_rows']} rows"
                     f"  ·  {st['frames_written']} frames"
                     f"  ·  {st['segments_done']} segments")
            if st["transcode_pending"]:
                stats += f"  ·  transcode: {st['transcode_pending']} pending"
            free_gb = st["disk_free_bytes"] / 1024**3
            stats += f"\ndisk: {free_gb:.0f} GB free"
            self._lbl_stats.setText(stats)
        else:
            if st["session_id"]:
                self._lbl_status.setText(f"Last: {st['session_id']}")
            else:
                self._lbl_status.setText("No session")
            self._lbl_status.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            self._lbl_stats.setText(
                "" if not st["ffmpeg_found"] else "")
            if not st["ffmpeg_found"]:
                self._lbl_stats.setText(
                    "ffmpeg not found — keeping .avi only")

        # Action buttons.
        self._btn_photo.setEnabled(self._camera.is_open())
        self._btn_note.setEnabled(rec)
