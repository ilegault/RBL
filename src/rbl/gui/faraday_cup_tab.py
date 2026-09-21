"""
faraday_cup_tab.py
PySide6 widget for the "Faraday Cup" outer tab.

Renders the live Faraday cup current from the Keithley 6482 dual-channel
picoammeter (Channel 1), displays active acquisition run metrics (duration,
sample count, running average computed strictly from logged samples), and
provides a live scrolling plot with historical navigation.

WHY THIS EXISTS
---------------
The beamline's four NEC log amps measure slit current — the beam intercepted by
the slit jaws on the way through. The Faraday cup measures transmitted current —
the charge that reaches the target / sample location.

This tab displays the live cup current as a first-class quantity, auto-scaled
from nanoamps to milliamps, provides a dedicated connection panel to connect
and disconnect the picoammeter independently, tracks threshold-triggered
acquisition runs with running averages, and renders a live current plot over time.

RUNNING AVERAGE COMPUTED FROM LOGGED SAMPLES
--------------------------------------------
The running average is computed strictly from the samples logged by
CupSessionWriter, never from a parallel accumulator. Two numbers derived
independently are two numbers that can drift apart; computing the average from
logged samples ensures that the number on screen and the number in the session file
agree unconditionally. Over-range samples are excluded from the average, and their
exclusion count is rendered visibly.

PLOT NAVIGATION (SHARED PATTERN WITH SLIT CURRENTS)
---------------------------------------------------
- Default 2-minute viewport window (WINDOW_SECONDS = 120).
- Slider at max (>= 9800) -> LIVE mode: window tracks "now".
- Drag slider left -> FROZEN mode: window locked to historical timestamp.
- Buffer holds ~1 hour of history (BUFFER_CAPACITY = 36 000 @ 10 Hz).
- Zoom in / zoom out time-window buttons.
- "Jump to Live" button returns to live edge.

ONE INSTRUMENT, ONE OWNER
-------------------------
This tab owns no driver, opens no VISA session, and spawns no threads. Beamline
(rbl/state/picoammeter_link.py) is the sole owner of the instrument handle and
publishes CupState snapshots.

NO UNIT CONVERSION
------------------
The Keithley 6482 returns current already in Amperes. The tab receives the
snapshot and auto-scales the display for human readability via format_current(),
without altering or converting the physical value.

STALE DATA & DISCONNECT
-----------------------
With nothing connected, the tab clearly displays a disconnected status and
placeholder ("—"), never displaying zero or a stale measurement.
Over-range conditions are shown explicitly as "OVER-RANGE" rather than numbers.
Disconnection mid-run cleanly closes the active run and resets all metrics.

CUP ACTUATION AND POSITION FEEDBACK (ADR 0003)
----------------------------------------------
The Faraday cup tab provides manual Insert (IN) and Retract (OUT) actuation
controls operated via the LabJack T7. Commanded position and confirmed position
are presented as two distinct, always-visible indicators and are never merged.
The controller status contacts report IN, OUT, In Transit, or Indeterminate.
An indeterminate reading is presented as a fault, not as a position.
A commanded move that fails to confirm within CUP_MOVE_CONFIRMATION_TIMEOUT_S (2.0 s)
raises a visible fault naming which move failed and does not re-command.
When the controller is not in AUTO mode, the tab clearly informs the operator
that remote commands will be accepted and ignored.

AUTHORITY RULE (ADR 0003 Decision 4)
-------------------------------------
The acquisition state machine uses an AuthorityDetector that runs both
current-inference (CupDetector) and confirmed-position (CupPositionDetector)
in parallel. The active source — "confirmed position" or "inference (current)" —
is shown in the actuation panel. When FIO_STATE is absent (non-FULL profile) or
the controller is in LOCAL mode, inference governs so hand insertions still record.
When both sources disagree (position says IN but current is below the arm
threshold, or vice versa), the disagreement is displayed as a fault carrying both
readings. Nothing in this code resolves the disagreement.

AUTOMATED SAMPLING CYCLE (ADR 0003)
------------------------------------
The Sampling Cycle panel lets an operator arm a repeating insert-dwell-retract
sequence (ticket 09). The scheduler is a pure object (SamplingCycleScheduler)
that receives the current time as a parameter and returns the action to take —
INSERT, RETRACT, or SKIPPED. It calls no clock. The tab drives it on every
actuation state update from the T7, which arrives on the stream worker's
schedule. Period and dwell are editable via QuietDoubleSpinBox and take effect
at the next period boundary, never mid-insertion. The cycle is disarmed on
construction and after every application start; arming is always an explicit
operator action. A manual insert or retract calls notify_manual_insert /
notify_manual_retract immediately, so the next scheduled boundary that falls
while a manual run is open is skipped and recorded in the session file.
"""
from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rbl.config.cup_config import (
    CUP_CYCLE_DWELL_S,
    CUP_CYCLE_PERIOD_S,
    CUP_IDLE_HEARTBEAT_INTERVAL_S,
    CUP_MOVE_CONFIRMATION_TIMEOUT_S,
    KEITHLEY_6482_DEFAULT_RESOURCE,
)
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.gui.widgets.inputs import (
    QuietDoubleSpinBox,
    ScientificDoubleSpinBox,
    unit_row,
)
from rbl.gui.widgets.live_plot import LivePlotPanel
from rbl.hardware.cup_status import CupPosition
from rbl.hardware.current_monitor import RollingBuffer, format_current
from rbl.hardware.dose_model import DoseAccumulator, patch_area_cm2
from rbl.services.cup_acquisition import (
    AuthorityDetector,
    CupAcquisitionStateMachine,
    CupReading,
    RunClosed,
    RunOpened,
)
from rbl.services.cup_session_writer import CupSessionWriter
from rbl.services.sampling_cycle import (
    CycleInsert,
    CycleRetract,
    CycleSkipped,
    CycleState,
    SamplingCycleScheduler,
)
from rbl.snapshots import CupActuationState, CupState

if TYPE_CHECKING:
    from rbl.state.beamline import Beamline

log = logging.getLogger(__name__)


