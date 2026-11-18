"""
axis_drive.py
One steering AXIS's raster drive: amplitude, offset, frequency, and output enable.

The Function Generators tab is organised per channel because that is how the
instruments are organised. This widget is organised per axis because that is
how the beam is: X+ and X- are one push-pull pair driven from one generator,
and setting them to different amplitudes or frequencies does not steer the
beam differently — it just breaks the differential drive. So the Overview
offers one amplitude and one frequency per axis and holds the pair together,
including the 0 deg / 180 deg phase relationship, which is never exposed here.

AMPLITUDE IS IN Vpp, the same number the Function Generators tab shows and the
instrument itself takes. An earlier version showed peak volts here so it would
read straight across to the amplifier bars, but that put a factor of two
between this box and every other screen: the same setpoint read 2.0 here and
4.0 on the FG tab, and two screens disagreeing about one setpoint is exactly
what the shared model exists to prevent. One unit from the box to the
instrument means no conversion exists anywhere in the path. What the operator
needs to stay inside the +/-5 V interlock is shown separately, as the INPUT
PEAK line under the boxes (see CONTEXT.md "Raster drive"), computed by
peak_status so it classifies exactly as the FG tab does, using the larger of
the axis's two channels' input peaks from each channel's real shape and offset.

Offset is controlled with a Link ± toggle (owned by FuncGenSetpoints). When
linked, one Offset box sets the + plate to +offset and the - plate to -offset.
When unlinked, two per-plate boxes edit each channel independently. Offset boxes
never clamp against amplitude so the operator can type freely and see the input
peak line turn FAULT when over the ±5 V limit.

Commands nothing. It edits a shared FuncGenSetpoints entry through its owner
(signals out) and renders whatever readback it is handed — every path to a
generator still goes through Beamline.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from rbl.gui import theme
from rbl.gui.widgets.inputs import QuietDoubleSpinBox, unit_row
from rbl.gui.widgets.mini import VMiniBar
from rbl.hardware.funcgen_driver import MAX_AMP_VPP, MAX_GEN_VOLTS
from rbl.hardware.funcgen_safety import PEAK_WARN_VOLTS, peak_status
from rbl.state.setpoints import AXIS_CHANNELS, AXIS_GENERATOR


class AxisDriveControl(QGroupBox):
    """Amplitude / offset / frequency / output for one steering axis (both its channels)."""

    # axis label, amplitude Vpp (native units), frequency Hz
    params_edited         = Signal(str, float, float)
    offset_edited         = Signal(str, float)
    channel_offset_edited = Signal(str, float)
    link_toggled          = Signal(str, bool)
    output_toggled        = Signal(str, bool)

    def __init__(self, axis: str, parent=None):
        self.axis     = axis
        self.channels = AXIS_CHANNELS[axis]          # ("A1", "A2")
        self.slits    = (f"{axis}+", f"{axis}-")
        gen           = AXIS_GENERATOR[axis]
        super().__init__(f"{axis} axis — {self.slits[0]} / {self.slits[1]}  (Gen {gen})",
                         parent)

        # Suppresses the edited signals while the shared setpoint model is
        # writing INTO this widget, so a sync from the other tab is not
        # mistaken for the operator typing here.
        self._syncing = False
        self._plus_shape = "Triangle"
        self._minus_shape = "Triangle"
        self._plus_amp_vpp = 0.0
        self._minus_amp_vpp = 0.0

        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # The bar sits BESIDE the entry boxes rather than under them: vertical,
        # it costs ~50 px of width instead of ~40 px of height, which is what
        # lets both axis panels fit side by side on one row.
        row = QHBoxLayout()
        row.setSpacing(4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(3)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        self.spn_amp = QuietDoubleSpinBox()
        self.spn_amp.setRange(0.0, MAX_AMP_VPP)
        self.spn_amp.setDecimals(3)
        self.spn_amp.setSingleStep(0.1)
        self.spn_amp.setMinimumWidth(20)
        self.spn_amp.setMaximumWidth(120)
        self.spn_amp.setMinimumHeight(22)
        self.spn_amp.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        self.spn_amp.setToolTip(
            f"Peak-to-peak amplitude (Vpp) on both {self.slits[0]} and "
            f"{self.slits[1]}, the same number the Function Generators tab "
            "shows.\n"
            "The input peak line below shows what the amplifier input sees."
        )
        self.spn_amp.valueChanged.connect(self._on_edited)
        # Units go in a label beside each box, never in the box as a spinbox
        # suffix: a suffix is part of the editable text, so the cursor can land
        # behind it and a select-all-and-retype takes it with it. That is the
        # "weird bugs" the units inside these boxes were causing.
        form.addRow(self._form_label("Amplitude:"),
                    unit_row(self.spn_amp, "Vpp", font_size=theme.FS_LABEL))

        self.spn_freq = QuietDoubleSpinBox()
        self.spn_freq.setRange(0.0001, 25_000_000.0)
        self.spn_freq.setDecimals(4)
        self.spn_freq.setMinimumWidth(20)
        self.spn_freq.setMaximumWidth(120)
        self.spn_freq.setMinimumHeight(22)
        self.spn_freq.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        self.spn_freq.setToolTip(
            f"Sweep rate on this axis. Both {self.slits[0]} and {self.slits[1]} "
            "run at the same frequency — a mismatched pair is not a raster.\n"
            "Sub-hertz rates are typed in full (e.g. 0.514) — the box no "
            "longer commits the first digit you type."
        )
        self.spn_freq.valueChanged.connect(self._on_edited)
        form.addRow(self._form_label("Frequency:"),
                    unit_row(self.spn_freq, "Hz", font_size=theme.FS_LABEL))

        self.chk_link = QCheckBox("Link ±")
        self.chk_link.setChecked(True)
        self.chk_link.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        self.chk_link.setToolTip(
            f"Hold {self.slits[0]} and {self.slits[1]} offsets equal and opposite "
            "(+offset on +, −offset on −).\n"
            "Untick to set each plate's DC offset independently."
        )
        self.chk_link.toggled.connect(self._on_link_toggled)
        form.addRow("", self.chk_link)

        self.spn_offset = self._make_offset_spinbox(
            f"Differential offset (V): {self.slits[0]} gets +offset, "
            f"{self.slits[1]} gets −offset."
        )
        self.spn_offset.valueChanged.connect(self._on_offset_edited)
        self._linked_offset_row = unit_row(self.spn_offset, "V", font_size=theme.FS_LABEL)
        form.addRow(self._form_label("Offset:"), self._linked_offset_row)

        self.spn_offset_plus = self._make_offset_spinbox(
            f"DC offset (V) on {self.slits[0]} ({self.channels[0]})."
        )
        self.spn_offset_plus.valueChanged.connect(
            lambda v: self._on_channel_offset_edited(self.channels[0], v)
        )
        self._plus_offset_row = unit_row(self.spn_offset_plus, "V", font_size=theme.FS_LABEL)
        form.addRow(self._form_label(f"{self.slits[0]} offset:"), self._plus_offset_row)

        self.spn_offset_minus = self._make_offset_spinbox(
            f"DC offset (V) on {self.slits[1]} ({self.channels[1]})."
        )
        self.spn_offset_minus.valueChanged.connect(
            lambda v: self._on_channel_offset_edited(self.channels[1], v)
        )
        self._minus_offset_row = unit_row(self.spn_offset_minus, "V", font_size=theme.FS_LABEL)
        form.addRow(self._form_label(f"{axis}− offset:"), self._minus_offset_row)

        self._form = form
        self._apply_link_visibility(True)

        row.addLayout(form, stretch=1)

        # Commanded amplitude marked on the same track as the measured one, so
        # "did the apply land?" is one glance rather than two numbers compared
        # in your head (the same idiom as the slit position bars).
        self.bar = VMiniBar(f"{self.slits[0]}/{self.slits[1]}", 0.0, MAX_AMP_VPP,
                            unit="Vpp", color=theme.SLIT_COLORS[self.slits[0]],
                            decimals=2)
        # Minimum wide enough for the value label; no fixed cap so the bar
        # can grow with available space and compress when squeezed.
        self.bar.setMinimumWidth(20)
        row.addWidget(self.bar)
        lay.addLayout(row)

        # The input peak the amplitude produces — the consequence, directly
        # under the cause. Full width and NOT
        # wrapping: a wrapping label inside the form above reports a height
        # the form does not reserve, and the second line lands under the
        # button below it.
        self.lbl_hv = QLabel("")
        self.lbl_hv.setStyleSheet(
            f"color: #7a2000; font-size: {theme.FS_CAPTION}px;")
        lay.addWidget(self.lbl_hv)

        self.btn_output = QPushButton("Output OFF")
        self.btn_output.setCheckable(True)
        self.btn_output.setMinimumHeight(24)
        self.btn_output.setStyleSheet(
            "QPushButton { background:#8c0000; color:white; font-weight:bold;"
            f" font-size:{theme.FS_LABEL}px; }}"
            "QPushButton:checked { background:#1a7000; color:white; font-weight:bold; }"
            "QPushButton:hover { background:#a00000; }"
            "QPushButton:checked:hover { background:#228a00; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_output.setToolTip(
            "Commanded output state for both channels of this axis.\n"
            "Like the Function Generators tab, this is an intent — it reaches "
            "the instrument on the next Apply, not on the click."
        )
        self.btn_output.toggled.connect(self._on_output_toggled)
        lay.addWidget(self.btn_output)

        self.lbl_readback = QLabel("—")
        self.lbl_readback.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {theme.FS_CAPTION}px; color: {theme.NEUTRAL};"
        )
        self.lbl_readback.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.lbl_readback)

        self.set_connected(False)
        self._update_hv_label()

    @staticmethod
    def _form_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        return lbl

    @staticmethod
    def _make_offset_spinbox(tooltip: str) -> QuietDoubleSpinBox:
        spn = QuietDoubleSpinBox()
        spn.setRange(-MAX_GEN_VOLTS, MAX_GEN_VOLTS)
        spn.setDecimals(4)
        spn.setSingleStep(0.1)
        spn.setMinimumWidth(20)
        spn.setMaximumWidth(120)
        spn.setMinimumHeight(22)
        spn.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        spn.setToolTip(tooltip)
        return spn

    def _apply_link_visibility(self, linked: bool) -> None:
        self._form.setRowVisible(self._linked_offset_row, linked)
        self._form.setRowVisible(self._plus_offset_row, not linked)
        self._form.setRowVisible(self._minus_offset_row, not linked)

    # ---- Setpoint in / out -----------------------------------------------------

    def set_setpoint(
        self,
        amp_vpp: float,
        freq_hz: float,
        output_on: bool,
        matched: bool = True,
        *,
        linked: bool = True,
        plus_offset_v: float = 0.0,
        minus_offset_v: float = 0.0,
        plus_shape: str = "Triangle",
        minus_shape: str = "Triangle",
        plus_amp_vpp: float | None = None,
        minus_amp_vpp: float | None = None,
    ):
        """Render the shared setpoint. Never emits — this is a sync, not an edit.

        Written through sync_value() rather than setValue(): this is the path a
        Function Generators tab edit arrives on, and it must not reformat a
        number the operator is part-way through typing in these boxes.
        """
        self._syncing = True
        try:
            self.spn_amp.sync_value(amp_vpp)
            self.spn_freq.sync_value(freq_hz)
            self.spn_offset.sync_value(plus_offset_v)
            self.spn_offset_plus.sync_value(plus_offset_v)
            self.spn_offset_minus.sync_value(minus_offset_v)
            self.chk_link.setChecked(linked)
            self.btn_output.setChecked(output_on)
        finally:
            self._syncing = False
        self._plus_shape = plus_shape
        self._minus_shape = minus_shape
        self._plus_amp_vpp = amp_vpp if plus_amp_vpp is None else plus_amp_vpp
        self._minus_amp_vpp = amp_vpp if minus_amp_vpp is None else minus_amp_vpp
        self._apply_link_visibility(linked)
        self.btn_output.setText("Output ON" if output_on else "Output OFF")
        self.bar.set_target(amp_vpp)
        self._update_hv_label(matched)

    def _on_edited(self, *_):
        if self._syncing:
            return
        self._plus_amp_vpp = self._minus_amp_vpp = self.spn_amp.value()
        self._update_hv_label()
        self.bar.set_target(self.spn_amp.value())
        self.params_edited.emit(self.axis, self.spn_amp.value(),
                                self.spn_freq.value())

    def _on_offset_edited(self, value: float):
        if self._syncing:
            return
        self._update_hv_label()
        self.offset_edited.emit(self.axis, value)

    def _on_channel_offset_edited(self, key: str, value: float):
        if self._syncing:
            return
        self._update_hv_label()
        self.channel_offset_edited.emit(key, value)

    def _on_link_toggled(self, checked: bool):
        self._apply_link_visibility(checked)
        if self._syncing:
            return
        self._update_hv_label()
        self.link_toggled.emit(self.axis, checked)

    def _on_output_toggled(self, checked: bool):
        self.btn_output.setText("Output ON" if checked else "Output OFF")
        if self._syncing:
            return
        self.output_toggled.emit(self.axis, checked)

    # ---- Readback in -----------------------------------------------------------

    def set_readback(self, channels: dict, connected: bool):
        """`channels`: channel key -> ChannelSnapshot, for this axis's two channels."""
        plus  = channels.get(self.channels[0])
        minus = channels.get(self.channels[1])
        self.bar.set(None if plus is None else plus.amp_vpp,
                     stale=not connected)

        style = ("font-family: Consolas, 'Courier New', monospace; "
                 f"font-size: {theme.FS_CAPTION}px; color: {{}};")
        if not connected:
            self.lbl_readback.setText(f"Gen {AXIS_GENERATOR[self.axis]} not connected")
            self.lbl_readback.setStyleSheet(style.format(theme.NEUTRAL))
            return
        if plus is None or minus is None:
            self.lbl_readback.setText("waiting for readback")
            self.lbl_readback.setStyleSheet(style.format(theme.NEUTRAL))
            return

        out = "ON" if (plus.output_on and minus.output_on) else \
              ("OFF" if not (plus.output_on or minus.output_on) else "SPLIT")
        self.lbl_readback.setText(
            f"{self.slits[0]} {plus.amp_vpp:.2f} Vpp {plus.offset_v:+.3f} V · "
            f"{self.slits[1]} {minus.amp_vpp:.2f} Vpp {minus.offset_v:+.3f} V"
            f" @ {plus.freq_hz:.4g} Hz out={out}"
        )
        # A pair that disagrees is not driving a differential raster, whatever
        # the setpoint boxes say — call it out on the live line, not just the
        # setpoint one.
        split = (abs(plus.amp_vpp - minus.amp_vpp) > 1e-6
                 or abs(plus.freq_hz - minus.freq_hz) > 1e-9
                 or plus.output_on != minus.output_on)
        self.lbl_readback.setStyleSheet(
            style.format(theme.WARN if split else theme.NEUTRAL))

    def set_connected(self, on: bool):
        for w in (
            self.spn_amp,
            self.spn_freq,
            self.spn_offset,
            self.spn_offset_plus,
            self.spn_offset_minus,
            self.chk_link,
            self.btn_output,
        ):
            w.setEnabled(on)

    # ---- Consequence label -----------------------------------------------------

    def _update_hv_label(self, matched: bool = True):
        """The larger input peak across this axis's two channels.

        Evaluated by peak_status using each channel's real shape and offset,
        the same function the Function Generators tab uses, so the two screens
        can never classify a peak differently. No kV figure is shown beside an
        input: the operator types Vpp and V and checks volts.
        """
        if self.chk_link.isChecked():
            plus_off = self.spn_offset.value()
            minus_off = -plus_off
        else:
            plus_off = self.spn_offset_plus.value()
            minus_off = self.spn_offset_minus.value()

        plus_status, plus_peak = peak_status(
            self._plus_shape, self._plus_amp_vpp, plus_off)
        minus_status, minus_peak = peak_status(
            self._minus_shape, self._minus_amp_vpp, minus_off)
        if minus_peak > plus_peak:
            status, peak = minus_status, minus_peak
        else:
            status, peak = plus_status, plus_peak

        text = f"input peak {peak:.2f} V"
        if not matched:
            text += "  ⚠ pair differs"
        self.lbl_hv.setText(text)
        self.lbl_hv.setToolTip(
            f"The amplifier input sees a peak of {peak:.3f} V "
            f"(warn above {PEAK_WARN_VOLTS:.0f} V, blocked above 5 V)."
            + ("\n\nThis axis's two channels hold different setpoints - set "
               "them per channel on the Function Generators tab."
               if not matched else "")
        )

        if status == "block":
            role = theme.FAULT
        elif status == "warn" or not matched:
            role = theme.WARN
        else:
            role = None
        self.lbl_hv.setStyleSheet(
            f"color: {role}; font-size: {theme.FS_CAPTION}px; font-weight: bold;"
            if role else f"color: #7a2000; font-size: {theme.FS_CAPTION}px;")
