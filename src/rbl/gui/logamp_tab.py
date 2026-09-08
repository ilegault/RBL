"""
logamp_tab.py
PySide6 widget for the "Beam Current" outer tab.

Renders the four NEC log amps at ~10 Hz: each slit current numerically, on a
live rolling plot, and as a beam-centering indicator.

This tab owns no LabJack handle and does no unit conversion. Beamline holds
the single shared T7 and converts each window's volts to amps once (see
rbl/state/labjack_link.py); this tab renders the LogAmpState that comes out
of it. A screen that re-derived the current from the raw payload — which this
one used to do — is a second copy of the log-amp calibration curve that can
drift away from the first.

Plot navigation (from TDS-T8 live_plot mechanism):
  - Fixed 2-minute viewport window (WINDOW_SECONDS = 120).
  - Slider at max → LIVE mode: window tracks "now".
  - Drag slider left → FROZEN mode: window locked to historical position.
  - Buffer holds ~1 hour of history (BUFFER_CAPACITY = 36 000 @ 10 Hz).
"""
import time

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rbl.config import hardware_config as SC
from rbl.gui import theme
from rbl.gui.widgets.beam_indicator import BeamPositionIndicator
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.live_plot import LivePlotPanel
from rbl.hardware.current_monitor import RollingBuffer, format_current
from rbl.hardware.labjack_driver import LJM_AVAILABLE
from rbl.state.snapshots import LogAmpState

# ─── The tab widget ───────────────────────────────────────────────────────────

