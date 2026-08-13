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
"""
import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QInputDialog, QScrollArea, QSizePolicy,
)

from rbl.config.recording_config import (
    RES_PRESETS, PREVIEW_FPS_DEFAULT, RECORD_FPS_MIN, RECORD_FPS_MAX,
    CSV_INTERVAL_MIN_S, CSV_INTERVAL_MAX_S,
    SEGMENT_SECONDS_CHOICES, QUALITY_PRESETS, QUALITY_DEFAULT,
)
from rbl.gui import theme

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


# ---- Feed label with optional crosshair overlay ----------------------------

class _FeedLabel(QLabel):
    """QLabel that draws a crosshair + thirds guides via QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._crosshair = False
        self._base_pix: QPixmap | None = None  # unscaled source frame

    def set_base_pixmap(self, pix: QPixmap):
        """Store the source frame and scale it to the current label size."""
        self._base_pix = pix
        self._rescale()

    def _rescale(self):
        if self._base_pix is None or self._base_pix.isNull():
            return
        scaled = self._base_pix.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def set_crosshair(self, on: bool):
        self._crosshair = on
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._crosshair or self.pixmap() is None or self.pixmap().isNull():
            return
        w, h = self.width(), self.height()
        painter = QPainter(self)
        pen = QPen(QColor(0, 255, 0, 180), 1)
        painter.setPen(pen)
        # Centre cross
        painter.drawLine(w // 2, 0, w // 2, h)
        painter.drawLine(0, h // 2, w, h // 2)
        # Thirds guides (rule-of-thirds)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(w // 3, 0, w // 3, h)
        painter.drawLine(2 * w // 3, 0, 2 * w // 3, h)
        painter.drawLine(0, h // 3, w, h // 3)
        painter.drawLine(0, 2 * h // 3, w, 2 * h // 3)
        painter.end()

    def mouseDoubleClickEvent(self, event):
        self.parent()._toggle_fullscreen()


# ---- Fullscreen window -----------------------------------------------------

class _FullscreenWindow(QWidget):
    def __init__(self, camera_tab, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._tab = camera_tab
        self.setWindowTitle("Camera — Fullscreen")
        self.setStyleSheet("background: black;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lbl = _FeedLabel()
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._lbl.set_crosshair(camera_tab._chk_crosshair.isChecked())
        lay.addWidget(self._lbl)
        self.showFullScreen()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self._tab._fullscreen_win = None
        super().closeEvent(event)

    def update_frame(self, pix: QPixmap):
        scaled = pix.scaled(self.width(), self.height(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        self._lbl.setPixmap(scaled)


# ---- Camera tab ------------------------------------------------------------

class CameraTab(QWidget):
    """Full-view camera tab sharing one CameraSource with the Overview panel."""

    def __init__(self, recorder, camera, parent=None):
        super().__init__(parent)
        self._recorder = recorder
        self._camera   = camera
        self._blocking = False
        self._fullscreen_win: _FullscreenWindow | None = None
        self._fit_mode = True   # True = scale to fit; False = 1:1 in scroll area

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

        s1.addWidget(QLabel("Quality:"))
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

        s1.addStretch(1)
        lay.addLayout(s1)

        # ---- Live feed -----------------------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setWidgetResizable(False)

        self._feed = _FeedLabel(self)
        self._feed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._feed.setMinimumSize(320, 240)
        self._feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._feed.setStyleSheet("background: #111; color: #888;")
        self._feed.setText("No camera")
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
        self._feed.setPixmap(QPixmap())
        self._feed.setText("No camera")
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
            base_pix = QPixmap.fromImage(img)

            if self._fit_mode:
                self._feed.setFixedSize(self._scroll.viewport().size())
                self._feed.set_base_pixmap(base_pix)
            else:
                self._feed._base_pix = None
                self._feed.setFixedSize(base_pix.size())
                self._feed.setPixmap(base_pix)

            if self._fullscreen_win is not None:
                self._fullscreen_win.update_frame(base_pix)
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

    def _on_crosshair_toggled(self, on: bool):
        self._feed.set_crosshair(on)
        if self._fullscreen_win:
            self._fullscreen_win._lbl.set_crosshair(on)

    def _on_fit_toggled(self, on: bool):
        if on:
            self._fit_mode = True
            self._btn_oneto1.setChecked(False)

    def _on_oneto1_toggled(self, on: bool):
        if on:
            self._fit_mode = False
            self._btn_fit.setChecked(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self._feed.setFixedSize(self._scroll.viewport().size())

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
        self._blocking = False

        for w in (self._spin_rec_fps, self._combo_quality):
            w.setEnabled(not rec)

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
