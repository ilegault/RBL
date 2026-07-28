"""
overview_tab.py
Every subsystem on one screen: slit positions (with the controls to move
them), log-amp beam reconstruction, the raster drive (with the controls to set
it), and HV amplifier output.

Laid out after the Michigan accelerator overview screen: each live value sits
in a scaled mini bar chart rather than standing alone as digits, so the screen
answers "is this where it should be?" at a glance, and each control sits
directly on the bar it acts on — the operator adjusts a value and watches the
same track it is drawn on.

Composition only — no hardware access, no unit conversion, no polling of its
own. Every value here already exists somewhere else in the app (Beamline's
typed snapshots and the shared FuncGenSetpoints), and every command goes out
through Beamline's command surface, which is where the interlocks live. This
tab never touches a driver.

Scope of the controls here:
  - SLIT MOTION: absolute target + Move, per slit. No step size, no jog, no
    stop — relative motion and the abort live on the Stepper Motors tab, which
    has the limit-switch context that makes them safe.
  - RASTER DRIVE: amplitude and frequency per AXIS, plus output intent and one
    Apply. Per axis, not per channel, because X+/X- are a push-pull pair that
    must share both numbers; the 0 deg / 180 deg phase relationship and the
    triangle shape are held for you and are not editable here. Per-channel
    editing, offset, shape, load and the 10 MHz timebase lock stay on the
    Function Generators tab.
The HV amplifiers stay read-only — nothing on this screen commands them.

Setpoints are shared, not copied: the boxes here and the Function Generators
tab's panels are two views on one FuncGenSetpoints object, so neither screen
can show a stale value or silently overwrite what the other has typed.
"""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox,
)

from rbl.config import hardware_config as SC
from rbl.hardware.current_monitor import format_current
from rbl.hardware.funcgen_safety import (
    CHANNEL_ROLE, peak_status, PEAK_WARN_VOLTS, PEAK_MAX_VOLTS,
)
from rbl.gui import theme
from rbl.gui.widgets.axis_drive import AxisDriveControl
from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
from rbl.gui.widgets.mini import MiniBar, Sparkline
from rbl.gui.widgets.slit_control import SlitControl
from rbl.state.beamline import Beamline
from rbl.state.setpoints import AXIS_CHANNELS, AXIS_GENERATOR
from rbl.state.snapshots import MotorState, LogAmpState, AmpState, FuncGenState


