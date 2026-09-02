"""
camera_tab.py
Full-screen camera view with session controls.

Shares the same SessionRecorder and CameraSource as the Overview panel — there
is exactly one cv2.VideoCapture in the process.  Both views subscribe to the
same signals; neither caches a setting locally.

Extras over the compact RecordingPanel:
  - Crosshair overlay (drawn with QPainter, never burned into frames/photos)
  - Fit / 1:1 toggle
  - Double-click feed to go fullscreen; Esc to exit

Phase 1 fix (2026-08-25): replaced _FeedLabel (QLabel subclass) with VideoView.
The QLabel setPixmap() path had a size ratchet in the DragPanel scroll area
(see video_view.py).  VideoView's minimumSizeHint() is a constant so the layout
is always free to shrink it.  No frame data was ever affected.

Phase 2 (2026-08-25): UI restructured to expose master codec and camera pixel
format controls.  The "Preview" spin box is relabelled "Acquire" to make clear
it controls acquisition rate, not display rate.
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QInputDialog, QScrollArea, QSizePolicy,
)

from rbl.config.recording_config import (
    RES_PRESETS, PREVIEW_FPS_DEFAULT, RECORD_FPS_MIN, RECORD_FPS_MAX,
    QUALITY_PRESETS, QUALITY_DEFAULT,
    MASTER_CODECS, MASTER_CODEC_DEFAULT,
)
from rbl.gui import theme
from rbl.gui.widgets.video_view import VideoView

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

_CAMERA_FORMATS = [
    ("MJPG (compressed, fast)", "MJPG"),
    ("YUY2 (uncompressed)",     "YUY2"),
]


# ---- Fullscreen window -----------------------------------------------------

class _FullscreenWindow(QWidget):
    def __init__(self, camera_tab, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._tab = camera_tab
        self.setWindowTitle("Camera — Fullscreen")
        self.setStyleSheet("background: black;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._feed = VideoView()
        self._feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._feed.set_crosshair(camera_tab._chk_crosshair.isChecked())
        lay.addWidget(self._feed)
        self.showFullScreen()

    def set_frame(self, img: QImage):
        self._feed.set_frame(img)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self._tab._fullscreen_win = None
        super().closeEvent(event)


# ---- Camera tab ------------------------------------------------------------

class CameraTab(QWidget):
    """Full-view camera tab sharing one CameraSource with the Overview panel."""

    def __init__(self, recorder, camera, parent=None):
        super().__init__(parent)
        self._recorder = recorder
        self._camera   = camera
        self._blocking = False
        self._fullscreen_win: "_FullscreenWindow | None" = None
        self._fit_mode = True

        self._build_ui()
        self._connect_signals()
        self._render()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        # ---- Top controls row ----------------------------------------------
        top = QHBoxLayout()
        top.setSpacing(6)

        self._combo_cam = QComboBox()
        for i in range(5):
            self._combo_cam.addItem(f"Camera {i}", i)
        top.addWidget(self._combo_cam)

        self._btn_open = QPushButton("Open")
        self._btn_open.setFixedWidth(60)
        self._btn_open.clicked.connect(self._toggle_camera)
        top.addWidget(self._btn_open)

        self._lbl_res = QLabel("")
        self._lbl_res.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        top.addWidget(self._lbl_res)

        top.addStretch(1)

        self._chk_crosshair = QCheckBox("Crosshair")
        self._chk_crosshair.toggled.connect(self._on_crosshair_toggled)
        top.addWidget(self._chk_crosshair)
        lay.addLayout(top)

        # ---- Settings row --------------------------------------------------
        s1 = QHBoxLayout()
        s1.setSpacing(6)
        s1.addWidget(QLabel("Res:"))
        self._combo_res = QComboBox()
        for w, h, label in RES_PRESETS:
            self._combo_res.addItem(label, (w, h))
        self._combo_res.setCurrentIndex(len(RES_PRESETS) - 1)
        s1.addWidget(self._combo_res)

        s1.addWidget(QLabel("Format:"))
        self._combo_fmt = QComboBox()
        for label, data in _CAMERA_FORMATS:
            self._combo_fmt.addItem(label, data)
        self._combo_fmt.setToolTip(
            "Pixel format requested from the camera.\n"
            "MJPG: compressed, available at all resolutions.\n"
            "YUY2: uncompressed 4:2:2, typically 5 fps at 1080p.")
        s1.addWidget(self._combo_fmt)

        s1.addWidget(QLabel("Acquire:"))
        self._spin_preview = QSpinBox()
        self._spin_preview.setRange(1, 120)
        self._spin_preview.setValue(PREVIEW_FPS_DEFAULT)
        self._spin_preview.setSuffix(" fps")
        self._spin_preview.setToolTip(
            "Frame rate requested from the camera (acquisition rate). "
            "Lower it to make an uncompressed pixel format available.")
        s1.addWidget(self._spin_preview)

        s1.addWidget(QLabel("Record:"))
        self._spin_rec_fps = QSpinBox()
        self._spin_rec_fps.setRange(RECORD_FPS_MIN, RECORD_FPS_MAX)
        self._spin_rec_fps.setValue(self._recorder._record_fps)
        self._spin_rec_fps.setSuffix(" fps")
        self._spin_rec_fps.setToolTip(
            "How many of the acquired frames are written. "
            "Cannot exceed the acquire rate.")
        self._spin_rec_fps.valueChanged.connect(self._on_rec_fps_changed)
        s1.addWidget(self._spin_rec_fps)

        s1.addWidget(QLabel("Master:"))
        self._combo_master = QComboBox()
        for label in MASTER_CODECS:
            self._combo_master.addItem(label)
        self._combo_master.setCurrentText(MASTER_CODEC_DEFAULT)
        self._combo_master.setToolTip(
            "On-disk codec. MJPEG q98 is the default; FFV1 is lossless.")
        self._combo_master.currentTextChanged.connect(self._on_master_changed)
        s1.addWidget(self._combo_master)

        s1.addWidget(QLabel("MP4 copy (CRF):"))
        self._combo_quality = QComboBox()
        for label in QUALITY_PRESETS:
            self._combo_quality.addItem(label)
        self._combo_quality.setCurrentText(QUALITY_DEFAULT)
        self._combo_quality.currentTextChanged.connect(self._on_quality_changed)
        s1.addWidget(self._combo_quality)

        self._btn_fit = QPushButton("Fit")
        self._btn_fit.setCheckable(True)
        self._btn_fit.setChecked(True)
        self._btn_fit.setFixedWidth(40)
        self._btn_fit.toggled.connect(self._on_fit_toggled)
        s1.addWidget(self._btn_fit)

        self._btn_oneto1 = QPushButton("1:1")
        self._btn_oneto1.setCheckable(True)
        self._btn_oneto1.setFixedWidth(40)
        self._btn_oneto1.toggled.connect(self._on_oneto1_toggled)
        s1.addWidget(self._btn_oneto1)

        self._chk_video = QCheckBox("Record video")
        self._chk_video.setChecked(self._recorder._video_enabled)
        self._chk_video.toggled.connect(self._on_video_enabled_changed)
        s1.addWidget(self._chk_video)

        s1.addStretch(1)
        lay.addLayout(s1)

        # ---- Live feed (VideoView in a scroll area for 1:1 mode) -----------
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)   # fit mode; changed in 1:1

        self._feed = VideoView()
        self._feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._feed.double_clicked.connect(self._toggle_fullscreen)
        self._scroll.setWidget(self._feed)
        lay.addWidget(self._scroll, stretch=1)

        # ---- Status bar ----------------------------------------------------
        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        lay.addWidget(self._lbl_status)

        # ---- Bottom action row ---------------------------------------------
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self._btn_photo = QPushButton("Take Photo")
        self._btn_photo.setToolTip(
            "Saves a lossless PNG plus a beamline-state sidecar, independent "
            "of the video settings. For a shot that matters, this is the "
            "highest-quality path in the app.")
        self._btn_photo.clicked.connect(self._take_photo)
        btns.addWidget(self._btn_photo)

        self._btn_note = QPushButton("Add Note")
        self._btn_note.clicked.connect(self._add_note)
        btns.addWidget(self._btn_note)

        self._btn_session = QPushButton("● START SESSION")
        self._btn_session.setMinimumHeight(36)
        self._btn_session.clicked.connect(self._toggle_session)
        btns.addWidget(self._btn_session)

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
        self._camera.format_ready.connect(self._on_format_ready)
        self._camera.closed.connect(self._on_camera_closed_ui)
        self._camera.preview_ready.connect(self._on_preview_frame)

    # ---- Camera control ----------------------------------------------------

    def _toggle_camera(self):
        if self._camera.is_open():
            self._camera.close()
        else:
            idx    = self._combo_cam.currentData()
            w, h   = self._combo_res.currentData()
            fps    = self._spin_preview.value()
            fourcc = self._combo_fmt.currentData()
            self._camera.open(idx, w, h, fps, fourcc=fourcc)

    def _on_camera_opened(self, w: int, h: int, fps: float):
        self._lbl_res.setText(f"{w}x{h} @ {fps:.0f} fps")
        self._spin_rec_fps.setMaximum(min(RECORD_FPS_MAX, max(1, int(fps))))
        self._btn_open.setText("Close")
        self._combo_cam.setEnabled(False)
        if not self._recorder.is_recording():
            self._recorder.set_video_enabled(True)
        self._render()

    def _on_format_ready(self, fourcc: str):
        current = self._lbl_res.text()
        if "·" not in current and current:
            self._lbl_res.setText(f"{current} · {fourcc}")

    def _on_camera_closed_ui(self):
        self._lbl_res.setText("")
        self._feed.clear("No camera")
        self._spin_rec_fps.setMaximum(RECORD_FPS_MAX)
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
            self._feed.set_frame(img)
            if self._fullscreen_win is not None:
                self._fullscreen_win.set_frame(img)
        except Exception:
            pass

    # ---- Settings handlers -------------------------------------------------

    def _on_rec_fps_changed(self, v: int):
        if self._blocking:
            return
        self._recorder.set_record_fps(v)

    def _on_quality_changed(self, label: str):
        if self._blocking:
            return
        self._recorder.set_quality(label)

    def _on_master_changed(self, label: str):
        if self._blocking:
            return
        self._recorder.set_master_codec(label)

    def _on_video_enabled_changed(self, on: bool):
        if self._blocking:
            return
        self._recorder.set_video_enabled(on)

    def _on_crosshair_toggled(self, on: bool):
        self._feed.set_crosshair(on)
        if self._fullscreen_win:
            self._fullscreen_win._feed.set_crosshair(on)

    def _on_fit_toggled(self, on: bool):
        if on:
            self._fit_mode = True
            self._btn_oneto1.setChecked(False)
            self._feed.set_scale_mode("fit")
            self._scroll.setWidgetResizable(True)

    def _on_oneto1_toggled(self, on: bool):
        if on:
            self._fit_mode = False
            self._btn_fit.setChecked(False)
            self._feed.set_scale_mode("one_to_one")
            self._scroll.setWidgetResizable(False)

    def _toggle_fullscreen(self):
        if self._fullscreen_win is not None:
            self._fullscreen_win.close()
        else:
            self._fullscreen_win = _FullscreenWindow(self)

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

        self._blocking = True
        self._spin_rec_fps.setValue(self._recorder._record_fps)
        self._combo_quality.setCurrentText(self._recorder._quality_label)
        self._combo_master.setCurrentText(self._recorder._master_codec)
        self._chk_video.setChecked(self._recorder._video_enabled)
        self._blocking = False

        for w in (self._spin_rec_fps, self._combo_master,
                  self._combo_quality, self._chk_video):
            w.setEnabled(not rec)

        if not self._camera.is_open() or not _CV2_OK:
            self._chk_video.setEnabled(False)

        ffmpeg_found = st.get("ffmpeg_found", False)
        self._combo_quality.setEnabled(not rec and ffmpeg_found)
        if not ffmpeg_found:
            self._combo_quality.setToolTip(
                "ffmpeg not found — no MP4 copy is made. "
                "The Master setting is what is recorded.")
        else:
            self._combo_quality.setToolTip("")

        if rec:
            elapsed = st["elapsed_s"]
            h = int(elapsed) // 3600
            m = (int(elapsed) % 3600) // 60
            s = int(elapsed) % 60
            self._btn_session.setText("■ STOP SESSION")
            self._btn_session.setStyleSheet(
                f"background: {theme.FAULT}; color: white; font-weight: bold;")
            self._lbl_status.setText(
                f"● REC  {st['session_id']}  ·  "
                f"{h:02d}:{m:02d}:{s:02d}  ·  {st['frames_written']} frames")
            self._lbl_status.setStyleSheet(
                f"color: {theme.FAULT}; font-size: {theme.FS_CAPTION}px;")
        else:
            self._btn_session.setText("● START SESSION")
            self._btn_session.setStyleSheet("")
            self._lbl_status.setText(
                st["session_id"] if st["session_id"] else "No session")
            self._lbl_status.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")

        self._btn_photo.setEnabled(self._camera.is_open())
        self._btn_note.setEnabled(rec)
