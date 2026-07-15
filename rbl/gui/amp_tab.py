"""
amp_tab.py
PySide6 widget for the "HV Amplifiers" outer tab.

Displays the VOLTAGE MONITOR and CURRENT MONITOR readings of four
EEL5000.20.100 high-voltage amplifiers (X+, X-, Y+, Y-), wired to a LabJack T7
via a CB37 terminal board on AIN4..AIN11.

This tab does NOT own a LabJack connection. MainWindow owns the single shared
LabJackT7 + poll worker and feeds every tab the same 12-channel reading dict.
We take AIN4..AIN11 and ignore AIN0..AIN3 (the log amps). See
rbl/hardware/labjack_poller.py.

Plot navigation mirrors current_tab.py exactly:
  - Fixed 2-minute viewport (WINDOW_SECONDS = 120).
  - Slider at max  -> LIVE: window tracks "now".
  - Slider dragged -> FROZEN: window locked to a historical position.
  - Buffer holds ~1 hour (BUFFER_CAPACITY = 36 000 @ 10 Hz).
"""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox, QSizePolicy, QSlider,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from rbl.hardware.labjack_driver import LJM_AVAILABLE
from rbl.hardware.current_monitor import RollingBuffer
from rbl.hardware.amp_monitor import (
    monitor_to_kv, monitor_to_ma, format_kv, format_ma,
    voltage_status, current_status,
)
from rbl.hardware import slit_config as SC
from labjack_panel import LabJackPanel


# Status -> stylesheet color
_STATUS_COLOR = {
    "ok":   "#1a7a1a",   # green
    "peak": "#c47a00",   # amber — legal only as a <4 ms transient
    "over": "#c0392b",   # red   — out of spec / bad reading
}


