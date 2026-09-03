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

MILLIMETRES
-----------
The scope measures TIME.  A width in milliseconds only becomes a width in
millimetres once something of a known real-space size has been seen at the
same sweep speed - and that is what the BPM's fiducial marks are for.  With
the BPM controller's output selector on fiducial marks, "Calibrate" measures
the gap between the two calibration peaks, divides the head's known 6 cm by
it, and stores the resulting mm/s against the BPM by name.

From then on every width on this tab, on the waveform plot, in the history
chart and in the CSV log carries its millimetre value BESIDE its
milliseconds - never instead of it.  The calibration is a rate (the speed
the BPM sweeps its wire across the aperture), so it survives any later
change of timebase, volts/div or record length.  It does NOT survive
changing which BPM the controller is showing, which is why calibrations are
stored per BPM and the active one is named on screen.

THE WIDTH LADDER — ONE WIDTH IS NOT A BEAM
------------------------------------------
Half maximum describes the CORE and says nothing about where the beam ends.
That matters here specifically: the raster planner derives the overscan
needed for a given edge droop by inverting a normal CDF from the FWHM alone,
so every droop figure it prints rests on the beam being Gaussian.

So each peak is measured at three levels, every shot, independently:

    FWHM     50 %      the core
    FW1/e²   13.5 %    the optics convention; exactly 4σ for a Gaussian
    FWTM     10 %      the tails

For a true Gaussian these are locked together — FW(f)/FWHM = √(ln(1/f)/ln2),
so FWTM/FWHM = 1.8226 and FW1/e²/FWHM = 1.6986. The MEASURED ratio is
therefore the Gaussian assumption on trial: above 1.8226 is heavier tails
than Gaussian (halo, and the planner is optimistic); below it is a
flat-topped or scraped profile.

Nothing is carried between shots and no level is inferred from another. With
quadrupole focusing the profile is not the same shape twice, so a width
derived from a remembered shape would be describing a beam that has gone.

A level can fail in two ways and they mean different things: it can sit
BELOW THE NOISE FLOOR (refused - a crossing found there is a crossing of the
noise), or the two peaks' SKIRTS CAN OVERLAP so the trace never falls that
low before the neighbour. The second is real information about the beam. In
both cases the fit's number is offered beside it and LABELLED fit-derived;
neither is ever quietly truncated at the fence, which would read as a narrow
beam.

