"""
current_tab.py
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

from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QComboBox, QMessageBox, QSizePolicy,
    QSlider,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from rbl.hardware.labjack_driver import LabJackT7, LJM_AVAILABLE
from rbl.hardware.current_monitor import (
    voltage_to_current, format_current, beam_centering, RollingBuffer,
)
from rbl.hardware import slit_config as SC


# ─── Background poll thread ───────────────────────────────────────────────────

class LabJackPollWorker(QThread):
    reading = Signal(float, dict)   # t_seconds, {AIN0: V, AIN1: V, ...}
    error   = Signal(str)

    def __init__(self, lj: LabJackT7, period_s: float = 0.1):
        super().__init__()
        self.lj       = lj
        self.period   = period_s
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        t0 = time.time()
        while self._running and self.lj.connected:
            tloop = time.time()
            try:
                values = self.lj.read_channels()
                self.reading.emit(tloop - t0, values)
            except Exception as e:
                self.error.emit(str(e))
                break
            elapsed   = time.time() - tloop
            remaining = max(0.0, self.period - elapsed)
            self.msleep(int(remaining * 1000))


# ─── The tab widget ───────────────────────────────────────────────────────────

class CurrentTab(QWidget):
    """The 'Beam Current' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # fixed 2-minute viewport

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lj      = LabJackT7()
        self.worker  = None
        self.buffers = {name: RollingBuffer(self.BUFFER_CAPACITY)
                        for name in SC.LABJACK_CHANNEL_MAP.keys()}

        # Plot state
        self._is_live           = True
        self._frozen_right_edge = None   # float: elapsed-seconds anchor

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection ────────────────────────────────────────────────────
        conn_box = QGroupBox("LabJack T7 Connection")
        conn = QHBoxLayout(conn_box)
        conn.addWidget(QLabel("Connection:"))
        self.cbo_conn = QComboBox()
        self.cbo_conn.addItems(["USB", "ETHERNET", "ANY"])
        conn.addWidget(self.cbo_conn)
        conn.addWidget(QLabel("Identifier:"))
        self.le_ident = QLineEdit("ANY")
        self.le_ident.setMaximumWidth(140)
        conn.addWidget(self.le_ident)
        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self._toggle_connection)
        conn.addWidget(self.btn_conn)
        self.lbl_conn_status = QLabel("● Disconnected")
        self.lbl_conn_status.setStyleSheet("color: #666666; font-weight: bold;")
        conn.addWidget(self.lbl_conn_status)
        self.lbl_serial = QLabel("")
        self.lbl_serial.setStyleSheet("color: #555; font-style: italic;")
        conn.addWidget(self.lbl_serial, stretch=1)
        layout.addWidget(conn_box)

        # ── Per-channel numeric readouts ──────────────────────────────────
        ro_box = QGroupBox("Live Readings")
        ro = QGridLayout(ro_box)
        ro.setSpacing(6)
        self.lbl_v = {}
        self.lbl_i = {}
        mono = QFont("Menlo", 13)
        mono.setBold(True)
        for col, (ain, jaw) in enumerate(SC.LABJACK_CHANNEL_MAP.items()):
            ro.addWidget(QLabel(f"{jaw}  ({ain})"), 0, col)
            self.lbl_v[ain] = QLabel("—")
            self.lbl_v[ain].setStyleSheet("color: #555; font-family: Menlo;")
            ro.addWidget(self.lbl_v[ain], 1, col)
            self.lbl_i[ain] = QLabel("—")
            self.lbl_i[ain].setFont(mono)
            self.lbl_i[ain].setStyleSheet("color: #1a7a1a; font-weight: bold;")
            ro.addWidget(self.lbl_i[ain], 2, col)
        layout.addWidget(ro_box)

        # ── Beam-centering indicator ──────────────────────────────────────
        center_box = QGroupBox("Beam Centering Indicator")
        center = QHBoxLayout(center_box)
        self.lbl_xc = QLabel("X imbalance: —")
        self.lbl_yc = QLabel("Y imbalance: —")
        self.lbl_xc.setFont(mono)
        self.lbl_yc.setFont(mono)
        center.addWidget(self.lbl_xc)
        center.addStretch()
        center.addWidget(self.lbl_yc)
        layout.addWidget(center_box)

        # ── Live plot ─────────────────────────────────────────────────────
        plot_box = QGroupBox("Live Currents (2-min window)")
        pv = QVBoxLayout(plot_box)

        # Plot mode indicator + jump-to-live button
        nav_row = QHBoxLayout()
        self.lbl_mode = QLabel("● LIVE  (last 2 min)")
        self.lbl_mode.setStyleSheet(
            "color: #1a7a1a; font-weight: bold; padding: 2px 6px;"
        )
        self.btn_jump_live = QPushButton("Jump to Live")
        self.btn_jump_live.setVisible(False)
        self.btn_jump_live.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " padding:2px 8px; }"
            "QPushButton:hover { background:#0063b1; }"
        )
        self.btn_jump_live.clicked.connect(self._jump_to_live)
        nav_row.addWidget(self.lbl_mode)
        nav_row.addStretch()
        nav_row.addWidget(self.btn_jump_live)
        pv.addLayout(nav_row)

        self.fig    = Figure(figsize=(7, 3))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Current (A)")
        self.ax.set_yscale("log")
        self.ax.grid(True, which="both", alpha=0.3)
        self._lines = {}
        colors = {"X+": "#e74c3c", "X-": "#3498db", "Y+": "#c47a00", "Y-": "#1a7a1a"}
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            line, = self.ax.plot([], [], label=f"{jaw} ({ain})",
                                 color=colors.get(jaw, "k"), lw=1.5)
            self._lines[ain] = line
        self.ax.legend(loc="upper left", fontsize=8)
        self.fig.tight_layout()
        pv.addWidget(self.canvas, stretch=1)

        # History slider: 0 = oldest, 10000 = live (rightmost = newest)
        slider_row = QHBoxLayout()
        lbl_hist = QLabel("◀ History")
        lbl_hist.setStyleSheet("color: #555; font-size: 10px;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10_000)
        self.slider.setValue(10_000)   # start in live mode
        self.slider.setTickInterval(1_000)
        self.slider.setToolTip(
            "Drag left to browse history (2-min window). "
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
            self.btn_conn.setEnabled(False)
            QMessageBox.information(
                self, "LabJack library missing",
                "labjack-ljm is not installed. Run:\n\n"
                "    pip install labjack-ljm\n\n"
                "Plus install the LJM system library from labjack.com.\n"
                "The Beam Current tab will be disabled until then."
            )

    # ---- Connection lifecycle ------------------------------------------------

    def _toggle_connection(self):
        if self.lj.connected:
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        try:
            self.lj.connect(self.cbo_conn.currentText(),
                            self.le_ident.text().strip() or "ANY")
            sn = self.lj.serial_number()
            self.lbl_serial.setText(f"T7 serial #{sn}")
            self.btn_conn.setText("Disconnect")
            self.lbl_conn_status.setText("● Connected")
            self.lbl_conn_status.setStyleSheet("color: #1a7a1a; font-weight: bold;")
            self.worker = LabJackPollWorker(self.lj, period_s=0.1)
            self.worker.reading.connect(self._on_reading)
            self.worker.error.connect(self._on_error)
            self.worker.start()
            self._redraw_timer.start()
        except Exception as e:
            QMessageBox.critical(self, "LabJack connect failed", str(e))

    def _do_disconnect(self):
        self._redraw_timer.stop()
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None
        self.lj.disconnect()
        self.btn_conn.setText("Connect")
        self.lbl_conn_status.setText("● Disconnected")
        self.lbl_conn_status.setStyleSheet("color: #666666; font-weight: bold;")
        self.lbl_serial.setText("")

    # ---- Slots ---------------------------------------------------------------

    def _on_reading(self, t: float, values: dict):
        spec   = SC.LOG_AMP_MODELS[SC.DEFAULT_LOG_AMP_MODEL]
        v1nA   = spec["v_at_1nA"]
        v1mA   = spec["v_at_1mA"]

        currents = {}
        for ain, V in values.items():
            I = voltage_to_current(V, v1nA, v1mA)
            currents[ain] = I
            self.lbl_v[ain].setText(f"{V:6.3f} V")
            self.lbl_i[ain].setText(format_current(I))
            self.buffers[ain].append(t, I)

        ain_xplus  = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "X+"), None)
        ain_xminus = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "X-"), None)
        ain_yplus  = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "Y+"), None)
        ain_yminus = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "Y-"), None)
        if ain_xplus and ain_xminus:
            xc = beam_centering(currents[ain_xplus], currents[ain_xminus])
            self.lbl_xc.setText(f"X imbalance: {xc:+.3f}" if not (xc != xc) else "X imbalance: —")
        if ain_yplus and ain_yminus:
            yc = beam_centering(currents[ain_yplus], currents[ain_yminus])
            self.lbl_yc.setText(f"Y imbalance: {yc:+.3f}" if not (yc != yc) else "Y imbalance: —")

        # Auto-advance slider to live edge when in live mode
        if self._is_live:
            self.slider.blockSignals(True)
            self.slider.setValue(10_000)
            self.slider.blockSignals(False)

    def _on_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)
        self._do_disconnect()

    # ---- Slider / navigation -------------------------------------------------

    def _on_slider_changed(self, val: int):
        if val >= 9_800:
            self._enter_live_mode()
        else:
            self._enter_frozen_mode(val)

    def _enter_live_mode(self):
        self._is_live = True
        self._frozen_right_edge = None
        self.lbl_mode.setText("● LIVE  (last 2 min)")
        self.lbl_mode.setStyleSheet(
            "color: #1a7a1a; font-weight: bold; padding: 2px 6px;"
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
        w_start = max(t_oldest, self._frozen_right_edge - self.WINDOW_SECONDS)
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

        t_left = t_right - self.WINDOW_SECONDS

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
            self.ax.set_xlim(-self.WINDOW_SECONDS, 0)
            self.ax.relim()
            self.ax.autoscale_view(scalex=False, scaley=True)
            self.canvas.draw_idle()

    # ---- Owner-callable cleanup ----------------------------------------------

    def shutdown(self):
        self._do_disconnect()


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
