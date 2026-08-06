"""
profiler_tab.py
PySide6 widget for the "Beam Profiler" outer tab.

DELIBERATE SCOPE RESTRICTION
-----------------------------
This tab is READ-ONLY for scope settings (channel, baud, port).  It has no
waveform math controls, no triggering controls, and no calibration controls
beyond what is documented below.  This is an explicit design decision.

The tab subscribes to Beamline.scope_changed (ScopeState snapshots) and
Beamline.scope_error (string messages).  It never holds a driver reference
or opens a serial port.

LAYOUT
------
  [Connection bar]                      -- port entry, connect/disconnect
  [FWHM readout panel]                  -- live FWHM in samples and seconds
  [Waveform plot]                       -- rolling voltage trace (log or linear)
  [FWHM history plot]                   -- rolling FWHM-vs-time strip chart
"""
import logging
import math
import time
from datetime import datetime, timezone

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QLineEdit, QSizePolicy,
)

from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill

log = logging.getLogger(__name__)

# Maximum FWHM history points retained (1 Hz → 1 h)
_MAX_HISTORY = 3600


class ProfilerTab(QWidget):
    """Beam profile display tab.

    Subscribes to beamline.scope_changed for live ScopeState snapshots.
    """

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline    = beamline
        self._last_state = None
        self._last_time  = 0.0

        # FWHM rolling history: [(unix_time, fwhm_seconds|nan)]
        self._fwhm_history: list[tuple[float, float]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection bar ────────────────────────────────────────────────────
        conn_box = QGroupBox("TDS 2012 Oscilloscope")
        conn_lay = QHBoxLayout(conn_box)

        conn_lay.addWidget(QLabel("Port:"))
        self._le_port = QLineEdit()
        self._le_port.setPlaceholderText("e.g. COM5  (leave blank for auto-discover)")
        self._le_port.setMaximumWidth(220)
        conn_lay.addWidget(self._le_port)

        self._btn_connect = QPushButton("Connect")
        self._btn_connect.clicked.connect(self._on_connect_toggle)
        conn_lay.addWidget(self._btn_connect)

        self._pill = StatusPill()
        conn_lay.addWidget(self._pill)
        conn_lay.addStretch()
        layout.addWidget(conn_box)

        # ── FWHM readout panel ────────────────────────────────────────────────
        fwhm_box = QGroupBox("FWHM Readout")
        fwhm_lay = QHBoxLayout(fwhm_box)

        fwhm_lay.addWidget(QLabel("Channel:"))
        self._lbl_channel = QLabel("—")
        self._lbl_channel.setStyleSheet(f"color: {theme.NEUTRAL};")
        fwhm_lay.addWidget(self._lbl_channel)

        fwhm_lay.addSpacing(20)
        fwhm_lay.addWidget(QLabel("FWHM:"))
        self._lbl_fwhm_s = QLabel("—")
        self._lbl_fwhm_s.setStyleSheet(
            f"font-size: {theme.FS_VALUE}px; font-weight: bold;"
        )
        fwhm_lay.addWidget(self._lbl_fwhm_s)
        self._lbl_fwhm_unit = QLabel("s")
        self._lbl_fwhm_unit.setStyleSheet(f"color: {theme.NEUTRAL};")
        fwhm_lay.addWidget(self._lbl_fwhm_unit)

        fwhm_lay.addSpacing(20)
        fwhm_lay.addWidget(QLabel("(samples:"))
        self._lbl_fwhm_samp = QLabel("—")
        self._lbl_fwhm_samp.setStyleSheet(f"color: {theme.NEUTRAL};")
        fwhm_lay.addWidget(self._lbl_fwhm_samp)
        fwhm_lay.addWidget(QLabel(")"))

        fwhm_lay.addSpacing(20)
        self._lbl_status = QLabel("(not connected)")
        self._lbl_status.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        fwhm_lay.addWidget(self._lbl_status)
        fwhm_lay.addStretch()
        layout.addWidget(fwhm_box)

        # ── Waveform plot ─────────────────────────────────────────────────────
        wave_box = QGroupBox("Waveform (last acquisition)")
        wave_lay = QVBoxLayout(wave_box)

        self._fig_wave = Figure(figsize=(8, 2.5))
        self._canvas_wave = FigureCanvasQTAgg(self._fig_wave)
        self._canvas_wave.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._ax_wave = self._fig_wave.add_subplot(111)
        self._ax_wave.set_ylabel("Voltage (V)")
        self._ax_wave.set_xlabel("Sample index")
        self._ax_wave.grid(True, alpha=0.3)
        self._fig_wave.tight_layout()
        wave_lay.addWidget(self._canvas_wave)
        layout.addWidget(wave_box, stretch=2)

        # ── FWHM history strip chart ──────────────────────────────────────────
        hist_box = QGroupBox("FWHM History")
        hist_lay = QVBoxLayout(hist_box)

        self._fig_hist = Figure(figsize=(8, 2))
        self._canvas_hist = FigureCanvasQTAgg(self._fig_hist)
        self._canvas_hist.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._ax_hist = self._fig_hist.add_subplot(111)
        self._ax_hist.set_ylabel("FWHM (s)")
        self._ax_hist.set_xlabel("Time (s ago)")
        self._ax_hist.grid(True, alpha=0.3)
        self._fig_hist.tight_layout()
        hist_lay.addWidget(self._canvas_hist)
        layout.addWidget(hist_box, stretch=1)

        # ── Calibration hook ──────────────────────────────────────────────────
        cal_box = QGroupBox("FWHM Reference (Calibration)")
        cal_lay = QHBoxLayout(cal_box)

        self._btn_save_ref = QPushButton("Save FWHM Reference")
        self._btn_save_ref.setEnabled(False)
        self._btn_save_ref.setToolTip(
            "Save the current FWHM reading as the reference value for this session."
        )
        self._btn_save_ref.clicked.connect(self._on_save_fwhm_ref)
        cal_lay.addWidget(self._btn_save_ref)

        self._lbl_ref = QLabel(self._load_fwhm_ref_label())
        self._lbl_ref.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;"
        )
        cal_lay.addWidget(self._lbl_ref, stretch=1)
        layout.addWidget(cal_box)

        # ── Subscribe to Beamline signals ─────────────────────────────────────
        self.beamline.scope_changed.connect(self._on_scope_state)
        self.beamline.scope_error.connect(self._on_scope_error)

        # ── Redraw timers ─────────────────────────────────────────────────────
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(200)     # 5 Hz readout update
        self._redraw_timer.timeout.connect(self._redraw_readout)
        self._redraw_timer.start()

        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(1000)      # 1 Hz plot refresh
        self._plot_timer.timeout.connect(self._redraw_plots)
        self._plot_timer.start()

        self._connected = False

    # -----------------------------------------------------------------------
    # Connection bar
    # -----------------------------------------------------------------------

    def _on_connect_toggle(self):
        if self._connected:
            self.beamline.disconnect_scope()
            self._connected = False
            self._btn_connect.setText("Connect")
            self._pill.set_connected(False)
            self._lbl_status.setText("(disconnected)")
        else:
            port = self._le_port.text().strip() or None
            self.beamline.connect_scope(port)
            self._connected = True
            self._btn_connect.setText("Disconnect")
            self._pill.set_connected(True, "● Connecting…")
            self._lbl_status.setText("Connecting…")

    # -----------------------------------------------------------------------
    # Beamline signal handlers
    # -----------------------------------------------------------------------

    def _on_scope_state(self, state):
        """Receive ScopeState; cache for next redraw tick."""
        self._last_state = state
        self._last_time  = time.time()

        if state.connected and not math.isnan(state.fwhm_seconds):
            self._btn_save_ref.setEnabled(True)

        now = time.time()
        fwhm = state.fwhm_seconds
        self._fwhm_history.append((now, fwhm))
        if len(self._fwhm_history) > _MAX_HISTORY:
            self._fwhm_history = self._fwhm_history[-_MAX_HISTORY:]

    def _on_scope_error(self, msg: str):
        log.warning("profiler_tab: scope error: %s", msg)
        self._lbl_status.setText(f"Error: {msg[:60]}")
        self._lbl_status.setStyleSheet(f"color: {theme.FAULT}; font-style: italic;")

    # -----------------------------------------------------------------------
    # Readout redraw (200 ms)
    # -----------------------------------------------------------------------

    def _redraw_readout(self):
        state = self._last_state
        if state is None:
            return

        # Connection pill
        if state.connected:
            self._pill.set_connected(True, "● Connected")
            self._lbl_status.setStyleSheet(f"color: {theme.NEUTRAL}; font-style: italic;")
        else:
            self._pill.set_connected(False)

        self._lbl_channel.setText(state.channel)

        # FWHM
        if not math.isnan(state.fwhm_seconds):
            # Express in best-fit units (ns / µs / ms / s)
            fwhm_s = state.fwhm_seconds
            if fwhm_s < 1e-6:
                txt  = f"{fwhm_s * 1e9:.3g}"
                unit = "ns"
            elif fwhm_s < 1e-3:
                txt  = f"{fwhm_s * 1e6:.3g}"
                unit = "µs"
            elif fwhm_s < 1.0:
                txt  = f"{fwhm_s * 1e3:.3g}"
                unit = "ms"
            else:
                txt  = f"{fwhm_s:.4g}"
                unit = "s"
            self._lbl_fwhm_s.setText(txt)
            self._lbl_fwhm_s.setStyleSheet(
                f"font-size: {theme.FS_VALUE}px; font-weight: bold; color: {theme.OK};"
            )
            self._lbl_fwhm_unit.setText(unit)
        else:
            self._lbl_fwhm_s.setText("—")
            self._lbl_fwhm_s.setStyleSheet(
                f"font-size: {theme.FS_VALUE}px; font-weight: bold; color: {theme.NEUTRAL};"
            )
            self._lbl_fwhm_unit.setText("s")

        if not math.isnan(state.fwhm_samples):
            self._lbl_fwhm_samp.setText(f"{state.fwhm_samples:.1f}")
        else:
            self._lbl_fwhm_samp.setText("—")

        if state.error:
            self._lbl_status.setText(state.error[:70])
            self._lbl_status.setStyleSheet(
                f"color: {theme.WARN}; font-style: italic;"
            )
        elif state.connected:
            self._lbl_status.setText("OK")
            self._lbl_status.setStyleSheet(f"color: {theme.OK}; font-style: italic;")

    # -----------------------------------------------------------------------
    # Plot redraw (1 s)
    # -----------------------------------------------------------------------

    def _redraw_plots(self):
        self._redraw_waveform()
        self._redraw_fwhm_history()

    def _redraw_waveform(self):
        state = self._last_state
        if state is None or not state.volts_downsampled:
            return

        self._ax_wave.cla()
        self._ax_wave.set_ylabel("Voltage (V)")
        self._ax_wave.set_xlabel("Sample index")
        self._ax_wave.grid(True, alpha=0.3)

        xs = list(range(len(state.volts_downsampled)))
        self._ax_wave.plot(xs, state.volts_downsampled, linewidth=0.8,
                           color="#2980b9")

        # Mark the half-maximum lines if FWHM is valid
        if not math.isnan(state.fwhm_samples) and state.volts_downsampled:
            peak = max(state.volts_downsampled)
            half = peak / 2.0
            self._ax_wave.axhline(half, color=theme.WARN, linewidth=0.8,
                                  linestyle="--", label="half-max")

        self._canvas_wave.draw_idle()

    def _redraw_fwhm_history(self):
        if not self._fwhm_history:
            return

        now    = time.time()
        valid  = [(t, f) for t, f in self._fwhm_history
                  if not math.isnan(f) and f > 0]
        if not valid:
            return

        xs = [now - t for t, _ in valid]
        ys = [f for _, f in valid]

        self._ax_hist.cla()
        self._ax_hist.set_ylabel("FWHM (s)")
        self._ax_hist.set_xlabel("Time (s ago)")
        self._ax_hist.grid(True, alpha=0.3)
        self._ax_hist.plot(xs, ys, color=theme.OK, linewidth=0.8)
        self._ax_hist.invert_xaxis()
        self._canvas_hist.draw_idle()

    # -----------------------------------------------------------------------
    # Calibration hook
    # -----------------------------------------------------------------------

    def _on_save_fwhm_ref(self):
        state = self._last_state
        if state is None or math.isnan(state.fwhm_seconds):
            return
        iso = datetime.now(timezone.utc).isoformat()
        entry = {
            "fwhm_seconds":  state.fwhm_seconds,
            "fwhm_samples":  state.fwhm_samples,
            "channel":       state.channel,
            "xincr":         state.xincr,
            "saved_iso":     iso,
        }
        try:
            from rbl.config.persistence import load_config, save_config
            cfg = load_config()
            cfg["fwhm_calibration"] = entry
            save_config(cfg)
            log.info("profiler_tab: saved FWHM reference: %s", entry)
        except Exception as exc:
            log.exception("profiler_tab: failed to save FWHM reference: %s", exc)
        self._lbl_ref.setText(self._fmt_ref(entry))

    @staticmethod
    def _fmt_ref(entry: dict) -> str:
        fwhm_s = entry.get("fwhm_seconds", math.nan)
        iso    = entry.get("saved_iso", "")
        if math.isnan(fwhm_s):
            return "(no reference saved)"
        return f"Reference: {fwhm_s:.4g} s  (saved {iso[:19].replace('T', ' ')} UTC)"

    @staticmethod
    def _load_fwhm_ref_label() -> str:
        try:
            from rbl.config.persistence import load_config
            cfg   = load_config()
            entry = cfg.get("fwhm_calibration")
            if entry:
                return ProfilerTab._fmt_ref(entry)
        except Exception:
            pass
        return "(no reference saved)"

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    def shutdown(self):
        self._redraw_timer.stop()
        self._plot_timer.stop()
