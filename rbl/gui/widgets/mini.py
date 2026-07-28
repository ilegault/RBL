"""
mini.py
Small "dumb" display widgets for the Overview tab.

Modelled on the LabVIEW accelerator overview screens at Michigan: every live
number sits inside a scaled track, so one glance says where the value is
*within its range*, not just what it reads. "3.2 kV" alone does not tell an
operator whether that is comfortable or nearly at the rail; the same number
two-thirds of the way along a 0–5 kV track does, without reading the digits
at all.

Each widget only renders whatever value it is given — no polling, no unit
conversion, no hardware access. That logic lives in the Beamline state layer
(Phase 6); these widgets just have a `set(...)` / `push(...)` method and
something to draw.
"""
import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPolygonF
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)

from rbl.gui import theme


def _is_number(value) -> bool:
    """True when *value* is something we can actually draw.

    Snapshots use NaN for "channel not sampled this window" and None for "no
    reading yet"; both must render as a blank track rather than as zero, which
    would read as a real measurement of zero.
    """
    return value is not None and not (isinstance(value, float) and math.isnan(value))


class ValueTile(QWidget):
    """A labelled numeric readout: a name, a value, and a unit."""

    def __init__(self, name: str, unit: str = "", parent=None):
        super().__init__(parent)
        self._unit = unit
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        lay.addWidget(self.lbl_name)

        self.lbl_value = QLabel("—")
        self.lbl_value.setStyleSheet("font-weight: bold; font-size: 14px;")
        lay.addWidget(self.lbl_value)

    def set(self, value: float, stale: bool = False, fmt: str = "{:.3f}"):
        if not _is_number(value):
            self.lbl_value.setText("—")
        else:
            text = fmt.format(value)
            self.lbl_value.setText(f"{text} {self._unit}".strip())
        self.lbl_value.setStyleSheet(
            "font-weight: bold; font-size: 14px;"
            + (f" color: {theme.MUTED};" if stale else "")
        )


