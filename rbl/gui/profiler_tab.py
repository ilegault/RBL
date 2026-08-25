"""
profiler_tab.py
PySide6 widget for the "Beam Profiler" outer tab.

WHAT THIS TAB SHOWS
-------------------
One channel carries two peaks, and they are the X profile and the Y profile
- two DIFFERENT DIRECTIONS measured in one trace, not one beam measured
twice.  So each gets its own headline number and its own history line, and
their ratio is displayed as the beam's ASPECT rather than as an error: an
elliptical beam is a real beam, and averaging X with Y would hide the one
event worth seeing, which is the two moving apart.

WHEN THE BEAM IS RASTERED the raw trace is a train of raster teeth whose
ENVELOPE carries the beam width.  Half maximum on the raw trace measures a
tooth - it reads a few hundred microseconds where the beam is milliseconds
wide, and the Gaussian fit collapses to r2 ~ 0.3.  Setting "Raster period"
to the ripple period switches the analysis onto the envelope.

TWO NUMBERS, AND WHICH ONE WINS
-------------------------------
  half-maximum : interpolated crossings.  Honest, but it only looks at four
                 samples per peak, so noise moves it - and noise on top of a
                 peak biases it NARROW, because it lifts the apparent peak
                 height and so the half-maximum level with it.
  Gaussian fit : a sum of N Gaussians, one per peak, over every sample.
                 Better on a noisy beam, but only while the model actually
                 describes the data.

The headline number is the fit while its r2 clears SCOPE_FIT_MIN_R2, and the
half-maximum mean otherwise.  Which one is showing is always labelled - the
tab never presents a number without saying where it came from.

ACQUISITION IS ON DEMAND
------------------------
Connecting opens the link and stops there. A waveform is fetched when the
operator presses Take shot (F5), or repeatedly once the mode is set to
continuous. Free-running was the old default and it is what made this tab
heavy: a transfer takes over a second, so the link was busy essentially
always, with the fit and the redraw on top — and while rastering it was
re-fetching a trace nobody was reading. Reading a profile is something you
do at a moment of your choosing.

SCOPE RESTRICTION
-----------------
This tab owns no driver and opens no serial port.  It subscribes to
Beamline.scope_changed (ScopeState) and Beamline.scope_error (str), and
pushes analysis settings back through Beamline, never to the worker
directly.  Triggering, timebase and vertical scale stay on the front panel.

LAYOUT
------
  [Connection bar]        port entry, connect/disconnect
  [FWHM readout]          headline width, source, per-peak widths, spread
  [Analysis controls]     peaks, smoothing, scope averaging, channel
  [Waveform plot]         trace + fit + one half-maximum span per peak
  [FWHM history]          rolling strip chart, one line per peak
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
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QSizePolicy, QCheckBox,
)

from rbl.config.scope_config import (
    SCOPE_AVERAGE_SWEEPS, SCOPE_AXIS_LABELS, SCOPE_ENVELOPE_MS,
    SCOPE_EXPECTED_PEAKS, SCOPE_MAX_PEAKS, SCOPE_POINTS, SCOPE_POINTS_ANCHOR,
    SCOPE_POINTS_CHOICES, SCOPE_POLL_CHOICES, SCOPE_POLL_INTERVAL_S,
    SCOPE_CONTINUOUS_DEFAULT, SCOPE_RECORD_POINTS, SCOPE_SMOOTH_WINDOW,
)
from rbl.gui import theme
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.gui.widgets.port_picker import PortPicker
from rbl.gui.widgets.inputs import (
    NoScrollComboBox, NoScrollSpinBox, QuietDoubleSpinBox, unit_row,
)

log = logging.getLogger(__name__)

# Maximum FWHM history points retained (1 Hz → 1 h)
_MAX_HISTORY = 3600

# Peak-to-peak agreement beyond this reads as a problem, not as noise.
_SPREAD_WARN = 0.15

_PEAK_COLORS = ["#2980b9", "#c0392b", "#8e44ad", "#16a085"]


def fmt_seconds(value: float, digits: int = 3) -> tuple[str, str]:
    """(number, unit) in whichever of ns/µs/ms/s reads best."""
    if value is None or not isinstance(value, (int, float)) or math.isnan(value):
        return "—", "s"
    a = abs(value)
    if a < 1e-6:
        return f"{value * 1e9:.{digits}g}", "ns"
    if a < 1e-3:
        return f"{value * 1e6:.{digits}g}", "µs"
    if a < 1.0:
        return f"{value * 1e3:.{digits}g}", "ms"
    return f"{value:.{digits + 1}g}", "s"


def fmt_seconds_str(value: float, digits: int = 3) -> str:
    num, unit = fmt_seconds(value, digits)
    return "—" if num == "—" else f"{num} {unit}"


# How long a single shot may take before the button re-arms itself.  A
# 2500-point transfer at 19200 baud is a little over a second; averaging on
# the scope and a slow trigger can stretch that, so this is deliberately
# several times the worst honest case - it is a stuck-button escape hatch,
# not a transfer deadline.
SHOT_TIMEOUT_MS = 20_000


def _carries_measurement(state) -> bool:
    """True when this snapshot holds beam data, not just link status.

    An idle ping and an error report are both `connected` snapshots with
    every data field at its default.  They say something about the LINK;
    they say nothing about the BEAM, and treating them as data is what
    blanked the waveform pane after every shot.
    """
    return bool(state.corrected_downsampled) or bool(state.peaks)


class ProfilerTab(QWidget):
    """Beam profile display tab.

    Subscribes to beamline.scope_changed for live ScopeState snapshots.
    """

    def __init__(self, beamline, parent=None):
        super().__init__(parent)
        self.beamline    = beamline
        # TWO caches, deliberately.  `_last_state` is the latest snapshot of
        # ANY kind and answers "is the link up?".  `_last_measured` is the
        # latest snapshot that actually CARRIES a measurement and answers
        # "what is the beam doing?".  They were one variable, and the idle
        # ping the worker emits milliseconds after a single shot overwrote
        # the measurement before the 1 s plot timer ever saw it - the
        # waveform pane went blank after every shot while the history plot,
        # which reads its own list, kept working.  An idle or error snapshot
        # is a statement about the link; it must never erase the last thing
        # measured.
        self._last_state    = None   # latest snapshot of ANY kind - status
        self._last_measured = None   # latest snapshot that CARRIES data
        self._last_time  = 0.0
        # Redraws are gated on new data. Repainting three matplotlib lines
        # and a dozen labels on a timer, whether or not anything changed, is
        # the app's own share of the lag — and in single-shot mode nothing
        # changes for minutes at a time.
        self._readout_dirty = False
        self._plot_dirty    = False
        self._shot_pending  = False
        self._connected     = False

        # Shot watchdog.  A press that never lands used to leave the button
        # disabled and reading "Acquiring..." forever, with nothing on
        # screen saying why - the worker had died and no signal was coming.
        # scope_worker no longer dies silently, but a button whose only way
        # back is a signal from somewhere else is a button that can stick,
        # so it now re-arms itself and says the shot did not land.
        self._shot_watchdog = QTimer(self)
        self._shot_watchdog.setSingleShot(True)
        self._shot_watchdog.setInterval(SHOT_TIMEOUT_MS)
        self._shot_watchdog.timeout.connect(self._on_shot_timeout)

        # Rolling history: [(unix_time, mean_fwhm, [(axis, fwhm), ...])]
        self._fwhm_history: list[tuple[float, float, list]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # _build_connection_box() writes into the status label, so that
        # label has to exist before the connection bar is built.
        self._lbl_status = QLabel("(not connected)")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-style: italic;")

        layout.addWidget(self._build_connection_box())
        layout.addWidget(self._build_readout_box())
        layout.addWidget(self._build_controls_box())
        layout.addWidget(self._build_load_box())
        layout.addWidget(self._build_waveform_box(), stretch=3)
        layout.addWidget(self._build_history_box(), stretch=2)
        layout.addWidget(self._build_calibration_box())

        # ── Subscribe to Beamline signals ─────────────────────────────────
        self.beamline.scope_changed.connect(self._on_scope_state)
        self.beamline.scope_error.connect(self._on_scope_error)

        # ── Redraw timers ─────────────────────────────────────────────────
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
    # Construction helpers
    # -----------------------------------------------------------------------

    def _build_connection_box(self) -> QGroupBox:
        box = QGroupBox("TDS 2012 Oscilloscope")
        lay = QHBoxLayout(box)

        self._picker = PortPicker("tds2012")
        self._picker.status.connect(self._on_picker_status)
        lay.addWidget(self._picker)

        self._btn_connect = QPushButton("Connect")
        self._btn_connect.clicked.connect(self._on_connect_toggle)
        lay.addWidget(self._btn_connect)

        # The primary action. Connecting opens the link and stops there;
        # a waveform is fetched when the operator asks for one. A transfer
        # takes over a second, so free-running it is what made the app feel
        # heavy — and while rastering it was fetching a trace that was not
        # telling anyone anything.
        self._btn_shot = QPushButton("Take shot")
        self._btn_shot.setShortcut("F5")
        self._btn_shot.setToolTip(
            "Fetch ONE waveform and measure it.  (F5)\n\n"
            "Takes about a second at 2500 points — the transfer is the slow "
            "part, not the analysis.")
        self._btn_shot.setStyleSheet("QPushButton { font-weight: bold; }")
        self._btn_shot.clicked.connect(self._on_take_shot)
        self._btn_shot.setEnabled(False)
        lay.addWidget(self._btn_shot)

        self._pill = StatusPill()
        lay.addWidget(self._pill)
        lay.addStretch()
        return box

    def _on_picker_status(self, message: str, colour: str):
        self._lbl_status.setText(message)
        self._lbl_status.setStyleSheet(f"color: {colour}; font-style: italic;")

    def _on_take_shot(self):
        """Ask for one waveform. The button reports back until it lands."""
        if not self.beamline.request_scope_shot():
            self._lbl_status.setText("not connected — press Connect first")
            self._lbl_status.setStyleSheet(
                f"color: {theme.WARN}; font-style: italic;")
            return
        self._shot_pending = True
        self._shot_watchdog.start()
        self._btn_shot.setEnabled(False)
        self._btn_shot.setText("Acquiring…")
        self._lbl_status.setText("fetching one waveform…")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-style: italic;")

    def _shot_finished(self):
        self._shot_pending = False
        self._shot_watchdog.stop()
        if self._connected:
            self._btn_shot.setEnabled(True)
        self._btn_shot.setText("Take shot")

    def _on_shot_timeout(self):
        """The shot never came back.  Re-arm rather than stay stuck.

        The link is left alone on purpose: the scope is usually still there
        and answering, and dropping the port would turn a missed shot into
        a reconnect the operator did not ask for.  Press again, or press
        Disconnect if it really is gone.
        """
        if not self._shot_pending:
            return
        log.warning("profiler_tab: shot did not land within %.0f s",
                    SHOT_TIMEOUT_MS / 1000.0)
        self._shot_pending = False
        if self._connected:
            self._btn_shot.setEnabled(True)
        self._btn_shot.setText("Take shot")
        self._lbl_status.setText(
            f"no waveform after {SHOT_TIMEOUT_MS // 1000} s — press Take shot "
            f"again (the link was left open)")
        self._lbl_status.setStyleSheet(
            f"color: {theme.WARN}; font-style: italic;")

    def _on_mode_changed(self, *_):
        """Switch between single-shot and free-running."""
        continuous = bool(self._cb_mode.currentData())
        self.beamline.set_scope_continuous(continuous)
        self._cb_poll.setEnabled(continuous)
        if continuous:
            self._lbl_status.setText(
                f"continuous — a shot every {self._cb_poll.currentText()}")
        else:
            self._lbl_status.setText("idle — press Take shot (F5)")
        self._lbl_status.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-style: italic;")

    def _build_readout_box(self) -> QGroupBox:
        box = QGroupBox("Beam Profile")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(14)

        # X and Y are DIFFERENT MEASUREMENTS. They get equal billing; there
        # is no single "the FWHM" to put in one big number, and showing a
        # mean as the headline would invite reading a round beam into an
        # elliptical one.
        self._lbl_axis_value = {}
        self._lbl_axis_name  = {}
        for col, axis in enumerate(SCOPE_AXIS_LABELS[:2]):
            name = QLabel(f"{axis} FWHM:")
            name.setStyleSheet(f"font-size: {theme.FS_LABEL}px;")
            grid.addWidget(name, 0, col * 3)
            value = QLabel("—")
            value.setStyleSheet(
                f"font-size: {theme.FS_BIG}px; font-weight: bold;")
            grid.addWidget(value, 0, col * 3 + 1)
            unit = QLabel("")
            unit.setStyleSheet(f"color: {theme.NEUTRAL};")
            grid.addWidget(unit, 0, col * 3 + 2)
            self._lbl_axis_name[axis]  = unit
            self._lbl_axis_value[axis] = value

        grid.addWidget(QLabel("X/Y ratio:"), 0, 6)
        self._lbl_ratio = QLabel("—")
        self._lbl_ratio.setToolTip(
            "Beam aspect: X width over Y width. 1.0 is round. This is a "
            "shape, not a fault — an elliptical beam is a real beam.")
        grid.addWidget(self._lbl_ratio, 0, 7)

        grid.addWidget(QLabel("Channel:"), 0, 8)
        self._lbl_channel = QLabel("—")
        self._lbl_channel.setStyleSheet(f"color: {theme.NEUTRAL};")
        grid.addWidget(self._lbl_channel, 0, 9)

        grid.addWidget(self._lbl_status, 0, 10, 1, 2)

        # Row 2 — provenance and quality
        grid.addWidget(QLabel("Measured by:"), 1, 0)
        self._lbl_source = QLabel("—")
        self._lbl_source.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        grid.addWidget(self._lbl_source, 1, 1, 1, 2)

        grid.addWidget(QLabel("Fit r²:"), 1, 3)
        self._lbl_r2 = QLabel("—")
        self._lbl_r2.setToolTip(
            "How well a sum of Gaussians describes the trace. Below 0.90 the "
            "model is wrong — a missed peak, or raster teeth being measured "
            "instead of the beam envelope.")
        grid.addWidget(self._lbl_r2, 1, 4)

        grid.addWidget(QLabel("S/N:"), 1, 5)
        self._lbl_snr = QLabel("—")
        self._lbl_snr.setStyleSheet(f"color: {theme.NEUTRAL};")
        self._lbl_snr.setToolTip(
            "Peak height over the noise sigma of the quiet edges, floored at "
            "half an ADC count.")
        grid.addWidget(self._lbl_snr, 1, 6)

        grid.addWidget(QLabel("Separation:"), 1, 7)
        self._lbl_sep = QLabel("—")
        self._lbl_sep.setStyleSheet(f"color: {theme.NEUTRAL};")
        grid.addWidget(self._lbl_sep, 1, 8)

        grid.addWidget(QLabel("Scope PWIDTH:"), 1, 9)
        self._lbl_pwidth = QLabel("—")
        self._lbl_pwidth.setStyleSheet(f"color: {theme.NEUTRAL};")
        self._lbl_pwidth.setToolTip(
            "The scope's own positive-pulse-width measurement at the 50 % "
            "level — an independent check, and meaningless on a rastered "
            "trace, where it measures one raster tooth.")
        grid.addWidget(self._lbl_pwidth, 1, 10)

        # Row 3 — a warning line that stays out of the way until it matters
        self._lbl_warn = QLabel("")
        self._lbl_warn.setStyleSheet(
            theme.status_label(theme.FAULT, bold=False)
            + f"font-size: {theme.FS_CAPTION}px;")
        self._lbl_warn.setVisible(False)
        grid.addWidget(self._lbl_warn, 2, 0, 1, 12)

        grid.setColumnStretch(11, 1)
        return box

    def _build_controls_box(self) -> QGroupBox:
        box = QGroupBox("Analysis")
        lay = QHBoxLayout(box)

        lay.addWidget(QLabel("Peaks:"))
        self._sp_peaks = NoScrollSpinBox()
        self._sp_peaks.setRange(1, SCOPE_MAX_PEAKS)
        self._sp_peaks.setValue(SCOPE_EXPECTED_PEAKS)
        self._sp_peaks.setToolTip(
            "Beam crossings per trace. The sweep crosses the aperture once "
            "outbound and once on the way back, so normally 2.")
        self._sp_peaks.valueChanged.connect(self._on_analysis_changed)
        lay.addWidget(self._sp_peaks)

        lay.addSpacing(12)
        lay.addWidget(QLabel("Smoothing:"))
        self._sp_smooth = NoScrollSpinBox()
        self._sp_smooth.setRange(1, 101)
        self._sp_smooth.setSingleStep(2)
        self._sp_smooth.setValue(SCOPE_SMOOTH_WINDOW)
        self._sp_smooth.setMaximumWidth(70)
        self._sp_smooth.setToolTip(
            "Boxcar width applied before the half-maximum search. 1 disables "
            "it. Noise on top of a peak biases the half-maximum reading "
            "NARROW; on a noisy beam 9–25 samples costs nothing.")
        self._sp_smooth.valueChanged.connect(self._on_analysis_changed)
        lay.addLayout(unit_row(self._sp_smooth, "samples"))

        lay.addSpacing(12)
        lay.addWidget(QLabel("Raster period:"))
        self._sp_envelope = QuietDoubleSpinBox()
        self._sp_envelope.setRange(0.0, 100.0)
        self._sp_envelope.setDecimals(2)
        self._sp_envelope.setSingleStep(0.25)
        self._sp_envelope.setValue(SCOPE_ENVELOPE_MS)
        self._sp_envelope.setMaximumWidth(80)
        self._sp_envelope.setToolTip(
            "Set this to the raster ripple period you can see on the trace, "
            "and the analysis measures the ENVELOPE of the raster teeth "
            "instead of one tooth. 0 disables it.\n\n"
            "Match the ripple: a window wider than the ripple broadens the "
            "profile and reads high.")
        self._sp_envelope.valueChanged.connect(self._on_analysis_changed)
        lay.addLayout(unit_row(self._sp_envelope, "ms"))

        lay.addSpacing(12)
        lay.addWidget(QLabel("Scope averaging:"))
        self._cb_average = NoScrollComboBox()
        for label, value in (("off (sample)", 1), ("4", 4), ("16", 16),
                             ("64", 64), ("128", 128)):
            self._cb_average.addItem(label, value)
        idx = self._cb_average.findData(SCOPE_AVERAGE_SWEEPS)
        self._cb_average.setCurrentIndex(idx if idx >= 0 else 0)
        self._cb_average.setToolTip(
            "Sweeps averaged by the SCOPE itself. Cleans the trace before it "
            "is digitised, so it beats host-side smoothing — but it needs a "
            "repeating signal and a stable trigger.")
        self._cb_average.currentIndexChanged.connect(self._on_average_changed)
        lay.addWidget(self._cb_average)

        lay.addSpacing(12)
        lay.addWidget(QLabel("Channel:"))
        self._cb_channel = NoScrollComboBox()
        self._cb_channel.addItems(["CH1", "CH2"])
        self._cb_channel.currentTextChanged.connect(self._on_channel_changed)
        lay.addWidget(self._cb_channel)

        lay.addSpacing(12)
        lay.addWidget(QLabel("Polarity:"))
        self._cb_polarity = NoScrollComboBox()
        for label, value in (("auto", "auto"), ("positive", "pos"),
                             ("negative", "neg")):
            self._cb_polarity.addItem(label, value)
        self._cb_polarity.setToolTip(
            "Peak direction. 'auto' takes whichever excursion is larger; "
            "force it when the baseline wanders.")
        self._cb_polarity.currentIndexChanged.connect(self._on_analysis_changed)
        lay.addWidget(self._cb_polarity)

        lay.addSpacing(12)
        self._chk_show_raw = QCheckBox("Show raw trace")
        self._chk_show_raw.setChecked(True)
        lay.addWidget(self._chk_show_raw)

        lay.addStretch()
        return box

    def _build_load_box(self) -> QGroupBox:
        """The two knobs that decide how hard this tab works the link.

        Worth being blunt about the difference between them, because they
        are not interchangeable:

          Points   shortens the TRANSFER by carrying less of the record.
                   DATA:START/STOP take a contiguous slice - they do not
                   decimate - so half the points is half the TIME WINDOW at
                   the same resolution, and a peak outside the slice is
                   simply absent from the data.

          Interval leaves each acquisition exactly as it is and just does
                   fewer of them.  It costs nothing but update rate, which
                   makes it the first thing to reach for.
        """
        box = QGroupBox("Acquisition")
        lay = QHBoxLayout(box)

        lay.addWidget(QLabel("Mode:"))
        self._cb_mode = NoScrollComboBox()
        self._cb_mode.addItem("single shot", False)
        self._cb_mode.addItem("continuous", True)
        idx = self._cb_mode.findData(bool(SCOPE_CONTINUOUS_DEFAULT))
        self._cb_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self._cb_mode.setToolTip(
            "Single shot: the link stays open and nothing is fetched until "
            "you press Take shot. This is the default — a transfer takes "
            "over a second, and free-running it is what makes the app "
            "heavy.\n\n"
            "Continuous: fetch repeatedly, waiting the interval beside this "
            "between shots.")
        self._cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        lay.addWidget(self._cb_mode)

        lay.addSpacing(14)
        lay.addWidget(QLabel("Points:"))
        self._cb_points = NoScrollComboBox()
        for n in SCOPE_POINTS_CHOICES:
            share = 100.0 * n / SCOPE_RECORD_POINTS
            label = f"{n}" if n >= SCOPE_RECORD_POINTS else f"{n}  ({share:.0f} % of window)"
            self._cb_points.addItem(label, n)
        idx = self._cb_points.findData(SCOPE_POINTS)
        self._cb_points.setCurrentIndex(idx if idx >= 0 else 0)
        self._cb_points.setToolTip(
            "How much of the scope's 2500-point record to transfer.\n\n"
            "This CROPS the record — it does not coarsen it. 1000 points is "
            "1000 consecutive samples at full resolution, i.e. 40 % of the "
            "time window, with the rest discarded. A peak outside the slice "
            "is not in the data at all.\n\n"
            "Shorten the timebase to match, or leave it at 2500.")
        self._cb_points.currentIndexChanged.connect(self._on_points_changed)
        lay.addWidget(self._cb_points)

        lay.addWidget(QLabel("Keep:"))
        self._cb_anchor = NoScrollComboBox()
        self._cb_anchor.addItem("middle of the screen", "centre")
        self._cb_anchor.addItem("left edge", "start")
        idx = self._cb_anchor.findData(SCOPE_POINTS_ANCHOR)
        self._cb_anchor.setCurrentIndex(idx if idx >= 0 else 0)
        self._cb_anchor.setToolTip(
            "Which part of the record the shortened transfer keeps.")
        self._cb_anchor.currentIndexChanged.connect(self._on_points_changed)
        lay.addWidget(self._cb_anchor)

        lay.addSpacing(14)
        lay.addWidget(QLabel("Every:"))
        self._cb_poll = NoScrollComboBox()
        for secs in SCOPE_POLL_CHOICES:
            self._cb_poll.addItem("as fast as it can" if secs <= 0
                                  else f"{secs:g} s", secs)
        idx = self._cb_poll.findData(SCOPE_POLL_INTERVAL_S)
        self._cb_poll.setCurrentIndex(idx if idx >= 0 else 0)
        self._cb_poll.setToolTip(
            "In continuous mode, seconds to wait between shots. Each one "
            "still costs the same; there are just fewer of them, so nothing "
            "is lost but update rate — try this before shortening the "
            "record.")
        self._cb_poll.currentIndexChanged.connect(self._on_poll_changed)
        self._cb_poll.setEnabled(bool(SCOPE_CONTINUOUS_DEFAULT))
        lay.addWidget(self._cb_poll)

        lay.addSpacing(14)
        self._lbl_cost = QLabel("—")
        self._lbl_cost.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
        self._lbl_cost.setToolTip(
            "Measured time for the last waveform transfer, and the window it "
            "covered.")
        lay.addWidget(self._lbl_cost)

        lay.addStretch()
        return box

    def _on_points_changed(self, *_):
        self.beamline.set_scope_points(self._cb_points.currentData(),
                                       self._cb_anchor.currentData())
        self._cb_anchor.setEnabled(
            self._cb_points.currentData() < SCOPE_RECORD_POINTS)

    def _on_poll_changed(self, *_):
        self.beamline.set_scope_poll_interval(self._cb_poll.currentData())
        self.beamline.set_scope_continuous(bool(self._cb_mode.currentData()))

    def _build_waveform_box(self) -> QGroupBox:
        box = QGroupBox("Waveform (last acquisition)")
        lay = QVBoxLayout(box)
        self._fig_wave = Figure(figsize=(8, 3.0))
        self._canvas_wave = FigureCanvasQTAgg(self._fig_wave)
        self._canvas_wave.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax_wave = self._fig_wave.add_subplot(111)
        self._ax_wave.set_ylabel("Voltage (V)")
        self._ax_wave.set_xlabel("Time (ms)")
        self._ax_wave.grid(True, alpha=0.3)
        self._fig_wave.tight_layout()
        lay.addWidget(self._canvas_wave)
        return box

    def _build_history_box(self) -> QGroupBox:
        box = QGroupBox("FWHM History")
        lay = QVBoxLayout(box)
        self._fig_hist = Figure(figsize=(8, 2))
        self._canvas_hist = FigureCanvasQTAgg(self._fig_hist)
        self._canvas_hist.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ax_hist = self._fig_hist.add_subplot(111)
        self._ax_hist.set_ylabel("FWHM (ms)")
        self._ax_hist.set_xlabel("Time (s ago)")
        self._ax_hist.grid(True, alpha=0.3)
        self._fig_hist.tight_layout()
        lay.addWidget(self._canvas_hist)
        return box

    def _build_calibration_box(self) -> QGroupBox:
        box = QGroupBox("FWHM Reference (Calibration)")
        lay = QHBoxLayout(box)
        self._btn_save_ref = QPushButton("Save FWHM Reference")
        self._btn_save_ref.setEnabled(False)
        self._btn_save_ref.setToolTip(
            "Save the current FWHM reading as the reference value for this "
            "session.")
        self._btn_save_ref.clicked.connect(self._on_save_fwhm_ref)
        lay.addWidget(self._btn_save_ref)

        self._lbl_ref = QLabel(self._load_fwhm_ref_label())
        self._lbl_ref.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;")
        lay.addWidget(self._lbl_ref, stretch=1)
        return box

    # -----------------------------------------------------------------------
    # Connection bar
    # -----------------------------------------------------------------------

    def _on_connect_toggle(self):
        if self._connected:
            self.beamline.disconnect_scope()
            self._connected = False
            self._btn_connect.setText("Connect")
            self._btn_shot.setEnabled(False)
            self._picker.set_busy(False)
            self._pill.set_connected(False)
            self._lbl_status.setText("(disconnected)")
        else:
            port = self._picker.current_port()
            self._push_settings()
            self.beamline.connect_scope(port)
            self._connected = True
            self._btn_connect.setText("Disconnect")
            self._btn_shot.setEnabled(True)
            self._picker.set_busy(True)
            self._pill.set_connected(True, "● Connecting…")
            self._lbl_status.setText("Connecting…")

    def connect_if_needed(self) -> tuple:
        """Open the scope link if it is not already open.  (status, detail).

        For the Overview tab's Connect All.  With the picker on
        'Auto-detect' this hands None to connect_scope(), which probes the
        saved port first and then scans - the same thing pressing Connect
        does.
        """
        if self._connected or self.beamline.scope_connected:
            return "already", "scope already connected"
        self._on_connect_toggle()
        if self._connected:
            port = self._picker.current_port() or "auto-detected port"
            return "connected", f"scope on {port}"
        return "failed", "scope did not connect"

    # -----------------------------------------------------------------------
    # Analysis controls
    # -----------------------------------------------------------------------

    def _push_settings(self):
        """Send every analysis setting to the Beamline in one go."""
        self.beamline.set_scope_analysis(
            smooth=self._sp_smooth.value(),
            peaks=self._sp_peaks.value(),
            polarity=self._cb_polarity.currentData(),
            envelope_ms=self._sp_envelope.value(),
        )
        self.beamline.set_scope_average(self._cb_average.currentData())
        self.beamline.set_scope_channel(self._cb_channel.currentText())
        self.beamline.set_scope_points(self._cb_points.currentData(),
                                       self._cb_anchor.currentData())
        self.beamline.set_scope_poll_interval(self._cb_poll.currentData())
        self.beamline.set_scope_continuous(bool(self._cb_mode.currentData()))

    def _on_analysis_changed(self, *_):
        self.beamline.set_scope_analysis(
            smooth=self._sp_smooth.value(),
            peaks=self._sp_peaks.value(),
            polarity=self._cb_polarity.currentData(),
            envelope_ms=self._sp_envelope.value(),
        )

    def _on_average_changed(self, *_):
        self.beamline.set_scope_average(self._cb_average.currentData())

    def _on_channel_changed(self, text: str):
        self.beamline.set_scope_channel(text)

    # -----------------------------------------------------------------------
    # Beamline signal handlers
    # -----------------------------------------------------------------------

    def _on_scope_state(self, state):
        """Receive ScopeState; cache for the next redraw tick."""
        self._last_state = state
        self._last_time  = time.time()
        self._readout_dirty = True

        # `_plot_dirty` keys off the MEASUREMENT, not off `not state.idle`:
        # an error snapshot has idle=False and no trace, and used to arm a
        # redraw that could only early-return - consuming the redraw that a
        # good snapshot had asked for.
        if _carries_measurement(state):
            self._last_measured = state
            self._plot_dirty    = True

        if self._shot_pending and not state.idle:
            self._shot_finished()

        if state.connected and not math.isnan(state.fwhm_seconds):
            self._btn_save_ref.setEnabled(True)

        # Idle pings, disconnects and error reports used to append a
        # (t, nan, []) row apiece, padding the history with rows that plot
        # nothing and push real points off the end of the window.
        if _carries_measurement(state):
            per_peak = [(p.get("axis", "?"), p.get("fwhm_seconds", math.nan))
                        for p in state.peaks]
            self._fwhm_history.append(
                (time.time(), state.fwhm_seconds, per_peak))
            if len(self._fwhm_history) > _MAX_HISTORY:
                self._fwhm_history = self._fwhm_history[-_MAX_HISTORY:]

    def _on_scope_error(self, msg: str):
        log.warning("profiler_tab: scope error: %s", msg)
        # An error IS the end of the shot.  Waiting for a waveform that is
        # never coming is what left the button disabled.
        if self._shot_pending:
            self._shot_finished()
        self._lbl_status.setText(f"Error: {msg[:60]}")
        self._lbl_status.setStyleSheet(
            f"color: {theme.FAULT}; font-style: italic;")

    # -----------------------------------------------------------------------
    # Readout redraw (200 ms)
    # -----------------------------------------------------------------------

    def _redraw_readout(self):
        """The status line describes the LINK and comes from `status`;
        every value above it describes the BEAM and comes from `state`."""
        status = self._last_state
        if status is None or not self._readout_dirty:
            return
        self._readout_dirty = False

        # ---- link status: from the latest snapshot of any kind -----------
        if status.connected and status.idle:
            self._pill.set_connected(True, "● Connected — idle")
        elif status.connected and status.continuous:
            self._pill.set_connected(True, "● Connected — continuous")
        else:
            self._pill.set_connected(bool(status.connected))
        self._lbl_channel.setText(status.channel)

        # ---- the numbers: from the last snapshot that measured something -
        # An idle snapshot no longer returns here.  It used to, and that is
        # why the widths, r² and S/N were usually never written at all: in
        # single-shot mode the idle ping lands a few milliseconds after the
        # measurement and wins the 200 ms readout timer nearly every time.
        state = self._last_measured
        if state is None:
            if status.idle:
                self._lbl_status.setText(
                    "idle — scope connected, press Take shot (F5)")
                self._lbl_status.setStyleSheet(
                    f"color: {theme.NEUTRAL}; font-style: italic;")
            return

        # ---- one width per axis ------------------------------------------
        by_axis = {p.get("axis"): p for p in state.peaks}
        for axis, lbl in self._lbl_axis_value.items():
            peak = by_axis.get(axis)
            if peak is not None and peak.get("resolved"):
                num, unit = fmt_seconds(peak["fwhm_seconds"])
                colour = theme.OK
            elif peak is not None:
                num, unit, colour = "unresolved", "", theme.WARN
            else:
                num, unit, colour = "—", "", theme.NEUTRAL
            size = theme.FS_BIG if unit else theme.FS_LABEL
            lbl.setText(num)
            lbl.setStyleSheet(
                f"font-size: {size}px; font-weight: bold; color: {colour};")
            self._lbl_axis_name[axis].setText(unit)

        ratio = state.xy_ratio
        self._lbl_ratio.setText("—" if math.isnan(ratio) else f"{ratio:.2f}")

        # ---- where the number came from ----------------------------------
        if state.fwhm_source == "fit":
            src = "Gaussian fit"
        elif state.fwhm_source == "half-max":
            src = "half-maximum (fit not trusted)"
        else:
            src = "—"
        if state.envelope_samples > 1:
            src += f", raster envelope ({state.envelope_samples} samples)"
        self._lbl_source.setText(src)

        if state.separations_seconds:
            self._lbl_sep.setText(
                "  ".join(fmt_seconds_str(s) for s in state.separations_seconds))
        else:
            self._lbl_sep.setText("—")

        # ---- fit quality --------------------------------------------------
        r2 = state.fit_r_squared
        if not math.isnan(r2):
            self._lbl_r2.setText(f"{r2:.3f}")
            self._lbl_r2.setStyleSheet(theme.status_label(
                theme.OK if r2 >= 0.90 else theme.WARN, bold=False))
        else:
            self._lbl_r2.setText("—")
            self._lbl_r2.setStyleSheet("")

        snr = state.signal_to_noise
        self._lbl_snr.setText("—" if math.isnan(snr) else f"{snr:.0f}")

        # What the last acquisition actually cost, measured rather than
        # predicted — the honest answer to "is this setting helping?".
        if state.points and not math.isnan(state.transfer_seconds):
            window = ""
            if state.points < SCOPE_RECORD_POINTS:
                window = (f", {100.0 * state.points / SCOPE_RECORD_POINTS:.0f} %"
                          f" of the window")
            self._lbl_cost.setText(
                f"last transfer {state.transfer_seconds:.2f} s "
                f"({state.points} pts{window})")
        else:
            self._lbl_cost.setText("—")
        self._lbl_pwidth.setText(fmt_seconds_str(state.scope_pwidth_seconds))

        # ---- the warning line ---------------------------------------------
        warnings = []
        if state.clipped_low or state.clipped_high:
            where = "bottom" if state.clipped_low else "top"
            warnings.append(
                f"Trace is clipped at the {where} of the screen "
                f"({state.clipped_fraction * 100:.0f} % of samples) — a "
                f"flat-topped peak has no true maximum, so these widths are "
                f"not real. Change the volts/div or vertical position.")
        if (not math.isnan(r2) and r2 < 0.90 and state.envelope_samples <= 1
                and state.n_peaks >= 1):
            warnings.append(
                "Fit r² is low. If the beam is rastered, set the raster "
                "period so the envelope is measured instead of one tooth.")
        if warnings:
            self._lbl_warn.setText("  ".join(warnings))
            self._lbl_warn.setVisible(True)
        else:
            self._lbl_warn.setVisible(False)

        # ---- status line ---------------------------------------------------
        # Read from `status`, not `state`: this line is about the link right
        # now, while everything above it is about the last beam measured.
        # An idle link reads "idle", not "OK", with the widths still on
        # screen above it.
        if status.idle:
            self._lbl_status.setText(
                "idle — scope connected, press Take shot (F5)")
            self._lbl_status.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-style: italic;")
        elif status.error:
            self._lbl_status.setText(status.error[:80])
            self._lbl_status.setStyleSheet(
                f"color: {theme.WARN}; font-style: italic;")
        elif status.connected:
            self._lbl_status.setText("OK")
            self._lbl_status.setStyleSheet(
                f"color: {theme.OK}; font-style: italic;")

    # -----------------------------------------------------------------------
    # Plot redraw (1 s)
    # -----------------------------------------------------------------------

    def _redraw_plots(self):
        if not self._plot_dirty:
            return
        # Clear the flag only on a draw that actually happened.  Clearing it
        # first and then early-returning is how a redraw armed by a good
        # snapshot got thrown away and never came back.
        if self._redraw_waveform():
            self._plot_dirty = False
        elif self._last_measured is None:
            self._plot_dirty = False      # nothing to draw yet; don't spin
        self._redraw_fwhm_history()

    def _redraw_waveform(self) -> bool:
        state = self._last_measured
        if state is None or not state.corrected_downsampled:
            return False

        ax = self._ax_wave
        ax.cla()
        ax.set_ylabel("Voltage (V)")
        ax.set_xlabel("Time (ms)")
        ax.grid(True, alpha=0.3)

        corrected = state.corrected_downsampled
        n = len(corrected)

        # Time axis uses the DOWNSAMPLED step, not xincr: plotting
        # downsampled points against xincr compresses the axis and every
        # time on it silently becomes wrong.
        dt = state.xincr_downsampled
        t0 = state.xzero
        if math.isnan(dt) or math.isnan(t0):
            xs = list(range(n))
            ax.set_xlabel("Sample index")
        else:
            xs = [(t0 + i * dt) * 1e3 for i in range(n)]

        if self._chk_show_raw.isChecked() and state.volts_downsampled:
            sign = -1.0 if state.flipped else 1.0
            base = state.baseline_volts
            if not math.isnan(base):
                raw = [sign * (v - base) for v in state.volts_downsampled]
                ax.plot(xs[:len(raw)], raw[:len(xs)], linewidth=0.6,
                        color="#c8c8c8", label="raw", zorder=1)

        # Name the blue trace for what it actually is: once the raster
        # envelope is on, the plotted line is the envelope, not a smoothed
        # copy of the signal, and calling it "smoothed" would invite reading
        # the raster teeth as noise.
        if state.envelope_samples > 1:
            label = f"raster envelope ({state.envelope_samples} samples)"
        elif state.smooth_window > 1:
            label = "smoothed ×%d" % state.smooth_window
        else:
            label = state.channel
        ax.plot(xs, corrected, linewidth=1.0, color="#2980b9", label=label,
                zorder=2)

        if state.fit_curve_downsampled:
            fit_curve = state.fit_curve_downsampled
            ax.plot(xs[:len(fit_curve)], fit_curve[:len(xs)], linewidth=1.2,
                    linestyle="--", color="#e67e22", zorder=3,
                    label=f"fit r²={state.fit_r_squared:.3f}")

        # One half-maximum span per peak, drawn where the eye expects it.
        peak_v = max(corrected) if corrected else 1.0
        for k, p in enumerate(state.peaks):
            colour = _PEAK_COLORS[k % len(_PEAK_COLORS)]
            if not p.get("resolved"):
                centre = p.get("centre_seconds", math.nan)
                if not math.isnan(centre):
                    ax.axvline(centre * 1e3, linewidth=0.8, linestyle=":",
                               color=theme.FAULT)
                continue
            half = p["half_volts"]
            lx, rx = p["left_seconds"] * 1e3, p["right_seconds"] * 1e3
            ax.annotate("", xy=(lx, half), xytext=(rx, half),
                        arrowprops=dict(arrowstyle="<->", lw=1.3,
                                        color=colour))
            ax.text((lx + rx) / 2, half + 0.06 * peak_v,
                    f"{p.get('axis', '')}  {fmt_seconds_str(p['fwhm_seconds'])}",
                    ha="center", va="bottom", fontsize=8, color=colour)

        ax.set_ylim(min(corrected) - 0.08 * peak_v, peak_v * 1.30)
        ax.legend(loc="upper right", fontsize=8)
        self._fig_wave.tight_layout()
        self._canvas_wave.draw_idle()
        return True

    def _redraw_fwhm_history(self):
        """One line per AXIS. Never a single combined trace: X and Y moving
        apart is the interesting event, and averaging them hides it."""
        if not self._fwhm_history:
            return
        now = time.time()

        ax = self._ax_hist
        ax.cla()
        ax.set_ylabel("FWHM (ms)")
        ax.set_xlabel("Time (s ago)")
        ax.grid(True, alpha=0.3)

        drew = False
        axes_seen = []
        for _t, _f, per in self._fwhm_history:
            for entry in per:
                if entry[0] not in axes_seen:
                    axes_seen.append(entry[0])

        for k, axis in enumerate(axes_seen[:SCOPE_MAX_PEAKS]):
            pts = []
            for t, _f, per in self._fwhm_history:
                for name, width in per:
                    if name == axis and width == width and width > 0:
                        pts.append((now - t, width * 1e3))
            if pts:
                ax.plot([x for x, _ in pts], [y for _, y in pts],
                        linewidth=1.1,
                        color=_PEAK_COLORS[k % len(_PEAK_COLORS)],
                        label=f"{axis}")
                drew = True

        if drew:
            ax.legend(loc="upper left", fontsize=8, ncol=4)
            ax.invert_xaxis()
            self._fig_hist.tight_layout()
            self._canvas_hist.draw_idle()

    # -----------------------------------------------------------------------
    # Calibration hook
    # -----------------------------------------------------------------------

    def _on_save_fwhm_ref(self):
        state = self._last_measured
        if state is None or math.isnan(state.fwhm_seconds):
            return
        iso = datetime.now(timezone.utc).isoformat()
        entry = {
            "fwhm_seconds":      state.fwhm_seconds,
            "fwhm_samples":      state.fwhm_samples,
            "fwhm_source":       state.fwhm_source,
            "fwhm_x_seconds":    state.fwhm_x_seconds,
            "fwhm_y_seconds":     state.fwhm_y_seconds,
            "xy_ratio":          state.xy_ratio,
            "peak_fwhm_seconds": {p.get("axis", "?"): p.get("fwhm_seconds")
                                  for p in state.peaks},
            "envelope_samples":  state.envelope_samples,
            "fit_r_squared":     state.fit_r_squared,
            "channel":           state.channel,
            "xincr":             state.xincr,
            "smooth_window":     state.smooth_window,
            "saved_iso":         iso,
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
        source = entry.get("fwhm_source", "")
        if fwhm_s is None or math.isnan(fwhm_s):
            return "(no reference saved)"
        tail = f" via {source}" if source else ""
        return (f"Reference: {fmt_seconds_str(fwhm_s, 4)}{tail}  "
                f"(saved {iso[:19].replace('T', ' ')} UTC)")

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
