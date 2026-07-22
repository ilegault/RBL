"""
amp_tab.py
PySide6 widget for the "HV Amplifiers" outer tab.

Displays the VOLTAGE MONITOR and CURRENT MONITOR readings of four
EEL5000.20.100 high-voltage amplifiers (X+, X-, Y+, Y-), wired to a LabJack T7
via a CB37 terminal board on AIN6..AIN13.

This tab does NOT own a LabJack connection. MainWindow owns the single shared
LabJackT7 + stream worker and feeds every tab the same window payload. We take
AIN6..AIN13 and ignore AIN0..AIN3 (the log amps).

ONE plot, two viewing modes (driven by the time-window zoom)
-----------------------------------------------------------
There is a single matplotlib figure (voltage over current).  The time-window
control seamlessly changes WHAT it shows:

  * TREND mode   (window >= SNAPSHOT_MAX_SECONDS):
        One point per stream window (10 Hz) of peak-kV / RMS-mA, held in the
        rolling history buffers.  This is the DC-bias / drift / fault view.

  * SNAPSHOT mode (window <  SNAPSHOT_MAX_SECONDS):
        The actual high-rate waveform samples from the most recent windows,
        drawn on the SAME axes and lines.  Zooming the window down to a few ms
        finally lets you see the deflection waveform itself — the 10 Hz trend
        can never resolve it.

There is deliberately NO second waveform plot: the one figure switches modes.

Applying stream settings
------------------------
The profile and single-channel target combos only STAGE a selection; nothing
touches the hardware until "Apply" is pressed.  Each profile/target change is a
full eStreamStop -> reconfigure -> eStreamStart cycle on the T7, so committing
them one deliberate click at a time avoids the churn (and transient glitches) of
restarting the stream on every stray combo event.
"""
import time
import collections

import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox, QSizePolicy, QSlider, QComboBox,
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
from rbl.config import hardware_config as SC
from rbl.config.labjack_stream_config import (
    STREAM_PROFILES, GUI_REFRESH_HZ, window_samples, resolution_index,
    is_single_channel, DEFAULT_SINGLE_CHANNEL,
)
from labjack_panel import LabJackPanel


# Reverse map: AIN name -> (amp label, kind) for the 8 amplifier monitors.
# Built from AMP_CHANNEL_MAP so it always tracks the wiring config.
_AIN_TO_AMP = {
    SC.AMP_CHANNEL_MAP[amp][kind]: (amp, kind)
    for amp in SC.AMP_LABELS
    for kind in ("voltage", "current")
}


# Zoom step list (seconds, descending).  Snapping to preset values keeps labels
# clean: the 15→5→1 jump avoids the ugly 7.5/3.75/1.875/0.9375... sequence,
# and halving from exactly 1 s gives tidy ms values (500, 250, 125, …).
_ZOOM_STEPS = [
    3600, 1800, 900, 600, 300, 120, 60, 30, 15, 5, 1,
    0.5, 0.25, 0.125, 0.0625, 0.03125, 0.016, 0.008, 0.004, 0.002, 0.001,
]


# Status -> stylesheet color
_STATUS_COLOR = {
    "ok":   "#1a7a1a",   # green
    "peak": "#c47a00",   # amber — legal only as a <4 ms transient
    "over": "#c0392b",   # red   — out of spec / bad reading
}


