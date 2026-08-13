"""
live_plot.py
Shared chrome for a live-scrolling matplotlib plot: the canvas/figure, the
history slider, the LIVE/FROZEN state machine, the time-window zoom-step
list, and the redraw timer.

This was duplicated almost verbatim between amp_tab.py and logamp_tab.py.
Deliberately NOT shared here: the nav row (mode label, zoom buttons,
jump-to-live button). The two tabs' nav rows differ in real ways — amp_tab
interleaves its own vertical (voltage/current) zoom controls between the
time-zoom buttons and the jump-to-live button, and even orders its time-zoom
buttons the other way round from the log-amp tab. Forcing both through one
shared row would either silently reorder amp_tab's buttons or leak its extra
controls onto the simpler log-amp plot. Each tab builds its own nav row and
wires it to this panel's `zoom_in()` / `zoom_out()` / `jump_to_live()` and
`navigation_changed` / `zoom_changed` signals.
"""
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QSizePolicy

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg


class LivePlotPanel(QObject):
    """Canvas/figure, history slider, LIVE/FROZEN state machine, zoom-step
    list, and redraw timer for a live-scrolling plot.

    Not a QWidget itself: `slider_row` is a loose QHBoxLayout the caller
    places in its own layout (`pv.addLayout(panel.slider_row)`) alongside
    whatever nav row and canvas/legend/beam-indicator content it builds
    itself.

    Two data callbacks, deliberately split by cost:

    `live_edge_provider()` returns the newest available timestamp (or None),
    used on every redraw tick while LIVE — so it must be cheap (a `.latest()`
    per buffer, not a sort).

    `span_provider()` returns `(t_oldest, t_newest)`, used only when a slider
    drag enters FROZEN mode to convert the drag position into a timestamp —
    infrequent, so it may snapshot/sort.

    `min_window_seconds`, if given, floors how far `zoom_in` can shrink the
    window — the log-amp tab passes 1.0 so it never zooms into the sub-second
    range the amplifier waveform-snapshot view uses; amp_tab passes None.
    """

    # Full zoom-step list (seconds, descending).  Snapping to preset values
    # keeps labels clean: the 15→5→1 jump avoids the ugly
    # 7.5/3.75/1.875/0.9375... sequence, and halving from exactly 1 s gives
    # tidy ms values (500, 250, 125, ...).
    ZOOM_STEPS = [
        3600, 1800, 900, 600, 300, 120, 60, 30, 15, 5, 1,
        0.5, 0.25, 0.125, 0.0625, 0.03125, 0.016, 0.008, 0.004, 0.002, 0.001,
    ]

    # Two distinct signals rather than one, because the callers format the
    # FROZEN-mode label differently depending on which triggered the change
    # (one tab shows the elapsed-time range only right after a slider move,
    # not after a subsequent zoom).
    navigation_changed = Signal()   # slider-driven: entered LIVE or FROZEN
    zoom_changed        = Signal()   # window_seconds changed via zoom in/out

    def __init__(self, *, window_seconds: float, live_edge_provider, span_provider,
                 min_window_seconds: float = None, figsize=(7, 3),
                 redraw_interval_ms: int = 200, parent=None):
        super().__init__(parent)
        self.live_edge_provider = live_edge_provider
        self.span_provider = span_provider
        self._min_window_seconds = min_window_seconds
        self.window_seconds = float(window_seconds)
        self.is_live = True
        self.frozen_right_edge = None   # float: elapsed-seconds anchor

        self.fig = Figure(figsize=figsize)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── History slider: 0 = oldest, 10000 = live (rightmost = newest) ───
        self.slider_row = QHBoxLayout()
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
        self.slider_row.addWidget(lbl_hist)
        self.slider_row.addWidget(self.slider, stretch=1)
        self.slider_row.addWidget(lbl_live)

        self.redraw_timer = QTimer(self)
        self.redraw_timer.setInterval(redraw_interval_ms)

    # ---- Owner-callable lifecycle ---------------------------------------------

    def start(self):
        self.redraw_timer.start()

    def stop(self):
        self.redraw_timer.stop()

    def force_to_live(self):
        """Snap back to the live edge without going through the slider signal."""
        self.slider.blockSignals(True)
        self.slider.setValue(10_000)
        self.slider.blockSignals(False)

    # ---- LIVE / FROZEN state machine ------------------------------------------

    def _on_slider_changed(self, val: int):
        if val >= 9_800:
            self.enter_live_mode()
        else:
            self._enter_frozen_mode(val)

    def enter_live_mode(self):
        self.is_live = True
        self.frozen_right_edge = None
        self.navigation_changed.emit()

    def _enter_frozen_mode(self, slider_val: int):
        span = self.span_provider()
        if span is None:
            return
        t_oldest, t_newest = span
        width = t_newest - t_oldest
        if width <= 0:
            return
        frac = slider_val / 10_000.0
        self.frozen_right_edge = t_oldest + frac * width
        self.is_live = False
        self.navigation_changed.emit()

    def jump_to_live(self):
        self.slider.setValue(10_000)
        self.enter_live_mode()

    def freeze_at_live_edge(self) -> bool:
        """Freeze with the right edge at the newest sample available.

        The programmatic counterpart to dragging the slider all the way right
        and stopping: it pins the view to "now" instead of to a fraction of the
        history span, which is what an automatic freeze wants — an event just
        happened and the operator needs to see the moments before it.

        Returns True if the view was frozen. False means there was no history
        to anchor to, in which case LIVE is left alone rather than freezing on
        an empty axis.
        """
        span = self.span_provider()
        if span is None:
            return False
        _t_oldest, t_newest = span
        if t_newest is None:
            return False

        self.frozen_right_edge = float(t_newest)
        self.is_live = False
        # Move the slider to match without re-entering _on_slider_changed,
        # which would recompute the edge from the slider fraction and undo
        # the exact anchor set above.
        self.slider.blockSignals(True)
        self.slider.setValue(9_700)   # below the 9_800 live threshold
        self.slider.blockSignals(False)
        self.navigation_changed.emit()
        return True

    # ---- Zoom ------------------------------------------------------------------

    def zoom_in(self):
        """Decrease the time window (zoom in), snapping to the next preset step."""
        candidates = self.ZOOM_STEPS if self._min_window_seconds is None else [
            s for s in self.ZOOM_STEPS if s >= self._min_window_seconds
        ]
        for step in candidates:          # descending
            if step < self.window_seconds - 1e-9:
                self.window_seconds = step
                break
        self.zoom_changed.emit()

    def zoom_out(self):
        """Increase the time window (zoom out), snapping to the next preset step."""
        for step in reversed(self.ZOOM_STEPS):   # ascending
            if step > self.window_seconds + 1e-9:
                self.window_seconds = step
                break
        self.zoom_changed.emit()

    # ---- Window computation -----------------------------------------------------

    def compute_window(self):
        """(t_left, t_right) for the current LIVE/FROZEN state, or None."""
        if self.is_live:
            t_right = self.live_edge_provider()
            if t_right is None:
                return None
        else:
            if self.frozen_right_edge is None:
                return None
            t_right = self.frozen_right_edge
        return (t_right - self.window_seconds, t_right)
