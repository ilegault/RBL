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
from collections import deque

import matplotlib

from rbl.services.vacuum_logger import VacuumLogger

matplotlib.use("QtAgg")
# The Figure/Canvas pair now lives inside LivePlotPanel; this module only
# needs the backend selected before that widget is constructed.

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rbl.config.vacuum_config import (
    GAUGE_DISPLAY_NAMES,
    STALE_THRESHOLD_S,
    UI_GOOD_VACUUM_TORR,
)
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.gui.widgets.inputs import NoScrollSpinBox
from rbl.gui.widgets.live_plot import LivePlotPanel
from rbl.gui.widgets.port_picker import PortPicker, PortScanWorker

log = logging.getLogger(__name__)

# Maximum pressure history retained per gauge (1 Hz poll → 24 h)
_MAX_HISTORY = 86_400

# Default visible time window, and the floor for zoom-in.  Pressure is polled
# at 1 Hz, so zooming below a few seconds shows nothing useful.
_PLOT_WINDOW_S     = 300.0
_PLOT_MIN_WINDOW_S = 5.0

# Per-instrument colour palettes so XGS and VGC traces never overlap visually.
# XGS-600: warm tones (reds / oranges / yellows)
_XGS_COLORS = ["#d62728", "#ff7f0e", "#e377c2", "#bcbd22"]
# VGC083:  cool tones (blues / greens / purples)
_VGC_COLORS = ["#1f77b4", "#2ca02c", "#17becf", "#9467bd"]
# Fallback for unknown instruments
_FALLBACK_COLORS = ["#7f7f7f", "#8c564b"]

# Channel-index counters are tracked per instrument inside VacuumTab;
# this helper picks the right palette + index.
def _color_for_key(key: str, index_in_instrument: int) -> str:
    if key.startswith("xgs600:"):
        pal = _XGS_COLORS
    elif key.startswith("vgc083:"):
        pal = _VGC_COLORS
    else:
        pal = _FALLBACK_COLORS
    return pal[index_in_instrument % len(pal)]

# Persistence key for the set of gauges the operator has hidden.
_HIDDEN_CFG_KEY = "vacuum_hidden_gauges"

# Pressure-column font size, in POINTS.  Deliberately not theme.FS_BIG: that
# constant is documented as px for stylesheets, and this is a QFont point size.
_PRESSURE_FONT_CFG_KEY  = "vacuum_pressure_font_pt"
_PRESSURE_FONT_PT_DEFAULT = 19
_PRESSURE_FONT_PT_MIN     = 10
_PRESSURE_FONT_PT_MAX     = 72


def _load_pressure_font_pt() -> int:
    """Restore the pressure-column font size from the config store."""
    try:
        from rbl.config.persistence import load_config
        return int(load_config().get(_PRESSURE_FONT_CFG_KEY, _PRESSURE_FONT_PT_DEFAULT))
    except Exception as exc:
        log.debug("vacuum_tab: could not load pressure font pt: %s", exc)
        return _PRESSURE_FONT_PT_DEFAULT


def _save_pressure_font_pt(pt: int):
    """Persist the pressure-column font size.  Best effort; never raises."""
    try:
        from rbl.config.persistence import load_config, save_config
        cfg = load_config()
        cfg[_PRESSURE_FONT_CFG_KEY] = pt
        save_config(cfg)
    except Exception as exc:
        log.debug("vacuum_tab: could not save pressure font pt: %s", exc)


def _load_hidden_gauges() -> set:
    """Restore the operator's hidden-gauge selection from the config store."""
    try:
        from rbl.config.persistence import load_config
        return set(load_config().get(_HIDDEN_CFG_KEY, []))
    except Exception as exc:
        log.debug("vacuum_tab: could not load hidden gauges: %s", exc)
        return set()


def _save_hidden_gauges(hidden: set):
    """Persist the hidden-gauge selection.  Best effort; never raises."""
    try:
        from rbl.config.persistence import load_config, save_config
        cfg = load_config()
        cfg[_HIDDEN_CFG_KEY] = sorted(hidden)
        save_config(cfg)
    except Exception as exc:
        log.debug("vacuum_tab: could not save hidden gauges: %s", exc)

# Table column indices
_COL_NAME  = 0
_COL_INST  = 1
_COL_CH    = 2
_COL_PRESS = 3
_COL_UNITS = 4
_COL_STATE = 5
_N_COLS    = 6