The tail RATIO has no fit fallback at all, deliberately: a sum-of-Gaussians
fit's own ratio is 1.8226 by construction, so a fit-derived value there
would read as "perfectly Gaussian" when it means "nobody measured".

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
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rbl.config.scope_config import (
    SCOPE_AVERAGE_SWEEPS,
    SCOPE_AXIS_LABELS,
    SCOPE_CONTINUOUS_DEFAULT,
    SCOPE_ENVELOPE_MS,
    SCOPE_EXPECTED_PEAKS,
    SCOPE_MAX_PEAKS,
    SCOPE_POINTS,
    SCOPE_POINTS_ANCHOR,
    SCOPE_POINTS_CHOICES,
    SCOPE_POLL_CHOICES,
    SCOPE_POLL_INTERVAL_S,
    SCOPE_RECORD_POINTS,
    SCOPE_SMOOTH_WINDOW,
    SCOPE_TAIL_TOLERANCE,
    SCOPE_WIDTH_LEVELS,
)
from rbl.gui import theme
from rbl.gui.widgets.bpm_calibration_panel import BpmCalibrationPanel
from rbl.gui.widgets.connection_bar import StatusPill
from rbl.gui.widgets.inputs import (
    NoScrollComboBox,
    NoScrollSpinBox,
    QuietDoubleSpinBox,
    unit_row,
)
from rbl.gui.widgets.port_picker import PortPicker
from rbl.hardware.bpm_calibration import seconds_to_mm
from rbl.hardware.profile_fwhm import (
    LEVEL_FWTM,
    gaussian_width_ratio,
    level_label,
)
from rbl.services.profile_logger import ProfileLogger

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

        # Profile data logger (CSV + waveform dump)
        self._logger: ProfileLogger = None

        # ---- BPM calibration -------------------------------------------
        # BPM calibration state is managed by self.bpm_cal_panel (below).
        # The host keeps _saved_analysis (analysis spinbox values stashed
        # when entering cal mode) and _mm_per_second (updated via signal).
        self._saved_analysis  = None
        self._mm_per_second   = math.nan

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
        layout.addWidget(self._build_widths_box())
        layout.addWidget(self._build_controls_box())
        layout.addWidget(self._build_load_box())
        layout.addWidget(self._build_waveform_box(), stretch=3)
        layout.addWidget(self._build_history_box(), stretch=2)
        self.bpm_cal_panel = BpmCalibrationPanel(beamline=self.beamline)
        layout.addWidget(self.bpm_cal_panel)
        layout.addWidget(self._build_calibration_box())
        layout.addWidget(self._build_logging_box())

        # ── Subscribe to Beamline signals ─────────────────────────────────
        self.beamline.scope_changed.connect(self._on_scope_state)
        self.beamline.scope_error.connect(self._on_scope_error)

        # ── BPM calibration panel signals ─────────────────────────────────
        self.bpm_cal_panel.scale_changed.connect(self._on_bpm_scale_changed)
        self.bpm_cal_panel.entering_cal_mode.connect(self._on_bpm_entering_cal_mode)
        self.bpm_cal_panel.exiting_cal_mode.connect(self._on_bpm_exiting_cal_mode)
        self.bpm_cal_panel.take_shot_requested.connect(self._on_take_shot)
        self.bpm_cal_panel.cal_state_ready.connect(self._redraw_fiducials)

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

    def _build_widths_box(self) -> QGroupBox:
        """The width ladder: one row per level, one column per axis.

        A table rather than three more headline numbers, because the point
        of the ladder is the COMPARISON down a column - a width at 10 % that
        is more than 1.8226 times the width at 50 % is the whole finding,
        and that is only visible with the levels stacked.

        Each cell also carries where its number came from. A measured width
        and a fit-derived one are different kinds of claim, and the tab never
        shows one in a way that could be read as the other.
        """
        box = QGroupBox("Width Ladder")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3)

        axes = list(SCOPE_AXIS_LABELS[:2])
        head = QLabel("Level")
        head.setStyleSheet(f"color: {theme.NEUTRAL}; "
                           f"font-size: {theme.FS_CAPTION}px;")
        grid.addWidget(head, 0, 0)
        for col, axis in enumerate(axes):
            lbl = QLabel(axis)
            lbl.setStyleSheet("font-weight: bold;")
            grid.addWidget(lbl, 0, col + 1)
        vs_g = QLabel("vs Gaussian")
        vs_g.setStyleSheet(f"color: {theme.NEUTRAL}; "
                           f"font-size: {theme.FS_CAPTION}px;")
        vs_g.setToolTip(
            "What this level's width would be, for a Gaussian with the "
            "measured FWHM. FW(f)/FWHM = √(ln(1/f)/ln 2) — a property of the "
            "Gaussian function, not of any beam, which is why it is the one "
            "number here that does not come from the trace.")
        grid.addWidget(vs_g, 0, len(axes) + 1)

        self._lbl_level = {}          # (label, axis) -> QLabel
        self._lbl_level_ratio = {}    # label -> QLabel
        for row, level in enumerate(SCOPE_WIDTH_LEVELS, start=1):
            label = level_label(level)
            name = QLabel(f"{label}  ({level * 100:g} %)")
            name.setStyleSheet(f"color: {theme.NEUTRAL};")
            if abs(level - 0.13533528323661270) < 1e-6:
                name.setToolTip(
                    "The optics convention for beam diameter. For a Gaussian "
                    "this width is exactly 4σ.")
            elif abs(level - 0.10) < 1e-9:
                name.setToolTip(
                    "Full width at tenth maximum — where the tails are. Its "
                    "ratio to FWHM is what tests whether the beam is really "
                    "Gaussian.")
            grid.addWidget(name, row, 0)
            for col, axis in enumerate(axes):
                cell = QLabel("—")
                grid.addWidget(cell, row, col + 1)
                self._lbl_level[(label, axis)] = cell
            expect = QLabel(f"×{gaussian_width_ratio(level):.4f}")
            expect.setStyleSheet(f"color: {theme.NEUTRAL}; "
                                 f"font-size: {theme.FS_CAPTION}px;")
            grid.addWidget(expect, row, len(axes) + 1)
            self._lbl_level_ratio[label] = expect

        # The verdict line. This is what the table is FOR.
        self._lbl_tails = QLabel("—")
        self._lbl_tails.setWordWrap(True)
        self._lbl_tails.setToolTip(
            "Measured FWTM ÷ measured FWHM, against the 1.8226 a true "
            "Gaussian gives.\n\n"
            "Heavier tails mean the raster planner's edge-droop and overscan "
            "figures — which invert a normal CDF from the FWHM — are "
            "optimistic. Lighter means a flat-topped or scraped profile.\n\n"
            "There is no fit fallback here on purpose: a Gaussian fit's own "
            "ratio is 1.8226 whatever the beam is doing.")
        grid.addWidget(self._lbl_tails, len(SCOPE_WIDTH_LEVELS) + 1, 0,
                       1, len(axes) + 2)

        grid.setColumnStretch(len(axes) + 2, 1)
        return box

    def _fmt_level_cell(self, rec: dict) -> tuple:
        """(text, colour) for one level of one axis.

        Three outcomes, never blurred together: a measured width, a
        fit-derived stand-in that says so, and nothing at all with the reason
        in the tooltip.
        """
        if rec is None:
            return "—", theme.NEUTRAL
        secs = rec.get("seconds", math.nan)
        mm   = seconds_to_mm(secs, self._mm_per_second)
        if rec.get("resolved") and secs == secs:
            if mm == mm:
                return f"{mm:.3g} mm  ({fmt_seconds_str(secs)})", theme.OK
            return fmt_seconds_str(secs), theme.OK
        if rec.get("source") == "fit":
            fit_s = rec.get("fit_seconds", math.nan)
            fit_mm = seconds_to_mm(fit_s, self._mm_per_second)
            shown = (f"{fit_mm:.3g} mm" if fit_mm == fit_mm
                     else fmt_seconds_str(fit_s))
            return f"{shown}  (fit)", theme.WARN
        return "—", theme.WARN

    def _redraw_widths(self, state):
        """Fill the ladder from one snapshot."""
        by_axis = {p.get("axis"): p for p in state.peaks}
        for (label, axis), cell in self._lbl_level.items():
            peak = by_axis.get(axis) or {}
            rec  = (peak.get("levels") or {}).get(label)
            text, colour = self._fmt_level_cell(rec)
            cell.setText(text)
            cell.setStyleSheet(f"color: {colour};")
            cell.setToolTip((rec or {}).get("note", ""))

        # ---- the verdict --------------------------------------------------
        expected = gaussian_width_ratio(LEVEL_FWTM)
        parts, worst, colour = [], 0.0, theme.OK
        for axis, ratio, excess in (
                (SCOPE_AXIS_LABELS[0], state.tail_ratio_x, state.tail_excess_x),
                (SCOPE_AXIS_LABELS[1] if len(SCOPE_AXIS_LABELS) > 1 else "Y",
                 state.tail_ratio_y, state.tail_excess_y)):
            if ratio != ratio:
                continue
            parts.append(f"{axis} {ratio:.3f} ({excess * 100:+.1f} %)")
            if abs(excess) > abs(worst):
                worst = excess

        if not parts:
            self._lbl_tails.setText(
                state.tail_note or "Tails: not measured this shot.")
            self._lbl_tails.setStyleSheet(
                f"color: {theme.NEUTRAL}; font-size: {theme.FS_CAPTION}px;")
            return

        verdict = "consistent with a Gaussian"
        if worst > SCOPE_TAIL_TOLERANCE:
            colour  = theme.WARN
            verdict = ("HEAVIER tails than Gaussian — the raster planner's "
                       "edge-droop and overscan figures are optimistic")
        elif worst < -SCOPE_TAIL_TOLERANCE:
            colour  = theme.WARN
            verdict = ("LIGHTER tails than Gaussian — a flat-topped or "
                       "scraped profile; check for an aperture upstream")
        self._lbl_tails.setText(
            f"Tails:  FWTM/FWHM = {'   '.join(parts)}   "
            f"vs {expected:.4f} for a Gaussian  —  {verdict}")
        self._lbl_tails.setStyleSheet(
            theme.status_label(colour, bold=False)
            + f"font-size: {theme.FS_CAPTION}px;")

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

    def _build_logging_box(self) -> QGroupBox:
        box = QGroupBox("Data Logging")
        lay = QHBoxLayout(box)
        self._btn_log = QPushButton("Start Logging")
        self._btn_log.setEnabled(False)
        self._btn_log.setToolTip(
            "Log every acquisition's waveform and FWHM stats to CSV files "
            "under ~/Desktop/RBL_log/data/scope/.\n\n"
            "Same format as vacuum and calibration logs.")
        self._btn_log.clicked.connect(self._on_log_toggle)
        lay.addWidget(self._btn_log)

        self._lbl_log_path = QLabel("(not logging)")
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;")
        lay.addWidget(self._lbl_log_path, stretch=1)
        return box

    def _on_log_toggle(self):
        if self._logger is None:
            self._start_logging()
        else:
            self._stop_logging()

    def _start_logging(self):
        self._logger = ProfileLogger()
        self._btn_log.setText("Stop Logging")
        self._lbl_log_path.setText(self._logger.csv_path)
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.OK}; font-size: 10px; font-style: italic;")
        log.info("profiler_tab: logging started -> %s", self._logger.csv_path)

    def _stop_logging(self):
        if self._logger is None:
            return
        path = self._logger.close()
        self._logger = None
        self._btn_log.setText("Start Logging")
        self._lbl_log_path.setText(f"Saved: {path}")
        self._lbl_log_path.setStyleSheet(
            f"color: {theme.NEUTRAL}; font-size: 10px; font-style: italic;")
        log.info("profiler_tab: logging stopped -> %s", path)

    # -----------------------------------------------------------------------
    # BPM scope calibration: milliseconds -> millimetres
    # -----------------------------------------------------------------------

    # ---- BPM calibration panel slots ------------------------------------

    def _on_bpm_scale_changed(self, mm_per_second: float, _name: str) -> None:
        """Scale updated by the BPM calibration panel — refresh all displays."""
        self._mm_per_second = mm_per_second
        self._readout_dirty = True
        self._plot_dirty    = True

    def _on_bpm_entering_cal_mode(self) -> None:
        """BPM panel is entering fiducial mode — disable analysis controls."""
        self._saved_analysis = {
            "peaks":       self._sp_peaks.value(),
            "smooth":      self._sp_smooth.value(),
            "envelope_ms": self._sp_envelope.value(),
        }
        for w in (self._sp_peaks, self._sp_smooth, self._sp_envelope):
            w.setEnabled(False)

    def _on_bpm_exiting_cal_mode(self) -> None:
        """BPM panel is exiting fiducial mode — restore analysis controls."""
        for w in (self._sp_peaks, self._sp_smooth, self._sp_envelope):
            w.setEnabled(True)
        if self._saved_analysis:
            self._sp_peaks.setValue(self._saved_analysis["peaks"])
            self._sp_smooth.setValue(self._saved_analysis["smooth"])
            self._sp_envelope.setValue(self._saved_analysis["envelope_ms"])
            self._saved_analysis = None
        self._on_analysis_changed()
        self._plot_dirty = True

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
        # The mm scale is pushed with everything else, so a reconnect comes
        # back calibrated. A scale that quietly vanishes on reconnect is
        # worse than none: the readings keep their format and change their
        # meaning.
        self.beamline.set_scope_mm_scale(self._mm_per_second, self._cal_active)
        self.beamline.set_scope_calibration_mode(
            self._cal_mode, spacing_mm=self._sp_spacing.value(),
            override=self._current_override())

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

        # A FIDUCIAL trace is not a beam measurement. It must not enter the
        # FWHM history, must not be written to the profile log, and must not
        # arm "Save FWHM Reference" - those all describe a beam, and there
        # is no beam on this trace. Everything it does mean is handled by
        # _on_calibration_state.
        if getattr(state, "cal_mode", False):
            self.bpm_cal_panel.on_scope_state(state)
            return

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

            self._btn_log.setEnabled(True)
            if self._logger is None:
                self._start_logging()
            if self._logger is not None:
                try:
                    self._logger.write_state(state)
                except Exception:
                    log.exception("profiler_tab: logger write failed")

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

        # In calibration mode the trace on screen is fiducial marks, so
        # every beam number here belongs to the last BEAM shot, not to what
        # is plotted. They are left exactly as they were rather than blanked
        # or, worse, recomputed from marks - and the status line says which
        # kind of trace is showing.
        if getattr(state, "cal_mode", False):
            self._lbl_status.setText(
                "calibration mode — fiducial marks, not a beam "
                "(the widths above are from the last beam shot)")
            self._lbl_status.setStyleSheet(
                f"color: {theme.WARN}; font-style: italic;")
            return

        # ---- one width per axis ------------------------------------------
        # When a calibration is in force the HEADLINE is millimetres and the
        # milliseconds ride along beside it. Millimetres are what the beam
        # actually is; milliseconds are how this instrument happened to
        # measure it. But the ms is never dropped - it is the raw
        # measurement, and it is what a later recalibration would rescale.
        # The scale used is the one in force NOW, not the one that was in
        # force when the shot was taken. That is deliberate and it matches
        # the history chart: mm/s is a property of the BPM's sweep, not of
        # the moment a shot happened, so recalibrating is a better reading
        # of every measurement rather than a new era. The snapshot keeps its
        # own `fwhm_mm` for the log, which is where the value as-recorded
        # belongs.
        mmps = self._mm_per_second
        by_axis = {p.get("axis"): p for p in state.peaks}
        for axis, lbl in self._lbl_axis_value.items():
            peak = by_axis.get(axis)
            if peak is not None and peak.get("resolved"):
                secs = peak["fwhm_seconds"]
                width_mm = seconds_to_mm(secs, mmps)
                if width_mm == width_mm:
                    num  = f"{width_mm:.3g}"
                    unit = f"mm  ({fmt_seconds_str(secs)})"
                else:
                    num, unit = fmt_seconds(secs)
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

        self._redraw_widths(state)

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
        if getattr(state, "cal_mode", False):
            return self._redraw_fiducials(state)

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
            label = f"{p.get('axis', '')}  {fmt_seconds_str(p['fwhm_seconds'])}"
            width_mm = seconds_to_mm(p["fwhm_seconds"], self._mm_per_second)
            if width_mm == width_mm:
                label = (f"{p.get('axis', '')}  {width_mm:.3g} mm\n"
                         f"{fmt_seconds_str(p['fwhm_seconds'])}")
            ax.text((lx + rx) / 2, half + 0.06 * peak_v, label,
                    ha="center", va="bottom", fontsize=8, color=colour)

            # The rest of the ladder, drawn thin and unannotated. The half
            # maximum keeps the arrow and the number because it is the
            # headline; the lower levels are here so the SHAPE between them
            # is visible - a beam whose 10 % span is much wider than 1.82×
            # its 50 % span looks wrong on the trace before any ratio is
            # read. Only MEASURED spans are drawn: a fit-derived width has
            # no crossings on this trace to draw between, and inventing them
            # would put a line where no data supports one.
            for lvl_label, rec in (p.get("levels") or {}).items():
                if lvl_label == "FWHM" or not rec.get("resolved"):
                    continue
                l2, r2 = rec.get("left_seconds"), rec.get("right_seconds")
                if l2 is None or r2 is None or math.isnan(l2) or math.isnan(r2):
                    continue
                yv = rec.get("volts", math.nan)
                if math.isnan(yv):
                    continue
                ax.plot([l2 * 1e3, r2 * 1e3], [yv, yv], linewidth=0.9,
                        linestyle=":", color=colour, alpha=0.75, zorder=4)
                ax.text(l2 * 1e3, yv, f"{lvl_label} ", ha="right",
                        va="center", fontsize=7, color=colour, alpha=0.9)

        ax.set_ylim(min(corrected) - 0.08 * peak_v, peak_v * 1.30)
        ax.legend(loc="upper right", fontsize=8)
        self._fig_wave.tight_layout()
        self._canvas_wave.draw_idle()
        return True

    def _redraw_fiducials(self, state) -> bool:
        """Draw a fiducial trace with the peak choice made visible.

        This plot IS the confirmation step. The auto-pick drops the tallest
        peak as the trigger, which is right on every trace anyone has shown
        it - but "right on every trace so far" is not a thing to hide a
        divide behind. So the trigger is drawn greyed and labelled as
        ignored, the two marks that were used are drawn in colour, and the
        span between them is annotated with both the time measured and the
        real-space distance assumed. If the app picked wrong, it is wrong on
        screen before it is wrong in a saved calibration.
        """
        ax = self._ax_wave
        ax.cla()
        ax.set_ylabel("Voltage (V)")
        ax.set_xlabel("Time (ms)")
        ax.grid(True, alpha=0.3)

        corrected = state.corrected_downsampled
        n  = len(corrected)
        dt = state.xincr_downsampled
        t0 = state.xzero
        if math.isnan(dt) or math.isnan(t0):
            xs = list(range(n))
            ax.set_xlabel("Sample index")
        else:
            xs = [(t0 + i * dt) * 1e3 for i in range(n)]

        ax.plot(xs, corrected, linewidth=1.0, color="#2980b9",
                label="fiducial marks", zorder=2)

        peak_v = max(corrected) if corrected else 1.0
        # Peak positions come back in seconds from the trigger, the same
        # frame the trace is plotted in - no sample-index arithmetic here,
        # and so no way for the downsampling factor to move a marker.
        # cal_peaks carry apex times measured from the START OF THE RECORD.
        # xzero shifts them into the same frame the trace is plotted in; it
        # cancels out of the separation, which is why the calibration itself
        # never needs it.
        shift = 0.0 if math.isnan(t0) else t0
        for k, p in enumerate(state.cal_peaks):
            apex = p.get("apex_seconds", math.nan)
            if math.isnan(apex):
                continue
            t_ms = (apex + shift) * 1e3
            if p.get("role") == "fiducial":
                ax.axvline(t_ms, linewidth=1.2, color="#2980b9", alpha=0.9)
                ax.text(t_ms, peak_v * 1.10, f"peak {k + 1}", ha="center",
                        va="bottom", fontsize=8, color="#2980b9")
            elif p.get("role") == "trigger":
                ax.axvline(t_ms, linewidth=1.0, linestyle="--",
                           color="#9aa0a6")
                ax.text(t_ms, peak_v * 1.10,
                        f"peak {k + 1}\ntrigger — ignored", ha="center",
                        va="bottom", fontsize=8, color="#9aa0a6")
            else:
                ax.axvline(t_ms, linewidth=0.8, linestyle=":",
                           color="#9aa0a6")

        fid = list(state.cal_fiducial_seconds or [])
        if len(fid) == 2:
            lx, rx = (fid[0] + shift) * 1e3, (fid[1] + shift) * 1e3
            y = peak_v * 0.55
            ax.annotate("", xy=(lx, y), xytext=(rx, y),
                        arrowprops=dict(arrowstyle="<->", lw=1.4,
                                        color="#c0392b"))
            sep_ms = state.cal_separation_seconds * 1e3
            mm_per_ms = state.cal_mm_per_second * 1e-3
            ax.text((lx + rx) / 2, y + 0.04 * peak_v,
                    f"{state.cal_spacing_mm:g} mm  =  {sep_ms:.4g} ms\n"
                    f"{mm_per_ms:.4g} mm/ms",
                    ha="center", va="bottom", fontsize=9, color="#c0392b")

        ax.set_ylim(min(corrected) - 0.08 * peak_v, peak_v * 1.45)
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
        # The history holds SECONDS and is converted here, at draw time, so
        # recalibrating rescales the whole chart instead of leaving a step
        # in it. That is right: the scale is a property of the BPM's sweep,
        # not of the moment each point was taken, so a better measurement of
        # it is a better reading of every point.
        mmps  = self._mm_per_second
        to_mm = mmps == mmps
        scale = mmps if to_mm else 1e3
        ax.set_ylabel("FWHM (mm)" if to_mm else "FWHM (ms)")
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
                        pts.append((now - t, width * scale))
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
        if self._logger is not None:
            try:
                self._logger.close()
            except Exception:
                log.exception("profiler_tab: error closing logger on shutdown")
            self._logger = None
