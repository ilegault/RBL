"""
zwo_controls.py
Reusable ZWO camera settings, preview adjustments, and status widget.

WHY THIS EXISTS
---------------
The Overview tab's recording panel and the Camera tab must show identical
controls and status for the ZWO scientific camera (ticket 73, spec D11).
Duplicating these widgets across both panels would risk drift in ranges,
snapping rules, and signal handling.

This widget owns no hardware or camera state; it is a pure view of the
SessionRecorder. All displayed values come from the snapped settings
confirmed by the Capture Process (`state()["zwo_settings"]`), never from
what was typed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rbl.config.capture_config import (
    CAPTURE_DARK_FRAMES_DEFAULT,
    CAPTURE_KEEP_ONE_IN_N_DEFAULT,
    CAPTURE_KEEP_ONE_IN_N_MAX,
    PREVIEW_BRIGHTNESS_DEFAULT,
    PREVIEW_BRIGHTNESS_MAX,
    PREVIEW_BRIGHTNESS_MIN,
    ZWO_EXPOSURE_US_DEFAULT,
    ZWO_GAIN_DEFAULT,
)
from rbl.gui.widgets.inputs import (
    NoScrollComboBox,
    NoScrollSlider,
    NoScrollSpinBox,
    QuietDoubleSpinBox,
)

if TYPE_CHECKING:
    from rbl.services.session_recorder import SessionRecorder


def preview_caption(mode: str) -> str:
    """Return the display caption for the given preview stretch mode.

    The preview is for operator observation only and does not represent
    scientific intensity measurements.
    """
    if mode == "auto":
        return "Display stretch: not intensity"
    return "Linear display"


class ZwoControls(QWidget):
    """Widget displaying ZWO camera settings, preview sliders, and status."""

    def __init__(self, recorder: SessionRecorder, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recorder = recorder

        self._build_ui()
        self._connect_signals()
        self._render()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # Exposure & Gain
        row_exp_gain = QHBoxLayout()
        row_exp_gain.setSpacing(4)
        row_exp_gain.addWidget(QLabel("Exposure:"))
        self.spin_exposure_ms = QuietDoubleSpinBox(self)
        self.spin_exposure_ms.setDecimals(3)
        self.spin_exposure_ms.setRange(0.001, 100_000.0)
        self.spin_exposure_ms.setValue(ZWO_EXPOSURE_US_DEFAULT / 1000.0)
        row_exp_gain.addWidget(self.spin_exposure_ms)
        row_exp_gain.addWidget(QLabel("ms"))

        row_exp_gain.addWidget(QLabel("Gain:"))
        self.spin_gain = NoScrollSpinBox(self)
        self.spin_gain.setRange(0, 1000)
        self.spin_gain.setValue(ZWO_GAIN_DEFAULT)
        row_exp_gain.addWidget(self.spin_gain)
        lay.addLayout(row_exp_gain)

        # Bit depth & Keep 1 in N
        row_depth_keep = QHBoxLayout()
        row_depth_keep.setSpacing(4)
        row_depth_keep.addWidget(QLabel("Bit depth:"))
        self.combo_bit_depth = NoScrollComboBox(self)
        self.combo_bit_depth.addItem("RAW16 (default)", 16)
        self.combo_bit_depth.addItem("RAW8", 8)
        row_depth_keep.addWidget(self.combo_bit_depth)

        row_depth_keep.addWidget(QLabel("Keep 1 in N:"))
        self.spin_keep_one_in_n = NoScrollSpinBox(self)
        self.spin_keep_one_in_n.setRange(1, CAPTURE_KEEP_ONE_IN_N_MAX)
        self.spin_keep_one_in_n.setValue(CAPTURE_KEEP_ONE_IN_N_DEFAULT)
        row_depth_keep.addWidget(self.spin_keep_one_in_n)
        lay.addLayout(row_depth_keep)

        # ROI controls
        row_roi = QHBoxLayout()
        row_roi.setSpacing(4)
        row_roi.addWidget(QLabel("ROI:"))
        self.spin_roi_x = NoScrollSpinBox(self)
        self.spin_roi_x.setRange(0, 10000)
        row_roi.addWidget(self.spin_roi_x)

        self.spin_roi_y = NoScrollSpinBox(self)
        self.spin_roi_y.setRange(0, 10000)
        row_roi.addWidget(self.spin_roi_y)

        self.spin_roi_width = NoScrollSpinBox(self)
        self.spin_roi_width.setRange(0, 10000)
        row_roi.addWidget(self.spin_roi_width)

        self.spin_roi_height = NoScrollSpinBox(self)
        self.spin_roi_height.setRange(0, 10000)
        row_roi.addWidget(self.spin_roi_height)

        self.btn_full_sensor = QPushButton("Full sensor", self)
        row_roi.addWidget(self.btn_full_sensor)
        lay.addLayout(row_roi)

        # Preview & Brightness
        row_prev = QHBoxLayout()
        row_prev.setSpacing(4)
        row_prev.addWidget(QLabel("Preview:"))
        self.combo_preview = NoScrollComboBox(self)
        self.combo_preview.addItem("Auto stretch", "auto")
        self.combo_preview.addItem("Linear", "linear")
        row_prev.addWidget(self.combo_preview)

        row_prev.addWidget(QLabel("Brightness:"))
        self.slider_brightness = NoScrollSlider(Qt.Orientation.Horizontal, self)
        self.slider_brightness.setRange(
            int(PREVIEW_BRIGHTNESS_MIN * 100),
            int(PREVIEW_BRIGHTNESS_MAX * 100),
        )
        self.slider_brightness.setValue(int(PREVIEW_BRIGHTNESS_DEFAULT * 100))
        row_prev.addWidget(self.slider_brightness)
        lay.addLayout(row_prev)

        # Take Darks
        row_darks = QHBoxLayout()
        row_darks.setSpacing(4)
        self.btn_take_darks = QPushButton("Take Darks", self)
        row_darks.addWidget(self.btn_take_darks)
        lay.addLayout(row_darks)

        # Status line
        self.lbl_status = QLabel("", self)
        lay.addWidget(self.lbl_status)

    def _connect_signals(self) -> None:
        self.spin_exposure_ms.editingFinished.connect(self._on_exposure_edited)
        self.spin_gain.valueChanged.connect(self._on_gain_changed)
        self.combo_bit_depth.currentIndexChanged.connect(self._on_bit_depth_changed)
        self.spin_keep_one_in_n.valueChanged.connect(self._on_keep_one_in_n_changed)

        for spn in (self.spin_roi_x, self.spin_roi_y, self.spin_roi_width, self.spin_roi_height):
            spn.editingFinished.connect(self._on_roi_edited)

        self.btn_full_sensor.clicked.connect(self._on_full_sensor_clicked)

        self.combo_preview.currentIndexChanged.connect(self._on_preview_changed)
        self.slider_brightness.valueChanged.connect(self._on_preview_changed)

        self.btn_take_darks.clicked.connect(self._on_take_darks_clicked)

        self._recorder.state_changed.connect(self._render)
        self._recorder.settings_changed.connect(self._render)

    # ---- User write handlers ------------------------------------------------

    def _on_exposure_edited(self) -> None:
        ms = self.spin_exposure_ms.value()
        self._recorder.set_zwo_settings(exposure_us=round(ms * 1000))

    def _on_gain_changed(self, value: int) -> None:
        self._recorder.set_zwo_settings(gain=value)

    def _on_bit_depth_changed(self, idx: int) -> None:
        depth = self.combo_bit_depth.currentData()
        if depth is not None:
            self._recorder.set_zwo_settings(bit_depth=int(depth))

    def _on_keep_one_in_n_changed(self, value: int) -> None:
        self._recorder.set_zwo_settings(keep_one_in_n=value)

    def _on_roi_edited(self) -> None:
        roi = {
            "x": self.spin_roi_x.value(),
            "y": self.spin_roi_y.value(),
            "width": self.spin_roi_width.value(),
            "height": self.spin_roi_height.value(),
        }
        self._recorder.set_zwo_settings(roi=roi)

    def _on_full_sensor_clicked(self) -> None:
        info = self._recorder.state().get("zwo_info", {})
        sw = info.get("sensor_width", 0)
        sh = info.get("sensor_height", 0)
        if sw > 0 and sh > 0:
            self._recorder.set_zwo_settings(roi={"x": 0, "y": 0, "width": sw, "height": sh})

    def _on_preview_changed(self, *args: Any) -> None:
        mode = self.combo_preview.currentData() or "auto"
        brightness = self.slider_brightness.value() / 100.0
        self._recorder.set_zwo_preview(mode, brightness)

    def confirm_darks(self) -> bool:
        """Ask operator to cap lens before taking dark frames.

        Returns True if confirmed, False otherwise.
        """
        answer = QMessageBox.question(
            self,
            "Dark Frames",
            "Cap the lens before taking Dark Frames.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_take_darks_clicked(self) -> None:
        if self.confirm_darks():
            self._recorder.take_darks(CAPTURE_DARK_FRAMES_DEFAULT)

    # ---- Render state -------------------------------------------------------

    def _render(self) -> None:
        """Render widget state from recorder.state()."""
        state = self._recorder.state()
        zwo_open = state.get("zwo_open", False)
        zwo_recording = state.get("zwo_recording", False)
        info = state.get("zwo_info", {})
        settings = state.get("zwo_settings", {})
        status = state.get("zwo_status", {})

        # Enablement of inputs
        inputs = [
            self.spin_exposure_ms,
            self.spin_gain,
            self.combo_bit_depth,
            self.spin_roi_x,
            self.spin_roi_y,
            self.spin_roi_width,
            self.spin_roi_height,
            self.btn_full_sensor,
            self.spin_keep_one_in_n,
            self.combo_preview,
            self.slider_brightness,
        ]
        for inp in inputs:
            inp.setEnabled(zwo_open)

        self.btn_take_darks.setEnabled(zwo_open and not zwo_recording)

        # Control ranges
        controls = info.get("controls", {})
        if "Exposure" in controls:
            exp_ctrl = controls["Exposure"]
            self.spin_exposure_ms.setRange(exp_ctrl["min"] / 1000.0, exp_ctrl["max"] / 1000.0)

        if "Gain" in controls:
            gain_ctrl = controls["Gain"]
            self.spin_gain.setRange(gain_ctrl["min"], gain_ctrl["max"])

        sw = info.get("sensor_width", 0)
        sh = info.get("sensor_height", 0)
        if sw > 0:
            self.spin_roi_x.setRange(0, sw)
            self.spin_roi_width.setRange(0, sw)
        if sh > 0:
            self.spin_roi_y.setRange(0, sh)
            self.spin_roi_height.setRange(0, sh)

        # Display values with signals blocked
        for w in inputs:
            w.blockSignals(True)
        try:
            if "exposure_us" in settings:
                self.spin_exposure_ms.setValue(settings["exposure_us"] / 1000.0)

            if "gain" in settings:
                self.spin_gain.setValue(settings["gain"])

            if "bit_depth" in settings:
                idx = self.combo_bit_depth.findData(settings["bit_depth"])
                if idx >= 0:
                    self.combo_bit_depth.setCurrentIndex(idx)

            if "roi" in settings and settings["roi"]:
                r = settings["roi"]
                if isinstance(r, dict):
                    self.spin_roi_x.setValue(r.get("x", 0))
                    self.spin_roi_y.setValue(r.get("y", 0))
                    self.spin_roi_width.setValue(r.get("width", 0))
                    self.spin_roi_height.setValue(r.get("height", 0))
                elif isinstance(r, (tuple, list)) and len(r) == 4:
                    self.spin_roi_x.setValue(r[0])
                    self.spin_roi_y.setValue(r[1])
                    self.spin_roi_width.setValue(r[2])
                    self.spin_roi_height.setValue(r[3])

            if "keep_one_in_n" in settings:
                self.spin_keep_one_in_n.setValue(settings["keep_one_in_n"])

            if "preview_mode" in settings:
                idx = self.combo_preview.findData(settings["preview_mode"])
                if idx >= 0:
                    self.combo_preview.setCurrentIndex(idx)

            if "brightness" in settings:
                self.slider_brightness.setValue(round(settings["brightness"] * 100))
        finally:
            for w in inputs:
                w.blockSignals(False)

        # Status line
        if status and "acquired_fps" in status:
            acquired_fps = status.get("acquired_fps", 0.0)
            frames_written = status.get("frames_written", 0)
            dropped = status.get("frames_dropped_camera", 0) + status.get("frames_dropped_queue", 0)
            temp_c = status.get("sensor_temperature_c", 0.0)
            bytes_per_hour = status.get("bytes_per_hour", 0.0)
            disk_free_bytes = status.get("disk_free_bytes", 0)

            self.lbl_status.setText(
                f"{acquired_fps:.1f} fps · {frames_written} written · {dropped} dropped · "
                f"{temp_c:.1f} °C · ≈{bytes_per_hour / 1e9:.1f} GB/h · "
                f"{disk_free_bytes / 1024**3:.0f} GB free"
            )
        else:
            self.lbl_status.setText("")
