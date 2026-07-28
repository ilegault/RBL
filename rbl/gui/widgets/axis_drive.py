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

Offset is not offered at all: the raster runs centred, so offset is 0 V and the
peak the amplifier sees is simply half the amplitude.

Commands nothing. It edits a shared FuncGenSetpoints entry through its owner
(signals out) and renders whatever readback it is handed — every path to a
generator still goes through Beamline.
"""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSizePolicy,
)

from rbl.hardware.funcgen_driver import MAX_AMP_VPP
from rbl.hardware.funcgen_safety import peak_status, _AMP_GAIN, PEAK_MAX_VOLTS
from rbl.gui import theme
from rbl.gui.widgets.mini import MiniBar
from rbl.state.setpoints import AXIS_CHANNELS, AXIS_GENERATOR


class AxisDriveControl(QGroupBox):
    """Amplitude / frequency / output for one steering axis (both its channels)."""

    # axis label, amplitude Vpp, frequency Hz
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
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(3)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(3)

        def entry_row(spin):
            """Keep a number box number-sized.

            This panel sits in a wide column, and a spinbox stretched across
            all of it reads as a text field rather than a value you nudge.
            """
            spin.setMaximumWidth(130)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(spin)
            row.addStretch(1)
            return row

        self.spn_amp = QDoubleSpinBox()
        self.spn_amp.setRange(0.0, MAX_AMP_VPP)
        self.spn_amp.setDecimals(4)
        self.spn_amp.setSingleStep(0.1)
        self.spn_amp.setSuffix(" Vpp")
        self.spn_amp.setToolTip(
            f"Peak-to-peak drive on both {self.slits[0]} and {self.slits[1]}.\n"
            "Offset is 0 V, so the peak the amplifier input sees is half of this "
            f"and must stay within {PEAK_MAX_VOLTS:.0f} V."
        )
        self.spn_amp.valueChanged.connect(self._on_edited)
        form.addRow("Amplitude:", entry_row(self.spn_amp))

        self.spn_freq = QDoubleSpinBox()
        self.spn_freq.setRange(0.0001, 25_000_000.0)
        self.spn_freq.setDecimals(4)
        self.spn_freq.setSuffix(" Hz")
        self.spn_freq.setToolTip(
            f"Sweep rate on this axis. Both {self.slits[0]} and {self.slits[1]} "
            "run at the same frequency — a mismatched pair is not a raster."
        )
        self.spn_freq.valueChanged.connect(self._on_edited)
        form.addRow("Frequency:", entry_row(self.spn_freq))
        lay.addLayout(form)

        # What the amplitude means at the plates — the number that actually
        # matters, one 1000x gain stage away from the box above it.
        self.lbl_hv = QLabel("")
        self.lbl_hv.setStyleSheet("color: #7a2000; font-size: 10px;")
        lay.addWidget(self.lbl_hv)

        # Commanded amplitude marked on the same track as the measured one, so
        # "did the apply land?" is one glance rather than two numbers compared
        # in your head (the same idiom as the slit position bars).
        self.bar = MiniBar(f"{self.slits[0]}/{self.slits[1]} amplitude",
                           0.0, MAX_AMP_VPP, unit="Vpp",
                           color=theme.SLIT_COLORS[self.slits[0]])
        lay.addWidget(self.bar)

        self.btn_output = QPushButton("Output OFF")
        self.btn_output.setCheckable(True)
        self.btn_output.setMinimumHeight(28)
        self.btn_output.setStyleSheet(
            "QPushButton { background:#8c0000; color:white; font-weight:bold; }"
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
            "font-family: Consolas, 'Courier New', monospace; font-size: 9px;"
            f" color: {theme.NEUTRAL};"
        )
        self.lbl_readback.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.lbl_readback)

        self.set_connected(False)
        self._update_hv_label()

    # ---- Setpoint in / out -----------------------------------------------------

    def set_setpoint(self, amp_vpp: float, freq_hz: float, output_on: bool,
                     matched: bool = True):
        """Render the shared setpoint. Never emits — this is a sync, not an edit."""
        self._syncing = True
        try:
            self.spn_amp.setValue(amp_vpp)
            self.spn_freq.setValue(freq_hz)
            self.btn_output.setChecked(output_on)
        finally:
            self._syncing = False
        self.btn_output.setText("Output ON" if output_on else "Output OFF")
        self.bar.set_target(amp_vpp)
        self._update_hv_label(matched)

    def _on_edited(self, *_):
        if self._syncing:
            return
        self._update_hv_label()
        self.bar.set_target(self.spn_amp.value())
        self.params_edited.emit(self.axis, self.spn_amp.value(),
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
        self.bar.set(None if plus is None else plus.amp_vpp, stale=not connected)

        if not connected:
            self.lbl_readback.setText(f"Gen {AXIS_GENERATOR[self.axis]} not connected")
            self.lbl_readback.setStyleSheet(
                "font-family: Consolas, 'Courier New', monospace; font-size: 9px;"
                f" color: {theme.NEUTRAL};")
            return
        if plus is None or minus is None:
            self.lbl_readback.setText("waiting for readback")
            return

        out = "ON" if (plus.output_on and minus.output_on) else \
              ("OFF" if not (plus.output_on or minus.output_on) else "SPLIT")
        self.lbl_readback.setText(
            f"{self.slits[0]} {plus.amp_vpp:.3f} · {self.slits[1]} {minus.amp_vpp:.3f} Vpp"
            f"  @ {plus.freq_hz:.4g} Hz  out={out}"
        )
        # A pair that disagrees is not driving a differential raster, whatever
        # the setpoint boxes say — call it out on the live line, not just the
        # setpoint one.
        split = (abs(plus.amp_vpp - minus.amp_vpp) > 1e-6
                 or abs(plus.freq_hz - minus.freq_hz) > 1e-9
                 or plus.output_on != minus.output_on)
        self.lbl_readback.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 9px;"
            f" color: {theme.WARN if split else theme.NEUTRAL};"
        )

    def set_connected(self, on: bool):
        for w in (self.spn_amp, self.spn_freq, self.btn_output):
            w.setEnabled(on)

    # ---- Consequence label -----------------------------------------------------

    def _update_hv_label(self, matched: bool = True):
        """What this amplitude does at the plates, and whether it is allowed.

        With offset fixed at 0 V the plate swings +/-(amp/2)*gain, and the peak
        the EEL5000 input sees is exactly amp/2 — the same interlock the
        Function Generators tab enforces, evaluated by the same function.
        """
        amp = self.spn_amp.value()
        status, peak = peak_status("Triangle", amp, 0.0)
        plate_peak_kv = (amp / 2.0) * _AMP_GAIN / 1000.0

        text = (f"→ ±{plate_peak_kv:.4f} kV/plate  ({amp:.4f} kV p-p)"
                f"   |   peak {peak:.3f} V")
        if not matched:
            text += "   ⚠ pair differs — set per channel on the FG tab"
        self.lbl_hv.setText(text)

        if status == "block":
            role = theme.FAULT
        elif status == "warn" or not matched:
            role = theme.WARN
        else:
            role = None
        self.lbl_hv.setStyleSheet(
            f"color: {role}; font-size: 10px; font-weight: bold;" if role
            else "color: #7a2000; font-size: 10px;"
        )
