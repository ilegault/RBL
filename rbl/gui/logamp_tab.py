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
import math
from PySide6.QtCore import QTimer, Qt, QSize, QPointF
from PySide6.QtGui import QFont, QPainter, QColor, QPen, QBrush
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
    voltage_to_current, format_current, beam_centering, RollingBuffer,
)
from rbl.config import hardware_config as SC
from labjack_panel import LabJackPanel


# ─── Beam-position indicator ───────────────────────────────────────────────────

class BeamPositionIndicator(QWidget):
    """A small square frame with a dot marking the estimated beam position.

    The dot is placed from the left/right (X) and top/bottom (Y) log-amp current
    imbalance: more current collected on a jaw pulls the estimate toward that
    jaw.  Each axis takes the ``beam_centering`` metric in [-1, 1] (0 = centred,
    +1 = fully on the '+' jaw, -1 = fully on the '-' jaw).  This is only a coarse
    guess — the four slit jaws sample the beam tails, not its centroid — so it is
    labelled as an estimate, not a measurement.
    """

    # Jaw colours reused from the plot / readouts for a consistent palette.
    _COL_XP = QColor("#e74c3c")
    _COL_XM = QColor("#3498db")
    _COL_YP = QColor("#c47a00")
    _COL_YM = QColor("#1a7a1a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x = float("nan")   # X imbalance in [-1, 1]  (+ → X+ jaw)
        self._y = float("nan")   # Y imbalance in [-1, 1]  (+ → Y+ jaw)
        # Small but shrinkable so the whole app can still be compressed.
        self.setMinimumSize(80, 80)
        self.setMaximumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip(
            "Estimated beam position from the relative log-amp currents.\n"
            "The dot moves toward whichever jaw is collecting more current."
        )

    def set_position(self, x: float, y: float):
        """Update the estimate (values are the per-axis centering metric)."""
        self._x = x
        self._y = y
        self.update()

    def sizeHint(self):
        return QSize(150, 150)

    # Keep the drawable region square regardless of the box it lands in.
    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return w

    @staticmethod
    def _clamp(v: float) -> float:
        return -1.0 if v < -1.0 else (1.0 if v > 1.0 else v)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        margin = 16                       # room for the X±/Y± jaw labels
        side = min(w, h) - 2 * margin
        if side <= 0:
            return
        x0 = (w - side) / 2.0
        y0 = (h - side) / 2.0
        cx = x0 + side / 2.0
        cy = y0 + side / 2.0
        half = side / 2.0

        # Square aperture frame.
        p.setBrush(QBrush(QColor("#fafafa")))
        p.setPen(QPen(QColor("#555"), 1.5))
        p.drawRect(int(x0), int(y0), int(side), int(side))

        # Centre crosshair.
        p.setPen(QPen(QColor("#bbb"), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(x0), int(cy), int(x0 + side), int(cy))
        p.drawLine(int(cx), int(y0), int(cx), int(y0 + side))

        # Jaw labels around the frame.
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(self._COL_XM))
        p.drawText(int(x0 - 14), int(cy + 4), "X-")
        p.setPen(QPen(self._COL_XP))
        p.drawText(int(x0 + side + 2), int(cy + 4), "X+")
        p.setPen(QPen(self._COL_YP))
        p.drawText(int(cx - 6), int(y0 - 4), "Y+")
        p.setPen(QPen(self._COL_YM))
        p.drawText(int(cx - 6), int(y0 + side + 12), "Y-")

        # The beam-position dot.
        if math.isnan(self._x) or math.isnan(self._y):
            # No usable signal — draw a faint hollow marker at the centre.
            p.setPen(QPen(QColor("#bbb"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = side * 0.05
            p.drawEllipse(QPointF(cx, cy), r, r)
            return

        dx = self._clamp(self._x)
        dy = self._clamp(self._y)
        px = cx + dx * half          # +X imbalance → toward the X+ (right) jaw
        py = cy - dy * half          # +Y imbalance → toward the Y+ (top) jaw

        # Guide lines from centre to the estimate.
        p.setPen(QPen(QColor("#e07b39"), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(cx, cy), QPointF(px, py))

        # The dot itself (circle inside the square).
        r = max(4.0, side * 0.07)
        p.setPen(QPen(QColor("#a83a00"), 1.5))
        p.setBrush(QBrush(QColor("#e67e22")))
        p.drawEllipse(QPointF(px, py), r, r)


# ─── The tab widget ───────────────────────────────────────────────────────────

class CurrentTab(QWidget):
    """The 'Beam Current' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # fixed 2-minute viewport

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection (shared panel; MainWindow does the actual connecting) ──
        self.lj_panel = LabJackPanel()

        mono = QFont("Consolas", 13)
        mono.setBold(True)

        # ── Per-channel numeric readouts ──────────────────────────────────
        ro_box = QGroupBox("Live Readings")
        ro = QGridLayout(ro_box)
        ro.setSpacing(6)
        self.lbl_v = {}
        self.lbl_i = {}
        for col, (ain, jaw) in enumerate(SC.LABJACK_CHANNEL_MAP.items()):
            ro.addWidget(QLabel(f"{jaw}  ({ain})"), 0, col)
            self.lbl_v[ain] = QLabel("—")
            self.lbl_v[ain].setStyleSheet("color: #555; font-family: Consolas, 'Courier New', monospace;")
            ro.addWidget(self.lbl_v[ain], 1, col)
            self.lbl_i[ain] = QLabel("—")
            self.lbl_i[ain].setFont(mono)
            self.lbl_i[ain].setStyleSheet("color: #1a7a1a; font-weight: bold;")
            ro.addWidget(self.lbl_i[ain], 2, col)

        # ── Beam-centering indicator ──────────────────────────────────────
        center_box = QGroupBox("Beam Position (estimated)")
        center = QVBoxLayout(center_box)
        center.setSpacing(4)

        # Circle-in-a-square visual guess of where the beam sits, driven by the
        # relative log-amp currents.
        self.beam_indicator = BeamPositionIndicator()
        center.addWidget(self.beam_indicator, stretch=1,
                         alignment=Qt.AlignmentFlag.AlignHCenter)

        self.lbl_xc = QLabel("X imbalance: —")
        self.lbl_yc = QLabel("Y imbalance: —")
        self.lbl_xc.setFont(mono)
        self.lbl_yc.setFont(mono)
        imb_row = QHBoxLayout()
        imb_row.addWidget(self.lbl_xc)
        imb_row.addStretch()
        imb_row.addWidget(self.lbl_yc)
        center.addLayout(imb_row)

        # ── Assemble upper section: left (connection + centering) | right (readouts) ──
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addWidget(self.lj_panel)
        left_col.addWidget(center_box)
        left_col.addStretch()

        upper_row = QHBoxLayout()
        upper_row.setSpacing(8)
        upper_row.addLayout(left_col)
        upper_row.addWidget(ro_box, stretch=1)
        layout.addLayout(upper_row)

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
        # Scale (ticks + label) on the RIGHT: the live edge and newest values
        # arrive from the right, so the axis reads next to where the trace ends.
        self.ax.set_ylabel("Current (A)")
        self.ax.yaxis.set_label_position("right")
        self.ax.yaxis.tick_right()
        self.ax.set_yscale("log")
        self.ax.grid(True, which="both", alpha=0.3)
        self._lines = {}
        colors = {"X+": "#e74c3c", "X-": "#3498db", "Y+": "#c47a00", "Y-": "#1a7a1a"}
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            line, = self.ax.plot([], [], label=f"{jaw} ({ain})",
                                 color=colors.get(jaw, "k"), lw=1.5)
            self._lines[ain] = line
        # Legend on the RIGHT side of the plot, to match the right-hand scale.
        self.ax.legend(loc="upper right", fontsize=8)
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
            self.lbl_i[ain].setStyleSheet("color: #1a7a1a; font-weight: bold;")
            self.buffers[ain].append(t, I)

        # Beam-centering indicators.
        ain_xp = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "X+"), None)
        ain_xm = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "X-"), None)
        ain_yp = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "Y+"), None)
        ain_ym = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "Y-"), None)
        xc = yc = float("nan")
        if ain_xp and ain_xm:
            xc = beam_centering(currents.get(ain_xp, float("nan")),
                                currents.get(ain_xm, float("nan")))
            self.lbl_xc.setText(
                f"X imbalance: {xc:+.3f}" if not math.isnan(xc) else "X imbalance: —"
            )
        if ain_yp and ain_ym:
            yc = beam_centering(currents.get(ain_yp, float("nan")),
                                currents.get(ain_ym, float("nan")))
            self.lbl_yc.setText(
                f"Y imbalance: {yc:+.3f}" if not math.isnan(yc) else "Y imbalance: —"
            )
        self.beam_indicator.set_position(xc, yc)

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

        ain_xplus  = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "X+"), None)
        ain_xminus = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "X-"), None)
        ain_yplus  = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "Y+"), None)
        ain_yminus = next((a for a, j in SC.LABJACK_CHANNEL_MAP.items() if j == "Y-"), None)
        xc = yc = float("nan")
        if ain_xplus and ain_xminus:
            xc = beam_centering(currents[ain_xplus], currents[ain_xminus])
            self.lbl_xc.setText(f"X imbalance: {xc:+.3f}" if not (xc != xc) else "X imbalance: —")
        if ain_yplus and ain_yminus:
            yc = beam_centering(currents[ain_yplus], currents[ain_yminus])
            self.lbl_yc.setText(f"Y imbalance: {yc:+.3f}" if not (yc != yc) else "Y imbalance: —")
        self.beam_indicator.set_position(xc, yc)

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
