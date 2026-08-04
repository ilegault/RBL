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
  - RASTER DRIVE: amplitude and frequency per AXIS, plus output intent, the
    10 MHz timebase lock, and one Apply. Per axis, not per channel, because
    X+/X- are a push-pull pair that must share both numbers; the 0 deg /
    180 deg phase relationship and the triangle shape are held for you and are
    not editable here. Amplitude is in PEAK volts, which is what reads across
    to the amplifier bars below (see axis_drive.py). Per-channel editing,
    offset, shape and load stay on the Function Generators tab.
The HV amplifiers stay read-only — nothing on this screen commands them. Their
panels are grouped by axis with each pair's waveforms drawn over each other,
because the failure worth catching there is a RELATIONSHIP (a pair that has
stopped being mirror images), which no single channel's readout can show. Each
pair's trace window is sized to the drive that pair is MEASURED to be running
(see rbl/hardware/amp_trace.py), not to the 0.1 s stream window, so the picture
holds a couple of readable cycles at any raster rate; the caption under it
reads that window back.

Setpoints are shared, not copied: the boxes here and the Function Generators
tab's panels are two views on one FuncGenSetpoints object, so neither screen
can show a stale value or silently overwrite what the other has typed.
"""
import dataclasses
import math

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox, QCheckBox,
)

from rbl.config import hardware_config as SC
from rbl.hardware.amp_monitor import pair_correlation
from rbl.hardware.current_monitor import format_current
from rbl.hardware.funcgen_safety import (
    CHANNEL_ROLE, peak_status, PEAK_WARN_VOLTS, PEAK_MAX_VOLTS,
)
from rbl.gui import theme
from rbl.gui.widgets.axis_drive import AxisDriveControl
from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
from rbl.gui.widgets.camera_widget import CameraWidget
from rbl.gui.widgets.drag_panel import DragPanel, PanelArea
from rbl.gui.widgets.mini import MiniBar, PairTrace
from rbl.gui.widgets.slit_control import SlitControl
from rbl.state.beamline import Beamline
from rbl.state.setpoints import AXIS_CHANNELS, AXIS_GENERATOR
from rbl.state.snapshots import MotorState, LogAmpState, AmpState, FuncGenState


def _format_span(seconds: float) -> str:
    """A trace window in the unit a person would say it in."""
    if seconds >= 1.0:
        return f"{seconds:.2f} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.3g} ms"
    return f"{seconds * 1e6:.3g} µs"


def _format_freq(hz: float) -> str:
    if hz >= 1e6:
        return f"{hz / 1e6:.3g} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3g} kHz"
    return f"{hz:.3g} Hz"


class OverviewTab(QWidget):
    _REDRAW_INTERVAL_MS = 100   # 10 Hz — cheap tile/bar updates, not a plot
    _NOTE_FRAMES = 30           # ~3 s of redraws a command note stays up

    _STANDING_MESSAGE = ("Positions are absolute distance from beam centre; "
                         "the 0.2 mm home offset is applied for you.")

    _STANDING_DRIVE_MESSAGE = (
        "Triangle, 0 V offset, 0°/180° push-pull. Amplitude is PEAK volts at "
        "the amplifier input — 1 V pk = 1 kV per plate. The Function "
        "Generators tab shows the same setpoint peak-to-peak."
    )

    # A perfect push-pull pair correlates at -1.0. The thresholds are loose
    # because a real pair is measured through two amplifier monitors with
    # their own noise — this has to flag a pair coming apart, not a pair that
    # is 2% short of textbook.
    _ANTIPHASE_OK   = -0.90
    _ANTIPHASE_WARN = -0.50

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

        # BeamPositionIndicator brings its own titled group box.
        self.beam = BeamPositionIndicator(compact=True)

        # Camera widget — live USB feed with photo/video capture.
        self.camera = CameraWidget()
        self.camera.set_metadata_provider(self._camera_metadata)

        # Five independent draggable panels; FuncGen and HV are separate so
        # they can be stacked in the same column or spread across columns.
        # Initial layout: one panel per column (cols 0-4).
        self._panel_area = PanelArea()
        self._panel_area.add(DragPanel(self.beam,                  stretch=0), col=0)
        self._panel_area.add(DragPanel(self._build_slit_box(),     stretch=1), col=1)
        self._panel_area.add(DragPanel(self.camera,                stretch=1), col=2)
        self._panel_area.add(DragPanel(self._build_funcgen_box(),  stretch=1), col=3)
        self._panel_area.add(DragPanel(self._build_hv_box(),       stretch=1), col=4)
        outer.addWidget(self._panel_area, stretch=1)

        # A move commanded on the Stepper Motors tab publishes its target
        # through Beamline; render it here so the caret and the target box on
        # this screen agree with that one. The reverse direction (a move made
        # here) travels the same wire — see Beamline.move_slit.
        beamline.slit_target_changed.connect(self._on_slit_target_changed)

        beamline.motors_changed.connect(self._on_motors)
        beamline.logamps_changed.connect(self._on_logamps)
        beamline.amps_changed.connect(self._on_amps)
        beamline.funcgens_changed.connect(self._on_funcgens)
        beamline.command_failed.connect(self._on_failure)
        beamline.timebase_changed.connect(self._on_timebase_changed)

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
            pill.setStyleSheet(theme.pill(False) + f"font-size: {theme.FS_VALUE}px;")
            self.pills[key] = pill
            strip.addWidget(pill)

        self.lbl_failure = QLabel("")
        self.lbl_failure.setWordWrap(True)
        self.lbl_failure.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False)
            + f"font-size: {theme.FS_LABEL}px;")
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

        # The standing note sits ABOVE the gap readout, not under the whole
        # panel: it says what the numbers directly below it MEAN (absolute from
        # centre, 0.2 mm offset already applied), and a caption that explains a
        # figure has to be read before the figure, not after it. It is also
        # where the "not zeroed" warning lands, which is worth meeting on the
        # way down to the gap rather than after having believed it.
        self.lbl_motion = QLabel(self._STANDING_MESSAGE)
        self.lbl_motion.setWordWrap(True)
        self.lbl_motion.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False)
            + f"font-size: {theme.FS_LABEL}px;")
        lay.addWidget(self.lbl_motion)

        self.lbl_gaps = QLabel("Gap  X: —   Y: —")
        self.lbl_gaps.setStyleSheet(
            f"font-weight: bold; font-size: {theme.FS_BIG}px;")
        lay.addWidget(self.lbl_gaps)

        lay.addWidget(self._build_current_box())

        return box

    # Where each slit's current bar sits in the diamond: (row, col, row span,
    # col span) on a 3x4 grid of equal-stretch columns. Y+ on top, Y- on the
    # bottom, X- left of X+ — the slits laid out the way they are in the beam,
    # not in reading order. A 2x2 grid put X+ and X- side by side and Y+ under
    # X+, which reads as a table of four unrelated numbers; this reads as an
    # aperture, so "the beam is high and left" is a shape rather than four
    # comparisons done in your head.
    #
    # FOUR columns, each bar two of them wide, rather than two columns with the
    # Y bars spanning both. Every bar is then the same width as every other —
    # which is what makes the four readings COMPARABLE at a glance, since a
    # decade bar's meaning is entirely in how far along its own track the fill
    # sits. Spanning columns 1-2 also centres the Y bars over the X pair
    # exactly, because their offset (one column) is the same on both sides.
    _DIAMOND_COLUMNS = 4
    _DIAMOND_CELLS = {
        "Y+": (0, 1, 1, 2),   # row, col, row span, col span
        "X-": (1, 0, 1, 2),
        "X+": (1, 2, 1, 2),
        "Y-": (2, 1, 1, 2),
    }

    def _build_current_box(self) -> QGroupBox:
        """Log-amp current per slit — which blade the beam is actually hitting.

        Decade-scaled: the log amps span 1 nA to 1 mA, so on a linear bar every
        reading below ~10 µA would sit invisibly against the left edge.

        Laid out as a diamond (see _DIAMOND_CELLS): each bar sits in the
        direction of the slit it reads.
        """
        box = QGroupBox("Beam Current on Slits")
        grid = QGridLayout(box)
        grid.setSpacing(4)
        self.currents = {}
        for slit in SC.AXIS_LABELS:
            bar = MiniBar(
                f"{slit} current", SC.LOG_AMP_MIN_A, SC.LOG_AMP_MAX_A,
                color=theme.SLIT_COLORS[slit], ticks=7, log_scale=True,
                fmt=format_current,
                scale_captions=["1 nA", "1 µA", "1 mA"],
            )
            self.currents[slit] = bar
            row, col, row_span, col_span = self._DIAMOND_CELLS[slit]
            grid.addWidget(bar, row, col, row_span, col_span)
        # Every column shares the width evenly, which is what makes all four
        # bars come out the same size: each spans two columns, so X-/X+ stay
        # mirror images of each other and Y+/Y- are the same width as them
        # rather than sizing to their own captions.
        for col in range(self._DIAMOND_COLUMNS):
            grid.setColumnStretch(col, 1)
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

        # Both axes on one row: they are read together (is X sweeping as fast
        # as Y is slow?) and applied together, so stacking them put the two
        # halves of one comparison a screenful apart.
        axes_row = QHBoxLayout()
        axes_row.setSpacing(6)
        self.drives: dict[str, AxisDriveControl] = {}
        for axis in AXIS_CHANNELS:
            ctrl = AxisDriveControl(axis)
            ctrl.params_edited.connect(self._on_axis_params_edited)
            ctrl.output_toggled.connect(self._on_axis_output_toggled)
            self.drives[axis] = ctrl
            axes_row.addWidget(ctrl, stretch=1)
        lay.addLayout(axes_row)

        lay.addLayout(self._build_timebase_row())

        self.btn_apply_all = QPushButton("Apply All — X && Y")
        self.btn_apply_all.setMinimumHeight(42)
        self.btn_apply_all.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            f" font-size:{theme.FS_VALUE}px; }}"
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
            theme.status_label(theme.NEUTRAL, bold=False)
            + f"font-size: {theme.FS_CAPTION}px;")
        lay.addWidget(self.lbl_drive_note)
        return box

    def _build_timebase_row(self) -> QHBoxLayout:
        """The 10 MHz reference lock, shared with the Function Generators tab.

        X and Y live on two separate DG1022Z units running their own crystals,
        so without a shared reference the X/Y phase relationship walks — the
        raster slowly turns into a Lissajous figure. Nothing else on this
        screen shows that happening, which is why the control belongs here and
        not only two tabs away.
        """
        row = QHBoxLayout()
        row.setSpacing(6)

        self.chk_ext_ref = QCheckBox("Share 10 MHz timebase")
        self.chk_ext_ref.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
        self.chk_ext_ref.setToolTip(
            "Two separate DG1022Z units drift on their own clocks, so their "
            "X/Y phase relationship will not stay fixed.\n\n"
            "To lock them: connect a BNC cable from Gen A's rear-panel "
            "[10MHz Out] to Gen B's rear-panel [10MHz In], then enable this.\n"
            "Gen A keeps its internal clock (master); Gen B follows the shared "
            "10 MHz reference (external). Un-checking returns both to internal."
        )
        self.chk_ext_ref.setEnabled(False)
        self.chk_ext_ref.toggled.connect(self._on_ext_ref_toggled)
        row.addWidget(self.chk_ext_ref)

        self.lbl_timebase = QLabel("A: —  B: —")
        self.lbl_timebase.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-weight: bold; "
            f"font-size: {theme.FS_LABEL}px;")
        row.addWidget(self.lbl_timebase)
        row.addStretch(1)
        return row

    def _build_hv_box(self) -> QGroupBox:
        """One panel per AXIS, stacked, each with its pair's waveforms overlaid.

        Grouped by axis rather than four independent cells because the thing
        worth checking is the RELATIONSHIP inside each pair: driven correctly,
        X+ and X- are mirror images crossing at zero, and that is only visible
        when they are drawn over each other on one scale. Four separately
        auto-scaled traces in a 2x2 grid show four plausible-looking waves and
        hide the one fault — the two units' clocks drifting apart — that
        nothing else on this screen would catch.

        Each panel's x span comes from the pair's own measured period, so the
        two panels are free to be at different time scales — which they must
        be, since a raster runs its fast and slow axes decades apart. The
        caption is what keeps that honest.
        """
        box = QGroupBox("HV Amplifier Output")
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        self.hv_bars: dict[str, MiniBar] = {}
        self.hv_traces: dict[str, PairTrace] = {}
        self.hv_phase: dict[str, QLabel] = {}
        self.hv_window: dict[str, QLabel] = {}
        for axis in ("X", "Y"):
            plus, minus = f"{axis}+", f"{axis}-"
            panel = QGroupBox(f"{axis}  —  {plus} / {minus}")
            cell = QVBoxLayout(panel)
            cell.setContentsMargins(6, 3, 6, 4)
            cell.setSpacing(2)

            bars = QHBoxLayout()
            bars.setSpacing(6)
            for amp in (plus, minus):
                bar = MiniBar(f"{amp} peak", 0.0, SC.AMP_MAX_KV, unit="kV",
                              color=theme.SLIT_COLORS[amp])
                self.hv_bars[amp] = bar
                bars.addWidget(bar, stretch=1)
            cell.addLayout(bars)

            trace = PairTrace(theme.SLIT_COLORS[plus], theme.SLIT_COLORS[minus])
            trace.setToolTip(
                f"{plus} and {minus} deflection waveforms, drawn on one shared "
                "scale over a window sized to the measured drive — a couple of "
                "cycles of whatever this pair is actually doing, triggered on "
                "the rising edge so it holds still.\n"
                "Driven push-pull they are mirror images crossing at zero. "
                "Two traces sliding past each other mean the generators are "
                "not sharing a timebase.\n"
                "The caption below reads back the window it settled on; "
                "'no cycle found' means one raw stream window, unmeasured."
            )
            self.hv_traces[axis] = trace
            cell.addWidget(trace, stretch=1)

            # Window on the left, phase on the right: the caption says WHAT is
            # being shown, and a correlation is worth nothing until you know
            # the trace under it holds a whole cycle.
            foot = QHBoxLayout()
            foot.setSpacing(6)
            win = QLabel("—")
            win.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            self.hv_window[axis] = win
            foot.addWidget(win)
            foot.addStretch(1)

            lbl = QLabel("—")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            lbl.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            self.hv_phase[axis] = lbl
            foot.addWidget(lbl)
            cell.addLayout(foot)

            lay.addWidget(panel)
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

    def _on_slit_target_changed(self, slit: str, mm: float):
        """Some screen commanded *slit* to *mm* — show it on that slit's bar."""
        ctrl = self.slits.get(slit)
        if ctrl is not None:
            ctrl.set_target_mm(mm)

    def _on_failure(self, subsystem: str, msg: str):
        self.lbl_failure.setText(f"! {subsystem}: {msg}")
        self.lbl_failure.setStyleSheet(
            theme.status_label(theme.FAULT, bold=False)
            + f"font-size: {theme.FS_LABEL}px;")

    # ---- Camera metadata provider ------------------------------------------

    def _camera_metadata(self) -> dict:
        """Snapshot of every live readout at the moment of photo/video capture.

        Passed to CameraWidget so each sidecar JSON contains the full beamline
        state — slit positions, beam currents, HV readings, and function-
        generator setpoints — paired with the image by matching filename stem.
        """
        def _dc(obj):
            return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else {}

        return {
            "motors":   _dc(self._motors),
            "logamps":  _dc(self._logamps),
            "amps":     _dc(self._amps),
            "funcgens": _dc(self._funcgens),
        }

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
        # move_slit publishes the target itself, to every screen at once —
        # this one included, via _on_slit_target_changed. Setting the caret
        # here as well would be the one path that skipped the other tab.
        if self.beamline.move_slit(slit, mm):
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

    # ---- Cross-unit timebase ---------------------------------------------------

    def _on_ext_ref_toggled(self, checked: bool):
        """Lock/unlock Gen B to Gen A's 10 MHz reference.

        The instrument sequence and the both-EXT guard live in
        Beamline.set_shared_timebase — this screen is the second one offering
        the control, and a guard that protects hardware cannot live in
        whichever widget the operator happened to use.
        """
        ok, message = self.beamline.set_shared_timebase(checked)
        first_line = message.splitlines()[0]
        if not ok:
            # Never leave the box showing a lock that is not there.
            self.chk_ext_ref.blockSignals(True)
            self.chk_ext_ref.setChecked(False)
            self.chk_ext_ref.blockSignals(False)
            if checked:
                self._warn("Reference clock", message)
        self._note_drive(first_line, theme.OK if ok else theme.FAULT)
        self._redraw_timebase(self.beamline.read_timebase())

    def _warn(self, title: str, message: str):
        """Every blocking dialog on this tab goes through one overridable
        method, so a test can drive the path without a live dialog — the same
        reason _confirm_unzeroed_move and _confirm_high_peak are methods."""
        QMessageBox.warning(self, title, message)

    def _on_timebase_changed(self, clocks: dict):
        """The other screen changed the lock — follow it."""
        self._redraw_timebase(clocks)

    def _redraw_timebase(self, clocks: dict):
        locked = clocks.get("A") == "INT" and clocks.get("B") == "EXT"
        self.lbl_timebase.setText(f"A: {clocks.get('A', '—')}  B: {clocks.get('B', '—')}")
        self.lbl_timebase.setStyleSheet(
            theme.status_label(theme.OK if locked else theme.NEUTRAL)
            + f"font-size: {theme.FS_LABEL}px;")
        self.chk_ext_ref.blockSignals(True)
        self.chk_ext_ref.setChecked(locked)
        self.chk_ext_ref.blockSignals(False)

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
            theme.status_label(role, bold=False) + f"font-size: {theme.FS_CAPTION}px;")
        self._drive_note_frames = self._NOTE_FRAMES

    def _note(self, text: str, role: str = theme.NEUTRAL):
        """Show the operator's own last action, briefly outranking the
        standing message (see _redraw_motion_message)."""
        self._set_motion_text(text, role)
        self._note_frames = self._NOTE_FRAMES

    def _set_motion_text(self, text: str, role: str):
        self.lbl_motion.setText(text)
        self.lbl_motion.setStyleSheet(
            theme.status_label(role, bold=False) + f"font-size: {theme.FS_LABEL}px;")

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
        # Sharing a timebase only means anything with BOTH units connected —
        # there is nothing to lock one generator to.
        both = all(funcgens.connected.get(g, False) for g in ("A", "B"))
        self.chk_ext_ref.setEnabled(both)
        self._redraw_timebase(funcgens.timebase)
        self._redraw_drive_message()
        for gen in ("A", "B"):
            self.pills[gen].setStyleSheet(theme.pill(funcgens.connected.get(gen, False)))

    def _redraw_drive_message(self):
        if self._drive_note_frames > 0:
            self._drive_note_frames -= 1
            return
        self.lbl_drive_note.setText(self._STANDING_DRIVE_MESSAGE)
        self.lbl_drive_note.setStyleSheet(
            theme.status_label(theme.NEUTRAL, bold=False)
            + f"font-size: {theme.FS_CAPTION}px;")

    def _redraw_amps(self):
        amps = self._amps
        for amp, bar in self.hv_bars.items():
            ch = amps.channels.get(amp)
            bar.set(ch.peak_kv if ch is not None else None, stale=not amps.connected)

        for axis, trace in self.hv_traces.items():
            plus = amps.channels.get(f"{axis}+")
            minus = amps.channels.get(f"{axis}-")
            wave_p = plus.wave_kv if plus is not None else ()
            wave_m = minus.wave_kv if minus is not None else ()
            trace.set_pair(wave_p, wave_m)
            # Both members of a pair are windowed together, so either one
            # carries the answer — take whichever actually streamed.
            timed = next((c for c in (plus, minus)
                          if c is not None and not math.isnan(c.wave_span_s)), None)
            self._redraw_window_label(axis, timed, amps.connected)
            self._redraw_phase_label(axis, wave_p, wave_m, amps.connected)

        self.pills["amps"].setStyleSheet(theme.pill(amps.connected))

    def _redraw_window_label(self, axis: str, channel, connected: bool):
        """Say what time window the trace above it is showing.

        The window is no longer a fixed 0.1 s — it is however long two cycles
        of the measured drive take — so the trace on its own gives no sense of
        scale, and two panels showing the same-looking wave can be a decade
        apart in frequency. This is the axis for both of them.
        """
        lbl = self.hv_window[axis]
        if not connected or channel is None or not channel.wave_span_s > 0.0:
            lbl.setText("—")
            return
        span, freq = channel.wave_span_s, channel.wave_freq_hz
        if math.isnan(freq) or freq <= 0.0:
            # Nothing periodic to lock onto: this is a raw window, and saying
            # so is the difference between "undriven" and "measured at 0 Hz".
            lbl.setText(f"{_format_span(span)} raw window — no cycle found")
            return
        cycles = span * freq
        lbl.setText(f"{cycles:.0f} cycles @ {_format_freq(freq)}  ·  "
                    f"{_format_span(span)}/window")

    def _redraw_phase_label(self, axis: str, wave_p, wave_m, connected: bool):
        """Put a number on what the overlaid traces show.

        The picture answers "are these mirror images?" faster than any number
        can, but only while someone is looking at it. The correlation is the
        same question in a form you can glance at, and it is the same for any
        amplitude or frequency — so it says "phase", not "amplitude".
        """
        lbl = self.hv_phase[axis]
        if not connected:
            lbl.setText("—")
            lbl.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            return

        corr = pair_correlation(wave_p, wave_m)
        if math.isnan(corr):
            lbl.setText("no waveform on this profile")
            lbl.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            return

        if corr <= self._ANTIPHASE_OK:
            text, role = f"push-pull locked  (r {corr:+.2f})", theme.OK
        elif corr <= self._ANTIPHASE_WARN:
            text, role = f"phase slipping  (r {corr:+.2f})", theme.WARN
        else:
            text, role = f"NOT anti-phase  (r {corr:+.2f})", theme.FAULT
        lbl.setText(text)
        lbl.setStyleSheet(theme.status_label(role, bold=False)
                          + f"font-size: {theme.FS_CAPTION}px;")