class VacuumTab(QWidget):
    """Vacuum pressure display tab.

    Subscribes to beamline.vacuum_changed for live VacuumState snapshots.
    All layout sections are described in-line below.
    """

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline         = beamline
        self._last_state      = None    # most recent VacuumState
        self._last_time       = 0.0    # wall-clock time of last received state
        # Per-gauge pressure history: label -> [(unix_time, pressure|None)]
        self._history:        dict[str, list] = {}
        # Gauge label -> plot line object
        self._plot_lines:     dict[str, object] = {}
        # Gauge label -> QCheckBox in the legend panel
        self._gauge_checks:   dict[str, QCheckBox] = {}
        # Per-instrument channel count (for colour assignment within palette).
        self._xgs_ch_count:   int = 0
        self._vgc_ch_count:   int = 0
        # Gauge labels the operator has switched off.  A VGC083 channel that
        # is configured in VGC_ACTIVE_CHANNELS but has no gauge physically
        # attached still answers every poll — with the 1.10E+03 sentinel —
        # so it shows up as a real row.  Hiding is the operator's call, not
        # something we can infer, hence a manual toggle that persists.
        self._hidden_gauges:  set = _load_hidden_gauges()
        # Logging service — set by Phase 6
        self._logger          = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        conn_box = self._build_connection_box()
        tbl_box  = self._build_gauge_table_box()
        plot_box = self._build_plot_box()
        log_bar  = self._build_logging_bar()

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(conn_box)
        top_row.addWidget(tbl_box, stretch=1)
        layout.addLayout(top_row)
        layout.addWidget(plot_box, stretch=1)
        layout.addLayout(log_bar)

        self.beamline.vacuum_changed.connect(self._on_vacuum_state)
        self.beamline.vacuum_error.connect(self._on_vacuum_error)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(100)
        self._redraw_timer.timeout.connect(self._redraw)
        self._redraw_timer.start()

        self.plot.start()

    # ── Section builders ──────────────────────────────────────────────────────

    def _build_connection_box(self) -> QGroupBox:
        """Gauge Controllers group: auto-detect button + two instrument bars."""
        conn_box = QGroupBox("Gauge Controllers")
        conn_lay = QVBoxLayout(conn_box)
        conn_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        # Detect both, and stop there. This used to be "Auto-detect &
        # Connect All", which ran the scan ON THE GUI THREAD - seconds of
        # frozen window - and then started polling whatever it found. Now
        # the scan runs on its own thread and only fills the two dropdowns;
        # connecting stays a deliberate press per instrument.
        detect_row = QHBoxLayout()
        self._btn_auto = QPushButton("Detect both controllers")
        self._btn_auto.setToolTip(
            "Scan the COM ports for both gauge controllers and select what\n"
            "each one is on.  Does not connect.  Findings are saved so the\n"
            "next launch tries them first."
        )
        self._btn_auto.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 14px; }"
        )
        self._btn_auto.clicked.connect(self._on_auto_detect)
        detect_row.addWidget(self._btn_auto)

        self._lbl_scan = QLabel("")
        self._lbl_scan.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-style: italic;")
        detect_row.addWidget(self._lbl_scan, stretch=1)
        conn_lay.addLayout(detect_row)
        self._scan_worker = None

        # First argument is the serial_transport PROBE KEY, not a label -
        # Detect matches on it, and a display string there would probe for
        # an instrument that does not exist.
        self._xgs_bar  = _InstrumentBar("xgs600", "Agilent XGS-600",
                                        short_name="XGS-600")
        self._vgc_bar  = _InstrumentBar("vgc083", "INFICON VGC083",
                                        short_name="VGC083")
        self._xgs_bar.connect_clicked.connect(self._on_xgs_connect)
        self._vgc_bar.connect_clicked.connect(self._on_vgc_connect)
        self._xgs_bar.disconnect_clicked.connect(self._on_xgs_disconnect)
        self._vgc_bar.disconnect_clicked.connect(self._on_vgc_disconnect)
        self._xgs_bar.status_message.connect(self._on_scan_status)
        self._vgc_bar.status_message.connect(self._on_scan_status)

        conn_lay.addWidget(self._xgs_bar)
        conn_lay.addWidget(self._vgc_bar)
        conn_lay.addStretch()

        # ── VGC083 ion-gauge status (inside INFICON VGC083 groupbox) ──────────
        ig_row = QHBoxLayout()
        ig_row.addWidget(QLabel("IG:"))
        self._lbl_ig_on = StatusPill("● ON", "● OFF")
        self._lbl_ig_on.set_connected(False, "● —")
        ig_row.addWidget(self._lbl_ig_on)

        ig_row.addSpacing(20)
        ig_row.addWidget(QLabel("Degas:"))
        self._lbl_degas = QLabel("—")
        self._lbl_degas.setStyleSheet(f"color: {theme.NEUTRAL};")
        ig_row.addWidget(self._lbl_degas)

        ig_row.addSpacing(20)
        ig_row.addWidget(QLabel("Fault:"))
        self._lbl_fault = QLabel("—")
        self._lbl_fault.setStyleSheet(f"color: {theme.NEUTRAL};")
        ig_row.addWidget(self._lbl_fault)

        ig_row.addStretch()
        self._vgc_bar.layout().addLayout(ig_row)
        return conn_box

    def _build_gauge_table_box(self) -> QGroupBox:
        """Gauge Readings group: pressure table + font-size spinner."""
        tbl_box = QGroupBox("Gauge Readings")
        tbl_lay = QVBoxLayout(tbl_box)

        self._table = QTableWidget(80, _N_COLS)
        self._table.setHorizontalHeaderLabels([
            "Display Name", "Instrument", "Channel",
            "Pressure", "Units", "State",
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        # Bold, large font for the Pressure column so it stands out.
        self._pressure_font = QFont()
        self._pressure_font.setPointSize(_load_pressure_font_pt())
        self._pressure_font.setBold(True)

        # Font size spinner — rows auto-adjust to keep digits from clipping.
        font_row = QHBoxLayout()
        font_row.addStretch()
        font_row.addWidget(QLabel("Pressure font:"))
        self._spin_press_font = NoScrollSpinBox()
        self._spin_press_font.setRange(_PRESSURE_FONT_PT_MIN, _PRESSURE_FONT_PT_MAX)
        self._spin_press_font.setValue(self._pressure_font.pointSize())
        self._spin_press_font.setSuffix(" pt")
        self._spin_press_font.setToolTip(
            "Size of the pressure readings in the table below. Saved between sessions.")
        self._spin_press_font.valueChanged.connect(self._on_pressure_font_changed)
        font_row.addWidget(self._spin_press_font)
        tbl_lay.addLayout(font_row)

        tbl_lay.addWidget(self._table)
        self._apply_row_height()
        return tbl_box

    def _build_plot_box(self) -> QGroupBox:
        """Pressure History group: rolling log-scale plot with legend strip."""
        plot_box = QGroupBox("Pressure History")
        plot_lay = QVBoxLayout(plot_box)

        # Same shared chrome the log-amp and amplifier tabs use: history
        # slider, LIVE/FROZEN state machine, snapped zoom steps, redraw timer.
        self.plot = LivePlotPanel(
            window_seconds     = _PLOT_WINDOW_S,
            live_edge_provider = self._live_edge,
            span_provider      = self._history_span,
            min_window_seconds = _PLOT_MIN_WINDOW_S,
            figsize            = (8, 3),
            redraw_interval_ms = 500,     # 1 Hz data; 2 Hz redraw is plenty
        )
        self.plot.navigation_changed.connect(self._on_navigation_changed)
        self.plot.zoom_changed.connect(self._on_zoom_changed)
        self.plot.redraw_timer.timeout.connect(self._redraw_plot)

        # Nav row: mode label, time-window zoom, jump-to-live.
        nav_row = QHBoxLayout()
        self.lbl_mode = QLabel(f"● LIVE  (last {int(_PLOT_WINDOW_S)} s)")
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
        )
        nav_row.addWidget(self.lbl_mode)
        lbl_time = QLabel("  Time:")
        lbl_time.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 15px;")
        nav_row.addWidget(lbl_time)
        btn_time_out = QPushButton("－")
        btn_time_out.setFixedWidth(28)
        btn_time_out.setToolTip("Increase time window (zoom out)")
        btn_time_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_out.clicked.connect(self.plot.zoom_out)
        btn_time_in = QPushButton("＋")
        btn_time_in.setFixedWidth(28)
        btn_time_in.setToolTip("Decrease time window (zoom in)")
        btn_time_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_in.clicked.connect(self.plot.zoom_in)
        nav_row.addWidget(btn_time_out)
        nav_row.addWidget(btn_time_in)

        self._chk_autoscale = QCheckBox("Auto Y")
        self._chk_autoscale.setChecked(True)
        self._chk_autoscale.setToolTip(
            "Rescale the pressure axis to the visible traces on every redraw.\n"
            "Uncheck to hold the current decades while you compare readings."
        )
        nav_row.addWidget(self._chk_autoscale)

        nav_row.addStretch()
        self.btn_jump_live = QPushButton("Jump to Live")
        self.btn_jump_live.setVisible(False)
        self.btn_jump_live.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " padding:2px 8px; }"
            "QPushButton:hover { background:#0063b1; }"
        )
        self.btn_jump_live.clicked.connect(self.plot.jump_to_live)
        nav_row.addWidget(self.btn_jump_live)
        plot_lay.addLayout(nav_row)

        self._ax = self.plot.fig.add_subplot(111)
        self._ax.set_yscale("log")
        self._ax.set_ylabel("Pressure")
        self._ax.set_xlabel("Time (s, relative to window right edge)")
        self._ax.grid(True, which="both", alpha=0.3)
        self._ax_right = self._ax.secondary_yaxis("right")
        self._ax_right.set_ylabel("Pressure")
        self.plot.fig.tight_layout()

        # Gauge legend as a compact horizontal strip above the canvas.
        legend_bar = QWidget()
        legend_bar_lay = QHBoxLayout(legend_bar)
        legend_bar_lay.setContentsMargins(0, 0, 0, 0)
        legend_bar_lay.setSpacing(6)

        leg_title = QLabel("Gauges:")
        leg_title.setStyleSheet(
            f"font-size: 13px; color: {theme.NEUTRAL}; font-weight: bold;"
        )
        legend_bar_lay.addWidget(leg_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(30)
        scroll.setToolTip("Untick to hide a gauge from the plot and table.")
        self._legend_host = QWidget()
        self._legend_lay  = QHBoxLayout(self._legend_host)
        self._legend_lay.setSpacing(8)
        self._legend_lay.setContentsMargins(0, 0, 0, 0)
        self._lbl_no_gauges = QLabel("(no gauges yet)")
        self._lbl_no_gauges.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;"
        )
        self._legend_lay.addWidget(self._lbl_no_gauges)
        self._legend_lay.addStretch()
        scroll.setWidget(self._legend_host)
        legend_bar_lay.addWidget(scroll, stretch=1)

        self._btn_show_all = QPushButton("Show all")
        self._btn_show_all.setFixedWidth(80)
        self._btn_show_all.setToolTip("Re-enable every hidden gauge.")
        self._btn_show_all.clicked.connect(self._on_show_all_gauges)
        legend_bar_lay.addWidget(self._btn_show_all)

        plot_lay.addWidget(legend_bar)
        plot_lay.addWidget(self.plot.canvas, stretch=1)
        plot_lay.addLayout(self.plot.slider_row)
        return plot_box

    def _build_logging_bar(self) -> QHBoxLayout:
        """Bottom bar: Start/Stop Logging button + current log path label."""
        log_bar = QHBoxLayout()
        self._btn_log = QPushButton("Start Logging")
        self._btn_log.setEnabled(False)   # enabled once a state arrives
        self._lbl_log_path = QLabel("(not logging)")
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;"
        )
        self._btn_log.clicked.connect(self._on_log_toggle)
        log_bar.addWidget(self._btn_log)
        log_bar.addWidget(self._lbl_log_path, stretch=1)
        return log_bar

    # -----------------------------------------------------------------------
    # Connection bar handlers
    # -----------------------------------------------------------------------

    def _on_pressure_font_changed(self, pt: int):
        self._pressure_font.setPointSize(pt)
        self._apply_row_height()
        _save_pressure_font_pt(pt)
        # _redraw() runs on a 100 ms timer and rebuilds every cell, so the new
        # font appears within one tick.  No explicit re-render call needed.

    def _apply_row_height(self):
        """Rows must grow with the font or the digits get clipped mid-glyph."""
        from PySide6.QtGui import QFontMetrics
        metrics_h = QFontMetrics(self._pressure_font).height()
        self._table.verticalHeader().setDefaultSectionSize(max(32, metrics_h + 10))

    def _on_auto_detect(self):
        """Scan for both controllers on a background thread, then select them."""
        if self._scan_worker is not None:
            return
        self._btn_auto.setEnabled(False)
        self._btn_auto.setText("Scanning…")
        self._on_scan_status("probing serial ports for both controllers…",
                             theme.NEUTRAL)
        self._scan_worker = PortScanWorker(["xgs600", "vgc083"], self)
        self._scan_worker.scan_done.connect(self._on_auto_detect_done)
        self._scan_worker.start()

    def _on_auto_detect_done(self, ports: dict):
        self._btn_auto.setEnabled(True)
        self._btn_auto.setText("Detect both controllers")
        self._scan_worker = None

        self._xgs_bar.refresh_ports()
        self._vgc_bar.refresh_ports()
        if "xgs600" in ports:
            self._xgs_bar.set_port_text(ports["xgs600"])
        if "vgc083" in ports:
            self._vgc_bar.set_port_text(ports["vgc083"])

        if not ports:
            log.info("vacuum_tab: detect found no instruments")
            self._on_scan_status(
                "no gauge controller answered — check the cables and that "
                "each adapter has a driver", theme.WARN)
            return
        found = ", ".join(f"{k} on {v}" for k, v in sorted(ports.items()))
        missing = [k for k in ("xgs600", "vgc083") if k not in ports]
        msg = f"found {found}"
        if missing:
            msg += f"  —  no answer from {', '.join(missing)}"
        self._on_scan_status(msg, theme.OK if not missing else theme.WARN)

    def _on_scan_status(self, message: str, colour: str):
        self._lbl_scan.setText(message)
        self._lbl_scan.setStyleSheet(f"color: {colour}; font-style: italic;")

    def _on_xgs_connect(self):
        self._connect_one("xgs600", self._xgs_bar)

    def _on_vgc_connect(self):
        self._connect_one("vgc083", self._vgc_bar)

    def _connect_one(self, key: str, bar):
        """Connect one controller to the port selected in its dropdown.

        With 'Auto-detect' selected there is nothing to open yet, so this
        runs the probe FIRST - on the picker's own background thread, not
        this one - and the operator presses Connect again once a port is
        selected.  The old code called discover() inline here, which froze
        the window for the length of the scan.
        """
        port = bar.port_text()
        if port:
            self.beamline.connect_vacuum({key: port})
            return
        self._on_scan_status(
            f"no port selected for {key} — detecting, then press Connect",
            theme.NEUTRAL)
        bar.detect()

    def connect_if_needed(self) -> tuple:
        """Connect both gauge controllers on their selected/saved ports.

        (status, detail), for the Overview tab's Connect All.  A controller
        whose picker is on 'Auto-detect' falls back to the port saved by
        the last successful scan rather than starting one here: a probe
        opens every port in turn and can take seconds, and Connect All is
        already doing four other things.  Press 'Detect both controllers'
        on this tab once and the saved port is what Connect All uses from
        then on.
        """
        from rbl.hardware.serial_transport import load_saved_ports
        saved = {}
        try:
            saved = load_saved_ports() or {}
        except Exception:
            saved = {}

        live = self.beamline.vacuum_connected
        results, failed = [], []
        for key, bar, name in (("xgs600", self._xgs_bar, "XGS-600"),
                               ("vgc083", self._vgc_bar, "VGC083")):
            if live.get(key):
                results.append(f"{name} already connected")
                continue
            port = bar.port_text() or saved.get(key, "")
            if not port:
                failed.append(f"{name}: no port known — run Detect on the "
                              f"Vacuum tab")
                continue
            self.beamline.connect_vacuum({key: port})
            results.append(f"{name} on {port}")

        if failed and not results:
            return "failed", "; ".join(failed)
        if failed:
            return "partial", "; ".join(results + failed)
        return "connected", "; ".join(results)

    def _on_xgs_disconnect(self):
        self.beamline.disconnect_xgs600()
        self._xgs_bar.set_connected(False)

    def _on_vgc_disconnect(self):
        self.beamline.disconnect_vgc083()
        self._vgc_bar.set_connected(False)

    # -----------------------------------------------------------------------
    # Beamline signal handlers
    # -----------------------------------------------------------------------

    def _on_vacuum_state(self, state):
        """Receive VacuumState from Beamline; cache for next redraw tick."""
        self._last_state = state
        self._last_time  = time.time()

        # Enable logging button once we have at least one reading
        if not self._btn_log.isEnabled():
            self._btn_log.setEnabled(True)

        # Update connection bars
        self._xgs_bar.set_connected(state.xgs_connected)
        self._vgc_bar.set_connected(state.vgc_connected)

        # Append to history (deque caps automatically at _MAX_HISTORY)
        now = time.time()
        for r in state.xgs_readings:
            key = f"xgs600:{r.channel.label}"
            if key not in self._history:
                self._history[key] = deque(maxlen=_MAX_HISTORY)
            self._history[key].append((now, r.pressure))

        for r in state.vgc_readings:
            key = f"vgc083:{r.channel}"
            if key not in self._history:
                self._history[key] = deque(maxlen=_MAX_HISTORY)
            self._history[key].append((now, r.pressure))

        # Auto-start logging on the first measurement
        if self._logger is None:
            self._start_logging()

        # Forward to active logger; reopen if gauge set changed
        if self._logger is not None:
            ok = self._logger.write_row(state)
            if not ok:
                log.warning("vacuum_tab: gauge set changed mid-session — reopening logger")
                self._logger.close()
                self._logger = VacuumLogger(
                    self._build_gauge_labels(state),
                    output_dir=None,
                )
                self._logger.write_header_comment(self._build_comment_lines(state))
                self._logger.write_row(state)

    # -----------------------------------------------------------------------
    # Logging toggle
    # -----------------------------------------------------------------------

    def _on_log_toggle(self):
        if self._logger is None:
            self._start_logging()
        else:
            self._stop_logging()

    def _start_logging(self):
        state = self._last_state
        if state is None:
            return
        labels = self._build_gauge_labels(state)
        self._logger = VacuumLogger(labels)
        self._logger.write_header_comment(self._build_comment_lines(state))
        self._btn_log.setText("Stop Logging")
        self._lbl_log_path.setText(self._logger.csv_path)
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.OK}; font-size: 10px; font-style: italic;"
        )
        log.info("vacuum_tab: logging started -> %s", self._logger.csv_path)

    def _stop_logging(self):
        if self._logger is None:
            return
        path = self._logger.close()
        self._logger = None
        self._btn_log.setText("Start Logging")
        self._lbl_log_path.setText(f"Saved: {path}")
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;"
        )
        log.info("vacuum_tab: logging stopped — file closed: %s", path)

    @staticmethod
    def _build_gauge_labels(state) -> list[str]:
        labels = []
        for r in state.xgs_readings:
            labels.append(f"xgs600:{r.channel.label}")
        for r in state.vgc_readings:
            labels.append(f"vgc083:{r.channel}")
        return labels

    @staticmethod
    def _build_comment_lines(state) -> list[str]:
        lines = [f"vacuum_logger RBL {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"]
        if state.xgs_connected:
            lines.append(f"xgs600: units={state.units_xgs}")
        if state.vgc_connected:
            lines.append(f"vgc083: units={state.units_vgc}")
        return lines

    def _on_vacuum_error(self, msg: str):
        log.warning("vacuum_tab: error signal: %s", msg)

    # -----------------------------------------------------------------------
    # Gauge visibility
    # -----------------------------------------------------------------------

    def _is_visible(self, key: str) -> bool:
        return key not in self._hidden_gauges

    def _sync_legend(self):
        """Add a checkbox for any gauge seen for the first time.

        Rows are only ever added, never removed — a gauge that drops out
        mid-run (cable pulled, controller reset) keeps its toggle so the
        operator's choice survives the outage.
        """
        for key in self._history:
            if key in self._gauge_checks:
                continue
            # Pick colour from the instrument's own palette.
            if key.startswith("xgs600:"):
                idx = self._xgs_ch_count
                self._xgs_ch_count += 1
            elif key.startswith("vgc083:"):
                idx = self._vgc_ch_count
                self._vgc_ch_count += 1
            else:
                idx = len(self._gauge_checks)
            colour = _color_for_key(key, idx)
            display = GAUGE_DISPLAY_NAMES.get(key, key)

            row = QHBoxLayout()
            row.setSpacing(4)
            swatch = QLabel("━")
            swatch.setStyleSheet(
                f"color: {colour}; font-weight: bold; font-size: 13px;"
            )
            chk = QCheckBox(display)
            chk.setChecked(self._is_visible(key))
            chk.setToolTip(
                f"{key}\n\nUntick to remove this gauge from the plot and the "
                "readings table.  Polling and CSV logging are unaffected."
            )
            chk.toggled.connect(
                lambda checked, k=key: self._on_gauge_toggled(k, checked)
            )
            row.addWidget(swatch)
            row.addWidget(chk, stretch=1)

            # Insert before the trailing stretch.
            self._legend_lay.insertLayout(self._legend_lay.count() - 1, row)
            self._gauge_checks[key] = chk
            self._lbl_no_gauges.setVisible(False)

    def _on_gauge_toggled(self, key: str, checked: bool):
        if checked:
            self._hidden_gauges.discard(key)
        else:
            self._hidden_gauges.add(key)
            # Drop the stale line so it cannot linger on the canvas.
            line = self._plot_lines.pop(key, None)
            if line is not None:
                line.remove()
        _save_hidden_gauges(self._hidden_gauges)
        log.info("vacuum_tab: gauge %s %s", key,
                 "shown" if checked else "hidden")
        self._redraw_plot()

    def _on_show_all_gauges(self):
        self._hidden_gauges.clear()
        for chk in self._gauge_checks.values():
            chk.setChecked(True)
        _save_hidden_gauges(self._hidden_gauges)
        self._redraw_plot()

    # -----------------------------------------------------------------------
    # LivePlotPanel data callbacks
    # -----------------------------------------------------------------------

    def _visible_history(self) -> dict:
        """History restricted to gauges the operator has left switched on."""
        return {k: v for k, v in self._history.items() if self._is_visible(k)}

    def _live_edge(self):
        """Newest timestamp across visible gauges, or None.  Must stay cheap."""
        newest = None
        for pts in self._visible_history().values():
            if pts:
                t = pts[-1][0]
                if newest is None or t > newest:
                    newest = t
        return newest

    def _history_span(self):
        """(t_oldest, t_newest) across visible gauges, or None."""
        oldest = newest = None
        for pts in self._visible_history().values():
            if not pts:
                continue
            if oldest is None or pts[0][0] < oldest:
                oldest = pts[0][0]
            if newest is None or pts[-1][0] > newest:
                newest = pts[-1][0]
        if oldest is None or newest is None:
            return None
        return (oldest, newest)

    # -----------------------------------------------------------------------
    # Mode label
    # -----------------------------------------------------------------------

    @staticmethod
    def _fmt_window(w: float) -> str:
        if w >= 3600:
            return f"{w / 3600:.0f} h"
        if w >= 60:
            return f"{w / 60:.0f} min"
        return f"{w:.0f} s"

    def _set_live_label(self):
        self.lbl_mode.setText(
            f"● LIVE  (last {self._fmt_window(self.plot.window_seconds)})"
        )
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
        )

    def _on_navigation_changed(self):
        self.btn_jump_live.setVisible(not self.plot.is_live)
        if self.plot.is_live:
            self._set_live_label()
            return
        edge = self.plot.frozen_right_edge
        if edge is None:
            return
        ago = max(0.0, time.time() - edge)
        self.lbl_mode.setText(
            f"⏸  Frozen  —  {self._fmt_window(self.plot.window_seconds)} "
            f"ending {self._fmt_window(ago)} ago"
        )
        self.lbl_mode.setStyleSheet(
            "color: #8c6000; font-weight: bold; padding: 2px 6px;"
        )

    def _on_zoom_changed(self):
        if self.plot.is_live:
            self._set_live_label()
            return
        self.lbl_mode.setText(
            f"⏸  Frozen  —  window {self._fmt_window(self.plot.window_seconds)}"
        )

    # -----------------------------------------------------------------------
    # Table redraw (100 ms timer)
    # -----------------------------------------------------------------------

    def _redraw(self):
        state    = self._last_state
        age      = time.time() - self._last_time if self._last_time else float("inf")
        is_stale = age > STALE_THRESHOLD_S

        # Hidden gauges are skipped here as well as in the plot, so unticking
        # a phantom channel removes it from both views at once.  Polling and
        # CSV logging deliberately still cover it — hiding is a display
        # choice, and silently dropping a channel from the log would make the
        # record depend on GUI state.
        rows = []
        if state is not None:
            for r in state.xgs_readings:
                key  = f"xgs600:{r.channel.label}"
                if not self._is_visible(key):
                    continue
                name = GAUGE_DISPLAY_NAMES.get(key, r.channel.label)
                rows.append((name, "XGS-600", r.channel.label,
                             r.pressure, state.units_xgs, r.state))
            for r in state.vgc_readings:
                key  = f"vgc083:{r.channel}"
                if not self._is_visible(key):
                    continue
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

            for col, text in enumerate([name, inst, ch, press_text, units,
                                         state_str]):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == _COL_PRESS:
                    item.setFont(self._pressure_font)
                    item.setForeground(QColor(press_color))
                self._table.setItem(row_idx, col, item)

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
        """Redraw visible traces inside the current LIVE/FROZEN time window.

        Persistent Line2D objects are updated in place rather than the axes
        being cleared each tick.  cla() plus re-plot discards the axis limits,
        which fights the autoscale and makes a frozen window jump back to the
        live edge on every redraw.
        """
        self._sync_legend()

        window = self.plot.compute_window()
        if window is None:
            return
        t_left, t_right = window

        y_lo = y_hi = None
        any_data = False

        for key, pts in self._history.items():
            line = self._plot_lines.get(key)

            if not self._is_visible(key):
                continue

            # Log axis: a non-positive or None pressure has no position on it.
            # Dropping those points is what keeps a sentinel reading (the
            # VGC083's 1.10E+03 / OFF_OR_OVERRANGE, already parsed to None)
            # from being drawn as if it were a real pressure.
            xs = []
            ys = []
            for t, p in pts:
                if p is None or p <= 0 or not (t_left <= t <= t_right):
                    continue
                xs.append(t - t_right)
                ys.append(p)

            if line is None:
                # Re-derive the colour from the legend checkbox's swatch so
                # the plot line always matches.  Fall back to palette index 0.
                chk_keys = list(self._gauge_checks)
                if key in self._gauge_checks:
                    # Count how many keys with the same prefix appear before
                    # this one — that's the instrument-local index.
                    prefix = key.split(":")[0] + ":"
                    idx = sum(1 for k in chk_keys[:chk_keys.index(key)]
                              if k.startswith(prefix))
                    colour = _color_for_key(key, idx)
                else:
                    colour = _color_for_key(key, 0)
                line, = self._ax.plot([], [], lw=1.5, color=colour,
                                      label=GAUGE_DISPLAY_NAMES.get(key, key))
                self._plot_lines[key] = line

            if not xs:
                line.set_data([], [])
                continue

            # Decimate to ≤600 points so wide time windows stay responsive.
            if len(xs) > 600:
                step = len(xs) // 600
                xs   = xs[::step]
                ys   = ys[::step]

            line.set_data(xs, ys)
            any_data = True
            lo, hi = min(ys), max(ys)
            y_lo = lo if y_lo is None else min(y_lo, lo)
            y_hi = hi if y_hi is None else max(y_hi, hi)

        self._ax.set_xlim(-self.plot.window_seconds, 0)

        if any_data and self._chk_autoscale.isChecked():
            # Pad by a factor either side so traces never touch the frame.
            # A flat trace would otherwise give lo == hi and a zero-height
            # axis, which matplotlib renders as a blank plot on a log scale.
            if y_hi <= y_lo:
                y_lo, y_hi = y_lo / 3.0, y_hi * 3.0
            self._ax.set_ylim(y_lo / 2.0, y_hi * 2.0)

        self._refresh_plot_legend()
        self.plot.canvas.draw_idle()

    def _refresh_plot_legend(self):
        """Show only visible traces in the matplotlib legend."""
        handles = [ln for k, ln in self._plot_lines.items()
                   if self._is_visible(k) and len(ln.get_xdata())]
        if handles:
            self._ax.legend(handles=handles, loc="upper right", fontsize=8)
        elif self._ax.get_legend() is not None:
            self._ax.get_legend().remove()

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    def shutdown(self):
        self._redraw_timer.stop()
        self.plot.stop()
        if self._logger is not None:
            try:
                self._logger.close()
            except Exception:
                log.exception("vacuum_tab: error closing logger on shutdown")
            self._logger = None


