"""
axis_drive.py
One steering AXIS's raster drive: amplitude, frequency, and output enable.

The Function Generators tab is organised per channel because that is how the
instruments are organised. This widget is organised per axis because that is
how the beam is: X+ and X- are one push-pull pair driven from one generator,
and setting them to different amplitudes or frequencies does not steer the
beam differently — it just breaks the differential drive. So the Overview
offers one amplitude and one frequency per axis and holds the pair together,
including the 0 deg / 180 deg phase relationship, which is never exposed here.

AMPLITUDE IS IN PEAK VOLTS, not the RIGOL's native peak-to-peak. The number an
operator is checking against is the HV amplifier's peak output, and with the
1000x gain a peak volt IS a kV at the plate — so 2.0 here reads straight
across to the 2.00 kV on the amplifier bars below, with no halving in your
head. It is also exactly the quantity the +/-5 V input interlock is defined
on, since offset is fixed at 0 V. The Function Generators tab still shows the
same setpoint in Vpp, which is what its instrument-shaped view should show;
the conversion is one factor of two and lives in this file only.

Offset is not offered at all: the raster runs centred, so offset is 0 V.

Commands nothing. It edits a shared FuncGenSetpoints entry through its owner
(signals out) and renders whatever readback it is handed — every path to a
generator still goes through Beamline.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
from rbl.hardware.funcgen_driver import MAX_AMP_VPP
from rbl.hardware.funcgen_safety import _AMP_GAIN, PEAK_MAX_VOLTS, peak_status
from rbl.state.setpoints import AXIS_CHANNELS, AXIS_GENERATOR

# Peak amplitude ceiling, in volts at the amplifier input. The generator's
# limit is peak-to-peak; at 0 V offset the peak is exactly half of it, and
# that half is also the interlock's ceiling — one number, three meanings.
MAX_AMP_PEAK_V = MAX_AMP_VPP / 2.0


def peak_to_vpp(peak_v: float) -> float:
    """Displayed peak volts -> the RIGOL's native peak-to-peak setpoint."""
    return peak_v * 2.0


def vpp_to_peak(amp_vpp: float) -> float:
    return amp_vpp / 2.0


class AxisDriveControl(QGroupBox):
    """Amplitude / frequency / output for one steering axis (both its channels)."""

    # axis label, amplitude Vpp (native units), frequency Hz
    params_edited  = Signal(str, float, float)
    output_toggled = Signal(str, bool)

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
        self.spn_amp.setRange(0.0, MAX_AMP_PEAK_V)
        self.spn_amp.setDecimals(3)
        self.spn_amp.setSingleStep(0.05)
        self.spn_amp.setMinimumWidth(20)
        self.spn_amp.setMaximumWidth(120)
        self.spn_amp.setMinimumHeight(22)
        self.spn_amp.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        self.spn_amp.setToolTip(
            f"Peak drive on both {self.slits[0]} and {self.slits[1]}, in volts "
            "at the amplifier input.\n"
            "The 1000x gain makes this the kV at the plate directly: 2.0 here "
            "is ±2 kV on each plate.\n"
            f"Offset is 0 V, so this is also the interlock peak and must stay "
            f"within {PEAK_MAX_VOLTS:.0f} V.\n"
            "The Function Generators tab shows the same setpoint peak-to-peak "
            "(twice this)."
        )
        self.spn_amp.valueChanged.connect(self._on_edited)
        # Units go in a label beside each box, never in the box as a spinbox
        # suffix: a suffix is part of the editable text, so the cursor can land
        # behind it and a select-all-and-retype takes it with it. That is the
        # "weird bugs" the units inside these boxes were causing.
        form.addRow(self._form_label("Amplitude:"),
                    unit_row(self.spn_amp, "V pk", font_size=theme.FS_LABEL))

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

        row.addLayout(form, stretch=1)

        # Commanded amplitude marked on the same track as the measured one, so
        # "did the apply land?" is one glance rather than two numbers compared
        # in your head (the same idiom as the slit position bars).
        self.bar = VMiniBar(f"{self.slits[0]}/{self.slits[1]}", 0.0, MAX_AMP_PEAK_V,
                            unit="V pk", color=theme.SLIT_COLORS[self.slits[0]],
                            decimals=2)
        # Minimum wide enough for the value label; no fixed cap so the bar
        # can grow with available space and compress when squeezed.
        self.bar.setMinimumWidth(20)
        row.addWidget(self.bar)
        lay.addLayout(row)

        # What the amplitude does at the plates, and how much rail is left —
        # the consequence, directly under the cause. Full width and NOT
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

    # ---- Setpoint in / out -----------------------------------------------------

    def set_setpoint(self, amp_vpp: float, freq_hz: float, output_on: bool,
                     matched: bool = True):
        """Render the shared setpoint. Never emits — this is a sync, not an edit.

        Written through sync_value() rather than setValue(): this is the path a
        Function Generators tab edit arrives on, and it must not reformat a
        number the operator is part-way through typing in these boxes.
        """
        peak = vpp_to_peak(amp_vpp)
        self._syncing = True
        try:
            self.spn_amp.sync_value(peak)
            self.spn_freq.sync_value(freq_hz)
            self.btn_output.setChecked(output_on)
        finally:
            self._syncing = False
        self.btn_output.setText("Output ON" if output_on else "Output OFF")
        self.bar.set_target(peak)
        self._update_hv_label(matched)

    def _on_edited(self, *_):
        if self._syncing:
            return
        self._update_hv_label()
        self.bar.set_target(self.spn_amp.value())
        # Out in native peak-to-peak: the setpoint model and every driver call
        # below it speak the instrument's units, and only this widget's face
        # is in peak volts.
        self.params_edited.emit(self.axis, peak_to_vpp(self.spn_amp.value()),
                                self.spn_freq.value())

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
        self.bar.set(None if plus is None else vpp_to_peak(plus.amp_vpp),
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
            f"{self.slits[0]} {vpp_to_peak(plus.amp_vpp):.2f} · "
            f"{self.slits[1]} {vpp_to_peak(minus.amp_vpp):.2f} V pk"
            f"  @ {plus.freq_hz:.4g} Hz  out={out}"
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
        for w in (self.spn_amp, self.spn_freq, self.btn_output):
            w.setEnabled(on)

    # ---- Consequence label -----------------------------------------------------

    def _update_hv_label(self, matched: bool = True):
        """What this amplitude does at the plates, and how much rail is left.

        With offset fixed at 0 V the plate swings +/-peak*gain, and the peak
        the EEL5000 input sees is the displayed number itself — the same
        interlock the Function Generators tab enforces, evaluated by the same
        function so the two screens can never classify a peak differently.
        """
        peak_v = self.spn_amp.value()
        status, peak = peak_status("Triangle", peak_to_vpp(peak_v), 0.0)
        plate_kv = peak_v * _AMP_GAIN / 1000.0

        text = f"→ ±{plate_kv:.3f} kV/plate · {peak:.2f} of {PEAK_MAX_VOLTS:.0f} V in"
        if not matched:
            text += "  ⚠ pair differs"
        self.lbl_hv.setText(text)
        self.lbl_hv.setToolTip(
            f"±{plate_kv:.3f} kV on each plate, so {2 * plate_kv:.3f} kV "
            f"differential across the pair.\n"
            f"The amplifier input sees {peak:.3f} V of its "
            f"{PEAK_MAX_VOLTS:.0f} V maximum."
            + ("\n\nThis axis's two channels hold different setpoints — set "
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
