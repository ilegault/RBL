"""
amp_tab.py
PySide6 widget for the "HV Amplifiers" outer tab.

Displays the VOLTAGE MONITOR and CURRENT MONITOR readings of four
EEL5000.20.100 high-voltage amplifiers (X+, X-, Y+, Y-), wired to a LabJack T7
via a CB37 terminal board on AIN6..AIN13.

This tab does NOT own a LabJack connection and does NOT scale a monitor
voltage itself. Beamline owns the single shared LabJackT7 + stream worker and
converts each window once (rbl/state/labjack_link.py); this tab renders the
AmpState that falls out — the AIN6..AIN13 half of it. The log amps (AIN0..3)
in the same window reach the Beam Current tab as a LogAmpState and are not
this tab's business.

ONE plot, two viewing modes (driven by the time-window zoom)
-----------------------------------------------------------
There is a single matplotlib figure (voltage over current).  The time-window
control seamlessly changes WHAT it shows:

  * TREND mode   (window >  SNAPSHOT_MAX_SECONDS, i.e. above 1 s):
        One point per stream window (10 Hz) of RMS-kV / RMS-mA, held in the
        rolling history buffers.  RMS (not peak) is the summary statistic here
        so the wide-window envelope reads as a clean, stable line rather than a
        jagged peak trace.  This is the DC-bias / drift / fault view.

  * SNAPSHOT mode (window <= SNAPSHOT_MAX_SECONDS, i.e. 1 s and below):
        The actual high-rate waveform samples from the most recent windows,
        drawn on the SAME axes and lines.  Zooming the window down to a few ms
        finally lets you see the deflection waveform itself — the 10 Hz trend
        can never resolve it.  Above 1 s the raw ring no longer covers the view,
        so RMS takes over.

There is deliberately NO second waveform plot: the one figure switches modes.

Vertical (voltage) scale
------------------------
The voltage axis does NOT auto-center on the data (that made a small ripple on
a real DC offset look centered on zero and hid the true operating voltage).
Instead it defaults to the full +/-5 kV rating envelope and is manually
scalable: the ＋/－ "Volts" buttons zoom it and a vertical click-drag on the
plot pans it — the vertical analogue of the scroll-wheel time zoom.  A mirrored
kV axis is drawn on the right-hand side for readability.

Applying stream settings
------------------------
The profile and single-channel target combos only STAGE a selection; nothing
touches the hardware until "Apply" is pressed.  Each profile/target change is a
full eStreamStop -> reconfigure -> eStreamStart cycle on the T7, so committing
them one deliberate click at a time avoids the churn (and transient glitches) of
restarting the stream on every stray combo event.
"""
import math
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rbl.config import hardware_config as SC
from rbl.config.calibration_config import CAL_UNCERTAINTY_V
from rbl.config.labjack_stream_config import (
    DEFAULT_AMP_PAIR,
    DEFAULT_SINGLE_CHANNEL,
    GUI_REFRESH_HZ,
    STREAM_PROFILES,
    is_pair_channel,
    is_single_channel,
    pair_choices,
    resolution_index,
    window_samples,
)
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import LabJackPanel
from rbl.gui.widgets.inputs import NoScrollComboBox
from rbl.gui.widgets.live_plot import LivePlotPanel
from rbl.hardware.ac_metrics import fundamental
from rbl.hardware.amp_monitor import current_status, voltage_status
from rbl.hardware.current_monitor import RollingBuffer
from rbl.hardware.funcgen_safety import _AMP_GAIN, CHANNEL_ROLE
from rbl.hardware.labjack_driver import LJM_AVAILABLE
from rbl.hardware.waveform_ring import WaveformRing, decimate_minmax
from rbl.state.snapshots import AmpState

# Status -> stylesheet color
_STATUS_COLOR = {
    "ok":   theme.OK,     # nominal
    "peak": theme.WARN,   # legal only as a <4 ms transient
    "over": theme.FAULT,  # out of spec / bad reading
}


# DG1022Z shape strings -> short labels for the Commanded row. The generator
# reports a ramp as "RAMP,<symmetry>,..." so only the head is matched.
_SHAPE_LABEL = {
    "SIN": "SINE", "SINE": "SINE",
    "SQU": "SQR",  "SQUARE": "SQR",
    "RAMP": "TRI", "TRI": "TRI", "TRIANGLE": "TRI",
    "DC": "DC",
    "PULS": "PULSE", "NOIS": "NOISE",
}


def _fmt_hz(freq_hz: float) -> str:
    """Compact frequency for a narrow table cell: 250 Hz, 1.50 kHz, 12.5 kHz."""
    if not math.isfinite(freq_hz) or freq_hz <= 0:
        return "—"
    if freq_hz < 1000:
        return f"{freq_hz:.0f} Hz"
    return f"{freq_hz / 1000:.2f} kHz".replace(".00 ", " ")


