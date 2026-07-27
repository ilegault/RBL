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
from PySide6.QtCore import QTimer, Qt, QSize, QPointF, QRectF
from PySide6.QtGui import QFont, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QMessageBox, QSizePolicy,
    QSlider, QComboBox, QDoubleSpinBox, QFormLayout,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from rbl.hardware.labjack_driver import LJM_AVAILABLE
from rbl.hardware.current_monitor import (
    voltage_to_current, format_current, beam_centering, RollingBuffer,
)
from rbl.hardware import beam_reconstruction as BR
from rbl.config import hardware_config as SC
from rbl.gui.labjack_panel import LabJackPanel
from rbl.gui import theme


# ─── Beam-position indicator ───────────────────────────────────────────────────

# Jaw colours reused from the plot / readouts for a consistent palette.
_JAW_COLORS = theme.JAW_COLORS

# FWHM -> sigma for a Gaussian.  Operators think in spot width, the maths wants
# sigma, so the spinbox takes FWHM and this converts.
_FWHM_TO_SIGMA = 1.0 / 2.35482


class _ApertureView(QWidget):
    """The slit aperture and the beam inside it, drawn to scale in millimetres.

    One isotropic mm-per-pixel scale is used for both axes, so a 3 mm gap really
    does look three times a 1 mm gap and a 3x10 mm aperture really does look
    tall and narrow.  The blades are drawn where the Galil says they are; the
    beam is drawn where the log-amp currents say it is.

    Nothing here computes anything — the parent hands it a finished
    ``BeamEstimate`` and it draws what it is given.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edges     = {}      # jaw -> SIGNED mm; empty when Galil unavailable
        self.currents  = {}      # jaw -> Amps
        self.est       = None    # BR.BeamEstimate, or None before first data
        self.sigma_mm  = 0.85
        self.span_x    = 0.0     # raster half-travel, 0 in static mode
        self.span_y    = 0.0
        self.raster    = False
        self.ratio_x   = float("nan")   # fallback when jaw positions are absent
        self.ratio_y   = float("nan")
        self.setMinimumSize(190, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # ---- Geometry helpers ---------------------------------------------------

    def _fov_mm(self) -> tuple:
        """Half-extent of the field of view per axis, in mm, as (x, y).

        Sized from the jaws themselves so the aperture always fills a useful
        fraction of the frame, with headroom past the blades — that margin is
        where raster overscan shows up, so it has to be visible.

        The two axes are sized independently because the aperture is not
        square: a 3 mm gap on X beside a 10 mm gap on Y would leave the X view
        almost empty if both shared one extent.  The drawing still uses a
        single mm-per-pixel scale, so the picture stays honest — the axes
        differ in how much they SHOW, not in how much a millimetre measures.
        """
        def reach(jaws, beam_centre, span):
            r = max((abs(self.edges[j]) for j in jaws if j in self.edges),
                    default=3.0)
            if self.est is not None and self.est.ok:
                r = max(r, abs(beam_centre) + span + 2 * self.sigma_mm)
            return max(1.0, r * 1.35)

        bx = self.est.x if (self.est is not None and self.est.ok) else 0.0
        by = self.est.y if (self.est is not None and self.est.ok) else 0.0
        return (reach(("X+", "X-"), bx, self.span_x),
                reach(("Y+", "Y-"), by, self.span_y))

    def _tint(self, jaw: str) -> float:
        """How lit up a blade should be, 0..1, from its current on a log scale.

        The log amps span 1 nA to 1 mA — six decades — so a linear tint would
        leave everything below a microamp looking identically dead.
        """
        i = self.currents.get(jaw)
        if i is None or math.isnan(i) or i <= 1e-9:
            return 0.0
        f = (math.log10(i) + 9.0) / 6.0
        return 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)

    # ---- Painting -----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin_x, margin_y = 20, 16      # room for the X±/Y± jaw labels
        x0 = float(margin_x)
        y0 = float(margin_y)
        vw = self.width()  - 2 * margin_x
        vh = self.height() - 2 * margin_y
        if vw < 40 or vh < 40:
            return
        cx = x0 + vw / 2.0
        cy = y0 + vh / 2.0

        if not self.edges:
            self._paint_without_geometry(p, x0, y0, vw, vh, cx, cy)
            return

        fov_x, fov_y = self._fov_mm()
        # ONE scale for both axes -- whichever axis is tighter sets it, so the
        # picture is never stretched and a millimetre is a millimetre.
        s = min((vw / 2.0) / fov_x, (vh / 2.0) / fov_y)

        def X(mm): return cx + mm * s
        def Y(mm): return cy - mm * s    # +Y is up

        # Field of view.
        p.setBrush(QBrush(QColor("#fdfdfd")))
        p.setPen(QPen(QColor("#888"), 1.0))
        p.drawRect(QRectF(x0, y0, vw, vh))

        bad = set(self.est.bad_jaws) if self.est is not None else set()
        self._paint_blades(p, X, Y, x0, y0, vw, vh, bad)

        # Nominal beam axis.
        p.setPen(QPen(QColor("#bbb"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x0, cy), QPointF(x0 + vw, cy))
        p.drawLine(QPointF(cx, y0), QPointF(cx, y0 + vh))

        self._paint_jaw_labels(p, x0, y0, vw, vh, cx, cy)

        if self.est is not None and self.est.ok:
            self._paint_beam(p, X, Y, s)
        else:
            self._paint_no_estimate(p, x0, y0, vw, vh, cy)

        self._paint_scale_bar(p, x0, y0, vh, s, min(fov_x, fov_y))

    def _paint_blades(self, p, X, Y, x0, y0, vw, vh, bad):
        """Four slit blades at their measured positions, lit by their current.

        Each blade is drawn as the solid region it actually occupies — from its
        edge outward to the limit of the view — because that region is exactly
        what the beam has to miss to get through.
        """
        rects = {
            "X+": lambda e: QRectF(X(e), y0, x0 + vw - X(e), vh),
            "X-": lambda e: QRectF(x0, y0, X(e) - x0, vh),
            "Y+": lambda e: QRectF(x0, y0, vw, Y(e) - y0),
            "Y-": lambda e: QRectF(x0, Y(e), vw, y0 + vh - Y(e)),
        }
        for jaw, make in rects.items():
            e = self.edges.get(jaw)
            if e is None or math.isnan(e):
                continue
            r = make(e).normalized()
            base = QColor(_JAW_COLORS[jaw])
            fill = QColor(base)
            # Blades overlap at the corners, so keep them translucent enough
            # that the overlap reads as overlap rather than as a fifth object.
            fill.setAlpha(int(35 + 120 * self._tint(jaw)))
            p.setBrush(QBrush(fill))
            if jaw in bad:
                p.setPen(QPen(QColor(theme.FAULT), 2.0, Qt.PenStyle.DashLine))
            else:
                p.setPen(QPen(base, 1.5))
            p.drawRect(r)

    def _paint_jaw_labels(self, p, x0, y0, vw, vh, cx, cy):
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(_JAW_COLORS["X-"])))
        p.drawText(int(x0 - 18), int(cy + 4), "X-")
        p.setPen(QPen(QColor(_JAW_COLORS["X+"])))
        p.drawText(int(x0 + vw + 3), int(cy + 4), "X+")
        p.setPen(QPen(QColor(_JAW_COLORS["Y+"])))
        p.drawText(int(cx - 7), int(y0 - 4), "Y+")
        p.setPen(QPen(QColor(_JAW_COLORS["Y-"])))
        p.drawText(int(cx - 7), int(y0 + vh + 12), "Y-")

    def _paint_beam(self, p, X, Y, s):
        """The reconstructed beam, then the uncertainty on where its centre is."""
        est = self.est
        px, py = X(est.x), Y(est.y)

        if self.raster:
            # Swept envelope: a flat top with spot-softened edges.  Rounding the
            # corners by the spot size is a fair picture of that softening.
            rx = max(2.0, self.span_x * s)
            ry = max(2.0, self.span_y * s)
            body = QRectF(px - rx, py - ry, 2 * rx, 2 * ry)
            p.setBrush(QBrush(QColor(230, 126, 34, 70)))
            p.setPen(QPen(QColor("#e67e22"), 1.5))
            soft = min(self.sigma_mm * s, rx, ry)
            p.drawRoundedRect(body, soft, soft)
        else:
            # Static spot: 1-sigma solid, 2-sigma outline for the tails that are
            # doing the actual measuring.
            r1 = max(2.0, self.sigma_mm * s)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(230, 126, 34, 110), 1.0, Qt.PenStyle.DashLine))
            p.drawEllipse(QPointF(px, py), 2 * r1, 2 * r1)
            p.setBrush(QBrush(QColor(230, 126, 34, 90)))
            p.setPen(QPen(QColor("#e67e22"), 1.5))
            p.drawEllipse(QPointF(px, py), r1, r1)

        # Where the centre could be, given the width is only assumed.  This
        # closes to a point for a centred beam and opens up as it goes off
        # centre — the honest shape of the ambiguity, not a fudge factor.
        # Drawn as error bars rather than a box: at this size a box reads as
        # just another shape in the picture, where capped whiskers say
        # "uncertainty" on sight.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#7a2a00"), 1.4))
        cap = 3.0
        bx0, bx1 = X(est.x_lo), X(est.x_hi)
        by0, by1 = Y(est.y_hi), Y(est.y_lo)
        if abs(bx1 - bx0) > 1.5:
            p.drawLine(QPointF(bx0, py), QPointF(bx1, py))
            p.drawLine(QPointF(bx0, py - cap), QPointF(bx0, py + cap))
            p.drawLine(QPointF(bx1, py - cap), QPointF(bx1, py + cap))
        if abs(by1 - by0) > 1.5:
            p.drawLine(QPointF(px, by0), QPointF(px, by1))
            p.drawLine(QPointF(px - cap, by0), QPointF(px + cap, by0))
            p.drawLine(QPointF(px - cap, by1), QPointF(px + cap, by1))

        # The centre itself.
        p.setPen(QPen(QColor("#4a1a00"), 1.5))
        p.setBrush(QBrush(QColor("#7a2a00")))
        p.drawEllipse(QPointF(px, py), 2.5, 2.5)

    def _paint_no_estimate(self, p, x0, y0, vw, vh, cy):
        """Grey wash plus the reason, when the currents cannot support a beam."""
        p.setBrush(QBrush(QColor(250, 250, 250, 190)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(x0, y0, vw, vh))

        msg = "waiting for data"
        if self.est is not None and self.est.reason:
            msg = self.est.reason
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#999")))
        p.drawText(QRectF(x0 + 4, cy - 24, vw - 8, 48),
                   int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
                   "NO BEAM ESTIMATE\n" + msg)

    def _paint_scale_bar(self, p, x0, y0, vh, s, fov):
        """A labelled ruler, so the drawing reads as millimetres not pixels."""
        step = 0.5
        for candidate in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0):
            if candidate <= fov * 0.7:
                step = candidate
        length = step * s
        bx = x0 + 6
        by = y0 + vh - 8
        # The bar sits on top of whichever blade happens to be there, so give it
        # a backing or the label disappears into a saturated jaw colour.
        p.setBrush(QBrush(QColor(255, 255, 255, 205)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(bx - 3, by - 17, length + 6, 23))
        p.setPen(QPen(QColor("#666"), 1.5))
        p.drawLine(QPointF(bx, by), QPointF(bx + length, by))
        p.drawLine(QPointF(bx, by - 3), QPointF(bx, by + 3))
        p.drawLine(QPointF(bx + length, by - 3), QPointF(bx + length, by + 3))
        p.setFont(QFont("Consolas", 7))
        p.drawText(QPointF(bx, by - 5), f"{step:g} mm")

    def _paint_without_geometry(self, p, x0, y0, vw, vh, cx, cy):
        """Fallback view for when the Galil is not supplying jaw positions.

        Without mm positions there is no scale and no reconstruction — only the
        raw current imbalance, which is what the old indicator always showed.
        It is still useful for spotting drift, so it is kept, but it is labelled
        unmistakably so nobody reads millimetres off a picture that has none.
        """
        p.setBrush(QBrush(QColor("#f4f4f4")))
        p.setPen(QPen(QColor("#999"), 1.5, Qt.PenStyle.DashLine))
        p.drawRect(QRectF(x0, y0, vw, vh))

        p.setPen(QPen(QColor("#bbb"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x0, cy), QPointF(x0 + vw, cy))
        p.drawLine(QPointF(cx, y0), QPointF(cx, y0 + vh))
        self._paint_jaw_labels(p, x0, y0, vw, vh, cx, cy)

        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#b06000")))
        p.drawText(QRectF(x0, y0 + 3, vw, 26),
                   int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap),
                   "NO JAW POSITIONS\nrelative only — not to scale")

        r = max(4.0, min(vw, vh) * 0.05)
        if math.isnan(self.ratio_x) or math.isnan(self.ratio_y):
            p.setPen(QPen(QColor("#bbb"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
            return

        dx = max(-1.0, min(1.0, self.ratio_x))
        dy = max(-1.0, min(1.0, self.ratio_y))
        px = cx + dx * (vw / 2.0)
        py = cy - dy * (vh / 2.0)
        p.setPen(QPen(QColor("#c9a06a"), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(cx, cy), QPointF(px, py))
        p.setPen(QPen(QColor("#8a6a3a"), 1.5))
        p.setBrush(QBrush(QColor("#d0a878")))
        p.drawEllipse(QPointF(px, py), r + 2, r + 2)


class BeamPositionIndicator(QWidget):
    """Panel showing where the log-amp currents say the beam is.

    Each NEC log amp reads a whole slit blade, so its current is all the beam
    landing on that blade — the integral of the beam profile past that blade's
    edge.  Combined with the jaw positions the Galil reports, that makes four
    knife-edge measurements, which is enough to place the beam in millimetres
    instead of merely nudging a dot toward whichever jaw reads higher.

    What it cannot do is measure the beam's width.  Beam intensity is not
    measured anywhere in this program, and per axis there are two currents
    against three unknowns (centre, width, intensity).  Taking the ratio kills
    the intensity and leaves one equation in two unknowns, so the centre can
    only be solved against an ASSUMED width — hence the spot-size box.  The
    drawn band shows how far that assumption could be moving the answer: it
    shrinks to nothing for a centred beam and widens as the beam goes off
    centre, which is precisely when someone is most tempted to trust it.

    Static and raster modes are a manual toggle.  The widget deliberately does
    not read the function generators or HV amplifiers — it reports what the log
    amps see, and nothing else.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._currents = {}
        self._edges    = {}
        self._zeroed   = False
        self._connected = False

        self.setFixedWidth(288)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)

        title = QLabel("Beam Position")
        title.setStyleSheet("font-weight: bold; color: #444; font-size: 13px;")
        lay.addWidget(title)

        # ── Mode + assumptions ────────────────────────────────────────────
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Static beam", "Rastering"])
        self.cmb_mode.setToolTip(
            "Static: a stationary spot.\n"
            "Rastering: the steerer is sweeping the beam, so each current is a\n"
            "time-average over the sweep and the envelope is drawn instead."
        )
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        lay.addWidget(self.cmb_mode)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(2)

        self.spn_spot = QDoubleSpinBox()
        self.spn_spot.setRange(0.05, 25.0)
        self.spn_spot.setSingleStep(0.1)
        self.spn_spot.setDecimals(2)
        self.spn_spot.setValue(2.0)
        self.spn_spot.setSuffix(" mm")
        self.spn_spot.setToolTip(
            "Spot size (FWHM) you believe the beam has.\n"
            "The currents cannot measure this — it is an assumption, and the\n"
            "band in the picture shows how much it is moving the answer."
        )
        self.spn_spot.valueChanged.connect(self._recompute)
        self.lbl_spot = QLabel("Spot FWHM")
        self.lbl_spot.setStyleSheet("font-size: 12px; color: #555;")
        form.addRow(self.lbl_spot, self.spn_spot)

        self.spn_span_x = QDoubleSpinBox()
        self.spn_span_x.setRange(0.0, 50.0)
        self.spn_span_x.setSingleStep(0.5)
        self.spn_span_x.setDecimals(2)
        self.spn_span_x.setValue(3.0)
        self.spn_span_x.setSuffix(" mm")
        self.spn_span_x.setToolTip("Raster half-travel on X (centre to turn-around).")
        self.spn_span_x.valueChanged.connect(self._recompute)
        self.lbl_span_x = QLabel("Sweep X ±")
        self.lbl_span_x.setStyleSheet("font-size: 12px; color: #555;")
        form.addRow(self.lbl_span_x, self.spn_span_x)

        self.spn_span_y = QDoubleSpinBox()
        self.spn_span_y.setRange(0.0, 50.0)
        self.spn_span_y.setSingleStep(0.5)
        self.spn_span_y.setDecimals(2)
        self.spn_span_y.setValue(8.0)
        self.spn_span_y.setSuffix(" mm")
        self.spn_span_y.setToolTip("Raster half-travel on Y (centre to turn-around).")
        self.spn_span_y.valueChanged.connect(self._recompute)
        self.lbl_span_y = QLabel("Sweep Y ±")
        self.lbl_span_y.setStyleSheet("font-size: 12px; color: #555;")
        form.addRow(self.lbl_span_y, self.spn_span_y)

        lay.addLayout(form)

        # ── The picture ───────────────────────────────────────────────────
        self.view = _ApertureView()
        lay.addWidget(self.view, stretch=1)

        # ── Readouts ──────────────────────────────────────────────────────
        self.lbl_xy = QLabel("X  —      Y  —")
        self.lbl_xy.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        self.lbl_xy.setStyleSheet("color: #a83a00;")
        self.lbl_xy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_xy)

        self.lbl_status = QLabel("Galil not connected — no jaw positions")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-size: 11px; color: #888;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_status)

        self.lbl_overscan = QLabel("")
        self.lbl_overscan.setWordWrap(True)
        self.lbl_overscan.setStyleSheet("font-size: 11px; color: #888;")
        self.lbl_overscan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_overscan)

        self._on_mode_changed()

    # ---- Inputs -------------------------------------------------------------

    def set_currents(self, currents_by_jaw: dict):
        """Latest log-amp current per jaw label, in Amps."""
        self._currents = dict(currents_by_jaw)
        self._recompute()

    def set_jaw_state(self, state: dict):
        """Jaw geometry from the motor tab.

        ``state`` carries ``positions`` (jaw label -> UNSIGNED mm from beam
        centre, exactly as the motor tab displays them), ``zeroed`` (have all
        four axes been homed or zeroed this session) and ``connected``.
        """
        self._connected = bool(state.get("connected", False))
        self._zeroed    = bool(state.get("zeroed", False))
        positions       = state.get("positions") or {}

        # The Galil reports each jaw as a distance from centre with no sign;
        # the '-' jaws live on the negative side of the axis.  Getting this
        # backwards would silently mirror the whole picture.
        self._edges = {}
        for jaw, mm in positions.items():
            if mm is None or math.isnan(mm):
                continue
            self._edges[jaw] = abs(mm) if jaw.endswith("+") else -abs(mm)
        self._recompute()

    # ---- Internals ----------------------------------------------------------

    def _on_mode_changed(self, *_):
        raster = self.cmb_mode.currentIndex() == 1
        for wdg in (self.lbl_span_x, self.spn_span_x,
                    self.lbl_span_y, self.spn_span_y):
            wdg.setVisible(raster)
        self._recompute()

    def _recompute(self):
        raster   = self.cmb_mode.currentIndex() == 1
        sigma    = self.spn_spot.value() * _FWHM_TO_SIGMA
        span_x   = self.spn_span_x.value() if raster else 0.0
        span_y   = self.spn_span_y.value() if raster else 0.0

        v = self.view
        v.currents = self._currents
        v.edges    = self._edges
        v.sigma_mm = sigma
        v.span_x   = span_x
        v.span_y   = span_y
        v.raster   = raster

        if len(self._edges) == 4:
            est = BR.reconstruct(self._currents, self._edges, sigma,
                                 span_x, span_y)
            v.est = est
            self._update_readout(est)
        else:
            v.est = None
            # No geometry — fall back to the bare current imbalance.
            v.ratio_x = beam_centering(self._currents.get("X+", float("nan")),
                                       self._currents.get("X-", float("nan")))
            v.ratio_y = beam_centering(self._currents.get("Y+", float("nan")),
                                       self._currents.get("Y-", float("nan")))
            self.lbl_xy.setText("X  —      Y  —")

        self._update_status()
        self._update_overscan(raster)
        v.update()

    def _update_readout(self, est):
        if not est.ok:
            self.lbl_xy.setText("X  —      Y  —")
            return
        # Only the centre is reported.  Width is assumed, not measured, so
        # printing a number for it would dress an input up as a result.
        # Snap to zero first: a signed format turns -1e-17 into a "-0.00" that
        # reads as a real leftward offset.
        x = 0.0 if abs(est.x) < 5e-3 else est.x
        y = 0.0 if abs(est.y) < 5e-3 else est.y
        self.lbl_xy.setText(f"X {x:+.2f}   Y {y:+.2f} mm")

    def _update_status(self):
        if not self._connected:
            self.lbl_status.setText(
                "Galil not connected — showing current imbalance only")
            self.lbl_status.setStyleSheet("font-size: 11px; color: #b06000;")
        elif len(self._edges) < 4:
            self.lbl_status.setText("Waiting for all four jaw positions")
            self.lbl_status.setStyleSheet("font-size: 11px; color: #b06000;")
        elif not self._zeroed:
            self.lbl_status.setText(
                "⚠ Axes not zeroed this session — mm positions may be wrong")
            self.lbl_status.setStyleSheet(f"font-size: 11px; color: {theme.FAULT};")
        else:
            self.lbl_status.setText("Jaw positions live and zeroed")
            self.lbl_status.setStyleSheet(f"font-size: 11px; color: {theme.OK};")

    def _update_overscan(self, raster: bool):
        """In raster mode, say which blades the sweep is actually reaching.

        The sweep is meant to carry the beam clear off both ends of the
        aperture so deposition speed stays constant across the sample.  A blade
        sitting at the noise floor is one the beam never reaches — which is the
        failure this readout exists to catch.
        """
        if not raster:
            self.lbl_overscan.setText("")
            return
        flags = BR.overscan_flags(self._currents)
        missed = [jaw for jaw, hit in flags.items() if not hit]
        if not missed:
            self.lbl_overscan.setText("Overscan: beam reaching all four blades")
            self.lbl_overscan.setStyleSheet(f"font-size: 11px; color: {theme.OK};")
        else:
            self.lbl_overscan.setText("Not reaching: " + ", ".join(missed))
            self.lbl_overscan.setStyleSheet(f"font-size: 11px; color: {theme.FAULT};")


