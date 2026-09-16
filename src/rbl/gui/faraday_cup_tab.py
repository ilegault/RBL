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
"""
from __future__ import annotations

import logging
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
    CUP_IDLE_HEARTBEAT_INTERVAL_S,
    KEITHLEY_6482_DEFAULT_RESOURCE,
)
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.gui.widgets.live_plot import LivePlotPanel
from rbl.hardware.current_monitor import RollingBuffer, format_current
from rbl.services.cup_acquisition import (
    CupAcquisitionStateMachine,
    CupReading,
    RunClosed,
    RunOpened,
)
from rbl.services.cup_session_writer import CupSessionWriter
from rbl.snapshots import CupState

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
        self.acquisition = CupAcquisitionStateMachine()
        self.session_writer: CupSessionWriter = (
            session_writer if session_writer is not None else CupSessionWriter()
        )
        self._last_heartbeat_t: float = 0.0
        self.buffer = RollingBuffer(self.BUFFER_CAPACITY)

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
            "VISA resource identifier for the Keithley 6482 (e.g. GPIB0::14::INSTR)"
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

        # Update acquisition state machine
        reading = CupReading(
            t=t_sample,
            current=state.current,
            over_range=state.over_range,
            connected=True,
        )
        transition = self.acquisition.update(reading)

        if transition is not None:
            if isinstance(transition, RunOpened):
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
            if self.beamline is not None:
                self.beamline.set_cup_acquiring(self.acquisition.is_acquiring)

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
