"""
overview_tab.py
Read-only summary of every subsystem on one screen: jaw positions, log-amp
beam reconstruction, function-generator amplitudes, and HV amplifier output.

Composition only — no hardware access, no unit conversion, no polling of its
own. Every value here already exists somewhere else in the app (Beamline's
typed snapshots); this tab exists so an operator does not have to click
through four tabs to see whether the beam looks right.

No hardware-actuating controls: the repo owner has not yet signed off on an
Overview-tab E-stop / all-outputs-off (see the implementation plan, Phase 9).
Beamline.emergency_stop()/all_outputs_off() already exist for when that is
approved — this tab only reads.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
)

from rbl.config import hardware_config as SC
from rbl.hardware.funcgen_driver import MAX_AMP_VPP
from rbl.hardware.funcgen_safety import CHANNEL_ROLE
from rbl.gui import theme
from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
from rbl.gui.widgets.mini import ValueTile, MiniBar, Sparkline
from rbl.state.beamline import Beamline
from rbl.state.snapshots import MotorState, LogAmpState, AmpState, FuncGenState


class OverviewTab(QWidget):
    _REDRAW_INTERVAL_MS = 100   # 10 Hz — cheap tile/bar updates, not a plot

    def __init__(self, beamline: Beamline, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        self._visible = False

        # Latest snapshot per subsystem, applied to widgets on the redraw
        # timer rather than per-signal — signals can arrive at stream rate
        # (10 Hz+) and there are ~20 tiles here across four subsystems, so
        # repainting on every one of them would make the app feel slower
        # than it did before this tab existed.
        self._motors: MotorState = MotorState(connected=False, zeroed=False)
        self._logamps: LogAmpState = LogAmpState(connected=False)
        self._amps: AmpState = AmpState(connected=False)
        self._funcgens: FuncGenState = FuncGenState()

        outer = QVBoxLayout(self)

        self.lbl_failure = QLabel("")
        self.lbl_failure.setWordWrap(True)
        self.lbl_failure.setStyleSheet(theme.status_label(theme.NEUTRAL, bold=False))
        outer.addWidget(self.lbl_failure)

        body = QHBoxLayout()
        outer.addLayout(body, stretch=1)

        # ── Beam position (same widget the Beam Current tab uses) ──────────
        self.beam = BeamPositionIndicator(compact=True)
        body.addWidget(self.beam)

        # ── Jaws, funcgen amplitudes, HV output ─────────────────────────────
        right = QVBoxLayout()
        body.addLayout(right, stretch=1)

        jaw_box = QGroupBox("Jaw Positions")
        jaw_grid = QGridLayout(jaw_box)
        self.jaws = {}
        for col, jaw in enumerate(SC.AXIS_LABELS):
            tile = ValueTile(jaw, "mm")
            self.jaws[jaw] = tile
            jaw_grid.addWidget(tile, 0, col)
        right.addWidget(jaw_box)

        gen_box = QGroupBox("Function Generator Amplitude")
        gen_grid = QGridLayout(gen_box)
        self.amps = {}
        for col, key in enumerate(CHANNEL_ROLE):
            bar = MiniBar(f"{key} ({CHANNEL_ROLE[key]})", 0.0, MAX_AMP_VPP)
            self.amps[key] = bar
            gen_grid.addWidget(bar, 0, col)
        right.addWidget(gen_box)

        hv_box = QGroupBox("HV Amplifier Peak Output")
        hv_grid = QGridLayout(hv_box)
        self.hv = {}
        for row, amp in enumerate(SC.AMP_LABELS):
            spark = Sparkline(amp, theme.JAW_COLORS[amp])
            self.hv[amp] = spark
            hv_grid.addWidget(spark, row, 0)
        right.addWidget(hv_box)

        right.addStretch(1)

        beamline.motors_changed.connect(self._on_motors)
        beamline.logamps_changed.connect(self._on_logamps)
        beamline.amps_changed.connect(self._on_amps)
        beamline.funcgens_changed.connect(self._on_funcgens)
        beamline.command_failed.connect(self._on_failure)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(self._REDRAW_INTERVAL_MS)
        self._redraw_timer.timeout.connect(self._redraw)

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

        motors = self._motors
        for jaw, tile in self.jaws.items():
            axis = motors.axes.get(jaw)
            tile.set(axis.pos_mm if axis is not None else None, stale=not motors.connected)

        self.beam.set_jaw_state({
            "connected": motors.connected,
            "zeroed": motors.zeroed,
            "positions": {jaw: axis.pos_mm for jaw, axis in motors.axes.items()},
        })
        self.beam.set_currents(dict(self._logamps.currents))

        funcgens = self._funcgens
        for key, bar in self.amps.items():
            ch = funcgens.channels.get(key)
            gen_letter = key[0]
            connected = funcgens.connected.get(gen_letter, False)
            bar.set(ch.amp_vpp if ch is not None else None, stale=not connected)

        amps = self._amps
        for amp, spark in self.hv.items():
            ch = amps.channels.get(amp)
            spark.push(ch.peak_kv if ch is not None else None)
