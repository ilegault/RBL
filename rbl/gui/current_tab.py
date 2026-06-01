"""
current_tab.py
PySide6 widget for the "Beam Current" outer tab.

Reads 4 analog inputs from a LabJack T7 at ~10 Hz, converts each log-amp
voltage to current, displays numerically + on a live rolling plot, and shows
a beam-centering indicator.
"""
import time

from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QComboBox, QMessageBox, QSizePolicy, QFormLayout,
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


# --- Background poll thread --------------------------------------------------

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
            elapsed = time.time() - tloop
            remaining = max(0.0, self.period - elapsed)
            self.msleep(int(remaining * 1000))


# --- The tab widget ----------------------------------------------------------

class CurrentTab(QWidget):
    """The 'Beam Current' outer tab."""

    BUFFER_CAPACITY = 600     # ~60 seconds at 10 Hz

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lj       = LabJackT7()
        self.worker   = None
        # One rolling buffer per channel
        self.buffers  = {name: RollingBuffer(self.BUFFER_CAPACITY)
                         for name in SC.LABJACK_CHANNEL_MAP.keys()}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection ─────────────────────────────────────────────────────
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
        self.lbl_conn_status.setStyleSheet("color: #888888; font-weight: bold;")
        conn.addWidget(self.lbl_conn_status)
        self.lbl_serial = QLabel("")
        self.lbl_serial.setStyleSheet("color: #888; font-style: italic;")
        conn.addWidget(self.lbl_serial, stretch=1)
        layout.addWidget(conn_box)

        # ── Per-channel numeric readouts ───────────────────────────────────
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
            self.lbl_v[ain].setStyleSheet("color: #aaa; font-family: Menlo;")
            ro.addWidget(self.lbl_v[ain], 1, col)
            self.lbl_i[ain] = QLabel("—")
            self.lbl_i[ain].setFont(mono)
            self.lbl_i[ain].setStyleSheet("color: #4caf50;")
            ro.addWidget(self.lbl_i[ain], 2, col)
        layout.addWidget(ro_box)

        # ── Beam-centering indicator ───────────────────────────────────────
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

        # ── Live plot ──────────────────────────────────────────────────────
        plot_box = QGroupBox("Live Currents (last 60 s)")
        pv = QVBoxLayout(plot_box)
        self.fig    = Figure(figsize=(7, 3))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Current (A)")
        self.ax.set_yscale("log")
        self.ax.grid(True, which="both", alpha=0.3)
        self._lines = {}
        colors = {"X+": "#e74c3c", "X-": "#3498db", "Y+": "#f1c40f", "Y-": "#2ecc71"}
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            line, = self.ax.plot([], [], label=f"{jaw} ({ain})",
                                 color=colors.get(jaw, "k"), lw=1)
            self._lines[ain] = line
        self.ax.legend(loc="upper left", fontsize=8)
        self.fig.tight_layout()
        pv.addWidget(self.canvas)
        layout.addWidget(plot_box, stretch=1)

        # Plot redraw at 5 Hz max (decoupled from poll thread)
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
            self.lbl_conn_status.setStyleSheet("color: #00FF00; font-weight: bold;")
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
        self.lbl_conn_status.setStyleSheet("color: #888888; font-weight: bold;")
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

        # Centering metric
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

    def _on_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)
        self._do_disconnect()

    def _redraw_plot(self):
        any_data = False
        for ain, line in self._lines.items():
            t, v = self.buffers[ain].snapshot()
            if len(t) > 1:
                # show last 60 s
                t_now = t[-1]
                mask  = t > (t_now - 60)
                line.set_data(t[mask] - t_now, v[mask])
                any_data = True
        if any_data:
            self.ax.set_xlim(-60, 0)
            self.ax.relim()
            self.ax.autoscale_view(scalex=False, scaley=True)
            self.canvas.draw_idle()

    # ---- Owner-callable cleanup ---------------------------------------------

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
