"""
logamp_tab.py
PySide6 widget for the "Beam Current" outer tab.

Reads 4 analog inputs from a LabJack T7 at ~10 Hz, converts each log-amp
voltage to current, displays numerically + on a live rolling plot, and shows
a beam-centering indicator.

Plot navigation (from TDS-T8 live_plot mechanism):
  - Fixed 2-minute viewport window (WINDOW_SECONDS = 120).
  - Slider at max → LIVE mode: window tracks "now".
  - Drag slider left → FROZEN mode: window locked to historical position.
  - Buffer holds ~1 hour of history (BUFFER_CAPACITY = 36 000 @ 10 Hz).
"""
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox, QSizePolicy,
    QSlider,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from rbl.hardware.labjack_driver import LJM_AVAILABLE
from rbl.hardware.current_monitor import (
    voltage_to_current, format_current, RollingBuffer,
)
from rbl.config import hardware_config as SC
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
from rbl.gui import theme


# ─── The tab widget ───────────────────────────────────────────────────────────

class CurrentTab(QWidget):
    """The 'Beam Current' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # default 2-minute viewport
    _TIME_STEPS     = [3600, 1800, 900, 600, 300, 120, 60, 30, 15, 5, 1]

    def __init__(self, parent=None):
        super().__init__(parent)
        # This tab does not own a LabJack handle or stream worker. MainWindow
        # owns the single shared instance and feeds readings via _on_window().
        self._t0 = time.monotonic()   # reset on labjack_connected
        self.buffers = {name: RollingBuffer(self.BUFFER_CAPACITY)
                        for name in SC.LABJACK_CHANNEL_MAP.keys()}

        # Plot state
        self._is_live           = True
        self._frozen_right_edge = None   # float: elapsed-seconds anchor
        self._window_seconds    = float(self.WINDOW_SECONDS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection (shared panel; MainWindow does the actual connecting) ──
        self.lj_panel = LabJackPanel()

        mono = QFont("Consolas", 15)
        mono.setBold(True)

        # ── Per-channel numeric readouts ──────────────────────────────────
        ro_box = QGroupBox("Live Readings")
        ro = QGridLayout(ro_box)
        ro.setSpacing(2)
        ro.setContentsMargins(4, 2, 4, 2)
        self.lbl_v = {}
        self.lbl_i = {}
        for col, (ain, jaw) in enumerate(SC.LABJACK_CHANNEL_MAP.items()):
            hdr = QLabel(f"{jaw}  ({ain})")
            hdr.setStyleSheet("font-size: 15px; color: #444;")
            ro.addWidget(hdr, 0, col)
            self.lbl_v[ain] = QLabel("—")
            self.lbl_v[ain].setStyleSheet(
                "color: #555; font-family: Consolas, 'Courier New', monospace; font-size: 15px;"
            )
            ro.addWidget(self.lbl_v[ain], 1, col)
            self.lbl_i[ain] = QLabel("—")
            self.lbl_i[ain].setFont(mono)
            self.lbl_i[ain].setStyleSheet(theme.status_label(theme.OK))
            ro.addWidget(self.lbl_i[ain], 2, col)

        # Beam indicator — placed in the plot section below, left of the canvas
        self.beam_indicator = BeamPositionIndicator()

        # ── Assemble upper section: connection panel | readouts ────────────
        upper_row = QHBoxLayout()
        upper_row.setSpacing(8)
        upper_row.addWidget(self.lj_panel)
        upper_row.addWidget(ro_box, stretch=1)
        layout.addLayout(upper_row)

        # ── Live plot ─────────────────────────────────────────────────────
        plot_box = QGroupBox("Live Currents")
        pv = QVBoxLayout(plot_box)

        # Plot mode indicator + time-window controls + jump-to-live button
        nav_row = QHBoxLayout()
        self.lbl_mode = QLabel("● LIVE  (last 120 s)")
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
        )
        nav_row.addWidget(self.lbl_mode)
        lbl_time = QLabel("  Time:")
        lbl_time.setStyleSheet("color: #555; font-size: 15px;")
        nav_row.addWidget(lbl_time)
        btn_time_out = QPushButton("－")
        btn_time_out.setFixedWidth(28)
        btn_time_out.setToolTip("Increase time window (zoom out)")
        btn_time_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_out.clicked.connect(self._zoom_time_out)
        btn_time_in = QPushButton("＋")
        btn_time_in.setFixedWidth(28)
        btn_time_in.setToolTip("Decrease time window (zoom in)")
        btn_time_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_in.clicked.connect(self._zoom_time_in)
        nav_row.addWidget(btn_time_out)
        nav_row.addWidget(btn_time_in)
        nav_row.addStretch()
        self.btn_jump_live = QPushButton("Jump to Live")
        self.btn_jump_live.setVisible(False)
        self.btn_jump_live.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " padding:2px 8px; }"
            "QPushButton:hover { background:#0063b1; }"
        )
        self.btn_jump_live.clicked.connect(self._jump_to_live)
        nav_row.addWidget(self.btn_jump_live)
        pv.addLayout(nav_row)

        self.fig    = Figure(figsize=(7, 3))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        # Scale (ticks + label) on the RIGHT: the live edge and newest values
        # arrive from the right, so the axis reads next to where the trace ends.
        self.ax.set_ylabel("Current (A)")
        self.ax.yaxis.set_label_position("right")
        self.ax.yaxis.tick_right()
        self.ax.set_yscale("log")
        self.ax.grid(True, which="both", alpha=0.3)
        self._lines = {}
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            line, = self.ax.plot([], [], color=theme.JAW_COLORS.get(jaw, "k"), lw=1.5)
            self._lines[ain] = line
        self.fig.tight_layout()

        # Qt legend panel (right of canvas — avoids matplotlib layout fighting)
        _legend_w = QWidget()
        _legend_w.setFixedWidth(115)
        _leg_lay = QVBoxLayout(_legend_w)
        _leg_lay.setSpacing(3)
        _leg_lay.setContentsMargins(4, 8, 4, 4)
        _leg_title = QLabel("Legend")
        _leg_title.setStyleSheet("font-size: 15px; color: #555; font-weight: bold;")
        _leg_lay.addWidget(_leg_title)
        for _ain, _jaw in SC.LABJACK_CHANNEL_MAP.items():
            _row = QHBoxLayout()
            _swatch = QLabel("━")
            _swatch.setStyleSheet(
                f"color: {theme.JAW_COLORS.get(_jaw, '#000')}; font-weight: bold; font-size: 13px;"
            )
            _lbl = QLabel(f"{_jaw}  ({_ain})")
            _lbl.setStyleSheet("font-size: 15px;")
            _row.addWidget(_swatch)
            _row.addWidget(_lbl)
            _row.addStretch()
            _leg_lay.addLayout(_row)
        _leg_lay.addStretch()

        # Horizontal content row: beam position | canvas | legend
        _content_row = QHBoxLayout()
        _content_row.setSpacing(4)
        _content_row.addWidget(self.beam_indicator)
        _content_row.addWidget(self.canvas, stretch=1)
        _content_row.addWidget(_legend_w)
        pv.addLayout(_content_row, stretch=1)

        # History slider: 0 = oldest, 10000 = live (rightmost = newest)
        slider_row = QHBoxLayout()
        lbl_hist = QLabel("◀ History")
        lbl_hist.setStyleSheet("color: #555; font-size: 10px;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10_000)
        self.slider.setValue(10_000)   # start in live mode
        self.slider.setTickInterval(1_000)
        self.slider.setToolTip(
            "Drag left to browse history. "
            "Drag to far right to return to LIVE mode."
        )
        self.slider.valueChanged.connect(self._on_slider_changed)
        lbl_live = QLabel("Live ▶")
        lbl_live.setStyleSheet("color: #555; font-size: 10px;")
        slider_row.addWidget(lbl_hist)
        slider_row.addWidget(self.slider, stretch=1)
        slider_row.addWidget(lbl_live)
        pv.addLayout(slider_row)

        layout.addWidget(plot_box, stretch=1)

        # Redraw at 5 Hz max
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setInterval(200)
        self._redraw_timer.timeout.connect(self._redraw_plot)

        if not LJM_AVAILABLE:
            self.lj_panel.set_enabled(False)

    # ---- Connection lifecycle (driven by MainWindow) --------------------------

    def on_labjack_connected(self, serial: str):
        """MainWindow calls this after the shared T7 opens."""
        self._t0 = time.monotonic()
        self.lj_panel.set_connected(True, serial)
        self._redraw_timer.start()

    def on_labjack_disconnected(self):
        """MainWindow calls this after the shared T7 closes."""
        self._redraw_timer.stop()
        self.lj_panel.set_connected(False)

    # ---- Slots ---------------------------------------------------------------

    @staticmethod
    def _by_jaw(currents_by_ain: dict) -> dict:
        """Re-key AIN -> current as jaw label -> current for the indicator."""
        return {jaw: currents_by_ain.get(ain, float("nan"))
                for ain, jaw in SC.LABJACK_CHANNEL_MAP.items()}

    def set_jaw_state(self, state: dict):
        """Jaw geometry pushed over from the motor tab (see MotorTab.jaw_state)."""
        self.beam_indicator.set_jaw_state(state)

    def _on_window(self, payload: dict):
        """Consume one stream window from LabJackStreamWorker.

        Log-amp channels carry only a mean voltage (full waveform not stored).
        When the WAVEFORM profile is active, log-amp payload entries are None;
        display a "paused" state rather than stale numbers.
        """
        channels = payload["channels"]
        t        = payload["t"]

        v1nA  = SC.LOG_AMP_V_AT_1NA
        v1mA  = SC.LOG_AMP_V_AT_1MA

        currents = {}
        for ain in SC.LABJACK_CHANNEL_MAP.keys():
            ch_data = channels.get(ain)

            if ch_data is None:
                # WAVEFORM profile: log amps not sampled this window.
                self.lbl_v[ain].setText("—  (Waveform mode)")
                self.lbl_v[ain].setStyleSheet("color: #aaa; font-family: Consolas, 'Courier New', monospace;")
                self.lbl_i[ain].setText("—  (Waveform mode)")
                self.lbl_i[ain].setStyleSheet("color: #aaa; font-weight: bold;")
                currents[ain] = float("nan")
                continue

            V = ch_data["mean"]
            I = voltage_to_current(V, v1nA, v1mA)
            currents[ain] = I
            self.lbl_v[ain].setText(f"{V:6.3f} V")
            self.lbl_v[ain].setStyleSheet("color: #555; font-family: Consolas, 'Courier New', monospace;")
            self.lbl_i[ain].setText(format_current(I))
            self.lbl_i[ain].setStyleSheet(theme.status_label(theme.OK))
            self.buffers[ain].append(t, I)

        self.beam_indicator.set_currents(self._by_jaw(currents))

        if self._is_live:
            self.slider.blockSignals(True)
            self.slider.setValue(10_000)
            self.slider.blockSignals(False)

    def _on_reading(self, t: float, values: dict):
        v1nA   = SC.LOG_AMP_V_AT_1NA
        v1mA   = SC.LOG_AMP_V_AT_1MA

        currents = {}
        # The shared worker emits all 12 AINs. Take ONLY the log-amp channels;
        # AIN6-AIN13 belong to the HV amplifier tab and must be ignored here.
        for ain in SC.LABJACK_CHANNEL_MAP.keys():
            V = values.get(ain)
            if V is None:
                continue
            I = voltage_to_current(V, v1nA, v1mA)
            currents[ain] = I
            self.lbl_v[ain].setText(f"{V:6.3f} V")
            self.lbl_i[ain].setText(format_current(I))
            self.buffers[ain].append(t, I)

        self.beam_indicator.set_currents(self._by_jaw(currents))

        # Auto-advance slider to live edge when in live mode
        if self._is_live:
            self.slider.blockSignals(True)
            self.slider.setValue(10_000)
            self.slider.blockSignals(False)

    def _on_error(self, msg: str):
        # MainWindow owns teardown; we only surface the message.
        QMessageBox.warning(self, "LabJack poll error", msg)

    # ---- Slider / navigation -------------------------------------------------

    def _on_slider_changed(self, val: int):
        if val >= 9_800:
            self._enter_live_mode()
        else:
            self._enter_frozen_mode(val)

    def _enter_live_mode(self):
        self._is_live = True
        self._frozen_right_edge = None
        w = self._window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        self.lbl_mode.setText(f"● LIVE  (last {label})")
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
        )
        self.btn_jump_live.setVisible(False)

    def _enter_frozen_mode(self, slider_val: int):
        # Compute right-edge from slider position across full history span
        t_arr, _ = self.buffers[next(iter(self.buffers))].snapshot()
        if len(t_arr) < 2:
            return
        t_oldest = float(t_arr[0])
        t_newest = float(t_arr[-1])
        span = t_newest - t_oldest
        if span <= 0:
            return

        frac = slider_val / 10_000.0
        self._frozen_right_edge = t_oldest + frac * span
        self._is_live = False

        import datetime
        # Show elapsed-time window in the label
        w_start = max(t_oldest, self._frozen_right_edge - self._window_seconds)
        self.lbl_mode.setText(
            f"⏸  Frozen  —  t = [{w_start:+.0f} s … {self._frozen_right_edge:+.0f} s]"
        )
        self.lbl_mode.setStyleSheet(
            "color: #8c6000; font-weight: bold; padding: 2px 6px;"
        )
        self.btn_jump_live.setVisible(True)

    def _jump_to_live(self):
        self.slider.setValue(10_000)
        self._enter_live_mode()

    def _zoom_time_in(self):
        """Decrease the time window (zoom in on time axis)."""
        smaller = [s for s in self._TIME_STEPS if s < self._window_seconds]
        if smaller:
            self._window_seconds = float(max(smaller))
        else:
            self._window_seconds = max(1.0, self._window_seconds / 2)
        self._update_time_label()

    def _zoom_time_out(self):
        """Increase the time window (zoom out on time axis)."""
        larger = [s for s in self._TIME_STEPS if s > self._window_seconds]
        if larger:
            self._window_seconds = float(min(larger))
        else:
            self._window_seconds = min(3600.0, self._window_seconds * 2)
        self._update_time_label()

    def _update_time_label(self):
        w = self._window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        if self._is_live:
            self.lbl_mode.setText(f"● LIVE  (last {label})")
            self.lbl_mode.setStyleSheet(
                theme.status_label(theme.OK) + " padding: 2px 6px;"
            )
        else:
            self.lbl_mode.setText(
                f"⏸  Frozen  —  window {label}"
            )

    # ---- Plot redraw ---------------------------------------------------------

    def _redraw_plot(self):
        any_data = False

        # Determine window [t_left, t_right]
        ref_t  = None
        t_right = None

        if self._is_live:
            # Use the latest timestamp available across all channels
            for buf in self.buffers.values():
                _, val = buf.latest()
                t, _ = buf.latest()
                if not (t != t):   # not NaN
                    if ref_t is None or t > ref_t:
                        ref_t = t
            if ref_t is None:
                return
            t_right = ref_t
        else:
            t_right = self._frozen_right_edge
            if t_right is None:
                return

        t_left = t_right - self._window_seconds

        for ain, line in self._lines.items():
            t, v = self.buffers[ain].snapshot()
            if len(t) < 2:
                continue
            mask = (t >= t_left) & (t <= t_right)
            if mask.sum() < 2:
                line.set_data([], [])
                continue
            # Decimate to ≤600 points for performance
            t_win = t[mask]
            v_win = v[mask]
            if len(t_win) > 600:
                step   = len(t_win) // 600
                t_win  = t_win[::step]
                v_win  = v_win[::step]
            # Plot relative to right edge so x-axis is always [-120, 0]
            line.set_data(t_win - t_right, v_win)
            any_data = True

        if any_data:
            self.ax.set_xlim(-self._window_seconds, 0)
            self.ax.relim()
            self.ax.autoscale_view(scalex=False, scaley=True)
            self.canvas.draw_idle()

    # ---- Owner-callable cleanup ----------------------------------------------

    def shutdown(self):
        """MainWindow closes the LabJack itself; we just stop redrawing."""
        self._redraw_timer.stop()


# Standalone smoke test
if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = CurrentTab()
    w.resize(900, 700)
    w.show()
    print("[OK] current_tab loads")
    sys.exit(app.exec())
