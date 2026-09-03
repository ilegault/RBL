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

Phase 1 fix (2026-08-25): replaced the QLabel live feed with VideoView.
The old QLabel + setPixmap() caused a size ratchet: a label holding a pixmap
reports the pixmap's size as its minimumSizeHint(), which grew on every frame
and latched the DragPanel's scroll area open, clipping the content whenever
the panel was dragged smaller.  VideoView's minimumSizeHint() is a constant
(64x48), breaking the loop.  No frame data was ever affected — only the
preview widget layout.

Phase 2 (2026-08-25): UI restructured to expose the codec controls that
actually affect what lands on disk.  See recording_config.MASTER_CODECS.
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)

from rbl.config.recording_config import (
    CSV_INTERVAL_MAX_S,
    CSV_INTERVAL_MIN_S,
    MASTER_CODEC_DEFAULT,
    MASTER_CODECS,
    PREVIEW_FPS_DEFAULT,
    QUALITY_DEFAULT,
    QUALITY_PRESETS,
    RECORD_FPS_MAX,
    RECORD_FPS_MIN,
    RES_PRESETS,
    SEGMENT_SECONDS_CHOICES,
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


class RecordingPanel(QGroupBox):
    """Compact session recorder panel for the Overview PanelArea column."""

    def __init__(self, recorder, parent=None):
        super().__init__("Session Recorder", parent)
        self._recorder = recorder
        self._camera   = recorder._camera
        self._blocking = False

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

        # ---- Resolution + acquire fps + record fps -------------------------
        s1 = QHBoxLayout()
        s1.setSpacing(4)
        s1.addWidget(QLabel("Res:"))
        self._combo_res = QComboBox()
        for w, h, label in RES_PRESETS:
            self._combo_res.addItem(label, (w, h))
        self._combo_res.setCurrentIndex(len(RES_PRESETS) - 1)
        s1.addWidget(self._combo_res)

        s1.addWidget(QLabel("Acquire:"))
        self._spin_preview = QSpinBox()
        self._spin_preview.setRange(1, 120)
        self._spin_preview.setValue(PREVIEW_FPS_DEFAULT)
        self._spin_preview.setSuffix(" fps")
        self._spin_preview.setToolTip(
            "Frame rate requested from the camera. This is the acquisition "
            "rate — the Record box below only selects which of these frames "
            "are written. Lower it to make an uncompressed pixel format "
            "available at full resolution.")
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
        lay.addLayout(s1)

        # ---- Format combo (pixel format sent to camera) --------------------
        s_fmt = QHBoxLayout()
        s_fmt.setSpacing(4)
        s_fmt.addWidget(QLabel("Format:"))
        self._combo_fmt = QComboBox()
        for label, data in _CAMERA_FORMATS:
            self._combo_fmt.addItem(label, data)
        self._combo_fmt.setToolTip(
            "Pixel format requested from the camera.\n"
            "MJPG: compressed JPEG stream, available at all resolutions and frame rates.\n"
            "YUY2: uncompressed 4:2:2 — eliminates in-camera lossy encoding but "
            "requires lower frame rates at full resolution (typically 5 fps at 1080p).")
        s_fmt.addStretch()
        s_fmt.addWidget(self._combo_fmt)
        lay.addLayout(s_fmt)

        # ---- Live feed (VideoView — constant minimum size, no ratchet) -----
        self._feed = VideoView()
        self._feed.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._feed, stretch=1)

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
        for i in range(self._combo_seg.count()):
            if self._combo_seg.itemData(i) == self._recorder._segment_seconds:
                self._combo_seg.setCurrentIndex(i)
                break
        self._combo_seg.currentIndexChanged.connect(self._on_segment_changed)
        s2.addWidget(self._combo_seg)
        lay.addLayout(s2)

        # ---- Master codec row ----------------------------------------------
        s3 = QHBoxLayout()
        s3.setSpacing(4)
        s3.addWidget(QLabel("Master:"))
        self._combo_master = QComboBox()
        for label in MASTER_CODECS:
            self._combo_master.addItem(label)
        self._combo_master.setCurrentText(MASTER_CODEC_DEFAULT)
        self._combo_master.setToolTip(
            "On-disk codec — the compression that actually runs in the lab.\n\n"
            "MJPEG q98/q100: re-encodes each frame as JPEG; fast, small.\n"
            "FFV1 lossless: bit-exact, ~3× larger. Writes .mkv.\n"
            "PNG sequence: one PNG per frame in a folder; completely lossless,\n"
            "  independently openable. Only available at ≤ 10 fps.\n\n"
            "The MP4 copy (CRF) setting below is separate and requires ffmpeg.")
        self._combo_master.currentTextChanged.connect(self._on_master_changed)
        s3.addWidget(self._combo_master)
        lay.addLayout(s3)

        # ---- MP4 copy (CRF) row — disabled when ffmpeg absent --------------
        s4 = QHBoxLayout()
        s4.setSpacing(4)
        s4.addWidget(QLabel("MP4 copy (CRF):"))
        self._combo_quality = QComboBox()
        for label in QUALITY_PRESETS:
            self._combo_quality.addItem(label)
        self._combo_quality.setCurrentText(QUALITY_DEFAULT)
        self._combo_quality.currentTextChanged.connect(self._on_quality_changed)
        s4.addWidget(self._combo_quality)

        self._chk_video = QCheckBox("Record video")
        self._chk_video.setChecked(self._recorder._video_enabled)
        self._chk_video.toggled.connect(self._on_video_enabled_changed)
        s4.addWidget(self._chk_video)
        lay.addLayout(s4)

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
        self._btn_photo.setToolTip(
            "Saves a lossless PNG plus a beamline-state sidecar, independent "
            "of the video settings. For a shot that matters, this is the "
            "highest-quality path in the app.")
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
        # Clamp record fps to acquire fps so the spinbox cannot request more
        # frames than the camera delivers.
        self._spin_rec_fps.setMaximum(min(RECORD_FPS_MAX, max(1, int(fps))))
        self._btn_open.setText("Close")
        self._combo_cam.setEnabled(False)
        self._render()

    def _on_format_ready(self, fourcc: str):
        """Append the actual pixel format to the resolution label."""
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

    def _on_master_changed(self, label: str):
        if self._blocking:
            return
        self._recorder.set_master_codec(label)
        self._update_png_guard()

    def _on_video_enabled_changed(self, on: bool):
        if self._blocking:
            return
        self._recorder.set_video_enabled(on)

    def _update_png_guard(self):
        """Disable the PNG option when record_fps > 10."""
        model = self._combo_master.model()
        for i in range(model.rowCount()):
            label = model.item(i).text()
            if "PNG" in label:
                enabled = self._spin_rec_fps.value() <= 10
                item = model.item(i)
                if not enabled:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    item.setToolTip(
                        "PNG sequence is not available at > 10 fps — "
                        "imwrite at 30 fps stalls the camera thread and fills "
                        "any disk. Lower the Record fps first.")
                else:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)
                    item.setToolTip("")
                break

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
        self._spin_csv.setValue(self._recorder._csv_interval_s)
        self._combo_quality.setCurrentText(self._recorder._quality_label)
        self._combo_master.setCurrentText(self._recorder._master_codec)
        self._chk_video.setChecked(self._recorder._video_enabled)
        self._blocking = False

        for w in (self._spin_rec_fps, self._spin_csv, self._combo_seg,
                  self._combo_master, self._combo_quality, self._chk_video):
            w.setEnabled(not rec)

        # Video checkbox: disabled if no camera or no cv2.
        if not self._camera.is_open() or not _CV2_OK:
            self._chk_video.setEnabled(False)
            tip = ("Camera must be open to record video."
                   if _CV2_OK else "opencv-python not installed.")
            self._chk_video.setToolTip(tip)

        # CRF combo: disabled when ffmpeg absent — on lab machines it is inert.
        ffmpeg_found = st.get("ffmpeg_found", False)
        self._combo_quality.setEnabled(not rec and ffmpeg_found)
        if not ffmpeg_found:
            self._combo_quality.setToolTip(
                "ffmpeg not found — no MP4 copy is made. "
                "The Master setting above is what is recorded.")
        else:
            self._combo_quality.setToolTip("")

        self._update_png_guard()

        if rec:
            self._btn_session.setText("■ STOP SESSION")
            self._btn_session.setStyleSheet(
                f"background: {theme.FAULT}; color: white; font-weight: bold;")
        else:
            self._btn_session.setText("● START SESSION")
            self._btn_session.setStyleSheet("")

        if rec:
            elapsed = st["elapsed_s"]
            h = int(elapsed) // 3600
            m = (int(elapsed) % 3600) // 60
            s = int(elapsed) % 60
            self._lbl_status.setText(
                f"{st['session_id']}  ·  {h:02d}:{m:02d}:{s:02d}")
            self._lbl_status.setStyleSheet(
                f"color: {theme.OK}; font-size: {theme.FS_CAPTION}px;")

            frames_written = st["frames_written"]
            stats = (f"{st['csv_rows']} rows"
                     f"  ·  {frames_written} frames"
                     f"  ·  {st['segments_done']} segments")
            if st["transcode_pending"]:
                stats += f"  ·  transcode: {st['transcode_pending']} pending"
            free_gb = st["disk_free_bytes"] / 1024**3
            stats += f"\ndisk: {free_gb:.0f} GB free"

            # Estimated disk rate after enough frames to be meaningful.
            if frames_written >= 50 and elapsed > 0:
                bytes_written = st.get("bytes_written", 0)
                if bytes_written > 0:
                    rate_gb_h = (bytes_written / frames_written
                                 * self._recorder._record_fps * 3600 / 1e9)
                    stats += f"  ·  est. {rate_gb_h:.1f} GB/h"

            self._lbl_stats.setText(stats)
        else:
            if st["session_id"]:
                self._lbl_status.setText(f"Last: {st['session_id']}")
            else:
                self._lbl_status.setText("No session")
            self._lbl_status.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            self._lbl_stats.setText(
                "" if ffmpeg_found else "ffmpeg not found — keeping master only")

        self._btn_photo.setEnabled(self._camera.is_open())
        self._btn_note.setEnabled(rec)