class AmpTab(QWidget):
    """The 'HV Amplifiers' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # fixed 2-minute viewport

    def __init__(self, parent=None):
        super().__init__(parent)

        # One buffer per AIN. Keys are AIN names so _on_reading can index directly.
        self.buffers = {ain: RollingBuffer(self.BUFFER_CAPACITY)
                        for ain in SC.AMP_AIN_NAMES}

        # Plot state
        self._is_live           = True
        self._frozen_right_edge = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection (shared panel) ─────────────────────────────────────
        self.lj_panel = LabJackPanel()
        layout.addWidget(self.lj_panel)

        # ── Per-amplifier numeric readouts ────────────────────────────────
        ro_box = QGroupBox("Live Amplifier Monitors")
        ro = QGridLayout(ro_box)
        ro.setSpacing(6)

        mono = QFont("Menlo", 13)
        mono.setBold(True)
        small = QFont("Menlo", 9)

        ro.addWidget(QLabel(""), 0, 0)
        ro.addWidget(QLabel("Output Voltage"), 1, 0)
        ro.addWidget(QLabel("Current Draw"),   2, 0)

        self.lbl_kv = {}
        self.lbl_ma = {}
        for col, amp in enumerate(SC.AMP_LABELS, start=1):
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]

            hdr = QLabel(f"{amp}")
            hdr.setStyleSheet(
                f"color: {SC.AMP_COLORS[amp]}; font-weight: bold; font-size: 14px;"
            )
            sub = QLabel(f"{v_ain} / {i_ain}")
            sub.setFont(small)
            sub.setStyleSheet("color: #888;")
            hdr_box = QVBoxLayout()
            hdr_w = QWidget()
            hdr_box.setContentsMargins(0, 0, 0, 0)
            hdr_box.addWidget(hdr)
            hdr_box.addWidget(sub)
            hdr_w.setLayout(hdr_box)
            ro.addWidget(hdr_w, 0, col)

            self.lbl_kv[amp] = QLabel("—")
            self.lbl_kv[amp].setFont(mono)
            self.lbl_kv[amp].setStyleSheet("color: #555;")
            ro.addWidget(self.lbl_kv[amp], 1, col)

            self.lbl_ma[amp] = QLabel("—")
            self.lbl_ma[amp].setFont(mono)
            self.lbl_ma[amp].setStyleSheet("color: #555;")
            ro.addWidget(self.lbl_ma[amp], 2, col)

        layout.addWidget(ro_box)

        # ── Sampling caveat ───────────────────────────────────────────────
        note = QLabel(
            "Sampled at 10 Hz. During a kHz raster scan these read a time-average "
            "of the deflection waveform, not its peak. Use for DC bias, drift, and "
            "fault detection — not waveform capture."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "color: #8c6000; background: #fdf6e3; border: 1px solid #e0d5b0;"
            " border-radius: 4px; padding: 4px 8px; font-size: 11px;"
        )
        layout.addWidget(note)

        # ── Plots ─────────────────────────────────────────────────────────
        plot_box = QGroupBox("Amplifier History (2-min window)")
        pv = QVBoxLayout(plot_box)

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

        # Two stacked axes sharing the x-axis: voltage on top, current below.
        self.fig    = Figure(figsize=(7, 6))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)

        self.ax_v = self.fig.add_subplot(211)
        self.ax_i = self.fig.add_subplot(212, sharex=self.ax_v)

        self.ax_v.set_ylabel("Output Voltage (kV)")
        self.ax_v.grid(True, alpha=0.3)
        self.ax_v.axhline(0.0, color="#999", lw=0.8, ls="-")
        # Rating envelope: +/-5 kV
        self.ax_v.axhline( SC.AMP_MAX_KV, color="#c0392b", lw=0.8, ls="--", alpha=0.5)
        self.ax_v.axhline(-SC.AMP_MAX_KV, color="#c0392b", lw=0.8, ls="--", alpha=0.5)
        self.ax_v.tick_params(labelbottom=False)

        self.ax_i.set_ylabel("Current Draw (mA)")
        self.ax_i.set_xlabel("Time (s, relative to window right edge)")
        self.ax_i.grid(True, alpha=0.3)
        self.ax_i.axhline(0.0, color="#999", lw=0.8, ls="-")
        # DC rating envelope: +/-20 mA
        self.ax_i.axhline( SC.AMP_MAX_MA_DC, color="#c47a00", lw=0.8, ls="--", alpha=0.5)
        self.ax_i.axhline(-SC.AMP_MAX_MA_DC, color="#c47a00", lw=0.8, ls="--", alpha=0.5)

        # One line per amplifier per axis, keyed by amp label.
        self._lines_v = {}
        self._lines_i = {}
        for amp in SC.AMP_LABELS:
            c = SC.AMP_COLORS[amp]
            lv, = self.ax_v.plot([], [], label=amp, color=c, lw=1.5)
            li, = self.ax_i.plot([], [], label=amp, color=c, lw=1.5)
            self._lines_v[amp] = lv
            self._lines_i[amp] = li

        self.ax_v.legend(loc="upper left", fontsize=8, ncol=4)
        self.ax_i.legend(loc="upper left", fontsize=8, ncol=4)
        self.fig.tight_layout()
        pv.addWidget(self.canvas, stretch=1)

        # History slider: 0 = oldest, 10000 = live.
        slider_row = QHBoxLayout()
        lbl_hist = QLabel("◀ History")
        lbl_hist.setStyleSheet("color: #555; font-size: 10px;")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10_000)
        self.slider.setValue(10_000)
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
        self.lj_panel.set_connected(True, serial)
        self._redraw_timer.start()

    def on_labjack_disconnected(self):
        self._redraw_timer.stop()
        self.lj_panel.set_connected(False)

    # ---- Slots ---------------------------------------------------------------

    def _on_reading(self, t: float, values: dict):
        """Consume ONLY AIN4..AIN11. AIN0..AIN3 belong to the Beam Current tab."""
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]

            v_raw = values.get(v_ain)
            i_raw = values.get(i_ain)
            if v_raw is None or i_raw is None:
                continue

            kv = monitor_to_kv(v_raw)
            ma = monitor_to_ma(i_raw)

            self.buffers[v_ain].append(t, kv)
            self.buffers[i_ain].append(t, ma)

            self.lbl_kv[amp].setText(format_kv(kv))
            self.lbl_kv[amp].setStyleSheet(
                f"color: {_STATUS_COLOR[voltage_status(kv)]}; font-weight: bold;"
            )
            self.lbl_ma[amp].setText(format_ma(ma))
            self.lbl_ma[amp].setStyleSheet(
                f"color: {_STATUS_COLOR[current_status(ma)]}; font-weight: bold;"
            )

        if self._is_live:
            self.slider.blockSignals(True)
            self.slider.setValue(10_000)
            self.slider.blockSignals(False)

    def _on_error(self, msg: str):
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

        if self._is_live:
            ref_t = None
            for buf in self.buffers.values():
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

        for amp in SC.AMP_LABELS:
            for ain, line in (
                (SC.AMP_CHANNEL_MAP[amp]["voltage"], self._lines_v[amp]),
                (SC.AMP_CHANNEL_MAP[amp]["current"], self._lines_i[amp]),
            ):
                t, v = self.buffers[ain].snapshot()
                if len(t) < 2:
                    continue
                mask = (t >= t_left) & (t <= t_right)
                if mask.sum() < 2:
                    line.set_data([], [])
                    continue
                t_win = t[mask]
                v_win = v[mask]
                if len(t_win) > 600:      # decimate for redraw performance
                    step  = len(t_win) // 600
                    t_win = t_win[::step]
                    v_win = v_win[::step]
                line.set_data(t_win - t_right, v_win)
                any_data = True

        if any_data:
            self.ax_v.set_xlim(-self.WINDOW_SECONDS, 0)
            for ax in (self.ax_v, self.ax_i):
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
            self.canvas.draw_idle()

    # ---- Owner-callable cleanup ----------------------------------------------

    def shutdown(self):
        self._redraw_timer.stop()


# Standalone smoke test
if __name__ == "__main__":
    import os
    import sys
    if "DISPLAY" not in os.environ and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = AmpTab()

    # Feed one synthetic reading covering all 12 channels.
    w._on_reading(0.0, {
        "AIN0": 3.0, "AIN1": 3.0, "AIN2": 3.0, "AIN3": 3.0,   # log amps (ignored)
        "AIN4": 3.0, "AIN5":  1.0,      # X+ : 3 kV, 10 mA
        "AIN6": -3.0, "AIN7": 1.0,      # X- : -3 kV, 10 mA
        "AIN8": 2.0, "AIN9":  0.5,      # Y+ : 2 kV, 5 mA
        "AIN10": -2.0, "AIN11": 0.5,    # Y- : -2 kV, 5 mA
    })
    assert "3.000 kV" in w.lbl_kv["X+"].text(), w.lbl_kv["X+"].text()
    assert "10.000 mA" in w.lbl_ma["X+"].text(), w.lbl_ma["X+"].text()
    assert "-3.000 kV" in w.lbl_kv["X-"].text()
    # Log-amp AINs must NOT have been buffered here.
    assert "AIN0" not in w.buffers
    print("[OK] amp_tab: constructed and consumed a reading")

    w.resize(1000, 900)
    w.show()
    sys.exit(app.exec())
