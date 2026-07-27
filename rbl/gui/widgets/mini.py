"""
mini.py
Small "dumb" display widgets for the Overview tab.

Each widget only renders whatever value it is given — no polling, no unit
conversion, no hardware access. That logic lives in the Beamline state layer
(Phase 6); these widgets just have a `set(...)` method and something to draw.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar

from rbl.gui import theme


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
        if value is None:
            self.lbl_value.setText("—")
        else:
            text = fmt.format(value)
            self.lbl_value.setText(f"{text} {self._unit}".strip())
        self.lbl_value.setStyleSheet(
            "font-weight: bold; font-size: 14px;"
            + (f" color: {theme.MUTED};" if stale else "")
        )


class MiniBar(QWidget):
    """A small horizontal bar for a value with a known range, e.g. amplitude."""

    def __init__(self, name: str, minimum: float, maximum: float, parent=None):
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet(f"color: {theme.NEUTRAL}; font-size: 10px;")
        lay.addWidget(self.lbl_name)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        lay.addWidget(self.bar)

    def set(self, value: float, stale: bool = False, role: str = None):
        if value is None or self._max <= self._min:
            self.bar.setValue(0)
        else:
            frac = (value - self._min) / (self._max - self._min)
            frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
            self.bar.setValue(int(frac * 1000))
        color = theme.MUTED if stale else (role or theme.OK)
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; }}")


class Sparkline(QWidget):
    """A tiny recent-history trace, drawn as connected line segments.

    No axes, no ticks — this is a trend glance, not a plot to read values off.
    """

    def __init__(self, name: str, color: str = None, parent=None):
        super().__init__(parent)
        self._color = color or theme.OK
        self._values = []
        self._max_points = 60

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet(f"color: {self._color}; font-size: 10px; font-weight: bold;")
        lay.addWidget(self.lbl_name)

        self.lbl_value = QLabel("—")
        self.lbl_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_value.setStyleSheet("font-size: 10px;")
        lay.addWidget(self.lbl_value, stretch=1)

    def push(self, value: float, fmt: str = "{:.2f}"):
        self._values.append(value)
        if len(self._values) > self._max_points:
            self._values.pop(0)
        self.lbl_value.setText(fmt.format(value) if value is not None else "—")