class CurrentTab(QWidget):
    """The 'Beam Current' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # default 2-minute viewport
    # The log-amp tab never zooms below 1 s: log amps only carry a 10 Hz mean
    # voltage (see on_logamp_state), so a sub-second window has nothing to show.
    MIN_WINDOW_SECONDS = 1.0

    def __init__(self, beamline=None, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        # This tab does not own a LabJack handle or stream worker. MainWindow
        # owns the single shared instance; snapshots arrive via on_logamp_state().
        self._t0 = time.monotonic()   # reset on labjack_connected
        self.buffers = {name: RollingBuffer(self.BUFFER_CAPACITY)
                        for name in SC.LABJACK_CHANNEL_MAP.keys()}

        # The redraw timer only needs to run when BOTH hold: connected (data
        # is arriving) and visible (this tab is the one on screen). Buffers
        # keep filling in the background either way — only painting pauses.
        self._connected = False
        self._visible   = False

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
        for col, (ain, slit) in enumerate(SC.LABJACK_CHANNEL_MAP.items()):
            hdr = QLabel(f"{slit}  ({ain})")
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

        # Shared history slider + LIVE/FROZEN state machine + zoom-step list.
        # Redraw at 5 Hz max.
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
        btn_time_out.clicked.connect(self.plot.zoom_out)
        btn_time_in = QPushButton("＋")
        btn_time_in.setFixedWidth(28)
        btn_time_in.setToolTip("Decrease time window (zoom in)")
        btn_time_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_in.clicked.connect(self.plot.zoom_in)
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
        self.btn_jump_live.clicked.connect(self.plot.jump_to_live)
        nav_row.addWidget(self.btn_jump_live)
        pv.addLayout(nav_row)

        self.ax = self.plot.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        # Scale (ticks + label) on the RIGHT: the live edge and newest values
        # arrive from the right, so the axis reads next to where the trace ends.
        self.ax.set_ylabel("Current (A)")
        self.ax.yaxis.set_label_position("right")
        self.ax.yaxis.tick_right()
        self.ax.set_yscale("log")
        self.ax.grid(True, which="both", alpha=0.3)
        self._lines = {}
        for ain, slit in SC.LABJACK_CHANNEL_MAP.items():
            line, = self.ax.plot([], [], color=theme.SLIT_COLORS.get(slit, "k"), lw=1.5)
            self._lines[ain] = line
        self.plot.fig.tight_layout()

        # Qt legend panel (right of canvas — avoids matplotlib layout fighting)
        _legend_w = QWidget()
        _legend_w.setFixedWidth(115)
        _leg_lay = QVBoxLayout(_legend_w)
        _leg_lay.setSpacing(3)
        _leg_lay.setContentsMargins(4, 8, 4, 4)
        _leg_title = QLabel("Legend")
        _leg_title.setStyleSheet("font-size: 15px; color: #555; font-weight: bold;")
        _leg_lay.addWidget(_leg_title)
        for _ain, _slit in SC.LABJACK_CHANNEL_MAP.items():
            _row = QHBoxLayout()
            _swatch = QLabel("━")
            _swatch.setStyleSheet(
                f"color: {theme.SLIT_COLORS.get(_slit, '#000')}; "
                "font-weight: bold; font-size: 13px;"
            )
            _lbl = QLabel(f"{_slit}  ({_ain})")
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
        _content_row.addWidget(self.plot.canvas, stretch=1)
        _content_row.addWidget(_legend_w)
        pv.addLayout(_content_row, stretch=1)
        pv.addLayout(self.plot.slider_row)

        layout.addWidget(plot_box, stretch=1)

        self._on_navigation_changed()   # initial "● LIVE" label

        if not LJM_AVAILABLE:
            self.lj_panel.set_enabled(False)

    # ---- Connection lifecycle (driven by MainWindow) --------------------------

    def on_labjack_connected(self, serial: str):
        """MainWindow calls this after the shared T7 opens."""
        self._t0 = time.monotonic()
        self._connected = True
        self.lj_panel.set_connected(True, serial)
        self._update_redraw_state()

    def on_labjack_disconnected(self):
        """MainWindow calls this after the shared T7 closes."""
        self._connected = False
        self._update_redraw_state()
        self.lj_panel.set_connected(False)

    # ---- Visibility (QStackedWidget hides the non-current tab) ---------------
    #
    # The redraw timer is rendering, not data acquisition: it only needs to run
    # while this tab is the one on screen. Buffers keep filling via on_logamp_state
    # regardless of visibility, so no data is lost while hidden — only
    # painting pauses.

    def showEvent(self, event):
        super().showEvent(event)
        self._visible = True
        self._update_redraw_state()
        self._redraw_plot()   # repaint immediately, don't wait for a stale frame

    def hideEvent(self, event):
        super().hideEvent(event)
        self._visible = False
        self._update_redraw_state()

    def _update_redraw_state(self):
        if self._connected and self._visible:
            self.plot.start()
        else:
            self.plot.stop()

    # ---- Slots ---------------------------------------------------------------

    def set_slit_state(self, state: dict):
        """Slit geometry pushed over from the motor tab (see MotorTab.slit_state)."""
        self.beam_indicator.set_slit_state(state)

    def on_motor_state(self, state):
        """Slit geometry from Beamline.motors_changed (a state.MotorState).

        Adapts to the dict shape BeamPositionIndicator already expects rather
        than changing that widget's interface.
        """
        self.set_slit_state({
            "connected": state.connected,
            "zeroed":    state.zeroed,
            "positions": {slit: axis.pos_mm for slit, axis in state.axes.items()},
        })

    def on_logamp_state(self, state: LogAmpState):
        """Render one LogAmpState — the log-amp half of one stream window.

        The volts-to-amps conversion is NOT done here. It happens once, in
        Beamline (rbl/state/labjack_link.py), and every screen showing a beam
        current renders the result of that one conversion. This tab used to
        repeat the calculation on the raw payload while Beamline did it too
        for the Overview; two copies of a calibration curve is one more than
        can be kept correct.

        A slit missing from `state.volts` was not sampled this window — the
        WAVEFORM profile does not scan the log amps — and shows as paused
        rather than as a stale number. That is a different condition from a
        sampled channel whose current came back NaN (out of the log amp's
        calibrated range), which still shows its voltage: one is the profile
        doing what it was asked, the other is a reading worth investigating.
        """
        if not state.connected:
            return

        for ain, slit in SC.LABJACK_CHANNEL_MAP.items():
            if slit not in state.volts:
                self.lbl_v[ain].setText("—  (Waveform mode)")
                self.lbl_v[ain].setStyleSheet(
                    "color: #aaa; font-family: Consolas, 'Courier New', monospace;"
                )
                self.lbl_i[ain].setText("—  (Waveform mode)")
                self.lbl_i[ain].setStyleSheet("color: #aaa; font-weight: bold;")
                continue

            V = state.volts[slit]
            i_a = state.currents.get(slit, float("nan"))
            self.lbl_v[ain].setText(f"{V:6.3f} V")
            self.lbl_v[ain].setStyleSheet(
                "color: #555; font-family: Consolas, 'Courier New', monospace;"
            )
            self.lbl_i[ain].setText(format_current(i_a))
            self.lbl_i[ain].setStyleSheet(theme.status_label(theme.OK))
            self.buffers[ain].append(state.t, i_a)

        self.beam_indicator.set_currents(dict(state.currents))

        # Auto-advance slider to live edge when in live mode
        if self.plot.is_live:
            self.plot.force_to_live()

    def on_labjack_error(self, msg: str):
        # MainWindow owns teardown; we only surface the message.
        QMessageBox.warning(self, "LabJack poll error", msg)

    def on_profile_changed(self, profile_name: str) -> None:
        pass   # This tab renders log-amp currents regardless of profile.

    # ---- Buffer span (LivePlotPanel data callbacks) ---------------------------

    def _live_edge(self):
        """Newest timestamp available across all channels, or None.

        Cheap by design (per-buffer O(1) `.latest()`): called on every redraw
        tick while LIVE.
        """
        ref_t = None
        for buf in self.buffers.values():
            t, _ = buf.latest()
            if not (t != t):   # not NaN
                if ref_t is None or t > ref_t:
                    ref_t = t
        return ref_t

    def _buffer_span(self):
        """(t_oldest, t_newest) across the buffers, or None.

        Only called when a slider drag enters FROZEN mode, so the full
        snapshot/sort cost here is fine.
        """
        t_arr, _ = self.buffers[next(iter(self.buffers))].snapshot()
        if len(t_arr) < 2:
            return None
        return (float(t_arr[0]), float(t_arr[-1]))

    # ---- Mode label ------------------------------------------------------------
    #
    # Formatting stays here (not in LivePlotPanel) because the two tabs word
    # LIVE/FROZEN status slightly differently. Two callbacks rather than one,
    # matching a pre-existing distinction: right after a slider drag the
    # frozen label shows the elapsed-time range, but a subsequent zoom while
    # still frozen shows only the window width, not the range.

    def _set_live_label(self):
        w = self.plot.window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        self.lbl_mode.setText(f"● LIVE  (last {label})")
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
        )

    def _on_navigation_changed(self):
        self.btn_jump_live.setVisible(not self.plot.is_live)
        if self.plot.is_live:
            self._set_live_label()
            return
        span = self._buffer_span()
        t_oldest = span[0] if span is not None else self.plot.frozen_right_edge
        w_start = max(t_oldest, self.plot.frozen_right_edge - self.plot.window_seconds)
        self.lbl_mode.setText(
            f"⏸  Frozen  —  t = [{w_start:+.0f} s … {self.plot.frozen_right_edge:+.0f} s]"
        )
        self.lbl_mode.setStyleSheet(
            "color: #8c6000; font-weight: bold; padding: 2px 6px;"
        )

    def _on_zoom_changed(self):
        if self.plot.is_live:
            self._set_live_label()
            return
        w = self.plot.window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        self.lbl_mode.setText(f"⏸  Frozen  —  window {label}")

    # ---- Plot redraw ---------------------------------------------------------

    def _redraw_plot(self):
        window = self.plot.compute_window()
        if window is None:
            return
        t_left, t_right = window
        any_data = False

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
            self.ax.set_xlim(-self.plot.window_seconds, 0)
            self.ax.relim()
            self.ax.autoscale_view(scalex=False, scaley=True)
            self.plot.canvas.draw_idle()

    # ---- Owner-callable cleanup ----------------------------------------------

    def shutdown(self):
        """MainWindow closes the LabJack itself; we just stop redrawing."""
        self.plot.stop()