# ---------------------------------------------------------------------------
# Per-instrument connection bar (inline; LabJackPanel API does not fit)
# ---------------------------------------------------------------------------

class _InstrumentBar(QGroupBox):
    """One gauge controller: port picker | status | connect/disconnect.

    The port is CHOSEN, not typed. COM numbers move when a USB adapter is
    replugged into a different socket, and a wrong number fails as a
    timeout - which reads like a dead controller rather than a wrong port.
    Detect asks each port what it is and selects the one that answers as
    this instrument; it deliberately stops there rather than connecting,
    so finding out what is plugged in is separate from starting to drive it.
    """

    from PySide6.QtCore import Signal
    connect_clicked    = Signal()
    disconnect_clicked = Signal()
    status_message     = Signal(str, str)

    def __init__(self, key: str, display_name: str, short_name: str = "",
                 parent=None):
        """key is the PROBE key ("xgs600"), display_name titles the box,
        short_name is what status messages call it."""
        super().__init__(display_name, parent)
        self._key       = key            # serial_transport candidate key
        self._connected = False

        outer = QVBoxLayout(self)
        lay = QHBoxLayout()
        outer.addLayout(lay)

        self._picker = PortPicker(key, display_name=short_name or display_name)
        self._picker.status.connect(self.status_message)
        lay.addWidget(self._picker)

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
        """Selected port, or "" when 'Auto-detect' is chosen."""
        return self._picker.current_port() or ""

    def set_port_text(self, text: str):
        """Select a port (used after a scan to show what was found)."""
        self._picker.set_port(text)

    def detect(self):
        """Probe for this instrument and select it. Does not connect."""
        self._picker.detect()

    def refresh_ports(self):
        self._picker.refresh()

    def set_connected(self, connected: bool, detail: str = ""):
        self._connected = connected
        self._btn.setText("Disconnect" if connected else "Connect")
        self._picker.set_busy(connected)
        text = f"● Connected  {detail}".strip() if connected else "● Disconnected"
        self._pill.set_connected(connected, text)