class FaradayCupTab(QWidget):
    """The 'Faraday Cup' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # default 2-minute viewport
    MIN_WINDOW_SECONDS = 1.0   # minimum zoom step (1.0 s)

    def __init__(
        self,
        beamline: Beamline | None = None,
        parent: QWidget | None = None,
        session_writer: CupSessionWriter | None = None,
    ):
        super().__init__(parent)
        self.beamline = beamline
        self._connected = False
        self._visible = False
        self.acquisition = CupAcquisitionStateMachine(detector=AuthorityDetector())
        self.session_writer: CupSessionWriter = (
            session_writer if session_writer is not None else CupSessionWriter()
        )
        self._last_heartbeat_t: float = 0.0
        self.buffer = RollingBuffer(self.BUFFER_CAPACITY)

        # Faraday cup actuation state (ADR 0003)
        self._actuation_connected: bool = False
        self._commanded: CupPosition = CupPosition.IN
        self._confirmed: CupPosition = CupPosition.IN_TRANSIT
        self._auto_mode: bool = False
        self._stale: bool = False
        self._move_in_flight: bool = False
        self._move_target: CupPosition | None = None
        self._move_start_t: float = float("nan")
        self._last_state_t: float = float("nan")
        self._move_fault: str = ""
        self._last_logged_confirmed: CupPosition | None = None
        self._last_logged_indeterminate: bool = False
        self._disagreement_logged: bool = False
        self._was_auto_mode: bool | None = None

        # Sampling cycle scheduler (ADR 0003, ticket 09)
        self.cycle = SamplingCycleScheduler(
            period_s=CUP_CYCLE_PERIOD_S,
            dwell_s=CUP_CYCLE_DWELL_S,
        )

        # Dose & displacement damage tracking (ADR 0003)
        self._patch_width_x_mm: float = 0.0
        self._patch_height_y_mm: float = 0.0
        self._area_cm2: float = 0.0
        self._area_source: str = "Raster Planner"

        # Dose accumulator and run-open timestamp for zero-order hold (ADR 0003 Decision 7)
        self._accumulator: DoseAccumulator = DoseAccumulator()
        self._current_run_start_t: float = float("nan")
        self._last_commanded_in_t: float | None = None
        self._species: str = "NOT_SPECIFIED"
        self._beam_energy: str = "NOT_SPECIFIED"

        # Cycle fault message — set on arming refusal or cycle disarm; cleared on arm
        self._cycle_fault: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 1. Connection Panel ───────────────────────────────────────────────
        conn_box = QGroupBox("Keithley 6482 Picoammeter")
        conn_lay = QHBoxLayout(conn_box)

        conn_lay.addWidget(QLabel("VISA Resource:"))
        self.le_resource = QLineEdit(KEITHLEY_6482_DEFAULT_RESOURCE)
        self.le_resource.setMaximumWidth(220)
        self.le_resource.setToolTip(
            "VISA resource identifier for the Keithley 6482 (e.g. GPIB0::2::INSTR)"
        )
        conn_lay.addWidget(self.le_resource)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        conn_lay.addWidget(self.btn_connect)

        self.status_pill = StatusPill()
        conn_lay.addWidget(self.status_pill)

        self.lbl_ident = QLabel("")
        self.lbl_ident.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        conn_lay.addWidget(self.lbl_ident, stretch=1)

        layout.addWidget(conn_box)

        # ── 2. Middle Row: Live Reading | Acquisition Run ─────────────────────
        mid_row = QHBoxLayout()
        mid_row.setSpacing(8)

        # Left: Live Reading Panel
        reading_box = QGroupBox("Live Cup Current")
        reading_lay = QVBoxLayout(reading_box)
        reading_lay.setSpacing(6)
        reading_lay.setContentsMargins(12, 10, 12, 10)

        header_lay = QHBoxLayout()
        lbl_channel = QLabel("Faraday Cup  (Channel 1)")
        lbl_channel.setStyleSheet(
            f"font-size: {theme.FS_BIG}px; color: {theme.NEUTRAL}; font-weight: bold;"
        )
        header_lay.addWidget(lbl_channel)
        header_lay.addStretch()
        reading_lay.addLayout(header_lay)

        mono_font = QFont("Consolas", 24)
        mono_font.setBold(True)

        self.lbl_current = QLabel("  —    ")
        self.lbl_current.setFont(mono_font)
        self.lbl_current.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_current.setStyleSheet(theme.status_label(theme.MUTED))
        reading_lay.addWidget(self.lbl_current)

        detail_lay = QHBoxLayout()
        self.lbl_detail = QLabel("Not connected")
        self.lbl_detail.setStyleSheet(
            f"color: {theme.MUTED}; font-style: italic; font-size: {theme.FS_LABEL}px;"
        )
        detail_lay.addWidget(self.lbl_detail)
        detail_lay.addStretch()

        self.lbl_meta = QLabel("")
        self.lbl_meta.setStyleSheet(
            f"color: {theme.MUTED}; font-family: Consolas, 'Courier New', monospace; "
            f"font-size: {theme.FS_TINY}px;"
        )
        detail_lay.addWidget(self.lbl_meta)
        reading_lay.addLayout(detail_lay)
        reading_lay.addStretch()

        mid_row.addWidget(reading_box, stretch=1)

        # Middle: Cup Actuation & Position Panel (ADR 0003)
        act_box = QGroupBox("Cup Actuation")
        act_lay = QVBoxLayout(act_box)
        act_lay.setSpacing(6)
        act_lay.setContentsMargins(12, 10, 12, 10)

        # Command Buttons Row
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(8)

        self.btn_insert = QPushButton("Insert")
        self.btn_insert.setToolTip("Command Faraday cup IN (into beam path)")
        self.btn_insert.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_insert.setEnabled(False)
        self.btn_insert.clicked.connect(self._on_insert_clicked)
        btn_lay.addWidget(self.btn_insert)

        self.btn_retract = QPushButton("Retract")
        self.btn_retract.setToolTip("Command Faraday cup OUT (retracted from beam path)")
        self.btn_retract.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        self.btn_retract.setEnabled(False)
        self.btn_retract.clicked.connect(self._on_retract_clicked)
        btn_lay.addWidget(self.btn_retract)

        btn_lay.addStretch()
        act_lay.addLayout(btn_lay)

        # Position indicators grid (Commanded & Confirmed kept strictly apart)
        pos_grid = QGridLayout()
        pos_grid.setContentsMargins(0, 4, 0, 0)
        pos_grid.setSpacing(6)

        lbl_cmd_title = QLabel("Commanded Position:")
        lbl_cmd_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        pos_grid.addWidget(lbl_cmd_title, 0, 0)

        self.lbl_commanded = QLabel("  —    ")
        self.lbl_commanded.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_commanded.setMinimumWidth(140)
        pos_grid.addWidget(self.lbl_commanded, 0, 1)

        lbl_conf_title = QLabel("Confirmed Position:")
        lbl_conf_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        pos_grid.addWidget(lbl_conf_title, 1, 0)

        self.lbl_confirmed = QLabel("  —    ")
        self.lbl_confirmed.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_confirmed.setMinimumWidth(140)
        pos_grid.addWidget(self.lbl_confirmed, 1, 1)

        act_lay.addLayout(pos_grid)

        # Status notices & warnings
        self.lbl_auto_mode = QLabel("")
        self.lbl_auto_mode.setStyleSheet(
            f"color: {theme.WARN}; font-size: {theme.FS_TINY}px; font-weight: bold;"
        )
        self.lbl_auto_mode.setWordWrap(True)
        self.lbl_auto_mode.setVisible(False)
        act_lay.addWidget(self.lbl_auto_mode)

        self.lbl_stale = QLabel("")
        self.lbl_stale.setStyleSheet(
            f"color: {theme.WARN}; font-style: italic; font-size: {theme.FS_TINY}px;"
        )
        self.lbl_stale.setWordWrap(True)
        self.lbl_stale.setVisible(False)
        act_lay.addWidget(self.lbl_stale)

        # Active source indicator (ADR 0003 Decision 4)
        self.lbl_source = QLabel("")
        self.lbl_source.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_TINY}px;"
        )
        self.lbl_source.setWordWrap(True)
        self.lbl_source.setVisible(False)
        act_lay.addWidget(self.lbl_source)

        self.lbl_fault = QLabel("")
        self.lbl_fault.setStyleSheet(theme.status_label(theme.FAULT))
        self.lbl_fault.setWordWrap(True)
        self.lbl_fault.setVisible(False)
        act_lay.addWidget(self.lbl_fault)

        act_lay.addStretch()

        mid_row.addWidget(act_box, stretch=1)

        # Right: Acquisition Run Panel
        acq_box = QGroupBox("Acquisition Run")
        acq_lay = QVBoxLayout(acq_box)
        acq_lay.setSpacing(6)
        acq_lay.setContentsMargins(12, 10, 12, 10)

        acq_top_lay = QHBoxLayout()
        lbl_run_title = QLabel("Run Status:")
        lbl_run_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        acq_top_lay.addWidget(lbl_run_title)

        self.lbl_run_status = QLabel("Disconnected")
        self.lbl_run_status.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_run_status.setMinimumWidth(160)
        acq_top_lay.addWidget(self.lbl_run_status)
        acq_top_lay.addSpacing(10)

        self.btn_force_start = QPushButton("Force Start")
        self.btn_force_start.setStyleSheet("font-weight: bold; padding: 2px 10px;")
        self.btn_force_start.setToolTip(
            "Force an acquisition run to begin immediately regardless of current"
        )
        self.btn_force_start.clicked.connect(self._on_force_start_clicked)
        acq_top_lay.addWidget(self.btn_force_start)

        self.btn_force_stop = QPushButton("Force Stop")
        self.btn_force_stop.setStyleSheet("font-weight: bold; padding: 2px 10px;")
        self.btn_force_stop.setToolTip(
            "Force the active acquisition run to stop immediately regardless of current"
        )
        self.btn_force_stop.clicked.connect(self._on_force_stop_clicked)
        acq_top_lay.addWidget(self.btn_force_stop)
        acq_top_lay.addStretch()
        acq_lay.addLayout(acq_top_lay)

        # Run statistics grid
        stats_grid = QGridLayout()
        stats_grid.setContentsMargins(0, 4, 0, 0)
        stats_grid.setSpacing(4)

        lbl_avg_title = QLabel("Running Average:")
        lbl_avg_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        stats_grid.addWidget(lbl_avg_title, 0, 0)

        avg_font = QFont("Consolas", 18)
        avg_font.setBold(True)
        self.lbl_average = QLabel("  —    ")
        self.lbl_average.setFont(avg_font)
        self.lbl_average.setStyleSheet(theme.status_label(theme.MUTED))
        stats_grid.addWidget(self.lbl_average, 0, 1)

        self.lbl_avg_detail = QLabel("Not connected")
        self.lbl_avg_detail.setStyleSheet(
            f"color: {theme.MUTED}; font-style: italic; font-size: {theme.FS_TINY}px;"
        )
        stats_grid.addWidget(self.lbl_avg_detail, 1, 1)

        lbl_dur_title = QLabel("Duration:")
        lbl_dur_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        stats_grid.addWidget(lbl_dur_title, 2, 0)

        self.lbl_duration = QLabel("  —    ")
        self.lbl_duration.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        stats_grid.addWidget(self.lbl_duration, 2, 1)

        lbl_smp_title = QLabel("Samples:")
        lbl_smp_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        stats_grid.addWidget(lbl_smp_title, 3, 0)

        self.lbl_samples = QLabel("  —    ")
        self.lbl_samples.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        stats_grid.addWidget(self.lbl_samples, 3, 1)

        acq_lay.addLayout(stats_grid)
        acq_lay.addStretch()

        mid_row.addWidget(acq_box, stretch=1)
        layout.addLayout(mid_row)

        # ── 2b. Sampling Cycle (ADR 0003, ticket 09) ──────────────────────────
        cycle_box = QGroupBox("Sampling Cycle")
        # Outer vertical layout so the fault label (ticket 10) occupies a full row
        # below the controls row without needing a second QGroupBox.
        _cycle_vlay = QVBoxLayout(cycle_box)
        _cycle_vlay.setContentsMargins(12, 8, 12, 8)
        _cycle_vlay.setSpacing(4)
        cycle_lay = QHBoxLayout()
        cycle_lay.setSpacing(16)
        _cycle_vlay.addLayout(cycle_lay)

        # Period and dwell spinners (uses project input widgets, never bare spinbox)
        cycle_lay.addWidget(QLabel("Period:"))
        self.spn_cycle_period = QuietDoubleSpinBox()
        self.spn_cycle_period.setRange(10.0, 86400.0)
        self.spn_cycle_period.setDecimals(0)
        self.spn_cycle_period.setValue(CUP_CYCLE_PERIOD_S)
        self.spn_cycle_period.setToolTip(
            "Seconds between successive scheduled cup insertions (10 – 86400 s)"
        )
        self.spn_cycle_period.editingFinished.connect(self._on_cycle_period_changed)
        cycle_lay.addLayout(unit_row(self.spn_cycle_period, "s"))

        cycle_lay.addWidget(QLabel("Dwell:"))
        self.spn_cycle_dwell = QuietDoubleSpinBox()
        self.spn_cycle_dwell.setRange(1.0, 60.0)
        self.spn_cycle_dwell.setDecimals(1)
        self.spn_cycle_dwell.setValue(CUP_CYCLE_DWELL_S)
        self.spn_cycle_dwell.setToolTip(
            "Seconds the cup stays in the beam per scheduled insertion (1 – 60 s)"
        )
        self.spn_cycle_dwell.editingFinished.connect(self._on_cycle_dwell_changed)
        cycle_lay.addLayout(unit_row(self.spn_cycle_dwell, "s"))

        # Arm / Disarm button
        self.btn_cycle_arm = QPushButton("Arm Cycle")
        self.btn_cycle_arm.setStyleSheet("font-weight: bold; padding: 4px 14px;")
        self.btn_cycle_arm.setToolTip(
            "Arm the sampling cycle — first insertion at start + period"
        )
        self.btn_cycle_arm.clicked.connect(self._on_cycle_arm_clicked)
        cycle_lay.addWidget(self.btn_cycle_arm)

        # Stop button
        self.btn_cycle_stop = QPushButton("Stop Cycle")
        self.btn_cycle_stop.setStyleSheet("font-weight: bold; padding: 4px 14px;")
        self.btn_cycle_stop.setToolTip(
            "Stop the sampling cycle; retracts the cup if currently inserting"
        )
        self.btn_cycle_stop.setEnabled(False)
        self.btn_cycle_stop.clicked.connect(self._on_cycle_stop_clicked)
        cycle_lay.addWidget(self.btn_cycle_stop)

        cycle_lay.addSpacing(8)

        # Status label (cycle state + time to next insertion)
        self.lbl_cycle_state = QLabel("Disarmed")
        self.lbl_cycle_state.setStyleSheet(
            f"color: {theme.MUTED}; font-style: italic; font-size: {theme.FS_LABEL}px;"
        )
        self.lbl_cycle_state.setMinimumWidth(260)
        cycle_lay.addWidget(self.lbl_cycle_state, stretch=1)

        # Fault label — arming refusal reason or disarm-on-fault reason (ticket 10)
        # Shown below the control row so the operator cannot dismiss it without fixing
        # the underlying issue.  Uses FAULT colour role from theme, never a hex code.
        self.lbl_cycle_fault = QLabel("")
        self.lbl_cycle_fault.setStyleSheet(theme.status_label(theme.FAULT))
        self.lbl_cycle_fault.setWordWrap(True)
        self.lbl_cycle_fault.setVisible(False)
        _cycle_vlay.addWidget(self.lbl_cycle_fault)

        layout.addWidget(cycle_box)

        # ── 2d. Dose & Displacement Parameters (ADR 0003) ─────────────────────
        dose_box = QGroupBox("Dose Tracking & Displacement Parameters")
        dose_grid = QGridLayout(dose_box)
        dose_grid.setContentsMargins(12, 6, 12, 6)
        dose_grid.setHorizontalSpacing(12)
        dose_grid.setVerticalSpacing(4)

        # Col 0-1: Irradiated Area (from Raster Planner)
        lbl_area_title = QLabel("Irradiated Area:")
        lbl_area_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_area_title, 0, 0)

        self.lbl_area = QLabel("  —    ")
        self.lbl_area.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(self.lbl_area, 0, 1)

        lbl_src_title = QLabel("Area Source:")
        lbl_src_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_src_title, 1, 0)

        self.lbl_area_source = QLabel("Raster Planner")
        self.lbl_area_source.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        dose_grid.addWidget(self.lbl_area_source, 1, 1)

        # Col 2-3: Displacement Coefficient k & Depth
        lbl_k_title = QLabel("Displacement Coeff (k):")
        lbl_k_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_k_title, 0, 2)

        self.spn_k = ScientificDoubleSpinBox()
        self.spn_k.setRange(0.0, 1.0)
        self.spn_k.setValue(0.0)
        self.spn_k.setToolTip("Displacement damage coefficient k in dpa per (ions/cm²)")
        dose_grid.addLayout(unit_row(self.spn_k, "dpa/(ions/cm²)"), 0, 3)

        lbl_depth_title = QLabel("Damage Depth:")
        lbl_depth_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_depth_title, 1, 2)

        self.spn_depth = QuietDoubleSpinBox()
        self.spn_depth.setRange(0.0, 1e7)
        self.spn_depth.setDecimals(1)
        self.spn_depth.setValue(0.0)
        self.spn_depth.setToolTip("Sample damage depth for SRIM calculation in nanometers")
        dose_grid.addLayout(unit_row(self.spn_depth, "nm"), 1, 3)

        # Col 4-5: Provenance (SRIM version & entry date)
        lbl_srim_title = QLabel("SRIM Version:")
        lbl_srim_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_srim_title, 0, 4)

        self.le_srim_version = QLineEdit()
        self.le_srim_version.setPlaceholderText("e.g. SRIM-2013.00")
        self.le_srim_version.setToolTip("SRIM calculation code version")
        dose_grid.addWidget(self.le_srim_version, 0, 5)

        lbl_date_title = QLabel("Entry Date:")
        lbl_date_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_date_title, 1, 4)

        self.le_entry_date = QLineEdit()
        self.le_entry_date.setPlaceholderText("YYYY-MM-DD")
        self.le_entry_date.setToolTip("Date coefficient was derived or entered")
        dose_grid.addWidget(self.le_entry_date, 1, 5)

        # Row 2: Ion charge state q (required for arming — ticket 10)
        lbl_q_title = QLabel("Ion Charge State (q):")
        lbl_q_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_q_title, 2, 0)

        self.spn_charge_state = QuietDoubleSpinBox()
        self.spn_charge_state.setRange(0, 99)
        self.spn_charge_state.setDecimals(0)
        self.spn_charge_state.setValue(0)
        self.spn_charge_state.setToolTip(
            "Ion charge state q (positive integer; 0 = not configured)"
        )
        dose_grid.addLayout(unit_row(self.spn_charge_state, "e"), 2, 1)

        # Row 2 Col 2-5: Running totals (ticket 10)
        lbl_run_q_title = QLabel("Accumulated Q:")
        lbl_run_q_title.setStyleSheet(f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};")
        dose_grid.addWidget(lbl_run_q_title, 2, 2)

        self.lbl_running_q = QLabel("  —  ")
        self.lbl_running_q.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(self.lbl_running_q, 2, 3, 1, 3)

        # Row 3: Fluence and dpa
        lbl_run_fluence_title = QLabel("Accumulated Fluence:")
        lbl_run_fluence_title.setStyleSheet(
            f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(lbl_run_fluence_title, 3, 2)

        self.lbl_running_fluence = QLabel("  —  ")
        self.lbl_running_fluence.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(self.lbl_running_fluence, 3, 3, 1, 3)

        # Row 4: dpa and target
        lbl_run_dpa_title = QLabel("Accumulated dpa:")
        lbl_run_dpa_title.setStyleSheet(
            f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(lbl_run_dpa_title, 4, 0)

        self.lbl_running_dpa = QLabel("  —  ")
        self.lbl_running_dpa.setStyleSheet(
            f"font-family: Consolas, 'Courier New', monospace; "
            f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(self.lbl_running_dpa, 4, 1, 1, 2)

        lbl_target_title = QLabel("Target dpa:")
        lbl_target_title.setStyleSheet(
            f"font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
        )
        dose_grid.addWidget(lbl_target_title, 4, 3)

        self.spn_target_dpa = ScientificDoubleSpinBox()
        self.spn_target_dpa.setRange(0.0, 100.0)
        self.spn_target_dpa.setValue(0.0)
        self.spn_target_dpa.setToolTip(
            "Target dpa for this irradiation run (display only — reaching the target "
            "does not stop the beam, move slits, or alter the raster)"
        )
        dose_grid.addLayout(unit_row(self.spn_target_dpa, "dpa"), 4, 4, 1, 2)

        layout.addWidget(dose_box)

        # ── 3. Live Plot Panel ────────────────────────────────────────────────
        plot_box = QGroupBox("Live Current Trace")
        pv = QVBoxLayout(plot_box)
        pv.setContentsMargins(8, 8, 8, 8)
        pv.setSpacing(6)

        # LivePlotPanel manages canvas, history slider, and LIVE/FROZEN state
        self.plot = LivePlotPanel(
            window_seconds=self.WINDOW_SECONDS,
            live_edge_provider=self._live_edge,
            span_provider=self._buffer_span,
            min_window_seconds=self.MIN_WINDOW_SECONDS,
            figsize=(7, 3),
            redraw_interval_ms=200,
        )
        self.plot.navigation_changed.connect(self._on_navigation_changed)
        self.plot.zoom_changed.connect(self._on_zoom_changed)
        self.plot.redraw_timer.timeout.connect(self._redraw_plot)

        # Navigation row: mode indicator + time-window controls + jump-to-live
        nav_row = QHBoxLayout()
        self.lbl_mode = QLabel("● LIVE  (last 120 s)")
        self.lbl_mode.setStyleSheet(theme.status_label(theme.OK) + " padding: 2px 6px;")
        nav_row.addWidget(self.lbl_mode)

        lbl_time = QLabel("  Time:")
        lbl_time.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 15px;")
        nav_row.addWidget(lbl_time)

        btn_time_out = QPushButton("－")
        btn_time_out.setFixedWidth(28)
        btn_time_out.setToolTip("Increase time window (zoom out)")
        btn_time_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_out.clicked.connect(self.plot.zoom_out)
        nav_row.addWidget(btn_time_out)

        btn_time_in = QPushButton("＋")
        btn_time_in.setFixedWidth(28)
        btn_time_in.setToolTip("Decrease time window (zoom in)")
        btn_time_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_in.clicked.connect(self.plot.zoom_in)
        nav_row.addWidget(btn_time_in)

        nav_row.addStretch()

        self.btn_jump_live = QPushButton("Jump to Live")
        self.btn_jump_live.setVisible(False)
        self.btn_jump_live.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold; padding:2px 8px; }"
            "QPushButton:hover { background:#0063b1; }"
        )
        self.btn_jump_live.clicked.connect(self.plot.jump_to_live)
        nav_row.addWidget(self.btn_jump_live)
        pv.addLayout(nav_row)

        self.ax = self.plot.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Current (A)")
        self.ax.yaxis.set_label_position("right")
        self.ax.yaxis.tick_right()
        self.ax.grid(True, which="both", alpha=0.3)
        self._line, = self.ax.plot([], [], color="#004e8c", lw=1.5, label="Cup Current")
        self.plot.fig.tight_layout()

        pv.addWidget(self.plot.canvas, stretch=1)
        pv.addLayout(self.plot.slider_row)

        layout.addWidget(plot_box, stretch=1)

        self._on_navigation_changed()
        self._set_disconnected_view()
        self._set_actuation_disconnected_view()
        self._update_cycle_view(0.0)

    # ── Connection Handling ───────────────────────────────────────────────────

    def get_resource(self) -> str:
        """Return configured VISA resource string."""
        return self.le_resource.text().strip() or KEITHLEY_6482_DEFAULT_RESOURCE

    def connect_if_needed(self) -> tuple[str, str]:
        """Connect picoammeter if not already connected."""
        if self.beamline is None:
            return "failed", "No beamline attached"
        if self.beamline.picoammeter_connected:
            return "already", "Faraday cup picoammeter already connected"
        try:
            res = self.get_resource()
            self.beamline.connect_picoammeter(res)
            return "connected", f"Faraday cup picoammeter ({res})"
        except Exception as exc:
            return "failed", f"Picoammeter: {exc}"

    def _on_connect_clicked(self) -> None:
        if self.beamline is None:
            return
        if self._connected:
            self.beamline.disconnect_picoammeter()
        else:
            resource = self.get_resource()
            try:
                self.beamline.connect_picoammeter(resource)
            except Exception as exc:
                log.exception("FaradayCupTab connect failed: %s", exc)

    def _on_force_start_clicked(self) -> None:
        t_now = time.time()
        transition = self.acquisition.force_start(t=t_now)
        if transition is not None:
            self.session_writer.write_run_opened(
                t_host=t_now,
                run_id=transition.run_id,
                arm_threshold=transition.arm_threshold,
                release_threshold=transition.release_threshold,
                forced=True,
            )
            if self.beamline is not None:
                self.beamline.set_cup_acquiring(True)
        self._update_acquisition_view()

    def _on_force_stop_clicked(self) -> None:
        t_now = time.time()
        transition = self.acquisition.force_stop(t=t_now)
        if transition is not None:
            self.session_writer.write_run_closed(
                t_host=t_now,
                run_id=transition.run_id,
                reason=transition.reason,
            )
            stats = self.session_writer.last_insertion_stats
            mean_current = stats.mean_a
            std_current = stats.std_a
            cnt = stats.sample_count
            conf_t = (
                self._current_run_start_t
                if not math.isnan(self._current_run_start_t)
                else t_now
            )
            dwell = max(0.0, t_now - conf_t)
            self._accumulator.record_insertion(
                t_in=conf_t,
                t_out=t_now,
                mean_current_a=mean_current,
            )
            beam_on_s = self._accumulator.last_beam_on_s
            q = self._accumulator.total_charge_c
            cs = self.charge_state
            area = self._area_cm2
            k = self.displacement_coeff
            fluence = self._accumulator.fluence(cs, area)
            dpa = self._accumulator.dpa(cs, area, k)

            cmd_t = self._last_commanded_in_t
            self.session_writer.write_insertion_summary(
                run_id=transition.run_id,
                commanded_timestamp=cmd_t,
                confirmed_timestamp=conf_t,
                dwell=dwell,
                sample_count=cnt,
                mean_current_a=mean_current,
                std_current_a=std_current,
                beam_on_seconds=beam_on_s,
                charge=q,
                charge_state=cs,
                area=area,
                k=k,
                fluence=fluence,
                dpa=dpa,
                t_host=t_now,
                details="force_stop",
            )
            self._last_commanded_in_t = None
            self._current_run_start_t = float("nan")
            self._update_dose_view()
            if self.beamline is not None:
                self.beamline.set_cup_acquiring(False)
        self._update_acquisition_view()

    def _update_acquisition_view(self) -> None:
        """Update run status, duration, sample count, and running average."""
        if not self._connected:
            self.lbl_run_status.setText("Disconnected")
            self.lbl_run_status.setStyleSheet(theme.status_label(theme.MUTED))
            self.btn_force_start.setEnabled(False)
            self.btn_force_stop.setEnabled(False)
            self.lbl_average.setText("  —    ")
            self.lbl_average.setStyleSheet(theme.status_label(theme.MUTED))
            self.lbl_avg_detail.setText("Not connected")
            self.lbl_avg_detail.setStyleSheet(f"color: {theme.MUTED}; font-style: italic;")
            self.lbl_duration.setText("  —    ")
            self.lbl_samples.setText("  —    ")
            return

        stats = self.session_writer.active_run_stats

        if self.acquisition.is_acquiring:
            run_id = self.acquisition.current_run_id
            run_lbl = f"ACQUIRING (Run #{run_id})" if run_id else "ACQUIRING"
            self.lbl_run_status.setText(run_lbl)
            self.lbl_run_status.setStyleSheet(theme.status_label(theme.OK))
            self.btn_force_start.setEnabled(False)
            self.btn_force_stop.setEnabled(True)

            self.lbl_duration.setText(f"{stats.duration_s:.1f} s")

            if stats.over_range_samples > 0:
                self.lbl_samples.setText(
                    f"{stats.total_samples} ({stats.over_range_samples} over-range excluded)"
                )
                self.lbl_avg_detail.setText(
                    f"{stats.valid_samples} valid samples "
                    f"({stats.over_range_samples} over-range excluded)"
                )
                self.lbl_avg_detail.setStyleSheet(f"color: {theme.WARN}; font-style: italic;")
            else:
                self.lbl_samples.setText(f"{stats.total_samples}")
                self.lbl_avg_detail.setText(
                    f"{stats.valid_samples} valid samples"
                    if stats.valid_samples > 0
                    else "0 samples"
                )
                self.lbl_avg_detail.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")

            if stats.average_current_a is not None:
                self.lbl_average.setText(format_current(stats.average_current_a))
                self.lbl_average.setStyleSheet(theme.status_label(theme.OK))
            else:
                self.lbl_average.setText("  —    ")
                self.lbl_average.setStyleSheet(theme.status_label(theme.MUTED))
        else:
            if self.acquisition.cup_in_beam:
                self.lbl_run_status.setText("In Beam (Arming)")
                self.lbl_run_status.setStyleSheet(theme.status_label(theme.WARN))
            else:
                self.lbl_run_status.setText("Idle")
                self.lbl_run_status.setStyleSheet(theme.status_label(theme.MUTED))
            self.btn_force_start.setEnabled(True)
            self.btn_force_stop.setEnabled(False)

            self.lbl_duration.setText("  —    ")
            self.lbl_samples.setText("  —    ")
            self.lbl_average.setText("  —    ")
            self.lbl_average.setStyleSheet(theme.status_label(theme.MUTED))
            self.lbl_avg_detail.setText("No active run")
            self.lbl_avg_detail.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")

    def _set_disconnected_view(self, t: float | None = None) -> None:
        """Reset tab to clean, honest disconnected state."""
        t_now = time.time() if t is None else t
        was_connected = self._connected
        self._connected = False
        self._update_redraw_state()

        self.status_pill.set_connected(False)
        self.btn_connect.setText("Connect")
        self.lbl_current.setText("  —    ")
        self.lbl_current.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_detail.setText("Not connected")
        self.lbl_detail.setStyleSheet(f"color: {theme.MUTED}; font-style: italic;")
        self.lbl_ident.setText("")
        self.lbl_meta.setText("")

        transition = self.acquisition.disconnect(t=t_now)
        if transition is not None:
            self.session_writer.write_run_closed(
                t_host=t_now,
                run_id=transition.run_id,
                reason=transition.reason,
            )
        if was_connected:
            self.session_writer.write_disconnected(t_host=t_now)
        if self.beamline is not None:
            self.beamline.set_cup_acquiring(False)

        self._line.set_data([], [])
        self.ax.relim()
        self.plot.canvas.draw_idle()
        self._update_acquisition_view()

    # ── Snapshot / State Updates ──────────────────────────────────────────────

    def on_cup_state(self, state: CupState) -> None:
        """Render a CupState snapshot published by Beamline and log samples/markers."""
        t_sample = (
            state.t_host
            if (state.t_host == state.t_host and state.t_host is not None)
            else time.time()
        )

        if not state.connected:
            self._set_disconnected_view(t=t_sample)
            return

        was_connected = self._connected
        self._connected = True
        self._update_redraw_state()
        self.status_pill.set_connected(True)
        self.btn_connect.setText("Disconnect")

        if not was_connected:
            self.session_writer.write_connected(
                t_host=t_sample,
                ident=self.lbl_ident.text(),
                resource=self.get_resource(),
            )
            self._last_heartbeat_t = t_sample

        # Build reading including confirmed position and auto_mode from the latest
        # actuation snapshot so the authority rule can select the right source.
        # confirmed_position is None when T7 is not connected or status is stale
        # (non-FULL profile), which causes position_authoritative() to return False
        # and inference to govern — correct for hand insertions and diagnostic profiles.
        actuation_confirmed = (
            self._confirmed
            if self._actuation_connected and not self._stale
            else None
        )
        actuation_auto = self._auto_mode if self._actuation_connected else None
        reading = CupReading(
            t=t_sample,
            current=state.current,
            over_range=state.over_range,
            connected=True,
            confirmed_position=actuation_confirmed,
            auto_mode=actuation_auto,
        )
        transition = self.acquisition.update(reading)

        if transition is not None:
            if isinstance(transition, RunOpened):
                self._current_run_start_t = transition.t
                self.session_writer.write_run_opened(
                    t_host=t_sample,
                    run_id=transition.run_id,
                    arm_threshold=transition.arm_threshold,
                    release_threshold=transition.release_threshold,
                    forced=transition.forced,
                    t_inst=state.timestamp,
                )
            elif isinstance(transition, RunClosed):
                self.session_writer.write_run_closed(
                    t_host=t_sample,
                    run_id=transition.run_id,
                    reason=transition.reason,
                    t_inst=state.timestamp,
                )
                stats = self.session_writer.last_insertion_stats
                mean_current = stats.mean_a
                std_current = stats.std_a
                cnt = stats.sample_count
                conf_t = (
                    self._current_run_start_t
                    if not math.isnan(self._current_run_start_t)
                    else t_sample
                )
                dwell = max(0.0, t_sample - conf_t)

                self._accumulator.record_insertion(
                    t_in=conf_t,
                    t_out=t_sample,
                    mean_current_a=mean_current,
                )
                beam_on_s = self._accumulator.last_beam_on_s
                q = self._accumulator.total_charge_c
                cs = self.charge_state
                area = self._area_cm2
                k = self.displacement_coeff
                fluence = self._accumulator.fluence(cs, area)
                dpa = self._accumulator.dpa(cs, area, k)

                cmd_t = self._last_commanded_in_t
                self.session_writer.write_insertion_summary(
                    run_id=transition.run_id,
                    commanded_timestamp=cmd_t,
                    confirmed_timestamp=conf_t,
                    dwell=dwell,
                    sample_count=cnt,
                    mean_current_a=mean_current,
                    std_current_a=std_current,
                    beam_on_seconds=beam_on_s,
                    charge=q,
                    charge_state=cs,
                    area=area,
                    k=k,
                    fluence=fluence,
                    dpa=dpa,
                    t_host=t_sample,
                )
                self._last_commanded_in_t = None
                self._current_run_start_t = float("nan")
                self._update_dose_view()
            if self.beamline is not None:
                self.beamline.set_cup_acquiring(self.acquisition.is_acquiring)

        # Log disagreement fault when authority detector reports disagreement
        authority_det = self.acquisition.detector
        if isinstance(authority_det, AuthorityDetector):
            if authority_det.disagreement:
                if not self._disagreement_logged:
                    self.session_writer.write_fault_disagreement(
                        t_host=t_sample,
                        position=actuation_confirmed or CupPosition.INDETERMINATE,
                        current=state.current,
                        t_inst=state.timestamp,
                    )
                    self._disagreement_logged = True
            else:
                self._disagreement_logged = False

        # Log sample if acquiring, or periodic heartbeat if idle
        if self.acquisition.is_acquiring:
            run_id = self.acquisition.current_run_id or 1
            self.session_writer.write_sample(
                t_host=t_sample,
                t_inst=state.timestamp,
                current=state.current,
                status_word=state.status_word,
                over_range=state.over_range,
                run_id=run_id,
            )
        else:
            if (t_sample - self._last_heartbeat_t) >= CUP_IDLE_HEARTBEAT_INTERVAL_S:
                self.session_writer.write_idle_heartbeat(
                    t_host=t_sample,
                    t_inst=state.timestamp,
                )
                self._last_heartbeat_t = t_sample

        # Append to live plot rolling buffer
        val_to_plot = (
            state.current
            if (
                not state.over_range
                and state.current is not None
                and state.current == state.current
            )
            else float("nan")
        )
        self.buffer.append(t_sample, val_to_plot)

        if self.plot.is_live:
            self.plot.force_to_live()

        self._update_acquisition_view()

        if state.over_range:
            self.lbl_current.setText("OVER-RANGE")
            self.lbl_current.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_detail.setText("Over-range detected")
            self.lbl_detail.setStyleSheet(f"color: {theme.FAULT}; font-style: italic;")
        elif state.unavailable or state.current is None:
            self.lbl_current.setText("  —    ")
            self.lbl_current.setStyleSheet(theme.status_label(theme.WARN))
            self.lbl_detail.setText("Reading unavailable")
            self.lbl_detail.setStyleSheet(f"color: {theme.WARN}; font-style: italic;")
        else:
            self.lbl_current.setText(format_current(state.current))
            self.lbl_current.setStyleSheet(theme.status_label(theme.OK))
            self.lbl_detail.setText("Reading OK")
            self.lbl_detail.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")

        meta_parts: list[str] = []
        if state.status_word:
            meta_parts.append(f"Status: 0x{state.status_word:08X}")
        if state.timestamp == state.timestamp and state.timestamp is not None:  # not NaN
            meta_parts.append(f"Inst time: {state.timestamp:.3f} s")
        self.lbl_meta.setText("  |  ".join(meta_parts))

    def on_cup_connected(self, ident: str) -> None:
        """Called when Keithley 6482 connects successfully."""
        was_connected = self._connected
        self._connected = True
        self._update_redraw_state()
        self.status_pill.set_connected(True)
        self.btn_connect.setText("Disconnect")
        self.lbl_ident.setText(f"{ident}" if ident else "Keithley 6482")
        self.btn_force_start.setEnabled(True)
        self.btn_force_stop.setEnabled(False)
        if not was_connected:
            self.session_writer.write_connected(
                t_host=time.time(),
                ident=ident,
                resource=self.get_resource(),
            )

    def on_cup_disconnected(self) -> None:
        """Called when Keithley 6482 disconnects."""
        self._set_disconnected_view()

    def on_cup_error(self, msg: str) -> None:
        """Called when a worker/communication error occurs."""
        log.warning("FaradayCupTab error: %s", msg)
        self.lbl_detail.setText(f"Error: {msg}")
        self.lbl_detail.setStyleSheet(f"color: {theme.FAULT}; font-style: italic;")

    # ── Visibility and Lifecycle ──────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._visible = True
        self._update_redraw_state()
        self._redraw_plot()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._visible = False
        self._update_redraw_state()

    def _update_redraw_state(self) -> None:
        """Redraw timer only ticks when connected and tab is currently visible."""
        if self._connected and self._visible:
            self.plot.start()
        else:
            self.plot.stop()

    def shutdown(self) -> None:
        """Stop plot redraw timer and close session writer cleanly."""
        self.plot.stop()
        self.session_writer.close()

    def closeEvent(self, event) -> None:
        """Close session writer when widget closes."""
        self.shutdown()
        super().closeEvent(event)

    # ── Live Plot Data Callbacks and Redraw ────────────────────────────────────

    def _live_edge(self) -> float | None:
        """Newest timestamp available in the buffer, or None."""
        t, _ = self.buffer.latest()
        if t == t and t is not None:  # not NaN
            return float(t)
        return None

    def _buffer_span(self) -> tuple[float, float] | None:
        """(t_oldest, t_newest) across the buffer, or None."""
        t_arr, _ = self.buffer.snapshot()
        if len(t_arr) < 2:
            return None
        return (float(t_arr[0]), float(t_arr[-1]))

    def _set_live_label(self) -> None:
        w = self.plot.window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        self.lbl_mode.setText(f"● LIVE  (last {label})")
        self.lbl_mode.setStyleSheet(theme.status_label(theme.OK) + " padding: 2px 6px;")

    def _on_navigation_changed(self) -> None:
        self.btn_jump_live.setVisible(not self.plot.is_live)
        if self.plot.is_live:
            self._set_live_label()
            return
        span = self._buffer_span()
        t_oldest = span[0] if span is not None else self.plot.frozen_right_edge
        right_edge = self.plot.frozen_right_edge or 0.0
        oldest = t_oldest if t_oldest is not None else right_edge
        w_start = max(oldest, right_edge - self.plot.window_seconds)
        self.lbl_mode.setText(
            f"⏸  Frozen  —  t = [{w_start:+.0f} s … {right_edge:+.0f} s]"
        )
        self.lbl_mode.setStyleSheet(
            f"color: {theme.WARN}; font-weight: bold; padding: 2px 6px;"
        )

    def _on_zoom_changed(self) -> None:
        if self.plot.is_live:
            self._set_live_label()
            return
        w = self.plot.window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        self.lbl_mode.setText(f"⏸  Frozen  —  window {label}")

    def _redraw_plot(self) -> None:
        """Redraw matplotlib live plot trace."""
        window = self.plot.compute_window()
        if window is None:
            return
        t_left, t_right = window

        t, v = self.buffer.snapshot()
        if len(t) < 2:
            return
        mask = (t >= t_left) & (t <= t_right)
        if mask.sum() < 2:
            self._line.set_data([], [])
            return

        t_win = t[mask]
        v_win = v[mask]
        if len(t_win) > 600:
            step = len(t_win) // 600
            t_win = t_win[::step]
            v_win = v_win[::step]

        self._line.set_data(t_win - t_right, v_win)
        self.ax.set_xlim(-self.plot.window_seconds, 0)
        self.ax.relim()
        self.ax.autoscale_view(scalex=False, scaley=True)
        self.plot.canvas.draw_idle()

    # ── Cup Actuation and Confirmed Position (ADR 0003) ──────────────────────

    def _on_insert_clicked(self) -> None:
        """Handle operator pressing Insert button."""
        if self._move_in_flight:
            return
        if not self._actuation_connected:
            return
        # Inform the cycle scheduler that a manual insertion is starting
        t_now = self._last_state_t if not math.isnan(self._last_state_t) else time.time()
        self.cycle.notify_manual_insert(t_now)
        self._start_move(CupPosition.IN)
        if self.beamline is not None:
            self.beamline.command_cup_in()
        self._update_cycle_view(t_now)

    def _on_retract_clicked(self) -> None:
        """Handle operator pressing Retract button."""
        if self._move_in_flight:
            return
        if not self._actuation_connected:
            return
        # Inform the cycle scheduler that a manual retraction is starting
        t_now = self._last_state_t if not math.isnan(self._last_state_t) else time.time()
        self.cycle.notify_manual_retract(t_now)
        self._start_move(CupPosition.OUT)
        if self.beamline is not None:
            self.beamline.command_cup_out()
        self._update_cycle_view(t_now)

    def _start_move(self, target: CupPosition) -> None:
        """Initiate a commanded cup move and start confirmation timeout tracking."""
        self._move_in_flight = True
        self._move_target = target
        start_t = self._last_state_t if not math.isnan(self._last_state_t) else time.time()
        self._move_start_t = start_t
        if target == CupPosition.IN:
            self._last_commanded_in_t = start_t
        self._move_fault = ""
        self._commanded = target
        self._update_actuation_view()

    # ── Sampling Cycle (ADR 0003, ticket 09) ─────────────────────────────────

    def _on_cycle_arm_clicked(self) -> None:
        """Arm or disarm the sampling cycle.

        WHY THE GUARD EXISTS (ticket 10 / ADR 0003)
        -------------------------------------------
        An eight-hour run that produces charge with no dpa attached has failed
        at its only job: nothing in the archive can recover the coefficient
        afterwards.  Arming is therefore refused unless the whole dose chain is
        configured (coefficient, charge state, irradiated area) and position
        feedback is available (FULL stream profile, T7 connected).

        The refusal is not a warning the operator can dismiss — it names exactly
        which input is missing and stays visible until the issue is resolved.
        """
        t_now = self._last_state_t if not math.isnan(self._last_state_t) else time.time()
        if self.cycle.is_armed:
            self.cycle.disarm()
            self._cycle_fault = ""
            self._update_cycle_view(t_now)
            return

        reason = self._check_arm_preconditions()
        if reason:
            self._cycle_fault = f"Cannot arm: {reason}"
            self._update_cycle_view(t_now)
            return

        self._cycle_fault = ""
        self.session_writer.update_session_parameters(
            species=self.species,
            energy=self.beam_energy,
            charge_state=self.charge_state,
            area_cm2=self.irradiated_area_cm2,
            k=self.displacement_coeff,
            k_depth=self.damage_depth,
            srim_version=self.srim_version,
            entry_date=self.entry_date,
            cycle_period_s=self.cycle_period,
            cycle_dwell_s=self.cycle_dwell,
        )
        self.cycle.arm(t=t_now)
        self._update_cycle_view(t_now)

    def _check_arm_preconditions(self) -> str:
        """Return '' if all arming preconditions are met, else the first missing item.

        Checks in order: displacement coefficient, charge state, irradiated area,
        then position feedback availability.  Returns the first blocking reason so
        the operator is told exactly one thing to fix at a time.
        """
        if self.displacement_coeff == 0.0:
            return "displacement coefficient (k) is required"
        if self.charge_state <= 0:
            return "ion charge state is required"
        if self._area_cm2 <= 0.0:
            return "irradiated area is required — configure in Raster Planner"
        if not self._actuation_connected or self._stale:
            return "position feedback unavailable — connect T7 and use FULL stream profile"
        return ""

    def _on_cycle_stop_clicked(self) -> None:
        """Stop the cycle; retract the cup if currently inserting."""
        t_now = self._last_state_t if not math.isnan(self._last_state_t) else time.time()
        retract_action = self.cycle.stop(t=t_now)
        if retract_action is not None and not self._move_in_flight and self._actuation_connected:
            self._start_move(CupPosition.OUT)
            if self.beamline is not None:
                self.beamline.command_cup_out()
        self._update_cycle_view(t_now)

    def _on_cycle_period_changed(self) -> None:
        """Apply updated period from the spinner to the scheduler."""
        self.cycle.set_period(self.spn_cycle_period.value())

    def _on_cycle_dwell_changed(self) -> None:
        """Apply updated dwell from the spinner to the scheduler."""
        self.cycle.set_dwell(self.spn_cycle_dwell.value())

    def _tick_cycle(self, t: float) -> None:
        """Advance the cycle scheduler to time t and act on the result.

        Called on every on_cup_actuation_state update. The scheduler is pure
        and calls no clock; t comes from the T7 window timestamp.
        """
        if not self.cycle.is_armed:
            return

        result = self.cycle.tick(t)
        if result is None:
            return

        if isinstance(result, CycleInsert):
            # Scheduled insertion — command cup IN if no move is already in flight
            if not self._move_in_flight and self._actuation_connected:
                self._start_move(CupPosition.IN)
                if self.beamline is not None:
                    self.beamline.command_cup_in()

        elif isinstance(result, CycleRetract):
            # Dwell expired — command cup OUT
            if not self._move_in_flight and self._actuation_connected:
                self._start_move(CupPosition.OUT)
                if self.beamline is not None:
                    self.beamline.command_cup_out()

        elif isinstance(result, CycleSkipped):
            # Scheduled insertion skipped because a manual run was open.
            # Record the skip in the session file; do not command the cup.
            self.session_writer.write_cycle_insertion_skipped(
                t_host=t,
                insertion_due_t=result.insertion_due_t,
            )

        self._update_cycle_view(t)

    def _update_cycle_view(self, t: float) -> None:
        """Update the Sampling Cycle panel to reflect the current scheduler state."""
        state = self.cycle.state
        if state == CycleState.DISARMED:
            self.btn_cycle_arm.setText("Arm Cycle")
            self.btn_cycle_arm.setStyleSheet("font-weight: bold; padding: 4px 14px;")
            self.btn_cycle_stop.setEnabled(False)
            self.lbl_cycle_state.setText("Disarmed")
            self.lbl_cycle_state.setStyleSheet(
                f"color: {theme.MUTED}; font-style: italic; font-size: {theme.FS_LABEL}px;"
            )
        elif state == CycleState.WAITING:
            self.btn_cycle_arm.setText("Disarm Cycle")
            self.btn_cycle_arm.setStyleSheet(
                f"font-weight: bold; padding: 4px 14px; color: {theme.WARN};"
            )
            self.btn_cycle_stop.setEnabled(True)
            tti = self.cycle.time_to_next_insertion(t)
            if tti is not None:
                mins, secs = divmod(int(tti), 60)
                countdown = f"{mins:02d}:{secs:02d}"
                self.lbl_cycle_state.setText(
                    f"Armed — next insertion in {countdown}"
                )
            else:
                self.lbl_cycle_state.setText("Armed — waiting")
            self.lbl_cycle_state.setStyleSheet(
                f"color: {theme.OK}; font-size: {theme.FS_LABEL}px;"
            )
        elif state == CycleState.INSERTING:
            self.btn_cycle_arm.setText("Disarm Cycle")
            self.btn_cycle_arm.setStyleSheet(
                f"font-weight: bold; padding: 4px 14px; color: {theme.WARN};"
            )
            self.btn_cycle_stop.setEnabled(True)
            ttr = self.cycle.time_to_retract(t)
            if ttr is not None:
                self.lbl_cycle_state.setText(
                    f"Armed — inserting (retract in {ttr:.1f} s)"
                )
            else:
                self.lbl_cycle_state.setText("Armed — inserting")
            self.lbl_cycle_state.setStyleSheet(
                f"color: {theme.OK}; font-weight: bold; font-size: {theme.FS_LABEL}px;"
            )

        # Show or hide the cycle fault label (arming refusal or disarm reason)
        if self._cycle_fault:
            self.lbl_cycle_fault.setText(self._cycle_fault)
            self.lbl_cycle_fault.setVisible(True)
        else:
            self.lbl_cycle_fault.setVisible(False)

    def on_cup_actuation_state(self, state: CupActuationState) -> None:
        """Ingest CupActuationState snapshot published by Beamline (ADR 0003)."""
        if not state.connected:
            self._set_actuation_disconnected_view()
            return

        self._actuation_connected = True
        t_now = state.t if not math.isnan(state.t) else time.time()
        self._last_state_t = t_now
        _was_stale = self._stale

        # If commanded position changed externally (e.g. from script or another tab)
        if state.commanded != self._commanded:
            self._commanded = state.commanded
            if state.commanded != state.confirmed and not self._move_in_flight:
                self._move_in_flight = True
                self._move_target = state.commanded
                self._move_start_t = t_now
                self._move_fault = ""

        # Move confirmation and timeout checking
        if self._move_in_flight and self._move_target is not None:
            if state.confirmed == self._move_target:
                # Move confirmed!
                self._move_in_flight = False
                self._move_target = None
                self._move_fault = ""
            elif state.confirmed == CupPosition.INDETERMINATE:
                # Indeterminate contact reading during move
                self._move_in_flight = False
                target_name = "IN" if self._move_target == CupPosition.IN else "OUT"
                self._move_fault = (
                    f"Move Fault: Command {target_name} failed: contacts indeterminate"
                )
                self._move_target = None
            elif not math.isnan(self._move_start_t):
                elapsed = t_now - self._move_start_t
                if elapsed >= CUP_MOVE_CONFIRMATION_TIMEOUT_S:
                    self._move_in_flight = False
                    target_name = "IN" if self._move_target == CupPosition.IN else "OUT"
                    self._move_fault = (
                        f"Move Fault: Command {target_name} did not confirm "
                        f"within {CUP_MOVE_CONFIRMATION_TIMEOUT_S:.1f} s"
                    )
                    self.session_writer.write_fault_move_not_confirmed(
                        t_host=t_now,
                        commanded=self._move_target,
                        timeout_s=CUP_MOVE_CONFIRMATION_TIMEOUT_S,
                        details=self._move_fault,
                    )
                    self._move_target = None
                    # ADR 0003 decision 3: disarm the cycle — a retry loop is exactly
                    # the failure mode this decision exists to prevent.  The cup is left
                    # wherever it is; drive release is the wiring's job, not software's.
                    if self.cycle.is_armed:
                        self._cycle_fault = (
                            f"Cycle disarmed: move Command {target_name} did not confirm "
                            f"within {CUP_MOVE_CONFIRMATION_TIMEOUT_S:.1f} s"
                        )
                        self.cycle.disarm()
                        self._update_cycle_view(t_now)

        # Log confirmed position transitions (IN / OUT) and indeterminate faults
        if state.confirmed in (CupPosition.IN, CupPosition.OUT):
            if state.confirmed != self._last_logged_confirmed:
                t_trans = (
                    state.last_transition_t
                    if not math.isnan(state.last_transition_t)
                    else t_now
                )
                self.session_writer.write_position_transition(
                    t_host=t_trans,
                    position=state.confirmed,
                    details=f"confirmed_{state.confirmed.value.lower()}",
                )
                self._last_logged_confirmed = state.confirmed
                self._last_logged_indeterminate = False
        elif state.confirmed == CupPosition.INDETERMINATE:
            if not self._last_logged_indeterminate:
                self.session_writer.write_fault_impossible_status(
                    t_host=t_now,
                    details="both IN and OUT contacts asserted",
                )
                self._last_logged_indeterminate = True
                self._last_logged_confirmed = state.confirmed
        elif state.confirmed == CupPosition.IN_TRANSIT:
            self._last_logged_confirmed = state.confirmed
            self._last_logged_indeterminate = False

        # Log transition into LOCAL mode (not AUTO)
        if self._was_auto_mode is not None and self._was_auto_mode and not state.auto_mode:
            self.session_writer.write_fault_controller_not_in_auto(
                t_host=t_now,
                details="controller switched to LOCAL mode; remote commands ignored",
            )
        self._was_auto_mode = state.auto_mode

        self._commanded = state.commanded
        self._confirmed = state.confirmed
        self._auto_mode = state.auto_mode
        self._stale = state.stale

        # Disarm cycle when FIO_STATE goes stale (profile changed away from FULL).
        # Ticket 10 / ADR 0003: the inference thresholds cannot bound a 3-second insertion
        # and position feedback is exactly what prevents the run-boundary failures this
        # whole effort exists to fix.  Disarm immediately, name the reason.
        if not _was_stale and self._stale and self.cycle.is_armed:
            self._cycle_fault = (
                "Cycle disarmed: position feedback lost "
                "(stream profile changed from FULL)"
            )
            self.cycle.disarm()
            self._update_cycle_view(t_now)

        # Tick the sampling cycle scheduler on every actuation state update
        self._tick_cycle(t_now)

        self._update_actuation_view()

    def _update_actuation_view(self) -> None:
        """Update actuation buttons, commanded/confirmed indicators, and warning labels."""
        if not self._actuation_connected:
            self._set_actuation_disconnected_view()
            return

        self.btn_insert.setEnabled(True)
        self.btn_retract.setEnabled(True)

        # Commanded position indicator
        if self._commanded == CupPosition.IN:
            self.lbl_commanded.setText("IN")
            self.lbl_commanded.setStyleSheet(theme.status_label(theme.OK))
        elif self._commanded == CupPosition.OUT:
            self.lbl_commanded.setText("OUT")
            self.lbl_commanded.setStyleSheet(theme.status_label(theme.OK))
        else:
            self.lbl_commanded.setText("  —    ")
            self.lbl_commanded.setStyleSheet(theme.status_label(theme.MUTED))

        # Confirmed position indicator: four distinct states
        if self._confirmed == CupPosition.IN:
            self.lbl_confirmed.setText("IN")
            self.lbl_confirmed.setStyleSheet(theme.status_label(theme.OK))
        elif self._confirmed == CupPosition.OUT:
            self.lbl_confirmed.setText("OUT")
            self.lbl_confirmed.setStyleSheet(theme.status_label(theme.OK))
        elif self._confirmed == CupPosition.IN_TRANSIT:
            self.lbl_confirmed.setText("In Transit")
            self.lbl_confirmed.setStyleSheet(theme.status_label(theme.WARN))
        elif self._confirmed == CupPosition.INDETERMINATE:
            self.lbl_confirmed.setText("FAULT (Indeterminate)")
            self.lbl_confirmed.setStyleSheet(theme.status_label(theme.FAULT))
        else:
            self.lbl_confirmed.setText("  —    ")
            self.lbl_confirmed.setStyleSheet(theme.status_label(theme.MUTED))

        # Active source indicator (ADR 0003 Decision 4)
        authority_det = self.acquisition.detector
        if isinstance(authority_det, AuthorityDetector):
            if authority_det.using_position:
                self.lbl_source.setText("Source: confirmed position (T7 status contacts)")
                self.lbl_source.setStyleSheet(
                    f"color: {theme.OK}; font-size: {theme.FS_TINY}px;"
                )
            else:
                self.lbl_source.setText("Source: inference (cup current)")
                self.lbl_source.setStyleSheet(
                    f"color: {theme.NEUTRAL}; font-size: {theme.FS_TINY}px;"
                )
            self.lbl_source.setVisible(True)
        else:
            self.lbl_source.setVisible(False)

        # Fault label — indeterminate contacts, move fault, or source disagreement
        auth_det: AuthorityDetector | None = (
            authority_det if isinstance(authority_det, AuthorityDetector) else None
        )
        if self._confirmed == CupPosition.INDETERMINATE:
            self.lbl_fault.setText(
                "Fault: Indeterminate status contacts (both IN and OUT asserted)"
            )
            self.lbl_fault.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_fault.setVisible(True)
        elif auth_det is not None and auth_det.disagreement:
            inf_in = auth_det.inference_cup_in_beam
            pos_in = auth_det.position_cup_in_beam
            self.lbl_fault.setText(
                f"Disagreement: position says {'IN' if pos_in else 'OUT'} "
                f"but current inference says {'IN' if inf_in else 'OUT'}"
            )
            self.lbl_fault.setStyleSheet(theme.status_label(theme.WARN))
            self.lbl_fault.setVisible(True)
        elif self._move_fault:
            self.lbl_fault.setText(self._move_fault)
            self.lbl_fault.setStyleSheet(theme.status_label(theme.FAULT))
            self.lbl_fault.setVisible(True)
        else:
            self.lbl_fault.setText("")
            self.lbl_fault.setVisible(False)

        # AUTO mode status
        if not self._auto_mode:
            self.lbl_auto_mode.setText(
                "Controller not in AUTO (LOCAL mode) — remote commands will be accepted and ignored"
            )
            self.lbl_auto_mode.setStyleSheet(
                f"color: {theme.WARN}; font-weight: bold; font-size: {theme.FS_TINY}px;"
            )
            self.lbl_auto_mode.setVisible(True)
        else:
            self.lbl_auto_mode.setText("AUTO mode (remote actuation enabled)")
            self.lbl_auto_mode.setStyleSheet(f"color: {theme.OK}; font-size: {theme.FS_TINY}px;")
            self.lbl_auto_mode.setVisible(True)

        # Stale reading status
        if self._stale:
            self.lbl_stale.setText(
                "Status reading is stale (position feedback unavailable in non-FULL profile)"
            )
            self.lbl_stale.setStyleSheet(
                f"color: {theme.WARN}; font-style: italic; font-size: {theme.FS_TINY}px;"
            )
            self.lbl_stale.setVisible(True)
        else:
            self.lbl_stale.setText("")
            self.lbl_stale.setVisible(False)

    def _set_actuation_disconnected_view(self) -> None:
        """Reset actuation panel to disconnected view."""
        self._actuation_connected = False
        self._move_in_flight = False
        self._move_target = None
        self._move_fault = ""
        self.btn_insert.setEnabled(False)
        self.btn_retract.setEnabled(False)
        self.lbl_commanded.setText("  —    ")
        self.lbl_commanded.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_confirmed.setText("  —    ")
        self.lbl_confirmed.setStyleSheet(theme.status_label(theme.MUTED))
        self.lbl_auto_mode.setText("")
        self.lbl_auto_mode.setVisible(False)
        self.lbl_stale.setText("")
        self.lbl_stale.setVisible(False)
        self.lbl_source.setText("")
        self.lbl_source.setVisible(False)
        self.lbl_fault.setText("")
        self.lbl_fault.setVisible(False)

    # ── Dose Tracking & Displacement Parameters (ADR 0003) ───────────────────

    def on_patch_dimensions_changed(self, width_x_mm: float, height_y_mm: float) -> None:
        """Update irradiated area from Raster Planner patch dimensions.

        ADR 0003 Decision 7: The irradiated area comes directly from the Raster
        Planner tab's patch dimensions to avoid duplicate operator entry and ensure
        exact consistency between deflection planning and dose accumulation.
        """
        self._patch_width_x_mm = float(width_x_mm)
        self._patch_height_y_mm = float(height_y_mm)
        self._area_cm2 = patch_area_cm2(self._patch_width_x_mm, self._patch_height_y_mm)
        if self._area_cm2 > 0.0:
            dims = f"{self._patch_width_x_mm:.3f} × {self._patch_height_y_mm:.3f} mm"
            self.lbl_area.setText(f"{self._area_cm2:.4f} cm² ({dims})")
        else:
            self.lbl_area.setText("  —    ")

    @property
    def area_cm2(self) -> float:
        """Irradiated area in cm² derived from Raster Planner patch dimensions."""
        return self._area_cm2

    @property
    def area_source(self) -> str:
        """Source of the irradiated area calculation (e.g. 'Raster Planner')."""
        return self._area_source

    @property
    def patch_dimensions_mm(self) -> tuple[float, float]:
        """Patch dimensions (width X, height Y) in mm."""
        return (self._patch_width_x_mm, self._patch_height_y_mm)

    @property
    def displacement_coeff(self) -> float:
        """Operator-entered SRIM displacement damage coefficient k in dpa/(ions/cm²)."""
        return self.spn_k.value()

    @property
    def depth_nm(self) -> float:
        """Operator-entered damage depth in nanometers."""
        return self.spn_depth.value()

    @property
    def damage_depth(self) -> float:
        """Operator-entered damage depth in nanometers."""
        return self.spn_depth.value()

    @property
    def species(self) -> str:
        """Active beam species name."""
        return self._species

    @property
    def beam_energy(self) -> str:
        """Active beam energy description."""
        return self._beam_energy

    @property
    def irradiated_area_cm2(self) -> float:
        """Irradiated sample area in cm² from Raster Planner."""
        return self._area_cm2

    def on_species_changed(self, name: str, energy_ev: float, charge: int) -> None:
        """Handle active species update from the Raster Planner tab (ADR 0003)."""
        self._species = name
        self._beam_energy = f"{energy_ev / 1e6:.3f} MeV"
        if charge > 0:
            self.spn_charge_state.setValue(charge)
        self.session_writer.update_session_parameters(
            species=self._species,
            energy=self._beam_energy,
            charge_state=self.charge_state,
        )

    @property
    def srim_version(self) -> str:
        """Operator-entered SRIM calculation version."""
        return self.le_srim_version.text().strip()

    @property
    def entry_date(self) -> str:
        """Operator-entered provenance date for the displacement coefficient."""
        return self.le_entry_date.text().strip()

    @property
    def charge_state(self) -> int:
        """Operator-entered ion charge state q (positive integer; 0 = not configured)."""
        return int(self.spn_charge_state.value())

    @property
    def cycle_period(self) -> float:
        """Configured sampling cycle period in seconds."""
        return self.spn_cycle_period.value()

    @property
    def cycle_dwell(self) -> float:
        """Configured sampling cycle dwell time in seconds."""
        return self.spn_cycle_dwell.value()

    def _update_dose_view(self) -> None:

        """Refresh running Q, fluence, and dpa labels from the dose accumulator.

        WHY THIS IS CALLED ON RUN CLOSE, NOT ON EVERY SAMPLE
        ------------------------------------------------------
        ADR 0003 Decision 7: dose uses a zero-order hold — each insertion's
        current is held constant across the beam-on interval between insertions.
        The accumulator is only updated at run close, so refreshing on every
        sample would show stale values between insertions. The labels update
        once per insertion, which is the correct resolution for this measurement.

        TARGET DPA IS DISPLAY-ONLY
        --------------------------
        Reaching the target dpa changes only label colour.  Nothing in this
        method stops the beam, moves slits, or alters the raster — doing so
        would turn a display aid into a safety interlock, which ADR 0003
        explicitly does not create.
        """
        q = self._accumulator.total_charge_c
        cs = self.charge_state
        area = self._area_cm2
        k = self.displacement_coeff

        self.lbl_running_q.setText(f"{q:.6e} C")

        if cs > 0 and area > 0.0:
            fluence = self._accumulator.fluence(cs, area)
            self.lbl_running_fluence.setText(f"{fluence:.4e} ions/cm²")

            if k > 0.0:
                dpa = self._accumulator.dpa(cs, area, k)
                self.lbl_running_dpa.setText(f"{dpa:.4e} dpa")

                target = self.spn_target_dpa.value()
                if target > 0.0 and dpa >= target:
                    self.lbl_running_dpa.setStyleSheet(theme.status_label(theme.OK))
                else:
                    self.lbl_running_dpa.setStyleSheet(
                        f"font-family: Consolas, 'Courier New', monospace; "
                        f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.NEUTRAL};"
                    )
            else:
                self.lbl_running_dpa.setText("  —  (k not set)")
                self.lbl_running_dpa.setStyleSheet(
                    f"font-family: Consolas, 'Courier New', monospace; "
                    f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.MUTED};"
                )
        else:
            self.lbl_running_fluence.setText("  —  (inputs missing)")
            self.lbl_running_fluence.setStyleSheet(
                f"font-family: Consolas, 'Courier New', monospace; "
                f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.MUTED};"
            )
            self.lbl_running_dpa.setText("  —  (inputs missing)")
            self.lbl_running_dpa.setStyleSheet(
                f"font-family: Consolas, 'Courier New', monospace; "
                f"font-weight: bold; font-size: {theme.FS_LABEL}px; color: {theme.MUTED};"
            )