class AmpTab(QWidget):
    """The 'HV Amplifiers' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz (trend history)
    WINDOW_SECONDS  = 120      # default 2-minute viewport (trend mode)

    # At or below this window width the single plot renders the raw high-rate
    # waveform (snapshot mode); above it the 10 Hz RMS trend takes over.  1 s is
    # the hand-off: the raw ring holds ~1 s of samples, enough to fill a 1 s view
    # at full fidelity, and beyond that RMS is the honest summary.  Keeping the
    # ring this short (vs. the old 2 s) also keeps each snapshot redraw cheap,
    # which is what removes the sub-second scrolling lag.
    SNAPSHOT_MAX_SECONDS = 1.0

    # Grid rows of the Live Amplifier Monitors table. Named so the build and
    # the update path cannot drift apart, and so the order is changed in one
    # place. Row 0 is the per-amplifier header.
    R_MODE, R_CMD, R_MEAS, R_DELTA, R_PKPK, R_CUR, R_CURRMS = 1, 2, 3, 4, 5, 6, 7

    # --- Safety-freeze sizing (see freeze_on_safety_event) --------------------
    #
    # Windows admitted after a freeze is requested. Beamline emits
    # raw_window_ready (-> calibration runner -> interlock -> freeze) BEFORE
    # ingest_labjack_window (-> this tab), so the window holding the
    # over-current is always still in flight when the freeze arrives. 2 admits
    # that one plus the one after it, which costs 100 ms of post-trip trace and
    # buys confirmation that the current actually came down.
    SAFETY_ADMIT_WINDOWS = 2

    # Minimum viewport a safety freeze opens out to. The trip happens somewhere
    # inside a 100 ms stream window, but the frozen right edge can only sit at
    # that window's newest sample — so at a 1 ms view the visible slice is the
    # last 1 ms and the excursion is off-screen to the left. That is why a
    # freeze at a narrow window rendered an apparently empty frame. 0.25 s
    # guarantees both admitted windows are fully visible, and zoom still works
    # normally from there.
    SAFETY_MIN_WINDOW_S = 0.25

    # Vertical (voltage / current) zoom step per ＋/－ click.  Matches the time
    # axis, which halves/doubles the window: <1 zooms in, its reciprocal zooms
    # out about the current centre.
    V_ZOOM_FACTOR = 0.5

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

    # (profile_name, amp_label) — acquire a pair profile and its target in ONE
    # stream restart. See Beamline.set_stream_pair_profile for why the
    # two-request version could not be made reliable.
    pair_profile_requested = Signal(str, str)

    def __init__(self, beamline=None, parent=None):
        super().__init__(parent)
        self.beamline = beamline
        self._t0 = time.monotonic()   # reset on labjack_connected

        # The redraw timer only needs to run when BOTH hold: connected (data
        # is arriving) and visible (this tab is the one on screen). Buffers
        # keep filling in the background either way — only painting pauses.
        self._connected = False
        self._visible   = False

        # One trend buffer per AIN. Keys are AIN names so on_amp_state can index directly.
        self.buffers = {ain: RollingBuffer(self.BUFFER_CAPACITY)
                        for ain in SC.AMP_AIN_NAMES}

        # Raw-waveform ring, holding recent (t_end, values) window chunks per
        # AIN (values already in kV / mA).  Feeds snapshot mode.
        self.wave_ring = WaveformRing(
            SC.AMP_AIN_NAMES,
            keep_seconds=self.SNAPSHOT_MAX_SECONDS,
            window_duration_s=self.WINDOW_DURATION_S,
        )

        # Plot state (LIVE/FROZEN + window_seconds live on self.plot, built below)
        self._plot_mode         = "trend"   # "trend" | "snapshot"
        self._paused            = False     # user-toggled waveform freeze
        # Set when a safety interlock trips (see freeze_on_safety_event).
        # Distinct from _paused: _paused stops the REDRAW, this stops the
        # INGEST.  Freezing the view alone would not preserve anything — the
        # trend buffers and waveform ring keep overwriting themselves, so
        # within a second of the trip the current excursion that caused it has
        # already scrolled out of the history the operator wants to read.
        self._safety_frozen     = False
        self._safety_reason     = ""
        # Windows still to be admitted after a freeze is requested.  Beamline
        # emits raw_window_ready (which reaches the calibration runner, and so
        # trips the interlock) BEFORE ingest_labjack_window (which reaches this
        # tab), so at the instant freeze_on_safety_event is called the window
        # that actually contains the over-current has not been ingested yet.
        # Blocking immediately therefore threw away the single window the
        # operator most needs.  This lets the in-flight one through, then shuts.
        self._safety_admit_left = 0
        # Funcgen readback (amp label -> ChannelSnapshot) and the latest
        # measured channels, both cached so the Commanded / Δ rows can repaint
        # from whichever of the two arrives second.
        self._cmd: dict = {}
        self._last_channels: dict = {}
        # Per-sample interval of the last window, needed to convert raw samples
        # into a fundamental-frequency amplitude. None until the first window.
        self._sample_period = None

        # Vertical scale state.  The plot never auto-centers the voltage axis;
        # it holds these limits and the user zooms/pans them.  Voltage defaults
        # to the full +/-5 kV rating envelope; current to its +/-20 mA DC rating.
        self._ylim_v = [-SC.AMP_MAX_KV, SC.AMP_MAX_KV]
        self._ylim_i = [-SC.AMP_MAX_MA_DC, SC.AMP_MAX_MA_DC]

        # Active vertical click-drag pan (None when not dragging).
        self._pan = None

        # Single-channel mode state.  When a single-channel profile is active,
        # only self._single_target_ain streams live; the other seven monitors
        # (and all log amps) are paused.
        self._single_mode        = False
        self._single_target_ain  = DEFAULT_SINGLE_CHANNEL

        # What the hardware is CURRENTLY running (vs. the staged combo choices).
        # Apply is enabled only when a staged choice differs from these.
        self._applied_profile   = "FULL"
        self._applied_channel   = DEFAULT_SINGLE_CHANNEL
        self._applied_pair      = DEFAULT_AMP_PAIR
        # Staged targets are held per KIND. One combo serves both, so without
        # this, staging AMP_PAIR and back would lose the single-channel choice.
        self._staged_profile    = None
        self._staged_channel    = DEFAULT_SINGLE_CHANNEL
        self._staged_pair       = DEFAULT_AMP_PAIR

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Connection panel (added to top_row below) ─────────────────────
        self.lj_panel = LabJackPanel()

        ro_box   = self._build_monitors_box()
        prof_box = self._build_profile_box()

        # ── Assemble upper section: left col (connection + profile) | right (monitors) ──
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addWidget(self.lj_panel)
        left_col.addWidget(prof_box)
        left_col.addStretch()

        # Right side: the monitors table, now the only box here.
        monitors_row = QHBoxLayout()
        monitors_row.setSpacing(8)
        monitors_row.addWidget(ro_box)

        upper_row = QHBoxLayout()
        upper_row.setSpacing(8)
        upper_row.addLayout(left_col)
        upper_row.addLayout(monitors_row, stretch=1)
        layout.addLayout(upper_row)

        plot_box = self._build_plot_box()
        layout.addWidget(plot_box, stretch=1)

        # Lay the subplots out with room on the right for the mirrored kV axis.
        self._update_history_layout()

        if not LJM_AVAILABLE:
            self.lj_panel.set_enabled(False)


    # ---- Builder helpers (called once from __init__) -------------------

    def _build_monitors_box(self) -> QGroupBox:
        """Build the 'Live Amplifier Monitors' commanded-vs-measured grid."""
        # ── Per-amplifier numeric readouts ────────────────────────────────
        #
        # Organised around COMMANDED vs MEASURED, because that is the question
        # this screen exists to answer: is each amplifier doing what it was
        # told.  The previous layout listed four unconditional statistics
        # (peak kV, pk-pk kV, RMS kV, RMS mA) with nothing to compare them
        # against, and half of them were meaningless in whichever mode was
        # running — RMS of a DC hold is just noise, and the mean of a
        # zero-centred AC drive is ~0 no matter how hard the amp is working.
        #
        # AMPLITUDE CONVENTION, FIXED THROUGHOUT THIS FILE:
        #   "pk"    = peak amplitude = half of pk-pk, for a zero-offset
        #             waveform.  Always a MAGNITUDE, never signed.
        #   "pk-pk" = full excursion, max - min.  Always positive.
        #   DC rows show a signed LEVEL and say "DC", never "pk".
        # The generator speaks Vpp natively, the amplifier rating is quoted in
        # pk, and mixing the two silently is a factor-of-two error, so every
        # label here states which one it is.
        ro_box = QGroupBox("Live Amplifier Monitors — commanded vs measured")
        ro_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        ro_box.setMinimumWidth(0)
        ro = QGridLayout(ro_box)
        ro.setHorizontalSpacing(10)
        ro.setVerticalSpacing(2)
        ro.setContentsMargins(8, 6, 8, 6)

        mono  = QFont("Consolas", 12)
        mono.setBold(True)
        small = QFont("Consolas", 9)
        tiny  = QFont("Consolas", 8)

        ro.addWidget(QLabel(""), 0, 0)

        def _make_hdr(text, tip=""):
            lbl = QLabel(text)
            lbl.setFont(small)
            lbl.setStyleSheet("color: #666;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if tip:
                lbl.setToolTip(tip)
            return lbl

        # Row set is FIXED. Mode changes alter the text and colour in these
        # rows, never which rows exist — so switching between DC and AC, or
        # between sessions, cannot make the table jump around under the cursor.
        ro.addWidget(_make_hdr("Mode", "Waveform and frequency read back from the generator."),
                     self.R_MODE, 0)
        ro.addWidget(_make_hdr("Commanded", "AC: peak amplitude (half of the generator's Vpp).\n"
                                            "DC: the held level, signed."), self.R_CMD, 0)
        ro.addWidget(_make_hdr("Measured", "AC: pk-pk / 2, so it is sign-free and directly\n"
                                           "comparable with the commanded peak.\n"
                                           "DC: window mean, signed."), self.R_MEAS, 0)
        ro.addWidget(_make_hdr("Δ", "Measured minus commanded. Green inside the\n"
                                    f"±{CAL_UNCERTAINTY_V:.0f} V uncertainty budget."),
                     self.R_DELTA, 0)
        ro.addWidget(_make_hdr("Output pk-pk", "Full measured excursion, max - min."),
                     self.R_PKPK, 0)
        ro.addWidget(_make_hdr("Current", "AC: amplitude at the drive frequency (noise-rejected).\n"
                                          "DC: window mean current."), self.R_CUR, 0)
        ro.addWidget(_make_hdr("Current RMS", "True RMS about zero — the heating-relevant figure."),
                     self.R_CURRMS, 0)

        self.lbl_mode_c = {}   # commanded waveform + frequency
        self.lbl_cmd    = {}   # commanded amplitude (pk) or DC level
        self.lbl_meas   = {}   # measured amplitude (pk) or DC level
        self.lbl_dev    = {}   # measured - commanded
        self.lbl_pp     = {}   # measured pk-pk
        self.lbl_cur    = {}   # fundamental (AC) or mean (DC) current
        self.lbl_currms = {}   # RMS current

        # Fixed, generous column widths. The overlap in the old layout came
        # from four value columns with no minimum sharing whatever width was
        # left over: a long string in one column pushed into its neighbour
        # instead of widening the box. Reserving the width up front, and giving
        # every value column equal stretch, means text can never collide.
        # 148 px fits the widest cell the table can produce — "3.071 mA pk@f"
        # at 12 pt Consolas is ~123 px — with margin for a longer number.
        ro.setColumnMinimumWidth(0, 96)
        ro.setColumnStretch(0, 0)
        for col in range(1, len(SC.AMP_LABELS) + 1):
            ro.setColumnMinimumWidth(col, 148)
            ro.setColumnStretch(col, 1)

        for col, amp in enumerate(SC.AMP_LABELS, start=1):
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]

            hdr = QLabel(f"{amp}")
            hdr.setStyleSheet(
                f"color: {theme.SLIT_COLORS[amp]}; font-weight: bold; font-size: 14px;"
            )
            hdr.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            sub = QLabel(f"{v_ain}/{i_ain}")
            sub.setFont(tiny)
            sub.setStyleSheet("color: #888;")
            sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hdr_w = QWidget()
            hdr_box = QVBoxLayout(hdr_w)
            hdr_box.setContentsMargins(0, 0, 0, 0)
            hdr_box.setSpacing(0)
            hdr_box.addWidget(hdr)
            hdr_box.addWidget(sub)
            ro.addWidget(hdr_w, 0, col)

            for row, attr, font in (
                (self.R_MODE,   "lbl_mode_c", small),
                (self.R_CMD,    "lbl_cmd",    mono),
                (self.R_MEAS,   "lbl_meas",   mono),
                (self.R_DELTA,  "lbl_dev",    small),
                (self.R_PKPK,   "lbl_pp",     small),
                (self.R_CUR,    "lbl_cur",    mono),
                (self.R_CURRMS, "lbl_currms", small),
            ):
                lbl = QLabel("—")
                lbl.setFont(font)
                lbl.setStyleSheet("color: #555;")
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
                # Values are right-aligned and never wrap: a wrapped number
                # changes the row height and drags the whole grid with it.
                lbl.setWordWrap(False)
                lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Fixed)
                getattr(self, attr)[amp] = lbl
                ro.addWidget(lbl, row, col)

        # The "Raw Analog Inputs (V, unscaled)" box that used to sit here was
        # removed: it showed each AIN's window mean in raw monitor volts, which
        # is the same number the table above already reports in kV and mA after
        # applying the monitor ratios. The AIN each amplifier uses is on the
        # column header, so the wiring context it also carried is still present.
        #
        return ro_box

    def _build_profile_box(self) -> QGroupBox:
        """Build the 'Stream Profile' mode/target/apply selector group."""
        # ── Profile selector (placed right of lj_panel in top_row below) ────
        prof_box = QGroupBox("Stream Profile")
        prof_col = QVBoxLayout(prof_box)
        prof_col.setSpacing(4)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._profile_combo = NoScrollComboBox()
        for pname, pdata in STREAM_PROFILES.items():
            self._profile_combo.addItem(pdata["description"], userData=pname)
        self._profile_combo.setCurrentIndex(
            list(STREAM_PROFILES.keys()).index("FULL")
        )
        self._profile_combo.currentIndexChanged.connect(self._on_selection_staged)
        mode_row.addWidget(self._profile_combo, stretch=1)
        prof_col.addLayout(mode_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target:"))
        # ONE target combo, repopulated for whichever profile is staged.
        # Single-channel profiles target an AIN ("AIN8"); pair profiles target
        # an AMP LABEL ("Y+") because a pair's two AINs always travel together
        # and exposing them separately would only allow an incoherent scan
        # list. Two different kinds of thing, so the combo is rebuilt rather
        # than trying to hold both at once — and multi-channel profiles have no
        # target at all, which is why it can also be empty and disabled.
        self._single_combo = NoScrollComboBox()
        self._single_combo.setEnabled(False)
        self._single_combo.currentIndexChanged.connect(self._on_selection_staged)
        target_row.addWidget(self._single_combo, stretch=1)

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
        target_row.addWidget(self._apply_btn)
        prof_col.addLayout(target_row)

        self._profile_status = QLabel("")
        self._profile_status.setStyleSheet("color: #555; font-style: italic; font-size: 10px;")
        prof_col.addWidget(self._profile_status)

        # Populate the target combo for the initially-selected profile. The
        # profile combo's index was set before its signal was connected (so
        # construction does not fire a staging callback), which means nothing
        # has built the target list yet — without this it starts empty and
        # stays empty until the first profile change.
        self._staged_profile = self._profile_combo.currentData()
        self._populate_target_combo(self._staged_profile)
        self._on_selection_staged()

        return prof_box

    def _build_plot_box(self) -> QGroupBox:
        """Build the 'Amplifier History' plot group.

        Sets self.plot, self.fig, self.canvas, self.ax_v, self.ax_i,
        self.ax_v_right, self.ax_i_right, self._lines_v, self._lines_i,
        self.lbl_mode, self.btn_jump_live, self._btn_pause.
        """
        # ── History / waveform plot (one figure, two modes) ─────────────────
        # Shared history slider + LIVE/FROZEN state machine + zoom-step list.
        # The nav row stays local (see module docstring: amp_tab interleaves
        # its own vertical zoom controls and orders its time-zoom buttons the
        # other way round from the log-amp tab, so it is not built here).
        self.plot = LivePlotPanel(
            window_seconds=self.WINDOW_SECONDS,
            live_edge_provider=self._live_edge,
            span_provider=self._buffer_span,
            min_window_seconds=None,   # amp_tab zooms into the ms waveform range
            figsize=(7, 6),
            redraw_interval_ms=100,
        )
        self.plot.navigation_changed.connect(self._on_navigation_changed)
        self.plot.zoom_changed.connect(self._on_zoom_changed)
        self.plot.redraw_timer.timeout.connect(self._redraw_plot)

        plot_box = QGroupBox("Amplifier History")
        plot_box.setMinimumWidth(0)
        pv = QVBoxLayout(plot_box)

        nav_row = QHBoxLayout()
        self.lbl_mode = QLabel(f"● LIVE  ({int(self.plot.window_seconds)} s)")
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
        )
        nav_row.addWidget(self.lbl_mode)
        lbl_time = QLabel("Time:")
        lbl_time.setStyleSheet("color: #555; font-size: 10px; padding-left: 6px;")
        nav_row.addWidget(lbl_time)
        btn_zoom_in = QPushButton("＋")
        btn_zoom_in.setFixedWidth(28)
        btn_zoom_in.setToolTip("Zoom in — scroll wheel up (halve window). "
                               "At 1 s and below the plot shows the raw waveform.")
        btn_zoom_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_zoom_in.clicked.connect(self.plot.zoom_in)
        btn_zoom_out = QPushButton("－")
        btn_zoom_out.setFixedWidth(28)
        btn_zoom_out.setToolTip("Zoom out — scroll wheel down (double window)")
        btn_zoom_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_zoom_out.clicked.connect(self.plot.zoom_out)
        nav_row.addWidget(btn_zoom_in)
        nav_row.addWidget(btn_zoom_out)

        # Vertical (voltage) scale controls — the analogue of the time zoom.
        lbl_volts = QLabel("Volts:")
        lbl_volts.setStyleSheet("color: #555; font-size: 10px; padding-left: 10px;")
        nav_row.addWidget(lbl_volts)
        btn_vzoom_in = QPushButton("＋")
        btn_vzoom_in.setFixedWidth(28)
        btn_vzoom_in.setToolTip("Zoom in the vertical (voltage) scale about its centre.")
        btn_vzoom_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_vzoom_in.clicked.connect(self._v_zoom_in)
        btn_vzoom_out = QPushButton("－")
        btn_vzoom_out.setFixedWidth(28)
        btn_vzoom_out.setToolTip("Zoom out the vertical (voltage) scale about its centre.")
        btn_vzoom_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_vzoom_out.clicked.connect(self._v_zoom_out)
        btn_vreset = QPushButton("⤢")
        btn_vreset.setFixedWidth(28)
        btn_vreset.setToolTip("Reset the voltage axis to the full ±5 kV rating. "
                              "Drag the plot vertically to pan.")
        btn_vreset.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_vreset.clicked.connect(self._v_reset)
        nav_row.addWidget(btn_vzoom_in)
        nav_row.addWidget(btn_vzoom_out)
        nav_row.addWidget(btn_vreset)

        # Vertical (current) scale controls — independent of the voltage axis.
        lbl_amps = QLabel("Amps:")
        lbl_amps.setStyleSheet("color: #555; font-size: 10px; padding-left: 10px;")
        nav_row.addWidget(lbl_amps)
        btn_izoom_in = QPushButton("＋")
        btn_izoom_in.setFixedWidth(28)
        btn_izoom_in.setToolTip("Zoom in the vertical (current) scale about its centre.")
        btn_izoom_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_izoom_in.clicked.connect(self._i_zoom_in)
        btn_izoom_out = QPushButton("－")
        btn_izoom_out.setFixedWidth(28)
        btn_izoom_out.setToolTip("Zoom out the vertical (current) scale about its centre.")
        btn_izoom_out.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_izoom_out.clicked.connect(self._i_zoom_out)
        btn_ireset = QPushButton("⤢")
        btn_ireset.setFixedWidth(28)
        btn_ireset.setToolTip("Reset the current axis to the full ±20 mA rating.")
        btn_ireset.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_ireset.clicked.connect(self._i_reset)
        nav_row.addWidget(btn_izoom_in)
        nav_row.addWidget(btn_izoom_out)
        nav_row.addWidget(btn_ireset)
        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setToolTip("Pause / resume the waveform plot updates")
        self._btn_pause.setStyleSheet("padding: 2px 8px;")
        self._btn_pause.clicked.connect(self._toggle_pause)
        nav_row.addWidget(self._btn_pause)

        nav_row.addStretch()
        self.btn_jump_live = QPushButton("Jump to Live")
        self.btn_jump_live.setVisible(False)
        self.btn_jump_live.setStyleSheet(
            "QPushButton { background:#004e8c; color:white; font-weight:bold;"
            " padding:2px 8px; }"
            "QPushButton:hover { background:#0063b1; }"
        )
        # Routed through the tab, not straight to the panel: this button is
        # also how the operator releases a safety freeze, which has to restart
        # ingestion as well as move the view.
        self.btn_jump_live.clicked.connect(self._on_jump_live_clicked)
        nav_row.addWidget(self.btn_jump_live)
        pv.addLayout(nav_row)

        self.fig    = self.plot.fig
        self.canvas = self.plot.canvas
        self.canvas.setMinimumWidth(0)
        self.canvas.mpl_connect('scroll_event', self._on_scroll)
        # Vertical click-drag pans the voltage/current axis (time stays locked to
        # the window / history slider).
        self.canvas.mpl_connect('button_press_event',   self._on_press)
        self.canvas.mpl_connect('motion_notify_event',  self._on_motion)
        self.canvas.mpl_connect('button_release_event', self._on_release)

        self.ax_v = self.fig.add_subplot(211)
        self.ax_i = self.fig.add_subplot(212, sharex=self.ax_v)

        self.ax_v.set_ylabel("Output Voltage (kV)")
        self.ax_v.grid(True, alpha=0.3)
        self.ax_v.axhline(0.0, color="#999", lw=0.8, ls="-")
        # Rating envelope: +/-5 kV
        self.ax_v.axhline( SC.AMP_MAX_KV, color=theme.FAULT, lw=0.8, ls="--", alpha=0.5)
        self.ax_v.axhline(-SC.AMP_MAX_KV, color=theme.FAULT, lw=0.8, ls="--", alpha=0.5)
        self.ax_v.tick_params(labelbottom=False)

        self.ax_i.set_ylabel("Current Draw (mA)")
        self.ax_i.set_xlabel("Time (s, relative to window right edge)")
        self.ax_i.grid(True, alpha=0.3)
        self.ax_i.axhline(0.0, color="#999", lw=0.8, ls="-")
        # DC rating envelope: +/-20 mA
        self.ax_i.axhline( SC.AMP_MAX_MA_DC, color=theme.WARN, lw=0.8, ls="--", alpha=0.5)
        self.ax_i.axhline(-SC.AMP_MAX_MA_DC, color=theme.WARN, lw=0.8, ls="--", alpha=0.5)

        # Mirrored voltage axis on the right-hand side, requested for readability.
        # A secondary y-axis tracks ax_v's data limits automatically, so it
        # follows every vertical zoom/pan with no extra bookkeeping.
        self.ax_v_right = self.ax_v.secondary_yaxis("right")
        self.ax_v_right.set_ylabel("Output Voltage (kV)")

        # Mirrored current axis on the right-hand side — the same both-sides
        # readout the voltage plot has.  Tracks ax_i's data limits automatically,
        # so it follows every vertical zoom/pan of the current plot too.
        self.ax_i_right = self.ax_i.secondary_yaxis("right")
        self.ax_i_right.set_ylabel("Current Draw (mA)")

        # Establish the fixed default vertical scale up front (no auto-centering).
        self.ax_v.set_ylim(self._ylim_v)
        self.ax_i.set_ylim(self._ylim_i)

        # One line per amplifier per axis, keyed by amp label.  Reused by BOTH
        # trend and snapshot modes.
        self._lines_v = {}
        self._lines_i = {}
        for amp in SC.AMP_LABELS:
            c = theme.SLIT_COLORS[amp]
            lv, = self.ax_v.plot([], [], label=amp, color=c, lw=1.5)
            li, = self.ax_i.plot([], [], label=amp, color=c, lw=1.5)
            self._lines_v[amp] = lv
            self._lines_i[amp] = li

        self.fig.tight_layout()

        # Qt legend panel (right of canvas — sidesteps matplotlib secondary-axis
        # layout fighting when placing legends outside the axes)
        _amp_legend_w = QWidget()
        _amp_legend_w.setFixedWidth(65)
        _amp_leg_lay = QVBoxLayout(_amp_legend_w)
        _amp_leg_lay.setSpacing(3)
        _amp_leg_lay.setContentsMargins(4, 8, 4, 4)
        _alt = QLabel("Legend")
        _alt.setStyleSheet("font-size: 15px; color: #555; font-weight: bold;")
        _amp_leg_lay.addWidget(_alt)
        for _amp in SC.AMP_LABELS:
            _arow = QHBoxLayout()
            _aswatch = QLabel("━")
            _aswatch.setStyleSheet(
                f"color: {theme.SLIT_COLORS[_amp]}; font-weight: bold; font-size: 13px;"
            )
            _albl = QLabel(_amp)
            _albl.setStyleSheet("font-size: 15px;")
            _arow.addWidget(_aswatch)
            _arow.addWidget(_albl)
            _amp_leg_lay.addLayout(_arow)
        _amp_leg_lay.addStretch()

        _canvas_row = QHBoxLayout()
        _canvas_row.addWidget(self.canvas, stretch=1)
        _canvas_row.addWidget(_amp_legend_w)
        pv.addLayout(_canvas_row, stretch=1)

        # History slider: 0 = oldest, 10000 = live. Redraw at 10 Hz — matched
        # to the window arrival rate so the waveform scrolls smoothly (see
        # LivePlotPanel's redraw_interval_ms=100 above). The old 5 Hz redraw
        # showed every other window and then jumped two at once, which read
        # as lag. The per-frame work is now cheap (cached sample times, no
        # auto-scale), so 10 Hz is comfortable.
        pv.addLayout(self.plot.slider_row)

        return plot_box

    # ---- Size hints (compressibility) ----------------------------------------
    #
    # The default sizeHint() propagates up from the canvas figsize (700 px)
    # plus the very wide upper row (LabJack panel + monitor grids, ~1400 px
    # combined).  QScrollArea with widgetResizable=True always sizes the inner
    # widget to max(viewport, sizeHint), so without an override the tab is
    # locked to ~1400 px even in a narrow split pane, and the canvas never
    # visually compresses.  Returning a small preferred width here lets the
    # scroll area give the tab exactly the pane width, making the canvas fill
    # and respond to the splitter handle.

    def sizeHint(self):
        return QSize(400, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(150, super().minimumSizeHint().height())

    # ---- Connection lifecycle (driven by MainWindow) --------------------------

    def on_labjack_connected(self, serial: str):
        self._t0 = time.monotonic()
        # Start every buffer from a clean slate so the (possibly re-anchored)
        # stream timeline never mixes with data from a previous session.
        for buf in self.buffers.values():
            buf.clear()
        self.wave_ring.clear()
        self._connected = True
        self.lj_panel.set_connected(True, serial)
        self._update_redraw_state()

    def on_labjack_disconnected(self):
        self._connected = False
        self._update_redraw_state()
        self.lj_panel.set_connected(False)

    # ---- Visibility (QStackedWidget hides the non-current tab) ---------------
    #
    # The redraw timer is rendering, not data acquisition: it only needs to run
    # while this tab is the one on screen. Buffers keep filling via on_amp_state
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

    # ---- Buffer span (LivePlotPanel data callbacks) ---------------------------

    def _live_edge(self):
        """Newest timestamp available across all trend buffers, or None.

        Cheap by design (per-buffer O(1) `.latest()`): called on every redraw
        tick while LIVE and TREND mode is active (snapshot mode reads its own
        waveform-ring edge instead — see _redraw_snapshot).
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

    def on_profile_changed(self, profile_name: str):
        """Sync the UI to a profile that is now live on the hardware.

        Called by MainWindow after a switch completes, and by us during Apply.
        """
        if profile_name not in STREAM_PROFILES:
            return   # unknown name (e.g. a stale restore); leave the UI alone
        idx = list(STREAM_PROFILES.keys()).index(profile_name)
        self._profile_combo.blockSignals(True)
        self._profile_combo.setCurrentIndex(idx)
        self._profile_combo.blockSignals(False)

        # Rebuild the target list for the profile that is now live. Needed
        # because a calibration run switches the profile out from under this
        # tab, so the combo can be holding targets of the wrong kind entirely.
        if profile_name != self._staged_profile:
            self._staged_profile = profile_name
            self._populate_target_combo(profile_name)

        self._single_mode = is_single_channel(profile_name)
        pair_mode = is_pair_channel(profile_name)
        self._single_combo.setEnabled(self._single_mode or pair_mode)

        rate = STREAM_PROFILES[profile_name]["per_channel_rate_hz"]
        res  = resolution_index(profile_name)
        if self._single_mode:
            self._single_target_ain = self._single_combo.currentData()
            amp, kind = SC.AIN_TO_AMP[self._single_target_ain]
            self._profile_status.setText(
                f"{rate / 1000:.1f} kS/s  |  res idx {res}  |  "
                f"target {amp} {kind} ({self._single_target_ain})  |  "
                f"window {window_samples(profile_name)} pts"
            )
        elif pair_mode:
            pair = self._single_combo.currentData() or DEFAULT_AMP_PAIR
            self._profile_status.setText(
                f"{rate / 1000:.1f} kS/s/ch  |  res idx {res}  |  "
                f"pair {pair} ({SC.AMP_CHANNEL_MAP[pair]['current']}+"
                f"{SC.AMP_CHANNEL_MAP[pair]['voltage']})  |  "
                f"window {window_samples(profile_name)} pts"
            )
        else:
            self._profile_status.setText(
                f"{rate / 1000:.1f} kS/s/ch  |  res idx {res}  |  "
                f"window {window_samples(profile_name)} pts"
            )

        # This profile/target is now the live one; clear any pending Apply state.
        self._applied_profile = profile_name
        if self._single_mode:
            self._applied_channel = self._single_combo.currentData()
            self._staged_channel  = self._applied_channel
        elif pair_mode:
            self._applied_pair = self._single_combo.currentData()
            self._staged_pair  = self._applied_pair
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
        muted = "color: #bbb; font-weight: bold;"
        for amp in SC.AMP_LABELS:
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
            v_live = (not self._single_mode) or v_ain == self._single_target_ain
            i_live = (not self._single_mode) or i_ain == self._single_target_ain
            # Only the MEASURED cells are muted. The Mode / Commanded cells are
            # deliberately left alone: they come from the generator readback,
            # not the stream, so they stay true even when a monitor is not
            # being sampled — and knowing what a paused channel was told to do
            # is exactly what makes the pause readable rather than just blank.
            # Live cells are not restyled here; _refresh_monitors sets their
            # colour from the value on every window.
            if not v_live:
                for lbl in (self.lbl_meas[amp], self.lbl_pp[amp], self.lbl_dev[amp]):
                    lbl.setStyleSheet(muted)
                    lbl.setText("—")
            if not i_live:
                for lbl in (self.lbl_cur[amp], self.lbl_currms[amp]):
                    lbl.setStyleSheet(muted)
                    lbl.setText("—")

    def _update_history_layout(self):
        """Show only the relevant subplot in single-channel mode.

        Width is held to 0.82 (right edge 0.92) so the mirrored kV axis on the
        right has room for its ticks and label.
        """
        # Normalized figure coords: [left, bottom, width, height]
        _FULL = [0.10, 0.11, 0.82, 0.80]
        _TOP  = [0.10, 0.54, 0.82, 0.40]
        _BOT  = [0.10, 0.11, 0.82, 0.38]

        if self._single_mode:
            _, kind = SC.AIN_TO_AMP.get(self._single_target_ain, ("", "voltage"))
            is_voltage = (kind == "voltage")
            self.ax_v.set_visible(is_voltage)
            self.ax_v_right.set_visible(is_voltage)
            self.ax_i.set_visible(not is_voltage)
            self.ax_i_right.set_visible(not is_voltage)
            if is_voltage:
                self.ax_v.tick_params(labelbottom=True)
                self.ax_v.set_xlabel("Time (s, relative to window right edge)")
                self.ax_v.set_position(_FULL)
            else:
                self.ax_i.set_position(_FULL)
        else:
            self.ax_v.set_visible(True)
            self.ax_v_right.set_visible(True)
            self.ax_i.set_visible(True)
            self.ax_i_right.set_visible(True)
            self.ax_v.tick_params(labelbottom=False)
            self.ax_v.set_xlabel("")
            self.ax_v.set_position(_TOP)
            self.ax_i.set_position(_BOT)
        self.canvas.draw_idle()

    # ---- Profile / target staging + Apply ------------------------------------

    def _populate_target_combo(self, profile_name: str):
        """Rebuild the target combo for *profile_name*, preserving the choice.

        Rebuilt rather than filtered because the three cases carry different
        userData types — AIN name, amp label, or nothing — and a combo holding
        a mix of them would let _refresh_apply_state compare an AIN against an
        amp label and conclude they differ forever.
        """
        prev_single = self._staged_channel
        prev_pair   = self._staged_pair
        self._single_combo.blockSignals(True)
        self._single_combo.clear()
        if is_single_channel(profile_name):
            for amp in SC.AMP_LABELS:
                for kind in ("voltage", "current"):
                    ain = SC.AMP_CHANNEL_MAP[amp][kind]
                    self._single_combo.addItem(
                        f"{amp} {kind.capitalize()}  ({ain})", userData=ain)
            idx = self._single_combo.findData(prev_single or DEFAULT_SINGLE_CHANNEL)
            self._single_combo.setCurrentIndex(max(idx, 0))
            self._single_combo.setToolTip(
                "Single-channel mode: choose which amplifier monitor gets the "
                "full stream bandwidth.")
        elif is_pair_channel(profile_name):
            for amp in pair_choices(profile_name):
                v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
                i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]
                self._single_combo.addItem(
                    f"{amp}  ({i_ain}+{v_ain})", userData=amp)
            idx = self._single_combo.findData(prev_pair or DEFAULT_AMP_PAIR)
            self._single_combo.setCurrentIndex(max(idx, 0))
            self._single_combo.setToolTip(
                "Dual-channel mode: choose which amplifier's CURRENT and "
                "VOLTAGE monitors are streamed together. Both AINs of the pair "
                "always travel together — this is the pair a calibration sweep "
                "re-points as it moves from channel to channel.")
        else:
            self._single_combo.setToolTip(
                "This profile streams a fixed scan list — no target to choose.")
        self._single_combo.blockSignals(False)

    def _on_selection_staged(self, *_):
        """A combo changed — stage it and light up Apply if it differs from live.

        Nothing touches the hardware here.  The target combo is repopulated for
        whichever profile is *staged*, so the target can be chosen before
        applying.
        """
        staged_profile = self._profile_combo.currentData()
        if staged_profile != self._staged_profile:
            # Profile changed: rebuild the target list for its kind.
            self._staged_profile = staged_profile
            self._populate_target_combo(staged_profile)
        # Remember the staged target per kind, so switching profile back and
        # forth does not lose the other one's selection.
        data = self._single_combo.currentData()
        if is_single_channel(staged_profile):
            self._staged_channel = data
        elif is_pair_channel(staged_profile):
            self._staged_pair = data
        self._single_combo.setEnabled(
            bool(is_single_channel(staged_profile)
                 or is_pair_channel(staged_profile)))
        self._refresh_apply_state()

    def _refresh_apply_state(self):
        """Enable/highlight Apply iff the staged selection differs from live."""
        staged_profile = self._profile_combo.currentData()
        staged_target  = self._single_combo.currentData()
        if is_single_channel(staged_profile):
            target_differs = staged_target != self._applied_channel
        elif is_pair_channel(staged_profile):
            target_differs = staged_target != self._applied_pair
        else:
            target_differs = False
        pending = (staged_profile != self._applied_profile) or target_differs
        self._apply_btn.setEnabled(pending)
        if pending:
            self._apply_btn.setStyleSheet(
                f"QPushButton {{ background:{theme.WARN}; color:white; font-weight:bold;"
                " padding:2px 10px; }}"
                "QPushButton:hover { background:#d98c00; }"
            )
        else:
            self._apply_btn.setStyleSheet("")

    def set_profile_controls_enabled(self, on: bool):
        """Grey out profile/target selection for the duration of a
        calibration run (rbl/gui/calibration_tab.py).

        A profile switch mid-sweep does a full eStreamStop -> reconfigure ->
        eStreamStart cycle on the T7 — allowing one here would silently
        corrupt whatever the calibration run is in the middle of recording.
        """
        self._profile_combo.setEnabled(on)
        if not on:
            self._single_combo.setEnabled(False)
            self._apply_btn.setEnabled(False)
            self._profile_combo.setToolTip(
                "Disabled during a calibration run — switching the stream "
                "profile mid-run would corrupt it."
            )
        else:
            self._profile_combo.setToolTip("")
            self._single_combo.setEnabled(is_single_channel(self._profile_combo.currentData()))
            self._refresh_apply_state()

    def _apply_stream_settings(self):
        """Commit the staged profile/target to the hardware (one atomic action).

        A pair profile goes through pair_profile_requested, which sets profile
        and target together in ONE stream restart. The two-request version was
        unreliable: each request early-returns when its own field already
        matches, so selecting a different pair while already on AMP_PAIR did
        nothing, and switching into AMP_PAIR from elsewhere restarted twice.

        Single-channel emits the channel BEFORE the profile so a switch into a
        single-channel profile starts directly on the chosen target — same
        one-restart reasoning, via the older path.
        """
        staged_profile = self._profile_combo.currentData()
        staged_target  = self._single_combo.currentData()
        if staged_profile is None:
            return

        if is_pair_channel(staged_profile):
            self.pair_profile_requested.emit(staged_profile, staged_target or "")
        else:
            if (is_single_channel(staged_profile) and staged_target
                    and staged_target != self._applied_channel):
                self.single_channel_change_requested.emit(staged_target)
            if staged_profile != self._applied_profile:
                self.profile_change_requested.emit(staged_profile)

        # Update our own UI immediately.  When connected, MainWindow also calls
        # on_profile_changed after the restart; both are idempotent.
        self.on_profile_changed(staged_profile)

    def on_labjack_error(self, msg: str):
        QMessageBox.warning(self, "LabJack poll error", msg)

    # ---- Window ingestion ----------------------------------------------------

    def on_amp_state(self, state: AmpState):
        """Render one AmpState — the amplifier half of one stream window.

        Feeds three things per amplifier:
          * numeric readouts (peak/pk-pk/RMS scalars),
          * the 10 Hz trend buffers (RMS-kV / RMS-mA),
          * the raw-waveform ring (full window, in kV / mA) for snapshot mode.

        None of the monitor scaling happens here.  Beamline converts each
        window's volts to kV and mA once (see rbl/state/labjack_link.py) and
        this tab renders what comes out, including the full-resolution
        `window_kv` / `window_ma` arrays the scope view needs.  Keeping the
        EEL5000 monitor ratios in one file is the whole point: this tab used
        to apply them itself, in parallel with Beamline applying them for the
        Overview, so the same BNC had two independent paths to a number.

        Voltage and current are handled independently: a single-channel
        profile streams ONE of an amplifier's two monitors, so requiring both
        would blank a display that has half its data.
        """
        if not state.connected:
            return

        # A safety interlock has frozen this tab: stop taking new data in.
        # Everything already in the buffers, the waveform ring and the numeric
        # labels is the state at the moment of the trip, which is exactly what
        # there is to look at. Returning here (rather than gating the redraw)
        # keeps the plot fully interactive — the operator can still scrub the
        # slider and zoom around the event.
        if self._safety_frozen:
            if self._safety_admit_left <= 0:
                return
            # Admit this window, then anchor the view once it has landed —
            # see _safety_admit_left. Falls through to the normal ingest below.
            self._safety_admit_left -= 1
            if self._safety_admit_left == 0:
                QTimer.singleShot(0, self._anchor_safety_freeze)

        t = state.t
        # Adopt the stream's true sample period when present so stitched
        # waveform chunks use the real per-sample step (not a nominal guess).
        self.wave_ring.set_sample_period(state.sample_period)
        # Cache for the commanded-vs-measured comparison, which is driven by
        # the funcgen readback and so can arrive between windows.
        self._last_channels = dict(state.channels or {})

        for amp in SC.AMP_LABELS:
            ch = state.channels.get(amp)
            if ch is None:
                continue
            v_ain = SC.AMP_CHANNEL_MAP[amp]["voltage"]
            i_ain = SC.AMP_CHANNEL_MAP[amp]["current"]

            if ch.v_live:
                # The trend / history line plots the SIGNED MEAN (not RMS):
                # above the 1 s snapshot boundary this gives the true DC level
                # including polarity, so -4 kV reads as -4 kV, not +4 kV.
                # dc_kv is the pre-converted mean of the window's waveform in kV.
                self.buffers[v_ain].append(t, ch.dc_kv)

                if ch.window_kv is not None:
                    self.wave_ring.store(v_ain, t, ch.window_kv)

            if ch.i_live:
                self.buffers[i_ain].append(t, ch.rms_ma)

                if ch.window_ma is not None:
                    self.wave_ring.store(i_ain, t, ch.window_ma)

        # One repaint of the whole table per window, driven off the cached
        # measurement and the cached readback together — the two arrive on
        # independent signals and either may be the later one.
        self._sample_period = state.sample_period
        self._refresh_monitors()

        if self.plot.is_live:
            self.plot.force_to_live()

    # ---- Mode label ------------------------------------------------------------
    #
    # Formatting stays here (not in LivePlotPanel) because amp_tab's wording
    # differs from the log-amp tab's (a window-size / snapshot-vs-RMS suffix).

    def _is_snapshot(self) -> bool:
        """True when the window is narrow enough to show the raw waveform.

        Inclusive at the boundary: a 1 s window still shows the real waveform;
        only *above* 1 s does the RMS trend take over.
        """
        return self.plot.window_seconds <= self.SNAPSHOT_MAX_SECONDS + 1e-9

    def _window_label(self) -> str:
        ws = self.plot.window_seconds
        if ws < 1.0:
            body = f"{ws * 1000:.3g} ms"
        else:
            ws_int = int(ws)
            if ws_int < 60:
                body = f"{ws_int} s"
            else:
                m, s = divmod(ws_int, 60)
                body = f"{m} m" if s == 0 else f"{m} m {s} s"
        return f"{body} · waveform" if self._is_snapshot() else f"{body} · RMS"

    def _update_frozen_label(self):
        w_start = self.plot.frozen_right_edge - self.plot.window_seconds
        self.lbl_mode.setText(
            f"⏸  Frozen  —  [{w_start:+.3g} s … {self.plot.frozen_right_edge:+.3g} s]"
            f"  ({self._window_label()})"
        )
        self.lbl_mode.setStyleSheet(
            "color: #8c6000; font-weight: bold; padding: 2px 6px;"
        )

    # ---- Commanded vs measured -------------------------------------------------

    def on_funcgens_changed(self, state):
        """Cache the funcgen READBACK so the table can show what was asked for.

        Readback, not command intent: this is what the generator reports being
        set to, so it catches a command that silently failed to take, and it
        works outside a calibration run — the amp tab is the health screen, and
        it should say what the hardware is doing whoever asked it to.
        """
        self._cmd = {}
        channels = getattr(state, "channels", None) or {}
        for key, snap in channels.items():
            amp = CHANNEL_ROLE.get(key)
            if amp is None or snap is None:
                continue
            self._cmd[amp] = snap
        self._refresh_monitors()

    @staticmethod
    def _commanded(snap):
        """(is_dc, shape_label, freq_hz, amplitude_kv, output_on) from a readback.

        amplitude_kv is a PEAK amplitude on AC and a SIGNED LEVEL on DC, which
        is the distinction the rest of this file is careful about.

        The generator reports amplitude as Vpp — peak-to-PEAK — while the
        amplifier's rating, the calibration ladder and every "kV pk" on this
        screen are peak amplitudes. Halving happens HERE, once, so no caller
        has to remember which convention it is holding.
        """
        shape = str(getattr(snap, "shape", "") or "").upper()
        head  = shape.split(",")[0]
        freq  = float(getattr(snap, "freq_hz", 0.0) or 0.0)
        gain  = _AMP_GAIN / 1000.0        # generator volts -> output kV
        on    = bool(getattr(snap, "output_on", False))
        if head.startswith("DC") or freq <= 0:
            return (True, "DC", 0.0,
                    float(getattr(snap, "offset_v", 0.0) or 0.0) * gain, on)
        label = _SHAPE_LABEL.get(head, head[:4] or "AC")
        amp_kv = abs(float(getattr(snap, "amp_vpp", 0.0) or 0.0)) / 2.0 * gain
        return (False, label, freq, amp_kv, on)

    def _measured_amplitude_kv(self, ch, is_dc: bool) -> float:
        """Measured output in kV, in the same convention as the command.

        THIS IS THE COMPARISON THAT HAS TO BE RIGHT.

        On AC the drive is a zero-centred waveform, so the peak amplitude is
        pk-pk / 2 — computed from pkpk_kv, which is max-min and therefore
        always positive. It deliberately does NOT use AmpChannelSnapshot.peak_kv:
        that is the SIGNED sample of largest magnitude, so on a symmetric
        triangle it is +Vpk or -Vpk depending on nothing more than which
        extreme the window happened to catch. Subtracting a positive commanded
        peak from that flips the delta to about -200% every time the negative
        excursion wins, which is a comparison the operator would rightly stop
        trusting.

        On DC the held level is signed and polarity is part of the answer, so
        it uses the window mean.
        """
        if ch is None:
            return float("nan")
        if is_dc:
            return ch.dc_kv
        pkpk = ch.pkpk_kv
        return (pkpk / 2.0) if math.isfinite(pkpk) else float("nan")

    def _measured_current(self, amp: str, ch, is_dc: bool, freq_hz: float):
        """(display_ma, suffix) for the Current row.

        AC uses the amplitude at the DRIVE FREQUENCY, extracted from this
        window's raw samples. Neither of the obvious alternatives works here:
        the mean of a symmetric drive is ~0 however hard the amplifier is
        working, and the peak is dominated by the monitor's noise floor —
        against this rig's ~1.4 mA rms noise a peak reading biases +181% where
        the fundamental biases +0.2%.

        Falls back to RMS, flagged as such, when the window is too short to
        hold enough whole cycles (below ~40 Hz at a 100 ms window) — better to
        show a cruder number and say so than to show a blank.
        """
        if ch is None or not getattr(ch, "i_live", False):
            return float("nan"), ""
        if is_dc:
            return ch.dc_ma, "mean"
        wave = getattr(ch, "window_ma", None)
        sp   = getattr(self, "_sample_period", None)
        if wave is not None and sp:
            amp_ma, _phase = fundamental(wave, 1.0 / sp, freq_hz)
            if math.isfinite(amp_ma):
                return amp_ma, "pk@f"
        return ch.rms_ma, "rms*"

    def _refresh_monitors(self):
        """Repaint the whole commanded-vs-measured table.

        Driven by both the funcgen readback and the stream, so it is written to
        be safe to call from either at any time and to tolerate one of them
        being absent — at startup the readback arrives before any window, and
        on a single-channel profile half the monitors are simply not sampled.
        """
        for amp in SC.AMP_LABELS:
            snap = self._cmd.get(amp)
            ch   = self._last_channels.get(amp)
            l_mode = self.lbl_mode_c[amp]
            l_cmd  = self.lbl_cmd[amp]
            l_meas = self.lbl_meas[amp]
            l_dev  = self.lbl_dev[amp]
            l_pp   = self.lbl_pp[amp]
            l_cur  = self.lbl_cur[amp]
            l_rms  = self.lbl_currms[amp]

            # ---- Commanded side -----------------------------------------
            if snap is None:
                l_mode.setText("—")
                l_mode.setStyleSheet("color: #888;")
                l_cmd.setText("—")
                l_cmd.setStyleSheet("color: #555;")
                is_dc, freq, cmd_kv, out_on = True, 0.0, float("nan"), False
            else:
                is_dc, label, freq, cmd_kv, out_on = self._commanded(snap)
                l_mode.setText(label if is_dc else f"{label} {_fmt_hz(freq)}")
                l_mode.setStyleSheet(
                    "color: #333; font-weight: bold;" if out_on
                    else "color: #999;")
                if is_dc:
                    l_cmd.setText(f"{cmd_kv:+.3f} kV")
                else:
                    l_cmd.setText(f"{cmd_kv:.3f} kV pk")
                l_cmd.setStyleSheet(
                    "color: #004e8c; font-weight: bold;" if out_on
                    else "color: #999; font-weight: bold;")
                if not out_on:
                    l_mode.setText(l_mode.text() + " · OFF")

            # ---- Measured side ------------------------------------------
            v_live = ch is not None and getattr(ch, "v_live", False)
            meas_kv = self._measured_amplitude_kv(ch, is_dc) if v_live else float("nan")
            if not v_live or not math.isfinite(meas_kv):
                l_meas.setText("—")
                l_meas.setStyleSheet("color: #999;")
                l_pp.setText("—")
                l_pp.setStyleSheet("color: #999;")
            else:
                l_meas.setText(f"{meas_kv:+.3f} kV" if is_dc
                               else f"{meas_kv:.3f} kV pk")
                l_meas.setStyleSheet(
                    f"color: {_STATUS_COLOR[voltage_status(meas_kv)]}; "
                    f"font-weight: bold;")
                l_pp.setText(f"{ch.pkpk_kv:.3f} kV pk-pk")
                l_pp.setStyleSheet("color: #444;")

            # ---- Delta ---------------------------------------------------
            # Suppressed when the output is off: the generator still reports
            # the amplitude it is configured for, but nothing is driving the
            # amplifier, so a comparison would flag -100% on a channel behaving
            # exactly as intended.
            if (not out_on or not math.isfinite(cmd_kv) or abs(cmd_kv) < 1e-9
                    or not math.isfinite(meas_kv)):
                l_dev.setText("—")
                l_dev.setStyleSheet("color: #999;")
            else:
                d_kv = meas_kv - cmd_kv
                pct  = 100.0 * d_kv / abs(cmd_kv)
                # Inside the documented uncertainty budget is agreement, not a
                # finding — the same threshold the calibration tab refuses to
                # report deviations below.
                ok = abs(d_kv) * 1000.0 <= CAL_UNCERTAINTY_V
                l_dev.setText(f"{d_kv * 1000:+.0f} V ({pct:+.1f}%)")
                l_dev.setStyleSheet(
                    f"color: {'#1a7000' if ok else '#a05000'}; font-weight: bold;")

            # ---- Current -------------------------------------------------
            cur_ma, suffix = self._measured_current(amp, ch, is_dc, freq)
            if not math.isfinite(cur_ma):
                l_cur.setText("—")
                l_cur.setStyleSheet("color: #999;")
            else:
                l_cur.setText(f"{cur_ma:+.3f} mA {suffix}" if is_dc
                              else f"{cur_ma:.3f} mA {suffix}")
                l_cur.setStyleSheet(
                    f"color: {_STATUS_COLOR[current_status(cur_ma)]}; "
                    f"font-weight: bold;")
            if ch is not None and getattr(ch, "i_live", False) \
                    and math.isfinite(ch.rms_ma):
                l_rms.setText(f"{ch.rms_ma:.3f} mA rms")
                l_rms.setStyleSheet("color: #444;")
            else:
                l_rms.setText("—")
                l_rms.setStyleSheet("color: #999;")

    # ---- Safety freeze ---------------------------------------------------------

    def freeze_on_safety_event(self, reason: str):
        """Freeze the plot and stop ingesting, because an interlock fired.

        Called from MainWindow when the calibration over-current interlock
        trips. The point is forensic: by the time the operator has read the
        warning dialog the amplifier has been zeroed for several seconds, and
        in RMS/trend mode each point is a whole window's average, so the
        excursion that caused the trip is both averaged down and about to
        scroll away. Stopping the ingest at the trip keeps the last
        BUFFER_CAPACITY points and the whole waveform ring exactly as they
        were, and pinning the view to the newest sample puts the event at the
        right-hand edge where it can be zoomed into.

        Works the same in both modes because both read the same frozen
        buffers: RMS/trend gets the last few minutes of history, waveform gets
        the raw ring at full sample rate — which is the only view that shows
        how high the current actually went, rather than its window average.

        Safe to call repeatedly; the first call wins, so a second interlock
        event cannot overwrite the data from the first.
        """
        if self._safety_frozen:
            return
        self._safety_frozen = True
        self._safety_reason = reason
        # Do NOT anchor the view here. The window carrying the over-current has
        # not been ingested yet (see _safety_admit_left), so there is nothing
        # to anchor to; _anchor_safety_freeze runs once it has landed.
        self._safety_admit_left = self.SAFETY_ADMIT_WINDOWS

        # Drive the Pause button into its paused state so the freeze reads as
        # one thing on screen rather than two: the plot is stopped, the button
        # says Resume, and pressing Resume is what restarts it.
        self._paused = True
        self._btn_pause.setText("Resume")
        self._btn_pause.setStyleSheet(
            "background: #a00000; color: white; font-weight: bold; padding: 2px 8px;")
        self._update_safety_label()

    def _anchor_safety_freeze(self):
        """Pin the view now that the triggering window has been ingested.

        Also widens the time window if needed. The trip lands somewhere inside
        a 100 ms stream window, but the right edge can only be placed at that
        window's newest sample — so at a 1 ms view the visible slice is the
        last 1 ms of the window and the excursion is almost certainly outside
        it. That is why the frozen frame looked blank. Opening out to at least
        one full stream window guarantees the whole triggering window is on
        screen; the operator can then zoom back in and scrub to the spike.
        """
        if not self._safety_frozen:
            return   # released before the window arrived
        if self.plot.window_seconds < self.SAFETY_MIN_WINDOW_S:
            self.plot.window_seconds = self.SAFETY_MIN_WINDOW_S
        self.plot.freeze_at_live_edge()
        self.btn_jump_live.setText("Resume Live")
        self.btn_jump_live.setVisible(True)
        self._update_safety_label()
        self._redraw_plot()   # render the frozen frame immediately

    def _on_jump_live_clicked(self):
        if self._safety_frozen:
            self.clear_safety_freeze()
        else:
            self.plot.jump_to_live()

    def clear_safety_freeze(self):
        """Resume ingestion and return to live. Operator-initiated only."""
        if not self._safety_frozen:
            return
        self._safety_frozen = False
        self._safety_reason = ""
        self._safety_admit_left = 0
        self.btn_jump_live.setText("Jump to Live")
        # Release the pause this freeze imposed. Deliberately unconditional:
        # the freeze set it, so the freeze clears it. A pause the operator set
        # themselves before the trip is not worth preserving across an
        # interlock event — they pressed Resume, they want it running.
        self._paused = False
        self._btn_pause.setText("Pause")
        self._btn_pause.setStyleSheet("padding: 2px 8px;")
        # The buffers now have a gap spanning the freeze. Clearing the ring
        # prevents the waveform view from stitching across it and drawing a
        # cycle that never existed — the same reason a profile switch clears
        # it (see AmpTraceBuilder.clear).
        self.wave_ring.clear()
        self.plot.jump_to_live()

    def _update_safety_label(self):
        self.lbl_mode.setText(f"⛔ FROZEN — {self._safety_reason}")
        self.lbl_mode.setStyleSheet(
            "color: white; background: #a00000; font-weight: bold; "
            "padding: 2px 6px; border-radius: 3px;"
        )

    def _on_navigation_changed(self):
        self.btn_jump_live.setVisible(not self.plot.is_live)
        if self._safety_frozen:
            # Keep the interlock banner up while scrubbing; it outranks the
            # ordinary frozen/live wording until the operator resumes.
            self._update_safety_label()
            return
        if self.plot.is_live:
            self.lbl_mode.setText(f"● LIVE  ({self._window_label()})")
            self.lbl_mode.setStyleSheet(
                theme.status_label(theme.OK) + " padding: 2px 6px;"
            )
        else:
            self._update_frozen_label()

    def _on_zoom_changed(self):
        if self._safety_frozen:
            self._update_safety_label()
            return
        if self.plot.is_live:
            self.lbl_mode.setText(f"● LIVE  ({self._window_label()})")
        elif self.plot.frozen_right_edge is not None:
            self._update_frozen_label()

    def _on_scroll(self, event):
        if event.button == 'up':
            self.plot.zoom_in()
        elif event.button == 'down':
            self.plot.zoom_out()

    # ---- Vertical (voltage / current) scale: zoom, pan, reset ----------------

    def _apply_ylimits(self):
        """Push the held vertical limits onto both axes (no auto-scaling)."""
        self.ax_v.set_ylim(self._ylim_v)
        self.ax_i.set_ylim(self._ylim_i)

    @staticmethod
    def _zoom_span(ylim, factor: float):
        """Scale a [lo, hi] range about its centre by *factor*."""
        lo, hi = ylim
        centre = 0.5 * (lo + hi)
        half   = 0.5 * (hi - lo) * factor
        return [centre - half, centre + half]

    def _v_zoom(self, factor: float):
        """Zoom the voltage axis only about its centre."""
        self._ylim_v = self._zoom_span(self._ylim_v, factor)
        self._apply_ylimits()
        self.canvas.draw_idle()

    def _v_zoom_in(self):
        self._v_zoom(self.V_ZOOM_FACTOR)          # tighter span

    def _v_zoom_out(self):
        self._v_zoom(1.0 / self.V_ZOOM_FACTOR)    # wider span

    def _v_reset(self):
        """Reset the voltage axis to the full ±kV rating."""
        self._ylim_v = [-SC.AMP_MAX_KV, SC.AMP_MAX_KV]
        self._apply_ylimits()
        self.canvas.draw_idle()

    def _i_zoom(self, factor: float):
        """Zoom the current axis only about its centre."""
        self._ylim_i = self._zoom_span(self._ylim_i, factor)
        self._apply_ylimits()
        self.canvas.draw_idle()

    def _i_zoom_in(self):
        self._i_zoom(self.V_ZOOM_FACTOR)

    def _i_zoom_out(self):
        self._i_zoom(1.0 / self.V_ZOOM_FACTOR)

    def _i_reset(self):
        """Reset the current axis to the full ±mA rating."""
        self._ylim_i = [-SC.AMP_MAX_MA_DC, SC.AMP_MAX_MA_DC]
        self._apply_ylimits()
        self.canvas.draw_idle()

    def _on_press(self, event):
        """Begin a vertical pan.  Left button, inside one of the plot axes."""
        if event.button != 1 or event.inaxes is None or event.y is None:
            return
        if not event.inaxes.get_visible():
            return   # a hidden axis can still sit under the cursor in single mode
        if event.inaxes is self.ax_v:
            which, ylim0 = "v", self._ylim_v
        elif event.inaxes is self.ax_i:
            which, ylim0 = "i", self._ylim_i
        else:
            return
        height = event.inaxes.bbox.height
        if height <= 0:
            return
        # Data units per pixel, captured at grab time so the point under the
        # cursor stays under the cursor for the whole drag.
        per_px = (ylim0[1] - ylim0[0]) / height
        self._pan = {"which": which, "y0": event.y,
                     "ylim0": list(ylim0), "per_px": per_px}

    def _on_motion(self, event):
        if self._pan is None or event.y is None:
            return
        dpix  = event.y - self._pan["y0"]          # pixels dragged (up = +)
        shift = dpix * self._pan["per_px"]         # data units
        lo0, hi0 = self._pan["ylim0"]
        new = [lo0 - shift, hi0 - shift]           # drag up → view follows up
        if self._pan["which"] == "v":
            self._ylim_v = new
        else:
            self._ylim_i = new
        self._apply_ylimits()
        self.canvas.draw_idle()

    def _on_release(self, event):
        self._pan = None

    # ---- Plot redraw ---------------------------------------------------------

    def _toggle_pause(self):
        """Pause / resume the waveform plot updates.

        While a safety freeze is in force this button is the release for it:
        the freeze put the button into its paused state, so pressing Resume
        has to undo the whole thing — restart ingestion, not just redrawing.
        """
        if self._safety_frozen:
            self.clear_safety_freeze()
            return
        self._paused = not self._paused
        if self._paused:
            self._btn_pause.setText("Resume")
            self._btn_pause.setStyleSheet(
                "background: #004e8c; color: white; font-weight: bold; padding: 2px 8px;")
        else:
            self._btn_pause.setText("Pause")
            self._btn_pause.setStyleSheet("padding: 2px 8px;")

    def _redraw_plot(self):
        """Dispatch to the trend or waveform-snapshot renderer for this window."""
        # A safety freeze sets _paused, but must still redraw: ingestion has
        # stopped, so a redraw cannot show anything new — it only reflects the
        # operator scrubbing the slider or zooming, which is the entire point
        # of freezing. Honouring _paused here would leave the plot showing
        # whatever happened to be on it and ignore every navigation input.
        if self._paused and not self._safety_frozen:
            return
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

        window = self.plot.compute_window()
        if window is None:
            return
        t_left, t_right = window

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
            self.ax_v.set_xlim(-self.plot.window_seconds, 0)
            self._apply_ylimits()
            self.canvas.draw_idle()

    def _redraw_snapshot(self):
        """Raw high-rate waveform over the last window_seconds — the scope view.

        LIVE mode's right edge comes from the waveform ring, not the trend
        buffers `self.plot` uses — the ring can be ahead of the 10 Hz trend
        by up to one window — so this bypasses `self.plot.compute_window()`
        and reads `is_live`/`frozen_right_edge`/`window_seconds` directly.
        """
        if self.plot.is_live:
            t_right = self.wave_ring.latest_t()
        else:
            t_right = self.plot.frozen_right_edge
        if t_right is None:
            return

        t_left = t_right - self.plot.window_seconds
        any_data = False

        for amp in SC.AMP_LABELS:
            for ain, line in (
                (SC.AMP_CHANNEL_MAP[amp]["voltage"], self._lines_v[amp]),
                (SC.AMP_CHANNEL_MAP[amp]["current"], self._lines_i[amp]),
            ):
                if self._single_mode and ain != self._single_target_ain:
                    line.set_data([], [])
                    continue
                series = self.wave_ring.series(ain, t_left, t_right)
                if series is None:
                    line.set_data([], [])
                    continue
                tt, vv = series
                tt, vv = decimate_minmax(tt, vv, self.WF_MAX_POINTS)
                line.set_data(tt - t_right, vv)
                any_data = True

        if any_data:
            self.ax_v.set_xlim(-self.plot.window_seconds, 0)
            self._apply_ylimits()
            self.canvas.draw_idle()

    # ---- Owner-callable cleanup ----------------------------------------------

    def shutdown(self):
        self.plot.stop()