class AmpTab(QWidget):
    """The 'HV Amplifiers' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz (trend history)
    WINDOW_SECONDS  = 120      # default 2-minute viewport (trend mode)

    # Below this window width the single plot renders the raw high-rate waveform
    # (snapshot mode) instead of the 10 Hz trend.  2 s is the hand-off: at 2 s
    # the trend still has ~20 points, and the raw ring buffer holds enough to
    # fill the view.
    SNAPSHOT_MAX_SECONDS = 2.0

    # Each stream window spans exactly one GUI-refresh period of real time,
    # because window_samples == per_channel_rate_hz / GUI_REFRESH_HZ.
    WINDOW_DURATION_S = 1.0 / GUI_REFRESH_HZ   # 0.1 s

    # Max samples actually drawn per line in snapshot mode.  The raw window can
    # be 10 000 points (100 kS/s); pushing all of them into matplotlib every
    # frame is what made the plot stutter.  We min/max-decimate to this cap,
    # which preserves the waveform envelope (peaks are never hidden) while
    # keeping the redraw cheap.
    WF_MAX_POINTS = 2000

    # Emitted when the user applies a new profile selection.
    # MainWindow connects this to _set_stream_profile().
    profile_change_requested = Signal(str)

    # Emitted (with an AIN name) when the user applies a new single-channel
    # target.  MainWindow connects this to _set_stream_channel().
    single_channel_change_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t0 = time.monotonic()   # reset on labjack_connected

        # One trend buffer per AIN. Keys are AIN names so _on_window can index directly.
        self.buffers = {ain: RollingBuffer(self.BUFFER_CAPACITY)
                        for ain in SC.AMP_AIN_NAMES}

        # Raw-waveform ring, one deque per AIN, holding recent (t_end, values)
        # window chunks (values already in kV / mA).  Feeds snapshot mode.
        self._wave_chunks = {ain: collections.deque() for ain in SC.AMP_AIN_NAMES}

        # Plot state
        self._is_live           = True
        self._frozen_right_edge = None
        self._plot_mode         = "trend"   # "trend" | "snapshot"

        # Single-channel mode state.  When a single-channel profile is active,
        # only self._single_target_ain streams live; the other seven monitors
        # (and all log amps) are paused.
        self._single_mode        = False
        self._single_target_ain  = DEFAULT_SINGLE_CHANNEL

        # What the hardware is CURRENTLY running (vs. the staged combo choices).
        # Apply is enabled only when a staged choice differs from these.
        self._applied_profile   = "FULL"
        self._applied_channel   = DEFAULT_SINGLE_CHANNEL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection panel (added to top_row below) ─────────────────────
        self.lj_panel = LabJackPanel()

        # ── Per-amplifier numeric readouts ────────────────────────────────
        ro_box = QGroupBox("Live Amplifier Monitors")
        ro = QGridLayout(ro_box)
        ro.setSpacing(3)
        ro.setContentsMargins(6, 4, 6, 4)

        mono = QFont("Menlo", 13)
        mono.setBold(True)
        small = QFont("Menlo", 9)

        ro.addWidget(QLabel(""), 0, 0)

        def _make_hdr(text):
            lbl = QLabel(text)
            lbl.setFont(small)
            lbl.setStyleSheet("color: #666;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return lbl

        ro.addWidget(_make_hdr("Pk kV"), 1, 0)
        ro.addWidget(_make_hdr("PP kV"), 2, 0)
        ro.addWidget(_make_hdr("RM kV"), 3, 0)
        ro.addWidget(_make_hdr("RM mA"), 4, 0)

        self.lbl_kv   = {}   # peak output voltage
        self.lbl_pp   = {}   # pk-pk output voltage
        self.lbl_rms  = {}   # RMS output voltage
        self.lbl_ma   = {}   # RMS current draw
        for col, amp in enumerate(SC.AMP_LABELS, start=1):
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]

            hdr = QLabel(f"{amp}")
            hdr.setStyleSheet(
                f"color: {SC.AMP_COLORS[amp]}; font-weight: bold; font-size: 14px;"
            )
            sub = QLabel(f"{v_ain}/{i_ain}")
            sub.setFont(small)
            sub.setStyleSheet("color: #888;")
            hdr_w = QWidget()
            hdr_box = QVBoxLayout(hdr_w)
            hdr_box.setContentsMargins(0, 0, 0, 0)
            hdr_box.setSpacing(0)
            hdr_box.addWidget(hdr)
            hdr_box.addWidget(sub)
            ro.addWidget(hdr_w, 0, col)

            # One grid row per metric — aligns perfectly with col-0 headers
            for row, attr in enumerate(("lbl_kv", "lbl_pp", "lbl_rms"), start=1):
                lbl = QLabel("—")
                lbl.setFont(mono)
                lbl.setStyleSheet("color: #555;")
                getattr(self, attr)[amp] = lbl
                ro.addWidget(lbl, row, col)

            # Current: single RMS mA value
            lbl_ma = QLabel("—")
            lbl_ma.setFont(mono)
            lbl_ma.setStyleSheet("color: #555;")
            self.lbl_ma[amp] = lbl_ma
            ro.addWidget(lbl_ma, 4, col)

        # ── Profile selector (placed right of lj_panel in top_row below) ────
        prof_box = QGroupBox("Stream Profile")
        prof_col = QVBoxLayout(prof_box)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._profile_combo = QComboBox()
        for pname, pdata in STREAM_PROFILES.items():
            self._profile_combo.addItem(pdata["description"], userData=pname)
        self._profile_combo.setCurrentIndex(
            list(STREAM_PROFILES.keys()).index("FULL")
        )
        self._profile_combo.currentIndexChanged.connect(self._on_selection_staged)
        mode_row.addWidget(self._profile_combo, stretch=1)
        mode_row.addWidget(QLabel("Target:"))
        self._single_combo = QComboBox()
        for amp in SC.AMP_LABELS:
            for kind in ("voltage", "current"):
                ain = SC.AMP_CHANNEL_MAP[amp][kind]
                self._single_combo.addItem(
                    f"{amp} {kind.capitalize()}  ({ain})", userData=ain
                )
        default_idx = self._single_combo.findData(DEFAULT_SINGLE_CHANNEL)
        if default_idx >= 0:
            self._single_combo.setCurrentIndex(default_idx)
        self._single_combo.setEnabled(False)
        self._single_combo.setToolTip(
            "In single-channel mode, choose which amplifier monitor gets the "
            "full stream bandwidth."
        )
        self._single_combo.currentIndexChanged.connect(self._on_selection_staged)
        mode_row.addWidget(self._single_combo)

        # Apply button — the ONLY thing that commits a profile/target change to
        # the hardware.  Disabled until a staged choice differs from what's live.
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setToolTip(
            "Apply the selected stream profile / target to the LabJack.\n"
            "Changing the stream restarts it on the T7, so it is applied only "
            "when you click here — not on every dropdown change."
        )
        self._apply_btn.clicked.connect(self._apply_stream_settings)
        mode_row.addWidget(self._apply_btn)

        prof_col.addLayout(mode_row)
        self._profile_status = QLabel("")
        self._profile_status.setStyleSheet("color: #555; font-style: italic; font-size: 10px;")
        prof_col.addWidget(self._profile_status)

        # ── Assemble upper section: left col (connection + profile) | right (monitors) ──
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addWidget(self.lj_panel)
        left_col.addWidget(prof_box)
        left_col.addStretch()

        upper_row = QHBoxLayout()
        upper_row.setSpacing(8)
        upper_row.addLayout(left_col)
        upper_row.addWidget(ro_box, stretch=1)
        layout.addLayout(upper_row)

        # ── History / waveform plot (one figure, two modes) ─────────────────
        self._window_seconds = float(self.WINDOW_SECONDS)
        plot_box = QGroupBox("Amplifier History")
        pv = QVBoxLayout(plot_box)

        nav_row = QHBoxLayout()
        self.lbl_mode = QLabel(f"● LIVE  ({int(self._window_seconds)} s)")
        self.lbl_mode.setStyleSheet(
            "color: #1a7a1a; font-weight: bold; padding: 2px 6px;"
        )
        nav_row.addWidget(self.lbl_mode)
        btn_zoom_in = QPushButton("＋")
        btn_zoom_in.setFixedWidth(28)
        btn_zoom_in.setToolTip("Zoom in — scroll wheel up (halve window). "
                               "Below ~2 s the plot shows the raw waveform.")
        btn_zoom_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_zoom_in.clicked.connect(self._zoom_in)
        btn_zoom_out = QPushButton("－")
        btn_zoom_out.setFixedWidth(28)
        btn_zoom_out.setToolTip("Zoom out — scroll wheel down (double window)")
        btn_zoom_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_zoom_out.clicked.connect(self._zoom_out)
        nav_row.addWidget(btn_zoom_in)
        nav_row.addWidget(btn_zoom_out)
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

        self.fig    = Figure(figsize=(7, 6))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        self.canvas.mpl_connect('scroll_event', self._on_scroll)

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

        # One line per amplifier per axis, keyed by amp label.  Reused by BOTH
        # trend and snapshot modes.
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
        self._t0 = time.monotonic()
        # Start every buffer from a clean slate so the (possibly re-anchored)
        # stream timeline never mixes with data from a previous session.
        for buf in self.buffers.values():
            buf.__init__(self.BUFFER_CAPACITY)
        for dq in self._wave_chunks.values():
            dq.clear()
        self.lj_panel.set_connected(True, serial)
        self._redraw_timer.start()

    def on_labjack_disconnected(self):
        self._redraw_timer.stop()
        self.lj_panel.set_connected(False)

    def on_profile_changed(self, profile_name: str):
        """Sync the UI to a profile that is now live on the hardware.

        Called by MainWindow after a switch completes, and by us during Apply.
        """
        idx = list(STREAM_PROFILES.keys()).index(profile_name)
        self._profile_combo.blockSignals(True)
        self._profile_combo.setCurrentIndex(idx)
        self._profile_combo.blockSignals(False)

        self._single_mode = is_single_channel(profile_name)
        self._single_combo.setEnabled(self._single_mode)

        rate = STREAM_PROFILES[profile_name]["per_channel_rate_hz"]
        res  = resolution_index(profile_name)
        if self._single_mode:
            self._single_target_ain = self._single_combo.currentData()
            amp, kind = _AIN_TO_AMP[self._single_target_ain]
            self._profile_status.setText(
                f"{rate / 1000:.1f} kS/s  |  res idx {res}  |  "
                f"target {amp} {kind} ({self._single_target_ain})  |  "
                f"window {window_samples(profile_name)} pts"
            )
        else:
            self._profile_status.setText(
                f"{rate / 1000:.1f} kS/s/ch  |  res idx {res}  |  "
                f"window {window_samples(profile_name)} pts"
            )

        # This profile/target is now the live one; clear any pending Apply state.
        self._applied_profile = profile_name
        self._applied_channel = self._single_combo.currentData()
        self._refresh_apply_state()

        self._apply_paused_styling()
        self._update_history_layout()

    def _apply_paused_styling(self):
        """Grey out the numeric readouts of any monitor not streaming live.

        In single-channel mode only the target's voltage OR current readout is
        live; the rest are visually marked paused so their last value is not
        mistaken for a current reading.  In multi-channel mode nothing is muted
        (every monitor updates each window).
        """
        muted   = "color: #bbb; font-weight: bold;"
        neutral = "color: #555; font-weight: bold;"
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            v_live = (not self._single_mode) or v_ain == self._single_target_ain
            i_live = (not self._single_mode) or i_ain == self._single_target_ain
            if not v_live:
                for lbl in (self.lbl_kv[amp], self.lbl_pp[amp], self.lbl_rms[amp]):
                    lbl.setStyleSheet(muted)
                    lbl.setText("—")
            else:
                for lbl in (self.lbl_kv[amp], self.lbl_pp[amp], self.lbl_rms[amp]):
                    lbl.setStyleSheet(neutral)
            if not i_live:
                self.lbl_ma[amp].setStyleSheet(muted)
                self.lbl_ma[amp].setText("—")
            else:
                self.lbl_ma[amp].setStyleSheet(neutral)

    def _update_history_layout(self):
        """Show only the relevant subplot in single-channel mode."""
        # Normalized figure coords: [left, bottom, width, height]
        _FULL = [0.10, 0.11, 0.86, 0.80]
        _TOP  = [0.10, 0.54, 0.86, 0.40]
        _BOT  = [0.10, 0.11, 0.86, 0.38]

        if self._single_mode:
            _, kind = _AIN_TO_AMP.get(self._single_target_ain, ("", "voltage"))
            is_voltage = (kind == "voltage")
            self.ax_v.set_visible(is_voltage)
            self.ax_i.set_visible(not is_voltage)
            if is_voltage:
                self.ax_v.tick_params(labelbottom=True)
                self.ax_v.set_xlabel("Time (s, relative to window right edge)")
                self.ax_v.set_position(_FULL)
            else:
                self.ax_i.set_position(_FULL)
        else:
            self.ax_v.set_visible(True)
            self.ax_i.set_visible(True)
            self.ax_v.tick_params(labelbottom=False)
            self.ax_v.set_xlabel("")
            self.ax_v.set_position(_TOP)
            self.ax_i.set_position(_BOT)
        self.canvas.draw_idle()

    # ---- Profile / target staging + Apply ------------------------------------

    def _on_selection_staged(self, *_):
        """A combo changed — stage it and light up Apply if it differs from live.

        Nothing touches the hardware here.  The single-channel target combo is
        enabled whenever a single-channel profile is *staged*, so the user can
        pick the target before applying.
        """
        staged_profile = self._profile_combo.currentData()
        self._single_combo.setEnabled(is_single_channel(staged_profile))
        self._refresh_apply_state()

    def _refresh_apply_state(self):
        """Enable/highlight Apply iff the staged selection differs from live."""
        staged_profile = self._profile_combo.currentData()
        staged_channel = self._single_combo.currentData()
        pending = (staged_profile != self._applied_profile) or (
            is_single_channel(staged_profile)
            and staged_channel != self._applied_channel
        )
        self._apply_btn.setEnabled(pending)
        if pending:
            self._apply_btn.setStyleSheet(
                "QPushButton { background:#c47a00; color:white; font-weight:bold;"
                " padding:2px 10px; }"
                "QPushButton:hover { background:#d98c00; }"
            )
        else:
            self._apply_btn.setStyleSheet("")

    def _apply_stream_settings(self):
        """Commit the staged profile/target to the hardware (one atomic action).

        The channel is emitted before the profile so that a switch INTO a
        single-channel profile starts directly on the chosen target — a single
        stream restart instead of two.
        """
        staged_profile = self._profile_combo.currentData()
        staged_channel = self._single_combo.currentData()
        if staged_profile is None:
            return

        if (is_single_channel(staged_profile) and staged_channel
                and staged_channel != self._applied_channel):
            self.single_channel_change_requested.emit(staged_channel)
        if staged_profile != self._applied_profile:
            self.profile_change_requested.emit(staged_profile)

        # Update our own UI immediately.  When connected, MainWindow also calls
        # on_profile_changed after the restart; both are idempotent.
        self.on_profile_changed(staged_profile)

    def _on_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)

    # ---- Window ingestion ----------------------------------------------------

    def _on_window(self, payload: dict):
        """Consume one stream window from LabJackStreamWorker.

        Feeds three things per amplifier:
          * numeric readouts (peak/pk-pk/RMS scalars),
          * the 10 Hz trend buffers (peak-kV / RMS-mA),
          * the raw-waveform ring (full window, in kV / mA) for snapshot mode.
        """
        channels = payload["channels"]
        t = payload["t"]

        # Voltage and current are handled independently: in single-channel mode
        # only ONE of the two AINs for one amplifier is present, so requiring
        # both would blank the display.
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            v_ch  = channels.get(v_ain)
            i_ch  = channels.get(i_ain)

            if v_ch is not None:
                kv_peak = monitor_to_kv(v_ch["peak"])
                kv_pkpk = v_ch["pk_pk"] * SC.VOLTAGE_MONITOR_KV_PER_VOLT
                kv_rms  = monitor_to_kv(v_ch["rms"])
                self.buffers[v_ain].append(t, kv_peak)

                vstatus = voltage_status(kv_peak)
                self.lbl_kv[amp].setText(format_kv(kv_peak))
                self.lbl_kv[amp].setStyleSheet(
                    f"color: {_STATUS_COLOR[vstatus]}; font-weight: bold;"
                )
                self.lbl_pp[amp].setText(format_kv(kv_pkpk))
                self.lbl_pp[amp].setStyleSheet("color: #444; font-weight: bold;")
                self.lbl_rms[amp].setText(format_kv(kv_rms))
                self.lbl_rms[amp].setStyleSheet("color: #444; font-weight: bold;")

                wave = v_ch.get("waveform")
                if wave is not None:
                    self._store_wave_chunk(
                        v_ain, t, np.asarray(wave) * SC.VOLTAGE_MONITOR_KV_PER_VOLT
                    )

            if i_ch is not None:
                ma_rms = monitor_to_ma(i_ch["rms"])
                self.buffers[i_ain].append(t, ma_rms)

                istatus = current_status(ma_rms)
                self.lbl_ma[amp].setText(format_ma(ma_rms))
                self.lbl_ma[amp].setStyleSheet(
                    f"color: {_STATUS_COLOR[istatus]}; font-weight: bold;"
                )

                wave = i_ch.get("waveform")
                if wave is not None:
                    self._store_wave_chunk(
                        i_ain, t, np.asarray(wave) * SC.CURRENT_MONITOR_MA_PER_VOLT
                    )

        if self._is_live:
            self.slider.blockSignals(True)
            self.slider.setValue(10_000)
            self.slider.blockSignals(False)

    def _store_wave_chunk(self, ain: str, t_end: float, values: np.ndarray):
        """Append a raw window to the ring and drop chunks older than the ring."""
        dq = self._wave_chunks[ain]
        dq.append((t_end, values))
        cutoff = t_end - (self.SNAPSHOT_MAX_SECONDS + self.WINDOW_DURATION_S)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _on_reading(self, t: float, values: dict):
        """Legacy command-response path (AIN6..AIN13 only).

        Kept for the standalone smoke test / non-stream callers.  It fills the
        trend buffers only; snapshot mode requires the stream's waveform arrays.
        """
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

    # ---- Slider / navigation -------------------------------------------------

    def _on_slider_changed(self, val: int):
        if val >= 9_800:
            self._enter_live_mode()
        else:
            self._enter_frozen_mode(val)

    def _is_snapshot(self) -> bool:
        """True when the window is narrow enough to show the raw waveform."""
        return self._window_seconds < self.SNAPSHOT_MAX_SECONDS

    def _window_label(self) -> str:
        ws = self._window_seconds
        if ws < 1.0:
            body = f"{ws * 1000:.3g} ms"
        else:
            ws_int = int(ws)
            if ws_int < 60:
                body = f"{ws_int} s"
            else:
                m, s = divmod(ws_int, 60)
                body = f"{m} m" if s == 0 else f"{m} m {s} s"
        return f"{body} · waveform" if self._is_snapshot() else body

    def _enter_live_mode(self):
        self._is_live = True
        self._frozen_right_edge = None
        self.lbl_mode.setText(f"● LIVE  ({self._window_label()})")
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
        self._update_frozen_label()
        self.btn_jump_live.setVisible(True)

    def _update_frozen_label(self):
        w_start = self._frozen_right_edge - self._window_seconds
        self.lbl_mode.setText(
            f"⏸  Frozen  —  [{w_start:+.3g} s … {self._frozen_right_edge:+.3g} s]"
            f"  ({self._window_label()})"
        )
        self.lbl_mode.setStyleSheet(
            "color: #8c6000; font-weight: bold; padding: 2px 6px;"
        )

    def _jump_to_live(self):
        self.slider.setValue(10_000)
        self._enter_live_mode()

    # ---- Zoom ----------------------------------------------------------------

    def _zoom_in(self):
        # Snap to the next smaller preset step (largest step < current).
        for step in _ZOOM_STEPS:          # list is descending
            if step < self._window_seconds - 1e-9:
                self._window_seconds = step
                break
        self._after_zoom()

    def _zoom_out(self):
        # Snap to the next larger preset step (smallest step > current).
        for step in reversed(_ZOOM_STEPS):   # ascending
            if step > self._window_seconds + 1e-9:
                self._window_seconds = step
                break
        self._after_zoom()

    def _on_scroll(self, event):
        if event.button == 'up':
            self._zoom_in()
        elif event.button == 'down':
            self._zoom_out()

    def _after_zoom(self):
        if self._is_live:
            self.lbl_mode.setText(f"● LIVE  ({self._window_label()})")
        elif self._frozen_right_edge is not None:
            self._update_frozen_label()

    # ---- Plot redraw ---------------------------------------------------------

    def _redraw_plot(self):
        """Dispatch to the trend or waveform-snapshot renderer for this window."""
        if self._is_snapshot():
            self._set_plot_mode("snapshot")
            self._redraw_snapshot()
        else:
            self._set_plot_mode("trend")
            self._redraw_trend()

    def _set_plot_mode(self, mode: str):
        """Update x-axis labelling once when crossing the trend/snapshot boundary."""
        if mode == self._plot_mode:
            return
        self._plot_mode = mode
        xlabel = ("Time (s, waveform — relative to right edge)"
                  if mode == "snapshot"
                  else "Time (s, relative to window right edge)")
        bottom_ax = self.ax_i if self.ax_i.get_visible() else self.ax_v
        bottom_ax.set_xlabel(xlabel)

    def _redraw_trend(self):
        """10 Hz peak/RMS history — the drift / fault view (wide windows)."""
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

        t_left = t_right - self._window_seconds

        for amp in SC.AMP_LABELS:
            for ain, line in (
                (SC.AMP_CHANNEL_MAP[amp]["voltage"], self._lines_v[amp]),
                (SC.AMP_CHANNEL_MAP[amp]["current"], self._lines_i[amp]),
            ):
                if self._single_mode and ain != self._single_target_ain:
                    line.set_data([], [])
                    continue
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
            self.ax_v.set_xlim(-self._window_seconds, 0)
            for ax in (self.ax_v, self.ax_i):
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
            self.canvas.draw_idle()

    def _redraw_snapshot(self):
        """Raw high-rate waveform over the last window_seconds — the scope view."""
        if self._is_live:
            t_right = self._latest_wave_t()
        else:
            t_right = self._frozen_right_edge
        if t_right is None:
            return

        t_left = t_right - self._window_seconds
        any_data = False

        for amp in SC.AMP_LABELS:
            for ain, line in (
                (SC.AMP_CHANNEL_MAP[amp]["voltage"], self._lines_v[amp]),
                (SC.AMP_CHANNEL_MAP[amp]["current"], self._lines_i[amp]),
            ):
                if self._single_mode and ain != self._single_target_ain:
                    line.set_data([], [])
                    continue
                series = self._snapshot_series(ain, t_left, t_right)
                if series is None:
                    line.set_data([], [])
                    continue
                tt, vv = series
                tt, vv = self._decimate_minmax(tt, vv, self.WF_MAX_POINTS)
                line.set_data(tt - t_right, vv)
                any_data = True

        if any_data:
            self.ax_v.set_xlim(-self._window_seconds, 0)
            for ax in (self.ax_v, self.ax_i):
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
            self.canvas.draw_idle()

    def _latest_wave_t(self):
        """Most recent raw-window end time across all amp channels (or None)."""
        best = None
        for dq in self._wave_chunks.values():
            if dq:
                te = dq[-1][0]
                if best is None or te > best:
                    best = te
        return best

    def _snapshot_series(self, ain: str, t_left: float, t_right: float):
        """Concatenated (times, values) of raw samples in [t_left, t_right].

        Each stored chunk spans WINDOW_DURATION_S ending at its t_end; sample
        times are reconstructed on demand so the ring only holds the values.
        Returns None if nothing falls in the window.
        """
        dq = self._wave_chunks.get(ain)
        if not dq:
            return None
        ts, vs = [], []
        for t_end, vals in dq:
            n = len(vals)
            if n == 0:
                continue
            t_start = t_end - self.WINDOW_DURATION_S
            # Sample-centre times across the window.
            tt = t_start + (np.arange(n) + 0.5) * (self.WINDOW_DURATION_S / n)
            m = (tt >= t_left) & (tt <= t_right)
            if m.any():
                ts.append(tt[m])
                vs.append(vals[m])
        if not ts:
            return None
        return np.concatenate(ts), np.concatenate(vs)

    @staticmethod
    def _decimate_minmax(x: np.ndarray, y: np.ndarray, max_points: int):
        """Envelope-preserving decimation: bin the series and keep min+max/bin.

        Plain striding would drop transient peaks between samples; min/max
        binning keeps the visible envelope while capping the point count so a
        100 kS/s window redraws cheaply.
        """
        n = len(x)
        if n <= max_points:
            return x, y
        bins = max(1, max_points // 2)
        usable = (n // bins) * bins
        if usable < bins:
            return x, y
        xb = x[:usable].reshape(bins, -1)
        yb = y[:usable].reshape(bins, -1)
        x_out = np.repeat(xb[:, 0], 2)
        y_out = np.empty(bins * 2, dtype=float)
        y_out[0::2] = yb.min(axis=1)
        y_out[1::2] = yb.max(axis=1)
        return x_out, y_out

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
        "AIN4": 0.0, "AIN5": 0.0,                              # spare (ignored)
        "AIN13": 3.0, "AIN12":  1.0,    # X+ : 3 kV, 10 mA
        "AIN11": -3.0, "AIN10": 1.0,    # X- : -3 kV, 10 mA
        "AIN9": 2.0, "AIN8":  0.5,      # Y+ : 2 kV, 5 mA
        "AIN7": -2.0, "AIN6": 0.5,      # Y- : -2 kV, 5 mA
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