# ─── The tab widget ───────────────────────────────────────────────────────────

class CurrentTab(QWidget):
    """The 'Beam Current' outer tab."""

    BUFFER_CAPACITY = 36_000   # ~1 hour at 10 Hz
    WINDOW_SECONDS  = 120      # default 2-minute viewport
    _TIME_STEPS     = [3600, 1800, 900, 600, 300, 120, 60, 30, 15, 5, 1]

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
        self._window_seconds    = float(self.WINDOW_SECONDS)

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
        for col, (ain, jaw) in enumerate(SC.LABJACK_CHANNEL_MAP.items()):
            hdr = QLabel(f"{jaw}  ({ain})")
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
        btn_time_out.clicked.connect(self._zoom_time_out)
        btn_time_in = QPushButton("＋")
        btn_time_in.setFixedWidth(28)
        btn_time_in.setToolTip("Decrease time window (zoom in)")
        btn_time_in.setStyleSheet("font-weight: bold; padding: 1px 4px;")
        btn_time_in.clicked.connect(self._zoom_time_in)
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
        self.btn_jump_live.clicked.connect(self._jump_to_live)
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
        for ain, jaw in SC.LABJACK_CHANNEL_MAP.items():
            line, = self.ax.plot([], [], color=theme.JAW_COLORS.get(jaw, "k"), lw=1.5)
            self._lines[ain] = line
        self.fig.tight_layout()

        # Qt legend panel (right of canvas — avoids matplotlib layout fighting)
        _legend_w = QWidget()
        _legend_w.setFixedWidth(115)
        _leg_lay = QVBoxLayout(_legend_w)
        _leg_lay.setSpacing(3)
        _leg_lay.setContentsMargins(4, 8, 4, 4)
        _leg_title = QLabel("Legend")
        _leg_title.setStyleSheet("font-size: 15px; color: #555; font-weight: bold;")
        _leg_lay.addWidget(_leg_title)
        for _ain, _jaw in SC.LABJACK_CHANNEL_MAP.items():
            _row = QHBoxLayout()
            _swatch = QLabel("━")
            _swatch.setStyleSheet(
                f"color: {theme.JAW_COLORS.get(_jaw, '#000')}; font-weight: bold; font-size: 13px;"
            )
            _lbl = QLabel(f"{_jaw}  ({_ain})")
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
        _content_row.addWidget(self.canvas, stretch=1)
        _content_row.addWidget(_legend_w)
        pv.addLayout(_content_row, stretch=1)

        # History slider: 0 = oldest, 10000 = live (rightmost = newest)
        slider_row = QHBoxLayout()
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

    @staticmethod
    def _by_jaw(currents_by_ain: dict) -> dict:
        """Re-key AIN -> current as jaw label -> current for the indicator."""
        return {jaw: currents_by_ain.get(ain, float("nan"))
                for ain, jaw in SC.LABJACK_CHANNEL_MAP.items()}

    def set_jaw_state(self, state: dict):
        """Jaw geometry pushed over from the motor tab (see MotorTab.jaw_state)."""
        self.beam_indicator.set_jaw_state(state)

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
            self.lbl_i[ain].setStyleSheet(theme.status_label(theme.OK))
            self.buffers[ain].append(t, I)

        self.beam_indicator.set_currents(self._by_jaw(currents))

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

        self.beam_indicator.set_currents(self._by_jaw(currents))

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
        w = self._window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        self.lbl_mode.setText(f"● LIVE  (last {label})")
        self.lbl_mode.setStyleSheet(
            theme.status_label(theme.OK) + " padding: 2px 6px;"
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
        w_start = max(t_oldest, self._frozen_right_edge - self._window_seconds)
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

    def _zoom_time_in(self):
        """Decrease the time window (zoom in on time axis)."""
        smaller = [s for s in self._TIME_STEPS if s < self._window_seconds]
        if smaller:
            self._window_seconds = float(max(smaller))
        else:
            self._window_seconds = max(1.0, self._window_seconds / 2)
        self._update_time_label()

    def _zoom_time_out(self):
        """Increase the time window (zoom out on time axis)."""
        larger = [s for s in self._TIME_STEPS if s > self._window_seconds]
        if larger:
            self._window_seconds = float(min(larger))
        else:
            self._window_seconds = min(3600.0, self._window_seconds * 2)
        self._update_time_label()

    def _update_time_label(self):
        w = self._window_seconds
        label = f"{int(w)} s" if w >= 1 else f"{int(w * 1000)} ms"
        if self._is_live:
            self.lbl_mode.setText(f"● LIVE  (last {label})")
            self.lbl_mode.setStyleSheet(
                theme.status_label(theme.OK) + " padding: 2px 6px;"
            )
        else:
            self.lbl_mode.setText(
                f"⏸  Frozen  —  window {label}"
            )

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

        t_left = t_right - self._window_seconds

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
            self.ax.set_xlim(-self._window_seconds, 0)
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
