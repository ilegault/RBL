"""
vacuum_tab.py
PySide6 widget for the "Vacuum" outer tab.

DELIBERATE SCOPE RESTRICTION
-----------------------------
This tab is intentionally READ-ONLY.  It has no emission on/off toggle, no
degas control, no setpoints, and no calibration controls.  This is an explicit
design decision, not an oversight.  Future sessions should not add control
widgets here.

The tab subscribes to Beamline.vacuum_changed (VacuumState snapshots) and
Beamline.vacuum_error (string messages).  It never holds a driver reference
or opens a serial port.
"""
import logging
import time

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QSizePolicy, QLineEdit, QHeaderView,
)

from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.config.vacuum_config import (
    GAUGE_DISPLAY_NAMES, UI_GOOD_VACUUM_TORR, STALE_THRESHOLD_S,
    VGC_ACTIVE_CHANNELS,
)

log = logging.getLogger(__name__)

# Maximum pressure history retained per gauge (1 Hz poll → 1 h)
_MAX_HISTORY = 3600

# Table column indices
_COL_NAME  = 0
_COL_INST  = 1
_COL_CH    = 2
_COL_PRESS = 3
_COL_UNITS = 4
_COL_STATE = 5
_COL_AGE   = 6
_N_COLS    = 7


class VacuumTab(QWidget):
    """Vacuum pressure display tab.

    Subscribes to beamline.vacuum_changed for live VacuumState snapshots.
    All layout sections are described in-line below.
    """

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline    = beamline
        self._last_state = None    # most recent VacuumState
        self._last_time  = 0.0    # wall-clock time of last received state

        # Per-gauge pressure history: label -> [(unix_time, pressure|None)]
        self._history:   dict[str, list] = {}
        # Gauge label -> plot line object
        self._plot_lines: dict[str, object] = {}
        # Logging service — set by Phase 6
        self._logger     = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection bars ───────────────────────────────────────────────────
        conn_box = QGroupBox("Gauge Controllers")
        conn_lay = QVBoxLayout(conn_box)

        self._xgs_bar  = _InstrumentBar("XGS-600",  "Agilent XGS-600")
        self._vgc_bar  = _InstrumentBar("VGC083",   "INFICON VGC083")
        self._xgs_bar.connect_clicked.connect(self._on_xgs_connect)
        self._vgc_bar.connect_clicked.connect(self._on_vgc_connect)
        self._xgs_bar.disconnect_clicked.connect(self._on_vacuum_disconnect)
        self._vgc_bar.disconnect_clicked.connect(self._on_vacuum_disconnect)

        conn_lay.addWidget(self._xgs_bar)
        conn_lay.addWidget(self._vgc_bar)
        layout.addWidget(conn_box)

        # ── Gauge table ───────────────────────────────────────────────────────
        tbl_box = QGroupBox("Gauge Readings")
        tbl_lay = QVBoxLayout(tbl_box)

        self._table = QTableWidget(0, _N_COLS)
        self._table.setHorizontalHeaderLabels([
            "Display Name", "Instrument", "Channel",
            "Pressure", "Units", "State", "Age (s)",
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        tbl_lay.addWidget(self._table)
        layout.addWidget(tbl_box)

        # ── VGC083 ion-gauge status strip ─────────────────────────────────────
        ig_box = QGroupBox("VGC083 Ion Gauge Status")
        ig_lay = QHBoxLayout(ig_box)

        ig_lay.addWidget(QLabel("IG:"))
        self._lbl_ig_on = StatusPill("● ON", "● OFF")
        self._lbl_ig_on.set_connected(False, "● —")
        ig_lay.addWidget(self._lbl_ig_on)

        ig_lay.addSpacing(20)
        ig_lay.addWidget(QLabel("Degas:"))
        self._lbl_degas = QLabel("—")
        self._lbl_degas.setStyleSheet(f"color: {theme.NEUTRAL};")
        ig_lay.addWidget(self._lbl_degas)

        ig_lay.addSpacing(20)
        ig_lay.addWidget(QLabel("Fault:"))
        self._lbl_fault = QLabel("—")
        self._lbl_fault.setStyleSheet(f"color: {theme.NEUTRAL};")
        ig_lay.addWidget(self._lbl_fault)

        ig_lay.addStretch()
        layout.addWidget(ig_box)

        # ── Rolling pressure plot (log Y axis) ────────────────────────────────
        plot_box = QGroupBox("Pressure History")
        plot_lay = QVBoxLayout(plot_box)

        self._fig = Figure(figsize=(8, 3))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._ax = self._fig.add_subplot(111)
        self._ax.set_yscale("log")
        self._ax.set_ylabel("Pressure")
        self._ax.set_xlabel("Time (s ago)")
        self._ax.grid(True, which="both", alpha=0.3)
        self._fig.tight_layout()
        plot_lay.addWidget(self._canvas)

        # Channel toggle legend area (simple labels for now)
        self._lbl_channels = QLabel("(no gauges connected)")
        self._lbl_channels.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        plot_lay.addWidget(self._lbl_channels)

        layout.addWidget(plot_box, stretch=1)

        # ── Logging bar ───────────────────────────────────────────────────────
        log_bar = QHBoxLayout()
        self._btn_log = QPushButton("Start Logging")
        self._btn_log.setEnabled(False)   # enabled in Phase 6
        self._btn_log.setToolTip("Pressure logging is implemented in Phase 6")
        self._lbl_log_path = QLabel("(not logging)")
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;"
        )
        log_bar.addWidget(self._btn_log)
        log_bar.addWidget(self._lbl_log_path, stretch=1)
        layout.addLayout(log_bar)

        # ── Subscribe to Beamline signals ─────────────────────────────────────
        self.beamline.vacuum_changed.connect(self._on_vacuum_state)
        self.beamline.vacuum_error.connect(self._on_vacuum_error)

        # ── Redraw timer (100 ms) ─────────────────────────────────────────────
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(100)
        self._redraw_timer.timeout.connect(self._redraw)
        self._redraw_timer.start()

        # Plot redraw at lower rate (1 s is plenty for pressure data)
        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(1000)
        self._plot_timer.timeout.connect(self._redraw_plot)
        self._plot_timer.start()

    # -----------------------------------------------------------------------
    # Connection bar handlers
    # -----------------------------------------------------------------------

    def _on_xgs_connect(self):
        port = self._xgs_bar.port_text()
        ports = {"xgs600": port} if port else {}
        self.beamline.connect_vacuum(ports or None)

    def _on_vgc_connect(self):
        port = self._vgc_bar.port_text()
        ports = {"vgc083": port} if port else {}
        self.beamline.connect_vacuum(ports or None)

    def _on_vacuum_disconnect(self):
        self.beamline.disconnect_vacuum()
        self._xgs_bar.set_connected(False)
        self._vgc_bar.set_connected(False)

    # -----------------------------------------------------------------------
    # Beamline signal handlers
    # -----------------------------------------------------------------------

    def _on_vacuum_state(self, state):
        """Receive VacuumState from Beamline; cache for next redraw tick."""
        self._last_state = state
        self._last_time  = time.time()

        # Update connection bars
        self._xgs_bar.set_connected(state.xgs_connected)
        self._vgc_bar.set_connected(state.vgc_connected)

        # Append to history
        now = time.time()
        for r in state.xgs_readings:
            key = f"xgs600:{r.channel.label}"
            self._history.setdefault(key, []).append((now, r.pressure))
            if len(self._history[key]) > _MAX_HISTORY:
                self._history[key] = self._history[key][-_MAX_HISTORY:]

        for r in state.vgc_readings:
            key = f"vgc083:{r.channel}"
            self._history.setdefault(key, []).append((now, r.pressure))
            if len(self._history[key]) > _MAX_HISTORY:
                self._history[key] = self._history[key][-_MAX_HISTORY:]

    def _on_vacuum_error(self, msg: str):
        log.warning("vacuum_tab: error signal: %s", msg)

    # -----------------------------------------------------------------------
    # Table redraw (100 ms timer)
    # -----------------------------------------------------------------------

    def _redraw(self):
        state    = self._last_state
        age      = time.time() - self._last_time if self._last_time else float("inf")
        is_stale = age > STALE_THRESHOLD_S

        rows = []
        if state is not None:
            for r in state.xgs_readings:
                key  = f"xgs600:{r.channel.label}"
                name = GAUGE_DISPLAY_NAMES.get(key, r.channel.label)
                rows.append((name, "XGS-600", r.channel.label,
                             r.pressure, state.units_xgs, r.state))
            for r in state.vgc_readings:
                key  = f"vgc083:{r.channel}"
                name = GAUGE_DISPLAY_NAMES.get(key, r.channel)
                rows.append((name, "VGC083", r.channel,
                             r.pressure, state.units_vgc, r.state))

        self._table.setRowCount(len(rows))
        for row_idx, (name, inst, ch, pressure, units, state_str) in enumerate(rows):
            stale = is_stale

            # Pressure cell: show state text if not OK, engineering notation if OK
            if pressure is not None and state_str == "OK":
                press_text = f"{pressure:.3e}"
                press_color = (theme.OK if pressure <= UI_GOOD_VACUUM_TORR
                               else theme.NEUTRAL)
            else:
                press_text  = state_str
                press_color = theme.FAULT if state_str not in ("OFF", "OFF_OR_OVERRANGE") \
                              else theme.WARN

            if stale:
                press_color = theme.MUTED

            age_text = f"{age:.0f}" if state is not None else "—"

            for col, text in enumerate([name, inst, ch, press_text, units,
                                         state_str, age_text]):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == _COL_PRESS:
                    item.setForeground(
                        self._table.palette().text() if not stale
                        else self._table.palette().placeholderText()
                    )
                self._table.setItem(row_idx, col, item)

            # Colour the pressure cell
            if self._table.item(row_idx, _COL_PRESS):
                self._table.item(row_idx, _COL_PRESS).setForeground(
                    __import__("PySide6.QtGui", fromlist=["QColor"]).QColor(press_color)
                )

        # IG status strip (updated from last VGC readings in state)
        if state is not None and state.vgc_connected:
            ig_readings = [r for r in state.vgc_readings if r.channel == "IG"]
            if ig_readings:
                r = ig_readings[0]
                ig_on = r.state == "OK" and r.pressure is not None
                self._lbl_ig_on.set_connected(ig_on,
                                               "● ON" if ig_on else "● OFF")
            else:
                self._lbl_ig_on.set_connected(False, "● —")
        else:
            self._lbl_ig_on.set_connected(False, "● —")
            self._lbl_degas.setText("—")
            self._lbl_fault.setText("—")

    # -----------------------------------------------------------------------
    # Plot redraw (1 s timer)
    # -----------------------------------------------------------------------

    def _redraw_plot(self):
        if not self._history:
            return

        now = time.time()
        self._ax.cla()
        self._ax.set_yscale("log")
        self._ax.set_ylabel("Pressure")
        self._ax.set_xlabel("Time (s ago)")
        self._ax.grid(True, which="both", alpha=0.3)

        plotted = []
        for key, pts in self._history.items():
            # Keep only points with valid pressure for log-scale plotting
            valid = [(t, p) for t, p in pts if p is not None and p > 0]
            if not valid:
                continue
            ts, ps = zip(*valid)
            xs = [now - t for t in ts]
            self._ax.plot(xs, ps, label=key.split(":")[-1])
            plotted.append(key)

        if plotted:
            self._ax.legend(loc="upper left", fontsize=8)
            self._ax.invert_xaxis()

        self._canvas.draw_idle()

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    def shutdown(self):
        self._redraw_timer.stop()
        self._plot_timer.stop()


# ---------------------------------------------------------------------------
# Per-instrument connection bar (inline; LabJackPanel API does not fit)
# ---------------------------------------------------------------------------

class _InstrumentBar(QGroupBox):
    """Simple row: instrument name | port entry | status | connect/disconnect."""

    from PySide6.QtCore import Signal
    connect_clicked    = Signal()
    disconnect_clicked = Signal()

    def __init__(self, key: str, display_name: str, parent=None):
        super().__init__(display_name, parent)
        self._key       = key
        self._connected = False

        lay = QHBoxLayout(self)
        lay.addWidget(QLabel("Port:"))

        self._le_port = QLineEdit()
        self._le_port.setPlaceholderText("e.g. COM4  (leave blank to auto-discover)")
        self._le_port.setMaximumWidth(200)
        lay.addWidget(self._le_port)

        self._btn = QPushButton("Connect")
        self._btn.clicked.connect(self._on_click)
        lay.addWidget(self._btn)

        self._pill = StatusPill()
        lay.addWidget(self._pill)
        lay.addStretch()

    def _on_click(self):
        if self._connected:
            self.disconnect_clicked.emit()
        else:
            self.connect_clicked.emit()

    def port_text(self) -> str:
        return self._le_port.text().strip()

    def set_connected(self, connected: bool, detail: str = ""):
        self._connected = connected
        self._btn.setText("Disconnect" if connected else "Connect")
        text = f"● Connected  {detail}".strip() if connected else "● Disconnected"
        self._pill.set_connected(connected, text)
