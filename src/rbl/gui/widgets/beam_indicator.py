"""
beam_indicator.py
The beam-position indicator: an aperture picture plus slit-current readouts.

Moved out of logamp_tab.py so the Overview tab can embed the same widget the
Beam Current tab uses, fed the same ``BeamEstimate`` — the two views must never
be able to disagree about where the beam is.
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rbl.gui import theme
from rbl.gui.widgets.inputs import NoScrollComboBox, QuietDoubleSpinBox
from rbl.hardware import beam_reconstruction as BR
from rbl.hardware.current_monitor import beam_centering

# Slit colours reused from the plot / readouts for a consistent palette.
_SLIT_COLORS = theme.SLIT_COLORS


def _fmt_fwhm(seconds: float) -> str:
    """Format a FWHM duration in the most readable unit."""
    if seconds >= 1.0:
        return f"{seconds:.3f} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.3g} ms"
    return f"{seconds * 1e6:.3g} µs"


class _ApertureView(QWidget):
    """The slit aperture and the beam inside it, drawn to scale in millimetres.

    One isotropic mm-per-pixel scale is used for both axes, so a 3 mm gap really
    does look three times a 1 mm gap and a 3x10 mm aperture really does look
    tall and narrow.  The blades are drawn where the Galil says they are; the
    beam is drawn where the log-amp currents say it is.

    Nothing here computes anything — the parent hands it a finished
    ``BeamEstimate`` and it draws what it is given.
    """

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self.edges     = {}      # slit -> SIGNED mm; empty when Galil unavailable
        self.currents  = {}      # slit -> Amps
        self.est       = None    # BR.BeamEstimate, or None before first data
        self.sigma_mm  = 0.85
        self.span_x    = 0.0     # raster half-travel, 0 in static mode
        self.span_y    = 0.0
        self.raster    = False
        self.ratio_x   = float("nan")   # fallback when slit positions are absent
        self.ratio_y   = float("nan")
        # The drawing is resolution-independent (one mm-per-pixel scale,
        # computed from whatever size it gets), so a smaller floor costs
        # detail, never correctness. This is the one place that "square" is
        # actually enforced — the aperture picture is the thing being read,
        # so IT gets a square floor, regardless of what the panel around it
        # (title, controls, readouts) ends up shaped like.
        if compact:
            self.setMinimumSize(200, 300)
        else:
            self.setMinimumSize(250, 350)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    # ---- Geometry helpers ---------------------------------------------------

    def _fov_mm(self) -> tuple:
        """Half-extent of the field of view per axis, in mm, as (x, y).

        Sized from the slits themselves so the aperture always fills a useful
        fraction of the frame, with headroom past the blades — that margin is
        where raster overscan shows up, so it has to be visible.

        The two axes are sized independently because the aperture is not
        square: a 3 mm gap on X beside a 10 mm gap on Y would leave the X view
        almost empty if both shared one extent.  The drawing still uses a
        single mm-per-pixel scale, so the picture stays honest — the axes
        differ in how much they SHOW, not in how much a millimetre measures.
        """
        def reach(slits, beam_centre, span):
            r = max((abs(self.edges[j]) for j in slits if j in self.edges),
                    default=3.0)
            if self.est is not None and self.est.ok:
                r = max(r, abs(beam_centre) + span + 2 * self.sigma_mm)
            return max(1.0, r * 1.35)

        bx = self.est.x if (self.est is not None and self.est.ok) else 0.0
        by = self.est.y if (self.est is not None and self.est.ok) else 0.0
        return (reach(("X+", "X-"), bx, self.span_x),
                reach(("Y+", "Y-"), by, self.span_y))

    def _tint(self, slit: str) -> float:
        """How lit up a blade should be, 0..1, from its current on a log scale.

        The log amps span 1 nA to 1 mA — six decades — so a linear tint would
        leave everything below a microamp looking identically dead.
        """
        i = self.currents.get(slit)
        if i is None or math.isnan(i) or i <= 1e-9:
            return 0.0
        f = (math.log10(i) + 9.0) / 6.0
        return 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)

    # ---- Painting -----------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin_x, margin_y = 20, 16      # room for the X±/Y± slit labels
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

        bad = set(self.est.bad_slits) if self.est is not None else set()
        self._paint_blades(p, X, Y, x0, y0, vw, vh, bad)

        # Nominal beam axis.
        p.setPen(QPen(QColor("#bbb"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x0, cy), QPointF(x0 + vw, cy))
        p.drawLine(QPointF(cx, y0), QPointF(cx, y0 + vh))

        self._paint_slit_labels(p, x0, y0, vw, vh, cx, cy)

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
        for slit, make in rects.items():
            e = self.edges.get(slit)
            if e is None or math.isnan(e):
                continue
            r = make(e).normalized()
            base = QColor(_SLIT_COLORS[slit])
            fill = QColor(base)
            # Blades overlap at the corners, so keep them translucent enough
            # that the overlap reads as overlap rather than as a fifth object.
            fill.setAlpha(int(35 + 120 * self._tint(slit)))
            p.setBrush(QBrush(fill))
            if slit in bad:
                p.setPen(QPen(QColor(theme.FAULT), 2.0, Qt.PenStyle.DashLine))
            else:
                p.setPen(QPen(base, 1.5))
            p.drawRect(r)

    def _paint_slit_labels(self, p, x0, y0, vw, vh, cx, cy):
        p.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(_SLIT_COLORS["X-"])))
        p.drawText(int(x0 - 18), int(cy + 4), "X-")
        p.setPen(QPen(QColor(_SLIT_COLORS["X+"])))
        p.drawText(int(x0 + vw + 3), int(cy + 4), "X+")
        p.setPen(QPen(QColor(_SLIT_COLORS["Y+"])))
        p.drawText(int(cx - 7), int(y0 - 4), "Y+")
        p.setPen(QPen(QColor(_SLIT_COLORS["Y-"])))
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
        # a backing or the label disappears into a saturated slit colour.
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
        """Fallback view for when the Galil is not supplying slit positions.

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
        self._paint_slit_labels(p, x0, y0, vw, vh, cx, cy)

        p.setFont(QFont("Consolas", 7, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#b06000")))
        p.drawText(QRectF(x0, y0 + 3, vw, 26),
                   int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap),
                   "NO SLIT POSITIONS\nrelative only — not to scale")

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
    edge.  Combined with the slit positions the Galil reports, that makes four
    knife-edge measurements, which is enough to place the beam in millimetres
    instead of merely nudging a dot toward whichever slit reads higher.

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

    ``compact`` shrinks the panel for embedding in the Overview tab, where
    screen real estate is shared with several other subsystems and more are
    still to come. It only floors the panel's size — it does not cap it and
    does not force the panel itself into a square. The square-aperture idea
    lives on ``_ApertureView`` alone (the graph/frame of the slits, which is
    the thing actually being read); the title, mode controls, and readout
    labels around it are free to make the outer panel whatever rectangle
    they need, and the whole thing can grow past the floor when its
    container has room to give it.
    """

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self._currents = {}
        self._edges    = {}
        self._zeroed   = False
        self._connected = False
        self._compact  = compact

        if compact:
            # No fixed/minimum size set here on purpose: the panel's floor
            # falls out of its children's own minimums (title, combo, form
            # rows, the aperture view's square floor, readout labels) via the
            # layout below. Expanding lets it grow past that floor when the
            # Overview tab's layout has slack to give it.
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)
        else:
            self.setFixedWidth(288)
            self.setSizePolicy(QSizePolicy.Policy.Fixed,
                               QSizePolicy.Policy.Expanding)

        label_px = 10 if compact else 12

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2 if compact else 4)

        title = QLabel("Beam Position")
        title.setStyleSheet(
            "font-weight: bold; color: #444; "
            f"font-size: {11 if compact else 13}px;")
        lay.addWidget(title)

        # ── Mode + assumptions ────────────────────────────────────────────
        self.cmb_mode = NoScrollComboBox()
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

        self.spn_spot = QuietDoubleSpinBox()
        self.spn_spot.setRange(0.05, 25.0)
        self.spn_spot.setSingleStep(0.1)
        self.spn_spot.setDecimals(2)
        self.spn_spot.setValue(2.0)
        self.spn_spot.setToolTip(
            "Spot size (FWHM) you believe the beam has.\n"
            "The currents cannot measure this — it is an assumption, and the\n"
            "band in the picture shows how much it is moving the answer."
        )
        self.spn_spot.valueChanged.connect(self._recompute)
        self.lbl_spot = QLabel("Spot FWHM (mm)")
        self.lbl_spot.setStyleSheet(f"font-size: {label_px}px; color: #555;")
        form.addRow(self.lbl_spot, self.spn_spot)

        self.spn_span_x = QuietDoubleSpinBox()
        self.spn_span_x.setRange(0.0, 50.0)
        self.spn_span_x.setSingleStep(0.5)
        self.spn_span_x.setDecimals(2)
        self.spn_span_x.setValue(3.0)
        self.spn_span_x.setToolTip("Raster half-travel on X (centre to turn-around).")
        self.spn_span_x.valueChanged.connect(self._recompute)
        self.lbl_span_x = QLabel("Sweep X ± (mm)")
        self.lbl_span_x.setStyleSheet(f"font-size: {label_px}px; color: #555;")

        self.spn_span_y = QuietDoubleSpinBox()
        self.spn_span_y.setRange(0.0, 50.0)
        self.spn_span_y.setSingleStep(0.5)
        self.spn_span_y.setDecimals(2)
        self.spn_span_y.setValue(8.0)
        self.spn_span_y.setToolTip("Raster half-travel on Y (centre to turn-around).")
        self.spn_span_y.valueChanged.connect(self._recompute)
        self.lbl_span_y = QLabel("Sweep Y ± (mm)")
        self.lbl_span_y.setStyleSheet(f"font-size: {label_px}px; color: #555;")

        if compact:
            # Both sweep boxes on ONE row. Every row these controls take is a
            # row the aperture picture loses inside a fixed square, and the two
            # sweep numbers are read together anyway.
            self.lbl_span_x.setText("Sweep ± (mm)")
            self.lbl_span_y.setText("")
            sweeps = QHBoxLayout()
            sweeps.setContentsMargins(0, 0, 0, 0)
            sweeps.setSpacing(3)
            sweeps.addWidget(self.spn_span_x)
            sweeps.addWidget(self.spn_span_y)
            form.addRow(self.lbl_span_x, sweeps)
        else:
            form.addRow(self.lbl_span_x, self.spn_span_x)
            form.addRow(self.lbl_span_y, self.spn_span_y)

        lay.addLayout(form)

        # ── The picture ───────────────────────────────────────────────────
        self.view = _ApertureView(compact=compact)
        lay.addWidget(self.view, stretch=1)

        # ── Readouts ──────────────────────────────────────────────────────
        self._small = 10 if compact else 11
        self.lbl_xy = QLabel("X  —      Y  —")
        self.lbl_xy.setFont(QFont("Consolas", 10 if compact else 12,
                                  QFont.Weight.Bold))
        self.lbl_xy.setStyleSheet("color: #a83a00;")
        self.lbl_xy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_xy)

        self.lbl_status = QLabel("Galil not connected — no slit positions")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(f"font-size: {self._small}px; color: #888;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_status)

        self.lbl_overscan = QLabel("")
        self.lbl_overscan.setWordWrap(True)
        self.lbl_overscan.setStyleSheet(f"font-size: {self._small}px; color: #888;")
        self.lbl_overscan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_overscan)

        self.lbl_fwhm = QLabel("FWHM  —")
        self.lbl_fwhm.setFont(QFont("Consolas", 10 if compact else 11,
                                    QFont.Weight.Bold))
        self.lbl_fwhm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_fwhm)

        self.lbl_fwhm_note = QLabel("")
        self.lbl_fwhm_note.setWordWrap(True)
        self.lbl_fwhm_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_fwhm_note.setStyleSheet(f"font-size: {self._small}px; color: #888;")
        lay.addWidget(self.lbl_fwhm_note)

        self._fwhm_s: float = float("nan")

        self._on_mode_changed()

    # ---- Inputs -------------------------------------------------------------

    def set_fwhm(self, fwhm_seconds: float):
        """Live FWHM from the beam profiler scope, in seconds.

        Pass ``float('nan')`` when the scope is disconnected or the extraction
        failed.  Only shown in static-beam mode; ignored in raster mode.
        """
        self._fwhm_s = fwhm_seconds
        self._update_fwhm_label()

    def set_currents(self, currents_by_slit: dict):
        """Latest log-amp current per slit label, in Amps."""
        self._currents = dict(currents_by_slit)
        self._recompute()

    def set_slit_state(self, state: dict):
        """Slit geometry from the motor tab.

        ``state`` carries ``positions`` (slit label -> UNSIGNED mm from beam
        centre, exactly as the motor tab displays them), ``zeroed`` (have all
        four axes been homed or zeroed this session) and ``connected``.
        """
        self._connected = bool(state.get("connected", False))
        self._zeroed    = bool(state.get("zeroed", False))
        positions       = state.get("positions") or {}

        # The Galil reports each slit as a distance from centre with no sign;
        # the '-' slits live on the negative side of the axis.  Getting this
        # backwards would silently mirror the whole picture.
        self._edges = {}
        for slit, mm in positions.items():
            if mm is None or math.isnan(mm):
                continue
            self._edges[slit] = abs(mm) if slit.endswith("+") else -abs(mm)
        self._recompute()

    # ---- Internals ----------------------------------------------------------

    def _update_fwhm_label(self):
        fwhm = self._fwhm_s
        if math.isnan(fwhm):
            self.lbl_fwhm.setText("FWHM  —")
            self.lbl_fwhm.setStyleSheet("color: #888;")
            self.lbl_fwhm_note.setText(
                "Scope not connected — connect the TDS 2012 oscilloscope "
                "on the Beam Profiler tab for automatic FWHM measurement.")
            self.lbl_fwhm_note.setStyleSheet(
                f"font-size: {self._small}px; color: #b06000;")
        else:
            self.lbl_fwhm.setText(f"FWHM  {_fmt_fwhm(fwhm)}")
            self.lbl_fwhm.setStyleSheet(f"color: {theme.OK};")
            self.lbl_fwhm_note.setText("")

    def _on_mode_changed(self, *_):
        raster = self.cmb_mode.currentIndex() == 1
        # Spot FWHM spinbox: raster mode only — in static mode the scope
        # measures it directly, so manual entry is redundant.
        for wdg in (self.lbl_spot, self.spn_spot,
                    self.lbl_span_x, self.spn_span_x,
                    self.lbl_span_y, self.spn_span_y):
            wdg.setVisible(raster)
        self.lbl_fwhm.setVisible(not raster)
        self.lbl_fwhm_note.setVisible(not raster)
        self._recompute()

    def _recompute(self):
        raster   = self.cmb_mode.currentIndex() == 1
        sigma    = self.spn_spot.value() * BR.FWHM_TO_SIGMA
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
            self.lbl_status.setStyleSheet(f"font-size: {self._small}px; color: #b06000;")
        elif len(self._edges) < 4:
            self.lbl_status.setText("Waiting for all four slit positions")
            self.lbl_status.setStyleSheet(f"font-size: {self._small}px; color: #b06000;")
        elif not self._zeroed:
            self.lbl_status.setText(
                "⚠ Axes not zeroed this session — mm positions may be wrong")
            self.lbl_status.setStyleSheet(f"font-size: {self._small}px; color: {theme.FAULT};")
        else:
            self.lbl_status.setText("Slit positions live and zeroed")
            self.lbl_status.setStyleSheet(f"font-size: {self._small}px; color: {theme.OK};")

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
        missed = [slit for slit, hit in flags.items() if not hit]
        if not missed:
            self.lbl_overscan.setText("Overscan: beam reaching all four blades")
            self.lbl_overscan.setStyleSheet(f"font-size: {self._small}px; color: {theme.OK};")
        else:
            self.lbl_overscan.setText("Not reaching: " + ", ".join(missed))
            self.lbl_overscan.setStyleSheet(f"font-size: {self._small}px; color: {theme.FAULT};")
