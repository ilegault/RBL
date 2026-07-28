"""
overview_tab.py
Every subsystem on one screen: slit positions (with the controls to move
them), log-amp beam reconstruction, function-generator amplitudes, and HV
amplifier output.

Laid out after the Michigan accelerator overview screen: each live value sits
in a scaled mini bar chart rather than standing alone as digits, so the screen
answers "is this where it should be?" at a glance, and the slit controls sit
directly under the slit bars they act on — the operator adjusts the beam and
watches the same track it is drawn on.

Composition only — no hardware access, no unit conversion, no polling of its
own. Every value here already exists somewhere else in the app (Beamline's
typed snapshots), and every command goes out through Beamline's command
surface, which is where the interlocks live. This tab never touches a driver.

Scope of the controls here: SLIT MOTION ONLY (move, step, stop). The function
generators and HV amplifiers stay read-only on this screen — raising a voltage
is deliberately a trip to the Function Generators tab, where the full
interlock context is on screen.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox,
)

from rbl.config import hardware_config as SC
from rbl.hardware.current_monitor import format_current
from rbl.hardware.funcgen_driver import MAX_AMP_VPP
from rbl.hardware.funcgen_safety import CHANNEL_ROLE
from rbl.gui import theme
from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
from rbl.gui.widgets.mini import MiniBar, Sparkline
from rbl.gui.widgets.slit_control import SlitControl
from rbl.state.beamline import Beamline
from rbl.state.snapshots import MotorState, LogAmpState, AmpState, FuncGenState


class OverviewTab(QWidget):
    _REDRAW_INTERVAL_MS = 100   # 10 Hz — cheap tile/bar updates, not a plot
    _NOTE_FRAMES = 30           # ~3 s of redraws a command note stays up

    _STANDING_MESSAGE = ("Positions are absolute distance from beam centre; "
                         "the 0.2 mm home offset is applied for you.")

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

        # Redraws left before the motion line reverts from the operator's last
        # command note to the standing message.
        self._note_frames = 0

        outer = QVBoxLayout(self)

        outer.addLayout(self._build_status_strip())

        body = QHBoxLayout()
        outer.addLayout(body, stretch=1)

        # BeamPositionIndicator brings its own titled group box, so it goes in
        # unwrapped — a second frame around it would just nest two identical
        # titles.
        self.beam = BeamPositionIndicator(compact=True)
        body.addWidget(self.beam)
        body.addWidget(self._build_slit_box(), stretch=1)

        right = QVBoxLayout()
        right.addWidget(self._build_funcgen_box())
        right.addWidget(self._build_hv_box())
        right.addStretch(1)
        body.addLayout(right, stretch=1)

        beamline.motors_changed.connect(self._on_motors)
        beamline.logamps_changed.connect(self._on_logamps)
        beamline.amps_changed.connect(self._on_amps)
        beamline.funcgens_changed.connect(self._on_funcgens)
        beamline.command_failed.connect(self._on_failure)

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

        # The controls keep their natural height at the top of the column; the
        # slack goes here so STOP stays pinned at the bottom edge, where a hand
        # reaching for it lands.
        lay.addStretch(1)

        self.btn_stop = QPushButton("STOP ALL SLIT MOTION")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setStyleSheet(
            "QPushButton { background:#aa0000; color:white; font-size:14px;"
            f" font-weight:bold; border:2px solid {theme.FAULT}; }}"
            f"QPushButton:hover {{ background:{theme.FAULT}; }}"
            "QPushButton:disabled { background:#c0c0c0; color:#888;"
            " border:2px solid #a0a0a0; }"
        )
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.setEnabled(False)
        lay.addWidget(self.btn_stop)
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
        box = QGroupBox("Function Generator Amplitude")
        grid = QGridLayout(box)
        self.amps = {}
        for i, key in enumerate(CHANNEL_ROLE):
            role = CHANNEL_ROLE[key]
            bar = MiniBar(f"{key} ({role})", 0.0, MAX_AMP_VPP, unit="Vpp",
                          color=theme.SLIT_COLORS.get(role))
            self.amps[key] = bar
            grid.addWidget(bar, i // 2, i % 2)
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

    def _on_stop_clicked(self):
        """Abort all slit motion. No confirmation — a stop must be instant."""
        self.beamline.emergency_stop()
        self._note("STOP sent — all slit motion aborted", theme.FAULT)

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

        self.btn_stop.setEnabled(motors.connected)
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
        funcgens = self._funcgens
        for key, bar in self.amps.items():
            ch = funcgens.channels.get(key)
            connected = funcgens.connected.get(key[0], False)
            output_on = bool(ch is not None and ch.output_on)
            bar.set(ch.amp_vpp if ch is not None else None,
                    stale=not connected,
                    role=None if output_on else theme.NEUTRAL)
            state = "OUT ON" if output_on else "OUT OFF"
            bar.lbl_name.setText(f"{key} ({CHANNEL_ROLE[key]}) · {state}")
        for gen in ("A", "B"):
            self.pills[gen].setStyleSheet(theme.pill(funcgens.connected.get(gen, False)))

    def _redraw_amps(self):
        amps = self._amps
        for amp, spark in self.hv.items():
            ch = amps.channels.get(amp)
            peak = ch.peak_kv if ch is not None else None
            spark.push(peak)
            self.hv_bars[amp].set(peak, stale=not amps.connected)
        self.pills["amps"].setStyleSheet(theme.pill(amps.connected))