class BarTrack(QWidget):
    """The painted part of a MiniBar: groove, fill, tick marks, target marker.

    Fractions in, pixels out — it knows nothing about millimetres or volts.
    MiniBar owns the range and does the value→fraction conversion, so the two
    can disagree about units only in one place instead of two.
    """

    _GROOVE_H = 11
    _TICK_H   = 4
    _LABEL_H  = 12

    def __init__(self, ticks: int = 5, parent=None):
        super().__init__(parent)
        self._frac   = None          # None -> nothing measured yet
        self._target = None          # setpoint fraction, or None
        self._color  = theme.OK
        self._ticks  = max(2, ticks)
        self._tick_labels: list[str] = []
        self.setFixedHeight(self._GROOVE_H + self._TICK_H + self._LABEL_H)
        self.setMinimumWidth(80)

    # ---- State ---------------------------------------------------------------

    def set_fraction(self, frac, color: str = None):
        self._frac = None if frac is None else min(1.0, max(0.0, float(frac)))
        if color is not None:
            self._color = color
        self.update()

    def set_target_fraction(self, frac):
        self._target = None if frac is None else min(1.0, max(0.0, float(frac)))
        self.update()

    def set_tick_labels(self, labels: list[str]):
        """End-to-end scale captions, drawn under the ticks (left → right)."""
        self._tick_labels = list(labels)
        self.update()

    # ---- Paint ---------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width() - 1
        groove = QRectF(0.5, 0.5, w, self._GROOVE_H)

        p.setPen(QPen(QColor(theme.TRACK_EDGE), 1))
        p.setBrush(QBrush(QColor(theme.TRACK)))
        p.drawRoundedRect(groove, 2, 2)

        if self._frac is not None and self._frac > 0.0:
            fill = QRectF(groove)
            fill.setWidth(max(2.0, groove.width() * self._frac))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(self._color)))
            p.drawRoundedRect(fill, 2, 2)

        # Ticks below the groove, evenly spaced across the full scale.
        p.setPen(QPen(QColor(theme.TICK), 1))
        y0 = self._GROOVE_H + 1
        for i in range(self._ticks):
            x = 0.5 + w * (i / (self._ticks - 1))
            p.drawLine(QPointF(x, y0), QPointF(x, y0 + self._TICK_H))

        # Setpoint marker: a full-height line plus a caret, so it stays legible
        # both against the empty groove and on top of the fill.
        if self._target is not None:
            x = 0.5 + w * self._target
            p.setPen(QPen(QColor(theme.MARKER), 1))
            p.setBrush(QBrush(QColor(theme.MARKER)))
            p.drawLine(QPointF(x, 0.0), QPointF(x, float(self._GROOVE_H)))
            caret = QPolygonF([
                QPointF(x - 3.0, float(self._GROOVE_H + self._TICK_H)),
                QPointF(x + 3.0, float(self._GROOVE_H + self._TICK_H)),
                QPointF(x, float(self._GROOVE_H)),
            ])
            p.drawPolygon(caret)

        if self._tick_labels:
            p.setPen(QPen(QColor(theme.NEUTRAL)))
            font = p.font()
            font.setPointSize(7)
            p.setFont(font)
            y = self._GROOVE_H + self._TICK_H
            h = self._LABEL_H
            n = len(self._tick_labels)
            for i, text in enumerate(self._tick_labels):
                if n == 1:
                    p.drawText(QRectF(0, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
                    continue
                x = w * (i / (n - 1))
                if i == 0:
                    rect, align = QRectF(0, y, w / 2, h), Qt.AlignmentFlag.AlignLeft
                elif i == n - 1:
                    rect, align = QRectF(w / 2, y, w / 2, h), Qt.AlignmentFlag.AlignRight
                else:
                    rect, align = QRectF(x - w / 4, y, w / 2, h), Qt.AlignmentFlag.AlignCenter
                p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)

        p.end()


class MiniBar(QWidget):
    """A mini bar chart: name, live value, and where that value sits in range.

    The bar is the point — a number on its own says nothing about headroom.
    `set_target()` additionally marks a commanded setpoint on the same scale,
    which is how an operator sees a move in progress converge.
    """

    def __init__(self, name: str, minimum: float, maximum: float,
                 unit: str = "", color: str = None, decimals: int = 2,
                 ticks: int = 5, log_scale: bool = False, fmt=None,
                 scale_captions: list = None, parent=None):
        """`log_scale` places the value by decade — the only honest scale for
        a quantity that spans several of them, like a log amp's 1 nA … 1 mA.
        `fmt` is a callable value -> str for units the default decimals+suffix
        cannot express (again: currents, which change prefix as they grow).
        """
        super().__init__(parent)
        self._min      = float(minimum)
        self._max      = float(maximum)
        self._unit     = unit
        self._color    = color or theme.OK
        self._decimals = decimals
        self._log      = log_scale
        self._fmt      = fmt
        self._value    = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        header.addWidget(self.lbl_name)
        self.lbl_value = QLabel("—")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_value.setStyleSheet("font-weight: bold; font-size: 12px;")
        header.addWidget(self.lbl_value, stretch=1)
        lay.addLayout(header)

        self.track = BarTrack(ticks=ticks)
        self.track.set_tick_labels(scale_captions or self._scale_captions(ticks))
        lay.addWidget(self.track)

    # ---- Scale ---------------------------------------------------------------

    def _scale_captions(self, ticks: int) -> list[str]:
        """Captions for the end ticks and the midpoint only.

        Every tick gets a line, but only three get a number: at Overview widths
        a full row of labels collides into an unreadable smear.
        """
        fmt = "{:g}"
        mid = self._min + (self._max - self._min) / 2.0
        labels = [""] * max(2, ticks)
        labels[0] = fmt.format(self._min)
        labels[-1] = fmt.format(self._max)
        if len(labels) >= 3:
            labels[len(labels) // 2] = fmt.format(mid)
        return labels

    def _position(self, value):
        """Where *value* falls on this bar's scale, unclamped."""
        if self._log:
            if self._min <= 0.0 or self._max <= self._min:
                return None
            if value <= 0.0:
                return 0.0
            return ((math.log10(value) - math.log10(self._min))
                    / (math.log10(self._max) - math.log10(self._min)))
        if self._max <= self._min:
            return None
        return (value - self._min) / (self._max - self._min)

    def fraction(self):
        """Where the last value sits in range, 0..1 — or None if unmeasured."""
        if not _is_number(self._value):
            return None
        frac = self._position(self._value)
        return None if frac is None else min(1.0, max(0.0, frac))

    # ---- State ---------------------------------------------------------------

    def set(self, value: float, stale: bool = False, role: str = None):
        """Show *value*. `role` overrides the bar colour (e.g. theme.FAULT)."""
        self._value = value if _is_number(value) else None
        frac = self.fraction()
        color = theme.MUTED if stale else (role or self._color)
        self.track.set_fraction(frac, color)

        if self._value is None:
            self.lbl_value.setText("—")
        elif self._fmt is not None:
            self.lbl_value.setText(self._fmt(self._value))
        else:
            text = f"{self._value:.{self._decimals}f}"
            self.lbl_value.setText(f"{text} {self._unit}".strip())
        self.lbl_value.setStyleSheet(
            "font-weight: bold; font-size: 12px;"
            + (f" color: {theme.MUTED};" if stale else "")
        )

    def set_target(self, value: float):
        """Mark a commanded setpoint on the scale, or clear it with None."""
        self.track.set_target_fraction(
            self._position(value) if _is_number(value) else None)


class VBarTrack(QWidget):
    """BarTrack rotated: the fill grows upward from the bottom.

    A vertical track beside a column of number boxes costs a fraction of the
    width a horizontal one costs beneath them, which is what lets two axis
    panels sit side by side instead of stacked.
    """

    _GROOVE_W = 13
    _TICK_W   = 3

    def __init__(self, ticks: int = 5, parent=None):
        super().__init__(parent)
        self._frac   = None
        self._target = None
        self._color  = theme.OK
        self._ticks  = max(2, ticks)
        self.setFixedWidth(self._GROOVE_W + self._TICK_W + 2)
        # Tall enough that a fill fraction is still readable as a fraction —
        # beside a two-row form the track would otherwise be squeezed to the
        # form's height minus its own labels, which is about 30 px.
        self.setMinimumHeight(58)

    def set_fraction(self, frac, color: str = None):
        self._frac = None if frac is None else min(1.0, max(0.0, float(frac)))
        if color is not None:
            self._color = color
        self.update()

    def set_target_fraction(self, frac):
        self._target = None if frac is None else min(1.0, max(0.0, float(frac)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        h = self.height() - 1
        groove = QRectF(0.5, 0.5, self._GROOVE_W, h)
        p.setPen(QPen(QColor(theme.TRACK_EDGE), 1))
        p.setBrush(QBrush(QColor(theme.TRACK)))
        p.drawRoundedRect(groove, 2, 2)

        if self._frac is not None and self._frac > 0.0:
            fh = max(2.0, h * self._frac)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(self._color)))
            p.drawRoundedRect(QRectF(0.5, 0.5 + h - fh, self._GROOVE_W, fh), 2, 2)

        p.setPen(QPen(QColor(theme.TICK), 1))
        x0 = self._GROOVE_W + 1
        for i in range(self._ticks):
            y = 0.5 + h * (i / (self._ticks - 1))
            p.drawLine(QPointF(x0, y), QPointF(x0 + self._TICK_W, y))

        if self._target is not None:
            y = 0.5 + h * (1.0 - self._target)
            p.setPen(QPen(QColor(theme.MARKER), 1))
            p.setBrush(QBrush(QColor(theme.MARKER)))
            p.drawLine(QPointF(0.0, y), QPointF(float(self._GROOVE_W), y))
            p.drawPolygon(QPolygonF([
                QPointF(float(self._GROOVE_W + self._TICK_W), y - 3.0),
                QPointF(float(self._GROOVE_W + self._TICK_W), y + 3.0),
                QPointF(float(self._GROOVE_W), y),
            ]))
        p.end()


class VMiniBar(QWidget):
    """A vertical mini bar chart: value on top, scaled track below.

    Same contract as MiniBar — set() for the measured value, set_target() for
    the commanded one on the same scale — in a column instead of a row.
    """

    def __init__(self, name: str, minimum: float, maximum: float,
                 unit: str = "", color: str = None, decimals: int = 2,
                 ticks: int = 5, parent=None):
        super().__init__(parent)
        self._min      = float(minimum)
        self._max      = float(maximum)
        self._unit     = unit
        self._color    = color or theme.OK
        self._decimals = decimals
        self._value    = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(1)

        self.lbl_value = QLabel("—")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lbl_value.setStyleSheet("font-weight: bold; font-size: 11px;")
        lay.addWidget(self.lbl_value)

        self.track = VBarTrack(ticks=ticks)
        lay.addWidget(self.track, stretch=1, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.lbl_name = QLabel(name)
        self.lbl_name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lbl_name.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 9px;")
        lay.addWidget(self.lbl_name)

    def _position(self, value):
        if self._max <= self._min:
            return None
        return (value - self._min) / (self._max - self._min)

    def fraction(self):
        if not _is_number(self._value):
            return None
        frac = self._position(self._value)
        return None if frac is None else min(1.0, max(0.0, frac))

    def set(self, value: float, stale: bool = False, role: str = None):
        self._value = value if _is_number(value) else None
        self.track.set_fraction(self.fraction(),
                                theme.MUTED if stale else (role or self._color))
        if self._value is None:
            self.lbl_value.setText("—")
        else:
            text = f"{self._value:.{self._decimals}f}"
            self.lbl_value.setText(f"{text} {self._unit}".strip())
        self.lbl_value.setStyleSheet(
            "font-weight: bold; font-size: 11px;"
            + (f" color: {theme.MUTED};" if stale else ""))

    def set_target(self, value: float):
        self.track.set_target_fraction(
            self._position(value) if _is_number(value) else None)


class PairTrace(QWidget):
    """Two waveforms drawn over each other on one shared scale.

    The point is the RELATIONSHIP, not either trace's values: a push-pull pair
    driven correctly is two mirror-image traces crossing at zero, and a pair
    whose generators have drifted apart is two traces sliding past each other.
    Neither is visible on two separate plots with independent auto-scales, so
    both series share one symmetric-about-zero scale and one x grid here.

    Both series must be sampled on the SAME grid — same window, same length —
    or the phase they appear to have is an artefact of the resampling.
    """

    _ZERO = QColor("#999")

    def __init__(self, color_a: str, color_b: str, parent=None):
        super().__init__(parent)
        self._a: list = []
        self._b: list = []
        self._color_a = color_a
        self._color_b = color_b
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def set_pair(self, a, b):
        self._a = [v for v in (a or []) if _is_number(v)]
        self._b = [v for v in (b or []) if _is_number(v)]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width() - 1
        h = self.height() - 2

        # Symmetric about zero so the crossing point is the vertical centre —
        # an auto-scaled pair would put "zero" wherever the data happened to
        # average, and anti-phase would stop looking like anti-phase.
        span = max((abs(v) for v in self._a + self._b), default=0.0)
        p.setPen(QPen(self._ZERO, 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0, 1 + h / 2.0), QPointF(w, 1 + h / 2.0))
        if span <= 0.0 or len(self._a) < 2:
            p.end()
            return
        span *= 1.10   # a little headroom so peaks don't sit on the frame

        for values, color in ((self._a, self._color_a), (self._b, self._color_b)):
            if len(values) < 2:
                continue
            poly = QPolygonF()
            step = w / (len(values) - 1)
            for i, v in enumerate(values):
                poly.append(QPointF(i * step, 1 + h / 2.0 - (v / span) * (h / 2.0)))
            p.setPen(QPen(QColor(color), 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(poly)
        p.end()


class TraceArea(QWidget):
    """The painted part of a Sparkline: a self-scaling polyline, no axes."""

    def __init__(self, color: str = None, parent=None):
        super().__init__(parent)
        self._color = color or theme.OK
        self._values: list = []
        self.setFixedHeight(26)
        self.setMinimumWidth(60)

    def set_values(self, values: list):
        self._values = values
        self.update()

    def paintEvent(self, event):
        points = [v for v in self._values if _is_number(v)]
        if len(points) < 2:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width() - 1
        h = self.height() - 3

        lo, hi = min(points), max(points)
        span = hi - lo
        if span <= 0:
            # A flat trace still has a shape worth seeing (it's flat); draw it
            # down the middle rather than dividing by zero to place it.
            span = 1.0
            lo = lo - 0.5

        poly = QPolygonF()
        step = w / (len(points) - 1)
        for i, v in enumerate(points):
            y = 1.5 + h * (1.0 - (v - lo) / span)
            poly.append(QPointF(i * step, y))

        p.setPen(QPen(QColor(self._color), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(poly)
        p.end()


class Sparkline(QWidget):
    """A tiny recent-history trace, drawn as connected line segments.

    No axes, no ticks — this is a trend glance, not a plot to read values off.
    The number beside it is the value; the line is only "steady / drifting /
    just moved".
    """

    def __init__(self, name: str, color: str = None, parent=None):
        super().__init__(parent)
        self._color = color or theme.OK
        self._values = []
        self._max_points = 60

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet(
            f"color: {self._color}; font-size: 10px; font-weight: bold;")
        header.addWidget(self.lbl_name)

        self.lbl_value = QLabel("—")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_value.setStyleSheet("font-size: 10px;")
        header.addWidget(self.lbl_value, stretch=1)
        lay.addLayout(header)

        self.trace = TraceArea(self._color)
        lay.addWidget(self.trace)

    def push(self, value: float, fmt: str = "{:.2f}"):
        self._values.append(value)
        if len(self._values) > self._max_points:
            self._values.pop(0)
        self.lbl_value.setText(fmt.format(value) if _is_number(value) else "—")
        self.trace.set_values(self._values)