class OverviewTab(QWidget):
    _REDRAW_INTERVAL_MS = 100   # 10 Hz — cheap tile/bar updates, not a plot
    _NOTE_FRAMES = 30           # ~3 s of redraws a command note stays up

    _STANDING_MESSAGE = ("Positions are absolute distance from beam centre; "
                         "the 0.2 mm home offset is applied for you.")

    _STANDING_DRIVE_MESSAGE = (
        "Triangle, 0 V offset, 0°/180° push-pull. Two separate units drift "
        "apart on their own clocks — lock X to Y with the 10 MHz timebase "
        "option on the Function Generators tab."
    )

    def __init__(self, beamline: Beamline, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        self._visible = False

        # Latest snapshot per subsystem, applied to widgets on the redraw
        # timer rather than per-signal — signals can arrive at stream rate
        # (10 Hz+) and there are ~20 widgets here across four subsystems, so
        # repainting on every one of them would make the app feel slower
        # than it did before this tab existed.
        self._motors: MotorState = MotorState(connected=False, zeroed=False)
        self._logamps: LogAmpState = LogAmpState(connected=False)
        self._amps: AmpState = AmpState(connected=False)
        self._funcgens: FuncGenState = FuncGenState()

        # Was the Galil connected on the previous redraw? Used to preload each
        # target box with the live position exactly once, when the link comes
        # up, instead of fighting the operator's typing every 100 ms.
        self._motors_were_connected = False

        # Redraws left before each status line reverts from the operator's last
        # command note to its standing message.
        self._note_frames = 0
        self._drive_note_frames = 0

        # The shared setpoint model (also edited by the Function Generators
        # tab). This tab owns no setpoint of its own — the boxes below are a
        # view on this object.
        self._setpoints = beamline.funcgen_setpoints

        outer = QVBoxLayout(self)

        outer.addLayout(self._build_status_strip())

        body = QHBoxLayout()
        outer.addLayout(body, stretch=1)

        # Every column is pinned to the top rather than sharing the leftover
        # height. Three panels of different natural heights, each stretched to
        # match the tallest, is three frames with dead space inside them; this
        # way the slack collects once, at the bottom, which is also where the
        # next subsystem will go.
        top = Qt.AlignmentFlag.AlignTop

        # BeamPositionIndicator brings its own titled group box, so it goes in
        # unwrapped — a second frame around it would just nest two identical
        # titles.
        self.beam = BeamPositionIndicator(compact=True)
        body.addWidget(self.beam, alignment=top)
        body.addWidget(self._build_slit_box(), stretch=1, alignment=top)

        right = QVBoxLayout()
        right.addWidget(self._build_funcgen_box(), alignment=top)
        right.addWidget(self._build_hv_box(), alignment=top)
        right.addStretch(1)
        body.addLayout(right, stretch=1)

        beamline.motors_changed.connect(self._on_motors)
        beamline.logamps_changed.connect(self._on_logamps)
        beamline.amps_changed.connect(self._on_amps)
        beamline.funcgens_changed.connect(self._on_funcgens)
        beamline.command_failed.connect(self._on_failure)

        # Setpoint edits are applied immediately, not on the redraw timer: they
        # are the operator's own keystrokes and must never lag behind them.
        self._setpoints.changed.connect(self._on_setpoint_changed)
        for axis in self.drives:
            self._sync_axis_from_setpoints(axis)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(self._REDRAW_INTERVAL_MS)
        self._redraw_timer.timeout.connect(self._redraw)

    # ---- Construction ----------------------------------------------------------

    def _build_status_strip(self) -> QHBoxLayout:
        strip = QHBoxLayout()
        self.pills: dict[str, QLabel] = {}
        for key, caption in (("motors", "Slits"), ("logamps", "Log amps"),
                             ("amps", "HV amps"), ("A", "Gen A"), ("B", "Gen B")):
            pill = QLabel(f"● {caption}")
            pill.setStyleSheet(theme.pill(False))
            self.pills[key] = pill
            strip.addWidget(pill)

        self.lbl_failure = QLabel("")
        self.lbl_failure.setWordWrap(True)
        self.lbl_failure.setStyleSheet(theme.status_label(theme.NEUTRAL, bold=False))
        strip.addWidget(self.lbl_failure, stretch=1)
        return strip

    def _build_slit_box(self) -> QGroupBox:
        # "&&" because Qt eats a single '&' in a title as a mnemonic marker.
        box = QGroupBox("Slits — Position && Control")
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(4)
        self.slits: dict[str, SlitControl] = {}
        for i, slit in enumerate(SC.AXIS_LABELS):
            ctrl = SlitControl(slit)
            ctrl.move_requested.connect(self._on_move_requested)
            self.slits[slit] = ctrl
            grid.addWidget(ctrl, i // 2, i % 2)
        lay.addLayout(grid)

        self.lbl_gaps = QLabel("Gap  X: —   Y: —")
        self.lbl_gaps.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.lbl_gaps)

        lay.addWidget(self._build_current_box())

        self.lbl_motion = QLabel(self._STANDING_MESSAGE)
        self.lbl_motion.setWordWrap(True)
        self.lbl_motion.setStyleSheet(theme.status_label(theme.NEUTRAL, bold=False))
        lay.addWidget(self.lbl_motion)

        return box

    def _build_current_box(self) -> QGroupBox:
        """Log-amp current per slit — which blade the beam is actually hitting.

        Decade-scaled: the log amps span 1 nA to 1 mA, so on a linear bar every
        reading below ~10 µA would sit invisibly against the left edge.
        """
        box = QGroupBox("Beam Current on Slits")
        grid = QGridLayout(box)
        self.currents = {}
        for i, slit in enumerate(SC.AXIS_LABELS):
            bar = MiniBar(
                f"{slit} current", SC.LOG_AMP_MIN_A, SC.LOG_AMP_MAX_A,
                color=theme.SLIT_COLORS[slit], ticks=7, log_scale=True,
                fmt=format_current,
                scale_captions=["1 nA", "1 µA", "1 mA"],
            )
            self.currents[slit] = bar
            grid.addWidget(bar, i // 2, i % 2)
        return box

    def _build_funcgen_box(self) -> QGroupBox:
        """Raster drive: one amplitude and one frequency per steering axis.

        Per axis rather than per channel because X+ and X- are one push-pull
        pair — driving them at different amplitudes or frequencies does not
        steer the beam differently, it just stops the pair being differential.
        The 0 deg / 180 deg phase split that makes it differential is held
        automatically and is deliberately not editable here.
        """
        box = QGroupBox("Raster Drive — Function Generators")
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        self.drives: dict[str, AxisDriveControl] = {}
        for axis in AXIS_CHANNELS:
            ctrl = AxisDriveControl(axis)
            ctrl.params_edited.connect(self._on_axis_params_edited)
            ctrl.output_toggled.connect(self._on_axis_output_toggled)
            self.drives[axis] = ctrl
            lay.addWidget(ctrl)

        self.btn_apply_all = QPushButton("Apply All — X && Y")
        self.btn_apply_all.setMinimumHeight(34)
        self.btn_apply_all.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " font-size:13px; }"
            "QPushButton:hover { background:#0063b1; }"
            "QPushButton:disabled { background:#c0c0c0; color:#888; }"
        )
        self.btn_apply_all.setToolTip(
            "Configure all four channels with outputs untouched, then enable "
            "the outputs back-to-back, then phase-synchronise each unit — the "
            "same sequence as the Function Generators tab's Apply All, and the "
            "only ordering that brings the raster up aligned."
        )
        self.btn_apply_all.setEnabled(False)
        self.btn_apply_all.clicked.connect(self._on_apply_all)
        lay.addWidget(self.btn_apply_all)

        self.lbl_drive_note = QLabel(self._STANDING_DRIVE_MESSAGE)
        self.lbl_drive_note.setWordWrap(True)
        self.lbl_drive_note.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False) + "font-size: 10px;")
        lay.addWidget(self.lbl_drive_note)
        return box

    def _build_hv_box(self) -> QGroupBox:
        box = QGroupBox("HV Amplifier Peak Output")
        grid = QGridLayout(box)
        self.hv_bars = {}
        self.hv = {}
        for i, amp in enumerate(SC.AMP_LABELS):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            bar = MiniBar(f"{amp} peak", 0.0, SC.AMP_MAX_KV, unit="kV",
                          color=theme.SLIT_COLORS[amp])
            spark = Sparkline(f"{amp} trend", theme.SLIT_COLORS[amp])
            self.hv_bars[amp] = bar
            self.hv[amp] = spark
            cell.addWidget(bar)
            cell.addWidget(spark)
            grid.addLayout(cell, i // 2, i % 2)
        return box

    # ---- Signal handlers (cheap: cache only) -----------------------------------

    def _on_motors(self, state: MotorState):
        self._motors = state

    def _on_logamps(self, state: LogAmpState):
        self._logamps = state

    def _on_amps(self, state: AmpState):
        self._amps = state

    def _on_funcgens(self, state: FuncGenState):
        self._funcgens = state

    def _on_failure(self, subsystem: str, msg: str):
        self.lbl_failure.setText(f"! {subsystem}: {msg}")
        self.lbl_failure.setStyleSheet(theme.status_label(theme.FAULT, bold=False))

    # ---- Commands out ----------------------------------------------------------
    #
    # Slit motion only, and always through Beamline — the tab holds no driver
    # and does no unit conversion of its own.

    def _on_move_requested(self, slit: str, mm: float):
        if not self._motors.connected:
            self._on_failure("motors",
                             f"{slit}: Galil not connected — connect it on the "
                             "Stepper Motors tab")
            return
        if not self._motors.zeroed and not self._confirm_unzeroed_move(slit, mm):
            return
        if self.beamline.move_slit(slit, mm):
            self.slits[slit].bar.set_target(mm)
            self._note(f"Commanded {slit} → {mm:.3f} mm")

    def _confirm_unzeroed_move(self, slit: str, mm: float) -> bool:
        """Ask before moving on positions that are not referenced.

        Until an axis has been zeroed/homed, every mm figure is an offset from
        wherever the controller happened to power up — so a "1.5 mm" command is
        a move of unknown size toward the beam. That is worth one click of
        friction, and it lives in its own method so tests can drive both
        answers without a live dialog.
        """
        answer = QMessageBox.warning(
            self, "Slits not zeroed",
            f"{slit} has not been zeroed since the app started, so its "
            f"position in mm is not referenced to beam centre.\n\n"
            f"Move {slit} to {mm:.3f} mm anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    # ---- Raster drive: setpoint edits and Apply --------------------------------
    #
    # Editing a box changes the SHARED setpoint and nothing else; nothing
    # reaches an instrument until Apply. That is the Function Generators tab's
    # behaviour too, and keeping it identical is the point — an operator who
    # learned "typing is safe, Apply is the commit" on one screen must not
    # discover the other screen energises plates on a keystroke.

    def _on_axis_params_edited(self, axis: str, amp_vpp: float, freq_hz: float):
        self._setpoints.update_axis(axis, amp_vpp=amp_vpp, freq_hz=freq_hz)

    def _on_axis_output_toggled(self, axis: str, on: bool):
        self._setpoints.update_axis(axis, output_on=on)

    def _on_setpoint_changed(self, key: str, params):
        """A setpoint moved — here or on the Function Generators tab."""
        for axis, keys in AXIS_CHANNELS.items():
            if key in keys:
                self._sync_axis_from_setpoints(axis)

    def _sync_axis_from_setpoints(self, axis: str):
        params = self._setpoints.axis_params(axis)
        self.drives[axis].set_setpoint(
            params.amp_vpp, params.freq_hz, params.output_on,
            matched=self._setpoints.axis_matched(axis),
        )

    def _on_apply_all(self):
        """Push every channel's setpoint through Beamline's Apply-All sequence.

        Beamline does the configure -> enable outputs -> phase-synchronise
        ordering and enforces the ±5 V combined-peak interlock; what is left
        here is the advisory tier Beamline has no way to ask about — a peak
        past the warn threshold but under the ceiling gets one confirmation.
        """
        params_by_key = self._setpoints.all()

        warned = []
        for key, params in params_by_key.items():
            if not self._funcgens.connected.get(key[0], False):
                continue
            status, peak = peak_status(params.shape, params.amp_vpp, params.offset_v)
            if status == "warn":
                warned.append((CHANNEL_ROLE[key], peak))
        if warned and not self._confirm_high_peak(warned):
            self._note_drive("Apply cancelled at the high-peak confirmation")
            return

        if self.beamline.apply_all_channels(params_by_key):
            self._note_drive("Applied — outputs enabled together, phase synchronised",
                             theme.OK)

    def _confirm_high_peak(self, warned: list) -> bool:
        """Ask before applying a peak above the advisory threshold.

        Its own method so tests can drive both answers without a live dialog,
        exactly as _confirm_unzeroed_move does for slit motion.
        """
        lines = "\n".join(f"  {slit}: peak {peak:.4g} V" for slit, peak in warned)
        answer = QMessageBox.question(
            self, "High peak voltage",
            f"These channels are above the {PEAK_WARN_VOLTS:.0f} V advisory "
            f"threshold (hard ceiling {PEAK_MAX_VOLTS:.0f} V):\n\n{lines}\n\n"
            f"Apply anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _note_drive(self, text: str, role: str = theme.NEUTRAL):
        """Show the result of the operator's last Apply, briefly, then let the
        standing drive message come back (see _redraw_funcgens)."""
        self.lbl_drive_note.setText(text)
        self.lbl_drive_note.setStyleSheet(
            theme.status_label(role, bold=False) + "font-size: 10px;")
        self._drive_note_frames = self._NOTE_FRAMES

    def _note(self, text: str, role: str = theme.NEUTRAL):
        """Show the operator's own last action, briefly outranking the
        standing message (see _redraw_motion_message)."""
        self._set_motion_text(text, role)
        self._note_frames = self._NOTE_FRAMES

    def _set_motion_text(self, text: str, role: str):
        self.lbl_motion.setText(text)
        self.lbl_motion.setStyleSheet(theme.status_label(role, bold=False))

    # ---- Visibility (QStackedWidget hides the non-current tab) ----------------
    #
    # The redraw timer is rendering, not data acquisition: Beamline keeps
    # emitting regardless of which tab is on screen, so no data is lost while
    # hidden — only painting pauses.

    def showEvent(self, event):
        super().showEvent(event)
        self._visible = True
        self._redraw_timer.start()
        self._redraw()   # repaint immediately, don't wait for a stale frame

    def hideEvent(self, event):
        super().hideEvent(event)
        self._visible = False
        self._redraw_timer.stop()

    # ---- Redraw (expensive-ish: touches every widget) --------------------------

    def _redraw(self):
        if not self._visible:
            return

        self._redraw_slits()
        self._redraw_beam()
        self._redraw_funcgens()
        self._redraw_amps()

    def _redraw_slits(self):
        motors = self._motors
        for slit, ctrl in self.slits.items():
            axis = motors.axes.get(slit)
            stale = not motors.connected or axis is None
            ctrl.set_position(
                None if axis is None else axis.pos_mm,
                stale=stale,
                moving=bool(axis is not None and axis.moving),
                enabled=bool(axis is None or axis.enabled),
            )
            ctrl.set_enabled(motors.connected)

        if motors.connected and not self._motors_were_connected:
            # Link just came up: start each target box at the live position so
            # a stray Move can't drive a slit somewhere from a leftover value.
            for ctrl in self.slits.values():
                ctrl.sync_target_to_position()
        self._motors_were_connected = motors.connected

        self._redraw_gaps()
        self._redraw_motion_message()
        self.pills["motors"].setStyleSheet(theme.pill(motors.connected))

    def _redraw_gaps(self):
        def gap(plus: str, minus: str) -> str:
            a = self._motors.axes.get(plus)
            b = self._motors.axes.get(minus)
            if not self._motors.connected or a is None or b is None:
                return "—"
            # Both positions are UNSIGNED distances from centre, so the full
            # aperture on an axis is simply their sum.
            return f"{abs(a.pos_mm) + abs(b.pos_mm):.3f} mm"

        self.lbl_gaps.setText(f"Gap  X: {gap('X+', 'X-')}   Y: {gap('Y+', 'Y-')}")

    def _redraw_motion_message(self):
        """Restore the standing message once a command note has had its moment.

        The note ("Commanded X+ → …", "STOP sent") is the operator's own
        action and wins while it is fresh, but an unreferenced axis stays
        unreferenced — that warning has to come back on its own, not wait for
        the next click to remind anybody.
        """
        if self._note_frames > 0:
            self._note_frames -= 1
            return
        if self._motors.connected and not self._motors.zeroed:
            self._set_motion_text(
                "Slits are not zeroed — mm positions are not referenced to "
                "beam centre. Home each axis on the Stepper Motors tab.",
                theme.WARN)
        else:
            self._set_motion_text(self._STANDING_MESSAGE, theme.NEUTRAL)

    def _redraw_beam(self):
        motors = self._motors
        logamps = self._logamps
        self.beam.set_slit_state({
            "connected": motors.connected,
            "zeroed": motors.zeroed,
            "positions": {slit: axis.pos_mm for slit, axis in motors.axes.items()},
        })
        self.beam.set_currents(dict(logamps.currents))

        for slit, bar in self.currents.items():
            bar.set(logamps.currents.get(slit), stale=not logamps.connected)
        self.pills["logamps"].setStyleSheet(theme.pill(logamps.connected))

    def _redraw_funcgens(self):
        """Readback only — the setpoint boxes are driven by FuncGenSetpoints.

        Keeping the two apart is what stops a 10 Hz redraw from overwriting a
        half-typed amplitude: nothing on this path ever writes a spinbox.
        """
        funcgens = self._funcgens
        any_connected = False
        for axis, ctrl in self.drives.items():
            connected = funcgens.connected.get(AXIS_GENERATOR[axis], False)
            any_connected = any_connected or connected
            ctrl.set_readback(funcgens.channels, connected)
            ctrl.set_connected(connected)

        self.btn_apply_all.setEnabled(any_connected)
        self._redraw_drive_message()
        for gen in ("A", "B"):
            self.pills[gen].setStyleSheet(theme.pill(funcgens.connected.get(gen, False)))

    def _redraw_drive_message(self):
        if self._drive_note_frames > 0:
            self._drive_note_frames -= 1
            return
        self.lbl_drive_note.setText(self._STANDING_DRIVE_MESSAGE)
        self.lbl_drive_note.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False) + "font-size: 10px;")

    def _redraw_amps(self):
        amps = self._amps
        for amp, spark in self.hv.items():
            ch = amps.channels.get(amp)
            peak = ch.peak_kv if ch is not None else None
            spark.push(peak)
            self.hv_bars[amp].set(peak, stale=not amps.connected)
        self.pills["amps"].setStyleSheet(theme.pill(amps.connected))
