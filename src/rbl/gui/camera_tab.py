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
  - Full screen button (Esc to exit; double-click also works)

Phase 1 fix (2026-08-25): replaced _FeedLabel (QLabel subclass) with VideoView.
The QLabel setPixmap() path had a size ratchet in the DragPanel scroll area
(see video_view.py).  VideoView's minimumSizeHint() is a constant so the layout
is always free to shrink it.  No frame data was ever affected.

Phase 2 (2026-08-25): UI restructured to expose master codec and camera pixel
format controls.  The "Preview" spin box is relabelled "Acquire" to make clear
it controls acquisition rate, not display rate.

One camera at a time, chosen here (ticket 74): the "Camera:" row at the top
picks USB or ZWO, identically to RecordingPanel (shared in
camera_kind_mixin.py).  The USB-only controls are collected in
``usb_only_widgets`` and hidden under ZWO; the ZWO rows live in ``ZwoControls``
(``zwo_controls``).  The ZWO Preview is drawn as decoded, with a caption under
the view.  The USB path is unchanged (ADR 0008 decision 7).

ROI drawing (ticket 75): to choose an ROI, draw over the **Full sensor**
Preview; the numeric boxes then show the snapped result the Capture Process
replied with, never the drawn rectangle.  Drawing is on only while the kind is
ZWO, the camera is open and no ZWO run is recording (an ROI change mid-run
would start a new segment).  The Overview panel has no drawing.
"""
import importlib.util
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rbl.config.recording_config import (
    MASTER_CODEC_DEFAULT,
    MASTER_CODECS,
    PREVIEW_FPS_DEFAULT,
    QUALITY_DEFAULT,
    QUALITY_PRESETS,
    RECORD_FPS_MAX,
    RECORD_FPS_MIN,
    RES_PRESETS,
)
from rbl.gui import theme
from rbl.gui.widgets.camera_kind_mixin import CameraKindMixin
from rbl.gui.widgets.video_view import VideoView
from rbl.hardware.preview_stretch import view_rect_to_sensor_roi

_CV2_OK = importlib.util.find_spec("cv2") is not None

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

class CameraTab(CameraKindMixin, QWidget):
    """Full-view camera tab sharing one CameraSource with the Overview panel."""

    def __init__(self, recorder, camera, parent=None):
        super().__init__(parent)
        self._recorder = recorder
        self._camera   = camera
        self._blocking = False
        self._fullscreen_win: "_FullscreenWindow | None" = None
        self._fit_mode = True
        self._zwo_preview_fields: dict = {}

        self._build_ui()
        self._connect_signals()
        self._render()

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        kind_row = self._make_kind_row()
        kind_row.addStretch(1)
        lay.addLayout(kind_row)

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

        self.btn_fullscreen = QPushButton("Full screen")
        self.btn_fullscreen.setToolTip(
            "Full screen (Esc to exit). Double-click the picture also works."
        )
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        top.addWidget(self.btn_fullscreen)
        lay.addLayout(top)

        # ---- Settings row --------------------------------------------------
        s1 = QHBoxLayout()
        s1.setSpacing(6)
        lbl_res_t = QLabel("Res:")
        s1.addWidget(lbl_res_t)
        self._combo_res = QComboBox()
        for w, h, label in RES_PRESETS:
            self._combo_res.addItem(label, (w, h))
        self._combo_res.setCurrentIndex(len(RES_PRESETS) - 1)
        s1.addWidget(self._combo_res)

        lbl_fmt = QLabel("Format:")
        s1.addWidget(lbl_fmt)
        self._combo_fmt = QComboBox()
        for label, data in _CAMERA_FORMATS:
            self._combo_fmt.addItem(label, data)
        self._combo_fmt.setToolTip(
            "Pixel format requested from the camera.\n"
            "MJPG: compressed, available at all resolutions.\n"
            "YUY2: uncompressed 4:2:2, typically 5 fps at 1080p.")
        s1.addWidget(self._combo_fmt)

        lbl_acq = QLabel("Acquire:")
        s1.addWidget(lbl_acq)
        self._spin_preview = QSpinBox()
        self._spin_preview.setRange(1, 120)
        self._spin_preview.setValue(PREVIEW_FPS_DEFAULT)
        self._spin_preview.setSuffix(" fps")
        self._spin_preview.setToolTip(
            "Frame rate requested from the camera (acquisition rate). "
            "Lower it to make an uncompressed pixel format available.")
        s1.addWidget(self._spin_preview)

        lbl_rec = QLabel("Record:")
        s1.addWidget(lbl_rec)
        self._spin_rec_fps = QSpinBox()
        self._spin_rec_fps.setRange(RECORD_FPS_MIN, RECORD_FPS_MAX)
        self._spin_rec_fps.setValue(self._recorder._record_fps)
        self._spin_rec_fps.setSuffix(" fps")
        self._spin_rec_fps.setToolTip(
            "How many of the acquired frames are written. "
            "Cannot exceed the acquire rate.")
        self._spin_rec_fps.valueChanged.connect(self._on_rec_fps_changed)
        s1.addWidget(self._spin_rec_fps)

        lbl_master = QLabel("Master:")
        s1.addWidget(lbl_master)
        self._combo_master = QComboBox()
        for label in MASTER_CODECS:
            self._combo_master.addItem(label)
        self._combo_master.setCurrentText(MASTER_CODEC_DEFAULT)
        self._combo_master.setToolTip(
            "On-disk codec. MJPEG q98 is the default; FFV1 is lossless.")
        self._combo_master.currentTextChanged.connect(self._on_master_changed)
        s1.addWidget(self._combo_master)

        lbl_mp4 = QLabel("MP4 copy (CRF):")
        s1.addWidget(lbl_mp4)
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

        self._btn_video = QPushButton("Start Video")
        self._btn_video.clicked.connect(self._toggle_video)
        s1.addWidget(self._btn_video)

        s1.addStretch(1)
        lay.addLayout(s1)

        # ---- ZWO rows (shown only under the ZWO camera) --------------------
        lay.addWidget(self._make_zwo_controls())

        # ---- Live feed (VideoView in a scroll area for 1:1 mode) -----------
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)   # fit mode; changed in 1:1

        self._feed = VideoView()
        self._feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._feed.double_clicked.connect(self._toggle_fullscreen)
        self._feed.rect_drawn.connect(self._on_rect_drawn)
        self._scroll.setWidget(self._feed)
        lay.addWidget(self._scroll, stretch=1)
        lay.addWidget(self._make_preview_caption())

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

        # This tab never exposed the segment length, so it has no Segment widget.
        self.usb_only_widgets = [
            self._combo_cam,
            lbl_res_t, self._combo_res,
            lbl_fmt, self._combo_fmt,
            lbl_acq, self._spin_preview,
            lbl_rec, self._spin_rec_fps,
            lbl_master, self._combo_master,
            lbl_mp4, self._combo_quality,
        ]

    # ---- Signal wiring -----------------------------------------------------

    def _connect_signals(self):
        self._recorder.state_changed.connect(self._render)
        self._recorder.settings_changed.connect(self._render)
        self._recorder.photo_taken.connect(self._on_photo_taken)
        self._camera.opened.connect(self._on_camera_opened)
        self._camera.format_ready.connect(self._on_format_ready)
        self._camera.closed.connect(self._on_camera_closed_ui)
        self._camera.preview_ready.connect(self._on_preview_frame)
        self._connect_zwo_preview()

    @property
    def video_view(self) -> VideoView:
        """The live feed widget (read-only)."""
        return self._feed

    # ---- ROI drawing -------------------------------------------------------

    def _on_zwo_preview_frame(self, image, fields: dict) -> None:
        # Remember the geometry of the latest Preview even while hidden: the
        # drawn rectangle is mapped through the frame that is on screen.
        self._zwo_preview_fields = dict(fields)
        super()._on_zwo_preview_frame(image, fields)

    def _on_rect_drawn(self, x: int, y: int, w: int, h: int) -> None:
        f = self._zwo_preview_fields
        roi = f.get("roi")
        painted = self._feed.painted_rect()
        if not roi or not f.get("width") or not f.get("height") or painted.isEmpty():
            return
        sx, sy, sw, sh = view_rect_to_sensor_roi(
            (x, y, w, h),
            (painted.x(), painted.y(), painted.width(), painted.height()),
            (int(f["width"]), int(f["height"])), roi)
        if sw > 0 and sh > 0:
            self._recorder.set_zwo_settings(
                roi={"x": sx, "y": sy, "width": sw, "height": sh})

    # ---- Camera control ----------------------------------------------------

    def _toggle_camera(self):
        if self._is_zwo():
            self._toggle_zwo_camera()
        elif self._camera.is_open():
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

    def _draw_zwo_preview(self, image):
        self._feed.set_frame(image)
        if self._fullscreen_win is not None:
            self._fullscreen_win.set_frame(image)

    def _on_preview_frame(self, frame):
        if not self.isVisible() or self._is_zwo():
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

    def _toggle_video(self):
        st = self._recorder.state()
        if st.get("video_active", False):
            self._recorder.stop_video()
        else:
            self._recorder.start_video()

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
        video_active = st.get("video_active", False)

        self._blocking = True
        self._spin_rec_fps.setValue(self._recorder._record_fps)
        self._combo_quality.setCurrentText(self._recorder._quality_label)
        self._combo_master.setCurrentText(self._recorder._master_codec)
        self._blocking = False

        for w in (self._spin_rec_fps, self._combo_master):
            w.setEnabled(not video_active)

        ffmpeg_found = st.get("ffmpeg_found", False)
        self._combo_quality.setEnabled(not video_active and ffmpeg_found)
        if not ffmpeg_found:
            self._combo_quality.setToolTip(
                "ffmpeg not found — no MP4 copy is made. "
                "The Master setting is what is recorded.")
        else:
            self._combo_quality.setToolTip("")

        zwo = self._render_kind(st)
        self._feed.set_rect_drawing(
            zwo and bool(st.get("zwo_open", False))
            and not st.get("zwo_recording", False))
        cam_open = st.get("zwo_open", False) if zwo else self._camera.is_open()

        # Video button: enabled only while session recording and camera is open.
        if not rec:
            self._btn_video.setEnabled(False)
            self._btn_video.setToolTip("Start a session first")
            self._btn_video.setText("Start Video")
        elif not cam_open:
            self._btn_video.setEnabled(False)
            self._btn_video.setToolTip("Open the camera first")
            self._btn_video.setText("Start Video")
        else:
            self._btn_video.setEnabled(True)
            self._btn_video.setToolTip("")
            running = st.get("zwo_recording", False) if zwo else video_active
            self._btn_video.setText("Stop Video" if running else "Start Video")

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

        self._btn_photo.setEnabled(cam_open)
        self._btn_note.setEnabled(rec)
